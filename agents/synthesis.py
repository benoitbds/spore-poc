"""Synthesis Agent for SPORE.

Takes enriched collisions and generates hypotheses using LLM.
Supports multiple providers (Anthropic, DeepSeek) via abstraction.
"""

import json
from typing import Any

from config import get_genome
from llm import get_llm_client, LLMResponse
from models.collision import Collision
from models.hypothesis import (
    Hypothesis,
    Bridge,
    BridgeType,
    Prediction,
    NoBridgeFound,
)
from models.gap_manifest import (
    GapManifest,
    DataGap,
    CompetenceGap,
    EpistemicGap,
    GapCriticality,
    ExplorationPriority,
)
from agents.base import PipelineState, load_prompt, format_context
from logging_config import get_logger, get_token_tracker
from progress import get_progress_tracker

logger = get_logger("synthesis_agent")


def parse_bridge_type(type_str: str) -> BridgeType:
    """Parse bridge type string to enum."""
    type_map = {
        "structural_analogy": BridgeType.STRUCTURAL_ANALOGY,
        "causal_transfer": BridgeType.CAUSAL_TRANSFER,
        "methodological_transfer": BridgeType.METHODOLOGICAL_TRANSFER,
        "conceptual_reframe": BridgeType.CONCEPTUAL_REFRAME,
    }
    return type_map.get(type_str.lower(), BridgeType.STRUCTURAL_ANALOGY)


def parse_gap_manifest(data: dict[str, Any]) -> GapManifest:
    """Parse gap manifest from JSON response."""
    data_gaps = []
    for gap in data.get("data_gaps", []):
        criticality = gap.get("criticality", "medium").lower()
        data_gaps.append(DataGap(
            domain=gap.get("domain", "unknown"),
            description=gap.get("description", ""),
            criticality=GapCriticality(criticality) if criticality in ["low", "medium", "high", "critical"] else GapCriticality.MEDIUM,
            recurrence=1,
        ))

    competence_gaps = []
    for gap in data.get("competence_gaps", []):
        competence_gaps.append(CompetenceGap(
            type=gap.get("type", "unknown"),
            description=gap.get("description", ""),
            workaround_used=gap.get("workaround_used"),
            confidence_degradation=float(gap.get("confidence_degradation", 0.0)),
        ))

    epistemic_gaps = []
    for gap in data.get("epistemic_gaps", []):
        priority = gap.get("exploration_priority", "medium").lower()
        epistemic_gaps.append(EpistemicGap(
            zone=gap.get("zone", "unknown"),
            signal=gap.get("signal", ""),
            exploration_priority=ExplorationPriority(priority) if priority in ["low", "medium", "high"] else ExplorationPriority.MEDIUM,
        ))

    return GapManifest(
        data_gaps=data_gaps,
        competence_gaps=competence_gaps,
        epistemic_gaps=epistemic_gaps,
    )


async def synthesize_hypothesis(
    collision: Collision,
    genome_version: str,
) -> Hypothesis | NoBridgeFound:
    """Synthesize a hypothesis from a collision using LLM.

    Args:
        collision: The enriched collision
        genome_version: Current genome version for tracing

    Returns:
        Hypothesis if bridge found, NoBridgeFound otherwise
    """
    # Get LLM client for synthesis agent
    client = get_llm_client("synthesis")

    # Load and format prompt
    prompt_template = load_prompt("synthesis")

    prompt = prompt_template.format(
        domain_a_name=collision.domain_a.name,
        domain_a_concepts=", ".join(collision.domain_a.key_concepts),
        domain_a_context=format_context(collision.context_a),
        domain_b_name=collision.domain_b.name,
        domain_b_concepts=", ".join(collision.domain_b.key_concepts),
        domain_b_context=format_context(collision.context_b),
    )

    logger.info(
        "synthesizing",
        domain_a=collision.domain_a.name,
        domain_b=collision.domain_b.name,
    )

    try:
        response = await client.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
        )

        # Track tokens
        tracker = get_token_tracker()
        tracker.log_call(
            agent="synthesis",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            provider=response.provider,
            cache_hit=response.cache_hit,
        )

        # Parse response
        content = response.content

        # Try to extract JSON from the response
        try:
            # Handle potential markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())

        except json.JSONDecodeError as e:
            logger.error(
                "json_parse_failed",
                domain_a=collision.domain_a.name,
                domain_b=collision.domain_b.name,
                error=str(e),
                content_preview=content[:500],
            )
            # Return as no bridge found due to parsing error
            return NoBridgeFound(
                collision=collision.pair,
                reason=f"Failed to parse LLM response: {str(e)}",
                gap_manifest=GapManifest(),
            )

        # Check if bridge was found
        if not data.get("bridge_found", False):
            logger.info(
                "no_bridge_found",
                domain_a=collision.domain_a.name,
                domain_b=collision.domain_b.name,
                reason=data.get("reason", "Unknown"),
            )
            return NoBridgeFound(
                collision=collision.pair,
                reason=data.get("reason", "No meaningful connection found"),
                gap_manifest=parse_gap_manifest(data.get("gap_manifest", {})),
            )

        # Parse bridge
        bridge_data = data.get("bridge", {})
        bridge = Bridge(
            summary=bridge_data.get("summary", ""),
            mechanism=bridge_data.get("mechanism", ""),
            type=parse_bridge_type(bridge_data.get("type", "structural_analogy")),
        )

        # Parse predictions
        predictions = []
        for pred_data in data.get("predictions", []):
            predictions.append(Prediction(
                statement=pred_data.get("statement", ""),
                metric=pred_data.get("metric", ""),
                expected_range=pred_data.get("expected_range", ""),
                differs_from_consensus=pred_data.get("differs_from_consensus", True),
            ))

        # Create hypothesis
        hypothesis = Hypothesis(
            genome_version=genome_version,
            collision=collision.pair,
            bridge=bridge,
            predictions=predictions,
            kill_condition=data.get("kill_condition", "Not specified"),
            next_steps=data.get("next_steps", []),
            relevant_datasets=data.get("relevant_datasets", []),
            sources_used=data.get("sources_used", []),
            gap_manifest=parse_gap_manifest(data.get("gap_manifest", {})),
        )

        logger.info(
            "hypothesis_generated",
            id=hypothesis.id,
            domain_a=collision.domain_a.name,
            domain_b=collision.domain_b.name,
            bridge_type=bridge.type.value,
        )

        return hypothesis

    except Exception as e:
        logger.error(
            "llm_api_error",
            domain_a=collision.domain_a.name,
            domain_b=collision.domain_b.name,
            error=str(e),
        )
        return NoBridgeFound(
            collision=collision.pair,
            reason=f"API error: {str(e)}",
            gap_manifest=GapManifest(),
        )


async def synthesis_agent(state: PipelineState) -> PipelineState:
    """Synthesis Agent: generates hypotheses from collisions.

    This agent:
    1. Takes each collision that passed the gate (or all collisions if no gate)
    2. Calls Claude to generate a hypothesis
    3. Handles NO_BRIDGE_FOUND cases gracefully

    Args:
        state: Current pipeline state with gated_collisions (or collisions)

    Returns:
        Updated state with hypotheses and no_bridges
    """
    # Use gated_collisions if available (gate was run), otherwise use all collisions
    gated_collisions = state.get("gated_collisions")
    if gated_collisions is not None:
        collisions = gated_collisions
        logger.info("using_gated_collisions", count=len(collisions))
    else:
        collisions = state.get("collisions", [])
        logger.info("using_all_collisions", count=len(collisions))

    if not collisions:
        logger.warning("no_collisions_to_synthesize")
        state["hypotheses"] = []
        state["no_bridges"] = []
        return state

    logger.info("synthesis_starting", n_collisions=len(collisions))

    # Get genome version for tracing
    genome = get_genome()
    genome_version = genome.version

    # Get progress tracker
    progress_tracker = get_progress_tracker()

    hypotheses: list[Hypothesis] = []
    no_bridges: list[NoBridgeFound] = []
    errors = state.get("errors", [])

    # Process each collision sequentially (to manage rate limits)
    for i, collision in enumerate(collisions):
        # Update progress: current collision
        progress_tracker.set_current_collision(
            domain_a=collision.domain_a.name,
            domain_b=collision.domain_b.name,
            distance=collision.pair.distance_score,
            stage="synthesis",
        )

        try:
            result = await synthesize_hypothesis(
                collision=collision,
                genome_version=genome_version,
            )

            # Get current cost from token tracker
            tracker = get_token_tracker()
            current_cost = tracker.total_cost

            if isinstance(result, Hypothesis):
                hypotheses.append(result)
                # Update progress: bridge found
                progress_tracker.collision_processed(
                    bridge_found=True,
                    hypothesis={
                        "id": result.id,
                        "one_liner": result.bridge.summary[:100] if result.bridge else "",
                        "score": 0.0,  # Will be updated after critics
                        "domains": f"{collision.domain_a.name} × {collision.domain_b.name}",
                    },
                )
            else:
                no_bridges.append(result)
                # Update progress: no bridge
                progress_tracker.collision_processed(bridge_found=False)

            # Update cost in progress
            progress_tracker.update_cost(current_cost)

        except Exception as e:
            logger.error(
                "synthesis_failed",
                collision_index=i,
                error=str(e),
            )
            errors.append({
                "agent": "synthesis",
                "collision_index": i,
                "domain_a": collision.domain_a.name,
                "domain_b": collision.domain_b.name,
                "error": str(e),
            })
            # Record as no bridge
            no_bridges.append(NoBridgeFound(
                collision=collision.pair,
                reason=f"Processing error: {str(e)}",
                gap_manifest=GapManifest(),
            ))
            # Update progress: no bridge due to error
            progress_tracker.collision_processed(bridge_found=False)

    # Calculate bridge rate
    total = len(collisions)
    bridge_rate = len(hypotheses) / total if total > 0 else 0

    logger.info(
        "synthesis_complete",
        hypotheses=len(hypotheses),
        no_bridges=len(no_bridges),
        bridge_rate=f"{bridge_rate:.1%}",
    )

    state["hypotheses"] = hypotheses
    state["no_bridges"] = no_bridges
    state["errors"] = errors

    # Update metrics
    metrics = state.get("metrics", {})
    metrics["bridge_rate"] = bridge_rate
    metrics["hypotheses_generated"] = len(hypotheses)
    metrics["no_bridges_count"] = len(no_bridges)
    state["metrics"] = metrics

    return state
