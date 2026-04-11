"""Test the Literature Grounding Agent on the self-repairing catalysts hypothesis.

Usage:
    cd /home/baq/Projects/spore-poc
    python -m tests.test_literature_grounding
"""

import asyncio
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.literature_grounding import literature_grounding_agent, GroundingInput


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


def _validate_references(output: dict) -> tuple[int, int]:
    """Validate that all references have a valid paper_id or DOI.

    Returns:
        (total_refs, valid_refs) counts.
    """
    total = 0
    valid = 0

    # Check evidence_base
    for paper in output.get("evidence_base", []):
        total += 1
        pid = paper.get("paper_id", "")
        doi = paper.get("doi")
        if pid or doi:
            valid += 1
        else:
            print(f"  [INVALID] Evidence paper missing both paper_id and DOI: {paper.get('title', '?')}")

    # Check counter_evidence
    for paper in output.get("counter_evidence", []):
        total += 1
        pid = paper.get("paper_id", "")
        doi = paper.get("doi")
        if pid or doi:
            valid += 1
        else:
            print(f"  [INVALID] Counter-evidence paper missing both paper_id and DOI: {paper.get('title', '?')}")

    # Check closest_existing_work
    novelty = output.get("novelty_assessment", {})
    for paper in novelty.get("closest_existing_work", []):
        total += 1
        pid = paper.get("paper_id", "")
        doi = paper.get("doi")
        if pid or doi:
            valid += 1
        else:
            print(f"  [INVALID] Closest work missing both paper_id and DOI: {paper.get('title', '?')}")

    return total, valid


async def main() -> None:
    print("=" * 70)
    print("SPORE Literature Grounding Agent — Test")
    print("=" * 70)
    print()
    print(f"Hypothesis: {TEST_HYPOTHESIS['hypothesis'][:100]}...")
    print(f"Domains: {TEST_HYPOTHESIS['domains']}")
    print()

    # Run the agent
    print("Running Literature Grounding Agent...")
    print("-" * 70)
    output = await literature_grounding_agent(TEST_HYPOTHESIS)
    print("-" * 70)
    print()

    # Display results
    novelty = output["novelty_assessment"]
    print("NOVELTY ASSESSMENT")
    print(f"  Score: {novelty.get('score', '?')}")
    print(f"  Verdict: {novelty.get('verdict', '?')}")
    print()

    closest = novelty.get("closest_existing_work", [])
    if closest:
        print(f"  Closest existing work ({len(closest)} papers):")
        for i, w in enumerate(closest, 1):
            doi_str = f" DOI:{w.get('doi')}" if w.get("doi") else ""
            print(f"    {i}. [{w.get('year', '?')}] {w.get('title', '?')}{doi_str}")
            print(f"       Similarity: {w.get('similarity', '?')} | Difference: {w.get('key_difference', '?')}")
    print()

    # Top 5 evidence papers
    evidence = output["evidence_base"]
    print(f"EVIDENCE BASE ({len(evidence)} papers)")
    for i, paper in enumerate(evidence[:5], 1):
        doi_str = f" DOI:{paper.get('doi')}" if paper.get("doi") else ""
        print(f"  {i}. [{paper.get('year', '?')}] {paper.get('title', '?')}{doi_str}")
        print(f"     Type: {paper.get('support_type', '?')} | Citations: {paper.get('citation_count', '?')}")
        print(f"     Relevance: {paper.get('relevance', '?')}")
    print()

    # Counter evidence
    counter = output["counter_evidence"]
    if counter:
        print(f"COUNTER EVIDENCE ({len(counter)} papers)")
        for i, paper in enumerate(counter, 1):
            print(f"  {i}. [{paper.get('severity', '?')}] {paper.get('title', '?')}")
            print(f"     Finding: {paper.get('finding', '?')}")
        print()

    # Gap manifest update
    gaps = output["gap_manifest_update"]
    print("GAP MANIFEST UPDATE")
    print(f"  Closed gaps: {gaps.get('closed_gaps', [])}")
    print(f"  New gaps: {gaps.get('new_gaps', [])}")
    print(f"  Data available: {gaps.get('data_available', [])}")
    print()

    # Kill reason
    if output["kill_reason"]:
        print(f"KILLED: {output['kill_reason']}")
    else:
        print("STATUS: Hypothesis ALIVE - proceeding to next pipeline stage")
    print()

    # Search queries used
    print(f"SEARCH QUERIES ({len(output['search_queries'])} queries)")
    for q in output["search_queries"]:
        print(f"  [{q.get('type', '?')}] {q.get('query', '?')}")
    print()

    # Reference validation
    print("REFERENCE VALIDATION")
    total_refs, valid_refs = _validate_references(output)
    print(f"  Total references cited: {total_refs}")
    print(f"  Valid (with paper_id or DOI): {valid_refs}")
    hallucinated = total_refs - valid_refs
    if hallucinated == 0:
        print("  PASS: Zero hallucinated references")
    else:
        print(f"  FAIL: {hallucinated} hallucinated references detected!")
    print()

    # Total papers found
    print(f"TOTAL UNIQUE PAPERS FOUND: {len(output['all_papers'])}")
    print()

    # Summary
    print("=" * 70)
    verdict_emoji = {
        "novel": "NEW",
        "incremental": "~",
        "already_explored": "OLD",
        "already_proven": "DEAD",
    }
    v = novelty.get("verdict", "?")
    print(f"SUMMARY: [{verdict_emoji.get(v, '?')}] novelty={novelty.get('score', '?')}, "
          f"evidence={len(evidence)}, counter={len(counter)}, "
          f"kill={'YES' if output['kill_reason'] else 'NO'}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
