"""Unit tests for the ReviewerAgent post-LLM mechanical override.

Covers the four primary cases of the recalibrated S6.4 rule:

  1. Very low composite             → poubelle
  2. Low composite + high halluc    → poubelle
  3. Extreme hallucination_risk     → poubelle
  4. Acceptable signals             → no override (None)

Plus a few boundary cases at the threshold values.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m unittest tests.test_reviewer_override -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.reviewer import evaluate_override  # noqa: E402


class TestEvaluateOverride(unittest.TestCase):
    """Pure-function tests for ``agents.reviewer.evaluate_override``."""

    # ── 1. Very low composite ──────────────────────────────────────

    def test_very_low_composite_kills_regardless_of_halluc(self) -> None:
        result = evaluate_override(composite=0.30, hallucination_risk=0.10)
        self.assertIsNotNone(result)
        verdict, reason = result
        self.assertEqual(verdict, "poubelle")
        self.assertIn("composite", reason)
        self.assertIn("0.30", reason)

    def test_composite_just_below_035_threshold_kills(self) -> None:
        result = evaluate_override(composite=0.349, hallucination_risk=0.20)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "poubelle")

    def test_composite_at_035_does_not_kill(self) -> None:
        # Strict inequality: composite >= 0.35 escapes the first rule.
        result = evaluate_override(composite=0.35, hallucination_risk=0.20)
        self.assertIsNone(result)

    # ── 2. Stacked low composite + high halluc ─────────────────────

    def test_stacked_low_composite_plus_high_halluc_kills(self) -> None:
        result = evaluate_override(composite=0.40, hallucination_risk=0.60)
        self.assertIsNotNone(result)
        verdict, reason = result
        self.assertEqual(verdict, "poubelle")
        self.assertIn("low composite", reason)
        self.assertIn("high hallucination", reason)

    def test_stacked_only_one_signal_does_not_kill(self) -> None:
        # composite below 0.42 alone, halluc below 0.55 → no kill.
        self.assertIsNone(
            evaluate_override(composite=0.40, hallucination_risk=0.50)
        )
        # halluc above 0.55 alone, composite >= 0.42 → no kill from rule 2.
        # (rule 3 only fires above 0.65)
        self.assertIsNone(
            evaluate_override(composite=0.45, hallucination_risk=0.60)
        )

    # ── 3. Extreme hallucination_risk ──────────────────────────────

    def test_extreme_halluc_kills_even_with_strong_composite(self) -> None:
        result = evaluate_override(composite=0.80, hallucination_risk=0.70)
        self.assertIsNotNone(result)
        verdict, reason = result
        self.assertEqual(verdict, "poubelle")
        self.assertIn("hallucination", reason.lower())
        self.assertIn("0.70", reason)

    def test_halluc_at_065_does_not_kill(self) -> None:
        # Strict inequality: halluc must be > 0.65 to trigger rule 3.
        self.assertIsNone(
            evaluate_override(composite=0.50, hallucination_risk=0.65)
        )

    # ── 4. Acceptable signals → no override ────────────────────────

    def test_typical_acceptable_hypothesis_passes(self) -> None:
        # Realistic numbers from a healthy run (close to the historical
        # median): composite 0.50, halluc 0.30. LLM verdict stands.
        self.assertIsNone(
            evaluate_override(composite=0.50, hallucination_risk=0.30)
        )

    def test_borderline_recent_drift_sample_passes(self) -> None:
        # Reproduces the actual SPORE-2026-04-26 hypothesis that got
        # killed by the old rule and is rescued by the new one.
        # (composite 0.375, halluc 0.525 — both moderate, no stacking
        # severity → must not be killed.)
        self.assertIsNone(
            evaluate_override(composite=0.375, hallucination_risk=0.525)
        )


if __name__ == "__main__":
    unittest.main()
