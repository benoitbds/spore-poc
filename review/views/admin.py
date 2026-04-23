"""Admin page — destructive operations gated behind SPORE_ADMIN_MODE.

Currently hosts:
  * "Reset user custom requests" — drop a user's custom_requests +
    linked briefs so the same account can re-test the signup flow
    without having to spin up a new email each time.

Never exposed publicly: Streamlit listens on port 8501 locally (no
nginx-proxy-manager route). The env-var gate is a soft belt-and-braces
in case the surface ever changes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from helpers import run_async
from storage import init_database
from storage.database import get_connection
from logging_config import get_logger

logger = get_logger("review.admin")


def _admin_enabled() -> bool:
    """Return True if SPORE_ADMIN_MODE is set to '1'. Default: disabled."""
    return os.getenv("SPORE_ADMIN_MODE", "").strip() == "1"


async def _find_user(email: str) -> dict[str, Any] | None:
    """Look up a user by email (case-insensitive). Returns row dict or None."""
    normalized = email.strip().lower()
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT id, email, created_at, free_brief_used, credits "
            "FROM users WHERE email = ?",
            (normalized,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def _list_user_custom_requests(user_id: str) -> list[dict[str, Any]]:
    """Return all custom_requests for a user, newest first."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT id, domain_a, domain_b, status, brief_id,
                      hypothesis_id, created_at, completed_at
               FROM custom_requests
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def _list_user_briefs(user_id: str) -> list[dict[str, Any]]:
    """Return briefs linked to a user via their custom_requests."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT b.id, b.status, b.created_at, b.is_stub, b.stub_reason
               FROM briefs b
               JOIN custom_requests cr ON cr.brief_id = b.id
               WHERE cr.user_id = ?
               ORDER BY b.created_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def _reset_user_custom_requests(
    user_id: str,
    email: str,
) -> dict[str, int]:
    """Delete a user's briefs then their custom_requests in one transaction.

    Order matters: briefs reference custom_requests.brief_id via the JOIN
    used above. We delete briefs first, then custom_requests, so a
    concurrent reader never sees dangling brief rows.

    Does NOT touch the users table — the account stays active, only the
    1-per-user launch quota is released.

    Returns:
        ``{"deleted_requests": int, "deleted_briefs": int}``
    """
    async with get_connection() as conn:
        # Step 1: collect the brief_ids we're about to delete.
        cursor = await conn.execute(
            """SELECT b.id FROM briefs b
               JOIN custom_requests cr ON cr.brief_id = b.id
               WHERE cr.user_id = ?""",
            (user_id,),
        )
        brief_ids = [r[0] for r in await cursor.fetchall()]

        # Step 2: delete briefs (by id, not by join — SQLite DELETE...JOIN
        # isn't portable, and the id list is tiny for a single user).
        deleted_briefs = 0
        if brief_ids:
            placeholders = ",".join("?" * len(brief_ids))
            cursor = await conn.execute(
                f"DELETE FROM briefs WHERE id IN ({placeholders})",
                brief_ids,
            )
            deleted_briefs = cursor.rowcount or 0

        # Step 3: delete the custom_requests themselves.
        cursor = await conn.execute(
            "DELETE FROM custom_requests WHERE user_id = ?",
            (user_id,),
        )
        deleted_requests = cursor.rowcount or 0

        await conn.commit()

    logger.warning(
        "admin_reset_user_custom_requests",
        user_id=user_id,
        email=email,
        deleted_requests=deleted_requests,
        deleted_briefs=deleted_briefs,
        brief_ids=brief_ids,
    )
    return {
        "deleted_requests": deleted_requests,
        "deleted_briefs": deleted_briefs,
    }


def _format_row_table(rows: list[dict[str, Any]], cols: list[str]) -> list[dict[str, Any]]:
    """Restrict rows to the given columns for cleaner st.dataframe display."""
    return [{c: r.get(c) for c in cols} for r in rows]


def render() -> None:
    """Render the admin page."""
    st.title("⚙️ Admin")
    st.caption(
        "Opérations destructives. Réservé à l'opérateur SPORE. "
        "Visible uniquement quand SPORE_ADMIN_MODE=1."
    )

    if not _admin_enabled():
        st.warning(
            "Mode admin désactivé. Pour activer cette page, définir "
            "`SPORE_ADMIN_MODE=1` dans l'environnement du process Streamlit "
            "puis redémarrer (`pm2 restart spore-streamlit`)."
        )
        return

    st.markdown("---")
    st.subheader("Reset user custom requests")
    st.caption(
        "Supprime les `custom_requests` et les `briefs` associés d'un "
        "utilisateur donné. La row `users` elle-même n'est PAS touchée : "
        "le compte reste actif, seul le quota 1-par-user est remis à zéro."
    )

    with st.form("admin_lookup_form", clear_on_submit=False):
        email_input = st.text_input(
            "Email de l'utilisateur",
            placeholder="user@example.com",
        )
        lookup_submitted = st.form_submit_button("🔍 Rechercher")

    if lookup_submitted:
        st.session_state["admin_last_lookup_email"] = email_input
        # Invalidate any prior confirm state when we re-search.
        st.session_state.pop("admin_reset_confirmed", None)

    email = st.session_state.get("admin_last_lookup_email", "")
    if not email:
        st.info("Saisis un email pour afficher ses `custom_requests`.")
        return

    run_async(init_database())
    user = run_async(_find_user(email))
    if user is None:
        st.error(f"Utilisateur non trouvé : {email}")
        return

    st.success(f"**{user['email']}** — `{user['id']}` — inscrit le {user['created_at']}")
    cols = st.columns(3)
    cols[0].metric("Brief gratuit utilisé", "oui" if user["free_brief_used"] else "non")
    cols[1].metric("Crédits", user["credits"])

    # Load both tables
    requests = run_async(_list_user_custom_requests(user["id"]))
    briefs = run_async(_list_user_briefs(user["id"]))
    cols[2].metric("Collisions totales", len(requests))

    st.markdown("#### Custom requests")
    if not requests:
        st.caption("Aucune `custom_request` pour cet utilisateur.")
    else:
        st.dataframe(
            _format_row_table(
                requests,
                ["id", "status", "domain_a", "domain_b",
                 "brief_id", "created_at", "completed_at"],
            ),
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("#### Briefs associés")
    if not briefs:
        st.caption("Aucun `brief` associé aux collisions de cet utilisateur.")
    else:
        st.dataframe(
            _format_row_table(
                briefs,
                ["id", "status", "is_stub", "stub_reason", "created_at"],
            ),
            hide_index=True,
            use_container_width=True,
        )

    # Nothing to reset → no danger zone shown.
    if not requests and not briefs:
        st.info("Rien à réinitialiser pour cet utilisateur.")
        return

    st.markdown("---")
    st.markdown("### 🔴 Danger zone")
    st.caption(
        f"Supprimer **{len(requests)} custom_request(s)** et "
        f"**{len(briefs)} brief(s) associé(s)** pour `{user['email']}`. "
        "Cette action est irréversible. La row `users` reste intacte."
    )

    confirm = st.checkbox(
        f"Je confirme vouloir réinitialiser les collisions de {user['email']}",
        key=f"admin_reset_confirm_{user['id']}",
    )
    reset_disabled = not confirm

    if st.button(
        "🗑️ Réinitialiser les collisions de cet utilisateur",
        type="primary",
        disabled=reset_disabled,
    ):
        try:
            stats = run_async(_reset_user_custom_requests(user["id"], user["email"]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Échec du reset : {exc}")
            logger.error(
                "admin_reset_failed",
                user_id=user["id"], email=user["email"], error=str(exc),
            )
            return
        st.success(
            f"Quota réinitialisé pour **{user['email']}** — "
            f"{stats['deleted_requests']} request(s) et "
            f"{stats['deleted_briefs']} brief(s) supprimés. "
            "L'utilisateur peut maintenant relancer une collision sur mesure."
        )
        # Clear confirm so a second click doesn't re-run the (now no-op) reset
        st.session_state.pop(f"admin_reset_confirm_{user['id']}", None)
