"""LangGraph subgraph for the SPORE post-fire pipeline.

Pipeline:
  Literature Grounding → Hypothesis Sharpening → Experimental Protocol
  → Multi-Reviewer Panel → Meta-Reviewer → (revision loop or Brief Generator)

Conditional edges:
  - After grounding: kill if already_proven or fatal counter-evidence
  - After meta-reviewer: revise_and_resubmit loops back to sharpening (max 2)
"""

from datetime import date
from typing import Any, TypedDict
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
    PanelOutput,
)
from agents.research_brief_generator import save_brief
from agents.vulgarization import vulgarization_agent
from storage import save_brief as save_brief_db, init_database
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

    # Brief
    brief_id: str
    brief_md_path: str
    brief_json_path: str

    # Vulgarization
    vulgarization_fr: dict[str, Any]

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

    return {
        **state,
        "grounding": dict(output),
        "kill_reason": output["kill_reason"],
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

    return {
        **state,
        "panel": {
            "reviews": [dict(r) for r in output["reviews"]],
            "meta_review": dict(meta),
        },
        "meta_verdict": meta["verdict"],
        "revision_count": iteration,
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

    return {**state, "vulgarization_fr": vulg_dict}


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

    # Register the brief in the SQLite briefs table so the UI picks it up.
    # hypothesis_id comes from the state if the pipeline was triggered for a
    # specific stored hypothesis; otherwise we use the brief_id as a placeholder.
    hypothesis_id = state.get("hypothesis_id", brief_id)
    revision_count = int(state.get("revision_count", 0) or 0)
    try:
        await init_database()
        await save_brief_db(
            brief_id=brief_id,
            hypothesis_id=hypothesis_id,
            grounding_data=state.get("grounding"),
            sharpened_data=dict(sharpened),
            protocol_data=dict(protocol),
            panel_data={
                "reviews": [dict(r) for r in panel["reviews"]],
                "meta_review": dict(panel["meta_review"]),
            },
            status="complete",
            brief_md_path=md_path_str or None,
            brief_json_path=json_path_str or None,
            revision_count=revision_count,
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


# ── Conditional edge functions ───────────────────────────────

def should_continue_after_grounding(state: PostFireState) -> str:
    """Check kill conditions after grounding."""
    if state.get("kill_reason"):
        logger.warning("hypothesis_killed_at_grounding", reason=state["kill_reason"])
        return "killed"
    return "continue"


def should_revise_or_publish(state: PostFireState) -> str:
    """Decide whether to revise, publish, or reject after meta-review."""
    verdict = state.get("meta_verdict", "publish_brief")
    revision_count = state.get("revision_count", 0)

    if verdict == "reject":
        logger.warning("hypothesis_rejected_by_panel")
        return "rejected"

    if verdict == "revise_and_resubmit" and revision_count < 2:
        logger.info("revision_requested", iteration=revision_count)
        return "revise"

    # publish_brief OR revise_and_resubmit at max iterations
    return "publish"


# ── Graph construction ───────────────────────────────────────

def create_post_fire_pipeline() -> StateGraph:
    """Create the post-fire LangGraph pipeline.

    Returns:
        Configured StateGraph (not compiled).
    """
    workflow = StateGraph(PostFireState)

    # Add nodes
    workflow.add_node("literature_grounding", node_literature_grounding)
    workflow.add_node("persist_grounding_kill", node_persist_grounding_kill)
    workflow.add_node("hypothesis_sharpening", node_hypothesis_sharpening)
    workflow.add_node("experimental_protocol", node_experimental_protocol)
    workflow.add_node("multi_reviewer_panel", node_multi_reviewer_panel)
    workflow.add_node("research_brief_generator", node_research_brief)
    workflow.add_node("vulgarization", node_vulgarization)

    # Entry point
    workflow.set_entry_point("literature_grounding")

    # Grounding → kill (persisted) or continue to sharpening
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
            "rejected": END,
        },
    )

    workflow.add_edge("research_brief_generator", "vulgarization")
    workflow.add_edge("vulgarization", END)

    return workflow


async def run_post_fire_pipeline(
    hypothesis: str,
    domains: list[str],
    mechanisms: str,
    keywords: list[str] | None = None,
    gap_manifest: dict[str, Any] | None = None,
) -> PostFireState:
    """Run the complete post-fire pipeline.

    Args:
        hypothesis: The fire-rated hypothesis text.
        domains: Scientific domains of the collision.
        mechanisms: Proposed mechanisms.
        keywords: Optional keywords for search.
        gap_manifest: Optional existing gap manifest.

    Returns:
        Final PostFireState with all results.
    """
    initial_state: PostFireState = {
        "hypothesis": hypothesis,
        "domains": domains,
        "mechanisms": mechanisms,
        "keywords": keywords or [],
        "gap_manifest": gap_manifest or {},
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
