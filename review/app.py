"""SPORE Dashboard — main Streamlit application.

Multi-page interface centered on Research Briefs as the primary product.
"""

import streamlit as st

# Page config must be the first Streamlit call
st.set_page_config(
    page_title="SPORE",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "About": "SPORE — Système de Production d'Opportunités de Recherche par Exploration",
    },
)

from views import dashboard, briefs, hypotheses, digests, evolution, pilotage, admin


PAGES = {
    "📊 Dashboard": dashboard,
    "📄 Research Briefs": briefs,
    "🔬 Hypothèses": hypotheses,
    "📰 Digests": digests,
    "🧬 Évolution": evolution,
    "⚙️ Pilotage": pilotage,
    "🔧 Admin": admin,
}


def main():
    # Sidebar
    st.sidebar.markdown("# 🧬 SPORE")
    st.sidebar.caption("Système de Production d'Opportunités\nde Recherche par Exploration")
    st.sidebar.markdown("---")

    # If another page set `nav` in session_state (e.g., from a "Voir le brief" button),
    # honor it once and then clear it.
    nav_override = st.session_state.pop("nav", None)
    default_index = 0
    page_keys = list(PAGES.keys())
    if nav_override and nav_override in page_keys:
        default_index = page_keys.index(nav_override)

    selection = st.sidebar.radio(
        "Navigation",
        page_keys,
        index=default_index,
        label_visibility="collapsed",
    )

    # System status in sidebar
    st.sidebar.markdown("---")
    try:
        from helpers import get_system_status, get_status_emoji
        status = get_system_status()
        emoji = get_status_emoji(status["status"])
        st.sidebar.caption(f"**Statut:** {emoji} {status['status'].upper()}")
        if status.get("run_id") and status["status"] == "running":
            st.sidebar.caption(f"`{status['run_id']}`")
    except Exception:
        pass

    # Render selected page
    try:
        PAGES[selection].render()
    except Exception as e:
        st.error(f"Erreur dans la page '{selection}': {e}")
        import traceback
        with st.expander("Traceback"):
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
