"""Reprise : caviardage du vocabulaire proscrit dans les raisons libres du juge.

Le garde consigne, pour l'audit, les ``reasons`` en texte libre du juge LLM
dans ``guard_report_json.judge.judge_reasons``. Ce rapport est du texte écrit
par SPORE (D-017, niveau 1, tolérance nulle) : une raison qui porte un terme
proscrit n'a pas sa place en base, et M9 (``scripts/v2/checks/m9_db.py``) la
relève sur la colonne.

Les **codes** de raison (``reasons`` de premier niveau) posent le même
problème par un autre chemin : le front les affiche tels quels dans les
coulisses d'une idée dont aucune tentative n'est publiée, et un code de la
forme ``mechanical:us_spelling:sulfur`` sert donc le mot en HTML — deux-points
n'est pas un caractère de mot, la règle de M9 y lit une occurrence.

``narrative.guard`` et ``narrative.checks`` appliquent désormais les deux
traitements avant d'écrire : filtre mécanique sur les raisons libres, code
technique (nombre de formes, identifiant ``vocab_<langue>_<règle>``) sur les
raisons de premier niveau. Ce script applique les mêmes traitements, mot pour
mot, aux lignes **déjà** écrites d'une copie de base.

Propriétés :

* **idempotent** : une raison libre déjà caviardée est un objet JSON, pas une
  chaîne, et un code déjà technique est reconnu comme tel ; un second passage
  ne change rien ;
* **borné au rapport** : seule la colonne ``guard_report_json`` est réécrite,
  et dans ce rapport seules les raisons (``judge.judge_reasons`` et les codes
  de ``reasons``). Le texte d'un récit (``body_md``, ``title``,
  ``mechanism``…) n'est jamais lu pour écriture, ni son empreinte
  ``body_sha256`` ;
* **minimal** : une ligne dont aucune raison ne bouge n'est pas réécrite (ses
  octets restent identiques), et une ligne réécrite ne voit changer que la
  colonne ``guard_report_json`` — ``updated_at`` compris, que le front lit
  comme la date de publication ;
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

from narrative.checks import RULE_ID_PREFIX, redact_free_text, rule_id
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


def rewrite_code(code: str, lang: str) -> str:
    """Code de raison rendu inoffensif pour un contrôle lexical.

    Deux familles recopiaient un terme lisible derrière un deux-points, que le
    front sert tel quel : ``mechanical:us_spelling:<formes>`` (mots du récit,
    remplacés par leur nombre) et ``mechanical:proscribed_vocab:<catégories>``
    (remplacées par leur identifiant technique ``vocab_<langue>_<règle>``, dont
    le souligné interdit toute lecture en occurrence). Les autres codes sont
    déjà des codes.

    Args:
        code: Code de raison stocké.
        lang: Langue de la ligne (``fr`` ou ``en``).

    Returns:
        Le code, réécrit si besoin.
    """
    head, sep, payload = code.partition(":")
    if head != "mechanical" or not sep:
        return code
    family, sep, payload = payload.partition(":")
    if not sep or not payload:
        return code
    if family == "us_spelling":
        if payload.isdigit():
            return code
        return f"mechanical:us_spelling:{len(payload.split(','))}"
    if family == "proscribed_vocab":
        names = [
            name if name.startswith(f"{RULE_ID_PREFIX}_") else rule_id(lang or "fr", name)
            for name in payload.split(",")
        ]
        return "mechanical:proscribed_vocab:" + ",".join(names)
    return code


def sanitise_codes(codes: Sequence[Any], lang: str) -> tuple[list[Any], int]:
    """Passe les codes de raison d'un rapport à ``rewrite_code``.

    Args:
        codes: Contenu de ``reasons``.
        lang: Langue de la ligne.

    Returns:
        ``(codes, nombre de codes réécrits)``.
    """
    out: list[Any] = []
    rewritten = 0
    for item in codes:
        if not isinstance(item, str):
            out.append(item)
            continue
        new = rewrite_code(item, lang)
        rewritten += int(new != item)
        out.append(new)
    return out, rewritten


def sanitise_report(report: Any, lang: str = "") -> tuple[dict[str, Any] | None, list[str], int]:
    """Rapport de garde corrigé, ou ``None`` s'il n'y avait rien à corriger.

    Tout le reste du rapport (``version``, ``lang``, ``mechanical``,
    ``decision``, ``jury``, et les autres champs de ``judge``) est laissé
    intact, à sa place et dans son ordre ; seules les raisons bougent.

    Args:
        report: Rapport désérialisé.
        lang: Langue de la ligne (défaut : celle du rapport).

    Returns:
        ``(rapport corrigé | None, règles déclenchées, codes réécrits)``.
    """
    if not isinstance(report, dict):
        return None, [], 0
    lang = lang or (report.get("lang") if isinstance(report.get("lang"), str) else "")
    rules: list[str] = []
    rewritten = 0
    judge = report.get("judge")
    if isinstance(judge, dict) and isinstance(judge.get("judge_reasons"), list):
        cleaned, rules = sanitise_reasons(judge["judge_reasons"])
        if rules:
            judge["judge_reasons"] = cleaned
    for holder in (report, judge):
        if isinstance(holder, dict) and isinstance(holder.get("reasons"), list):
            codes, count = sanitise_codes(holder["reasons"], lang)
            if count:
                holder["reasons"] = codes
                rewritten += count
    if not rules and not rewritten:
        return None, [], 0
    return report, rules, rewritten


def run(db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Applique le filtre à toutes les lignes de ``v2_stories``.

    Args:
        db_path: Copie de base de travail.
        dry_run: Ne rien écrire.

    Returns:
        Résumé JSON (lignes lues, lignes changées, raisons caviardées, codes
        réécrits, règles).
    """
    changed_rows: list[dict[str, Any]] = []
    rule_counts: Counter[str] = Counter()
    codes_rewritten = 0
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
            fixed, rules, codes = sanitise_report(report, str(row["lang"] or ""))
            if fixed is None:
                continue
            rule_counts.update(rules)
            codes_rewritten += codes
            changed_rows.append(
                {
                    "story_id": int(row["id"]),
                    "brief_id": row["brief_id"],
                    "lang": row["lang"],
                    "attempt": int(row["attempt"]),
                    "status": row["status"],
                    "reasons_redacted": len(rules),
                    "codes_rewritten": codes,
                    "rules": sorted(set(rules)),
                }
            )
            if not dry_run:
                # Une seule colonne est écrite, en SQL explicite : ni le texte
                # du récit, ni son empreinte, ni ``updated_at`` — la ligne n'a
                # pas été republiée, et le front lit cette date comme la date
                # de publication (sitemap et chronologie des coulisses).
                with conn:
                    conn.execute(
                        "UPDATE v2_stories SET guard_report_json = ? WHERE id = ?",
                        (json.dumps(fixed, ensure_ascii=False), int(row["id"])),
                    )
            logger.info(
                "sanitize_report_rewritten",
                story_id=int(row["id"]),
                brief_id=row["brief_id"],
                lang=row["lang"],
                attempt=int(row["attempt"]),
                rules=sorted(set(rules)),
                codes_rewritten=codes,
                written=not dry_run,
            )
    return {
        "db": str(db_path),
        "dry_run": dry_run,
        "rows": len(rows),
        "rows_changed": len(changed_rows),
        "reasons_redacted": int(sum(rule_counts.values())),
        "codes_rewritten": codes_rewritten,
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
