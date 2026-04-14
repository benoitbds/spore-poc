"""Gate Agent for SPORE.

Lightweight filter that determines if a collision is worth
exploring further. Uses lightweight model for cost efficiency.
"""

import json
import re
from typing import Any

from agents.base import PipelineState, load_prompt
from models.collision import Collision
from config import get_genome
from llm import get_llm_client
from logging_config import get_logger, get_token_tracker
from progress import get_progress_tracker

logger = get_logger("gate_agent")


class GateResult:
    """Result of the gate evaluation."""

    def __init__(self, collision: Collision, plausible: bool, reason: str):
        self.collision = collision
        self.plausible = plausible
        self.reason = reason


async def evaluate_collision(collision: Collision) -> GateResult:
    """Evaluate if a collision is worth exploring.

    Args:
        collision: The collision to evaluate

    Returns:
        GateResult with plausibility decision
    """
    # Get LLM client for gate agent
    client = get_llm_client("gate")

    # Load and format the gate prompt
    prompt_template = load_prompt("gate")

    domain_a = collision.pair.domain_a
    domain_b = collision.pair.domain_b

    prompt = prompt_template.format(
        domain_a_name=domain_a.name,
        domain_a_concepts=", ".join(domain_a.key_concepts[:5]),
        domain_b_name=domain_b.name,
        domain_b_concepts=", ".join(domain_b.key_concepts[:5]),
    )

    try:
        response = await client.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )

        # Track tokens
        tracker = get_token_tracker()
        tracker.log_call(
            agent="gate",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            provider=response.provider,
            cache_hit=response.cache_hit,
        )

        # Parse response
        content = response.content.strip()

        # Try to parse JSON - with robust extraction
        plausible = None
        reason = None

        # Extract JSON from markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            result = json.loads(content)
            # Support both old format (plausible: bool) and new format (decision: PASS/REJECT)
            if "decision" in result:
                plausible = result["decision"].upper() == "PASS"
            else:
                plausible = result.get("plausible", False)
            reason = result.get("reason", "No reason provided")
        except json.JSONDecodeError:
            # Try to extract JSON from text
            json_match = re.search(r'\{[^{}]*(?:"decision"|"plausible")[^{}]*\}', content, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    if "decision" in result:
                        plausible = result["decision"].upper() == "PASS"
                    else:
                        plausible = result.get("plausible", False)
                    reason = result.get("reason", "No reason provided")
                except json.JSONDecodeError:
                    pass

            # Fallback text parsing
            if plausible is None:
                content_upper = content.upper()
                if '"PASS"' in content_upper or "'PASS'" in content_upper:
                    plausible = True
                elif '"REJECT"' in content_upper or "'REJECT'" in content_upper:
                    plausible = False
                elif '"plausible": true' in content.lower():
                    plausible = True
                elif '"plausible": false' in content.lower():
                    plausible = False
                else:
                    logger.warning(
                        "gate_json_parse_failed",
                        domain_a=domain_a.name,
                        domain_b=domain_b.name,
                        response=content[:200],
                    )
                    plausible = False
                    reason = "JSON parse error - defaulting to REJECT"

                if reason is None:
                    reason_match = re.search(r'"reason":\s*"([^"]*)"', content)
                    reason = reason_match.group(1) if reason_match else "Could not parse reason"

        logger.info(
            "gate_evaluated",
            domain_a=domain_a.name,
            domain_b=domain_b.name,
            plausible=plausible,
            reason=reason[:100],
        )

        return GateResult(collision, plausible, reason)

    except Exception as e:
        logger.error(
            "gate_evaluation_failed",
            domain_a=domain_a.name,
            domain_b=domain_b.name,
            error=str(e),
        )
        # On error, default to plausible to not block pipeline
        return GateResult(collision, True, f"Error: {str(e)}")


async def gate_agent(state: PipelineState) -> PipelineState:
    """Gate Agent: filters collisions before synthesis.

    This agent:
    1. Takes all enriched collisions
    2. Evaluates each with a lightweight model (Haiku)
    3. Filters to only plausible collisions for synthesis

    Args:
        state: Current pipeline state with collisions

    Returns:
        Updated state with filtered collisions and gate results
    """
    collisions = state.get("collisions", [])

    if not collisions:
        logger.warning("gate_no_collisions")
        state["gated_collisions"] = []
        state["gate_results"] = []
        return state

    logger.info("gate_starting", n_collisions=len(collisions))

    # Get progress tracker
    progress_tracker = get_progress_tracker()

    # Evaluate all collisions
    gate_results: list[GateResult] = []
    gated_collisions: list[Collision] = []
    rejected_collisions: list[dict[str, Any]] = []

    for collision in collisions:
        # Update progress: current collision at gate stage
        progress_tracker.set_current_collision(
            domain_a=collision.domain_a.name,
            domain_b=collision.domain_b.name,
            distance=collision.pair.distance_score,
            stage="gate",
        )
        result = await evaluate_collision(collision)
        gate_results.append(result)

        if result.plausible:
            gated_collisions.append(collision)
        else:
            rejected_collisions.append({
                "domain_a": collision.pair.domain_a.name,
                "domain_b": collision.pair.domain_b.name,
                "reason": result.reason,
            })

    # Calculate gate pass rate
    pass_rate = len(gated_collisions) / len(collisions) if collisions else 0

    progress_tracker.gate_done(
        passed=len(gated_collisions),
        rejected=len(rejected_collisions),
    )
    logger.info(
        "gate_complete",
        total=len(collisions),
        passed=len(gated_collisions),
        rejected=len(rejected_collisions),
        pass_rate=f"{pass_rate:.1%}",
    )

    # Store results in state
    state["gated_collisions"] = gated_collisions
    state["gate_results"] = [
        {
            "domain_a": r.collision.pair.domain_a.name,
            "domain_b": r.collision.pair.domain_b.name,
            "plausible": r.plausible,
            "reason": r.reason,
        }
        for r in gate_results
    ]
    state["rejected_collisions"] = rejected_collisions

    # Update metrics
    metrics = state.get("metrics", {})
    metrics["gate_total"] = len(collisions)
    metrics["gate_passed"] = len(gated_collisions)
    metrics["gate_pass_rate"] = pass_rate
    state["metrics"] = metrics

    return state
