#!/usr/bin/env python3
"""scripts/generate_anthology.py — N4.2 lead-magnet PDF generation.

Generates ``spore-anthology-2026.pdf`` from a curated subset of published
briefs and writes it under ``../spore-web/public/downloads/`` so the
file is served statically by the Next.js frontend.

Pipeline (synchronous, ~5 s on a laptop):

    SQLite (read-only)
         │
         ▼  ─── load 8 selected briefs (vulgarization_data + scores)
         │
         ▼  ─── Jinja2 render (templates/anthology/anthology.html.j2)
         │
         ▼  ─── WeasyPrint render (anthology.css → A4 print stylesheet)
         │
         ▼
    public/downloads/spore-anthology-2026.pdf

The selection is hard-coded (see ``ANTHOLOGY_BRIEF_IDS`` below) — chosen
in the S6 audit on the basis of panel_consensus_score and novelty_score
mix. Re-running the script overwrites the PDF idempotently; the order
of briefs in the output follows the order of the constant.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python scripts/generate_anthology.py

Optional flags:
    --output PATH     override the default output path
    --dry-run         render HTML only, skip PDF (useful for iteration)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "spore.db"
TEMPLATE_DIR = REPO_ROOT / "templates" / "anthology"
DEFAULT_OUTPUT = (
    REPO_ROOT.parent / "spore-web" / "public" / "downloads" / "spore-anthology-2026.pdf"
)

# ── Editorial selection (fixed for this anthology run) ────────────────
# Mix of top panel + top novelty, validated in the S6 audit.
ANTHOLOGY_BRIEF_IDS: tuple[str, ...] = (
    "SPR-2026-816D",  # Panel 7.00 / Novelty 0.90 — chimie quantique × bio structurale
    "SPR-2026-6FEB",  # Panel 6.84 / Novelty 0.85 — bio moléculaire × nanotech
    "SPR-2026-5301",  # Panel 6.97 / Novelty 0.80 — photochimie × génie chimique
    "SPR-2026-9A56",  # Panel 6.91 / Novelty 0.80 — génie chimique × manufacturing
    "SPR-2026-FBF3",  # Panel 6.93 / Novelty 0.70 — bio synthétique × régénération
    "SPR-2026-28B2",  # Panel 6.46 / Novelty 0.90 — outlier nouveauté
    "SPR-2026-7626",  # Panel 6.82 / Novelty 0.80
    "SPR-2026-B151",  # Panel 6.72 / Novelty 0.80
)


# ── DB access ──────────────────────────────────────────────────────


def open_readonly() -> sqlite3.Connection:
    """Open the SQLite DB in readonly mode; any DML/DDL raises."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found at {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_brief(conn: sqlite3.Connection, brief_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT id, panel_consensus_score, novelty_score, "
        "       sharpened_data, vulgarization_data, grounding_data "
        "FROM briefs WHERE id = ? AND status = 'complete'",
        (brief_id,),
    ).fetchone()
    if row is None:
        return None

    sharpened = _json(row["sharpened_data"]) or {}
    vulgar = _json(row["vulgarization_data"]) or {}

    domains = sharpened.get("domains") or []
    domain_a = domains[0] if len(domains) >= 1 else "—"
    domain_b = domains[1] if len(domains) >= 2 else "—"

    title_fr = (vulgar.get("title_fr") or "").strip() or sharpened.get("title", brief_id)

    # `concretely` is stored either as a dict (intro/phase1/phase2/phase3)
    # or, on a few rows, as a free-form string. Normalise to dict.
    concretely_raw = vulgar.get("concretely")
    if isinstance(concretely_raw, dict):
        concretely = concretely_raw
    elif isinstance(concretely_raw, str) and concretely_raw.strip():
        concretely = {"intro": concretely_raw.strip()}
    else:
        concretely = None

    return {
        "id": brief_id,
        "title_fr": title_fr,
        "domain_a": domain_a,
        "domain_b": domain_b,
        "panel_score": _fmt_score(row["panel_consensus_score"], decimals=2),
        "novelty_score": _fmt_score(row["novelty_score"], decimals=2),
        "imagine_that": (vulgar.get("imagine_that") or "").strip(),
        "hypothesis_in_brief": (vulgar.get("hypothesis_in_brief") or "").strip(),
        "why_it_matters": (vulgar.get("why_it_matters") or "").strip(),
        "concretely": concretely,
        "reviewers_say": (vulgar.get("reviewers_say") or "").strip(),
    }


def fetch_global_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return numbers used in the preamble (collisions, briefs, kill rate).

    Mirrors the aggregation used by the website (``spore-web/src/lib/db.ts
    :: getStats``): ``collisions`` = ``SUM(collisions_processed) FROM runs``,
    not ``COUNT(*) FROM hypotheses`` — the latter only counts collisions
    that survived the Gate stage and have nothing to do with the public
    "total collisions tried" counter.
    """
    total_collisions = conn.execute(
        "SELECT COALESCE(SUM(collisions_processed), 0) FROM runs"
    ).fetchone()[0]
    total_briefs = conn.execute(
        "SELECT COUNT(*) FROM briefs WHERE status = 'complete'"
    ).fetchone()[0]
    if total_collisions > 0:
        kill_rate = round(100 - (total_briefs / total_collisions) * 100, 1)
    else:
        kill_rate = 0.0
    # FR-style thousands separator (narrow no-break space U+202F).
    return {
        "total_collisions": f"{total_collisions:,}".replace(",", " "),
        "total_briefs": total_briefs,
        "kill_rate": str(kill_rate).replace(".", ","),
        "domains_count": "500",  # OpenAlex level-2 concept count, stable assumption
    }


def _json(blob: Any) -> Optional[dict[str, Any]]:
    if not blob:
        return None
    if isinstance(blob, (dict, list)):
        return blob
    try:
        return json.loads(blob)
    except (TypeError, json.JSONDecodeError):
        return None


def _fmt_score(value: Any, decimals: int = 2) -> str:
    try:
        return f"{float(value):.{decimals}f}".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


# ── Rendering ──────────────────────────────────────────────────────


def render_html(briefs: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("anthology.html.j2")

    now = datetime.now()
    months_fr = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    generation_date_fr = f"{now.day} {months_fr[now.month - 1]} {now.year}"

    return template.render(
        briefs=briefs,
        year=now.year,
        generation_date_fr=generation_date_fr,
        months_period="six",
        **stats,
    )


def render_pdf(html_str: str, output: Path) -> None:
    """Run WeasyPrint with TEMPLATE_DIR as base_url so anthology.css resolves."""
    output.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(TEMPLATE_DIR)).write_pdf(target=str(output))


# ── Orchestration ──────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output PDF path (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT.parent)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render HTML to stdout, skip PDF generation",
    )
    args = parser.parse_args()

    print(f"Loading {len(ANTHOLOGY_BRIEF_IDS)} selected briefs from {DB_PATH}")
    with open_readonly() as conn:
        briefs: list[dict[str, Any]] = []
        for brief_id in ANTHOLOGY_BRIEF_IDS:
            brief = fetch_brief(conn, brief_id)
            if brief is None:
                print(
                    f"  WARNING: {brief_id} not found or not complete — skipped",
                    file=sys.stderr,
                )
                continue
            briefs.append(brief)
            print(f"  ✓ {brief_id} — {brief['title_fr'][:60]}")

        if not briefs:
            print("ERR: no briefs available — aborting", file=sys.stderr)
            return 1

        stats = fetch_global_stats(conn)
        print(
            f"\nGlobal stats: {stats['total_collisions']} collisions / "
            f"{stats['total_briefs']} briefs / kill rate {stats['kill_rate']} %"
        )

    print("\nRendering HTML…")
    html_str = render_html(briefs, stats)
    print(f"  → {len(html_str):,} bytes of HTML")

    if args.dry_run:
        print("\n[--dry-run] HTML only, skipping PDF.")
        sys.stdout.write(html_str)
        return 0

    print("\nRendering PDF via WeasyPrint…")
    render_pdf(html_str, args.output)
    size_kb = args.output.stat().st_size / 1024
    print(f"  → {args.output}")
    print(f"  → {size_kb:.1f} KB")

    print(f"\nDone. {len(briefs)} briefs, {size_kb:.1f} KB output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
