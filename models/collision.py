"""Collision models for domain pair collisions."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from models.domain import Domain


class CollisionStrategy(str, Enum):
    """Strategy used to generate the collision."""

    SEMANTIC_DISTANCE = "semantic_distance"
    STRUCTURAL_ANALOGY = "structural_analogy"
    ANOMALY_GUIDED = "anomaly_guided"
    HISTORICAL_TEMPLATE = "historical_template"


class CollisionPair(BaseModel):
    """A pair of domains selected for collision.

    Based on Design Doc section 4.3 collision schema.
    """

    domain_a: Domain = Field(..., description="First domain in the collision")
    domain_b: Domain = Field(..., description="Second domain in the collision")
    strategy: CollisionStrategy = Field(
        default=CollisionStrategy.SEMANTIC_DISTANCE,
        description="Strategy used to select this collision"
    )
    distance_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Semantic distance between domains (0=identical, 1=maximally distant)"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "domain_a": {"id": "D-0042", "name": "Ant Colony Optimization"},
                "domain_b": {"id": "D-1337", "name": "5G Spectrum Allocation"},
                "strategy": "structural_analogy",
                "distance_score": 0.58,
            }
        }
    }


class Collision(BaseModel):
    """A collision with enriched context, ready for synthesis.

    This is the enriched version of CollisionPair with
    context from literature sources.
    """

    pair: CollisionPair = Field(..., description="The domain pair")
    context_a: list[str] = Field(
        default_factory=list,
        description="Relevant abstracts for domain A"
    )
    context_b: list[str] = Field(
        default_factory=list,
        description="Relevant abstracts for domain B"
    )
    enrichment_source: str = Field(
        default="semantic_scholar",
        description="Source used for context enrichment"
    )

    @property
    def domain_a(self) -> Domain:
        return self.pair.domain_a

    @property
    def domain_b(self) -> Domain:
        return self.pair.domain_b

    @property
    def distance_score(self) -> float:
        return self.pair.distance_score
