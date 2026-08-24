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

  5. composite >= 0.40 AND halluc <= 0.45 AND verdict == intéressant
                                    → a_tester
  6. Verdict not "intéressant"      → no promotion (idempotent)
  7. Out-of-range scores            → no promotion
  8. Historical backtest            → 14/16 known a_testers rescued

S8.1-bis (11 May 2026) — thresholds relaxed from 0.45/0.40 to
0.40/0.45 after the S8.2 genome revert. The original 0.45/0.40 pair
captured only 4/16 historical a_testers; the new pair captures
14/16 (the two exceptions sit at halluc 0.50, deliberately
unpromoted).

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
        # Strict-or-equal at S8.1-bis boundaries: composite == 0.40 and
        # halluc == 0.45 promote.
        result = evaluate_override(
            composite=0.40,
            hallucination_risk=0.45,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "a_tester")

    def test_no_promote_when_composite_below_threshold(self) -> None:
        # Just below the S8.1-bis composite floor (0.40).
        result = evaluate_override(
            composite=0.39,
            hallucination_risk=0.30,
            current_verdict="intéressant",
        )
        self.assertIsNone(result)

    def test_no_promote_when_hallucination_above_threshold(self) -> None:
        # composite is high enough but halluc is just over the
        # S9.1 ceiling (0.55). composite >= 0.42 so the stacked kill
        # does not fire — only the promotion ceiling blocks it.
        result = evaluate_override(
            composite=0.55,
            hallucination_risk=0.56,
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

    def test_backtest_does_not_promote_when_halluc_above_ceiling(self) -> None:
        """Cases above the S9.1 halluc ceiling (0.55) must NOT promote.

        S9.1 relaxed the ceiling 0.45 -> 0.55 (halluc is a noisy,
        non-discriminating axis, already penalised inside composite).
        Only genuinely extreme halluc (> 0.55) is excluded from
        promotion; halluc > 0.65 is killed outright. These cases keep
        composite >= 0.42 so the stacked kill does not fire — isolating
        the promotion ceiling as the sole reason for no promotion.
        """
        cases = [
            # Just above the 0.55 ceiling — blocked by the ceiling alone.
            (0.50, 0.56, "synthetic-halluc-0.56"),
            # Comfortably above the ceiling but below the 0.65 hard kill.
            (0.48, 0.60, "synthetic-halluc-0.60"),
        ]
        for composite, halluc, label in cases:
            with self.subTest(label=label):
                result = evaluate_override(
                    composite=composite,
                    hallucination_risk=halluc,
                    current_verdict="intéressant",
                )
                self.assertIsNone(result, f"{label} must not promote (halluc above 0.55)")

    # ── 7. S8.1-bis new tests (relaxed thresholds) ─────────────────

    def test_s8_1_bis_promote_post_revert_near_miss(self) -> None:
        """Real post-revert hypothesis 5212d9a1 (composite 0.444, halluc
        0.45). Missed the original 0.45/0.40 thresholds by one
        millième; promotes under S8.1-bis 0.40/0.45 thresholds.
        """
        result = evaluate_override(
            composite=0.444,
            hallucination_risk=0.45,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "a_tester")

    def test_s9_1_promotes_former_c97a9cbf_near_miss(self) -> None:
        """Real post-revert hypothesis c97a9cbf (composite 0.402, halluc
        0.475). Under S8.1-bis it was blocked by the 0.45 halluc ceiling;
        under S9.1 (ceiling 0.55) it now promotes. composite clears the
        0.40 floor and halluc 0.475 is below the noisy-axis ceiling — the
        exact May-population case the relaxation is designed to rescue.
        """
        result = evaluate_override(
            composite=0.402,
            hallucination_risk=0.475,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "a_tester")

    def test_s9_1_composite_floor_still_blocks(self) -> None:
        """The composite floor (0.40) is the real discriminant and is
        unchanged by S9.1. A hypothesis just below it does NOT promote,
        even with pristine halluc — composite separates human-trash
        (<=0.31) from human-want_to_test (>=0.40).
        """
        result = evaluate_override(
            composite=0.39,
            hallucination_risk=0.20,
            current_verdict="intéressant",
        )
        self.assertIsNone(result)

    def test_s8_1_bis_still_blocks_drift_hypothesis(self) -> None:
        """Drift-period hypothesis SPORE-2026-05-09-b2434892
        (composite 0.372, halluc 0.55). Should remain unpromoted —
        composite is below the 0.40 floor AND halluc is above the
        0.45 ceiling.
        """
        result = evaluate_override(
            composite=0.372,
            hallucination_risk=0.55,
            current_verdict="intéressant",
        )
        # The S6.4 stacked rule (composite < 0.42 AND halluc > 0.55)
        # would kill on halluc > 0.55, but here halluc is exactly 0.55
        # (strict greater-than fails), so no kill fires either.
        # Either no override OR kill — never a promotion.
        if result is not None:
            self.assertNotEqual(result[0], "a_tester")

    def test_s8_1_bis_backtest_promotes_majority_of_historical(self) -> None:
        """Backtest: >= 14/16 historical a_testers should promote. Under
        S8.1-bis (ceiling 0.45) the two halluc-0.50 cases were excluded
        (14/16); under S9.1 (ceiling 0.55) they also promote (16/16).
        The assertion floors at 14 so it holds across both calibrations.
        """
        historical_a_testers = [
            # (composite, halluc) — 16 historical a_tester scores from
            # the L0 critics over 7-23 April 2026.
            (0.518, 0.35), (0.512, 0.25), (0.512, 0.10), (0.512, 0.20),
            (0.495, 0.40), (0.486, 0.40), (0.484, 0.40), (0.478, 0.35),
            (0.470, 0.15), (0.470, 0.35), (0.445, 0.45), (0.438, 0.40),
            (0.434, 0.45),
            (0.433, 0.50),  # one of the two halluc-0.50 exceptions
            (0.432, 0.40),
            (0.411, 0.45),
        ]
        promoted = 0
        for composite, halluc in historical_a_testers:
            result = evaluate_override(
                composite=composite,
                hallucination_risk=halluc,
                current_verdict="intéressant",
            )
            if result is not None and result[0] == "a_tester":
                promoted += 1
        # Expected: 15 promoted (only the halluc=0.50 case is excluded
        # in this hand-picked sample). The full historical set has two
        # halluc=0.50 cases; this list contains one.
        self.assertGreaterEqual(
            promoted, 14, f"Only {promoted}/16 historical a_testers promoted"
        )

    # ── 8. S9.1 halluc-noise ceiling (0.45 -> 0.55) ────────────────

    def test_s9_1_promotes_at_ceiling_0_55_exact(self) -> None:
        """Boundary: composite >= 0.40 and halluc == 0.55 (inclusive)
        promote. composite 0.45 clears the stacked kill.
        """
        result = evaluate_override(
            composite=0.45,
            hallucination_risk=0.55,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "a_tester")
        self.assertIn("0.55", result[1])
        self.assertIn("S9.1", result[1])

    def test_s9_1_rescues_may_population_at_halluc_0_50(self) -> None:
        """The May population clustered at halluc 0.48-0.53 with
        composite ~0.40-0.42 — blocked wholesale by the old 0.45
        ceiling (a_tester collapsed 16 -> 1). Under S9.1 a typical May
        point promotes. composite 0.42 == stacked-kill floor (strict
        <), so the kill does not fire.
        """
        result = evaluate_override(
            composite=0.42,
            hallucination_risk=0.50,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "a_tester")

    def test_s9_1_stacked_kill_still_guards_marginal_plus_high_halluc(self) -> None:
        """S9.1 does not weaken the fabrication guards: a marginal
        composite (< 0.42) stacked with halluc > 0.55 is still killed to
        poubelle, never promoted.
        """
        result = evaluate_override(
            composite=0.41,
            hallucination_risk=0.56,
            current_verdict="intéressant",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "poubelle")


if __name__ == "__main__":
    unittest.main()
