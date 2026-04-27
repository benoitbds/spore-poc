"""Resend-backed transactional emails for the API.

Uses the official ``resend`` SDK (POST to https://api.resend.com/emails).
All calls are synchronous HTTPS; run them in ``run_in_executor`` or keep
them inline — they typically return in <500 ms.
"""

from __future__ import annotations

import resend
from logging_config import get_logger

from api.config import BASE_URL, FROM_EMAIL, RESEND_API_KEY

logger = get_logger("api.emails")
resend.api_key = RESEND_API_KEY


# Admin-side notifications (to operators, not paying users).
ADMIN_NOTIFICATION_EMAIL = "benoit.bds@gmail.com"

# Disposable / test email domains — signups from these MUST NOT spam the
# admin inbox. Matched as a case-insensitive "@{domain}" suffix so
# "yopmailx.com" and "foo.mail-tester.com" don't accidentally get skipped.
_TEST_EMAIL_DOMAINS = ("yopmail.com", "mail-tester.com")


def _is_test_email(email: str) -> bool:
    lower = (email or "").strip().lower()
    return any(lower.endswith(f"@{d}") for d in _TEST_EMAIL_DOMAINS)


def _footer_html() -> str:
    return (
        '<hr style="border:none;border-top:1px solid #eee;margin:24px 0;" />'
        '<p style="color:#888;font-size:12px;">'
        "SPORE Research &middot; "
        f'<a href="{BASE_URL}" style="color:#888;">spore-research.com</a>'
        "</p>"
    )


def _button(href: str, label: str) -> str:
    return (
        f'<p style="text-align:center;margin:32px 0;">'
        f'<a href="{href}" '
        f'style="background:#111;color:#fff;padding:12px 24px;'
        f'text-decoration:none;border-radius:6px;font-weight:600;">'
        f"{label}</a></p>"
    )


def send_magic_link(email: str, token: str, next_path: str | None = None) -> None:
    """Send a magic-link email.

    Link points at ``{BASE_URL}/auth/verify?token=…`` (+ ``&next=…`` when
    provided). ``VerifyClient.tsx`` already reads the ``next`` query param
    and redirects to it on successful verification, falling back to
    ``/briefs`` when absent.

    Args:
        email: Recipient address.
        token: One-shot magic-link token.
        next_path: Optional post-verify redirect; must be a relative path
            starting with ``/`` (the caller is responsible for sanitizing).
    """
    from urllib.parse import quote
    link = f"{BASE_URL}/auth/verify?token={token}"
    if next_path:
        link += f"&next={quote(next_path, safe='/')}"
    html = (
        '<div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;">'
        '<h2 style="color:#111;">Votre accès SPORE</h2>'
        "<p>Cliquez sur le bouton ci-dessous pour vous connecter. "
        "Le lien expire dans 24 heures et ne peut être utilisé qu'une seule fois.</p>"
        + _button(link, "Se connecter")
        + '<p style="color:#666;font-size:13px;">'
        "Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br />"
        f'<a href="{link}" style="color:#666;word-break:break-all;">{link}</a>'
        "</p>"
        + _footer_html()
        + "</div>"
    )
    try:
        resp = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": "Votre accès SPORE",
            "html": html,
        })
        logger.info("magic_link_sent", email=email, resend_id=resp.get("id"))
    except Exception as exc:  # noqa: BLE001 — email failure must not 500 auth flow
        logger.error("magic_link_send_failed", email=email, error=str(exc))
        raise


def send_brief_ready(email: str, brief_id: str, title: str) -> None:
    """Notify a customer that their custom brief is ready."""
    link = f"{BASE_URL}/briefs/{brief_id}"
    safe_title = title or "Votre brief SPORE"
    html = (
        '<div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;">'
        '<h2 style="color:#111;">Votre brief est prêt</h2>'
        f"<p><strong>{safe_title}</strong></p>"
        "<p>Votre collision custom a été traitée par le pipeline SPORE. "
        "Le brief complet (grounding littérature, hypothèse formalisée, "
        "protocole expérimental, panel multi-reviewer) est disponible.</p>"
        + _button(link, "Ouvrir le brief")
        + f'<p style="color:#666;font-size:13px;">Référence : {brief_id}</p>'
        + _footer_html()
        + "</div>"
    )
    try:
        resp = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": "Votre brief SPORE est prêt",
            "html": html,
        })
        logger.info("brief_ready_sent", email=email, brief_id=brief_id, resend_id=resp.get("id"))
    except Exception as exc:  # noqa: BLE001
        logger.error("brief_ready_send_failed", email=email, brief_id=brief_id, error=str(exc))


def send_admin_new_signup(email: str, created_at: str) -> None:
    """Notify the admin inbox that a brand-new user just signed up.

    Fire-and-forget: every failure mode (Resend outage, bad env, HTTP
    timeout) is logged and swallowed — a broken admin notification must
    never 500 the signup flow.

    Silently skipped when the signup email matches a test domain
    (yopmail.com, mail-tester.com) to avoid spamming the admin during
    dev / load testing.
    """
    if _is_test_email(email):
        logger.info("admin_signup_notif_skipped_test_domain", email=email)
        return

    body = (
        "Nouvel utilisateur inscrit sur SPORE\n"
        "\n"
        f"Email : {email}\n"
        f"Date : {created_at}\n"
    )
    try:
        resp = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": ADMIN_NOTIFICATION_EMAIL,
            "subject": f"SPORE — Nouvel inscrit : {email}",
            "text": body,
        })
        logger.info(
            "admin_signup_notif_sent",
            email=email,
            resend_id=resp.get("id"),
        )
    except Exception as exc:  # noqa: BLE001 — signup must not depend on email
        logger.error("admin_signup_notif_failed", email=email, error=str(exc))


def send_purchase_confirmation(email: str, type_: str, amount_cents: int) -> None:
    """Send an order confirmation. Non-blocking failure (not critical)."""
    labels = {
        "single": "1 brief SPORE",
        "pack_5": "Pack de 5 briefs SPORE",
        "custom": "1 collision custom SPORE",
    }
    label = labels.get(type_, type_)
    amount_eur = f"{amount_cents / 100:.2f} €"
    html = (
        '<div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;">'
        '<h2 style="color:#111;">Confirmation d\'achat</h2>'
        f"<p>Nous avons bien reçu votre paiement de <strong>{amount_eur}</strong> "
        f"pour <strong>{label}</strong>.</p>"
        + _button(f"{BASE_URL}/dashboard", "Accéder à mon espace")
        + _footer_html()
        + "</div>"
    )
    try:
        resp = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": "Confirmation d'achat SPORE",
            "html": html,
        })
        logger.info(
            "purchase_confirmation_sent",
            email=email, type=type_, cents=amount_cents, resend_id=resp.get("id"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("purchase_confirmation_send_failed", email=email, error=str(exc))
