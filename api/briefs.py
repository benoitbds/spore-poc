"""Brief delivery endpoint.

``GET /api/briefs/{id}/full`` serves a complete brief payload (all four
JSON blobs + markdown + metadata) to an authenticated user, enforcing
the credit economy:

  1. If the user already owns this brief (a paid ``purchases`` row
     exists), deliver for free — idempotent re-downloads don't burn
     credits.
  2. Else, if the user has never claimed the free brief
     (``free_brief_used = 0``), deliver + flip the flag + record a
     ``type='free'`` purchase row so (1) catches the next re-download.
  3. Else, if ``credits > 0``: deliver, decrement credits, record a
     ``type='single'`` purchase row.
  4. Else: 402 Payment Required with a pointer at the pricing page.

Briefs flagged ``status='rejected'`` or ``status='killed'`` are hidden
from the public API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_config import get_logger
from storage.database import get_brief
from api.auth import get_current_user
from api.config import BASE_URL, PRICES
from api.db import (
    mark_free_brief_used,
    record_brief_ownership,
    update_user_credits,
    user_owns_brief,
)

logger = get_logger("api.briefs")

router = APIRouter(prefix="/api/briefs", tags=["briefs"])

# Briefs in these states must never be served via the public API.
_PUBLIC_BLOCKED_STATUSES = {"rejected", "killed", "pending"}


# ── Response model ─────────────────────────────────────────────────


class BriefFullResponse(BaseModel):
    id: str
    hypothesis_id: str
    status: str
    created_at: str | None = None

    novelty_score: Optional[float] = None
    novelty_verdict: Optional[str] = None
    panel_consensus_score: Optional[float] = None
    panel_verdict: Optional[str] = None

    formal_statement: Optional[str] = None
    markdown: Optional[str] = Field(
        None, description="Full brief markdown if brief_md_path exists on disk"
    )

    grounding_data: Optional[dict[str, Any]] = None
    sharpened_data: Optional[dict[str, Any]] = None
    protocol_data: Optional[dict[str, Any]] = None
    panel_data: Optional[dict[str, Any]] = None
    vulgarization_data: Optional[dict[str, Any]] = None
    # S7.4 Phase 4 — EN translations populated by the post-fire
    # translation_hook node (or by the backfill scripts for existing
    # briefs). NULL on rows whose translation has not yet landed; the
    # frontend already falls back to the FR payload (Phase 3 wiring).
    panel_data_en: Optional[dict[str, Any]] = None
    vulgarization_data_en: Optional[dict[str, Any]] = None

    delivery_reason: str = Field(
        ...,
        description="How access was granted: 'owned' | 'free' | 'credit'",
    )


# ── Route ──────────────────────────────────────────────────────────


@router.get("/{brief_id}/full", response_model=BriefFullResponse)
async def get_full_brief(
    brief_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> BriefFullResponse:
    """Deliver a full brief — idempotent, credit-aware, fail-closed on unknown."""
    brief = await get_brief(brief_id)
    if brief is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brief not found")

    if brief.get("status") in _PUBLIC_BLOCKED_STATUSES:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "brief not available",
        )

    user_id = current_user["id"]

    # ── LAUNCH MODE: all briefs are free after email auth ──────────
    # The credit/payment logic below is preserved but commented out.
    # To re-enable monetisation, uncomment the original block and
    # remove the launch-mode section.

    # 1. Already owned — always free (no duplicate purchase row).
    if await user_owns_brief(user_id, brief_id):
        logger.info("brief_delivered_owned", user_id=user_id, brief_id=brief_id)
        return _build_response(brief, delivery_reason="owned")

    # 2. Launch mode — free for everyone, record ownership.
    await record_brief_ownership(
        user_id=user_id, brief_id=brief_id, type_="launch_free", amount_cents=0,
    )
    logger.info("brief_delivered_launch_free", user_id=user_id, brief_id=brief_id)
    return _build_response(brief, delivery_reason="free")

    # ── Original monetisation logic (disabled during launch) ───────
    # # 2. First free brief of the user's lifetime.
    # if not current_user["free_brief_used"]:
    #     await mark_free_brief_used(user_id)
    #     await record_brief_ownership(
    #         user_id=user_id, brief_id=brief_id, type_="free", amount_cents=0,
    #     )
    #     logger.info("brief_delivered_free", user_id=user_id, brief_id=brief_id)
    #     return _build_response(brief, delivery_reason="free")
    #
    # # 3. Spend a credit.
    # if current_user["credits"] > 0:
    #     await update_user_credits(user_id, -1)
    #     await record_brief_ownership(
    #         user_id=user_id,
    #         brief_id=brief_id,
    #         type_="single",
    #         amount_cents=PRICES["single"],
    #     )
    #     logger.info("brief_delivered_credit", user_id=user_id, brief_id=brief_id)
    #     return _build_response(brief, delivery_reason="credit")
    #
    # # 4. Payment required.
    # raise HTTPException(
    #     status_code=status.HTTP_402_PAYMENT_REQUIRED,
    #     detail={
    #         "error": "no_credits",
    #         "checkout_url": f"{BASE_URL}/pricing",
    #         "message": "No credits left.",
    #     },
    # )


# ── Helpers ────────────────────────────────────────────────────────


def _parse_json_field(value: Any) -> Optional[dict[str, Any]]:
    """Decode a JSON text column into a dict (None-safe)."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value if isinstance(value, dict) else {"items": value}
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _read_markdown(brief: dict[str, Any]) -> Optional[str]:
    """Return the brief markdown if brief_md_path points at a readable file."""
    md_path = brief.get("brief_md_path")
    if not md_path:
        return None
    p = Path(md_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        return p.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _build_response(
    brief: dict[str, Any], delivery_reason: str,
) -> BriefFullResponse:
    return BriefFullResponse(
        id=brief["id"],
        hypothesis_id=brief["hypothesis_id"],
        status=brief.get("status") or "complete",
        created_at=brief.get("created_at"),
        novelty_score=brief.get("novelty_score"),
        novelty_verdict=brief.get("novelty_verdict"),
        panel_consensus_score=brief.get("panel_consensus_score"),
        panel_verdict=brief.get("panel_verdict"),
        formal_statement=brief.get("formal_statement"),
        markdown=_read_markdown(brief),
        grounding_data=_parse_json_field(brief.get("grounding_data")),
        sharpened_data=_parse_json_field(brief.get("sharpened_data")),
        protocol_data=_parse_json_field(brief.get("protocol_data")),
        panel_data=_parse_json_field(brief.get("panel_data")),
        panel_data_en=_parse_json_field(brief.get("panel_data_en")),
        vulgarization_data=_parse_json_field(brief.get("vulgarization_data")),
        vulgarization_data_en=_parse_json_field(brief.get("vulgarization_data_en")),
        delivery_reason=delivery_reason,
    )
