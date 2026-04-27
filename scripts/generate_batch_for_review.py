"""One-shot batch generator for external reviewer evaluation.

Fires ``COUNT`` custom-collision requests on a fixed ``domain_a`` in
surprise mode, bypassing the 1-per-user quota enforced by the public
/api/custom/free endpoint (this is an admin batch, not a real user).
Runs the existing ``run_custom_request`` pipeline end-to-end on each
request, then emits a console table + a Markdown report under
``outputs/batches/`` ranking the briefs by panel consensus score, with
the top-2 publishable ones starred and ready to share.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m scripts.generate_batch_for_review

Tweak ``DOMAIN_A``, ``COUNT``, ``PARALLEL`` at the top of the file
before each run. Idempotent on the system user — reusing an existing
row rather than creating a duplicate.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings
from logging_config import get_logger, setup_logging
from storage import init_database
from storage.database import get_connection
from api.db import create_custom_request, get_or_create_user

logger = get_logger("scripts.generate_batch_for_review")


# ── Configuration ────────────────────────────────────────────────────

DOMAIN_A: str = "Skeletal Muscle"
COUNT: int = 5
SYSTEM_USER_EMAIL: str = "system-batch@spore-research.com"
PARALLEL: int = 1  # sequential by default — don't saturate DeepSeek
TIMEOUT_PER_REQUEST_SECONDS: int = 600


# ── Steps ────────────────────────────────────────────────────────────


async def provision_requests(user_id: str) -> list[str]:
    """Insert ``COUNT`` custom_requests in surprise mode, bypassing quota.

    The public endpoints /api/custom/free and /api/custom/free-signup
    enforce user_has_any_custom_request() → 409 Conflict on the second
    attempt. We insert directly via ``create_custom_request`` instead
    because this is an admin batch run on a system user — there is no
    paying-user quota to respect here.

    Args:
        user_id: System user owning the batch.

    Returns:
        List of fresh ``cus_…`` request ids, ordered by creation.
    """
    request_ids: list[str] = []
    for index in range(COUNT):
        rid = await create_custom_request(
            user_id=user_id,
            domain_a=DOMAIN_A,
            domain_b=None,        # surprise mode — runner picks the partner
            purchase_id=None,     # free admin batch
            status="pending",
            mode="surprise",
        )
        logger.info(
            "batch_request_created",
            index=index,
            request_id=rid,
            domain_a=DOMAIN_A,
            mode="surprise",
        )
        request_ids.append(rid)
    return request_ids


async def run_one(request_id: str, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    """Execute one custom_request end-to-end with a per-request timeout.

    The ``run_custom_request`` import is lazy because pulling it in loads
    sentence_transformers + the full post-fire graph (≈ 5s cold start).
    Callers in parallel mode share a semaphore to cap concurrency.
    """
    # Lazy — the cascade (sentence_transformers + LangGraph) is heavy.
    from api.custom_runner import run_custom_request

    async with semaphore:
        logger.info("pipeline_started", request_id=request_id)
        try:
            result = await asyncio.wait_for(
                run_custom_request(request_id),
                timeout=TIMEOUT_PER_REQUEST_SECONDS,
            )
            logger.info(
                "pipeline_complete",
                request_id=request_id,
                brief_id=result.get("brief_id"),
                is_stub=result.get("is_stub", False),
                low_confidence=result.get("low_confidence"),
            )
            return {"request_id": request_id, "ok": True, **result}
        except asyncio.TimeoutError:
            logger.error(
                "pipeline_timeout",
                request_id=request_id,
                timeout_s=TIMEOUT_PER_REQUEST_SECONDS,
            )
            return {
                "request_id": request_id, "ok": False,
                "error": f"timeout after {TIMEOUT_PER_REQUEST_SECONDS}s",
            }
        except Exception as exc:  # noqa: BLE001 — never abort the batch
            logger.error("pipeline_failed", request_id=request_id, error=str(exc))
            return {"request_id": request_id, "ok": False, "error": str(exc)}


async def enrich_with_db_state(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Augment each run summary with the latest DB state.

    Reads ``custom_requests`` + ``briefs`` so the final row carries the
    resolved ``domain_b`` (surprise pick), current status, panel score,
    novelty score and stub flags — fields the pipeline dict alone
    doesn't surface.
    """
    async with get_connection() as conn:
        for s in summaries:
            cursor = await conn.execute(
                """SELECT status, domain_a, domain_b, brief_id, hypothesis_id,
                          error_message, completed_at
                   FROM custom_requests WHERE id = ?""",
                (s["request_id"],),
            )
            cr = await cursor.fetchone()
            if cr:
                s["status"] = cr["status"]
                s["domain_a"] = cr["domain_a"]
                s["domain_b"] = cr["domain_b"]
                # Prefer the DB brief_id (authoritative) over the runner dict.
                s["brief_id"] = cr["brief_id"] or s.get("brief_id")
                s["db_error_message"] = cr["error_message"]
                s["completed_at"] = cr["completed_at"]
            brief_id = s.get("brief_id")
            if brief_id:
                cursor = await conn.execute(
                    """SELECT panel_consensus_score, panel_verdict, novelty_score,
                              novelty_verdict, is_stub, stub_reason
                       FROM briefs WHERE id = ?""",
                    (brief_id,),
                )
                br = await cursor.fetchone()
                if br:
                    s["panel_consensus_score"] = br["panel_consensus_score"]
                    s["panel_verdict"] = br["panel_verdict"]
                    s["novelty_score"] = br["novelty_score"]
                    s["novelty_verdict"] = br["novelty_verdict"]
                    s["is_stub"] = bool(br["is_stub"])
                    s["stub_reason"] = br["stub_reason"]
    return summaries


def rank_summaries(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort: publishable (complete + not stub) first by score DESC, then the rest."""
    def key(s: dict[str, Any]) -> tuple[int, float]:
        publishable = (
            s.get("status") == "complete" and not s.get("is_stub", False)
        )
        score = s.get("panel_consensus_score") or 0.0
        # (not publishable, -score) → publishable first, highest score first.
        return (0 if publishable else 1, -float(score))
    return sorted(summaries, key=key)


def top_publishable(summaries: list[dict[str, Any]], n: int = 2) -> list[dict[str, Any]]:
    publishable = [
        s for s in summaries
        if s.get("status") == "complete" and not s.get("is_stub", False)
    ]
    return publishable[:n]


# ── Output formatters ────────────────────────────────────────────────


def format_console_table(summaries: list[dict[str, Any]]) -> str:
    """Fixed-width text table, one row per request."""
    top_ids = {s["request_id"] for s in top_publishable(summaries, n=2)}
    header = (
        f"{'★':^3} {'#':<3} {'request_id':<34} {'brief_id':<20} "
        f"{'domain_b':<32} {'status':<10} {'stub':<5} {'score':<6}"
    )
    lines = [header, "─" * len(header)]
    for i, s in enumerate(summaries, 1):
        star = "★" if s["request_id"] in top_ids else " "
        score = s.get("panel_consensus_score")
        score_str = f"{score:.1f}" if isinstance(score, (int, float)) else "-"
        domain_b = (s.get("domain_b") or "?")[:32]
        brief_id = s.get("brief_id") or "-"
        lines.append(
            f"{star:^3} {i:<3} {s['request_id']:<34} {brief_id:<20} "
            f"{domain_b:<32} {str(s.get('status', '?')):<10} "
            f"{('yes' if s.get('is_stub') else 'no'):<5} {score_str:<6}"
        )
    return "\n".join(lines)


def format_markdown_report(
    summaries: list[dict[str, Any]],
    started_at: str,
    ended_at: str,
) -> str:
    """Full report saved to outputs/batches/."""
    top_ids = {s["request_id"] for s in top_publishable(summaries, n=2)}
    lines: list[str] = []
    lines.append(f"# Batch review — {DOMAIN_A}")
    lines.append("")
    lines.append(f"- **Started**: {started_at}")
    lines.append(f"- **Ended**: {ended_at}")
    lines.append(f"- **Domain A (fixed)**: `{DOMAIN_A}`")
    lines.append(f"- **Mode**: surprise (SPORE picks domain B)")
    lines.append(f"- **Count requested**: {COUNT}")
    lines.append(f"- **System user**: `{SYSTEM_USER_EMAIL}`")
    lines.append("")
    lines.append("## Results (ranked: publishable first, panel score DESC)")
    lines.append("")
    lines.append(
        "| ★ | # | request_id | brief_id | domain_b | status | stub | "
        "panel | novelty |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(summaries, 1):
        star = "★" if s["request_id"] in top_ids else ""
        panel = s.get("panel_consensus_score")
        panel_str = f"{panel:.1f}" if isinstance(panel, (int, float)) else "—"
        nov = s.get("novelty_score")
        nov_str = f"{nov:.2f}" if isinstance(nov, (int, float)) else "—"
        lines.append(
            f"| {star} | {i} | `{s['request_id']}` | "
            f"`{s.get('brief_id') or '—'}` | {s.get('domain_b') or '?'} | "
            f"{s.get('status', '?')} | "
            f"{'yes' if s.get('is_stub') else 'no'} | {panel_str} | {nov_str} |"
        )
    lines.append("")

    lines.append("## URLs to share with Fabien (top 2 publishable)")
    lines.append("")
    top = top_publishable(summaries, n=2)
    if top:
        for s in top:
            lines.append(
                f"- `{s['brief_id']}` — {s.get('domain_a')} × {s.get('domain_b')} "
                f"— panel {s.get('panel_consensus_score'):.1f}, "
                f"novelty {s.get('novelty_score'):.2f}"
            )
            lines.append(
                f"  → https://spore-research.com/briefs/{s['brief_id']}"
            )
    else:
        lines.append(
            "_No publishable brief produced. Consider re-running the batch._"
        )
    lines.append("")

    lines.append("## Failures / stubs (transparency)")
    lines.append("")
    non_pub = [
        s for s in summaries
        if not (s.get("status") == "complete" and not s.get("is_stub", False))
    ]
    if non_pub:
        for s in non_pub:
            bits = [f"status=`{s.get('status')}`"]
            if s.get("is_stub"):
                bits.append(f"stub_reason=`{s.get('stub_reason')}`")
            if s.get("error"):
                bits.append(f"runner_error=`{s.get('error')}`")
            if s.get("db_error_message"):
                bits.append(f"db_error=`{s.get('db_error_message')}`")
            lines.append(
                f"- `{s['request_id']}` "
                f"({s.get('domain_a')} × {s.get('domain_b') or '?'}) "
                f"— {', '.join(bits)}"
            )
    else:
        lines.append("_None — every collision produced a publishable brief._")

    return "\n".join(lines) + "\n"


# ── Main orchestration ───────────────────────────────────────────────


async def main() -> int:
    setup_logging()
    started_at = datetime.now().isoformat(timespec="seconds")
    logger.info(
        "batch_start", domain_a=DOMAIN_A, count=COUNT,
        parallel=PARALLEL, timeout_s=TIMEOUT_PER_REQUEST_SECONDS,
    )

    await init_database()

    # Idempotent: first call inserts + triggers the admin notif, subsequent
    # runs hit the SELECT branch silently.
    user = await get_or_create_user(SYSTEM_USER_EMAIL)
    user_id = user["id"]
    logger.info("batch_system_user", user_id=user_id, email=user["email"])

    request_ids = await provision_requests(user_id)

    semaphore = asyncio.Semaphore(PARALLEL)
    summaries: list[dict[str, Any]] = await asyncio.gather(
        *[run_one(rid, semaphore) for rid in request_ids]
    )
    summaries = await enrich_with_db_state(summaries)
    summaries = rank_summaries(summaries)

    ended_at = datetime.now().isoformat(timespec="seconds")

    # Console output
    print()
    print(f"=== Batch review — {DOMAIN_A} ===")
    print(f"Started: {started_at}    Ended: {ended_at}")
    print(format_console_table(summaries))
    print()

    # Markdown report
    settings = get_settings()
    batches_dir = Path(settings.output_dir) / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    slug = (
        DOMAIN_A.lower().replace(" ", "_").replace("/", "-").replace(".", "")
    )
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    md_path = batches_dir / f"{slug}_{ts}.md"
    md_path.write_text(
        format_markdown_report(summaries, started_at, ended_at),
        encoding="utf-8",
    )
    print(f"Report saved: {md_path}")

    publishable = top_publishable(summaries, n=len(summaries))
    stubs = [s for s in summaries if s.get("is_stub")]
    failed = [s for s in summaries if s.get("status") == "failed"]
    logger.info(
        "batch_complete",
        total=len(summaries),
        publishable=len(publishable),
        stubs=len(stubs),
        failed=len(failed),
        report_path=str(md_path),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
