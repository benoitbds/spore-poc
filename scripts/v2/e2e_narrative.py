"""Bout-en-bout réel de la couche narrative, par le nœud câblé au graphe (MUST 6).

Ce script ne réimplémente rien et n'appelle **pas** ``run_narrative_layer``
directement : il appelle ``narrative.graph.node_narrative_layer``, c'est-à-dire
exactement l'objet que ``graph/post_fire_pipeline.py`` ajoute au graphe
Post-Fire après ``validate_brief``, avec pour seule entrée un ``PostFireState``
comme celui que le graphe lui passerait. C'est ce que MUST 6 demande de prouver :
que la couche tourne par le chemin du pipeline, pas par un chemin de calibration.

Conséquence directe sur la façon dont la preuve est construite : le nœud câblé
**avale toutes les exceptions** (`narrative_layer_node_failed`) et rend `{}`
quoi qu'il arrive, pour ne jamais retarder la publication d'un brief validé. Son
retour ne dit donc rien. Tous les champs du fichier de preuve sont par
conséquent **relus en base après l'appel** (`v2_stories`, `v2_llm_costs`) : un
passage qui n'aurait rien fait produirait une preuve vide, pas une preuve
flatteuse.

Le nœud ne prend ni base, ni étiquette, ni configuration en argument — c'est
tout l'intérêt, c'est le nœud de production. Les deux sont donc posés par
l'environnement, avant le premier appel à ``get_settings`` :

- ``SPORE_DB_PATH`` → la copie de travail (jamais la base servie) ;
- ``SPORE_V2_RUN_LABEL`` → ``e2e`` (``DATA_CONTRACT.md``, énumération des
  étiquettes ; le plafond du run porte sur tout ce qui n'est pas ``pipeline``).

Déroulé :

1. copie fraîche par ``sqlite3 -readonly <source> ".backup <copie>"`` (jamais
   ``cp``), ``PRAGMA quick_check`` — option ``--backup`` ;
2. choix du brief : publié, complet, non-stub, hors du jeu de calibration, et
   sans récit déjà en base ; contrôlé **avant** tout appel LLM, sinon la couche
   rendrait ``brief_not_published_full`` et le passage serait perdu ;
3. plafond de dépense du run vérifié avant l'appel (10 USD, toutes bases de
   travail confondues) ;
4. un appel à ``node_narrative_layer`` avec un ``PostFireState`` ;
5. relecture de la base et écriture de ``docs/v2/evidence/narrative-e2e.json`` ;
6. réécriture de ``spend.json``.

Usage (depuis la racine du clone) ::

    PYTHONPATH=. python -m scripts.v2.e2e_narrative --backup --load-llm-keys

``--dry-run`` fait 1 à 3 sans appeler le modèle. ``--evidence-only --brief <id>``
recalcule la preuve depuis les lignes d'un passage déjà fait, sans rien dépenser :
tout ce qui se relit en base est refait, l'horodatage et la durée du passage sont
repris du fichier existant, et le fichier le dit (``rebuilt_at``). Sortie : la
preuve sur stdout ; journaux sur stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import structlog

from narrative.config import RUN_LABEL_ENV, NarrativeConfig, load_config
from narrative.safety import assert_safe_write_path, resolve_path
from scripts.v2.backfill_narrative import (
    RUN_CAP_USD,
    _configure_logging,
    spend_status,
    write_spend_json,
)
from scripts.v2.calibrate_narrative import CALIBRATION_SET
from storage import narrative_db

logger = structlog.get_logger("scripts.v2.e2e_narrative")

DATA_DIR = Path("/home/baq/Projects/spore-v2-data")
#: Base servie par le front de développement : lue en ``-readonly`` pour la
#: copie, jamais ouverte en écriture (WAL).
DEFAULT_SOURCE_DB = DATA_DIR / "dev-front.db"
DEFAULT_COPY_DB = DATA_DIR / "e2e.db"
DEFAULT_EVIDENCE = Path("/home/baq/Projects/spore-v2/docs/v2/evidence/narrative-e2e.json")
RUN_LABEL = "e2e"
#: Variable d'environnement lue par ``config.get_settings`` (alias ``db_path``).
DB_PATH_ENV = "SPORE_DB_PATH"
LANGS: tuple[str, ...] = ("fr", "en")


class E2EError(RuntimeError):
    """Refus avant tout appel LLM (copie, brief, budget, chemins)."""


def build_parser() -> argparse.ArgumentParser:
    """Arguments de la ligne de commande.

    Returns:
        Analyseur.
    """
    parser = argparse.ArgumentParser(
        prog="scripts.v2.e2e_narrative",
        description="Bout-en-bout réel de la couche narrative par le nœud câblé (MUST 6).",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DB, help="base source")
    parser.add_argument("--db", type=Path, default=DEFAULT_COPY_DB, help="copie de travail")
    parser.add_argument("--backup", action="store_true", help="refaire la copie avant le passage")
    parser.add_argument("--brief", default=None, help="brief imposé (sinon le plus récent éligible)")
    parser.add_argument("--out", type=Path, default=DEFAULT_EVIDENCE, help="fichier de preuve")
    parser.add_argument("--config", type=Path, default=None, help="configuration narrative")
    parser.add_argument(
        "--load-llm-keys",
        action="store_true",
        help="charger les clés LLM par narrative.safety.load_llm_keys",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=RUN_CAP_USD,
        help="plafond de dépense du run, toutes bases confondues",
    )
    parser.add_argument("--spend-json", type=Path, default=None, help="consolidation de dépense")
    parser.add_argument("--dry-run", action="store_true", help="tout sauf l'appel au modèle")
    parser.add_argument(
        "--evidence-only",
        action="store_true",
        help="reconstruire la preuve depuis la base, sans appeler le modèle (exige --brief)",
    )
    return parser


# ── Copie et choix du brief ─────────────────────────────────────────


def backup_database(source: Path, target: Path) -> str:
    """Copie en ligne ``sqlite3 -readonly <source> ".backup <copie>"``.

    Args:
        source: Base source, jamais écrite.
        target: Copie à créer.

    Returns:
        Méthode employée.

    Raises:
        E2EError: Copie en échec ou incohérente.
    """
    if not source.exists():
        raise E2EError(f"base source absente : {source}")
    assert_safe_write_path(target, what="db")
    target.parent.mkdir(parents=True, exist_ok=True)
    cli = shutil.which("sqlite3")
    if cli:
        done = subprocess.run(
            [cli, "-readonly", str(source), f'.backup "{target}"'],
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        if done.returncode != 0:
            raise E2EError(f"sqlite3 .backup en échec : {done.stderr.strip()[:300]}")
        method = "sqlite3 -readonly .backup"
    else:  # pragma: no cover — même API de sauvegarde en ligne
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        method = "sqlite3 backup API (lecture seule)"
    with narrative_db.connect(target, readonly=True) as conn:
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
    if check != "ok":
        raise E2EError(f"copie incohérente (quick_check : {check})")
    logger.info("e2e_backup_done", source=str(source), target=str(target), method=method)
    return method


def pick_brief(
    db_path: Path, imposed: str | None, *, allow_existing_story: bool = False
) -> dict[str, Any]:
    """Choisit un brief publié complet, hors jeu de calibration et sans récit.

    Le prédicat est exactement celui que la couche appliquera
    (``narrative_db.is_full_published_brief``) : le contrôler ici évite un
    passage perdu sur ``brief_not_published_full``.

    Args:
        db_path: Copie de travail.
        imposed: Brief imposé, ou ``None`` pour le plus récent éligible.
        allow_existing_story: Accepter un brief qui porte déjà un récit
            (reconstruction de preuve : le passage a déjà eu lieu).

    Returns:
        ``brief_id``, ``created_at``, ``domains``, ``hypothesis_id``,
        ``candidates`` (nombre de briefs éligibles).

    Raises:
        E2EError: Aucun brief éligible, ou brief imposé inéligible.
    """
    with narrative_db.connect(db_path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM briefs ORDER BY created_at DESC")]
        # Lecture seule : le schéma ``v2_*`` n'est pas créé ici (il l'est plus
        # tard, sur une connexion en écriture). Sans la table, aucun brief ne
        # porte encore de récit.
        has_stories = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'v2_stories'"
            ).fetchone()
        )
        with_story = (
            {str(row[0]) for row in conn.execute("SELECT DISTINCT brief_id FROM v2_stories")}
            if has_stories
            else set()
        )

    eligible = [
        row
        for row in rows
        if narrative_db.is_full_published_brief(row)
        and str(row["id"]) not in set(CALIBRATION_SET)
        and (allow_existing_story or str(row["id"]) not in with_story)
    ]
    if imposed:
        chosen = next((row for row in eligible if str(row["id"]) == imposed), None)
        if chosen is None:
            raise E2EError(
                f"{imposed} n'est pas éligible : il doit être publié, complet, non-stub, "
                "hors du jeu de calibration et sans récit déjà en base"
            )
    elif eligible:
        chosen = eligible[0]
    else:
        raise E2EError(f"aucun brief éligible dans {db_path}")

    sharpened: dict[str, Any] = {}
    if chosen.get("sharpened_data"):
        try:
            sharpened = json.loads(chosen["sharpened_data"])
        except (TypeError, ValueError):
            sharpened = {}
    return {
        "brief_id": str(chosen["id"]),
        "created_at": str(chosen.get("created_at")),
        "hypothesis_id": chosen.get("hypothesis_id"),
        "domains": sharpened.get("domains") or [],
        "panel_consensus_score": chosen.get("panel_consensus_score"),
        "candidates": len(eligible),
    }


# ── Relecture de la base ────────────────────────────────────────────


def read_stories(db_path: Path, brief_id: str) -> dict[str, Any]:
    """Relit ce que la couche a écrit, seule source du fichier de preuve.

    Args:
        db_path: Copie de travail.
        brief_id: Brief.

    Returns:
        ``statuses``, ``story_ids``, ``writer_model``, ``guard_model``,
        ``prompt_versions``, ``attempts``, ``words``.
    """
    with narrative_db.connect(db_path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM v2_stories WHERE brief_id = ? ORDER BY lang, attempt", (brief_id,)
            )
        ]

    statuses: dict[str, str | None] = {lang: None for lang in LANGS}
    story_ids: dict[str, int | None] = {lang: None for lang in LANGS}
    attempts: dict[str, int] = {lang: 0 for lang in LANGS}
    models: dict[str, set[str]] = {"writer": set(), "guard": set()}
    prompts: dict[str, set[str]] = {"writer_or_translate": set(), "guard": set()}
    for row in rows:
        lang = str(row["lang"])
        attempts[lang] = attempts.get(lang, 0) + 1
        if row["writer_model"]:
            models["writer"].add(str(row["writer_model"]))
        if row["guard_model"]:
            models["guard"].add(str(row["guard_model"]))
        if row["prompt_version"]:
            prompts["writer_or_translate"].add(str(row["prompt_version"]))
        if row["guard_prompt_version"]:
            prompts["guard"].add(str(row["guard_prompt_version"]))
        # Le récit publié fait foi ; sinon le statut de la dernière tentative.
        if row["status"] == "published" or statuses.get(lang) is None:
            statuses[lang] = str(row["status"])
            story_ids[lang] = int(row["id"])

    return {
        "statuses": statuses,
        "story_ids": story_ids,
        "attempts": attempts,
        # ``v2_stories.writer_model`` est une colonne TEXT : un passage normal
        # n'a qu'un modèle, et la preuve porte alors une chaîne, comme la
        # colonne. La liste n'apparaît que si le repli du fournisseur s'est
        # déclenché en cours de route — un fait qu'il faut voir, pas aplatir.
        "writer_model": _one_or_all(models["writer"]),
        "guard_model": _one_or_all(models["guard"]),
        "prompt_versions": {key: sorted(value) for key, value in prompts.items()},
        "rows": rows,
    }


def _one_or_all(values: set[str]) -> str | list[str] | None:
    """Une valeur unique telle quelle, plusieurs en liste, aucune en ``None``.

    Args:
        values: Valeurs relevées sur les lignes.

    Returns:
        La chaîne si le relevé est unanime, la liste triée sinon, ``None`` si
        rien n'a été relevé.
    """
    if not values:
        return None
    if len(values) == 1:
        return next(iter(values))
    return sorted(values)


def read_costs(db_path: Path, brief_id: str, since_id: int) -> dict[str, Any]:
    """Coût réel du passage, lu dans ``v2_llm_costs``.

    Args:
        db_path: Copie de travail.
        brief_id: Brief.
        since_id: Dernier identifiant avant le passage.

    Returns:
        ``cost_usd``, ``by_node``, ``llm_calls``, ``run_labels``.
    """
    with narrative_db.connect(db_path, readonly=True) as conn:
        rows = list(
            conn.execute(
                "SELECT node, run_label, model, cost_usd FROM v2_llm_costs "
                "WHERE id > ? AND brief_id = ?",
                (since_id, brief_id),
            )
        )
    # Les totaux sont sommés sur les valeurs BRUTES puis arrondis une seule
    # fois : sommer des valeurs déjà arrondies ferait diverger ``cost_usd`` de
    # ``SELECT SUM(cost_usd)`` sur la base, et la preuve doit pouvoir être
    # recalculée à l'identique depuis les lignes.
    by_node: dict[str, float] = {}
    labels: set[str] = set()
    total = 0.0
    for node, label, _model, cost in rows:
        value = float(cost or 0.0)
        by_node[str(node)] = by_node.get(str(node), 0.0) + value
        total += value
        labels.add(str(label))
    return {
        "cost_usd": round(total, 10),
        "by_node_usd": {node: round(value, 10) for node, value in sorted(by_node.items())},
        "llm_calls": len(rows),
        "run_labels": sorted(labels),
    }


# ── Passage ─────────────────────────────────────────────────────────


async def run_wired_node(state: Mapping[str, Any]) -> dict[str, Any]:
    """Appelle le nœud **câblé au graphe**, et rien d'autre.

    ``node_narrative_layer`` est importé ici, après que l'environnement a été
    posé, et c'est le même objet que ``graph.post_fire_pipeline`` ajoute au
    graphe. Il ne lève jamais et rend toujours ``{}`` : la preuve se lit en
    base, pas dans ce retour.

    Args:
        state: ``PostFireState`` (lecture seule).

    Returns:
        Le retour du nœud, attendu vide.
    """
    from narrative.graph import node_narrative_layer

    return await node_narrative_layer(state)


def _now() -> str:
    """Horodatage ISO 8601 UTC.

    Returns:
        ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wired_node_is_the_graph_node() -> bool:
    """Contrôle que le nœud appelé est celui que le graphe câble.

    Returns:
        ``True`` si ``graph.post_fire_pipeline`` importe le même objet.
    """
    import graph.post_fire_pipeline as pipeline
    from narrative.graph import node_narrative_layer

    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    block = source.split("# --- v2 narrative layer ---", 1)
    if len(block) != 2:
        return False
    wired = block[1].split("# --- end v2 narrative layer ---", 1)[0]
    return (
        "from narrative.graph import node_narrative_layer" in wired
        and 'workflow.add_node("narrative_layer", node_narrative_layer)' in wired
        and callable(node_narrative_layer)
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée.

    Args:
        argv: Arguments (``sys.argv[1:]`` par défaut).

    Returns:
        Code de sortie : 0 si la preuve est écrite, 2 sur refus.
    """
    args = build_parser().parse_args(argv)
    _configure_logging()
    try:
        return _run(args)
    except E2EError as exc:
        logger.error("e2e_refused", error=str(exc))
        return 2


def _run(args: argparse.Namespace) -> int:
    """Déroulé complet, une fois les arguments analysés.

    Args:
        args: Arguments.

    Returns:
        Code de sortie.

    Raises:
        E2EError: Refus avant ou pendant le passage.
    """
    copy = assert_safe_write_path(args.db, what="db")
    out = assert_safe_write_path(args.out, what="evidence")
    config: NarrativeConfig = load_config(args.config)
    spend_json = assert_safe_write_path(
        args.spend_json or config.backfill_spend_json, what="spend_json"
    )
    if RUN_LABEL not in config.backfill_capped_labels:
        raise E2EError(f"étiquette {RUN_LABEL!r} hors des libellés plafonnés du contrat")

    if args.backup:
        backup_method = backup_database(resolve_path(args.source), copy)
    else:
        if not copy.exists():
            raise E2EError(f"copie absente : {copy} (relancer avec --backup)")
        backup_method = "copie existante, non refaite"

    if args.evidence_only and not args.brief:
        raise E2EError("--evidence-only exige --brief : la preuve porte sur un brief nommé")
    brief = pick_brief(copy, args.brief, allow_existing_story=args.evidence_only)
    logger.info("e2e_brief_chosen", **{k: v for k, v in brief.items() if k != "domains"})

    # L'environnement est le seul canal : le nœud câblé ne prend pas de base.
    os.environ[DB_PATH_ENV] = str(copy)
    os.environ[RUN_LABEL_ENV] = RUN_LABEL

    # Les clés sont chargées avant le premier ``get_settings()`` : ``SporeSettings``
    # exige ``DEEPSEEK_API_KEY`` dès sa construction, et le contrôle de
    # ``SPORE_DB_PATH`` juste en dessous passe par lui. ``load_llm_keys`` est le
    # seul chemin autorisé pour les clés (narrative/safety.py).
    if args.load_llm_keys:
        from narrative.safety import load_llm_keys

        load_llm_keys()
    elif not os.environ.get("DEEPSEEK_API_KEY"):
        raise E2EError("aucune clé LLM dans l'environnement (utiliser --load-llm-keys)")

    from config import get_settings

    effective_db = Path(get_settings().db_path)
    if effective_db != copy:
        raise E2EError(f"{DB_PATH_ENV} inopérant : la couche viserait {effective_db}, pas {copy}")
    if config.effective_run_label() != RUN_LABEL:
        raise E2EError(f"{RUN_LABEL_ENV} inopérant : étiquette {config.effective_run_label()!r}")
    if not _wired_node_is_the_graph_node():
        raise E2EError("le nœud appelé n'est pas celui que graph/post_fire_pipeline.py câble")

    spend_before = spend_status(copy, spend_json, config)
    room = args.max_usd - float(spend_before["total_usd"])
    if room <= config.backfill_estimated_usd_per_brief:
        raise E2EError(
            f"plafond atteint : {spend_before['total_usd']} USD sur {args.max_usd}, "
            f"il reste {room:.6f} USD pour un brief estimé à "
            f"{config.backfill_estimated_usd_per_brief} USD"
        )

    with narrative_db.connect(copy) as conn:
        narrative_db.ensure_narrative_schema(conn)
        baseline = narrative_db.max_cost_id(conn)

    state: dict[str, Any] = {
        "run_id": f"e2e-{_now()}",
        "hypothesis_id": brief["hypothesis_id"],
        "brief_id": brief["brief_id"],
        "brief_validated": True,
        "is_stub": False,
        "errors": [],
    }

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "brief": brief,
                    "db": str(copy),
                    "backup_method": backup_method,
                    "spend_before_usd": spend_before["total_usd"],
                    "room_usd": round(room, 6),
                    "state": state,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.evidence_only:
        # Reconstruction de la preuve depuis la base, sans appeler le modèle.
        # Tout ce qui se relit en base est recalculé ; les seuls éléments qui
        # ne s'y trouvent pas — l'horodatage du passage, sa durée et le retour
        # du nœud — sont repris du fichier existant, et le fichier le dit
        # (``rebuilt_at``). Sans preuve antérieure, il n'y a rien à reprendre.
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise E2EError(f"--evidence-only sans preuve antérieure lisible : {exc}") from exc
        if previous.get("brief_id") != brief["brief_id"]:
            raise E2EError(
                f"la preuve existante porte sur {previous.get('brief_id')!r}, "
                f"pas sur {brief['brief_id']!r}"
            )
        started_at = str(previous.get("started_at"))
        finished_at = str(previous.get("finished_at"))
        duration = previous.get("duration_s")
        returned = previous.get("node_returned", {})
        state = previous.get("state", state)
        # La méthode de copie décrit comment la base du PASSAGE a été produite,
        # pas ce qu'a fait la reconstruction : elle se reprend telle quelle.
        backup_method = str(previous.get("backup_method", backup_method))
        baseline = 0
    else:
        started_at, started = _now(), time.monotonic()
        returned = asyncio.run(run_wired_node(state))
        duration = round(time.monotonic() - started, 1)
        finished_at = _now()

    stories = read_stories(copy, brief["brief_id"])
    costs = read_costs(copy, brief["brief_id"], baseline)

    evidence = {
        "must": "MUST 6 — bout-en-bout réel de la couche narrative",
        "brief_id": brief["brief_id"],
        "db": str(copy),
        "source_db": str(resolve_path(args.source)),
        "backup_method": backup_method,
        "entry_point": "narrative.graph.node_narrative_layer "
        "(le nœud câblé par graph/post_fire_pipeline.py après validate_brief)",
        "node_returned": returned,
        "state": state,
        "statuses": stories["statuses"],
        "story_ids": stories["story_ids"],
        "attempts": stories["attempts"],
        "writer_model": stories["writer_model"],
        "guard_model": stories["guard_model"],
        "prompt_versions": stories["prompt_versions"],
        "configured_prompt_versions": {
            "story_writer": config.writer.prompt,
            "story_guard": config.guard.llm.prompt,
            "story_translate": config.translate.prompt,
        },
        "config_version": config.version,
        "run_label": RUN_LABEL,
        "run_labels_seen": costs["run_labels"],
        "cost_usd": costs["cost_usd"],
        "cost_by_node_usd": costs["by_node_usd"],
        "llm_calls": costs["llm_calls"],
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": duration,
    }
    if args.evidence_only:
        evidence["rebuilt_at"] = _now()
        evidence["rebuilt_note"] = (
            "preuve recalculée depuis les lignes de la base ; started_at, finished_at, "
            "duration_s et node_returned sont repris du passage d'origine"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    final_spend = spend_status(copy, spend_json, config)
    write_spend_json(spend_json, final_spend)
    evidence["run_spend_total_usd"] = final_spend["total_usd"]
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    logger.info(
        "e2e_done",
        brief_id=brief["brief_id"],
        statuses=stories["statuses"],
        cost_usd=costs["cost_usd"],
        run_spend_total_usd=final_spend["total_usd"],
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
