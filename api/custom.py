"""Custom-collision status endpoint + internal re-trigger route.

Customers poll ``GET /api/custom/{id}/status`` to know when their paid
collision has been processed. The internal ``POST /api/custom/{id}/run``
exists so an operator can re-queue a failed run without hitting Stripe.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from logging_config import get_logger
from api.auth import get_current_user
from api.custom_runner import run_custom_request
from api.db import get_custom_request

logger = get_logger("api.custom")

router = APIRouter(prefix="/api/custom", tags=["custom"])


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
