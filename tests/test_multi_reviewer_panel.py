"""Unit tests for the meta-reviewer threshold logic (S8.4).

Covers ``threshold_verdict()`` and the three threshold constants in
``agents.multi_reviewer_panel``. The function is a pure mapping from
``(consensus_score, iteration)`` to verdict — no DB, no LLM — so the
tests are cheap and exhaustive.

S6.4 -> S8.4 calibration history:

  S6.4 (2 May 2026):
    PUBLISH_THRESHOLD          = 7.0  iter 1 publish floor
    REJECT_THRESHOLD           = 4.5  reject floor
    ITER2_PUBLISH_THRESHOLD    = 6.0  iter 2 publish floor

  S8.4 (11 May 2026):
    PUBLISH_THRESHOLD          = 6.5  was 7.0
    REJECT_THRESHOLD           = 4.5  unchanged
    ITER2_PUBLISH_THRESHOLD    = 5.5  was 6.0

  Calibration source: 22 published briefs from the productive April
  2026 window. The top 10 visible historical briefs cluster between
  consensus 6.55 and 7.00 at iter 1; the 7.0 floor captured only 1
  of them. The new 6.5 floor captures all 10. The 6.0 iter-2 floor
  rejected SPORE-2026-05-10-5212d9a1 at 5.84 (sat inside the
  historical published profile per the panel review); the new 5.5
  lets it through.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m unittest tests.test_multi_reviewer_panel -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.multi_reviewer_panel import (  # noqa: E402
    ITER2_PUBLISH_THRESHOLD,
    PUBLISH_THRESHOLD,
    REJECT_THRESHOLD,
    threshold_verdict,
)


class TestThresholdConstants(unittest.TestCase):
    """Confirm the S8.4 numeric calibration is in place."""

    def test_publish_threshold(self) -> None:
        self.assertEqual(PUBLISH_THRESHOLD, 6.5)

    def test_reject_threshold(self) -> None:
        self.assertEqual(REJECT_THRESHOLD, 4.5)

    def test_iter2_publish_threshold(self) -> None:
        self.assertEqual(ITER2_PUBLISH_THRESHOLD, 5.5)


class TestThresholdVerdictIter1(unittest.TestCase):
    """Iter 1 routing: publish >= 6.5, reject < 4.5, else revise."""

    def test_publish_at_threshold(self) -> None:
        # Strict-or-equal: 6.5 publishes.
        self.assertEqual(threshold_verdict(6.5, iteration=1), "publish_brief")

    def test_publish_well_above_threshold(self) -> None:
        self.assertEqual(threshold_verdict(8.0, iteration=1), "publish_brief")
        self.assertEqual(threshold_verdict(7.0, iteration=1), "publish_brief")

    def test_revise_just_below_publish_threshold(self) -> None:
        self.assertEqual(
            threshold_verdict(6.4, iteration=1), "revise_and_resubmit"
        )

    def test_revise_at_reject_floor(self) -> None:
        # Strict inequality on reject: 4.5 escapes the reject branch.
        self.assertEqual(
            threshold_verdict(4.5, iteration=1), "revise_and_resubmit"
        )

    def test_revise_in_middle_band(self) -> None:
        self.assertEqual(
            threshold_verdict(5.0, iteration=1), "revise_and_resubmit"
        )
        self.assertEqual(
            threshold_verdict(6.0, iteration=1), "revise_and_resubmit"
        )

    def test_reject_below_floor(self) -> None:
        self.assertEqual(threshold_verdict(4.49, iteration=1), "reject")
        self.assertEqual(threshold_verdict(3.0, iteration=1), "reject")
        self.assertEqual(threshold_verdict(0.0, iteration=1), "reject")


class TestThresholdVerdictIter2(unittest.TestCase):
    """Iter 2+ routing: binary, publish >= 5.5 else reject."""

    def test_publish_at_threshold(self) -> None:
        self.assertEqual(threshold_verdict(5.5, iteration=2), "publish_brief")

    def test_publish_5212d9a1_case(self) -> None:
        """Real case: SPORE-2026-05-10-5212d9a1 reached consensus 5.84
        at iter 2. Rejected under S6.4 (6.0 floor); publishes under
        S8.4 (5.5 floor). This is the case that motivated S8.4.
        """
        self.assertEqual(threshold_verdict(5.84, iteration=2), "publish_brief")

    def test_reject_just_below_threshold(self) -> None:
        self.assertEqual(threshold_verdict(5.49, iteration=2), "reject")

    def test_reject_well_below(self) -> None:
        self.assertEqual(threshold_verdict(4.0, iteration=2), "reject")
        self.assertEqual(threshold_verdict(0.0, iteration=2), "reject")

    def test_no_revise_at_iter2(self) -> None:
        """Iter 2 must collapse to binary — never revise."""
        for score in [3.0, 5.0, 5.4, 5.5, 6.0, 7.0, 8.0]:
            with self.subTest(score=score):
                verdict = threshold_verdict(score, iteration=2)
                self.assertIn(verdict, ("publish_brief", "reject"))
                self.assertNotEqual(verdict, "revise_and_resubmit")

    def test_iter3_behaves_same_as_iter2(self) -> None:
        """The function uses ``iteration >= 2`` so iter 3, 4, ... map
        to the same binary rule."""
        self.assertEqual(threshold_verdict(5.84, iteration=3), "publish_brief")
        self.assertEqual(threshold_verdict(5.49, iteration=3), "reject")


class TestHistoricalBacktest(unittest.TestCase):
    """Backtest the S8.4 thresholds against the empirical distribution.

    The 10 top published historical briefs (April 2026 window) all sat
    between consensus 6.55 and 7.00 at iter 1. Under S6.4 (7.0 floor)
    only the 7.00 sample would have published at iter 1; the rest would
    have gone to iter 2 with a 6.0 floor (and most would have published
    eventually). Under S8.4 (6.5 floor) all 10 publish at iter 1.
    """

    HISTORICAL_TOP10_ITER1 = [
        # (consensus, brief_id)
        (7.00, "SPR-2026-816D"),
        (6.97, "SPR-2026-5301"),
        (6.93, "SPR-2026-FBF3"),
        (6.91, "SPR-2026-9A56"),
        (6.84, "SPR-2026-6FEB"),
        (6.82, "SPR-2026-7626"),
        (6.72, "SPR-2026-B151"),
        (6.66, "SPR-2026-7516"),
        (6.60, "SPR-2026-1BA4"),
        (6.55, "SPR-2026-4328"),
    ]

    def test_all_top10_publish_at_iter1_under_s8_4(self) -> None:
        for consensus, brief_id in self.HISTORICAL_TOP10_ITER1:
            with self.subTest(brief_id=brief_id, consensus=consensus):
                self.assertEqual(
                    threshold_verdict(consensus, iteration=1),
                    "publish_brief",
                    f"{brief_id} (consensus {consensus}) should publish at iter 1 under S8.4",
                )

    def test_5212d9a1_publishes_at_iter2_under_s8_4(self) -> None:
        """The motivating case — 5212d9a1 at iter 2 with consensus 5.84."""
        self.assertEqual(threshold_verdict(5.84, iteration=2), "publish_brief")

    def test_pathological_cases_still_rejected(self) -> None:
        cases = [
            (3.5, 1, "reject"),
            (5.0, 2, "reject"),
            (5.4, 2, "reject"),
            (4.4, 1, "reject"),
        ]
        for consensus, iteration, expected in cases:
            with self.subTest(consensus=consensus, iteration=iteration):
                self.assertEqual(
                    threshold_verdict(consensus, iteration=iteration),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
