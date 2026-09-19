"""Sous-graphe narratif et nœud d'enveloppe ``narrative_layer``.

Le sous-graphe a son propre état (``NarrativeState``) et se compile à part.
Le graphe Post-Fire ne le voit qu'à travers un nœud, ``narrative_layer``,
câblé après ``validate_brief`` par un bloc additif de
``graph/post_fire_pipeline.py`` ; ``PostFireState`` ne change pas.

::

    story_writer → story_guard ─published→ story_translate_en → story_guard_en
         ↑______rejected (≤ 2 nouvelles)┘│         ↑_____rejected (≤ 2)____┘│
                        épuisé / échec ──┴──→ theme_tagger ←── publié / épuisé
                                              → brief_link → neighbours_refresh → END

Garanties :

* le brief est déjà ``complete`` quand la couche démarre ; elle ne lit que
  l'identifiant du brief et de l'hypothèse dans l'état, n'écrit que dans les
  tables ``v2_*`` et ne peut pas le dépublier ;
* chaque nœud attrape ses erreurs ; l'enveloppe attrape tout le reste, borne
  la durée par ``asyncio.wait_for`` et rend toujours ``{}`` — une exception
  qui remonterait de ``run_post_fire_pipeline`` ferait passer en ``failed``
  une collision sur mesure déjà publiée (``api/custom_runner.py``) ;
* un échec n'enlève que l'étage fiction : après un échec ou un délai
  dépassé, les brouillons ouverts sont rejetés et les étapes mécaniques
  (thèmes, lien, voisines) rejouées sous leur propre délai.
"""

from __future__ import annotations

import asyncio
import operator
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph

from logging_config import get_logger, log_context
from narrative import mechanical, story
from narrative.config import NarrativeConfig, get_config
from narrative.safety import assert_safe_write_path
from storage import narrative_db

logger = get_logger("narrative.graph")


class NarrativeState(TypedDict, total=False):
    """État propre au sous-graphe narratif.

    Attributes:
        brief_id: Brief publié.
        hypothesis_id: Hypothèse d'origine (état du post-fire), si connue.
        run_id: Run L0 d'origine, pour les journaux.
        db_path: Base résolue et contrôlée par l'enveloppe.
        run_label: Étiquette du registre de coût.
        write_stories: Produire les récits (le backfill mécanique s'en passe).
        refresh_neighbours: Recalculer le maillage en fin de sous-graphe.
        fr_status: Statut de l'étape FR (voir ``narrative.story``).
        fr_story_id: Dernière ligne FR (brouillon, publiée ou rejetée).
        fr_attempt: Dernière tentative FR.
        fr_tries: Passages dans ``story_writer`` pendant cette exécution
            (borne la boucle même si une tentative n'a pas pu être écrite).
        en_status: Statut de l'étape EN.
        en_story_id: Dernière ligne EN.
        en_attempt: Dernière tentative EN.
        en_tries: Passages dans ``story_translate_en`` pendant cette exécution.
        themes: Slugs écrits par ``theme_tagger``.
        link_method: Méthode écrite par ``brief_link``.
        neighbours: Indicateurs de ``neighbours_refresh``.
        events: Journal des étapes (réducteur additif).
    """

    brief_id: str
    hypothesis_id: str | None
    run_id: str | None
    db_path: str
    run_label: str
    write_stories: bool
    refresh_neighbours: bool
    fr_status: str
    fr_story_id: int | None
    fr_attempt: int
    fr_tries: int
    en_status: str
    en_story_id: int | None
    en_attempt: int
    en_tries: int
    themes: list[str]
    link_method: str | None
    neighbours: dict[str, Any]
    events: Annotated[list[str], operator.add]


def _config() -> NarrativeConfig:
    return get_config()


# ── Nœuds ───────────────────────────────────────────────────────────


async def node_story_writer(state: NarrativeState) -> dict[str, Any]:
    """Rédige une tentative FR (ou constate qu'il n'y a rien à faire).

    Args:
        state: État narratif.

    Returns:
        Mise à jour ``fr_*``.
    """
    if not state.get("write_stories", True):
        return {"fr_status": story.SKIPPED, "events": ["story_writer:skipped"]}
    tries = int(state.get("fr_tries") or 0) + 1
    try:
        result = await story.write_step(
            brief_id=state["brief_id"],
            config=_config(),
            db_path=state["db_path"],
            run_label=state["run_label"],
        )
    except Exception as exc:  # noqa: BLE001 — l'étage fiction tombe, rien d'autre
        logger.error("narrative_story_writer_node_failed", brief_id=state.get("brief_id"), error=str(exc)[:300])
        return {"fr_status": story.EXHAUSTED, "fr_tries": tries, "events": ["story_writer:error"]}
    return {
        "fr_status": result.status,
        "fr_story_id": result.story_id,
        "fr_attempt": result.attempt,
        "fr_tries": tries,
        "events": [f"story_writer:{result.status}:{result.attempt}"],
    }


async def node_story_guard(state: NarrativeState) -> dict[str, Any]:
    """Statue sur le brouillon FR.

    Args:
        state: État narratif.

    Returns:
        Mise à jour ``fr_status``.
    """
    if state.get("fr_status") != story.DRAFT or not state.get("fr_story_id"):
        return {"events": [f"story_guard:pass:{state.get('fr_status')}"]}
    try:
        result = await story.guard_step(
            brief_id=state["brief_id"],
            story_id=int(state["fr_story_id"]),
            lang="fr",
            config=_config(),
            db_path=state["db_path"],
            run_label=state["run_label"],
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.error("narrative_story_guard_node_failed", brief_id=state.get("brief_id"), error=str(exc)[:300])
        return {"fr_status": story.REJECTED, "events": ["story_guard:error"]}
    return {"fr_status": result.status, "events": [f"story_guard:{result.status}"]}


async def node_story_translate_en(state: NarrativeState) -> dict[str, Any]:
    """Traduit le récit FR publié en anglais britannique.

    Args:
        state: État narratif.

    Returns:
        Mise à jour ``en_*``.
    """
    tries = int(state.get("en_tries") or 0) + 1
    try:
        result = await story.translate_step(
            brief_id=state["brief_id"],
            source_story_id=state.get("fr_story_id"),
            config=_config(),
            db_path=state["db_path"],
            run_label=state["run_label"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("narrative_story_translate_node_failed", brief_id=state.get("brief_id"), error=str(exc)[:300])
        return {"en_status": story.EXHAUSTED, "en_tries": tries, "events": ["story_translate_en:error"]}
    return {
        "en_status": result.status,
        "en_story_id": result.story_id,
        "en_attempt": result.attempt,
        "en_tries": tries,
        "events": [f"story_translate_en:{result.status}:{result.attempt}"],
    }


async def node_story_guard_en(state: NarrativeState) -> dict[str, Any]:
    """Statue sur le brouillon EN (même garde, listes anglaises).

    Args:
        state: État narratif.

    Returns:
        Mise à jour ``en_status``.
    """
    if state.get("en_status") != story.DRAFT or not state.get("en_story_id"):
        return {"events": [f"story_guard_en:pass:{state.get('en_status')}"]}
    try:
        result = await story.guard_step(
            brief_id=state["brief_id"],
            story_id=int(state["en_story_id"]),
            lang="en",
            config=_config(),
            db_path=state["db_path"],
            run_label=state["run_label"],
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.error("narrative_story_guard_en_node_failed", brief_id=state.get("brief_id"), error=str(exc)[:300])
        return {"en_status": story.REJECTED, "events": ["story_guard_en:error"]}
    return {"en_status": result.status, "events": [f"story_guard_en:{result.status}"]}


async def node_theme_tagger(state: NarrativeState) -> dict[str, Any]:
    """Thèmes du brief (règle mécanique).

    Args:
        state: État narratif.

    Returns:
        Mise à jour ``themes``.
    """
    try:
        themes = await asyncio.to_thread(mechanical.tag_brief, state["db_path"], state["brief_id"], _config())
    except Exception as exc:  # noqa: BLE001
        logger.error("narrative_theme_tagger_failed", brief_id=state.get("brief_id"), error=str(exc)[:300])
        return {"events": ["theme_tagger:error"]}
    return {"themes": [slug for slug, _ in themes], "events": ["theme_tagger:done"]}


async def node_brief_link(state: NarrativeState) -> dict[str, Any]:
    """Lien brief ↔ hypothèse (``pipeline_state``).

    Args:
        state: État narratif.

    Returns:
        Mise à jour ``link_method``.
    """
    try:
        method = await asyncio.to_thread(
            mechanical.link_brief, state["db_path"], state["brief_id"], state.get("hypothesis_id")
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("narrative_brief_link_failed", brief_id=state.get("brief_id"), error=str(exc)[:300])
        return {"events": ["brief_link:error"]}
    return {"link_method": method, "events": [f"brief_link:{method or 'skipped'}"]}


async def node_neighbours_refresh(state: NarrativeState) -> dict[str, Any]:
    """Recalcule le maillage de toutes les idées publiées.

    Args:
        state: État narratif.

    Returns:
        Mise à jour ``neighbours``.
    """
    if not state.get("refresh_neighbours", True):
        return {"events": ["neighbours_refresh:skipped"]}
    try:
        summary = await asyncio.to_thread(mechanical.refresh_neighbours, state["db_path"], _config())
    except Exception as exc:  # noqa: BLE001
        logger.error("narrative_neighbours_refresh_failed", brief_id=state.get("brief_id"), error=str(exc)[:300])
        return {"events": ["neighbours_refresh:error"]}
    return {"neighbours": summary, "events": ["neighbours_refresh:done"]}


# ── Routage ─────────────────────────────────────────────────────────


def route_after_story_guard(state: NarrativeState) -> str:
    """Après le garde FR : traduire, réécrire, ou passer aux étapes mécaniques.

    Args:
        state: État narratif.

    Returns:
        Nœud suivant.
    """
    status = state.get("fr_status")
    if status == story.PUBLISHED:
        return "story_translate_en"
    limit = _config().max_attempts
    if (
        status == story.REJECTED
        and int(state.get("fr_attempt") or 0) < limit
        and int(state.get("fr_tries") or 0) < limit
    ):
        return "story_writer"
    return "theme_tagger"


def route_after_story_guard_en(state: NarrativeState) -> str:
    """Après le garde EN : retraduire ou passer aux étapes mécaniques.

    Args:
        state: État narratif.

    Returns:
        Nœud suivant.
    """
    limit = _config().translate_max_attempts
    if (
        state.get("en_status") == story.REJECTED
        and int(state.get("en_attempt") or 0) < limit
        and int(state.get("en_tries") or 0) < limit
    ):
        return "story_translate_en"
    return "theme_tagger"


def build_narrative_graph() -> StateGraph:
    """Sous-graphe narratif (non compilé).

    Returns:
        ``StateGraph(NarrativeState)`` câblé.
    """
    workflow = StateGraph(NarrativeState)
    workflow.add_node("story_writer", node_story_writer)
    workflow.add_node("story_guard", node_story_guard)
    workflow.add_node("story_translate_en", node_story_translate_en)
    workflow.add_node("story_guard_en", node_story_guard_en)
    workflow.add_node("theme_tagger", node_theme_tagger)
    workflow.add_node("brief_link", node_brief_link)
    workflow.add_node("neighbours_refresh", node_neighbours_refresh)

    workflow.set_entry_point("story_writer")
    workflow.add_edge("story_writer", "story_guard")
    workflow.add_conditional_edges(
        "story_guard",
        route_after_story_guard,
        {
            "story_translate_en": "story_translate_en",
            "story_writer": "story_writer",
            "theme_tagger": "theme_tagger",
        },
    )
    workflow.add_edge("story_translate_en", "story_guard_en")
    workflow.add_conditional_edges(
        "story_guard_en",
        route_after_story_guard_en,
        {"story_translate_en": "story_translate_en", "theme_tagger": "theme_tagger"},
    )
    workflow.add_edge("theme_tagger", "brief_link")
    workflow.add_edge("brief_link", "neighbours_refresh")
    workflow.add_edge("neighbours_refresh", END)
    return workflow


_compiled: Any = None


def compiled_narrative_graph() -> Any:
    """Sous-graphe compilé (une fois par processus).

    Returns:
        Graphe compilé.
    """
    global _compiled
    if _compiled is None:
        _compiled = build_narrative_graph().compile()
    return _compiled


# ── Enveloppe ───────────────────────────────────────────────────────


def _default_db_path() -> Path:
    from config import get_settings

    return Path(get_settings().db_path)


def _prepare(db_path: Path, brief_id: str) -> dict[str, Any] | None:
    """Crée le schéma ``v2_*`` si besoin et relit le brief.

    Args:
        db_path: Base contrôlée.
        brief_id: Brief.

    Returns:
        Ligne ``briefs`` ou ``None``.
    """
    with narrative_db.connect(db_path) as conn:
        narrative_db.ensure_narrative_schema(conn)
        return narrative_db.fetch_brief(conn, brief_id)


async def _recover(state: Mapping[str, Any], config: NarrativeConfig, reason: str) -> None:
    """Après un échec ou un délai dépassé : ferme les brouillons, rejoue la queue mécanique.

    Args:
        state: État initial du sous-graphe.
        config: Configuration.
        reason: Code de raison ajouté aux brouillons rejetés.
    """
    db_path, brief_id = state["db_path"], state["brief_id"]

    def close_drafts() -> int:
        with narrative_db.connect(db_path) as conn:
            return narrative_db.reject_open_drafts(conn, brief_id, reason)

    async def tail() -> None:
        closed = await asyncio.to_thread(close_drafts)
        if closed:
            logger.warning("narrative_open_drafts_rejected", brief_id=brief_id, count=closed, reason=reason)
        tail_state = dict(state)
        for node in (node_theme_tagger, node_brief_link, node_neighbours_refresh):
            await node(tail_state)  # type: ignore[arg-type]

    try:
        await asyncio.wait_for(tail(), timeout=config.tail_timeout_s)
    except Exception as exc:  # noqa: BLE001 — rien ne remonte de la couche
        logger.error(
            "narrative_layer_recovery_failed",
            brief_id=brief_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )


async def run_narrative_layer(
    *,
    brief_id: str,
    hypothesis_id: str | None = None,
    run_id: str | None = None,
    db_path: str | Path | None = None,
    run_label: str | None = None,
    write_stories: bool = True,
    refresh_neighbours: bool = True,
    config: NarrativeConfig | None = None,
) -> dict[str, Any]:
    """Exécute la couche narrative pour un brief. Ne lève jamais (hors annulation).

    Args:
        brief_id: Brief publié.
        hypothesis_id: Hypothèse d'origine (état du post-fire), si connue.
        run_id: Run d'origine, pour les journaux.
        db_path: Base ; par défaut ``SPORE_DB_PATH`` (``get_settings``).
        run_label: Étiquette de coût ; par défaut configuration ou
            ``SPORE_V2_RUN_LABEL``.
        write_stories: Produire les récits.
        refresh_neighbours: Recalculer le maillage à la fin.
        config: Configuration (par défaut ``get_config()``).

    Returns:
        Résumé : ``ran``, ``reason`` si la couche n'a rien fait, statuts
        ``fr_status`` / ``en_status``, thèmes, lien, indicateurs du maillage,
        ``failed`` si le sous-graphe a échoué.
    """
    summary: dict[str, Any] = {"brief_id": brief_id, "ran": False}
    try:
        config = config or get_config()
        resolved = assert_safe_write_path(db_path or _default_db_path(), what="db")
        row = await asyncio.to_thread(_prepare, resolved, brief_id)
        if not narrative_db.is_full_published_brief(row):
            summary["reason"] = "brief_not_published_full" if row else "brief_missing"
            logger.info("narrative_layer_skipped", brief_id=brief_id, reason=summary["reason"])
            return summary

        state: NarrativeState = {
            "brief_id": brief_id,
            "hypothesis_id": hypothesis_id,
            "run_id": run_id,
            "db_path": str(resolved),
            "run_label": run_label or config.effective_run_label(),
            "write_stories": write_stories,
            "refresh_neighbours": refresh_neighbours,
            "events": [],
        }
        summary["ran"] = True
        try:
            final = await asyncio.wait_for(
                compiled_narrative_graph().ainvoke(
                    state, config={"recursion_limit": config.recursion_limit}
                ),
                timeout=config.layer_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 — délai ou panne : l'étage fiction tombe
            reason = "layer_timeout" if isinstance(exc, TimeoutError) else f"layer_failed:{type(exc).__name__}"
            logger.error(
                "narrative_layer_subgraph_failed",
                brief_id=brief_id,
                reason=reason,
                error=str(exc)[:300],
            )
            summary["failed"] = reason
            await _recover(state, config, reason)
            return summary

        for key in ("fr_status", "en_status", "themes", "link_method", "neighbours", "events"):
            if key in final:
                summary[key] = final[key]
        logger.info(
            "narrative_layer_done",
            brief_id=brief_id,
            fr_status=final.get("fr_status"),
            en_status=final.get("en_status"),
            themes=final.get("themes"),
        )
    except Exception as exc:  # noqa: BLE001 — rien ne remonte de la couche
        summary["failed"] = f"layer_error:{type(exc).__name__}"
        logger.error(
            "narrative_layer_failed",
            brief_id=brief_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
    return summary


async def node_narrative_layer(state: Mapping[str, Any]) -> dict[str, Any]:
    """Nœud d'enveloppe câblé après ``validate_brief`` dans le graphe Post-Fire.

    Lit ``brief_id``, ``hypothesis_id``, ``run_id`` et ``is_stub`` dans
    ``PostFireState`` ; ne modifie jamais l'état du post-fire.

    Args:
        state: ``PostFireState`` final (lecture seule).

    Returns:
        ``{}``, toujours.
    """
    try:
        brief_id = state.get("brief_id")
        if not brief_id or state.get("is_stub"):
            logger.info("narrative_layer_not_applicable", brief_id=brief_id, is_stub=bool(state.get("is_stub")))
            return {}
        with log_context(
            node="narrative_layer",
            run_id=state.get("run_id"),
            hypothesis_id=state.get("hypothesis_id"),
            brief_id=brief_id,
        ):
            await run_narrative_layer(
                brief_id=str(brief_id),
                hypothesis_id=state.get("hypothesis_id"),
                run_id=state.get("run_id"),
            )
    except Exception as exc:  # noqa: BLE001 — la publication du brief ne dépend jamais de la couche
        logger.error("narrative_layer_node_failed", error_type=type(exc).__name__, error=str(exc)[:300])
    return {}
