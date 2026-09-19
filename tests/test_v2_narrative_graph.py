"""v2 — sous-graphe narratif et enveloppe ``run_narrative_layer`` (LLM simulé).

Couvre : chemin nominal FR → EN → thèmes → lien → voisines ; deux nouvelles
tentatives au plus (FR et EN) ; échec de parsing, doute et note sous le seuil
rejettent ; reprise idempotente ; registre de coût (une ligne par réponse,
troncature comprise) ; délai global dépassé (brouillon fermé, queue
mécanique rejouée) ; base de production refusée ; aucune exception ne sort.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_narrative_graph
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative import graph as narrative_graph
from narrative.config import override_config
from narrative.graph import run_narrative_layer
from tests.test_s11_llm_contract import TempDatabase
from tests.v2_narrative_support import (
    FakeScript,
    good_story_en,
    good_story_fr,
    insert_brief,
    insert_hypothesis,
    judge_verdict,
    make_config,
    rows,
    use_script,
)

BRIEF = "SPR-2026-0A01"


class NarrativeLayerTests(TempDatabase):
    """La couche sur une base temporaire, v1 initialisée."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.config = make_config(Path(self._tmp.name))
        insert_brief(self.db_path, BRIEF, domains=("Hydrology", "Materials Chemistry"))
        for index in range(4):
            insert_brief(self.db_path, f"SPR-2026-0B0{index}", domains=("Hydrology", "Oceanography"))
        insert_brief(self.db_path, "SPR-2026-0S01", is_stub=1, hypothesis_id="cus_1")

    async def run_layer(self, script: FakeScript, **kwargs: Any) -> dict[str, Any]:
        """Exécute la couche avec un script de réponses.

        Args:
            script: Réponses simulées.
            **kwargs: Arguments de ``run_narrative_layer``.

        Returns:
            Résumé de la couche.
        """
        kwargs.setdefault("brief_id", BRIEF)
        kwargs.setdefault("db_path", self.db_path)
        with use_script(script), override_config(self.config):
            return await run_narrative_layer(**kwargs)

    def stories(self, lang: str | None = None) -> list[dict[str, Any]]:
        """Lignes ``v2_stories`` du brief.

        Args:
            lang: Langue, ou toutes.

        Returns:
            Lignes par tentative.
        """
        sql = "SELECT * FROM v2_stories WHERE brief_id = ?"
        params: list[Any] = [BRIEF]
        if lang:
            sql += " AND lang = ?"
            params.append(lang)
        return rows(self.db_path, sql + " ORDER BY lang, attempt", params)

    async def test_nominal_path(self) -> None:
        script = FakeScript(
            {
                "story_writer": [good_story_fr()],
                "story_guard": [judge_verdict()],
                "translation": [good_story_en()],
            }
        )
        summary = await self.run_layer(script, hypothesis_id="SPORE-2026-09-19-abcd1234", run_id="run-x")
        self.assertEqual((summary["fr_status"], summary["en_status"]), ("published", "published"))

        fr, en = self.stories("fr"), self.stories("en")
        self.assertEqual([row["status"] for row in fr], ["published"])
        self.assertEqual([row["status"] for row in en], ["published"])
        self.assertEqual(en[0]["source_story_id"], fr[0]["id"])
        self.assertEqual(en[0]["story_year"], fr[0]["story_year"])
        self.assertEqual((fr[0]["writer_model"], fr[0]["guard_model"]), ("mock", "mock"))
        self.assertEqual(fr[0]["prompt_version"], "story_writer_v0")
        self.assertEqual(en[0]["prompt_version"], "story_translate_v0")
        self.assertEqual(fr[0]["guard_prompt_version"], "story_guard_v0")
        self.assertEqual(len(fr[0]["body_sha256"]), 64)
        report = json.loads(fr[0]["guard_report_json"])
        self.assertEqual(report["decision"], "published")
        self.assertEqual(set(report["judge"]["scores"]), set(judge_verdict()["scores"]))
        self.assertGreater(fr[0]["cost_usd"], 0.0)

        # Registre : une ligne par appel, étiquette par défaut « pipeline ».
        costs = rows(self.db_path, "SELECT node, run_label, brief_id FROM v2_llm_costs ORDER BY id")
        self.assertEqual(
            [row["node"] for row in costs],
            ["story_writer", "story_guard", "story_translate", "story_guard_en"],
        )
        self.assertEqual({row["run_label"] for row in costs}, {"pipeline"})
        self.assertEqual({row["brief_id"] for row in costs}, {BRIEF})

        # Étapes mécaniques.
        themes = rows(self.db_path, "SELECT * FROM v2_brief_themes WHERE brief_id = ?", [BRIEF])
        self.assertTrue(1 <= len(themes) <= 2)
        self.assertEqual({row["mapping_version"] for row in themes}, {"themes_v1"})
        link = rows(self.db_path, "SELECT * FROM v2_brief_hypothesis WHERE brief_id = ?", [BRIEF])
        self.assertEqual((link[0]["hypothesis_id"], link[0]["method"]), ("SPORE-2026-09-19-abcd1234", "pipeline_state"))
        neighbours = rows(self.db_path, "SELECT * FROM v2_brief_neighbours")
        self.assertTrue(neighbours)
        # Le stub seul ne se lie à personne ; aucune idée complète ne pointe vers lui.
        self.assertFalse([row for row in neighbours if "0S01" in row["neighbour_id"] or "0S01" in row["brief_id"]])

    async def test_writer_retries_at_most_twice_then_gives_up(self) -> None:
        script = FakeScript({"story_writer": ["pas du JSON"], "story_guard": [judge_verdict()], "translation": [good_story_en()]})
        summary = await self.run_layer(script)
        fr = self.stories("fr")
        self.assertEqual([row["attempt"] for row in fr], [1, 2, 3])
        self.assertEqual({row["status"] for row in fr}, {"rejected"})
        for row in fr:
            self.assertIn("writer:failed:JSONDecodeError", json.loads(row["guard_report_json"])["reasons"])
        self.assertEqual(self.stories("en"), [])
        self.assertEqual(summary["fr_status"], "rejected")
        # Trois tentatives, chacune avec le rejeu JSON unique de complete_json.
        self.assertEqual(script.count("story_writer"), 6)
        self.assertEqual(script.count("story_guard"), 0)
        # L'étage fiction tombe, le reste tourne.
        self.assertTrue(rows(self.db_path, "SELECT * FROM v2_brief_themes WHERE brief_id = ?", [BRIEF]))
        self.assertTrue(rows(self.db_path, "SELECT * FROM v2_brief_neighbours"))

    async def test_judge_doubt_rejects_every_attempt(self) -> None:
        script = FakeScript({"story_writer": [good_story_fr()], "story_guard": [judge_verdict(doubts=["Un doute."])]})
        await self.run_layer(script)
        fr = self.stories("fr")
        self.assertEqual(len(fr), 3)
        self.assertEqual({row["status"] for row in fr}, {"rejected"})
        self.assertIn("judge:doubt", json.loads(fr[0]["guard_report_json"])["reasons"])

    async def test_judge_parse_failure_rejects(self) -> None:
        script = FakeScript({"story_writer": [good_story_fr()], "story_guard": ["{cassé"]})
        await self.run_layer(script)
        self.assertEqual({row["status"] for row in self.stories("fr")}, {"rejected"})

    async def test_score_below_threshold_then_success(self) -> None:
        script = FakeScript(
            {
                "story_writer": [good_story_fr()],
                "story_guard": [judge_verdict(overrides={"fidelity": 4}), judge_verdict(), judge_verdict()],
                "translation": [good_story_en()],
            }
        )
        summary = await self.run_layer(script)
        fr = self.stories("fr")
        self.assertEqual([row["status"] for row in fr], ["rejected", "published"])
        self.assertIn("judge:below_threshold:fidelity", json.loads(fr[0]["guard_report_json"])["reasons"])
        self.assertEqual(summary["en_status"], "published")

    async def test_translation_retries_at_most_twice(self) -> None:
        script = FakeScript(
            {
                "story_writer": [good_story_fr()],
                "story_guard": [judge_verdict()],
                "translation": [good_story_en(mechanism="The behavior of the center was analyzed.")],
            }
        )
        summary = await self.run_layer(script)
        self.assertEqual([row["status"] for row in self.stories("fr")], ["published"])
        en = self.stories("en")
        self.assertEqual([row["attempt"] for row in en], [1, 2, 3])
        self.assertEqual({row["status"] for row in en}, {"rejected"})
        self.assertIn("mechanical:us_spelling", " ".join(json.loads(en[0]["guard_report_json"])["reasons"]))
        self.assertEqual(summary["en_status"], "rejected")

    async def test_resume_is_idempotent(self) -> None:
        script = FakeScript({"story_writer": [good_story_fr()], "story_guard": [judge_verdict()], "translation": [good_story_en()]})
        await self.run_layer(script)
        before = self.stories()
        calls = len(script.calls)
        await self.run_layer(script)
        self.assertEqual(self.stories(), before)
        self.assertEqual(len(script.calls), calls)

    async def test_truncation_is_retried_and_billed(self) -> None:
        script = FakeScript(
            {
                "story_writer": [("{\"title\": \"coupé", "length"), good_story_fr()],
                "story_guard": [judge_verdict()],
                "translation": [good_story_en()],
            }
        )
        await self.run_layer(script)
        writer_costs = rows(self.db_path, "SELECT * FROM v2_llm_costs WHERE node = 'story_writer'")
        # La réponse tronquée est facturée : deux lignes pour la rédaction.
        self.assertEqual(len(writer_costs), 2)
        self.assertEqual([row["status"] for row in self.stories("fr")], ["published"])

    async def test_run_label_from_environment(self) -> None:
        script = FakeScript({"story_writer": [good_story_fr()], "story_guard": [judge_verdict()], "translation": [good_story_en()]})
        with mock.patch.dict("os.environ", {"SPORE_V2_RUN_LABEL": "canary"}):
            await self.run_layer(script)
        self.assertEqual({row["run_label"] for row in rows(self.db_path, "SELECT run_label FROM v2_llm_costs")}, {"canary"})
        self.assertEqual({row["run_label"] for row in self.stories()}, {"canary"})

    async def test_global_timeout_closes_drafts_and_runs_the_tail(self) -> None:
        script = FakeScript(
            {"story_writer": [good_story_fr()], "story_guard": [judge_verdict()]},
            delay={"story_guard": 5.0},
        )
        self.config = self.config.with_changes(layer_timeout_s=0.5)
        summary = await self.run_layer(script)
        self.assertEqual(summary["failed"], "layer_timeout")
        fr = self.stories("fr")
        self.assertEqual(len(fr), 1)
        self.assertEqual(fr[0]["status"], "rejected")
        self.assertIn("layer_timeout", json.loads(fr[0]["guard_report_json"])["reasons"])
        self.assertTrue(rows(self.db_path, "SELECT * FROM v2_brief_themes WHERE brief_id = ?", [BRIEF]))
        self.assertTrue(rows(self.db_path, "SELECT * FROM v2_brief_neighbours"))

    async def test_subgraph_crash_never_escapes(self) -> None:
        broken = mock.Mock()
        broken.ainvoke = mock.AsyncMock(side_effect=RuntimeError("boom"))
        with mock.patch.object(narrative_graph, "compiled_narrative_graph", return_value=broken):
            summary = await self.run_layer(FakeScript({}))
        self.assertEqual(summary["failed"], "layer_failed:RuntimeError")
        self.assertTrue(rows(self.db_path, "SELECT * FROM v2_brief_themes WHERE brief_id = ?", [BRIEF]))

    async def test_unpublished_or_stub_brief_is_skipped(self) -> None:
        insert_brief(self.db_path, "SPR-2026-0P01", status="pending")
        script = FakeScript({"story_writer": [good_story_fr()]})
        summary = await self.run_layer(script, brief_id="SPR-2026-0P01")
        self.assertEqual((summary["ran"], summary["reason"]), (False, "brief_not_published_full"))
        summary = await self.run_layer(script, brief_id="SPR-2026-0S01")
        self.assertFalse(summary["ran"])
        summary = await self.run_layer(script, brief_id="SPR-2026-FFFF")
        self.assertEqual(summary["reason"], "brief_missing")
        self.assertEqual(script.count("story_writer"), 0)
        self.assertEqual(rows(self.db_path, "SELECT * FROM v2_stories"), [])

    async def test_production_database_is_refused_before_any_write(self) -> None:
        target = Path("/home/baq/Projects/spore-poc/data/v2-test-never-created.db")
        script = FakeScript({"story_writer": [good_story_fr()]})
        summary = await self.run_layer(script, db_path=target)
        self.assertEqual(summary["failed"], "layer_error:UnsafePathError")
        self.assertFalse(target.exists())
        self.assertEqual(script.count("story_writer"), 0)

    async def test_self_referencing_hypothesis_is_not_linked(self) -> None:
        script = FakeScript({"story_writer": ["x"]})
        await self.run_layer(script, hypothesis_id=BRIEF)
        self.assertEqual(rows(self.db_path, "SELECT * FROM v2_brief_hypothesis"), [])

    async def test_linked_hypothesis_embeddings_drive_neighbours(self) -> None:
        insert_hypothesis(
            self.db_path,
            "H-1",
            summary="s",
            domain_a=("Hydrology", "Earth Sciences", [1.0, 0.0, 0.0]),
            domain_b=("Materials Chemistry", "Chemistry", [0.0, 1.0, 0.0]),
        )
        script = FakeScript({"story_writer": ["x"]})
        await self.run_layer(script, hypothesis_id="H-1")
        methods = {row["method"] for row in rows(self.db_path, "SELECT method FROM v2_brief_neighbours")}
        self.assertIn("domain_embedding", methods)


if __name__ == "__main__":
    unittest.main()
