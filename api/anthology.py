"""Anthology PDF download endpoint (N4.2).

Lead-magnet flow: the user submits their email on ``/anthology``, the
API stores them in ``newsletter_subscribers`` (reusing N4.1's table) and
emails them the public download link plus an optional newsletter
confirmation link.

The PDF itself is served statically by the Next.js frontend at
``{BASE_URL}/downloads/spore-anthology-2026.pdf``. There is no token
gating on the file — the email is the qualification signal for the
lead, not an access control. This is the standard lead-magnet pattern
(α in the sprint design discussion).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from api.db import (
    create_subscriber,
    get_subscriber_by_email,
    refresh_subscriber_confirmation,
)
from api.emails import send_anthology_email
from logging_config import get_logger

logger = get_logger("api.anthology")

router = APIRouter(prefix="/api/anthology", tags=["anthology"])

ANTHOLOGY_SOURCE = "anthology_download"


class AnthologyRequest(BaseModel):
    email: EmailStr = Field(..., description="Email to send the PDF link to")


class AnthologyResponse(BaseModel):
    ok: bool
    message: str


@router.post("/request", response_model=AnthologyResponse)
async def request_anthology(payload: AnthologyRequest) -> AnthologyResponse:
    """Email the anthology PDF link to ``payload.email``.

    Subscriber-state branching:
      - **new**         → INSERT row, source='anthology_download',
                          email carries the PDF link AND a confirmation link
      - **unconfirmed** → rotate confirmation_token, refresh source,
                          email carries the PDF link AND a fresh confirmation link
      - **confirmed**   → leave the row alone, email carries the PDF link
                          only (no confirmation link — already confirmed)
    """
    email = payload.email.strip().lower()
    existing = await get_subscriber_by_email(email)

    if existing is None:
        sub = await create_subscriber(email=email, source=ANTHOLOGY_SOURCE)
        flow = "create"
        send_confirmation_link = True
    elif existing["confirmed"] and not existing["unsubscribed"]:
        sub = existing
        flow = "already_confirmed"
        send_confirmation_link = False
    else:
        # Unconfirmed (never confirmed, or unsubscribed and re-engaging).
        sub = await refresh_subscriber_confirmation(
            email=email, source=ANTHOLOGY_SOURCE,
        )
        flow = "refresh"
        send_confirmation_link = True

    try:
        send_anthology_email(
            email=email,
            unsubscribe_token=sub["unsubscribe_token"],
            confirmation_token=(
                sub["confirmation_token"] if send_confirmation_link else None
            ),
        )
    except Exception:  # noqa: BLE001 — already logged inside the sender
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Impossible d'envoyer l'email. Réessayez dans un instant.",
        )

    logger.info(
        "anthology_requested",
        email=email,
        flow=flow,
        confirmation_sent=send_confirmation_link,
    )
    return AnthologyResponse(
        ok=True,
        message="Le lien de téléchargement vient de partir vers votre boîte mail.",
    )
