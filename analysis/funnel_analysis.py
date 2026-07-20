#!/usr/bin/env python3
"""SPORE content-quality diagnosis — read-only funnel & distribution analysis.

Reads a read-only / immutable copy of spore.db and prints the funnel,
score distributions, per-persona behaviour, temporal drift and grounding
health. Mutates NOTHING. Intended to back analysis/content-quality-diagnosis.md.

Usage:
    python analysis/funnel_analysis.py [path_to_db]
Default db: /tmp/spore_ro.db  (a copy; see report for provenance)
"""
from __future__ import annotations

import json
import sqlite3
import statistics as st
import sys
from collections import Counter, defaultdict

DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/spore_ro.db"
URI = f"file:{DB}?mode=ro&immutable=1"


def conn():
    return sqlite3.connect(URI, uri=True)


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def pct(parts):
    """quantile helper on a list"""
    s = sorted(parts)
    if not s:
        return {}
    n = len(s)

    def q(p):
        i = max(0, min(n - 1, int(round(p * (n - 1)))))
        return s[i]

    return {
        "n": n, "min": round(s[0], 2), "p25": round(q(0.25), 2),
        "median": round(q(0.5), 2), "p75": round(q(0.75), 2),
        "max": round(s[-1], 2), "mean": round(sum(s) / n, 2),
    }


c = conn()
c.row_factory = sqlite3.Row

# ─────────────────────────────────────────────────────────────────────────
hr("1. L0 FUNNEL — runs (collisions → bridge → curated hypotheses)")
rows = c.execute(
    "SELECT collisions_processed cp, hypotheses_generated hg, bridge_rate br, "
    "total_cost_usd cost, status FROM runs WHERE status='completed'"
).fetchall()
tot_coll = sum(r["cp"] or 0 for r in rows)
tot_hyp = sum(r["hg"] or 0 for r in rows)
tot_cost = sum(r["cost"] or 0 for r in rows)
print(f"completed runs        : {len(rows)}")
print(f"collisions processed  : {tot_coll}")
print(f"hypotheses generated  : {tot_hyp}  ({100*tot_hyp/max(tot_coll,1):.1f}% of collisions)")
print(f"total L0 cost (USD)    : {tot_cost:.2f}")
failed = c.execute("SELECT COUNT(*) n FROM runs WHERE status='failed'").fetchone()["n"]
running = c.execute("SELECT COUNT(*) n FROM runs WHERE status='running'").fetchone()["n"]
print(f"failed runs           : {failed}   running/stuck: {running}")

# ─────────────────────────────────────────────────────────────────────────
hr("2. HYPOTHESES table — status + critic composite distribution")
hyp = c.execute("SELECT id, generated_at, status, scores_json FROM hypotheses").fetchall()
print("status counts:", dict(Counter(h["status"] for h in hyp)))
crit = defaultdict(list)
for h in hyp:
    if not h["scores_json"]:
        continue
    try:
        s = json.loads(h["scores_json"])
    except Exception:
        continue
    for k in ("novelty", "coherence", "testability", "impact_potential",
              "hallucination_risk", "composite"):
        if k in s and isinstance(s[k], (int, float)):
            crit[k].append(float(s[k]))
for k, v in crit.items():
    print(f"  {k:18s}", pct(v))

# ─────────────────────────────────────────────────────────────────────────
hr("3. BRIEFS funnel (post-fire pipeline)")
briefs = c.execute("SELECT * FROM briefs").fetchall()
real = [b for b in briefs if not b["is_stub"]]
stub = [b for b in briefs if b["is_stub"]]
print(f"total briefs rows     : {len(briefs)}")
print(f"  real (is_stub=0)    : {len(real)}")
print(f"  stubs (is_stub=1)   : {len(stub)}  reasons:",
      dict(Counter(b["stub_reason"] for b in stub)))
print("status   :", dict(Counter(b["status"] for b in briefs)))
print("verdict  :", dict(Counter(b["panel_verdict"] for b in real)))
pub = [b for b in real if b["panel_verdict"] == "publish_brief"]
rej = [b for b in real if b["panel_verdict"] == "reject"]
print(f"publish rate among real briefs: {len(pub)}/{len(real)} = {100*len(pub)/max(len(real),1):.0f}%")

# ─────────────────────────────────────────────────────────────────────────
hr("4. CONSENSUS distribution + threshold sensitivity")
cons = [b["panel_consensus_score"] for b in real if b["panel_consensus_score"] is not None]
print("consensus:", pct(cons))
# histogram in 0.25 bins
bins = Counter()
for x in cons:
    bins[round(x * 4) / 4] += 1
print("histogram (0.25 bins):")
for k in sorted(bins):
    print(f"  {k:>4}: {'#'*bins[k]} ({bins[k]})")
for thr in (5.5, 6.0, 6.5, 7.0):
    n = sum(1 for x in cons if x >= thr)
    print(f"  >= {thr}: {n}/{len(cons)} ({100*n/len(cons):.0f}%)")
# How many sit in the 'dead zone' 6.5..7.0 — would publish now but NOT under old 7.0
dz = [x for x in cons if 6.5 <= x < 7.0]
print(f"  in [6.5,7.0) 'rescued by S8.4': {len(dz)}/{len(cons)} ({100*len(dz)/len(cons):.0f}%)")

# ─────────────────────────────────────────────────────────────────────────
hr("5. PER-PERSONA reviewer behaviour (from panel_data JSON)")
persona_scores = defaultdict(list)
persona_conf = defaultdict(list)
persona_verdict = defaultdict(Counter)
parse_fail = 0
zero_conf = 0
lowest_counter = Counter()  # which persona is the min in each brief
for b in real:
    if not b["panel_data"]:
        continue
    try:
        pd = json.loads(b["panel_data"])
    except Exception:
        parse_fail += 1
        continue
    reviews = pd.get("reviews", [])
    per_brief = {}
    for r in reviews:
        p = r.get("reviewer_persona", "?")
        s = r.get("overall_score")
        conf = r.get("confidence", 0)
        if conf == 0:
            zero_conf += 1
        if isinstance(s, (int, float)):
            persona_scores[p].append(float(s))
            per_brief[p] = float(s)
        persona_conf[p].append(float(conf))
        persona_verdict[p][r.get("verdict", "?")] += 1
    if per_brief:
        lo = min(per_brief, key=per_brief.get)
        lowest_counter[lo] += 1
print(f"{'persona':20s} {'mean':>5} {'med':>5} {'min':>5} {'max':>5} {'mean_conf':>9}")
for p in sorted(persona_scores, key=lambda x: st.mean(persona_scores[x])):
    sc = persona_scores[p]
    print(f"{p:20s} {st.mean(sc):5.2f} {st.median(sc):5.2f} {min(sc):5.1f} {max(sc):5.1f} "
          f"{st.mean(persona_conf[p]):9.2f}")
print("\nverdict mix per persona:")
for p in persona_verdict:
    print(f"  {p:20s}", dict(persona_verdict[p]))
print("\n'lowest score in the brief' belongs to:", dict(lowest_counter))
print(f"zero-confidence reviewer instances (silently dropped): {zero_conf}")
print(f"panel_data parse failures: {parse_fail}")

# ─────────────────────────────────────────────────────────────────────────
hr("6. GROUNDING health — evidence counts & novelty verdicts")
ev = [b["evidence_count"] for b in real if b["evidence_count"] is not None]
cev = [b["counter_evidence_count"] for b in real if b["counter_evidence_count"] is not None]
print("evidence_count       :", pct([float(x) for x in ev]))
print("counter_evidence_cnt :", pct([float(x) for x in cev]))
print("briefs with 0 evidence:", sum(1 for x in ev if x == 0), "/", len(ev))
print("novelty_verdict      :", dict(Counter(b["novelty_verdict"] for b in real)))
nov = [b["novelty_score"] for b in real if b["novelty_score"] is not None]
print("novelty_score        :", pct(nov))
# Semantic Scholar cache size as a proxy for API usage
ssc = c.execute("SELECT COUNT(*) n FROM semantic_scholar_cache").fetchone()["n"]
print("semantic_scholar_cache rows:", ssc)

# ─────────────────────────────────────────────────────────────────────────
hr("7. REVISION outcomes")
print("revision_count distribution:", dict(Counter(b["revision_count"] for b in real)))
for rc in sorted(set(b["revision_count"] for b in real if b["revision_count"] is not None)):
    grp = [b for b in real if b["revision_count"] == rc]
    pub_n = sum(1 for b in grp if b["panel_verdict"] == "publish_brief")
    cs = [b["panel_consensus_score"] for b in grp if b["panel_consensus_score"] is not None]
    print(f"  rev={rc}: n={len(grp)} publish={pub_n} consensus={pct(cs)}")

c.close()
print("\n[done] read-only analysis complete — no writes performed.")
