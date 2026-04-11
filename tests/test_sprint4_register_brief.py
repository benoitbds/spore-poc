"""Register the existing brief SPR-2026-7626 into the database and test the Streamlit pages.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_sprint4_register_brief
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage import init_database, save_brief, list_briefs, get_brief


async def main():
    await init_database()

    # Load the existing brief JSON
    brief_path = Path(__file__).parent.parent / "outputs" / "briefs" / "SPR-2026-7626.json"
    if not brief_path.exists():
        print(f"Brief file not found: {brief_path}")
        return

    data = json.loads(brief_path.read_text(encoding="utf-8"))
    brief_id = data["brief_id"]
    md_path = brief_path.with_suffix(".md")

    print(f"Registering brief {brief_id} in database...")

    await save_brief(
        brief_id=brief_id,
        hypothesis_id="test-catalyst-hypothesis",
        grounding_data=data.get("grounding"),
        sharpened_data=data.get("sharpened"),
        protocol_data=data.get("protocol"),
        panel_data=data.get("panel"),
        status="complete",
        brief_md_path=str(md_path),
        brief_json_path=str(brief_path),
    )

    print("Brief registered successfully!")

    # Verify it's in the DB
    brief = await get_brief(brief_id)
    if brief:
        print(f"\nVerification:")
        print(f"  ID: {brief['id']}")
        print(f"  Status: {brief['status']}")
        print(f"  Novelty: {brief['novelty_score']}")
        print(f"  Verdict: {brief['novelty_verdict']}")
        print(f"  Panel score: {brief['panel_consensus_score']}")
        print(f"  Panel verdict: {brief['panel_verdict']}")
        print(f"  Evidence: {brief['evidence_count']}")
        print(f"  Predictions: {brief['prediction_count']}")
        print(f"  MD path: {brief['brief_md_path']}")
    else:
        print("ERROR: Brief not found in DB after save!")

    # List all briefs
    all_briefs = await list_briefs()
    print(f"\nTotal briefs in DB: {len(all_briefs)}")
    for b in all_briefs:
        print(f"  {b['id']} | score={b['panel_consensus_score']} | {b['panel_verdict']}")


if __name__ == "__main__":
    asyncio.run(main())
