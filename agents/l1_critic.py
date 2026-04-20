"""L1 Critic Agent - Evaluates and validates proposed mutations.

Part of Team L1 (Trainers) - Design Doc section 2.3.

The Critic evaluates each mutation proposed by the Strategist:
- Risk of degradation
- Coherence with constitution.yaml
- Conflict with previous mutations
- Overall recommendation (validate/reject)
"""

import json
from typing import Any

import yaml

from config import get_constitution
from llm import get_llm_client
from models.mutation import (
    Mutation,
    MutationStatus,
    MutationRisk,
    StrategyProposal,
)
from logging_config import get_logger, get_token_tracker

logger = get_logger("l1_critic")

CRITIC_PROMPT = """You are the L1 Critic for SPORE, a scientific hypothesis generation system.

Your role: Evaluate proposed mutations for safety, coherence, and effectiveness.

## Constitution (IMMUTABLE RULES)
```yaml
{constitution_yaml}
```

## Recent Mutation History
{mutation_history}

## Mutation to Evaluate
```json
{mutation_json}
```

## Evaluation Criteria

1. **Constitution Compliance**
   - Does this respect all safety limits?
   - Does it require human approval?
   - Does it violate any ethical guidelines?

2. **Risk Assessment**
   - What could go wrong?
   - Is the change reversible?
   - What's the blast radius if it fails?

3. **Conflict Detection**
   - Does this conflict with recent mutations?
   - Could this undo previous improvements?
   - Are there interaction effects to consider?

4. **Effectiveness**
   - Is the justification sound?
   - Is the expected impact realistic?
   - Is this the right lever to pull?

## Response Format

Respond with a JSON object:
```json
{{
  "verdict": "validate" | "reject",
  "confidence": 0.0-1.0,
  "concerns": ["list of concerns"],
  "recommendations": ["suggested modifications if any"],
  "risk_assessment": {{
    "degradation_risk": "low" | "medium" | "high",
    "reversibility": "easy" | "medium" | "hard",
    "blast_radius": "narrow" | "medium" | "wide"
  }},
  "reasoning": "Detailed explanation of decision"
}}
```

Be thorough but not overly conservative. The goal is to catch real risks,
not to block all change. Small, reversible changes should generally be approved.
"""


async def evaluate_mutation(
    mutation: Mutation,
    recent_mutations: list[dict],
) -> tuple[bool, str, list[str]]:
    """Evaluate a single mutation.

    Args:
        mutation: Mutation to evaluate
        recent_mutations: Recently applied/rejected mutations loaded from the
            mutations SQLite table (each dict has keys applied_at, mutation_type,
            target_path, new_value, status)

    Returns:
        Tuple of (is_valid, verdict_reason, concerns)
    """
    constitution = get_constitution()

    if recent_mutations:
        history_lines = []
        for m in recent_mutations[:5]:  # helper already returns newest-first
            date_str = str(m.get("applied_at", ""))[:10]
            history_lines.append(
                f"- [{date_str}] {m.get('mutation_type', '?')}: "
                f"{m.get('target_path', '?')} = {m.get('new_value', '?')} "
                f"({m.get('status', '?')})"
            )
        mutation_history = "\n".join(history_lines)
    else:
        mutation_history = "No recent mutations."

    prompt = CRITIC_PROMPT.format(
        constitution_yaml=yaml.dump(constitution.to_dict(), default_flow_style=False),
        mutation_history=mutation_history,
        mutation_json=mutation.model_dump_json(indent=2),
    )

    # Get LLM client
    client = get_llm_client("synthesis")  # Use synthesis config for L1 critic

    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.2,  # Low temperature for consistent evaluation
    )

    get_token_tracker().log_call(
        "l1_critic",
        response.model,
        response.input_tokens,
        response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    content = response.content

    try:
        # Handle markdown code blocks
        if "```json" in content:
            json_start = content.index("```json") + 7
            json_end = content.index("```", json_start)
            content = content[json_start:json_end].strip()
        elif "```" in content:
            json_start = content.index("```") + 3
            json_end = content.index("```", json_start)
            content = content[json_start:json_end].strip()

        data = json.loads(content)

        is_valid = data.get("verdict") == "validate"
        reasoning = data.get("reasoning", "")
        concerns = data.get("concerns", [])

        # Update mutation with critic feedback
        mutation.critic_verdict = data.get("verdict")
        mutation.critic_concerns = concerns

        return is_valid, reasoning, concerns

    except (json.JSONDecodeError, ValueError) as e:
        logger.error("critic_parse_error", error=str(e))
        return False, f"Failed to parse critic response: {e}", ["Parse error"]


async def evaluate_proposal(
    proposal: StrategyProposal,
    recent_mutations: list[dict],
) -> StrategyProposal:
    """Evaluate all mutations in a proposal.

    Args:
        proposal: Strategy proposal with mutations
        recent_mutations: Recently applied mutations

    Returns:
        Updated proposal with validation status
    """
    logger.info(
        "critic_evaluation_starting",
        mutations_to_evaluate=len(proposal.mutations)
    )

    validated_mutations = []
    rejected_mutations = []

    for mutation in proposal.mutations:
        is_valid, reasoning, concerns = await evaluate_mutation(
            mutation,
            recent_mutations,
        )

        if is_valid:
            mutation.status = MutationStatus.VALIDATED
            validated_mutations.append(mutation)
            logger.info(
                "mutation_validated",
                mutation_id=mutation.id,
                type=mutation.type.value,
            )
        else:
            mutation.status = MutationStatus.REJECTED
            rejected_mutations.append(mutation)
            logger.info(
                "mutation_rejected",
                mutation_id=mutation.id,
                type=mutation.type.value,
                reason=reasoning[:100],
            )

    # Update proposal with only validated mutations
    proposal.mutations = validated_mutations

    # Add rejection info to risks
    if rejected_mutations:
        proposal.risks.append(
            f"{len(rejected_mutations)} mutation(s) rejected by Critic"
        )

    logger.info(
        "critic_evaluation_complete",
        validated=len(validated_mutations),
        rejected=len(rejected_mutations),
    )

    return proposal


def quick_constitution_check(mutation: Mutation) -> tuple[bool, list[str]]:
    """Quick constitution compliance check without LLM.

    Args:
        mutation: Mutation to check

    Returns:
        Tuple of (passes_check, violations)
    """
    constitution = get_constitution()
    violations = []

    # Check chaos_floor
    if mutation.target_path == "randomness.chaos_floor":
        if isinstance(mutation.new_value, (int, float)):
            if mutation.new_value < constitution.chaos_floor:
                violations.append(
                    f"Cannot set chaos_floor below {constitution.chaos_floor}"
                )

    # Check excluded domains
    excluded = constitution.excluded_domains
    if mutation.target_path.startswith("domains") and mutation.new_value:
        if any(ex in str(mutation.new_value).lower() for ex in excluded):
            violations.append(
                f"Domain touches excluded area: {excluded}"
            )

    return len(violations) == 0, violations
