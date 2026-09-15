"""S10-C — rejeu des briefs publics ('complete') à cartes anglaises.

Couvre, sur base et répertoires temporaires, sans appel LLM réel :

* un brief 'complete' à panel mixte est rejoué sans jamais quitter
  'complete' ni exposer de colonne dérivée à NULL, les cartes non signalées
  et la meta restent identiques à l'octet près ;
* les contrôles avant écriture refusent sans rien écrire : dérive du ``.md``
  non déclarée, carte non signalée modifiée par la traduction, sauvegarde
  périmée ; une dérive déclarée est corrigée ;
* un échec après écriture restaure le brief public dans son état d'origine ;
* ``md_scope_violations`` borne le diff aux sections des éléments traduits ;
* la sélection ``--all-complete-english`` ne retient que les briefs publics
  dont le panel porte des cartes anglaises.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s10c_public_replay
"""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.lang_guard import check_panel_language  # noqa: E402
from scripts import replay_blocked_briefs as replay  # noqa: E402
from scripts.backup_blocked_briefs import sha256_file  # noqa: E402
from scripts.translate_brief_panel import translate_panel_to_fr  # noqa: E402
from tests.test_s10a_panel_language import (  # noqa: E402
    FakeTranslationClient,
    _patched_client,
    mixed_panel,
)
from tests.test_s10b_replay import (  # noqa: E402
    BRIEF_ID,
    FRENCH_VULGARIZATION,
    TempEnvironment,
    _fake_vulgarization,
    _patched_tail,
    _row,
)


def _files_state(briefs_dir: Path) -> dict[str, str]:
    """Hash de chaque fichier du répertoire de briefs."""
    return {p.name: sha256_file(p) for p in sorted(briefs_dir.iterdir())}


class PublicReplayTests(TempEnvironment):
    """Rejeu d'un brief déjà publié."""

    async def seed_public_brief(self) -> None:
        """Brief 'complete' à panel mixte : contrarian et industrialist anglais."""
        await self.seed_blocked_brief(mixed_panel())
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE briefs SET status = 'complete' WHERE id = ?", (BRIEF_ID,))
        conn.commit()
        conn.close()

    async def _run(self, backup_dir: Path, **kwargs: Any) -> replay.ReplayOutcome:
        """Rejoue avec les doubles LLM par défaut."""
        vulgarization = kwargs.pop("vulgarization", _fake_vulgarization())
        patches = _patched_tail(vulgarization)
        with _patched_client(FakeTranslationClient()), patches[0], patches[1], patches[2]:
            return await replay.replay_brief(BRIEF_ID, dry_run=False, backup_dir=backup_dir, **kwargs)

    async def test_public_brief_never_leaves_complete_nor_exposes_nulls(self) -> None:
        await self.seed_public_brief()
        backup_dir = self.make_backup()
        before = _row(self.db_path)
        panel_before = json.loads(before["panel_data"])
        observed: list[dict[str, Any]] = []
        original_vulgarization = replay.node_vulgarization

        async def spy(state: Any) -> Any:
            observed.append(_row(self.db_path))
            return await original_vulgarization(state)

        with mock.patch.object(replay, "node_vulgarization", spy):
            result = await self._run(backup_dir)

        self.assertEqual(result.outcome, "completed", result)
        self.assertEqual(sorted(result.details["cards"]), ["contrarian", "industrialist"])
        # Juste après la réécriture du brief : toujours publié, dérivés présents.
        mid = observed[0]
        self.assertEqual(mid["status"], "complete")
        for column in ("vulgarization_data", "panel_data_en", "vulgarization_data_en"):
            self.assertIsNotNone(mid[column], column)

        after = _row(self.db_path)
        self.assertEqual(after["status"], "complete")
        for column in ("created_at", "panel_consensus_score", "panel_verdict", "revision_count", "brief_md_path"):
            self.assertEqual(after[column], before[column], column)
        panel_after = json.loads(after["panel_data"])
        self.assertEqual(check_panel_language(panel_after, "fr"), [])
        for idx, persona in enumerate(p["reviewer_persona"] for p in panel_before["reviews"]):
            if persona not in ("contrarian", "industrialist"):
                self.assertEqual(json.dumps(panel_after["reviews"][idx]), json.dumps(panel_before["reviews"][idx]))
        self.assertEqual(json.dumps(panel_after["meta_review"]), json.dumps(panel_before["meta_review"]))
        self.assertEqual(json.loads(after["vulgarization_data"]), FRENCH_VULGARIZATION)

    async def test_failure_after_write_restores_public_brief(self) -> None:
        await self.seed_public_brief()
        before_row = _row(self.db_path)
        before_files = _files_state(self.briefs_dir)
        backup_dir = self.make_backup()

        result = await self._run(backup_dir, vulgarization=_fake_vulgarization(RuntimeError("LLM indisponible")))

        self.assertEqual((result.outcome, result.reason), ("still_blocked", "vulgarization_failed"))
        self.assertTrue(result.restored)
        self.assertEqual(_row(self.db_path), before_row)
        self.assertEqual(_files_state(self.briefs_dir), before_files)

    async def test_translation_touching_an_unflagged_card_writes_nothing(self) -> None:
        await self.seed_public_brief()
        backup_dir = self.make_backup()
        before_row = _row(self.db_path)
        before_files = _files_state(self.briefs_dir)

        async def sloppy_translation(label: str, panel: dict[str, Any], **kwargs: Any) -> Any:
            with _patched_client(FakeTranslationClient()):
                fr, warnings, usage = await translate_panel_to_fr(label, panel, **kwargs)
            fr = copy.deepcopy(fr)
            fr["reviews"][0]["strengths"] = ["L'hypothèse est claire, testable et bien posée dans le protocole."]
            return fr, warnings, usage

        with mock.patch("graph.post_fire_pipeline.translate_panel_data_to_fr", sloppy_translation):
            result = await self._run(backup_dir)

        self.assertEqual((result.outcome, result.reason), ("still_blocked", "untouched_card_modified"))
        self.assertFalse(result.restored)
        self.assertEqual(_row(self.db_path), before_row)
        self.assertEqual(_files_state(self.briefs_dir), before_files)

    async def test_md_drift_blocks_unless_declared(self) -> None:
        await self.seed_public_brief()
        md_path = self.briefs_dir / f"{BRIEF_ID}.md"
        drifted = md_path.read_text(encoding="utf-8").replace(
            "- **Panel verdict**: publish_brief", "- **Panel verdict**: revise_and_resubmit"
        )
        md_path.write_text(drifted, encoding="utf-8")
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE briefs SET body_markdown = ? WHERE id = ?", (drifted, BRIEF_ID))
        conn.commit()
        conn.close()
        backup_dir = self.make_backup()
        before_row = _row(self.db_path)

        blocked = await self._run(backup_dir)
        self.assertEqual((blocked.outcome, blocked.reason), ("still_blocked", "unexpected_md_drift"))
        self.assertEqual(_row(self.db_path), before_row)

        fixed = await self._run(backup_dir, accept_md_drift=frozenset({BRIEF_ID}))
        self.assertEqual(fixed.outcome, "completed", fixed)
        self.assertIn("- **Panel verdict**: publish_brief", _row(self.db_path)["body_markdown"])

    async def test_stale_backup_writes_nothing(self) -> None:
        await self.seed_public_brief()
        backup_dir = self.make_backup()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE briefs SET vulgarization_data = ? WHERE id = ?",
            (json.dumps({"reviewers_say": "Modifié après la sauvegarde."}), BRIEF_ID),
        )
        conn.commit()
        conn.close()
        before_row = _row(self.db_path)

        result = await self._run(backup_dir)
        self.assertEqual((result.outcome, result.reason), ("still_blocked", "backup_stale"))
        self.assertIn("vulgarization_data", result.details["differences"])
        self.assertEqual(_row(self.db_path), before_row)

    async def test_selection_keeps_public_briefs_with_english_cards_only(self) -> None:
        await self.seed_public_brief()
        self.assertEqual(replay.complete_english_brief_ids(self.db_path), [BRIEF_ID])
        backup_dir = self.make_backup()
        await self._run(backup_dir)
        self.assertEqual(replay.complete_english_brief_ids(self.db_path), [])


class MdScopeTests(unittest.TestCase):
    """Borne du diff du .md."""

    BEFORE = "\n".join(
        [
            "## Metadata",
            "- **Panel verdict**: publish_brief",
            "### 5.2 Applications industrielles et marche",
            "- The market is narrow.",
            "## 6. Panel Review Summary",
            "| contrarian | 3.5/10 | weak_reject | The effect is small |",
            "| methodologist | 6.5/10 | weak_accept | Protocole clair |",
            "### 6.1 Consensus",
            "- Consensus en français.",
            "#### Contrarian",
            "- **Strengths**: The controls are good.",
        ]
    )

    def test_translated_sections_are_allowed(self) -> None:
        after = (
            self.BEFORE.replace("The market is narrow.", "Le marché est étroit.")
            .replace("The effect is small", "L'effet est faible")
            .replace("The controls are good.", "Les contrôles sont bons.")
        )
        violations, sections = replay.md_scope_violations(self.BEFORE, after, {"contrarian", "industrialist"}, False)
        self.assertEqual(violations, [])
        self.assertIn("#### Contrarian", sections)

    def test_other_sections_are_violations(self) -> None:
        after = (
            self.BEFORE.replace("publish_brief", "revise_and_resubmit")
            .replace("Protocole clair", "Protocole limpide")
            .replace("Consensus en français.", "Autre consensus.")
            .replace("The market is narrow.", "Le marché est étroit.")
        )
        violations, _ = replay.md_scope_violations(self.BEFORE, after, {"contrarian"}, False)
        joined = " ".join(violations)
        self.assertIn("## Metadata", joined)
        self.assertIn("methodologist", joined)
        self.assertIn("### 6.1 Consensus", joined)
        self.assertIn("### 5.2", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
