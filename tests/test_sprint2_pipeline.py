"""Sprint 2 end-to-end test: Grounding → Sharpening → Protocol.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_sprint2_pipeline
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.literature_grounding import literature_grounding_agent, GroundingInput
from agents.hypothesis_sharpening import hypothesis_sharpening_agent, SharpeningInput
from agents.experimental_protocol import experimental_protocol_agent


TEST_HYPOTHESIS = GroundingInput(
    hypothesis=(
        "Self-repairing catalysts via oscillatory surface reconstruction: "
        "Catalysts that automatically repair themselves through oscillatory "
        "surface reconstruction, where periodic surface restructuring driven "
        "by reaction conditions continuously regenerates active sites"
    ),
    domains=["Catalytic Materials", "Surface Science"],
    mechanisms=(
        "Periodic surface restructuring driven by reaction conditions "
        "continuously regenerates active sites. The oscillatory reconstruction "
        "is self-sustaining: the catalytic reaction itself provides the driving "
        "force for surface rearrangement, creating a feedback loop where "
        "degraded sites are preferentially restructured."
    ),
    keywords=[
        "self-repairing catalyst",
        "oscillatory surface reconstruction",
        "active site regeneration",
        "surface restructuring",
        "catalyst deactivation",
    ],
    gap_manifest={
        "data_gaps": [
            {"domain": "Surface Science", "description": "No in-situ data on oscillatory reconstruction at catalytic temperatures"}
        ],
        "competence_gaps": [],
        "epistemic_gaps": [
            {"zone": "feedback mechanism", "signal": "unclear how reaction drives reconstruction"}
        ],
    },
)


def print_section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)
    print()


async def main() -> None:
    print_section("SPORE Sprint 2 — Grounding → Sharpening → Protocol")

    # ── Step 1: Literature Grounding ─────────────────────────
    print_section("STEP 1 / 3 — Literature Grounding")
    grounding = await literature_grounding_agent(TEST_HYPOTHESIS)

    novelty = grounding["novelty_assessment"]
    print(f"Novelty: {novelty.get('score', '?')} — {novelty.get('verdict', '?')}")
    print(f"Evidence papers: {len(grounding['evidence_base'])}")
    print(f"Counter-evidence: {len(grounding['counter_evidence'])}")

    if grounding["kill_reason"]:
        print(f"\nKILLED: {grounding['kill_reason']}")
        print("Pipeline stopped.")
        return

    print("Hypothesis ALIVE — continuing to sharpening.")

    # ── Step 2: Hypothesis Sharpening ────────────────────────
    print_section("STEP 2 / 3 — Hypothesis Sharpening")
    sharpening_input = SharpeningInput(
        hypothesis=TEST_HYPOTHESIS["hypothesis"],
        domains=TEST_HYPOTHESIS["domains"],
        mechanisms=TEST_HYPOTHESIS["mechanisms"],
        novelty_assessment=grounding["novelty_assessment"],
        evidence_base=grounding["evidence_base"],
        counter_evidence=grounding["counter_evidence"],
    )
    sharpened = await hypothesis_sharpening_agent(sharpening_input)

    print(f"Title: {sharpened['title']}")
    print(f"\nFormal statement:\n  {sharpened['formal_statement']}")

    print(f"\nIndependent variables ({len(sharpened['independent_variables'])}):")
    for v in sharpened["independent_variables"]:
        print(f"  - {v.get('name', '?')} [{v.get('type', '?')}]: {v.get('range', '?')} {v.get('unit', '')}")

    print(f"\nDependent variables ({len(sharpened['dependent_variables'])}):")
    for v in sharpened["dependent_variables"]:
        print(f"  - {v.get('name', '?')} [{v.get('type', '?')}]: expected {v.get('expected_direction', '?')} ({v.get('unit', '')})")

    mechanism = sharpened["proposed_mechanism"]
    print(f"\nCausal chain ({len(mechanism.get('causal_chain', []))} steps):")
    for step in mechanism.get("causal_chain", []):
        print(f"  {step}")

    print(f"\nKey assumptions ({len(mechanism.get('key_assumptions', []))}):")
    for a in mechanism.get("key_assumptions", []):
        print(f"  - {a}")

    print(f"\nKnown unknowns ({len(mechanism.get('known_unknowns', []))}):")
    for u in mechanism.get("known_unknowns", []):
        print(f"  - {u}")

    print(f"\nFalsifiable predictions ({len(sharpened['falsifiable_predictions'])}):")
    for i, p in enumerate(sharpened["falsifiable_predictions"], 1):
        print(f"  {i}. {p.get('prediction', '?')}")
        print(f"     Bound: {p.get('quantitative_bound', '?')}")
        print(f"     Method: {p.get('measurement_method', '?')}")
        print(f"     H0: {p.get('null_hypothesis', '?')}")
        print(f"     Test: {p.get('statistical_test', '?')}")

    print(f"\nBoundary conditions ({len(sharpened['boundary_conditions'])}):")
    for c in sharpened["boundary_conditions"]:
        print(f"  - {c.get('condition', '?')}")
        print(f"    Justification: {c.get('justification', '')}")

    print(f"\nTheoretical framework: {sharpened['theoretical_framework']}")

    # ── Step 3: Experimental Protocol ────────────────────────
    print_section("STEP 3 / 3 — Experimental Protocol")
    protocol = await experimental_protocol_agent(sharpened, grounding["evidence_base"])

    print(f"Protocol: {protocol['protocol_title']}")
    print(f"Timeline: {protocol['overall_timeline']}")
    print(f"Budget: {protocol['overall_budget_estimate']}")

    for phase in protocol["phases"]:
        pn = phase.get("phase_number", "?")
        print(f"\n--- Phase {pn}: {phase.get('phase_name', '?')} ---")
        print(f"  Objective: {phase.get('objective', '?')}")

        res = phase.get("required_resources", {})
        print(f"  Cost: {res.get('estimated_cost', '?')} | Duration: {res.get('estimated_duration', '?')}")

        if res.get("equipment"):
            print(f"  Equipment: {', '.join(res['equipment'][:5])}")
        if res.get("software"):
            print(f"  Software: {', '.join(res['software'][:5])}")

        print(f"  Outputs: {len(phase.get('expected_outputs', []))} deliverables")

        criteria = phase.get("success_criteria", [])
        if criteria:
            print(f"  Success criteria ({len(criteria)}):")
            for c in criteria[:3]:
                print(f"    - {c.get('metric', '?')}: {c.get('threshold', '?')}")

        gonogo = phase.get("go_nogo_decision", {})
        print(f"  GO if: {gonogo.get('go_if', '?')}")
        print(f"  NO-GO if: {gonogo.get('nogo_if', '?')}")
        print(f"  PIVOT if: {gonogo.get('pivot_if', '?')}")

        risks = phase.get("risks", [])
        if risks:
            print(f"  Risks ({len(risks)}):")
            for r in risks[:3]:
                print(f"    [{r.get('probability', '?')}] {r.get('risk', '?')}")

    qs = protocol["phase_1_quick_start"]
    print(f"\n--- Quick Start ---")
    print(f"  Can start today: {qs.get('can_start_today', '?')}")
    print(f"  First action: {qs.get('first_action', '?')}")
    if qs.get("tools_needed"):
        print(f"  Tools: {', '.join(qs['tools_needed'])}")
    if qs.get("open_data_sources"):
        print(f"  Open data: {', '.join(qs['open_data_sources'])}")

    # ── Summary ──────────────────────────────────────────────
    print_section("PIPELINE SUMMARY")
    print(f"Novelty: {novelty.get('score', '?')} ({novelty.get('verdict', '?')})")
    print(f"Title: {sharpened['title']}")
    print(f"Predictions: {len(sharpened['falsifiable_predictions'])}")
    print(f"Protocol phases: {len(protocol['phases'])}")
    print(f"Timeline: {protocol['overall_timeline']}")
    print(f"Budget: {protocol['overall_budget_estimate']}")
    print(f"Can start today: {qs.get('can_start_today', '?')}")
    print(f"First action: {qs.get('first_action', '?')}")


if __name__ == "__main__":
    asyncio.run(main())
