"""Tests for the L1 mutation_locks mechanism and mutation_policy.

Covers:
  * basic lock check (existing): exact-path, active vs expired
  * parent/child/sibling lock conflict semantics
  * mutation_policy cooldown (gap < min vs gap > min)
  * mutation_policy per-cycle rate limit
  * mutation_policy oscillation detection (A -> B -> A)

Runs against a temporary SQLite DB and temporary genome YAML — no
pollution of production state.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_mutation_locks
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.l1_executor import execute_proposal, _load_active_locks
from models.mutation import (
    Mutation,
    MutationRisk,
    MutationStatus,
    MutationType,
    StrategyProposal,
)
from storage.database import SCHEMA, save_mutation, get_mutations_for_path


# ── fixtures ──────────────────────────────────────────────────────────

def _make_validated_mutation(target_path: str, old_value, new_value) -> Mutation:
    m = Mutation(
        type=MutationType.PARAMETER_CHANGE,
        target_path=target_path,
        old_value=old_value,
        new_value=new_value,
        description=f"test mutation on {target_path}",
        justification="unit test",
        estimated_impact="none",
        risk=MutationRisk.LOW,
    )
    m.status = MutationStatus.VALIDATED
    return m


def _write_basic_genome(tmp_path: Path) -> None:
    """Genome with several paths + no active locks. Tests set locks per case."""
    genome = {
        "agents": {
            "curator": {"parameters": {"top_percent": 0.15}},
            "synthesis": {"parameters": {"temperature": 0.7}},
        },
        "score_weights": {
            "coherence": 0.4,
            "novelty": 0.2,
            "hallucination_risk": -0.15,
        },
        "randomness": {"distance_min": 0.35, "distance_max": 0.85},
    }
    with open(tmp_path, "w") as f:
        yaml.dump(genome, f, default_flow_style=False)


def _write_genome_with_lock(tmp_path: Path, lock_path: str, lock_until_iso: str) -> None:
    genome = {
        "agents": {
            "curator": {"parameters": {"top_percent": 0.15}},
        },
        "score_weights": {
            "coherence": 0.4,
            "hallucination_risk": -0.15,
        },
        "mutation_locks": [
            {
                "path": lock_path,
                "locked_until": lock_until_iso,
                "reason": "test lock",
            },
        ],
    }
    with open(tmp_path, "w") as f:
        yaml.dump(genome, f, default_flow_style=False)


async def _make_temp_db() -> Path:
    fd, p = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    db_path = Path(p)
    async with aiosqlite.connect(db_path) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()
    return db_path


async def _seed_mutation(
    db_path: Path,
    cycle_id: str,
    path: str,
    old_value,
    new_value,
    applied_at: datetime,
    status: str = "applied",
) -> None:
    import json
    await save_mutation(
        {
            "id": f"SEED-{cycle_id}-{path}-{applied_at.timestamp()}",
            "cycle_id": cycle_id,
            "applied_at": applied_at.isoformat(),
            "mutation_type": "parameter_change",
            "target_path": path,
            "old_value": json.dumps(old_value, sort_keys=True, default=str),
            "new_value": json.dumps(new_value, sort_keys=True, default=str),
            "justification": "test seed",
            "status": status,
        },
        db_path=db_path,
    )


def _propose(mutations: list[Mutation]) -> StrategyProposal:
    return StrategyProposal(
        based_on_observation=datetime.now().isoformat(),
        mutations=mutations,
        rationale="test",
    )


# ── test cases ────────────────────────────────────────────────────────

async def test_basic_locks(report) -> None:
    """Original tests: active lock blocks, expired lock ignored, free path passes."""
    now = datetime.now()
    future_iso = (now + timedelta(days=3)).isoformat()
    past_iso = (now - timedelta(days=1)).isoformat()

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        # Two locks: one future, one past (expired)
        genome = {
            "agents": {
                "curator": {"parameters": {"top_percent": 0.15}},
                "synthesis": {"parameters": {"temperature": 0.7}},
            },
            "score_weights": {"coherence": 0.4},
            "mutation_locks": [
                {
                    "path": "agents.synthesis.parameters.temperature",
                    "locked_until": future_iso,
                    "reason": "future lock",
                },
                {
                    "path": "score_weights.coherence",
                    "locked_until": past_iso,
                    "reason": "expired lock",
                },
            ],
        }
        with open(genome_path, "w") as f:
            yaml.dump(genome, f, default_flow_style=False)

        active = _load_active_locks(genome_path)
        if set(active.keys()) != {"agents.synthesis.parameters.temperature"}:
            report("FAIL [helper] active keys = " + str(set(active.keys())))
        else:
            report("PASS [helper] only future lock is active, expired one dropped")

        mutations = [
            _make_validated_mutation("agents.synthesis.parameters.temperature", 0.7, 0.5),
            _make_validated_mutation("agents.curator.parameters.top_percent", 0.15, 0.20),
            _make_validated_mutation("score_weights.coherence", 0.4, 0.5),
        ]
        applied, failed = await execute_proposal(
            _propose(mutations),
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
            cycle_id="CYCLE-basic-locks",
            db_path=db_path,
        )
        applied_paths = {m.target_path for m in applied}
        failed_paths = {m.target_path for m, _ in failed}

        if "agents.synthesis.parameters.temperature" not in failed_paths:
            report("FAIL [basic 1] locked-future not blocked")
        else:
            report("PASS [basic 1] locked-future blocked")
        if "agents.curator.parameters.top_percent" not in applied_paths:
            report("FAIL [basic 2] unlocked not applied")
        else:
            report("PASS [basic 2] unlocked applied")
        if "score_weights.coherence" not in applied_paths:
            report("FAIL [basic 3] expired-lock not applied")
        else:
            report("PASS [basic 3] expired-lock applied")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_parent_child_sibling_locks(report) -> None:
    """Lock on 'score_weights.hallucination_risk' must also block parent
    'score_weights' and any child 'score_weights.hallucination_risk.foo',
    while leaving sibling 'score_weights.novelty' free."""
    future_iso = (datetime.now() + timedelta(days=3)).isoformat()

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        _write_genome_with_lock(
            genome_path,
            "score_weights.hallucination_risk",
            future_iso,
        )
        # Use the unlocked "novelty" sibling; add novelty to genome for apply step.
        with open(genome_path) as f:
            g = yaml.safe_load(f)
        g["score_weights"]["novelty"] = 0.2
        # Nested child requires a dict at hallucination_risk; use a sentinel
        g["score_weights"]["hallucination_risk"] = {"factor": 1.0}
        with open(genome_path, "w") as f:
            yaml.dump(g, f, default_flow_style=False)

        mutations = [
            # Parent of lock
            _make_validated_mutation("score_weights", {}, {"hallucination_risk": -0.1}),
            # Child of lock
            _make_validated_mutation(
                "score_weights.hallucination_risk.factor", 1.0, 0.8
            ),
            # Sibling — should pass
            _make_validated_mutation("score_weights.novelty", 0.2, 0.25),
        ]
        applied, failed = await execute_proposal(
            _propose(mutations),
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
            cycle_id="CYCLE-parent-child",
            db_path=db_path,
        )
        applied_paths = {m.target_path for m in applied}
        failed_paths = {m.target_path for m, _ in failed}

        if "score_weights" not in failed_paths:
            report("FAIL [parent-of-lock] parent mutation should be blocked")
        else:
            report("PASS [parent-of-lock] parent mutation blocked")
        if "score_weights.hallucination_risk.factor" not in failed_paths:
            report("FAIL [child-of-lock] child mutation should be blocked")
        else:
            report("PASS [child-of-lock] child mutation blocked")
        if "score_weights.novelty" not in applied_paths:
            report("FAIL [sibling] sibling mutation should pass")
        else:
            report("PASS [sibling] sibling mutation applied")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_cooldown_blocks_recent(report) -> None:
    """Path mutated in a recent cycle (gap < min_cycles=3) is blocked."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        _write_basic_genome(genome_path)

        # Seed: path mutated 10s ago. With n_cycles=3, this lone cycle is
        # in the window. The new cycle is immediately after — gap = 1.
        await _seed_mutation(
            db_path,
            cycle_id="CYCLE-PREV",
            path="score_weights.coherence",
            old_value=0.3,
            new_value=0.4,
            applied_at=datetime.now() - timedelta(seconds=10),
        )

        mutation = _make_validated_mutation("score_weights.coherence", 0.4, 0.5)
        applied, failed = await execute_proposal(
            _propose([mutation]),
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
            cycle_id="CYCLE-NEW",
            db_path=db_path,
        )
        reasons = {m.target_path: r for m, r in failed}
        if mutation.target_path in {m.target_path for m in applied}:
            report("FAIL [cooldown block] mutation applied despite recent cycle")
        elif "cooldown" in reasons.get(mutation.target_path, "").lower():
            report(f"PASS [cooldown block] reason: {reasons[mutation.target_path]}")
        else:
            report(f"FAIL [cooldown block] unexpected reason: {reasons}")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_cooldown_allows_old(report) -> None:
    """Path mutated long ago (pushed out of the last-3-cycles window) is allowed."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        _write_basic_genome(genome_path)

        # Seed an OLD mutation on path X and 3 NEWER mutations on OTHER paths.
        # With n_cycles=3, the top-3 distinct cycle_ids are the 3 newer ones
        # and X's cycle is out of window -> cooldown doesn't trigger.
        base = datetime.now()
        await _seed_mutation(
            db_path, "CYCLE-OLD", "score_weights.coherence",
            0.3, 0.4, base - timedelta(days=10),
        )
        for i, path in enumerate([
            "agents.curator.parameters.top_percent",
            "randomness.distance_min",
            "score_weights.novelty",
        ]):
            await _seed_mutation(
                db_path, f"CYCLE-N{i}", path,
                0.1, 0.2, base - timedelta(minutes=10 - i),
            )

        mutation = _make_validated_mutation("score_weights.coherence", 0.4, 0.5)
        applied, failed = await execute_proposal(
            _propose([mutation]),
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
            cycle_id="CYCLE-CURRENT",
            db_path=db_path,
        )
        if mutation.target_path in {m.target_path for m in applied}:
            report("PASS [cooldown allow] old mutation out of window -> applied")
        else:
            reasons = {m.target_path: r for m, r in failed}
            report(f"FAIL [cooldown allow] unexpectedly blocked: {reasons}")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_rate_limit_per_cycle(report) -> None:
    """Third mutation in a cycle must be blocked (max_per_cycle=2)."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        _write_basic_genome(genome_path)

        # Three mutations on distinct paths with no cooldown/lock conflict.
        mutations = [
            _make_validated_mutation("agents.curator.parameters.top_percent", 0.15, 0.20),
            _make_validated_mutation("randomness.distance_min", 0.35, 0.40),
            _make_validated_mutation("score_weights.novelty", 0.2, 0.25),
        ]
        applied, failed = await execute_proposal(
            _propose(mutations),
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
            cycle_id="CYCLE-RATE",
            db_path=db_path,
        )
        if len(applied) != 2:
            report(f"FAIL [rate limit] expected 2 applied, got {len(applied)}")
        elif len(failed) != 1:
            report(f"FAIL [rate limit] expected 1 failed, got {len(failed)}")
        else:
            reason = failed[0][1]
            if "rate limit" in reason.lower() or "max" in reason.lower():
                report(f"PASS [rate limit] 3rd mutation blocked: {reason}")
            else:
                report(f"FAIL [rate limit] wrong reason: {reason}")

        # Audit trail: the REJECTED mutation must be persisted to the DB
        blocked_rows = await get_mutations_for_path(
            "score_weights.novelty", n_cycles=5, db_path=db_path,
        )
        if any(r["status"] == "rejected" for r in blocked_rows):
            report("PASS [audit trail] REJECTED mutation persisted to mutations table")
        else:
            report(f"FAIL [audit trail] rejected mutation not in DB: {blocked_rows}")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_oscillation_detection(report) -> None:
    """A -> B -> A pattern must be blocked (max_reversals=1)."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        _write_basic_genome(genome_path)

        # Seed: past A -> B on path X in an OUT-of-cooldown-window cycle
        # (so the cooldown check doesn't short-circuit before we reach
        # the oscillation check). Cooldown window is 3 cycles; oscillation
        # window is 5. We need ≥ 3 other cycles newer than the seed so
        # cooldown passes, but within 5 so oscillation still sees it.
        base = datetime.now()
        await _seed_mutation(
            db_path, "CYCLE-AB", "score_weights.coherence",
            old_value=0.3, new_value=0.4,
            applied_at=base - timedelta(minutes=30),
        )
        # Three newer cycles on OTHER paths — pushes CYCLE-AB out of the
        # top-3 cooldown window but keeps it within the top-5 oscillation window.
        for i, path in enumerate([
            "agents.curator.parameters.top_percent",
            "randomness.distance_min",
            "score_weights.novelty",
        ]):
            await _seed_mutation(
                db_path, f"CYCLE-PAD{i}", path,
                0.1, 0.2, base - timedelta(minutes=20 - i),
            )

        # Propose B -> A reversal (new_value=0.3 == past old_value)
        mutation = _make_validated_mutation("score_weights.coherence", 0.4, 0.3)
        applied, failed = await execute_proposal(
            _propose([mutation]),
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
            cycle_id="CYCLE-REVERSAL",
            db_path=db_path,
        )
        reasons = {m.target_path: r for m, r in failed}
        if mutation.target_path in {m.target_path for m in applied}:
            report("FAIL [oscillation] A->B->A pattern was not blocked")
        elif "oscillation" in reasons.get(mutation.target_path, "").lower():
            report(f"PASS [oscillation] A->B->A blocked: {reasons[mutation.target_path]}")
        else:
            report(f"FAIL [oscillation] unexpected reason: {reasons}")

        # Negative control: propose B -> C (novel value, no reversal) -> should pass
        mutation2 = _make_validated_mutation("score_weights.coherence", 0.4, 0.6)
        applied2, failed2 = await execute_proposal(
            _propose([mutation2]),
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
            cycle_id="CYCLE-NOVEL",
            db_path=db_path,
        )
        if mutation2.target_path in {m.target_path for m in applied2}:
            report("PASS [oscillation negative] novel value applied")
        else:
            reasons2 = {m.target_path: r for m, r in failed2}
            report(f"FAIL [oscillation negative] novel value blocked: {reasons2}")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


# ── auto-rollback tests ───────────────────────────────────────────────

async def _seed_run(
    db_path: Path,
    run_id: str,
    completed_at: datetime,
    bridge_rate: float,
) -> None:
    """Insert a completed run row for the rollback aggregator to aggregate."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """INSERT OR REPLACE INTO runs
               (id, started_at, completed_at, collisions_requested, collisions_processed,
                hypotheses_generated, bridge_rate, total_tokens_in, total_tokens_out,
                total_cost_usd, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                completed_at.isoformat(),
                completed_at.isoformat(),
                100, 100, int(bridge_rate * 100),
                bridge_rate,
                0, 0, 0.0,
                "completed",
            ),
        )
        await conn.commit()


async def _seed_hypothesis(
    db_path: Path,
    hyp_id: str,
    generated_at: datetime,
    composite: float,
    status: str = "generated",
) -> None:
    scores_json = json.dumps({"composite": composite, "coherence": 0.4, "novelty": 0.3})
    collision_json = json.dumps({"domain_a": {"name": "A"}, "domain_b": {"name": "B"}})
    bridge_json = json.dumps({"summary": "test"})
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """INSERT OR REPLACE INTO hypotheses
               (id, generated_at, genome_version, collision_json, bridge_json,
                scores_json, kill_condition, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hyp_id, generated_at.isoformat(), "l0_v1",
                collision_json, bridge_json, scores_json,
                "n/a", status,
            ),
        )
        await conn.commit()


async def test_rollback_triggers_on_degradation(report) -> None:
    """A cycle whose post-mutation metrics degrade > threshold is reverted."""
    from agents.l1_rollback import check_and_apply_rollback

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        # Genome snapshot: post-mutation state (what's on disk now)
        genome_post = {
            "score_weights": {
                "coherence": 0.4,
                "hallucination_risk": -0.5,  # bad value the mutation applied
                "novelty": 0.2,
            },
        }
        with open(genome_path, "w") as f:
            yaml.dump(genome_post, f)

        base = datetime.now()
        # 5 pre-mutation runs with GOOD metrics (bridge 0.9, composite 0.65)
        for i in range(5):
            ts = base - timedelta(hours=24 + i)
            await _seed_run(db_path, f"RUN-PRE-{i}", ts, bridge_rate=0.9)
            await _seed_hypothesis(db_path, f"HYP-PRE-{i}-a", ts, composite=0.70)
            await _seed_hypothesis(
                db_path, f"HYP-PRE-{i}-b", ts, composite=0.60, status="curated",
            )

        # The cycle being evaluated — applied between pre and post runs.
        applied_at = base - timedelta(hours=20)
        await _seed_mutation(
            db_path, cycle_id="CYCLE-BAD",
            path="score_weights.hallucination_risk",
            old_value=-0.15,                                   # good baseline
            new_value=-0.5,                                    # what bricked it
            applied_at=applied_at, status="applied",
        )

        # 3 post-mutation runs with DEGRADED metrics (bridge 0.4, composite 0.30)
        for i in range(3):
            ts = base - timedelta(hours=15 - i)
            await _seed_run(db_path, f"RUN-POST-{i}", ts, bridge_rate=0.4)
            await _seed_hypothesis(db_path, f"HYP-POST-{i}-a", ts, composite=0.35)
            # curation rate also tanked: only 1 of 3 hypotheses curated
            if i == 0:
                await _seed_hypothesis(
                    db_path, f"HYP-POST-{i}-b", ts, composite=0.30, status="curated",
                )
            else:
                await _seed_hypothesis(db_path, f"HYP-POST-{i}-b", ts, composite=0.25)

        result = await check_and_apply_rollback(
            genome_path=genome_path,
            db_path=db_path,
        )

        if result["status"] != "rolled_back":
            report(f"FAIL [rollback trigger] expected rolled_back, got {result}")
            return

        report(
            f"PASS [rollback trigger] worst_metric={result['worst_metric']}, "
            f"degradation={result['worst_degradation']:.1%}, "
            f"threshold={result['threshold']:.0%}"
        )

        # Genome restoration: the bad value (-0.5) must be replaced by old (-0.15)
        with open(genome_path) as f:
            restored = yaml.safe_load(f)
        if restored["score_weights"]["hallucination_risk"] == -0.15:
            report("PASS [rollback genome] hallucination_risk restored to -0.15")
        else:
            report(
                f"FAIL [rollback genome] got "
                f"{restored['score_weights']['hallucination_risk']}, expected -0.15"
            )

        # DB status: mutation row marked rolled_back
        past_on_path = await get_mutations_for_path(
            "score_weights.hallucination_risk", n_cycles=5, db_path=db_path,
        )
        if any(m["status"] == "rolled_back" for m in past_on_path):
            report("PASS [rollback db] mutation marked rolled_back")
        else:
            report(f"FAIL [rollback db] mutation not marked: {past_on_path}")

    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_rollback_skips_insufficient_runs(report) -> None:
    """Fewer than MIN_POST_MUTATION_RUNS after the mutation -> defer."""
    from agents.l1_rollback import check_and_apply_rollback

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        with open(genome_path, "w") as f:
            yaml.dump({"score_weights": {"coherence": 0.4}}, f)

        base = datetime.now()
        await _seed_mutation(
            db_path, cycle_id="CYCLE-RECENT",
            path="score_weights.coherence",
            old_value=0.3, new_value=0.45,
            applied_at=base - timedelta(minutes=5),
            status="applied",
        )
        # Only ONE post-mutation run (< MIN=2)
        await _seed_run(db_path, "RUN-ONLY-ONE", base - timedelta(minutes=2), 0.5)

        result = await check_and_apply_rollback(genome_path=genome_path, db_path=db_path)
        if result["status"] == "insufficient_runs" and result["runs_after"] == 1:
            report("PASS [rollback defer] only 1 post-mutation run -> insufficient_runs")
        else:
            report(f"FAIL [rollback defer] expected insufficient_runs, got {result}")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_rollback_skips_within_threshold(report) -> None:
    """Degradation below threshold -> within_threshold, no rollback."""
    from agents.l1_rollback import check_and_apply_rollback

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)
    db_path = await _make_temp_db()
    try:
        # Initial genome with a "mutated" value
        with open(genome_path, "w") as f:
            yaml.dump({"score_weights": {"coherence": 0.4}}, f)

        base = datetime.now()
        # Good pre-mutation runs
        for i in range(5):
            ts = base - timedelta(hours=24 + i)
            await _seed_run(db_path, f"RUN-PRE-{i}", ts, bridge_rate=0.80)
            await _seed_hypothesis(db_path, f"HYP-PRE-{i}", ts, composite=0.60)

        applied_at = base - timedelta(hours=20)
        await _seed_mutation(
            db_path, cycle_id="CYCLE-MILD",
            path="score_weights.coherence",
            old_value=0.4, new_value=0.45,
            applied_at=applied_at, status="applied",
        )

        # Post-mutation: only 5% degradation (well below 15%)
        for i in range(3):
            ts = base - timedelta(hours=15 - i)
            await _seed_run(db_path, f"RUN-POST-{i}", ts, bridge_rate=0.76)
            await _seed_hypothesis(db_path, f"HYP-POST-{i}", ts, composite=0.57)

        result = await check_and_apply_rollback(genome_path=genome_path, db_path=db_path)
        if result["status"] == "within_threshold":
            report(
                f"PASS [rollback skip mild] max_deg={max(result['per_metric_degradation'].values()):.1%} "
                f"below threshold={result['threshold']:.0%}"
            )
        else:
            report(f"FAIL [rollback skip mild] expected within_threshold, got {result}")
    finally:
        genome_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


async def test_rollback_threshold_from_constitution(report) -> None:
    """Sanity: the orchestrator reads rollback_threshold from constitution.yaml."""
    from agents.l1_rollback import _rollback_threshold
    t = _rollback_threshold()
    # constitution.yaml has 0.15; tolerate ±0.001 for float read from YAML
    if abs(t - 0.15) < 0.001:
        report(f"PASS [rollback threshold] read {t} from constitution")
    else:
        report(f"FAIL [rollback threshold] expected 0.15, got {t}")


# ── runner ────────────────────────────────────────────────────────────

async def run_tests() -> int:
    results: list[str] = []
    def report(msg: str) -> None:
        print(msg)
        results.append(msg)

    await test_basic_locks(report)
    print()
    await test_parent_child_sibling_locks(report)
    print()
    await test_cooldown_blocks_recent(report)
    print()
    await test_cooldown_allows_old(report)
    print()
    await test_rate_limit_per_cycle(report)
    print()
    await test_oscillation_detection(report)
    print()
    await test_rollback_threshold_from_constitution(report)
    print()
    await test_rollback_skips_insufficient_runs(report)
    print()
    await test_rollback_skips_within_threshold(report)
    print()
    await test_rollback_triggers_on_degradation(report)

    failures = sum(1 for r in results if r.startswith("FAIL"))
    print()
    if failures:
        print(f"=== {failures} FAILURE(S) of {len(results)} assertions ===")
    else:
        print(f"=== ALL {len(results)} ASSERTIONS PASSED ===")
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(run_tests()))
