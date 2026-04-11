"""L1 Observer Agent - Analyzes L0 metrics and produces observation reports.

Part of Team L1 (Trainers) - Design Doc section 2.3.

The Observer reads from SQLite:
- Run metrics (bridge_rate, scores, costs)
- Human feedback distribution
- Gap manifests (aggregated)
- Domain fertility analysis

Produces an ObservationReport for the Strategist.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Optional

from models.hypothesis import HumanFeedback, HypothesisStatus
from models.mutation import ObservationReport
from storage import get_connection
from logging_config import get_logger

logger = get_logger("l1_observer")


async def gather_run_metrics(
    limit_runs: int = 5
) -> dict[str, Any]:
    """Gather metrics from recent runs.

    Args:
        limit_runs: Number of recent runs to analyze

    Returns:
        Dictionary with aggregated run metrics
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (limit_runs,),
        )
        runs = await cursor.fetchall()

        if not runs:
            return {
                "run_ids": [],
                "total_collisions": 0,
                "total_hypotheses": 0,
                "avg_bridge_rate": 0.0,
                "total_cost": 0.0,
            }

        run_ids = [r["id"] for r in runs]
        total_collisions = sum(r["collisions_processed"] or 0 for r in runs)
        total_hypotheses = sum(r["hypotheses_generated"] or 0 for r in runs)
        total_cost = sum(r["total_cost_usd"] or 0 for r in runs)

        avg_bridge_rate = (
            sum(r["bridge_rate"] or 0 for r in runs) / len(runs)
            if runs else 0
        )

        return {
            "run_ids": run_ids,
            "total_collisions": total_collisions,
            "total_hypotheses": total_hypotheses,
            "avg_bridge_rate": avg_bridge_rate,
            "total_cost": total_cost,
        }


async def gather_hypothesis_metrics() -> dict[str, Any]:
    """Gather metrics from hypotheses.

    Returns:
        Dictionary with hypothesis statistics
    """
    async with get_connection() as conn:
        # Count by status
        cursor = await conn.execute(
            """
            SELECT status, COUNT(*) as count
            FROM hypotheses
            GROUP BY status
            """
        )
        status_counts = {row["status"]: row["count"] for row in await cursor.fetchall()}

        # Count by human feedback
        cursor = await conn.execute(
            """
            SELECT human_feedback, COUNT(*) as count
            FROM hypotheses
            WHERE human_feedback IS NOT NULL
            GROUP BY human_feedback
            """
        )
        feedback_counts = {
            row["human_feedback"]: row["count"]
            for row in await cursor.fetchall()
        }

        # Get score statistics
        cursor = await conn.execute(
            """
            SELECT scores_json
            FROM hypotheses
            WHERE scores_json IS NOT NULL
            """
        )
        rows = await cursor.fetchall()

        composite_scores = []
        for row in rows:
            try:
                scores = json.loads(row["scores_json"])
                if scores.get("composite"):
                    composite_scores.append(scores["composite"])
            except (json.JSONDecodeError, KeyError):
                pass

        avg_composite = sum(composite_scores) / len(composite_scores) if composite_scores else 0

        # Score distribution by bucket
        score_buckets = {"0.0-0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-1.0": 0}
        for score in composite_scores:
            if score < 0.3:
                score_buckets["0.0-0.3"] += 1
            elif score < 0.5:
                score_buckets["0.3-0.5"] += 1
            elif score < 0.7:
                score_buckets["0.5-0.7"] += 1
            else:
                score_buckets["0.7-1.0"] += 1

        return {
            "status_counts": status_counts,
            "feedback_counts": feedback_counts,
            "avg_composite_score": avg_composite,
            "score_distribution": score_buckets,
            "total_scored": len(composite_scores),
        }


async def analyze_score_feedback_correlation() -> Optional[float]:
    """Analyze correlation between composite scores and positive human feedback.

    Returns:
        Correlation coefficient or None if insufficient data
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT scores_json, human_feedback
            FROM hypotheses
            WHERE scores_json IS NOT NULL
            AND human_feedback IS NOT NULL
            """
        )
        rows = await cursor.fetchall()

        if len(rows) < 3:
            return None

        scores = []
        feedback_values = []

        for row in rows:
            try:
                scores_data = json.loads(row["scores_json"])
                composite = scores_data.get("composite", 0)
                scores.append(composite)

                # Convert feedback to numeric: trash=0, interesting=1, want_to_test=2
                fb = row["human_feedback"]
                if fb == HumanFeedback.TRASH.value:
                    feedback_values.append(0)
                elif fb == HumanFeedback.INTERESTING.value:
                    feedback_values.append(1)
                elif fb == HumanFeedback.WANT_TO_TEST.value:
                    feedback_values.append(2)
            except (json.JSONDecodeError, KeyError):
                pass

        if len(scores) < 3:
            return None

        # Simple Pearson correlation
        n = len(scores)
        mean_s = sum(scores) / n
        mean_f = sum(feedback_values) / n

        numerator = sum(
            (s - mean_s) * (f - mean_f)
            for s, f in zip(scores, feedback_values)
        )

        denom_s = sum((s - mean_s) ** 2 for s in scores) ** 0.5
        denom_f = sum((f - mean_f) ** 2 for f in feedback_values) ** 0.5

        if denom_s == 0 or denom_f == 0:
            return None

        return numerator / (denom_s * denom_f)


async def aggregate_gap_manifests() -> dict[str, list[dict[str, Any]]]:
    """Aggregate gap manifests to find recurring gaps.

    Returns:
        Dictionary with top gaps by type
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT gap_manifest_json
            FROM hypotheses
            WHERE gap_manifest_json IS NOT NULL
            """
        )
        rows = await cursor.fetchall()

        data_gaps: Counter = Counter()
        competence_gaps: Counter = Counter()
        epistemic_gaps: Counter = Counter()

        data_gap_details: dict[str, dict] = {}
        competence_gap_details: dict[str, dict] = {}
        epistemic_gap_details: dict[str, dict] = {}

        for row in rows:
            try:
                manifest = json.loads(row["gap_manifest_json"])

                for gap in manifest.get("data_gaps", []):
                    key = f"{gap.get('domain', 'unknown')}:{gap.get('description', '')[:50]}"
                    data_gaps[key] += 1
                    data_gap_details[key] = {
                        "domain": gap.get("domain"),
                        "description": gap.get("description"),
                        "criticality": gap.get("criticality", "medium"),
                    }

                for gap in manifest.get("competence_gaps", []):
                    key = f"{gap.get('type', 'unknown')}:{gap.get('description', '')[:50]}"
                    competence_gaps[key] += 1
                    competence_gap_details[key] = {
                        "type": gap.get("type"),
                        "description": gap.get("description"),
                    }

                for gap in manifest.get("epistemic_gaps", []):
                    key = f"{gap.get('zone', 'unknown')}:{gap.get('signal', '')[:50]}"
                    epistemic_gaps[key] += 1
                    epistemic_gap_details[key] = {
                        "zone": gap.get("zone"),
                        "signal": gap.get("signal"),
                    }

            except json.JSONDecodeError:
                pass

        # Get top 5 of each
        top_data = [
            {**data_gap_details[k], "recurrence": c}
            for k, c in data_gaps.most_common(5)
        ]
        top_competence = [
            {**competence_gap_details[k], "recurrence": c}
            for k, c in competence_gaps.most_common(5)
        ]
        top_epistemic = [
            {**epistemic_gap_details[k], "recurrence": c}
            for k, c in epistemic_gaps.most_common(5)
        ]

        return {
            "data_gaps": top_data,
            "competence_gaps": top_competence,
            "epistemic_gaps": top_epistemic,
        }


async def analyze_domain_fertility() -> dict[str, Any]:
    """Analyze which domains are most/least fertile.

    Returns:
        Dictionary with domain fertility analysis
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT collision_json, scores_json, human_feedback, status
            FROM hypotheses
            """
        )
        rows = await cursor.fetchall()

        domain_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "collisions": 0,
                "hypotheses": 0,
                "total_score": 0.0,
                "positive_feedback": 0,
                "negative_feedback": 0,
            }
        )

        for row in rows:
            try:
                collision = json.loads(row["collision_json"])
                domain_a = collision.get("domain_a", {}).get("name", "unknown")
                domain_b = collision.get("domain_b", {}).get("name", "unknown")

                for domain in [domain_a, domain_b]:
                    domain_stats[domain]["collisions"] += 1

                    if row["status"] in [
                        HypothesisStatus.CURATED.value,
                        HypothesisStatus.HUMAN_REVIEWED.value,
                    ]:
                        domain_stats[domain]["hypotheses"] += 1

                    if row["scores_json"]:
                        scores = json.loads(row["scores_json"])
                        if scores.get("composite"):
                            domain_stats[domain]["total_score"] += scores["composite"]

                    if row["human_feedback"]:
                        if row["human_feedback"] in [
                            HumanFeedback.INTERESTING.value,
                            HumanFeedback.WANT_TO_TEST.value,
                        ]:
                            domain_stats[domain]["positive_feedback"] += 1
                        elif row["human_feedback"] == HumanFeedback.TRASH.value:
                            domain_stats[domain]["negative_feedback"] += 1

            except json.JSONDecodeError:
                pass

        # Calculate fertility metrics
        fertility = {}
        for domain, stats in domain_stats.items():
            if stats["collisions"] > 0:
                bridge_rate = stats["hypotheses"] / stats["collisions"]
                avg_score = (
                    stats["total_score"] / stats["hypotheses"]
                    if stats["hypotheses"] > 0 else 0
                )
                total_feedback = stats["positive_feedback"] + stats["negative_feedback"]
                feedback_ratio = (
                    stats["positive_feedback"] / total_feedback
                    if total_feedback > 0 else 0.5
                )

                fertility[domain] = {
                    "collisions": stats["collisions"],
                    "bridge_rate": round(bridge_rate, 3),
                    "avg_score": round(avg_score, 3),
                    "feedback_ratio": round(feedback_ratio, 3),
                }

        # Identify over/under exploited
        sorted_by_collisions = sorted(
            fertility.items(),
            key=lambda x: x[1]["collisions"],
            reverse=True
        )

        overexploited = [
            d for d, stats in sorted_by_collisions[:5]
            if stats["collisions"] > 10 and stats["feedback_ratio"] < 0.5
        ]

        underexplored = [
            d for d, stats in sorted_by_collisions
            if stats["collisions"] < 5 and stats["bridge_rate"] > 0.5
        ][:5]

        return {
            "fertility": fertility,
            "overexploited": overexploited,
            "underexplored": underexplored,
        }


async def identify_issues_and_opportunities(
    run_metrics: dict,
    hypothesis_metrics: dict,
    correlation: Optional[float],
    gaps: dict,
    domain_analysis: dict,
) -> tuple[list[str], list[str]]:
    """Synthesize observations into actionable issues and opportunities.

    Returns:
        Tuple of (issues, opportunities)
    """
    issues = []
    opportunities = []

    # Check bridge rate
    if run_metrics.get("avg_bridge_rate", 0) < 0.5:
        issues.append(
            f"Low bridge rate ({run_metrics['avg_bridge_rate']:.1%}): "
            "Many collisions fail to produce hypotheses"
        )
        opportunities.append(
            "Consider widening fertile distance range or softening synthesis prompts"
        )

    # Check feedback distribution
    feedback = hypothesis_metrics.get("feedback_counts", {})
    trash_count = feedback.get("trash", 0)
    interesting_count = feedback.get("interesting", 0)
    want_test_count = feedback.get("want_to_test", 0)
    total_feedback = trash_count + interesting_count + want_test_count

    if total_feedback > 5 and trash_count / total_feedback > 0.5:
        issues.append(
            f"High trash rate ({trash_count}/{total_feedback}): "
            "Majority of reviewed hypotheses are rejected"
        )
        opportunities.append(
            "Tighten curation criteria or improve synthesis quality"
        )

    if want_test_count > 0 and want_test_count / total_feedback > 0.2:
        opportunities.append(
            f"{want_test_count} hypotheses marked 'want to test' - "
            "analyze what made these successful"
        )

    # Check score-feedback correlation
    if correlation is not None:
        if correlation < 0.3:
            issues.append(
                f"Weak score-feedback correlation ({correlation:.2f}): "
                "Composite score doesn't predict human preference"
            )
            opportunities.append(
                "Re-calibrate score weights based on human feedback patterns"
            )
        elif correlation > 0.7:
            opportunities.append(
                f"Strong score-feedback correlation ({correlation:.2f}): "
                "Scoring system is well-calibrated"
            )

    # Check gaps
    data_gaps = gaps.get("data_gaps", [])
    if data_gaps:
        top_gap = data_gaps[0]
        if top_gap.get("recurrence", 0) > 5:
            issues.append(
                f"Recurring data gap: {top_gap.get('domain')} - "
                f"{top_gap.get('description', '')[:50]} (seen {top_gap['recurrence']}x)"
            )
            opportunities.append(
                f"Consider adding data source for {top_gap.get('domain')}"
            )

    # Check domain fertility
    overexploited = domain_analysis.get("overexploited", [])
    if overexploited:
        issues.append(
            f"Overexploited domains with poor feedback: {', '.join(overexploited[:3])}"
        )
        opportunities.append(
            "Temporarily exclude or reduce weight for overexploited domains"
        )

    underexplored = domain_analysis.get("underexplored", [])
    if underexplored:
        opportunities.append(
            f"Promising underexplored domains: {', '.join(underexplored[:3])}"
        )

    return issues, opportunities


async def generate_observation_report(
    limit_runs: int = 5
) -> ObservationReport:
    """Generate a complete observation report from L0 data.

    This is the main entry point for the Observer agent.

    Args:
        limit_runs: Number of recent runs to analyze

    Returns:
        ObservationReport with all metrics and insights
    """
    logger.info("observation_starting", limit_runs=limit_runs)

    # Gather all data
    run_metrics = await gather_run_metrics(limit_runs)
    hypothesis_metrics = await gather_hypothesis_metrics()
    correlation = await analyze_score_feedback_correlation()
    gaps = await aggregate_gap_manifests()
    domain_analysis = await analyze_domain_fertility()

    # Identify issues and opportunities
    issues, opportunities = await identify_issues_and_opportunities(
        run_metrics,
        hypothesis_metrics,
        correlation,
        gaps,
        domain_analysis,
    )

    # Calculate derived metrics
    total_hypotheses = hypothesis_metrics.get("status_counts", {}).get(
        HypothesisStatus.CURATED.value, 0
    ) + hypothesis_metrics.get("status_counts", {}).get(
        HypothesisStatus.HUMAN_REVIEWED.value, 0
    )

    total_curated = hypothesis_metrics.get("status_counts", {}).get(
        HypothesisStatus.CURATED.value, 0
    )

    cost_per_curated = (
        run_metrics["total_cost"] / total_curated
        if total_curated > 0 else 0
    )

    report = ObservationReport(
        analyzed_run_ids=run_metrics["run_ids"],
        total_collisions=run_metrics["total_collisions"],
        total_hypotheses=run_metrics["total_hypotheses"],
        total_curated=total_curated,
        bridge_rate=run_metrics["avg_bridge_rate"],
        curation_rate=(
            total_curated / run_metrics["total_hypotheses"]
            if run_metrics["total_hypotheses"] > 0 else 0
        ),
        avg_composite_score=hypothesis_metrics["avg_composite_score"],
        score_distribution=hypothesis_metrics["score_distribution"],
        feedback_distribution=hypothesis_metrics.get("feedback_counts", {}),
        score_feedback_correlation=correlation,
        top_data_gaps=gaps["data_gaps"],
        top_competence_gaps=gaps["competence_gaps"],
        top_epistemic_gaps=gaps["epistemic_gaps"],
        domain_fertility=domain_analysis["fertility"],
        overexploited_domains=domain_analysis["overexploited"],
        underexplored_domains=domain_analysis["underexplored"],
        total_cost_usd=run_metrics["total_cost"],
        cost_per_curated=cost_per_curated,
        issues=issues,
        opportunities=opportunities,
    )

    logger.info(
        "observation_complete",
        runs_analyzed=len(run_metrics["run_ids"]),
        issues_found=len(issues),
        opportunities_found=len(opportunities),
    )

    return report
