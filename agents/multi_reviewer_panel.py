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
from llm.json_parse import complete_json
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


class MetaReviewOutput(TypedDict, total=False):
    """Output from the meta-reviewer."""

    consensus_score: float
    verdict: str
    key_consensus: list[str]
    key_disagreements: list[str]
    critical_path: str
    final_recommendation: str
    brief_quality_gate: bool
    revision_guidance: list[str]
    # Audit trail when Python thresholds override the LLM verdict
    llm_verdict: str
    llm_consensus_score: float
    verdict_override_reason: str


class PanelOutput(TypedDict):
    """Combined output of the full panel."""

    reviews: list[ReviewerOutput]
    meta_review: MetaReviewOutput


# ── Consensus + verdict thresholds ────────────────────────────────
#
# The LLM's self-reported consensus_score and verdict are replaced by a
# Python computation: a confidence-weighted mean of reviewer scores, then
# threshold-based routing. This keeps the LLM responsible for the written
# synthesis (final_recommendation, critical_path, consensus/disagreements)
# but removes its ability to loop indefinitely on revise_and_resubmit.

PUBLISH_THRESHOLD = 6.5         # consensus >= 6.5 at iter 1 → publish_brief (S8.4, was 7.0)
REJECT_THRESHOLD = 4.5          # consensus <  4.5          → reject (unchanged)
ITER2_PUBLISH_THRESHOLD = 5.5   # at iter 2: >= 5.5 publish, else reject (S8.4, was 6.0)


# ── Relative selection gate (S9.3, 2026-07-21) ────────────────────
#
# The absolute thresholds above let 100% of briefs publish (26/26 on the
# 2026-07-20 batch). A mixed-batch test on 5 poubelle + 3 a_tester
# hypotheses measured what the panel can actually do:
#
#   poubelle  n=5  mean 5.95  range 5.46-6.36
#   a_tester  n=3  mean 6.42  range 6.18-6.62
#   AUC 0.933 — the panel RANKS correctly, one inversion.
#
# So the panel is not broken; ITER2_PUBLISH_THRESHOLD (5.5) is simply
# parked below the entire poubelle band. Rather than re-tuning that
# constant — the S8.4 trap of calibrating on survivors, which only ever
# ratchets the gate down — publication is decided relative to recent
# panel output: publish the top (1 - SELECTION_PERCENTILE) of a trailing
# window. Relative selection is what an AUC of 0.933 licenses; it needs
# no absolute calibration and self-corrects if the panel's scale drifts.
#
# The absolute floor is the safety net for the case the mixed-batch test
# does NOT cover: it is bimodal, so it proves separation between
# poubelle-tier and a_tester-tier but not ranking *within* the all-🔥
# stream, which is the only regime this gate runs in. On an all-mediocre
# batch, a purely relative rule would still publish its top slice. The
# floor is set at the poubelle band's upper edge measured in the test.

SELECTION_PERCENTILE = 0.70   # publish the top 30% of the trailing window
SELECTION_WINDOW = 20         # number of recent scored briefs considered
SELECTION_FLOOR = 6.0         # never publish below this, whatever the window
SELECTION_MIN_SAMPLES = 8     # below this, fall back to the floor alone


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile of ``values`` at quantile ``q``.

    Args:
        values: Sample values; order is irrelevant. Must not be empty.
        q: Quantile in [0, 1].

    Returns:
        The interpolated percentile.

    Raises:
        ValueError: If ``values`` is empty.
    """
    if not values:
        raise ValueError("percentile() requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def selection_threshold(recent_scores: list[float]) -> tuple[float, str]:
    """Effective publication threshold from recent panel output.

    Combines the relative rule (percentile of the trailing window) with
    the absolute floor, taking whichever is stricter. Early in the
    corpus' life — fewer than ``SELECTION_MIN_SAMPLES`` scored briefs —
    the percentile is too noisy to trust and the floor applies alone.

    Args:
        recent_scores: Consensus scores of recent scored briefs.

    Returns:
        Tuple of (threshold, reason) where reason identifies which rule
        bound, for logging and audit.
    """
    if len(recent_scores) < SELECTION_MIN_SAMPLES:
        return SELECTION_FLOOR, "floor_insufficient_history"
    relative = percentile(recent_scores, SELECTION_PERCENTILE)
    if relative >= SELECTION_FLOOR:
        return relative, "relative_percentile"
    return SELECTION_FLOOR, "floor_binding"


def compute_consensus_score(reviews: list[ReviewerOutput]) -> float:
    """Confidence-weighted mean of reviewer scores.

    Reviewers with ``confidence == 0`` (fallbacks) are excluded. If all
    reviewers have zero confidence, returns 0.0 — the run will be rejected
    by ``threshold_verdict``.
    """
    total_weight = sum(float(r.get("confidence", 0.0)) for r in reviews)
    if total_weight <= 0:
        return 0.0
    weighted_sum = sum(
        float(r.get("overall_score", 0.0)) * float(r.get("confidence", 0.0))
        for r in reviews
    )
    return round(weighted_sum / total_weight, 2)


def threshold_verdict(consensus_score: float, iteration: int) -> str:
    """Python-side verdict from the consensus score and revision iteration.

    iter 1: publish if >= 6.5, reject if < 4.5, else revise_and_resubmit.
    iter 2+: binary — publish if >= 5.5, else reject. No revise at iter 2+.

    S8.4 (11 May 2026) recalibrated PUBLISH_THRESHOLD 7.0 -> 6.5 and
    ITER2_PUBLISH_THRESHOLD 6.0 -> 5.5 on the empirical distribution
    of 22 published briefs (April 2026). The previous 7.0 threshold
    captured only 1 of the 10 top historical briefs at iter 1
    (consensus range 6.55-7.00, median ~6.8); the new 6.5 threshold
    captures all 10. The previous 6.0 iter-2 threshold rejected
    SPORE-2026-05-10-5212d9a1 at consensus 5.84, which sits inside
    the historical published range; the new 5.5 threshold lets it
    through while still excluding consensus < 5.5 as legitimately
    weak. REJECT_THRESHOLD is unchanged.
    """
    if iteration >= 2:
        return "publish_brief" if consensus_score >= ITER2_PUBLISH_THRESHOLD else "reject"
    if consensus_score >= PUBLISH_THRESHOLD:
        return "publish_brief"
    if consensus_score < REJECT_THRESHOLD:
        return "reject"
    return "revise_and_resubmit"


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

    try:
        data, _response = await complete_json(
            client,
            [{"role": "user", "content": prompt}],
            agent=f"reviewer_{persona}",
            max_tokens=2000,
            temperature=0.5,
            tracker=tracker,
        )
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
        # confidence=0.0 so the fallback does not pollute the weighted consensus.
        return ReviewerOutput(
            reviewer_persona=persona,
            overall_score=5.0,
            verdict="weak_accept",
            strengths=["Unable to parse review"],
            weaknesses=["Review parsing failed"],
            critical_questions=[],
            recommendation="Manual review needed.",
            confidence=0.0,
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
            # confidence=0.0 so the failed reviewer is excluded from the consensus.
            valid_reviews.append(ReviewerOutput(
                reviewer_persona=persona,
                overall_score=5.0,
                verdict="weak_accept",
                strengths=["Review failed"],
                weaknesses=["Review failed due to error"],
                critical_questions=[],
                recommendation="Manual review needed.",
                confidence=0.0,
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

    # Python-computed consensus — the source of truth regardless of what the LLM writes.
    py_consensus = compute_consensus_score(reviews)
    py_verdict = threshold_verdict(py_consensus, iteration)

    try:
        data, _response = await complete_json(
            client,
            [{"role": "user", "content": prompt}],
            agent="meta_reviewer",
            max_tokens=2000,
            temperature=0.3,
            tracker=tracker,
        )
        llm_consensus = float(data.get("consensus_score", py_consensus))
        llm_verdict = str(data.get("verdict", py_verdict))

        override_reason = None
        if llm_verdict != py_verdict:
            override_reason = (
                f"Python threshold override: consensus {py_consensus:.2f} "
                f"at iter {iteration} → {py_verdict} (LLM said {llm_verdict})"
            )
            logger.info(
                "meta_reviewer_verdict_override",
                llm_verdict=llm_verdict,
                python_verdict=py_verdict,
                consensus_score=py_consensus,
                iteration=iteration,
            )

        meta: MetaReviewOutput = {
            "consensus_score": py_consensus,
            "verdict": py_verdict,
            "key_consensus": data.get("key_consensus", []),
            "key_disagreements": data.get("key_disagreements", []),
            "critical_path": data.get("critical_path", ""),
            "final_recommendation": data.get("final_recommendation", ""),
            "brief_quality_gate": bool(data.get("brief_quality_gate", py_verdict == "publish_brief")),
            "revision_guidance": data.get("revision_guidance", []),
            "llm_verdict": llm_verdict,
            "llm_consensus_score": llm_consensus,
        }
        if override_reason:
            meta["verdict_override_reason"] = override_reason

        logger.info(
            "meta_reviewer_complete",
            consensus_score=py_consensus,
            verdict=py_verdict,
            iteration=iteration,
            overridden=override_reason is not None,
        )
        return meta
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("meta_reviewer_parse_failed", error=str(exc))
        # LLM synthesis failed to parse — fall back to the Python-only decision.
        return {
            "consensus_score": py_consensus,
            "verdict": py_verdict,
            "key_consensus": ["Meta-review parsing failed"],
            "key_disagreements": [],
            "critical_path": "Manual review needed",
            "final_recommendation": (
                f"Meta-review failed to parse. Python consensus {py_consensus:.2f} "
                f"at iter {iteration} → verdict '{py_verdict}'."
            ),
            "brief_quality_gate": py_verdict == "publish_brief",
            "revision_guidance": [],
            "llm_verdict": "parse_failed",
            "llm_consensus_score": 0.0,
            "verdict_override_reason": "Meta-reviewer LLM parse failure — Python fallback",
        }


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
