"""Offline editorial review of FR rows containing "découverte" in the SPORE DB.

DELIVERABLE: this script is part of sprint N1.1 (rename "découverte" →
brief / hypothèse / piste). It produces a CSV of every row whose FR-text
columns contain "découv" so a human reviewer can decide, line by line,
whether to rewrite the wording (rare — see the project memory rule
``project_spore_decouverte_taxonomy``: most occurrences are correct French
scientific vocabulary and must NOT be modified).

LANCEMENT HUMAIN OFFLINE UNIQUEMENT — ne pas exécuter automatiquement.
The script does NOT modify the database. It opens SQLite read-only and
writes a CSV to /tmp/db_fr_review.csv (configurable via --output).

Usage:
    .venv/bin/python scripts/audit_n1_1_fr_db_review.py
    .venv/bin/python scripts/audit_n1_1_fr_db_review.py --dry-run
    .venv/bin/python scripts/audit_n1_1_fr_db_review.py --output /tmp/foo.csv

Périmètre scanné (colonnes FR internes, candidates au backfill manuel) :
    - briefs.body_markdown
    - briefs.vulgarization_data
    - briefs.panel_data
    - hypotheses.impact_analysis_json
    - hypotheses.auto_feedback_json

Périmètre EXCLU (contenu EN externe — citations bibliographiques
immuables, NE PAS ÉTENDRE le scope à ces colonnes) :
    - briefs.grounding_data
    - hypotheses.collision_json
    - hypotheses.bridge_json
    - semantic_scholar_cache.response_json
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "spore.db"
DEFAULT_OUTPUT = Path("/tmp/db_fr_review.csv")

CONTEXT_RADIUS = 100  # chars before/after the first match in each row

# (table, column, id_column) — id_column is the row's primary key for traceability
SCANNED_COLUMNS: list[tuple[str, str, str]] = [
    ("briefs", "body_markdown", "id"),
    ("briefs", "vulgarization_data", "id"),
    ("briefs", "panel_data", "id"),
    ("hypotheses", "impact_analysis_json", "id"),
    ("hypotheses", "auto_feedback_json", "id"),
]


def find_first_match(text: str) -> int | None:
    """Return the lowest index of any case-variant of "découv" in *text*.

    Matches both "découv" (lowercase) and "Découv" (capitalised). Other
    forms (DECOUV, DÉCOUVERT) are looked up as well so a row with "Décou"
    in its title still surfaces.
    """
    if not text:
        return None
    candidates = ("découv", "Découv", "DÉCOUV", "DECOUV")
    indices = [text.find(c) for c in candidates]
    indices = [i for i in indices if i >= 0]
    return min(indices) if indices else None


def count_matches(text: str) -> int:
    """Total number of case-variant matches of "découv" in *text*."""
    if not text:
        return 0
    lowered = text.lower()
    # casefold-style count without LOCALE specials — accent is part of the
    # literal so lowering the text and counting "découv" is enough.
    return lowered.count("découv")


def context_snippet(text: str, idx: int, radius: int = CONTEXT_RADIUS) -> str:
    """Return a ±radius window around *idx* with newlines flattened."""
    if idx is None or not text:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + radius)
    snippet = text[start:end].replace("\n", " ").replace("\r", " ")
    snippet = " ".join(snippet.split())  # collapse runs of whitespace
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def scan_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    id_column: str,
) -> list[dict[str, str | int]]:
    """Return one record per row whose *column* contains a découv variant."""
    sql = (
        f"SELECT {id_column}, {column} FROM {table} "
        f"WHERE {column} LIKE '%découv%' OR {column} LIKE '%Découv%' "
        f"   OR {column} LIKE '%DÉCOUV%' OR {column} LIKE '%DECOUV%'"
    )
    cur = conn.execute(sql)
    rows = []
    for row_id, content in cur.fetchall():
        if not content:
            continue
        first_idx = find_first_match(content)
        if first_idx is None:
            continue
        rows.append(
            {
                "table": table,
                "row_id": row_id,
                "column": column,
                "match_count_in_row": count_matches(content),
                "context_snippet": context_snippet(content, first_idx),
                "recommended_action": "TBD",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline editorial review of FR rows containing 'découverte' "
            "in the SPORE SQLite DB. Read-only on the DB. Writes a CSV."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to spore.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"CSV output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary stats per column, do NOT write the CSV.",
    )
    args = parser.parse_args()

    db_path: Path = args.db
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    # Read-only connection. The URI form + mode=ro guarantees no writes.
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        all_rows: list[dict[str, str | int]] = []
        for table, column, id_column in SCANNED_COLUMNS:
            rows = scan_column(conn, table, column, id_column)
            print(
                f"[{table}.{column}] matched {len(rows)} row(s)",
                file=sys.stderr,
            )
            all_rows.extend(rows)
    finally:
        conn.close()

    print(
        f"\nTotal: {len(all_rows)} row(s) across "
        f"{len(SCANNED_COLUMNS)} column(s) in the FR-internal scope.",
        file=sys.stderr,
    )
    print(
        "EN-external columns (grounding_data, collision_json, bridge_json, "
        "semantic_scholar_cache) were NOT scanned — bibliographic "
        "citations are immutable per the project taxonomy memory.",
        file=sys.stderr,
    )

    if args.dry_run:
        print(
            "\n--dry-run: CSV NOT written. Re-run without --dry-run to "
            f"persist to {args.output}.",
            file=sys.stderr,
        )
        return 0

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "table",
        "row_id",
        "column",
        "match_count_in_row",
        "context_snippet",
        "recommended_action",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nWrote {len(all_rows)} row(s) to {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
