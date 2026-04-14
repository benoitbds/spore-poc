"""Runtime constitution guard for the L0 pipeline.

Enforces constitution.yaml rules before the pipeline processes collisions:
  1. Excluded domains — any collision touching an excluded domain kills the run.
  2. Daily budget — if cumulative cost today >= max_budget_per_day, kill.
  3. Chaos floor — if genome.randomness.chaos_floor < constitution.safety.chaos_floor,
     emit a WARNING (L1 has mutated below the floor) but do not kill.

The guard runs as a LangGraph node between `explorer` and `gate`. On kill, it
sets `state["killed"] = True` and `state["kill_reason"] = "..."` so the graph
can short-circuit to END via a conditional edge.
"""

from typing import Any

from config import get_constitution, get_genome
from storage.database import get_connection
from logging_config import get_logger

logger = get_logger("constitution_guard")


def _domain_is_excluded(domain_name: str, excluded: list[str]) -> str | None:
    """Return the matching excluded term if the domain violates, else None.

    Partial, case-insensitive match:
      - substring match after normalising ``_-`` to spaces
        (e.g. rule ``weapons`` matches domain ``weapons_development``);
      - shared-token fallback at length >= 3
        (e.g. rule ``weapons_development`` matches domain ``Weapons Research``
        via the shared token ``weapons``).
    """
    if not domain_name:
        return None
    dn_norm = domain_name.lower().replace("_", " ").replace("-", " ")
    dn_tokens = {t for t in dn_norm.split() if len(t) >= 3}
    for term in excluded:
        t_raw = (term or "").strip().lower()
        if not t_raw:
            continue
        t_norm = t_raw.replace("_", " ").replace("-", " ")
        if t_norm in dn_norm or dn_norm in t_norm:
            return term
        t_tokens = {t for t in t_norm.split() if len(t) >= 3}
        if t_tokens & dn_tokens:
            return term
    return None


async def _daily_cost_usd() -> float:
    """Sum of total_cost_usd across all runs started today (UTC-local of SQLite)."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd), 0.0) AS spent "
            "FROM runs WHERE date(started_at) = date('now')"
        )
        row = await cursor.fetchone()
        return float(row["spent"] if row and "spent" in row.keys() else 0.0)


async def check_constitution(
    state: dict[str, Any],
    constitution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enforce constitution rules on the pipeline state.

    Args:
        state: Current PipelineState (mutable dict).
        constitution: Constitution dict. If None, loads via ``get_constitution()``.

    Returns:
        Modified state. On violation, ``state["killed"]`` is True and
        ``state["kill_reason"]`` describes the reason.
    """
    if constitution is None:
        constitution = get_constitution().to_dict()

    ethics = constitution.get("ethics", {}) or {}
    safety = constitution.get("safety", {}) or {}
    excluded = [d for d in (ethics.get("excluded_domains") or []) if isinstance(d, str)]
    max_budget = float(safety.get("max_budget_per_day", 50.0))
    const_chaos_floor = float(safety.get("chaos_floor", 0.10))

    run_id = state.get("run_id", "?")

    # ── 1. Excluded domains ─────────────────────────────────
    collisions = state.get("collisions") or []
    collision_pairs = state.get("collision_pairs") or []
    # Prefer enriched collisions if present; fall back to the raw pairs.
    to_check = collisions if collisions else collision_pairs

    for item in to_check:
        # Works for both Collision (has domain_a/domain_b via @property)
        # and CollisionPair (domain_a/domain_b fields directly).
        try:
            da = getattr(item, "domain_a", None)
            db = getattr(item, "domain_b", None)
            da_name = getattr(da, "name", "") if da else ""
            db_name = getattr(db, "name", "") if db else ""
        except Exception:  # noqa: BLE001 — defensive, never want guard to crash
            continue

        for name in (da_name, db_name):
            hit = _domain_is_excluded(name, excluded)
            if hit:
                reason = f"Constitution: excluded domain '{name}' matches rule '{hit}'"
                logger.error(
                    "constitution_check_fail",
                    run_id=run_id,
                    rule="excluded_domains",
                    domain=name,
                    matched_term=hit,
                )
                state["killed"] = True
                state["kill_reason"] = reason
                return state

    logger.info(
        "constitution_check_pass",
        run_id=run_id,
        rule="excluded_domains",
        n_checked=len(to_check),
    )

    # ── 2. Daily budget ─────────────────────────────────────
    try:
        spent = await _daily_cost_usd()
    except Exception as exc:  # noqa: BLE001 — fail open on DB error, log loudly
        logger.error(
            "constitution_check_error",
            run_id=run_id,
            rule="daily_budget",
            error=str(exc),
        )
        spent = 0.0

    if spent >= max_budget:
        reason = (
            f"Constitution: daily budget exceeded "
            f"(${spent:.2f} spent >= ${max_budget:.2f} max)"
        )
        logger.error(
            "constitution_check_fail",
            run_id=run_id,
            rule="daily_budget",
            spent_usd=spent,
            max_usd=max_budget,
        )
        state["killed"] = True
        state["kill_reason"] = reason
        return state

    logger.info(
        "constitution_check_pass",
        run_id=run_id,
        rule="daily_budget",
        spent_usd=round(spent, 4),
        max_usd=max_budget,
    )

    # ── 3. Chaos floor (soft check: WARN only) ──────────────
    try:
        genome = get_genome()
        genome_chaos = float(
            genome.to_dict().get("randomness", {}).get("chaos_floor", const_chaos_floor)
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "constitution_check_error",
            run_id=run_id,
            rule="chaos_floor",
            error=str(exc),
        )
        genome_chaos = const_chaos_floor

    if genome_chaos < const_chaos_floor:
        logger.warning(
            "constitution_chaos_floor_breached",
            run_id=run_id,
            rule="chaos_floor",
            genome_chaos=genome_chaos,
            constitution_chaos=const_chaos_floor,
            note="L1 has mutated randomness.chaos_floor below the constitutional minimum",
        )
    else:
        logger.info(
            "constitution_check_pass",
            run_id=run_id,
            rule="chaos_floor",
            genome_chaos=genome_chaos,
            constitution_chaos=const_chaos_floor,
        )

    return state


async def constitution_guard_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node wrapper around ``check_constitution``.

    Reads the constitution injected into state (key ``constitution``), or falls
    back to loading via ``get_constitution()``.
    """
    constitution = state.get("constitution")
    return await check_constitution(state, constitution)
