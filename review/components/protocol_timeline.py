"""Visual timeline of the 3-phase experimental protocol."""

from typing import Any

import streamlit as st


PHASE_COLORS = {1: "#10B981", 2: "#F59E0B", 3: "#EF4444"}
PHASE_ICONS = {1: "💻", 2: "🧪", 3: "🏗️"}


def protocol_timeline(protocol: dict[str, Any]) -> None:
    """Render the 3-phase protocol as a horizontal timeline.

    Args:
        protocol: Protocol dict with phases and phase_1_quick_start.
    """
    phases = protocol.get("phases", [])
    if not phases:
        st.info("Aucun protocole disponible.")
        return

    # Overall info
    timeline = protocol.get("overall_timeline", "?")
    budget = protocol.get("overall_budget_estimate", "?")
    st.caption(f"**Timeline globale:** {timeline} · **Budget:** {budget}")

    # 3 phase columns
    cols = st.columns(len(phases))
    for i, phase in enumerate(phases[:3]):
        pn = phase.get("phase_number", i + 1)
        name = phase.get("phase_name", f"Phase {pn}")
        icon = PHASE_ICONS.get(pn, "📋")
        res = phase.get("required_resources", {})
        cost = res.get("estimated_cost", "?")
        duration = res.get("estimated_duration", "?")
        gonogo = phase.get("go_nogo_decision", {})
        go_if = gonogo.get("go_if", "")
        nogo_if = gonogo.get("nogo_if", "")

        with cols[i]:
            st.markdown(f"### {icon} Phase {pn}")
            st.markdown(f"**{name}**")
            st.caption(f"⏱️ {duration}")
            st.caption(f"💰 {cost}")
            st.markdown("**Objectif:**")
            st.markdown(f"<small>{phase.get('objective', '')[:200]}</small>", unsafe_allow_html=True)
            if go_if:
                st.markdown("**GO si:**")
                st.markdown(f"<small>:green[{go_if[:180]}]</small>", unsafe_allow_html=True)
            if nogo_if:
                st.markdown("**NO-GO si:**")
                st.markdown(f"<small>:red[{nogo_if[:180]}]</small>", unsafe_allow_html=True)

    # Quick start
    qs = protocol.get("phase_1_quick_start", {})
    if qs:
        st.markdown("---")
        can_start = qs.get("can_start_today", False)
        icon = "🚀" if can_start else "⏸️"
        st.markdown(f"### {icon} Quick Start")
        if can_start:
            st.success(f"**Première action :** {qs.get('first_action', '?')}")
        else:
            st.info(qs.get("first_action", "Pas d'action immédiate possible."))
        tools = qs.get("tools_needed", [])
        data_sources = qs.get("open_data_sources", [])
        if tools:
            st.caption(f"**Outils :** {', '.join(tools)}")
        if data_sources:
            st.caption(f"**Données ouvertes :** {', '.join(data_sources)}")
