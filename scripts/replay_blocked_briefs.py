"""S10-B — rejoue la queue du pipeline post-fire sur les briefs bloqués.

Les briefs produits avant S10-A portent des cartes reviewer (et parfois la
meta-review) en anglais dans ``panel_data``. ``node_validate_brief`` les a
retenus en 'pending' via ``check_panel_language``. Le panel n'est pas refait :
on traduit l'évaluation historique, puis on régénère tout ce qui en dérive.

Pour chaque brief, d'un bloc et dans cet ordre :

1. **Rehydratation** d'un ``PostFireState`` depuis la ligne ``briefs``
   (``grounding_data``, ``sharpened_data`` — dont ``domains`` —,
   ``protocol_data``, ``panel_data``, ``revision_count``) et depuis la copie
   **sauvegardée** du sidecar ``.json`` pour ``original_hypothesis``, qui
   n'existe nulle part ailleurs. L'ancienne ``vulgarization_data`` et les
   colonnes EN ne sont jamais injectées : si la vulgarisation échoue, le
   brief ne doit pas pouvoir être promu avec un ``reviewers_say`` pollué.
2. **Normalisation** par ``node_normalize_panel_language``, réutilisé tel
   quel (cartes signalées + meta-review). S'il laisse de l'anglais ou casse
   la cohérence, arrêt avant toute écriture.
3. **Régénération** du ``.md`` et du ``.json`` via
   ``agents.research_brief_generator.save_brief`` avec le ``brief_id``
   existant et ``generated_on`` = date de ``created_at``, puis
   ``storage.update_brief`` colonne par colonne (``panel_data``,
   ``body_markdown``, chemins ; colonnes dérivées remises à NULL).
   ``node_research_brief`` et ``storage.save_brief`` ne sont PAS utilisés :
   leur ``INSERT OR REPLACE`` remettrait ``created_at`` à l'instant présent,
   ce qui déplacerait les briefs dans la chronologie du site et
   contaminerait la fenêtre de sélection S9.3 (``ORDER BY created_at``).
4. **Vulgarisation** puis **traduction EN** : ``node_vulgarization`` et
   ``node_translation_hook``, réutilisés tels quels.
5. **Contrôles de rejeu**, plus stricts que le chemin nominal : vulgarisation
   et traductions présentes, panel FR détecté français, panel EN détecté
   anglais et réellement différent du FR, sidecar identique à la ligne,
   colonnes invariantes identiques à la sauvegarde. Un échec laisse le brief
   en 'pending' (``replay_still_blocked``).
6. **Promotion** par ``node_validate_brief`` — jamais d'``UPDATE status``
   écrit à la main. ``check_panel`` et ``check_panel_language`` décident.

Jamais modifiés : ``id``, ``created_at``, ``panel_consensus_score``,
``panel_verdict``, ``revision_count``.

Garde-fous avant écriture : sauvegarde S10-B vérifiée couvrant le brief
(``scripts/backup_blocked_briefs.py``), branche ``master`` sans modification
suivie (le cron de 04:15 UTC exécute l'arbre de travail), hors fenêtre du
cron et sans ``autopilot`` en cours.

Rejouable : un brief déjà 'complete' est ignoré ; un brief resté bloqué peut
être relancé — la normalisation est idempotente et l'hypothèse est relue
dans la sauvegarde, pas dans le sidecar que le rejeu a pu réécrire.

Journal par brief : ``replay_started``, ``replay_cards_translated`` (avec le
coût), puis ``replay_completed`` ou ``replay_still_blocked`` (avec le motif).
En mode ``--all-pending``, le premier brief resté bloqué arrête la série.

Usage::

    python -m scripts.replay_blocked_briefs --brief-id SPR-2026-4B85 --dry-run
    python -m scripts.replay_blocked_briefs --brief-id SPR-2026-4B85
    python -m scripts.replay_blocked_briefs --all-pending [--dry-run]
    python -m scripts.replay_blocked_briefs --all-pending --backup-dir data/backups/s10b-...
    python -m scripts.replay_blocked_briefs --restore-brief SPR-2026-4B85 --from data/backups/s10c-...

``--restore-brief`` remet un brief dans l'état capturé par une sauvegarde
(ligne, ``.md``, ``.json``, hashes vérifiés après écriture) via
``scripts.backup_blocked_briefs.restore_brief``. Mêmes garde-fous que le
rejeu : master propre, hors fenêtre du cron, sans autopilot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base import load_prompt  # noqa: E402
from agents.research_brief_generator import save_brief as write_brief_files  # noqa: E402
from config import get_settings  # noqa: E402
from graph.lang_guard import card_texts, check_panel_language, detect  # noqa: E402
from graph.panel_coherence import check_panel  # noqa: E402
from graph.post_fire_pipeline import (  # noqa: E402
    PostFireState,
    _flagged_card_indices,
    _meta_review_texts,
    node_normalize_panel_language,
    node_translation_hook,
    node_validate_brief,
    node_vulgarization,
)
from logging_config import (  # noqa: E402
    TokenTracker,
    get_logger,
    get_token_tracker,
    reset_token_tracker,
    setup_logging,
)
from scripts.backup_blocked_briefs import (  # noqa: E402
    BACKUP_ROOT,
    INVARIANT_COLUMNS,
    PENDING_SCOPE_SQL,
    BackupError,
    RestoreError,
    restore_brief,
    verify_backup,
)
from scripts.translate_brief_panel import (  # noqa: E402
    META_LIST_FIELDS,
    META_STRING_FIELDS,
    REVIEWER_LIST_FIELDS,
    REVIEWER_STRING_FIELDS,
)
from storage import init_database, update_brief  # noqa: E402

logger = get_logger("scripts.replay_blocked_briefs")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Fenêtre UTC pendant laquelle le rejeu refuse d'écrire : autopilot L0 lancé à
# 04:15 (les briefs du corpus sont créés entre 04:33 et 05:04), digest à 05:30.
CRON_WINDOW_UTC: tuple[time, time] = (time(4, 0), time(5, 45))

# Tarif utilisé par l'estimation du dry-run : tous les agents du rejeu
# (traduction, vulgarisation) tournent sur deepseek-v4-flash.
ESTIMATE_MODEL = "deepseek-v4-flash"
# Heuristiques de l'estimation, volontairement prudentes : ~4 caractères par
# token, gabarit de prompt ~400 tokens par appel de traduction, sortie de
# traduction ~1,2 fois l'entrée.
CHARS_PER_TOKEN = 4.0
TRANSLATION_PROMPT_OVERHEAD_TOKENS = 400
TRANSLATION_OUTPUT_RATIO = 1.2

REPLAY_SCOPE_STATUS = "pending"


class RehydrationError(RuntimeError):
    """La ligne ou le sidecar ne permettent pas de reconstruire l'état."""


class PreflightError(RuntimeError):
    """Un garde-fou interdit toute écriture."""


@dataclass
class ReplayOutcome:
    """Résultat du traitement d'un brief.

    Attributes:
        brief_id: Identifiant du brief.
        outcome: ``completed``, ``still_blocked``, ``skipped`` ou ``dry_run``.
        reason: Motif pour ``still_blocked`` et ``skipped``.
        cost_usd: Coût LLM réel (ou estimé en dry-run).
        cards_translated: Nombre de cartes signalées anglaises.
        meta_translated: Meta-review signalée anglaise.
        details: Informations complémentaires pour le compte rendu.
    """

    brief_id: str
    outcome: str
    reason: str | None = None
    cost_usd: float = 0.0
    cards_translated: int = 0
    meta_translated: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# ── Lecture ─────────────────────────────────────────────────────────────


def _loads(value: Any) -> Any:
    """Décode une colonne JSON SQLite.

    Args:
        value: Chaîne JSON, ``None`` ou objet déjà décodé.

    Returns:
        L'objet Python, ou ``None``.
    """
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def load_brief_row(db_path: Path, brief_id: str) -> dict[str, Any] | None:
    """Lit une ligne ``briefs`` en lecture seule.

    Args:
        db_path: Base SQLite.
        brief_id: Identifiant du brief.

    Returns:
        La ligne sous forme de dict, ou ``None`` si absente.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def scope_skip_reason(row: dict[str, Any]) -> str | None:
    """Dit pourquoi une ligne n'entre pas dans le périmètre du rejeu.

    Args:
        row: Ligne ``briefs``.

    Returns:
        ``None`` si le brief est à rejouer, sinon le motif d'exclusion.
    """
    if row.get("status") == "complete":
        return "already_complete"
    if row.get("status") != REPLAY_SCOPE_STATUS:
        return f"status_{row.get('status')}"
    if row.get("is_stub"):
        return "stub"
    if row.get("kill_reason"):
        return "killed"
    if row.get("panel_verdict") != "publish_brief":
        return f"panel_verdict_{row.get('panel_verdict')}"
    if not row.get("panel_data"):
        return "no_panel"
    return None


def rehydrate_state(row: dict[str, Any], sidecar: dict[str, Any]) -> PostFireState:
    """Reconstruit l'état post-panel d'un brief existant.

    L'état contient exactement ce que la queue du graphe consomme
    (normalisation, vulgarisation, traduction, validation). Il ne contient
    **pas** ``vulgarization_fr``, ``vulgarization_en`` ni ``panel_en`` : ces
    artefacts dérivent du panel anglais et doivent être régénérés, sans quoi
    un échec de vulgarisation laisserait passer l'ancien ``reviewers_say``.

    ``mechanisms``, ``keywords`` et ``gap_manifest`` ne sont stockés nulle
    part et ne sont lus que par le grounding et le sharpening, hors rejeu.

    Args:
        row: Ligne ``briefs`` (colonnes JSON en chaîne ou décodées).
        sidecar: Contenu du sidecar ``.json`` d'origine (copie sauvegardée),
            source unique de ``original_hypothesis``.

    Returns:
        Un ``PostFireState`` prêt pour ``node_normalize_panel_language``.

    Raises:
        RehydrationError: Champ requis absent, ou incohérence entre la ligne
            et le sidecar.
    """
    brief_id = row.get("id")
    if not brief_id:
        raise RehydrationError("ligne sans id")
    if sidecar.get("brief_id") not in (None, brief_id):
        raise RehydrationError(
            f"{brief_id} : le sidecar appartient à {sidecar.get('brief_id')}"
        )

    grounding = _loads(row.get("grounding_data"))
    sharpened_raw = _loads(row.get("sharpened_data"))
    protocol = _loads(row.get("protocol_data"))
    panel = _loads(row.get("panel_data"))
    for name, value in (
        ("grounding_data", grounding),
        ("sharpened_data", sharpened_raw),
        ("protocol_data", protocol),
        ("panel_data", panel),
    ):
        if not isinstance(value, dict) or not value:
            raise RehydrationError(f"{brief_id} : {name} absent ou vide")
    if not isinstance(panel.get("reviews"), list) or not isinstance(panel.get("meta_review"), dict):
        raise RehydrationError(f"{brief_id} : panel_data sans reviews/meta_review")

    # node_research_brief recopie domains dans sharpened_data pour le front ;
    # le SharpeningOutput d'origine ne le porte pas (le sidecar le confirme).
    sharpened = {k: v for k, v in sharpened_raw.items() if k != "domains"}
    row_domains = sharpened_raw.get("domains")
    side_domains = sidecar.get("domains")
    if row_domains and side_domains and list(row_domains) != list(side_domains):
        raise RehydrationError(f"{brief_id} : domains diffèrent entre la ligne et le sidecar")
    domains = list(row_domains or side_domains or [])
    if not domains:
        raise RehydrationError(f"{brief_id} : domains introuvables")

    hypothesis = sidecar.get("original_hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise RehydrationError(f"{brief_id} : original_hypothesis absente du sidecar")

    meta = panel["meta_review"]
    return {
        "hypothesis": hypothesis,
        "domains": domains,
        "grounding": grounding,
        "grounding_degraded": bool(row.get("low_evidence")),
        "kill_reason": None,
        "sharpened": sharpened,
        "protocol": protocol,
        "panel": panel,
        "meta_verdict": str(meta.get("verdict", row.get("panel_verdict") or "")),
        "revision_count": int(row.get("revision_count") or 0),
        "hypothesis_id": row.get("hypothesis_id") or brief_id,
        "brief_id": brief_id,
        "brief_md_path": row.get("brief_md_path") or "",
        "brief_json_path": row.get("brief_json_path") or "",
        "is_stub": False,
        "errors": [],
    }


def generation_date(row: dict[str, Any]) -> date:
    """Date d'origine du brief, tirée de ``created_at``.

    Args:
        row: Ligne ``briefs``.

    Returns:
        La date à passer en ``generated_on``.

    Raises:
        RehydrationError: ``created_at`` absent ou illisible.
    """
    created_at = row.get("created_at")
    try:
        return date.fromisoformat(str(created_at)[:10])
    except (TypeError, ValueError) as exc:
        raise RehydrationError(f"{row.get('id')} : created_at illisible ({created_at!r})") from exc


# ── Sauvegarde ──────────────────────────────────────────────────────────


def find_backup_for(brief_id: str, backup_dir: Path | None) -> tuple[Path, dict[str, Any]]:
    """Trouve et vérifie la sauvegarde S10-B couvrant un brief.

    Args:
        brief_id: Identifiant du brief.
        backup_dir: Sauvegarde imposée, ou ``None`` pour la plus récente
            ``data/backups/s10b-*`` qui couvre le brief.

    Returns:
        ``(répertoire, entrée du manifeste pour ce brief)``.

    Raises:
        BackupError: Aucune sauvegarde vérifiée ne couvre le brief.
    """
    candidates = [backup_dir] if backup_dir is not None else sorted(
        BACKUP_ROOT.glob("s10b-*"), reverse=True
    )
    for candidate in candidates:
        manifest_path = candidate / "MANIFEST.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if brief_id not in manifest.get("briefs", {}):
            continue
        verified = verify_backup(candidate)
        return candidate, verified["briefs"][brief_id]
    raise BackupError(f"aucune sauvegarde S10-B vérifiée ne couvre {brief_id}")


def load_backup_sidecar(backup_dir: Path, brief_id: str) -> dict[str, Any]:
    """Charge la copie sauvegardée du sidecar ``.json``.

    Args:
        backup_dir: Répertoire de sauvegarde vérifié.
        brief_id: Identifiant du brief.

    Returns:
        Le sidecar d'origine.
    """
    return json.loads((backup_dir / "briefs" / f"{brief_id}.json").read_text(encoding="utf-8"))


# ── Garde-fous ──────────────────────────────────────────────────────────


def preflight(now: datetime | None = None, ignore_cron_window: bool = False) -> None:
    """Vérifie que le rejeu peut écrire sans gêner le cron.

    Args:
        now: Instant de référence (UTC), injectable pour les tests.
        ignore_cron_window: Désactive le contrôle de fenêtre horaire.

    Raises:
        PreflightError: Branche autre que master, modification suivie dans
            l'arbre, autopilot en cours, ou fenêtre du cron.
    """
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
    ).stdout.strip()
    if branch != "master":
        raise PreflightError(f"branche en checkout : {branch!r}, attendu 'master'")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
    ).stdout.strip()
    if dirty:
        raise PreflightError(f"modifications suivies non commitées :\n{dirty}")
    running = subprocess.run(
        ["pgrep", "-f", "cli.py autopilot"], capture_output=True, text=True, check=False
    )
    if running.returncode == 0:
        raise PreflightError(f"autopilot en cours (pid {running.stdout.split()})")
    if not ignore_cron_window:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).time()
        start, end = CRON_WINDOW_UTC
        if start <= current <= end:
            raise PreflightError(f"fenêtre du cron L0 ({start}–{end} UTC)")
    logger.info("replay_preflight_ok", branch=branch)


# ── Contrôles ───────────────────────────────────────────────────────────


def _panel_prose(panel: dict[str, Any]) -> str:
    """Toute la prose d'un panel (cartes + meta-review).

    Args:
        panel: Payload ``panel_data`` ou ``panel_data_en``.

    Returns:
        Le texte concaténé.
    """
    reviews = [card_texts(r) for r in panel.get("reviews") or [] if isinstance(r, dict)]
    return " ".join([*reviews, _meta_review_texts(panel.get("meta_review"))])


def replay_language_problems(panel: dict[str, Any], panel_en: dict[str, Any] | None) -> list[str]:
    """Les trois critères de langue retenus pour valider un rejeu.

    1. ``panel_data`` français : aucune carte signalée par le gate, meta-review
       non anglaise, et la prose entière détectée ``fr``.
    2. ``panel_data_en`` anglais : aucune carte signalée ``fr``, meta-review
       non française, prose entière détectée ``en``.
    3. ``panel_data_en`` réellement différent : aucune carte ni meta-review
       dont la prose EN est identique à la prose FR (symptôme de la
       traduction EN→EN dégénérée observée sur les 9 briefs).

    Args:
        panel: Panel français normalisé.
        panel_en: Traduction anglaise, ou ``None`` si absente.

    Returns:
        La liste des problèmes, vide si les trois critères passent.
    """
    problems: list[str] = []
    if check_panel_language(panel, "fr"):
        problems.append("panel_fr_cards_not_french")
    if detect(_meta_review_texts(panel.get("meta_review"))) == "en":
        problems.append("panel_fr_meta_english")
    if detect(_panel_prose(panel)) != "fr":
        problems.append("panel_fr_prose_not_detected_fr")

    if not isinstance(panel_en, dict) or not panel_en:
        problems.append("panel_en_missing")
        return problems
    if check_panel_language(panel_en, "en"):
        problems.append("panel_en_cards_not_english")
    if detect(_meta_review_texts(panel_en.get("meta_review"))) == "fr":
        problems.append("panel_en_meta_french")
    if detect(_panel_prose(panel_en)) != "en":
        problems.append("panel_en_prose_not_detected_en")

    fr_reviews = panel.get("reviews") or []
    en_reviews = panel_en.get("reviews") or []
    for idx, (fr_card, en_card) in enumerate(zip(fr_reviews, en_reviews)):
        if isinstance(fr_card, dict) and isinstance(en_card, dict):
            text = card_texts(fr_card)
            if text and text == card_texts(en_card):
                problems.append(f"panel_en_card_{idx}_identical_to_fr")
    fr_meta = _meta_review_texts(panel.get("meta_review"))
    if fr_meta and fr_meta == _meta_review_texts(panel_en.get("meta_review")):
        problems.append("panel_en_meta_identical_to_fr")
    return problems


def sidecar_mismatches(row: dict[str, Any], sidecar_path: Path) -> list[str]:
    """Compare le sidecar ``.json`` à la ligne ``briefs``.

    Args:
        row: Ligne relue après écriture.
        sidecar_path: Chemin du sidecar.

    Returns:
        Les clés divergentes (vide si cohérent).
    """
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["sidecar_unreadable"]
    pairs = {
        "panel": "panel_data",
        "vulgarization_fr": "vulgarization_data",
        "panel_en": "panel_data_en",
        "vulgarization_en": "vulgarization_data_en",
    }
    mismatches = [key for key, col in pairs.items() if sidecar.get(key) != _loads(row.get(col))]
    if sidecar.get("brief_id") != row.get("id"):
        mismatches.append("brief_id")
    return mismatches


def invariant_changes(row: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Colonnes invariantes modifiées par rapport à la sauvegarde.

    Args:
        row: Ligne actuelle.
        snapshot: Instantané ``row`` du manifeste.

    Returns:
        ``{colonne: (avant, après)}`` pour chaque écart.
    """
    return {
        col: (snapshot.get(col), row.get(col))
        for col in INVARIANT_COLUMNS
        if snapshot.get(col) != row.get(col)
    }


# ── Estimation du dry-run ───────────────────────────────────────────────


def _translation_cost(chars: int, calls: int, pricing: dict[str, float]) -> float:
    """Coût estimé d'une traduction.

    Args:
        chars: Caractères de prose à traduire.
        calls: Nombre d'appels LLM (un par champ).
        pricing: Tarif ``TokenTracker.PRICING`` du modèle.

    Returns:
        Le coût en USD.
    """
    tokens_in = chars / CHARS_PER_TOKEN + calls * TRANSLATION_PROMPT_OVERHEAD_TOKENS
    tokens_out = chars / CHARS_PER_TOKEN * TRANSLATION_OUTPUT_RATIO
    return (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000


def estimate_replay_cost(
    state: PostFireState,
    card_indices: list[int],
    meta_english: bool,
    old_vulgarization: dict[str, Any] | None,
) -> dict[str, float]:
    """Estime le coût LLM du rejeu d'un brief, sans aucun appel.

    Args:
        state: État rehydraté.
        card_indices: Cartes à traduire EN→FR.
        meta_english: Meta-review à traduire EN→FR.
        old_vulgarization: Ancienne vulgarisation, pour la taille de sortie.

    Returns:
        Coût par étape et total, en USD.
    """
    pricing = TokenTracker.PRICING[ESTIMATE_MODEL]
    panel = state["panel"]
    reviews = panel.get("reviews") or []
    card_calls = len(REVIEWER_LIST_FIELDS) + len(REVIEWER_STRING_FIELDS)
    meta_calls = len(META_LIST_FIELDS) + len(META_STRING_FIELDS)

    fr_chars = sum(len(card_texts(reviews[i])) for i in card_indices)
    fr_calls = card_calls * len(card_indices)
    if meta_english:
        fr_chars += len(_meta_review_texts(panel.get("meta_review")))
        fr_calls += meta_calls
    normalize = _translation_cost(fr_chars, fr_calls, pricing) if fr_calls else 0.0

    vulg_chars = len(json.dumps(old_vulgarization or {}, ensure_ascii=False))
    vulg_in = (
        len(load_prompt("vulgarization_fr"))
        + len(_panel_prose(panel))
        + len(json.dumps(state["sharpened"], ensure_ascii=False))
        + len(json.dumps(state["protocol"], ensure_ascii=False))
    ) / CHARS_PER_TOKEN
    vulgarization = (
        vulg_in * pricing["input"] + vulg_chars / CHARS_PER_TOKEN * pricing["output"]
    ) / 1_000_000

    panel_en = _translation_cost(
        len(_panel_prose(panel)), card_calls * len(reviews) + meta_calls, pricing
    )
    vulg_en = _translation_cost(vulg_chars, max(len(old_vulgarization or {}), 1), pricing)
    total = normalize + vulgarization + panel_en + vulg_en
    return {
        "normalize_usd": round(normalize, 6),
        "vulgarization_usd": round(vulgarization, 6),
        "translation_en_usd": round(panel_en + vulg_en, 6),
        "total_usd": round(total, 6),
    }


# ── Rejeu ───────────────────────────────────────────────────────────────


async def rewrite_brief_artifacts(state: PostFireState, generated_on: date) -> PostFireState:
    """Régénère le ``.md``, le ``.json`` et met à jour la ligne existante.

    Conserve le ``brief_id`` : les fichiers sont réécrits sous le même nom et
    la ligne est modifiée par ``update_brief`` (``UPDATE ... WHERE id``),
    jamais réinsérée. ``created_at``, les scores, le verdict et
    ``revision_count`` ne figurent pas dans l'UPDATE. Les colonnes qui
    dérivent du panel anglais (``vulgarization_data``, ``panel_data_en``,
    ``vulgarization_data_en``) sont remises à NULL dans le même UPDATE :
    elles sont régénérées juste après, et la ligne ne porte jamais un
    mélange du nouveau panel et des anciens dérivés. La ligne reste en
    'pending' pendant toute l'opération, donc invisible du site.

    Args:
        state: État avec le panel normalisé.
        generated_on: Date d'origine du brief.

    Returns:
        L'état avec les chemins réécrits.

    Raises:
        RehydrationError: Le générateur n'a rien écrit, les chemins ne
            correspondent pas à ceux de la ligne, ou la ligne a disparu.
    """
    brief_id = state["brief_id"]
    panel = state["panel"]
    md_path, json_path = await write_brief_files(
        brief_id=brief_id,
        hypothesis=state["hypothesis"],
        domains=state["domains"],
        grounding=state["grounding"],
        sharpened=state["sharpened"],
        protocol=state["protocol"],
        panel={"reviews": panel["reviews"], "meta_review": panel["meta_review"]},
        generated_on=generated_on,
    )
    if md_path is None or json_path is None:
        raise RehydrationError(f"{brief_id} : le générateur n'a pas écrit le brief")
    for label, written, expected in (
        ("md", md_path, state.get("brief_md_path")),
        ("json", json_path, state.get("brief_json_path")),
    ):
        if expected and Path(expected).resolve() != Path(written).resolve():
            raise RehydrationError(
                f"{brief_id} : chemin {label} écrit {written} ≠ ligne {expected}"
            )

    body_markdown = Path(md_path).read_text(encoding="utf-8")
    await init_database()
    updated = await update_brief(
        brief_id,
        panel_data=json.dumps(panel),
        body_markdown=body_markdown,
        brief_md_path=str(md_path),
        brief_json_path=str(json_path),
        vulgarization_data=None,
        panel_data_en=None,
        vulgarization_data_en=None,
    )
    if not updated:
        raise RehydrationError(f"{brief_id} : ligne absente au moment de l'UPDATE")
    logger.info(
        "replay_brief_rewritten",
        brief_id=brief_id,
        md_path=str(md_path),
        generated_on=generated_on.isoformat(),
        body_markdown_chars=len(body_markdown),
    )
    return {**state, "brief_md_path": str(md_path), "brief_json_path": str(json_path)}


def _blocked(outcome: ReplayOutcome, reason: str, **details: Any) -> ReplayOutcome:
    """Journalise et renvoie un résultat ``still_blocked``.

    Args:
        outcome: Résultat en cours de construction.
        reason: Motif du blocage.
        **details: Contexte ajouté au journal et au compte rendu.

    Returns:
        Le résultat mis à jour.
    """
    outcome.outcome = "still_blocked"
    outcome.reason = reason
    outcome.cost_usd = round(get_token_tracker().total_cost, 6)
    outcome.details.update(details)
    logger.error(
        "replay_still_blocked",
        brief_id=outcome.brief_id,
        reason=reason,
        cost_usd=outcome.cost_usd,
        **details,
    )
    return outcome


async def replay_brief(
    brief_id: str,
    *,
    dry_run: bool,
    backup_dir: Path | None = None,
) -> ReplayOutcome:
    """Rejoue la queue du pipeline sur un brief.

    Args:
        brief_id: Identifiant du brief.
        dry_run: N'écrire rien et n'appeler aucun LLM.
        backup_dir: Sauvegarde imposée (sinon la plus récente qui le couvre).

    Returns:
        Le résultat du traitement.
    """
    db_path = get_settings().db_path
    outcome = ReplayOutcome(brief_id=brief_id, outcome="dry_run" if dry_run else "completed")

    row = load_brief_row(db_path, brief_id)
    if row is None:
        outcome.outcome, outcome.reason = "skipped", "not_found"
        logger.warning("replay_skipped", brief_id=brief_id, reason="not_found")
        return outcome
    skip = scope_skip_reason(row)
    if skip is not None:
        outcome.outcome, outcome.reason = "skipped", skip
        logger.info("replay_skipped", brief_id=brief_id, reason=skip, status=row.get("status"))
        return outcome

    try:
        backup_path, entry = find_backup_for(brief_id, backup_dir)
        sidecar = load_backup_sidecar(backup_path, brief_id)
        backup_label = str(backup_path)
    except BackupError as exc:
        if not dry_run:
            return _blocked(outcome, "no_verified_backup", error=str(exc))
        entry = {"row": {col: row.get(col) for col in INVARIANT_COLUMNS}}
        sidecar = json.loads(Path(row["brief_json_path"]).read_text(encoding="utf-8"))
        backup_label = f"AUCUNE ({exc})"

    try:
        state = rehydrate_state(row, sidecar)
        generated_on = generation_date(row)
    except RehydrationError as exc:
        return _blocked(outcome, "rehydration_failed", error=str(exc))

    panel = state["panel"]
    card_indices = _flagged_card_indices(panel)
    personas = [panel["reviews"][i].get("reviewer_persona", "?") for i in card_indices]
    meta_english = detect(_meta_review_texts(panel.get("meta_review"))) == "en"
    outcome.cards_translated = len(card_indices)
    outcome.meta_translated = meta_english
    outcome.details.update(
        {
            "backup": backup_label,
            "cards": personas,
            "meta_english": meta_english,
            "generated_on": generated_on.isoformat(),
            "invariants": {col: row.get(col) for col in INVARIANT_COLUMNS},
        }
    )

    if dry_run:
        estimate = estimate_replay_cost(state, card_indices, meta_english, _loads(row.get("vulgarization_data")))
        outcome.cost_usd = estimate["total_usd"]
        outcome.details.update(
            {
                "estimate": estimate,
                "gate_language_now": len(check_panel_language(panel, "fr")),
                "gate_coherence_now": check_panel(panel),
                "hypothesis_chars": len(state["hypothesis"]),
                "domains": state["domains"],
            }
        )
        logger.info("replay_dry_run", brief_id=brief_id, **outcome.details)
        return outcome

    reset_token_tracker()
    logger.info(
        "replay_started",
        brief_id=brief_id,
        backup=backup_label,
        cards=personas,
        meta_english=meta_english,
        generated_on=generated_on.isoformat(),
    )

    # 2. Normalisation — le nœud du graphe, tel quel. Aucune écriture avant
    # que le panel ne passe les deux gates.
    state = await node_normalize_panel_language(state)
    normalize_cost = round(get_token_tracker().total_cost, 6)
    logger.info(
        "replay_cards_translated",
        brief_id=brief_id,
        cards_translated=len(card_indices),
        meta_translated=meta_english,
        cost_usd=normalize_cost,
    )
    remaining = check_panel_language(state["panel"], "fr")
    if remaining or detect(_meta_review_texts(state["panel"].get("meta_review"))) == "en":
        return _blocked(outcome, "normalization_failed", remaining=remaining)
    incoherent = check_panel(state["panel"])
    if incoherent:
        return _blocked(outcome, "panel_incoherent", problems=incoherent)

    # 3. Régénération du brief sous le même identifiant.
    try:
        state = await rewrite_brief_artifacts(state, generated_on)
    except RehydrationError as exc:
        return _blocked(outcome, "rewrite_failed", error=str(exc))

    # 4. Vulgarisation puis traduction EN — nœuds du graphe, tels quels.
    state = await node_vulgarization(state)
    if not state.get("vulgarization_fr"):
        return _blocked(outcome, "vulgarization_failed")
    state = await node_translation_hook(state)
    if not state.get("panel_en") or not state.get("vulgarization_en"):
        return _blocked(
            outcome,
            "translation_failed",
            panel_en=bool(state.get("panel_en")),
            vulgarization_en=bool(state.get("vulgarization_en")),
        )

    # 5. Contrôles propres au rejeu, avant toute promotion.
    language = replay_language_problems(state["panel"], state.get("panel_en"))
    if language:
        return _blocked(outcome, "translation_degenerate", problems=language)
    fresh = load_brief_row(db_path, brief_id) or {}
    changed = invariant_changes(fresh, entry["row"])
    if changed:
        return _blocked(outcome, "invariant_changed", changes={k: list(v) for k, v in changed.items()})
    mismatches = sidecar_mismatches(fresh, Path(state["brief_json_path"]))
    if mismatches:
        return _blocked(outcome, "sidecar_mismatch", keys=mismatches)

    # 6. Promotion — node_validate_brief et ses deux gates, rien d'autre.
    state = await node_validate_brief(state)
    outcome.cost_usd = round(get_token_tracker().total_cost, 6)
    if not state.get("brief_validated"):
        return _blocked(
            outcome,
            "validation_refused",
            missing_fields=state.get("missing_fields"),
            panel_incoherences=state.get("panel_incoherences"),
            panel_language_mismatches=state.get("panel_language_mismatches"),
        )

    final = load_brief_row(db_path, brief_id) or {}
    outcome.details["status"] = final.get("status")
    logger.info(
        "replay_completed",
        brief_id=brief_id,
        status=final.get("status"),
        cost_usd=outcome.cost_usd,
        normalize_cost_usd=normalize_cost,
        cards_translated=len(card_indices),
        meta_translated=meta_english,
    )
    return outcome


def pending_brief_ids(db_path: Path) -> list[str]:
    """Briefs du périmètre S10-B, dans l'ordre chronologique.

    Args:
        db_path: Base SQLite.

    Returns:
        Les identifiants.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(PENDING_SCOPE_SQL)]
    finally:
        conn.close()


def _print_outcome(result: ReplayOutcome) -> None:
    """Affiche le compte rendu d'un brief.

    Args:
        result: Résultat du traitement.
    """
    head = f"{result.brief_id}  {result.outcome}"
    if result.reason:
        head += f" ({result.reason})"
    print(head)
    if result.outcome == "skipped":
        return
    d = result.details
    print(f"  sauvegarde     : {d.get('backup')}")
    print(f"  à traduire     : {result.cards_translated} carte(s) {d.get('cards')}, meta EN={d.get('meta_english')}")
    print(f"  generated_on   : {d.get('generated_on')}")
    print(f"  invariants     : {d.get('invariants')}")
    if result.outcome == "dry_run":
        est = d["estimate"]
        print(f"  gates actuels  : langue={d['gate_language_now']} carte(s) signalée(s), cohérence={d['gate_coherence_now'] or 'OK'}")
        print(f"  hypothèse      : {d['hypothesis_chars']} car. (sidecar) ; domains={d['domains']}")
        print(
            "  écritures      : .md, .json, UPDATE briefs(panel_data, body_markdown, chemins,"
            " vulgarization_data/panel_data_en/vulgarization_data_en → NULL puis régénérés),"
            " promotion par node_validate_brief"
        )
        print(
            f"  coût estimé    : ${est['total_usd']:.4f} (normalisation ${est['normalize_usd']:.4f},"
            f" vulgarisation ${est['vulgarization_usd']:.4f}, EN ${est['translation_en_usd']:.4f})"
        )
    else:
        print(f"  coût réel      : ${result.cost_usd:.4f}")
        extra = {k: v for k, v in d.items() if k not in {"backup", "cards", "meta_english", "generated_on", "invariants"}}
        if extra:
            print(f"  détails        : {extra}")


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments.

    Returns:
        Le parseur configuré.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--brief-id", action="append", help="Brief à rejouer (répétable).")
    group.add_argument("--all-pending", action="store_true", help="Tous les briefs du périmètre S10-B.")
    group.add_argument("--restore-brief", metavar="BRIEF_ID", help="Restaurer ce brief depuis --from.")
    parser.add_argument("--from", dest="restore_from", type=Path, help="Sauvegarde source de --restore-brief.")
    parser.add_argument("--dry-run", action="store_true", help="Aucune écriture, aucun appel LLM.")
    parser.add_argument("--backup-dir", type=Path, help="Sauvegarde S10-B à utiliser (défaut : la plus récente).")
    parser.add_argument(
        "--ignore-cron-window",
        action="store_true",
        help=f"Écrire même dans la fenêtre du cron ({CRON_WINDOW_UTC[0]}–{CRON_WINDOW_UTC[1]} UTC).",
    )
    return parser


async def main() -> int:
    """Point d'entrée CLI.

    Returns:
        0 si aucun brief n'est resté bloqué, 1 sinon, 2 si un garde-fou refuse.
    """
    setup_logging()
    parser = _build_arg_parser()
    args = parser.parse_args()
    db_path = get_settings().db_path

    if args.restore_brief:
        if args.restore_from is None:
            parser.error("--restore-brief exige --from <dossier-de-sauvegarde>")
        try:
            preflight(ignore_cron_window=args.ignore_cron_window)
            summary = restore_brief(args.restore_from, args.restore_brief, db_path)
        except PreflightError as exc:
            logger.error("replay_preflight_refused", error=str(exc))
            print(f"REFUS — {exc}", file=sys.stderr)
            return 2
        except RestoreError as exc:
            logger.error("restore_refused_or_failed", brief_id=args.restore_brief, error=str(exc))
            print(f"ÉCHEC DE RESTAURATION — {exc}", file=sys.stderr)
            return 3
        print(
            f"{summary['brief_id']}  restauré depuis {summary['backup']} (format {summary['format']})"
            f" — status={summary['status']}, colonnes {summary['columns_verified']},"
            f" fichiers {summary['files_verified']} : hashes vérifiés"
        )
        return 0

    brief_ids = pending_brief_ids(db_path) if args.all_pending else list(args.brief_id)
    if not brief_ids:
        print("Aucun brief à rejouer.")
        return 0

    if not args.dry_run:
        try:
            preflight(ignore_cron_window=args.ignore_cron_window)
        except PreflightError as exc:
            logger.error("replay_preflight_refused", error=str(exc))
            print(f"REFUS — {exc}", file=sys.stderr)
            return 2

    results: list[ReplayOutcome] = []
    for brief_id in brief_ids:
        result = await replay_brief(brief_id, dry_run=args.dry_run, backup_dir=args.backup_dir)
        results.append(result)
        _print_outcome(result)
        if result.outcome == "still_blocked":
            remaining = brief_ids[len(results):]
            if remaining:
                print(f"Arrêt : {brief_id} reste bloqué, {len(remaining)} brief(s) non traité(s) : {remaining}")
            break

    total = sum(r.cost_usd for r in results)
    label = "estimé" if args.dry_run else "réel"
    counts = {k: sum(1 for r in results if r.outcome == k) for k in ("completed", "still_blocked", "skipped", "dry_run")}
    print(f"\nTotal {label} : ${total:.4f} — {counts}")
    return 1 if counts["still_blocked"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
