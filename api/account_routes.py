"""Account endpoints — user's unlocked briefs + custom requests.

Both endpoints require JWT Bearer auth and only return rows belonging
to the authenticated user.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from logging_config import get_logger
from storage.database import get_connection
from api.auth import get_current_user

logger = get_logger("api.account")

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/briefs")
async def account_briefs(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> list[dict[str, Any]]:
    """Return all briefs the user has unlocked (purchased, free, or credit).

    Joins purchases (status='paid', brief_id IS NOT NULL) with briefs to
    provide title, domains, date, and verdict info needed by the /account
    page.
    """
    user_id = current_user["id"]
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT
                p.brief_id,
                p.type        AS purchase_type,
                p.amount_cents,
                p.paid_at,
                b.panel_consensus_score,
                b.panel_verdict,
                b.novelty_verdict,
                b.created_at  AS brief_created_at,
                b.status      AS brief_status
            FROM purchases p
            LEFT JOIN briefs b ON b.id = p.brief_id
            WHERE p.user_id = ?
              AND p.status = 'paid'
              AND p.brief_id IS NOT NULL
            ORDER BY p.paid_at DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@router.get("/custom-requests")
async def account_custom_requests(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> list[dict[str, Any]]:
    """Return all custom collision requests belonging to the user."""
    user_id = current_user["id"]
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT id, domain_a, domain_b, status, hypothesis_id,
                   brief_id, error_message, created_at, completed_at
            FROM custom_requests
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@router.get("/purchases")
async def account_purchases(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> list[dict[str, Any]]:
    """Return the user's paid purchase history."""
    user_id = current_user["id"]
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT id, type, amount_cents, brief_id,
                   stripe_session_id, paid_at
            FROM purchases
            WHERE user_id = ?
              AND status = 'paid'
            ORDER BY paid_at DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]
