"""``explainer_addendum`` : notes grand public des trois blocs de l'étage 2 (v2.1 B2, E-018).

L'étage « L'idée, expliquée » affichait, dans deux de ses blocs, la matière brute du dossier :
l'objection du contradicteur (« ce qui pourrait la tuer ») et les questions décisives des
relecteurs (« ce qu'on ignore »), dans leur vocabulaire technique et parfois en anglais. La v2
avait nommé la solution sans la construire (``docs/v2/ARCHITECTURE_INFO.md:441``) : des notes
grand public, en FR et en EN, dérivées du brief et gardées, sans nouvelle référence.

Un appel de rédaction par (brief, langue) rend les trois notes ensemble ; le français et
l'anglais sont écrits chacun depuis les mêmes sources, qui sont en anglais. Puis le garde,
fail-closed, en deux temps comme pour les récits :

1. contrôles mécaniques (``narrative.checks``) : présence et longueur de chaque note, aucune
   référence, aucun terme proscrit ni « nous » éditorial, aucune entrée de la denylist
   d'identité, orthographe britannique en anglais, français accentué, et **aucun nombre absent
   des sources** — une note ne doit rien chiffrer que le brief ne chiffre pas ;
2. un juge LLM distinct (``addendum_guard_v*``) : fidélité, absence de fait nouveau, statut
   honnête, lisibilité, chacun noté, plus un verdict.

Chaque tentative est écrite dans ``v2_explainer_addendum``, publiée ou rejetée, avec son rapport.
Rien ici n'influence la sélection ni la publication d'un brief.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from logging_config import get_logger
from narrative.checks import (
    citation_hits,
    french_residue_hits,
    identity_check,
    proscribed_hits,
    us_spelling_hits,
)
from narrative.config import AddendumConfig, NarrativeConfig
from narrative.inputs import bullets, load_blob
from narrative.llm import CostMeter, call_json
from narrative.prompting import render_prompt
from storage import narrative_db

logger = get_logger("narrative.addendum")

BLOCS: tuple[str, ...] = narrative_db.ADDENDUM_BLOCS

#: Critères notés par le juge.
JUDGE_CRITERIA: tuple[str, ...] = ("fidelity", "no_new_facts", "status_honest", "plain_language")

#: Longueur maximale d'un élément de source injecté dans le prompt.
ITEM_MAX_CHARS = 500

#: Nombre maximal d'éléments par liste de sources.
LIST_MAX_ITEMS = 5

#: Lettres accentuées du français (même liste que le front, ``idea-text.ts``).
ACCENTED = re.compile(r"[àâäáãåçéèêëíìîïñóòôöõúùûüýÿœæÀÂÄÁÃÅÇÉÈÊËÍÌÎÏÑÓÒÔÖÕÚÙÛÜÝŸŒÆ]")

#: Au-delà de cette longueur, un texte français sans une seule lettre accentuée est désaccentué
#: (B3). Plus bas que les 200 signes du contrat : une note fait deux à quatre phrases.
FR_UNACCENTED_MIN_CHARS = 80

#: Nombres écrits en chiffres. « 2,5 » et « 2.5 » sont le même nombre.
NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])")

#: Préfixe technique des objections du contradicteur (« FAIL REASON #1: »), retiré.
FAIL_REASON = re.compile(r"^\s*fail\s+reason\s*#?\d*\s*(\([^)]*\))?\s*[:—–-]\s*", re.IGNORECASE)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def _clip(text: str, limit: int = ITEM_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _strings(values: Any, limit: int = LIST_MAX_ITEMS) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence):
        return []
    out: list[str] = []
    for item in values:
        text = _clip(_text(item))
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def normalise_number(raw: str) -> str:
    """Forme canonique d'un nombre écrit en chiffres (« 2,50 » → « 2.5 »).

    Args:
        raw: Nombre tel qu'écrit.

    Returns:
        Forme comparable.
    """
    whole, _, frac = raw.replace(",", ".").partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def numbers_in(text: str) -> set[str]:
    """Nombres écrits en chiffres dans un texte, sous forme canonique.

    Args:
        text: Texte.

    Returns:
        Ensemble des nombres.
    """
    return {normalise_number(m.group(0)) for m in NUMBER.finditer(text)}


@dataclass(frozen=True)
class AddendumSources:
    """Ce que le rédacteur et le juge savent du brief, bloc par bloc.

    Attributes:
        brief_id: Identifiant du brief.
        formal_statement: Énoncé formel de l'hypothèse.
        counter_evidence: Constats des contre-preuves, avec leur sévérité, sans référence.
        objection: Objection principale du contradicteur, étiquette technique retirée.
        kill_condition: Condition de réfutation de l'hypothèse, si elle est liée.
        boundaries: Conditions de validité.
        unknowns: Inconnues du mécanisme.
        gaps: Lacunes relevées dans la littérature.
        questions: Questions décisives des relecteurs.
        predictions: Prédictions testables, avec leur borne chiffrée.
        protocol: Phases du protocole (nom, objectif, durée).
    """

    brief_id: str
    formal_statement: str
    counter_evidence: tuple[str, ...]
    objection: str
    kill_condition: str
    boundaries: tuple[str, ...]
    unknowns: tuple[str, ...]
    gaps: tuple[str, ...]
    questions: tuple[str, ...]
    predictions: tuple[str, ...]
    protocol: tuple[str, ...]

    def all_text(self) -> str:
        """Toutes les sources en un texte (contrôle des nombres).

        Returns:
            Texte concaténé.
        """
        parts = [self.formal_statement, self.objection, self.kill_condition]
        for group in (
            self.counter_evidence, self.boundaries, self.unknowns, self.gaps,
            self.questions, self.predictions, self.protocol,
        ):
            parts.extend(group)
        return "\n".join(p for p in parts if p)

    def has_substance(self) -> bool:
        """Assez de matière pour les trois notes.

        Returns:
            ``True`` si l'énoncé existe et que chaque bloc a au moins une source.
        """
        return bool(
            self.formal_statement
            and (self.counter_evidence or self.objection or self.kill_condition or self.boundaries)
            and (self.unknowns or self.gaps or self.questions)
            and (self.predictions or self.protocol)
        )


def _kill_condition(conn: Any, brief_id: str) -> str:
    try:
        row = conn.execute(
            "SELECT h.kill_condition FROM briefs b JOIN hypotheses h ON h.id = b.hypothesis_id WHERE b.id = ?",
            (brief_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001 — base sans lien ni table : la source est facultative
        return ""
    return _clip(_text(row[0])) if row else ""


def extract_sources(row: Mapping[str, Any], *, kill_condition: str = "") -> AddendumSources:
    """Construit les sources des trois notes à partir d'une ligne ``briefs``.

    Aucune référence bibliographique n'entre : ni titre, ni auteur, ni DOI des contre-preuves.

    Args:
        row: Ligne ``briefs``.
        kill_condition: Condition de réfutation lue dans ``hypotheses``, si liée.

    Returns:
        Sources normalisées et bornées.
    """
    sharpened = load_blob(row.get("sharpened_data")) or {}
    sharpened = sharpened if isinstance(sharpened, Mapping) else {}
    mechanism = sharpened.get("proposed_mechanism")
    mechanism = mechanism if isinstance(mechanism, Mapping) else {}
    grounding = load_blob(row.get("grounding_data")) or {}
    grounding = grounding if isinstance(grounding, Mapping) else {}
    panel = load_blob(row.get("panel_data")) or {}
    panel = panel if isinstance(panel, Mapping) else {}
    protocol = load_blob(row.get("protocol_data")) or {}
    protocol = protocol if isinstance(protocol, Mapping) else {}

    counter: list[str] = []
    for item in grounding.get("counter_evidence") or []:
        if not isinstance(item, Mapping):
            continue
        finding = _text(item.get("finding") or item.get("key_finding"))
        if finding:
            severity = _text(item.get("severity"))
            counter.append(_clip(f"[{severity}] {finding}" if severity else finding))
        if len(counter) >= LIST_MAX_ITEMS:
            break

    reviews = [r for r in panel.get("reviews") or [] if isinstance(r, Mapping)]
    objection = ""
    for review in reviews:
        if _text(review.get("reviewer_persona")) == "contrarian":
            weaknesses = _strings(review.get("weaknesses"), limit=1)
            if weaknesses:
                objection = _clip(FAIL_REASON.sub("", weaknesses[0]).strip(), 700)
            break
    questions: list[str] = []
    for review in reviews:
        for q in _strings(review.get("critical_questions"), limit=3):
            if q not in questions:
                questions.append(q)
    boundaries = []
    for item in sharpened.get("boundary_conditions") or []:
        if isinstance(item, Mapping) and _text(item.get("condition")):
            boundaries.append(_clip(_text(item.get("condition"))))
    gap_update = grounding.get("gap_manifest_update")
    gaps = _strings(gap_update.get("new_gaps") if isinstance(gap_update, Mapping) else None)

    predictions: list[str] = []
    for item in sharpened.get("falsifiable_predictions") or []:
        if isinstance(item, Mapping):
            text = _text(item.get("prediction"))
            bound = _text(item.get("quantitative_bound"))
            if text:
                predictions.append(_clip(f"{text} (bound: {bound})" if bound else text))
        if len(predictions) >= LIST_MAX_ITEMS:
            break
    phases: list[str] = []
    for item in protocol.get("phases") or []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("phase_name"))
        objective = _text(item.get("objective"))
        resources = item.get("required_resources")
        duration = _text(resources.get("estimated_duration")) if isinstance(resources, Mapping) else ""
        line = " — ".join(x for x in (name, objective, f"duration: {duration}" if duration else "") if x)
        if line:
            phases.append(_clip(line))
        if len(phases) >= LIST_MAX_ITEMS:
            break

    return AddendumSources(
        brief_id=str(row.get("id") or ""),
        formal_statement=_clip(_text(sharpened.get("formal_statement") or row.get("formal_statement")), 1200),
        counter_evidence=tuple(counter),
        objection=objection,
        kill_condition=kill_condition,
        boundaries=tuple(boundaries[:LIST_MAX_ITEMS]),
        unknowns=tuple(_strings(mechanism.get("known_unknowns"))),
        gaps=tuple(gaps),
        questions=tuple(questions[: LIST_MAX_ITEMS + 1]),
        predictions=tuple(predictions),
        protocol=tuple(phases),
    )


# ── Rédaction ───────────────────────────────────────────────────────

LANG_NAMES = {"fr": "français", "en": "anglais britannique"}


def writer_prompt(sources: AddendumSources, lang: str, cfg: AddendumConfig) -> str:
    """Prompt de rédaction des trois notes.

    Args:
        sources: Sources du brief.
        lang: Langue de sortie.
        cfg: Réglages des notes.

    Returns:
        Prompt complet.
    """
    min_words, max_words = cfg.words[lang]
    return render_prompt(
        cfg.writer.prompt,
        lang=lang,
        lang_name=LANG_NAMES[lang],
        min_words=min_words,
        max_words=max_words,
        formal_statement=sources.formal_statement or "(absent)",
        counter_evidence=bullets(list(sources.counter_evidence), empty="(aucune contre-preuve)"),
        objection=sources.objection or "(aucune)",
        kill_condition=sources.kill_condition or "(non liée)",
        boundaries=bullets(list(sources.boundaries), empty="(aucune)"),
        unknowns=bullets(list(sources.unknowns), empty="(aucune)"),
        gaps=bullets(list(sources.gaps), empty="(aucune)"),
        questions=bullets(list(sources.questions), empty="(aucune)"),
        predictions=bullets(list(sources.predictions), empty="(aucune)"),
        protocol=bullets(list(sources.protocol), empty="(aucun)"),
    )


def normalise_texts(data: Any) -> dict[str, Any]:
    """Ramène la sortie du rédacteur aux trois notes.

    Args:
        data: Objet JSON rendu par le LLM.

    Returns:
        ``{bloc: texte}`` ; une valeur absente ou mal typée reste telle quelle pour le garde.
    """
    if not isinstance(data, Mapping):
        return {}
    out: dict[str, Any] = {}
    for bloc in BLOCS:
        value = data.get(bloc)
        out[bloc] = " ".join(value.split()) if isinstance(value, str) else value
    return out


def texts_sha256(texts: Mapping[str, Any]) -> str:
    """Empreinte des trois notes (référencée par le jury).

    Args:
        texts: Notes.

    Returns:
        Empreinte hexadécimale.
    """
    joined = "\n\n".join(f"{bloc}:{texts.get(bloc) or ''}" for bloc in BLOCS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ── Garde mécanique ─────────────────────────────────────────────────


def mechanical_check(
    texts: Mapping[str, Any], sources: AddendumSources, lang: str, cfg: AddendumConfig, denylist_path: Path,
) -> dict[str, Any]:
    """Contrôles mécaniques des trois notes.

    Args:
        texts: Notes normalisées.
        sources: Sources du brief.
        lang: Langue des notes.
        cfg: Réglages des notes.
        denylist_path: Denylist d'identité (fail-closed si absente).

    Returns:
        ``passed``, ``checks`` par bloc et ``reasons`` (codes).
    """
    min_words, max_words = cfg.words[lang]
    allowed_numbers = numbers_in(sources.all_text())
    reasons: list[str] = []
    checks: dict[str, Any] = {}
    for bloc in BLOCS:
        text = texts.get(bloc)
        if not isinstance(text, str) or not text.strip():
            reasons.append(f"{bloc}:missing")
            checks[bloc] = {"present": False}
            continue
        words = len(text.split())
        bloc_checks: dict[str, Any] = {"present": True, "words": words}
        if not min_words <= words <= max_words:
            reasons.append(f"{bloc}:length")
        cites = citation_hits(text)
        if cites:
            reasons.append(f"{bloc}:citation")
            bloc_checks["citation"] = cites
        banned = proscribed_hits(text, lang)
        if banned:
            reasons.append(f"{bloc}:vocabulary")
            bloc_checks["vocabulary"] = banned
        invented = sorted(numbers_in(text) - allowed_numbers)
        if invented:
            reasons.append(f"{bloc}:number_not_in_sources")
            bloc_checks["numbers_not_in_sources"] = invented
        if lang == "en":
            us = us_spelling_hits(text)
            if us:
                reasons.append(f"{bloc}:us_spelling")
                bloc_checks["us_spelling"] = us
            if french_residue_hits(text):
                reasons.append(f"{bloc}:french_residue")
        elif len(text) >= FR_UNACCENTED_MIN_CHARS and not ACCENTED.search(text):
            reasons.append(f"{bloc}:unaccented_french")
        checks[bloc] = bloc_checks
    identity = identity_check(
        [t for t in texts.values() if isinstance(t, str)], denylist_path
    )
    checks["identity_denylist"] = identity.passed
    if not identity.present:
        reasons.append("identity_denylist:missing")
    elif not identity.passed:
        reasons.append("identity_denylist:hit")
    return {"passed": not reasons, "checks": checks, "reasons": reasons}


# ── Juge ────────────────────────────────────────────────────────────


def judge_prompt(texts: Mapping[str, Any], sources: AddendumSources, lang: str, cfg: AddendumConfig) -> str:
    """Prompt du juge.

    Args:
        texts: Notes à juger.
        sources: Sources du brief.
        lang: Langue des notes.
        cfg: Réglages.

    Returns:
        Prompt complet.
    """
    return render_prompt(
        cfg.judge.prompt,
        lang_name=LANG_NAMES[lang],
        threshold=cfg.threshold,
        score_min=cfg.score_min,
        score_max=cfg.score_max,
        sources=writer_prompt_sources(sources),
        tuer=str(texts.get("tuer") or ""),
        ignore=str(texts.get("ignore") or ""),
        tester=str(texts.get("tester") or ""),
    )


def writer_prompt_sources(sources: AddendumSources) -> str:
    """Les sources du brief, telles que le rédacteur les a reçues, pour le juge.

    Args:
        sources: Sources du brief.

    Returns:
        Texte structuré.
    """
    return "\n".join(
        [
            f"ÉNONCÉ : {sources.formal_statement or '(absent)'}",
            "CONTRE-PREUVES :",
            bullets(list(sources.counter_evidence), empty="(aucune)"),
            f"OBJECTION DU CONTRADICTEUR : {sources.objection or '(aucune)'}",
            f"CONDITION DE RÉFUTATION : {sources.kill_condition or '(non liée)'}",
            "CONDITIONS DE VALIDITÉ :",
            bullets(list(sources.boundaries), empty="(aucune)"),
            "INCONNUES :",
            bullets(list(sources.unknowns), empty="(aucune)"),
            "LACUNES DE LA LITTÉRATURE :",
            bullets(list(sources.gaps), empty="(aucune)"),
            "QUESTIONS DES RELECTEURS :",
            bullets(list(sources.questions), empty="(aucune)"),
            "PRÉDICTIONS :",
            bullets(list(sources.predictions), empty="(aucune)"),
            "PROTOCOLE :",
            bullets(list(sources.protocol), empty="(aucun)"),
        ]
    )


def evaluate_judgement(data: Any, cfg: AddendumConfig) -> dict[str, Any]:
    """Transforme la sortie du juge en section ``judge`` du rapport, fail-closed.

    Args:
        data: Objet JSON rendu par le juge.
        cfg: Réglages (échelle, seuil).

    Returns:
        ``passed``, ``scores``, ``doubts``, ``raw_verdict``, ``reasons`` (codes).
    """
    section: dict[str, Any] = {"passed": False, "scores": {}, "threshold": cfg.threshold, "doubts": []}
    reasons: list[str] = []
    if not isinstance(data, Mapping):
        section["reasons"] = ["judge:malformed_output"]
        return section
    scores_raw = data.get("scores")
    scores: dict[str, Any] = {}
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
            if not cfg.score_min <= value <= cfg.score_max:
                reasons.append(f"judge:score_out_of_scale:{name}")
            elif value < cfg.threshold:
                reasons.append(f"judge:below_threshold:{name}")
    section["scores"] = scores
    doubts = data.get("doubts") or []
    if not isinstance(doubts, list):
        doubts = [str(doubts)]
    doubts = [str(d)[:300] for d in doubts if str(d).strip()]
    section["doubts"] = doubts
    if doubts:
        reasons.append("judge:doubt")
    verdict = data.get("verdict")
    section["raw_verdict"] = verdict if isinstance(verdict, str) else None
    if not isinstance(verdict, str) or verdict.strip().lower() != "accept":
        reasons.append("judge:verdict_not_accept")
    section["passed"] = not reasons
    section["reasons"] = reasons
    return section


# ── Orchestration ───────────────────────────────────────────────────


def _load(db_path: str | Path, brief_id: str, lang: str) -> tuple[dict | None, dict | None, int]:
    with narrative_db.connect(db_path) as conn:
        row = narrative_db.fetch_brief(conn, brief_id)
        published = narrative_db.published_addendum(conn, brief_id, lang)
        attempt = narrative_db.next_addendum_attempt(conn, brief_id, lang)
        kill = _kill_condition(conn, brief_id)
    if row is not None:
        row = dict(row)
        row["_kill_condition"] = kill
    return row, published, attempt


def _insert(db_path: str | Path, **fields: Any) -> int:
    with narrative_db.connect(db_path) as conn:
        return narrative_db.insert_addendum(conn, **fields)


async def produce_addendum(
    db_path: str | Path,
    brief_id: str,
    lang: str,
    *,
    config: NarrativeConfig,
    run_label: str,
) -> dict[str, Any]:
    """Rédige, garde et enregistre les notes d'un brief dans une langue.

    Idempotent : un brief qui a déjà des notes publiées dans cette langue n'est pas retouché, et
    les tentatives épuisées ne sont pas relancées.

    Args:
        db_path: Base de travail (garde de chemins appliquée par l'appelant).
        brief_id: Brief complet publié.
        lang: ``fr`` ou ``en``.
        config: Configuration de la couche (section ``addendum`` obligatoire).
        run_label: Étiquette de coût.

    Returns:
        ``status`` (``published``, ``rejected``, ``exhausted``, ``skipped``), ``attempts``,
        ``cost_usd`` et, le cas échéant, ``reasons``.
    """
    cfg = config.addendum
    if cfg is None:
        return {"status": "skipped", "reason": "addendum_not_configured"}
    row, published, attempt = await asyncio.to_thread(_load, db_path, brief_id, lang)
    if row is None or not narrative_db.is_full_published_brief(row):
        return {"status": "skipped", "reason": "not_a_full_published_brief"}
    if published is not None:
        return {"status": "published", "attempts": 0, "cost_usd": 0.0, "existing": True}
    if attempt > cfg.max_attempts:
        return {"status": "exhausted", "attempts": 0, "cost_usd": 0.0}
    sources = extract_sources(row, kill_condition=row.get("_kill_condition") or "")
    if not sources.has_substance():
        return {"status": "skipped", "reason": "insufficient_sources"}

    spent = 0.0
    last_reasons: list[str] = []
    while attempt <= cfg.max_attempts:
        meter = CostMeter()
        report: dict[str, Any] = {"lang": lang, "attempt": attempt}
        texts: dict[str, Any] = {}
        writer_model = judge_model = None
        try:
            result = await call_json(
                step=cfg.writer, node="addendum_writer", prompt=writer_prompt(sources, lang, cfg),
                config=config, db_path=db_path, run_label=run_label, brief_id=brief_id, meter=meter,
            )
            writer_model = result.model
            texts = normalise_texts(result.data)
            mechanical = mechanical_check(texts, sources, lang, cfg, config.identity_denylist_path)
            report["mechanical"] = mechanical
            if mechanical["passed"]:
                judged = await call_json(
                    step=cfg.judge, node="addendum_guard", prompt=judge_prompt(texts, sources, lang, cfg),
                    config=config, db_path=db_path, run_label=run_label, brief_id=brief_id, meter=meter,
                )
                judge_model = judged.model
                report["judge"] = evaluate_judgement(judged.data, cfg)
            reasons = list(mechanical["reasons"]) + list((report.get("judge") or {}).get("reasons") or [])
            if not mechanical["passed"]:
                reasons.append("judge:skipped_after_mechanical_failure")
        except Exception as exc:  # noqa: BLE001 — une panne d'appel rejette la tentative, rien de plus
            logger.error("narrative_addendum_call_failed", brief_id=brief_id, lang=lang, error=str(exc)[:300])
            reasons = [f"addendum:failed:{type(exc).__name__}"]
        status = "published" if not reasons else "rejected"
        report["decision"] = status
        report["reasons"] = reasons
        spent += meter.cost_usd
        await asyncio.to_thread(
            _insert, db_path,
            brief_id=brief_id, lang=lang, attempt=attempt, status=status,
            tuer=texts.get("tuer") if isinstance(texts.get("tuer"), str) else None,
            ignore=texts.get("ignore") if isinstance(texts.get("ignore"), str) else None,
            tester=texts.get("tester") if isinstance(texts.get("tester"), str) else None,
            guard_report_json=json.dumps(report, ensure_ascii=False),
            writer_model=writer_model, guard_model=judge_model,
            prompt_version=cfg.writer.prompt, guard_prompt_version=cfg.judge.prompt,
            body_sha256=texts_sha256(texts) if texts else None,
            cost_usd=meter.cost_usd, tokens_in=meter.tokens_in, tokens_out=meter.tokens_out,
            run_label=run_label,
        )
        logger.info("narrative_addendum_attempt", brief_id=brief_id, lang=lang, attempt=attempt, decision=status)
        if status == "published":
            return {"status": "published", "attempts": attempt, "cost_usd": round(spent, 6)}
        last_reasons = reasons
        attempt += 1
    return {"status": "rejected", "attempts": attempt - 1, "cost_usd": round(spent, 6), "reasons": last_reasons}


async def produce_all_langs(
    db_path: str | Path, brief_id: str, *, config: NarrativeConfig, run_label: str,
) -> dict[str, Any]:
    """Notes FR puis EN d'un brief.

    Args:
        db_path: Base de travail.
        brief_id: Brief.
        config: Configuration.
        run_label: Étiquette de coût.

    Returns:
        ``{"fr": …, "en": …}``.
    """
    return {
        lang: await produce_addendum(db_path, brief_id, lang, config=config, run_label=run_label)
        for lang in ("fr", "en")
    }
