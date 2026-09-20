"""``story_guard`` : décide seul de la publication d'un récit, fail-closed.

Deux temps :

1. contrôles mécaniques en Python (``narrative.checks``) ;
2. juge LLM distinct (prompt ``story_guard_v*``), verdict JSON noté sur sept
   critères.

Un échec de parsing, un doute exprimé, un verdict autre que ``accept``, une
note absente, hors échelle ou sous le seuil, ou une erreur d'appel donnent
``rejected``. Le rapport suit ``guard_report_json`` du contrat de données ;
ses ``reasons`` de premier niveau sont des codes produits ici, jamais du texte
brut du LLM (le front affiche la décision, les contrôles et les raisons).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from logging_config import get_logger
from narrative.checks import (
    MechanicalSettings,
    coerce_year,
    normalise,
    run_mechanical_checks,
    year_window,
)
from narrative.config import NarrativeConfig
from narrative.inputs import StoryInputs, bullets
from narrative.llm import CostMeter, call_json
from narrative.prompting import render_prompt

logger = get_logger("narrative.guard")

#: Critères notés par le juge (clés de ``judge.scores``).
JUDGE_CRITERIA: tuple[str, ...] = (
    "fidelity",
    "no_real_entities",
    "no_promise_or_advice",
    "not_claimed_proven",
    "limit_present",
    "readable_at_15",
    "constitution_exclusions",
)

#: Contrôles fermés du juge (prompt ``story_guard_v4`` et suivants) :
#: nom → (réponse défavorable, « sans objet » autorisé). Le juge répond
#: « oui », « non » ou « sans objet » ; c'est Python qui en déduit le rejet,
#: pour que la décision ne dépende plus de l'impression générale du juge
#: (itération 4 : sept récits publiés avec des notes de 9 sur des défauts que
#: les jurés ont relevés). Un contrôle absent ou illisible rejette aussi.
JUDGE_CONTROLS: dict[str, tuple[str, bool]] = {
    "raison_donnee": ("non", False),
    "mot_sujet_glose": ("non", True),
    "calcul_juste": ("non", True),
    "chiffre_pivot_lisible": ("non", False),
    "succes_enonce_comme_loi": ("oui", False),
    "fait_qui_change": ("non", False),
    "objet_du_brief_conserve": ("non", False),
    "geste_dans_le_domaine": ("non", True),
    "contradiction": ("oui", False),
    "attente_medicale_bornee": ("non", True),
}

#: Réponses acceptées pour un contrôle, après normalisation.
_CONTROL_YES: frozenset[str] = frozenset({"oui", "yes", "true", "vrai", "o"})
_CONTROL_NO: frozenset[str] = frozenset({"non", "no", "false", "faux", "n"})
_CONTROL_NA: frozenset[str] = frozenset(
    {"sans objet", "sans-objet", "sansobjet", "non applicable", "n/a", "na", "aucun", "aucune"}
)

#: Exclusions utilisées si la constitution est illisible (fail-closed : le
#: juge reçoit au moins les exclusions connues).
DEFAULT_EXCLUSIONS: tuple[str, ...] = (
    "weapons_development",
    "surveillance_technology",
    "any domain with dual-use concerns without human approval",
)


@lru_cache(maxsize=4)
def constitution_exclusions(path: Path) -> tuple[str, ...]:
    """Domaines exclus par la constitution, lus en lecture seule.

    Args:
        path: ``data/constitution.yaml``.

    Returns:
        Liste ``ethics.excluded_domains`` ; exclusions par défaut si le
        fichier est illisible.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        excluded = (data.get("ethics") or {}).get("excluded_domains") or []
        items = tuple(str(item) for item in excluded if str(item).strip())
        if items:
            return items
    except (OSError, yaml.YAMLError, AttributeError) as exc:
        logger.error("narrative_constitution_unreadable", error=str(exc)[:200])
    return DEFAULT_EXCLUSIONS


@dataclass
class GuardOutcome:
    """Décision du garde pour une tentative.

    Attributes:
        decision: ``published`` ou ``rejected``.
        report: Rapport complet (``guard_report_json``).
        judge_model: Modèle du juge réellement appelé, ``None`` s'il n'a pas
            été appelé.
        meter: Coût et jetons du juge.
    """

    decision: str
    report: dict[str, Any]
    judge_model: str | None = None
    meter: CostMeter = field(default_factory=CostMeter)

    @property
    def published(self) -> bool:
        """Récit publiable."""
        return self.decision == "published"


def mechanical_settings(config: NarrativeConfig, lang: str) -> MechanicalSettings:
    """Paramètres mécaniques d'une langue.

    Args:
        config: Configuration de la couche.
        lang: ``fr`` ou ``en``.

    Returns:
        Paramètres prêts pour ``run_mechanical_checks``.
    """
    min_words, max_words = config.words[lang]
    year_min, year_max = year_window(config.year_offset_min, config.year_offset_max)
    return MechanicalSettings(
        lang=lang,
        min_words=min_words,
        max_words=max_words,
        year_min=year_min,
        year_max=year_max,
        title_max_chars=config.title_max_chars,
        denylist_path=config.identity_denylist_path,
        strict_first_person_plural=config.guard.strict_first_person_plural,
    )


def normalise_control(value: Any) -> str | None:
    """Ramène une réponse de contrôle à ``oui``, ``non`` ou ``sans objet``.

    Args:
        value: Valeur rendue par le juge.

    Returns:
        Réponse normalisée, ou ``None`` si elle est illisible (rejet).
    """
    if not isinstance(value, str):
        return None
    text = normalise(value).strip(" .;:!\"'")
    if text in _CONTROL_YES:
        return "oui"
    if text in _CONTROL_NO:
        return "non"
    if text in _CONTROL_NA:
        return "sans objet"
    return None


def evaluate_controls(data: Any) -> tuple[dict[str, str | None], list[str]]:
    """Lit le bloc ``controles`` du juge et en déduit les motifs de rejet.

    Le juge ne décide pas : il répond à dix questions fermées, étayées par
    ses constats, et Python applique la règle. Une réponse défavorable,
    absente ou illisible rejette le récit (fail-closed).

    Args:
        data: Objet JSON rendu par le juge.

    Returns:
        Les réponses normalisées et les codes de raison produits.
    """
    raw = data.get("controles") if isinstance(data, Mapping) else None
    if not isinstance(raw, Mapping):
        return ({name: None for name in JUDGE_CONTROLS}, ["judge:controls_missing"])
    answers: dict[str, str | None] = {}
    reasons: list[str] = []
    for name, (unfavourable, allow_na) in JUDGE_CONTROLS.items():
        answer = normalise_control(raw.get(name))
        answers[name] = answer
        if answer is None:
            reasons.append(f"judge:control_unreadable:{name}")
        elif answer == unfavourable:
            reasons.append(f"judge:control_failed:{name}")
        elif answer == "sans objet" and not allow_na:
            reasons.append(f"judge:control_not_applicable:{name}")
    return answers, reasons


def _sanitise_constats(data: Any) -> dict[str, Any] | None:
    """Relevé factuel du juge, borné, conservé pour l'audit.

    Args:
        data: Objet JSON rendu par le juge.

    Returns:
        Constats tronqués, ou ``None`` s'ils sont absents.
    """
    raw = data.get("constats") if isinstance(data, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    constats: dict[str, Any] = {}
    for key, value in list(raw.items())[:20]:
        if isinstance(value, list):
            constats[str(key)[:60]] = [str(item)[:200] for item in value[:10]]
        else:
            constats[str(key)[:60]] = str(value)[:400]
    return constats


def evaluate_verdict(data: Any, config: NarrativeConfig) -> dict[str, Any]:
    """Transforme la sortie du juge en section ``judge`` du rapport.

    Args:
        data: Objet JSON rendu par le juge.
        config: Configuration (échelle, seuils, contrôles fermés).

    Returns:
        Section ``judge`` : ``passed``, ``constats``, ``controles``,
        ``scores``, ``threshold``, ``thresholds``, ``doubts``,
        ``raw_verdict``, ``reasons`` (codes).
    """
    guard = config.guard
    section: dict[str, Any] = {
        "passed": False,
        "scores": {},
        "threshold": guard.threshold,
        "thresholds": {name: guard.threshold_for(name) for name in JUDGE_CRITERIA},
        "doubts": [],
        "raw_verdict": None,
    }
    reasons: list[str] = []
    if not isinstance(data, Mapping):
        section["reasons"] = ["judge:malformed_output"]
        return section

    constats = _sanitise_constats(data)
    if constats is not None:
        section["constats"] = constats
    if guard.require_controls:
        answers, control_reasons = evaluate_controls(data)
        section["controles"] = answers
        reasons.extend(control_reasons)

    scores_raw = data.get("scores")
    scores: dict[str, float | None] = {}
    if not isinstance(scores_raw, Mapping):
        reasons.append("judge:scores_missing")
    else:
        for name in JUDGE_CRITERIA:
            value = scores_raw.get(name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                scores[name] = None
                reasons.append(f"judge:score_missing:{name}")
                continue
            scores[name] = value
            if not guard.score_min <= value <= guard.score_max:
                reasons.append(f"judge:score_out_of_scale:{name}")
            elif value < guard.threshold_for(name):
                reasons.append(f"judge:below_threshold:{name}")
    section["scores"] = scores

    doubts = data.get("doubts")
    if doubts is None:
        doubts = []
    if not isinstance(doubts, list):
        reasons.append("judge:doubts_malformed")
        doubts = [str(doubts)]
    doubts = [str(item)[:300] for item in doubts if str(item).strip()]
    section["doubts"] = doubts
    if doubts:
        reasons.append("judge:doubt")

    verdict = data.get("verdict")
    section["raw_verdict"] = verdict if isinstance(verdict, str) else None
    if not isinstance(verdict, str) or verdict.strip().lower() != "accept":
        reasons.append("judge:verdict_not_accept")

    judge_reasons = data.get("reasons")
    if isinstance(judge_reasons, list):
        # Texte du juge conservé pour l'audit, hors des raisons affichées.
        section["judge_reasons"] = [str(item)[:300] for item in judge_reasons][:10]

    section["passed"] = not reasons
    section["reasons"] = reasons
    return section


def _judge_prompt(
    story: Mapping[str, Any],
    inputs: StoryInputs,
    config: NarrativeConfig,
    lang: str,
) -> str:
    limits = list(inputs.limits) + [f"Contre-preuve : {item}" for item in inputs.counter_evidence]
    return render_prompt(
        config.guard.llm.prompt,
        current_year=datetime.now(UTC).year,
        score_min=config.guard.score_min,
        score_max=config.guard.score_max,
        constitution_exclusions=bullets(constitution_exclusions(config.constitution_path)),
        lang=lang,
        brief_title=inputs.title or "(sans titre)",
        formal_statement=inputs.formal_statement or "(absent)",
        causal_chain=bullets(inputs.causal_chain),
        limits=bullets(limits),
        story_title=str(story.get("title") or ""),
        story_year=str(coerce_year(story.get("year")) or story.get("year") or ""),
        story_place=str(story.get("place") or ""),
        story_mechanism=str(story.get("mechanism") or ""),
        story_limit=str(story.get("limit_or_risk") or ""),
        story_body=str(story.get("body_markdown") or ""),
    )


async def guard_story(
    story: Mapping[str, Any],
    inputs: StoryInputs,
    *,
    lang: str,
    config: NarrativeConfig,
    db_path: str | Path,
    run_label: str,
    node: str,
) -> GuardOutcome:
    """Contrôle un récit et décide de sa publication.

    Ne lève jamais : toute erreur (prompt, appel, parsing) produit un rejet.

    Args:
        story: Récit (sortie normalisée du rédacteur ou du traducteur).
        inputs: Entrées du brief (mécanisme et limites de référence).
        lang: ``fr`` ou ``en``.
        config: Configuration de la couche.
        db_path: Base du registre de coût.
        run_label: Étiquette de coût.
        node: Nom du nœud (``story_guard`` ou ``story_guard_en``).

    Returns:
        Décision, rapport, modèle du juge et coût.
    """
    meter = CostMeter()
    reasons: list[str] = []
    try:
        mechanical = run_mechanical_checks(story, mechanical_settings(config, lang))
    except Exception as exc:  # noqa: BLE001 — un contrôle qui plante rejette
        logger.error("narrative_guard_mechanical_crashed", node=node, error=str(exc)[:300])
        mechanical = {"passed": False, "checks": {}, "reasons": ["mechanical:crashed"]}
    reasons.extend(mechanical.get("reasons", []))

    judge: dict[str, Any] = {"passed": False, "model": None, "skipped": False}
    judge_model: str | None = None
    if not mechanical["passed"] and config.guard.skip_judge_on_mechanical_failure:
        judge.update({"skipped": True, "reasons": ["judge:skipped_after_mechanical_failure"]})
    else:
        try:
            prompt = _judge_prompt(story, inputs, config, lang)
            result = await call_json(
                step=config.guard.llm,
                node=node,
                prompt=prompt,
                config=config,
                db_path=db_path,
                run_label=run_label,
                brief_id=inputs.brief_id,
                meter=meter,
            )
            judge_model = result.model
            judge.update(evaluate_verdict(result.data, config))
            judge["model"] = result.model
        except Exception as exc:  # noqa: BLE001 — parsing ou appel en échec : rejet
            logger.warning(
                "narrative_guard_judge_failed",
                node=node,
                brief_id=inputs.brief_id,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            judge.update({"passed": False, "reasons": [f"judge:call_failed:{type(exc).__name__}"]})
        reasons.extend(judge.get("reasons", []))

    decision = "published" if mechanical["passed"] and judge.get("passed") is True else "rejected"
    if decision == "rejected" and not reasons:
        reasons.append("guard:not_accepted")
    report = {
        "version": config.guard.llm.prompt,
        "lang": lang,
        "mechanical": {"passed": bool(mechanical["passed"]), "checks": mechanical.get("checks", {})},
        "judge": judge,
        "decision": decision,
        "reasons": reasons,
    }
    logger.info(
        "narrative_guard_decision",
        node=node,
        brief_id=inputs.brief_id,
        lang=lang,
        decision=decision,
        reasons=reasons[:10],
    )
    return GuardOutcome(decision=decision, report=report, judge_model=judge_model, meter=meter)


def failure_report(
    config: NarrativeConfig, lang: str, reasons: Sequence[str]
) -> dict[str, Any]:
    """Rapport d'une tentative rejetée avant le garde (rédaction ou traduction en échec).

    Args:
        config: Configuration.
        lang: ``fr`` ou ``en``.
        reasons: Codes de raison.

    Returns:
        Rapport au format du contrat, décision ``rejected``.
    """
    return {
        "version": config.guard.llm.prompt,
        "lang": lang,
        "mechanical": {"passed": False, "checks": {}},
        "judge": {"passed": False, "model": None, "skipped": True},
        "decision": "rejected",
        "reasons": list(reasons),
    }
