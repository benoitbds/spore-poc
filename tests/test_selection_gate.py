"""Unit tests for the S9.3 relative selection gate.

Covers ``percentile()``, ``selection_threshold()`` and the router branch
in ``should_revise_or_publish`` that applies them. All pure functions —
no DB, no LLM.

Why the gate exists
-------------------
The absolute panel thresholds (S8.4) published 26/26 briefs on the
2026-07-20 batch. A mixed-batch test on 2026-07-21 (5 poubelle-rated
hypotheses + 3 a_tester) measured what the panel can actually do:

    poubelle  n=5  mean 5.95  range 5.46-6.36
    a_tester  n=3  mean 6.42  range 6.18-6.62
    AUC 0.933 — one inversion (6.36 poubelle > 6.18 a_tester)

The panel ranks; the iter-2 threshold of 5.5 was simply parked below the
whole poubelle band. Re-tuning that constant on published briefs is the
S8.4 circularity (calibrating on survivors only ever ratchets the gate
down), so publication is decided relative to recent panel output
instead, with an absolute floor for the all-mediocre-batch case the
bimodal test does not cover.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m unittest tests.test_selection_gate -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.multi_reviewer_panel import (  # noqa: E402
    percentile,
    selection_threshold,
    SELECTION_FLOOR,
    SELECTION_MIN_SAMPLES,
    SELECTION_PERCENTILE,
)
from graph.post_fire_pipeline import should_revise_or_publish  # noqa: E402


def _state(**kwargs):
    """Minimal PostFireState-ish dict clearing every gate but selection."""
    base = {
        "meta_verdict": "publish_brief",
        "revision_count": 2,
        "grounding": {"evidence_base": [{"title": "a paper"}]},
        "grounding_degraded": False,
        "panel": {"meta_review": {"consensus_score": 6.5}},
        "selection_threshold": 6.27,
    }
    base.update(kwargs)
    return base


class TestPercentile(unittest.TestCase):
    def test_endpoints(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(percentile(values, 0.0), 1.0)
        self.assertEqual(percentile(values, 1.0), 5.0)

    def test_median(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5), 3.0)

    def test_interpolates_between_samples(self) -> None:
        # q=0.5 on 4 points sits between the 2nd and 3rd.
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)

    def test_order_independent(self) -> None:
        self.assertEqual(
            percentile([5.0, 1.0, 3.0, 2.0, 4.0], 0.7),
            percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.7),
        )

    def test_single_value(self) -> None:
        self.assertEqual(percentile([6.4], 0.7), 6.4)

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            percentile([], 0.7)


class TestSelectionThreshold(unittest.TestCase):
    def test_insufficient_history_falls_back_to_floor(self) -> None:
        scores = [6.1] * (SELECTION_MIN_SAMPLES - 1)
        threshold, reason = selection_threshold(scores)
        self.assertEqual(threshold, SELECTION_FLOOR)
        self.assertEqual(reason, "floor_insufficient_history")

    def test_empty_history_falls_back_to_floor(self) -> None:
        self.assertEqual(selection_threshold([]), (SELECTION_FLOOR, "floor_insufficient_history"))

    def test_floor_binds_on_a_weak_window(self) -> None:
        # An all-mediocre window: the relative rule would publish its top
        # slice anyway, the floor stops it.
        threshold, reason = selection_threshold([5.0] * 20)
        self.assertEqual(threshold, SELECTION_FLOOR)
        self.assertEqual(reason, "floor_binding")

    def test_relative_rule_binds_on_a_strong_window(self) -> None:
        threshold, reason = selection_threshold([7.0] * 20)
        self.assertGreater(threshold, SELECTION_FLOOR)
        self.assertEqual(reason, "relative_percentile")

    def test_reproduces_the_2026_07_20_batch(self) -> None:
        """The 26 real briefs of 2026-07-20 → ~31% publish instead of 100%."""
        batch = [
            6.51, 6.25, 6.59, 6.07, 6.72, 6.62, 6.19, 5.72, 5.81, 6.29,
            5.86, 6.11, 5.78, 6.02, 6.58, 5.70, 6.52, 6.25, 6.44, 6.11,
            5.94, 6.01, 5.50, 6.10, 6.07, 5.86,
        ]
        threshold, reason = selection_threshold(batch)
        self.assertEqual(reason, "relative_percentile")
        kept = [s for s in batch if s >= threshold]
        # Top 30% of the window, give or take ties at the boundary.
        self.assertGreaterEqual(len(kept), 6)
        self.assertLessEqual(len(kept), 10)
        self.assertLess(len(kept), len(batch))

    def test_percentile_constant_selects_the_top_slice(self) -> None:
        # Guards the direction of SELECTION_PERCENTILE: 0.70 must mean
        # "keep the top 30%", not "keep the bottom 70%".
        window = [float(i) for i in range(1, 21)]
        threshold, _ = selection_threshold(window)
        kept = [s for s in window if s >= threshold]
        self.assertLess(len(kept), len(window) / 2)
        self.assertAlmostEqual(SELECTION_PERCENTILE, 0.70)


class TestSelectionGateRouting(unittest.TestCase):
    def test_publishes_above_threshold(self) -> None:
        self.assertEqual(should_revise_or_publish(_state()), "publish")

    def test_publishes_exactly_at_threshold(self) -> None:
        state = _state(
            panel={"meta_review": {"consensus_score": 6.27}},
            selection_threshold=6.27,
        )
        self.assertEqual(should_revise_or_publish(state), "publish")

    def test_rejects_below_threshold(self) -> None:
        state = _state(panel={"meta_review": {"consensus_score": 6.0}})
        self.assertEqual(should_revise_or_publish(state), "rejected")

    def test_missing_threshold_defaults_to_floor(self) -> None:
        # Threshold resolution failed upstream → the floor still applies.
        state = _state(panel={"meta_review": {"consensus_score": 6.2}})
        del state["selection_threshold"]
        self.assertEqual(should_revise_or_publish(state), "publish")

        state = _state(panel={"meta_review": {"consensus_score": 5.9}})
        del state["selection_threshold"]
        self.assertEqual(should_revise_or_publish(state), "rejected")

    def test_grounding_gate_still_wins(self) -> None:
        # Ordering: an ungrounded brief is rejected even when its
        # consensus clears the selection threshold.
        state = _state(
            panel={"meta_review": {"consensus_score": 9.0}},
            grounding={"evidence_base": []},
        )
        self.assertEqual(should_revise_or_publish(state), "rejected")

    def test_panel_reject_still_wins(self) -> None:
        state = _state(
            meta_verdict="reject",
            panel={"meta_review": {"consensus_score": 9.0}},
        )
        self.assertEqual(should_revise_or_publish(state), "rejected")

    def test_revise_is_not_short_circuited_by_selection(self) -> None:
        # A revisable brief below the selection threshold must still get
        # its revision pass rather than being rejected outright.
        state = _state(
            meta_verdict="revise_and_resubmit",
            revision_count=1,
            panel={"meta_review": {"consensus_score": 5.0}},
        )
        self.assertEqual(should_revise_or_publish(state), "revise")

    def test_degraded_grounding_still_subject_to_selection(self) -> None:
        # The degraded path skips the grounding gate, not the selection
        # gate: a weak brief does not publish just because SS was down.
        state = _state(
            grounding={"evidence_base": []},
            grounding_degraded=True,
            panel={"meta_review": {"consensus_score": 5.0}},
        )
        self.assertEqual(should_revise_or_publish(state), "rejected")


if __name__ == "__main__":
    unittest.main()
