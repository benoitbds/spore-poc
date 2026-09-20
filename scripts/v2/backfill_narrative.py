"""Backfill de la couche narrative v2 sur une copie de base.

Pour chaque brief publié complet (non-stub) : récit FR, garde, version
anglaise, garde EN — par le même code que le pipeline
(``narrative.graph.run_narrative_layer``). Pour tous les briefs publiés :
lien brief ↔ hypothèse (``fk`` puis ``text_match`` par les sidecars de
développement), thèmes, signalements de la vulgarisation
(``explainer_flags``, D-017, sans LLM, ``--no-stories`` compris), puis
maillage des voisines (une fois, à la fin).

Propriétés :

* **idempotent et reprenable** : un récit publié n'est jamais refait ; les
  tentatives sont comptées en base (trois au plus par brief et par langue) ;
  un lien existant n'est pas écrasé ; thèmes, signalements et voisines sont
  recalculés (un signalement relu par l'opérateur garde son statut) ;
* **limité en débit** (``--rate-limit`` secondes entre deux briefs) ;
* **plafonné** : ``--max-usd`` (par défaut 10 USD moins la dépense déjà
  enregistrée dans les bases listées par ``docs/v2/evidence/spend.json``) ;
  le script s'arrête avant de dépasser le plafond, sur une estimation
  prudente par brief ;
* **sûr** : refuse une base ou un fichier de sortie situés en production
  (``narrative.safety``) hors ``SPORE_V2_PRODUCTION=1`` ; ``--dry-run``
  ouvre la base en lecture seule, n'écrit rien, ne charge aucune clé et
  n'appelle aucun LLM.

Usage (depuis la racine du clone, interpréteur de production en lecture) ::

    PYTHONPATH=. python -m scripts.v2.backfill_narrative \\
        --db ~/Projects/spore-v2-data/dev-pipe.db --dry-run

Sortie : un résumé JSON sur stdout ; journaux structurés sur stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import structlog

from narrative.config import NarrativeConfig, load_config
from narrative.linking import SummaryIndex, resolve_backfill_link
from narrative.safety import (
    assert_safe_write_path,
    is_production_mode,
    production_root_of,
    resolve_path,
)
from storage import narrative_db

#: Plafond de dépense LLM du run v2 (DATA_CONTRACT, v2_llm_costs).
RUN_CAP_USD = 10.0


def _configure_logging() -> None:
    """Journaux JSON sur stderr, sans passer par ``get_settings``.

    ``logging_config.setup_logging`` exige ``DEEPSEEK_API_KEY`` ; un
    ``--dry-run`` n'en a pas besoin et ne doit rien charger.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


logger = structlog.get_logger("scripts.v2.backfill_narrative")


def build_parser() -> argparse.ArgumentParser:
    """Arguments de la ligne de commande.

    Returns:
        Analyseur.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", required=True, type=Path, help="copie de base de travail (obligatoire)")
    parser.add_argument("--dry-run", action="store_true", help="lecture seule : plan et estimation")
    parser.add_argument("--limit", type=int, default=None, help="nombre maximal de briefs à traiter")
    parser.add_argument("--brief", action="append", default=[], help="brief à traiter (répétable)")
    parser.add_argument("--run-label", default="backfill", help="étiquette du registre de coût")
    parser.add_argument("--max-usd", type=float, default=None, help="plafond de ce passage (USD)")
    parser.add_argument("--rate-limit", type=float, default=None, help="pause entre deux briefs (s)")
    parser.add_argument(
        "--sidecars-dir",
        type=Path,
        default=None,
        help="sidecars JSON des briefs (lecture seule) ; défaut : <SPORE_OUTPUT_DIR ou clone>/outputs/briefs",
    )
    parser.add_argument("--spend-json", type=Path, default=None, help="consolidation de la dépense du run")
    parser.add_argument(
        "--no-stories", action="store_true", help="liens, thèmes, signalements et voisines seulement"
    )
    parser.add_argument("--no-spend-update", action="store_true", help="ne pas réécrire spend.json")
    parser.add_argument(
        "--load-llm-keys",
        action="store_true",
        help="charger DEEPSEEK_API_KEY / ANTHROPIC_API_KEY depuis le .env de production (hors --dry-run)",
    )
    parser.add_argument("--config", type=Path, default=None, help="configuration narrative")
    return parser


# ── Dépense ─────────────────────────────────────────────────────────


def _read_spend_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _capped_costs(db_path: Path, labels: Sequence[str]) -> dict[str, float]:
    """Coûts par étiquette plafonnée d'une base (lecture seule).

    Args:
        db_path: Base.
        labels: Étiquettes comptées dans le plafond.

    Returns:
        ``{étiquette: USD}`` (vide si la base ou la table manquent).
    """
    if not db_path.is_file():
        return {}
    try:
        with narrative_db.connect(db_path, readonly=True) as conn:
            by_label = narrative_db.costs_by_label(conn)
    except sqlite3.Error:
        return {}
    return {label: value for label, value in by_label.items() if label in labels}


def spend_status(db_path: Path, spend_json: Path, config: NarrativeConfig) -> dict[str, Any]:
    """Dépense déjà enregistrée sur toutes les bases de travail du run.

    Args:
        db_path: Base de ce passage (ajoutée aux sources).
        spend_json: Consolidation existante.
        config: Configuration (étiquettes plafonnées).

    Returns:
        ``total_usd``, ``by_label``, ``sources``, ``recorded_total_usd``.
    """
    recorded = _read_spend_json(spend_json)
    sources = [str(item) for item in recorded.get("sources", []) if isinstance(item, str)]
    resolved_db = str(resolve_path(db_path))
    if resolved_db not in sources:
        sources.append(resolved_db)
    by_label: Counter[str] = Counter()
    for source in sorted(set(sources)):
        for label, value in _capped_costs(Path(source), config.backfill_capped_labels).items():
            by_label[label] += value
    computed = float(sum(by_label.values()))
    recorded_total = float(recorded.get("total_usd") or 0.0)
    return {
        "total_usd": round(max(computed, recorded_total), 6),
        "computed_usd": round(computed, 6),
        "recorded_total_usd": round(recorded_total, 6),
        "by_label": {label: round(value, 6) for label, value in sorted(by_label.items())},
        "sources": sorted(set(sources)),
    }


def write_spend_json(path: Path, status: dict[str, Any]) -> None:
    """Réécrit la consolidation de dépense (chemin contrôlé par l'appelant).

    ``total_usd`` ne baisse jamais : c'est le maximum du total recalculé sur
    les bases sources et du total déjà consigné (une base source supprimée ne
    fait pas disparaître sa dépense du plafond). ``computed_usd`` garde le
    recalcul seul.

    Args:
        path: ``docs/v2/evidence/spend.json``.
        status: Résultat de ``spend_status``.
    """
    payload = {
        "total_usd": status["total_usd"],
        "computed_usd": status["computed_usd"],
        "by_label": status["by_label"],
        "sources": status["sources"],
        "updated_at": narrative_db.utc_now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ── Plan ────────────────────────────────────────────────────────────


def _attempts(conn: sqlite3.Connection, brief_id: str, lang: str) -> tuple[bool, int]:
    if not narrative_db.table_exists(conn, "v2_stories"):
        return False, 0
    published = narrative_db.published_story(conn, brief_id, lang) is not None
    return published, narrative_db.next_attempt(conn, brief_id, lang) - 1


def plan_stories(
    conn: sqlite3.Connection,
    config: NarrativeConfig,
    *,
    only: Sequence[str],
    limit: int | None,
) -> list[dict[str, Any]]:
    """Briefs qui ont encore du travail de récit, ordre des identifiants.

    Args:
        conn: Connexion (lecture seule suffit).
        config: Configuration (tentatives maximales).
        only: Briefs demandés (tous si vide).
        limit: Nombre maximal de briefs.

    Returns:
        Plan : ``brief_id``, ``fr_published``, ``fr_attempts``,
        ``en_published``, ``en_attempts``.
    """
    wanted = set(only)
    plan: list[dict[str, Any]] = []
    for row in narrative_db.list_published_briefs(conn):
        if row["is_stub"] or (wanted and row["id"] not in wanted):
            continue
        fr_published, fr_attempts = _attempts(conn, row["id"], "fr")
        en_published, en_attempts = _attempts(conn, row["id"], "en")
        fr_open = not fr_published and fr_attempts < config.max_attempts
        en_open = fr_published and not en_published and en_attempts < config.translate_max_attempts
        if fr_open or en_open:
            plan.append(
                {
                    "brief_id": row["id"],
                    "fr_published": fr_published,
                    "fr_attempts": fr_attempts,
                    "en_published": en_published,
                    "en_attempts": en_attempts,
                }
            )
        if limit is not None and len(plan) >= limit:
            break
    return plan


def plan_links(conn: sqlite3.Connection, sidecars_dir: Path | None) -> dict[str, Any]:
    """Liens à créer (``fk``, ``text_match``) pour les briefs complets publiés.

    Args:
        conn: Connexion.
        sidecars_dir: Sidecars de développement, ou ``None``.

    Returns:
        ``candidates`` (liste de ``LinkCandidate``), ``existing``,
        ``unresolved`` (identifiants), ``ambiguous``.
    """
    existing = narrative_db.links_by_brief(conn)
    index = SummaryIndex(conn)
    candidates = []
    unresolved: list[str] = []
    for row in narrative_db.list_published_briefs(conn):
        if row["is_stub"] or row["id"] in existing:
            continue
        candidate = resolve_backfill_link(row, index, sidecars_dir)
        if candidate is None:
            unresolved.append(row["id"])
        else:
            candidates.append(candidate)
    return {
        "candidates": candidates,
        "existing": len(existing),
        "unresolved": unresolved,
        "ambiguous": sum(1 for item in candidates if item.ambiguous),
    }


# ── Exécution ───────────────────────────────────────────────────────


def _default_sidecars_dir() -> Path:
    output_dir = os.environ.get("SPORE_OUTPUT_DIR")
    base = Path(output_dir) if output_dir else REPO_ROOT / "outputs"
    return base / "briefs"


def _check_sidecars_dir(path: Path) -> Path | None:
    """Répertoire de sidecars de développement, jamais la production.

    Args:
        path: Répertoire demandé.

    Returns:
        Chemin résolu, ou ``None`` s'il n'existe pas.

    Raises:
        SystemExit: Répertoire situé en production hors bascule.
    """
    resolved = resolve_path(path)
    if production_root_of(resolved) is not None and not is_production_mode():
        raise SystemExit(f"sidecars : répertoire de production refusé ({resolved})")
    return resolved if resolved.is_dir() else None


def _dry_run_neighbours(conn: sqlite3.Connection, config: NarrativeConfig, links: dict[str, Any]) -> dict[str, Any]:
    """Maillage calculé en mémoire, liens candidats compris (rien n'est écrit)."""
    from narrative import mechanical
    from narrative.neighbours import (
        IdeaNode,
        compute_neighbours,
        idea_vector,
        load_embedding_index,
        summarise,
    )

    ideas = mechanical.load_ideas(conn, config)
    link_map = dict(narrative_db.links_by_brief(conn))
    for candidate in links["candidates"]:
        link_map[candidate.brief_id] = (candidate.hypothesis_id, candidate.method)
    embeddings = load_embedding_index(conn)
    refreshed = [
        idea
        if idea.is_stub
        else IdeaNode(
            idea.brief_id,
            idea.is_stub,
            idea.domains,
            idea.themes,
            idea_vector(idea.brief_id, idea.domains, link_map, embeddings),
        )
        for idea in ideas
    ]
    edges = compute_neighbours(refreshed, mechanical.neighbour_settings(config))
    return summarise(refreshed, edges)


def dry_run(args: argparse.Namespace, config: NarrativeConfig, db_path: Path, spend: dict[str, Any], cap: float, sidecars: Path | None) -> dict[str, Any]:
    """Plan complet en lecture seule.

    Args:
        args: Arguments.
        config: Configuration.
        db_path: Base (ouverte en lecture seule).
        spend: Dépense déjà enregistrée.
        cap: Plafond de ce passage.
        sidecars: Sidecars de développement.

    Returns:
        Résumé JSON.
    """
    from narrative import explainer_flags, mechanical
    from narrative.themes import coverage

    with narrative_db.connect(db_path, readonly=True) as conn:
        published = narrative_db.list_published_briefs(conn)
        plan = plan_stories(conn, config, only=args.brief, limit=args.limit)
        links = plan_links(conn, sidecars)
        themes = mechanical.tag_all(conn, config, write=False)
        flags = explainer_flags.flag_all(conn, config, write=False)
        mapping = mechanical.theme_mapping(config)
        parents = mechanical.parent_index(config, conn)
        domain_names = [
            name for row in published if not row["is_stub"] for name in narrative_db.brief_domains(row)
        ]
        neighbours = _dry_run_neighbours(conn, config, links)
        schema_present = narrative_db.narrative_schema_present(conn)

    estimate = round(len(plan) * config.backfill_estimated_usd_per_brief, 4)
    return {
        "mode": "dry_run",
        "db": str(db_path),
        "schema_present": schema_present,
        "published": {
            "full": sum(1 for row in published if not row["is_stub"]),
            "stubs": sum(1 for row in published if row["is_stub"]),
        },
        "stories": {
            "briefs_with_work": len(plan),
            "first": [item["brief_id"] for item in plan[:10]],
            "estimated_usd": estimate,
            "would_stop_at_cap": estimate > cap,
        },
        "links": {
            "existing": links["existing"],
            "new_by_method": dict(Counter(item.method for item in links["candidates"])),
            "ambiguous": links["ambiguous"],
            "unresolved_count": len(links["unresolved"]),
            "unresolved_first": links["unresolved"][:10],
            "sidecars_dir": str(sidecars) if sidecars else None,
        },
        "themes": {
            "mapping_version": mapping.version,
            "placeholder": mapping.placeholder,
            "per_theme": dict(Counter(slug for items in themes.values() for slug, _ in items)),
            "coverage": coverage(domain_names, mapping, parents),
        },
        "explainer_flags": flags,
        "neighbours": neighbours,
        "budget": {"already_spent_usd": spend["total_usd"], "cap_this_run_usd": round(cap, 6)},
    }


async def run(args: argparse.Namespace, config: NarrativeConfig, db_path: Path, spend: dict[str, Any], cap: float, sidecars: Path | None, spend_json: Path) -> dict[str, Any]:
    """Backfill réel (écrit dans la copie de base).

    Args:
        args: Arguments.
        config: Configuration.
        db_path: Base contrôlée.
        spend: Dépense déjà enregistrée.
        cap: Plafond de ce passage.
        sidecars: Sidecars de développement.
        spend_json: Consolidation de dépense.

    Returns:
        Résumé JSON.
    """
    from narrative import explainer_flags, mechanical
    from narrative.config import override_config
    from narrative.graph import run_narrative_layer

    with narrative_db.connect(db_path) as conn:
        narrative_db.ensure_narrative_schema(conn)
        links = plan_links(conn, sidecars)
        for candidate in links["candidates"]:
            narrative_db.upsert_brief_hypothesis(
                conn, candidate.brief_id, candidate.hypothesis_id, candidate.method, overwrite=False
            )
        themes = mechanical.tag_all(conn, config, write=True)
        flags = explainer_flags.flag_all(conn, config, write=True)
        plan = [] if args.no_stories else plan_stories(conn, config, only=args.brief, limit=args.limit)
        start_cost_id = narrative_db.max_cost_id(conn)

    rate = config.backfill_rate_limit_s if args.rate_limit is None else max(0.0, args.rate_limit)
    processed: list[dict[str, Any]] = []
    stopped: str | None = None
    with override_config(config):
        for index, item in enumerate(plan):
            with narrative_db.connect(db_path, readonly=True) as conn:
                spent_run = narrative_db.sum_costs(conn, None, since_id=start_cost_id)
            if spent_run + config.backfill_estimated_usd_per_brief > cap:
                stopped = "budget_cap"
                logger.warning("backfill_budget_cap_reached", spent_run=spent_run, cap=cap)
                break
            result = await run_narrative_layer(
                brief_id=item["brief_id"],
                db_path=db_path,
                run_label=args.run_label,
                refresh_neighbours=False,
                config=config,
            )
            processed.append(
                {
                    "brief_id": item["brief_id"],
                    "fr_status": result.get("fr_status"),
                    "en_status": result.get("en_status"),
                    "failed": result.get("failed"),
                }
            )
            if index + 1 < len(plan) and rate:
                await asyncio.sleep(rate)

    neighbours = mechanical.refresh_neighbours(db_path, config, write=True)
    with narrative_db.connect(db_path, readonly=True) as conn:
        spent_run = narrative_db.sum_costs(conn, None, since_id=start_cost_id)

    final_spend = spend_status(db_path, spend_json, config)
    if not args.no_spend_update:
        write_spend_json(spend_json, final_spend)
    return {
        "mode": "run",
        "db": str(db_path),
        "links_written": dict(Counter(item.method for item in links["candidates"])),
        "links_unresolved_count": len(links["unresolved"]),
        "themes_written": len(themes),
        "explainer_flags": flags,
        "stories": {"planned": len(plan), "processed": processed, "stopped": stopped},
        "neighbours": neighbours,
        "budget": {
            "spent_this_run_usd": round(spent_run, 6),
            "cap_this_run_usd": round(cap, 6),
            "run_total_usd": final_spend["total_usd"],
        },
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

    # Refus avant toute lecture ou écriture (même un stat) si la base est en
    # production.
    db_path = assert_safe_write_path(args.db, what="db")
    if not db_path.is_file():
        print(json.dumps({"error": f"base introuvable : {db_path}"}), file=sys.stderr)
        return 2
    # Le pipeline (llm_calls, settings) doit viser la même copie.
    os.environ["SPORE_DB_PATH"] = str(db_path)

    config = load_config(args.config) if args.config else load_config()
    if not args.dry_run and args.run_label not in config.backfill_capped_labels:
        # Une étiquette hors plafond (« pipeline », faute de frappe) ferait
        # échapper la dépense au plafond du run.
        print(json.dumps({"error": f"étiquette hors plafond refusée : {args.run_label!r}"}), file=sys.stderr)
        return 2
    spend_json = resolve_path(args.spend_json or config.backfill_spend_json)
    if not args.dry_run and not args.no_spend_update:
        assert_safe_write_path(spend_json, what="spend_json")

    sidecars = _check_sidecars_dir(args.sidecars_dir or _default_sidecars_dir())

    spend = spend_status(db_path, spend_json, config)
    remaining = max(0.0, RUN_CAP_USD - spend["total_usd"])
    cap = remaining if args.max_usd is None else min(max(0.0, args.max_usd), remaining)

    if args.dry_run:
        if args.load_llm_keys:
            logger.warning("backfill_dry_run_ignores_llm_keys")
        summary = dry_run(args, config, db_path, spend, cap, sidecars)
    else:
        if args.load_llm_keys:
            from narrative.safety import load_llm_keys

            load_llm_keys()
        try:
            import config as spore_config

            spore_config._settings = None  # relire SPORE_DB_PATH
        except ImportError:  # pragma: no cover
            pass
        started = time.monotonic()
        summary = asyncio.run(run(args, config, db_path, spend, cap, sidecars, spend_json))
        summary["duration_s"] = round(time.monotonic() - started, 1)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
