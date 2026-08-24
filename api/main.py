"""FastAPI application entrypoint for the SPORE monetisation API.

Mounts the auth / stripe / briefs / custom / newsletter routers. CORS is
configured from ``api.config.CORS_ORIGINS``. The lifespan handler runs
``init_database()`` at startup so any new ``CREATE TABLE IF NOT EXISTS``
in ``storage/database.py:SCHEMA`` materialises before the first request
(introduced for N4.1 ``newsletter_subscribers``; covers all future
schema additions too).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.account_routes import router as account_router
from api.anthology import router as anthology_router
from api.auth import router as auth_router
from api.briefs import router as briefs_router
from api.config import CORS_ORIGINS
from api.custom import router as custom_router
from api.newsletter import router as newsletter_router
from api.stripe_routes import router as stripe_router
from logging_config import get_logger
from storage.database import init_database

logger = get_logger("api.main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Run schema migrations once at API startup. Idempotent."""
    await init_database()
    logger.info("api_db_initialised")
    yield


app = FastAPI(
    title="SPORE Research API",
    version="0.1.0",
    description="Magic-link auth, brief delivery, Stripe checkout, custom collisions.",
    lifespan=lifespan,
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
app.include_router(account_router)
app.include_router(stripe_router)
app.include_router(briefs_router)
app.include_router(custom_router)
app.include_router(newsletter_router)
app.include_router(anthology_router)
