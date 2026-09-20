"""Outils communs des tests de la couche narrative v2 (aucun appel réseau).

* ``FakeClient`` : client LLM qui passe par la vraie couche ``LLMClient``
  (mesure ``llm_calls``, contrôle de fin de génération) mais dont
  ``_complete`` rend des réponses scriptées ;
* récits synthétiques FR et EN qui passent les contrôles mécaniques ;
* configuration de test : denylist d'identité synthétique (jamais la vraie),
  délais et backoff courts ;
* base temporaire : schéma v1 par ``init_database`` (``TempDatabase``), puis
  briefs et hypothèses insérés en SQL.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from llm.client import LLMClient, LLMResponse
from narrative import llm as narrative_llm
from narrative.config import LLMStepConfig, NarrativeConfig, load_config
from narrative.guard import JUDGE_CONTROLS

#: Entrée synthétique de denylist (aucun lien avec la vraie liste).
DENYLIST_ENTRY = "Zorblax Quendimor"

YEAR = datetime.now(UTC).year + 15

_FR_SENTENCES = (
    "Lina Varesse posa la main sur la paroi tiède du bassin et sentit une vibration lente, presque régulière.",
    "Le capteur accroché au bord affichait une courbe qui montait chaque fois que la marée gagnait le chenal.",
    "Depuis trois saisons, l'équipe de la station de Port-Salant mesurait la réponse du matériau aux variations de sel.",
    "Tomas relisait les relevés du matin et comparait la porosité mesurée avec celle prévue par le modèle.",
    "Quand l'eau devenait plus salée, les pores s'ouvraient un peu et la pression sur la digue baissait.",
    "Personne ne parlait de miracle : le mécanisme restait fragile et chaque semaine apportait une surprise.",
    "Ce jour-là, une eau chargée de sable fin arriva du large et les pores commencèrent à se boucher.",
    "La courbe se figea, puis la paroi redevint rigide, exactement au moment où la houle forcissait.",
    "Lina nota l'heure, la hauteur de la vague et la couleur trouble de l'eau dans son carnet.",
    "Il faudrait filtrer les sédiments, ou accepter que la digue ne respire pas pendant les tempêtes.",
    "Au village, certains voulaient déjà couvrir toute la côte de ce béton, d'autres refusaient d'attendre.",
    "Tomas rappela que l'essai ne couvrait qu'un seul estuaire et que rien ne garantissait le même résultat ailleurs.",
)

_EN_SENTENCES = (
    "Lina Varesse laid her hand on the warm wall of the basin and felt a slow, almost regular tremor.",
    "The sensor clipped to the edge showed a curve that rose each time the tide filled the channel.",
    "For three seasons, the team at the Port-Salant station had measured how the material answered changes in salt.",
    "Tomas reread the morning readings and compared the measured porosity with the value the model expected.",
    "When the water grew saltier, the pores opened slightly and the pressure on the sea wall fell.",
    "Nobody spoke of a miracle: the mechanism remained fragile and every week brought a surprise.",
    "That day, water heavy with fine sand arrived from the open sea and the pores began to clog.",
    "The curve froze, then the wall turned rigid again, just as the swell was building.",
    "Lina noted the time, the height of the wave and the cloudy colour of the water in her notebook.",
    "The sediment would have to be filtered, or people would have to accept a wall that could not breathe in storms.",
    "In the village, some already wanted the whole coast covered in this concrete, while others refused to wait.",
    "Tomas pointed out that the trial covered a single estuary and that nothing guaranteed the same result elsewhere.",
)


def _body(sentences: Sequence[str], rounds: int = 3) -> str:
    paragraphs = []
    for _ in range(rounds):
        paragraphs.append(" ".join(sentences[:6]))
        paragraphs.append(" ".join(sentences[6:]))
    return "\n\n".join(paragraphs)


def good_story_fr(**overrides: Any) -> dict[str, Any]:
    """Récit FR valide (≈ 600 mots, année dans la fenêtre).

    Args:
        **overrides: Champs à remplacer.

    Returns:
        Objet JSON du rédacteur.
    """
    story = {
        "title": "La saison où la digue apprit à respirer",
        "year": YEAR,
        "place": "Port-Salant, un estuaire fictif de la façade atlantique",
        "body_markdown": _body(_FR_SENTENCES),
        "mechanism": "Un matériau dont les pores s'ouvrent quand la salinité augmente laisse passer l'eau et réduit la pression sur l'ouvrage.",
        "limit_or_risk": "Les sédiments fins bouchent les pores et la paroi redevient rigide pendant les tempêtes.",
    }
    story.update(overrides)
    return story


def good_story_en(**overrides: Any) -> dict[str, Any]:
    """Traduction EN valide (anglais britannique, ≈ 650 mots).

    Args:
        **overrides: Champs à remplacer.

    Returns:
        Objet JSON du traducteur.
    """
    story = {
        "title": "The season the sea wall learnt to breathe",
        "place": "Port-Salant, a fictional estuary on the Atlantic coast",
        "body_markdown": _body(_EN_SENTENCES),
        "mechanism": "A material whose pores open as salinity rises lets water through and lowers the pressure on the structure.",
        "limit_or_risk": "Fine sediment clogs the pores and the wall turns rigid again during storms.",
    }
    story.update(overrides)
    return story


def judge_verdict(
    score: int = 9,
    *,
    verdict: str = "accept",
    doubts: Sequence[str] = (),
    overrides: Mapping[str, Any] | None = None,
    controls: Mapping[str, Any] | None = None,
    drop_controls: bool = False,
) -> dict[str, Any]:
    """Verdict du juge, au format ``story_guard_v4`` (constats et contrôles).

    Args:
        score: Note de tous les critères.
        verdict: ``accept`` ou ``reject``.
        doubts: Doutes exprimés.
        overrides: Notes à remplacer par critère.
        controls: Réponses de contrôle à remplacer.
        drop_controls: Omettre entièrement le bloc ``controles``.

    Returns:
        Objet JSON du juge.
    """
    scores = {
        name: score
        for name in (
            "fidelity",
            "no_real_entities",
            "no_promise_or_advice",
            "not_claimed_proven",
            "limit_present",
            "readable_at_15",
            "constitution_exclusions",
        )
    }
    scores.update(overrides or {})
    verdict_json: dict[str, Any] = {
        "constats": {"objet_du_brief": "un matériau poreux", "objet_du_récit": "le même matériau"},
        "scores": scores,
        "doubts": list(doubts),
        "verdict": verdict,
        "reasons": ["ok"],
    }
    if not drop_controls:
        answers = {
            name: ("non" if unfavourable == "oui" else "oui")
            for name, (unfavourable, _) in JUDGE_CONTROLS.items()
        }
        answers.update(controls or {})
        verdict_json["controles"] = answers
    return verdict_json


Reply = str | dict[str, Any] | BaseException | tuple[str, str]
Responder = Callable[[int], Reply]


class FakeClient(LLMClient):
    """Client scripté ; ``_complete`` rend la réponse suivante du script.

    Une réponse peut être un objet (sérialisé en JSON), du texte brut, une
    exception à lever, ou un couple ``(texte, finish_reason)``.
    """

    provider = "mock"

    def __init__(self, agent: str, script: FakeScript) -> None:
        """Construit le client d'un agent.

        Args:
            agent: Nom d'agent de l'étape (``story_writer``…).
            script: Script partagé.
        """
        self.agent = agent
        self.script = script

    async def _complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
        json_mode: bool,
    ) -> LLMResponse:
        reply = self.script.next(self.agent, messages[-1]["content"])
        if self.script.delay:
            await asyncio.sleep(self.script.delay.get(self.agent, 0.0))
        finish = "stop"
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, tuple):
            reply, finish = reply
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return LLMResponse(
            content=content,
            input_tokens=1000,
            output_tokens=500,
            model="mock",
            provider="mock",
            finish_reason=finish,
            requested_model="mock",
        )


class FakeScript:
    """Réponses par agent : liste consommée dans l'ordre, dernière répétée.

    Attributes:
        calls: Appels reçus, ``(agent, prompt)``.
        delay: Pause par agent avant de répondre (s).
    """

    def __init__(self, replies: Mapping[str, Sequence[Reply] | Responder], delay: Mapping[str, float] | None = None) -> None:
        """Construit le script.

        Args:
            replies: Agent → liste de réponses ou fonction ``n → réponse``.
            delay: Pause par agent.
        """
        self.replies = dict(replies)
        self.calls: list[tuple[str, str]] = []
        self.delay = dict(delay or {})

    def next(self, agent: str, prompt: str) -> Reply:
        """Réponse suivante d'un agent.

        Args:
            agent: Nom d'agent.
            prompt: Prompt reçu.

        Returns:
            Réponse scriptée.
        """
        index = sum(1 for name, _ in self.calls if name == agent)
        self.calls.append((agent, prompt))
        source = self.replies[agent]
        if callable(source):
            return source(index)
        return source[min(index, len(source) - 1)]

    def count(self, agent: str) -> int:
        """Nombre d'appels reçus par un agent.

        Args:
            agent: Nom d'agent.

        Returns:
            Nombre d'appels.
        """
        return sum(1 for name, _ in self.calls if name == agent)

    def factory(self) -> Callable[[LLMStepConfig], LLMClient]:
        """Fabrique à passer à ``narrative.llm.set_client_factory``.

        Returns:
            Fabrique.
        """
        return lambda step: FakeClient(step.agent, self)


class use_script:
    """Pose la fabrique de client scriptée le temps d'un bloc."""

    def __init__(self, script: FakeScript) -> None:
        """Mémorise le script.

        Args:
            script: Script à poser.
        """
        self.script = script
        self.previous: Any = None

    def __enter__(self) -> FakeScript:
        self.previous = narrative_llm.set_client_factory(self.script.factory())
        return self.script

    def __exit__(self, *exc: object) -> None:
        narrative_llm.set_client_factory(self.previous)


def make_config(tmp: Path, *, denylist: bool = True, **changes: Any) -> NarrativeConfig:
    """Configuration réelle, denylist synthétique et délais courts.

    Args:
        tmp: Répertoire temporaire.
        denylist: Créer la denylist synthétique (sinon chemin absent).
        **changes: Autres champs à remplacer.

    Returns:
        Configuration de test.
    """
    path = tmp / "identity_denylist.txt"
    if denylist:
        path.write_text(f"# synthétique\n{DENYLIST_ENTRY}\n", encoding="utf-8")
    base = load_config()
    values: dict[str, Any] = {
        "identity_denylist_path": path,
        "retry_base_delay_s": 0.0,
        "retry_max_delay_s": 0.0,
        "llm_call_timeout_s": 10.0,
        "layer_timeout_s": 30.0,
        "tail_timeout_s": 10.0,
        "backfill_rate_limit_s": 0.0,
    }
    values.update(changes)
    return base.with_changes(**values)


# ── Base ────────────────────────────────────────────────────────────


def sharpened(domains: Sequence[str], title: str = "Titre scientifique") -> dict[str, Any]:
    """Blob ``sharpened_data`` minimal et réaliste.

    Args:
        domains: Domaines de la collision.
        title: Titre.

    Returns:
        Blob.
    """
    return {
        "title": title,
        "formal_statement": "Si la salinité augmente, la porosité du matériau augmente et la pression baisse.",
        "domains": list(domains),
        "proposed_mechanism": {
            "causal_chain": ["La salinité ouvre les pores.", "Les pores ouverts laissent passer l'eau."],
            "key_assumptions": ["Le matériau reste stable."],
            "known_unknowns": ["Effet des sédiments fins."],
        },
        "falsifiable_predictions": [
            {"prediction": "La pression baisse de 20 %.", "quantitative_bound": "entre 15 et 25 %"}
        ],
        "boundary_conditions": [{"condition": "Eau peu chargée", "justification": "Les pores se bouchent."}],
    }


def insert_brief(
    db_path: Path,
    brief_id: str,
    *,
    status: str = "complete",
    is_stub: int = 0,
    hypothesis_id: str | None = None,
    domains: Sequence[str] = ("Hydrology", "Materials Chemistry"),
    with_vulgarisation: bool = True,
) -> None:
    """Insère une ligne ``briefs``.

    Args:
        db_path: Base.
        brief_id: Identifiant.
        status: Statut.
        is_stub: Stub.
        hypothesis_id: ``briefs.hypothesis_id`` (par défaut l'identifiant du brief).
        domains: Domaines.
        with_vulgarisation: Renseigner ``vulgarization_data``.
    """
    vulg = {"title_fr": "Titre", "hypothesis_in_brief": "Résumé.", "why_it_matters": "Enjeu."}
    grounding = {"counter_evidence": [{"finding": "Les sédiments réduisent l'effet.", "severity": "minor", "doi": "10.1/x", "title": "T"}]}
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO briefs (id, hypothesis_id, status, is_stub, sharpened_data, vulgarization_data, "
            "grounding_data, panel_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                brief_id,
                hypothesis_id or brief_id,
                status,
                is_stub,
                json.dumps(sharpened(domains)),
                json.dumps(vulg) if with_vulgarisation else None,
                json.dumps(grounding),
                json.dumps({"meta_review": {"key_disagreements": ["Portée limitée."]}}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def insert_hypothesis(
    db_path: Path,
    hypothesis_id: str,
    *,
    summary: str,
    domain_a: tuple[str, str | None, Sequence[float] | None],
    domain_b: tuple[str, str | None, Sequence[float] | None],
    generated_at: str = "2026-09-01T00:00:00",
) -> None:
    """Insère une ligne ``hypotheses`` (collision et pont).

    Args:
        db_path: Base.
        hypothesis_id: Identifiant.
        summary: ``bridge_json.summary``.
        domain_a: ``(nom, parent_domain, embedding)``.
        domain_b: ``(nom, parent_domain, embedding)``.
        generated_at: Date de génération.
    """

    def domain(item: tuple[str, str | None, Sequence[float] | None]) -> dict[str, Any]:
        name, parent, embedding = item
        return {"name": name, "parent_domain": parent, "embedding": list(embedding) if embedding else None}

    collision = {"domain_a": domain(domain_a), "domain_b": domain(domain_b), "distance_score": 0.5}
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO hypotheses (id, generated_at, genome_version, collision_json, bridge_json, "
            "kill_condition) VALUES (?, ?, ?, ?, ?, ?)",
            (
                hypothesis_id,
                generated_at,
                "test",
                json.dumps(collision),
                json.dumps({"summary": summary, "mechanism": "m", "type": "t"}),
                "k",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def rows(db_path: Path, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Exécute une requête de lecture.

    Args:
        db_path: Base.
        sql: Requête.
        params: Paramètres.

    Returns:
        Lignes sous forme de dictionnaires.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
