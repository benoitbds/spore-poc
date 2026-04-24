"""Enrich the SPORE domain corpus to 500 subdomains via OpenAlex.

The current ``data/domains/all_science.json`` ships with 200 curated
subdomains across 9 disciplines (Physics, Chemistry, Biology, Computer
Science, Earth Sciences, Medicine, Mathematics, Social Sciences,
Engineering). That density is too sparse for the surprise-mode custom
runner: around an arbitrary user domain, the fertile zone (cosine
distance 0.4-0.7) typically contains only ~30 candidates, and the
partner picks tend to be either too obvious (close adjacent fields) or
too abstract (distant high-level buckets). The 2026-04-24 batch on
"Muscle regenerative medicine" produced 0/5 publishable briefs — a
symptom of this sparsity.

This script uses the OpenAlex **Topics** API (the modern replacement
for /concepts, which deprecated the ``ancestors`` field in 2024 — every
concept in the cached dump now comes back with ``ancestors: null``).
Topics ship with a clean ``domain → field → subfield → topic``
hierarchy and a ``keywords`` array that maps directly onto SPORE's
``key_concepts`` shape.

Strategy:
    1. Fetch OpenAlex /topics (sort=works_count:desc) with cursor
       pagination and polite-pool ``mailto=``.
    2. Filter: STEM ``field`` whitelist (drops Arts & Humanities,
       Business/Management, …) + display-name heuristics.
    3. Dedupe against existing 200 by fuzzy name match; matches enrich
       the existing entry with an ``openalex_id`` — they do NOT replace.
    4. Pick new entries by works_count DESC until total == TARGET_COUNT.
    5. Assign each new entry to one of the 9 SPORE disciplines via the
       ``field`` → discipline mapping below.
    6. Write-with-backup + validation probes (critical domains present,
       load-time measurement).

Idempotent: reruns hit ``data/openalex_concepts_cache.json`` instead
of re-fetching (delete the cache to refresh). The corpus backup is
timestamped per run, so safe to call repeatedly.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m scripts.enrich_domain_corpus
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging_config import get_logger, setup_logging

logger = get_logger("scripts.enrich_domain_corpus")


# ── Configuration ────────────────────────────────────────────────────

TARGET_COUNT: int = 500
OPENALEX_FETCH_LIMIT: int = 800          # margin for quality filtering
OPENALEX_PER_PAGE: int = 200
OPENALEX_MAILTO: str = "contact@spore-research.com"
OPENALEX_BASE_URL: str = "https://api.openalex.org/topics"
MAX_RETRIES: int = 4
RETRY_BASE_DELAY: float = 1.5

CORPUS_PATH: Path = Path("data/domains/all_science.json")
CACHE_PATH: Path = Path("data/openalex_topics_cache.json")

# OpenAlex ``field.display_name`` → SPORE discipline bucket.
# Fields not listed (e.g. "Arts and Humanities", "Business, Management
# and Accounting") are filtered out as non-STEM.
FIELD_TO_DISCIPLINE: dict[str, str] = {
    "Physics and Astronomy": "Physics",
    "Chemistry": "Chemistry",
    "Chemical Engineering": "Chemistry",
    "Biochemistry, Genetics and Molecular Biology": "Biology",
    "Agricultural and Biological Sciences": "Biology",
    "Immunology and Microbiology": "Biology",
    "Neuroscience": "Biology",
    "Computer Science": "Computer Science",
    "Decision Sciences": "Computer Science",
    "Earth and Planetary Sciences": "Earth Sciences",
    "Environmental Science": "Earth Sciences",
    "Medicine": "Medicine",
    "Health Professions": "Medicine",
    "Nursing": "Medicine",
    "Dentistry": "Medicine",
    "Pharmacology, Toxicology and Pharmaceutics": "Medicine",
    "Mathematics": "Mathematics",
    "Engineering": "Engineering",
    "Materials Science": "Engineering",
    "Energy": "Engineering",
    "Economics, Econometrics and Finance": "Social Sciences",
    "Psychology": "Social Sciences",
    # NOTE: the OpenAlex field ``Social Sciences`` itself is deliberately
    # omitted — at top works_count it leaks "Classical Antiquity Studies",
    # "French Urban and Social Studies", etc. past the STEM gate. The
    # niche-but-STEM parts of social science (Psychology, Economics) land
    # in dedicated fields already covered above.
}

# Display-name patterns that disqualify a topic outright.
# Mirrors the constitution's ``excluded_domains`` (weapons, surveillance)
# and trims obvious non-STEM buckets that slip past the field filter.
EXCLUSION_PATTERNS: list[re.Pattern[str]] = [
    # ``\bhistory\b`` (not ``\bhistory of\b``) so entries like "American
    # Environmental and Regional History" get caught — they can be
    # categorised under Environmental Science but the content is
    # historiography, not STEM research.
    re.compile(r"\bhistory\b", re.I),
    re.compile(r"\bphilosophy of\b", re.I),
    re.compile(r"\b(ancient|medieval|colonial|postcolonial)\b", re.I),
    re.compile(r"\b(war|warfare|military|weapons?|armament|munitions?|ballistic)\b", re.I),
    re.compile(r"\b(surveillance|spyware)\b", re.I),
    re.compile(r"\bmarketing\b", re.I),
    re.compile(r"\btheology\b", re.I),
    re.compile(r"\b(humanities|literature)\b", re.I),
    re.compile(r"\btourism\b", re.I),
]

# Domains that MUST be present in the enriched corpus. Validated after build.
CRITICAL_DOMAINS: list[str] = [
    "Regenerative Medicine",
    "Stem Cells",
    "Skeletal Muscle",
    "Oncology",
    "Electrochemistry",
    "Marine Biogeochemistry",
    "Carbon Sequestration",
]

# Load-time budget before persisted-embedding cache would be needed.
LOAD_BUDGET_SECONDS: float = 5.0


# ── HTTP layer ───────────────────────────────────────────────────────


async def _request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """GET with exponential backoff on 429/5xx.

    OpenAlex is generous (10 req/s in the polite pool), but we still guard
    against transient failures and rate-limit bursts. Raises the last
    exception if all ``MAX_RETRIES`` attempts fail.
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(url, params=params, timeout=30.0)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "openalex_retry",
                    status=resp.status_code, attempt=attempt + 1, delay=delay,
                )
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.TransportError as exc:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "openalex_transport_error", error=str(exc),
                attempt=attempt + 1, delay=delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError(f"OpenAlex failed after {MAX_RETRIES} retries: {url}")


async def fetch_openalex_topics(limit: int) -> list[dict[str, Any]]:
    """Fetch ``limit`` topics sorted by works_count DESC.

    Uses cursor pagination (OpenAlex requires ``cursor=*`` as the entry
    point, subsequent pages pull the next cursor from meta). Results are
    cached to ``CACHE_PATH`` — delete the file to force a refresh.
    """
    if CACHE_PATH.exists():
        cached = json.loads(CACHE_PATH.read_text())
        if len(cached) >= limit:
            logger.info("openalex_cache_hit", count=len(cached))
            return cached[:limit]

    topics: list[dict[str, Any]] = []
    cursor: Optional[str] = "*"
    async with httpx.AsyncClient() as client:
        while cursor and len(topics) < limit:
            params: dict[str, Any] = {
                "sort": "works_count:desc",
                "per_page": OPENALEX_PER_PAGE,
                "cursor": cursor,
                "mailto": OPENALEX_MAILTO,
            }
            data = await _request_with_retry(client, OPENALEX_BASE_URL, params)
            results = data.get("results", [])
            topics.extend(results)
            cursor = data.get("meta", {}).get("next_cursor")
            logger.info(
                "openalex_page_fetched",
                fetched=len(results), total=len(topics), cursor=cursor,
            )

    topics = topics[:limit]
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(topics, ensure_ascii=False))
    logger.info("openalex_cache_written", path=str(CACHE_PATH), count=len(topics))
    return topics


# ── Filtering + dedup ────────────────────────────────────────────────


def _topic_field(topic: dict[str, Any]) -> Optional[str]:
    """Return the topic's field display_name, if any."""
    return (topic.get("field") or {}).get("display_name")


def _name_slug(name: str) -> str:
    """Normalize for fuzzy name comparison (lower, strip punctuation/spaces)."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def filter_quality(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop non-STEM and surface-level noise before downstream work."""
    kept: list[dict[str, Any]] = []
    drop_counts = {"not_stem": 0, "excluded_pattern": 0, "too_short": 0}
    for t in topics:
        name = t.get("display_name", "")
        field = _topic_field(t)
        if field not in FIELD_TO_DISCIPLINE:
            drop_counts["not_stem"] += 1
            continue
        if any(pat.search(name) for pat in EXCLUSION_PATTERNS):
            drop_counts["excluded_pattern"] += 1
            continue
        if len(name) < 4:
            drop_counts["too_short"] += 1
            continue
        kept.append(t)
    logger.info("quality_filter_applied", kept=len(kept), drops=drop_counts)
    return kept


def dedupe_openalex(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Within the OpenAlex pool, collapse near-duplicates to the highest-volume."""
    seen: dict[str, dict[str, Any]] = {}
    for t in topics:
        slug = _name_slug(t.get("display_name", ""))
        if not slug:
            continue
        existing = seen.get(slug)
        if existing is None or t.get("works_count", 0) > existing.get("works_count", 0):
            seen[slug] = t
    deduped = list(seen.values())
    deduped.sort(key=lambda x: x.get("works_count", 0), reverse=True)
    logger.info("openalex_deduped", before=len(topics), after=len(deduped))
    return deduped


# ── Merge with existing corpus ───────────────────────────────────────


def load_existing_corpus() -> dict[str, Any]:
    """Load and return the current corpus JSON."""
    with CORPUS_PATH.open() as f:
        return json.load(f)


def flatten_existing(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    """Emit a flat list of existing subdomains with their discipline name."""
    flat: list[dict[str, Any]] = []
    for disc in corpus.get("disciplines", []):
        disc_name = disc["name"]
        for sub in disc.get("subdomains", []):
            flat.append({**sub, "_discipline": disc_name})
    return flat


def enrich_existing_with_openalex_ids(
    existing: list[dict[str, Any]],
    openalex: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Attach ``openalex_id`` to each existing entry that fuzzy-matches.

    Returns (enriched_list, used_openalex_slugs) so the caller can skip
    already-matched OpenAlex concepts when picking new additions.
    """
    by_slug = {_name_slug(c["display_name"]): c for c in openalex}
    used: set[str] = set()
    enriched: list[dict[str, Any]] = []
    match_count = 0
    for sub in existing:
        slug = _name_slug(sub["name"])
        match = by_slug.get(slug)
        if match:
            oid = match["id"].rsplit("/", 1)[-1]
            enriched.append({**sub, "openalex_id": oid})
            used.add(slug)
            match_count += 1
        else:
            enriched.append(sub)
    logger.info(
        "existing_enriched_with_openalex_ids",
        matched=match_count, unmatched=len(existing) - match_count,
    )
    return enriched, used


def build_new_entries(
    topics: list[dict[str, Any]],
    used_slugs: set[str],
    existing_ids: set[str],
    needed: int,
) -> list[dict[str, Any]]:
    """Turn unused OpenAlex topics into SPORE subdomain shape.

    Picks top-``needed`` by works_count. The ``id`` is derived from the
    OpenAlex topic id (``T14423`` → ``OA-T14423``) and deduplicated
    against the existing id set in the unlikely case of collision.
    ``key_concepts`` is populated from the topic's native ``keywords``
    array (typically 5-10 terms per topic), falling back to a trimmed
    description if keywords are absent.
    """
    new_entries: list[dict[str, Any]] = []
    for t in topics:
        if len(new_entries) >= needed:
            break
        slug = _name_slug(t["display_name"])
        if slug in used_slugs:
            continue
        field = _topic_field(t)
        if field is None:
            continue
        discipline = FIELD_TO_DISCIPLINE.get(field)
        if discipline is None:
            continue
        openalex_id = t["id"].rsplit("/", 1)[-1]
        sub_id = f"OA-{openalex_id}"
        if sub_id in existing_ids:
            continue

        keywords = [k.strip() for k in (t.get("keywords") or []) if k and k.strip()]
        if not keywords:
            # Fallback: trim the description to one short phrase so the
            # embedding has *some* semantic payload beyond the name.
            desc = (t.get("description") or "").strip()
            if desc:
                keywords = [desc[:200]]

        new_entries.append({
            "id": sub_id,
            "name": t["display_name"],
            "parent_domain": discipline,
            "key_concepts": keywords,
            "_discipline": discipline,
            "openalex_id": openalex_id,
            "_works_count": t.get("works_count", 0),
        })
    logger.info("new_entries_built", count=len(new_entries), needed=needed)
    return new_entries


def assemble_corpus(
    existing_corpus: dict[str, Any],
    enriched_existing: list[dict[str, Any]],
    new_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild the {disciplines:[{name, subdomains:[…]}]} shape.

    Preserves existing discipline order. New entries are appended to their
    mapped discipline's subdomains list; if the mapping lands on a
    discipline that doesn't exist yet (e.g. only from OpenAlex), a fresh
    bucket is created.
    """
    original_disciplines = [d["name"] for d in existing_corpus.get("disciplines", [])]
    buckets: dict[str, list[dict[str, Any]]] = {n: [] for n in original_disciplines}

    for sub in enriched_existing:
        disc = sub.pop("_discipline")
        buckets.setdefault(disc, []).append(sub)

    for entry in new_entries:
        disc = entry.pop("_discipline")
        # Strip private/debug fields that shouldn't land in the corpus.
        entry.pop("_works_count", None)
        buckets.setdefault(disc, []).append(entry)

    rebuilt_disciplines: list[dict[str, Any]] = []
    for name in list(dict.fromkeys(original_disciplines + list(buckets.keys()))):
        rebuilt_disciplines.append({
            "name": name,
            "subdomains": buckets.get(name, []),
        })

    return {
        "domain": existing_corpus.get("domain", "all_science"),
        "description": existing_corpus.get(
            "description",
            "Tous les domaines scientifiques pour exploration interdisciplinaire",
        ),
        "disciplines": rebuilt_disciplines,
    }


# ── Validation probes ────────────────────────────────────────────────


def collect_names(corpus: dict[str, Any]) -> list[str]:
    return [
        sub["name"]
        for disc in corpus["disciplines"]
        for sub in disc["subdomains"]
    ]


def _slug_index(names: list[str]) -> dict[str, str]:
    return {_name_slug(n): n for n in names}


def validate_critical_present(corpus: dict[str, Any]) -> list[tuple[str, bool, Optional[str]]]:
    """Check each CRITICAL_DOMAINS string produces a fertile-zone partner.

    Semantic check (not slug match): the embedding-on-the-fly path is
    what matters for real users. A name like "Skeletal Muscle" doesn't
    have to appear verbatim in the corpus — it just has to land near
    enough STEM neighbours that ``get_random_partner_for`` returns at
    least one partner in the fertile zone [0.4, 0.7]. This mirrors the
    actual user flow in /custom surprise mode.

    Returns (want, found, resolved_partner_name).
    """
    from knowledge.domain_map import reset_domain_map, get_domain_map, DomainNotInterpretableError

    reset_domain_map()
    dm = get_domain_map("all_science")
    out: list[tuple[str, bool, Optional[str]]] = []
    for want in CRITICAL_DOMAINS:
        try:
            picked = dm.get_random_partner_for(want)
        except DomainNotInterpretableError:
            out.append((want, False, None))
            continue
        if picked is None:
            out.append((want, False, None))
        else:
            partner, _ = picked
            out.append((want, True, partner.name))
    return out


def measure_load_time() -> float:
    """Load the domain_map fresh and return elapsed seconds."""
    # Reset the singleton so the timing reflects a cold load. The first
    # call in a process pays the one-time sentence-transformer import
    # overhead; that's what the production custom runner pays too.
    from knowledge.domain_map import reset_domain_map, get_domain_map
    reset_domain_map()
    t0 = time.perf_counter()
    dm = get_domain_map("all_science")
    elapsed = time.perf_counter() - t0
    logger.info("domain_map_load_timed", elapsed=elapsed, count=dm.domain_count)
    return elapsed


# ── Top-level orchestration ──────────────────────────────────────────


def backup_corpus() -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = CORPUS_PATH.parent / f"{CORPUS_PATH.name}.backup_{ts}"
    shutil.copy2(CORPUS_PATH, backup_path)
    logger.info("corpus_backup_created", path=str(backup_path))
    return backup_path


def write_corpus(corpus: dict[str, Any]) -> None:
    payload = json.dumps(corpus, ensure_ascii=False, indent=2)
    CORPUS_PATH.write_text(payload, encoding="utf-8")
    size = CORPUS_PATH.stat().st_size
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    logger.info(
        "corpus_written", path=str(CORPUS_PATH), bytes=size, sha256_prefix=digest,
    )


def format_recap(
    *,
    before_count: int,
    after_count: int,
    backup_path: Path,
    critical_checks: list[tuple[str, bool, Optional[str]]],
    top_names: list[tuple[str, int]],
    load_elapsed: float,
    size_before: int,
    size_after: int,
) -> str:
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append(f"  Domain corpus enrichment — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("=" * 68)
    lines.append("")
    lines.append(f"Corpus path:     {CORPUS_PATH}")
    lines.append(f"Backup:          {backup_path}")
    lines.append(f"Domain count:    {before_count} → {after_count}")
    lines.append(f"File size:       {size_before} → {size_after} bytes")
    lines.append(f"Cold load time:  {load_elapsed:.2f}s  (budget {LOAD_BUDGET_SECONDS}s)")
    lines.append("")
    lines.append("Critical-domain probes (surprise-mode resolution):")
    for want, found, partner in critical_checks:
        tick = "[OK]" if found else "[MISS]"
        arrow = f"  → {partner}" if partner else ""
        lines.append(f"  {tick:6} {want}{arrow}")
    lines.append("")
    lines.append("Top 20 new additions by works_count (OpenAlex):")
    for i, (name, wc) in enumerate(top_names, 1):
        lines.append(f"  {i:3}. {name}  ({wc:,} works)")
    lines.append("")
    lines.append("To apply: /home/baq/.nvm/versions/node/v24.14.0/bin/pm2 restart spore-api")
    lines.append("")
    return "\n".join(lines)


async def main() -> int:
    setup_logging()
    logger.info("enrich_start", target=TARGET_COUNT, fetch_limit=OPENALEX_FETCH_LIMIT)

    existing_corpus = load_existing_corpus()
    existing_flat = flatten_existing(existing_corpus)
    existing_ids = {s["id"] for s in existing_flat}
    before_count = len(existing_flat)
    size_before = CORPUS_PATH.stat().st_size

    raw = await fetch_openalex_topics(OPENALEX_FETCH_LIMIT)
    filtered = filter_quality(raw)
    deduped = dedupe_openalex(filtered)

    enriched_existing, used_slugs = enrich_existing_with_openalex_ids(
        existing_flat, deduped,
    )

    needed = max(0, TARGET_COUNT - before_count)
    new_entries = build_new_entries(
        topics=deduped,
        used_slugs=used_slugs,
        existing_ids=existing_ids,
        needed=needed,
    )

    # Snapshot top-20 new-addition names BEFORE we strip debug fields.
    top_names = [
        (e["name"], e.get("_works_count", 0))
        for e in sorted(new_entries, key=lambda x: x.get("_works_count", 0), reverse=True)[:20]
    ]

    new_corpus = assemble_corpus(existing_corpus, enriched_existing, new_entries)
    after_count = len(collect_names(new_corpus))

    if after_count < TARGET_COUNT:
        logger.warning(
            "target_not_reached",
            after=after_count, target=TARGET_COUNT,
            deduped_available=len(deduped), needed_was=needed,
        )

    backup_path = backup_corpus()
    write_corpus(new_corpus)
    size_after = CORPUS_PATH.stat().st_size

    # Cold load first (honest timing: fresh singleton, forces embedding
    # recompute). Then critical probes reuse the loaded map.
    load_elapsed = measure_load_time()
    critical_checks = validate_critical_present(new_corpus)

    print(format_recap(
        before_count=before_count,
        after_count=after_count,
        backup_path=backup_path,
        critical_checks=critical_checks,
        top_names=top_names,
        load_elapsed=load_elapsed,
        size_before=size_before,
        size_after=size_after,
    ))
    logger.info(
        "enrich_complete",
        before=before_count, after=after_count,
        load_elapsed_s=load_elapsed,
        critical_hit=sum(1 for _, ok, _ in critical_checks if ok),
        critical_total=len(critical_checks),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
