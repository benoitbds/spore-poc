"""Local dev entrypoint for the SPORE API.

Usage:
    .venv/bin/python -m api.run

Behind the scenes this is just ``uvicorn api.main:app``. For production,
run uvicorn directly with ``--workers N`` behind a reverse proxy (nginx
/ Caddy / Traefik) that terminates TLS; do not set ``reload=True``.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Start uvicorn on 0.0.0.0:8042 with reload for dev."""
    port = int(os.getenv("PORT", "8042"))
    host = os.getenv("HOST", "0.0.0.0")
    reload = os.getenv("API_RELOAD", "1") == "1"
    uvicorn.run("api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
