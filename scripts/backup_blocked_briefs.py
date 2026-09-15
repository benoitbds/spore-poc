"""S10-B — sauvegarde préalable au rejeu des briefs bloqués en 'pending'.

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
   de fichier.
3. Copie le ``.md`` et le ``.json`` de chaque brief.
4. Écrit ``MANIFEST.json`` : SHA-256 de chaque fichier source et copie,
   SHA-256 de la base sauvegardée, ``PRAGMA integrity_check`` sur la copie,
   et l'instantané des colonnes invariantes de chaque ligne
   (``created_at``, ``panel_consensus_score``, ``panel_verdict``,
   ``revision_count``) lu dans la copie.
5. Relit tout et vérifie : même hash source/copie, lignes présentes dans la
   base sauvegardée. Échec = code de sortie non nul.

Usage::

    python -m scripts.backup_blocked_briefs --all-pending
    python -m scripts.backup_blocked_briefs --brief-id SPR-2026-4B85
    python -m scripts.backup_blocked_briefs --verify data/backups/s10b-20260915T060000Z

Restauration (manuelle, documentée ici, jamais automatique) : arrêter le
front, ``cp <backup>/spore.db data/spore.db`` après avoir supprimé
``data/spore.db-wal`` et ``-shm``, puis recopier ``<backup>/briefs/*`` dans
``outputs/briefs/``. Pour un seul brief, préférer restaurer ses fichiers et
réécrire ses colonnes depuis la base sauvegardée.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

# Colonnes que le rejeu ne doit jamais modifier : instantané de référence
# pour la vérification post-rejeu.
INVARIANT_COLUMNS: tuple[str, ...] = (
    "created_at",
    "panel_consensus_score",
    "panel_verdict",
    "revision_count",
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


def _row_snapshot(conn: sqlite3.Connection, brief_id: str) -> dict[str, Any] | None:
    """Lit les colonnes invariantes et les chemins d'un brief.

    Args:
        conn: Connexion SQLite.
        brief_id: Identifiant du brief.

    Returns:
        Le dict des colonnes, ou ``None`` si la ligne est absente.
    """
    cols = ", ".join((*INVARIANT_COLUMNS, "status", "brief_md_path", "brief_json_path"))
    row = conn.execute(f"SELECT {cols} FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    if row is None:
        return None
    keys = (*INVARIANT_COLUMNS, "status", "brief_md_path", "brief_json_path")
    return dict(zip(keys, row))


def create_backup(db_path: Path, brief_ids: list[str], backup_dir: Path) -> dict[str, Any]:
    """Crée la sauvegarde complète et son manifeste.

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
                src = Path(snapshot[key] or "")
                if not snapshot[key] or not src.is_file():
                    raise BackupError(f"{brief_id} : fichier {key} introuvable ({src})")
                dest = files_dir / src.name
                shutil.copy2(src, dest)
                entry["files"][src.name] = {
                    "source": str(src),
                    "sha256": sha256_file(dest),
                    "bytes": dest.stat().st_size,
                }
            briefs[brief_id] = entry
    finally:
        backup_conn.close()

    manifest: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "S10-B replay of pending briefs",
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
    )
    return manifest


def verify_backup(backup_dir: Path, require_sources_unchanged: bool = False) -> dict[str, Any]:
    """Vérifie une sauvegarde contre son manifeste.

    Contrôle : présence et hash de la base et de chaque copie, intégrité
    SQLite de la copie, présence de chaque brief dans la copie. Avec
    ``require_sources_unchanged``, exige aussi que les fichiers sources
    aient encore le hash sauvegardé — c'est l'état attendu juste avant le
    premier rejeu, et ce qui cesse d'être vrai une fois le brief rejoué.

    Args:
        backup_dir: Répertoire de sauvegarde.
        require_sources_unchanged: Comparer aussi les fichiers sources.

    Returns:
        Le manifeste chargé.

    Raises:
        BackupError: Au premier écart constaté.
    """
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError(f"manifeste absent : {manifest_path}")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))

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

    file_count = 0
    for brief_id, entry in manifest["briefs"].items():
        for name, meta in entry["files"].items():
            copy = backup_dir / "briefs" / name
            if not copy.is_file() or sha256_file(copy) != meta["sha256"]:
                raise BackupError(f"{brief_id} : copie absente ou altérée ({copy})")
            if require_sources_unchanged:
                src = Path(meta["source"])
                if not src.is_file() or sha256_file(src) != meta["sha256"]:
                    raise BackupError(f"{brief_id} : source modifiée depuis la sauvegarde ({src})")
            file_count += 1

    logger.info(
        "backup_verified",
        path=str(backup_dir),
        briefs=len(manifest["briefs"]),
        files=file_count,
        sources_checked=require_sources_unchanged,
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
    group.add_argument("--all-pending", action="store_true", help="Tous les briefs du périmètre S10-B.")
    group.add_argument("--verify", type=Path, help="Vérifier une sauvegarde existante.")
    parser.add_argument(
        "--sources-unchanged",
        action="store_true",
        help="Avec --verify : exiger que les fichiers sources soient identiques aux copies.",
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
            print(f"OK — {len(manifest['briefs'])} briefs vérifiés dans {args.verify}")
            return 0

        db_path = get_settings().db_path
        brief_ids = select_brief_ids(db_path, None if args.all_pending else args.brief_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = BACKUP_ROOT / f"s10b-{stamp}"
        create_backup(db_path, brief_ids, backup_dir)
        manifest = verify_backup(backup_dir, require_sources_unchanged=True)
    except BackupError as exc:
        logger.error("backup_failed", error=str(exc))
        print(f"ÉCHEC — {exc}", file=sys.stderr)
        return 1

    files = sum(len(e["files"]) for e in manifest["briefs"].values())
    print(f"OK — {backup_dir}")
    print(f"     base : {manifest['db']['sha256'][:16]}…  integrity_check=ok")
    print(f"     {len(manifest['briefs'])} briefs, {files} fichiers, hashes vérifiés")
    for brief_id in manifest["briefs"]:
        print(f"     - {brief_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
