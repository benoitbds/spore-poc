"""Explorer Agent for SPORE.

Generates random domain collisions and enriches them with context
from literature sources.
"""

import asyncio
from typing import Any

from knowledge.domain_map import get_domain_map
from knowledge.semantic_scholar import get_context as get_ss_context
from knowledge.arxiv_client import get_context as get_arxiv_context
from models.collision import Collision, CollisionPair
from models.domain import Domain
from agents.base import PipelineState
from logging_config import get_logger

logger = get_logger("explorer_agent")


async def enrich_domain_context(
    domain: Domain,
    max_papers: int = 3,
) -> list[str]:
    """Enrich a domain with context from literature sources.

    Fetches abstracts from both Semantic Scholar and ArXiv,
    combining the results for richer context.

    Args:
        domain: The domain to enrich
        max_papers: Max papers per source

    Returns:
        Combined list of abstracts
    """
    # Fetch from both sources in parallel
    ss_task = get_ss_context(
        domain.name,
        domain.key_concepts,
        max_papers=max_papers,
    )
    arxiv_task = get_arxiv_context(
        domain.name,
        domain.key_concepts,
        max_papers=max_papers,
    )

    try:
        results = await asyncio.gather(ss_task, arxiv_task, return_exceptions=True)

        abstracts = []
        for result in results:
            if isinstance(result, list):
                abstracts.extend(result)
            elif isinstance(result, Exception):
                logger.warning(
                    "context_fetch_error",
                    domain=domain.name,
                    error=str(result),
                )

        return abstracts

    except Exception as e:
        logger.error("enrich_domain_failed", domain=domain.name, error=str(e))
        return []


async def enrich_collision(
    collision_pair: CollisionPair,
    max_papers_per_domain: int = 3,
) -> Collision:
    """Enrich a collision pair with literature context.

    Args:
        collision_pair: The raw collision pair
        max_papers_per_domain: Max papers to fetch per domain

    Returns:
        Enriched Collision object with populated Domain.context_abstracts
    """
    logger.info(
        "enriching_collision",
        domain_a=collision_pair.domain_a.name,
        domain_b=collision_pair.domain_b.name,
    )

    # Fetch context for both domains in parallel
    context_a_task = enrich_domain_context(
        collision_pair.domain_a,
        max_papers=max_papers_per_domain,
    )
    context_b_task = enrich_domain_context(
        collision_pair.domain_b,
        max_papers=max_papers_per_domain,
    )

    context_a, context_b = await asyncio.gather(context_a_task, context_b_task)

    logger.info(
        "context_fetched",
        domain_a=collision_pair.domain_a.name,
        context_a_count=len(context_a),
        domain_b=collision_pair.domain_b.name,
        context_b_count=len(context_b),
    )

    # Create enriched Domain objects with context_abstracts populated
    enriched_domain_a = collision_pair.domain_a.model_copy(
        update={"context_abstracts": context_a}
    )
    enriched_domain_b = collision_pair.domain_b.model_copy(
        update={"context_abstracts": context_b}
    )

    # Create new CollisionPair with enriched domains
    enriched_pair = CollisionPair(
        domain_a=enriched_domain_a,
        domain_b=enriched_domain_b,
        strategy=collision_pair.strategy,
        distance_score=collision_pair.distance_score,
    )

    return Collision(
        pair=enriched_pair,
        context_a=context_a,
        context_b=context_b,
        enrichment_source="semantic_scholar+arxiv",
    )


async def explorer_agent(state: PipelineState) -> PipelineState:
    """Explorer Agent: generates and enriches domain collisions.

    This agent:
    1. Gets random collision pairs from the domain map
    2. Enriches each collision with literature context
    3. Returns the enriched collisions in the state

    Args:
        state: Current pipeline state

    Returns:
        Updated state with collision_pairs and collisions
    """
    from config import get_genome

    n_collisions = state.get("n_collisions", 10)
    distance_min = state.get("distance_min", 0.4)
    distance_max = state.get("distance_max", 0.7)

    # Get cross_discipline_ratio from genome
    genome = get_genome()
    cross_discipline_ratio = genome.randomness.get("cross_discipline_ratio", 0.7)

    # Get domain map for the specified domain
    domain = state.get("domain", "materials_science")

    logger.info(
        "explorer_starting",
        n_collisions=n_collisions,
        distance_range=f"{distance_min}-{distance_max}",
        cross_discipline_ratio=cross_discipline_ratio,
        domain=domain,
    )
    domain_map = get_domain_map(domain)

    collision_pairs = domain_map.get_multiple_collisions(
        n=n_collisions,
        distance_min=distance_min,
        distance_max=distance_max,
        unique=True,
        cross_discipline_ratio=cross_discipline_ratio,
    )

    if not collision_pairs:
        logger.error("no_collision_pairs_generated")
        state["collision_pairs"] = []
        state["collisions"] = []
        state["errors"] = state.get("errors", []) + [{
            "agent": "explorer",
            "error": "No valid collision pairs in the fertile zone",
        }]
        return state

    logger.info("collision_pairs_generated", count=len(collision_pairs))

    # Enrich collisions with context (with some parallelism, but rate-limited)
    enriched_collisions = []
    errors = state.get("errors", [])

    # Process in batches to respect rate limits
    batch_size = 3
    for i in range(0, len(collision_pairs), batch_size):
        batch = collision_pairs[i:i + batch_size]
        tasks = [enrich_collision(cp) for cp in batch]

        try:
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(batch_results):
                if isinstance(result, Collision):
                    enriched_collisions.append(result)
                else:
                    logger.warning(
                        "collision_enrichment_failed",
                        index=i + j,
                        error=str(result),
                    )
                    errors.append({
                        "agent": "explorer",
                        "collision_index": i + j,
                        "error": str(result),
                    })

        except Exception as e:
            logger.error("batch_enrichment_failed", batch_index=i, error=str(e))
            errors.append({
                "agent": "explorer",
                "batch_index": i,
                "error": str(e),
            })

    logger.info(
        "explorer_complete",
        pairs_generated=len(collision_pairs),
        collisions_enriched=len(enriched_collisions),
    )

    state["collision_pairs"] = collision_pairs
    state["collisions"] = enriched_collisions
    state["errors"] = errors

    return state
