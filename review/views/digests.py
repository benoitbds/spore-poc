"""Digests page — daily digests with links to generated briefs."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from helpers import run_async
from storage import init_database, list_briefs


DIGESTS_DIR = Path(__file__).parent.parent.parent / "outputs" / "digests"


async def _load_brief_mapping():
    """Map hypothesis_id -> brief_id."""
    await init_database()
    briefs = await list_briefs(limit=500)
    return {b["hypothesis_id"]: b["id"] for b in briefs if b.get("hypothesis_id")}


def _enrich_with_brief_links(content: str, brief_map: dict[str, str]) -> str:
    """If a hypothesis ID in the digest has a brief, append a link note."""
    if not brief_map:
        return content

    pattern = re.compile(r"(SPORE-\d{4}-\d{2}-\d{2}-[0-9a-f]{8})")

    def replacer(match):
        hid = match.group(1)
        if hid in brief_map:
            return f"{hid} 📄 [brief `{brief_map[hid]}`]"
        return hid

    return pattern.sub(replacer, content)


def render():
    """Render the Digests page."""
    st.title("📰 Digests")
    st.caption("Digests quotidiens des hypothèses générées")

    if not DIGESTS_DIR.exists():
        st.info(f"Aucun digest trouvé dans {DIGESTS_DIR}.")
        return

    files = sorted(DIGESTS_DIR.glob("digest_*.md"), reverse=True)
    if not files:
        st.info("Aucun digest généré pour le moment.")
        return

    brief_map = run_async(_load_brief_mapping())

    options = {f.stem.replace("digest_", ""): f for f in files}
    selected_label = st.selectbox("Sélectionner un digest", list(options.keys()))

    if not selected_label:
        return

    selected = options[selected_label]
    content = selected.read_text(encoding="utf-8")
    enriched = _enrich_with_brief_links(content, brief_map)

    dc1, dc2 = st.columns([3, 1])
    with dc1:
        st.caption(f"Fichier: `{selected.name}` — {len(content.splitlines())} lignes")
    with dc2:
        st.download_button(
            "📥 Télécharger",
            data=content,
            file_name=selected.name,
            mime="text/markdown",
            use_container_width=True,
        )

    st.markdown("---")
    st.markdown(enriched)
