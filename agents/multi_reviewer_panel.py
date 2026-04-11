"""Multi-Reviewer Panel for SPORE post-fire pipeline.

Runs 5 reviewer personas in parallel, then a meta-reviewer synthesis.
Supports a revision loop (max 2 iterations) via the Hypothesis Sharpening Agent.

Personas:
1. Methodologist — protocol rigor
2. Domain Expert — theoretical coherence
3. Contrarian — failure modes
4. Industrialist — commercial potential
5. Funding Strategist — fundability
"""

import asyncio
import json
from typing import Any, TypedDict

from agents.base import load_prompt
from agents.hypothesis_sharpening import SharpeningOutput
from agents.experimental_protocol import ProtocolOutput
from llm import get_llm_client
from logging_config import get_logger, get_token_tracker

logger = get_logger("multi_reviewer_panel")


class ReviewerOutput(TypedDict):
    """Output from a single reviewer."""

    reviewer_persona: str
    overall_score: float
    verdict: str
    strengths: list[str]
    weaknesses: list[str]
    critical_questions: list[str]
    recommendation: str
    confidence: float


class MetaReviewOutput(TypedDict):
    """Output from the meta-reviewer."""

    consensus_score: float
    verdict: str
    key_consensus: list[str]
    key_disagreements: list[str]
    critical_path: str
    final_recommendation: str
    brief_quality_gate: bool
    revision_guidance: list[str]


class PanelOutput(TypedDict):
    """Combined output of the full panel."""

    reviews: list[ReviewerOutput]
    meta_review: MetaReviewOutput


def _extract_json(content: str) -> dict[str, Any]:
    """Extract JSON from LLM response."""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return json.loads(content.strip())


def _format_variables(sharpened: SharpeningOutput) -> str:
    """Format variables for reviewer prompts."""
    lines = ["Independent:"]
    for v in sharpened["independent_variables"]:
        lines.append(f"  - {v.get('name', '?')} ({v.get('type', '?')}): {v.get('range', '?')} {v.get('unit', '')}")
    lines.append("Dependent:")
    for v in sharpened["dependent_variables"]:
        lines.append(f"  - {v.get('name', '?')} ({v.get('type', '?')}): {v.get('expected_direction', '?')} ({v.get('unit', '')})")
    return "\n".join(lines)


def _format_predictions(sharpened: SharpeningOutput) -> str:
    """Format predictions for reviewer prompts."""
    lines = []
    for i, p in enumerate(sharpened["falsifiable_predictions"], 1):
        lines.append(f"{i}. {p.get('prediction', '?')}")
        lines.append(f"   Bound: {p.get('quantitative_bound', '?')}")
        lines.append(f"   Method: {p.get('measurement_method', '?')}")
        lines.append(f"   H0: {p.get('null_hypothesis', '?')}")
    return "\n".join(lines)


def _format_mechanism(sharpened: SharpeningOutput) -> str:
    """Format mechanism for reviewer prompts."""
    m = sharpened["proposed_mechanism"]
    lines = ["Causal chain:"]
    for step in m.get("causal_chain", []):
        lines.append(f"  {step}")
    lines.append("Key assumptions:")
    for a in m.get("key_assumptions", []):
        lines.append(f"  - {a}")
    lines.append("Known unknowns:")
    for u in m.get("known_unknowns", []):
        lines.append(f"  - {u}")
    return "\n".join(lines)


def _format_protocol_summary(protocol: ProtocolOutput) -> str:
    """Format protocol summary for reviewer prompts."""
    lines = [f"Title: {protocol['protocol_title']}"]
    lines.append(f"Timeline: {protocol['overall_timeline']}")
    lines.append(f"Budget: {protocol['overall_budget_estimate']}")
    for phase in protocol["phases"]:
        pn = phase.get("phase_number", "?")
        lines.append(f"\nPhase {pn}: {phase.get('phase_name', '?')}")
        lines.append(f"  Objective: {phase.get('objective', '?')}")
        res = phase.get("required_resources", {})
        lines.append(f"  Cost: {res.get('estimated_cost', '?')} | Duration: {res.get('estimated_duration', '?')}")
        gonogo = phase.get("go_nogo_decision", {})
        lines.append(f"  GO: {gonogo.get('go_if', '?')}")
        lines.append(f"  NO-GO: {gonogo.get('nogo_if', '?')}")
    return "\n".join(lines)


def _format_protocol_full(protocol: ProtocolOutput) -> str:
    """Format full protocol for methodologist review."""
    lines = [f"Title: {protocol['protocol_title']}"]
    lines.append(f"Timeline: {protocol['overall_timeline']}")
    lines.append(f"Budget: {protocol['overall_budget_estimate']}")
    for phase in protocol["phases"]:
        pn = phase.get("phase_number", "?")
        lines.append(f"\n=== Phase {pn}: {phase.get('phase_name', '?')} ===")
        lines.append(f"Objective: {phase.get('objective', '?')}")
        lines.append(f"Methodology: {phase.get('methodology', '?')}")
        res = phase.get("required_resources", {})
        lines.append(f"Cost: {res.get('estimated_cost', '?')} | Duration: {res.get('estimated_duration', '?')}")
        for c in phase.get("success_criteria", []):
            lines.append(f"  Criterion: {c.get('metric', '?')} >= {c.get('threshold', '?')}")
        gonogo = phase.get("go_nogo_decision", {})
        lines.append(f"GO: {gonogo.get('go_if', '?')}")
        lines.append(f"NO-GO: {gonogo.get('nogo_if', '?')}")
        lines.append(f"PIVOT: {gonogo.get('pivot_if', '?')}")
        for r in phase.get("risks", []):
            lines.append(f"  Risk [{r.get('probability', '?')}]: {r.get('risk', '?')}")
    return "\n".join(lines)


def _format_evidence(papers: list[dict[str, Any]]) -> str:
    """Format evidence papers for prompts."""
    if not papers:
        return "No evidence papers."
    lines = []
    for p in papers[:8]:
        doi = f" DOI:{p['doi']}" if p.get("doi") else ""
        lines.append(f"- [{p.get('year', '?')}] {p.get('title', '?')}{doi}")
        lines.append(f"  {p.get('support_type', '?')}: {p.get('relevance', '')}")
    return "\n".join(lines)


async def _run_single_reviewer(
    persona: str,
    prompt_name: str,
    format_kwargs: dict[str, str],
) -> ReviewerOutput:
    """Run a single reviewer persona.

    Args:
        persona: Reviewer persona name.
        prompt_name: Prompt template name (without .txt).
        format_kwargs: Keyword args for prompt formatting.

    Returns:
        ReviewerOutput dict.
    """
    client = get_llm_client("multi_reviewer_panel")
    tracker = get_token_tracker()

    prompt_template = load_prompt(prompt_name)
    prompt = prompt_template.format(**format_kwargs)

    logger.info("running_reviewer", persona=persona)
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.5,
    )

    tracker.log_call(
        agent=f"reviewer_{persona}",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    try:
        data = _extract_json(response.content)
        review = ReviewerOutput(
            reviewer_persona=persona,
            overall_score=float(data.get("overall_score", 5.0)),
            verdict=data.get("verdict", "weak_accept"),
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            critical_questions=data.get("critical_questions", []),
            recommendation=data.get("recommendation", ""),
            confidence=float(data.get("confidence", 0.5)),
        )
        # Capture funding programs for funding_strategist
        if persona == "funding_strategist" and "funding_programs" in data:
            review["funding_programs"] = data["funding_programs"]  # type: ignore[typeddict-unknown-key]
        logger.info("reviewer_complete", persona=persona, score=review["overall_score"], verdict=review["verdict"])
        return review
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("reviewer_parse_failed", persona=persona, error=str(exc))
        return ReviewerOutput(
            reviewer_persona=persona,
            overall_score=5.0,
            verdict="weak_accept",
            strengths=["Unable to parse review"],
            weaknesses=["Review parsing failed"],
            critical_questions=[],
            recommendation="Manual review needed.",
            confidence=0.3,
        )


async def run_panel(
    sharpened: SharpeningOutput,
    protocol: ProtocolOutput,
    evidence_base: list[dict[str, Any]],
    counter_evidence: list[dict[str, Any]],
    novelty_assessment: dict[str, Any],
) -> list[ReviewerOutput]:
    """Run all 5 reviewers in parallel.

    Args:
        sharpened: Output from Hypothesis Sharpening Agent.
        protocol: Output from Experimental Protocol Agent.
        evidence_base: Evidence papers from Literature Grounding.
        counter_evidence: Counter-evidence from Literature Grounding.
        novelty_assessment: Novelty assessment from Literature Grounding.

    Returns:
        List of 5 ReviewerOutput dicts.
    """
    title = sharpened["title"]
    formal = sharpened["formal_statement"]
    variables = _format_variables(sharpened)
    predictions = _format_predictions(sharpened)
    mechanism = _format_mechanism(sharpened)
    protocol_summary = _format_protocol_summary(protocol)
    protocol_full = _format_protocol_full(protocol)
    evidence_str = _format_evidence(evidence_base)
    counter_str = _format_evidence(counter_evidence)
    primary_domain = sharpened.get("theoretical_framework", "the relevant field")

    # Define the 5 reviewer tasks
    tasks = [
        _run_single_reviewer("methodologist", "reviewer_methodologist", {
            "title": title,
            "formal_statement": formal,
            "variables": variables,
            "predictions": predictions,
            "protocol": protocol_full,
        }),
        _run_single_reviewer("domain_expert", "reviewer_domain_expert", {
            "title": title,
            "formal_statement": formal,
            "primary_domain": primary_domain,
            "mechanism": mechanism,
            "evidence_base": evidence_str,
            "novelty_assessment": json.dumps(novelty_assessment, indent=2, ensure_ascii=False),
        }),
        _run_single_reviewer("contrarian", "reviewer_contrarian", {
            "title": title,
            "formal_statement": formal,
            "mechanism": mechanism,
            "counter_evidence": counter_str,
            "predictions": predictions,
        }),
        _run_single_reviewer("industrialist", "reviewer_industrialist", {
            "title": title,
            "formal_statement": formal,
            "primary_domain": primary_domain,
            "protocol_summary": protocol_summary,
            "predictions": predictions,
        }),
        _run_single_reviewer("funding_strategist", "reviewer_funding_strategist", {
            "title": title,
            "formal_statement": formal,
            "primary_domain": primary_domain,
            "protocol_summary": protocol_summary,
            "budget": protocol["overall_budget_estimate"],
            "timeline": protocol["overall_timeline"],
        }),
    ]

    logger.info("panel_starting", reviewer_count=5)
    reviews = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle any exceptions
    valid_reviews: list[ReviewerOutput] = []
    for i, result in enumerate(reviews):
        if isinstance(result, Exception):
            persona = ["methodologist", "domain_expert", "contrarian", "industrialist", "funding_strategist"][i]
            logger.error("reviewer_failed", persona=persona, error=str(result))
            valid_reviews.append(ReviewerOutput(
                reviewer_persona=persona,
                overall_score=5.0,
                verdict="weak_accept",
                strengths=["Review failed"],
                weaknesses=["Review failed due to error"],
                critical_questions=[],
                recommendation="Manual review needed.",
                confidence=0.2,
            ))
        else:
            valid_reviews.append(result)

    logger.info("panel_complete", review_count=len(valid_reviews))
    return valid_reviews


async def run_meta_reviewer(
    reviews: list[ReviewerOutput],
    sharpened: SharpeningOutput,
    iteration: int = 1,
) -> MetaReviewOutput:
    """Run the meta-reviewer to synthesize the panel.

    Args:
        reviews: List of 5 ReviewerOutput dicts.
        sharpened: Sharpened hypothesis for context.
        iteration: Current revision iteration (1 or 2).

    Returns:
        MetaReviewOutput with verdict and synthesis.
    """
    client = get_llm_client("multi_reviewer_panel")
    tracker = get_token_tracker()

    prompt_template = load_prompt("meta_reviewer")
    prompt = prompt_template.format(
        reviews_json=json.dumps([dict(r) for r in reviews], indent=2, ensure_ascii=False),
        title=sharpened["title"],
        formal_statement=sharpened["formal_statement"],
        iteration=iteration,
    )

    logger.info("meta_reviewer_starting", iteration=iteration)
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.3,
    )

    tracker.log_call(
        agent="meta_reviewer",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    try:
        data = _extract_json(response.content)
        meta = MetaReviewOutput(
            consensus_score=float(data.get("consensus_score", 5.0)),
            verdict=data.get("verdict", "publish_brief"),
            key_consensus=data.get("key_consensus", []),
            key_disagreements=data.get("key_disagreements", []),
            critical_path=data.get("critical_path", ""),
            final_recommendation=data.get("final_recommendation", ""),
            brief_quality_gate=data.get("brief_quality_gate", True),
            revision_guidance=data.get("revision_guidance", []),
        )
        logger.info(
            "meta_reviewer_complete",
            consensus_score=meta["consensus_score"],
            verdict=meta["verdict"],
            quality_gate=meta["brief_quality_gate"],
        )
        return meta
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("meta_reviewer_parse_failed", error=str(exc))
        # Compute fallback consensus score
        total_weighted = sum(r["overall_score"] * r["confidence"] for r in reviews)
        total_confidence = sum(r["confidence"] for r in reviews)
        consensus = total_weighted / total_confidence if total_confidence > 0 else 5.0
        return MetaReviewOutput(
            consensus_score=consensus,
            verdict="publish_brief" if consensus >= 5.0 else "reject",
            key_consensus=["Meta-review parsing failed"],
            key_disagreements=[],
            critical_path="Manual review needed",
            final_recommendation="Meta-review failed to parse. Publishing based on consensus score.",
            brief_quality_gate=consensus >= 5.0,
            revision_guidance=[],
        )


async def full_panel_review(
    sharpened: SharpeningOutput,
    protocol: ProtocolOutput,
    evidence_base: list[dict[str, Any]],
    counter_evidence: list[dict[str, Any]],
    novelty_assessment: dict[str, Any],
    iteration: int = 1,
) -> PanelOutput:
    """Run the full panel: 5 reviewers + meta-reviewer.

    Args:
        sharpened: Output from Hypothesis Sharpening Agent.
        protocol: Output from Experimental Protocol Agent.
        evidence_base: Evidence papers.
        counter_evidence: Counter-evidence.
        novelty_assessment: Novelty assessment.
        iteration: Revision iteration (1 or 2).

    Returns:
        PanelOutput with all reviews and meta-review.
    """
    reviews = await run_panel(
        sharpened, protocol, evidence_base, counter_evidence, novelty_assessment
    )
    meta = await run_meta_reviewer(reviews, sharpened, iteration)

    return PanelOutput(reviews=reviews, meta_review=meta)
