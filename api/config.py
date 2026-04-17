"""Configuration for the SPORE monetisation API.

Secrets are loaded from the environment and **fail hard at import time**
if missing — the alternative (silent defaults like "change-me-in-prod")
hides security-critical misconfiguration in production.

Environment variables:
    STRIPE_SECRET_KEY       — Stripe API secret (sk_live_ / sk_test_)
    STRIPE_WEBHOOK_SECRET   — Stripe webhook signing secret (whsec_…)
    RESEND_API_KEY          — Resend API key (re_…)
    JWT_SECRET              — HMAC secret for session JWTs (>=32 chars)
    BASE_URL                — Public site URL (default: https://spore-research.com)
    API_URL                 — Public API URL (default: https://api.spore-research.com)
    FROM_EMAIL              — RFC-5322 From header (default: SPORE <noreply@spore-research.com>)
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str, min_len: int = 1) -> str:
    """Return env var ``name`` or raise ValueError if unset / too short."""
    val = os.getenv(name)
    if not val or len(val) < min_len:
        raise ValueError(
            f"Required environment variable {name!r} is missing or too short "
            f"(need at least {min_len} chars). Set it in .env before starting the API."
        )
    return val


# ── Hard-fail secrets ──────────────────────────────────────────────
STRIPE_SECRET_KEY: str = _require("STRIPE_SECRET_KEY", min_len=10)
STRIPE_WEBHOOK_SECRET: str = _require("STRIPE_WEBHOOK_SECRET", min_len=10)
RESEND_API_KEY: str = _require("RESEND_API_KEY", min_len=10)
JWT_SECRET: str = _require("JWT_SECRET", min_len=32)

# ── Soft config (defaults OK for local dev) ────────────────────────
BASE_URL: str = os.getenv("BASE_URL", "https://spore-research.com")
API_URL: str = os.getenv("API_URL", "https://api.spore-research.com")
FROM_EMAIL: str = os.getenv("FROM_EMAIL", "SPORE <noreply@spore-research.com>")

# ── Launch mode ────────────────────────────────────────────────────
# When true, briefs and 1 custom collision per user are delivered for
# free (Stripe checkout is bypassed). Flip to "false" to restore the
# paid flow.
LAUNCH_MODE: bool = os.getenv("LAUNCH_MODE", "true").strip().lower() == "true"

# ── Product pricing (cents EUR) ────────────────────────────────────
PRICES: dict[str, int] = {
    "single": 900,      # 9 €  — one brief
    "pack_5": 2900,     # 29 € — five briefs (credits)
    "custom": 2500,     # 25 € — one custom collision
}

# ── JWT parameters ─────────────────────────────────────────────────
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRY_DAYS: int = 30
MAGIC_LINK_EXPIRY_HOURS: int = 24

# ── CORS ───────────────────────────────────────────────────────────
CORS_ORIGINS: list[str] = [
    "https://spore-research.com",
    "https://www.spore-research.com",
    "http://localhost:3000",
]
