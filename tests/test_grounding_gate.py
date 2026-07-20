"""Unit tests for the S9.2 grounding gate in should_revise_or_publish.

The gate blocks publication of briefs with an empty evidence base
(silent grounding-analysis failure), while preserving:
  - the panel's own reject / revise routing,
  - the degraded path (transient Semantic Scholar outage), which
    publishes with a low_evidence flag for later enrichment.

Calibrated on the historical brief SPR-2026-52AA: Semantic Scholar
returned papers but the grounding LLM crashed, leaving evidence_base
empty; the brief was published anyway. The gate now rejects exactly
that profile.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m unittest tests.test_grounding_gate -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.post_fire_pipeline import should_revise_or_publish  # noqa: E402


def _state(**kwargs):
    """Minimal PostFireState-ish dict for the router."""
    base = {
        "meta_verdict": "publish_brief",
        "revision_count": 1,
        "grounding": {"evidence_base": [{"title": "a paper"}]},
        "grounding_degraded": False,
    }
    base.update(kwargs)
    return base


class TestGroundingGate(unittest.TestCase):
    # ── Panel routing is preserved ────────────────────────────────

    def test_panel_reject_still_rejects(self) -> None:
        self.assertEqual(
            should_revise_or_publish(_state(meta_verdict="reject")),
            "rejected",
        )

    def test_revise_still_revises_below_max(self) -> None:
        self.assertEqual(
            should_revise_or_publish(
                _state(meta_verdict="revise_and_resubmit", revision_count=1)
            ),
            "revise",
        )

    def test_revise_at_max_publishes_when_grounded(self) -> None:
        # revise_and_resubmit at iteration 2 falls through to publish,
        # and the grounding gate lets it through (evidence present).
        self.assertEqual(
            should_revise_or_publish(
                _state(meta_verdict="revise_and_resubmit", revision_count=2)
            ),
            "publish",
        )

    # ── Normal publish path ───────────────────────────────────────

    def test_publish_with_evidence(self) -> None:
        self.assertEqual(should_revise_or_publish(_state()), "publish")

    # ── S9.2 grounding gate ───────────────────────────────────────

    def test_gate_rejects_empty_evidence_non_degraded(self) -> None:
        # The 52AA profile: panel said publish, but evidence_base is
        # empty and SS was NOT down → reject.
        self.assertEqual(
            should_revise_or_publish(
                _state(grounding={"evidence_base": []}, grounding_degraded=False)
            ),
            "rejected",
        )

    def test_gate_skipped_when_degraded(self) -> None:
        # SS outage: empty evidence is expected; publish with low_evidence
        # flag for later enrichment.
        self.assertEqual(
            should_revise_or_publish(
                _state(grounding={"evidence_base": []}, grounding_degraded=True)
            ),
            "publish",
        )

    def test_gate_handles_missing_grounding_key(self) -> None:
        # Defensive: no grounding dict at all → treated as empty evidence,
        # non-degraded → reject rather than crash.
        st = _state()
        del st["grounding"]
        self.assertEqual(should_revise_or_publish(st), "rejected")

    def test_gate_does_not_touch_panel_reject_even_if_evidence_present(self) -> None:
        # Ordering: an explicit panel reject wins regardless of evidence.
        self.assertEqual(
            should_revise_or_publish(_state(meta_verdict="reject")),
            "rejected",
        )


if __name__ == "__main__":
    unittest.main()
