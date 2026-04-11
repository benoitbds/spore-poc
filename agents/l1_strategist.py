"""L1 Strategist Agent - Proposes mutations based on observations.

Part of Team L1 (Trainers) - Design Doc section 2.3.

The Strategist receives the ObservationReport and current genome,
then proposes 1-3 prioritized mutations to improve L0 quality.

Mutation types:
- Prompt modifications
- Parameter changes (distance, temperature, etc.)
- Source modifications
- Score weight adjustments
- Domain exclusions
"""

import json
from typing import Any, Optional

import yaml

from config import get_genome, get_constitution
from llm import get_llm_client
from models.mutation import (
    Mutation,
    MutationType,
    MutationRisk,
    ObservationReport,
    StrategyProposal,
)
from logging_config import get_logger, get_token_tracker

logger = get_logger("l1_strategist")

STRATEGIST_PROMPT = """You are the L1 Strategist for SPORE, a scientific hypothesis generation system.

Your role: Analyze observation data and propose mutations to improve hypothesis quality.

## Current Genome (L0 Configuration)
```yaml
{genome_yaml}
```

## Constitution (IMMUTABLE - must respect these limits)
```yaml
{constitution_yaml}
```

## Observation Report
```json
{observation_json}
```

## Your Task

Based on the observation report, propose 1-3 mutations to improve L0 performance.
Each mutation must:
1. Address a specific issue or opportunity from the report
2. Be data-driven (cite the specific metric/observation)
3. Respect the constitution limits
4. Have a clear expected outcome

## Available Mutation Types

1. **parameter_change**: Modify genome parameters
   - distance_min/distance_max (fertile zone)
   - temperature (0.0-1.0)
   - strategy_weights
   - top_percent (curation threshold)
   - max_tokens

2. **score_weight_adjustment**: Modify composite score weights
   - novelty, coherence, testability, impact_potential
   - hallucination_risk (negative weight)
   - Weights should sum to ~1.0 (accounting for negative)

3. **domain_exclusion**: Temporarily exclude overexploited domains
   - Add to an exclusion list
   - Should be time-limited or conditional

4. **prompt_modification**: Suggest prompt changes
   - Provide the specific change needed
   - Target: synthesis, critic_devil, critic_angel, impact

5. **source_modification**: Add/remove data sources
   - Requires human approval per constitution

## Response Format

Respond with a JSON object:
```json
{{
  "rationale": "Overall strategy explanation",
  "mutations": [
    {{
      "type": "parameter_change",
      "target_path": "randomness.distance_min",
      "old_value": 0.40,
      "new_value": 0.35,
      "description": "Widen fertile zone to capture more bridges",
      "justification": "Bridge rate is only 45%, widening distance range may capture valid bridges currently being filtered",
      "estimated_impact": "Expected 10-15% increase in bridge rate",
      "risk": "low",
      "observation_summary": "Bridge rate 45% below target 60%"
    }}
  ],
  "expected_outcomes": ["Increased bridge rate", "More diverse hypotheses"],
  "risks": ["May increase noise in results"]
}}
```

IMPORTANT:
- Maximum 3 mutations per cycle (constitution limit)
- chaos_floor minimum is 0.10 (constitution limit)
- source changes require human approval - flag them but don't auto-apply
- Prioritize mutations by estimated impact
- Be conservative - small changes are better than large ones
"""


async def propose_mutations(
    observation_report: ObservationReport,
) -> StrategyProposal:
    """Generate mutation proposals based on observation report.

    Args:
        observation_report: Report from L1 Observer

    Returns:
        StrategyProposal with prioritized mutations
    """
    logger.info("strategist_starting")

    # Load current genome and constitution
    genome = get_genome()
    constitution = get_constitution()

    # Prepare prompt
    prompt = STRATEGIST_PROMPT.format(
        genome_yaml=yaml.dump(genome.to_dict(), default_flow_style=False),
        constitution_yaml=yaml.dump(constitution.to_dict(), default_flow_style=False),
        observation_json=observation_report.model_dump_json(indent=2),
    )

    # Get LLM client
    client = get_llm_client("synthesis")  # Use synthesis config for L1 strategist

    # Call LLM
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4000,
        temperature=0.3,  # Lower temperature for more consistent analysis
    )

    get_token_tracker().log_call(
        "l1_strategist",
        response.model,
        response.input_tokens,
        response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    # Parse response
    content = response.content

    # Extract JSON from response
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
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("strategist_parse_error", error=str(e))
        # Return empty proposal on parse error
        return StrategyProposal(
            based_on_observation=observation_report.generated_at.isoformat(),
            mutations=[],
            rationale="Failed to parse LLM response",
            expected_outcomes=[],
            risks=["Parse error - no mutations proposed"],
        )

    # Convert to Mutation objects
    mutations = []
    for m_data in data.get("mutations", [])[:3]:  # Max 3 per constitution
        try:
            mutation = Mutation(
                type=MutationType(m_data["type"]),
                target_path=m_data["target_path"],
                old_value=m_data["old_value"],
                new_value=m_data["new_value"],
                description=m_data["description"],
                justification=m_data["justification"],
                estimated_impact=m_data["estimated_impact"],
                risk=MutationRisk(m_data.get("risk", "medium")),
                observation_summary=m_data.get("observation_summary"),
                based_on_run_id=(
                    observation_report.analyzed_run_ids[0]
                    if observation_report.analyzed_run_ids else None
                ),
            )
            mutations.append(mutation)
        except (KeyError, ValueError) as e:
            logger.warning("mutation_parse_error", error=str(e), mutation=m_data)
            continue

    proposal = StrategyProposal(
        based_on_observation=observation_report.generated_at.isoformat(),
        mutations=mutations,
        rationale=data.get("rationale", ""),
        expected_outcomes=data.get("expected_outcomes", []),
        risks=data.get("risks", []),
    )

    logger.info(
        "strategist_complete",
        mutations_proposed=len(mutations),
        mutation_types=[m.type.value for m in mutations],
    )

    return proposal


def validate_mutation_against_constitution(
    mutation: Mutation,
    constitution_dict: dict,
) -> tuple[bool, list[str]]:
    """Check if a mutation respects constitution limits.

    Args:
        mutation: The mutation to validate
        constitution_dict: Constitution as dictionary

    Returns:
        Tuple of (is_valid, list of concerns)
    """
    concerns = []
    safety = constitution_dict.get("safety", {})

    # Check chaos_floor
    if mutation.target_path == "randomness.chaos_floor":
        min_chaos = safety.get("chaos_floor", 0.10)
        if mutation.new_value < min_chaos:
            concerns.append(
                f"Cannot reduce chaos_floor below {min_chaos} (constitution limit)"
            )
            return False, concerns

    # Check if human approval is required
    human_required = safety.get("human_approval_required_for", [])

    if mutation.type == MutationType.SOURCE_MODIFICATION:
        if "new_data_source" in human_required:
            concerns.append("Source modification requires human approval")
            return False, concerns

    if mutation.type == MutationType.PROMPT_MODIFICATION:
        # Prompt changes are generally allowed but flagged
        concerns.append("Prompt modification - review recommended")

    return True, concerns
