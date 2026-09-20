"""Calibration des prompts de la couche narrative : une itération sur un jeu d'essai.

Pour chaque brief du jeu d'essai, le script exécute le **vrai** sous-graphe
narratif, par le chemin du pipeline (``narrative.graph.run_narrative_layer``) :
``story_writer`` → ``story_guard`` (deux nouvelles tentatives au plus) →
``story_translate_en`` → ``story_guard_en`` (idem) → étapes mécaniques. Les
récits ne sont jamais écrits ni retouchés à la main : le script ne fait que
lancer la couche et exporter ce qu'elle a écrit en base, pour le jury.

Déroulé :

1. contrôle du jeu d'essai sur la base source, ouverte en lecture seule :
   chaque brief doit être publié et complet (non-stub), sinon arrêt ;
2. copie fraîche de la base source par ``sqlite3 -readonly <source>
   ".backup <copie>"`` (jamais ``cp``), ``PRAGMA quick_check`` ; la copie
   d'une itération n'est jamais réutilisée ni écrasée ;
3. récits antérieurs des briefs du jeu retirés **de la copie** (sinon un récit
   déjà publié serait conservé et rien ne serait rédigé) ;
4. couche narrative, brief par brief, étiquette de coût ``calibration`` ;
   plafond vérifié avant chaque brief (dépense du run toutes bases confondues,
   ``docs/v2/evidence/spend.json``, 10 USD au plus), ``spend.json`` réécrit
   après chaque brief ;
5. export pour le jury, sous ``--out`` : par brief, ``context.md`` (ce que le
   rédacteur a reçu), ``<lang>__a<n>.md`` pour **chaque** tentative (titre,
   année, lieu, corps, empreinte, identifiant, **sans** le verdict du garde ;
   une tentative dont la rédaction a échoué garde sa fiche, avec la mention
   « aucun texte produit », pour que la numérotation n'ait pas de trou) et
   ``guard/<lang>__a<n>.json`` pour chaque tentative (rapport du garde, pour
   l'orchestrateur) ; ``summary.json`` pour l'itération.

Usage (depuis la racine du clone, interpréteur de production en lecture) ::

    PYTHONPATH=. python -m scripts.v2.calibrate_narrative \\
        --iteration 1 --load-llm-keys --max-usd 1.0

``--dry-run`` contrôle le jeu d'essai et le budget, sans copie, sans clé et
sans appel LLM. Sortie : un résumé JSON sur stdout ; journaux sur stderr.
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
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import structlog

from narrative.checks import count_words
from narrative.config import NarrativeConfig, load_config
from narrative.inputs import StoryInputs, bullets, extract_inputs, load_blob
from narrative.safety import assert_safe_write_path, production_root_of, resolve_path
from scripts.v2.backfill_narrative import (
    RUN_CAP_USD,
    _configure_logging,
    spend_status,
    write_spend_json,
)
from storage import narrative_db

logger = structlog.get_logger("scripts.v2.calibrate_narrative")

#: Jeu d'essai (domaines, ancienneté et qualité variés), contrôlé le 19/09
#: sur la copie de base : dix briefs publiés complets.
CALIBRATION_SET: tuple[str, ...] = (
    "SPR-2026-BFF6",
    "SPR-2026-D3A8",
    "SPR-2026-A08D",
    "SPR-2026-2A88",
    "SPR-2026-072C",
    "SPR-2026-8B20",
    "SPR-2026-816D",
    "SPR-2026-35F1",
    "SPR-2026-FBCA",
    "SPR-2026-0669",
)

DATA_DIR = Path("/home/baq/Projects/spore-v2-data")
#: Base source par défaut. Jamais ``dev-front.db`` : le front de développement
#: la lit et l'écrit (WAL), ce qui n'en fait pas une source stable pour une
#: itération. ``dev-b.db`` est une copie de travail dédiée, obtenue par
#: ``sqlite3 ".backup"``.
DEFAULT_BASE_DB = DATA_DIR / "dev-b.db"
DEFAULT_OUT_ROOT = Path("/home/baq/Projects/spore-v2/docs/v2/calibration")
LANGS: tuple[str, ...] = ("fr", "en")


class CalibrationError(RuntimeError):
    """Refus avant tout appel LLM (jeu d'essai, chemins, budget)."""


def build_parser() -> argparse.ArgumentParser:
    """Arguments de la ligne de commande.

    Returns:
        Analyseur.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--iteration", type=int, required=True, help="numéro de l'itération (≥ 1)")
    parser.add_argument("--briefs", nargs="+", default=list(CALIBRATION_SET), help="jeu d'essai")
    parser.add_argument(
        "--base-db", type=Path, default=DEFAULT_BASE_DB, help="base source (lecture seule)"
    )
    parser.add_argument(
        "--copy-db",
        type=Path,
        default=None,
        help="copie de travail (défaut : spore-v2-data/calib-iter<N>.db)",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="export (défaut : docs/v2/calibration/iter<N>/)"
    )
    parser.add_argument(
        "--load-llm-keys",
        action="store_true",
        help="charger DEEPSEEK_API_KEY / ANTHROPIC_API_KEY depuis le .env de production",
    )
    parser.add_argument(
        "--max-usd", type=float, default=None, help="plafond de cette itération (USD)"
    )
    parser.add_argument("--run-label", default="calibration", help="étiquette du registre de coût")
    parser.add_argument(
        "--concurrency", type=int, default=1, help="briefs traités en parallèle (1 à 5)"
    )
    parser.add_argument(
        "--refresh-neighbours",
        action="store_true",
        help="recalculer le maillage après chaque brief",
    )
    parser.add_argument("--config", type=Path, default=None, help="configuration narrative")
    parser.add_argument(
        "--spend-json", type=Path, default=None, help="consolidation de la dépense du run"
    )
    parser.add_argument("--no-spend-update", action="store_true", help="ne pas réécrire spend.json")
    parser.add_argument(
        "--dry-run", action="store_true", help="contrôles seulement : ni copie, ni LLM"
    )
    return parser


# ── Contrôles ───────────────────────────────────────────────────────


def check_briefs(db_path: Path, briefs: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Contrôle le jeu d'essai sur la base source (lecture seule).

    Args:
        db_path: Base source.
        briefs: Identifiants.

    Returns:
        ``{brief_id: {created_at, domains, has_vulgarisation_fr, has_vulgarisation_en}}``.

    Raises:
        CalibrationError: Brief absent, non publié, stub ou en double.
    """
    duplicates = sorted(item for item, n in Counter(briefs).items() if n > 1)
    if duplicates:
        raise CalibrationError(f"briefs en double : {', '.join(duplicates)}")
    problems: list[str] = []
    info: dict[str, dict[str, Any]] = {}
    with narrative_db.connect(db_path, readonly=True) as conn:
        for brief_id in briefs:
            row = narrative_db.fetch_brief(conn, brief_id)
            if row is None:
                problems.append(f"{brief_id} : absent")
            elif row.get("is_stub"):
                problems.append(f"{brief_id} : stub")
            elif not narrative_db.is_full_published_brief(row):
                problems.append(f"{brief_id} : non publié (status={row.get('status')})")
            else:
                info[brief_id] = {
                    "created_at": row.get("created_at"),
                    "domains": narrative_db.brief_domains(row),
                    "panel_consensus_score": row.get("panel_consensus_score"),
                    "has_vulgarisation_fr": bool(load_blob(row.get("vulgarization_data"))),
                    "has_vulgarisation_en": bool(load_blob(row.get("vulgarization_data_en"))),
                }
    if problems:
        raise CalibrationError("jeu d'essai refusé : " + " ; ".join(problems))
    return info


def resolve_paths(args: argparse.Namespace, config: NarrativeConfig) -> dict[str, Path]:
    """Chemins de l'itération, contrôlés avant toute écriture.

    Args:
        args: Arguments.
        config: Configuration (``spend.json`` par défaut).

    Returns:
        ``base``, ``copy``, ``out``, ``spend_json`` résolus.

    Raises:
        CalibrationError: Itération invalide, source en production, copie ou
            export déjà présents.
        UnsafePathError: Chemin d'écriture en production.
    """
    if args.iteration < 1:
        raise CalibrationError("--iteration doit valoir 1 ou plus")
    base = resolve_path(args.base_db)
    if production_root_of(base) is not None:
        raise CalibrationError(f"base source de production refusée : {base}")
    if not base.is_file():
        raise CalibrationError(f"base source introuvable : {base}")
    copy = resolve_path(args.copy_db or DATA_DIR / f"calib-iter{args.iteration}.db")
    out = resolve_path(args.out or DEFAULT_OUT_ROOT / f"iter{args.iteration}")
    spend_json = resolve_path(args.spend_json or config.backfill_spend_json)
    if copy == base:
        raise CalibrationError("la copie ne peut pas être la base source")
    if not args.dry_run:
        assert_safe_write_path(copy, what="calibration_db")
        assert_safe_write_path(out, what="calibration_out")
        if not args.no_spend_update:
            assert_safe_write_path(spend_json, what="spend_json")
        for suffix in ("", "-wal", "-shm", "-journal"):
            if Path(f"{copy}{suffix}").exists():
                raise CalibrationError(
                    f"copie déjà présente (itération déjà lancée ?) : {copy}{suffix}"
                )
        if out.exists() and any(out.iterdir()):
            raise CalibrationError(f"export déjà présent : {out}")
    return {"base": base, "copy": copy, "out": out, "spend_json": spend_json}


def backup_database(source: Path, target: Path) -> str:
    """Copie en ligne ``sqlite3 -readonly <source> ".backup <copie>"``.

    Args:
        source: Base source (jamais écrite).
        target: Copie à créer.

    Returns:
        Méthode employée.

    Raises:
        CalibrationError: Copie en échec ou incohérente.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    cli = shutil.which("sqlite3")
    if cli:
        command = [cli, "-readonly", str(source), f'.backup "{target}"']
        done = subprocess.run(command, capture_output=True, text=True, check=False, timeout=900)
        if done.returncode != 0:
            raise CalibrationError(f"sqlite3 .backup en échec : {done.stderr.strip()[:300]}")
        method = "sqlite3 -readonly .backup"
    else:  # pragma: no cover — même API de sauvegarde en ligne, par le module sqlite3
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
        raise CalibrationError(f"copie incohérente (quick_check : {check})")
    return method


def prepare_copy(copy: Path, briefs: Sequence[str]) -> dict[str, Any]:
    """Schéma ``v2_*`` et récits antérieurs du jeu retirés de la copie.

    Les coûts hérités de la source restent dans la copie : ils ne peuvent
    qu'être comptés deux fois dans le plafond (sens prudent), jamais oubliés.

    Args:
        copy: Copie de travail.
        briefs: Jeu d'essai.

    Returns:
        ``stories_removed``, ``inherited_cost_rows``, ``baseline_cost_id``.
    """
    with narrative_db.connect(copy) as conn:
        narrative_db.ensure_narrative_schema(conn)
        placeholders = ", ".join("?" for _ in briefs)
        with conn:
            removed = conn.execute(
                f"DELETE FROM v2_stories WHERE brief_id IN ({placeholders})", list(briefs)
            ).rowcount
        inherited = int(conn.execute("SELECT COUNT(*) FROM v2_llm_costs").fetchone()[0])
        baseline = narrative_db.max_cost_id(conn)
    return {
        "stories_removed": removed,
        "inherited_cost_rows": inherited,
        "baseline_cost_id": baseline,
    }


# ── Exécution ───────────────────────────────────────────────────────


def _ledger(copy: Path, since_id: int, brief_id: str | None = None) -> float:
    with narrative_db.connect(copy, readonly=True) as conn:
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM v2_llm_costs WHERE id > ?"
        params: list[Any] = [since_id]
        if brief_id is not None:
            sql += " AND brief_id = ?"
            params.append(brief_id)
        return float(conn.execute(sql, params).fetchone()[0])


async def run_iteration(
    args: argparse.Namespace,
    config: NarrativeConfig,
    paths: Mapping[str, Path],
    briefs: Sequence[str],
    cap: float,
    baseline: int,
) -> dict[str, Any]:
    """Couche narrative sur chaque brief, plafond vérifié avant chaque brief.

    Args:
        args: Arguments.
        config: Configuration.
        paths: Chemins résolus.
        briefs: Jeu d'essai.
        cap: Plafond de l'itération (USD).
        baseline: Dernier identifiant de ``v2_llm_costs`` avant l'itération.

    Returns:
        ``layer`` (résumé de la couche par brief), ``costs`` (par brief),
        ``stopped``, ``skipped`` (briefs non lancés).
    """
    from narrative.config import override_config
    from narrative.graph import run_narrative_layer

    copy = paths["copy"]
    concurrency = min(max(1, int(args.concurrency)), 5)
    semaphore = asyncio.Semaphore(concurrency)
    layer: dict[str, Any] = {}
    costs: dict[str, float] = {}
    state = {"inflight": 0, "observed_max": 0.0}
    stopped: str | None = None
    skipped: list[str] = []

    async def one(brief_id: str) -> None:
        try:
            started = time.monotonic()
            result = await run_narrative_layer(
                brief_id=brief_id,
                hypothesis_id=None,
                db_path=copy,
                run_label=args.run_label,
                refresh_neighbours=args.refresh_neighbours,
                config=config,
            )
            result["duration_s"] = round(time.monotonic() - started, 1)
            layer[brief_id] = result
            cost = await asyncio.to_thread(_ledger, copy, baseline, brief_id)
            costs[brief_id] = round(cost, 8)
            state["observed_max"] = max(float(state["observed_max"]), cost)
            if not args.no_spend_update:
                write_spend_json(
                    paths["spend_json"], spend_status(copy, paths["spend_json"], config)
                )
            logger.info(
                "calibration_brief_done",
                brief_id=brief_id,
                fr_status=result.get("fr_status"),
                en_status=result.get("en_status"),
                cost_usd=round(cost, 6),
            )
        finally:
            state["inflight"] = int(state["inflight"]) - 1
            semaphore.release()

    tasks: list[asyncio.Task[None]] = []
    with override_config(config):
        for index, brief_id in enumerate(briefs):
            await semaphore.acquire()
            spent = await asyncio.to_thread(_ledger, copy, baseline)
            estimate = max(config.backfill_estimated_usd_per_brief, float(state["observed_max"]))
            if spent + estimate * (int(state["inflight"]) + 1) > cap:
                semaphore.release()
                stopped = "budget_cap"
                skipped = list(briefs[index:])
                logger.warning("calibration_budget_cap_reached", spent=round(spent, 6), cap=cap)
                break
            state["inflight"] = int(state["inflight"]) + 1
            tasks.append(asyncio.create_task(one(brief_id)))
        await asyncio.gather(*tasks)
    return {"layer": layer, "costs": costs, "stopped": stopped, "skipped": skipped}


# ── Export ──────────────────────────────────────────────────────────


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip() or '(absent)'}\n"


def context_markdown(row: Mapping[str, Any], inputs: StoryInputs) -> str:
    """``context.md`` : ce que le rédacteur a reçu, pour juger la fidélité.

    Args:
        row: Ligne ``briefs``.
        inputs: Entrées du rédacteur (``extract_inputs``).

    Returns:
        Markdown (aucune référence bibliographique : titres, auteurs et DOI des
        contre-preuves sont écartés par ``extract_inputs``).
    """
    vulg = load_blob(row.get("vulgarization_data"))
    title_fr = vulg.get("title_fr") if isinstance(vulg, Mapping) else None
    parts = [
        f"# Contexte du brief {inputs.brief_id}\n",
        (
            "Faits du brief tels que le rédacteur du récit les a reçus (entrées de `story_writer`, "
            "extraites de la base par `narrative.inputs.extract_inputs`, textes tronqués comme dans "
            "le prompt). Aucune référence bibliographique.\n"
        ),
        _section("Titre scientifique (hypothèse affûtée)", inputs.title),
        _section(
            "Titre grand public (vulgarisation FR)", title_fr if isinstance(title_fr, str) else ""
        ),
        _section("Domaines de la collision", ", ".join(inputs.domains)),
        _section("Hypothèse affûtée : énoncé formel", inputs.formal_statement),
        _section("Mécanisme proposé (chaîne causale)", bullets(inputs.causal_chain, empty="")),
        _section("Hypothèses de travail", bullets(inputs.key_assumptions, empty="")),
        _section(
            "Vulgarisation FR",
            inputs.vulgarisation
            or "(absente pour ce brief : le récit s'appuie sur l'hypothèse affûtée, les prédictions et les limites, D-006)",
        ),
        _section("Prédictions testables (avec bornes)", bullets(inputs.predictions, empty="")),
        _section(
            "Contre-preuves (constats, sévérité)",
            bullets(inputs.counter_evidence, empty="(aucune contre-preuve)"),
        ),
        _section(
            "Limites (conditions de validité, inconnues, désaccords des relecteurs)",
            bullets(inputs.limits, empty=""),
        ),
    ]
    return "\n".join(parts)


#: Corps d'une tentative sans texte (rédaction en échec : le rédacteur n'a rien
#: rendu d'exploitable). La fiche existe quand même, sinon la numérotation des
#: tentatives aurait un trou inexpliqué pour le jury. Ce n'est pas un verdict du
#: garde : la raison de l'échec reste dans ``guard/<lang>__a<n>.json``.
NO_TEXT_BODY = "(aucun texte produit à cette tentative)"


def story_markdown(row: Mapping[str, Any]) -> str:
    """``<lang>__a<n>.md`` : le récit tel que le site l'afficherait, sans verdict.

    Une fiche est écrite pour **chaque** tentative, avec ou sans corps.

    Args:
        row: Ligne ``v2_stories``.

    Returns:
        Markdown.
    """
    body = str(row.get("body_md") or "").strip()
    lines = [
        f"# {row.get('title') or '(sans titre)'}",
        "",
        f"- Brief : {row['brief_id']} · langue : {row['lang']} · tentative {row['attempt']}",
        f"- story_id : {row['id']}",
        f"- body_sha256 : {row.get('body_sha256') or '(aucun)'}",
        f"- Année du récit : {row.get('story_year') if row.get('story_year') is not None else '(absente)'}",
        f"- Lieu : {row.get('story_place') or '(absent)'}",
        "",
        "---",
        "",
        body or NO_TEXT_BODY,
        "",
    ]
    return "\n".join(lines)


def _report(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        report = json.loads(row.get("guard_report_json") or "{}")
    except ValueError:
        report = {"unparsed": True}
    return report if isinstance(report, dict) else {"unparsed": True}


def _words(row: Mapping[str, Any], report: Mapping[str, Any]) -> int | None:
    length = ((report.get("mechanical") or {}).get("checks") or {}).get("length")
    if isinstance(length, Mapping) and isinstance(length.get("words"), int):
        return int(length["words"])
    body = row.get("body_md")
    return count_words(body) if isinstance(body, str) and body.strip() else None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def export_brief(conn: sqlite3.Connection, brief_id: str, out: Path) -> dict[str, Any]:
    """Écrit le dossier du jury d'un brief et rend son résumé.

    Args:
        conn: Connexion à la copie (lecture).
        brief_id: Brief.
        out: Répertoire de l'itération.

    Returns:
        Résumé par langue : tentatives, statut final, raisons, mots, coût.
    """
    folder = out / brief_id
    row = narrative_db.fetch_brief(conn, brief_id)
    if row is not None:
        _write(folder / "context.md", context_markdown(row, extract_inputs(row)))
    stories = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM v2_stories WHERE brief_id = ? ORDER BY lang, attempt", (brief_id,)
        )
    ]
    summary: dict[str, Any] = {}
    for lang in LANGS:
        attempts = []
        for story in (item for item in stories if item["lang"] == lang):
            report = _report(story)
            has_text = isinstance(story.get("body_md"), str) and bool(story["body_md"].strip())
            name = f"{lang}__a{story['attempt']}"
            # Une fiche par tentative, même sans texte (rédaction en échec) :
            # le jury voit la numérotation complète, sans le verdict du garde.
            _write(folder / f"{name}.md", story_markdown(story))
            guard = {
                "brief_id": brief_id,
                "lang": lang,
                "attempt": story["attempt"],
                "story_id": story["id"],
                "status": story["status"],
                "body_sha256": story.get("body_sha256"),
                "has_text": has_text,
                "writer_model": story.get("writer_model"),
                "guard_model": story.get("guard_model"),
                "prompt_version": story.get("prompt_version"),
                "guard_prompt_version": story.get("guard_prompt_version"),
                "source_story_id": story.get("source_story_id"),
                "cost_usd": story.get("cost_usd"),
                "tokens_in": story.get("tokens_in"),
                "tokens_out": story.get("tokens_out"),
                "report": report,
            }
            _write(
                folder / "guard" / f"{name}.json",
                json.dumps(guard, indent=2, ensure_ascii=False) + "\n",
            )
            attempts.append(
                {
                    "attempt": story["attempt"],
                    "story_id": story["id"],
                    "status": story["status"],
                    "has_text": has_text,
                    "file": f"{brief_id}/{name}.md",
                    "body_sha256": story.get("body_sha256"),
                    "words": _words(story, report) if has_text else None,
                    "reasons": list(report.get("reasons") or []),
                    "judge_scores": (report.get("judge") or {}).get("scores"),
                    "cost_usd": story.get("cost_usd"),
                    "prompt_version": story.get("prompt_version"),
                    "guard_prompt_version": story.get("guard_prompt_version"),
                    "writer_model": story.get("writer_model"),
                    "guard_model": story.get("guard_model"),
                }
            )
        published = [item for item in attempts if item["status"] == "published"]
        summary[lang] = {
            "attempts": len(attempts),
            "final_status": "published" if published else ("rejected" if attempts else "none"),
            "published_attempt": published[0]["attempt"] if published else None,
            "cost_usd": round(sum(float(item["cost_usd"] or 0.0) for item in attempts), 8),
            "detail": attempts,
        }
    return summary


def build_summary(
    *,
    args: argparse.Namespace,
    config: NarrativeConfig,
    paths: Mapping[str, Path],
    briefs: Sequence[str],
    info: Mapping[str, Any],
    prepared: Mapping[str, Any],
    run: Mapping[str, Any],
    per_brief: Mapping[str, Any],
    backup_method: str,
    cap: float,
    started_at: str,
) -> dict[str, Any]:
    """``summary.json`` de l'itération.

    Returns:
        Résumé : paramètres, résultats par brief et par langue, totaux.
    """
    copy = paths["copy"]
    baseline = int(prepared["baseline_cost_id"])
    with narrative_db.connect(copy, readonly=True) as conn:
        by_node = {
            row[0]: round(float(row[1]), 8)
            for row in conn.execute(
                "SELECT node, COALESCE(SUM(cost_usd), 0) FROM v2_llm_costs WHERE id > ? GROUP BY node",
                (baseline,),
            )
        }
        calls = int(
            conn.execute("SELECT COUNT(*) FROM v2_llm_costs WHERE id > ?", (baseline,)).fetchone()[
                0
            ]
        )
        models = sorted(
            {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT model FROM v2_llm_costs WHERE id > ?", (baseline,)
                )
            }
        )
    totals: Counter[str] = Counter()
    words: dict[str, list[int]] = {lang: [] for lang in LANGS}
    prompts_seen: dict[str, set[str]] = {"writer_or_translate": set(), "guard": set()}
    for summary in per_brief.values():
        for lang in LANGS:
            entry = summary.get(lang) or {}
            totals[f"{lang}_{entry.get('final_status', 'none')}"] += 1
            totals[f"{lang}_attempts"] += int(entry.get("attempts") or 0)
            for attempt in entry.get("detail") or []:
                if attempt.get("words") is not None:
                    words[lang].append(int(attempt["words"]))
                if attempt.get("prompt_version"):
                    prompts_seen["writer_or_translate"].add(str(attempt["prompt_version"]))
                if attempt.get("guard_prompt_version"):
                    prompts_seen["guard"].add(str(attempt["guard_prompt_version"]))
    spent = _ledger(copy, baseline)
    return {
        "iteration": args.iteration,
        "run_label": args.run_label,
        "started_at": started_at,
        "finished_at": narrative_db.utc_now(),
        "base_db": str(paths["base"]),
        "copy_db": str(copy),
        "backup_method": backup_method,
        "config_version": config.version,
        "prompt_versions": {
            "configured": {
                "story_writer": config.writer.prompt,
                "story_guard": config.guard.llm.prompt,
                "story_translate": config.translate.prompt,
            },
            "seen": {key: sorted(values) for key, values in prompts_seen.items()},
        },
        "models": models,
        "briefs_requested": list(briefs),
        "briefs_info": dict(info),
        "prepared_copy": dict(prepared),
        "stopped": run.get("stopped"),
        "skipped": list(run.get("skipped") or []),
        "briefs": {
            brief_id: {
                "layer": {
                    key: value
                    for key, value in (run.get("layer") or {}).get(brief_id, {}).items()
                    if key
                    in (
                        "ran",
                        "reason",
                        "failed",
                        "fr_status",
                        "en_status",
                        "vocab_flags",
                        "duration_s",
                        "events",
                    )
                },
                "ledger_cost_usd": (run.get("costs") or {}).get(brief_id),
                **per_brief.get(brief_id, {}),
            }
            for brief_id in briefs
        },
        "totals": {
            "briefs_processed": len(run.get("layer") or {}),
            "fr_published": totals["fr_published"],
            "fr_rejected": totals["fr_rejected"],
            "en_published": totals["en_published"],
            "en_rejected": totals["en_rejected"],
            "en_none": totals["en_none"],
            "fr_attempts": totals["fr_attempts"],
            "en_attempts": totals["en_attempts"],
            "words": {
                lang: {"min": min(values), "max": max(values), "n": len(values)} if values else None
                for lang, values in words.items()
            },
            "llm_calls": calls,
            "cost_usd": round(spent, 8),
            "cost_by_node_usd": by_node,
            "cap_this_iteration_usd": round(cap, 6),
        },
    }


# ── Point d'entrée ──────────────────────────────────────────────────


def _budget(
    paths: Mapping[str, Path], config: NarrativeConfig, max_usd: float | None
) -> tuple[dict[str, Any], float]:
    spend = spend_status(paths["copy"], paths["spend_json"], config)
    remaining = max(0.0, RUN_CAP_USD - float(spend["total_usd"]))
    cap = remaining if max_usd is None else min(max(0.0, max_usd), remaining)
    return spend, cap


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée.

    Args:
        argv: Arguments (``sys.argv`` par défaut).

    Returns:
        Code de sortie : 0, ou 2 pour un refus avant tout appel LLM.
    """
    _configure_logging()
    args = build_parser().parse_args(argv)
    config = load_config(args.config) if args.config else load_config()
    started_at = narrative_db.utc_now()
    try:
        if args.run_label not in config.backfill_capped_labels:
            raise CalibrationError(
                f"étiquette hors plafond refusée : {args.run_label!r} "
                f"(admises : {', '.join(config.backfill_capped_labels)})"
            )
        paths = resolve_paths(args, config)
        briefs = list(args.briefs)
        info = check_briefs(paths["base"], briefs)
        spend, cap = _budget(paths, config, args.max_usd)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "mode": "dry_run",
                        "iteration": args.iteration,
                        "briefs": info,
                        "paths": {key: str(value) for key, value in paths.items()},
                        "prompt_versions": {
                            "story_writer": config.writer.prompt,
                            "story_guard": config.guard.llm.prompt,
                            "story_translate": config.translate.prompt,
                        },
                        "budget": {
                            "already_spent_usd": spend["total_usd"],
                            "cap_this_iteration_usd": round(cap, 6),
                        },
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if cap < config.backfill_estimated_usd_per_brief:
            raise CalibrationError(
                f"budget insuffisant : {cap:.4f} USD disponibles (dépense du run : {spend['total_usd']:.4f} USD)"
            )
        if args.load_llm_keys:
            from narrative.safety import load_llm_keys

            load_llm_keys()
        if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
            raise CalibrationError("aucune clé LLM dans l'environnement (utiliser --load-llm-keys)")
    except CalibrationError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    backup_method = backup_database(paths["base"], paths["copy"])
    prepared = prepare_copy(paths["copy"], briefs)
    # Le pipeline (llm_calls, settings) doit viser la copie.
    os.environ["SPORE_DB_PATH"] = str(paths["copy"])
    try:
        import config as spore_config

        spore_config._settings = None
    except ImportError:  # pragma: no cover
        pass

    started = time.monotonic()
    run = asyncio.run(
        run_iteration(args, config, paths, briefs, cap, int(prepared["baseline_cost_id"]))
    )
    with narrative_db.connect(paths["copy"], readonly=True) as conn:
        per_brief = {brief_id: export_brief(conn, brief_id, paths["out"]) for brief_id in briefs}
    summary = build_summary(
        args=args,
        config=config,
        paths=paths,
        briefs=briefs,
        info=info,
        prepared=prepared,
        run=run,
        per_brief=per_brief,
        backup_method=backup_method,
        cap=cap,
        started_at=started_at,
    )
    summary["duration_s"] = round(time.monotonic() - started, 1)
    if not args.no_spend_update:
        final_spend = spend_status(paths["copy"], paths["spend_json"], config)
        write_spend_json(paths["spend_json"], final_spend)
        summary["run_spend_total_usd"] = final_spend["total_usd"]
    _write(
        paths["out"] / "summary.json",
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
    )
    print(
        json.dumps(
            summary["totals"] | {"stopped": summary["stopped"], "out": str(paths["out"])},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
