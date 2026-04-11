"""Progress tracking for SPORE runs.

Writes real-time progress to .spore_progress.json for dashboard monitoring.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field, asdict

# Progress file location
PROGRESS_FILE = Path(__file__).parent / ".spore_progress.json"


@dataclass
class CurrentCollision:
    """Current collision being processed."""
    domain_a: str = ""
    domain_b: str = ""
    distance: float = 0.0
    stage: str = "gate"  # gate, synthesis, critics, curator, impact


@dataclass
class RecentHypothesis:
    """Recent hypothesis for the feed."""
    id: str = ""
    one_liner: str = ""
    score: float = 0.0
    domains: str = ""


@dataclass
class RunProgress:
    """Full progress state for a run."""
    run_id: str = ""
    status: str = "starting"  # starting, running, completed, failed
    started_at: str = ""
    domain: str = "all_science"
    total_collisions: int = 0
    processed: int = 0
    bridges_found: int = 0
    no_bridge: int = 0
    curated: int = 0
    current_collision: Optional[CurrentCollision] = None
    cost_so_far: float = 0.0
    recent_hypotheses: list[RecentHypothesis] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    estimated_remaining_seconds: float = 0.0
    avg_score: float = 0.0
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        data = {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "domain": self.domain,
            "total_collisions": self.total_collisions,
            "processed": self.processed,
            "bridges_found": self.bridges_found,
            "no_bridge": self.no_bridge,
            "curated": self.curated,
            "cost_so_far": self.cost_so_far,
            "elapsed_seconds": self.elapsed_seconds,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "avg_score": self.avg_score,
        }

        if self.current_collision:
            data["current_collision"] = asdict(self.current_collision)
        else:
            data["current_collision"] = None

        data["recent_hypotheses"] = [asdict(h) for h in self.recent_hypotheses]

        if self.error_message:
            data["error_message"] = self.error_message

        return data


class ProgressTracker:
    """Tracks and persists run progress."""

    def __init__(self):
        self._progress: Optional[RunProgress] = None
        self._start_time: Optional[datetime] = None
        self._all_hypotheses: list[RecentHypothesis] = []

    def start_run(
        self,
        run_id: str,
        total_collisions: int,
        domain: str = "all_science",
    ) -> None:
        """Initialize progress for a new run."""
        self._start_time = datetime.now()
        self._all_hypotheses = []

        self._progress = RunProgress(
            run_id=run_id,
            status="running",
            started_at=self._start_time.isoformat(),
            domain=domain,
            total_collisions=total_collisions,
        )
        self._write()

    def set_current_collision(
        self,
        domain_a: str,
        domain_b: str,
        distance: float,
        stage: str = "gate",
    ) -> None:
        """Set the current collision being processed."""
        if not self._progress:
            return

        self._progress.current_collision = CurrentCollision(
            domain_a=domain_a,
            domain_b=domain_b,
            distance=distance,
            stage=stage,
        )
        self._update_elapsed()
        self._write()

    def update_stage(self, stage: str) -> None:
        """Update the current stage of processing."""
        if not self._progress or not self._progress.current_collision:
            return

        self._progress.current_collision.stage = stage
        self._update_elapsed()
        self._write()

    def collision_processed(
        self,
        bridge_found: bool,
        hypothesis: Optional[dict[str, Any]] = None,
        cost_delta: float = 0.0,
    ) -> None:
        """Record that a collision has been processed."""
        if not self._progress:
            return

        self._progress.processed += 1
        self._progress.cost_so_far += cost_delta

        if bridge_found:
            self._progress.bridges_found += 1

            # Add to recent hypotheses
            if hypothesis:
                recent = RecentHypothesis(
                    id=hypothesis.get("id", ""),
                    one_liner=hypothesis.get("one_liner", hypothesis.get("bridge_summary", ""))[:100],
                    score=hypothesis.get("score", 0.0),
                    domains=hypothesis.get("domains", ""),
                )
                self._all_hypotheses.append(recent)

                # Keep only top 5 by score
                self._all_hypotheses.sort(key=lambda h: h.score, reverse=True)
                self._progress.recent_hypotheses = self._all_hypotheses[:5]

                # Update average score
                if self._all_hypotheses:
                    self._progress.avg_score = sum(h.score for h in self._all_hypotheses) / len(self._all_hypotheses)
        else:
            self._progress.no_bridge += 1

        self._update_elapsed()
        self._estimate_remaining()
        self._write()

    def hypothesis_scored(self, hypothesis_id: str, score: float) -> None:
        """Update a hypothesis score after critics."""
        if not self._progress:
            return

        # Update score in recent hypotheses
        for h in self._all_hypotheses:
            if h.id == hypothesis_id:
                h.score = score
                break

        # Re-sort and update
        self._all_hypotheses.sort(key=lambda h: h.score, reverse=True)
        self._progress.recent_hypotheses = self._all_hypotheses[:5]

        # Update average score
        if self._all_hypotheses:
            self._progress.avg_score = sum(h.score for h in self._all_hypotheses) / len(self._all_hypotheses)

        self._write()

    def hypothesis_curated(self, count: int = 1) -> None:
        """Record that hypotheses have been curated."""
        if not self._progress:
            return

        self._progress.curated += count
        self._write()

    def update_cost(self, total_cost: float) -> None:
        """Update the total cost."""
        if not self._progress:
            return

        self._progress.cost_so_far = total_cost
        self._write()

    def complete_run(self, final_cost: float = 0.0) -> None:
        """Mark the run as complete."""
        if not self._progress:
            return

        self._progress.status = "completed"
        self._progress.current_collision = None
        if final_cost > 0:
            self._progress.cost_so_far = final_cost
        self._progress.estimated_remaining_seconds = 0
        self._update_elapsed()
        self._write()

    def fail_run(self, error: str) -> None:
        """Mark the run as failed."""
        if not self._progress:
            return

        self._progress.status = "failed"
        self._progress.error_message = error
        self._progress.current_collision = None
        self._update_elapsed()
        self._write()

    def _update_elapsed(self) -> None:
        """Update elapsed time."""
        if self._start_time:
            elapsed = (datetime.now() - self._start_time).total_seconds()
            self._progress.elapsed_seconds = elapsed

    def _estimate_remaining(self) -> None:
        """Estimate remaining time based on current progress."""
        if not self._progress or self._progress.processed == 0:
            return

        elapsed = self._progress.elapsed_seconds
        processed = self._progress.processed
        remaining = self._progress.total_collisions - processed

        if processed > 0:
            time_per_collision = elapsed / processed
            self._progress.estimated_remaining_seconds = time_per_collision * remaining

    def _write(self) -> None:
        """Write progress to file."""
        if not self._progress:
            return

        try:
            with open(PROGRESS_FILE, "w") as f:
                json.dump(self._progress.to_dict(), f, indent=2)
        except IOError:
            pass  # Ignore write errors


# Global tracker instance
_tracker: Optional[ProgressTracker] = None


def get_progress_tracker() -> ProgressTracker:
    """Get the global progress tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ProgressTracker()
    return _tracker


def reset_progress_tracker() -> None:
    """Reset the global progress tracker."""
    global _tracker
    _tracker = None


def get_current_progress() -> Optional[dict[str, Any]]:
    """Read current progress from file (for dashboard)."""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def clear_progress() -> None:
    """Clear the progress file."""
    if PROGRESS_FILE.exists():
        try:
            PROGRESS_FILE.unlink()
        except IOError:
            pass
