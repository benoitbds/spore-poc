"""Impact Agent for SPORE.

Translates technical hypotheses into impact analysis
understandable by non-specialists.
"""

import json
from typing import Any

from config import get_genome
from llm import get_llm_client
from models.hypothesis import Hypothesis, ImpactAnalysis, ImpactScore
from agents.base import PipelineState, load_prompt
from logging_config import get_logger, get_token_tracker
from progress import get_progress_tracker

logger = get_logger("impact_agent")


def parse_impact_score(score_str: str) -> ImpactScore:
    """Parse impact score string to enum."""
    score_map = {
        "incremental": ImpactScore.INCREMENTAL,
        "significatif": ImpactScore.SIGNIFICATIF,
        "transformatif": ImpactScore.TRANSFORMATIF,
        "civilisationnel": ImpactScore.CIVILISATIONNEL,
    }
    return score_map.get(score_str.lower(), ImpactScore.SIGNIFICATIF)


async def analyze_impact(hypothesis: Hypothesis) -> ImpactAnalysis | None:
    """Analyze the impact of a hypothesis.

    Args:
        hypothesis: The hypothesis to analyze

    Returns:
        ImpactAnalysis if successful, None otherwise
    """
    # Get LLM client for impact agent
    client = get_llm_client("impact")

    # Load and format prompt
    prompt_template = load_prompt("impact")

    # Format predictions
    predictions_text = "\n".join([
        f"- {p.statement} (métrique: {p.metric}, attendu: {p.expected_range})"
        for p in hypothesis.predictions
    ]) if hypothesis.predictions else "Aucune prédiction spécifique."

    prompt = prompt_template.format(
        domain_a=hypothesis.collision.domain_a.name,
        domain_b=hypothesis.collision.domain_b.name,
        bridge_summary=hypothesis.bridge.summary,
        bridge_mechanism=hypothesis.bridge.mechanism,
        predictions=predictions_text,
        kill_condition=hypothesis.kill_condition,
        composite_score=f"{hypothesis.scores.composite:.2f}" if hypothesis.scores else "N/A",
    )

    logger.info(
        "analyzing_impact",
        hypothesis_id=hypothesis.id,
        domain_a=hypothesis.collision.domain_a.name,
        domain_b=hypothesis.collision.domain_b.name,
    )

    try:
        response = await client.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )

        # Track tokens
        tracker = get_token_tracker()
        tracker.log_call(
            agent="impact",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            provider=response.provider,
            cache_hit=response.cache_hit,
        )

        # Parse response
        content = response.content

        # Handle potential markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        data = json.loads(content.strip())

        impact = ImpactAnalysis(
            vulgarisation=data.get("vulgarisation", ""),
            impact_concret=data.get("impact_concret", ""),
            industries_impactees=data.get("industries_impactees", []),
            taille_marche_estimee=data.get("taille_marche_estimee", ""),
            horizon_temporel=data.get("horizon_temporel", ""),
            solutions_existantes=data.get("solutions_existantes", ""),
            score_impact=parse_impact_score(data.get("score_impact", "significatif")),
            one_liner=data.get("one_liner", ""),
        )

        logger.info(
            "impact_analyzed",
            hypothesis_id=hypothesis.id,
            score_impact=impact.score_impact.value,
            industries_count=len(impact.industries_impactees),
        )

        return impact

    except json.JSONDecodeError as e:
        logger.error(
            "impact_json_parse_failed",
            hypothesis_id=hypothesis.id,
            error=str(e),
        )
        return None

    except Exception as e:
        logger.error(
            "impact_api_error",
            hypothesis_id=hypothesis.id,
            error=str(e),
        )
        return None


async def impact_agent(state: PipelineState) -> PipelineState:
    """Impact Agent: analyzes impact of curated hypotheses.

    This agent:
    1. Takes curated hypotheses from the pipeline
    2. Generates impact analysis for each
    3. Updates hypotheses with impact_analysis field

    Args:
        state: Current pipeline state with curated hypotheses

    Returns:
        Updated state with impact-analyzed hypotheses
    """
    curated = state.get("curated_hypotheses", [])

    if not curated:
        logger.warning("no_curated_hypotheses_for_impact")
        return state

    logger.info("impact_starting", n_hypotheses=len(curated))

    # Update progress stage
    progress_tracker = get_progress_tracker()
    progress_tracker.update_stage("impact")

    analyzed_hypotheses: list[Hypothesis] = []
    errors = state.get("errors", [])

    # Process each hypothesis sequentially
    for hypothesis in curated:
        try:
            impact = await analyze_impact(hypothesis=hypothesis)

            if impact:
                # Create updated hypothesis with impact analysis
                hypothesis.impact_analysis = impact
                analyzed_hypotheses.append(hypothesis)
            else:
                # Keep hypothesis even without impact analysis
                analyzed_hypotheses.append(hypothesis)
                errors.append({
                    "agent": "impact",
                    "hypothesis_id": hypothesis.id,
                    "error": "Failed to generate impact analysis",
                })

        except Exception as e:
            logger.error(
                "impact_failed",
                hypothesis_id=hypothesis.id,
                error=str(e),
            )
            errors.append({
                "agent": "impact",
                "hypothesis_id": hypothesis.id,
                "error": str(e),
            })
            # Keep hypothesis even on error
            analyzed_hypotheses.append(hypothesis)

    # Count successful analyses
    analyzed_count = sum(1 for h in analyzed_hypotheses if h.impact_analysis)

    logger.info(
        "impact_complete",
        total_hypotheses=len(curated),
        analyzed=analyzed_count,
    )

    state["curated_hypotheses"] = analyzed_hypotheses
    state["errors"] = errors

    # Update metrics
    metrics = state.get("metrics", {})
    metrics["impact_analyzed"] = analyzed_count
    state["metrics"] = metrics

    return state
