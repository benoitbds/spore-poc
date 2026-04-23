"""L1 Executor Agent - Applies validated mutations to the genome.

Part of Team L1 (Trainers) - Design Doc section 2.3.

The Executor:
1. Applies validated mutations to l0_genome.yaml
2. Commits changes to git with descriptive message
3. Logs mutations to SQLite (when table exists)
4. Supports automatic rollback if metrics degrade
"""

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from config import get_settings, get_genome, get_constitution
from models.mutation import (
    Mutation,
    MutationStatus,
    StrategyProposal,
)
from storage.database import (
    save_mutation,
    get_mutations_for_path,
    get_recent_mutations,
)
from logging_config import get_logger

logger = get_logger("l1_executor")


# Fail-safe defaults used when constitution.yaml lacks a mutation_policy section.
# Mirrored in data/constitution.yaml and config.py:_default_constitution.
_DEFAULT_MUTATION_POLICY: dict[str, Any] = {
    "min_cycles_between_mutations_same_path": 3,
    "max_mutations_per_cycle": 2,
    "oscillation_detection": {
        "enabled": True,
        "window_cycles": 5,
        "max_reversals_per_path": 1,
    },
}


def get_nested_value(data: dict, path: str) -> any:
    """Get a value from nested dict using dot notation path.

    Args:
        data: Dictionary to traverse
        path: Dot-separated path (e.g., "agents.synthesis.parameters.max_tokens")

    Returns:
        Value at path or None if not found
    """
    keys = path.split(".")
    current = data

    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None

    return current


def set_nested_value(data: dict, path: str, value: any) -> bool:
    """Set a value in nested dict using dot notation path.

    Args:
        data: Dictionary to modify
        path: Dot-separated path
        value: Value to set

    Returns:
        True if successful, False if path doesn't exist
    """
    keys = path.split(".")
    current = data

    # Navigate to parent
    for key in keys[:-1]:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return False

    # Set the value
    if isinstance(current, dict):
        current[keys[-1]] = value
        return True

    return False


def _load_active_locks(
    genome_path: Path,
) -> dict[str, tuple[datetime, str]]:
    """Load active (non-expired) mutation locks from genome YAML.

    Reads the ``mutation_locks`` section and returns a mapping
    ``target_path -> (locked_until_dt, reason)`` restricted to locks
    whose ``locked_until`` is strictly in the future. Expired or
    malformed entries are silently dropped.

    Args:
        genome_path: Path to genome YAML file.

    Returns:
        Mapping of locked paths to (expiry datetime, reason).
    """
    with open(genome_path) as f:
        data = yaml.safe_load(f) or {}

    raw_locks = data.get("mutation_locks") or []
    now = datetime.now()
    active: dict[str, tuple[datetime, str]] = {}

    for entry in raw_locks:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        until_raw = entry.get("locked_until")
        if not path or until_raw is None:
            continue

        if isinstance(until_raw, datetime):
            until = until_raw
        elif isinstance(until_raw, date):
            until = datetime.combine(until_raw, datetime.min.time())
        elif isinstance(until_raw, str):
            try:
                until = datetime.fromisoformat(until_raw)
            except ValueError:
                continue
        else:
            continue

        if until > now:
            active[path] = (until, entry.get("reason", ""))

    return active


def _load_mutation_policy() -> dict[str, Any]:
    """Load mutation_policy from constitution, merge with safe defaults.

    If the section is absent, logs a warning and returns the built-in defaults
    so that removing the section cannot silently disable safety.
    """
    raw = get_constitution().to_dict()
    section = raw.get("mutation_policy")
    if section is None:
        logger.warning(
            "mutation_policy_missing_using_defaults",
            defaults=_DEFAULT_MUTATION_POLICY,
        )
        return {
            **_DEFAULT_MUTATION_POLICY,
            "oscillation_detection": dict(
                _DEFAULT_MUTATION_POLICY["oscillation_detection"]
            ),
        }
    merged = {**_DEFAULT_MUTATION_POLICY, **section}
    merged["oscillation_detection"] = {
        **_DEFAULT_MUTATION_POLICY["oscillation_detection"],
        **(section.get("oscillation_detection") or {}),
    }
    return merged


def _find_conflicting_lock(
    mutation_path: str,
    active_locks: dict[str, tuple[datetime, str]],
) -> Optional[str]:
    """Return a locked path that conflicts with mutation_path, or None.

    Coverage (exact, parent-of-locked, child-of-locked):
      lock="score_weights.hallucination_risk"
      mutation="score_weights.hallucination_risk"      -> conflicts (exact)
      mutation="score_weights"                          -> conflicts (parent of lock)
      mutation="score_weights.hallucination_risk.x"     -> conflicts (child of lock)
      mutation="score_weights.novelty"                  -> does NOT conflict (sibling)
    """
    for locked_path in active_locks:
        if mutation_path == locked_path:
            return locked_path
        if mutation_path.startswith(locked_path + "."):
            return locked_path
        if locked_path.startswith(mutation_path + "."):
            return locked_path
    return None


def _values_equal(a: Any, b: Any) -> bool:
    """Compare two mutation values. Float tolerance for numerics; sorted JSON otherwise."""
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(
            b, sort_keys=True, default=str
        )


def _serialize_value(value: Any) -> str:
    """Serialize a mutation value for SQLite storage (deterministic JSON)."""
    return json.dumps(value, sort_keys=True, default=str)


def _deserialize_value(serialized: Optional[str]) -> Any:
    if serialized is None:
        return None
    try:
        return json.loads(serialized)
    except (json.JSONDecodeError, TypeError):
        return serialized


def _count_reversals_to(
    proposed_new_value: Any,
    past_mutations: list[dict],
) -> int:
    """Count how many past mutations on the same path had old_value matching the
    proposed new_value. An A -> B -> A pattern yields count=1."""
    count = 0
    for m in past_mutations:
        past_old = _deserialize_value(m.get("old_value"))
        if _values_equal(proposed_new_value, past_old):
            count += 1
    return count


def apply_mutation_to_genome(
    mutation: Mutation,
    genome_path: Path,
) -> tuple[bool, str]:
    """Apply a single mutation to the genome file.

    Args:
        mutation: Validated mutation to apply
        genome_path: Path to genome YAML file

    Returns:
        Tuple of (success, message)
    """
    # Load current genome
    with open(genome_path) as f:
        genome_data = yaml.safe_load(f)

    # Verify current value matches expected
    current_value = get_nested_value(genome_data, mutation.target_path)

    # Allow for type coercion in comparison
    if current_value != mutation.old_value:
        # Try string comparison
        if str(current_value) != str(mutation.old_value):
            return False, (
                f"Current value mismatch: expected {mutation.old_value}, "
                f"found {current_value}"
            )

    # Apply mutation
    if not set_nested_value(genome_data, mutation.target_path, mutation.new_value):
        return False, f"Failed to set value at path: {mutation.target_path}"

    # Update metadata
    genome_data["last_mutated"] = datetime.now().isoformat()
    genome_data["mutated_by"] = f"L1-{mutation.id}"

    # Write back
    with open(genome_path, "w") as f:
        yaml.dump(genome_data, f, default_flow_style=False, allow_unicode=True)

    return True, f"Applied: {mutation.target_path} = {mutation.new_value}"


def git_commit_mutation(
    mutation: Mutation,
    genome_path: Path,
) -> tuple[bool, str]:
    """Commit the mutation to git.

    Args:
        mutation: The applied mutation
        genome_path: Path to genome file

    Returns:
        Tuple of (success, message)
    """
    try:
        # Stage the genome file
        subprocess.run(
            ["git", "add", str(genome_path)],
            check=True,
            capture_output=True,
            cwd=genome_path.parent,
        )

        # Create commit message
        commit_msg = f"""[L1] {mutation.type.value}: {mutation.target_path}

Mutation ID: {mutation.id}
Change: {mutation.old_value} -> {mutation.new_value}

Justification: {mutation.justification}

Expected impact: {mutation.estimated_impact}
Risk level: {mutation.risk.value}

Co-Authored-By: SPORE L1 Strategist <noreply@spore.local>
"""

        # Commit
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            check=True,
            capture_output=True,
            text=True,
            cwd=genome_path.parent,
        )

        return True, f"Committed: {result.stdout.strip()}"

    except subprocess.CalledProcessError as e:
        return False, f"Git error: {e.stderr}"
    except Exception as e:
        return False, f"Error: {str(e)}"


async def execute_proposal(
    proposal: StrategyProposal,
    genome_path: Optional[Path] = None,
    commit_to_git: bool = True,
    dry_run: bool = False,
    cycle_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> tuple[list[Mutation], list[tuple[Mutation, str]]]:
    """Execute all validated mutations in a proposal.

    Lock and policy checks ordering (each one, if triggered, marks the mutation
    REJECTED and records it to the mutations table without applying):
      1. exact-or-parent-or-child path lock conflict
      2. cooldown (same path mutated within min_cycles_between_mutations_same_path)
      3. per-cycle rate limit (max_mutations_per_cycle applied so far)
      4. oscillation detection (A -> B -> A within window_cycles)

    Args:
        proposal: Proposal with validated mutations
        genome_path: Path to genome file (default: from settings)
        commit_to_git: Whether to commit changes to git
        dry_run: If True, don't actually apply changes and don't persist history
        cycle_id: L1 cycle identifier; if None, generates MANUAL-<ts>
        db_path: Override SQLite path (tests only)
    """
    if genome_path is None:
        settings = get_settings()
        genome_path = settings.genome_path

    if cycle_id is None:
        cycle_id = f"MANUAL-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    active_locks = _load_active_locks(genome_path)
    policy = _load_mutation_policy()
    osc_cfg = policy["oscillation_detection"]
    max_per_cycle = int(policy["max_mutations_per_cycle"])
    min_cycles_gap = int(policy["min_cycles_between_mutations_same_path"])

    logger.info(
        "executor_starting",
        cycle_id=cycle_id,
        mutations_to_apply=len(proposal.mutations),
        dry_run=dry_run,
        active_locks=len(active_locks),
        max_per_cycle=max_per_cycle,
        min_cycles_gap=min_cycles_gap,
        oscillation_enabled=osc_cfg["enabled"],
    )

    applied: list[Mutation] = []
    failed: list[tuple[Mutation, str]] = []
    applied_this_cycle = 0

    async def _record(mutation: Mutation, final_status: MutationStatus) -> None:
        """Persist mutation outcome to SQLite (skipped in dry_run per design D2)."""
        if dry_run:
            return
        try:
            await save_mutation(
                {
                    "id": mutation.id,
                    "cycle_id": cycle_id,
                    "applied_at": datetime.now().isoformat(),
                    "mutation_type": mutation.type.value,
                    "target_path": mutation.target_path,
                    "old_value": _serialize_value(mutation.old_value),
                    "new_value": _serialize_value(mutation.new_value),
                    "justification": mutation.justification,
                    "status": final_status.value,
                },
                db_path=db_path,
            )
        except Exception as e:
            logger.warning(
                "save_mutation_failed",
                mutation_id=mutation.id,
                error=str(e),
            )

    for mutation in proposal.mutations:
        if mutation.status != MutationStatus.VALIDATED:
            failed.append((mutation, "Not validated"))
            continue

        # 1. Lock check — exact + parent + child conflict
        conflict = _find_conflicting_lock(mutation.target_path, active_locks)
        if conflict is not None:
            until, reason = active_locks[conflict]
            if conflict == mutation.target_path:
                scope = "exact"
            elif mutation.target_path.startswith(conflict + "."):
                scope = "child-of-lock"
            else:
                scope = "parent-of-lock"
            block_msg = (
                f"path conflicts with locked '{conflict}' ({scope}), "
                f"until {until.isoformat()}: {reason}"
            )
            logger.warning(
                "mutation_blocked_by_lock",
                cycle_id=cycle_id,
                mutation_id=mutation.id,
                path=mutation.target_path,
                locked_path=conflict,
                conflict_scope=scope,
                locked_until=until.isoformat(),
                reason=reason,
            )
            mutation.status = MutationStatus.REJECTED
            failed.append((mutation, f"Mutation blocked: {block_msg}"))
            await _record(mutation, MutationStatus.REJECTED)
            continue

        # 2. Cooldown — same path mutated within last min_cycles_gap cycles
        recent_on_path = await get_mutations_for_path(
            mutation.target_path,
            n_cycles=min_cycles_gap,
            db_path=db_path,
        )
        cooldown_hit = [r for r in recent_on_path if r.get("status") == MutationStatus.APPLIED.value]
        if cooldown_hit:
            last = cooldown_hit[0]  # newest first in helper result
            block_msg = (
                f"cooldown: path muted in cycle {last.get('cycle_id')} "
                f"(last {min_cycles_gap} cycles), min_gap={min_cycles_gap}"
            )
            logger.warning(
                "mutation_policy_blocked",
                cycle_id=cycle_id,
                mutation_id=mutation.id,
                path=mutation.target_path,
                rule="cooldown",
                min_cycles_gap=min_cycles_gap,
                previous_cycle_id=last.get("cycle_id"),
            )
            mutation.status = MutationStatus.REJECTED
            failed.append((mutation, f"Mutation blocked: {block_msg}"))
            await _record(mutation, MutationStatus.REJECTED)
            continue

        # 3. Rate limit — per current cycle
        if applied_this_cycle >= max_per_cycle:
            block_msg = (
                f"rate limit: {applied_this_cycle} mutations already applied "
                f"this cycle, max={max_per_cycle}"
            )
            logger.warning(
                "mutation_policy_blocked",
                cycle_id=cycle_id,
                mutation_id=mutation.id,
                path=mutation.target_path,
                rule="max_per_cycle",
                max_per_cycle=max_per_cycle,
                applied_so_far=applied_this_cycle,
            )
            mutation.status = MutationStatus.REJECTED
            failed.append((mutation, f"Mutation blocked: {block_msg}"))
            await _record(mutation, MutationStatus.REJECTED)
            continue

        # 4. Oscillation detection — proposed new_value equals a past old_value
        if osc_cfg.get("enabled"):
            past_window = await get_mutations_for_path(
                mutation.target_path,
                n_cycles=int(osc_cfg["window_cycles"]),
                db_path=db_path,
            )
            reversal_count = _count_reversals_to(mutation.new_value, past_window)
            max_reversals = int(osc_cfg["max_reversals_per_path"])
            if reversal_count >= max_reversals:
                block_msg = (
                    f"oscillation detected: path has {reversal_count} reversal(s) "
                    f"in last {osc_cfg['window_cycles']} cycles "
                    f"(max={max_reversals})"
                )
                logger.warning(
                    "mutation_policy_blocked",
                    cycle_id=cycle_id,
                    mutation_id=mutation.id,
                    path=mutation.target_path,
                    rule="oscillation",
                    reversal_count=reversal_count,
                    max_reversals=max_reversals,
                    window_cycles=osc_cfg["window_cycles"],
                )
                mutation.status = MutationStatus.REJECTED
                failed.append((mutation, f"Mutation blocked: {block_msg}"))
                await _record(mutation, MutationStatus.REJECTED)
                continue

        if dry_run:
            logger.info(
                "dry_run_mutation",
                mutation_id=mutation.id,
                path=mutation.target_path,
                new_value=mutation.new_value,
            )
            mutation.status = MutationStatus.APPLIED
            mutation.applied_at = datetime.now()
            applied.append(mutation)
            applied_this_cycle += 1
            continue

        # Apply the mutation to genome YAML
        success, message = apply_mutation_to_genome(mutation, genome_path)

        if not success:
            logger.error(
                "mutation_apply_failed",
                mutation_id=mutation.id,
                error=message,
            )
            failed.append((mutation, message))
            continue

        if commit_to_git:
            git_success, git_message = git_commit_mutation(mutation, genome_path)
            if not git_success:
                logger.warning(
                    "git_commit_failed",
                    mutation_id=mutation.id,
                    error=git_message,
                )
                # Don't fail the mutation, just log

        mutation.status = MutationStatus.APPLIED
        mutation.applied_at = datetime.now()
        applied.append(mutation)
        applied_this_cycle += 1
        await _record(mutation, MutationStatus.APPLIED)

        logger.info(
            "mutation_applied",
            mutation_id=mutation.id,
            path=mutation.target_path,
            new_value=mutation.new_value,
        )

    logger.info(
        "executor_complete",
        applied=len(applied),
        failed=len(failed),
    )

    return applied, failed


def rollback_cycle_paths(
    paths_with_old_values: list[tuple[str, Any]],
    genome_path: Path,
    cycle_id: str,
    reason: str,
    commit_to_git: bool = True,
) -> tuple[bool, str]:
    """Atomically restore several genome paths to their pre-mutation values.

    Used by the L1 auto-rollback orchestrator when a whole cycle's
    mutations need to be reverted because L0 metrics degraded past the
    constitution's rollback_threshold. Writes the YAML once and makes
    a single git commit — cleaner than N sequential ``rollback_mutation``
    calls for a multi-mutation cycle.

    Args:
        paths_with_old_values: (target_path, old_value) tuples. Order is
            not important for correctness (each path is a leaf set).
        genome_path: Path to l0_genome.yaml.
        cycle_id: L1 cycle whose mutations are being reverted; used in
            the ``mutated_by`` field and the git commit subject.
        reason: Human-readable trigger (e.g. "composite degraded 18%").
        commit_to_git: Skip the git commit when running from tests.

    Returns:
        ``(True, summary)`` on success, ``(False, error)`` if any path
        fails to set or the YAML write fails. Git commit failures do
        not fail the call — the genome is restored regardless.
    """
    if not paths_with_old_values:
        return False, "no paths to roll back"

    with open(genome_path) as f:
        genome_data = yaml.safe_load(f)

    for target_path, old_value in paths_with_old_values:
        if not set_nested_value(genome_data, target_path, old_value):
            return False, f"failed to restore path: {target_path}"

    genome_data["last_mutated"] = datetime.now().isoformat()
    genome_data["mutated_by"] = f"L1-AUTO-ROLLBACK-{cycle_id}"

    with open(genome_path, "w") as f:
        yaml.dump(genome_data, f, default_flow_style=False, allow_unicode=True)

    if commit_to_git:
        commit_msg_lines = [
            f"[L1] AUTO-ROLLBACK cycle {cycle_id}",
            "",
            reason,
            "",
            f"Reverted {len(paths_with_old_values)} mutation(s):",
        ]
        for target_path, _ in paths_with_old_values:
            commit_msg_lines.append(f"  - {target_path}")
        commit_msg_lines += ["", "Co-Authored-By: SPORE L1 Auto-Rollback <noreply@spore.local>"]
        commit_msg = "\n".join(commit_msg_lines)

        try:
            subprocess.run(
                ["git", "add", str(genome_path)],
                check=True,
                capture_output=True,
                cwd=genome_path.parent,
            )
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                check=True,
                capture_output=True,
                text=True,
                cwd=genome_path.parent,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(
                "rollback_git_commit_failed",
                cycle_id=cycle_id,
                stderr=str(getattr(e, "stderr", ""))[:300],
            )
            # Don't fail the rollback — genome is restored on disk.

    logger.warning(
        "cycle_auto_rolled_back",
        cycle_id=cycle_id,
        n_paths=len(paths_with_old_values),
        reason=reason,
    )
    return True, f"rolled back {len(paths_with_old_values)} path(s) for cycle {cycle_id}"


def check_for_rollback(
    mutation: Mutation,
    metrics_before: dict[str, float],
    metrics_after: dict[str, float],
    rollback_threshold: float = 0.15,
) -> tuple[bool, str]:
    """Check if a mutation should be rolled back based on metric degradation.

    Args:
        mutation: The applied mutation
        metrics_before: Metrics before mutation
        metrics_after: Metrics after mutation
        rollback_threshold: Maximum allowed degradation (default 15%)

    Returns:
        Tuple of (should_rollback, reason)
    """
    key_metrics = ["bridge_rate", "avg_composite_score", "curation_rate"]

    for metric in key_metrics:
        before = metrics_before.get(metric, 0)
        after = metrics_after.get(metric, 0)

        if before > 0:
            degradation = (before - after) / before

            if degradation > rollback_threshold:
                return True, (
                    f"Metric {metric} degraded by {degradation:.1%} "
                    f"(threshold: {rollback_threshold:.1%})"
                )

    return False, "Metrics within acceptable range"


def rollback_mutation(
    mutation: Mutation,
    genome_path: Path,
    reason: str,
) -> tuple[bool, str]:
    """Rollback a mutation by restoring the old value.

    Args:
        mutation: The mutation to rollback
        genome_path: Path to genome file
        reason: Reason for rollback

    Returns:
        Tuple of (success, message)
    """
    # Load current genome
    with open(genome_path) as f:
        genome_data = yaml.safe_load(f)

    # Restore old value
    if not set_nested_value(genome_data, mutation.target_path, mutation.old_value):
        return False, f"Failed to restore value at path: {mutation.target_path}"

    # Update metadata
    genome_data["last_mutated"] = datetime.now().isoformat()
    genome_data["mutated_by"] = f"L1-ROLLBACK-{mutation.id}"

    # Write back
    with open(genome_path, "w") as f:
        yaml.dump(genome_data, f, default_flow_style=False, allow_unicode=True)

    # Update mutation status
    mutation.status = MutationStatus.ROLLED_BACK
    mutation.rolled_back_at = datetime.now()
    mutation.rollback_reason = reason

    # Git commit
    try:
        subprocess.run(
            ["git", "add", str(genome_path)],
            check=True,
            capture_output=True,
            cwd=genome_path.parent,
        )

        commit_msg = f"""[L1] ROLLBACK: {mutation.id}

Reverted: {mutation.target_path}
From: {mutation.new_value}
To: {mutation.old_value}

Reason: {reason}

Co-Authored-By: SPORE L1 Executor <noreply@spore.local>
"""

        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            check=True,
            capture_output=True,
            text=True,
            cwd=genome_path.parent,
        )

    except subprocess.CalledProcessError:
        pass  # Git commit failure is not critical

    logger.info(
        "mutation_rolled_back",
        mutation_id=mutation.id,
        reason=reason,
    )

    return True, f"Rolled back: {mutation.target_path} = {mutation.old_value}"
