"""LangGraph subgraph for the SPORE post-fire pipeline.

Pipeline:
  Literature Grounding → Hypothesis Sharpening → Experimental Protocol
  → Multi-Reviewer Panel → Meta-Reviewer → (revision loop or Brief Generator)

Conditional edges:
  - After grounding: kill if already_proven or fatal counter-evidence
  - After meta-reviewer: revise_and_resubmit loops back to sharpening (max 2)
"""

from datetime import date
from typing import Any, Optional, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, END

from agents.literature_grounding import (
    literature_grounding_agent,
    GroundingInput,
    GroundingOutput,
)
from agents.hypothesis_sharpening import (
    hypothesis_sharpening_agent,
    SharpeningInput,
    SharpeningOutput,
)
from agents.experimental_protocol import (
    experimental_protocol_agent,
    ProtocolOutput,
)
from agents.multi_reviewer_panel import (
    full_panel_review,
    selection_threshold,
    PanelOutput,
    SELECTION_FLOOR,
    SELECTION_WINDOW,
)
from agents.research_brief_generator import save_brief
from agents.translation import (
    translate_panel_data,
    translate_vulgarization_data,
)
from agents.vulgarization import vulgarization_agent
from knowledge import is_ss_circuit_open
from storage import (
    save_brief as save_brief_db,
    get_recent_consensus_scores,
    init_database,
    update_brief,
)
from logging_config import get_logger

logger = get_logger("post_fire_pipeline")


class PostFireState(TypedDict, total=False):
    """State for the post-fire pipeline."""

    # Input
    hypothesis: str
    domains: list[str]
    mechanisms: str
    keywords: list[str]
    gap_manifest: dict[str, Any]
    grounding_degraded: bool  # True when SS was unavailable

    # Literature Grounding
    grounding: dict[str, Any]
    kill_reason: str | None

    # Sharpening
    sharpened: dict[str, Any]

    # Protocol
    protocol: dict[str, Any]

    # Panel
    panel: dict[str, Any]

    # Meta-review
    meta_verdict: str
    revision_count: int

    # Relative selection gate (S9.3)
    selection_threshold: float
    selection_reason: str
    selection_window_size: int

    # Brief
    brief_id: str
    brief_md_path: str
    brief_json_path: str

    # Vulgarization
    vulgarization_fr: dict[str, Any]

    # S7.4 Phase 4 — EN translations populated by translation_hook
    # after vulgarization. May be absent if the LLM call fails — the
    # brief stays FR-only with frontend fallback.
    vulgarization_en: dict[str, Any]
    panel_en: dict[str, Any]

    # S3/C18 — verdict du nœud de validation final. brief_validated=False
    # signifie que la ligne est restée en 'pending' : elle existe en base,
    # elle n'est servie nulle part.
    brief_validated: bool
    missing_fields: list[str]

    # Stub flag — set when the brief is a stub (Synthesis refused to
    # bridge the pair). Stubs skip the translation_hook because they
    # carry no panel/vulgarization payload to translate.
    is_stub: bool

    # Errors
    errors: list[dict[str, Any]]


# ── Node functions ───────────────────────────────────────────

async def node_persist_grounding_kill(state: PostFireState) -> PostFireState:
    """Persist a killed-at-grounding brief row so the kill is auditable.

    Without this node the post-fire graph goes silently to END when the
    literature grounding sets ``kill_reason`` (already_proven or fatal
    counter-evidence), leaving no trace in the briefs table. We lose the
    ability to measure the grounding-kill rate (target 20-40% per the
    design doc).

    Writes a row with status='killed', kill_reason, grounding_data; all
    other brief fields NULL. brief_id format: SPR-<YYYY>-K<4hex>.
    """
    brief_id = f"SPR-{date.today().strftime('%Y')}-K{uuid4().hex[:4].upper()}"
    hypothesis_id = state.get("hypothesis_id", brief_id)
    kill_reason = state.get("kill_reason") or "unknown"

    try:
        await init_database()
        await save_brief_db(
            brief_id=brief_id,
            hypothesis_id=hypothesis_id,
            grounding_data=state.get("grounding"),
            sharpened_data=None,
            protocol_data=None,
            panel_data=None,
            status="killed",
            kill_reason=kill_reason,
        )
        logger.info(
            "grounding_kill_persisted",
            brief_id=brief_id,
            hypothesis_id=hypothesis_id,
            kill_reason=kill_reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "grounding_kill_persist_failed",
            hypothesis_id=hypothesis_id,
            error=str(exc),
        )

    return {**state, "brief_id": brief_id}


async def node_persist_panel_reject(state: PostFireState) -> PostFireState:
    """Persist a panel-rejected brief row so the score survives the reject.

    This is what keeps the S9.3 relative selection gate honest. The gate
    places its threshold at a percentile of recent consensus scores; if
    only published briefs were stored, that window would be a censored,
    survivors-only sample and the percentile would ratchet upward every
    cycle — the bottom slice is removed from the population that sets
    the next threshold — until nothing publishes at all. That is the
    S8.4 trap (calibrating on survivors) running in the other direction.

    Persisting rejects keeps the window representative of everything the
    panel actually scored. Evidence that the censoring was real: before
    this node, the minimum consensus in the whole briefs table was 5.50,
    exactly the old ITER2_PUBLISH_THRESHOLD.

    Writes a row with status='rejected' carrying the panel payload and
    consensus score. brief_id format: SPR-<YYYY>-R<4hex>.
    """
    brief_id = f"SPR-{date.today().strftime('%Y')}-R{uuid4().hex[:4].upper()}"
    hypothesis_id = state.get("hypothesis_id", brief_id)
    panel = state.get("panel")
    consensus = (panel or {}).get("meta_review", {}).get("consensus_score")

    try:
        await init_database()
        await save_brief_db(
            brief_id=brief_id,
            hypothesis_id=hypothesis_id,
            grounding_data=state.get("grounding"),
            sharpened_data=state.get("sharpened"),
            protocol_data=state.get("protocol"),
            panel_data=panel,
            status="rejected",
            revision_count=state.get("revision_count", 0),
        )
        logger.info(
            "panel_reject_persisted",
            brief_id=brief_id,
            hypothesis_id=hypothesis_id,
            consensus=consensus,
            selection_threshold=state.get("selection_threshold"),
        )
    except Exception as exc:  # noqa: BLE001 — a failed audit write must
        # not turn a reject into a crash; the run is over either way.
        logger.error(
            "panel_reject_persist_failed",
            hypothesis_id=hypothesis_id,
            error=str(exc),
        )

    return {**state, "brief_id": brief_id}


async def node_skip_grounding(state: PostFireState) -> PostFireState:
    """Inject an empty grounding stub when SS is unavailable.

    The pipeline continues with zero evidence — Sharpening, Protocol,
    and Panel all handle empty evidence_base gracefully.
    """
    logger.warning(
        "grounding_skipped_degraded",
        hypothesis=state["hypothesis"][:80],
    )
    empty_grounding: dict[str, Any] = {
        "novelty_assessment": {
            "score": None,
            "verdict": "unavailable",
            "closest_existing_work": [],
        },
        "evidence_base": [],
        "counter_evidence": [],
        "gap_manifest_update": {
            "closed_gaps": [],
            "new_gaps": ["Literature grounding was unavailable — all gaps remain open"],
            "data_available": [],
        },
        "search_queries": [],
        "kill_reason": None,
    }
    return {**state, "grounding": empty_grounding, "kill_reason": None}


async def node_literature_grounding(state: PostFireState) -> PostFireState:
    """Run literature grounding."""
    input_data = GroundingInput(
        hypothesis=state["hypothesis"],
        domains=state["domains"],
        mechanisms=state["mechanisms"],
        keywords=state.get("keywords", []),
        gap_manifest=state.get("gap_manifest", {}),
    )

    output = await literature_grounding_agent(input_data)

    # If the breaker tripped during this grounding (or was already open),
    # surface that to downstream nodes via grounding_degraded so they know
    # the empty evidence_base reflects an outage, not a niche topic.
    breaker_open = is_ss_circuit_open()

    return {
        **state,
        "grounding": dict(output),
        "kill_reason": output["kill_reason"],
        "grounding_degraded": state.get("grounding_degraded", False) or breaker_open,
    }


async def node_hypothesis_sharpening(state: PostFireState) -> PostFireState:
    """Run hypothesis sharpening."""
    grounding = state["grounding"]

    # If this is a revision, incorporate panel feedback into the hypothesis
    revision_count = state.get("revision_count", 0)
    hypothesis = state["hypothesis"]
    if revision_count > 0 and "panel" in state:
        panel = state["panel"]
        meta = panel.get("meta_review", {})
        guidance = meta.get("revision_guidance", [])
        if guidance:
            hypothesis += "\n\nREVISION GUIDANCE FROM PANEL (iteration " + str(revision_count) + "):\n"
            hypothesis += "\n".join(f"- {g}" for g in guidance)

    input_data = SharpeningInput(
        hypothesis=hypothesis,
        domains=state["domains"],
        mechanisms=state["mechanisms"],
        novelty_assessment=grounding.get("novelty_assessment", {}),
        evidence_base=grounding.get("evidence_base", []),
        counter_evidence=grounding.get("counter_evidence", []),
    )

    output = await hypothesis_sharpening_agent(input_data)

    return {
        **state,
        "sharpened": dict(output),
    }


async def node_experimental_protocol(state: PostFireState) -> PostFireState:
    """Run experimental protocol design."""
    sharpened = SharpeningOutput(**state["sharpened"])
    evidence_base = state["grounding"].get("evidence_base", [])

    output = await experimental_protocol_agent(sharpened, evidence_base)

    return {
        **state,
        "protocol": dict(output),
    }


async def node_multi_reviewer_panel(state: PostFireState) -> PostFireState:
    """Run the 5-reviewer panel + meta-reviewer."""
    sharpened = SharpeningOutput(**state["sharpened"])
    protocol = ProtocolOutput(**state["protocol"])
    grounding = state["grounding"]
    iteration = state.get("revision_count", 0) + 1

    output = await full_panel_review(
        sharpened=sharpened,
        protocol=protocol,
        evidence_base=grounding.get("evidence_base", []),
        counter_evidence=grounding.get("counter_evidence", []),
        novelty_assessment=grounding.get("novelty_assessment", {}),
        iteration=iteration,
    )

    meta = output["meta_review"]

    # S9.3 relative selection: the threshold depends on recent panel
    # output, so it is resolved here (async, DB read) and carried in
    # state for the sync router to apply.
    try:
        recent = await get_recent_consensus_scores(window=SELECTION_WINDOW)
        threshold, reason = selection_threshold(recent)
    except Exception as exc:  # noqa: BLE001 — never let the gate break the run
        logger.warning(
            "selection_threshold_unavailable",
            error=str(exc),
            fallback=SELECTION_FLOOR,
        )
        recent, threshold, reason = [], SELECTION_FLOOR, "floor_read_failed"

    logger.info(
        "selection_threshold_resolved",
        threshold=round(threshold, 2),
        reason=reason,
        window_size=len(recent),
        consensus=meta.get("consensus_score"),
    )

    return {
        **state,
        "panel": {
            "reviews": [dict(r) for r in output["reviews"]],
            "meta_review": dict(meta),
        },
        "meta_verdict": meta["verdict"],
        "revision_count": iteration,
        "selection_threshold": threshold,
        "selection_reason": reason,
        "selection_window_size": len(recent),
    }


async def node_vulgarization(state: PostFireState) -> PostFireState:
    """Produce French vulgarization and persist it into brief JSON + DB."""
    import json as _json
    from pathlib import Path as _Path

    sharpened = SharpeningOutput(**state["sharpened"])
    protocol = ProtocolOutput(**state["protocol"])
    panel = PanelOutput(
        reviews=state["panel"]["reviews"],
        meta_review=state["panel"]["meta_review"],
    )

    try:
        vulg = await vulgarization_agent(
            sharpened=sharpened,
            protocol=protocol,
            panel=panel,
            domains=state["domains"],
            grounding=state.get("grounding", {}),
        )
    except Exception as exc:
        logger.error("vulgarization_failed", brief_id=state.get("brief_id"), error=str(exc))
        return {**state}

    vulg_dict = dict(vulg)

    # Patch the JSON brief file on disk with the vulgarization block
    json_path = state.get("brief_json_path")
    if json_path:
        try:
            p = _Path(json_path)
            data = _json.loads(p.read_text(encoding="utf-8"))
            data["vulgarization_fr"] = vulg_dict
            p.write_text(
                _json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("vulgarization_written_to_json", path=str(p))
        except Exception as exc:
            logger.error("vulgarization_json_update_failed", error=str(exc))

    # Update the DB with vulgarization_data
    brief_id = state.get("brief_id")
    if brief_id:
        try:
            await init_database()
            from storage.database import get_connection
            async with get_connection() as conn:
                await conn.execute(
                    "UPDATE briefs SET vulgarization_data = ? WHERE id = ?",
                    (_json.dumps(vulg_dict, ensure_ascii=False), brief_id),
                )
                await conn.commit()
            logger.info("vulgarization_saved_db", brief_id=brief_id)
        except Exception as exc:
            logger.error("vulgarization_db_update_failed", error=str(exc))

    # No rebuild hook: since Phase 2, spore-web reads SQLite directly
    # via better-sqlite3 (readonly + WAL) so a freshly committed brief
    # row is visible to the next Server Component request with no
    # static-rebuild loop in between.
    return {**state, "vulgarization_fr": vulg_dict}


# ── S7.4 Phase 4: post-fire translation hook ─────────────────


async def _persist_translation_updates(
    brief_id: str,
    updates: dict[str, Any],
) -> None:
    """UPDATE briefs SET (panel_data_en | vulgarization_data_en) for brief_id.

    Mirrors the inline-UPDATE pattern used by ``node_vulgarization``.
    Only touches the columns present in ``updates``; absent payloads
    leave their column NULL (the brief stays FR-only on that layer).
    """
    import json as _json
    from storage.database import get_connection

    if not updates:
        return

    set_clauses: list[str] = []
    params: list[Any] = []
    for col, payload in updates.items():
        set_clauses.append(f"{col} = ?")
        params.append(_json.dumps(payload, ensure_ascii=False))
    params.append(brief_id)

    sql = f"UPDATE briefs SET {', '.join(set_clauses)} WHERE id = ?"
    async with get_connection() as conn:
        await conn.execute(sql, params)
        await conn.commit()


async def _patch_json_sidecar(
    json_path: str | None,
    updates: dict[str, Any],
) -> None:
    """Patch ``outputs/briefs/{id}.json`` with EN translation blocks.

    Adds ``vulgarization_en`` and/or ``panel_en`` keys (matching the
    EN columns) to the disk sidecar so offline tools (anthology PDF,
    outreach extraction) see the EN payload alongside the FR.
    Best-effort: a missing file or write error is logged and the DB
    update remains the source of truth.
    """
    import json as _json
    from pathlib import Path as _Path

    if not json_path or not updates:
        return

    # Map DB column name to JSON sidecar key. The sidecar already
    # uses ``vulgarization_fr`` for the FR vulgarization block; we
    # use ``vulgarization_en`` for the EN counterpart and ``panel_en``
    # for the EN panel.
    sidecar_keys = {
        "vulgarization_data_en": "vulgarization_en",
        "panel_data_en": "panel_en",
    }

    try:
        p = _Path(json_path)
        if not p.exists():
            logger.debug("translation_sidecar_missing", path=str(p))
            return
        data = _json.loads(p.read_text(encoding="utf-8"))
        for col, payload in updates.items():
            key = sidecar_keys.get(col)
            if key is not None:
                data[key] = payload
        p.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("translation_sidecar_patched", path=str(p))
    except Exception as exc:
        logger.error(
            "translation_sidecar_patch_failed",
            path=str(json_path),
            error=str(exc),
        )


async def node_translation_hook(state: PostFireState) -> dict[str, Any]:
    """Translate vulgarization_data and panel_data FR -> EN.

    Called after ``node_vulgarization`` once both FR payloads are
    persisted to the briefs row. Persists the EN counterparts into
    ``vulgarization_data_en`` and ``panel_data_en``, plus patches the
    on-disk JSON sidecar.

    Properties:
      * **Idempotent** — calling twice on the same brief produces the
        same result (translation is deterministic up to LLM
        non-determinism, and the UPDATE replaces existing values).
      * **Resilient** — if either translator fails, the brief stays
        FR-only on that layer with a logged error; the pipeline
        progresses to END normally so the FR brief is still
        published.
      * **Conditional** — skipped for stub briefs (no panel/vulg
        payload to translate) and for briefs whose brief_id was not
        set upstream (defensive — should not happen on the publish
        path).
    """
    brief_id = state.get("brief_id")
    if not brief_id:
        logger.warning("translation_hook_no_brief_id")
        return {}

    if state.get("is_stub"):
        logger.info("translation_hook_skip_stub", brief_id=brief_id)
        return {}

    panel_payload = state.get("panel")
    vulg_payload = state.get("vulgarization_fr")

    if not panel_payload and not vulg_payload:
        logger.warning(
            "translation_hook_nothing_to_translate", brief_id=brief_id
        )
        return {}

    updates: dict[str, Any] = {}
    state_updates: dict[str, Any] = {}

    if vulg_payload:
        try:
            vulg_en, warnings, usage = await translate_vulgarization_data(
                brief_id, dict(vulg_payload)
            )
            updates["vulgarization_data_en"] = vulg_en
            state_updates["vulgarization_en"] = vulg_en
            logger.info(
                "translation_hook_vulgarization_done",
                brief_id=brief_id,
                warnings_count=len(warnings),
                cost_usd=usage.get("cost_usd"),
            )
            if warnings:
                logger.warning(
                    "translation_hook_vulgarization_warnings",
                    brief_id=brief_id,
                    warnings=warnings[:5],
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "translation_hook_vulgarization_failed",
                brief_id=brief_id,
                error=str(exc),
            )

    if panel_payload:
        try:
            panel_en, warnings, usage = await translate_panel_data(
                brief_id, dict(panel_payload)
            )
            updates["panel_data_en"] = panel_en
            state_updates["panel_en"] = panel_en
            logger.info(
                "translation_hook_panel_done",
                brief_id=brief_id,
                warnings_count=len(warnings),
                cost_usd=usage.get("cost_usd"),
            )
            if warnings:
                logger.warning(
                    "translation_hook_panel_warnings",
                    brief_id=brief_id,
                    warnings=warnings[:5],
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "translation_hook_panel_failed",
                brief_id=brief_id,
                error=str(exc),
            )

    if not updates:
        logger.warning(
            "translation_hook_no_updates_persisted",
            brief_id=brief_id,
            note="both translators failed — brief stays FR-only",
        )
        return {**state, **state_updates}

    try:
        await init_database()
        await _persist_translation_updates(brief_id, updates)
        logger.info(
            "translation_hook_db_updated",
            brief_id=brief_id,
            columns=list(updates.keys()),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "translation_hook_db_update_failed",
            brief_id=brief_id,
            error=str(exc),
        )

    await _patch_json_sidecar(state.get("brief_json_path"), updates)

    return {**state, **state_updates}


async def node_research_brief(state: PostFireState) -> PostFireState:
    """Generate and save the research brief."""
    brief_id = f"SPR-{date.today().strftime('%Y')}-{uuid4().hex[:4].upper()}"

    sharpened = SharpeningOutput(**state["sharpened"])
    protocol = ProtocolOutput(**state["protocol"])
    panel = PanelOutput(
        reviews=state["panel"]["reviews"],
        meta_review=state["panel"]["meta_review"],
    )

    md_path, json_path = await save_brief(
        brief_id=brief_id,
        hypothesis=state["hypothesis"],
        domains=state["domains"],
        grounding=state["grounding"],
        sharpened=sharpened,
        protocol=protocol,
        panel=panel,
    )

    # save_brief returns (None, None) when the panel verdict is 'reject'.
    # In the normal graph flow that branch never fires — should_revise_or_publish
    # already routes 'reject' directly to END. This is a belt-and-braces
    # guard for callers that invoke save_brief outside the graph.
    md_path_str = str(md_path) if md_path else ""
    json_path_str = str(json_path) if json_path else ""

    # Phase 2: mirror the just-written .md into the body_markdown column
    # so the frontend Server Component can render without touching disk.
    # Best-effort: a missing/unreadable file just skips the column —
    # the file remains the source of truth until the next backfill.
    body_markdown: Optional[str] = None
    if md_path is not None:
        try:
            body_markdown = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "brief_body_markdown_read_failed",
                brief_id=brief_id,
                path=md_path_str,
                error=str(exc),
            )

    # Register the brief in the SQLite briefs table so the UI picks it up.
    # hypothesis_id comes from the state if the pipeline was triggered for a
    # specific stored hypothesis; otherwise we use the brief_id as a placeholder.
    hypothesis_id = state.get("hypothesis_id", brief_id)
    revision_count = int(state.get("revision_count", 0) or 0)
    try:
        await init_database()
        # Mirror ``domains`` onto sharpened_data so the frontend cards
        # (which read sharpened_data.domains via briefRowToBrief) show the
        # collision pair without needing to crack open the sidecar JSON.
        sharpened_for_db = {**dict(sharpened), "domains": list(state["domains"])}
        await save_brief_db(
            brief_id=brief_id,
            hypothesis_id=hypothesis_id,
            grounding_data=state.get("grounding"),
            sharpened_data=sharpened_for_db,
            protocol_data=dict(protocol),
            panel_data={
                "reviews": [dict(r) for r in panel["reviews"]],
                "meta_review": dict(panel["meta_review"]),
            },
            # S3/C18 — 'pending', pas 'complete'. Le générateur a écrit sa
            # ligne ; le pipeline n'est pas allé au bout. La promotion en
            # 'complete' appartient à node_validate_brief, en fin de graphe,
            # après la vulgarisation et le hook de traduction.
            #
            # Sans ça, la ligne était publiable dès cet instant : quand la
            # vulgarisation échouait ensuite (node_vulgarization attrape et
            # rend l'état inchangé), le brief restait publié, incomplet, sans
            # que rien ne le signale. Trace : SPR-2026-4469, -FBCA, -A2C5.
            #
            # 'pending' est la valeur par défaut de la colonne au schéma, donc
            # aucune migration : les lignes non promues portent simplement ce
            # que le schéma prévoyait déjà. Le prédicat du front
            # (spore-web/src/lib/brief-visibility.ts) teste status='complete'
            # OR is_stub, donc une ligne 'pending' est déjà exclue de toutes
            # les surfaces publiques sans y toucher.
            status="pending",
            brief_md_path=md_path_str or None,
            brief_json_path=json_path_str or None,
            revision_count=revision_count,
            body_markdown=body_markdown,
        )
    except Exception as exc:
        logger.error("brief_db_save_failed", brief_id=brief_id, error=str(exc))

    logger.info("brief_generated", brief_id=brief_id, md_path=md_path_str)

    return {
        **state,
        "brief_id": brief_id,
        "brief_md_path": md_path_str,
        "brief_json_path": json_path_str,
    }


# S3/C18 — champs exigés pour promouvoir un brief en 'complete'.
#
# La vulgarisation FR est requise : c'est le trou que C18 ferme, et un brief
# publié sans elle est celui que le site rend mal aujourd'hui.
#
# L'anglais (vulgarization_data_en, panel_data_en) est délibérément ABSENT de
# cette liste. SPR-2026-F2F4 est publié aujourd'hui avec sa vulgarisation FR
# et sans l'EN, légitimement : le hook de traduction est un enrichissement,
# pas une condition de publication. L'exiger bloquerait des briefs corrects.
REQUIRED_FOR_COMPLETE: tuple[str, ...] = (
    "sharpened",
    "protocol",
    "panel",
    "vulgarization_fr",
)


def _missing_required_fields(state: PostFireState) -> list[str]:
    """Champs requis absents ou vides dans l'état final du graphe.

    Contrôle mécanique de présence et de complétude — pas de jugement LLM,
    conformément au pattern du projet (seuils du méta-reviewer, gates S9.2 et
    S9.3, override Python du consensus).
    """
    missing: list[str] = []
    for field in REQUIRED_FOR_COMPLETE:
        value = state.get(field)
        if not value:
            missing.append(field)
    return missing


async def node_validate_brief(state: PostFireState) -> PostFireState:
    """Dernier nœud du graphe : promeut le brief en 'complete', ou le laisse.

    ``complete`` doit signifier « le pipeline a réussi », pas « le générateur
    a écrit sa ligne ». Ce nœud est le seul endroit qui écrit ce statut sur le
    chemin de publication.

    Une ligne non promue reste en 'pending' : invisible du site (le prédicat
    du front exige 'complete' ou is_stub), conservée en base pour diagnostic,
    et détectée par ``scripts/daily_pipeline_digest.py`` au-delà de 2 h.

    Les nœuds ``persist_panel_reject`` et ``persist_grounding_kill`` ne
    passent PAS par ici : ils écrivent 'rejected' et 'killed', terminaux par
    conception, et n'ont jamais été publiables.
    """
    brief_id = state.get("brief_id")
    if not brief_id:
        # Aucun brief à valider — chemin non atteint en pratique, le nœud
        # n'est câblé qu'après research_brief_generator.
        return {**state}

    missing = _missing_required_fields(state)
    if missing:
        logger.error(
            "brief_validation_failed",
            brief_id=brief_id,
            missing_fields=missing,
            # Le payload brut de ce qui a échoué en amont, tronqué. Les 26
            # reviewer_parse_failed d'août ne l'enregistrent pas, ce qui les
            # rend indiagnosticables ; on ne reproduit pas ce trou.
            last_error=str(state.get("errors", [])[-1])[:500]
            if state.get("errors")
            else None,
            revision_count=state.get("revision_count", 0),
        )
        return {**state, "brief_validated": False, "missing_fields": missing}

    try:
        await init_database()
        await update_brief(brief_id, status="complete")
    except Exception as exc:
        logger.error("brief_promotion_failed", brief_id=brief_id, error=str(exc))
        return {**state, "brief_validated": False, "missing_fields": ["db_write"]}

    logger.info("brief_validated", brief_id=brief_id)
    return {**state, "brief_validated": True, "missing_fields": []}


# ── Conditional edge functions ───────────────────────────────

def should_skip_grounding(state: PostFireState) -> str:
    """Route to skip_grounding or full grounding based on degraded flag."""
    if state.get("grounding_degraded"):
        return "skip"
    return "full"


def should_continue_after_grounding(state: PostFireState) -> str:
    """Check kill conditions after grounding."""
    if state.get("kill_reason"):
        logger.warning("hypothesis_killed_at_grounding", reason=state["kill_reason"])
        return "killed"
    return "continue"


def should_revise_or_publish(state: PostFireState) -> str:
    """Decide whether to revise, publish, or reject after meta-review.

    S9.2 grounding gate (2026-07-20): a brief with an empty evidence base
    has no literature grounding to stand on and is not publishable, even
    when the panel voted publish. The gate is skipped on the degraded
    path (``grounding_degraded`` — a transient Semantic Scholar outage),
    where the brief publishes with a low_evidence flag and is enriched
    later by ``scripts/enrich_degraded_briefs.py``. It fires on the
    silent grounding-analysis failure mode: SS returned papers but the
    grounding LLM crashed, leaving ``evidence_base`` empty (observed on
    the historical brief SPR-2026-52AA, whose grounding recorded
    "LLM analysis failed — manual review needed" and 0 evidence yet was
    published). On the 27 historical real briefs this gate rejects
    exactly SPR-2026-52AA (its twin SPR-2026-B172, same 0-evidence
    profile, was already panel-rejected). It is an objective grounding
    check, not a calibrated panel threshold.
    """
    verdict = state.get("meta_verdict", "publish_brief")
    revision_count = state.get("revision_count", 0)

    if verdict == "reject":
        logger.warning("hypothesis_rejected_by_panel")
        return "rejected"

    if verdict == "revise_and_resubmit" and revision_count < 2:
        logger.info("revision_requested", iteration=revision_count)
        return "revise"

    # publish_brief OR revise_and_resubmit at max iterations.
    evidence_base = state.get("grounding", {}).get("evidence_base", [])
    if not evidence_base and not state.get("grounding_degraded"):
        logger.warning(
            "brief_rejected_grounding_gate",
            reason="empty evidence base with grounding available "
            "(analysis likely failed)",
            revision_count=revision_count,
        )
        return "rejected"

    # S9.3 relative selection gate. The panel's absolute thresholds pass
    # essentially everything (26/26 on 2026-07-20); this gate keeps only
    # the top slice of recent panel output, with an absolute floor. The
    # threshold was resolved in node_multi_reviewer_panel.
    consensus = state.get("panel", {}).get("meta_review", {}).get(
        "consensus_score", 0.0
    )
    threshold = state.get("selection_threshold", SELECTION_FLOOR)
    if consensus < threshold:
        logger.info(
            "brief_rejected_selection_gate",
            consensus=consensus,
            threshold=round(threshold, 2),
            reason=state.get("selection_reason"),
            window_size=state.get("selection_window_size"),
        )
        return "rejected"

    return "publish"


# ── Graph construction ───────────────────────────────────────

def create_post_fire_pipeline() -> StateGraph:
    """Create the post-fire LangGraph pipeline.

    Returns:
        Configured StateGraph (not compiled).
    """
    workflow = StateGraph(PostFireState)

    # Add nodes
    workflow.add_node("grounding_router", lambda state: state)  # passthrough
    workflow.add_node("literature_grounding", node_literature_grounding)
    workflow.add_node("skip_grounding", node_skip_grounding)
    workflow.add_node("persist_grounding_kill", node_persist_grounding_kill)
    workflow.add_node("persist_panel_reject", node_persist_panel_reject)
    workflow.add_node("hypothesis_sharpening", node_hypothesis_sharpening)
    workflow.add_node("experimental_protocol", node_experimental_protocol)
    workflow.add_node("multi_reviewer_panel", node_multi_reviewer_panel)
    workflow.add_node("research_brief_generator", node_research_brief)
    workflow.add_node("vulgarization", node_vulgarization)
    workflow.add_node("translation_hook", node_translation_hook)
    workflow.add_node("validate_brief", node_validate_brief)

    # Entry point — routes to full grounding or degraded skip
    workflow.set_entry_point("grounding_router")
    workflow.add_conditional_edges(
        "grounding_router",
        should_skip_grounding,
        {
            "full": "literature_grounding",
            "skip": "skip_grounding",
        },
    )

    # Skip grounding → straight to sharpening
    workflow.add_edge("skip_grounding", "hypothesis_sharpening")

    # Full grounding → kill (persisted) or continue to sharpening
    workflow.add_conditional_edges(
        "literature_grounding",
        should_continue_after_grounding,
        {
            "killed": "persist_grounding_kill",
            "continue": "hypothesis_sharpening",
        },
    )
    workflow.add_edge("persist_grounding_kill", END)

    # Linear edges
    workflow.add_edge("hypothesis_sharpening", "experimental_protocol")
    workflow.add_edge("experimental_protocol", "multi_reviewer_panel")

    # Meta-reviewer → revise, publish, or reject
    workflow.add_conditional_edges(
        "multi_reviewer_panel",
        should_revise_or_publish,
        {
            "revise": "hypothesis_sharpening",
            "publish": "research_brief_generator",
            "rejected": "persist_panel_reject",
        },
    )

    workflow.add_edge("persist_panel_reject", END)

    workflow.add_edge("research_brief_generator", "vulgarization")
    workflow.add_edge("vulgarization", "translation_hook")
    # S3/C18 — la validation est le dernier nœud du chemin de publication.
    workflow.add_edge("translation_hook", "validate_brief")
    workflow.add_edge("validate_brief", END)

    return workflow


async def run_post_fire_pipeline(
    hypothesis: str,
    domains: list[str],
    mechanisms: str,
    keywords: list[str] | None = None,
    gap_manifest: dict[str, Any] | None = None,
    grounding_degraded: bool = False,
) -> PostFireState:
    """Run the complete post-fire pipeline.

    Args:
        hypothesis: The fire-rated hypothesis text.
        domains: Scientific domains of the collision.
        mechanisms: Proposed mechanisms.
        keywords: Optional keywords for search.
        gap_manifest: Optional existing gap manifest.
        grounding_degraded: If True, skip literature grounding (SS was down).

    Returns:
        Final PostFireState with all results.
    """
    if not grounding_degraded and is_ss_circuit_open():
        logger.warning(
            "post_fire_grounding_skipped_circuit_open",
            message="Semantic Scholar circuit breaker OPEN — routing to skip_grounding",
        )
        grounding_degraded = True

    initial_state: PostFireState = {
        "hypothesis": hypothesis,
        "domains": domains,
        "mechanisms": mechanisms,
        "keywords": keywords or [],
        "gap_manifest": gap_manifest or {},
        "grounding_degraded": grounding_degraded,
        "kill_reason": None,
        "revision_count": 0,
        "errors": [],
    }

    workflow = create_post_fire_pipeline()
    app = workflow.compile()

    logger.info("post_fire_pipeline_starting", hypothesis=hypothesis[:100])
    final_state = await app.ainvoke(initial_state)
    logger.info(
        "post_fire_pipeline_complete",
        brief_id=final_state.get("brief_id"),
        kill_reason=final_state.get("kill_reason"),
        meta_verdict=final_state.get("meta_verdict"),
        revision_count=final_state.get("revision_count"),
    )

    return final_state
