"""v2 — couche narrative câblée dans le graphe Post-Fire réel.

* Câblage : le graphe Post-Fire compilé contient ``narrative_layer`` après
  ``validate_brief`` (bloc additif), et rien d'autre n'a bougé.
* Fail-closed de bout en bout, par ``run_post_fire_pipeline`` : les nœuds
  amont sont remplacés par des doubles pour atteindre vite le vrai
  ``validate_brief`` ; le récit est rejeté, le brief reste ``complete``,
  aucune exception ne sort, l'état final n'est pas modifié par la couche.
* Garde d'import : les modules chargés viennent du clone
  ``~/Projects/spore-v2-poc`` (le venv de production porte un ``.pth`` vers la
  production).

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_narrative_pipeline
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import graph.post_fire_pipeline as post_fire
from narrative import graph as narrative_graph
from narrative.config import override_config
from tests.test_s11_llm_contract import TempDatabase
from tests.v2_narrative_support import (
    FakeScript,
    make_config,
    rows,
    sharpened,
    use_script,
)

CLONE_ROOT = Path("/home/baq/Projects/spore-v2-poc")
BRIEF = "SPR-2026-E2E0"
HYPOTHESIS = "SPORE-2026-09-19-e2e00000"

#: Nœuds du graphe Post-Fire de ``pre-v2`` (le bloc v2 n'en retire aucun).
V1_NODES = {
    "grounding_router",
    "literature_grounding",
    "skip_grounding",
    "persist_grounding_kill",
    "persist_panel_reject",
    "hypothesis_sharpening",
    "experimental_protocol",
    "multi_reviewer_panel",
    "normalize_panel_language",
    "research_brief_generator",
    "vulgarization",
    "translation_hook",
    "validate_brief",
}


class ImportOriginTests(unittest.TestCase):
    """Les modules exercés viennent du clone, jamais de la production."""

    def test_modules_come_from_the_clone(self) -> None:
        for name in (
            "config",
            "logging_config",
            "llm.client",
            "llm.json_parse",
            "storage.database",
            "storage.narrative_db",
            "graph.post_fire_pipeline",
            "narrative",
            "narrative.graph",
            "narrative.guard",
            "scripts.translate_brief_vulgarization",
        ):
            with self.subTest(module=name):
                module = importlib.import_module(name)
                origin = Path(module.__file__).resolve()
                self.assertTrue(
                    origin.is_relative_to(CLONE_ROOT),
                    f"{name} importé depuis {origin}",
                )
                self.assertFalse(origin.is_relative_to(Path("/home/baq/Projects/spore-poc")))

    def test_config_module_is_not_shadowed_by_the_config_directory(self) -> None:
        # ``config/narrative/`` vit à côté du module cœur ``config.py`` : un
        # ``config/__init__.py`` le masquerait et le pipeline perdrait
        # ``get_settings``. Ce répertoire ne doit jamais devenir un paquet.
        import config

        self.assertEqual(Path(config.__file__).name, "config.py")
        self.assertTrue(callable(getattr(config, "get_settings", None)))
        self.assertFalse((CLONE_ROOT / "config" / "__init__.py").exists())


class WiringTests(unittest.TestCase):
    """Le bloc additif du graphe Post-Fire."""

    def test_narrative_layer_is_reachable_after_validate_brief(self) -> None:
        compiled = post_fire.create_post_fire_pipeline().compile()
        drawn = compiled.get_graph()
        edges = {(edge.source, edge.target) for edge in drawn.edges}
        self.assertIn(("validate_brief", "narrative_layer"), edges)
        self.assertIn(("narrative_layer", "__end__"), edges)
        self.assertTrue(V1_NODES <= set(drawn.nodes))
        self.assertIn("narrative_layer", drawn.nodes)
        # Le reste du câblage v1 est intact.
        for edge in (
            ("translation_hook", "validate_brief"),
            ("vulgarization", "translation_hook"),
            ("research_brief_generator", "vulgarization"),
            ("persist_panel_reject", "__end__"),
            ("persist_grounding_kill", "__end__"),
        ):
            self.assertIn(edge, edges)

    def test_wired_node_is_the_narrative_wrapper(self) -> None:
        graph = post_fire.create_post_fire_pipeline()
        # LangGraph 1.1.10 : StateNodeSpec.runnable est un RunnableCallable dont
        # ``afunc`` porte la coroutine enregistrée.
        runnable = graph.nodes["narrative_layer"].runnable
        self.assertIs(getattr(runnable, "afunc", None), narrative_graph.node_narrative_layer)

    def test_post_fire_state_is_unchanged(self) -> None:
        keys = set(post_fire.PostFireState.__annotations__)
        self.assertFalse({key for key in keys if "story" in key or "narrative" in key or "theme" in key})


def _panel() -> dict[str, Any]:
    return {
        "reviews": [{"reviewer_persona": "methodologist", "overall_score": 8, "verdict": "publish"}],
        "meta_review": {"consensus_score": 8.5, "verdict": "publish_brief"},
    }


class EndToEndFailClosedTests(TempDatabase):
    """``run_post_fire_pipeline`` jusqu'au vrai ``validate_brief``, puis la couche."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.config = make_config(Path(self._tmp.name))

    def patches(self, *, incoherent_panel: bool = False) -> list[Any]:
        """Doubles des nœuds amont et des contrôles de panel.

        Args:
            incoherent_panel: Faire échouer le contrôle de cohérence du panel
                (le brief reste ``pending``).

        Returns:
            Gestionnaires ``mock.patch``.
        """
        db_path = self.db_path

        async def grounding(state: dict[str, Any]) -> dict[str, Any]:
            return {"grounding": {"evidence_base": [{"title": "p"}], "counter_evidence": []}}

        async def sharpening(state: dict[str, Any]) -> dict[str, Any]:
            return {"sharpened": sharpened(["Hydrology", "Materials Chemistry"])}

        async def protocol(state: dict[str, Any]) -> dict[str, Any]:
            return {"protocol": {"protocol_title": "p"}}

        async def panel(state: dict[str, Any]) -> dict[str, Any]:
            return {
                "panel": _panel(),
                "meta_verdict": "publish_brief",
                "selection_threshold": 6.0,
            }

        async def passthrough(state: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def research_brief(state: dict[str, Any]) -> dict[str, Any]:
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "INSERT INTO briefs (id, hypothesis_id, status, sharpened_data) VALUES (?, ?, 'pending', ?)",
                    (BRIEF, state.get("hypothesis_id") or BRIEF, json.dumps(state["sharpened"])),
                )
                conn.commit()
            finally:
                conn.close()
            return {"brief_id": BRIEF}

        async def vulgarization(state: dict[str, Any]) -> dict[str, Any]:
            return {"vulgarization_fr": {"title_fr": "Titre", "hypothesis_in_brief": "Résumé."}}

        return [
            mock.patch.object(post_fire, "is_ss_circuit_open", lambda: False),
            mock.patch.object(post_fire, "node_literature_grounding", grounding),
            mock.patch.object(post_fire, "node_hypothesis_sharpening", sharpening),
            mock.patch.object(post_fire, "node_experimental_protocol", protocol),
            mock.patch.object(post_fire, "node_multi_reviewer_panel", panel),
            mock.patch.object(post_fire, "node_normalize_panel_language", passthrough),
            mock.patch.object(post_fire, "node_research_brief", research_brief),
            mock.patch.object(post_fire, "node_vulgarization", vulgarization),
            mock.patch.object(post_fire, "node_translation_hook", passthrough),
            mock.patch.object(
                post_fire, "check_panel", lambda panel: ["incohérent"] if incoherent_panel else []
            ),
            mock.patch.object(post_fire, "check_panel_language", lambda panel, lang: []),
        ]

    async def run_pipeline(self, script: FakeScript, *, incoherent_panel: bool = False) -> dict[str, Any]:
        """Lance le post-fire avec les doubles et un script LLM pour la couche.

        Args:
            script: Réponses simulées de la couche narrative.
            incoherent_panel: Voir ``patches``.

        Returns:
            État final du post-fire.
        """
        managers = self.patches(incoherent_panel=incoherent_panel)
        for manager in managers:
            manager.start()
        try:
            with use_script(script), override_config(self.config):
                return await post_fire.run_post_fire_pipeline(
                    hypothesis="H",
                    domains=["Hydrology", "Materials Chemistry"],
                    mechanisms="M",
                    run_id="run-e2e",
                    hypothesis_id=HYPOTHESIS,
                )
        finally:
            for manager in reversed(managers):
                manager.stop()

    def brief_status(self) -> str:
        """Statut du brief en base.

        Returns:
            Statut.
        """
        return rows(self.db_path, "SELECT status FROM briefs WHERE id = ?", [BRIEF])[0]["status"]

    async def test_story_rejected_brief_complete_no_exception(self) -> None:
        # Rédacteur hors service : JSON invalide à chaque appel.
        script = FakeScript({"story_writer": ["pas du JSON"], "story_guard": ["{}"], "translation": ["{}"]})
        final = await self.run_pipeline(script)  # aucune exception ne doit sortir

        self.assertTrue(final["brief_validated"])
        self.assertEqual(final["brief_id"], BRIEF)
        self.assertEqual(self.brief_status(), "complete")
        stories = rows(self.db_path, "SELECT * FROM v2_stories WHERE brief_id = ?", [BRIEF])
        self.assertEqual(len(stories), 3)
        self.assertEqual({row["status"] for row in stories}, {"rejected"})
        self.assertFalse(rows(self.db_path, "SELECT * FROM v2_stories WHERE status = 'published'"))
        # La couche a quand même posé thèmes et lien, sans toucher à l'état.
        self.assertTrue(rows(self.db_path, "SELECT * FROM v2_brief_themes WHERE brief_id = ?", [BRIEF]))
        link = rows(self.db_path, "SELECT * FROM v2_brief_hypothesis WHERE brief_id = ?", [BRIEF])
        self.assertEqual(link[0]["hypothesis_id"], HYPOTHESIS)
        self.assertFalse({key for key in final if "story" in key or "theme" in key})

    async def test_layer_crash_is_contained(self) -> None:
        with mock.patch.object(narrative_graph, "run_narrative_layer", mock.AsyncMock(side_effect=RuntimeError("boom"))):
            final = await self.run_pipeline(FakeScript({}))
        self.assertTrue(final["brief_validated"])
        self.assertEqual(self.brief_status(), "complete")

    async def test_layer_skips_an_unvalidated_brief(self) -> None:
        script = FakeScript({"story_writer": ["x"]})
        final = await self.run_pipeline(script, incoherent_panel=True)
        self.assertFalse(final["brief_validated"])
        self.assertEqual(self.brief_status(), "pending")
        # Brief non promu : la couche ne fait rien, pas même un appel.
        self.assertEqual(script.count("story_writer"), 0)
        self.assertFalse(rows(self.db_path, "SELECT * FROM v2_stories"))


if __name__ == "__main__":
    unittest.main()
