"""Retry failed custom collision requests.

Scans custom_requests with status='failed' created in the last 48 h
and re-runs the pipeline for each. Designed to be called manually or
via a cron job (e.g. every 6 h).

Usage:
    .venv/bin/python scripts/retry_failed_customs.py [--max N] [--dry-run]

Options:
    --max N       Maximum number of retries per invocation (default: 5).
    --dry-run     List eligible requests without running them.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is on sys.path so imports work when run as a script.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import get_connection, init_database  # noqa: E402
from logging_config import get_logger  # noqa: E402

logger = get_logger("retry_failed_customs")

MAX_RETRIES = 3


async def get_eligible_requests(hours: int = 48) -> list[dict]:
    """Return failed custom_requests created in the last ``hours`` hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT id, domain_a, domain_b, user_id, status,
                   retry_count, error_message, created_at
            FROM custom_requests
            WHERE status = 'failed'
              AND created_at >= ?
              AND COALESCE(retry_count, 0) < ?
            ORDER BY created_at DESC
            """,
            (cutoff, MAX_RETRIES),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def bump_retry_count(request_id: str) -> None:
    """Increment retry_count on a custom_request."""
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE custom_requests SET retry_count = COALESCE(retry_count, 0) + 1 WHERE id = ?",
            (request_id,),
        )
        await conn.commit()


async def run_one(request_id: str) -> bool:
    """Retry a single custom request. Returns True on success."""
    from api.custom_runner import run_custom_request

    await bump_retry_count(request_id)

    try:
        result = await run_custom_request(request_id)
        logger.info(
            "retry_success",
            request_id=request_id,
            brief_id=result.get("brief_id"),
        )
        return True
    except Exception as exc:
        logger.warning(
            "retry_failed",
            request_id=request_id,
            error=str(exc),
        )
        return False


async def main(max_runs: int, dry_run: bool) -> None:
    await init_database()
    eligible = await get_eligible_requests()

    if not eligible:
        print("No eligible failed custom requests found.")
        return

    print(f"Found {len(eligible)} eligible request(s):")
    for r in eligible:
        retries = r.get("retry_count") or 0
        print(
            f"  {r['id']}  {r['domain_a']} × {r['domain_b']}  "
            f"retry {retries}/{MAX_RETRIES}  "
            f"error: {(r.get('error_message') or '?')[:60]}"
        )

    if dry_run:
        print("\n--dry-run: no action taken.")
        return

    to_run = eligible[:max_runs]
    print(f"\nRetrying {len(to_run)} request(s)…")

    ok = 0
    for r in to_run:
        print(f"\n→ {r['id']} ({r['domain_a']} × {r['domain_b']})")
        success = await run_one(r["id"])
        if success:
            ok += 1
            print(f"  ✅ success")
        else:
            print(f"  ❌ still failed")

    print(f"\nDone: {ok}/{len(to_run)} succeeded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retry failed custom collisions")
    parser.add_argument("--max", type=int, default=5, help="Max retries per run")
    parser.add_argument("--dry-run", action="store_true", help="List only, don't run")
    args = parser.parse_args()
    asyncio.run(main(args.max, args.dry_run))
