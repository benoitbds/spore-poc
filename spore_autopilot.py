"""SPORE Autopilot - automated hypothesis generation with digest.

Runs as a cron job to generate hypotheses and produce weekly digests.
"""

import asyncio
import smtplib
from collections import Counter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import get_settings, get_genome
from graph import run_pipeline
from storage import init_database, list_hypotheses, save_hypothesis, cleanup_stale_runs
from models.hypothesis import Hypothesis, HypothesisStatus, ImpactScore
from models.gap_manifest import GapManifest, DataGap
from agents.impact import analyze_impact
from logging_config import setup_logging, get_logger

logger = get_logger("autopilot")


async def run_autopilot(
    n_collisions: int = 100,
    domain: str = "all_science",
    send_email: bool = False,
) -> str:
    """Run autopilot cycle and generate digest.

    Args:
        n_collisions: Number of domain collisions to generate
        domain: Domain to explore (materials_science, all_science)
        send_email: Whether to send digest via email

    Returns:
        Path to the generated digest file
    """
    setup_logging()

    logger.info(
        "autopilot_starting",
        n_collisions=n_collisions,
        domain=domain,
        send_email=send_email,
    )

    # 1. Initialize database
    await init_database()

    # 1b. Cleanup stale runs (stuck > 6h) before starting a new one
    cleaned = await cleanup_stale_runs(timeout_hours=6)
    if cleaned:
        logger.warning("stale_runs_cleaned", count=cleaned)

    # 2. Run pipeline with n_collisions
    logger.info("running_pipeline", n_collisions=n_collisions)

    result = await run_pipeline(
        n_collisions=n_collisions,
        save_to_db=True,
        domain=domain,
    )

    run_metrics = result.get("metrics", {})
    curated_hypotheses = result.get("curated_hypotheses", [])
    gap_manifests = result.get("gap_manifests", [])
    no_bridges = result.get("no_bridges", [])

    logger.info(
        "pipeline_complete",
        hypotheses_generated=run_metrics.get("hypotheses_generated", 0),
        curated_count=len(curated_hypotheses),
    )

    # 3. Ensure impact analysis was run (it runs in the pipeline)
    # If any curated hypotheses don't have impact analysis, run it now
    hypotheses_needing_impact = [
        h for h in curated_hypotheses if h.impact_analysis is None
    ]

    if hypotheses_needing_impact:
        logger.info(
            "running_additional_impact_analysis",
            count=len(hypotheses_needing_impact),
        )

        for hypothesis in hypotheses_needing_impact:
            impact = await analyze_impact(hypothesis)
            if impact:
                hypothesis.impact_analysis = impact
                await save_hypothesis(hypothesis)

    # 4. Extract recurring gaps from all gap manifests
    recurring_gaps = _extract_recurring_gaps(
        [h.gap_manifest for h in curated_hypotheses] + gap_manifests +
        [nb.gap_manifest for nb in no_bridges if hasattr(nb, 'gap_manifest')]
    )

    # 5. Get top hypotheses by impact score
    top_hypotheses = _get_top_hypotheses(curated_hypotheses, limit=5)

    # 6. Generate digest markdown
    digest_content = generate_digest_markdown(
        run_metrics=run_metrics,
        top_hypotheses=top_hypotheses,
        recurring_gaps=recurring_gaps,
        domain=domain,
        total_curated=len(curated_hypotheses),
    )

    # 7. Save to outputs/digests/digest_YYYY-MM-DD.md
    settings = get_settings()
    digest_dir = settings.output_dir / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    digest_path = digest_dir / f"digest_{today}.md"

    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(digest_content)

    logger.info("digest_saved", path=str(digest_path))

    # 8. Optionally send email
    if send_email:
        recipients = _get_email_recipients()
        if recipients:
            success = send_digest_email(digest_content, recipients)
            if success:
                logger.info("digest_email_sent", recipients=recipients)
            else:
                logger.error("digest_email_failed")

    return str(digest_path)


def _extract_recurring_gaps(gap_manifests: list[GapManifest], top_n: int = 3) -> list[dict]:
    """Extract the most recurring gaps across all manifests.

    Args:
        gap_manifests: List of gap manifests to analyze
        top_n: Number of top recurring gaps to return

    Returns:
        List of recurring gap dictionaries
    """
    # Count data gaps by domain
    domain_gaps: Counter = Counter()
    gap_details: dict[str, list[str]] = {}

    for manifest in gap_manifests:
        if manifest is None:
            continue
        for gap in manifest.data_gaps:
            domain_gaps[gap.domain] += 1
            if gap.domain not in gap_details:
                gap_details[gap.domain] = []
            if gap.description not in gap_details[gap.domain]:
                gap_details[gap.domain].append(gap.description)

    # Get top recurring gaps
    recurring = []
    for domain, count in domain_gaps.most_common(top_n):
        recurring.append({
            "domain": domain,
            "recurrence": count,
            "descriptions": gap_details.get(domain, [])[:3],  # Top 3 descriptions
        })

    return recurring


def _get_top_hypotheses(hypotheses: list[Hypothesis], limit: int = 5) -> list[Hypothesis]:
    """Get top hypotheses sorted by impact score.

    Args:
        hypotheses: List of hypotheses to rank
        limit: Maximum number to return

    Returns:
        List of top hypotheses
    """
    # Define score order for ImpactScore
    score_order = {
        ImpactScore.CIVILISATIONNEL: 4,
        ImpactScore.TRANSFORMATIF: 3,
        ImpactScore.SIGNIFICATIF: 2,
        ImpactScore.INCREMENTAL: 1,
    }

    def impact_sort_key(h: Hypothesis) -> tuple:
        # Sort by impact score (higher better), then by composite score (higher better)
        impact_value = 0
        if h.impact_analysis and h.impact_analysis.score_impact:
            impact_value = score_order.get(h.impact_analysis.score_impact, 0)

        composite = 0.0
        if h.scores and h.scores.composite:
            composite = h.scores.composite

        return (impact_value, composite)

    sorted_hypotheses = sorted(hypotheses, key=impact_sort_key, reverse=True)
    return sorted_hypotheses[:limit]


def generate_digest_markdown(
    run_metrics: dict,
    top_hypotheses: list[Hypothesis],
    recurring_gaps: list[dict],
    domain: str = "all_science",
    total_curated: int = 0,
) -> str:
    """Generate markdown digest content.

    Args:
        run_metrics: Metrics from the pipeline run
        top_hypotheses: Top hypotheses by impact score
        recurring_gaps: List of recurring gap dictionaries
        domain: Domain that was explored
        total_curated: Total number of curated hypotheses

    Returns:
        Markdown string for the digest
    """
    today = datetime.now().strftime("%Y-%m-%d")
    today_formatted = datetime.now().strftime("%B %d, %Y")

    # Build the markdown content
    lines = [
        f"# SPORE Digest - {today_formatted}",
        "",
        "## Run Summary",
        "",
        f"**Domain:** {domain}",
        f"**Collisions processed:** {run_metrics.get('collisions_processed', 0)}",
        f"**Hypotheses generated:** {run_metrics.get('hypotheses_generated', 0)}",
        f"**Curated hypotheses:** {total_curated}",
        f"**Bridge rate:** {run_metrics.get('bridge_rate', 0):.1%}",
        f"**Total cost:** ${run_metrics.get('total_cost_usd', 0):.4f}",
        "",
        "---",
        "",
    ]

    # Top 5 hypotheses by impact score
    lines.append("## Top Hypotheses")
    lines.append("")

    if not top_hypotheses:
        lines.append("*No curated hypotheses in this run.*")
        lines.append("")
    else:
        for i, h in enumerate(top_hypotheses, 1):
            lines.append(f"### {i}. {h.id}")
            lines.append("")

            # Domains
            domain_a = h.collision.domain_a.name if h.collision.domain_a else "Unknown"
            domain_b = h.collision.domain_b.name if h.collision.domain_b else "Unknown"
            lines.append(f"**Collision:** {domain_a} x {domain_b}")
            lines.append("")

            # One-liner and vulgarisation
            if h.impact_analysis:
                if h.impact_analysis.one_liner:
                    lines.append(f"> {h.impact_analysis.one_liner}")
                    lines.append("")

                if h.impact_analysis.vulgarisation:
                    lines.append(f"**Explanation:** {h.impact_analysis.vulgarisation}")
                    lines.append("")

                # Score and horizon
                lines.append(f"**Impact Score:** {h.impact_analysis.score_impact.value}")
                lines.append(f"**Time Horizon:** {h.impact_analysis.horizon_temporel}")
                lines.append("")

                # Industries
                if h.impact_analysis.industries_impactees:
                    industries = ", ".join(h.impact_analysis.industries_impactees)
                    lines.append(f"**Industries:** {industries}")
                    lines.append("")
            else:
                # Fallback to bridge summary
                lines.append(f"**Bridge:** {h.bridge.summary}")
                lines.append("")

            # Composite score
            if h.scores and h.scores.composite:
                lines.append(f"**Composite Score:** {h.scores.composite:.3f}")
                lines.append("")

            lines.append("---")
            lines.append("")

    # Top 3 recurring gaps
    lines.append("## Recurring Knowledge Gaps")
    lines.append("")

    if not recurring_gaps:
        lines.append("*No recurring gaps identified in this run.*")
        lines.append("")
    else:
        for gap in recurring_gaps:
            lines.append(f"### {gap['domain']} ({gap['recurrence']} occurrences)")
            lines.append("")
            for desc in gap.get('descriptions', []):
                lines.append(f"- {desc}")
            lines.append("")

    # Link to Streamlit review
    lines.append("---")
    lines.append("")
    lines.append("## Review Interface")
    lines.append("")
    lines.append("To review and provide feedback on hypotheses, launch the Streamlit interface:")
    lines.append("")
    lines.append("```bash")
    lines.append("spore review")
    lines.append("```")
    lines.append("")
    lines.append("Or visit: http://localhost:8501")
    lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append(f"*Generated by SPORE Autopilot on {datetime.now().isoformat()}*")
    lines.append("")

    return "\n".join(lines)


def send_digest_email(
    digest_content: str,
    recipients: list[str],
) -> bool:
    """Send digest via SMTP.

    Args:
        digest_content: The markdown digest content
        recipients: List of email addresses to send to

    Returns:
        True if email was sent successfully, False otherwise
    """
    settings = get_settings()

    # Check if email settings are configured
    if not settings.smtp_host or not settings.smtp_user:
        logger.warning("smtp_not_configured")
        return False

    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"SPORE Digest - {datetime.now().strftime('%Y-%m-%d')}"
        msg["From"] = settings.smtp_user
        msg["To"] = ", ".join(recipients)

        # Add plain text version (the markdown)
        text_part = MIMEText(digest_content, "plain", "utf-8")
        msg.attach(text_part)

        # Optionally, convert markdown to HTML for richer email
        # For now, we just send plain text

        # Connect and send
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

        return True

    except smtplib.SMTPException as e:
        logger.error("smtp_send_failed", error=str(e))
        return False
    except Exception as e:
        logger.error("email_send_failed", error=str(e))
        return False


def _get_email_recipients() -> list[str]:
    """Get email recipients from settings.

    Returns:
        List of email addresses
    """
    settings = get_settings()
    recipients_str = settings.digest_recipients

    if not recipients_str:
        return []

    # Split by comma and strip whitespace
    return [r.strip() for r in recipients_str.split(",") if r.strip()]


async def main():
    """Main entry point for standalone execution."""
    import argparse

    parser = argparse.ArgumentParser(description="SPORE Autopilot")
    parser.add_argument(
        "--collisions", "-n",
        type=int,
        default=100,
        help="Number of collisions to generate (default: 100)",
    )
    parser.add_argument(
        "--domain", "-d",
        type=str,
        default="all_science",
        choices=["materials_science", "all_science"],
        help="Domain to explore (default: all_science)",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Send digest via email",
    )

    args = parser.parse_args()

    digest_path = await run_autopilot(
        n_collisions=args.collisions,
        domain=args.domain,
        send_email=args.send_email,
    )

    print(f"Digest saved to: {digest_path}")


if __name__ == "__main__":
    asyncio.run(main())
