"""Étapes « récit » de la couche : rédaction, garde, traduction, persistées.

Chaque tentative laisse une ligne ``v2_stories`` : ``draft`` à la rédaction
(ou à la traduction), puis ``published`` ou ``rejected`` au garde. Une
rédaction ou une traduction en échec laisse une ligne ``rejected`` avec son
rapport : l'essai compte dans les trois tentatives du contrat et son coût
reste visible.

Les fonctions ne lèvent pas : elles rendent un statut. Les écritures en base
passent par ``asyncio.to_thread`` (connexions courtes, aucune transaction
ouverte pendant un appel LLM).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from logging_config import get_logger
from narrative.checks import coerce_year
from narrative.config import NarrativeConfig
from narrative.guard import failure_report, guard_story
from narrative.inputs import StoryInputs, extract_inputs
from narrative.llm import CostMeter
from narrative.writer import StoryText, body_sha256, translate_story, write_story
from storage import narrative_db

logger = get_logger("narrative.story")

#: Statuts d'étape rendus aux nœuds.
PUBLISHED = "published"
REJECTED = "rejected"
DRAFT = "draft"
EXHAUSTED = "exhausted"
SKIPPED = "skipped"


@dataclass(frozen=True)
class StepResult:
    """Issue d'une étape de récit.

    Attributes:
        status: ``draft``, ``published``, ``rejected``, ``exhausted`` ou
            ``skipped``.
        story_id: Ligne ``v2_stories`` concernée.
        attempt: Numéro de tentative (0 si aucune tentative n'a eu lieu).
    """

    status: str
    story_id: int | None = None
    attempt: int = 0


def _load_inputs(db_path: str | Path, brief_id: str) -> StoryInputs | None:
    with narrative_db.connect(db_path) as conn:
        row = narrative_db.fetch_brief(conn, brief_id)
    return extract_inputs(row) if row else None


async def load_inputs(db_path: str | Path, brief_id: str) -> StoryInputs | None:
    """Entrées du récit, relues dans la ligne ``briefs``.

    Args:
        db_path: Base.
        brief_id: Brief.

    Returns:
        Entrées, ou ``None`` si le brief n'existe pas.
    """
    return await asyncio.to_thread(_load_inputs, db_path, brief_id)


def _story_columns(text: StoryText) -> dict[str, Any]:
    story = text.story
    year = coerce_year(story.get("year"))

    def as_text(name: str) -> str | None:
        value = story.get(name)
        return value if isinstance(value, str) else None

    return {
        "title": as_text("title"),
        "story_year": year,
        "story_place": as_text("place"),
        "body_md": as_text("body_markdown"),
        "mechanism": as_text("mechanism"),
        "limit_staged": as_text("limit_or_risk"),
        "body_sha256": body_sha256(story.get("body_markdown")),
    }


def row_as_story(row: Mapping[str, Any]) -> dict[str, Any]:
    """Relit une ligne ``v2_stories`` sous la forme contrôlée par le garde.

    Args:
        row: Ligne ``v2_stories``.

    Returns:
        ``title``, ``year``, ``place``, ``body_markdown``, ``mechanism``,
        ``limit_or_risk``.
    """
    return {
        "title": row.get("title"),
        "year": row.get("story_year"),
        "place": row.get("story_place"),
        "body_markdown": row.get("body_md"),
        "mechanism": row.get("mechanism"),
        "limit_or_risk": row.get("limit_staged"),
    }


def _plan_attempt(db_path: str | Path, brief_id: str, lang: str, max_attempts: int) -> tuple[str, int, int | None]:
    """Décide s'il reste une tentative pour ``(brief, langue)``.

    Returns:
        ``(statut, tentative, id publié)`` : ``published`` si un récit publié
        existe, ``exhausted`` si les tentatives sont épuisées, sinon ``draft``
        avec le numéro de la prochaine tentative.
    """
    with narrative_db.connect(db_path) as conn:
        published = narrative_db.published_story(conn, brief_id, lang)
        if published is not None:
            return PUBLISHED, int(published["attempt"]), int(published["id"])
        attempt = narrative_db.next_attempt(conn, brief_id, lang)
    if attempt > max_attempts:
        return EXHAUSTED, attempt - 1, None
    return DRAFT, attempt, None


def _insert(db_path: str | Path, fields: dict[str, Any]) -> int:
    with narrative_db.connect(db_path) as conn:
        return narrative_db.insert_story(conn, **fields)


async def _record_failure(
    *,
    db_path: str | Path,
    brief_id: str,
    lang: str,
    attempt: int,
    reason: str,
    meter: CostMeter,
    config: NarrativeConfig,
    run_label: str,
    prompt_version: str,
    source_story_id: int | None = None,
) -> StepResult:
    fields = {
        "brief_id": brief_id,
        "lang": lang,
        "attempt": attempt,
        "status": REJECTED,
        "guard_report_json": json.dumps(failure_report(config, lang, [reason]), ensure_ascii=False),
        "prompt_version": prompt_version,
        "guard_prompt_version": config.guard.llm.prompt,
        "cost_usd": round(meter.cost_usd, 8),
        "tokens_in": meter.tokens_in,
        "tokens_out": meter.tokens_out,
        "run_label": run_label,
        "source_story_id": source_story_id,
    }
    try:
        story_id = await asyncio.to_thread(_insert, db_path, fields)
    except (sqlite3.Error, ValueError) as exc:
        logger.error("narrative_story_failure_not_recorded", brief_id=brief_id, lang=lang, error=str(exc)[:300])
        story_id = None
    return StepResult(REJECTED, story_id, attempt)


async def write_step(
    *,
    brief_id: str,
    config: NarrativeConfig,
    db_path: str | Path,
    run_label: str,
) -> StepResult:
    """Rédige une tentative FR et l'enregistre en brouillon.

    Args:
        brief_id: Brief.
        config: Configuration.
        db_path: Base.
        run_label: Étiquette de coût.

    Returns:
        ``published`` (déjà publié, rien à faire), ``exhausted``, ``draft``
        (brouillon à contrôler) ou ``rejected`` (rédaction en échec).
    """
    status, attempt, published_id = await asyncio.to_thread(
        _plan_attempt, db_path, brief_id, "fr", config.max_attempts
    )
    if status != DRAFT:
        return StepResult(status, published_id, attempt)

    meter = CostMeter()
    try:
        inputs = await load_inputs(db_path, brief_id)
        if inputs is None or not inputs.has_substance():
            raise ValueError("brief sans hypothèse exploitable")
        text = await write_story(
            inputs, config=config, db_path=db_path, run_label=run_label, meter=meter
        )
    except Exception as exc:  # noqa: BLE001 — une rédaction en échec est une tentative rejetée
        logger.warning(
            "narrative_story_writer_failed",
            brief_id=brief_id,
            attempt=attempt,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return await _record_failure(
            db_path=db_path,
            brief_id=brief_id,
            lang="fr",
            attempt=attempt,
            reason=f"writer:failed:{type(exc).__name__}",
            meter=meter,
            config=config,
            run_label=run_label,
            prompt_version=config.writer.prompt,
        )

    fields = {
        "brief_id": brief_id,
        "lang": "fr",
        "attempt": attempt,
        "status": DRAFT,
        **_story_columns(text),
        "writer_model": text.model,
        "prompt_version": text.prompt_version,
        "guard_prompt_version": config.guard.llm.prompt,
        "cost_usd": round(meter.cost_usd, 8),
        "tokens_in": meter.tokens_in,
        "tokens_out": meter.tokens_out,
        "run_label": run_label,
    }
    try:
        story_id = await asyncio.to_thread(_insert, db_path, fields)
    except (sqlite3.Error, ValueError) as exc:
        logger.error("narrative_story_draft_not_recorded", brief_id=brief_id, error=str(exc)[:300])
        return StepResult(REJECTED, None, attempt)
    logger.info("narrative_story_drafted", brief_id=brief_id, lang="fr", attempt=attempt, story_id=story_id)
    return StepResult(DRAFT, story_id, attempt)


def _get_story(db_path: str | Path, story_id: int) -> dict[str, Any] | None:
    with narrative_db.connect(db_path) as conn:
        return narrative_db.get_story(conn, story_id)


def _finalize(db_path: str | Path, story_id: int, fields: dict[str, Any]) -> bool:
    with narrative_db.connect(db_path) as conn:
        try:
            return narrative_db.finalize_story(conn, story_id, **fields)
        except sqlite3.IntegrityError:
            # Un autre récit est déjà publié pour cette langue : celui-ci est
            # rejeté, l'index partiel reste la dernière barrière.
            report = json.loads(fields.get("guard_report_json") or "{}")
            report["decision"] = REJECTED
            report.setdefault("reasons", []).append("db:already_published")
            return narrative_db.finalize_story(
                conn,
                story_id,
                status=REJECTED,
                guard_report_json=json.dumps(report, ensure_ascii=False),
            )


async def guard_step(
    *,
    brief_id: str,
    story_id: int,
    lang: str,
    config: NarrativeConfig,
    db_path: str | Path,
    run_label: str,
) -> StepResult:
    """Contrôle un brouillon et statue (``published`` ou ``rejected``).

    Args:
        brief_id: Brief.
        story_id: Brouillon à contrôler.
        lang: ``fr`` ou ``en``.
        config: Configuration.
        db_path: Base.
        run_label: Étiquette de coût.

    Returns:
        Décision ; ``rejected`` pour toute erreur (fail-closed).
    """
    node = "story_guard" if lang == "fr" else "story_guard_en"
    row = await asyncio.to_thread(_get_story, db_path, story_id)
    if row is None or row.get("status") != DRAFT:
        logger.warning("narrative_guard_no_draft", brief_id=brief_id, story_id=story_id)
        return StepResult(REJECTED, story_id, int(row["attempt"]) if row else 0)

    inputs = await load_inputs(db_path, brief_id)
    if inputs is None:
        outcome_report = failure_report(config, lang, ["guard:brief_missing"])
        decision, judge_model, meter = REJECTED, None, CostMeter()
    else:
        outcome = await guard_story(
            row_as_story(row),
            inputs,
            lang=lang,
            config=config,
            db_path=db_path,
            run_label=run_label,
            node=node,
        )
        outcome_report, decision = outcome.report, outcome.decision
        judge_model, meter = outcome.judge_model, outcome.meter

    fields = {
        "status": decision,
        "guard_report_json": json.dumps(outcome_report, ensure_ascii=False),
        "guard_model": judge_model,
        "guard_prompt_version": config.guard.llm.prompt,
        "cost_usd": round(float(row.get("cost_usd") or 0.0) + meter.cost_usd, 8),
        "tokens_in": int(row.get("tokens_in") or 0) + meter.tokens_in,
        "tokens_out": int(row.get("tokens_out") or 0) + meter.tokens_out,
    }
    try:
        changed = await asyncio.to_thread(_finalize, db_path, story_id, fields)
    except (sqlite3.Error, ValueError) as exc:
        logger.error("narrative_guard_decision_not_recorded", brief_id=brief_id, error=str(exc)[:300])
        return StepResult(REJECTED, story_id, int(row["attempt"]))
    if not changed:
        logger.warning("narrative_guard_draft_already_closed", brief_id=brief_id, story_id=story_id)
    # La base fait foi : un brouillon fermé entre-temps (délai global) reste rejeté.
    final = await asyncio.to_thread(_get_story, db_path, story_id)
    status = PUBLISHED if final and final.get("status") == PUBLISHED else REJECTED
    return StepResult(status, story_id, int(row["attempt"]))


async def translate_step(
    *,
    brief_id: str,
    source_story_id: int | None,
    config: NarrativeConfig,
    db_path: str | Path,
    run_label: str,
) -> StepResult:
    """Traduit le récit FR publié et enregistre le brouillon EN.

    Args:
        brief_id: Brief.
        source_story_id: Récit FR publié (relu en base).
        config: Configuration.
        db_path: Base.
        run_label: Étiquette de coût.

    Returns:
        ``published`` (EN déjà publié), ``exhausted``, ``skipped`` (pas de
        source publiée), ``draft`` ou ``rejected``.
    """
    status, attempt, published_id = await asyncio.to_thread(
        _plan_attempt, db_path, brief_id, "en", config.translate_max_attempts
    )
    if status != DRAFT:
        return StepResult(status, published_id, attempt)

    source = await asyncio.to_thread(_get_story, db_path, source_story_id) if source_story_id else None
    if source is None or source.get("status") != PUBLISHED or source.get("lang") != "fr":
        logger.warning("narrative_translate_no_source", brief_id=brief_id, source_story_id=source_story_id)
        return StepResult(SKIPPED, None, 0)

    meter = CostMeter()
    try:
        text = await translate_story(
            source,
            brief_id=brief_id,
            config=config,
            db_path=db_path,
            run_label=run_label,
            meter=meter,
        )
    except Exception as exc:  # noqa: BLE001 — une traduction en échec est une tentative rejetée
        logger.warning(
            "narrative_story_translate_failed",
            brief_id=brief_id,
            attempt=attempt,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return await _record_failure(
            db_path=db_path,
            brief_id=brief_id,
            lang="en",
            attempt=attempt,
            reason=f"translate:failed:{type(exc).__name__}",
            meter=meter,
            config=config,
            run_label=run_label,
            prompt_version=config.translate.prompt,
            source_story_id=int(source["id"]),
        )

    fields = {
        "brief_id": brief_id,
        "lang": "en",
        "attempt": attempt,
        "status": DRAFT,
        **_story_columns(text),
        "source_story_id": int(source["id"]),
        "writer_model": text.model,
        "prompt_version": text.prompt_version,
        "guard_prompt_version": config.guard.llm.prompt,
        "cost_usd": round(meter.cost_usd, 8),
        "tokens_in": meter.tokens_in,
        "tokens_out": meter.tokens_out,
        "run_label": run_label,
    }
    try:
        story_id = await asyncio.to_thread(_insert, db_path, fields)
    except (sqlite3.Error, ValueError) as exc:
        logger.error("narrative_story_draft_not_recorded", brief_id=brief_id, error=str(exc)[:300])
        return StepResult(REJECTED, None, attempt)
    logger.info("narrative_story_drafted", brief_id=brief_id, lang="en", attempt=attempt, story_id=story_id)
    return StepResult(DRAFT, story_id, attempt)
