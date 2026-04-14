"""Re-run the meta-reviewer only, at iter 2, on every brief still stuck in
revise_and_resubmit. Reuses the 5 stored individual reviews — does NOT
re-run them. Forces a binary publish/reject via the Python thresholds.

Updates in place:
- briefs.panel_consensus_score (Python-computed, weighted by confidence)
- briefs.panel_verdict (iter-2 binary)
- briefs.revision_count = 1
- briefs.panel_data JSON (new meta_review inserted)
- outputs/briefs/<brief_id>.json (on-disk copy, driving the public site)
"""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.multi_reviewer_panel import (  # noqa: E402
    run_meta_reviewer,
    compute_consensus_score,
    threshold_verdict,
)
from agents.hypothesis_sharpening import SharpeningOutput  # noqa: E402
from config import get_settings  # noqa: E402
from logging_config import get_logger, setup_logging  # noqa: E402

logger = get_logger("reprocess_briefs_iter2")

ITER_STATE_REVISION_COUNT = 1  # node_multi_reviewer_panel would compute iter=2 from this


def _load_sharpened(row: sqlite3.Row) -> SharpeningOutput:
    """Rehydrate a SharpeningOutput from the stored sharpened_data JSON."""
    raw = json.loads(row["sharpened_data"]) if row["sharpened_data"] else {}
    # SharpeningOutput is a TypedDict — we only need a dict shaped like it.
    # Fill the keys the meta-reviewer prompt uses.
    return SharpeningOutput(
        title=raw.get("title", "Untitled"),
        formal_statement=raw.get("formal_statement", ""),
        independent_variables=raw.get("independent_variables", []),
        dependent_variables=raw.get("dependent_variables", []),
        proposed_mechanism=raw.get("proposed_mechanism", {}),
        falsifiable_predictions=raw.get("falsifiable_predictions", []),
        boundary_conditions=raw.get("boundary_conditions", []),
        theoretical_framework=raw.get("theoretical_framework", ""),
    )


async def reprocess_one(brief_id: str, row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
    """Re-run meta-reviewer at iter 2 on a single brief. Persist results."""
    panel_raw = json.loads(row["panel_data"]) if row["panel_data"] else {}
    reviews = panel_raw.get("reviews", [])

    if not reviews:
        logger.error("reprocess_no_reviews", brief_id=brief_id)
        return {"brief_id": brief_id, "ok": False, "reason": "no reviews in panel_data"}

    sharpened = _load_sharpened(row)

    # Iteration the meta-reviewer sees (1-based). revision_count=1 in state
    # would yield iteration=2 in node_multi_reviewer_panel; we bypass the node
    # and pass iteration=2 directly.
    iteration = 2

    # Pre-compute what the Python-side verdict WILL be (the node also computes
    # it, but this lets us log an explicit before/after line).
    py_score = compute_consensus_score(reviews)
    py_verdict = threshold_verdict(py_score, iteration)

    try:
        new_meta = await run_meta_reviewer(
            reviews=reviews,
            sharpened=sharpened,
            iteration=iteration,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("reprocess_meta_reviewer_failed", brief_id=brief_id, error=str(exc))
        return {"brief_id": brief_id, "ok": False, "reason": str(exc)}

    # Persist on-disk JSON (drives the public Next.js site) — same inode as
    # spore-web/data/briefs/*.json, so a single write covers both surfaces.
    settings = get_settings()
    json_path = Path(row["brief_json_path"]) if row["brief_json_path"] else (
        settings.output_dir / "briefs" / f"{brief_id}.json"
    )
    on_disk_updated = False
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            data.setdefault("panel", {})["meta_review"] = dict(new_meta)
            json_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            on_disk_updated = True
        except Exception as exc:  # noqa: BLE001
            logger.error("reprocess_json_update_failed", brief_id=brief_id, error=str(exc))

    # Update DB — inline, single UPDATE to keep semantics atomic.
    new_panel_data = {"reviews": reviews, "meta_review": dict(new_meta)}
    conn.execute(
        """
        UPDATE briefs
        SET panel_consensus_score = ?,
            panel_verdict = ?,
            revision_count = ?,
            panel_data = ?
        WHERE id = ?
        """,
        (
            float(new_meta["consensus_score"]),
            str(new_meta["verdict"]),
            ITER_STATE_REVISION_COUNT,
            json.dumps(new_panel_data, ensure_ascii=False),
            brief_id,
        ),
    )
    conn.commit()

    logger.info(
        "brief_reprocessed",
        brief_id=brief_id,
        old_verdict=row["panel_verdict"],
        new_verdict=new_meta["verdict"],
        py_pre_score=py_score,
        py_pre_verdict=py_verdict,
        final_score=new_meta["consensus_score"],
        on_disk_updated=on_disk_updated,
    )
    return {
        "brief_id": brief_id,
        "ok": True,
        "old_verdict": row["panel_verdict"],
        "old_score": row["panel_consensus_score"],
        "new_verdict": new_meta["verdict"],
        "new_score": new_meta["consensus_score"],
        "llm_verdict": new_meta.get("llm_verdict"),
        "llm_score": new_meta.get("llm_consensus_score"),
    }


async def main() -> None:
    setup_logging()
    db_path = get_settings().db_path

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT *
        FROM briefs
        WHERE panel_verdict = 'revise_and_resubmit'
        ORDER BY panel_consensus_score DESC
        """
    ).fetchall()

    print(f"[reprocess] found {len(rows)} briefs in revise_and_resubmit")
    logger.info("reprocess_start", count=len(rows))

    results = []
    for i, row in enumerate(rows, 1):
        bid = row["id"]
        print(f"[{i}/{len(rows)}] {bid} — reprocessing...")
        res = await reprocess_one(bid, row, conn)
        results.append(res)
        if res["ok"]:
            marker = "→"
            print(
                f"    {res['old_score']:.2f} {res['old_verdict']} {marker} "
                f"{res['new_score']:.2f} {res['new_verdict']} "
                f"(LLM said {res.get('llm_verdict', '?')} @ {res.get('llm_score', 0):.2f})"
            )
        else:
            print(f"    FAIL — {res.get('reason', 'unknown')}")

    publish = sum(1 for r in results if r.get("new_verdict") == "publish_brief")
    reject = sum(1 for r in results if r.get("new_verdict") == "reject")
    failed = sum(1 for r in results if not r.get("ok"))

    print()
    print(f"[reprocess] summary: publish={publish}  reject={reject}  failed={failed}")
    conn.close()
    logger.info("reprocess_complete", publish=publish, reject=reject, failed=failed)


if __name__ == "__main__":
    asyncio.run(main())
