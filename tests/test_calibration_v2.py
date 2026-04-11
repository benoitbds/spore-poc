"""Calibration v2: Gate retest + Reviewer on synthetic bad hypotheses.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_calibration_v2
"""

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.gate import evaluate_collision
from agents.reviewer import review_hypothesis
from models.collision import Collision, CollisionPair
from models.domain import Domain
from models.hypothesis import (
    Hypothesis, Bridge, BridgeType, Scores, Prediction, HypothesisStatus,
)
from models.gap_manifest import GapManifest, DataGap, GapCriticality


# ── Gate test pairs (same 20) ───────────────────────────────
GATE_TEST_PAIRS = [
    # Should REJECT
    ("Biochemistry", "Pharmacology"),
    ("Machine Learning", "Statistics"),
    ("Bioinformatics", "Genomics"),
    ("Organic Chemistry", "Inorganic Chemistry"),
    ("Neuroscience", "Network Science"),
    ("Molecular Biology", "Genetics"),
    ("Astrophysics", "Cosmology"),
    ("Ecology", "Economics"),
    # Should PASS
    ("Seismology", "Medical Imaging"),
    ("Catalytic Materials", "Surface Science"),
    ("Swarm Intelligence", "Traffic Engineering"),
    ("Crystallography", "Aperiodic Tilings"),
    ("Bacterial Immunology", "Gene Editing"),
    ("Origami Mathematics", "Deployable Structures"),
    ("Mycology", "Materials Science"),
    ("Electrochemistry", "Neurostimulation"),
    # Ambiguous
    ("Fluid Dynamics", "Urban Planning"),
    ("Quantum Computing", "Drug Discovery"),
    ("Game Theory", "Evolutionary Biology"),
    ("Acoustics", "Viticulture"),
]


def _make_collision(a: str, b: str) -> Collision:
    da = Domain(id=f"t-{a[:8]}", name=a, key_concepts=[a.lower()])
    db = Domain(id=f"t-{b[:8]}", name=b, key_concepts=[b.lower()])
    pair = CollisionPair(domain_a=da, domain_b=db, strategy="semantic_distance", distance_score=0.5)
    return Collision(pair=pair, context_a=[], context_b=[], enrichment_source="test")


# ── 5 synthetic BAD hypotheses for reviewer test ─────────────
def _make_bad_hypotheses() -> list[Hypothesis]:
    """Create 5 intentionally bad hypotheses."""
    bad = []

    # 1. Lexical analogy, no mechanism
    bad.append(Hypothesis(
        id="BAD-001-lexical",
        generated_at=datetime.now(),
        genome_version="test",
        collision=CollisionPair(
            domain_a=Domain(id="t1", name="Neuroscience", key_concepts=["neural networks"]),
            domain_b=Domain(id="t2", name="Telecommunications", key_concepts=["network protocols"]),
            strategy="semantic_distance", distance_score=0.3,
        ),
        bridge=Bridge(
            summary="Neural networks in the brain can inspire better telecommunications network protocols",
            mechanism="Both domains use 'networks' so principles from one can transfer to the other",
            type=BridgeType.STRUCTURAL_ANALOGY,
        ),
        scores=Scores(novelty=0.2, coherence=0.3, testability=0.2, impact_potential=0.3, hallucination_risk=0.6, composite=0.22),
        predictions=[
            Prediction(statement="This could improve network performance", metric="performance", expected_range="better"),
        ],
        kill_condition="If networks don't improve",
        next_steps=["Study networks"],
        relevant_labs=[], relevant_datasets=[], sources_used=[],
        gap_manifest=GapManifest(data_gaps=[
            DataGap(domain="both", description="No data on cross-domain transfer", criticality=GapCriticality.CRITICAL, recurrence=1),
        ]),
        status=HypothesisStatus.CURATED,
    ))

    # 2. Vague mechanism, no quantitative predictions
    bad.append(Hypothesis(
        id="BAD-002-vague",
        generated_at=datetime.now(),
        genome_version="test",
        collision=CollisionPair(
            domain_a=Domain(id="t3", name="Psychology", key_concepts=["behavior"]),
            domain_b=Domain(id="t4", name="Meteorology", key_concepts=["weather patterns"]),
            strategy="semantic_distance", distance_score=0.7,
        ),
        bridge=Bridge(
            summary="Weather patterns might influence human psychological states in ways that could be modeled",
            mechanism="Complex systems in weather and psychology share some kind of emergent behavior patterns",
            type=BridgeType.CONCEPTUAL_REFRAME,
        ),
        scores=Scores(novelty=0.4, coherence=0.2, testability=0.15, impact_potential=0.3, hallucination_risk=0.5, composite=0.28),
        predictions=[
            Prediction(statement="Weather might affect mood in predictable ways", metric="mood score", expected_range="some effect"),
        ],
        kill_condition="If no correlation found",
        next_steps=["Collect data"],
        relevant_labs=[], relevant_datasets=[], sources_used=[],
        gap_manifest=GapManifest(data_gaps=[
            DataGap(domain="Psychology", description="No standardized mood dataset linked to weather", criticality=GapCriticality.HIGH, recurrence=1),
            DataGap(domain="Meteorology", description="Local weather data hard to correlate", criticality=GapCriticality.HIGH, recurrence=1),
            DataGap(domain="both", description="Confounders not identified", criticality=GapCriticality.CRITICAL, recurrence=1),
        ]),
        status=HypothesisStatus.CURATED,
    ))

    # 3. Domains too close, gate should have rejected
    bad.append(Hypothesis(
        id="BAD-003-tooclose",
        generated_at=datetime.now(),
        genome_version="test",
        collision=CollisionPair(
            domain_a=Domain(id="t5", name="Organic Chemistry", key_concepts=["synthesis"]),
            domain_b=Domain(id="t6", name="Medicinal Chemistry", key_concepts=["drug design"]),
            strategy="semantic_distance", distance_score=0.2,
        ),
        bridge=Bridge(
            summary="Organic synthesis methods can be used to make better drugs",
            mechanism="Applying standard organic reactions to pharmaceutical synthesis",
            type=BridgeType.METHODOLOGICAL_TRANSFER,
        ),
        scores=Scores(novelty=0.15, coherence=0.7, testability=0.6, impact_potential=0.4, hallucination_risk=0.2, composite=0.38),
        predictions=[
            Prediction(statement="New synthetic route yields 30% more drug compound", metric="yield %", expected_range="25-35%"),
        ],
        kill_condition="If yield < 20%",
        next_steps=["Synthesize compound"],
        relevant_labs=[], relevant_datasets=[], sources_used=[],
        gap_manifest=GapManifest(),
        status=HypothesisStatus.CURATED,
    ))

    # 4. Mechanism violates known principles
    bad.append(Hypothesis(
        id="BAD-004-violation",
        generated_at=datetime.now(),
        genome_version="test",
        collision=CollisionPair(
            domain_a=Domain(id="t7", name="Thermodynamics", key_concepts=["entropy"]),
            domain_b=Domain(id="t8", name="Information Theory", key_concepts=["entropy"]),
            strategy="semantic_distance", distance_score=0.4,
        ),
        bridge=Bridge(
            summary="Reducing information entropy in data systems could reduce thermodynamic entropy in physical systems",
            mechanism="Since both use the concept of entropy, organizing data could spontaneously organize matter",
            type=BridgeType.CONCEPTUAL_REFRAME,
        ),
        scores=Scores(novelty=0.5, coherence=0.1, testability=0.1, impact_potential=0.6, hallucination_risk=0.8, composite=0.18),
        predictions=[
            Prediction(statement="Compressing data will cool the storage medium", metric="temperature drop", expected_range="1-5°C"),
        ],
        kill_condition="If no temperature change",
        next_steps=["Measure temperature"],
        relevant_labs=[], relevant_datasets=[], sources_used=[],
        gap_manifest=GapManifest(),
        status=HypothesisStatus.CURATED,
    ))

    # 5. Impact grossly exaggerated
    bad.append(Hypothesis(
        id="BAD-005-exaggerated",
        generated_at=datetime.now(),
        genome_version="test",
        collision=CollisionPair(
            domain_a=Domain(id="t9", name="Phytochemistry", key_concepts=["plant compounds"]),
            domain_b=Domain(id="t10", name="Oncology", key_concepts=["cancer treatment"]),
            strategy="semantic_distance", distance_score=0.5,
        ),
        bridge=Bridge(
            summary="A common plant compound could cure all forms of cancer",
            mechanism="Plant polyphenols have antioxidant properties that might inhibit tumor growth somehow",
            type=BridgeType.CAUSAL_TRANSFER,
        ),
        scores=Scores(novelty=0.3, coherence=0.3, testability=0.4, impact_potential=0.9, hallucination_risk=0.7, composite=0.32),
        predictions=[
            Prediction(statement="Polyphenol extract reduces tumor size", metric="tumor volume", expected_range="significant reduction"),
        ],
        kill_condition="If no effect on tumors",
        next_steps=["Test on cell lines"],
        relevant_labs=[], relevant_datasets=[], sources_used=[],
        gap_manifest=GapManifest(data_gaps=[
            DataGap(domain="Oncology", description="No clinical evidence for this compound", criticality=GapCriticality.CRITICAL, recurrence=1),
        ]),
        status=HypothesisStatus.CURATED,
    ))

    return bad


async def test_gate():
    """Gate retest on 20 pairs."""
    print("=" * 70)
    print("  GATE v2 CALIBRATION (20 pairs)")
    print("=" * 70)
    print()

    results = []
    for a, b in GATE_TEST_PAIRS:
        collision = _make_collision(a, b)
        result = await evaluate_collision(collision)
        decision = "PASS" if result.plausible else "REJECT"
        results.append((a, b, decision, result.reason))
        tag = "PASS  " if result.plausible else "REJECT"
        print(f"  [{tag}] {a} x {b}")
        print(f"           {result.reason[:100]}")

    counts = Counter(d for _, _, d, _ in results)
    total = len(results)
    rej = counts["REJECT"] / total * 100

    print()
    print(f"  PASS:   {counts['PASS']:>2}/{total}  ({counts['PASS']/total*100:.0f}%)")
    print(f"  REJECT: {counts['REJECT']:>2}/{total}  ({rej:.0f}%)")
    print()

    # Check expected results
    expected_reject = set(GATE_TEST_PAIRS[:8])  # first 8 should reject
    expected_pass = set(GATE_TEST_PAIRS[8:16])  # next 8 should pass
    correct = 0
    for a, b, decision, _ in results:
        if (a, b) in expected_reject and decision == "REJECT":
            correct += 1
        elif (a, b) in expected_pass and decision == "PASS":
            correct += 1
    print(f"  Accuracy on expected pairs (16 non-ambiguous): {correct}/16")

    if 40 <= rej <= 60:
        print(f"  STATUS: IN TARGET")
    elif rej < 40:
        print(f"  STATUS: TOO LENIENT ({rej:.0f}%)")
    else:
        print(f"  STATUS: TOO STRICT ({rej:.0f}%)")

    return counts


async def test_reviewer_bad():
    """Reviewer on 5 synthetic bad hypotheses."""
    print()
    print("=" * 70)
    print("  REVIEWER — SYNTHETIC BAD HYPOTHESES (5)")
    print("=" * 70)
    print()

    bad_hyps = _make_bad_hypotheses()
    verdicts = []

    for h in bad_hyps:
        feedback = await review_hypothesis(h)
        verdicts.append(feedback.verdict)
        doms = f"{h.collision.domain_a.name} x {h.collision.domain_b.name}"
        score = f"{h.scores.composite:.3f}" if h.scores else "N/A"
        print(f"  [{feedback.verdict:>12}] {h.id:<22} {doms:<40} score={score}")
        print(f"               Bridge: {h.bridge.summary[:80]}")
        print(f"               Comment: {feedback.comment[:100]}")
        print()

    counts = Counter(verdicts)
    poub = counts.get("poubelle", 0)
    print(f"  DISTRIBUTION: poubelle={poub} intéressant={counts.get('intéressant', 0)+counts.get('interessant', 0)} a_tester={counts.get('a_tester', 0)}")
    print()

    if poub >= 3:
        print(f"  STATUS: GOOD — {poub}/5 bad hypotheses correctly identified as poubelle")
    elif poub >= 1:
        print(f"  STATUS: PARTIAL — only {poub}/5 bad hypotheses identified as poubelle")
    else:
        print(f"  STATUS: FAIL — 0/5 bad hypotheses put in poubelle")

    return counts


async def main():
    gate = await test_gate()
    rev = await test_reviewer_bad()

    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    total_g = sum(gate.values())
    print(f"  Gate: {gate.get('REJECT',0)}/{total_g} rejected ({gate.get('REJECT',0)/total_g*100:.0f}%)")
    print(f"  Reviewer bad hyps: {rev.get('poubelle',0)}/5 correctly poubelle")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
