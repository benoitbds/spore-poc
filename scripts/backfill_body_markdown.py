"""One-shot backfill: populate ``briefs.body_markdown`` from the .md files.

Phase 2 of the SSG-to-DB refactor adds a ``body_markdown`` column to the
``briefs`` table. New briefs are written with the column populated; this
script handles the rows that pre-date the migration.

Behaviour:
  * Selects every ``briefs`` row where ``body_markdown IS NULL``.
  * For each, reads the markdown from ``brief_md_path`` if set, else
    falls back to ``outputs/briefs/{id}.md`` by convention.
  * UPDATE briefs SET body_markdown = ? WHERE id = ?.
  * Missing files → log warning + skip (no crash).
  * Idempotent: re-runs only touch rows still NULL.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m scripts.backfill_body_markdown
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings
from logging_config import get_logger, setup_logging
from storage import init_database
from storage.database import get_connection

logger = get_logger("scripts.backfill_body_markdown")


def _resolve_md_path(brief_id: str, brief_md_path: Optional[str]) -> Optional[Path]:
    """Pick the on-disk path for a brief's markdown.

    Prefer the explicit ``brief_md_path`` column when present; fall back
    to ``{output_dir}/briefs/{brief_id}.md``. Returns ``None`` if no
    candidate exists on disk.
    """
    candidates: list[Path] = []
    if brief_md_path:
        candidates.append(Path(brief_md_path))
    settings = get_settings()
    candidates.append(Path(settings.output_dir) / "briefs" / f"{brief_id}.md")
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


async def backfill() -> dict[str, int]:
    """Run the backfill. Returns a counters dict for the summary log."""
    await init_database()

    counters = {"considered": 0, "updated": 0, "skipped_missing_file": 0,
                "skipped_empty": 0, "errors": 0}

    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT id, brief_md_path FROM briefs "
            "WHERE body_markdown IS NULL ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()

        for row in rows:
            counters["considered"] += 1
            brief_id = row["id"]
            md_path = _resolve_md_path(brief_id, row["brief_md_path"])
            if md_path is None:
                logger.warning(
                    "backfill_body_markdown_missing_file",
                    brief_id=brief_id,
                    candidate_brief_md_path=row["brief_md_path"],
                )
                counters["skipped_missing_file"] += 1
                continue

            try:
                content = md_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.error(
                    "backfill_body_markdown_read_failed",
                    brief_id=brief_id, path=str(md_path), error=str(exc),
                )
                counters["errors"] += 1
                continue

            if not content.strip():
                logger.warning(
                    "backfill_body_markdown_empty_file",
                    brief_id=brief_id, path=str(md_path),
                )
                counters["skipped_empty"] += 1
                continue

            await conn.execute(
                "UPDATE briefs SET body_markdown = ? WHERE id = ?",
                (content, brief_id),
            )
            logger.info(
                "backfill_body_markdown",
                brief_id=brief_id, bytes=len(content), path=str(md_path),
            )
            counters["updated"] += 1

        await conn.commit()

    return counters


def main() -> int:
    setup_logging()
    counters = asyncio.run(backfill())
    print()
    print("=== Backfill summary ===")
    for k, v in counters.items():
        print(f"  {k}: {v}")
    if counters["errors"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
