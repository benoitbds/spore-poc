"""S11-B.2 — contexte de journalisation : run_id, hypothesis_id, node.

Couvre, sans appel LLM réel :

* ``merge_contextvars`` est bien en tête des processors — sans quoi tout le
  reste est décoratif ;
* les douze nœuds du post-fire sont décorés, lient leur sujet à l'entrée et le
  délient à la sortie, y compris quand le nœud lève ;
* un événement émis dans le fan-out des reviewers porte ``run_id`` et
  ``hypothesis_id`` : les tâches d'``asyncio.gather`` héritent du contexte ;
* la mesure par appel (B.1) reprend ces deux champs dans ``llm_calls``.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s11_log_context
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import graph.post_fire_pipeline as post_fire  # noqa: E402
from agents.multi_reviewer_panel import run_panel  # noqa: E402
from logging_config import get_logger, log_context, setup_logging  # noqa: E402
from tests.test_s11_llm_contract import (  # noqa: E402
    PROMPT,
    PROTOCOL,
    REVIEW,
    SHARPENED,
    ScriptedClient,
    TempDatabase,
    response,
)

NODES = (
    "node_persist_grounding_kill",
    "node_persist_panel_reject",
    "node_skip_grounding",
    "node_literature_grounding",
    "node_hypothesis_sharpening",
    "node_experimental_protocol",
    "node_multi_reviewer_panel",
    "node_normalize_panel_language",
    "node_vulgarization",
    "node_translation_hook",
    "node_research_brief",
    "node_validate_brief",
)


def captured() -> Any:
    """Capture les événements en gardant la fusion du contexte.

    ``capture_logs`` désactive les processors configurés ; sans réinjecter
    ``merge_contextvars``, le test vérifierait le contraire de ce qu'il croit.

    Returns:
        Gestionnaire de contexte rendant la liste des événements capturés.
    """
    return structlog.testing.capture_logs(
        processors=[structlog.contextvars.merge_contextvars]
    )


class ProcessorOrderTests(unittest.TestCase):
    """La fusion du contexte doit précéder tout le reste."""

    def test_merge_contextvars_comes_first(self) -> None:
        setup_logging()
        processors = structlog.get_config()["processors"]
        self.assertIs(processors[0], structlog.contextvars.merge_contextvars)


class NodeDecorationTests(unittest.IsolatedAsyncioTestCase):
    """Chaque nœud lie son sujet, et le rend."""

    def test_every_node_is_decorated(self) -> None:
        for name in NODES:
            node = getattr(post_fire, name)
            self.assertTrue(hasattr(node, "__wrapped__"), name)

    async def test_node_binds_its_subject_and_unbinds_on_exit(self) -> None:
        logger = get_logger("test")

        @post_fire.logged_node("experimental_protocol")
        async def node(state: dict[str, Any]) -> dict[str, Any]:
            logger.info("probe")
            return state

        with captured() as events:
            await node({"run_id": "run-1", "hypothesis_id": "SPORE-2026-09-16-abcd"})

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["node"], "experimental_protocol")
        self.assertEqual(events[0]["run_id"], "run-1")
        self.assertEqual(events[0]["hypothesis_id"], "SPORE-2026-09-16-abcd")
        self.assertEqual(structlog.contextvars.get_contextvars(), {})

    async def test_context_is_released_even_when_the_node_raises(self) -> None:
        @post_fire.logged_node("experimental_protocol")
        async def node(state: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("protocole illisible")

        with self.assertRaises(RuntimeError):
            await node({"run_id": "run-2", "hypothesis_id": "SPORE-x"})
        self.assertEqual(structlog.contextvars.get_contextvars(), {})

    async def test_missing_identity_binds_nothing_rather_than_null(self) -> None:
        logger = get_logger("test")

        @post_fire.logged_node("vulgarization")
        async def node(state: dict[str, Any]) -> dict[str, Any]:
            logger.info("probe")
            return state

        with captured() as events:
            await node({})

        self.assertEqual(events[0]["node"], "vulgarization")
        self.assertNotIn("run_id", events[0])
        self.assertNotIn("hypothesis_id", events[0])


class FanOutTests(unittest.IsolatedAsyncioTestCase):
    """Le fan-out des reviewers hérite du contexte de son nœud."""

    async def test_events_emitted_inside_the_panel_carry_the_subject(self) -> None:
        probe = get_logger("reviewer_probe")

        async def fake_complete_json(client: Any, messages: Any, **kwargs: Any) -> Any:
            # Émis depuis la tâche du reviewer, pas depuis le nœud.
            probe.info("reviewer_probe", reviewer=kwargs["node"])
            return dict(REVIEW), response()

        @post_fire.logged_node("multi_reviewer_panel")
        async def node(state: dict[str, Any]) -> Any:
            return await run_panel(SHARPENED, PROTOCOL, [], [], {})

        with mock.patch("agents.multi_reviewer_panel.get_llm_client"), mock.patch(
            "agents.multi_reviewer_panel.complete_json", fake_complete_json
        ), mock.patch("agents.multi_reviewer_panel.load_prompt", return_value="{title}"):
            with captured() as events:
                reviews = await node(
                    {"run_id": "run-3", "hypothesis_id": "SPORE-2026-09-16-28b15004"}
                )

        self.assertEqual(len(reviews), 5)
        probes = [event for event in events if event["event"] == "reviewer_probe"]
        self.assertEqual(len(probes), 5)
        for event in probes:
            self.assertEqual(event["run_id"], "run-3")
            self.assertEqual(event["hypothesis_id"], "SPORE-2026-09-16-28b15004")
            self.assertEqual(event["node"], "multi_reviewer_panel")


class TelemetryContextTests(TempDatabase):
    """``llm_calls`` reprend le sujet lié par le nœud."""

    async def test_row_carries_run_and_hypothesis(self) -> None:
        client = ScriptedClient([response()])
        with log_context(run_id="run-4", hypothesis_id="SPORE-2026-09-16-8bc4e466"):
            await client.complete(PROMPT, max_tokens=2000, node="meta_reviewer")

        rows = self.llm_calls()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "run-4")
        self.assertEqual(rows[0]["hypothesis_id"], "SPORE-2026-09-16-8bc4e466")

    async def test_row_without_context_keeps_null(self) -> None:
        client = ScriptedClient([response()])
        await client.complete(PROMPT, max_tokens=2000, node="meta_reviewer")
        rows = self.llm_calls()
        self.assertIsNone(rows[0]["run_id"])
        self.assertIsNone(rows[0]["hypothesis_id"])


class PipelinePropagationTests(unittest.TestCase):
    """Les appelants du post-fire transmettent l'identité."""

    def test_callers_pass_run_and_hypothesis_ids(self) -> None:
        pipeline = Path("graph/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("run_id=state.get(\"run_id\")", pipeline)
        self.assertIn("hypothesis_id=hypothesis.id", pipeline)

        runner = Path("api/custom_runner.py").read_text(encoding="utf-8")
        self.assertIn("hypothesis_id=hypothesis.id", runner)

    def test_initial_state_carries_the_identity(self) -> None:
        source = Path("graph/post_fire_pipeline.py").read_text(encoding="utf-8")
        self.assertIn('"run_id": run_id,', source)
        self.assertIn('"hypothesis_id": hypothesis_id,', source)


if __name__ == "__main__":
    # json est importé pour les fixtures partagées ; référence explicite pour
    # que le linter ne le retire pas.
    assert json is not None
    unittest.main(verbosity=2)
