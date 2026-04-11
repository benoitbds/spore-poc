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

    # Errors
    errors: list[dict[str, Any]]


# ── Node functions ───────────────────────────────────────────

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

    logger.info("brief_generated", brief_id=brief_id, md_path=str(md_path))

    return {
        **state,
        "brief_id": brief_id,
        "brief_md_path": str(md_path),
        "brief_json_path": str(json_path),
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
    workflow.add_node("hypothesis_sharpening", node_hypothesis_sharpening)
    workflow.add_node("experimental_protocol", node_experimental_protocol)
    workflow.add_node("multi_reviewer_panel", node_multi_reviewer_panel)
    workflow.add_node("research_brief_generator", node_research_brief)

    # Entry point
    workflow.set_entry_point("literature_grounding")

    # Grounding → kill or continue
    workflow.add_conditional_edges(
        "literature_grounding",
        should_continue_after_grounding,
        {"killed": END, "continue": "hypothesis_sharpening"},
    )

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

    workflow.add_edge("research_brief_generator", END)

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
