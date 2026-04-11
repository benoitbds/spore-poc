"""Domain model for scientific domains."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Domain(BaseModel):
    """A scientific domain or sub-domain.

    Based on Design Doc section 4.2.
    """

    id: str = Field(..., description="Unique identifier (e.g., 'D-0042')")
    name: str = Field(..., description="Human-readable name")
    parent_domain: Optional[str] = Field(None, description="Parent domain ID or name")
    taxonomy_source: str = Field(default="custom", description="Source of taxonomy (openalex, custom)")
    embedding: Optional[list[float]] = Field(None, description="Dense vector embedding")
    key_concepts: list[str] = Field(default_factory=list, description="5-10 key concepts/keywords")
    recent_anomalies: list[str] = Field(default_factory=list, description="Recent unusual findings")
    papers_count_12m: Optional[int] = Field(None, description="Papers in last 12 months")
    last_updated: Optional[datetime] = Field(None, description="Last update timestamp")

    # Context enrichment (filled by knowledge sources)
    context_abstracts: list[str] = Field(
        default_factory=list,
        description="Relevant abstracts from literature search"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "D-0042",
                "name": "Ant Colony Optimization",
                "parent_domain": "Swarm Intelligence",
                "taxonomy_source": "openalex",
                "key_concepts": ["pheromone", "stigmergy", "foraging"],
                "recent_anomalies": ["arxiv:2403.xxxxx — unexpected convergence..."],
                "papers_count_12m": 342,
            }
        }
    }
