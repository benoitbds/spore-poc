"""Base utilities for SPORE agents."""

from pathlib import Path
from typing import Any, Optional, TypedDict

from models.collision import Collision, CollisionPair
from models.hypothesis import Hypothesis, NoBridgeFound
from models.gap_manifest import GapManifest


class DebateLog(TypedDict):
    """Log of the devil/angel debate for a hypothesis."""

    hypothesis_id: str
    devil_verdict: str
    devil_criticisms: list[dict[str, Any]]
    devil_scores: dict[str, float]
    angel_verdict: str
    angel_evidence: list[dict[str, Any]]
    angel_scores: dict[str, float]
    final_scores: dict[str, float]


class PipelineState(TypedDict, total=False):
    """State passed through the LangGraph pipeline.

    This follows the LangGraph pattern where each agent
    receives and modifies a shared state dictionary.
    """

    # Configuration
    n_collisions: int
    distance_min: float
    distance_max: float
    domain: str  # Domain to use: "materials_science" or "all_science"
    run_id: str

    # Exploration phase
    collision_pairs: list[CollisionPair]
    collisions: list[Collision]  # Enriched with context

    # Gate phase (filtering before synthesis)
    gated_collisions: list[Collision]  # Collisions that passed the gate
    gate_results: list[dict[str, Any]]  # All gate evaluation results
    rejected_collisions: list[dict[str, Any]]  # Collisions rejected by gate

    # Synthesis phase
    hypotheses: list[Hypothesis]
    no_bridges: list[NoBridgeFound]

    # Critique phase
    debate_logs: list[DebateLog]

    # Curation phase
    curated_hypotheses: list[Hypothesis]

    # Aggregated data
    gap_manifests: list[GapManifest]

    # Metrics
    metrics: dict[str, Any]

    # Errors (non-fatal)
    errors: list[dict[str, Any]]


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory.

    Args:
        name: Name of the prompt file (without .txt extension)

    Returns:
        The prompt template string
    """
    prompt_path = Path(__file__).parent.parent / "prompts" / f"{name}.txt"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")

    return prompt_path.read_text()


def format_predictions(predictions: list[dict[str, Any]]) -> str:
    """Format predictions for prompt injection.

    Args:
        predictions: List of prediction dictionaries

    Returns:
        Formatted string of predictions
    """
    if not predictions:
        return "No predictions specified."

    lines = []
    for i, pred in enumerate(predictions, 1):
        statement = pred.get("statement", "")
        metric = pred.get("metric", "")
        expected = pred.get("expected_range", "")
        lines.append(f"{i}. {statement}")
        lines.append(f"   Metric: {metric}")
        lines.append(f"   Expected: {expected}")

    return "\n".join(lines)


def format_context(abstracts: list[str], max_chars: int = 4000) -> str:
    """Format context abstracts for prompt injection.

    Args:
        abstracts: List of abstract strings
        max_chars: Maximum total characters to include

    Returns:
        Formatted context string
    """
    if not abstracts:
        return "No recent literature context available."

    combined = "\n\n".join(abstracts)

    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n[... truncated for length ...]"

    return combined
