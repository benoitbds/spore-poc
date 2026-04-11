"""Brief Detail Page - Full brief rendering with panel scores and actions."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from helpers import run_async
from storage import init_database, get_brief, update_brief


def _load_brief_data(brief_id: str) -> tuple[dict | None, str | None, dict | None]:
    """Load brief data from DB and filesystem.

    Returns:
        Tuple of (db_row, markdown_content, json_data).
    """
    # Try DB first
    db_row = run_async(_get_brief_from_db(brief_id))

    # Load files
    settings_mod = __import__("config", fromlist=["get_settings"])
    settings = settings_mod.get_settings()
    briefs_dir = settings.output_dir / "briefs"

    md_path = briefs_dir / f"{brief_id}.md"
    json_path = briefs_dir / f"{brief_id}.json"

    md_content = None
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")
    elif db_row and db_row.get("brief_md_path"):
        p = Path(db_row["brief_md_path"])
        if p.exists():
            md_content = p.read_text(encoding="utf-8")

    json_data = None
    if json_path.exists():
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
    elif db_row and db_row.get("brief_json_path"):
        p = Path(db_row["brief_json_path"])
        if p.exists():
            json_data = json.loads(p.read_text(encoding="utf-8"))

    return db_row, md_content, json_data


async def _get_brief_from_db(brief_id: str):
    await init_database()
    return await get_brief(brief_id)


async def _update_brief_status(brief_id: str, status: str):
    await init_database()
    return await update_brief(brief_id, status=status)


def render():
    """Render the Brief Detail page."""
    brief_id = st.session_state.get("selected_brief_id")

    if not brief_id:
        st.warning("Aucun brief selectionne. Retournez a la page Research Briefs.")
        if st.button("Retour aux briefs"):
            st.session_state.pop("page_override", None)
            st.rerun()
        return

    db_row, md_content, json_data = _load_brief_data(brief_id)

    if not md_content and not json_data:
        st.error(f"Brief {brief_id} introuvable sur le disque.")
        return

    # Header
    title = "Untitled"
    if json_data:
        title = json_data.get("sharpened", {}).get("title", brief_id)
    st.title(title)
    st.caption(f"SPORE ID: {brief_id}")

    # Back button
    if st.button("< Retour aux briefs"):
        st.session_state.pop("page_override", None)
        st.session_state.pop("selected_brief_id", None)
        st.rerun()

    st.markdown("---")

    # Panel scores table
    if json_data:
        panel = json_data.get("panel", {})
        reviews = panel.get("reviews", [])
        meta = panel.get("meta_review", {})

        if reviews:
            st.subheader("Panel Review")

            # Scores as columns
            cols = st.columns(len(reviews) + 1)

            for i, r in enumerate(reviews):
                persona = r.get("reviewer_persona", "?").replace("_", " ").title()
                score = r.get("overall_score", 0)
                verdict = r.get("verdict", "?")
                confidence = r.get("confidence", 0)

                # Color by score
                if score >= 7:
                    color = "green"
                elif score >= 5:
                    color = "orange"
                else:
                    color = "red"

                with cols[i]:
                    st.markdown(f"**{persona}**")
                    st.markdown(f"### :{color}[{score:.1f}]")
                    st.caption(f"{verdict} (conf: {confidence:.0%})")

            # Meta-reviewer column
            with cols[-1]:
                consensus = meta.get("consensus_score", 0)
                c_color = "green" if consensus >= 7 else ("orange" if consensus >= 5 else "red")
                st.markdown("**Consensus**")
                st.markdown(f"### :{c_color}[{consensus:.1f}]")
                st.caption(meta.get("verdict", "?"))

            st.markdown("---")

    # Human Override
    st.subheader("Human Override")
    col1, col2, col3 = st.columns(3)
    current_status = db_row.get("status", "complete") if db_row else "complete"
    st.caption(f"Statut actuel: **{current_status}**")

    with col1:
        if st.button("Approve", type="primary", use_container_width=True):
            run_async(_update_brief_status(brief_id, "approved"))
            st.success("Brief approuve!")
            st.rerun()
    with col2:
        if st.button("Reject", type="secondary", use_container_width=True):
            run_async(_update_brief_status(brief_id, "rejected"))
            st.warning("Brief rejete.")
            st.rerun()
    with col3:
        if st.button("Flag for Review", use_container_width=True):
            run_async(_update_brief_status(brief_id, "flagged"))
            st.info("Brief marque pour review.")
            st.rerun()

    st.markdown("---")

    # Download buttons
    st.subheader("Telechargements")
    dl_col1, dl_col2 = st.columns(2)

    if md_content:
        with dl_col1:
            st.download_button(
                "Telecharger Markdown",
                data=md_content,
                file_name=f"{brief_id}.md",
                mime="text/markdown",
                use_container_width=True,
            )

    if json_data:
        with dl_col2:
            st.download_button(
                "Telecharger JSON",
                data=json.dumps(json_data, indent=2, ensure_ascii=False),
                file_name=f"{brief_id}.json",
                mime="application/json",
                use_container_width=True,
            )

    st.markdown("---")

    # Full brief markdown rendering
    st.subheader("Brief complet")

    if md_content:
        st.markdown(md_content, unsafe_allow_html=False)
    elif json_data:
        st.json(json_data)
