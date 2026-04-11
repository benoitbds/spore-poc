"""Run ImpactAgent on existing curated hypotheses.

This script loads curated hypotheses from the database and runs
the ImpactAgent on them without re-running the full pipeline.
"""

import asyncio
from pathlib import Path

import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from config import get_settings, get_genome
from storage import init_database, list_hypotheses, save_hypothesis
from models.hypothesis import Hypothesis, HypothesisStatus
from agents.impact import analyze_impact
from logging_config import setup_logging, get_logger, get_token_tracker, reset_token_tracker

logger = get_logger("run_impact")
console = Console()


async def run_impact_on_curated() -> None:
    """Run ImpactAgent on all curated hypotheses without impact analysis."""
    setup_logging()
    reset_token_tracker()

    console.print("\n[bold]Loading curated hypotheses...[/bold]")

    # Initialize database
    await init_database()

    # Load curated hypotheses
    hypotheses = await list_hypotheses(status=HypothesisStatus.CURATED, limit=100)

    # Filter those without impact analysis
    to_analyze = [h for h in hypotheses if h.impact_analysis is None]

    console.print(f"  Found {len(hypotheses)} curated hypotheses")
    console.print(f"  {len(to_analyze)} need impact analysis")

    if not to_analyze:
        console.print("[green]All curated hypotheses already have impact analysis![/green]")
        return

    # Get model info from genome
    genome = get_genome()
    model = genome.get_agent_model("impact")
    provider = genome.get_agent_provider("impact")

    console.print(f"\n[bold]Running ImpactAgent on {len(to_analyze)} hypotheses...[/bold]")
    console.print(f"  Provider: {provider}")
    console.print(f"  Model: {model}")

    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing...", total=len(to_analyze))

        for hypothesis in to_analyze:
            progress.update(
                task,
                description=f"Analyzing: {hypothesis.collision.domain_a.name} × {hypothesis.collision.domain_b.name}"
            )

            try:
                impact = await analyze_impact(hypothesis)

                if impact:
                    hypothesis.impact_analysis = impact
                    await save_hypothesis(hypothesis)

                    # Also update YAML file if it exists
                    yaml_path = Path("outputs") / f"{hypothesis.id}.yaml"
                    if yaml_path.exists():
                        with open(yaml_path, "w") as f:
                            yaml.dump(
                                hypothesis.to_yaml_dict(),
                                f,
                                default_flow_style=False,
                                allow_unicode=True,
                                sort_keys=False,
                            )

                    results.append({
                        "id": hypothesis.id,
                        "domains": f"{hypothesis.collision.domain_a.name} × {hypothesis.collision.domain_b.name}",
                        "success": True,
                        "one_liner": impact.one_liner[:60] + "..." if len(impact.one_liner) > 60 else impact.one_liner,
                        "score_impact": impact.score_impact.value,
                    })
                else:
                    results.append({
                        "id": hypothesis.id,
                        "domains": f"{hypothesis.collision.domain_a.name} × {hypothesis.collision.domain_b.name}",
                        "success": False,
                        "one_liner": "Failed",
                        "score_impact": "N/A",
                    })

            except Exception as e:
                logger.error("impact_failed", hypothesis_id=hypothesis.id, error=str(e))
                results.append({
                    "id": hypothesis.id,
                    "domains": f"{hypothesis.collision.domain_a.name} × {hypothesis.collision.domain_b.name}",
                    "success": False,
                    "one_liner": f"Error: {str(e)[:40]}",
                    "score_impact": "N/A",
                })

            progress.advance(task)

    # Display results
    console.print("\n[bold]Impact Analysis Results[/bold]")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Hypothesis", style="cyan")
    table.add_column("Success")
    table.add_column("Impact")
    table.add_column("One-liner")

    for r in results:
        success_str = "[green]✓[/green]" if r["success"] else "[red]✗[/red]"
        table.add_row(
            r["id"][-12:],
            success_str,
            r["score_impact"],
            r["one_liner"],
        )

    console.print(table)

    # Token usage
    tracker = get_token_tracker()
    summary = tracker.summary()

    console.print(f"\n[bold]Token Usage[/bold]")
    console.print(f"  Input: {summary['total_input_tokens']:,}")
    console.print(f"  Output: {summary['total_output_tokens']:,}")
    console.print(f"  Cost: ${summary['total_cost_usd']:.4f}")

    success_count = sum(1 for r in results if r["success"])
    console.print(f"\n[bold]Summary[/bold]")
    console.print(f"  Analyzed: {success_count}/{len(to_analyze)}")


if __name__ == "__main__":
    asyncio.run(run_impact_on_curated())
