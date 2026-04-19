"""L1 Executor Agent - Applies validated mutations to the genome.

Part of Team L1 (Trainers) - Design Doc section 2.3.

The Executor:
1. Applies validated mutations to l0_genome.yaml
2. Commits changes to git with descriptive message
3. Logs mutations to SQLite (when table exists)
4. Supports automatic rollback if metrics degrade
"""

import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from config import get_settings, get_genome
from models.mutation import (
    Mutation,
    MutationStatus,
    StrategyProposal,
)
from logging_config import get_logger

logger = get_logger("l1_executor")


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
) -> tuple[list[Mutation], list[tuple[Mutation, str]]]:
    """Execute all validated mutations in a proposal.

    Args:
        proposal: Proposal with validated mutations
        genome_path: Path to genome file (default: from settings)
        commit_to_git: Whether to commit changes to git
        dry_run: If True, don't actually apply changes

    Returns:
        Tuple of (applied_mutations, failed_mutations_with_reasons)
    """
    if genome_path is None:
        settings = get_settings()
        genome_path = settings.genome_path

    active_locks = _load_active_locks(genome_path)

    logger.info(
        "executor_starting",
        mutations_to_apply=len(proposal.mutations),
        dry_run=dry_run,
        active_locks=len(active_locks),
    )

    applied = []
    failed = []

    for mutation in proposal.mutations:
        if mutation.status != MutationStatus.VALIDATED:
            failed.append((mutation, "Not validated"))
            continue

        if mutation.target_path in active_locks:
            until, reason = active_locks[mutation.target_path]
            block_msg = f"locked until {until.isoformat()}: {reason}"
            logger.warning(
                "mutation_blocked_by_lock",
                mutation_id=mutation.id,
                path=mutation.target_path,
                locked_until=until.isoformat(),
                reason=reason,
            )
            mutation.status = MutationStatus.REJECTED
            failed.append((mutation, f"Mutation blocked: {block_msg}"))
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
            continue

        # Apply the mutation
        success, message = apply_mutation_to_genome(mutation, genome_path)

        if not success:
            logger.error(
                "mutation_apply_failed",
                mutation_id=mutation.id,
                error=message,
            )
            failed.append((mutation, message))
            continue

        # Commit to git
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
