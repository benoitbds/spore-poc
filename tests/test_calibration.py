"""Calibration test for Gate and Reviewer agents after prompt recalibration.

Tests:
1. Gate Agent: 20 domain pairs (mix of close, distant, good collisions)
2. Reviewer Agent: 20 most recent hypotheses from DB

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_calibration
"""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.gate import evaluate_collision
from agents.reviewer import review_hypothesis
from models.collision import Collision, CollisionPair
from models.domain import Domain
from storage import init_database, list_hypotheses


# ── Gate test pairs ──────────────────────────────────────────
# Mix: ~8 should be rejected, ~8 should pass, ~4 ambiguous
GATE_TEST_PAIRS = [
    # Should REJECT — too close / established field
    ("Biochemistry", "Pharmacology"),
    ("Machine Learning", "Statistics"),
    ("Bioinformatics", "Genomics"),
    ("Organic Chemistry", "Inorganic Chemistry"),
    ("Neuroscience", "Network Science"),
    ("Molecular Biology", "Genetics"),
    ("Astrophysics", "Cosmology"),
    ("Ecology", "Economics"),
    # Should PASS — genuine cross-domain potential
    ("Seismology", "Medical Imaging"),
    ("Catalytic Materials", "Surface Science"),
    ("Swarm Intelligence", "Traffic Engineering"),
    ("Crystallography", "Aperiodic Tilings"),
    ("Bacterial Immunology", "Gene Editing"),
    ("Origami Mathematics", "Deployable Structures"),
    ("Mycology", "Materials Science"),
    ("Electrochemistry", "Neurostimulation"),
    # Ambiguous — could go either way
    ("Fluid Dynamics", "Urban Planning"),
    ("Quantum Computing", "Drug Discovery"),
    ("Game Theory", "Evolutionary Biology"),
    ("Acoustics", "Viticulture"),
]


def _make_collision(domain_a_name: str, domain_b_name: str) -> Collision:
    """Create a minimal collision object for testing."""
    da = Domain(
        id=f"test-{domain_a_name.lower().replace(' ', '-')}",
        name=domain_a_name,
        key_concepts=[domain_a_name.lower()],
    )
    db = Domain(
        id=f"test-{domain_b_name.lower().replace(' ', '-')}",
        name=domain_b_name,
        key_concepts=[domain_b_name.lower()],
    )
    pair = CollisionPair(domain_a=da, domain_b=db, strategy="semantic_distance", distance_score=0.5)
    return Collision(pair=pair, context_a=[], context_b=[], enrichment_source="test")


async def test_gate():
    """Run Gate Agent on 20 domain pairs and show distribution."""
    print("=" * 70)
    print("  GATE AGENT CALIBRATION TEST (20 pairs)")
    print("=" * 70)
    print()

    results = []
    for domain_a, domain_b in GATE_TEST_PAIRS:
        collision = _make_collision(domain_a, domain_b)
        result = await evaluate_collision(collision)
        decision = "PASS" if result.plausible else "REJECT"
        results.append(decision)
        status = "PASS  " if result.plausible else "REJECT"
        print(f"  [{status}] {domain_a} x {domain_b}")
        print(f"           {result.reason[:100]}")

    counts = Counter(results)
    total = len(results)
    reject_pct = counts["REJECT"] / total * 100
    pass_pct = counts["PASS"] / total * 100

    print()
    print(f"  DISTRIBUTION:")
    print(f"    PASS:   {counts['PASS']:>2} / {total}  ({pass_pct:.0f}%)")
    print(f"    REJECT: {counts['REJECT']:>2} / {total}  ({reject_pct:.0f}%)")
    print()

    if 40 <= reject_pct <= 60:
        print(f"  STATUS: IN TARGET (40-60% reject)")
    elif reject_pct < 40:
        print(f"  STATUS: TOO LENIENT (target: 40-60% reject, got {reject_pct:.0f}%)")
    else:
        print(f"  STATUS: TOO STRICT (target: 40-60% reject, got {reject_pct:.0f}%)")

    return counts


async def test_reviewer():
    """Run Reviewer Agent on 20 most recent hypotheses and show distribution."""
    print()
    print("=" * 70)
    print("  REVIEWER AGENT CALIBRATION TEST (20 hypotheses)")
    print("=" * 70)
    print()

    await init_database()
    hypotheses = await list_hypotheses(limit=20)

    if not hypotheses:
        print("  No hypotheses in database. Skipping reviewer test.")
        return Counter()

    print(f"  Testing {len(hypotheses)} hypotheses from DB...")
    print()

    # Collect old verdicts for comparison
    old_verdicts = []
    new_verdicts = []

    for h in hypotheses:
        # Get old auto_feedback
        import aiosqlite
        from config import get_settings
        settings = get_settings()
        async with aiosqlite.connect(settings.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT auto_feedback_json FROM hypotheses WHERE id = ?", (h.id,)
            )
            row = await cursor.fetchone()
            old_verdict = "none"
            if row and row["auto_feedback_json"]:
                af = json.loads(row["auto_feedback_json"])
                old_verdict = af.get("verdict", "none")
            old_verdicts.append(old_verdict)

        # Run new reviewer
        feedback = await review_hypothesis(h)
        new_verdicts.append(feedback.verdict)

        doms = f"{h.collision.domain_a.name} x {h.collision.domain_b.name}"
        if len(doms) > 40:
            doms = doms[:37] + "..."
        score = f"{h.scores.composite:.3f}" if h.scores and h.scores.composite else "N/A"
        changed = " CHANGED" if old_verdict != feedback.verdict else ""
        print(f"  [{feedback.verdict:>12}] {doms:<42} score={score}  (was: {old_verdict}){changed}")

    # Distribution comparison
    old_counts = Counter(old_verdicts)
    new_counts = Counter(new_verdicts)
    total = len(hypotheses)

    print()
    print(f"  DISTRIBUTION COMPARISON (n={total}):")
    print()
    print(f"  {'Verdict':<15} {'OLD':>6} {'OLD%':>6} {'NEW':>6} {'NEW%':>6} {'Target':>8}")
    print(f"  {'-'*55}")
    for verdict in ["poubelle", "intéressant", "a_tester", "none", "interessant"]:
        old_n = old_counts.get(verdict, 0)
        new_n = new_counts.get(verdict, 0)
        if old_n == 0 and new_n == 0:
            continue
        old_p = old_n / total * 100
        new_p = new_n / total * 100
        target = {"poubelle": "25-30%", "intéressant": "50-55%", "interessant": "50-55%", "a_tester": "15-20%"}.get(verdict, "")
        print(f"  {verdict:<15} {old_n:>6} {old_p:>5.0f}% {new_n:>6} {new_p:>5.0f}% {target:>8}")

    print()
    poubelle_pct = new_counts.get("poubelle", 0) / total * 100
    a_tester_pct = new_counts.get("a_tester", 0) / total * 100
    interessant_pct = (new_counts.get("intéressant", 0) + new_counts.get("interessant", 0)) / total * 100

    if 20 <= poubelle_pct <= 35 and 10 <= a_tester_pct <= 25:
        print(f"  STATUS: WELL CALIBRATED")
    elif poubelle_pct < 15:
        print(f"  STATUS: NOT ENOUGH POUBELLE ({poubelle_pct:.0f}%, target 25-30%)")
    elif a_tester_pct > 30:
        print(f"  STATUS: TOO MANY A_TESTER ({a_tester_pct:.0f}%, target 15-20%)")
    else:
        print(f"  STATUS: poubelle={poubelle_pct:.0f}% int={interessant_pct:.0f}% fire={a_tester_pct:.0f}%")

    return new_counts


async def main():
    gate_counts = await test_gate()
    reviewer_counts = await test_reviewer()

    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    gate_reject = gate_counts.get("REJECT", 0)
    gate_total = sum(gate_counts.values())
    print(f"  Gate rejection rate: {gate_reject}/{gate_total} = {gate_reject/gate_total*100:.0f}%")

    rev_total = sum(reviewer_counts.values())
    if rev_total:
        rev_poub = reviewer_counts.get("poubelle", 0)
        rev_int = reviewer_counts.get("intéressant", 0) + reviewer_counts.get("interessant", 0)
        rev_fire = reviewer_counts.get("a_tester", 0)
        print(f"  Reviewer distribution: poubelle={rev_poub} int={rev_int} fire={rev_fire}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
