"""v2 — script de backfill ``scripts/v2/backfill_narrative.py`` (LLM simulé).

* ``--dry-run`` : lecture seule (aucune table ``v2_*`` créée), plan, liens
  ``fk`` / ``text_match`` par les sidecars, thèmes et maillage en mémoire.
* Passage réel : récits FR et EN par le code du pipeline, liens, thèmes,
  voisines, registre étiqueté ``backfill``, ``spend.json`` réécrit ; un
  second passage n'appelle plus aucun LLM (idempotent, reprenable).
* Plafond : ``--max-usd 0`` arrête avant tout appel.
* Refus : base ou sidecars situés en production.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_narrative_backfill
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as spore_config
from narrative.config import DEFAULT_CONFIG_PATH
from narrative.safety import UnsafePathError
from scripts.v2 import backfill_narrative
from storage import init_database
from tests.v2_narrative_support import (
    DENYLIST_ENTRY,
    FakeScript,
    good_story_en,
    good_story_fr,
    insert_brief,
    insert_hypothesis,
    judge_verdict,
    rows,
    use_script,
)

FULL = [f"SPR-2026-1{index:03X}" for index in range(6)]
STUBS = [f"SPR-2026-5{index:03X}" for index in range(4)]


class BackfillTests(unittest.TestCase):
    """Backfill sur une base temporaire au schéma v1."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "work.db"
        self.sidecars = root / "outputs" / "briefs"
        self.sidecars.mkdir(parents=True)
        self.spend_json = root / "evidence" / "spend.json"
        self._env = mock.patch.dict(os.environ, {"SPORE_DB_PATH": str(self.db_path)})
        self._env.start()
        self._saved_settings = spore_config._settings
        spore_config._settings = None
        asyncio.run(init_database())

        vectors = [[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1, 0, 0], [0, 0.9, 0.1, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        for index, brief_id in enumerate(FULL):
            insert_brief(self.db_path, brief_id, domains=(f"Domain {index}", "Hydrology"))
            insert_hypothesis(
                self.db_path,
                f"SPORE-H{index}",
                summary=f"Hypothèse numéro {index}",
                domain_a=(f"Domain {index}", "Earth Sciences", vectors[index]),
                domain_b=("Hydrology", "Earth Sciences", [0.5, 0.5, 0.5, 0.5]),
            )
            if index < 4:
                (self.sidecars / f"{brief_id}.json").write_text(
                    json.dumps({"original_hypothesis": f"Hypothèse numéro {index}"}), encoding="utf-8"
                )
        # Un lien par clé exploitable (briefs.hypothesis_id désigne une hypothèse).
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE briefs SET hypothesis_id = 'SPORE-H4' WHERE id = ?", (FULL[4],))
        conn.commit()
        conn.close()
        for index, stub in enumerate(STUBS):
            insert_brief(self.db_path, stub, is_stub=1, hypothesis_id=f"cus_{index}")
        insert_brief(self.db_path, "SPR-2026-9999", status="rejected")

        denylist = root / "denylist.txt"
        denylist.write_text(DENYLIST_ENTRY + "\n", encoding="utf-8")
        raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        raw["paths"]["identity_denylist"] = str(denylist)
        raw["retry"]["base_delay_s"] = 0.0
        raw["backfill"]["rate_limit_s"] = 0.0
        self.config_path = root / "narrative.yaml"
        self.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    def tearDown(self) -> None:
        spore_config._settings = self._saved_settings
        self._env.stop()
        self._tmp.cleanup()

    def main(self, *extra: str) -> dict[str, Any]:
        """Lance le script et décode son résumé JSON.

        Args:
            *extra: Arguments supplémentaires.

        Returns:
            Résumé.
        """
        argv = [
            "--db", str(self.db_path),
            "--sidecars-dir", str(self.sidecars),
            "--spend-json", str(self.spend_json),
            "--config", str(self.config_path),
            *extra,
        ]
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}), contextlib.redirect_stdout(out):
            code = backfill_narrative.main(argv)
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_dry_run_writes_nothing(self) -> None:
        summary = self.main("--dry-run")
        self.assertEqual(summary["published"], {"full": 6, "stubs": 4})
        self.assertEqual(summary["stories"]["briefs_with_work"], 6)
        self.assertEqual(summary["links"]["new_by_method"], {"text_match": 4, "fk": 1})
        self.assertEqual(summary["links"]["unresolved_count"], 1)
        self.assertEqual(summary["neighbours"]["full_inbound_min_max"][0], 3)
        self.assertEqual(summary["neighbours"]["stub_inbound_min_max"], [3, 3])
        tables = rows(self.db_path, "SELECT name FROM sqlite_master WHERE name LIKE 'v2_%'")
        self.assertEqual(tables, [])
        self.assertFalse(self.spend_json.exists())

    def test_run_then_resume_is_idempotent(self) -> None:
        script = FakeScript(
            {"story_writer": [good_story_fr()], "story_guard": [judge_verdict()], "translation": [good_story_en()]}
        )
        with use_script(script):
            first = self.main("--run-label", "backfill")
        self.assertEqual(first["stories"]["planned"], 6)
        self.assertEqual({item["fr_status"] for item in first["stories"]["processed"]}, {"published"})
        self.assertEqual({item["en_status"] for item in first["stories"]["processed"]}, {"published"})
        published = rows(self.db_path, "SELECT lang, COUNT(*) AS n FROM v2_stories WHERE status = 'published' GROUP BY lang")
        self.assertEqual({row["lang"]: row["n"] for row in published}, {"en": 6, "fr": 6})
        links = rows(self.db_path, "SELECT method, COUNT(*) AS n FROM v2_brief_hypothesis GROUP BY method")
        self.assertEqual({row["method"]: row["n"] for row in links}, {"text_match": 4, "fk": 1})
        self.assertEqual(len(rows(self.db_path, "SELECT DISTINCT brief_id FROM v2_brief_themes")), 6)
        self.assertEqual(first["neighbours"]["full_inbound_min_max"][0], 3)
        self.assertEqual({row["run_label"] for row in rows(self.db_path, "SELECT run_label FROM v2_llm_costs")}, {"backfill"})
        spend = json.loads(self.spend_json.read_text(encoding="utf-8"))
        self.assertGreater(spend["total_usd"], 0.0)
        self.assertIn(str(self.db_path.resolve()), spend["sources"])

        calls = len(script.calls)
        with use_script(script):
            second = self.main("--run-label", "backfill")
        self.assertEqual(second["stories"]["planned"], 0)
        self.assertEqual(len(script.calls), calls)

    def test_budget_cap_stops_before_any_call(self) -> None:
        script = FakeScript({"story_writer": [good_story_fr()], "story_guard": [judge_verdict()], "translation": [good_story_en()]})
        with use_script(script):
            summary = self.main("--max-usd", "0", "--no-spend-update")
        self.assertEqual(summary["stories"]["stopped"], "budget_cap")
        self.assertEqual(script.calls, [])
        # Les étapes mécaniques ont tourné.
        self.assertTrue(rows(self.db_path, "SELECT * FROM v2_brief_neighbours"))

    def test_limit_and_brief_filters(self) -> None:
        summary = self.main("--dry-run", "--limit", "2")
        self.assertEqual(summary["stories"]["briefs_with_work"], 2)
        summary = self.main("--dry-run", "--brief", FULL[3])
        self.assertEqual(summary["stories"]["first"], [FULL[3]])

    def test_production_database_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {}), self.assertRaises(UnsafePathError):
            backfill_narrative.main(["--db", "/home/baq/Projects/spore-poc/data/v2-never.db", "--dry-run"])

    def test_production_sidecars_are_refused(self) -> None:
        with mock.patch.dict(os.environ, {}), self.assertRaises(SystemExit):
            backfill_narrative.main(
                [
                    "--db", str(self.db_path),
                    "--dry-run",
                    "--config", str(self.config_path),
                    "--spend-json", str(self.spend_json),
                    "--sidecars-dir", "/home/baq/Projects/spore-poc/outputs/briefs",
                ]
            )


if __name__ == "__main__":
    unittest.main()
