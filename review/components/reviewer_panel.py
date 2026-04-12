"""Visual panel showing 5 reviewers with scores and verdicts."""

from typing import Any

import streamlit as st


PERSONA_ICONS = {
    "methodologist": "🔬",
    "domain_expert": "🧪",
    "contrarian": "😈",
    "industrialist": "🏭",
    "funding_strategist": "💰",
}

PERSONA_LABELS = {
    "methodologist": "Méthodologue",
    "domain_expert": "Domain Expert",
    "contrarian": "Contrarian",
    "industrialist": "Industriel",
    "funding_strategist": "Funding",
}


def _score_color(score: float) -> str:
    if score >= 7:
        return "green"
    if score >= 5:
        return "orange"
    return "red"


def reviewer_panel(reviews: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    """Render the 5-reviewer panel as columns.

    Args:
        reviews: List of reviewer dicts.
        meta: Meta-reviewer dict.
    """
    if not reviews:
        st.info("Aucune review disponible.")
        return

    # 5 reviewer columns
    cols = st.columns(5)
    for i, r in enumerate(reviews[:5]):
        persona = r.get("reviewer_persona", "?")
        score = r.get("overall_score", 0)
        verdict = r.get("verdict", "?")
        confidence = r.get("confidence", 0)
        icon = PERSONA_ICONS.get(persona, "👤")
        label = PERSONA_LABELS.get(persona, persona.replace("_", " ").title())
        color = _score_color(score)

        key_point = ""
        if r.get("strengths"):
            key_point = r["strengths"][0][:90]
        elif r.get("recommendation"):
            key_point = r["recommendation"][:90]

        with cols[i]:
            st.markdown(f"### {icon} {label}")
            st.markdown(f"## :{color}[{score:.1f}]/10")
            st.caption(f"**{verdict}** · conf {confidence:.0%}")
            if key_point:
                st.caption(key_point)

    st.markdown("---")

    # Meta-reviewer summary
    consensus = meta.get("consensus_score", 0)
    c_color = _score_color(consensus)
    m_col1, m_col2 = st.columns([1, 3])
    with m_col1:
        st.markdown("### Consensus")
        st.markdown(f"# :{c_color}[{consensus:.1f}]/10")
        v = meta.get("verdict", "?")
        st.markdown(f"**{v}**")
    with m_col2:
        consensus_points = meta.get("key_consensus", [])
        if consensus_points:
            st.markdown("**Consensus :**")
            for p in consensus_points[:3]:
                st.markdown(f"- {p}")
        disagreements = meta.get("key_disagreements", [])
        if disagreements:
            st.markdown("**Désaccords :**")
            for d in disagreements[:2]:
                st.markdown(f"- {d}")
        critical = meta.get("critical_path")
        if critical:
            st.markdown("**Critical path :**")
            st.markdown(f"> {critical}")
