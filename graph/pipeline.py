"""LangGraph pipeline for SPORE L0.

Implements the full L0 pipeline:
Explorer → Gate → Synthesis → Critics → Curator → Impact
"""

from datetime import datetime
from typing import Annotated, Any, Optional
from uuid import uuid4

from langgraph.graph import StateGraph, END

from agents.base import PipelineState
from agents.constitution_guard import constitution_guard_node
from agents.explorer import explorer_agent
from agents.gate import gate_agent
from agents.synthesis import synthesis_agent
from agents.critic import critic_agent
from agents.curator import curator_agent
from agents.impact import impact_agent
from agents.reviewer import review_hypothesis, AutoFeedback
from config import get_constitution
from graph.post_fire_pipeline import run_post_fire_pipeline
from storage import (
    init_database,
    save_hypothesis,
    save_run,
    update_run,
    save_metric,
    update_hypothesis_auto_feedback,
)
from storage.database import get_connection
from logging_config import get_logger, get_token_tracker, reset_token_tracker
from progress import get_progress_tracker, reset_progress_tracker

logger = get_logger("pipeline")


def should_continue_after_constitution(state: PipelineState) -> str:
    """Short-circuit the pipeline if the constitution guard killed the state."""
    if state.get("killed"):
        logger.warning(
            "pipeline_killed_by_constitution",
            reason=state.get("kill_reason"),
        )
        return "killed"
    return "continue"


def should_run_synthesis(state: PipelineState) -> str:
    """Decide whether to run synthesis based on gate results.

    If no collisions passed the gate, skip directly to curator.

    Args:
        state: Current pipeline state

    Returns:
        "synthesis" if gated collisions exist, "curator" otherwise
    """
    gated_collisions = state.get("gated_collisions", [])

    if not gated_collisions:
        logger.info("skipping_synthesis", reason="no collisions passed gate")
        return "curator"

    return "synthesis"


def should_run_critics(state: PipelineState) -> str:
    """Decide whether to run critics based on synthesis results.

    If no hypotheses were generated (all NO_BRIDGE_FOUND),
    skip directly to curator.

    Args:
        state: Current pipeline state

    Returns:
        "critic" if hypotheses exist, "curator" otherwise
    """
    hypotheses = state.get("hypotheses", [])

    if not hypotheses:
        logger.info("skipping_critics", reason="no hypotheses generated")
        return "curator"

    return "critic"


def should_run_impact(state: PipelineState) -> str:
    """Decide whether to run impact analysis.

    Only run if there are curated hypotheses.

    Args:
        state: Current pipeline state

    Returns:
        "impact" if curated hypotheses exist, END otherwise
    """
    curated = state.get("curated_hypotheses", [])

    if not curated:
        logger.info("skipping_impact", reason="no curated hypotheses")
        return "end"

    return "impact"


async def reviewer_and_post_fire(state: PipelineState) -> PipelineState:
    """Run the ReviewerAgent on curated hypotheses and trigger post-fire for any rated fire.

    Hypotheses with auto-feedback verdict "a_tester" (fire) are passed
    through the post-fire deep validation pipeline.
    """
    get_progress_tracker().update_stage("reviewer")
    curated = state.get("curated_hypotheses", [])
    if not curated:
        return state

    fire_briefs: list[dict[str, Any]] = []

    # Collect feedback by hypothesis id so run_pipeline can persist it AFTER
    # save_hypothesis (which does INSERT OR REPLACE without the feedback
    # column and would otherwise wipe any value we write here).
    auto_feedback_by_id: dict[str, Any] = {}

    for hypothesis in curated:
        try:
            feedback = await review_hypothesis(hypothesis)
            logger.info(
                "auto_review_result",
                hypothesis_id=hypothesis.id,
                verdict=feedback.verdict,
            )
            auto_feedback_by_id[hypothesis.id] = feedback

            if feedback.verdict == "a_tester":
                # Fire hypothesis — trigger post-fire pipeline
                logger.info("fire_hypothesis_detected", hypothesis_id=hypothesis.id)
                try:
                    pf_state = await run_post_fire_pipeline(
                        hypothesis=hypothesis.bridge.summary,
                        domains=[
                            hypothesis.collision.domain_a.name,
                            hypothesis.collision.domain_b.name,
                        ],
                        mechanisms=hypothesis.bridge.mechanism,
                        keywords=[],
                        gap_manifest=hypothesis.gap_manifest.model_dump()
                        if hypothesis.gap_manifest else {},
                    )
                    fire_briefs.append({
                        "hypothesis_id": hypothesis.id,
                        "brief_id": pf_state.get("brief_id"),
                        "verdict": pf_state.get("meta_verdict"),
                    })
                except Exception as e:
                    logger.error(
                        "post_fire_failed",
                        hypothesis_id=hypothesis.id,
                        error=str(e),
                    )

        except Exception as e:
            logger.error("auto_review_failed", hypothesis_id=hypothesis.id, error=str(e))

    # Store post-fire results in metrics
    metrics = state.get("metrics", {})
    metrics["fire_briefs"] = fire_briefs
    metrics["fire_count"] = len(fire_briefs)
    state["metrics"] = metrics
    state["auto_feedback_by_id"] = auto_feedback_by_id

    return state


def create_pipeline() -> StateGraph:
    """Create the L0 LangGraph pipeline.

    The pipeline follows this flow:
    1. Explorer: Generate and enrich collisions
    2. Gate: Filter collisions (Haiku) - target 40-60% pass rate
    3. Synthesis: Generate hypotheses from gated collisions (Sonnet)
    4. Critics: Run adversarial debate (skipped if no hypotheses)
    5. Curator: Rank and filter hypotheses
    6. Impact: Analyze impact of curated hypotheses
    7. Reviewer + Post-Fire: Auto-review and deep validation for fire hypotheses

    Returns:
        Configured StateGraph
    """
    # Create the graph with PipelineState as the state type
    workflow = StateGraph(PipelineState)

    # Add nodes
    workflow.add_node("explorer", explorer_agent)
    workflow.add_node("constitution_check", constitution_guard_node)
    workflow.add_node("gate", gate_agent)
    workflow.add_node("synthesis", synthesis_agent)
    workflow.add_node("critic", critic_agent)
    workflow.add_node("curator", curator_agent)
    workflow.add_node("impact", impact_agent)
    workflow.add_node("reviewer_post_fire", reviewer_and_post_fire)

    # Set entry point
    workflow.set_entry_point("explorer")

    # Add edges
    workflow.add_edge("explorer", "constitution_check")

    # After constitution check: kill or continue to gate
    workflow.add_conditional_edges(
        "constitution_check",
        should_continue_after_constitution,
        {
            "killed": END,
            "continue": "gate",
        }
    )

    # Conditional edge after gate
    workflow.add_conditional_edges(
        "gate",
        should_run_synthesis,
        {
            "synthesis": "synthesis",
            "curator": "curator",
        }
    )

    # Conditional edge after synthesis
    workflow.add_conditional_edges(
        "synthesis",
        should_run_critics,
        {
            "critic": "critic",
            "curator": "curator",
        }
    )

    workflow.add_edge("critic", "curator")

    # Conditional edge after curator
    workflow.add_conditional_edges(
        "curator",
        should_run_impact,
        {
            "impact": "impact",
            "end": END,
        }
    )

    # After impact, run reviewer + post-fire for fire hypotheses
    workflow.add_edge("impact", "reviewer_post_fire")
    workflow.add_edge("reviewer_post_fire", END)

    return workflow


async def run_pipeline(
    n_collisions: int = 10,
    distance_min: float = 0.4,
    distance_max: float = 0.7,
    save_to_db: bool = True,
    domain: str = "materials_science",
    fix_domain_a: Optional[str] = None,
) -> PipelineState:
    """Run the complete L0 pipeline.

    Args:
        n_collisions: Number of collisions to generate
        distance_min: Minimum semantic distance for collisions
        distance_max: Maximum semantic distance for collisions
        save_to_db: Whether to save results to database
        domain: Domain to use for collisions ("materials_science" or "all_science")
        fix_domain_a: If set (e.g. "MED-003"), force every collision's
            domain_a to this subdomain. domain_b is still chosen from the
            fertile zone around it.

    Returns:
        Final pipeline state with all results
    """
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"

    logger.info(
        "pipeline_starting",
        run_id=run_id,
        n_collisions=n_collisions,
        distance_range=f"{distance_min}-{distance_max}",
    )

    # Reset token tracker for this run
    reset_token_tracker()

    # Initialize progress tracker
    reset_progress_tracker()
    progress_tracker = get_progress_tracker()
    progress_tracker.start_run(
        run_id=run_id,
        total_collisions=n_collisions,
        domain=domain,
    )

    # Initialize database
    if save_to_db:
        await init_database()
        await save_run(run_id, n_collisions)

    # Create initial state
    initial_state: PipelineState = {
        "n_collisions": n_collisions,
        "distance_min": distance_min,
        "distance_max": distance_max,
        "domain": domain,
        "fix_domain_a": fix_domain_a,
        "run_id": run_id,
        "collision_pairs": [],
        "collisions": [],
        "gated_collisions": [],
        "gate_results": [],
        "rejected_collisions": [],
        "hypotheses": [],
        "no_bridges": [],
        "debate_logs": [],
        "curated_hypotheses": [],
        "gap_manifests": [],
        "metrics": {},
        "errors": [],
        "constitution": get_constitution().to_dict(),
        "killed": False,
        "kill_reason": "",
    }

    # Create and compile the pipeline
    workflow = create_pipeline()
    app = workflow.compile()

    # Run the pipeline
    try:
        final_state = await app.ainvoke(initial_state)

        # Get token usage
        tracker = get_token_tracker()
        token_summary = tracker.summary()

        # Update metrics
        metrics = final_state.get("metrics", {})
        metrics["run_id"] = run_id
        metrics["total_tokens_in"] = token_summary["total_input_tokens"]
        metrics["total_tokens_out"] = token_summary["total_output_tokens"]
        metrics["total_cost_usd"] = token_summary["total_cost_usd"]
        metrics["collisions_requested"] = n_collisions
        metrics["collisions_processed"] = len(final_state.get("collisions", []))
        final_state["metrics"] = metrics

        # Save results to database
        if save_to_db:
            # Save curated hypotheses
            for hypothesis in final_state.get("curated_hypotheses", []):
                await save_hypothesis(hypothesis)

            # Persist auto-feedback AFTER save_hypothesis (which does
            # INSERT OR REPLACE without the auto_feedback_json column and
            # would wipe the value if written earlier).
            for hid, feedback in (final_state.get("auto_feedback_by_id") or {}).items():
                try:
                    await update_hypothesis_auto_feedback(hid, feedback)
                except Exception as persist_exc:  # noqa: BLE001
                    logger.error(
                        "auto_feedback_persist_failed",
                        hypothesis_id=hid,
                        error=str(persist_exc),
                    )

            # Save metrics
            for metric_name, value in metrics.items():
                if isinstance(value, (int, float)):
                    await save_metric(run_id, metric_name, float(value))

            # Update run record — killed-by-constitution runs are marked failed
            run_status = "failed" if final_state.get("killed") else "completed"
            run_error = final_state.get("kill_reason") if final_state.get("killed") else None
            await update_run(
                run_id,
                completed_at=datetime.now().isoformat(),
                collisions_processed=metrics.get("collisions_processed", 0),
                hypotheses_generated=metrics.get("hypotheses_generated", 0),
                bridge_rate=metrics.get("bridge_rate", 0),
                total_tokens_in=metrics.get("total_tokens_in", 0),
                total_tokens_out=metrics.get("total_tokens_out", 0),
                total_cost_usd=metrics.get("total_cost_usd", 0),
                status=run_status,
                error_message=run_error,
            )

        logger.info(
            "pipeline_complete",
            run_id=run_id,
            gate_pass_rate=f"{metrics.get('gate_pass_rate', 1.0):.1%}",
            hypotheses_generated=len(final_state.get("hypotheses", [])),
            curated=len(final_state.get("curated_hypotheses", [])),
            bridge_rate=f"{metrics.get('bridge_rate', 0):.1%}",
            total_cost=f"${metrics.get('total_cost_usd', 0):.4f}",
        )

        # Mark progress: failed if the constitution guard (or any other
        # node) killed the state; otherwise complete.
        if final_state.get("killed"):
            progress_tracker.fail_run(
                final_state.get("kill_reason") or "Pipeline killed"
            )
        else:
            progress_tracker.complete_run(final_cost=metrics.get("total_cost_usd", 0))

        return final_state

    except Exception as e:
        logger.error("pipeline_failed", run_id=run_id, error=str(e))

        # Mark progress as failed
        progress_tracker.fail_run(str(e))

        if save_to_db:
            await update_run(
                run_id,
                completed_at=datetime.now().isoformat(),
                status="failed",
                error_message=str(e),
            )

        raise


def get_compiled_pipeline():
    """Get a compiled pipeline ready for execution.

    Returns:
        Compiled LangGraph app
    """
    workflow = create_pipeline()
    return workflow.compile()
