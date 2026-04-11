"""SPORE Dashboard - Main Application.

Multi-page Streamlit interface for piloting SPORE.
"""

import streamlit as st

# Page config must be first Streamlit command
st.set_page_config(
    page_title="SPORE Dashboard",
    page_icon="🍄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Import pages
from views import dashboard, launch_run, review, digests, evolution, configuration
from views import briefs, brief_detail


def main():
    # Sidebar navigation
    st.sidebar.title("🍄 SPORE")
    st.sidebar.caption("Système de Production d'Opportunités\nde Recherche par Exploration")
    st.sidebar.markdown("---")

    # Navigation
    pages = {
        "📊 Dashboard": dashboard,
        "🚀 Lancer un run": launch_run,
        "🔬 Review": review,
        "📄 Research Briefs": briefs,
        "📰 Digests": digests,
        "🧬 Évolution (L1)": evolution,
        "⚙️ Configuration": configuration,
    }

    # Check for page override (brief detail navigation)
    if st.session_state.get("page_override") == "brief_detail":
        selection = st.sidebar.radio(
            "Navigation",
            list(pages.keys()),
            label_visibility="collapsed",
        )
        # Render brief detail instead of selected page
        try:
            brief_detail.render()
        except Exception as e:
            st.error(f"Erreur: {e}")
            import traceback
            st.code(traceback.format_exc())
        return

    selection = st.sidebar.radio(
        "Navigation",
        list(pages.keys()),
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")

    # System status in sidebar
    from helpers import get_system_status, get_status_emoji

    status = get_system_status()
    status_emoji = get_status_emoji(status["status"])
    st.sidebar.markdown(f"**Statut:** {status_emoji} {status['status'].upper()}")

    if status["status"] == "running" and status.get("run_id"):
        st.sidebar.caption(f"Run: `{status['run_id']}`")

    # Render selected page
    try:
        pages[selection].render()
    except Exception as e:
        st.error(f"Erreur lors du rendu de la page '{selection}': {e}")
        import traceback
        st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
