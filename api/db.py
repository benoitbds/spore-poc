"""Database helpers for the API layer.

These helpers target the four API tables (users, purchases,
custom_requests, magic_links) and reuse ``storage.database.get_connection``
so that every call goes through the same aiosqlite connection pattern as
the rest of SPORE.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from storage.database import get_connection


# ── Users ─────────────────────────────────────────────────────────


async def get_or_create_user(email: str) -> dict[str, Any]:
    """Return the user row for ``email``, creating it if absent.

    Returns a dict with keys: id, email, created_at, stripe_customer_id,
    free_brief_used (bool), credits (int).
    """
    email = email.strip().lower()
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        )
        row = await cursor.fetchone()
        if row is not None:
            return _user_row_to_dict(row)

        user_id = f"usr_{uuid4().hex[:24]}"
        await conn.execute(
            "INSERT INTO users (id, email) VALUES (?, ?)",
            (user_id, email),
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return _user_row_to_dict(row)


async def get_user(user_id: str) -> Optional[dict[str, Any]]:
    """Return the user row for ``user_id`` or None if unknown."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    return _user_row_to_dict(row) if row else None


async def update_user_credits(user_id: str, delta: int) -> int:
    """Add ``delta`` to a user's credits; returns the new balance.

    Negative deltas consume credits. Raises ValueError if the result would
    be negative (callers must verify availability first).
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT credits FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"unknown user {user_id}")
        new_balance = int(row["credits"]) + delta
        if new_balance < 0:
            raise ValueError(
                f"credits would go negative: {row['credits']} + {delta}"
            )
        await conn.execute(
            "UPDATE users SET credits = ? WHERE id = ?", (new_balance, user_id)
        )
        await conn.commit()
    return new_balance


async def mark_free_brief_used(user_id: str) -> None:
    """Flip ``free_brief_used`` to True (idempotent)."""
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE users SET free_brief_used = 1 WHERE id = ?", (user_id,)
        )
        await conn.commit()


async def set_stripe_customer_id(user_id: str, customer_id: str) -> None:
    """Persist the Stripe customer id for ``user_id``."""
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
            (customer_id, user_id),
        )
        await conn.commit()


def _user_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "created_at": row["created_at"],
        "stripe_customer_id": row["stripe_customer_id"],
        "free_brief_used": bool(row["free_brief_used"]),
        "credits": int(row["credits"] or 0),
    }


# ── Magic links ────────────────────────────────────────────────────


async def create_magic_link(user_id: str, ttl_hours: int) -> str:
    """Mint a one-shot magic-link token for ``user_id``. Returns the token."""
    token = uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ttl_hours)
    async with get_connection() as conn:
        await conn.execute(
            "INSERT INTO magic_links (token, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires_at.isoformat()),
        )
        await conn.commit()
    return token


async def consume_magic_link(token: str) -> Optional[str]:
    """Validate and burn a magic link. Returns the associated user_id or None.

    The row is atomically marked ``used=1`` to prevent token replay.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT user_id, expires_at, used FROM magic_links WHERE token = ?",
            (token,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if int(row["used"]) == 1:
            return None
        if row["expires_at"] < now:
            return None
        await conn.execute(
            "UPDATE magic_links SET used = 1 WHERE token = ? AND used = 0",
            (token,),
        )
        await conn.commit()
        return row["user_id"]


# ── Purchases ──────────────────────────────────────────────────────


async def create_purchase(
    user_id: str,
    type_: str,
    amount_cents: int,
    brief_id: Optional[str] = None,
    stripe_session_id: Optional[str] = None,
    status: str = "pending",
) -> str:
    """Insert a purchase row; returns the new purchase id.

    When ``status='paid'`` is passed on creation (free launch-mode flows
    that bypass Stripe), ``paid_at`` is set to now so the row sorts
    correctly in purchase-history listings.
    """
    pid = f"pur_{uuid4().hex[:24]}"
    paid_at = (
        datetime.now(timezone.utc).isoformat() if status == "paid" else None
    )
    async with get_connection() as conn:
        await conn.execute(
            "INSERT INTO purchases "
            "(id, user_id, brief_id, type, amount_cents, "
            "stripe_session_id, status, paid_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid, user_id, brief_id, type_, amount_cents,
                stripe_session_id, status, paid_at,
            ),
        )
        await conn.commit()
    return pid


async def mark_purchase_paid(
    stripe_session_id: str,
    payment_intent: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Mark the purchase with ``stripe_session_id`` as paid; return the row.

    Returns None if no purchase matches the session id.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM purchases WHERE stripe_session_id = ?",
            (stripe_session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if row["status"] == "paid":
            return dict(row)  # already processed, idempotent
        await conn.execute(
            "UPDATE purchases SET status = 'paid', paid_at = ?, "
            "stripe_payment_intent = COALESCE(?, stripe_payment_intent) "
            "WHERE stripe_session_id = ?",
            (now, payment_intent, stripe_session_id),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT * FROM purchases WHERE stripe_session_id = ?",
            (stripe_session_id,),
        )
        return dict(await cursor.fetchone())


async def user_owns_brief(user_id: str, brief_id: str) -> bool:
    """Return True if ``user_id`` has a paid purchase for ``brief_id``."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM purchases "
            "WHERE user_id = ? AND brief_id = ? AND status = 'paid' LIMIT 1",
            (user_id, brief_id),
        )
        row = await cursor.fetchone()
    return row is not None


async def record_brief_ownership(
    user_id: str,
    brief_id: str,
    type_: str,
    amount_cents: int,
) -> str:
    """Record a free / credit-funded brief access as a paid purchase row.

    The idempotency check (``user_owns_brief``) uses this table, so free
    and credit downloads must leave a trail here too.
    """
    now = datetime.now(timezone.utc).isoformat()
    pid = f"pur_{uuid4().hex[:24]}"
    async with get_connection() as conn:
        await conn.execute(
            "INSERT INTO purchases "
            "(id, user_id, brief_id, type, amount_cents, status, paid_at) "
            "VALUES (?, ?, ?, ?, ?, 'paid', ?)",
            (pid, user_id, brief_id, type_, amount_cents, now),
        )
        await conn.commit()
    return pid


# ── Custom requests ────────────────────────────────────────────────


async def create_custom_request(
    user_id: str,
    domain_a: str,
    domain_b: str,
    purchase_id: Optional[str] = None,
    status: str = "pending",
) -> str:
    """Insert a custom_request row; returns the new request id."""
    rid = f"cus_{uuid4().hex[:24]}"
    async with get_connection() as conn:
        await conn.execute(
            "INSERT INTO custom_requests "
            "(id, user_id, domain_a, domain_b, purchase_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, user_id, domain_a, domain_b, purchase_id, status),
        )
        await conn.commit()
    return rid


async def get_custom_request(request_id: str) -> Optional[dict[str, Any]]:
    """Return a custom_request row as a dict, or None."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM custom_requests WHERE id = ?", (request_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def user_has_any_custom_request(user_id: str) -> bool:
    """Return True if ``user_id`` already has a custom_request (any status).

    Used by the launch-mode free endpoint to enforce "1 custom per user".
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM custom_requests WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
    return row is not None


async def update_custom_request(
    request_id: str,
    status: Optional[str] = None,
    hypothesis_id: Optional[str] = None,
    brief_id: Optional[str] = None,
    error_message: Optional[str] = None,
    completed: bool = False,
) -> None:
    """Patch fields on a custom_request."""
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if hypothesis_id is not None:
        fields.append("hypothesis_id = ?")
        values.append(hypothesis_id)
    if brief_id is not None:
        fields.append("brief_id = ?")
        values.append(brief_id)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if completed:
        fields.append("completed_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
    if not fields:
        return
    values.append(request_id)
    async with get_connection() as conn:
        await conn.execute(
            f"UPDATE custom_requests SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await conn.commit()


async def link_custom_request_to_purchase(
    stripe_session_id: str,
    purchase_id: str,
) -> Optional[dict[str, Any]]:
    """Link the custom_request that points at ``purchase_id`` and mark it paid.

    Returns the updated custom_request or None if no such request exists.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM custom_requests WHERE purchase_id = ?",
            (purchase_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await conn.execute(
            "UPDATE custom_requests SET status = 'paid' WHERE id = ?",
            (row["id"],),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT * FROM custom_requests WHERE id = ?", (row["id"],)
        )
        return dict(await cursor.fetchone())
