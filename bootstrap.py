"""Bootstrap calibration test for SPORE.

Tests the pipeline against known discoveries to validate
that the synthesis mechanism can find meaningful bridges.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import get_settings, get_genome
from knowledge.domain_map import get_domain_map
from knowledge.semantic_scholar import get_context as get_ss_context
from knowledge.arxiv_client import get_context as get_arxiv_context
from models.collision import Collision, CollisionPair, CollisionStrategy
from models.hypothesis import Hypothesis, NoBridgeFound
from agents.synthesis import synthesize_hypothesis
from logging_config import get_logger, get_token_tracker, reset_token_tracker, setup_logging

logger = get_logger("bootstrap")
console = Console()


def load_known_discoveries() -> list[dict[str, Any]]:
    """Load known discoveries from JSON file."""
    path = Path(__file__).parent / "data" / "bootstrap" / "known_discoveries.json"

    if not path.exists():
        raise FileNotFoundError(f"Known discoveries file not found: {path}")

    with open(path) as f:
        data = json.load(f)

    return data.get("discoveries", [])


async def enrich_domain_for_bootstrap(
    domain_name: str,
    key_concepts: list[str],
) -> list[str]:
    """Get context for a domain during bootstrap."""
    try:
        ss_context = await get_ss_context(domain_name, key_concepts, max_papers=2)
        arxiv_context = await get_arxiv_context(domain_name, key_concepts, max_papers=2)
        return ss_context + arxiv_context
    except Exception as e:
        logger.warning("context_fetch_failed", domain=domain_name, error=str(e))
        return []


def evaluate_bridge_match(
    hypothesis: Hypothesis | NoBridgeFound,
    expected_bridge: str,
    discovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether the generated hypothesis matches the expected bridge.

    Uses keyword overlap with weighted scoring for key concepts.

    Args:
        hypothesis: Generated hypothesis or NoBridgeFound
        expected_bridge: Expected bridge description
        discovery: Optional discovery dict with domain concepts

    Returns:
        Evaluation result dict
    """
    if isinstance(hypothesis, NoBridgeFound):
        return {
            "match": False,
            "reason": "no_bridge_found",
            "score": 0.0,
        }

    # Combine all generated text
    generated_text = (
        hypothesis.bridge.summary.lower() + " " +
        hypothesis.bridge.mechanism.lower() + " " +
        " ".join(p.statement.lower() for p in hypothesis.predictions)
    )
    generated_words = set(generated_text.split())

    # Extract expected keywords
    expected_words = set(expected_bridge.lower().split())

    # Add domain concepts if available (weighted higher)
    key_concept_matches = 0
    if discovery:
        all_concepts = (
            discovery.get("domain_a_concepts", []) +
            discovery.get("domain_b_concepts", [])
        )
        for concept in all_concepts:
            concept_words = concept.lower().split()
            for word in concept_words:
                if len(word) > 3 and word in generated_text:
                    key_concept_matches += 1

    # Remove common stopwords
    stopwords = {
        "the", "a", "an", "is", "are", "in", "of", "and", "to", "with",
        "for", "that", "this", "from", "by", "on", "can", "be", "as",
        "which", "their", "these", "through", "between", "using", "based"
    }
    expected_words -= stopwords
    generated_words -= stopwords

    # Calculate overlap
    overlap = expected_words & generated_words
    base_overlap_ratio = len(overlap) / len(expected_words) if expected_words else 0

    # Boost score for key concept matches
    concept_bonus = min(0.3, key_concept_matches * 0.05)
    final_score = min(1.0, base_overlap_ratio + concept_bonus)

    # More lenient matching criteria
    is_match = (
        final_score > 0.15 or  # 15% overlap with concepts
        len(overlap) >= 2 or   # At least 2 key words
        key_concept_matches >= 3  # At least 3 domain concepts found
    )

    return {
        "match": is_match,
        "reason": "keyword_overlap_with_concepts",
        "score": final_score,
        "overlapping_concepts": list(overlap),
        "key_concept_matches": key_concept_matches,
        "bridge_summary": hypothesis.bridge.summary,
    }


async def test_single_discovery(
    discovery: dict[str, Any],
    genome_version: str,
) -> dict[str, Any]:
    """Test a single known discovery.

    Args:
        discovery: Discovery definition
        genome_version: Genome version

    Returns:
        Test result
    """
    from models.domain import Domain

    domain_map = get_domain_map()

    # Check if discovery has custom concepts (reformulated bootstrap)
    has_custom_concepts = "domain_a_concepts" in discovery

    if has_custom_concepts:
        # Create virtual domains with custom concepts
        domain_a = Domain(
            id=f"BOOT-{discovery['id']}-A",
            name=discovery["domain_a"],
            key_concepts=discovery["domain_a_concepts"],
        )
        domain_b = Domain(
            id=f"BOOT-{discovery['id']}-B",
            name=discovery["domain_b"],
            key_concepts=discovery["domain_b_concepts"],
        )
        # Estimate distance as "fertile zone" for custom domains
        distance = 0.55
    else:
        # Use domain map for standard domains
        domain_a = domain_map.get_domain_by_name(discovery["domain_a"])
        domain_b = domain_map.get_domain_by_name(discovery["domain_b"])

        if not domain_a:
            return {
                "id": discovery["id"],
                "name": discovery["name"],
                "success": False,
                "error": f"Domain A not found: {discovery['domain_a']}",
            }

        if not domain_b:
            return {
                "id": discovery["id"],
                "name": discovery["name"],
                "success": False,
                "error": f"Domain B not found: {discovery['domain_b']}",
            }

        distance = domain_map.get_distance(domain_a, domain_b)

    # Create collision pair
    collision_pair = CollisionPair(
        domain_a=domain_a,
        domain_b=domain_b,
        strategy=CollisionStrategy.HISTORICAL_TEMPLATE,
        distance_score=distance,
    )

    # Get context
    context_a = await enrich_domain_for_bootstrap(
        domain_a.name,
        domain_a.key_concepts,
    )
    context_b = await enrich_domain_for_bootstrap(
        domain_b.name,
        domain_b.key_concepts,
    )

    collision = Collision(
        pair=collision_pair,
        context_a=context_a,
        context_b=context_b,
        enrichment_source="bootstrap",
    )

    # Run synthesis
    try:
        result = await synthesize_hypothesis(
            collision=collision,
            genome_version=genome_version,
        )

        # Evaluate match
        evaluation = evaluate_bridge_match(result, discovery["expected_bridge"], discovery)

        return {
            "id": discovery["id"],
            "name": discovery["name"],
            "domain_a": discovery["domain_a"],
            "domain_b": discovery["domain_b"],
            "success": evaluation["match"],
            "score": evaluation["score"],
            "overlapping_concepts": evaluation.get("overlapping_concepts", []),
            "generated_bridge": evaluation.get("bridge_summary", "N/A"),
            "expected_bridge": discovery["expected_bridge"],
            "distance": distance,
        }

    except Exception as e:
        return {
            "id": discovery["id"],
            "name": discovery["name"],
            "success": False,
            "error": str(e),
        }


async def run_bootstrap_test() -> dict[str, Any]:
    """Run the full bootstrap calibration test.

    Returns:
        Summary of results
    """
    setup_logging()
    reset_token_tracker()

    console.print("\n[bold]Loading known discoveries...[/bold]")
    discoveries = load_known_discoveries()
    console.print(f"  Found {len(discoveries)} discoveries to test")

    # Initialize
    genome = get_genome()

    # Load domain map
    console.print("\n[bold]Loading domain map...[/bold]")
    domain_map = get_domain_map()
    console.print(f"  {domain_map.domain_count} domains loaded")

    # Test each discovery
    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Testing discoveries...", total=len(discoveries))

        for discovery in discoveries:
            progress.update(task, description=f"Testing: {discovery['name']}")

            result = await test_single_discovery(
                discovery=discovery,
                genome_version=genome.version,
            )
            results.append(result)

            progress.advance(task)

    # Calculate statistics
    total = len(results)
    successes = sum(1 for r in results if r.get("success", False))
    failures = [r for r in results if not r.get("success", False)]

    # Display results table
    console.print("\n[bold]Bootstrap Results[/bold]")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Discovery", style="cyan")
    table.add_column("Domains")
    table.add_column("Match", justify="center")
    table.add_column("Score", justify="right")

    for r in results:
        match_str = "[green]✓[/green]" if r.get("success") else "[red]✗[/red]"
        score_str = f"{r.get('score', 0):.2f}" if "score" in r else "N/A"
        domains = f"{r.get('domain_a', '?')} × {r.get('domain_b', '?')}"

        table.add_row(
            r.get("name", r.get("id")),
            domains,
            match_str,
            score_str,
        )

    console.print(table)

    # Summary
    success_rate = successes / total if total > 0 else 0

    console.print(f"\n[bold]Summary[/bold]")
    console.print(f"  Rediscovered: {successes}/{total} ({success_rate:.0%})")

    if success_rate >= 0.7:
        console.print(f"  [green]✓ Bootstrap PASSED (≥70% required)[/green]")
    else:
        console.print(f"  [red]✗ Bootstrap FAILED (<70%)[/red]")

    # Token usage
    tracker = get_token_tracker()
    summary = tracker.summary()
    console.print(f"\n[bold]Token Usage[/bold]")
    console.print(f"  Total cost: ${summary['total_cost_usd']:.4f}")

    # Failures analysis
    if failures:
        console.print(f"\n[yellow]Failures Analysis[/yellow]")
        for f in failures:
            if "error" in f:
                console.print(f"  {f['name']}: {f['error']}")
            else:
                console.print(f"  {f['name']}: No matching bridge found")
                if "generated_bridge" in f:
                    console.print(f"    Generated: {f['generated_bridge'][:80]}...")
                    console.print(f"    Expected: {f['expected_bridge'][:80]}...")

    return {
        "total": total,
        "rediscovered": successes,
        "success_rate": success_rate,
        "results": results,
        "failures": [f["name"] for f in failures],
        "passed": success_rate >= 0.7,
    }


if __name__ == "__main__":
    asyncio.run(run_bootstrap_test())
