"""Reviewer Agent for SPORE.

Provides automated scientific review of hypotheses.
Used for auto-feedback functionality in the Review dashboard.
"""

import json
from typing import Any, Optional

from pydantic import BaseModel, Field

from agents.base import load_prompt
from llm import get_llm_client, LLMResponse
from models.hypothesis import Hypothesis, HumanFeedback
from logging_config import get_logger

logger = get_logger("reviewer_agent")


class AutoFeedbackScores(BaseModel):
    """Scores from the auto-reviewer."""

    originalite: float = Field(..., ge=0.0, le=1.0, description="Originality score")
    faisabilite: float = Field(..., ge=0.0, le=1.0, description="Feasibility score")
    coherence: float = Field(..., ge=0.0, le=1.0, description="Coherence score")
    impact_realisme: float = Field(..., ge=0.0, le=1.0, description="Impact realism score")


class AutoFeedback(BaseModel):
    """Auto-generated feedback from the ReviewerAgent."""

    verdict: str = Field(..., description="poubelle | intéressant | a_tester")
    comment: str = Field(..., description="Justification comment")
    scores: AutoFeedbackScores = Field(..., description="Detailed scores")
    override_reason: Optional[str] = Field(
        None,
        description="Post-processing override reason (set when Python rules override the LLM verdict)",
    )

    def to_human_feedback(self) -> HumanFeedback:
        """Convert verdict to HumanFeedback enum."""
        mapping = {
            "poubelle": HumanFeedback.TRASH,
            "intéressant": HumanFeedback.INTERESTING,
            "interessant": HumanFeedback.INTERESTING,
            "a_tester": HumanFeedback.WANT_TO_TEST,
        }
        return mapping.get(self.verdict.lower(), HumanFeedback.INTERESTING)


def _get_reviewer_prompt() -> str:
    """Load the reviewer system prompt from file."""
    return load_prompt("reviewer")


def evaluate_override(
    composite: float,
    hallucination_risk: float,
    current_verdict: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """Mechanical post-LLM override. Returns ``(verdict, reason)`` or ``None``.

    Recalibrated S6.4 (2 May 2026) — see BACKLOG hotfix note. The old
    rule ``composite < 0.35 OR halluc > 0.40`` was killing 12 / 13 curated
    hypotheses over the past 8 days, against the historical 20 %
    poubelle target. Diagnosis: DeepSeek drift on hallucination_risk
    attribution after the 200 → 500 domain corpus expansion shifted the
    distribution upwards.

    The rule keeps three orthogonal kill paths, each requiring a
    genuinely strong signal rather than a single barely-above-threshold
    metric:

    1. **Very low composite** (< 0.35): the hypothesis as a whole is weak.
    2. **Stacked low composite + high halluc** (composite < 0.42 AND
       halluc > 0.55): mediocre on its own + bibliographic risk.
    3. **Extreme hallucination_risk** (> 0.65): even a strong composite
       doesn't rescue a hypothesis the critic flagged as fabrication-prone.

    S8.1 (8 May 2026) adds a symmetric promotion path. Diagnosis: zero
    a_tester verdicts produced since 24 April. The LLM reviewer scores
    "intéressant" with composite/halluc inside the historical a_tester
    range but does not promote the verdict on its own. Calibrated on
    16 a_tester hypotheses produced 7-23 April 2026: composite spans
    0.411-0.518 (avg 0.471), halluc spans 0.10-0.50 (avg 0.35).

    4. **Promotion intéressant -> a_tester** (S8.1): when the LLM
       verdict is ``intéressant`` and ``composite >= 0.45`` and
       ``hallucination_risk <= 0.40``, promote to ``a_tester``. The
       0.45 floor captures 14/16 historical a_tester cases; the 0.40
       halluc ceiling is conservative (avg historical 0.35) and
       excludes any signal of bibliographic fabrication.

    The ``current_verdict`` parameter is optional with default ``None``
    — call sites that do not need the promotion path can keep the
    two-arg signature, in which case only the kill paths apply.

    Returns ``None`` when the LLM verdict should stand.
    """
    if composite < 0.35:
        return (
            "poubelle",
            f"Post-processing: composite {composite:.2f} < 0.35",
        )
    if composite < 0.42 and hallucination_risk > 0.55:
        return (
            "poubelle",
            f"Post-processing: low composite ({composite:.2f}) + "
            f"high hallucination ({hallucination_risk:.2f})",
        )
    if hallucination_risk > 0.65:
        return (
            "poubelle",
            f"Post-processing: hallucination {hallucination_risk:.2f} > 0.65",
        )
    # S8.1 — symmetric promotion path. Only fires when the LLM stopped
    # at "intéressant" with scores inside the historical a_tester
    # range. Idempotent on already-a_tester or already-poubelle verdicts.
    if (
        current_verdict == "intéressant"
        and composite >= 0.45
        and hallucination_risk <= 0.40
    ):
        return (
            "a_tester",
            f"Post-processing: composite {composite:.2f} >= 0.45 + "
            f"hallucination {hallucination_risk:.2f} <= 0.40 "
            "(intéressant -> a_tester)",
        )
    return None


def format_hypothesis_for_review(hypothesis: Hypothesis) -> str:
    """Format hypothesis data for the reviewer prompt."""
    lines = []

    # Collision
    lines.append("## COLLISION")
    lines.append(f"Domaine A: {hypothesis.collision.domain_a.name}")
    if hypothesis.collision.domain_a.parent_domain:
        lines.append(f"  Discipline: {hypothesis.collision.domain_a.parent_domain}")
    lines.append(f"Domaine B: {hypothesis.collision.domain_b.name}")
    if hypothesis.collision.domain_b.parent_domain:
        lines.append(f"  Discipline: {hypothesis.collision.domain_b.parent_domain}")
    lines.append(f"Distance sémantique: {hypothesis.collision.distance_score:.3f}")
    lines.append("")

    # Bridge
    lines.append("## BRIDGE")
    lines.append(f"Type: {hypothesis.bridge.type.value}")
    lines.append(f"Résumé: {hypothesis.bridge.summary}")
    lines.append(f"Mécanisme: {hypothesis.bridge.mechanism}")
    lines.append("")

    # Predictions
    if hypothesis.predictions:
        lines.append("## PRÉDICTIONS TESTABLES")
        for i, pred in enumerate(hypothesis.predictions, 1):
            lines.append(f"{i}. {pred.statement}")
            lines.append(f"   Métrique: {pred.metric}")
            lines.append(f"   Range attendu: {pred.expected_range}")
        lines.append("")

    # Kill condition
    lines.append("## CONDITION D'INVALIDATION")
    lines.append(hypothesis.kill_condition)
    lines.append("")

    # Scores from critics
    if hypothesis.scores:
        lines.append("## SCORES DU DÉBAT ADVERSARIAL")
        lines.append(f"Nouveauté: {hypothesis.scores.novelty:.2f}")
        lines.append(f"Cohérence: {hypothesis.scores.coherence:.2f}")
        lines.append(f"Testabilité: {hypothesis.scores.testability:.2f}")
        lines.append(f"Impact potentiel: {hypothesis.scores.impact_potential:.2f}")
        lines.append(f"Risque hallucination: {hypothesis.scores.hallucination_risk:.2f}")
        if hypothesis.scores.composite:
            lines.append(f"Score composite: {hypothesis.scores.composite:.2f}")
        lines.append("")

    # Gap manifest
    if hypothesis.gap_manifest:
        gaps = hypothesis.gap_manifest
        if gaps.data_gaps or gaps.competence_gaps or gaps.epistemic_gaps:
            lines.append("## GAPS DE CONNAISSANCE")
            if gaps.data_gaps:
                lines.append("Gaps de données:")
                for gap in gaps.data_gaps[:3]:
                    lines.append(f"  - {gap.description} (criticité: {gap.criticality.value})")
            if gaps.competence_gaps:
                lines.append("Gaps de compétence:")
                for gap in gaps.competence_gaps[:3]:
                    lines.append(f"  - {gap.description}")
            if gaps.epistemic_gaps:
                lines.append("Gaps épistémiques:")
                for gap in gaps.epistemic_gaps[:3]:
                    lines.append(f"  - {gap.zone}: {gap.signal}")
            lines.append("")

    # Impact analysis
    if hypothesis.impact_analysis:
        impact = hypothesis.impact_analysis
        lines.append("## ANALYSE D'IMPACT")
        lines.append(f"One-liner: {impact.one_liner}")
        lines.append(f"Vulgarisation: {impact.vulgarisation}")
        lines.append(f"Impact concret: {impact.impact_concret}")
        lines.append(f"Industries impactées: {', '.join(impact.industries_impactees)}")
        lines.append(f"Taille marché estimée: {impact.taille_marche_estimee}")
        lines.append(f"Horizon temporel: {impact.horizon_temporel}")
        lines.append(f"Score impact: {impact.score_impact.value}")
        lines.append("")

    return "\n".join(lines)


async def review_hypothesis(hypothesis: Hypothesis) -> AutoFeedback:
    """Review a hypothesis using the ReviewerAgent.

    Args:
        hypothesis: The hypothesis to review

    Returns:
        AutoFeedback with verdict, comment, and scores
    """
    # Get LLM client - use 'reviewer' agent config, fallback to 'synthesis' config
    try:
        client = get_llm_client("reviewer", with_fallback=True)
    except Exception:
        # If reviewer not configured, use synthesis config
        client = get_llm_client("synthesis", with_fallback=True)

    # Format hypothesis for review
    hypothesis_text = format_hypothesis_for_review(hypothesis)

    messages = [
        {"role": "user", "content": f"Voici l'hypothèse à évaluer:\n\n{hypothesis_text}"}
    ]

    logger.info(
        "reviewing_hypothesis",
        hypothesis_id=hypothesis.id,
    )

    response = await client.complete(
        messages=messages,
        system=_get_reviewer_prompt(),
        max_tokens=1000,
        temperature=0.3,  # Low temperature for consistent evaluation
    )

    # Parse JSON response
    content = response.content.strip()

    # Extract JSON from response (handle markdown code blocks)
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(content)
        feedback = AutoFeedback(
            verdict=data.get("verdict", "intéressant"),
            comment=data.get("comment", "Pas de commentaire"),
            scores=AutoFeedbackScores(
                originalite=float(data.get("scores", {}).get("originalite", 0.5)),
                faisabilite=float(data.get("scores", {}).get("faisabilite", 0.5)),
                coherence=float(data.get("scores", {}).get("coherence", 0.5)),
                impact_realisme=float(data.get("scores", {}).get("impact_realisme", 0.5)),
            ),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(
            "failed_to_parse_review_response",
            hypothesis_id=hypothesis.id,
            error=str(e),
            response=content[:500],
        )
        # Fail-closed: if the LLM response cannot be parsed, the hypothesis
        # is rejected rather than silently routed to "intéressant".
        logger.warning(
            "reviewer_parse_fallback",
            hypothesis_id=hypothesis.id,
            error=str(e),
        )
        return AutoFeedback(
            verdict="poubelle",
            comment=f"Parse error — fail-closed. Erreur: {str(e)[:80]}. Réponse brute: {content[:150]}",
            scores=AutoFeedbackScores(
                originalite=0.0,
                faisabilite=0.0,
                coherence=0.0,
                impact_realisme=0.0,
            ),
            override_reason="Parse error — fail closed",
        )

    # ── Post-processing: mechanical overrides independent of the LLM ───
    composite = hypothesis.scores.composite if hypothesis.scores and hypothesis.scores.composite is not None else 0.5
    hallucination_risk = hypothesis.scores.hallucination_risk if hypothesis.scores else 0.0

    original_verdict = feedback.verdict
    override = evaluate_override(
        composite,
        hallucination_risk,
        current_verdict=original_verdict,
    )
    if override is not None:
        new_verdict, new_reason = override
        feedback.verdict = new_verdict
        feedback.override_reason = new_reason

    if feedback.verdict != original_verdict:
        logger.warning(
            "reviewer_verdict_override",
            hypothesis_id=hypothesis.id,
            original=original_verdict,
            new=feedback.verdict,
            reason=feedback.override_reason,
            composite=composite,
            hallucination_risk=hallucination_risk,
        )

    logger.info(
        "review_complete",
        hypothesis_id=hypothesis.id,
        verdict=feedback.verdict,
        overridden=feedback.override_reason is not None,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )

    return feedback
