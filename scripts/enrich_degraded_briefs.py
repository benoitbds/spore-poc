"""Re-run literature grounding on briefs flagged low_evidence=1.

When Semantic Scholar was down during a custom run, the brief was
produced without literature grounding (novelty_assessment=unavailable,
evidence_base=[], counter_evidence=[]). This script:

  1. Finds briefs with low_evidence=1.
  2. For each, runs the Literature Grounding Agent with the stored
     hypothesis/domains.
  3. Updates the brief's grounding_data in the DB.
  4. Clears low_evidence flag.
  5. Optionally sends a "brief enriched" email to the customer.

Usage:
    .venv/bin/python scripts/enrich_degraded_briefs.py [--dry-run] [--max N]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.literature_grounding import (  # noqa: E402
    literature_grounding_agent,
    GroundingInput,
)
from storage.database import get_connection, init_database, update_brief  # noqa: E402
from logging_config import get_logger  # noqa: E402

logger = get_logger("enrich_degraded_briefs")


async def get_degraded_briefs() -> list[dict]:
    """Return briefs with low_evidence=1."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT b.id AS brief_id, b.hypothesis_id,
                   b.grounding_data, b.sharpened_data,
                   cr.domain_a, cr.domain_b, cr.user_id
            FROM briefs b
            LEFT JOIN custom_requests cr ON cr.brief_id = b.id
            WHERE b.low_evidence = 1
            ORDER BY b.created_at DESC
            """
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def enrich_one(brief: dict) -> bool:
    """Re-run literature grounding for a single degraded brief.

    Returns True on success.
    """
    brief_id = brief["brief_id"]
    domain_a = brief.get("domain_a") or ""
    domain_b = brief.get("domain_b") or ""
    domains = [d for d in [domain_a, domain_b] if d]

    # Extract hypothesis text from sharpened_data
    sharpened = brief.get("sharpened_data")
    if isinstance(sharpened, str):
        sharpened = json.loads(sharpened)
    hypothesis = ""
    if sharpened:
        hypothesis = (
            sharpened.get("formal_statement")
            or sharpened.get("title")
            or ""
        )

    if not hypothesis or not domains:
        logger.warning(
            "enrich_skip_missing_data",
            brief_id=brief_id,
            has_hypothesis=bool(hypothesis),
            has_domains=bool(domains),
        )
        return False

    logger.info("enrich_starting", brief_id=brief_id, domains=domains)

    try:
        grounding_input = GroundingInput(
            hypothesis=hypothesis,
            domains=domains,
            mechanisms="",
            keywords=[],
            gap_manifest={},
        )
        output = await literature_grounding_agent(grounding_input)

        # Check if we actually got papers this time
        evidence_count = len(output.get("evidence_base", []))
        if evidence_count == 0:
            logger.warning(
                "enrich_still_empty",
                brief_id=brief_id,
                detail="SS still returning 0 papers — try again later",
            )
            return False

        # Update the brief in DB
        grounding_json = json.dumps(dict(output), ensure_ascii=False)
        novelty = output.get("novelty_assessment", {})

        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE briefs
                SET grounding_data = ?,
                    novelty_score = ?,
                    novelty_verdict = ?,
                    evidence_count = ?,
                    counter_evidence_count = ?,
                    low_evidence = 0
                WHERE id = ?
                """,
                (
                    grounding_json,
                    novelty.get("score"),
                    novelty.get("verdict"),
                    evidence_count,
                    len(output.get("counter_evidence", [])),
                    brief_id,
                ),
            )
            await conn.commit()

        logger.info(
            "enrich_success",
            brief_id=brief_id,
            evidence_count=evidence_count,
            novelty_verdict=novelty.get("verdict"),
        )
        return True

    except Exception as exc:
        logger.error("enrich_failed", brief_id=brief_id, error=str(exc))
        return False


async def main(max_briefs: int, dry_run: bool) -> None:
    await init_database()
    degraded = await get_degraded_briefs()

    if not degraded:
        print("No degraded briefs found (low_evidence=1).")
        return

    print(f"Found {len(degraded)} degraded brief(s):")
    for b in degraded:
        print(
            f"  {b['brief_id']}  "
            f"{b.get('domain_a', '?')} × {b.get('domain_b', '?')}"
        )

    if dry_run:
        print("\n--dry-run: no action taken.")
        return

    to_process = degraded[:max_briefs]
    print(f"\nEnriching {len(to_process)} brief(s)…")

    ok = 0
    for b in to_process:
        success = await enrich_one(b)
        status = "enriched" if success else "still degraded"
        print(f"  {b['brief_id']}: {status}")
        if success:
            ok += 1

    print(f"\nDone: {ok}/{len(to_process)} enriched.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-run literature grounding on degraded briefs"
    )
    parser.add_argument("--max", type=int, default=10, help="Max briefs to process")
    parser.add_argument("--dry-run", action="store_true", help="List only")
    args = parser.parse_args()
    asyncio.run(main(args.max, args.dry_run))
