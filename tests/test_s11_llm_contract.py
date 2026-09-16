"""S11-B.1 — fin de génération, mesure par appel, suppression des replis.

Couvre, sans appel LLM réel ni écriture dans la base de production :

* les deux clients renseignent ``finish_reason``, le modèle **servi** et
  l'empreinte de configuration ; ``requested_model`` garde le nom demandé,
  clé de tarification du tracker ;
* ``client.complete`` refuse toute fin autre que ``stop``, y compris quand le
  contenu serait parsable, et écrit la ligne ``llm_calls`` **avant** de lever ;
* la mesure ne bloque jamais : une écriture impossible produit un warning et
  l'appel rend sa réponse ;
* ``complete_json`` rejoue une fois à plafond doublé, borné par
  ``MAX_TOKENS_CEILING``, et laisse passer une seconde troncature ;
* ``FallbackClient`` ne rejoue ni ne bascule sur une troncature — il le fait
  pour une panne transitoire ;
* le panel n'a plus de repli : un reviewer ou une meta-review en échec lève au
  lieu de produire une carte à confidence 0.0 ou une synthèse « parse_failed » ;
* une traduction terminée en ``length`` lève et n'écrit rien.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s11_llm_contract
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from agents.multi_reviewer_panel import (  # noqa: E402
    MetaReviewFailed,
    PanelReviewFailed,
    run_meta_reviewer,
    run_panel,
)
from llm.client import (  # noqa: E402
    AnthropicClient,
    DeepSeekClient,
    FallbackClient,
    LLMClient,
    LLMResponse,
    normalize_anthropic_stop_reason,
)
from llm.errors import (  # noqa: E402
    LLMOutputIncomplete,
    LLMOutputTruncated,
    LLMResourceExhausted,
)
from llm.json_parse import complete_json  # noqa: E402
from llm.limits import MAX_TOKENS_CEILING  # noqa: E402
from storage import init_database  # noqa: E402
from tests.test_s10a_panel_language import (  # noqa: E402
    FakeTranslationClient,
    _patched_client,
    english_panel,
)

PROMPT = [{"role": "user", "content": "Rends un objet json."}]


class ScriptedClient(LLMClient):
    """Client scripté : rend, dans l'ordre, les réponses ou erreurs fournies.

    Enregistre les paramètres de chaque appel pour que les tests vérifient le
    plafond demandé, la tentative et le nœud, sans dépendre d'un fournisseur.
    """

    provider = "scripted"

    def __init__(self, scripted: list[Any]) -> None:
        """Construit le client.

        Args:
            scripted: Réponses (``LLMResponse``) ou exceptions à rendre, dans
                l'ordre des appels.
        """
        self.scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    async def _complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
        json_mode: bool,
    ) -> LLMResponse:
        """Rend l'élément scripté suivant."""
        self.calls.append({"max_tokens": max_tokens, "temperature": temperature})
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(
    *,
    content: str = '{"ok": true}',
    finish_reason: str = "stop",
    output_tokens: int = 100,
    model: str = "deepseek-v4-flash-2026-09",
) -> LLMResponse:
    """Construit une réponse de test.

    Args:
        content: Texte produit.
        finish_reason: Motif de fin de génération.
        output_tokens: Jetons produits.
        model: Modèle servi, tel que renvoyé par l'API.

    Returns:
        LLMResponse renseignée comme le ferait un client réel.
    """
    return LLMResponse(
        content=content,
        input_tokens=42,
        output_tokens=output_tokens,
        model=model,
        provider="scripted",
        finish_reason=finish_reason,
        requested_model="deepseek-v4-flash",
        system_fingerprint="fp_test",
    )


class TempDatabase(unittest.IsolatedAsyncioTestCase):
    """Base temporaire, settings redirigés."""

    initialize = True

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "spore.db"
        self._env = mock.patch.dict(
            os.environ,
            {"SPORE_DB_PATH": str(self.db_path), "SPORE_OUTPUT_DIR": str(root / "outputs")},
        )
        self._env.start()
        self._saved_settings = config._settings
        config._settings = None
        if self.initialize:
            await init_database()

    async def asyncTearDown(self) -> None:
        config._settings = self._saved_settings
        self._env.stop()
        self._tmp.cleanup()

    def llm_calls(self) -> list[sqlite3.Row]:
        """Lit la table de mesure.

        Returns:
            Toutes les lignes de ``llm_calls``, les plus anciennes d'abord.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
        finally:
            conn.close()
        return rows


# ── Clients : ce que l'API dit vraiment ─────────────────────────────────


class ProviderFieldsTests(unittest.IsolatedAsyncioTestCase):
    """Champs renseignés par chaque client."""

    async def test_deepseek_reports_finish_reason_served_model_and_fingerprint(self) -> None:
        client = DeepSeekClient(api_key="test-key")
        api_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok": true}'),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=11, completion_tokens=8000, prompt_cache_hit_tokens=11
            ),
            model="deepseek-v4-flash-2026-09-01",
            system_fingerprint="fp_2026_09",
        )

        async def fake_create(**kwargs: Any) -> Any:
            return api_response

        with mock.patch.object(client.client.chat.completions, "create", fake_create):
            result = await client._complete(PROMPT, 8000, 0.4, None, True)

        self.assertEqual(result.finish_reason, "length")
        # Le modèle servi, pas celui demandé : c'est tout l'objet de B.1.
        self.assertEqual(result.model, "deepseek-v4-flash-2026-09-01")
        self.assertEqual(result.requested_model, "deepseek-v4-flash")
        self.assertEqual(result.system_fingerprint, "fp_2026_09")
        self.assertTrue(result.cache_hit)

    async def test_deepseek_missing_finish_reason_is_unknown(self) -> None:
        client = DeepSeekClient(api_key="test-key")
        api_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason=None)],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            model="deepseek-v4-flash",
        )

        async def fake_create(**kwargs: Any) -> Any:
            return api_response

        with mock.patch.object(client.client.chat.completions, "create", fake_create):
            result = await client._complete(PROMPT, 100, 0.4, None, True)

        self.assertEqual(result.finish_reason, "unknown")
        self.assertIsNone(result.system_fingerprint)

    async def test_anthropic_normalises_stop_reason(self) -> None:
        self.assertEqual(normalize_anthropic_stop_reason("end_turn"), "stop")
        self.assertEqual(normalize_anthropic_stop_reason("stop_sequence"), "stop")
        self.assertEqual(normalize_anthropic_stop_reason("max_tokens"), "length")
        # Valeur inconnue conservée : elle sera refusée, pas réinterprétée.
        self.assertEqual(normalize_anthropic_stop_reason("refusal"), "refusal")
        self.assertEqual(normalize_anthropic_stop_reason(None), "unknown")

        client = AnthropicClient(api_key="test-key", model="claude-sonnet-5")
        api_response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="bonjour")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=7),
            model="claude-sonnet-5-20260210",
            stop_reason="max_tokens",
        )

        async def fake_create(**kwargs: Any) -> Any:
            return api_response

        with mock.patch.object(client.client.messages, "create", fake_create):
            result = await client._complete(PROMPT, 1000, 0.7, None, False)

        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(result.model, "claude-sonnet-5-20260210")
        self.assertEqual(result.requested_model, "claude-sonnet-5")


# ── Contrôle dans la couche client ──────────────────────────────────────


class FinishReasonGateTests(TempDatabase):
    """Aucune réponse non terminée ne ressort de ``complete``."""

    async def test_stop_passes_through(self) -> None:
        client = ScriptedClient([response()])
        result = await client.complete(PROMPT, max_tokens=500, node="synthesis")
        self.assertEqual(result.content, '{"ok": true}')

    async def test_length_raises_even_when_content_parses(self) -> None:
        # Contenu parsable, génération coupée : c'est le cas que le contrôle
        # avant parsing existe pour attraper.
        client = ScriptedClient([response(finish_reason="length", output_tokens=500)])
        with self.assertRaises(LLMOutputTruncated) as caught:
            await client.complete(PROMPT, max_tokens=500, node="experimental_protocol")
        error = caught.exception
        self.assertEqual(error.node, "experimental_protocol")
        self.assertEqual(error.max_tokens, 500)
        self.assertEqual(error.output_tokens, 500)
        self.assertEqual(error.attempt, 1)

    async def test_resource_exhausted_and_unknown_reasons_raise_distinctly(self) -> None:
        exhausted = ScriptedClient([response(finish_reason="insufficient_system_resource")])
        with self.assertRaises(LLMResourceExhausted):
            await exhausted.complete(PROMPT, max_tokens=500, node="gate")

        filtered = ScriptedClient([response(finish_reason="content_filter")])
        with self.assertRaises(LLMOutputIncomplete):
            await filtered.complete(PROMPT, max_tokens=500, node="gate")

        silent = ScriptedClient([response(finish_reason="unknown")])
        with self.assertRaises(LLMOutputIncomplete):
            await silent.complete(PROMPT, max_tokens=500, node="gate")

    async def test_node_is_mandatory(self) -> None:
        client = ScriptedClient([response()])
        with self.assertRaises(TypeError):
            await client.complete(PROMPT, max_tokens=500)  # type: ignore[call-arg]


# ── Mesure ──────────────────────────────────────────────────────────────


class TelemetryTests(TempDatabase):
    """Une ligne par appel, y compris pour les appels refusés."""

    async def test_row_written_for_a_normal_call(self) -> None:
        client = ScriptedClient([response()])
        await client.complete(PROMPT, max_tokens=1234, node="vulgarization", attempt=2)

        rows = self.llm_calls()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["node"], "vulgarization")
        self.assertEqual(row["provider"], "scripted")
        self.assertEqual(row["model"], "deepseek-v4-flash")
        self.assertEqual(row["response_model"], "deepseek-v4-flash-2026-09")
        self.assertEqual(row["system_fingerprint"], "fp_test")
        self.assertEqual(row["attempt"], 2)
        self.assertEqual(row["max_tokens"], 1234)
        self.assertEqual(row["finish_reason"], "stop")
        self.assertEqual(row["output_tokens"], 100)

    async def test_truncated_call_is_recorded_before_raising(self) -> None:
        client = ScriptedClient([response(finish_reason="length")])
        with self.assertRaises(LLMOutputTruncated):
            await client.complete(PROMPT, max_tokens=8000, node="experimental_protocol")

        rows = self.llm_calls()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["finish_reason"], "length")
        self.assertEqual(rows[0]["node"], "experimental_protocol")

    async def test_failed_measurement_never_blocks_the_call(self) -> None:
        client = ScriptedClient([response()])
        with mock.patch(
            "llm.telemetry._insert", side_effect=RuntimeError("base verrouillée")
        ):
            result = await client.complete(PROMPT, max_tokens=500, node="synthesis")
        self.assertEqual(result.content, '{"ok": true}')
        self.assertEqual(self.llm_calls(), [])


class TelemetryWithoutSchemaTests(TempDatabase):
    """Un processus qui n'a pas initialisé le schéma mesure quand même."""

    initialize = False

    async def test_table_is_created_on_first_write(self) -> None:
        client = ScriptedClient([response()])
        await client.complete(PROMPT, max_tokens=500, node="translate_panel_en_fr")
        rows = self.llm_calls()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["node"], "translate_panel_en_fr")


# ── Rejeu à plafond doublé ──────────────────────────────────────────────


class WidenOnTruncationTests(TempDatabase):
    """``complete_json`` corrige un plafond trop bas, une seule fois."""

    async def test_single_retry_at_double_the_ceiling(self) -> None:
        client = ScriptedClient(
            [response(finish_reason="length"), response(content='{"phases": []}')]
        )
        data, last = await complete_json(
            client, PROMPT, node="experimental_protocol", max_tokens=8000, temperature=0.4
        )

        self.assertEqual(data, {"phases": []})
        self.assertEqual([call["max_tokens"] for call in client.calls], [8000, 16000])
        rows = self.llm_calls()
        self.assertEqual([row["attempt"] for row in rows], [1, 2])
        self.assertEqual([row["finish_reason"] for row in rows], ["length", "stop"])
        self.assertEqual(last.finish_reason, "stop")

    async def test_second_truncation_propagates_without_repair(self) -> None:
        client = ScriptedClient(
            [response(finish_reason="length"), response(finish_reason="length")]
        )
        with self.assertRaises(LLMOutputTruncated):
            await complete_json(
                client, PROMPT, node="experimental_protocol", max_tokens=8000, temperature=0.4
            )
        self.assertEqual(len(client.calls), 2)

    async def test_widening_is_capped_by_the_ceiling(self) -> None:
        client = ScriptedClient([response(finish_reason="length"), response()])
        await complete_json(
            client, PROMPT, node="literature_grounding", max_tokens=20000, temperature=0.4
        )
        self.assertEqual(
            [call["max_tokens"] for call in client.calls], [20000, MAX_TOKENS_CEILING]
        )

    async def test_no_retry_when_already_at_the_ceiling(self) -> None:
        client = ScriptedClient([response(finish_reason="length")])
        with self.assertRaises(LLMOutputTruncated):
            await complete_json(
                client,
                PROMPT,
                node="literature_grounding",
                max_tokens=MAX_TOKENS_CEILING,
                temperature=0.4,
            )
        self.assertEqual(len(client.calls), 1)

    async def test_invalid_json_on_a_complete_output_still_retries_at_zero(self) -> None:
        # C17b inchangé : la sortie est complète, seul le format cloche.
        client = ScriptedClient([response(content="pas du json"), response()])
        data, _last = await complete_json(
            client, PROMPT, node="meta_reviewer", max_tokens=2000, temperature=0.3
        )
        self.assertEqual(data, {"ok": True})
        self.assertEqual([call["temperature"] for call in client.calls], [0.3, 0.0])
        self.assertEqual([call["max_tokens"] for call in client.calls], [2000, 2000])


# ── Client de repli ─────────────────────────────────────────────────────


class FallbackBehaviourTests(TempDatabase):
    """Une troncature ne se rejoue pas, et ne bascule pas au tarif Sonnet."""

    async def test_truncation_is_not_retried_nor_handed_to_the_secondary(self) -> None:
        primary = ScriptedClient([response(finish_reason="length")])
        secondary = ScriptedClient([response()])
        client = FallbackClient(primary, secondary, max_retries=3, base_delay=0.0)

        with self.assertRaises(LLMOutputTruncated):
            await client.complete(PROMPT, max_tokens=4000, node="synthesis")

        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(secondary.calls, [])

    async def test_incomplete_output_is_not_retried_either(self) -> None:
        primary = ScriptedClient([response(finish_reason="content_filter")])
        secondary = ScriptedClient([response()])
        client = FallbackClient(primary, secondary, max_retries=3, base_delay=0.0)

        with self.assertRaises(LLMOutputIncomplete):
            await client.complete(PROMPT, max_tokens=4000, node="synthesis")
        self.assertEqual(secondary.calls, [])

    async def test_transient_failures_still_retry_then_fall_back(self) -> None:
        primary = ScriptedClient(
            [
                response(finish_reason="insufficient_system_resource"),
                RuntimeError("502"),
                response(finish_reason="insufficient_system_resource"),
            ]
        )
        secondary = ScriptedClient([response(content='{"ok": 1}')])
        client = FallbackClient(primary, secondary, max_retries=3, base_delay=0.0)

        result = await client.complete(PROMPT, max_tokens=4000, node="synthesis")

        self.assertEqual(len(primary.calls), 3)
        self.assertEqual(len(secondary.calls), 1)
        self.assertEqual(result.provider, "scripted(fallback)")


# ── Panel : plus aucun repli ────────────────────────────────────────────


SHARPENED: dict[str, Any] = {
    "title": "Titre",
    "formal_statement": "Énoncé formel",
    "independent_variables": [
        {"name": "température", "type": "continue", "range": "20-40", "unit": "°C"}
    ],
    "dependent_variables": [
        {"name": "rendement", "type": "continue", "expected_direction": "hausse", "unit": "%"}
    ],
    "falsifiable_predictions": [
        {
            "prediction": "Le rendement augmente",
            "quantitative_bound": "+15 %",
            "measurement_method": "spectrométrie",
            "null_hypothesis": "aucun effet",
        }
    ],
    "proposed_mechanism": {
        "causal_chain": ["A entraîne B"],
        "key_assumptions": ["B est mesurable"],
    },
    "theoretical_framework": "biophysique",
}
PROTOCOL: dict[str, Any] = {
    "protocol_title": "Protocole de validation",
    "phases": [],
    "overall_budget_estimate": "100 k€",
    "overall_timeline": "12 mois",
}
REVIEW: dict[str, Any] = {
    "overall_score": 6.5,
    "verdict": "weak_accept",
    "strengths": ["Point fort"],
    "weaknesses": ["Point faible"],
    "critical_questions": ["Question"],
    "recommendation": "Recommandation",
    "confidence": 0.8,
}


class PanelWithoutFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Une note sans évaluation n'entre pas dans le consensus."""

    async def test_a_failing_reviewer_stops_the_panel(self) -> None:
        async def fake_complete_json(client: Any, messages: Any, **kwargs: Any) -> Any:
            if kwargs["node"] == "reviewer_contrarian":
                raise json.JSONDecodeError("Unterminated string", "{", 0)
            return dict(REVIEW), response()

        with mock.patch("agents.multi_reviewer_panel.get_llm_client"), mock.patch(
            "agents.multi_reviewer_panel.complete_json", fake_complete_json
        ), mock.patch("agents.multi_reviewer_panel.load_prompt", return_value="{title}"):
            with self.assertRaises(PanelReviewFailed) as caught:
                await run_panel(SHARPENED, PROTOCOL, [], [], {})

        self.assertEqual(caught.exception.personas, ["contrarian"])

    async def test_a_truncated_reviewer_stops_the_panel(self) -> None:
        async def fake_complete_json(client: Any, messages: Any, **kwargs: Any) -> Any:
            if kwargs["node"] == "reviewer_methodologist":
                raise LLMOutputTruncated(
                    "tronqué",
                    node="reviewer_methodologist",
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    finish_reason="length",
                    max_tokens=2000,
                    output_tokens=2000,
                    attempt=2,
                )
            return dict(REVIEW), response()

        with mock.patch("agents.multi_reviewer_panel.get_llm_client"), mock.patch(
            "agents.multi_reviewer_panel.complete_json", fake_complete_json
        ), mock.patch("agents.multi_reviewer_panel.load_prompt", return_value="{title}"):
            # Le panel ne rattrape pas la troncature : il échoue, en gardant
            # la cause pour que B.5 sache quoi consigner.
            with self.assertRaises(PanelReviewFailed) as caught:
                await run_panel(SHARPENED, PROTOCOL, [], [], {})

        self.assertEqual(caught.exception.personas, ["methodologist"])
        self.assertIsInstance(caught.exception.cause, LLMOutputTruncated)

    async def test_a_failing_meta_review_stops_the_run(self) -> None:
        reviews = [dict(REVIEW, reviewer_persona=p) for p in ("methodologist", "contrarian")]

        async def fake_complete_json(client: Any, messages: Any, **kwargs: Any) -> Any:
            raise json.JSONDecodeError("Unterminated string", "{", 0)

        with mock.patch("agents.multi_reviewer_panel.get_llm_client"), mock.patch(
            "agents.multi_reviewer_panel.complete_json", fake_complete_json
        ), mock.patch("agents.multi_reviewer_panel.load_prompt", return_value="{reviews_json}"):
            with self.assertRaises(MetaReviewFailed) as caught:
                await run_meta_reviewer(reviews, SHARPENED, iteration=2)

        self.assertEqual(caught.exception.iteration, 2)

    async def test_no_fallback_marker_remains_in_the_module(self) -> None:
        source = Path("agents/multi_reviewer_panel.py").read_text(encoding="utf-8")
        # Les marqueurs des deux replis supprimés ne doivent plus apparaître
        # dans du code produisant une carte ou une meta-review.
        self.assertNotIn('recommendation="Manual review needed."', source)
        self.assertNotIn('"llm_verdict": "parse_failed"', source)


# ── Traduction ──────────────────────────────────────────────────────────


class TranslationGateTests(unittest.IsolatedAsyncioTestCase):
    """Un texte coupé ne devient pas une carte publiée."""

    async def test_truncated_translation_raises_and_writes_nothing(self) -> None:
        from scripts.translate_brief_panel import translate_panel_to_fr

        panel = english_panel()
        before = copy.deepcopy(panel)
        fake = FakeTranslationClient(finish_reason="length")

        with _patched_client(fake):
            with self.assertRaises(LLMOutputTruncated):
                await translate_panel_to_fr("SPR-TEST", panel)

        # Le panel source n'a pas été touché : rien à restaurer.
        self.assertEqual(panel, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
