"""S10-C — manifeste restaurable et restauration d'un brief.

Couvre, sur base et répertoires temporaires, sans appel LLM réel :

* le manifeste format 2 : les colonnes réécrites par le rejeu sont stockées
  en blobs séparés, le manifeste n'en garde que les hashes ;
* le cycle sauvegarde → rejeu → restauration : la ligne, le ``.md`` et le
  ``.json`` reviennent à l'octet près, **sans la copie de la base** ;
* un manifeste format 1 (S10-B) se restaure depuis la copie de la base, et
  pas sans elle ;
* les refus : brief absent du manifeste, blob altéré, invariants divergents.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s10c_restore
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import replay_blocked_briefs as replay  # noqa: E402
from scripts.backup_blocked_briefs import (  # noqa: E402
    MANIFEST_NAME,
    RESTORABLE_BLOB_COLUMNS,
    RestoreError,
    create_backup,
    restore_brief,
    sha256_file,
    verify_backup,
)
from tests.test_s10a_panel_language import (  # noqa: E402
    FakeTranslationClient,
    _patched_client,
    english_panel,
)
from tests.test_s10b_replay import (  # noqa: E402
    BRIEF_ID,
    TempEnvironment,
    _fake_vulgarization,
    _patched_tail,
    _row,
)


def _files_state(briefs_dir: Path) -> dict[str, str]:
    """Hash de chaque fichier du répertoire de briefs."""
    return {p.name: sha256_file(p) for p in sorted(briefs_dir.iterdir())}


class RestoreTests(TempEnvironment):
    """Restauration d'un brief depuis une sauvegarde."""

    async def _replay(self, backup_dir: Path) -> None:
        """Rejoue le brief de test avec les doubles LLM."""
        patches = _patched_tail(_fake_vulgarization())
        with _patched_client(FakeTranslationClient()), patches[0], patches[1], patches[2]:
            result = await replay.replay_brief(BRIEF_ID, dry_run=False, backup_dir=backup_dir)
        self.assertEqual(result.outcome, "completed", result)

    async def test_manifest_holds_hashes_and_blobs_hold_values(self) -> None:
        await self.seed_blocked_brief(english_panel())
        backup_dir = self.make_backup()
        manifest = json.loads((backup_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        entry = manifest["briefs"][BRIEF_ID]
        row = _row(self.db_path)

        self.assertEqual(manifest["format"], 2)
        self.assertEqual(set(entry["blobs"]), set(RESTORABLE_BLOB_COLUMNS))
        self.assertEqual(entry["row"]["status"], "pending")
        for column in RESTORABLE_BLOB_COLUMNS:
            meta = entry["blobs"][column]
            self.assertEqual((backup_dir / meta["file"]).read_bytes().decode("utf-8"), row[column])
            self.assertNotIn(row[column], json.dumps(manifest, ensure_ascii=False))

    async def test_replayed_brief_comes_back_exactly_without_db_copy(self) -> None:
        await self.seed_blocked_brief(english_panel())
        before_row = _row(self.db_path)
        before_files = _files_state(self.briefs_dir)
        backup_dir = self.make_backup()

        await self._replay(backup_dir)
        self.assertEqual(_row(self.db_path)["status"], "complete")
        self.assertNotEqual(_files_state(self.briefs_dir), before_files)

        # Le manifeste format 2 se suffit : la copie de la base est retirée.
        (backup_dir / "spore.db").unlink()
        verify_backup(backup_dir, check_database=False)
        summary = restore_brief(backup_dir, BRIEF_ID, self.db_path)

        self.assertEqual(summary["format"], 2)
        self.assertEqual(_row(self.db_path), before_row)
        self.assertEqual(_files_state(self.briefs_dir), before_files)

        # Et le brief restauré se rejoue à nouveau (sauvegarde sans base :
        # le rejeu vérifie la base, on repart d'une sauvegarde neuve).
        fresh_backup = self.backup_root / "s10c-after-restore"
        create_backup(self.db_path, [BRIEF_ID], fresh_backup)
        await self._replay(fresh_backup)

    async def test_format_1_manifest_restores_from_db_copy_only(self) -> None:
        await self.seed_blocked_brief(english_panel())
        before_row = _row(self.db_path)
        before_files = _files_state(self.briefs_dir)
        backup_dir = self.make_backup()
        manifest = json.loads((backup_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest.pop("format")
        for entry in manifest["briefs"].values():
            entry.pop("blobs")
        (backup_dir / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        shutil.rmtree(backup_dir / "blobs")

        await self._replay(backup_dir)
        summary = restore_brief(backup_dir, BRIEF_ID, self.db_path)
        self.assertEqual(summary["format"], 1)
        self.assertEqual(_row(self.db_path), before_row)
        self.assertEqual(_files_state(self.briefs_dir), before_files)

        (backup_dir / "spore.db").unlink()
        with self.assertRaises(RestoreError):
            restore_brief(backup_dir, BRIEF_ID, self.db_path)

    async def test_refuses_brief_absent_from_manifest(self) -> None:
        await self.seed_blocked_brief(english_panel())
        backup_dir = self.make_backup()
        before = _row(self.db_path)
        with self.assertRaisesRegex(RestoreError, "absent du manifeste"):
            restore_brief(backup_dir, "SPR-2026-ZZZZ", self.db_path)
        self.assertEqual(_row(self.db_path), before)

    async def test_refuses_altered_blob_before_writing(self) -> None:
        await self.seed_blocked_brief(english_panel())
        backup_dir = self.make_backup()
        await self._replay(backup_dir)
        after_replay_row = _row(self.db_path)
        after_replay_files = _files_state(self.briefs_dir)

        blob = backup_dir / json.loads((backup_dir / MANIFEST_NAME).read_text())["briefs"][BRIEF_ID]["blobs"]["panel_data"]["file"]
        blob.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RestoreError, "blob panel_data"):
            restore_brief(backup_dir, BRIEF_ID, self.db_path)
        self.assertEqual(_row(self.db_path), after_replay_row)
        self.assertEqual(_files_state(self.briefs_dir), after_replay_files)

    async def test_refuses_when_invariants_diverge(self) -> None:
        await self.seed_blocked_brief(english_panel())
        backup_dir = self.make_backup()
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE briefs SET panel_consensus_score = 9.9 WHERE id = ?", (BRIEF_ID,))
        conn.commit()
        conn.close()
        before: dict[str, Any] = _row(self.db_path)
        with self.assertRaisesRegex(RestoreError, "invariants"):
            restore_brief(backup_dir, BRIEF_ID, self.db_path)
        self.assertEqual(_row(self.db_path), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
