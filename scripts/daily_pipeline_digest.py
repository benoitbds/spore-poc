#!/usr/bin/env python3
"""Digest quotidien des échecs du pipeline post-fire (S3/C19).

Un échec journalise en ``error`` dans ``/var/log/spore.log``, que personne ne
lit — c'est pour ça que trois semaines sont passées entre les échecs de
vulgarisation de SPR-2026-4469 et leur découverte. Ce script porte l'échec
jusqu'à la boîte mail, sans système d'alerting à maintenir.

**Un mail par jour au plus. Aucun si tout va bien.**

## La ligne de partage : alerte ou compteur

Alerter tout ferait presque un mail par jour rien qu'avec les 26
``reviewer_parse_failed`` mensuels — un volume qui garantit qu'on cesse de
lire. Or ces 26-là sont *gérés* : le repli du panel met ``confidence=0.0``,
ce qui sort l'avis du consensus pondéré, par conception. Preuve empirique :
sur les 14 briefs portant une review non parsée, **13 sont ``rejected``**. Le
mécanisme a fonctionné.

D'où la règle appliquée ici :

    Alerte ce qui a CHANGÉ L'ISSUE d'un run.
    Compte ce qui a été ABSORBÉ par un repli de conception.

Un compteur qui dérive redevient une alerte : au-delà de
``DRIFT_THRESHOLD`` de la population du jour, ce n'est plus du bruit absorbé,
c'est une panne de format.

## Usage

    python -m scripts.daily_pipeline_digest [--dry-run] [--date YYYY-MM-DD]

Cron, après le run L0 de 04:15 UTC :

    30 5 * * *  \
                .venv/bin/python -m scripts.daily_pipeline_digest

Le script n'écrit rien : ni base, ni fichier d'état. Il lit le log et la base
en lecture seule, et envoie un mail ou non.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notify import PROJECT_ROOT, iter_json_events, send_email  # noqa: E402

L0_LOG = Path("/var/log/spore.log")

# ── Classement des événements ────────────────────────────────────────────
#
# ALERTE : l'issue du run a changé — un brief n'existe pas, ou existe
# incomplet. Personne d'autre ne le rattrapera.
ALERT_EVENTS: dict[str, str] = {
    "analysis_parse_failed": "grounding : analyse illisible, run avorté (D2)",
    "sharpening_parse_failed": "sharpening illisible, run avorté",
    "protocol_parse_failed": "protocole illisible, run avorté",
    "vulgarization_failed": "vulgarisation en échec, brief non promu (C18)",
    "brief_validation_failed": "brief bloqué en 'pending' (C18)",
    "brief_promotion_failed": "promotion en base impossible (C18)",
    "brief_db_save_failed": "brief généré mais non persisté",
    "custom_brief_not_promoted": "RUN PAYANT : brief non promu (C18)",
    "custom_post_fire_failed": "RUN PAYANT : post-fire en échec",
}

# COMPTEUR : absorbé par un repli de conception. On mesure, on n'alerte pas —
# sauf dérive.
COUNTER_EVENTS: dict[str, str] = {
    "reviewer_parse_failed": "review non parsée (repli confidence=0.0)",
    "json_parse_repaired": "JSON réparé par le parseur partagé (C17b)",
    "json_parse_retried": "JSON obtenu au second appel (C17b)",
    "json_parse_repair_insufficient": "réparation insuffisante (C17b)",
    "post_fire_failed": "post-fire abandonné (la cause a déjà alerté)",
    "query_extraction_failed": "requêtes de recherche en repli",
    "no_papers_found": "aucun article trouvé (donnée, pas échec)",
}

# Au-delà de cette fraction de la population du jour, un compteur redevient
# une alerte. 10 % : en dessous, c'est le bruit que les replis absorbent ;
# au-dessus, c'est un format qui a changé.
DRIFT_THRESHOLD = 0.10

# Un brief encore 'pending' au-delà de ce délai n'est plus en cours : un run
# post-fire complet dure quelques minutes.
PENDING_STALE_HOURS = 2


def _anchor_date(events: list[dict[str, Any]], forced: str | None) -> str | None:
    """Jour à analyser : celui fourni, sinon le dernier présent dans le log.

    L'ancrage sur le log plutôt que sur ``date.today()`` évite de rendre un
    digest vide quand le cron a pris du retard ou que le run a échoué avant
    d'écrire quoi que ce soit.
    """
    if forced:
        return forced
    dates = {e.get("timestamp", "")[:10] for e in events}
    dates.discard("")
    return max(dates) if dates else None


def collect(log_path: Path, forced_date: str | None) -> dict[str, Any]:
    """Compte les événements du jour ancré, par classe."""
    events = list(iter_json_events(log_path))
    anchor = _anchor_date(events, forced_date)
    if anchor is None:
        return {"anchor": None, "alerts": [], "counters": Counter(), "population": 0}

    day = [e for e in events if e.get("timestamp", "")[:10] == anchor]

    alerts: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    for evt in day:
        name = evt.get("event", "")
        if name in ALERT_EVENTS:
            alerts.append(evt)
        elif name in COUNTER_EVENTS:
            counters[name] += 1

    # Population du jour : ce à quoi rapporter un compteur pour juger d'une
    # dérive. Le nombre de reviews lancées est le dénominateur naturel.
    population = sum(1 for e in day if e.get("event") == "running_reviewer")

    return {"anchor": anchor, "alerts": alerts, "counters": counters,
            "population": population, "day_events": len(day)}


def stale_pending_briefs(db_path: Path, hours: int = PENDING_STALE_HOURS) -> list[tuple[str, str]]:
    """Briefs restés en 'pending' au-delà du délai (C18 Q3).

    Lecture seule stricte : connexion ``mode=ro``, aucune écriture possible.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = conn.execute(
            "SELECT id, created_at FROM briefs "
            "WHERE status = 'pending' AND created_at < ? ORDER BY created_at",
            (cutoff,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def drifting_counters(counters: Counter, population: int) -> list[tuple[str, int, float]]:
    """Compteurs dont la fréquence dépasse le seuil de dérive."""
    if population <= 0:
        return []
    out = []
    for name, n in counters.items():
        # post_fire_failed et no_papers_found ne se rapportent pas au nombre
        # de reviews : les exclure du test de dérive plutôt que de comparer
        # des grandeurs sans rapport.
        if name in ("post_fire_failed", "no_papers_found"):
            continue
        ratio = n / population
        if ratio > DRIFT_THRESHOLD:
            out.append((name, n, ratio))
    return sorted(out, key=lambda t: -t[2])


def _evt_line(evt: dict[str, Any]) -> str:
    name = evt.get("event", "?")
    label = ALERT_EVENTS.get(name, name)
    subject = (
        evt.get("brief_id")
        or evt.get("hypothesis_id")
        or evt.get("request_id")
        or "—"
    )
    detail = evt.get("error") or evt.get("missing_fields") or evt.get("reason") or ""
    line = f"  • {label}\n    sujet : {subject}"
    if detail:
        line += f"\n    détail : {str(detail)[:200]}"
    return line


def format_digest(
    data: dict[str, Any],
    stale: list[tuple[str, str]],
    drifts: list[tuple[str, int, float]],
) -> tuple[str, str]:
    anchor = data["anchor"]
    alerts = data["alerts"]
    counters = data["counters"]

    n = len(alerts) + len(stale) + len(drifts)
    subject = f"[SPORE] {n} chose(s) à regarder — pipeline du {anchor}"

    parts = [f"Digest pipeline post-fire — {anchor}", ""]

    if alerts:
        parts += [f"ÉCHECS ({len(alerts)}) — l'issue d'un run a changé", ""]
        parts += [_evt_line(e) for e in alerts]
        parts.append("")

    if stale:
        parts += [
            f"BRIEFS BLOQUÉS EN 'pending' ({len(stale)}) — "
            f"plus de {PENDING_STALE_HOURS} h, invisibles du site",
            "",
        ]
        parts += [f"  • {bid}  (créé {created})" for bid, created in stale]
        parts.append("")

    if drifts:
        parts += [
            f"COMPTEURS EN DÉRIVE ({len(drifts)}) — au-delà de "
            f"{DRIFT_THRESHOLD:.0%}, ce n'est plus du bruit absorbé",
            "",
        ]
        for name, count, ratio in drifts:
            parts.append(
                f"  • {COUNTER_EVENTS.get(name, name)} : {count} "
                f"({ratio:.0%} de {data['population']} reviews)"
            )
        parts.append("")

    if counters:
        parts += ["Absorbés (pour mémoire, aucune action) :", ""]
        for name, count in counters.most_common():
            parts.append(f"  {count:>4}  {COUNTER_EVENTS.get(name, name)}")
        parts.append("")

    parts += [
        "—",
        "scripts/daily_pipeline_digest.py — aucun mail n'est envoyé les jours",
        "sans échec, sans brief bloqué et sans dérive de compteur.",
    ]
    return subject, "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Afficher le digest au lieu de l'envoyer. N'envoie rien.",
    )
    parser.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD",
        help="Jour à analyser (défaut : le dernier présent dans le log).",
    )
    parser.add_argument(
        "--log", default=str(L0_LOG), help=f"Chemin du log (défaut {L0_LOG}).",
    )
    parser.add_argument(
        "--always", action="store_true",
        help="Rendre le digest même si rien n'est à signaler (diagnostic).",
    )
    args = parser.parse_args()

    data = collect(Path(args.log), args.date)
    if data["anchor"] is None:
        print("daily_pipeline_digest: aucun événement daté dans le log", file=sys.stderr)
        return 0

    db_path = PROJECT_ROOT / "data" / "spore.db"
    stale = stale_pending_briefs(db_path)
    drifts = drifting_counters(data["counters"], data["population"])

    if not (data["alerts"] or stale or drifts) and not args.always:
        # Le cas nominal : rien à dire, donc rien envoyé.
        return 0

    subject, body = format_digest(data, stale, drifts)

    if args.dry_run or args.always:
        print(f"Subject: {subject}")
        print()
        print(body)
        return 0

    try:
        send_email(subject, body)
    except Exception as exc:  # send_email avale déjà les erreurs SMTP
        print(f"daily_pipeline_digest: erreur inattendue : {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
