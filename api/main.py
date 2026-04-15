"""FastAPI application entrypoint for the SPORE monetisation API.

Mounts the auth / stripe / briefs / custom routers. CORS is configured
from ``api.config.CORS_ORIGINS``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import router as auth_router
from api.config import CORS_ORIGINS
from api.stripe_routes import router as stripe_router

app = FastAPI(
    title="SPORE Research API",
    version="0.1.0",
    description="Magic-link auth, brief delivery, Stripe checkout, custom collisions.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Stripe-Signature"],
)


@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(stripe_router)
