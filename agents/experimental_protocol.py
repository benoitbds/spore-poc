"""Experimental Protocol Agent for SPORE post-fire pipeline.

Designs a 3-phase validation protocol (in silico, minimal experimental,
full protocol) with explicit go/no-go criteria at each stage.
"""

import json
from typing import Any, TypedDict

from agents.base import load_prompt
from agents.hypothesis_sharpening import SharpeningOutput
from llm import get_llm_client
from logging_config import get_logger, get_token_tracker

logger = get_logger("experimental_protocol")


class ProtocolOutput(TypedDict):
    """Output of the Experimental Protocol Agent."""

    protocol_title: str
    overall_timeline: str
    overall_budget_estimate: str
    phases: list[dict[str, Any]]
    phase_1_quick_start: dict[str, Any]


def _extract_json(content: str) -> dict[str, Any]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return json.loads(content.strip())


def _format_variables(variables: list[dict[str, str]]) -> str:
    """Format variable list for prompt."""
    if not variables:
        return "None specified."
    lines = []
    for v in variables:
        direction = f", expected: {v['expected_direction']}" if "expected_direction" in v else ""
        lines.append(f"- {v.get('name', '?')} ({v.get('type', '?')}): {v.get('range', '?')} {v.get('unit', '')}{direction}")
    return "\n".join(lines)


def _format_predictions(predictions: list[dict[str, str]]) -> str:
    """Format predictions for prompt."""
    if not predictions:
        return "None specified."
    lines = []
    for i, p in enumerate(predictions, 1):
        lines.append(
            f"{i}. {p.get('prediction', '?')}\n"
            f"   Bound: {p.get('quantitative_bound', '?')}\n"
            f"   Method: {p.get('measurement_method', '?')}\n"
            f"   H0: {p.get('null_hypothesis', '?')}\n"
            f"   Test: {p.get('statistical_test', '?')}"
        )
    return "\n".join(lines)


def _format_boundary_conditions(conditions: list[dict[str, str]]) -> str:
    """Format boundary conditions for prompt."""
    if not conditions:
        return "None specified."
    return "\n".join(
        f"- {c.get('condition', '?')} — {c.get('justification', '')}"
        for c in conditions
    )


def _format_evidence(papers: list[dict[str, Any]]) -> str:
    """Format evidence for prompt."""
    if not papers:
        return "No evidence papers available."
    lines = []
    for p in papers[:8]:
        doi = f" DOI:{p['doi']}" if p.get("doi") else ""
        lines.append(f"- [{p.get('year', '?')}] {p.get('title', '?')}{doi}")
    return "\n".join(lines)


async def experimental_protocol_agent(
    sharpened: SharpeningOutput,
    evidence_base: list[dict[str, Any]],
) -> ProtocolOutput:
    """Run the Experimental Protocol pipeline.

    Takes a sharpened hypothesis and designs a 3-phase validation protocol.

    Args:
        sharpened: Output from the Hypothesis Sharpening Agent.
        evidence_base: Evidence papers from Literature Grounding.

    Returns:
        ProtocolOutput with 3-phase protocol and quick-start guide.
    """
    client = get_llm_client("experimental_protocol")
    tracker = get_token_tracker()

    mechanism = sharpened["proposed_mechanism"]
    mechanism_str = (
        "Causal chain:\n"
        + "\n".join(f"  {step}" for step in mechanism.get("causal_chain", []))
        + "\nKey assumptions:\n"
        + "\n".join(f"  - {a}" for a in mechanism.get("key_assumptions", []))
        + "\nKnown unknowns:\n"
        + "\n".join(f"  - {u}" for u in mechanism.get("known_unknowns", []))
    )

    prompt_template = load_prompt("experimental_protocol")
    prompt = prompt_template.format(
        title=sharpened["title"],
        formal_statement=sharpened["formal_statement"],
        independent_variables=_format_variables(sharpened["independent_variables"]),
        dependent_variables=_format_variables(sharpened["dependent_variables"]),
        mechanism=mechanism_str,
        predictions=_format_predictions(sharpened["falsifiable_predictions"]),
        boundary_conditions=_format_boundary_conditions(sharpened["boundary_conditions"]),
        theoretical_framework=sharpened["theoretical_framework"],
        evidence_base=_format_evidence(evidence_base),
    )

    logger.info("designing_protocol", title=sharpened["title"])
    # max_tokens 5000 → 10000: 3-phase protocol JSON runs up to 18 KB
    # (~4500 tokens) on iter-2 resubmissions. Was truncated mid-string
    # at char 18512 for SPORE-2026-04-14-156f6c3a, causing
    # "Unterminated string starting at: line 179 column 35".
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=10000,
        temperature=0.4,
    )

    tracker.log_call(
        agent="experimental_protocol",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    try:
        data = _extract_json(response.content)
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("protocol_parse_failed", error=str(exc), raw=response.content[:500])
        raise ValueError(f"Failed to parse protocol output: {exc}") from exc

    output = ProtocolOutput(
        protocol_title=data.get("protocol_title", "Untitled Protocol"),
        overall_timeline=data.get("overall_timeline", "TBD"),
        overall_budget_estimate=data.get("overall_budget_estimate", "TBD"),
        phases=data.get("phases", []),
        phase_1_quick_start=data.get("phase_1_quick_start", {}),
    )

    logger.info(
        "protocol_complete",
        title=output["protocol_title"],
        n_phases=len(output["phases"]),
        can_start_today=output["phase_1_quick_start"].get("can_start_today", False),
        overall_timeline=output["overall_timeline"],
        overall_budget=output["overall_budget_estimate"],
    )

    return output
