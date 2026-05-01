#!/usr/bin/env python3
"""scripts/outreach_extract.py — N4.4 researcher-to-researcher outreach.

Reads ``briefs.grounding_data.evidence_base`` from SQLite and produces, for
each cited author, a personalised email draft ready to copy-paste into a
mail client. Tracks every generated draft in a single CSV
(``outputs/outreach/_tracking.csv``) so Bac can mark sent / response /
follow-up state by hand.

Strictly READ-ONLY against the database — opens the connection in
``mode=ro`` to make any accidental write fail loudly. No emails are sent
from this script; integration with Resend is intentionally out of scope
(see hard rules in the sprint brief).

Usage
-----
::

    cd /home/baq/Projects/spore-poc
    .venv/bin/python scripts/outreach_extract.py --brief-id SPR-2026-816D
    .venv/bin/python scripts/outreach_extract.py --all-published

Output
------
``outputs/outreach/{brief_id}/{author_lastname}_{doi_short}.md`` — one
draft per (brief, author) pair (within a brief, an author appearing in
multiple papers is keyed to the highest citation_count paper).

``outputs/outreach/_tracking.csv`` — append-only ledger. Header is written
on first run, every subsequent run appends new rows for newly extracted
(brief_id, author_name) pairs. Pairs already present in the CSV are
skipped to avoid clobbering Bac's manual annotations.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "spore.db"
TEMPLATE_PATH = REPO_ROOT / "templates" / "outreach_email.md"
OUTPUT_ROOT = REPO_ROOT / "outputs" / "outreach"
TRACKING_CSV = OUTPUT_ROOT / "_tracking.csv"

# Hard caps — the deontology the README documents.
MAX_AUTHORS_PER_PAPER = 3       # only the first 3 listed authors get emailed
SITE_URL = "https://spore-research.com"

CSV_FIELDS = [
    "date_extracted",
    "brief_id",
    "brief_title",
    "author_name",
    "paper_title",
    "paper_doi",
    "email_sent",
    "email_sent_date",
    "email_address",
    "response_received",
    "response_date",
    "response_summary",
    "followed_up",
    "notes",
]


# ── DB access ──────────────────────────────────────────────────────


def open_readonly() -> sqlite3.Connection:
    """Open the SQLite DB read-only; any DML/DDL raises."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found at {DB_PATH}")
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_brief(conn: sqlite3.Connection, brief_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT id, status, grounding_data, sharpened_data, vulgarization_data "
        "FROM briefs WHERE id = ?",
        (brief_id,),
    ).fetchone()
    return _decode_brief_row(row) if row else None


def fetch_all_published(conn: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    cursor = conn.execute(
        "SELECT id, status, grounding_data, sharpened_data, vulgarization_data "
        "FROM briefs WHERE status = 'complete' "
        "ORDER BY created_at DESC"
    )
    for row in cursor:
        decoded = _decode_brief_row(row)
        if decoded:
            yield decoded


def _decode_brief_row(row: sqlite3.Row) -> Optional[dict[str, Any]]:
    grounding = _safe_json_load(row["grounding_data"])
    sharpened = _safe_json_load(row["sharpened_data"])
    vulgarization = _safe_json_load(row["vulgarization_data"])
    if grounding is None:
        # Stub or partial brief — no evidence_base to mine.
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "grounding": grounding,
        "sharpened": sharpened or {},
        "vulgarization": vulgarization or {},
    }


def _safe_json_load(blob: Any) -> Optional[dict[str, Any]]:
    if not blob:
        return None
    if isinstance(blob, (dict, list)):
        return blob
    try:
        return json.loads(blob)
    except (TypeError, json.JSONDecodeError):
        return None


# ── Author extraction & template binding ───────────────────────────


def extract_author_drafts(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one draft dict per (author, brief). De-duplicates within the brief.

    When the same author appears across multiple evidence_base entries,
    keep the entry with the highest ``citation_count`` (most established
    work — strongest excuse to reach out).
    """
    grounding = brief["grounding"]
    evidence = grounding.get("evidence_base") or []

    by_author: dict[str, dict[str, Any]] = {}
    for paper in evidence:
        if not isinstance(paper, dict):
            continue
        authors = paper.get("authors") or []
        if not isinstance(authors, list):
            continue
        capped = [a for a in authors[:MAX_AUTHORS_PER_PAPER] if isinstance(a, str) and a.strip()]
        for author in capped:
            existing = by_author.get(author)
            if existing is None or _citation_count(paper) > _citation_count(existing["paper"]):
                by_author[author] = {"author": author.strip(), "paper": paper}

    return [
        _build_draft(brief, entry["author"], entry["paper"])
        for entry in by_author.values()
    ]


def _citation_count(paper: dict[str, Any]) -> int:
    raw = paper.get("citation_count")
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _build_draft(brief: dict[str, Any], author: str, paper: dict[str, Any]) -> dict[str, Any]:
    first_name, last_name = _split_name(author)
    domains = _extract_domains(brief)
    return {
        "brief_id": brief["id"],
        "brief_title": _brief_title(brief),
        "domain_a": domains[0],
        "domain_b": domains[1],
        "author_name": author,
        "first_name": first_name,
        "last_name": last_name,
        "paper_title": (paper.get("title") or "").strip(),
        "paper_title_short": _truncate(paper.get("title") or "", 80),
        "paper_doi": (paper.get("doi") or "").strip(),
        "year": paper.get("year") or "",
        "key_finding_short": _truncate(paper.get("key_finding") or "", 100),
        "topic_short": _topic_keywords(paper.get("title") or ""),
        "brief_url": f"{SITE_URL}/briefs/{brief['id']}",
    }


def _split_name(full_name: str) -> tuple[str, str]:
    """Best-effort first/last split.

    "S. V. Rao"          -> ("S. V.", "Rao")
    "Frank Neese"        -> ("Frank", "Neese")
    "Kantharuban Sivalingam" -> ("Kantharuban", "Sivalingam")
    "M. Atanasov"        -> ("M.", "Atanasov")
    Single token         -> ("", token)
    """
    parts = full_name.strip().split()
    if len(parts) <= 1:
        return ("", parts[0] if parts else "")
    return (" ".join(parts[:-1]), parts[-1])


def _brief_title(brief: dict[str, Any]) -> str:
    """Prefer the FR vulgarised title, fall back to the sharpened EN title."""
    fr = (brief["vulgarization"].get("title_fr") or "").strip()
    if fr:
        return fr
    return (brief["sharpened"].get("title") or "").strip() or brief["id"]


def _extract_domains(brief: dict[str, Any]) -> tuple[str, str]:
    """Pull ``[domain_a, domain_b]`` from sharpened_data.domains."""
    domains = brief["sharpened"].get("domains")
    if isinstance(domains, list) and len(domains) >= 2:
        a = str(domains[0]).strip()
        b = str(domains[1]).strip()
        if a and b:
            return (a, b)
    # Fallback (rare): look in grounding_data.domains
    grounding_domains = brief["grounding"].get("domains")
    if isinstance(grounding_domains, list) and len(grounding_domains) >= 2:
        return (str(grounding_domains[0]).strip(), str(grounding_domains[1]).strip())
    return ("(domaine A)", "(domaine B)")


def _truncate(text: str, max_chars: int) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= max_chars:
        return clean
    cut = clean[: max_chars - 1]
    last_space = cut.rfind(" ")
    base = cut[:last_space] if last_space > max_chars - 30 else cut
    return base.rstrip() + "…"


_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "with",
    "by", "from", "as", "at", "via", "into", "is", "are",
}


def _topic_keywords(title: str) -> str:
    """Extract a short topic phrase from a paper title for the email subject."""
    if not title:
        return "votre domaine"
    tokens = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]+", title)
    significant = [t for t in tokens if t.lower() not in _STOPWORDS]
    return " ".join(significant[:5]) or title[:40]


# ── File output ────────────────────────────────────────────────────


def render_email(template: str, draft: dict[str, Any]) -> str:
    """Fill {placeholders} in the template. Raises on any missing key."""
    mapping = {
        "first_name": draft["first_name"],
        "last_name": draft["last_name"],
        "paper_title": draft["paper_title_short"],
        "year": str(draft["year"] or ""),
        "brief_title": draft["brief_title"],
        "domain_a": draft["domain_a"],
        "domain_b": draft["domain_b"],
        "key_finding_short": draft["key_finding_short"] or "votre travail",
        "topic_short": draft["topic_short"],
        "brief_url": draft["brief_url"],
    }
    rendered = template
    for key, value in mapping.items():
        rendered = rendered.replace("{" + key + "}", value)
    leftover = re.findall(r"\{[a-z_]+\}", rendered)
    if leftover:
        raise ValueError(f"Unbound placeholders in template: {set(leftover)}")
    return rendered


def safe_filename(draft: dict[str, Any]) -> str:
    last = draft["last_name"] or "unknown"
    last_safe = re.sub(r"[^A-Za-z0-9\-]+", "", last)[:40] or "author"
    doi = draft["paper_doi"]
    if doi:
        doi_short = re.sub(r"[^A-Za-z0-9]+", "", doi)[-8:]
    else:
        doi_short = "no-doi"
    return f"{last_safe}_{doi_short}.md"


def write_email_file(draft: dict[str, Any], rendered: str) -> Path:
    folder = OUTPUT_ROOT / draft["brief_id"]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe_filename(draft)
    target.write_text(rendered, encoding="utf-8")
    return target


# ── Tracking CSV ───────────────────────────────────────────────────


def load_existing_tracking_keys() -> set[tuple[str, str]]:
    """Return the set of (brief_id, author_name) pairs already in the CSV.

    Used to skip re-appending rows that Bac may have annotated since the
    previous run. Append-only writes keep manual edits intact.
    """
    if not TRACKING_CSV.exists():
        return set()
    keys: set[tuple[str, str]] = set()
    with TRACKING_CSV.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            keys.add((row.get("brief_id", ""), row.get("author_name", "")))
    return keys


def append_tracking_rows(rows: list[dict[str, Any]]) -> None:
    """Append-only CSV writer. Creates the file with header on first call."""
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    write_header = not TRACKING_CSV.exists()
    with TRACKING_CSV.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_tracking_row(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "date_extracted": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brief_id": draft["brief_id"],
        "brief_title": draft["brief_title"],
        "author_name": draft["author_name"],
        "paper_title": draft["paper_title_short"],
        "paper_doi": draft["paper_doi"],
        "email_sent": "False",
        "email_sent_date": "",
        "email_address": "",
        "response_received": "False",
        "response_date": "",
        "response_summary": "",
        "followed_up": "False",
        "notes": "",
    }


# ── Orchestration ──────────────────────────────────────────────────


def process_brief(
    brief: dict[str, Any],
    template: str,
    seen_keys: set[tuple[str, str]],
) -> tuple[int, int]:
    """Generate drafts for one brief. Returns (written, skipped_duplicates)."""
    drafts = extract_author_drafts(brief)
    written = 0
    skipped = 0
    new_tracking: list[dict[str, Any]] = []
    for draft in drafts:
        key = (draft["brief_id"], draft["author_name"])
        if key in seen_keys:
            skipped += 1
            continue
        rendered = render_email(template, draft)
        write_email_file(draft, rendered)
        new_tracking.append(build_tracking_row(draft))
        seen_keys.add(key)
        written += 1
    if new_tracking:
        append_tracking_rows(new_tracking)
    return written, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--brief-id", help="Process a single brief by id (SPR-2026-XXXX)")
    group.add_argument(
        "--all-published",
        action="store_true",
        help="Process every brief with status='complete'",
    )
    args = parser.parse_args()

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    seen_keys = load_existing_tracking_keys()

    total_written = 0
    total_skipped = 0
    briefs_processed = 0

    with open_readonly() as conn:
        if args.brief_id:
            brief = fetch_brief(conn, args.brief_id)
            if brief is None:
                print(f"ERR: brief {args.brief_id} not found or has no grounding_data", file=sys.stderr)
                return 1
            written, skipped = process_brief(brief, template, seen_keys)
            total_written += written
            total_skipped += skipped
            briefs_processed = 1
            print(f"{args.brief_id}: {written} drafts written, {skipped} skipped (already in CSV)")
        else:
            for brief in fetch_all_published(conn):
                written, skipped = process_brief(brief, template, seen_keys)
                total_written += written
                total_skipped += skipped
                briefs_processed += 1
                print(f"{brief['id']}: {written} drafts written, {skipped} skipped")

    print(
        f"\nDone — {briefs_processed} briefs processed, "
        f"{total_written} new drafts, {total_skipped} skipped duplicates."
    )
    print(f"Drafts:   {OUTPUT_ROOT}/")
    print(f"Tracking: {TRACKING_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
