"""Tables additives de la couche narrative v2 (``v2_*``).

Source du schéma : ``docs/v2/DATA_CONTRACT.md`` (dépôt ``spore-v2``). Ce module
est volontairement séparé de ``storage/database.py`` (cœur) : il ne crée que
des tables préfixées ``v2_`` par ``CREATE TABLE IF NOT EXISTS`` et
``CREATE INDEX IF NOT EXISTS``, n'ajoute aucune colonne aux tables existantes
et ne touche à aucune ligne v1. La v1 fonctionne sur une base migrée.

Accès synchrone (``sqlite3``) : le même code sert au script de backfill et,
via ``asyncio.to_thread``, aux nœuds du sous-graphe narratif. Chaque opération
ouvre une connexion courte ; aucune transaction ne reste ouverte pendant un
appel LLM.

Pas de clé étrangère vers ``hypotheses`` : sur le chemin L0, le post-fire
tourne avant ``save_hypothesis`` (recon pipeline §12, piège 7), la ligne
``hypotheses`` n'existe donc pas encore quand le lien est écrit.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Tables créées par ce module, dans l'ordre de création.
NARRATIVE_TABLES: tuple[str, ...] = (
    "v2_stories",
    "v2_brief_themes",
    "v2_brief_hypothesis",
    "v2_brief_neighbours",
    "v2_llm_costs",
)

#: DDL exact du contrat de données. Idempotent, strictement additif.
NARRATIVE_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS v2_stories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brief_id TEXT NOT NULL,
        lang TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        status TEXT NOT NULL,
        title TEXT,
        story_year INTEGER,
        story_place TEXT,
        body_md TEXT,
        mechanism TEXT,
        limit_staged TEXT,
        source_story_id INTEGER NULL,
        guard_report_json TEXT,
        writer_model TEXT,
        guard_model TEXT,
        prompt_version TEXT,
        guard_prompt_version TEXT,
        body_sha256 TEXT,
        cost_usd REAL,
        tokens_in INTEGER,
        tokens_out INTEGER,
        run_label TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_v2_stories_brief_lang_attempt
        ON v2_stories(brief_id, lang, attempt)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_v2_stories_one_published
        ON v2_stories(brief_id, lang) WHERE status = 'published'
    """,
    """
    CREATE TABLE IF NOT EXISTS v2_brief_themes (
        brief_id TEXT NOT NULL,
        theme_slug TEXT NOT NULL,
        source TEXT NOT NULL,
        mapping_version TEXT NOT NULL,
        created_at TEXT,
        PRIMARY KEY (brief_id, theme_slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v2_brief_hypothesis (
        brief_id TEXT PRIMARY KEY,
        hypothesis_id TEXT NOT NULL,
        method TEXT NOT NULL,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v2_brief_neighbours (
        brief_id TEXT NOT NULL,
        neighbour_id TEXT NOT NULL,
        rank INTEGER NOT NULL,
        score REAL,
        method TEXT NOT NULL,
        computed_at TEXT,
        PRIMARY KEY (brief_id, neighbour_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v2_llm_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_label TEXT NOT NULL,
        brief_id TEXT NULL,
        node TEXT NOT NULL,
        model TEXT NOT NULL,
        tokens_in INTEGER,
        tokens_out INTEGER,
        cost_usd REAL NOT NULL,
        created_at TEXT
    )
    """,
)

#: Valeurs admises, contrôlées en Python (le contrat ne pose pas de CHECK).
STORY_LANGS = frozenset({"fr", "en"})
STORY_STATUSES = frozenset({"draft", "published", "rejected"})
LINK_METHODS = frozenset({"fk", "text_match", "pipeline_state"})
THEME_SOURCES = frozenset({"domain", "parent_domain", "fallback"})
NEIGHBOUR_METHODS = frozenset(
    {"domain_embedding", "theme", "domain", "inbound_patch", "stub_ring"}
)

#: Prédicat de publication du front (``src/lib/brief-visibility.ts``).
PUBLISHED_BRIEF_PREDICATE = (
    "((status = 'complete' AND hypothesis_id IS NOT NULL) OR is_stub = 1)"
)

#: Colonnes de ``v2_stories`` que les écritures acceptent.
_STORY_COLUMNS = frozenset(
    {
        "brief_id",
        "lang",
        "attempt",
        "status",
        "title",
        "story_year",
        "story_place",
        "body_md",
        "mechanism",
        "limit_staged",
        "source_story_id",
        "guard_report_json",
        "writer_model",
        "guard_model",
        "prompt_version",
        "guard_prompt_version",
        "body_sha256",
        "cost_usd",
        "tokens_in",
        "tokens_out",
        "run_label",
    }
)

#: Délai d'attente d'un verrou d'écriture (le pipeline et l'API écrivent aussi).
BUSY_TIMEOUT_MS = 30_000


def utc_now() -> str:
    """Horodatage ISO 8601 UTC au format du contrat (``YYYY-MM-DDTHH:MM:SSZ``).

    Returns:
        Horodatage courant.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def connect(db_path: str | Path, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """Ouvre une connexion courte, fermée à la sortie du bloc.

    Args:
        db_path: Base SQLite.
        readonly: Ouvrir en lecture seule (``mode=ro``) ; la base doit exister.

    Yields:
        Connexion avec ``row_factory = sqlite3.Row``.
    """
    if readonly:
        uri = f"file:{Path(db_path).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    else:
        conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        yield conn
    finally:
        conn.close()


def ensure_narrative_schema(conn: sqlite3.Connection) -> None:
    """Crée les tables et index ``v2_*`` s'ils manquent (idempotent, additif).

    Args:
        conn: Connexion en écriture.
    """
    with conn:
        for statement in NARRATIVE_SCHEMA_STATEMENTS:
            conn.execute(statement)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Indique si une table existe.

    Args:
        conn: Connexion.
        name: Nom de table.

    Returns:
        ``True`` si la table existe.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def narrative_schema_present(conn: sqlite3.Connection) -> bool:
    """Indique si toutes les tables ``v2_*`` existent.

    Args:
        conn: Connexion.

    Returns:
        ``True`` si les cinq tables existent.
    """
    return all(table_exists(conn, name) for name in NARRATIVE_TABLES)


# ── Briefs (lecture seule) ───────────────────────────────────────────


def fetch_brief(conn: sqlite3.Connection, brief_id: str) -> dict[str, Any] | None:
    """Lit une ligne ``briefs``.

    Args:
        conn: Connexion.
        brief_id: Identifiant ``SPR-YYYY-XXXX``.

    Returns:
        La ligne sous forme de dictionnaire, ou ``None``.
    """
    row = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    return dict(row) if row is not None else None


def is_full_published_brief(row: Mapping[str, Any] | None) -> bool:
    """Brief complet (non-stub) et publié au sens du front.

    Args:
        row: Ligne ``briefs`` ou ``None``.

    Returns:
        ``True`` si ``status='complete'``, ``hypothesis_id`` renseigné et
        ``is_stub`` nul.
    """
    if not row:
        return False
    return (
        row.get("status") == "complete"
        and row.get("hypothesis_id") is not None
        and not row.get("is_stub")
    )


def list_published_briefs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Briefs publiés (complets et stubs), ordonnés par identifiant.

    Args:
        conn: Connexion.

    Returns:
        Lignes avec ``id``, ``hypothesis_id``, ``created_at``, ``is_stub`` et
        ``sharpened_data``.
    """
    rows = conn.execute(
        "SELECT id, hypothesis_id, created_at, COALESCE(is_stub, 0) AS is_stub, "
        "sharpened_data FROM briefs WHERE "
        + PUBLISHED_BRIEF_PREDICATE
        + " ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def brief_domains(row: Mapping[str, Any]) -> list[str]:
    """Domaines de la collision, lus dans ``sharpened_data.domains``.

    Args:
        row: Ligne ``briefs`` (au moins ``sharpened_data``).

    Returns:
        Noms de domaines, dans l'ordre stocké, sans doublon ni vide.
    """
    raw = row.get("sharpened_data")
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        return []
    domains = data.get("domains") if isinstance(data, dict) else None
    seen: list[str] = []
    for item in domains or []:
        if isinstance(item, str) and item.strip() and item.strip() not in seen:
            seen.append(item.strip())
    return seen


# ── v2_stories ──────────────────────────────────────────────────────


def _check_story_fields(fields: Mapping[str, Any]) -> None:
    unknown = set(fields) - _STORY_COLUMNS
    if unknown:
        raise ValueError(f"colonnes v2_stories inconnues : {sorted(unknown)}")
    if "lang" in fields and fields["lang"] not in STORY_LANGS:
        raise ValueError(f"langue de récit invalide : {fields['lang']!r}")
    if "status" in fields and fields["status"] not in STORY_STATUSES:
        raise ValueError(f"statut de récit invalide : {fields['status']!r}")


def insert_story(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insère une tentative de récit.

    Args:
        conn: Connexion en écriture.
        **fields: Colonnes de ``v2_stories`` (``brief_id``, ``lang``,
            ``attempt`` et ``status`` obligatoires).

    Returns:
        Identifiant de la ligne.

    Raises:
        ValueError: Colonne ou valeur inconnue.
        sqlite3.IntegrityError: Tentative déjà enregistrée, ou second récit
            publié pour la même langue.
    """
    _check_story_fields(fields)
    for required in ("brief_id", "lang", "attempt", "status"):
        if fields.get(required) in (None, ""):
            raise ValueError(f"champ obligatoire absent : {required}")
    now = utc_now()
    columns = [*fields.keys(), "created_at", "updated_at"]
    values = [*fields.values(), now, now]
    placeholders = ", ".join("?" for _ in columns)
    with conn:
        cursor = conn.execute(
            f"INSERT INTO v2_stories ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
    return int(cursor.lastrowid)


def update_story(conn: sqlite3.Connection, story_id: int, **fields: Any) -> None:
    """Met à jour une tentative de récit.

    Args:
        conn: Connexion en écriture.
        story_id: Identifiant de la ligne.
        **fields: Colonnes à modifier.

    Raises:
        ValueError: Colonne ou valeur inconnue.
        sqlite3.IntegrityError: Second récit publié pour la même langue.
    """
    _check_story_fields(fields)
    if not fields:
        return
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with conn:
        conn.execute(
            f"UPDATE v2_stories SET {assignments}, updated_at = ? WHERE id = ?",
            [*fields.values(), utc_now(), story_id],
        )


def finalize_story(conn: sqlite3.Connection, story_id: int, **fields: Any) -> bool:
    """Statue sur un brouillon : ne modifie la ligne que si elle est ``draft``.

    Le délai global de la couche peut rejeter les brouillons ouverts
    (``reject_open_drafts``) pendant qu'un garde termine dans un fil : la
    condition ``status = 'draft'`` rend ce rejet définitif.

    Args:
        conn: Connexion en écriture.
        story_id: Identifiant de la ligne.
        **fields: Colonnes à écrire (``status`` compris).

    Returns:
        ``True`` si la ligne était encore un brouillon et a été modifiée.

    Raises:
        ValueError: Colonne ou valeur inconnue.
        sqlite3.IntegrityError: Second récit publié pour la même langue.
    """
    _check_story_fields(fields)
    if not fields:
        return False
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with conn:
        cursor = conn.execute(
            f"UPDATE v2_stories SET {assignments}, updated_at = ? "
            "WHERE id = ? AND status = 'draft'",
            [*fields.values(), utc_now(), story_id],
        )
    return cursor.rowcount == 1


def get_story(conn: sqlite3.Connection, story_id: int) -> dict[str, Any] | None:
    """Lit une tentative de récit.

    Args:
        conn: Connexion.
        story_id: Identifiant.

    Returns:
        La ligne, ou ``None``.
    """
    row = conn.execute("SELECT * FROM v2_stories WHERE id = ?", (story_id,)).fetchone()
    return dict(row) if row is not None else None


def published_story(
    conn: sqlite3.Connection, brief_id: str, lang: str
) -> dict[str, Any] | None:
    """Récit publié d'un brief dans une langue.

    Args:
        conn: Connexion.
        brief_id: Brief.
        lang: ``fr`` ou ``en``.

    Returns:
        La ligne publiée, ou ``None``.
    """
    row = conn.execute(
        "SELECT * FROM v2_stories WHERE brief_id = ? AND lang = ? AND status = 'published'",
        (brief_id, lang),
    ).fetchone()
    return dict(row) if row is not None else None


def next_attempt(conn: sqlite3.Connection, brief_id: str, lang: str) -> int:
    """Numéro de la prochaine tentative pour ``(brief, langue)``.

    Args:
        conn: Connexion.
        brief_id: Brief.
        lang: ``fr`` ou ``en``.

    Returns:
        ``max(attempt) + 1``, ou 1 si aucune tentative.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) FROM v2_stories WHERE brief_id = ? AND lang = ?",
        (brief_id, lang),
    ).fetchone()
    return int(row[0]) + 1


def reject_open_drafts(conn: sqlite3.Connection, brief_id: str, reason: str) -> int:
    """Passe en ``rejected`` les brouillons restés ouverts d'un brief.

    Un brouillon ouvert signifie que le garde n'a pas statué (délai global,
    panne) : fail-closed, il ne sera jamais publié.

    Args:
        conn: Connexion en écriture.
        brief_id: Brief.
        reason: Code de raison ajouté au rapport de garde.

    Returns:
        Nombre de lignes modifiées.
    """
    rows = conn.execute(
        "SELECT id, guard_report_json FROM v2_stories WHERE brief_id = ? AND status = 'draft'",
        (brief_id,),
    ).fetchall()
    changed = 0
    for row in rows:
        try:
            report = json.loads(row["guard_report_json"]) if row["guard_report_json"] else {}
        except ValueError:
            report = {}
        if not isinstance(report, dict):
            report = {}
        report["decision"] = "rejected"
        report.setdefault("reasons", []).append(reason)
        if finalize_story(
            conn,
            int(row["id"]),
            status="rejected",
            guard_report_json=json.dumps(report, ensure_ascii=False),
        ):
            changed += 1
    return changed


# ── v2_brief_themes ─────────────────────────────────────────────────


def replace_brief_themes(
    conn: sqlite3.Connection,
    brief_id: str,
    themes: Sequence[tuple[str, str]],
    mapping_version: str,
) -> None:
    """Remplace les thèmes d'un brief (une transaction).

    Args:
        conn: Connexion en écriture.
        brief_id: Brief.
        themes: Couples ``(slug, source)``.
        mapping_version: Version de la table de correspondance.

    Raises:
        ValueError: Source inconnue.
    """
    for _slug, source in themes:
        if source not in THEME_SOURCES:
            raise ValueError(f"source de thème invalide : {source!r}")
    now = utc_now()
    with conn:
        conn.execute("DELETE FROM v2_brief_themes WHERE brief_id = ?", (brief_id,))
        conn.executemany(
            "INSERT INTO v2_brief_themes (brief_id, theme_slug, source, mapping_version, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            [(brief_id, slug, source, mapping_version, now) for slug, source in themes],
        )


def themes_by_brief(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Thèmes enregistrés, par brief.

    Args:
        conn: Connexion.

    Returns:
        ``{brief_id: [slug, …]}`` (vide si la table manque).
    """
    if not table_exists(conn, "v2_brief_themes"):
        return {}
    result: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT brief_id, theme_slug FROM v2_brief_themes ORDER BY brief_id, theme_slug"
    ):
        result.setdefault(row["brief_id"], []).append(row["theme_slug"])
    return result


# ── v2_brief_hypothesis ─────────────────────────────────────────────


def upsert_brief_hypothesis(
    conn: sqlite3.Connection,
    brief_id: str,
    hypothesis_id: str,
    method: str,
    *,
    overwrite: bool = True,
) -> None:
    """Enregistre le lien brief ↔ hypothèse.

    Args:
        conn: Connexion en écriture.
        brief_id: Brief.
        hypothesis_id: ``hypotheses.id``.
        method: ``fk``, ``text_match`` ou ``pipeline_state``.
        overwrite: Remplacer un lien existant (sinon il est conservé).

    Raises:
        ValueError: Méthode inconnue.
    """
    if method not in LINK_METHODS:
        raise ValueError(f"méthode de lien invalide : {method!r}")
    conflict = (
        "DO UPDATE SET hypothesis_id = excluded.hypothesis_id, method = excluded.method, "
        "created_at = excluded.created_at"
        if overwrite
        else "DO NOTHING"
    )
    with conn:
        conn.execute(
            "INSERT INTO v2_brief_hypothesis (brief_id, hypothesis_id, method, created_at) "
            f"VALUES (?, ?, ?, ?) ON CONFLICT(brief_id) {conflict}",
            (brief_id, hypothesis_id, method, utc_now()),
        )


def links_by_brief(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Liens enregistrés, par brief.

    Args:
        conn: Connexion.

    Returns:
        ``{brief_id: (hypothesis_id, method)}`` (vide si la table manque).
    """
    if not table_exists(conn, "v2_brief_hypothesis"):
        return {}
    return {
        row["brief_id"]: (row["hypothesis_id"], row["method"])
        for row in conn.execute("SELECT brief_id, hypothesis_id, method FROM v2_brief_hypothesis")
    }


# ── v2_brief_neighbours ─────────────────────────────────────────────


def replace_all_neighbours(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[str, str, int, float | None, str]],
) -> int:
    """Remplace tout le maillage (recalcul complet, une transaction).

    Args:
        conn: Connexion en écriture.
        rows: ``(brief_id, neighbour_id, rank, score, method)``.

    Returns:
        Nombre de lignes écrites.

    Raises:
        ValueError: Méthode inconnue ou lien vers soi-même.
    """
    now = utc_now()
    materialised = []
    for brief_id, neighbour_id, rank, score, method in rows:
        if method not in NEIGHBOUR_METHODS:
            raise ValueError(f"méthode de voisinage invalide : {method!r}")
        if brief_id == neighbour_id:
            raise ValueError(f"voisin de soi-même : {brief_id}")
        materialised.append((brief_id, neighbour_id, rank, score, method, now))
    with conn:
        conn.execute("DELETE FROM v2_brief_neighbours")
        conn.executemany(
            "INSERT INTO v2_brief_neighbours (brief_id, neighbour_id, rank, score, method, "
            "computed_at) VALUES (?, ?, ?, ?, ?, ?)",
            materialised,
        )
    return len(materialised)


# ── v2_llm_costs ────────────────────────────────────────────────────


def insert_llm_cost(
    conn: sqlite3.Connection,
    *,
    run_label: str,
    brief_id: str | None,
    node: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
) -> int:
    """Enregistre le coût d'un appel LLM.

    Args:
        conn: Connexion en écriture.
        run_label: Étiquette du run (``pipeline``, ``backfill``…).
        brief_id: Brief concerné, s'il y en a un.
        node: Nœud appelant (``story_writer``…).
        model: Modèle facturé (clé de tarification du ``TokenTracker``).
        tokens_in: Jetons d'entrée.
        tokens_out: Jetons de sortie.
        cost_usd: Coût calculé par le ``TokenTracker``.

    Returns:
        Identifiant de la ligne.
    """
    with conn:
        cursor = conn.execute(
            "INSERT INTO v2_llm_costs (run_label, brief_id, node, model, tokens_in, tokens_out, "
            "cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_label,
                brief_id,
                node,
                model or "unknown",
                int(tokens_in),
                int(tokens_out),
                float(cost_usd),
                utc_now(),
            ),
        )
    return int(cursor.lastrowid)


def sum_costs(
    conn: sqlite3.Connection,
    labels: Iterable[str] | None = None,
    *,
    since_id: int = 0,
) -> float:
    """Somme des coûts enregistrés.

    Args:
        conn: Connexion.
        labels: Étiquettes retenues (toutes si ``None``).
        since_id: Ne compter que les lignes d'identifiant supérieur.

    Returns:
        Total en USD (0 si la table manque).
    """
    if not table_exists(conn, "v2_llm_costs"):
        return 0.0
    sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM v2_llm_costs WHERE id > ?"
    params: list[Any] = [since_id]
    if labels is not None:
        label_list = list(labels)
        if not label_list:
            return 0.0
        sql += f" AND run_label IN ({', '.join('?' for _ in label_list)})"
        params.extend(label_list)
    return float(conn.execute(sql, params).fetchone()[0])


def costs_by_label(conn: sqlite3.Connection) -> dict[str, float]:
    """Coûts cumulés par étiquette.

    Args:
        conn: Connexion.

    Returns:
        ``{run_label: total_usd}`` (vide si la table manque).
    """
    if not table_exists(conn, "v2_llm_costs"):
        return {}
    return {
        row[0]: float(row[1])
        for row in conn.execute(
            "SELECT run_label, COALESCE(SUM(cost_usd), 0) FROM v2_llm_costs GROUP BY run_label"
        )
    }


def max_cost_id(conn: sqlite3.Connection) -> int:
    """Plus grand identifiant de ``v2_llm_costs`` (0 si vide ou absente).

    Args:
        conn: Connexion.

    Returns:
        Identifiant maximal.
    """
    if not table_exists(conn, "v2_llm_costs"):
        return 0
    return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM v2_llm_costs").fetchone()[0])
