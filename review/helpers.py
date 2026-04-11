"""Helper functions for SPORE Streamlit dashboard."""

import asyncio
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

# Status file for tracking run state
STATUS_FILE = Path(__file__).parent.parent / ".spore_status.json"


def run_async(coro):
    """Run an async coroutine in Streamlit."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def get_system_status() -> dict[str, Any]:
    """Get current system status from status file."""
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"status": "idle", "run_id": None, "started_at": None}


def set_system_status(
    status: str,
    run_id: str | None = None,
    error: str | None = None,
    **extra: Any,
) -> None:
    """Update system status file."""
    data = {
        "status": status,
        "run_id": run_id,
        "updated_at": datetime.now().isoformat(),
        **extra,
    }
    if error:
        data["error"] = error

    with open(STATUS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def launch_run_background(
    n_collisions: int,
    domain: str,
    cross_ratio: float,
) -> str:
    """Launch a SPORE run in background subprocess.

    Returns the run_id.
    """
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # Update status to running
    set_system_status(
        status="running",
        run_id=run_id,
        n_collisions=n_collisions,
        domain=domain,
        started_at=datetime.now().isoformat(),
    )

    # Build command
    project_root = Path(__file__).parent.parent
    cmd = [
        "python", "-m", "cli", "run",
        "--collisions", str(n_collisions),
        "--domain", domain,
    ]

    # Launch in background
    subprocess.Popen(
        cmd,
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    return run_id


def format_cost(cost: float) -> str:
    """Format cost in USD."""
    if cost < 0.01:
        return f"${cost:.4f}"
    elif cost < 1:
        return f"${cost:.3f}"
    else:
        return f"${cost:.2f}"


def format_rate(rate: float) -> str:
    """Format a rate as percentage."""
    return f"{rate * 100:.1f}%"


def get_status_emoji(status: str) -> str:
    """Get emoji for system status."""
    return {
        "idle": "🟢",
        "running": "🔄",
        "error": "🔴",
        "evolving": "🧬",
    }.get(status, "⚪")


def get_feedback_emoji(feedback: str | None) -> str:
    """Get emoji for human feedback."""
    if feedback is None:
        return "⏳"
    return {
        "trash": "🗑️",
        "interesting": "🤔",
        "want_to_test": "🔥",
    }.get(feedback, "❓")
