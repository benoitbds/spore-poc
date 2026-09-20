"""v2 — script de calibration ``scripts/v2/calibrate_narrative.py`` (LLM simulé).

* Itération réelle : copie ``.backup`` de la base source (jamais écrite), vrai
  sous-graphe narratif, étiquette ``calibration``, export du jury (contexte,
  une fiche par tentative avec texte, sans verdict ; rapports du garde à
  part), ``summary.json``, ``spend.json`` réécrit.
* Refus avant toute copie et tout appel : stub ou brief non publié dans le
  jeu, itération déjà lancée, budget épuisé, base source de production.
* ``--dry-run`` : contrôles seulement.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_calibrate_narrative
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as spore_config
from narrative.config import DEFAULT_CONFIG_PATH, load_config
from scripts.v2 import calibrate_narrative
from storage import init_database
from tests.v2_narrative_support import (
    DENYLIST_ENTRY,
    FakeScript,
    good_story_en,
    good_story_fr,
    insert_brief,
    judge_verdict,
    rows,
    use_script,
)

BRIEFS = ["SPR-2026-CA01", "SPR-2026-CA02"]
STUB = "SPR-2026-CA50"
PENDING = "SPR-2026-CA60"

#: Mots d'un verdict : aucune fiche destinée au jury ne doit les contenir.
VERDICT_WORDS = ("published", "rejected", "decision", "mechanical", "judge", "reasons", "draft")


class CalibrationScriptTests(unittest.TestCase):
    """Calibration sur une base source temporaire au schéma v1."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "source.db"
        self._env = mock.patch.dict(os.environ, {"SPORE_DB_PATH": str(self.source)})
        self._env.start()
        self._saved_settings = spore_config._settings
        spore_config._settings = None
        asyncio.run(init_database())
        for brief_id in BRIEFS:
            insert_brief(self.source, brief_id, domains=("Hydrology", "Materials Chemistry"))
        insert_brief(self.source, STUB, is_stub=1, hypothesis_id="cus_1")
        insert_brief(self.source, PENDING, status="pending")

        denylist = self.root / "denylist.txt"
        denylist.write_text(DENYLIST_ENTRY + "\n", encoding="utf-8")
        raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        raw["paths"]["identity_denylist"] = str(denylist)
        raw["retry"]["base_delay_s"] = 0.0
        raw["retry"]["max_delay_s"] = 0.0
        self.config_path = self.root / "narrative.yaml"
        self.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.spend_json = self.root / "evidence" / "spend.json"

    def tearDown(self) -> None:
        spore_config._settings = self._saved_settings
        self._env.stop()
        self._tmp.cleanup()

    def paths(self, iteration: int) -> tuple[Path, Path]:
        """Copie et export d'une itération."""
        return self.root / "data" / f"calib-iter{iteration}.db", self.root / "out" / f"iter{iteration}"

    def main(self, *extra: str, iteration: int = 1, briefs: list[str] | None = None) -> tuple[int, str, str]:
        """Lance le script ; rend le code, la sortie et l'erreur standard."""
        copy, out_dir = self.paths(iteration)
        argv = [
            "--iteration", str(iteration),
            "--briefs", *(briefs or BRIEFS),
            "--base-db", str(self.source),
            "--copy-db", str(copy),
            "--out", str(out_dir),
            "--spend-json", str(self.spend_json),
            "--config", str(self.config_path),
            *extra,
        ]  # fmt: skip
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "dummy"}),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = calibrate_narrative.main(argv)
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def script() -> FakeScript:
        """Premier récit trop court (rejeté par le garde mécanique), puis récits valides."""
        return FakeScript(
            {
                "story_writer": [good_story_fr(body_markdown="Trop court pour un récit."), good_story_fr()],
                "story_guard": [judge_verdict()],
                "translation": [good_story_en()],
            }
        )

    def test_iteration_runs_the_real_layer_and_exports_for_the_jury(self) -> None:
        script = self.script()
        with use_script(script):
            code, _, err = self.main()
        self.assertEqual(code, 0, err[-2000:])
        copy, out_dir = self.paths(1)

        # La source n'est jamais écrite ; la copie porte les récits étiquetés « calibration ».
        self.assertEqual(rows(self.source, "SELECT name FROM sqlite_master WHERE name LIKE 'v2_%'"), [])
        stories = rows(copy, "SELECT brief_id, lang, attempt, status, run_label FROM v2_stories ORDER BY brief_id, lang, attempt")
        self.assertEqual(
            [(r["brief_id"], r["lang"], r["attempt"], r["status"]) for r in stories],
            [
                (BRIEFS[0], "en", 1, "published"),
                (BRIEFS[0], "fr", 1, "rejected"),
                (BRIEFS[0], "fr", 2, "published"),
                (BRIEFS[1], "en", 1, "published"),
                (BRIEFS[1], "fr", 1, "published"),
            ],
        )
        self.assertEqual({r["run_label"] for r in stories}, {"calibration"})
        self.assertEqual({r["run_label"] for r in rows(copy, "SELECT run_label FROM v2_llm_costs")}, {"calibration"})

        # Dossier du jury : contexte, une fiche par tentative, rapports à part.
        folder = out_dir / BRIEFS[0]
        context = (folder / "context.md").read_text(encoding="utf-8")
        self.assertIn("La pression baisse de 20 %. (borne : entre 15 et 25 %)", context)
        self.assertIn("Les sédiments réduisent l'effet.", context)
        self.assertIn("Condition de validité : Eau peu chargée", context)
        self.assertNotIn("10.1/x", context)
        self.assertEqual(
            sorted(p.name for p in folder.glob("*.md")), ["context.md", "en__a1.md", "fr__a1.md", "fr__a2.md"]
        )
        self.assertEqual(
            sorted(p.name for p in (folder / "guard").iterdir()), ["en__a1.json", "fr__a1.json", "fr__a2.json"]
        )
        story_rows = {
            (r["lang"], r["attempt"]): r
            for r in rows(copy, "SELECT * FROM v2_stories WHERE brief_id = ?", [BRIEFS[0]])
        }
        for name in ("fr__a1", "fr__a2", "en__a1"):
            text = (folder / f"{name}.md").read_text(encoding="utf-8")
            lang, attempt = name.split("__a")
            row = story_rows[(lang, int(attempt))]
            self.assertIn(f"story_id : {row['id']}", text)
            self.assertIn(f"body_sha256 : {row['body_sha256']}", text)
            self.assertIn(f"Année du récit : {row['story_year']}", text)
            self.assertIn(row["body_md"].strip(), text)
            for word in VERDICT_WORDS:
                self.assertNotIn(word, text.casefold())
        guard = json.loads((folder / "guard" / "fr__a1.json").read_text(encoding="utf-8"))
        self.assertEqual(guard["status"], "rejected")
        self.assertTrue(any(reason.startswith("mechanical:length") for reason in guard["report"]["reasons"]))

        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        first = summary["briefs"][BRIEFS[0]]
        self.assertEqual((first["fr"]["attempts"], first["fr"]["final_status"], first["fr"]["published_attempt"]), (2, "published", 2))
        self.assertEqual((first["en"]["attempts"], first["en"]["final_status"]), (1, "published"))
        self.assertTrue(first["fr"]["detail"][0]["reasons"])
        self.assertGreaterEqual(first["fr"]["detail"][1]["words"], 450)
        self.assertEqual(summary["totals"]["fr_published"], 2)
        self.assertEqual(summary["totals"]["en_published"], 2)
        self.assertGreater(summary["totals"]["cost_usd"], 0.0)
        # Version lue dans la configuration, jamais écrite en dur : une révision
        # de prompt (calibration) ne doit pas casser ce test. Ce qui compte ici,
        # c'est que le résumé reporte bien la version réellement configurée.
        self.assertEqual(
            summary["prompt_versions"]["configured"]["story_writer"],
            load_config(self.config_path).writer.prompt,
        )
        self.assertEqual(summary["run_label"], "calibration")
        self.assertIsNone(summary["stopped"])

        spend = json.loads(self.spend_json.read_text(encoding="utf-8"))
        self.assertIn(str(copy.resolve()), spend["sources"])
        self.assertGreater(spend["total_usd"], 0.0)
        self.assertEqual(spend["by_label"].keys(), {"calibration"})

        # Une itération ne se relance pas sur sa propre copie.
        code, _, err = self.main()
        self.assertEqual(code, 2)
        self.assertIn("déjà", err)

    def test_parallel_iteration_processes_every_brief(self) -> None:
        with use_script(self.script()):
            code, out, err = self.main("--concurrency", "2", iteration=2)
        self.assertEqual(code, 0, err[-2000:])
        totals = json.loads(out)
        self.assertEqual((totals["briefs_processed"], totals["fr_published"], totals["en_published"]), (2, 2, 2))

    def test_refusals_happen_before_any_copy_or_call(self) -> None:
        script = self.script()
        cases: list[tuple[list[str], dict[str, Any]]] = [
            ([], {"briefs": [BRIEFS[0], STUB]}),
            ([], {"briefs": [BRIEFS[0], PENDING]}),
            ([], {"briefs": [BRIEFS[0], BRIEFS[0]]}),
            (["--max-usd", "0"], {}),
            ([], {"iteration": 0}),
            (["--run-label", "pipeline"], {}),
        ]
        with use_script(script):
            for extra, kwargs in cases:
                with self.subTest(extra=extra, **kwargs):
                    code, _, err = self.main(*extra, **kwargs)
                    self.assertEqual(code, 2)
                    self.assertIn("error", err)
        self.assertEqual(script.calls, [])
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / "out").exists())

    def test_production_source_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "dummy"}), contextlib.redirect_stderr(io.StringIO()) as err:
            code = calibrate_narrative.main(
                [
                    "--iteration", "1",
                    "--base-db", "/home/baq/Projects/spore-poc/data/spore.db",
                    "--copy-db", str(self.root / "c.db"),
                    "--out", str(self.root / "o"),
                    "--config", str(self.config_path),
                ]
            )  # fmt: skip
        self.assertEqual(code, 2)
        self.assertIn("production", err.getvalue())
        self.assertFalse((self.root / "c.db").exists())

    def test_dry_run_checks_only(self) -> None:
        script = self.script()
        with use_script(script):
            code, out, _ = self.main("--dry-run")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertEqual(sorted(plan["briefs"]), BRIEFS)
        # Version lue dans la configuration, jamais écrite en dur : une révision
        # de prompt (calibration) ne doit pas casser ce test.
        self.assertEqual(
            plan["prompt_versions"]["story_guard"],
            load_config(self.config_path).guard.llm.prompt,
        )
        self.assertEqual(script.calls, [])
        self.assertFalse((self.root / "data").exists())
        self.assertFalse(self.spend_json.exists())

    def test_every_attempt_has_a_sheet_even_without_text(self) -> None:
        # Rédaction en échec : la tentative est enregistrée sans corps
        # (narrative.story._record_failure). Sa fiche doit exister quand même,
        # sinon la numérotation des tentatives a un trou inexpliqué pour le jury.
        script = FakeScript(
            {
                # Deux sorties illisibles : l'appel et son unique rejeu
                # (llm.json_parse.complete_json) échouent, la tentative 1 est
                # enregistrée sans corps ; la tentative 2 réussit.
                "story_writer": ["pas du JSON", "pas du JSON", good_story_fr()],
                "story_guard": [judge_verdict()],
                "translation": [good_story_en()],
            }
        )
        with use_script(script):
            code, _, err = self.main(iteration=3, briefs=[BRIEFS[0]])
        self.assertEqual(code, 0, err[-2000:])
        copy, out_dir = self.paths(3)
        folder = out_dir / BRIEFS[0]
        stories = rows(
            copy,
            "SELECT lang, attempt, body_md FROM v2_stories WHERE brief_id = ? ORDER BY lang, attempt",
            [BRIEFS[0]],
        )
        self.assertEqual([(r["lang"], r["attempt"]) for r in stories], [("en", 1), ("fr", 1), ("fr", 2)])
        self.assertIsNone(stories[1]["body_md"])
        self.assertEqual(
            sorted(p.name for p in folder.glob("*.md")),
            ["context.md", "en__a1.md", "fr__a1.md", "fr__a2.md"],
        )
        empty = (folder / "fr__a1.md").read_text(encoding="utf-8")
        self.assertIn(calibrate_narrative.NO_TEXT_BODY, empty)
        # Pas de verdict dans la fiche, pas même la raison de l'échec.
        self.assertNotIn("JSONDecodeError", empty)
        for word in VERDICT_WORDS:
            self.assertNotIn(word, empty.casefold())
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        detail = summary["briefs"][BRIEFS[0]]["fr"]["detail"]
        self.assertEqual([item["has_text"] for item in detail], [False, True])
        self.assertEqual(
            [item["file"] for item in detail], [f"{BRIEFS[0]}/fr__a1.md", f"{BRIEFS[0]}/fr__a2.md"]
        )
        self.assertIsNone(detail[0]["words"])
        guard = json.loads((folder / "guard" / "fr__a1.json").read_text(encoding="utf-8"))
        self.assertFalse(guard["has_text"])
        self.assertTrue(any("writer:failed" in reason for reason in guard["report"]["reasons"]))

    def test_default_set_is_ten_distinct_briefs(self) -> None:
        self.assertEqual(len(set(calibrate_narrative.CALIBRATION_SET)), 10)


if __name__ == "__main__":
    unittest.main()
