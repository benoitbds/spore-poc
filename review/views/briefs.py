"""Research Briefs Page - List and filter generated briefs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from helpers import run_async
from storage import init_database, list_briefs
from storage.database import get_connection


async def _get_briefs(status_filter=None):
    """Load briefs from database, falling back to filesystem."""
    await init_database()
    briefs = await list_briefs(status=status_filter, limit=200)
    return briefs


async def _get_briefs_from_files():
    """Load briefs from JSON files on disk as fallback."""
    settings_mod = __import__("config", fromlist=["get_settings"])
    settings = settings_mod.get_settings()
    briefs_dir = settings.output_dir / "briefs"

    if not briefs_dir.exists():
        return []

    briefs = []
    for json_file in sorted(briefs_dir.glob("SPR-*.json"), reverse=True):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            md_path = json_file.with_suffix(".md")
            briefs.append({
                "id": data.get("brief_id", json_file.stem),
                "hypothesis_id": data.get("original_hypothesis", "")[:80],
                "status": "complete",
                "novelty_score": data.get("grounding", {}).get("novelty_assessment", {}).get("score"),
                "novelty_verdict": data.get("grounding", {}).get("novelty_assessment", {}).get("verdict"),
                "evidence_count": len(data.get("grounding", {}).get("evidence_base", [])),
                "panel_consensus_score": data.get("panel", {}).get("meta_review", {}).get("consensus_score"),
                "panel_verdict": data.get("panel", {}).get("meta_review", {}).get("verdict"),
                "formal_statement": data.get("sharpened", {}).get("formal_statement", ""),
                "created_at": data.get("generated_at", ""),
                "brief_md_path": str(md_path) if md_path.exists() else None,
                "brief_json_path": str(json_file),
                "domains": data.get("domains", []),
                "title": data.get("sharpened", {}).get("title", "Untitled"),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return briefs


def render():
    """Render the Research Briefs page."""
    st.title("Research Briefs")
    st.caption("Briefs de recherche generees par le pipeline post-fire")

    # Try DB first, fall back to filesystem
    db_briefs = run_async(_get_briefs())
    if db_briefs:
        briefs = db_briefs
    else:
        briefs = run_async(_get_briefs_from_files())

    if not briefs:
        st.info("Aucun brief genere pour le moment. Lancez le pipeline post-fire sur une hypothese fire.")
        return

    # Enrich file-based briefs with title if missing
    for b in briefs:
        if not b.get("title") and b.get("brief_json_path"):
            try:
                data = json.loads(Path(b["brief_json_path"]).read_text(encoding="utf-8"))
                b["title"] = data.get("sharpened", {}).get("title", "Untitled")
                b["domains"] = data.get("domains", [])
            except Exception:
                b["title"] = "Untitled"
                b["domains"] = []

    # Filters
    col1, col2 = st.columns(2)
    with col1:
        status_options = ["All"] + list({b.get("status", "?") for b in briefs})
        status_filter = st.selectbox("Statut", status_options)
    with col2:
        all_domains = set()
        for b in briefs:
            for d in (b.get("domains") or []):
                all_domains.add(d)
        domain_options = ["All"] + sorted(all_domains)
        domain_filter = st.selectbox("Domaine", domain_options)

    # Apply filters
    filtered = briefs
    if status_filter != "All":
        filtered = [b for b in filtered if b.get("status") == status_filter]
    if domain_filter != "All":
        filtered = [b for b in filtered if domain_filter in (b.get("domains") or [])]

    # Sort by consensus score
    filtered.sort(
        key=lambda b: b.get("panel_consensus_score") or 0,
        reverse=True,
    )

    # Display count
    st.markdown(f"**{len(filtered)}** briefs")

    # Table
    for b in filtered:
        brief_id = b.get("id", "?")
        title = b.get("title", "Untitled")
        domains = " x ".join(b.get("domains", []))
        novelty = b.get("novelty_score")
        panel_score = b.get("panel_consensus_score")
        verdict = b.get("panel_verdict", "?")
        created = b.get("created_at", "?")

        # Verdict styling
        verdict_colors = {
            "publish_brief": "green",
            "revise_and_resubmit": "orange",
            "reject": "red",
        }
        v_color = verdict_colors.get(verdict, "gray")

        with st.container():
            cols = st.columns([1, 3, 1.5, 1, 1, 1, 1])
            cols[0].markdown(f"`{brief_id}`")
            cols[1].markdown(f"**{title[:60]}**" + ("..." if len(title) > 60 else ""))
            cols[2].caption(domains)
            cols[3].metric("Novelty", f"{novelty:.2f}" if novelty else "?", label_visibility="collapsed")
            cols[4].metric("Panel", f"{panel_score:.1f}" if panel_score else "?", label_visibility="collapsed")
            cols[5].markdown(f":{v_color}[{verdict}]")
            if cols[6].button("Detail", key=f"btn_{brief_id}"):
                st.session_state["selected_brief_id"] = brief_id
                st.session_state["page_override"] = "brief_detail"
                st.rerun()

        st.divider()
