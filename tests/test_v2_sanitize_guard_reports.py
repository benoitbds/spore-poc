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
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative.checks import PROSCRIBED_EN, PROSCRIBED_FR, normalise, us_spelling_hits
from scripts.v2.sanitize_guard_reports import (
    rewrite_code,
    run,
    sanitise_reasons,
    sanitise_report,
)
from storage import narrative_db

#: Raison du juge qui porte un terme proscrit (« découverte », règle FR de M9).
DIRTY = "Limite mise en scène : dérive de la fibre, découverte par un contrôle sans cellules."

#: Raison sans terme proscrit.
CLEAN = "Mécanisme fidèle : la rigidité monte, la fréquence de résonance avec elle."


def report(reasons: list, codes: list | None = None) -> dict:
    """Rapport de garde complet, au format du contrat de données.

    Args:
        reasons: Contenu de ``judge.judge_reasons``.
        codes: Contenu de ``reasons`` (codes de premier niveau).

    Returns:
        Rapport désérialisé.
    """
    codes = codes or []
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
        "reasons": list(codes),
    }


class SanitiseReportTests(unittest.TestCase):
    """Filtre appliqué à un rapport désérialisé."""

    def test_dirty_reason_is_redacted_with_its_rule(self) -> None:
        fixed, rules, codes = sanitise_report(report([CLEAN, DIRTY]))
        self.assertEqual(rules, ["vocab_fr_decouverte"])
        self.assertEqual(codes, 0)
        self.assertEqual(fixed["judge"]["judge_reasons"][0], CLEAN)
        self.assertEqual(
            fixed["judge"]["judge_reasons"][1],
            {"code": "reason_redacted", "rule": "vocab_fr_decouverte", "index": 1},
        )

    def test_clean_report_is_left_alone(self) -> None:
        self.assertEqual(sanitise_report(report([CLEAN])), (None, [], 0))

    def test_everything_but_the_reasons_is_kept(self) -> None:
        fixed, _, _ = sanitise_report(report([DIRTY]))
        expected = report([])
        expected["judge"]["judge_reasons"] = fixed["judge"]["judge_reasons"]
        self.assertEqual(fixed, expected)
        self.assertEqual(list(fixed), list(expected))
        self.assertEqual(list(fixed["judge"]), list(expected["judge"]))

    def test_already_redacted_reasons_are_stable(self) -> None:
        once, _, _ = sanitise_report(report([DIRTY]))
        self.assertEqual(sanitise_report(once), (None, [], 0))
        self.assertEqual(sanitise_reasons(once["judge"]["judge_reasons"])[1], [])

    def test_report_without_free_reasons_is_ignored(self) -> None:
        for value in (None, "texte", {"judge": {}}, {"judge": {"judge_reasons": "non"}}):
            with self.subTest(value=value):
                self.assertEqual(sanitise_report(value), (None, [], 0))


class RewriteCodeTests(unittest.TestCase):
    """Codes de raison : un code, jamais un mot qu'un contrôle lexical peut lire."""

    def test_us_spelling_keeps_the_count_not_the_words(self) -> None:
        self.assertEqual(rewrite_code("mechanical:us_spelling:sulfur", "en"), "mechanical:us_spelling:1")
        self.assertEqual(
            rewrite_code("mechanical:us_spelling:sulfur,aluminum,center", "en"),
            "mechanical:us_spelling:3",
        )

    def test_proscribed_vocab_uses_the_rule_identifier(self) -> None:
        self.assertEqual(
            rewrite_code("mechanical:proscribed_vocab:decouverte", "fr"),
            "mechanical:proscribed_vocab:vocab_fr_decouverte",
        )
        self.assertEqual(
            rewrite_code("mechanical:proscribed_vocab:discover,first_person_plural", "en"),
            "mechanical:proscribed_vocab:vocab_en_discover,vocab_en_first_person_plural",
        )

    def test_already_technical_codes_are_stable(self) -> None:
        for code in (
            "mechanical:us_spelling:3",
            "mechanical:proscribed_vocab:vocab_fr_decouverte",
        ):
            with self.subTest(code=code):
                self.assertEqual(rewrite_code(code, "fr"), code)

    def test_other_codes_are_left_alone(self) -> None:
        for code in (
            "judge:below_threshold:fidelity",
            "judge:skipped_after_mechanical_failure",
            "mechanical:length:812",
            "mechanical:identity_denylist:hits=2",
            "mechanical:citation:doi,url",
            "guard:not_accepted",
            "mechanical",
        ):
            with self.subTest(code=code):
                self.assertEqual(rewrite_code(code, "en"), code)

    def test_rewritten_codes_carry_no_readable_term(self) -> None:
        # Les règles du run sont bornées par des frontières de mot ; un terme
        # posé derrière un deux-points s'y lit (« …:sulfur »), derrière un
        # souligné non (« …:vocab_fr_decouverte »).
        dirty = ("mechanical:us_spelling:sulfur", "mechanical:proscribed_vocab:decouverte")
        self.assertEqual(us_spelling_hits(dirty[0]), ["sulfur"])
        for code, lang in zip(dirty, ("en", "fr"), strict=True):
            clean = rewrite_code(code, lang)
            with self.subTest(code=code):
                self.assertEqual(us_spelling_hits(clean), [])
                for _, pattern in (*PROSCRIBED_FR, *PROSCRIBED_EN):
                    bounded = re.compile(rf"(?<!\w)(?:{pattern.pattern})(?!\w)")
                    self.assertIsNone(bounded.search(normalise(clean)))


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
            self.code_id = narrative_db.insert_story(
                conn,
                brief_id="SPR-2026-0003",
                lang="en",
                attempt=2,
                status="rejected",
                body_md="A body with sulfur in it.",
                guard_report_json=json.dumps(
                    report([CLEAN], ["mechanical:us_spelling:sulfur"]), ensure_ascii=False
                ),
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

    def test_only_the_offending_rows_change(self) -> None:
        before = self.rows()
        summary = run(self.db_path, dry_run=False)
        after = self.rows()
        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["rows_changed"], 2)
        self.assertEqual(summary["reasons_redacted"], 1)
        self.assertEqual(summary["codes_rewritten"], 1)
        self.assertEqual(summary["rules"], {"vocab_fr_decouverte": 1})
        self.assertEqual(
            sorted(c["story_id"] for c in summary["changed"]), sorted([self.dirty_id, self.code_id])
        )
        self.assertEqual(before[self.clean_id], after[self.clean_id])

    def test_reason_code_loses_the_word(self) -> None:
        run(self.db_path, dry_run=False)
        fixed = json.loads(self.rows()[self.code_id]["guard_report_json"])
        self.assertEqual(fixed["reasons"], ["mechanical:us_spelling:1"])
        self.assertEqual(us_spelling_hits(json.dumps(fixed["reasons"])), [])

    def test_only_the_report_column_is_rewritten(self) -> None:
        before = self.rows()
        run(self.db_path, dry_run=False)
        after = self.rows()
        for story_id, row in after.items():
            for column, value in row.items():
                if column == "guard_report_json":
                    continue
                with self.subTest(story_id=story_id, column=column):
                    # ``updated_at`` compris : la ligne n'a pas été republiée.
                    self.assertEqual(value, before[story_id][column])

    def test_dry_run_writes_nothing(self) -> None:
        before = self.rows()
        summary = run(self.db_path, dry_run=True)
        self.assertEqual(summary["rows_changed"], 2)
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
        self.assertEqual(summary["rows_changed"], 2)
        self.assertEqual(self.rows()[self.clean_id]["guard_report_json"], "{pas du JSON")


if __name__ == "__main__":
    unittest.main()
