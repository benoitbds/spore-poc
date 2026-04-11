"""Reviewer Agent for SPORE.

Provides automated scientific review of hypotheses.
Used for auto-feedback functionality in the Review dashboard.
"""

import json
from typing import Any

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
        # Return a default response
        feedback = AutoFeedback(
            verdict="intéressant",
            comment=f"Erreur d'analyse: {str(e)}. Réponse brute: {content[:200]}",
            scores=AutoFeedbackScores(
                originalite=0.5,
                faisabilite=0.5,
                coherence=0.5,
                impact_realisme=0.5,
            ),
        )

    logger.info(
        "review_complete",
        hypothesis_id=hypothesis.id,
        verdict=feedback.verdict,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )

    return feedback
