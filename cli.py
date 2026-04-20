"""CLI for SPORE - Système de Production d'Opportunités de Recherche par Exploration."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import get_settings, get_genome
from logging_config import setup_logging, get_token_tracker

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="spore")
def cli():
    """SPORE - Scientific hypothesis generator through domain collision."""
    setup_logging()


@cli.command()
@click.option(
    "--collisions", "-n",
    default=10,
    help="Number of domain collisions to generate",
)
@click.option(
    "--domain", "-d",
    default="materials_science",
    type=click.Choice(["materials_science", "all_science"], case_sensitive=False),
    help="Domain to explore",
)
@click.option(
    "--distance-min",
    default=0.4,
    help="Minimum semantic distance for collisions",
)
@click.option(
    "--distance-max",
    default=0.7,
    help="Maximum semantic distance for collisions",
)
@click.option(
    "--output", "-o",
    default=None,
    help="Output directory for YAML exports (default: outputs/)",
)
@click.option(
    "--no-save",
    is_flag=True,
    help="Don't save to database",
)
def run(
    collisions: int,
    domain: str,
    distance_min: float,
    distance_max: float,
    output: str | None,
    no_save: bool,
):
    """Run the SPORE pipeline to generate hypotheses."""
    console.print(Panel.fit(
        "[bold blue]SPORE[/bold blue] - Generating Scientific Hypotheses",
        subtitle=f"{collisions} collisions in {domain}",
    ))

    async def _run():
        from graph import run_pipeline
        from logging_config import get_token_tracker
        from storage import init_database, cleanup_stale_runs

        # Cleanup stale runs before starting a new one
        await init_database()
        cleaned = await cleanup_stale_runs(timeout_hours=6)
        if cleaned:
            console.print(f"[yellow]Cleaned {cleaned} stale run(s) stuck > 6h[/yellow]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running pipeline...", total=None)

            try:
                result = await run_pipeline(
                    n_collisions=collisions,
                    distance_min=distance_min,
                    distance_max=distance_max,
                    save_to_db=not no_save,
                    domain=domain,
                )

                progress.update(task, description="Pipeline complete!")

            except Exception as e:
                console.print(f"[red]Pipeline failed: {e}[/red]")
                raise

        return result

    # Run the async pipeline
    result = asyncio.run(_run())

    # Display results
    metrics = result.get("metrics", {})
    curated = result.get("curated_hypotheses", [])

    console.print("\n[bold green]Results[/bold green]")
    console.print(f"  Collisions processed: {metrics.get('collisions_processed', 0)}")
    console.print(f"  Hypotheses generated: {metrics.get('hypotheses_generated', 0)}")
    console.print(f"  Bridge rate: {metrics.get('bridge_rate', 0):.1%}")
    console.print(f"  Curated hypotheses: {len(curated)}")

    # Token usage
    console.print("\n[bold yellow]Token Usage[/bold yellow]")
    console.print(f"  Input tokens: {metrics.get('total_tokens_in', 0):,}")
    console.print(f"  Output tokens: {metrics.get('total_tokens_out', 0):,}")
    console.print(f"  Total cost: ${metrics.get('total_cost_usd', 0):.4f}")

    # Export to YAML if requested or by default
    output_dir = Path(output) if output else get_settings().output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = metrics.get("run_id", datetime.now().strftime("%Y%m%d-%H%M%S"))

    for hypothesis in curated:
        yaml_path = output_dir / f"{hypothesis.id}.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(hypothesis.to_yaml_dict(), f, default_flow_style=False, allow_unicode=True)

    if curated:
        console.print(f"\n[green]Exported {len(curated)} hypotheses to {output_dir}/[/green]")

    # Show top hypotheses
    if curated:
        console.print("\n[bold]Top Hypotheses[/bold]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("ID", style="dim")
        table.add_column("Domains")
        table.add_column("Bridge", max_width=50)
        table.add_column("Score", justify="right")

        for h in curated[:5]:
            score = f"{h.scores.composite:.3f}" if h.scores and h.scores.composite else "N/A"
            table.add_row(
                h.id[-12:],
                f"{h.collision.domain_a.name} × {h.collision.domain_b.name}",
                h.bridge.summary[:50] + "..." if len(h.bridge.summary) > 50 else h.bridge.summary,
                score,
            )

        console.print(table)


@cli.command()
def bootstrap():
    """Run calibration test on known discoveries."""
    console.print(Panel.fit(
        "[bold blue]SPORE Bootstrap[/bold blue] - Calibration Test",
        subtitle="Testing against 10 known discoveries",
    ))

    async def _bootstrap():
        from bootstrap import run_bootstrap_test

        results = await run_bootstrap_test()
        return results

    try:
        results = asyncio.run(_bootstrap())
    except ImportError:
        console.print("[yellow]Bootstrap module not yet implemented. Run after creating data/bootstrap/known_discoveries.json[/yellow]")
        return

    # Display results
    console.print("\n[bold green]Bootstrap Results[/bold green]")
    console.print(f"  Discoveries tested: {results.get('total', 0)}")
    console.print(f"  Rediscovered: {results.get('rediscovered', 0)}")
    console.print(f"  Success rate: {results.get('success_rate', 0):.0%}")

    if results.get("failures"):
        console.print("\n[yellow]Failures:[/yellow]")
        for failure in results.get("failures", []):
            console.print(f"  - {failure}")


@cli.command()
@click.option("--port", "-p", default=8501, help="Port to run Streamlit on")
def review(port: int):
    """Launch the Streamlit review interface."""
    import subprocess

    console.print(f"[blue]Launching review interface on port {port}...[/blue]")

    review_app = Path(__file__).parent / "review" / "app.py"

    if not review_app.exists():
        console.print("[yellow]Review app not found. Creating minimal app...[/yellow]")
        # Will be created in step 8

    try:
        subprocess.run(
            ["streamlit", "run", str(review_app), "--server.port", str(port)],
            check=True,
        )
    except FileNotFoundError:
        console.print("[red]Streamlit not found. Install with: pip install streamlit[/red]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Review interface stopped.[/yellow]")


@cli.command()
def stats():
    """Show statistics about generated hypotheses."""
    async def _stats():
        from storage import list_hypotheses, get_metrics, init_database

        await init_database()
        hypotheses = await list_hypotheses(limit=1000)

        return hypotheses

    hypotheses = asyncio.run(_stats())

    if not hypotheses:
        console.print("[yellow]No hypotheses in database yet. Run 'spore run' first.[/yellow]")
        return

    console.print(Panel.fit(
        f"[bold blue]SPORE Statistics[/bold blue]",
        subtitle=f"{len(hypotheses)} hypotheses",
    ))

    # Count by status
    from collections import Counter
    status_counts = Counter(h.status.value for h in hypotheses)

    console.print("\n[bold]By Status[/bold]")
    for status, count in status_counts.most_common():
        console.print(f"  {status}: {count}")

    # Count by feedback
    feedback_counts = Counter(
        h.human_feedback.value if h.human_feedback else "pending"
        for h in hypotheses
    )

    console.print("\n[bold]By Human Feedback[/bold]")
    for feedback, count in feedback_counts.most_common():
        console.print(f"  {feedback}: {count}")

    # Score distribution
    scores = [h.scores.composite for h in hypotheses if h.scores and h.scores.composite]
    if scores:
        console.print("\n[bold]Score Distribution[/bold]")
        console.print(f"  Min: {min(scores):.3f}")
        console.print(f"  Max: {max(scores):.3f}")
        console.print(f"  Avg: {sum(scores)/len(scores):.3f}")


@cli.command()
def config():
    """Show current configuration."""
    settings = get_settings()
    genome = get_genome()

    console.print(Panel.fit("[bold blue]SPORE Configuration[/bold blue]"))

    console.print("\n[bold]Paths[/bold]")
    console.print(f"  Database: {settings.db_path}")
    console.print(f"  Output: {settings.output_dir}")
    console.print(f"  Genome: {settings.genome_path}")

    console.print("\n[bold]Genome (L0)[/bold]")
    console.print(f"  Version: {genome.version}")
    console.print(f"  Synthesis model: {genome.get_agent_model('synthesis')}")
    console.print(f"  Distance range: {genome.randomness.get('distance_min', 0.4)}-{genome.randomness.get('distance_max', 0.7)}")

    console.print("\n[bold]API[/bold]")
    api_key = settings.anthropic_api_key
    console.print(f"  Anthropic API: {'configured' if api_key and api_key != 'sk-test-placeholder' else 'not configured'}")
    ss_key = settings.semantic_scholar_api_key
    console.print(f"  Semantic Scholar: {'configured' if ss_key else 'using public rate limits'}")

    console.print("\n[bold]Email (Autopilot)[/bold]")
    smtp_configured = settings.smtp_host and settings.smtp_user
    console.print(f"  SMTP: {'configured' if smtp_configured else 'not configured'}")
    if settings.digest_recipients:
        console.print(f"  Recipients: {settings.digest_recipients}")


@cli.command()
@click.option(
    "--collisions", "-n",
    default=100,
    help="Number of domain collisions to generate (default: 100)",
)
@click.option(
    "--domain", "-d",
    default="all_science",
    type=click.Choice(["materials_science", "all_science"], case_sensitive=False),
    help="Domain to explore (default: all_science)",
)
@click.option(
    "--send-email",
    is_flag=True,
    help="Send digest via email after completion",
)
@click.option(
    "--fix-domain-a",
    "fix_domain_a",
    default=None,
    metavar="SUBDOMAIN_ID",
    help="Force every collision's domain_a to this subdomain ID "
    "(e.g. 'MED-003' for Tissue Regeneration). Targeted-run mode.",
)
def autopilot(collisions: int, domain: str, send_email: bool, fix_domain_a: str):
    """Run automated hypothesis generation with digest.

    Runs N collisions, curates hypotheses, runs impact analysis,
    and generates a markdown digest file. Optionally sends email.
    """
    subtitle = f"{collisions} collisions in {domain}"
    if fix_domain_a:
        subtitle += f" (domain_a fixed: {fix_domain_a})"
    console.print(Panel.fit(
        "[bold blue]SPORE Autopilot[/bold blue] - Automated Hypothesis Generation",
        subtitle=subtitle,
    ))

    async def _autopilot():
        from spore_autopilot import run_autopilot

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running autopilot...", total=None)

            try:
                digest_path = await run_autopilot(
                    n_collisions=collisions,
                    domain=domain,
                    send_email=send_email,
                    fix_domain_a=fix_domain_a,
                )

                progress.update(task, description="Autopilot complete!")
                return digest_path

            except Exception as e:
                console.print(f"[red]Autopilot failed: {e}[/red]")
                raise

    try:
        digest_path = asyncio.run(_autopilot())

        console.print("\n[bold green]Autopilot Complete[/bold green]")
        console.print(f"  Digest saved to: {digest_path}")

        if send_email:
            settings = get_settings()
            if settings.smtp_host and settings.smtp_user:
                console.print(f"  Email sent to: {settings.digest_recipients or 'N/A'}")
            else:
                console.print("  [yellow]Email not sent (SMTP not configured)[/yellow]")

        console.print("\n[dim]View the digest or run 'spore review' to provide feedback.[/dim]")

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise SystemExit(1)


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Analyze and propose mutations without applying them",
)
@click.option(
    "--limit-runs", "-n",
    default=5,
    help="Number of recent runs to analyze (default: 5)",
)
@click.option(
    "--observe-only",
    is_flag=True,
    help="Only generate observation report, no mutations",
)
def evolve(dry_run: bool, limit_runs: int, observe_only: bool):
    """Run L1 evolution cycle to improve L0 genome.

    Analyzes recent L0 performance and proposes mutations
    to improve hypothesis quality.
    """
    console.print(Panel.fit(
        "[bold magenta]SPORE L1[/bold magenta] - Evolution Cycle",
        subtitle="Analyzing L0 metrics and proposing improvements",
    ))

    async def _evolve():
        from graph.l1_pipeline import run_l1_cycle, show_observation_report

        if observe_only:
            console.print("\n[bold]Generating observation report...[/bold]")
            report = await show_observation_report(limit_runs)
            return {"observation_only": True, "report": report}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running L1 cycle...", total=None)

            try:
                result = await run_l1_cycle(
                    limit_runs=limit_runs,
                    dry_run=dry_run,
                )

                progress.update(task, description="L1 cycle complete!")
                return result

            except Exception as e:
                console.print(f"[red]L1 cycle failed: {e}[/red]")
                raise

    try:
        result = asyncio.run(_evolve())

        if result.get("observation_only"):
            report = result["report"]
            _display_observation_report(report)
            return

        # Display results
        console.print("\n[bold green]L1 Cycle Results[/bold green]")
        console.print(f"  Status: {result.get('status', 'unknown')}")

        if result.get("observation_report"):
            obs = result["observation_report"]
            console.print(f"\n[bold]Observations[/bold]")
            console.print(f"  Runs analyzed: {len(obs.get('analyzed_run_ids', []))}")
            console.print(f"  Bridge rate: {obs.get('bridge_rate', 0):.1%}")
            console.print(f"  Issues found: {len(obs.get('issues', []))}")
            console.print(f"  Opportunities: {len(obs.get('opportunities', []))}")

        mutations_applied = result.get("mutations_applied", [])
        if mutations_applied:
            console.print(f"\n[bold]Mutations Applied[/bold]")
            for m in mutations_applied:
                console.print(f"  [green]✓[/green] {m['type']}: {m['target_path']}")
                console.print(f"      {m['old_value']} → {m['new_value']}")
        elif dry_run:
            console.print("\n[yellow]Dry run - no mutations applied[/yellow]")
        else:
            console.print("\n[dim]No mutations applied this cycle[/dim]")

        if result.get("cost_usd"):
            console.print(f"\n[bold]Cost[/bold]: ${result['cost_usd']:.4f}")

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise SystemExit(1)


def _display_observation_report(report):
    """Display an observation report in a nice format."""
    console.print("\n[bold]Observation Report[/bold]")
    console.print(f"  Generated: {report.generated_at}")
    console.print(f"  Runs analyzed: {len(report.analyzed_run_ids)}")

    console.print(f"\n[bold]Metrics[/bold]")
    console.print(f"  Total collisions: {report.total_collisions}")
    console.print(f"  Total hypotheses: {report.total_hypotheses}")
    console.print(f"  Bridge rate: {report.bridge_rate:.1%}")
    console.print(f"  Avg composite score: {report.avg_composite_score:.3f}")
    console.print(f"  Total cost: ${report.total_cost_usd:.2f}")

    if report.feedback_distribution:
        console.print(f"\n[bold]Human Feedback[/bold]")
        for fb, count in report.feedback_distribution.items():
            emoji = {"trash": "🗑️", "interesting": "🤔", "want_to_test": "🔥"}.get(fb, "")
            console.print(f"  {emoji} {fb}: {count}")

    if report.score_feedback_correlation is not None:
        console.print(f"  Score-feedback correlation: {report.score_feedback_correlation:.2f}")

    if report.top_data_gaps:
        console.print(f"\n[bold]Top Data Gaps[/bold]")
        for gap in report.top_data_gaps[:3]:
            console.print(f"  - {gap.get('domain', 'unknown')}: {gap.get('description', '')[:50]}... (x{gap.get('recurrence', 1)})")

    if report.issues:
        console.print(f"\n[bold red]Issues[/bold red]")
        for issue in report.issues:
            console.print(f"  ⚠️  {issue}")

    if report.opportunities:
        console.print(f"\n[bold green]Opportunities[/bold green]")
        for opp in report.opportunities:
            console.print(f"  💡 {opp}")


@cli.command(name="post-fire")
@click.option(
    "--hypothesis-id", "-id",
    required=True,
    help="ID of the hypothesis to run through the post-fire pipeline",
)
def post_fire(hypothesis_id: str):
    """Run the post-fire deep validation pipeline on a hypothesis.

    Takes a hypothesis from the database and runs Literature Grounding,
    Hypothesis Sharpening, Experimental Protocol, Multi-Reviewer Panel,
    and Research Brief generation.
    """
    console.print(Panel.fit(
        "[bold red]SPORE Post-Fire Pipeline[/bold red]",
        subtitle=f"Hypothesis: {hypothesis_id}",
    ))

    async def _post_fire():
        from storage import get_hypothesis, init_database
        from graph.post_fire_pipeline import run_post_fire_pipeline

        await init_database()
        hypothesis = await get_hypothesis(hypothesis_id)

        if hypothesis is None:
            console.print(f"[red]Hypothesis '{hypothesis_id}' not found in database.[/red]")
            return None

        console.print(f"  Domains: {hypothesis.collision.domain_a.name} x {hypothesis.collision.domain_b.name}")
        console.print(f"  Bridge: {hypothesis.bridge.summary[:80]}...")
        console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running post-fire pipeline...", total=None)

            result = await run_post_fire_pipeline(
                hypothesis=hypothesis.bridge.summary,
                domains=[
                    hypothesis.collision.domain_a.name,
                    hypothesis.collision.domain_b.name,
                ],
                mechanisms=hypothesis.bridge.mechanism,
                keywords=[],
                gap_manifest=hypothesis.gap_manifest.model_dump()
                if hypothesis.gap_manifest else {},
            )

            progress.update(task, description="Post-fire pipeline complete!")

        return result

    try:
        result = asyncio.run(_post_fire())

        if result is None:
            raise SystemExit(1)

        # Display results
        console.print("\n[bold green]Results[/bold green]")

        if result.get("kill_reason"):
            console.print(f"  [red]KILLED: {result['kill_reason']}[/red]")
            return

        grounding = result.get("grounding", {})
        novelty = grounding.get("novelty_assessment", {})
        console.print(f"  Novelty: {novelty.get('score', '?')}/1.0 ({novelty.get('verdict', '?')})")
        console.print(f"  Evidence: {len(grounding.get('evidence_base', []))} papers")

        sharpened = result.get("sharpened", {})
        console.print(f"  Title: {sharpened.get('title', '?')}")
        console.print(f"  Predictions: {len(sharpened.get('falsifiable_predictions', []))}")

        protocol = result.get("protocol", {})
        console.print(f"  Timeline: {protocol.get('overall_timeline', '?')}")
        console.print(f"  Budget: {protocol.get('overall_budget_estimate', '?')}")

        panel = result.get("panel", {})
        meta = panel.get("meta_review", {})
        console.print(f"  Consensus: {meta.get('consensus_score', '?')}/10")
        console.print(f"  Verdict: {meta.get('verdict', '?')}")

        if result.get("brief_id"):
            console.print(f"\n  [green]Brief generated: {result['brief_id']}[/green]")
            console.print(f"  Markdown: {result.get('brief_md_path', '?')}")
            console.print(f"  JSON: {result.get('brief_json_path', '?')}")

        # Token costs
        tracker = get_token_tracker()
        summary = tracker.summary()
        console.print(f"\n[bold yellow]Cost[/bold yellow]: ${summary.get('total_cost_usd', 0):.4f}")

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise SystemExit(1)


@cli.command()
@click.option("--limit", "-n", default=20, help="Number of mutations to show")
def mutations(limit: int):
    """Show mutation history from L1 evolution cycles.

    Displays past mutations applied to the L0 genome.
    """
    console.print(Panel.fit(
        "[bold magenta]SPORE Mutations[/bold magenta] - L1 Evolution History",
    ))

    async def _mutations():
        from graph.l1_pipeline import get_mutation_history
        return await get_mutation_history(limit)

    try:
        history = asyncio.run(_mutations())

        if not history:
            console.print("\n[yellow]No mutations in history yet.[/yellow]")
            console.print("[dim]Run 'spore evolve' to start L1 evolution.[/dim]")
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Date", style="dim")
        table.add_column("ID")
        table.add_column("Type")
        table.add_column("Target")
        table.add_column("Status")

        for m in history:
            status_style = {
                "applied": "green",
                "rolled_back": "red",
                "rejected": "yellow",
            }.get(m.status.value, "white")

            table.add_row(
                m.created_at.strftime("%Y-%m-%d"),
                m.id[-10:],
                m.type.value,
                m.target_path,
                f"[{status_style}]{m.status.value}[/{status_style}]",
            )

        console.print(table)

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise SystemExit(1)


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
