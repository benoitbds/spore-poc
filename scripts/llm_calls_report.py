#!/usr/bin/env python3
"""Réédite le tableau A.4 du diagnostic S11 à partir de mesures réelles.

Le tableau A.4 de ``docs/S11A_diagnostic_troncature.md`` estimait les
``output_tokens`` par nœud à partir de la taille des artefacts stockés, faute
de mesure. Depuis S11/B.1, chaque appel LLM laisse une ligne dans ``llm_calls``
avec son nœud, son plafond, ses jetons produits et son motif de fin.

Ce script ne fait que lire : connexion en ``mode=ro``, aucune écriture.

Usage:
    .venv/bin/python -m scripts.llm_calls_report
    .venv/bin/python -m scripts.llm_calls_report --days 7
    .venv/bin/python -m scripts.llm_calls_report --since 2026-09-17 --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "spore.db"


def percentile(values: list[int], q: float) -> int:
    """Percentile à interpolation linéaire.

    Args:
        values: Valeurs, ordre indifférent. Non vide.
        q: Quantile dans [0, 1].

    Returns:
        Percentile arrondi à l'entier.
    """
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low))


def collect(db_path: Path, since: str) -> dict[str, Any]:
    """Lit les appels postérieurs à une date et les agrège par nœud.

    Args:
        db_path: Chemin de la base.
        since: Date ISO (``YYYY-MM-DD``) à partir de laquelle compter.

    Returns:
        Dictionnaire ``{"since": …, "nodes": [...], "finish_reasons": {...}}``.

    Raises:
        SystemExit: La base ou la table est absente.
    """
    if not db_path.exists():
        raise SystemExit(f"base introuvable : {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT node, max_tokens, output_tokens, finish_reason, attempt, "
            "provider, model, response_model FROM llm_calls "
            "WHERE date(created_at) >= ?",
            (since,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise SystemExit(f"table llm_calls illisible ({exc}) — B.1 déployé ?") from exc
    finally:
        conn.close()

    by_node: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_node.setdefault(row[0], []).append(row)

    nodes = []
    for node, calls in sorted(by_node.items()):
        produced = [c[2] for c in calls if c[2] is not None]
        ceilings = {c[1] for c in calls}
        reasons: dict[str, int] = {}
        for call in calls:
            reasons[call[3]] = reasons.get(call[3], 0) + 1
        at_ceiling = sum(1 for c in calls if c[2] is not None and c[2] >= c[1])
        nodes.append(
            {
                "node": node,
                "calls": len(calls),
                "max_tokens": sorted(ceilings),
                "p50": percentile(produced, 0.50) if produced else None,
                "p95": percentile(produced, 0.95) if produced else None,
                "p99": percentile(produced, 0.99) if produced else None,
                "max": max(produced) if produced else None,
                "at_ceiling": at_ceiling,
                "retries": sum(1 for c in calls if c[4] > 1),
                "finish_reasons": reasons,
            }
        )

    overall: dict[str, int] = {}
    for row in rows:
        overall[row[3]] = overall.get(row[3], 0) + 1

    served = sorted({(r[6], r[7]) for r in rows})
    return {"since": since, "total": len(rows), "nodes": nodes,
            "finish_reasons": overall, "models": served}


def render(data: dict[str, Any]) -> str:
    """Met en tableau le résultat de ``collect``.

    Args:
        data: Sortie de ``collect``.

    Returns:
        Texte prêt à coller dans un document.
    """
    lines = [
        f"Appels LLM depuis le {data['since']} — {data['total']} appels mesurés",
        "",
        "| Nœud | Appels | Plafond | p50 | p95 | p99 | max | au plafond | rejeux |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for node in data["nodes"]:
        ceilings = "/".join(str(c) for c in node["max_tokens"])
        lines.append(
            f"| `{node['node']}` | {node['calls']} | {ceilings} | "
            f"{node['p50']} | {node['p95']} | {node['p99']} | {node['max']} | "
            f"{node['at_ceiling']} | {node['retries']} |"
        )
    lines += ["", "Motifs de fin, tous nœuds confondus :", ""]
    for reason, count in sorted(data["finish_reasons"].items(), key=lambda kv: -kv[1]):
        share = count / data["total"] if data["total"] else 0
        lines.append(f"  {count:>6}  {reason}  ({share:.1%})")
    lines += ["", "Modèles demandés → servis :", ""]
    for requested, served in data["models"]:
        lines.append(f"  {requested} → {served}")
    if not data["total"]:
        lines.append("")
        lines.append("Aucun appel sur la période : le pipeline n'a pas tourné depuis B.1.")
    return "\n".join(lines)


def main() -> int:
    """Point d'entrée.

    Returns:
        Code de sortie du processus.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=7, help="Fenêtre en jours (défaut 7).")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                        help="Date de début ; prioritaire sur --days.")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"Base (défaut {DB_PATH}).")
    args = parser.parse_args()

    since = args.since or (
        datetime.now(timezone.utc) - timedelta(days=args.days)
    ).strftime("%Y-%m-%d")
    data = collect(Path(args.db), since)
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
