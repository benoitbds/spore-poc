"""Backfill: re-run the L0 ReviewerAgent on every hypothesis with no verdict.

Used once to populate auto_feedback_json for hypotheses that ran through
the pipeline before the Reviewer output was persisted. Safe to re-run —
it only touches rows where auto_feedback_json IS NULL.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.reviewer import review_hypothesis
from storage import init_database, update_hypothesis_auto_feedback
from storage.database import get_connection, _row_to_hypothesis  # type: ignore[attr-defined]
from logging_config import get_logger, setup_logging

logger = get_logger("backfill_reviewer")


async def main() -> None:
    setup_logging()
    await init_database()

    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM hypotheses
            WHERE auto_feedback_json IS NULL
              AND status != 'rejected'
            ORDER BY generated_at DESC
            """
        )
        rows = await cursor.fetchall()

    print(f"[backfill] found {len(rows)} hypotheses with NULL auto_feedback")
    logger.info("backfill_start", count=len(rows))

    ok = fail = 0
    verdicts: dict[str, int] = {}
    overrides = 0

    for i, row in enumerate(rows, 1):
        hid = row["id"]
        try:
            hypothesis = _row_to_hypothesis(row)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(rows)}] {hid}: load FAILED — {exc}")
            logger.error("backfill_load_failed", hypothesis_id=hid, error=str(exc))
            fail += 1
            continue

        try:
            feedback = await review_hypothesis(hypothesis)
            await update_hypothesis_auto_feedback(hid, feedback)
            verdicts[feedback.verdict] = verdicts.get(feedback.verdict, 0) + 1
            if feedback.override_reason:
                overrides += 1
            override_str = f" [OVERRIDE: {feedback.override_reason}]" if feedback.override_reason else ""
            print(f"[{i}/{len(rows)}] {hid[-18:]}: {feedback.verdict}{override_str}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(rows)}] {hid[-18:]}: FAIL — {exc}")
            logger.error("backfill_review_failed", hypothesis_id=hid, error=str(exc))
            fail += 1

    print("")
    print(f"[backfill] done: {ok} reviewed / {fail} failed")
    print(f"[backfill] verdicts: {verdicts}")
    print(f"[backfill] mechanical overrides applied: {overrides}")
    logger.info(
        "backfill_complete",
        ok=ok,
        fail=fail,
        verdicts=verdicts,
        overrides=overrides,
    )


if __name__ == "__main__":
    asyncio.run(main())
