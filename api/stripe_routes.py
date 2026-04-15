"""Stripe Checkout + webhook endpoints.

The checkout route takes an authenticated user, creates a Stripe
Checkout session, and returns the redirect URL. The webhook verifies
Stripe's signature, marks the matching purchase row as paid, applies
credits (or flips free_brief_used), and — for ``custom`` purchases —
enqueues the L0+post-fire pipeline via FastAPI BackgroundTasks.

``BackgroundTasks`` runs after the response is sent, so Stripe sees a
sub-second 200 regardless of pipeline duration. The actual pipeline
runs inside the uvicorn worker's event loop — acceptable for a POC,
but a dedicated queue (RQ / ARQ) is the next step once traffic grows.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

import stripe
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from pydantic import BaseModel, Field

from agents.constitution_guard import _domain_is_excluded
from logging_config import get_logger
from api.auth import get_current_user
from api.config import (
    BASE_URL,
    PRICES,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from api.db import (
    create_custom_request,
    create_purchase,
    get_user,
    link_custom_request_to_purchase,
    mark_free_brief_used,
    mark_purchase_paid,
    update_user_credits,
)
from api.emails import send_purchase_confirmation

logger = get_logger("api.stripe")

stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter(prefix="/api/stripe", tags=["stripe"])


# ── Constitution gate for custom collisions ────────────────────────
#
# Loaded once; the constitution excluded_domains list changes rarely.
_EXCLUDED_DOMAINS = [
    "weapons_development",
    "surveillance_technology",
    "any domain with dual-use concerns without human approval",
]


def _assert_domain_allowed(domain: str) -> None:
    """Raise 400 if ``domain`` would be killed by the constitution guard."""
    hit = _domain_is_excluded(domain, _EXCLUDED_DOMAINS)
    if hit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"domain '{domain}' is excluded by the SPORE constitution "
                   f"(matches rule '{hit}')",
        )


# ── Request models ─────────────────────────────────────────────────


class CheckoutRequest(BaseModel):
    type: Literal["single", "pack_5", "custom"] = Field(
        ..., description="Product type"
    )
    brief_id: Optional[str] = Field(
        None,
        description="For 'single': the brief to unlock. Optional — the user "
                    "can also spend the credit later.",
    )
    domain_a: Optional[str] = Field(
        None, description="Required for type='custom'"
    )
    domain_b: Optional[str] = Field(
        None, description="Required for type='custom'"
    )


class CheckoutResponse(BaseModel):
    checkout_url: str
    purchase_id: str


# ── Routes ─────────────────────────────────────────────────────────


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> CheckoutResponse:
    """Create a Stripe Checkout session and return its URL."""
    if payload.type not in PRICES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown product type")

    amount = PRICES[payload.type]
    user_id = current_user["id"]
    email = current_user["email"]

    # Custom collisions require two allowed domain names.
    custom_request_id: Optional[str] = None
    if payload.type == "custom":
        if not payload.domain_a or not payload.domain_b:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "'domain_a' and 'domain_b' are required for custom collisions",
            )
        _assert_domain_allowed(payload.domain_a)
        _assert_domain_allowed(payload.domain_b)

    # Pre-insert a pending purchase so the webhook can find it by session id.
    label_map = {"single": "brief", "pack_5": "pack_5", "custom": "custom"}
    purchase_id = await create_purchase(
        user_id=user_id,
        type_=payload.type,
        amount_cents=amount,
        brief_id=payload.brief_id if payload.type == "single" else None,
        status="pending",
    )

    if payload.type == "custom":
        custom_request_id = await create_custom_request(
            user_id=user_id,
            domain_a=payload.domain_a or "",
            domain_b=payload.domain_b or "",
            purchase_id=purchase_id,
            status="pending",
        )

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=email,
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "eur",
                    "unit_amount": amount,
                    "product_data": {
                        "name": _product_name(payload.type),
                        "description": _product_description(payload),
                    },
                },
            }],
            success_url=f"{BASE_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/payment/cancel",
            metadata={
                "user_id": user_id,
                "type": payload.type,
                "purchase_id": purchase_id,
                "brief_id": payload.brief_id or "",
                "custom_request_id": custom_request_id or "",
            },
        )
    except stripe.error.StripeError as exc:  # noqa: BLE001
        logger.error("stripe_checkout_failed", user_id=user_id, error=str(exc))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Stripe error: {exc.user_message or exc}"
        )

    # Backfill the session id on the pending purchase so the webhook can match.
    from storage.database import get_connection
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE purchases SET stripe_session_id = ? WHERE id = ?",
            (session.id, purchase_id),
        )
        await conn.commit()

    logger.info(
        "stripe_checkout_created",
        user_id=user_id, type=payload.type, session_id=session.id,
        purchase_id=purchase_id,
    )
    return CheckoutResponse(checkout_url=session.url, purchase_id=purchase_id)


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, str]:
    """Handle Stripe webhook events (Stripe-signed with STRIPE_WEBHOOK_SECRET)."""
    if stripe_signature is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing signature")
    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            body, stripe_signature, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.error("stripe_webhook_signature_fail", error=str(exc))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature")

    event_type = event.get("type")
    logger.info("stripe_webhook_received", type=event_type, id=event.get("id"))

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        await _handle_checkout_completed(session, background_tasks)

    # Other events (async payment failures, refunds, …) are acknowledged
    # without side effects for now.
    return {"received": "ok"}


# ── Webhook helpers ────────────────────────────────────────────────


async def _handle_checkout_completed(
    session: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> None:
    """Apply credits / free-brief flips / custom runs after payment."""
    session_id = session["id"]
    payment_intent = session.get("payment_intent")
    metadata = session.get("metadata") or {}
    type_ = metadata.get("type", "")

    updated = await mark_purchase_paid(session_id, payment_intent=payment_intent)
    if updated is None:
        logger.warning("stripe_webhook_unknown_session", session_id=session_id)
        return
    if updated.get("status") != "paid":
        logger.warning(
            "stripe_webhook_purchase_not_paid",
            session_id=session_id, status=updated.get("status"),
        )
        return

    user_id = metadata.get("user_id") or updated["user_id"]
    user = await get_user(user_id)
    if user is None:
        logger.error("stripe_webhook_unknown_user", user_id=user_id)
        return

    # Credit application — idempotent because mark_purchase_paid short-circuits
    # when the row is already paid (returns the existing row without rewriting).
    if type_ == "single":
        await update_user_credits(user_id, +1)
    elif type_ == "pack_5":
        await update_user_credits(user_id, +5)
    elif type_ == "custom":
        custom = await link_custom_request_to_purchase(
            session_id, purchase_id=updated["id"]
        )
        if custom is not None:
            # Defer the actual pipeline run — Stripe needs a fast 200.
            background_tasks.add_task(
                _run_custom_background,
                custom["id"],
            )
        else:
            logger.error("custom_request_missing", purchase_id=updated["id"])

    try:
        send_purchase_confirmation(
            user["email"], type_, int(updated["amount_cents"])
        )
    except Exception as exc:  # noqa: BLE001 — non-critical
        logger.error("purchase_confirmation_failed", error=str(exc))


async def _run_custom_background(request_id: str) -> None:
    """Background task wrapper; imported late to avoid circular imports."""
    from api.custom_runner import run_custom_request

    try:
        await run_custom_request(request_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("custom_runner_failed", request_id=request_id, error=str(exc))


def _product_name(type_: str) -> str:
    return {
        "single": "SPORE — 1 brief de recherche",
        "pack_5": "SPORE — Pack de 5 briefs",
        "custom": "SPORE — Collision custom",
    }.get(type_, "SPORE")


def _product_description(payload: CheckoutRequest) -> str:
    if payload.type == "custom":
        return f"Collision forcée : {payload.domain_a} × {payload.domain_b}"
    if payload.type == "pack_5":
        return "5 crédits pour débloquer 5 briefs de recherche."
    if payload.type == "single" and payload.brief_id:
        return f"Brief {payload.brief_id}"
    return "1 crédit pour débloquer 1 brief de recherche."
