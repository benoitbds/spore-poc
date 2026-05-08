"""Unit tests for the ReviewerAgent post-LLM mechanical override.

S6.4 (kill paths) — three rules that demote LLM verdicts to poubelle:

  1. Very low composite             → poubelle
  2. Low composite + high halluc    → poubelle
  3. Extreme hallucination_risk     → poubelle
  4. Acceptable signals             → no override (None)

S8.1 (promotion path) — symmetric rule that promotes intéressant to
a_tester when L0 critics scored the hypothesis in the historical
a_tester range with low hallucination flag. Calibrated on 16
a_tester historical cases (7-23 April 2026).

  5. composite >= 0.45 AND halluc <= 0.40 AND verdict == intéressant
                                    → a_tester
  6. Verdict not "intéressant"      → no promotion (idempotent)
  7. Out-of-range scores            → no promotion
  8. Historical backtest            → 6 known a_tester rescues

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

    # ── 5. S8.1 promotion rule ─────────────────────────────────────

    def test_promote_when_interessant_and_scores_in_range(self) -> None:
        result = evaluate_override(
            composite=0.50,
            hallucination_risk=0.30,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        verdict, reason = result
        self.assertEqual(verdict, "a_tester")
        self.assertIn("intéressant", reason)
        self.assertIn("a_tester", reason)
        self.assertIn("0.50", reason)

    def test_promote_at_thresholds_exact(self) -> None:
        # Strict-or-equal: composite == 0.45 and halluc == 0.40 promote.
        result = evaluate_override(
            composite=0.45,
            hallucination_risk=0.40,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "a_tester")

    def test_no_promote_when_composite_below_threshold(self) -> None:
        result = evaluate_override(
            composite=0.44,
            hallucination_risk=0.30,
            current_verdict="intéressant",
        )
        self.assertIsNone(result)

    def test_no_promote_when_hallucination_above_threshold(self) -> None:
        # composite is high enough but halluc is just over 0.40.
        result = evaluate_override(
            composite=0.55,
            hallucination_risk=0.41,
            current_verdict="intéressant",
        )
        self.assertIsNone(result)

    def test_no_promote_when_verdict_not_interessant(self) -> None:
        # Promotion is gated on the LLM having said "intéressant".
        # A poubelle hypothesis with passable scores does NOT get
        # promoted — the LLM's qualitative kill stands.
        result = evaluate_override(
            composite=0.50,
            hallucination_risk=0.30,
            current_verdict="poubelle",
        )
        # No kill rule fires (composite >= 0.35, halluc < 0.55, halluc < 0.65)
        # and the promotion gate excludes non-intéressant verdicts.
        self.assertIsNone(result)

    def test_no_promote_when_already_a_tester(self) -> None:
        # Idempotent: do not double-promote.
        result = evaluate_override(
            composite=0.50,
            hallucination_risk=0.30,
            current_verdict="a_tester",
        )
        self.assertIsNone(result)

    def test_no_promote_when_current_verdict_omitted(self) -> None:
        # Backwards-compat: callers that do not pass current_verdict get
        # only the kill paths. None of the kill rules fire here, so the
        # result is None.
        self.assertIsNone(
            evaluate_override(composite=0.50, hallucination_risk=0.30)
        )

    # ── 6. S8.1 historical backtest (data-driven) ──────────────────

    def test_backtest_promotes_known_undervalued_interessants(self) -> None:
        """Reference 13-24 April intéressants whose L0 scores match the
        historical a_tester profile — these are the cases the rule is
        designed to rescue.
        """
        cases = [
            # (composite, halluc, label) — all real points from
            # SPORE-2026-04-13 to SPORE-2026-04-23 + post-3 May
            (0.531, 0.30, "ref-21-37423fce"),
            (0.519, 0.20, "ref-23-7fa75d9d"),
            (0.517, 0.10, "ref-16-671129f4"),
            (0.505, 0.15, "ref-16-bee1b9b6"),
            (0.492, 0.20, "ref-19-a0b4f2ba"),
            (0.526, 0.25, "post-04-e1c3b07c"),
        ]
        for composite, halluc, label in cases:
            with self.subTest(label=label):
                result = evaluate_override(
                    composite=composite,
                    hallucination_risk=halluc,
                    current_verdict="intéressant",
                )
                self.assertIsNotNone(result, f"{label} should promote")
                self.assertEqual(result[0], "a_tester", f"{label} expected a_tester")

    def test_backtest_does_not_promote_borderline_halluc(self) -> None:
        """Real recent samples that should NOT promote because halluc
        is over the 0.40 conservative ceiling.
        """
        cases = [
            (0.463, 0.425, "post-08-991ed571"),
            (0.412, 0.45, "post-03-9089d50d"),
        ]
        for composite, halluc, label in cases:
            with self.subTest(label=label):
                result = evaluate_override(
                    composite=composite,
                    hallucination_risk=halluc,
                    current_verdict="intéressant",
                )
                self.assertIsNone(result, f"{label} must not promote (halluc too high)")


if __name__ == "__main__":
    unittest.main()
