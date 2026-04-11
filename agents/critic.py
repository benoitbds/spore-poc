"""Critic Agents for SPORE.

Implements the adversarial debate with Devil's Advocate and Angel's Advocate.
"""

import asyncio
import json
from typing import Any

from config import get_genome
from llm import get_llm_client
from models.hypothesis import Hypothesis, Scores
from agents.base import PipelineState, DebateLog, load_prompt, format_predictions, format_context
from logging_config import get_logger, get_token_tracker
from progress import get_progress_tracker

logger = get_logger("critic_agent")


async def run_devil_advocate(
    hypothesis: Hypothesis,
    context_a: list[str],
    context_b: list[str],
) -> dict[str, Any]:
    """Run the Devil's Advocate critic.

    Args:
        hypothesis: The hypothesis to critique
        context_a: Context for domain A
        context_b: Context for domain B

    Returns:
        Parsed critique response
    """
    client = get_llm_client("critic_devil")
    prompt_template = load_prompt("devil_advocate")

    # Format predictions
    predictions_text = format_predictions([
        {
            "statement": p.statement,
            "metric": p.metric,
            "expected_range": p.expected_range,
        }
        for p in hypothesis.predictions
    ])

    prompt = prompt_template.format(
        bridge_summary=hypothesis.bridge.summary,
        bridge_mechanism=hypothesis.bridge.mechanism,
        bridge_type=hypothesis.bridge.type.value,
        kill_condition=hypothesis.kill_condition,
        predictions=predictions_text,
        domain_a_name=hypothesis.collision.domain_a.name,
        domain_a_context=format_context(context_a),
        domain_b_name=hypothesis.collision.domain_b.name,
        domain_b_context=format_context(context_b),
    )

    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )

    # Track tokens
    tracker = get_token_tracker()
    tracker.log_call(
        agent="critic_devil",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    content = response.content

    # Parse JSON
    try:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())
    except json.JSONDecodeError as e:
        logger.warning("devil_json_parse_failed", error=str(e))
        return {
            "verdict": "flawed",
            "primary_flaw": "Unable to parse critique",
            "criticisms": [],
            "scores": {
                "novelty": 0.5,
                "coherence": 0.5,
                "testability": 0.5,
                "impact_potential": 0.5,
                "hallucination_risk": 0.5,
            },
            "recommendation": "revise",
        }


async def run_angel_advocate(
    hypothesis: Hypothesis,
    context_a: list[str],
    context_b: list[str],
) -> dict[str, Any]:
    """Run the Angel's Advocate supporter.

    Args:
        hypothesis: The hypothesis to support
        context_a: Context for domain A
        context_b: Context for domain B

    Returns:
        Parsed support response
    """
    client = get_llm_client("critic_angel")
    prompt_template = load_prompt("angel_advocate")

    predictions_text = format_predictions([
        {
            "statement": p.statement,
            "metric": p.metric,
            "expected_range": p.expected_range,
        }
        for p in hypothesis.predictions
    ])

    prompt = prompt_template.format(
        bridge_summary=hypothesis.bridge.summary,
        bridge_mechanism=hypothesis.bridge.mechanism,
        bridge_type=hypothesis.bridge.type.value,
        kill_condition=hypothesis.kill_condition,
        predictions=predictions_text,
        domain_a_name=hypothesis.collision.domain_a.name,
        domain_a_context=format_context(context_a),
        domain_b_name=hypothesis.collision.domain_b.name,
        domain_b_context=format_context(context_b),
    )

    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )

    # Track tokens
    tracker = get_token_tracker()
    tracker.log_call(
        agent="critic_angel",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    content = response.content

    # Parse JSON
    try:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())
    except json.JSONDecodeError as e:
        logger.warning("angel_json_parse_failed", error=str(e))
        return {
            "verdict": "moderate_support",
            "best_argument": "Unable to parse support",
            "supporting_evidence": [],
            "scores": {
                "novelty": 0.5,
                "coherence": 0.5,
                "testability": 0.5,
                "impact_potential": 0.5,
                "hallucination_risk": 0.5,
            },
            "quick_wins": [],
            "recommendation": "explore",
        }


def aggregate_scores(devil: dict[str, float], angel: dict[str, float]) -> Scores:
    """Aggregate scores from devil and angel advocates.

    Takes the average of both perspectives for a balanced view.

    Args:
        devil: Devil's scores
        angel: Angel's scores

    Returns:
        Aggregated Scores object
    """
    def avg(key: str) -> float:
        d = devil.get(key, 0.5)
        a = angel.get(key, 0.5)
        return (d + a) / 2

    scores = Scores(
        novelty=avg("novelty"),
        coherence=avg("coherence"),
        testability=avg("testability"),
        impact_potential=avg("impact_potential"),
        hallucination_risk=avg("hallucination_risk"),
    )

    # Compute composite score
    scores.compute_composite()

    return scores


async def critique_hypothesis(
    hypothesis: Hypothesis,
    collisions: list,  # To find context
) -> tuple[Hypothesis, DebateLog]:
    """Run both critics on a hypothesis in parallel.

    Args:
        hypothesis: The hypothesis to critique
        collisions: List of collisions to find context

    Returns:
        Tuple of (updated hypothesis with scores, debate log)
    """
    # Find matching collision for context
    context_a: list[str] = []
    context_b: list[str] = []

    for collision in collisions:
        if (collision.domain_a.id == hypothesis.collision.domain_a.id and
            collision.domain_b.id == hypothesis.collision.domain_b.id):
            context_a = collision.context_a
            context_b = collision.context_b
            break

    logger.info(
        "critiquing_hypothesis",
        id=hypothesis.id,
        domain_a=hypothesis.collision.domain_a.name,
        domain_b=hypothesis.collision.domain_b.name,
    )

    # Run both critics in parallel
    devil_task = run_devil_advocate(hypothesis, context_a, context_b)
    angel_task = run_angel_advocate(hypothesis, context_a, context_b)

    devil_result, angel_result = await asyncio.gather(devil_task, angel_task)

    # Aggregate scores
    devil_scores = devil_result.get("scores", {})
    angel_scores = angel_result.get("scores", {})
    final_scores = aggregate_scores(devil_scores, angel_scores)

    # Update hypothesis with scores
    hypothesis.scores = final_scores

    # Create debate log
    debate_log: DebateLog = {
        "hypothesis_id": hypothesis.id,
        "devil_verdict": devil_result.get("verdict", "unknown"),
        "devil_criticisms": devil_result.get("criticisms", []),
        "devil_scores": devil_scores,
        "angel_verdict": angel_result.get("verdict", "unknown"),
        "angel_evidence": angel_result.get("supporting_evidence", []),
        "angel_scores": angel_scores,
        "final_scores": {
            "novelty": final_scores.novelty,
            "coherence": final_scores.coherence,
            "testability": final_scores.testability,
            "impact_potential": final_scores.impact_potential,
            "hallucination_risk": final_scores.hallucination_risk,
            "composite": final_scores.composite or 0,
        },
    }

    logger.info(
        "critique_complete",
        id=hypothesis.id,
        devil_verdict=devil_result.get("verdict"),
        angel_verdict=angel_result.get("verdict"),
        composite_score=f"{final_scores.composite:.2f}" if final_scores.composite else "N/A",
    )

    return hypothesis, debate_log


async def critic_agent(state: PipelineState) -> PipelineState:
    """Critic Agent: runs adversarial debate on hypotheses.

    This agent:
    1. Takes each hypothesis
    2. Runs Devil and Angel advocates in parallel
    3. Aggregates scores
    4. Creates debate logs

    Args:
        state: Current pipeline state with hypotheses

    Returns:
        Updated state with scored hypotheses and debate logs
    """
    hypotheses = state.get("hypotheses", [])
    collisions = state.get("collisions", [])

    if not hypotheses:
        logger.info("no_hypotheses_to_critique")
        state["debate_logs"] = []
        return state

    logger.info("critic_starting", n_hypotheses=len(hypotheses))

    # Get progress tracker
    progress_tracker = get_progress_tracker()

    scored_hypotheses: list[Hypothesis] = []
    debate_logs: list[DebateLog] = []
    errors = state.get("errors", [])

    # Process hypotheses (could parallelize more, but respect rate limits)
    for hypothesis in hypotheses:
        # Update progress stage to critics
        progress_tracker.set_current_collision(
            domain_a=hypothesis.collision.domain_a.name,
            domain_b=hypothesis.collision.domain_b.name,
            distance=hypothesis.collision.distance_score,
            stage="critics",
        )

        try:
            scored_hyp, debate_log = await critique_hypothesis(
                hypothesis=hypothesis,
                collisions=collisions,
            )
            scored_hypotheses.append(scored_hyp)
            debate_logs.append(debate_log)

            # Update hypothesis score in progress
            if scored_hyp.scores and scored_hyp.scores.composite:
                progress_tracker.hypothesis_scored(
                    hypothesis_id=scored_hyp.id,
                    score=scored_hyp.scores.composite,
                )

        except Exception as e:
            logger.error(
                "critique_failed",
                hypothesis_id=hypothesis.id,
                error=str(e),
            )
            errors.append({
                "agent": "critic",
                "hypothesis_id": hypothesis.id,
                "error": str(e),
            })
            # Keep hypothesis but without scores
            scored_hypotheses.append(hypothesis)

    logger.info(
        "critic_complete",
        hypotheses_scored=len(scored_hypotheses),
        debate_logs=len(debate_logs),
    )

    state["hypotheses"] = scored_hypotheses
    state["debate_logs"] = debate_logs
    state["errors"] = errors

    return state
