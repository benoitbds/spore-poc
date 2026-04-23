"""Semantic Scholar API client for SPORE.

Full-featured async client with:
- Paper search by keywords
- Paper detail retrieval by ID
- Citation retrieval
- Retry with exponential backoff (1s, 2s, 4s, max 3 retries)
- Rate limiting (1 req/sec courteous)
- Local SQLite cache (semantic_scholar_cache table)
- Structured logging

Rate limits: 100 requests/5min without API key, 1000/5min with key.
"""

import asyncio
import hashlib
import json
import random
import time
from typing import Any, Optional

import aiosqlite
import httpx

from config import get_settings
from logging_config import get_logger

logger = get_logger("semantic_scholar")

# Semantic Scholar API endpoints
BASE_URL = "https://api.semanticscholar.org/graph/v1"
SEARCH_URL = f"{BASE_URL}/paper/search"
PAPER_URL = f"{BASE_URL}/paper"

# Default fields to request on every call
DEFAULT_FIELDS = [
    "title", "abstract", "year", "citationCount",
    "authors", "externalIds", "tldr",
]

# Cache TTL: 30 days — aggressive caching to survive 429 droughts
# while we wait for an API key. Scientific papers don't change.
CACHE_TTL_SECONDS = 30 * 24 * 3600

# Cache schema
CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_scholar_cache (
    cache_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ss_cache_created
    ON semantic_scholar_cache(created_at);
"""


# Global rate-limit state shared across all SemanticScholarClient instances
# (and therefore all concurrent custom runs inside the same uvicorn worker).
_global_lock = asyncio.Lock()
_global_last_request: float = 0.0
RATE_LIMIT_INTERVAL = 1.5   # seconds between requests
RATE_LIMIT_JITTER = 0.3     # random ±jitter added to each wait


class _SSCircuitBreaker:
    """Singleton circuit breaker for Semantic Scholar API calls.

    State machine:
      - CLOSED (default): calls proceed normally; consecutive failures counted.
      - OPEN (after MAX_FAILURES consecutive exhausted retries): calls are
        skipped immediately for COOLDOWN_SECONDS, returning [] / None.
      - After cooldown: the next call is allowed through as a probe.
        If it succeeds → CLOSED. If it fails → OPEN with reset cooldown.

    A "failure" = `_request_with_retry` exhausting its 5 internal retries.
    Sub-events (single 429 inside the retry loop) do NOT increment.
    """

    MAX_FAILURES = 3
    COOLDOWN_SECONDS = 300
    LOG_THROTTLE_SECONDS = 60

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._open_since: Optional[float] = None
        self._last_skip_log: float = 0.0

    def _is_open_at(self, now: float) -> bool:
        if self._consecutive_failures < self.MAX_FAILURES:
            return False
        if self._open_since is None:
            return False
        return (now - self._open_since) < self.COOLDOWN_SECONDS

    def is_open(self) -> bool:
        """Sync probe of the breaker state. Safe to call from non-async contexts."""
        return self._is_open_at(time.time())

    async def should_skip(self) -> bool:
        """Return True if the request must be short-circuited.

        Logs a throttled warning (max 1 per LOG_THROTTLE_SECONDS) so we
        don't spam the log with one line per skipped call.
        """
        async with self._lock:
            now = time.time()
            if self._is_open_at(now):
                if now - self._last_skip_log >= self.LOG_THROTTLE_SECONDS:
                    remaining = int(self.COOLDOWN_SECONDS - (now - self._open_since))
                    logger.warning(
                        "ss_circuit_breaker_skip",
                        consecutive_failures=self._consecutive_failures,
                        cooldown_remaining_s=max(remaining, 0),
                    )
                    self._last_skip_log = now
                return True
            return False

    async def record_success(self) -> None:
        async with self._lock:
            was_recovering = self._consecutive_failures >= self.MAX_FAILURES
            self._consecutive_failures = 0
            self._open_since = None
            self._last_skip_log = 0.0
            if was_recovering:
                logger.warning(
                    "ss_circuit_breaker_closed",
                    message="Semantic Scholar available again",
                )

    async def record_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.MAX_FAILURES:
                first_open = self._open_since is None
                self._open_since = time.time()
                self._last_skip_log = 0.0
                if first_open:
                    logger.warning(
                        "ss_circuit_breaker_opened",
                        consecutive_failures=self._consecutive_failures,
                        cooldown_seconds=self.COOLDOWN_SECONDS,
                    )
                else:
                    logger.warning(
                        "ss_circuit_breaker_probe_failed",
                        cooldown_extended_s=self.COOLDOWN_SECONDS,
                    )


# Module-level singleton: shared across the whole Python process, including
# all concurrent custom runs and L0/post-fire/explorer agents.
_ss_circuit_breaker = _SSCircuitBreaker()


def get_ss_circuit_breaker() -> _SSCircuitBreaker:
    return _ss_circuit_breaker


def is_ss_circuit_open() -> bool:
    """Sync helper for orchestrators that need the breaker state."""
    return _ss_circuit_breaker.is_open()


class SemanticScholarClient:
    """Async client for Semantic Scholar API with cache and retry."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: Optional API key for higher rate limits.
        """
        settings = get_settings()
        self.api_key = api_key or settings.semantic_scholar_api_key
        self._db_path = settings.db_path
        self._cache_initialized = False

    # ── Cache ────────────────────────────────────────────────

    async def _ensure_cache(self) -> None:
        """Create cache table if it doesn't exist."""
        if self._cache_initialized:
            return
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executescript(CACHE_SCHEMA)
            await conn.commit()
        self._cache_initialized = True

    @staticmethod
    def _cache_key(endpoint: str, params: dict[str, Any]) -> str:
        """Deterministic cache key from endpoint + params."""
        raw = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _get_cached(self, key: str) -> Optional[Any]:
        """Return cached response if fresh, else None."""
        await self._ensure_cache()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT response_json, created_at FROM semantic_scholar_cache WHERE cache_key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            if time.time() - row["created_at"] > CACHE_TTL_SECONDS:
                await conn.execute(
                    "DELETE FROM semantic_scholar_cache WHERE cache_key = ?", (key,)
                )
                await conn.commit()
                return None
            return json.loads(row["response_json"])

    async def _set_cached(self, key: str, data: Any) -> None:
        """Store a response in cache."""
        await self._ensure_cache()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """INSERT OR REPLACE INTO semantic_scholar_cache
                   (cache_key, response_json, created_at) VALUES (?, ?, ?)""",
                (key, json.dumps(data), time.time()),
            )
            await conn.commit()

    # ── Rate limiting ────────────────────────────────────────

    async def _rate_limit(self) -> None:
        """Enforce RATE_LIMIT_INTERVAL + jitter between requests, globally."""
        global _global_last_request
        async with _global_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - _global_last_request
            min_wait = RATE_LIMIT_INTERVAL + random.uniform(0, RATE_LIMIT_JITTER)
            if elapsed < min_wait:
                await asyncio.sleep(min_wait - elapsed)
            _global_last_request = asyncio.get_event_loop().time()

    # ── HTTP helpers ─────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    # Backoff schedule (seconds) for 429 / 5xx / connection errors.
    _BACKOFF_DELAYS = [2, 5, 10, 20, 40]

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Execute an HTTP request with aggressive backoff.

        Short-circuits via the global circuit breaker: when OPEN, returns
        None immediately without contacting the API.

        Backoff: 2s, 5s, 10s, 20s, 40s (5 retries). Retries on 429, 5xx,
        and connection errors. On final exhaustion, records a failure to
        the breaker, logs a warning, and returns None — the caller treats
        None as "no results" and continues with partial data.

        Returns:
            Parsed JSON response, or None on 404 / exhausted retries / open breaker.
        """
        breaker = get_ss_circuit_breaker()
        if await breaker.should_skip():
            return None

        last_error: Optional[Exception] = None

        for attempt, delay in enumerate(self._BACKOFF_DELAYS):
            await self._rate_limit()

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.request(
                        method, url, params=params, headers=self._headers()
                    )

                    if response.status_code == 404:
                        return None

                    if response.status_code == 429 or response.status_code >= 500:
                        logger.warning(
                            "ss_api_retryable_error",
                            status=response.status_code,
                            attempt=attempt + 1,
                            max_attempts=len(self._BACKOFF_DELAYS),
                            delay=delay,
                            url=url,
                        )
                        await asyncio.sleep(delay)
                        continue

                    response.raise_for_status()
                    await breaker.record_success()
                    return response.json()

            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_error = exc
                logger.warning(
                    "ss_api_connection_error",
                    error=str(exc),
                    attempt=attempt + 1,
                    delay=delay,
                )
                await asyncio.sleep(delay)

        logger.warning(
            "ss_api_exhausted_retries",
            url=url,
            total_wait=sum(self._BACKOFF_DELAYS),
            last_error=str(last_error),
        )
        await breaker.record_failure()
        return None

    # ── Public API ───────────────────────────────────────────

    async def search_papers(
        self,
        query: str,
        limit: int = 10,
        fields: Optional[list[str]] = None,
        year_min: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Search papers by keyword query.

        Args:
            query: Search keywords.
            limit: Max results (capped at 100).
            fields: Fields to return (defaults to DEFAULT_FIELDS).
            year_min: Optional minimum publication year filter.

        Returns:
            List of paper dicts.
        """
        if fields is None:
            fields = DEFAULT_FIELDS

        params: dict[str, Any] = {
            "query": query,
            "limit": min(limit, 100),
            "fields": ",".join(fields),
        }
        if year_min is not None:
            params["year"] = f"{year_min}-"

        # Check cache
        cache_key = self._cache_key("search", params)
        cached = await self._get_cached(cache_key)
        if cached is not None:
            logger.info("ss_cache_hit", query=query, count=len(cached))
            return cached

        logger.info("ss_search", query=query, limit=limit)
        data = await self._request_with_retry("GET", SEARCH_URL, params)

        if data is None:
            return []

        papers = data.get("data", [])
        logger.info("ss_search_results", query=query, count=len(papers))

        # Cache results
        await self._set_cached(cache_key, papers)
        return papers

    async def get_paper(
        self,
        paper_id: str,
        fields: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Get a paper by its Semantic Scholar ID, DOI, or ArXiv ID.

        Args:
            paper_id: Paper identifier (SS ID, DOI:xxx, ARXIV:xxx).
            fields: Fields to return.

        Returns:
            Paper dict or None if not found.
        """
        if fields is None:
            fields = DEFAULT_FIELDS

        params = {"fields": ",".join(fields)}

        cache_key = self._cache_key(f"paper/{paper_id}", params)
        cached = await self._get_cached(cache_key)
        if cached is not None:
            logger.info("ss_cache_hit_paper", paper_id=paper_id)
            return cached

        logger.info("ss_get_paper", paper_id=paper_id)
        data = await self._request_with_retry("GET", f"{PAPER_URL}/{paper_id}", params)

        if data is not None:
            await self._set_cached(cache_key, data)
        return data

    async def get_citations(
        self,
        paper_id: str,
        limit: int = 10,
        fields: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Get papers that cite a given paper.

        Args:
            paper_id: Paper identifier.
            limit: Max citations to return.
            fields: Fields for each citing paper.

        Returns:
            List of citing paper dicts.
        """
        if fields is None:
            fields = ["title", "year", "citationCount"]

        params = {
            "fields": ",".join(fields),
            "limit": min(limit, 100),
        }

        cache_key = self._cache_key(f"paper/{paper_id}/citations", params)
        cached = await self._get_cached(cache_key)
        if cached is not None:
            logger.info("ss_cache_hit_citations", paper_id=paper_id, count=len(cached))
            return cached

        logger.info("ss_get_citations", paper_id=paper_id, limit=limit)
        data = await self._request_with_retry(
            "GET", f"{PAPER_URL}/{paper_id}/citations", params
        )

        if data is None:
            return []

        # Citations endpoint returns {"data": [{"citingPaper": {...}}, ...]}
        citations = [
            item["citingPaper"]
            for item in data.get("data", [])
            if "citingPaper" in item
        ]
        await self._set_cached(cache_key, citations)
        logger.info("ss_citations_results", paper_id=paper_id, count=len(citations))
        return citations

    # ── Legacy interface (used by explorer agent) ────────────

    async def get_context(
        self,
        domain_name: str,
        key_concepts: list[str],
        max_papers: int = 5,
    ) -> list[str]:
        """Get context abstracts for a domain.

        This is the legacy interface used by the Explorer agent.

        Args:
            domain_name: Name of the scientific domain.
            key_concepts: Key concepts/keywords for the domain.
            max_papers: Maximum number of abstracts to return.

        Returns:
            List of formatted abstract strings.
        """
        query_parts = [domain_name] + key_concepts[:3]
        query = " ".join(query_parts)

        logger.info("getting_context", domain=domain_name, query=query)

        try:
            papers = await self.search_papers(query, limit=max_papers * 2)

            papers_with_abstracts = [
                p for p in papers
                if p.get("abstract") and len(p.get("abstract", "")) > 100
            ]

            papers_with_abstracts.sort(
                key=lambda p: p.get("citationCount", 0) or 0,
                reverse=True,
            )

            abstracts = []
            for paper in papers_with_abstracts[:max_papers]:
                abstract = paper.get("abstract", "").strip()
                title = paper.get("title", "Unknown")
                year = paper.get("year", "")
                formatted = f"[{title} ({year})]\n{abstract}"
                abstracts.append(formatted)

            logger.info("context_retrieved", domain=domain_name, abstracts=len(abstracts))
            return abstracts

        except Exception as e:
            logger.error("context_retrieval_failed", domain=domain_name, error=str(e))
            return []


# ── Singleton ────────────────────────────────────────────────

_client: Optional[SemanticScholarClient] = None


def get_semantic_scholar_client() -> SemanticScholarClient:
    """Get the global Semantic Scholar client (singleton).

    Emits a one-shot startup log on first instantiation indicating
    whether the Semantic Scholar API key is loaded. With a key the
    rate budget is 1 req/s cumulative; without it the anonymous
    quota is much lower and 429s are frequent.
    """
    global _client
    if _client is None:
        _client = SemanticScholarClient()
        if _client.api_key:
            logger.info(
                "ss_api_key_loaded",
                message="Semantic Scholar API key loaded",
            )
        else:
            logger.warning(
                "ss_api_key_missing",
                message="No Semantic Scholar API key found — using anonymous access",
            )
    return _client


async def get_context(
    domain_name: str,
    key_concepts: list[str],
    max_papers: int = 5,
) -> list[str]:
    """Convenience function to get context for a domain.

    Args:
        domain_name: Name of the scientific domain.
        key_concepts: Key concepts/keywords.
        max_papers: Maximum papers to retrieve.

    Returns:
        List of formatted abstracts.
    """
    client = get_semantic_scholar_client()
    return await client.get_context(domain_name, key_concepts, max_papers)
