"""Hypothèses page — list with filters, verdict badges, feedback overrides."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from helpers import run_async, get_feedback_emoji
from storage import (
    init_database, list_hypotheses, list_briefs,
    update_hypothesis_feedback, get_hypothesis_auto_feedback,
)
from storage.database import get_connection
from models.hypothesis import HumanFeedback


VERDICT_STYLE = {
    "poubelle": ("🗑️", "red"),
    "intéressant": ("🤔", "orange"),
    "interessant": ("🤔", "orange"),
    "a_tester": ("🔥", "green"),
}


async def _load_hypotheses():
    await init_database()
    return await list_hypotheses(limit=500)


async def _load_brief_mapping() -> dict[str, str]:
    """Map hypothesis_id -> brief_id for all briefs."""
    await init_database()
    briefs = await list_briefs(limit=500)
    return {b["hypothesis_id"]: b["id"] for b in briefs if b.get("hypothesis_id")}


async def _load_auto_feedbacks() -> dict[str, dict]:
    """Load all auto-feedback JSON blobs keyed by hypothesis id."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT id, auto_feedback_json FROM hypotheses WHERE auto_feedback_json IS NOT NULL"
        )
        rows = await cursor.fetchall()
        result = {}
        for row in rows:
            try:
                result[row["id"]] = json.loads(row["auto_feedback_json"])
            except (json.JSONDecodeError, TypeError):
                continue
        return result


async def _set_feedback(hypothesis_id: str, feedback: HumanFeedback | None):
    await init_database()
    return await update_hypothesis_feedback(hypothesis_id, feedback)


def render():
    """Render the Hypothèses page."""
    st.title("🔬 Hypothèses")
    st.caption("Toutes les hypothèses générées par SPORE L0")

    hyps = run_async(_load_hypotheses())
    brief_map = run_async(_load_brief_mapping())
    auto_fbs = run_async(_load_auto_feedbacks())

    if not hyps:
        st.info("Aucune hypothèse en base. Lancez un run.")
        return

    # ── Filters ──────────────────────────────────────────────
    with st.expander("🔍 Filtres", expanded=False):
        f1, f2, f3, f4 = st.columns(4)

        all_verdicts = sorted({auto_fbs.get(h.id, {}).get("verdict", "—") for h in hyps})
        all_domains = sorted({h.collision.domain_a.name for h in hyps} |
                             {h.collision.domain_b.name for h in hyps})

        with f1:
            verdict_filter = st.multiselect("Verdict reviewer", all_verdicts)
        with f2:
            domain_filter = st.multiselect("Domaine", all_domains)
        with f3:
            min_score, max_score = st.slider(
                "Score composite", 0.0, 1.0, (0.0, 1.0), 0.05,
            )
        with f4:
            days_back = st.selectbox("Période", [7, 30, 90, 365, 9999], index=1,
                                     format_func=lambda d: f"{d} derniers jours" if d < 9999 else "Tout")

    # Apply filters
    cutoff = datetime.now() - timedelta(days=days_back)
    filtered = []
    for h in hyps:
        if h.generated_at < cutoff:
            continue
        if verdict_filter:
            v = auto_fbs.get(h.id, {}).get("verdict", "—")
            if v not in verdict_filter:
                continue
        if domain_filter:
            if (h.collision.domain_a.name not in domain_filter
                    and h.collision.domain_b.name not in domain_filter):
                continue
        if h.scores and h.scores.composite:
            if not (min_score <= h.scores.composite <= max_score):
                continue
        filtered.append(h)

    st.markdown(f"**{len(filtered)}** hypothèse(s)")
    st.markdown("---")

    # ── List ─────────────────────────────────────────────────
    for h in filtered:
        doms = f"{h.collision.domain_a.name} × {h.collision.domain_b.name}"
        bridge_short = h.bridge.summary[:100] + ("..." if len(h.bridge.summary) > 100 else "")
        score = h.scores.composite if h.scores and h.scores.composite else None

        auto_fb = auto_fbs.get(h.id, {})
        verdict = auto_fb.get("verdict", "—")
        v_icon, v_color = VERDICT_STYLE.get(verdict, ("⏳", "gray"))

        brief_id = brief_map.get(h.id)

        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([1.3, 3, 1, 1.2, 1.5])

            with c1:
                st.markdown(f"`{h.id[-12:]}`")
                st.caption(h.generated_at.strftime("%Y-%m-%d"))

            with c2:
                st.markdown(f"**{doms}**")
                st.caption(bridge_short)

            with c3:
                if score is not None:
                    color = "green" if score >= 0.5 else ("orange" if score >= 0.4 else "red")
                    st.markdown(f"**:{color}[{score:.3f}]**")
                    st.caption("composite")
                else:
                    st.caption("—")

            with c4:
                st.markdown(f":{v_color}[{v_icon} {verdict}]")
                if brief_id:
                    if st.button(f"📄 Brief", key=f"brf_{h.id}", use_container_width=True):
                        st.session_state["selected_brief_id"] = brief_id
                        st.session_state["nav"] = "📄 Research Briefs"
                        st.rerun()

            with c5:
                current_fb = h.human_feedback.value if h.human_feedback else None
                fb_icon = get_feedback_emoji(current_fb)
                st.caption(f"Feedback: {fb_icon}")

                fb_cols = st.columns(3)
                with fb_cols[0]:
                    if st.button("🗑️", key=f"t_{h.id}", help="Trash"):
                        run_async(_set_feedback(h.id, HumanFeedback.TRASH))
                        st.rerun()
                with fb_cols[1]:
                    if st.button("🤔", key=f"i_{h.id}", help="Interesting"):
                        run_async(_set_feedback(h.id, HumanFeedback.INTERESTING))
                        st.rerun()
                with fb_cols[2]:
                    if st.button("🔥", key=f"f_{h.id}", help="Want to test"):
                        run_async(_set_feedback(h.id, HumanFeedback.WANT_TO_TEST))
                        st.rerun()
