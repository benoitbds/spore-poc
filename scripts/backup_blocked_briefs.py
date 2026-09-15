"""Sauvegarde préalable au rejeu des briefs (S10-B, étendue en S10-C).

Le rejeu (``scripts/replay_blocked_briefs.py``) réécrit des artefacts
existants : ``panel_data``, ``body_markdown``, ``vulgarization_data``, les
colonnes EN, le ``.md`` et le sidecar ``.json``. Cette sauvegarde en est une
**dépendance**, pas seulement un point de retour : ``original_hypothesis``
n'existe que dans le sidecar ``.json`` (la table ``hypotheses`` n'a aucune
ligne pour ces briefs), et le sidecar est réécrit par le rejeu. Le script de
rejeu refuse d'écrire tant qu'un manifeste vérifié ne couvre pas le brief.

Ce que fait le script :

1. Sélectionne les briefs — ``--brief-id`` explicites, ou ``--all-pending``
   (``status='pending'``, ``panel_verdict='publish_brief'``, sans
   ``kill_reason``, hors stubs).
2. Sauvegarde la base via ``sqlite3 <db> ".backup <dest>"`` — API de backup
   en ligne, sûre sous WAL avec le front en lecture continue. Pas de copie
   de fichier. Cette copie n'est plus nécessaire à la restauration d'un
   brief (manifeste format 2) : elle reste le filet de dernier recours.
3. Copie le ``.md`` et le ``.json`` de chaque brief.
4. Écrit, pour chaque brief et depuis la copie de la base (instantané
   cohérent), les colonnes que le rejeu réécrit (``RESTORABLE_BLOB_COLUMNS``)
   dans des fichiers séparés ``blobs/<brief_id>/<colonne>.txt``, octet pour
   octet. Le manifeste ne garde que leur SHA-256, leur taille, ou ``null``
   quand la colonne est NULL : les blobs des briefs publics pèsent plusieurs
   mégaoctets et n'ont rien à faire dans un JSON qu'on relit à la main.
5. Écrit ``MANIFEST.json`` (``format: 2``) : SHA-256 de chaque copie, de la
   base sauvegardée et de chaque blob, ``PRAGMA integrity_check`` sur la
   copie, et l'instantané des colonnes scalaires de la ligne
   (``ROW_SNAPSHOT_COLUMNS`` : invariants, ``status``, chemins tels qu'ils
   sont stockés — relatifs compris).
6. Relit tout et vérifie : même hash source/copie, blobs conformes, lignes
   présentes dans la base sauvegardée. Échec = code de sortie non nul.

Un manifeste format 1 (S10-B, sans ``format`` ni blobs) reste lisible et
vérifiable ; restaurer un brief depuis lui oblige à relire les colonnes dans
la copie de la base.

Usage::

    python -m scripts.backup_blocked_briefs --all-pending
    python -m scripts.backup_blocked_briefs --brief-id SPR-2026-4B85 --label s10c
    python -m scripts.backup_blocked_briefs --verify data/backups/s10c-20260915T080000Z

Restauration d'un brief : ``python -m scripts.replay_blocked_briefs
--restore-brief <id> --from <dossier>`` (voir ce script). Restauration de la
base entière, en dernier recours et à la main : arrêter le front, supprimer
``data/spore.db-wal`` et ``-shm``, ``cp <backup>/spore.db data/spore.db``,
puis recopier ``<backup>/briefs/*`` dans ``outputs/briefs/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings  # noqa: E402
from logging_config import get_logger, setup_logging  # noqa: E402

logger = get_logger("scripts.backup_blocked_briefs")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = PROJECT_ROOT / "data" / "backups"
MANIFEST_NAME = "MANIFEST.json"
MANIFEST_FORMAT = 2
BLOBS_DIR = "blobs"
DEFAULT_LABEL = "s10c"

# Colonnes que le rejeu ne doit jamais modifier : instantané de référence
# pour la vérification post-rejeu.
INVARIANT_COLUMNS: tuple[str, ...] = (
    "created_at",
    "panel_consensus_score",
    "panel_verdict",
    "revision_count",
)

# Colonnes scalaires portées en clair dans le manifeste. Les chemins sont
# conservés tels qu'ils sont stockés en base (SPR-2026-6FEB a un chemin
# relatif) : la restauration réécrit la valeur d'origine, pas une valeur
# normalisée.
ROW_SNAPSHOT_COLUMNS: tuple[str, ...] = (
    *INVARIANT_COLUMNS,
    "status",
    "brief_md_path",
    "brief_json_path",
)

# Colonnes que le rejeu réécrit, sauvegardées octet pour octet en fichiers.
RESTORABLE_BLOB_COLUMNS: tuple[str, ...] = (
    "panel_data",
    "body_markdown",
    "vulgarization_data",
    "panel_data_en",
    "vulgarization_data_en",
)

# Périmètre S10-B : briefs retenus en 'pending' après un vote de publication.
# Les rejetés ('rejected') et les tués ('killed') ne sont pas concernés.
PENDING_SCOPE_SQL = """
    SELECT id FROM briefs
    WHERE status = 'pending'
      AND panel_verdict = 'publish_brief'
      AND kill_reason IS NULL
      AND COALESCE(is_stub, 0) = 0
    ORDER BY created_at
"""

_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class BackupError(RuntimeError):
    """La sauvegarde est incomplète ou ne se vérifie pas."""


def sha256_file(path: Path) -> str:
    """Calcule le SHA-256 d'un fichier.

    Args:
        path: Chemin du fichier.

    Returns:
        L'empreinte hexadécimale.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    """Calcule le SHA-256 de l'encodage UTF-8 d'un texte.

    Args:
        text: Valeur de colonne.

    Returns:
        L'empreinte hexadécimale.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_brief_path(stored: str) -> Path:
    """Chemin disque d'un fichier de brief tel que stocké en base.

    Args:
        stored: Valeur de ``brief_md_path`` ou ``brief_json_path``, absolue
            ou relative à la racine du projet.

    Returns:
        Le chemin absolu.
    """
    path = Path(stored)
    return path if path.is_absolute() else PROJECT_ROOT / path


def select_brief_ids(db_path: Path, brief_ids: list[str] | None) -> list[str]:
    """Résout la liste des briefs à sauvegarder.

    Args:
        db_path: Base SQLite, ouverte en lecture seule.
        brief_ids: Identifiants explicites, ou ``None`` pour le périmètre
            ``--all-pending``.

    Returns:
        Les identifiants, dans l'ordre chronologique pour ``--all-pending``.

    Raises:
        BackupError: Un identifiant explicite est absent de la base.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if brief_ids is None:
            return [row[0] for row in conn.execute(PENDING_SCOPE_SQL)]
        found = {
            row[0]
            for row in conn.execute(
                f"SELECT id FROM briefs WHERE id IN ({','.join('?' * len(brief_ids))})",
                brief_ids,
            )
        }
    finally:
        conn.close()
    missing = [b for b in brief_ids if b not in found]
    if missing:
        raise BackupError(f"briefs absents de la base : {missing}")
    return list(brief_ids)


def backup_database(db_path: Path, dest: Path) -> None:
    """Sauvegarde la base via la commande ``.backup`` du CLI sqlite3.

    Args:
        db_path: Base source (en WAL, lue en continu par le front).
        dest: Fichier de destination, qui ne doit pas exister.

    Raises:
        BackupError: Le CLI échoue ou ne produit pas de fichier.
    """
    if dest.exists():
        raise BackupError(f"destination déjà présente : {dest}")
    result = subprocess.run(
        ["sqlite3", str(db_path), f".backup '{dest}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not dest.exists():
        raise BackupError(
            f"sqlite3 .backup a échoué (code {result.returncode}) : {result.stderr.strip()}"
        )


def read_row_columns(
    conn: sqlite3.Connection, brief_id: str, columns: tuple[str, ...]
) -> dict[str, Any] | None:
    """Lit des colonnes d'une ligne ``briefs``.

    Args:
        conn: Connexion SQLite.
        brief_id: Identifiant du brief.
        columns: Colonnes à lire.

    Returns:
        Le dict des colonnes, ou ``None`` si la ligne est absente.
    """
    row = conn.execute(
        f"SELECT {', '.join(columns)} FROM briefs WHERE id = ?", (brief_id,)
    ).fetchone()
    return dict(zip(columns, row)) if row is not None else None


def _row_snapshot(conn: sqlite3.Connection, brief_id: str) -> dict[str, Any] | None:
    """Lit les colonnes scalaires du manifeste pour un brief.

    Args:
        conn: Connexion SQLite.
        brief_id: Identifiant du brief.

    Returns:
        Le dict des colonnes, ou ``None`` si la ligne est absente.
    """
    return read_row_columns(conn, brief_id, ROW_SNAPSHOT_COLUMNS)


def _write_blobs(
    conn: sqlite3.Connection, brief_id: str, blobs_dir: Path
) -> dict[str, dict[str, Any] | None]:
    """Écrit les colonnes restaurables d'un brief en fichiers.

    Args:
        conn: Connexion à la copie de la base (instantané cohérent).
        brief_id: Identifiant du brief.
        blobs_dir: Répertoire ``blobs/<brief_id>`` à créer.

    Returns:
        ``{colonne: {"file", "sha256", "bytes"}}``, ou ``None`` pour une
        colonne NULL.

    Raises:
        BackupError: Une colonne n'est ni NULL ni du texte.
    """
    values = read_row_columns(conn, brief_id, RESTORABLE_BLOB_COLUMNS) or {}
    blobs_dir.mkdir(parents=True, exist_ok=False)
    entries: dict[str, dict[str, Any] | None] = {}
    for column in RESTORABLE_BLOB_COLUMNS:
        value = values.get(column)
        if value is None:
            entries[column] = None
            continue
        if not isinstance(value, str):
            raise BackupError(f"{brief_id}.{column} : type inattendu {type(value).__name__}")
        path = blobs_dir / f"{column}.txt"
        path.write_bytes(value.encode("utf-8"))
        entries[column] = {
            "file": str(path.relative_to(blobs_dir.parent.parent)),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    return entries


def create_backup(
    db_path: Path, brief_ids: list[str], backup_dir: Path
) -> dict[str, Any]:
    """Crée la sauvegarde complète et son manifeste (format 2).

    Args:
        db_path: Base SQLite de production.
        brief_ids: Briefs couverts par la sauvegarde.
        backup_dir: Répertoire à créer (ne doit pas exister).

    Returns:
        Le manifeste écrit.

    Raises:
        BackupError: Fichier manquant, échec du backup, ou base sauvegardée
            incohérente.
    """
    if not brief_ids:
        raise BackupError("aucun brief à sauvegarder")
    backup_dir.mkdir(parents=True, exist_ok=False)
    files_dir = backup_dir / "briefs"
    files_dir.mkdir()

    db_dest = backup_dir / "spore.db"
    backup_database(db_path, db_dest)
    logger.info("backup_db_written", path=str(db_dest))

    backup_conn = sqlite3.connect(f"file:{db_dest}?mode=ro", uri=True)
    try:
        integrity = backup_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError(f"integrity_check sur la base sauvegardée : {integrity}")
        briefs: dict[str, Any] = {}
        for brief_id in brief_ids:
            snapshot = _row_snapshot(backup_conn, brief_id)
            if snapshot is None:
                raise BackupError(f"{brief_id} absent de la base sauvegardée")
            entry: dict[str, Any] = {"row": snapshot, "files": {}}
            for key in ("brief_md_path", "brief_json_path"):
                if not snapshot[key]:
                    raise BackupError(f"{brief_id} : {key} vide")
                src = resolve_brief_path(snapshot[key])
                if not src.is_file():
                    raise BackupError(f"{brief_id} : fichier {key} introuvable ({src})")
                dest = files_dir / src.name
                shutil.copy2(src, dest)
                entry["files"][src.name] = {
                    "source": str(src),
                    "sha256": sha256_file(dest),
                    "bytes": dest.stat().st_size,
                }
            entry["blobs"] = _write_blobs(backup_conn, brief_id, backup_dir / BLOBS_DIR / brief_id)
            briefs[brief_id] = entry
    finally:
        backup_conn.close()

    manifest: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "replay of briefs with English panel cards (S10-B/S10-C)",
        "source_db": str(db_path),
        "db": {"file": "spore.db", "sha256": sha256_file(db_dest), "integrity_check": "ok"},
        "briefs": briefs,
    }
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "backup_manifest_written",
        path=str(backup_dir / MANIFEST_NAME),
        briefs=len(briefs),
        files=sum(len(e["files"]) for e in briefs.values()),
        blobs=sum(1 for e in briefs.values() for b in e["blobs"].values() if b),
        manifest_bytes=(backup_dir / MANIFEST_NAME).stat().st_size,
    )
    return manifest


def load_manifest(backup_dir: Path) -> dict[str, Any]:
    """Charge le manifeste d'une sauvegarde.

    Args:
        backup_dir: Répertoire de sauvegarde.

    Returns:
        Le manifeste.

    Raises:
        BackupError: Manifeste absent.
    """
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError(f"manifeste absent : {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_brief_entry(
    backup_dir: Path,
    brief_id: str,
    entry: dict[str, Any],
    require_sources_unchanged: bool = False,
) -> int:
    """Vérifie les copies de fichiers et les blobs d'un brief.

    Args:
        backup_dir: Répertoire de sauvegarde.
        brief_id: Identifiant du brief.
        entry: Entrée du manifeste.
        require_sources_unchanged: Comparer aussi les fichiers sources.

    Returns:
        Le nombre de fichiers vérifiés (copies et blobs).

    Raises:
        BackupError: Au premier écart constaté.
    """
    count = 0
    for name, meta in entry["files"].items():
        copy = backup_dir / "briefs" / name
        if not copy.is_file() or sha256_file(copy) != meta["sha256"]:
            raise BackupError(f"{brief_id} : copie absente ou altérée ({copy})")
        if require_sources_unchanged:
            src = Path(meta["source"])
            if not src.is_file() or sha256_file(src) != meta["sha256"]:
                raise BackupError(f"{brief_id} : source modifiée depuis la sauvegarde ({src})")
        count += 1
    for column, meta in (entry.get("blobs") or {}).items():
        if meta is None:
            continue
        blob = backup_dir / meta["file"]
        if not blob.is_file() or sha256_file(blob) != meta["sha256"]:
            raise BackupError(f"{brief_id} : blob {column} absent ou altéré ({blob})")
        count += 1
    return count


def verify_backup(
    backup_dir: Path,
    require_sources_unchanged: bool = False,
    check_database: bool = True,
) -> dict[str, Any]:
    """Vérifie une sauvegarde contre son manifeste.

    Contrôle : hash de chaque copie et de chaque blob, et — si
    ``check_database`` — présence, hash et intégrité SQLite de la copie de la
    base, présence de chaque brief dans la copie. Avec
    ``require_sources_unchanged``, exige aussi que les fichiers sources aient
    encore le hash sauvegardé — l'état attendu juste avant le premier rejeu.

    Args:
        backup_dir: Répertoire de sauvegarde.
        require_sources_unchanged: Comparer aussi les fichiers sources.
        check_database: Vérifier la copie de la base. Désactivable pour un
            manifeste format 2, qui se suffit à lui-même.

    Returns:
        Le manifeste chargé.

    Raises:
        BackupError: Au premier écart constaté.
    """
    manifest = load_manifest(backup_dir)
    if not check_database and manifest.get("format", 1) < MANIFEST_FORMAT:
        raise BackupError("un manifeste format 1 ne peut pas être vérifié sans la copie de la base")

    if check_database:
        db_copy = backup_dir / manifest["db"]["file"]
        if not db_copy.is_file() or sha256_file(db_copy) != manifest["db"]["sha256"]:
            raise BackupError(f"base sauvegardée absente ou altérée : {db_copy}")
        conn = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("integrity_check KO sur la base sauvegardée")
            for brief_id in manifest["briefs"]:
                if _row_snapshot(conn, brief_id) is None:
                    raise BackupError(f"{brief_id} absent de la base sauvegardée")
        finally:
            conn.close()

    file_count = sum(
        verify_brief_entry(backup_dir, brief_id, entry, require_sources_unchanged)
        for brief_id, entry in manifest["briefs"].items()
    )
    logger.info(
        "backup_verified",
        path=str(backup_dir),
        format=manifest.get("format", 1),
        briefs=len(manifest["briefs"]),
        files=file_count,
        sources_checked=require_sources_unchanged,
        database_checked=check_database,
    )
    return manifest


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments.

    Returns:
        Le parseur configuré.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--brief-id", action="append", help="Brief à sauvegarder (répétable).")
    group.add_argument("--all-pending", action="store_true", help="Tous les briefs 'pending' du périmètre S10-B.")
    group.add_argument("--verify", type=Path, help="Vérifier une sauvegarde existante.")
    parser.add_argument(
        "--sources-unchanged",
        action="store_true",
        help="Avec --verify : exiger que les fichiers sources soient identiques aux copies.",
    )
    parser.add_argument(
        "--label",
        default=DEFAULT_LABEL,
        help=f"Préfixe du dossier de sauvegarde (défaut : {DEFAULT_LABEL}).",
    )
    return parser


def main() -> int:
    """Point d'entrée CLI.

    Returns:
        Code de sortie : 0 si la sauvegarde est écrite et vérifiée.
    """
    setup_logging()
    args = _build_arg_parser().parse_args()
    try:
        if args.verify is not None:
            manifest = verify_backup(args.verify, require_sources_unchanged=args.sources_unchanged)
            print(
                f"OK — {len(manifest['briefs'])} briefs vérifiés dans {args.verify}"
                f" (format {manifest.get('format', 1)})"
            )
            return 0

        if not _LABEL_RE.match(args.label):
            raise BackupError(f"label invalide : {args.label!r}")
        db_path = get_settings().db_path
        brief_ids = select_brief_ids(db_path, None if args.all_pending else args.brief_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = BACKUP_ROOT / f"{args.label}-{stamp}"
        create_backup(db_path, brief_ids, backup_dir)
        manifest = verify_backup(backup_dir, require_sources_unchanged=True)
    except BackupError as exc:
        logger.error("backup_failed", error=str(exc))
        print(f"ÉCHEC — {exc}", file=sys.stderr)
        return 1

    files = sum(len(e["files"]) for e in manifest["briefs"].values())
    blobs = sum(1 for e in manifest["briefs"].values() for b in e["blobs"].values() if b)
    manifest_bytes = (backup_dir / MANIFEST_NAME).stat().st_size
    print(f"OK — {backup_dir}")
    print(f"     base : {manifest['db']['sha256'][:16]}…  integrity_check=ok")
    print(
        f"     {len(manifest['briefs'])} briefs, {files} fichiers, {blobs} blobs,"
        f" manifeste {manifest_bytes} octets, hashes vérifiés"
    )
    for brief_id in manifest["briefs"]:
        print(f"     - {brief_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
