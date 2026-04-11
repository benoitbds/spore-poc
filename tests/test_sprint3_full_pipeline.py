"""Sprint 3 full end-to-end test: Post-fire pipeline via LangGraph.

Runs the catalysts hypothesis through the complete pipeline:
  Grounding → Sharpening → Protocol → Panel (5 reviewers) → Meta → Brief

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_sprint3_full_pipeline
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.post_fire_pipeline import run_post_fire_pipeline


HYPOTHESIS = (
    "Self-repairing catalysts via oscillatory surface reconstruction: "
    "Catalysts that automatically repair themselves through oscillatory "
    "surface reconstruction, where periodic surface restructuring driven "
    "by reaction conditions continuously regenerates active sites"
)
DOMAINS = ["Catalytic Materials", "Surface Science"]
MECHANISMS = (
    "Periodic surface restructuring driven by reaction conditions "
    "continuously regenerates active sites. The oscillatory reconstruction "
    "is self-sustaining: the catalytic reaction itself provides the driving "
    "force for surface rearrangement, creating a feedback loop where "
    "degraded sites are preferentially restructured."
)
KEYWORDS = [
    "self-repairing catalyst",
    "oscillatory surface reconstruction",
    "active site regeneration",
    "surface restructuring",
    "catalyst deactivation",
]
GAP_MANIFEST = {
    "data_gaps": [
        {"domain": "Surface Science", "description": "No in-situ data on oscillatory reconstruction at catalytic temperatures"}
    ],
    "competence_gaps": [],
    "epistemic_gaps": [
        {"zone": "feedback mechanism", "signal": "unclear how reaction drives reconstruction"}
    ],
}


def print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)
    print()


async def main() -> None:
    print_section("SPORE Sprint 3 — Full Post-Fire Pipeline")

    state = await run_post_fire_pipeline(
        hypothesis=HYPOTHESIS,
        domains=DOMAINS,
        mechanisms=MECHANISMS,
        keywords=KEYWORDS,
        gap_manifest=GAP_MANIFEST,
    )

    # ── Results ──────────────────────────────────────────────
    print_section("PIPELINE RESULTS")

    # Kill check
    if state.get("kill_reason"):
        print(f"KILLED: {state['kill_reason']}")
        return

    # Grounding summary
    grounding = state.get("grounding", {})
    novelty = grounding.get("novelty_assessment", {})
    print(f"Novelty: {novelty.get('score', '?')}/1.0 ({novelty.get('verdict', '?')})")
    print(f"Evidence papers: {len(grounding.get('evidence_base', []))}")
    print(f"Counter-evidence: {len(grounding.get('counter_evidence', []))}")
    print()

    # Sharpening summary
    sharpened = state.get("sharpened", {})
    print(f"Title: {sharpened.get('title', '?')}")
    print(f"Predictions: {len(sharpened.get('falsifiable_predictions', []))}")
    print()

    # Protocol summary
    protocol = state.get("protocol", {})
    print(f"Protocol: {protocol.get('protocol_title', '?')}")
    print(f"Timeline: {protocol.get('overall_timeline', '?')}")
    print(f"Budget: {protocol.get('overall_budget_estimate', '?')}")
    print()

    # Panel summary
    panel = state.get("panel", {})
    reviews = panel.get("reviews", [])
    meta = panel.get("meta_review", {})

    print("REVIEWER PANEL:")
    for r in reviews:
        print(f"  {r['reviewer_persona']:20s} | {r['overall_score']:4.1f}/10 | {r['verdict']:15s} | conf={r['confidence']:.2f}")
    print()
    print(f"Consensus score: {meta.get('consensus_score', '?')}/10")
    print(f"Meta verdict: {meta.get('verdict', '?')}")
    print(f"Revision count: {state.get('revision_count', 0)}")
    print()

    print("Key consensus:")
    for c in meta.get("key_consensus", []):
        print(f"  - {c}")
    print()
    print("Key disagreements:")
    for d in meta.get("key_disagreements", []):
        print(f"  - {d}")
    print()
    print(f"Critical path: {meta.get('critical_path', '?')}")
    print()

    # Brief
    brief_id = state.get("brief_id")
    md_path = state.get("brief_md_path")
    json_path = state.get("brief_json_path")

    if brief_id:
        print_section(f"BRIEF GENERATED: {brief_id}")
        print(f"Markdown: {md_path}")
        print(f"JSON: {json_path}")
        print()

        # Show first 100 lines of the brief
        if md_path:
            p = Path(md_path)
            if p.exists():
                lines = p.read_text(encoding="utf-8").split("\n")
                print(f"--- Brief preview (first {min(100, len(lines))} of {len(lines)} lines) ---")
                print()
                for line in lines[:100]:
                    print(line)
                if len(lines) > 100:
                    print(f"\n... ({len(lines) - 100} more lines)")
    else:
        print("NO BRIEF GENERATED (rejected or killed)")

    print_section("DONE")


if __name__ == "__main__":
    asyncio.run(main())
