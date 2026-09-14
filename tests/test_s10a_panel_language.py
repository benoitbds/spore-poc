"""S10-A — normalisation de la langue du panel, lexique FR du gate, digest.

Couvre, sans appel LLM réel :

* ``node_normalize_panel_language`` : un panel anglais ressort français au
  sens de ``check_panel_language`` ; un second passage ne traduit rien ; un
  panel mixte ne voit traduites que les cartes signalées ; une traduction qui
  lève, ou qui casserait la cohérence, laisse l'état inchangé.
* ``graph/panel_coherence.py`` : le panel normalisé reste cohérent, et une
  carte négative française sans marqueur de réserve est toujours retenue.
* ``scripts/daily_pipeline_digest.py`` : le motif de blocage d'un brief resté
  en 'pending' est rendu, sans doublon avec l'alerte du jour.

Le traducteur EN→FR réel (``scripts.translate_brief_panel.translate_panel_to_fr``)
est exercé de bout en bout ; seul le client LLM est remplacé par un double qui
rend du français et compte ses appels.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s10a_panel_language
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import graph.post_fire_pipeline as post_fire  # noqa: E402
from graph.lang_guard import check_panel_language, detect  # noqa: E402
from graph.panel_coherence import check_panel  # noqa: E402
from llm.client import LLMResponse  # noqa: E402
from scripts import daily_pipeline_digest as digest  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────

# Phrase française de remplacement : porte des marqueurs de réserve du
# lexique FR (« probablement », « fragile », « insuffisante ») pour que les
# cartes négatives restent cohérentes une fois traduites.
FRENCH_WITH_RESERVE = (
    "Le postulat de départ est probablement fragile et la taille de "
    "l'échantillon est insuffisante pour conclure sur l'effet attendu."
)
# Même registre, sans aucun marqueur de réserve : sert à provoquer une
# régression de cohérence.
FRENCH_WITHOUT_RESERVE = (
    "Le protocole est clair et les phases sont bien décrites dans le "
    "document, avec des critères de passage pour chaque étape."
)
ENGLISH_ECHO = (
    "The protocol is described in the document and it is clear that the "
    "phases are defined with criteria for each of the steps."
)


def _english_card(persona: str, score: float, verdict: str) -> dict[str, Any]:
    """Carte reviewer rédigée en anglais."""
    weaknesses = [
        "The addressable market is likely too narrow for a product, and the "
        "assumption of adoption is not supported by the data.",
        "The sample size is likely too small for the effect that is expected.",
    ]
    if persona == "contrarian":
        weaknesses = [
            "FAIL REASON #1: The effect is likely too small to be detected with "
            "the sample that is proposed in the protocol.",
            "FAIL REASON #2 — The assumption that the models are correlated is "
            "not supported by the evidence.",
        ]
    return {
        "reviewer_persona": persona,
        "overall_score": score,
        "verdict": verdict,
        "strengths": [
            "The hypothesis is clear and testable, with a phased protocol that "
            "is well suited to the question.",
            "The predictions are quantitative and they can be falsified.",
        ],
        "weaknesses": weaknesses,
        "critical_questions": [
            "How will the authors control for the confounders that are listed?",
        ],
        "recommendation": (
            "The panel should fund Phase 1 only, and the team must show that "
            "the effect is detectable before the next phase."
        ),
        "confidence": 0.8,
    }


def _french_card(persona: str, score: float, verdict: str) -> dict[str, Any]:
    """Carte reviewer rédigée en français."""
    return {
        "reviewer_persona": persona,
        "overall_score": score,
        "verdict": verdict,
        "strengths": [
            "L'hypothèse est claire et testable, avec un protocole en phases qui "
            "est adapté à la question posée.",
        ],
        "weaknesses": [
            "La taille d'échantillon est probablement insuffisante pour détecter "
            "l'effet attendu dans les conditions du protocole.",
        ],
        "critical_questions": [
            "Comment les auteurs vont-ils contrôler les facteurs de confusion ?",
        ],
        "recommendation": (
            "Le panel recommande de financer la Phase 1 et de vérifier que "
            "l'effet est mesurable avant de poursuivre."
        ),
        "confidence": 0.8,
    }


def _english_meta() -> dict[str, Any]:
    """Meta-review rédigée en anglais."""
    return {
        "consensus_score": 6.3,
        "verdict": "publish_brief",
        "key_consensus": [
            "The reviewers agree that the hypothesis is testable and that the "
            "protocol is well structured.",
        ],
        "key_disagreements": [
            "The contrarian doubts that the effect is large enough to be seen.",
        ],
        "critical_path": "The success depends on whether the effect can be detected in Phase 1.",
        "final_recommendation": (
            "The panel recommends that the brief is published, with the caveat "
            "that the effect size must be confirmed first."
        ),
        "brief_quality_gate": True,
        "llm_verdict": "publish_brief",
        "llm_consensus_score": 6.3,
    }


def _french_meta() -> dict[str, Any]:
    """Meta-review rédigée en français."""
    return {
        "consensus_score": 6.3,
        "verdict": "publish_brief",
        "key_consensus": [
            "Les relecteurs s'accordent sur le fait que l'hypothèse est testable "
            "et que le protocole est bien structuré.",
        ],
        "key_disagreements": [
            "Le contradicteur doute que l'effet soit assez grand pour être vu.",
        ],
        "critical_path": "Le succès dépend de la détection de l'effet dans la Phase 1.",
        "final_recommendation": (
            "Le panel recommande la publication du brief, sous réserve que la "
            "taille d'effet soit d'abord confirmée."
        ),
        "brief_quality_gate": True,
        "llm_verdict": "publish_brief",
        "llm_consensus_score": 6.3,
    }


PERSONAS: list[tuple[str, float, str]] = [
    ("methodologist", 6.5, "weak_accept"),
    ("domain_expert", 7.0, "accept"),
    ("contrarian", 3.5, "weak_reject"),
    ("industrialist", 4.5, "weak_reject"),
    ("funding_strategist", 7.0, "accept"),
]


def english_panel() -> dict[str, Any]:
    """Panel entièrement anglais, meta-review comprise."""
    return {
        "reviews": [_english_card(*p) for p in PERSONAS],
        "meta_review": _english_meta(),
    }


def mixed_panel() -> dict[str, Any]:
    """Panel mixte : contrarian et industrialist anglais, le reste français."""
    english = {"contrarian", "industrialist"}
    return {
        "reviews": [
            _english_card(*p) if p[0] in english else _french_card(*p)
            for p in PERSONAS
        ],
        "meta_review": _french_meta(),
    }


class FakeTranslationClient:
    """Double du client LLM : rend du français, compte ses appels.

    Il répond au format des prompts EN→FR : autant d'éléments séparés par
    ``---`` que le prompt de liste en annonce, une phrase sinon.
    """

    _LIST_COUNT_RE = re.compile(r"TEXTE SOURCE \((\d+) éléments")

    def __init__(
        self,
        sentence: str = FRENCH_WITH_RESERVE,
        error: Exception | None = None,
    ) -> None:
        self.sentence = sentence
        self.error = error
        self.calls: list[str] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2500,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Rend la traduction simulée du prompt reçu."""
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        match = self._LIST_COUNT_RE.search(prompt)
        count = int(match.group(1)) if match else 1
        content = "\n---\n".join([self.sentence] * count)
        return LLMResponse(
            content=content,
            input_tokens=100,
            output_tokens=50,
            model="deepseek-v4-flash",
            provider="deepseek",
        )


def _patched_client(client: FakeTranslationClient) -> Callable[..., Any]:
    """Remplace la fabrique de client du traducteur par le double."""
    return mock.patch(
        "scripts.translate_brief_panel.get_llm_client",
        return_value=client,
    )


# ── node_normalize_panel_language ───────────────────────────────────────


class NormalizePanelLanguageTests(unittest.IsolatedAsyncioTestCase):
    """Comportement du nœud de normalisation."""

    async def test_english_panel_comes_out_french(self) -> None:
        panel = english_panel()
        self.assertEqual(len(check_panel_language(panel, "fr")), 5)
        client = FakeTranslationClient()

        with _patched_client(client):
            state = await post_fire.node_normalize_panel_language(
                {"panel": panel, "hypothesis": "test", "revision_count": 1}
            )

        normalized = state["panel"]
        self.assertEqual(check_panel_language(normalized, "fr"), [])
        self.assertEqual(
            detect(post_fire._meta_review_texts(normalized["meta_review"])), "fr"
        )
        self.assertGreater(len(client.calls), 0)

        # Tokens techniques recopiés verbatim.
        for before, after in zip(panel["reviews"], normalized["reviews"]):
            for key in ("reviewer_persona", "overall_score", "verdict", "confidence"):
                self.assertEqual(before[key], after[key])
        for key in ("consensus_score", "verdict", "llm_verdict", "brief_quality_gate"):
            self.assertEqual(panel["meta_review"][key], normalized["meta_review"][key])

        # FAIL REASON recollé verbatim, séparateur d'origine compris.
        contrarian = normalized["reviews"][2]["weaknesses"]
        self.assertTrue(contrarian[0].startswith("FAIL REASON #1: "))
        self.assertTrue(contrarian[1].startswith("FAIL REASON #2 — "))

        # Décision A : le panel normalisé reste cohérent.
        self.assertEqual(check_panel(normalized), [])

        # L'état d'entrée n'est pas muté.
        self.assertEqual(panel, english_panel())

    async def test_second_pass_makes_no_translation_call(self) -> None:
        first_client = FakeTranslationClient()
        with _patched_client(first_client):
            state = await post_fire.node_normalize_panel_language(
                {"panel": english_panel(), "hypothesis": "test"}
            )
        once = copy.deepcopy(state["panel"])

        second_client = FakeTranslationClient()
        translator = mock.AsyncMock(wraps=post_fire.translate_panel_data_to_fr)
        with _patched_client(second_client) as factory, mock.patch.object(
            post_fire, "translate_panel_data_to_fr", translator
        ):
            again = await post_fire.node_normalize_panel_language(state)

        translator.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(second_client.calls, [])
        self.assertEqual(again["panel"], once)

    async def test_mixed_panel_translates_only_flagged_cards(self) -> None:
        panel = mixed_panel()
        flagged = {p["reviewer"] for p in check_panel_language(panel, "fr")}
        self.assertEqual(flagged, {"contrarian", "industrialist"})
        client = FakeTranslationClient()

        with _patched_client(client):
            state = await post_fire.node_normalize_panel_language({"panel": panel})

        normalized = state["panel"]
        self.assertEqual(check_panel_language(normalized, "fr"), [])
        for idx, (before, after) in enumerate(zip(panel["reviews"], normalized["reviews"])):
            if before["reviewer_persona"] in flagged:
                self.assertNotEqual(before, after, f"carte {idx} non traduite")
            else:
                self.assertEqual(before, after, f"carte {idx} française modifiée")
        # Meta-review française : jamais envoyée au traducteur.
        self.assertEqual(normalized["meta_review"], panel["meta_review"])
        # 2 cartes × (3 listes + 1 chaîne) = 8 appels, rien de plus.
        self.assertEqual(len(client.calls), 8)
        self.assertFalse(any("Le contradicteur doute" in c for c in client.calls))

    async def test_translation_error_leaves_state_unchanged(self) -> None:
        panel = english_panel()
        state_in = {"panel": panel, "hypothesis": "test", "revision_count": 1}
        client = FakeTranslationClient(error=RuntimeError("provider down"))

        with _patched_client(client):
            state_out = await post_fire.node_normalize_panel_language(state_in)

        self.assertEqual(state_out, state_in)
        self.assertEqual(state_out["panel"], english_panel())
        self.assertEqual(len(client.calls), 1)

    async def test_output_still_english_leaves_state_unchanged(self) -> None:
        panel = english_panel()
        client = FakeTranslationClient(sentence=ENGLISH_ECHO)

        with _patched_client(client):
            state_out = await post_fire.node_normalize_panel_language({"panel": panel})

        # EnglishInOutputError levée par le validateur, absorbée par le nœud.
        self.assertEqual(state_out["panel"], english_panel())

    async def test_coherence_regression_leaves_state_unchanged(self) -> None:
        panel = english_panel()
        self.assertEqual(check_panel(panel), [])
        client = FakeTranslationClient(sentence=FRENCH_WITHOUT_RESERVE)

        with _patched_client(client):
            state_out = await post_fire.node_normalize_panel_language({"panel": panel})

        # L'industrialist négatif perdrait sa réserve affichée : refusé.
        self.assertEqual(state_out["panel"], english_panel())

    async def test_french_panel_is_a_passthrough(self) -> None:
        panel = {
            "reviews": [_french_card(*p) for p in PERSONAS],
            "meta_review": _french_meta(),
        }
        with mock.patch("scripts.translate_brief_panel.get_llm_client") as factory:
            state_out = await post_fire.node_normalize_panel_language({"panel": panel})
        factory.assert_not_called()
        self.assertIs(state_out["panel"], panel)

    def test_graph_routes_publish_through_normalization(self) -> None:
        graph = post_fire.create_post_fire_pipeline().compile().get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        self.assertIn(("multi_reviewer_panel", "normalize_panel_language"), edges)
        self.assertIn(("normalize_panel_language", "research_brief_generator"), edges)
        self.assertNotIn(("multi_reviewer_panel", "research_brief_generator"), edges)


# ── Lexique FR du gate de cohérence (décision A) ────────────────────────


class FrenchReserveLexiconTests(unittest.TestCase):
    """La voie de repli lexicale du gate fonctionne sur des cartes françaises."""

    def test_negative_french_card_without_any_marker_is_still_caught(self) -> None:
        card = _french_card("industrialist", 4.0, "weak_reject")
        card["weaknesses"] = [
            "Le marché visé correspond à des laboratoires de recherche publics "
            "qui achètent peu d'instruments de ce type chaque année."
        ]
        problems = check_panel({"reviews": [card]})
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["reviewer"], "industrialist")

    def test_negative_french_card_with_french_marker_passes(self) -> None:
        for marker_sentence in (
            "L'effet est probablement trop petit pour être mesuré.",
            "Aucun client industriel ne paiera pour ce modèle.",
            "Le postulat de départ est erroné.",
            "Le calendrier de commercialisation est incertain.",
        ):
            with self.subTest(sentence=marker_sentence):
                card = _french_card("industrialist", 4.0, "weak_reject")
                card["weaknesses"] = [marker_sentence]
                self.assertEqual(check_panel({"reviews": [card]}), [])

    def test_fail_reason_marker_still_passes(self) -> None:
        card = _french_card("contrarian", 3.5, "weak_reject")
        card["weaknesses"] = ["FAIL REASON #1: La chaîne causale repose sur un maillon non testé."]
        self.assertEqual(check_panel({"reviews": [card]}), [])


# ── Digest : motif de blocage ───────────────────────────────────────────


class DigestBlockMotifTests(unittest.TestCase):
    """Le digest nomme le motif d'un brief resté en 'pending'."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.log_path = root / "spore.log"
        self.db_path = root / "spore.db"

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE briefs (id TEXT PRIMARY KEY, status TEXT, created_at TEXT)"
        )
        old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        conn.executemany(
            "INSERT INTO briefs VALUES (?, ?, ?)",
            [
                ("SPR-TEST-LANG", "pending", old),
                ("SPR-TEST-COHE", "pending", old),
                ("SPR-TEST-NONE", "pending", old),
                ("SPR-TEST-DONE", "complete", old),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_log(self, events: list[dict[str, Any]]) -> None:
        lines = ["non-JSON line, skipped by the parser"]
        lines += [json.dumps(e) for e in events]
        self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _render(self, anchor: str | None = None) -> str:
        data = digest.collect(self.log_path, anchor)
        stale = digest.stale_pending_briefs(self.db_path)
        drifts = digest.drifting_counters(data["counters"], data["population"])
        _subject, body = digest.format_digest(data, stale, drifts)
        return body

    def test_pending_brief_shows_blocking_motif_from_an_earlier_day(self) -> None:
        self._write_log([
            {
                "event": "brief_panel_incoherent", "level": "error",
                "timestamp": "2026-09-10T04:40:00Z", "brief_id": "SPR-TEST-LANG",
                "problems": [{"reviewer": "industrialist", "reason": "no reserve"}],
            },
            {
                "event": "brief_panel_language_mismatch", "level": "error",
                "timestamp": "2026-09-11T04:45:00Z", "brief_id": "SPR-TEST-LANG",
                "cards": [
                    {"reviewer": "contrarian", "expected": "fr", "detected": "en", "excerpt": "The"},
                    {"reviewer": "domain_expert", "expected": "fr", "detected": "en", "excerpt": "The"},
                ],
            },
            {
                "event": "brief_panel_incoherent", "level": "error",
                "timestamp": "2026-09-11T04:50:00Z", "brief_id": "SPR-TEST-COHE",
                "problems": [{
                    "reviewer": "industrialist", "score": 4.0, "verdict": "weak_reject",
                    "reason": "score=4.0 verdict=weak_reject but displayed comment carries no reserve marker",
                }],
            },
            {"event": "running_reviewer", "level": "info", "timestamp": "2026-09-14T04:30:00Z"},
        ])

        body = self._render()

        lang_entry = body.split("• SPR-TEST-LANG", 1)[1].split("•", 1)[0]
        self.assertIn(digest.ALERT_EVENTS["brief_panel_language_mismatch"], lang_entry)
        self.assertIn("le 2026-09-11", lang_entry)
        self.assertIn("contrarian (en), domain_expert (en)", lang_entry)

        cohe_entry = body.split("• SPR-TEST-COHE", 1)[1].split("•", 1)[0]
        self.assertIn(digest.ALERT_EVENTS["brief_panel_incoherent"], cohe_entry)
        self.assertIn("industrialist : score=4.0", cohe_entry)

        none_entry = body.split("• SPR-TEST-NONE", 1)[1]
        self.assertIn("motif : inconnu", none_entry.split("•", 1)[0])

        self.assertNotIn("SPR-TEST-DONE", body)

    def test_same_day_alert_and_stale_entry_are_not_duplicated(self) -> None:
        self._write_log([
            {
                "event": "brief_panel_language_mismatch", "level": "error",
                "timestamp": "2026-09-14T04:45:00Z", "brief_id": "SPR-TEST-LANG",
                "cards": [{"reviewer": "contrarian", "expected": "fr", "detected": "en"}],
            },
            {
                "event": "vulgarization_failed", "level": "error",
                "timestamp": "2026-09-14T04:44:00Z", "brief_id": "SPR-TEST-LANG",
                "error": "timeout",
            },
        ])

        data = digest.collect(self.log_path, None)
        stale = digest.stale_pending_briefs(self.db_path)
        subject, body = digest.format_digest(data, stale, [])

        self.assertEqual(body.count("SPR-TEST-LANG"), 2)  # ligne pending + vulgarisation
        self.assertEqual(body.count(digest.ALERT_EVENTS["brief_panel_language_mismatch"]), 1)
        self.assertIn(digest.ALERT_EVENTS["vulgarization_failed"], body)
        # 1 alerte restante (vulgarisation) + 3 briefs pending.
        self.assertTrue(subject.startswith("[SPORE] 4 chose(s)"))

    def test_alert_line_details_cards_and_problems(self) -> None:
        cards_line = digest._evt_line({
            "event": "brief_panel_language_mismatch", "brief_id": "SPR-X",
            "cards": [{"reviewer": "contrarian", "detected": "en"}],
        })
        self.assertIn("détail : cartes détectées hors langue : contrarian (en)", cards_line)
        problems_line = digest._evt_line({
            "event": "brief_panel_incoherent", "brief_id": "SPR-X",
            "problems": [{"reviewer": "industrialist", "reason": "no reserve"}],
        })
        self.assertIn("détail : industrialist : no reserve", problems_line)

    def test_both_panel_checks_are_alert_events(self) -> None:
        self.assertIn("brief_panel_language_mismatch", digest.ALERT_EVENTS)
        self.assertIn("brief_panel_incoherent", digest.ALERT_EVENTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
