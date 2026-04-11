"""Hypothesis model - the core output of SPORE.

Based on Design Doc section 4.3.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from models.collision import CollisionPair
from models.gap_manifest import GapManifest


class BridgeType(str, Enum):
    """Type of interdisciplinary bridge."""

    STRUCTURAL_ANALOGY = "structural_analogy"
    CAUSAL_TRANSFER = "causal_transfer"
    METHODOLOGICAL_TRANSFER = "methodological_transfer"
    CONCEPTUAL_REFRAME = "conceptual_reframe"


class HypothesisStatus(str, Enum):
    """Lifecycle status of a hypothesis."""

    GENERATED = "generated"
    CURATED = "curated"
    HUMAN_REVIEWED = "human_reviewed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class HumanFeedback(str, Enum):
    """Human feedback categories."""

    TRASH = "trash"           # Poubelle
    INTERESTING = "interesting"  # Intéressant
    WANT_TO_TEST = "want_to_test"  # Je veux tester ça


class Bridge(BaseModel):
    """The hypothesized bridge between two domains."""

    summary: str = Field(..., description="Concise description of the bridge")
    mechanism: str = Field(..., description="Explanation of the shared mechanism")
    type: BridgeType = Field(..., description="Type of bridge")

    model_config = {
        "json_schema_extra": {
            "example": {
                "summary": "Pheromone-based ant foraging patterns can optimize 5G spectrum allocation",
                "mechanism": "Both systems involve distributed agents making local decisions that converge to global optima through stigmergic feedback",
                "type": "structural_analogy",
            }
        }
    }


class Scores(BaseModel):
    """Evaluation scores from CriticAgents."""

    novelty: float = Field(
        ..., ge=0.0, le=1.0,
        description="Novelty vs existing literature"
    )
    coherence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Physical/logical coherence"
    )
    testability: float = Field(
        ..., ge=0.0, le=1.0,
        description="Is a test protocol identifiable"
    )
    impact_potential: float = Field(
        ..., ge=0.0, le=1.0,
        description="Magnitude of change if true"
    )
    hallucination_risk: float = Field(
        ..., ge=0.0, le=1.0,
        description="Risk of being a hallucination"
    )
    composite: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Weighted aggregate score"
    )

    def compute_composite(
        self,
        weights: Optional[dict[str, float]] = None
    ) -> float:
        """Compute composite score with optional custom weights."""
        if weights is None:
            weights = {
                "novelty": 0.20,
                "coherence": 0.25,
                "testability": 0.25,
                "impact_potential": 0.15,
                "hallucination_risk": -0.15,  # Negative weight
            }

        score = (
            weights.get("novelty", 0.2) * self.novelty +
            weights.get("coherence", 0.25) * self.coherence +
            weights.get("testability", 0.25) * self.testability +
            weights.get("impact_potential", 0.15) * self.impact_potential +
            weights.get("hallucination_risk", -0.15) * self.hallucination_risk
        )

        # Normalize to 0-1 range
        self.composite = max(0.0, min(1.0, score))
        return self.composite

    model_config = {
        "json_schema_extra": {
            "example": {
                "novelty": 0.72,
                "coherence": 0.85,
                "testability": 0.91,
                "impact_potential": 0.68,
                "hallucination_risk": 0.15,
                "composite": 0.76,
            }
        }
    }


class ImpactScore(str, Enum):
    """Impact score categories."""

    INCREMENTAL = "incremental"
    SIGNIFICATIF = "significatif"
    TRANSFORMATIF = "transformatif"
    CIVILISATIONNEL = "civilisationnel"


class ImpactAnalysis(BaseModel):
    """Impact analysis for non-specialists."""

    vulgarisation: str = Field(
        ...,
        description="Explanation in 3 sentences max, no jargon, accessible to high schoolers"
    )
    impact_concret: str = Field(
        ...,
        description="What changes concretely in people's lives if confirmed"
    )
    industries_impactees: list[str] = Field(
        default_factory=list,
        description="List of impacted industry sectors"
    )
    taille_marche_estimee: str = Field(
        ...,
        description="Market size order of magnitude"
    )
    horizon_temporel: str = Field(
        ...,
        description="Time to real application: Court terme (1-3 ans) / Moyen terme (3-10 ans) / Long terme (10-20 ans) / Très long terme (20+ ans)"
    )
    solutions_existantes: str = Field(
        ...,
        description="How is this problem solved today and why would this be better"
    )
    score_impact: ImpactScore = Field(
        ...,
        description="Impact level"
    )
    one_liner: str = Field(
        ...,
        description="One punchy sentence that captures the stakes"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "vulgarisation": "Des scientifiques ont découvert que certains matériaux peuvent...",
                "impact_concret": "Les batteries de téléphone pourraient durer une semaine...",
                "industries_impactees": ["Électronique", "Automobile", "Énergie"],
                "taille_marche_estimee": "500 milliards USD",
                "horizon_temporel": "Moyen terme (3-10 ans)",
                "solutions_existantes": "Aujourd'hui on utilise le lithium-ion qui...",
                "score_impact": "transformatif",
                "one_liner": "Des batteries qui se rechargent en 5 minutes pourraient tuer la station-service.",
            }
        }
    }


class Prediction(BaseModel):
    """A testable prediction derived from the hypothesis."""

    statement: str = Field(..., description="Description of the prediction")
    metric: str = Field(..., description="Measurable quantity")
    expected_range: str = Field(..., description="Expected value or range")
    differs_from_consensus: bool = Field(
        default=True,
        description="Does this differ from current consensus"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "statement": "ACO-based allocation should reduce interference by 30%",
                "metric": "Signal-to-interference ratio",
                "expected_range": "15-20 dB improvement",
                "differs_from_consensus": True,
            }
        }
    }


class Hypothesis(BaseModel):
    """A complete hypothesis generated by SPORE.

    This is the core output of the L0 pipeline.
    Based on Design Doc section 4.3.
    """

    # Identity
    id: str = Field(
        default_factory=lambda: f"SPORE-{datetime.now().strftime('%Y-%m-%d')}-{uuid4().hex[:8]}",
        description="Unique identifier"
    )
    generated_at: datetime = Field(
        default_factory=datetime.now,
        description="Generation timestamp"
    )
    genome_version: str = Field(
        default="l0_v1",
        description="Version of the genome that generated this"
    )

    # Collision origin
    collision: CollisionPair = Field(..., description="The collision that generated this")

    # The hypothesis itself
    bridge: Bridge = Field(..., description="The hypothesized bridge")

    # Evaluation
    scores: Optional[Scores] = Field(None, description="Scores from CriticAgents")

    # Predictions (falsifiability)
    predictions: list[Prediction] = Field(
        default_factory=list,
        description="Testable predictions"
    )

    # Kill condition
    kill_condition: str = Field(
        ...,
        description="Precise criterion that would invalidate this hypothesis"
    )

    # Actionability
    next_steps: list[str] = Field(
        default_factory=list,
        description="Concrete next actions"
    )
    relevant_labs: list[str] = Field(
        default_factory=list,
        description="Identified labs or researchers"
    )
    relevant_datasets: list[str] = Field(
        default_factory=list,
        description="Identified public datasets"
    )

    # Traceability
    sources_used: list[str] = Field(
        default_factory=list,
        description="Sources cited (arxiv:xxxx, doi:xxxx)"
    )
    critic_debate_log: Optional[str] = Field(
        None,
        description="Reference to debate log file"
    )

    # Gap manifest
    gap_manifest: GapManifest = Field(
        default_factory=GapManifest,
        description="Knowledge gaps identified"
    )

    # Impact analysis (filled by ImpactAgent)
    impact_analysis: Optional[ImpactAnalysis] = Field(
        None,
        description="Impact analysis for non-specialists"
    )

    # Lifecycle
    status: HypothesisStatus = Field(
        default=HypothesisStatus.GENERATED,
        description="Current status in lifecycle"
    )
    human_feedback: Optional[HumanFeedback] = Field(
        None,
        description="Human feedback (filled post-review)"
    )
    human_feedback_comment: Optional[str] = Field(
        None,
        description="Optional comment from human reviewer"
    )

    def to_yaml_dict(self) -> dict:
        """Convert to a dict suitable for YAML export."""
        return self.model_dump(
            mode="json",
            exclude_none=True,
            by_alias=True
        )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "SPORE-2026-04-04-abc12345",
                "collision": {
                    "domain_a": {"id": "D-0042", "name": "Ant Colony Optimization"},
                    "domain_b": {"id": "D-1337", "name": "5G Spectrum Allocation"},
                    "strategy": "structural_analogy",
                    "distance_score": 0.58,
                },
                "bridge": {
                    "summary": "ACO for spectrum allocation",
                    "mechanism": "Stigmergic feedback",
                    "type": "structural_analogy",
                },
                "kill_condition": "No improvement over greedy allocation in simulations",
                "status": "generated",
            }
        }
    }


class NoBridgeFound(BaseModel):
    """Explicit signal that no bridge was found for a collision.

    This is a valid output from SynthesisAgent.
    """

    collision: CollisionPair = Field(..., description="The collision attempted")
    reason: str = Field(..., description="Why no bridge was found")
    gap_manifest: GapManifest = Field(
        default_factory=GapManifest,
        description="Gaps that may explain the failure"
    )
    generated_at: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp"
    )
