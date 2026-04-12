"""Backfill French vulgarization for existing research briefs.

Reads each brief JSON from outputs/briefs/, runs the vulgarization agent,
patches the JSON on disk, and updates the SQLite briefs table.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.vulgarization import vulgarization_agent
from agents.hypothesis_sharpening import SharpeningOutput
from agents.experimental_protocol import ProtocolOutput
from agents.multi_reviewer_panel import PanelOutput
from storage import init_database
from storage.database import get_connection
from config import get_settings
from logging_config import get_logger

logger = get_logger("backfill_vulgarization")


async def backfill_one(json_path: Path, force: bool = False) -> bool:
    """Backfill a single brief JSON. Returns True on success."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  skip {json_path.name}: cannot parse ({exc})")
        return False

    brief_id = data.get("brief_id", json_path.stem)

    if not force and data.get("vulgarization_fr"):
        print(f"  skip {brief_id}: already has vulgarization_fr (use --force to redo)")
        return False

    sharpened_raw = data.get("sharpened") or {}
    protocol_raw = data.get("protocol") or {}
    panel_raw = data.get("panel") or {}
    grounding = data.get("grounding") or {}
    domains = data.get("domains") or []

    try:
        sharpened = SharpeningOutput(**{
            "title": sharpened_raw.get("title", ""),
            "formal_statement": sharpened_raw.get("formal_statement", ""),
            "independent_variables": sharpened_raw.get("independent_variables", []),
            "dependent_variables": sharpened_raw.get("dependent_variables", []),
            "proposed_mechanism": sharpened_raw.get("proposed_mechanism", {}),
            "falsifiable_predictions": sharpened_raw.get("falsifiable_predictions", []),
            "boundary_conditions": sharpened_raw.get("boundary_conditions", []),
            "theoretical_framework": sharpened_raw.get("theoretical_framework", ""),
        })
        protocol = ProtocolOutput(**{
            "protocol_title": protocol_raw.get("protocol_title", ""),
            "overall_timeline": protocol_raw.get("overall_timeline", ""),
            "overall_budget_estimate": protocol_raw.get("overall_budget_estimate", ""),
            "phases": protocol_raw.get("phases", []),
            "phase_1_quick_start": protocol_raw.get("phase_1_quick_start", {}),
        })
        panel = PanelOutput(
            reviews=panel_raw.get("reviews", []),
            meta_review=panel_raw.get("meta_review", {}),
        )
    except Exception as exc:
        print(f"  skip {brief_id}: cannot reconstruct outputs ({exc})")
        return False

    print(f"  vulgarizing {brief_id}...", flush=True)
    try:
        vulg = await vulgarization_agent(
            sharpened=sharpened,
            protocol=protocol,
            panel=panel,
            domains=domains,
            grounding=grounding,
        )
    except Exception as exc:
        print(f"  FAIL {brief_id}: {exc}")
        return False

    vulg_dict = dict(vulg)
    data["vulgarization_fr"] = vulg_dict
    json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        async with get_connection() as conn:
            await conn.execute(
                "UPDATE briefs SET vulgarization_data = ? WHERE id = ?",
                (json.dumps(vulg_dict, ensure_ascii=False), brief_id),
            )
            await conn.commit()
    except Exception as exc:
        print(f"  warn {brief_id}: DB update failed ({exc})")

    print(f"  done {brief_id} — title_fr: {vulg_dict.get('title_fr', '?')[:80]}")
    return True


async def main() -> None:
    force = "--force" in sys.argv
    settings = get_settings()
    briefs_dir = settings.output_dir / "briefs"
    if not briefs_dir.exists():
        print(f"briefs dir not found: {briefs_dir}")
        return

    await init_database()

    files = sorted(briefs_dir.glob("SPR-*.json"))
    print(f"found {len(files)} brief JSONs in {briefs_dir}")

    ok = 0
    fail = 0
    for i, p in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {p.name}")
        if await backfill_one(p, force=force):
            ok += 1
        else:
            fail += 1

    print(f"\ndone: {ok} updated, {fail} skipped/failed")


if __name__ == "__main__":
    asyncio.run(main())
