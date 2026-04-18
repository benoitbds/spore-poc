"""Custom-collision status endpoint + internal re-trigger route.

Customers poll ``GET /api/custom/{id}/status`` to know when their paid
collision has been processed. The internal ``POST /api/custom/{id}/run``
exists so an operator can re-queue a failed run without hitting Stripe.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from agents.constitution_guard import _domain_is_excluded
from logging_config import get_logger
from api.auth import get_current_user
from api.config import LAUNCH_MODE
from api.custom_runner import run_custom_request
from api.db import (
    create_custom_request,
    create_purchase,
    get_custom_request,
    user_has_any_custom_request,
)

logger = get_logger("api.custom")

router = APIRouter(prefix="/api/custom", tags=["custom"])


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


class CustomFreeRequest(BaseModel):
    mode: Literal["surprise", "targeted"] = "targeted"
    domain_a: str = Field(..., min_length=3, max_length=80)
    # Required in targeted mode, optional in surprise mode (the runner
    # picks a random partner via the domain map in that case).
    domain_b: Optional[str] = Field(default=None, min_length=3, max_length=80)


class CustomFreeResponse(BaseModel):
    custom_request_id: str
    status: str
    message: str


class CustomStatusResponse(BaseModel):
    id: str
    status: str
    domain_a: str
    domain_b: str
    hypothesis_id: Optional[str] = None
    brief_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class CustomRunResponse(BaseModel):
    id: str
    status: str
    message: str


@router.post("/free", response_model=CustomFreeResponse)
async def custom_free(
    payload: CustomFreeRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> CustomFreeResponse:
    """Launch-mode endpoint — runs a custom collision for free, one per user.

    Bypasses Stripe entirely. Validates the two domains against the
    constitution exclusion list, enforces a 1-per-user lifetime cap,
    inserts a purchases row (type='launch_custom_free', amount_cents=0,
    status='paid') and a matching custom_requests row, then schedules
    the pipeline to run in a BackgroundTask.
    """
    if not LAUNCH_MODE:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "free custom collisions are not available",
        )

    if payload.mode == "targeted":
        if not payload.domain_b:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "'domain_b' is required in targeted mode",
            )
        if payload.domain_a.strip().lower() == payload.domain_b.strip().lower():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "'domain_a' and 'domain_b' must be different",
            )
        _assert_domain_allowed(payload.domain_b)

    _assert_domain_allowed(payload.domain_a)

    user_id = current_user["id"]

    if await user_has_any_custom_request(user_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Limite atteinte pour l'offre de lancement — une seule "
            "collision sur mesure offerte par utilisateur.",
        )

    purchase_id = await create_purchase(
        user_id=user_id,
        type_="launch_custom_free",
        amount_cents=0,
        status="paid",
    )
    request_id = await create_custom_request(
        user_id=user_id,
        domain_a=payload.domain_a.strip(),
        domain_b=payload.domain_b.strip() if payload.domain_b else None,
        purchase_id=purchase_id,
        status="paid",
        mode=payload.mode,
    )

    background_tasks.add_task(_run_wrapper, request_id)

    logger.info(
        "custom_free_enqueued",
        user_id=user_id, request_id=request_id, mode=payload.mode,
        domain_a=payload.domain_a, domain_b=payload.domain_b or "(surprise)",
    )
    return CustomFreeResponse(
        custom_request_id=request_id,
        status="running",
        message="collision lancée — suivi en temps réel sur /status",
    )


@router.get("/{request_id}/status", response_model=CustomStatusResponse)
async def custom_status(
    request_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> CustomStatusResponse:
    """Return the state of a custom_request. Owner-only."""
    request = await get_custom_request(request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "custom request not found")
    if request["user_id"] != current_user["id"]:
        # Don't leak existence — generic 404.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "custom request not found")
    return CustomStatusResponse(
        id=request["id"],
        status=request["status"],
        domain_a=request["domain_a"],
        domain_b=request["domain_b"],
        hypothesis_id=request.get("hypothesis_id"),
        brief_id=request.get("brief_id"),
        error_message=request.get("error_message"),
        created_at=request.get("created_at"),
        completed_at=request.get("completed_at"),
    )


@router.post("/{request_id}/run", response_model=CustomRunResponse)
async def custom_run(
    request_id: str,
    background_tasks: BackgroundTasks,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> CustomRunResponse:
    """Re-queue a custom_request. Only the owner can call; only if paid / failed.

    The normal flow is webhook → BackgroundTask; this endpoint is the
    manual fallback when a prior run crashed and the customer asks the
    support team to retry.
    """
    request = await get_custom_request(request_id)
    if request is None or request["user_id"] != current_user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "custom request not found")
    if request["status"] not in {"paid", "failed"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"cannot re-run request in status {request['status']!r} — "
            f"only 'paid' or 'failed' are retriable",
        )
    background_tasks.add_task(_run_wrapper, request_id)
    logger.info("custom_run_manual_enqueue", request_id=request_id)
    return CustomRunResponse(
        id=request_id, status="running",
        message="run has been queued; poll /status for progress",
    )


async def _run_wrapper(request_id: str) -> None:
    """Background wrapper that swallows exceptions (already logged upstream)."""
    try:
        await run_custom_request(request_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("custom_background_failed", request_id=request_id, error=str(exc))
