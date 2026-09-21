"""v2 — reprise du caviardage des raisons libres du juge (MUST 9, D-017).

Le script ``scripts/v2/sanitize_guard_reports.py`` applique aux lignes déjà
écrites le filtre que ``narrative.guard`` applique désormais avant d'écrire :
une raison du juge qui porte du vocabulaire proscrit cède la place à son code.
Ces tests prouvent qu'il ne touche à rien d'autre et qu'il est rejouable.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_sanitize_guard_reports
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.v2.sanitize_guard_reports import run, sanitise_reasons, sanitise_report
from storage import narrative_db

#: Raison du juge qui porte un terme proscrit (« découverte », règle FR de M9).
DIRTY = "Limite mise en scène : dérive de la fibre, découverte par un contrôle sans cellules."

#: Raison sans terme proscrit.
CLEAN = "Mécanisme fidèle : la rigidité monte, la fréquence de résonance avec elle."


def report(reasons: list) -> dict:
    """Rapport de garde complet, au format du contrat de données.

    Args:
        reasons: Contenu de ``judge.judge_reasons``.

    Returns:
        Rapport désérialisé.
    """
    return {
        "version": "story_guard_v1",
        "lang": "en",
        "mechanical": {"passed": True, "checks": {"schema": True, "no_proscribed_vocab": True}},
        "judge": {
            "passed": True,
            "model": "mock",
            "skipped": False,
            "scores": {"fidelity": 9},
            "threshold": 8,
            "doubts": [],
            "raw_verdict": "accept",
            "judge_reasons": list(reasons),
            "reasons": [],
        },
        "decision": "published",
        "reasons": [],
    }


class SanitiseReportTests(unittest.TestCase):
    """Filtre appliqué à un rapport désérialisé."""

    def test_dirty_reason_is_redacted_with_its_rule(self) -> None:
        fixed, rules = sanitise_report(report([CLEAN, DIRTY]))
        self.assertEqual(rules, ["vocab_fr_decouverte"])
        self.assertEqual(fixed["judge"]["judge_reasons"][0], CLEAN)
        self.assertEqual(
            fixed["judge"]["judge_reasons"][1],
            {"code": "reason_redacted", "rule": "vocab_fr_decouverte", "index": 1},
        )

    def test_clean_report_is_left_alone(self) -> None:
        self.assertEqual(sanitise_report(report([CLEAN])), (None, []))

    def test_everything_but_the_reasons_is_kept(self) -> None:
        fixed, _ = sanitise_report(report([DIRTY]))
        expected = report([])
        expected["judge"]["judge_reasons"] = fixed["judge"]["judge_reasons"]
        self.assertEqual(fixed, expected)
        self.assertEqual(list(fixed), list(expected))
        self.assertEqual(list(fixed["judge"]), list(expected["judge"]))

    def test_already_redacted_reasons_are_stable(self) -> None:
        once, _ = sanitise_report(report([DIRTY]))
        self.assertEqual(sanitise_report(once), (None, []))
        self.assertEqual(sanitise_reasons(once["judge"]["judge_reasons"])[1], [])

    def test_report_without_free_reasons_is_ignored(self) -> None:
        for value in (None, "texte", {"judge": {}}, {"judge": {"judge_reasons": "non"}}):
            with self.subTest(value=value):
                self.assertEqual(sanitise_report(value), (None, []))


class SanitiseDatabaseTests(unittest.TestCase):
    """Passage sur une base réelle : portée, idempotence, texte du récit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "work.db"
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            self.dirty_id = narrative_db.insert_story(
                conn,
                brief_id="SPR-2026-0001",
                lang="en",
                attempt=1,
                status="published",
                body_md="A body that must not move.",
                body_sha256="empreinte",
                guard_report_json=json.dumps(report([CLEAN, DIRTY]), ensure_ascii=False),
            )
            self.clean_id = narrative_db.insert_story(
                conn,
                brief_id="SPR-2026-0002",
                lang="fr",
                attempt=1,
                status="rejected",
                body_md="Un corps qui ne bouge pas non plus.",
                guard_report_json=json.dumps(report([CLEAN]), ensure_ascii=False),
            )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def rows(self) -> dict[int, dict]:
        """Lignes de ``v2_stories``, par identifiant.

        Returns:
            ``{id: ligne}``.
        """
        with narrative_db.connect(self.db_path, readonly=True) as conn:
            return {int(r["id"]): dict(r) for r in conn.execute("SELECT * FROM v2_stories")}

    def test_only_the_offending_row_changes(self) -> None:
        before = self.rows()
        summary = run(self.db_path, dry_run=False)
        after = self.rows()
        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["rows_changed"], 1)
        self.assertEqual(summary["rules"], {"vocab_fr_decouverte": 1})
        self.assertEqual(summary["changed"][0]["story_id"], self.dirty_id)
        self.assertEqual(before[self.clean_id], after[self.clean_id])

    def test_story_text_is_never_rewritten(self) -> None:
        before = self.rows()
        run(self.db_path, dry_run=False)
        after = self.rows()
        for story_id, row in after.items():
            for column in ("body_md", "body_sha256", "title", "mechanism", "limit_staged", "status"):
                with self.subTest(story_id=story_id, column=column):
                    self.assertEqual(row[column], before[story_id][column])

    def test_dry_run_writes_nothing(self) -> None:
        before = self.rows()
        summary = run(self.db_path, dry_run=True)
        self.assertEqual(summary["rows_changed"], 1)
        self.assertEqual(self.rows(), before)

    def test_second_pass_changes_nothing(self) -> None:
        run(self.db_path, dry_run=False)
        after_first = self.rows()
        summary = run(self.db_path, dry_run=False)
        self.assertEqual(summary["rows_changed"], 0)
        self.assertEqual(self.rows(), after_first)

    def test_unreadable_report_is_skipped_not_crashed(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.update_story(conn, self.clean_id, guard_report_json="{pas du JSON")
        summary = run(self.db_path, dry_run=False)
        self.assertEqual(summary["rows_changed"], 1)
        self.assertEqual(self.rows()[self.clean_id]["guard_report_json"], "{pas du JSON")


if __name__ == "__main__":
    unittest.main()
