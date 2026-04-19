"""Tests for the L1 mutation_locks mechanism.

Verifies that ``execute_proposal`` honors active locks declared in the
genome YAML's ``mutation_locks`` section.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_mutation_locks
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

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


def _write_genome(tmp_path: Path, future_iso: str, past_iso: str) -> None:
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
    with open(tmp_path, "w") as f:
        yaml.dump(genome, f, default_flow_style=False)


async def run_tests() -> int:
    failures = 0
    now = datetime.now()
    future_iso = (now + timedelta(days=3)).isoformat()
    past_iso = (now - timedelta(days=1)).isoformat()

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        genome_path = Path(tf.name)

    try:
        _write_genome(genome_path, future_iso, past_iso)

        # Sanity: helper picks up only the active lock
        active = _load_active_locks(genome_path)
        if set(active.keys()) != {"agents.synthesis.parameters.temperature"}:
            print(f"FAIL [helper] active keys = {set(active.keys())}")
            failures += 1
        else:
            print("PASS [helper] only future lock is active, expired one dropped")

        mutations = [
            _make_validated_mutation(
                "agents.synthesis.parameters.temperature", 0.7, 0.5
            ),
            _make_validated_mutation(
                "agents.curator.parameters.top_percent", 0.15, 0.20
            ),
            _make_validated_mutation(
                "score_weights.coherence", 0.4, 0.5
            ),
        ]
        proposal = StrategyProposal(
            based_on_observation=now.isoformat(),
            mutations=mutations,
            rationale="unit test",
        )

        applied, failed = await execute_proposal(
            proposal,
            genome_path=genome_path,
            commit_to_git=False,
            dry_run=False,
        )

        applied_paths = {m.target_path for m in applied}
        failed_paths = {m.target_path for m, _ in failed}

        # Case 1: locked-future mutation must be in failed with a "blocked" reason
        if "agents.synthesis.parameters.temperature" not in failed_paths:
            print("FAIL [case 1] locked-future mutation was not blocked")
            failures += 1
        else:
            blocked_reason = next(
                r for m, r in failed
                if m.target_path == "agents.synthesis.parameters.temperature"
            )
            if "blocked" not in blocked_reason.lower():
                print(f"FAIL [case 1] reason missing 'blocked': {blocked_reason!r}")
                failures += 1
            else:
                print(f"PASS [case 1] locked-future blocked: {blocked_reason}")

        # Case 2: unlocked mutation must apply
        if "agents.curator.parameters.top_percent" not in applied_paths:
            print("FAIL [case 2] unlocked mutation did not apply")
            failures += 1
        else:
            print("PASS [case 2] unlocked mutation applied")

        # Case 3: expired-lock mutation must apply
        if "score_weights.coherence" not in applied_paths:
            print("FAIL [case 3] expired-lock mutation did not apply")
            failures += 1
        else:
            print("PASS [case 3] expired-lock mutation applied")

        # Confirm the genome was actually mutated for the 2 applied paths
        with open(genome_path) as f:
            mutated = yaml.safe_load(f)
        if mutated["agents"]["curator"]["parameters"]["top_percent"] != 0.20:
            print("FAIL [genome write] top_percent not updated on disk")
            failures += 1
        if mutated["score_weights"]["coherence"] != 0.5:
            print("FAIL [genome write] coherence not updated on disk")
            failures += 1
        if mutated["agents"]["synthesis"]["parameters"]["temperature"] != 0.7:
            print("FAIL [genome write] temperature was mutated despite lock")
            failures += 1

    finally:
        genome_path.unlink(missing_ok=True)

    print()
    if failures:
        print(f"=== {failures} FAILURE(S) ===")
    else:
        print("=== ALL TESTS PASSED ===")
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(run_tests()))
