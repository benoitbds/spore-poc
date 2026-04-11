"""Curator Agent for SPORE.

Filters, ranks, and selects the top hypotheses based on scores.
"""

from typing import Any

from config import get_genome
from models.hypothesis import Hypothesis, HypothesisStatus
from models.gap_manifest import GapManifest
from agents.base import PipelineState
from logging_config import get_logger
from progress import get_progress_tracker

logger = get_logger("curator_agent")


def rank_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    """Rank hypotheses by composite score.

    Args:
        hypotheses: List of scored hypotheses

    Returns:
        Sorted list (highest score first)
    """
    def get_score(h: Hypothesis) -> float:
        if h.scores and h.scores.composite is not None:
            return h.scores.composite
        return 0.0

    return sorted(hypotheses, key=get_score, reverse=True)


def curate_top_percent(
    hypotheses: list[Hypothesis],
    top_percent: float = 0.10,
    min_count: int = 1,
) -> list[Hypothesis]:
    """Select the top N% of hypotheses.

    Args:
        hypotheses: Ranked list of hypotheses
        top_percent: Percentage to keep (0.10 = top 10%)
        min_count: Minimum number to keep regardless of percentage

    Returns:
        List of curated hypotheses
    """
    if not hypotheses:
        return []

    # Calculate how many to keep
    count = max(min_count, int(len(hypotheses) * top_percent))

    # Don't exceed total
    count = min(count, len(hypotheses))

    return hypotheses[:count]


def aggregate_gap_manifests(hypotheses: list[Hypothesis]) -> list[GapManifest]:
    """Aggregate gap manifests from all hypotheses.

    Args:
        hypotheses: List of hypotheses

    Returns:
        List of all gap manifests
    """
    return [h.gap_manifest for h in hypotheses if h.gap_manifest.total_gaps > 0]


async def curator_agent(state: PipelineState) -> PipelineState:
    """Curator Agent: filters and ranks hypotheses.

    This agent:
    1. Ranks hypotheses by composite score
    2. Selects the top N%
    3. Updates status to 'curated'
    4. Aggregates gap manifests

    Args:
        state: Current pipeline state with scored hypotheses

    Returns:
        Updated state with curated hypotheses
    """
    hypotheses = state.get("hypotheses", [])
    no_bridges = state.get("no_bridges", [])

    if not hypotheses:
        logger.info("no_hypotheses_to_curate")
        state["curated_hypotheses"] = []
        state["gap_manifests"] = []
        return state

    logger.info("curator_starting", n_hypotheses=len(hypotheses))

    # Get configuration
    genome = get_genome()
    top_percent = genome.get_agent_params("curator").get("top_percent", 0.10)

    # Rank hypotheses
    ranked = rank_hypotheses(hypotheses)

    # Log scores
    for i, h in enumerate(ranked[:5]):
        score = h.scores.composite if h.scores else "N/A"
        logger.debug(
            "hypothesis_rank",
            rank=i + 1,
            id=h.id,
            bridge=h.bridge.summary[:50] + "..." if len(h.bridge.summary) > 50 else h.bridge.summary,
            score=f"{score:.3f}" if isinstance(score, float) else score,
        )

    # Update progress stage
    progress_tracker = get_progress_tracker()
    progress_tracker.update_stage("curator")

    # Select top percent
    curated = curate_top_percent(ranked, top_percent=top_percent, min_count=1)

    # Update status
    for h in curated:
        h.status = HypothesisStatus.CURATED

    # Update progress with curated count
    progress_tracker.hypothesis_curated(count=len(curated))

    # Aggregate gap manifests from all hypotheses (not just curated)
    all_gap_manifests = aggregate_gap_manifests(hypotheses)

    # Also include gap manifests from no_bridges
    for nb in no_bridges:
        if nb.gap_manifest.total_gaps > 0:
            all_gap_manifests.append(nb.gap_manifest)

    # Calculate metrics
    total_generated = len(hypotheses)
    curated_count = len(curated)
    curation_rate = curated_count / total_generated if total_generated > 0 else 0

    # Score distribution
    scores = [h.scores.composite for h in hypotheses if h.scores and h.scores.composite is not None]
    avg_score = sum(scores) / len(scores) if scores else 0
    max_score = max(scores) if scores else 0

    logger.info(
        "curator_complete",
        total_hypotheses=total_generated,
        curated=curated_count,
        curation_rate=f"{curation_rate:.1%}",
        avg_score=f"{avg_score:.3f}",
        max_score=f"{max_score:.3f}",
        total_gaps=sum(gm.total_gaps for gm in all_gap_manifests),
    )

    state["curated_hypotheses"] = curated
    state["gap_manifests"] = all_gap_manifests

    # Update metrics
    metrics = state.get("metrics", {})
    metrics["curation_rate"] = curation_rate
    metrics["curated_count"] = curated_count
    metrics["avg_composite_score"] = avg_score
    metrics["max_composite_score"] = max_score
    metrics["total_gaps"] = sum(gm.total_gaps for gm in all_gap_manifests)
    state["metrics"] = metrics

    return state
