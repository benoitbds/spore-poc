"""Export SPORE global stats to a JSON file consumed by the public Next.js site."""

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "spore.db"
OUT_PATH = Path("/home/baq/Projects/spore-web/data/stats.json")


def main() -> None:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def q(sql, params=()):
        return conn.execute(sql, params).fetchall()

    # Core counts
    total_hypotheses = q("SELECT COUNT(*) as n FROM hypotheses")[0]["n"]
    total_curated = q(
        "SELECT COUNT(*) as n FROM hypotheses WHERE status IN ('curated','human_reviewed','validated')"
    )[0]["n"]
    total_briefs = q("SELECT COUNT(*) as n FROM briefs WHERE status != 'rejected'")[0]["n"]

    fire_count = q(
        "SELECT COUNT(*) as n FROM hypotheses WHERE json_extract(auto_feedback_json, '$.verdict') = 'a_tester'"
    )[0]["n"]

    # Total collisions ever processed
    total_collisions = q(
        "SELECT COALESCE(SUM(collisions_processed), 0) as n FROM runs WHERE status = 'completed'"
    )[0]["n"]

    # Novelty average across briefs
    avg_novelty_row = q(
        "SELECT AVG(novelty_score) as avg FROM briefs "
        "WHERE novelty_score IS NOT NULL AND status != 'rejected'"
    )
    avg_novelty = avg_novelty_row[0]["avg"] if avg_novelty_row[0]["avg"] else None

    # Panel consensus average
    avg_panel_row = q(
        "SELECT AVG(panel_consensus_score) as avg FROM briefs "
        "WHERE panel_consensus_score IS NOT NULL AND status != 'rejected'"
    )
    avg_panel = avg_panel_row[0]["avg"] if avg_panel_row[0]["avg"] else None

    # Cost cumul
    total_cost_row = q(
        "SELECT COALESCE(SUM(total_cost_usd), 0) as total FROM runs WHERE status = 'completed'"
    )
    total_cost = total_cost_row[0]["total"]

    # 30-day activity
    end = datetime.now().date()
    start = end - timedelta(days=30)

    runs_30d = q(
        """SELECT DATE(started_at) as day, SUM(collisions_processed) as n
           FROM runs WHERE status='completed' AND started_at >= ?
           GROUP BY DATE(started_at) ORDER BY day""",
        (start.isoformat(),),
    )
    briefs_30d = q(
        """SELECT DATE(created_at) as day, COUNT(*) as n
           FROM briefs WHERE created_at >= ?
           GROUP BY DATE(created_at) ORDER BY day""",
        (start.isoformat(),),
    )

    # Reviewer verdict distribution
    rows = q(
        """SELECT json_extract(auto_feedback_json, '$.verdict') as v
           FROM hypotheses WHERE auto_feedback_json IS NOT NULL"""
    )
    verdict_dist = Counter(r["v"] for r in rows if r["v"])

    # Top domains (most frequent across briefs)
    # Domains are in the JSON files — read them from disk
    briefs_dir = PROJECT_ROOT / "outputs" / "briefs"
    domain_counter: Counter[str] = Counter()
    if briefs_dir.exists():
        for jp in briefs_dir.glob("*.json"):
            try:
                data = json.loads(jp.read_text(encoding="utf-8"))
                for d in data.get("domains", []):
                    domain_counter[d] += 1
            except (json.JSONDecodeError, OSError):
                continue

    stats = {
        "generated_at": datetime.now().isoformat(),
        "totals": {
            "collisions": total_collisions,
            "hypotheses": total_hypotheses,
            "curated": total_curated,
            "fire_hypotheses": fire_count,
            "briefs": total_briefs,
        },
        "quality": {
            "avg_novelty_score": round(avg_novelty, 3) if avg_novelty else None,
            "avg_panel_consensus": round(avg_panel, 2) if avg_panel else None,
            "fire_rate": round(fire_count / total_hypotheses, 3) if total_hypotheses else 0,
        },
        "cost": {
            "total_usd": round(total_cost, 4),
            "per_brief_usd": round(total_cost / total_briefs, 4) if total_briefs else None,
        },
        "activity_30d": {
            "collisions_by_day": [
                {"day": r["day"], "count": r["n"] or 0} for r in runs_30d
            ],
            "briefs_by_day": [
                {"day": r["day"], "count": r["n"]} for r in briefs_30d
            ],
        },
        "reviewer_distribution": dict(verdict_dist),
        "top_domains": [
            {"domain": d, "count": n} for d, n in domain_counter.most_common(15)
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Stats written to {OUT_PATH}")
    print(json.dumps(stats["totals"], indent=2))


if __name__ == "__main__":
    main()
