"""Gap manifest models for tracking knowledge gaps.

Based on Design Doc section 5.3.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GapCriticality(str, Enum):
    """Criticality level of a gap."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExplorationPriority(str, Enum):
    """Priority for exploring an epistemic gap."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DataGap(BaseModel):
    """A gap in available data.

    "I know that this domain exists but I don't have the data."
    """

    domain: str = Field(..., description="Domain where data is missing")
    description: str = Field(..., description="What data is needed")
    criticality: GapCriticality = Field(
        default=GapCriticality.MEDIUM,
        description="How critical is this gap"
    )
    recurrence: int = Field(
        default=1,
        ge=1,
        description="Number of times this gap has appeared"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "domain": "crystallography",
                "description": "Need access to ICSD database for structural verification",
                "criticality": "high",
                "recurrence": 3,
            }
        }
    }


class CompetenceGap(BaseModel):
    """A gap in cognitive/computational capability.

    "I don't have the capability for this processing."
    """

    type: str = Field(..., description="Type of competence needed")
    description: str = Field(..., description="What capability is missing")
    workaround_used: Optional[str] = Field(
        None,
        description="Workaround applied (if any)"
    )
    confidence_degradation: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Impact on confidence score (0-1)"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "formal_verification",
                "description": "Cannot verify topological invariant of proposed structure",
                "workaround_used": "assumed based on analogy with known structures",
                "confidence_degradation": 0.25,
            }
        }
    }


class EpistemicGap(BaseModel):
    """A gap in awareness - unknown unknowns.

    "I don't know what I don't know in this area."
    """

    zone: str = Field(..., description="Area of potential blind spot")
    signal: str = Field(..., description="What triggered awareness of this gap")
    exploration_priority: ExplorationPriority = Field(
        default=ExplorationPriority.MEDIUM,
        description="Priority for exploring this zone"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "zone": "traditional_fermentation_practices",
                "signal": "found references suggesting relevance but couldn't evaluate depth",
                "exploration_priority": "medium",
            }
        }
    }


class GapManifest(BaseModel):
    """Complete gap manifest for a hypothesis.

    Attached to each hypothesis by the SynthesisAgent.
    Based on Design Doc section 5.3.
    """

    data_gaps: list[DataGap] = Field(
        default_factory=list,
        description="Gaps in available data"
    )
    competence_gaps: list[CompetenceGap] = Field(
        default_factory=list,
        description="Gaps in cognitive/computational capability"
    )
    epistemic_gaps: list[EpistemicGap] = Field(
        default_factory=list,
        description="Unknown unknowns - blind spots"
    )

    @property
    def total_gaps(self) -> int:
        """Total number of gaps across all categories."""
        return len(self.data_gaps) + len(self.competence_gaps) + len(self.epistemic_gaps)

    @property
    def has_critical_gaps(self) -> bool:
        """Check if any data gap is critical."""
        return any(g.criticality == GapCriticality.CRITICAL for g in self.data_gaps)

    def to_summary(self) -> str:
        """Generate a brief summary of gaps."""
        parts = []
        if self.data_gaps:
            parts.append(f"{len(self.data_gaps)} data gap(s)")
        if self.competence_gaps:
            parts.append(f"{len(self.competence_gaps)} competence gap(s)")
        if self.epistemic_gaps:
            parts.append(f"{len(self.epistemic_gaps)} epistemic gap(s)")
        return ", ".join(parts) if parts else "No gaps identified"
