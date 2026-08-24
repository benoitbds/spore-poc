"""Newsletter capture (N4.1).

Public, unauthenticated endpoints:

  - ``POST /api/newsletter/subscribe`` — accept an email + signup
    context, create or refresh a subscriber row, send the double-opt-in
    confirmation email.
  - ``GET /api/newsletter/confirm?token=…`` — flip ``confirmed=1`` for
    the matching row and 302-redirect to ``{BASE_URL}/newsletter/confirmed``.
  - ``GET /api/newsletter/unsubscribe?token=…`` — flip ``unsubscribed=1``
    for the matching row and 302-redirect to
    ``{BASE_URL}/newsletter/unsubscribed``.

Both confirm / unsubscribe are idempotent (a replayed click lands on the
same success page rather than the error page) and tolerant to invalid
tokens (redirect to ``/newsletter/error``). The email-sending failure on
``subscribe`` surfaces as 502 so the React form can show a retry-friendly
error.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from api.config import BASE_URL
from api.db import (
    create_subscriber,
    get_subscriber_by_confirmation_token,
    get_subscriber_by_email,
    get_subscriber_by_unsubscribe_token,
    mark_subscriber_confirmed,
    mark_subscriber_unsubscribed,
    refresh_subscriber_confirmation,
)
from api.emails import send_newsletter_confirmation
from logging_config import get_logger

logger = get_logger("api.newsletter")

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


# ── Request / response models ──────────────────────────────────────


class SubscribeRequest(BaseModel):
    email: EmailStr = Field(..., description="Email to subscribe")
    source: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Signup origin marker — 'brief:SPR-XXXX' | 'about' | …",
    )
    brief_id: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Brief id when source = 'brief:…'",
    )


class SubscribeResponse(BaseModel):
    ok: bool
    message: str


# ── Routes ─────────────────────────────────────────────────────────


@router.post("/subscribe", response_model=SubscribeResponse)
async def subscribe(payload: SubscribeRequest) -> SubscribeResponse:
    """Create or refresh a newsletter signup, send the confirmation email."""
    email = payload.email.strip().lower()
    existing = await get_subscriber_by_email(email)

    if existing and existing["confirmed"] and not existing["unsubscribed"]:
        # Already a confirmed live subscriber — surface 409 so the UI can
        # show a friendly "you're already in" instead of silently re-mailing.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vous êtes déjà inscrit à la newsletter SPORE.",
        )

    if existing:
        # Either unconfirmed (resend) or previously unsubscribed (re-opt-in).
        # In both cases we rotate the confirmation_token and fire a fresh
        # double-opt-in email — the old confirmation_token, if any, becomes
        # invalid (UNIQUE INSERT semantic doesn't apply, just SET).
        sub = await refresh_subscriber_confirmation(
            email=email,
            source=payload.source,
            brief_id=payload.brief_id,
        )
        flow = "refresh"
    else:
        sub = await create_subscriber(
            email=email,
            source=payload.source,
            brief_id=payload.brief_id,
        )
        flow = "create"

    try:
        send_newsletter_confirmation(
            email=email,
            confirmation_token=sub["confirmation_token"],
            unsubscribe_token=sub["unsubscribe_token"],
        )
    except Exception:  # noqa: BLE001 — already logged inside the sender
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Impossible d'envoyer l'email de confirmation. Réessayez.",
        )

    logger.info(
        "newsletter_subscribe_requested",
        email=email,
        source=payload.source,
        brief_id=payload.brief_id,
        flow=flow,
    )
    return SubscribeResponse(
        ok=True,
        message="Vérifiez votre boîte mail pour confirmer votre inscription.",
    )


@router.get("/confirm")
async def confirm(token: str) -> RedirectResponse:
    """Confirm a subscription via the email link. 302 to the frontend page.

    Idempotent: a second click on the same link still lands on
    ``/newsletter/confirmed`` rather than ``/newsletter/error`` — the row
    state determines whether we issue the UPDATE or skip it.
    """
    sub = await get_subscriber_by_confirmation_token(token)
    if sub is None:
        return RedirectResponse(
            url=f"{BASE_URL}/newsletter/error",
            status_code=status.HTTP_302_FOUND,
        )

    if not sub["confirmed"]:
        await mark_subscriber_confirmed(sub["id"])
        logger.info(
            "newsletter_confirmed",
            subscriber_id=sub["id"],
            email=sub["email"],
        )
    else:
        logger.info(
            "newsletter_confirmed_idempotent",
            subscriber_id=sub["id"],
            email=sub["email"],
        )
    return RedirectResponse(
        url=f"{BASE_URL}/newsletter/confirmed",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/unsubscribe")
async def unsubscribe(token: str) -> RedirectResponse:
    """One-click unsubscribe. 302 to the frontend ``/newsletter/unsubscribed``.

    Idempotent and tolerant: stale or unknown tokens redirect to
    ``/newsletter/error`` rather than 404 so users never see a raw API
    error page when clicking from their inbox.
    """
    sub = await get_subscriber_by_unsubscribe_token(token)
    if sub is None:
        return RedirectResponse(
            url=f"{BASE_URL}/newsletter/error",
            status_code=status.HTTP_302_FOUND,
        )

    if not sub["unsubscribed"]:
        await mark_subscriber_unsubscribed(sub["id"])
        logger.info(
            "newsletter_unsubscribed",
            subscriber_id=sub["id"],
            email=sub["email"],
        )
    else:
        logger.info(
            "newsletter_unsubscribed_idempotent",
            subscriber_id=sub["id"],
            email=sub["email"],
        )
    return RedirectResponse(
        url=f"{BASE_URL}/newsletter/unsubscribed",
        status_code=status.HTTP_302_FOUND,
    )
