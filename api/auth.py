"""Magic-link authentication + JWT session issuance.

Flow:
  1. Client POSTs their email to ``/api/auth/magic-link``.
  2. Server creates (or finds) the user, mints a one-shot token,
     and emails a verification link to the user.
  3. Client hits ``/api/auth/verify?token=…``; server atomically burns
     the token and returns a JWT session cookie (actually a Bearer
     token — the frontend stores it in memory).
  4. Client sends ``Authorization: Bearer <jwt>`` for subsequent calls;
     ``get_current_user`` is a FastAPI dependency that decodes the JWT
     and loads the user row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field

from logging_config import get_logger
from api.config import (
    JWT_ALGORITHM,
    JWT_EXPIRY_DAYS,
    JWT_SECRET,
    MAGIC_LINK_EXPIRY_HOURS,
)
from api.db import (
    consume_magic_link,
    create_magic_link,
    get_or_create_user,
    get_user,
)
from api.emails import send_magic_link

logger = get_logger("api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request / response models ──────────────────────────────────────


class MagicLinkRequest(BaseModel):
    email: EmailStr = Field(..., description="Email to send the magic link to")


class MagicLinkResponse(BaseModel):
    ok: bool
    message: str


class UserPublic(BaseModel):
    email: EmailStr
    credits: int
    free_brief_used: bool


class VerifyResponse(BaseModel):
    token: str = Field(..., description="JWT session token")
    user: UserPublic


# ── JWT helpers ────────────────────────────────────────────────────


def issue_jwt(user_id: str, email: str) -> str:
    """Sign a session JWT for ``user_id`` (valid ``JWT_EXPIRY_DAYS`` days)."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=JWT_EXPIRY_DAYS)).timestamp()),
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode + verify a JWT. Raises ``HTTPException(401)`` on failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """FastAPI dependency that extracts the Bearer JWT and loads the user.

    Raises 401 if the header is missing / malformed / the JWT is invalid,
    or if the user referenced by the JWT no longer exists.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    claims = decode_jwt(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="malformed token: no sub claim",
        )
    user = await get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user no longer exists",
        )
    return user


# ── Routes ─────────────────────────────────────────────────────────


@router.post("/magic-link", response_model=MagicLinkResponse)
async def request_magic_link(payload: MagicLinkRequest) -> MagicLinkResponse:
    """Create/find the user and email them a one-shot sign-in link."""
    user = await get_or_create_user(payload.email)
    token = await create_magic_link(user["id"], ttl_hours=MAGIC_LINK_EXPIRY_HOURS)
    try:
        send_magic_link(payload.email, token)
    except Exception:  # noqa: BLE001 — already logged by send_magic_link
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="could not send magic link email",
        )
    logger.info("magic_link_requested", user_id=user["id"], email=payload.email)
    return MagicLinkResponse(ok=True, message="Check your email")


@router.get("/verify", response_model=VerifyResponse)
async def verify_magic_link(token: str) -> VerifyResponse:
    """Exchange a magic-link token for a JWT. Idempotent.

    A second call with the same (non-expired) token re-issues a fresh
    JWT for the same user instead of failing — StrictMode, retries and
    shared clicks used to cause a valid session to be wiped on the
    second call because the backend rejected the replay.
    """
    state = await consume_magic_link(token)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired token",
        )

    user = await get_user(state["user_id"])
    if user is None:
        # Theoretically unreachable (token FK → user), but guard anyway.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="user missing after token verification",
        )
    jwt_token = issue_jwt(user["id"], user["email"])

    if state["already_used"]:
        logger.warning(
            "magic_link_verified_idempotent",
            user_id=user["id"], token_prefix=token[:8],
        )
    else:
        logger.info("magic_link_verified", user_id=user["id"])

    return VerifyResponse(
        token=jwt_token,
        user=UserPublic(
            email=user["email"],
            credits=user["credits"],
            free_brief_used=user["free_brief_used"],
        ),
    )


@router.get("/me", response_model=UserPublic)
async def me(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> UserPublic:
    """Return the authenticated user's public profile."""
    return UserPublic(
        email=current_user["email"],
        credits=current_user["credits"],
        free_brief_used=current_user["free_brief_used"],
    )
