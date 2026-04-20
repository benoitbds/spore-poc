"""L1 Pipeline - Orchestrates the evolution cycle.

Implements the L1 (Trainers) workflow:
1. Observer: Analyze L0 metrics
2. Strategist: Propose mutations
3. Critic: Validate mutations
4. Executor: Apply validated mutations

Based on Design Doc section 2.3.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import get_settings, get_genome
from models.mutation import (
    Mutation,
    MutationStatus,
    ObservationReport,
    StrategyProposal,
)
from agents.l1_observer import generate_observation_report
from agents.l1_strategist import propose_mutations
from agents.l1_critic import evaluate_proposal
from agents.l1_executor import execute_proposal
from storage import init_database, get_recent_mutations
from logging_config import get_logger, get_token_tracker, reset_token_tracker

logger = get_logger("l1_pipeline")


async def run_l1_cycle(
    limit_runs: int = 5,
    dry_run: bool = False,
    save_reports: bool = True,
) -> dict:
    """Run a complete L1 evolution cycle.

    Args:
        limit_runs: Number of recent runs to analyze
        dry_run: If True, don't actually apply mutations
        save_reports: Whether to save reports to files

    Returns:
        Dictionary with cycle results
    """
    cycle_id = f"L1-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    logger.info("l1_cycle_starting", cycle_id=cycle_id, dry_run=dry_run)

    reset_token_tracker()
    settings = get_settings()

    # Initialize database
    await init_database()

    results = {
        "cycle_id": cycle_id,
        "started_at": datetime.now().isoformat(),
        "dry_run": dry_run,
        "observation_report": None,
        "strategy_proposal": None,
        "mutations_applied": [],
        "mutations_rejected": [],
        "errors": [],
    }

    try:
        # === PHASE 1: OBSERVER ===
        logger.info("phase_1_observer")
        observation_report = await generate_observation_report(limit_runs)
        results["observation_report"] = observation_report.model_dump()

        if save_reports:
            report_path = settings.output_dir / f"observation_{cycle_id}.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w") as f:
                json.dump(observation_report.model_dump(), f, indent=2, default=str)
            logger.info("observation_saved", path=str(report_path))

        # Check if there are any issues to address
        if not observation_report.issues and not observation_report.opportunities:
            logger.info("no_issues_found", message="Nothing to improve")
            results["completed_at"] = datetime.now().isoformat()
            results["status"] = "no_action_needed"
            return results

        # === PHASE 2: STRATEGIST ===
        logger.info("phase_2_strategist")
        strategy_proposal = await propose_mutations(observation_report)
        results["strategy_proposal"] = strategy_proposal.model_dump()

        if save_reports:
            strategy_path = settings.output_dir / f"strategy_{cycle_id}.json"
            with open(strategy_path, "w") as f:
                json.dump(strategy_proposal.model_dump(), f, indent=2, default=str)
            logger.info("strategy_saved", path=str(strategy_path))

        if not strategy_proposal.mutations:
            logger.info("no_mutations_proposed")
            results["completed_at"] = datetime.now().isoformat()
            results["status"] = "no_mutations_proposed"
            return results

        # === PHASE 3: CRITIC ===
        logger.info("phase_3_critic")

        # Load recent mutations from SQLite for conflict detection
        recent_mutations = await get_recent_mutations(n_cycles=5)

        validated_proposal = await evaluate_proposal(
            strategy_proposal,
            recent_mutations,
        )

        validated_count = len(validated_proposal.mutations)
        rejected_count = len(strategy_proposal.mutations) - validated_count

        results["mutations_validated"] = validated_count
        results["mutations_rejected_by_critic"] = rejected_count

        if not validated_proposal.mutations:
            logger.info("all_mutations_rejected")
            results["completed_at"] = datetime.now().isoformat()
            results["status"] = "all_mutations_rejected"
            return results

        # === PHASE 4: EXECUTOR ===
        logger.info("phase_4_executor", dry_run=dry_run)

        applied, failed = await execute_proposal(
            validated_proposal,
            genome_path=settings.genome_path,
            commit_to_git=not dry_run,
            dry_run=dry_run,
            cycle_id=cycle_id,
        )

        results["mutations_applied"] = [m.model_dump() for m in applied]
        results["mutations_failed"] = [
            {"mutation": m.model_dump(), "reason": r}
            for m, r in failed
        ]

        # === COMPLETE ===
        tracker = get_token_tracker()
        token_summary = tracker.summary()

        results["completed_at"] = datetime.now().isoformat()
        results["status"] = "completed"
        results["tokens_used"] = token_summary["total_input_tokens"] + token_summary["total_output_tokens"]
        results["cost_usd"] = token_summary["total_cost_usd"]

        logger.info(
            "l1_cycle_complete",
            cycle_id=cycle_id,
            mutations_applied=len(applied),
            cost_usd=token_summary["total_cost_usd"],
        )

        return results

    except Exception as e:
        logger.error("l1_cycle_error", error=str(e))
        results["errors"].append(str(e))
        results["status"] = "error"
        results["completed_at"] = datetime.now().isoformat()
        raise


async def show_observation_report(limit_runs: int = 5) -> ObservationReport:
    """Generate and display observation report without proposing mutations.

    Useful for reviewing the current state before running a full cycle.

    Args:
        limit_runs: Number of recent runs to analyze

    Returns:
        ObservationReport
    """
    await init_database()
    return await generate_observation_report(limit_runs)


async def list_pending_mutations() -> list[Mutation]:
    """List mutations that are pending application.

    TODO: Implement once mutations table exists.

    Returns:
        List of pending mutations
    """
    # Placeholder - will read from SQLite
    return []


async def get_mutation_history(limit: int = 20) -> list[Mutation]:
    """Get recent mutation history.

    TODO: Implement once mutations table exists.

    Args:
        limit: Maximum mutations to return

    Returns:
        List of recent mutations
    """
    # Placeholder - will read from SQLite
    return []
