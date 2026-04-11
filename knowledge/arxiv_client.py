"""ArXiv API client for SPORE.

Provides paper search and abstract retrieval from ArXiv preprints.
ArXiv API is free and has generous rate limits (3 sec between requests recommended).
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from logging_config import get_logger

logger = get_logger("arxiv")

# ArXiv API endpoint (HTTPS required)
BASE_URL = "https://export.arxiv.org/api/query"

# Recommended rate limit: 3 seconds between requests
RATE_LIMIT_DELAY = 3.0

# ArXiv categories relevant to materials science
MATERIALS_CATEGORIES = [
    "cond-mat.mtrl-sci",  # Materials Science
    "cond-mat.soft",      # Soft Condensed Matter
    "cond-mat.str-el",    # Strongly Correlated Electrons
    "cond-mat.supr-con",  # Superconductivity
    "physics.app-ph",     # Applied Physics
    "physics.chem-ph",    # Chemical Physics
]


class ArxivClient:
    """Async client for ArXiv API."""

    def __init__(self):
        """Initialize the client."""
        self._last_request_time: float = 0

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time

        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)

        self._last_request_time = asyncio.get_event_loop().time()

    def _parse_entry(self, entry: ET.Element, ns: dict[str, str]) -> dict[str, Any]:
        """Parse a single ArXiv entry from XML.

        Args:
            entry: XML element for the entry
            ns: Namespace dictionary

        Returns:
            Parsed paper dictionary
        """
        # Helper to get text safely
        def get_text(xpath: str) -> str:
            elem = entry.find(xpath, ns)
            return elem.text.strip() if elem is not None and elem.text else ""

        # Get authors
        authors = []
        for author in entry.findall("atom:author", ns):
            name = author.find("atom:name", ns)
            if name is not None and name.text:
                authors.append(name.text.strip())

        # Get categories
        categories = []
        for cat in entry.findall("atom:category", ns):
            term = cat.get("term")
            if term:
                categories.append(term)

        # Extract ArXiv ID from the id URL
        arxiv_id = ""
        id_text = get_text("atom:id")
        if id_text:
            # Format: http://arxiv.org/abs/1234.56789v1
            match = re.search(r"arxiv.org/abs/(.+?)(?:v\d+)?$", id_text)
            if match:
                arxiv_id = match.group(1)

        # Parse published date
        published = get_text("atom:published")[:10]  # YYYY-MM-DD

        return {
            "arxiv_id": arxiv_id,
            "title": get_text("atom:title").replace("\n", " "),
            "abstract": get_text("atom:summary").replace("\n", " "),
            "authors": authors,
            "published": published,
            "categories": categories,
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else "",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    )
    async def search_papers(
        self,
        query: str,
        max_results: int = 10,
        categories: Optional[list[str]] = None,
        sort_by: str = "relevance",
    ) -> list[dict[str, Any]]:
        """Search for papers on ArXiv.

        Args:
            query: Search query
            max_results: Maximum number of results
            categories: ArXiv categories to search in (e.g., ["cond-mat.mtrl-sci"])
            sort_by: Sort order ("relevance", "lastUpdatedDate", "submittedDate")

        Returns:
            List of paper dictionaries
        """
        await self._rate_limit()

        # Build search query
        search_parts = [f"all:{query}"]

        if categories:
            cat_query = " OR ".join(f"cat:{cat}" for cat in categories)
            search_parts.append(f"({cat_query})")

        search_query = " AND ".join(search_parts)

        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": "descending",
        }

        url = f"{BASE_URL}?{urlencode(params)}"

        logger.debug("searching_arxiv", query=query, max_results=max_results)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

        # Parse XML response
        root = ET.fromstring(response.text)

        # Define namespace
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        papers = []
        for entry in root.findall("atom:entry", ns):
            paper = self._parse_entry(entry, ns)
            if paper.get("title"):  # Skip empty entries
                papers.append(paper)

        logger.info("arxiv_papers_found", query=query, count=len(papers))
        return papers

    async def get_context(
        self,
        domain_name: str,
        key_concepts: list[str],
        max_papers: int = 5,
    ) -> list[str]:
        """Get context abstracts for a domain from ArXiv.

        Args:
            domain_name: Name of the scientific domain
            key_concepts: Key concepts/keywords
            max_papers: Maximum abstracts to return

        Returns:
            List of formatted abstracts
        """
        # Build search query
        query_parts = [domain_name] + key_concepts[:2]
        query = " ".join(query_parts)

        logger.info("getting_arxiv_context", domain=domain_name, query=query)

        try:
            papers = await self.search_papers(
                query,
                max_results=max_papers * 2,
                categories=MATERIALS_CATEGORIES,
            )

            # Filter to papers with substantive abstracts
            papers_with_abstracts = [
                p for p in papers
                if p.get("abstract") and len(p.get("abstract", "")) > 100
            ]

            # Extract and format abstracts
            abstracts = []
            for paper in papers_with_abstracts[:max_papers]:
                abstract = paper.get("abstract", "").strip()
                title = paper.get("title", "Unknown")
                arxiv_id = paper.get("arxiv_id", "")
                published = paper.get("published", "")[:4]  # Year only

                # Format with metadata
                formatted = f"[{title} (arXiv:{arxiv_id}, {published})]\n{abstract}"
                abstracts.append(formatted)

            logger.info("arxiv_context_retrieved", domain=domain_name, abstracts=len(abstracts))
            return abstracts

        except Exception as e:
            logger.error("arxiv_context_failed", domain=domain_name, error=str(e))
            return []


# Global singleton
_client: Optional[ArxivClient] = None


def get_arxiv_client() -> ArxivClient:
    """Get the global ArXiv client."""
    global _client
    if _client is None:
        _client = ArxivClient()
    return _client


async def get_context(
    domain_name: str,
    key_concepts: list[str],
    max_papers: int = 5,
) -> list[str]:
    """Convenience function to get context for a domain from ArXiv.

    Args:
        domain_name: Name of the scientific domain
        key_concepts: Key concepts/keywords
        max_papers: Maximum papers to retrieve

    Returns:
        List of formatted abstracts
    """
    client = get_arxiv_client()
    return await client.get_context(domain_name, key_concepts, max_papers)
