"""Reprise : caviardage du vocabulaire proscrit dans les raisons libres du juge.

Le garde consigne, pour l'audit, les ``reasons`` en texte libre du juge LLM
dans ``guard_report_json.judge.judge_reasons``. Ce rapport est du texte écrit
par SPORE (D-017, niveau 1, tolérance nulle) : une raison qui porte un terme
proscrit n'a pas sa place en base, et M9 (``scripts/v2/checks/m9_db.py``) la
relève sur la colonne.

``narrative.guard`` applique désormais le filtre mécanique
(``narrative.checks.redact_free_text``) avant d'écrire. Ce script applique le
même filtre, mot pour mot, aux lignes **déjà** écrites d'une copie de base.

Propriétés :

* **idempotent** : une raison déjà caviardée est un objet JSON, pas une
  chaîne ; le script ne touche qu'aux chaînes, donc un second passage ne
  change rien ;
* **borné au rapport** : seule la colonne ``guard_report_json`` est réécrite,
  et dans ce rapport seules les entrées de ``judge.judge_reasons``. Le texte
  d'un récit (``body_md``, ``title``, ``mechanism``…) n'est jamais lu pour
  écriture, ni son empreinte ``body_sha256`` ;
* **minimal** : une ligne dont aucune raison ne bouge n'est pas réécrite (ses
  octets restent identiques) ;
* **sûr** : refuse une base de production (``narrative.safety``) hors
  ``SPORE_V2_PRODUCTION=1`` ; ``--dry-run`` ouvre la base en lecture seule et
  n'écrit rien.

Usage (depuis la racine du clone) ::

    PYTHONPATH=. python -m scripts.v2.sanitize_guard_reports \\
        --db ~/Projects/spore-v2-data/staging.db --dry-run

Sortie : un résumé JSON sur stdout ; journaux structurés sur stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import structlog

from narrative.checks import redact_free_text
from narrative.guard import JUDGE_REASON_MAX_CHARS
from narrative.safety import UnsafePathError, assert_safe_write_path
from scripts.v2.backfill_narrative import _configure_logging
from storage import narrative_db

logger = structlog.get_logger("scripts.v2.sanitize_guard_reports")


def build_parser() -> argparse.ArgumentParser:
    """Arguments de la ligne de commande.

    Returns:
        Analyseur.
    """
    parser = argparse.ArgumentParser(
        prog="scripts.v2.sanitize_guard_reports",
        description="Caviarde le vocabulaire proscrit des raisons libres du juge (D-017, MUST 9).",
    )
    parser.add_argument("--db", required=True, type=Path, help="copie de base de travail (obligatoire)")
    parser.add_argument("--dry-run", action="store_true", help="lecture seule : compte sans écrire")
    return parser


def sanitise_reasons(reasons: Sequence[Any]) -> tuple[list[Any], list[str]]:
    """Passe les raisons libres d'un rapport au filtre de vocabulaire.

    Une entrée qui n'est pas une chaîne (raison déjà caviardée, écrite sous
    la forme d'un objet) est recopiée telle quelle : c'est ce qui rend le
    script rejouable.

    Args:
        reasons: Contenu de ``judge.judge_reasons``.

    Returns:
        ``(raisons, règles déclenchées)`` ; la liste des règles est vide quand
        rien n'a changé.
    """
    out: list[Any] = []
    rules: list[str] = []
    for index, item in enumerate(reasons):
        if not isinstance(item, str):
            out.append(item)
            continue
        redacted = redact_free_text(item[:JUDGE_REASON_MAX_CHARS], index)
        if isinstance(redacted, dict):
            rules.extend(str(redacted["rule"]).split(","))
        out.append(redacted)
    return out, rules


def sanitise_report(report: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Rapport de garde corrigé, ou ``None`` s'il n'y avait rien à corriger.

    Tout le reste du rapport (``version``, ``lang``, ``mechanical``,
    ``decision``, ``reasons``, ``jury``, et les autres champs de ``judge``)
    est laissé intact, à sa place et dans son ordre.

    Args:
        report: Rapport désérialisé.

    Returns:
        ``(rapport corrigé | None, règles déclenchées)``.
    """
    if not isinstance(report, dict):
        return None, []
    judge = report.get("judge")
    if not isinstance(judge, dict) or not isinstance(judge.get("judge_reasons"), list):
        return None, []
    cleaned, rules = sanitise_reasons(judge["judge_reasons"])
    if not rules:
        return None, []
    judge["judge_reasons"] = cleaned
    return report, rules


def run(db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Applique le filtre à toutes les lignes de ``v2_stories``.

    Args:
        db_path: Copie de base de travail.
        dry_run: Ne rien écrire.

    Returns:
        Résumé JSON (lignes lues, lignes changées, raisons caviardées, règles).
    """
    changed_rows: list[dict[str, Any]] = []
    rule_counts: Counter[str] = Counter()
    with narrative_db.connect(db_path, readonly=dry_run) as conn:
        if not narrative_db.table_exists(conn, "v2_stories"):
            return {"error": "table v2_stories absente", "rows": 0, "rows_changed": 0}
        rows = conn.execute(
            "SELECT id, brief_id, lang, attempt, status, guard_report_json FROM v2_stories "
            "WHERE guard_report_json IS NOT NULL ORDER BY id"
        ).fetchall()
        for row in rows:
            try:
                report = json.loads(row["guard_report_json"])
            except ValueError as exc:
                logger.warning("sanitize_report_unreadable", story_id=int(row["id"]), error=str(exc)[:200])
                continue
            fixed, rules = sanitise_report(report)
            if fixed is None:
                continue
            rule_counts.update(rules)
            changed_rows.append(
                {
                    "story_id": int(row["id"]),
                    "brief_id": row["brief_id"],
                    "lang": row["lang"],
                    "attempt": int(row["attempt"]),
                    "status": row["status"],
                    "rules": sorted(set(rules)),
                }
            )
            if not dry_run:
                # Seule la colonne du rapport est écrite : le texte du récit
                # et son empreinte ne sont pas touchés.
                narrative_db.update_story(
                    conn,
                    int(row["id"]),
                    guard_report_json=json.dumps(fixed, ensure_ascii=False),
                )
            logger.info(
                "sanitize_report_redacted",
                story_id=int(row["id"]),
                brief_id=row["brief_id"],
                lang=row["lang"],
                attempt=int(row["attempt"]),
                rules=sorted(set(rules)),
                written=not dry_run,
            )
    return {
        "db": str(db_path),
        "dry_run": dry_run,
        "rows": len(rows),
        "rows_changed": len(changed_rows),
        "reasons_redacted": int(sum(rule_counts.values())),
        "rules": dict(sorted(rule_counts.items())),
        "changed": changed_rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée.

    Args:
        argv: Arguments (``sys.argv`` par défaut).

    Returns:
        Code de sortie.
    """
    _configure_logging()
    args = build_parser().parse_args(argv)
    try:
        db_path = assert_safe_write_path(args.db, what="db")
    except UnsafePathError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    if not db_path.is_file():
        print(json.dumps({"error": f"base introuvable : {db_path}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    summary = run(db_path, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
