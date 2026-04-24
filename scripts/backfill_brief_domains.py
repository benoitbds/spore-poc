"""Backfill ``briefs.sharpened_data.domains`` from the sidecar JSON files.

Why this exists
---------------
For the first 40-ish briefs (both pipeline output and stubs), the
``domains`` field was only ever written to the ``outputs/briefs/<id>.json``
sidecar — never mirrored into SQLite. The /discoveries cards read
``sharpened_data.domains`` via ``briefRowToBrief`` and were therefore
falling back to ``[]``, rendering "× —" on every card.

The pipeline writer now stamps domains into ``sharpened_data`` at save
time (see ``graph/post_fire_pipeline.py`` and ``api/custom_runner.py``).
This one-shot script repairs the rows that predate that change by
reading each sidecar JSON's top-level ``domains`` array and merging it
into the DB's ``sharpened_data`` column.

Idempotent: rows that already carry ``sharpened_data.domains`` are
skipped. Rows whose sidecar file is missing or malformed are logged and
skipped without aborting the run.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m scripts.backfill_brief_domains
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging_config import get_logger, setup_logging
from storage import init_database
from storage.database import get_connection

logger = get_logger("scripts.backfill_brief_domains")


async def backfill() -> dict[str, int]:
    """Walk briefs, update sharpened_data.domains from the sidecar JSON.

    Returns counters: ``{scanned, updated, already_had, sidecar_missing,
    sidecar_unreadable, no_domains_in_sidecar}``.
    """
    counters = {
        "scanned": 0,
        "updated": 0,
        "already_had": 0,
        "sidecar_missing": 0,
        "sidecar_unreadable": 0,
        "no_domains_in_sidecar": 0,
    }

    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT id, sharpened_data, brief_json_path
               FROM briefs
               ORDER BY created_at DESC"""
        )
        rows = await cursor.fetchall()

        for row in rows:
            counters["scanned"] += 1
            brief_id = row["id"]
            existing_raw = row["sharpened_data"]
            json_path = row["brief_json_path"]

            existing: dict = {}
            if existing_raw:
                try:
                    existing = json.loads(existing_raw) or {}
                except json.JSONDecodeError:
                    existing = {}

            if (
                isinstance(existing.get("domains"), list)
                and existing["domains"]
            ):
                counters["already_had"] += 1
                continue

            if not json_path:
                counters["sidecar_missing"] += 1
                logger.warning("sidecar_path_missing", brief_id=brief_id)
                continue

            sidecar = Path(json_path)
            if not sidecar.exists():
                counters["sidecar_missing"] += 1
                logger.warning(
                    "sidecar_file_missing",
                    brief_id=brief_id,
                    path=str(sidecar),
                )
                continue

            try:
                sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                counters["sidecar_unreadable"] += 1
                logger.warning(
                    "sidecar_unreadable",
                    brief_id=brief_id,
                    path=str(sidecar),
                    error=str(exc),
                )
                continue

            domains = sidecar_payload.get("domains")
            if not isinstance(domains, list) or not domains:
                counters["no_domains_in_sidecar"] += 1
                logger.warning(
                    "sidecar_has_no_domains",
                    brief_id=brief_id,
                    path=str(sidecar),
                )
                continue

            merged = {**existing, "domains": [str(d) for d in domains]}
            await conn.execute(
                "UPDATE briefs SET sharpened_data = ? WHERE id = ?",
                (json.dumps(merged), brief_id),
            )
            counters["updated"] += 1
            logger.info(
                "brief_domains_backfilled",
                brief_id=brief_id,
                domains=merged["domains"],
            )

        await conn.commit()

    return counters


async def main() -> int:
    setup_logging()
    await init_database()
    counters = await backfill()
    logger.info("backfill_complete", **counters)
    print()
    print("=== Backfill summary ===")
    for k, v in counters.items():
        print(f"  {k:28} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
