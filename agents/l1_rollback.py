"""L1 auto-rollback orchestrator.

Runs as Phase 0 of every L1 cycle: identifies the most recent applied
mutation cycle, compares L0 metrics before vs after, and reverts the
whole cycle if degradation exceeds the constitution's rollback_threshold.

The executor's ``rollback_cycle_paths`` and ``check_for_rollback``
helpers exist since b42e3c3 but had no caller — this module wires them
into the pipeline.
"""

import json
from pathlib import Path
from typing import Any, Optional

from config import get_constitution, get_settings
from storage.database import (
    _mutations_connection,
    get_recent_mutations,
    update_mutation_status,
)
from agents.l1_executor import rollback_cycle_paths
from logging_config import get_logger

logger = get_logger("l1_rollback")


MIN_POST_MUTATION_RUNS = 2
WINDOW_RUNS = 5          # Runs aggregated on each side of the mutation
LOOKBACK_CYCLES = 10     # Search depth in the mutations table
_KEY_METRICS = ("bridge_rate", "avg_composite_score", "curation_rate")


def _rollback_threshold() -> float:
    """Read the constitution-level threshold (default 0.15)."""
    safety = get_constitution().to_dict().get("safety", {}) or {}
    raw = safety.get("rollback_threshold", 0.15)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.15


async def _find_target_cycle(db_path: Optional[Path]) -> Optional[dict]:
    """Return the most recent cycle with applied (non-rolled-back) mutations.

    Shape: ``{"cycle_id": str, "earliest_applied_at": iso, "mutations": [dict]}``
    or ``None`` if history is empty / every recent cycle is already rolled back.
    """
    recent = await get_recent_mutations(n_cycles=LOOKBACK_CYCLES, db_path=db_path)
    if not recent:
        return None

    # Group by cycle_id, remember the latest applied_at per cycle for ordering.
    cycles: dict[str, dict] = {}
    for m in recent:
        cid = m["cycle_id"]
        entry = cycles.setdefault(
            cid,
            {"cycle_id": cid, "mutations": [], "earliest_applied_at": m["applied_at"], "latest_applied_at": m["applied_at"]},
        )
        entry["mutations"].append(m)
        if m["applied_at"] < entry["earliest_applied_at"]:
            entry["earliest_applied_at"] = m["applied_at"]
        if m["applied_at"] > entry["latest_applied_at"]:
            entry["latest_applied_at"] = m["applied_at"]

    # Most recent cycle first.
    ordered = sorted(cycles.values(), key=lambda c: c["latest_applied_at"], reverse=True)

    for c in ordered:
        applied = [m for m in c["mutations"] if m["status"] == "applied"]
        if not applied:
            # Cycle had only rejected / already rolled_back entries — skip.
            continue
        if any(m["status"] == "rolled_back" for m in c["mutations"]):
            # Cycle was already partially/fully rolled back previously.
            continue
        return {
            "cycle_id": c["cycle_id"],
            "earliest_applied_at": c["earliest_applied_at"],
            "mutations": applied,
        }
    return None


async def _count_runs_since(applied_at_iso: str, db_path: Optional[Path]) -> int:
    async with _mutations_connection(db_path) as conn:
        cursor = await conn.execute(
            """SELECT COUNT(*) FROM runs
               WHERE completed_at IS NOT NULL
                 AND completed_at >= ?
                 AND status = 'completed'""",
            (applied_at_iso,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def _aggregate_window(
    applied_at_iso: str,
    side: str,            # 'before' or 'after'
    n_runs: int,
    db_path: Optional[Path],
) -> dict[str, float]:
    """Mean of bridge_rate / composite / curation_rate over the window.

    side='before': last N completed runs strictly before applied_at.
    side='after':  first N completed runs at or after applied_at.
    Keys returned match what ``check_for_rollback`` expects.
    """
    if side not in ("before", "after"):
        raise ValueError(f"invalid side: {side!r}")

    if side == "before":
        where = "completed_at < ?"
        order = "DESC"
    else:
        where = "completed_at >= ?"
        order = "ASC"

    async with _mutations_connection(db_path) as conn:
        cursor = await conn.execute(
            f"""SELECT id, bridge_rate, completed_at
                FROM runs
                WHERE {where}
                  AND status = 'completed'
                  AND completed_at IS NOT NULL
                ORDER BY completed_at {order}
                LIMIT ?""",
            (applied_at_iso, n_runs),
        )
        runs = await cursor.fetchall()

    if not runs:
        return {}

    bridge_rates = [r["bridge_rate"] for r in runs if r["bridge_rate"] is not None]
    run_ids = [r["id"] for r in runs]

    # Hypotheses aggregation over the same run_ids (via timestamp window)
    min_t = min(r["completed_at"] for r in runs)
    max_t = max(r["completed_at"] for r in runs)

    async with _mutations_connection(db_path) as conn:
        cursor = await conn.execute(
            """SELECT scores_json, status
               FROM hypotheses
               WHERE generated_at >= ? AND generated_at <= ?""",
            (min_t, max_t),
        )
        hyps = await cursor.fetchall()

    composites: list[float] = []
    total = 0
    curated = 0
    for h in hyps:
        total += 1
        if (h["status"] or "").lower() == "curated":
            curated += 1
        scores_raw = h["scores_json"]
        if not scores_raw:
            continue
        try:
            scores = json.loads(scores_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        composite = scores.get("composite") if isinstance(scores, dict) else None
        if isinstance(composite, (int, float)):
            composites.append(float(composite))

    return {
        "bridge_rate": (sum(bridge_rates) / len(bridge_rates)) if bridge_rates else 0.0,
        "avg_composite_score": (sum(composites) / len(composites)) if composites else 0.0,
        "curation_rate": (curated / total) if total else 0.0,
        "n_runs": len(runs),
        "n_hypotheses": total,
        "run_ids": run_ids,
    }


def _evaluate_degradation(
    metrics_before: dict[str, float],
    metrics_after: dict[str, float],
    threshold: float,
) -> tuple[Optional[str], float, dict[str, float]]:
    """Per-metric (before-after)/before.

    Returns (worst_metric_or_None_if_ok, worst_degradation, per_metric_dict).
    Metrics where ``before == 0`` are skipped (no baseline = no signal).
    """
    per_metric: dict[str, float] = {}
    worst_metric: Optional[str] = None
    worst_deg = 0.0
    for k in _KEY_METRICS:
        b = metrics_before.get(k, 0.0)
        a = metrics_after.get(k, 0.0)
        if b is None or b <= 0:
            per_metric[k] = 0.0
            continue
        deg = (b - a) / b
        per_metric[k] = deg
        if deg > threshold and deg > worst_deg:
            worst_metric = k
            worst_deg = deg
    return worst_metric, worst_deg, per_metric


async def check_and_apply_rollback(
    genome_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Phase 0 of the L1 cycle: evaluate the previous cycle, roll back if bad.

    Status values in the return dict:
      - no_history          : mutations table empty or only rejected entries
      - already_rolled_back : the target cycle has rolled_back rows already
      - insufficient_runs   : fewer than MIN_POST_MUTATION_RUNS L0 runs after apply
      - no_baseline         : zero runs before applied_at to compare against
      - within_threshold    : all 3 key metrics degraded by <= rollback_threshold
      - rolled_back         : rollback applied; genome restored, DB updated
    """
    if genome_path is None:
        genome_path = get_settings().genome_path
    threshold = _rollback_threshold()

    target = await _find_target_cycle(db_path)
    if target is None:
        logger.info("rollback_no_history")
        return {"status": "no_history", "threshold": threshold}

    cycle_id = target["cycle_id"]
    earliest = target["earliest_applied_at"]
    mutations = target["mutations"]

    n_after = await _count_runs_since(earliest, db_path)
    if n_after < MIN_POST_MUTATION_RUNS:
        logger.info(
            "rollback_insufficient_runs",
            cycle_id=cycle_id,
            runs_after=n_after,
            min_required=MIN_POST_MUTATION_RUNS,
        )
        return {
            "status": "insufficient_runs",
            "cycle_id": cycle_id,
            "runs_after": n_after,
            "min_required": MIN_POST_MUTATION_RUNS,
            "threshold": threshold,
        }

    metrics_before = await _aggregate_window(earliest, "before", WINDOW_RUNS, db_path)
    metrics_after = await _aggregate_window(earliest, "after", WINDOW_RUNS, db_path)

    if not metrics_before:
        logger.warning("rollback_no_baseline", cycle_id=cycle_id)
        return {
            "status": "no_baseline",
            "cycle_id": cycle_id,
            "metrics_after": metrics_after,
            "threshold": threshold,
        }

    worst_metric, worst_deg, per_metric = _evaluate_degradation(
        metrics_before, metrics_after, threshold,
    )

    if worst_metric is None:
        logger.info(
            "rollback_within_threshold",
            cycle_id=cycle_id,
            per_metric_degradation={k: round(v, 4) for k, v in per_metric.items()},
            threshold=threshold,
        )
        return {
            "status": "within_threshold",
            "cycle_id": cycle_id,
            "metrics_before": metrics_before,
            "metrics_after": metrics_after,
            "per_metric_degradation": per_metric,
            "threshold": threshold,
        }

    # TRIGGER ROLLBACK
    reason = (
        f"Auto-rollback: {worst_metric} degraded by {worst_deg:.1%} "
        f"(threshold {threshold:.0%}) — cycle {cycle_id}"
    )
    logger.warning(
        "auto_rollback_triggered",
        cycle_id=cycle_id,
        worst_metric=worst_metric,
        worst_degradation=round(worst_deg, 4),
        threshold=threshold,
        per_metric_degradation={k: round(v, 4) for k, v in per_metric.items()},
        metrics_before={k: metrics_before.get(k) for k in _KEY_METRICS},
        metrics_after={k: metrics_after.get(k) for k in _KEY_METRICS},
        n_mutations=len(mutations),
    )

    paths_with_old: list[tuple[str, Any]] = []
    for m in mutations:
        try:
            old = json.loads(m["old_value"]) if m["old_value"] is not None else None
        except (json.JSONDecodeError, TypeError):
            old = m["old_value"]
        paths_with_old.append((m["target_path"], old))

    success, summary = rollback_cycle_paths(
        paths_with_old_values=paths_with_old,
        genome_path=Path(genome_path),
        cycle_id=cycle_id,
        reason=reason,
    )

    rolled_back_ids: list[str] = []
    if success:
        for m in mutations:
            try:
                await update_mutation_status(m["id"], "rolled_back", db_path=db_path)
                rolled_back_ids.append(m["id"])
            except Exception as exc:
                logger.error(
                    "rollback_db_status_update_failed",
                    mutation_id=m["id"],
                    error=str(exc),
                )

    return {
        "status": "rolled_back" if success else "rollback_failed",
        "cycle_id": cycle_id,
        "worst_metric": worst_metric,
        "worst_degradation": worst_deg,
        "threshold": threshold,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "per_metric_degradation": per_metric,
        "rolled_back_mutation_ids": rolled_back_ids,
        "error": None if success else summary,
    }
