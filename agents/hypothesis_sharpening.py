"""Hypothesis Sharpening Agent for SPORE post-fire pipeline.

Transforms a narrative hypothesis into a rigorous scientific formulation
with explicit variables, quantitative predictions, and falsifiable statements.
"""

import json
from typing import Any, TypedDict

from agents.base import load_prompt
from llm import get_llm_client
from llm.json_parse import complete_json
from logging_config import get_logger, get_token_tracker

logger = get_logger("hypothesis_sharpening")


class SharpeningInput(TypedDict):
    """Input for the Hypothesis Sharpening Agent."""

    hypothesis: str
    domains: list[str]
    mechanisms: str
    novelty_assessment: dict[str, Any]
    evidence_base: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]


class SharpeningOutput(TypedDict):
    """Output of the Hypothesis Sharpening Agent."""

    title: str
    formal_statement: str
    independent_variables: list[dict[str, str]]
    dependent_variables: list[dict[str, str]]
    proposed_mechanism: dict[str, Any]
    falsifiable_predictions: list[dict[str, str]]
    boundary_conditions: list[dict[str, str]]
    theoretical_framework: str


def _format_evidence_for_prompt(papers: list[dict[str, Any]]) -> str:
    """Format evidence papers for inclusion in prompt."""
    if not papers:
        return "No evidence papers available."
    lines = []
    for p in papers[:10]:
        doi = f" DOI:{p['doi']}" if p.get("doi") else ""
        lines.append(
            f"- [{p.get('year', '?')}] {p.get('title', '?')}{doi}\n"
            f"  Type: {p.get('support_type', '?')} | {p.get('relevance', '')}\n"
            f"  Key finding: {p.get('key_finding', 'N/A')}"
        )
    return "\n".join(lines)


async def hypothesis_sharpening_agent(input_data: SharpeningInput) -> SharpeningOutput:
    """Run the Hypothesis Sharpening pipeline.

    Takes a grounded hypothesis and produces a rigorous scientific
    formulation with variables, predictions, and boundary conditions.

    Args:
        input_data: Hypothesis + grounding results.

    Returns:
        SharpeningOutput with formal scientific formulation.
    """
    client = get_llm_client("hypothesis_sharpening")
    tracker = get_token_tracker()

    prompt_template = load_prompt("hypothesis_sharpening")
    prompt = prompt_template.format(
        hypothesis=input_data["hypothesis"],
        domains=", ".join(input_data["domains"]),
        mechanisms=input_data["mechanisms"],
        evidence_base=_format_evidence_for_prompt(input_data["evidence_base"]),
        counter_evidence=_format_evidence_for_prompt(input_data["counter_evidence"]),
        novelty_assessment=json.dumps(input_data["novelty_assessment"], indent=2, ensure_ascii=False),
    )

    logger.info("sharpening_hypothesis", domains=input_data["domains"])
    # max_tokens raised from 4000 to 8000: the sharpening JSON routinely
    # runs 7-8 KB (≈ 3500-4000 output tokens) and was being truncated
    # mid-JSON, producing unparseable output and killing the post-fire
    # pipeline silently. See hypothesis_sharpening_parse_failed forensics
    # for SPORE-2026-04-14-156f6c3a and SPORE-2026-04-14-98842ca5.
    try:
        data, _response = await complete_json(
            client,
            [{"role": "user", "content": prompt}],
            agent="hypothesis_sharpening",
            max_tokens=8000,
            temperature=0.4,
            tracker=tracker,
        )
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("sharpening_parse_failed", error=str(exc))
        raise ValueError(f"Failed to parse sharpening output: {exc}") from exc

    output = SharpeningOutput(
        title=data.get("title", "Untitled"),
        formal_statement=data.get("formal_statement", ""),
        independent_variables=data.get("independent_variables", []),
        dependent_variables=data.get("dependent_variables", []),
        proposed_mechanism=data.get("proposed_mechanism", {}),
        falsifiable_predictions=data.get("falsifiable_predictions", []),
        boundary_conditions=data.get("boundary_conditions", []),
        theoretical_framework=data.get("theoretical_framework", ""),
    )

    logger.info(
        "sharpening_complete",
        title=output["title"],
        n_predictions=len(output["falsifiable_predictions"]),
        n_independent_vars=len(output["independent_variables"]),
        n_dependent_vars=len(output["dependent_variables"]),
        n_boundary_conditions=len(output["boundary_conditions"]),
    )

    return output
