"""S10-C — glossaire technique et borne de ratio du traducteur de panel.

Couvre, sans appel LLM :

* le glossaire est présent dans les deux prompts, chaque terme dans son sens,
  avec la forme britannique côté FR→EN quand elle diffère ;
* « effet de taille » est explicitement proscrit côté EN→FR ;
* la borne FR/EN du sens EN→FR, recalibrée sur les traductions réelles de
  S10-B, accepte le 1,48 fidèle de SPR-2026-27B2 et signale encore une
  troncature ou un écho du texte source.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s10c_translation
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.translate_brief_panel import (  # noqa: E402
    BASE_PROMPT,
    BASE_PROMPT_FR,
    TECHNICAL_GLOSSARY,
    _build_list_prompt,
    _build_list_prompt_fr,
    _validate_text,
    _validate_text_fr,
)

# Question réelle de SPR-2026-27B2 (methodologist, critical_questions[2]).
EN_QUESTION = (
    "How will you handle missing data in the literature dataset? Will you use "
    "imputation, and if so, how might that bias the SHAP values?"
)
FR_QUESTION = (
    "Comment allez-vous traiter les données manquantes dans le jeu de données "
    "issu de la littérature ? Aurez-vous recours à l'imputation, et si oui, "
    "comment cela pourrait-il biaiser les valeurs SHAP ?"
)


class GlossaryTests(unittest.TestCase):
    """Le glossaire alimente les deux sens de traduction."""

    def test_every_pair_is_in_both_prompts(self) -> None:
        for en, fr, en_gb in TECHNICAL_GLOSSARY:
            self.assertIn(f'- "{en}" → « {fr} »', BASE_PROMPT_FR)
            self.assertIn(f'- « {fr} » → "{en_gb or en}"', BASE_PROMPT)

    def test_effect_size_rendering_is_pinned(self) -> None:
        self.assertIn('- "effect size" → « taille d\'effet »', BASE_PROMPT_FR)
        self.assertIn("Jamais « effet de taille »", BASE_PROMPT_FR)
        self.assertIn('- « taille d\'effet » → "effect size"', BASE_PROMPT)

    def test_british_form_on_fr_to_en_side(self) -> None:
        self.assertIn('"generalisability"', BASE_PROMPT)
        self.assertNotIn('"generalizability"', BASE_PROMPT)

    def test_glossary_reaches_the_per_call_prompts(self) -> None:
        self.assertIn("GLOSSAIRE TECHNIQUE", _build_list_prompt_fr(["a", "b"]))
        self.assertIn("TECHNICAL GLOSSARY", _build_list_prompt(["a", "b"]))

    def test_glossary_is_closed_and_unique(self) -> None:
        english = [en for en, _, _ in TECHNICAL_GLOSSARY]
        self.assertEqual(len(english), len(set(english)))
        self.assertLessEqual(len(TECHNICAL_GLOSSARY), 40)


class RatioBoundTests(unittest.TestCase):
    """Borne de longueur du sens EN→FR."""

    def test_faithful_long_french_question_is_accepted(self) -> None:
        ratio = len(FR_QUESTION) / len(EN_QUESTION)
        self.assertGreater(ratio, 1.45)
        self.assertEqual(_validate_text_fr("q", EN_QUESTION, FR_QUESTION), [])

    def test_truncation_and_echo_still_warn(self) -> None:
        truncated = FR_QUESTION[: int(len(EN_QUESTION) * 0.8)]
        echoed = f"{EN_QUESTION} {FR_QUESTION}"
        self.assertTrue(any("ratio" in w for w in _validate_text_fr("q", EN_QUESTION, truncated)))
        self.assertTrue(any("ratio" in w for w in _validate_text_fr("q", EN_QUESTION, echoed)))

    def test_fr_to_en_bound_is_unchanged(self) -> None:
        self.assertEqual(_validate_text("q", "a" * 100, "b" * 125), [])
        self.assertTrue(any("0.70-1.25" in w for w in _validate_text("q", "a" * 100, "b" * 126)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
