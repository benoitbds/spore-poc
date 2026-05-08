"""Tests for agents.translation — the FR->EN helpers used by the
post-fire LangGraph translation_hook node (S7.4 Phase 4).

These tests hit the real LLM (DeepSeek primary, Anthropic fallback)
so they cost money and take ~30-60 s each. Marked
``@pytest.mark.integration`` so the default ``pytest`` run skips
them; opt in with ``-m integration`` or by passing the file path
explicitly.

    cd /home/baq/Projects/spore-poc
    .venv/bin/pytest tests/test_agents_translation.py -m integration -v

The tests assert the *shape* of the output (keys present, tokens
preserved verbatim, prose translated) rather than exact wording —
LLM output is non-deterministic at temperature > 0 and we don't want
flakes from minor phrasing variation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from agents.translation import (  # noqa: E402
    FrenchInOutputError,
    translate_panel_data,
    translate_vulgarization_data,
)


# Sample FR vulgarization payload — minimal, covers every leaf field
# the translator looks at so a single call exercises the full path.
_FR_VULG_PAYLOAD = {
    "title_fr": "Un test minimal pour vérifier la traduction",
    "hypothesis_in_brief": (
        "Cette hypothèse propose une méthode pour tester la traduction "
        "FR vers EN. L'objectif est de vérifier que la pipeline produit "
        "du texte anglais cohérent avec le registre Nature-grade."
    ),
    "why_it_matters": (
        "Sans test minimal, la traduction pourrait régresser sans qu'on "
        "le détecte. Cette hypothèse documente le comportement attendu."
    ),
    "imagine_that": (
        "Imaginez que vous lancez un sprint d'intégration. Vous avez "
        "besoin d'un harnais qui valide la chaîne de bout en bout sans "
        "prendre une heure à exécuter."
    ),
    "concretely": {
        "intro": "Le protocole de test comprend trois étapes.",
        "phase1": "Phase 1 : préparer un payload FR minimal.",
        "phase2": "Phase 2 : appeler le traducteur.",
        "phase3": "Phase 3 : vérifier la structure et les tokens préservés.",
    },
    "reviewers_say": (
        "Le panel de relecture confirme que la pipeline produit du texte "
        "anglais conforme aux exigences de registre et de cohérence."
    ),
}


# Sample FR panel payload — one reviewer + meta_review, covering all
# the translated fields plus the verbatim-copy tokens.
_FR_PANEL_PAYLOAD = {
    "reviews": [
        {
            "reviewer_persona": "methodologist",
            "verdict": "accept",
            "overall_score": 8.0,
            "confidence": 0.9,
            "strengths": [
                "L'approche est rigoureuse et bien structurée.",
                "Les critères de succès sont quantitatifs et explicites.",
            ],
            "weaknesses": [
                "La taille d'échantillon de la Phase 2 est insuffisante.",
            ],
            "critical_questions": [
                "Comment justifiez-vous le seuil retenu pour le test "
                "statistique principal ?",
            ],
            "recommendation": (
                "Le panel recommande de publier le brief avec une révision "
                "préalable de la Phase 2."
            ),
        },
    ],
    "meta_review": {
        "verdict": "publish_brief",
        "consensus_score": 7.5,
        "key_consensus": [
            "L'hypothèse est jugée originale et méthodologiquement saine.",
        ],
        "key_disagreements": [
            "Le contraire estime que la Phase 2 manque de puissance.",
        ],
        "critical_path": (
            "La validation expérimentale de la prédiction principale "
            "dans la Phase 2."
        ),
        "final_recommendation": (
            "Le panel recommande la publication du brief après "
            "renforcement de la Phase 2."
        ),
        "revision_guidance": [
            "Augmenter la taille d'échantillon de la Phase 2.",
        ],
    },
}


# ── translate_vulgarization_data ───────────────────────────────────────


@pytest.mark.integration
async def test_translate_vulgarization_data_returns_en_payload() -> None:
    en_payload, warnings, usage = await translate_vulgarization_data(
        "TEST-VULG", _FR_VULG_PAYLOAD
    )

    # Shape
    assert isinstance(en_payload, dict)
    assert isinstance(warnings, list)
    assert isinstance(usage, dict)
    assert "cost_usd" in usage

    # Keys (neutral form on the EN side: ``title`` not ``title_fr``)
    assert "title" in en_payload
    assert "hypothesis_in_brief" in en_payload
    assert "why_it_matters" in en_payload
    assert "imagine_that" in en_payload
    assert "reviewers_say" in en_payload
    assert "concretely" in en_payload
    assert "phase1" in en_payload["concretely"]
    assert "phase2" in en_payload["concretely"]
    assert "phase3" in en_payload["concretely"]

    # Sanity: each leaf is non-empty.
    for key in ("title", "hypothesis_in_brief", "why_it_matters", "imagine_that"):
        assert isinstance(en_payload[key], str)
        assert len(en_payload[key]) > 0

    # imagine_that uses second-person address (active voice per Phase 1-bis)
    assert "you" in en_payload["imagine_that"].lower() or \
        "imagine" in en_payload["imagine_that"].lower()

    # Phase markers preserved verbatim
    assert "Phase 1" in en_payload["concretely"]["phase1"]
    assert "Phase 2" in en_payload["concretely"]["phase2"]
    assert "Phase 3" in en_payload["concretely"]["phase3"]


# ── translate_panel_data ───────────────────────────────────────────────


@pytest.mark.integration
async def test_translate_panel_data_preserves_tokens_and_translates_prose() -> None:
    en_payload, warnings, usage = await translate_panel_data(
        "TEST-PANEL", _FR_PANEL_PAYLOAD
    )

    # Shape
    assert isinstance(en_payload, dict)
    assert "reviews" in en_payload
    assert "meta_review" in en_payload
    assert len(en_payload["reviews"]) == 1

    review = en_payload["reviews"][0]

    # Backend tokens copied verbatim
    assert review["reviewer_persona"] == "methodologist"
    assert review["verdict"] == "accept"
    assert review["overall_score"] == 8.0
    assert review["confidence"] == 0.9

    # List counts preserved
    assert len(review["strengths"]) == 2
    assert len(review["weaknesses"]) == 1
    assert len(review["critical_questions"]) == 1

    # Prose translated (non-empty strings, not the FR source)
    assert isinstance(review["recommendation"], str)
    assert len(review["recommendation"]) > 0
    assert "le panel" not in review["recommendation"].lower()

    # meta_review tokens copied verbatim
    meta = en_payload["meta_review"]
    assert meta["verdict"] == "publish_brief"
    assert meta["consensus_score"] == 7.5

    # meta_review prose translated
    assert isinstance(meta["final_recommendation"], str)
    assert len(meta["final_recommendation"]) > 0
    assert len(meta["key_consensus"]) == 1
    assert len(meta["key_disagreements"]) == 1
    assert len(meta["revision_guidance"]) == 1


# ── Error surface check (no LLM call needed) ───────────────────────────


def test_french_in_output_error_importable() -> None:
    """Import-only check — the symbol must be exposed for catch sites."""
    assert FrenchInOutputError is not None
    assert issubclass(FrenchInOutputError, Exception)
