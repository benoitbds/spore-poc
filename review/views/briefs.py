"""Research Briefs page — list with filters and inline detail view."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from helpers import run_async
from storage import init_database, list_briefs, get_brief, update_brief
from components.kpi_card import verdict_badge, score_color, status_icon
from components.reviewer_panel import reviewer_panel
from components.protocol_timeline import protocol_timeline


# ── Data ─────────────────────────────────────────────────────

async def _load_briefs():
    await init_database()
    return await list_briefs(limit=500)


async def _fetch_brief(brief_id: str):
    await init_database()
    return await get_brief(brief_id)


async def _update_brief_status(brief_id: str, status: str):
    await init_database()
    return await update_brief(brief_id, status=status)


def _load_brief_files(brief_row: dict) -> tuple[str | None, dict | None, list[str]]:
    """Load markdown + JSON + extract domains/title from disk."""
    md_content = None
    json_data = None

    md_path = brief_row.get("brief_md_path")
    json_path = brief_row.get("brief_json_path")

    if md_path and Path(md_path).exists():
        md_content = Path(md_path).read_text(encoding="utf-8")
    if json_path and Path(json_path).exists():
        json_data = json.loads(Path(json_path).read_text(encoding="utf-8"))

    domains = json_data.get("domains", []) if json_data else []
    return md_content, json_data, domains


def _enrich_briefs(briefs: list[dict]) -> list[dict]:
    """Enrich brief rows with title and domains from JSON files."""
    enriched = []
    for b in briefs:
        title = b["id"]
        domains = []
        json_path = b.get("brief_json_path")
        if json_path and Path(json_path).exists():
            try:
                data = json.loads(Path(json_path).read_text(encoding="utf-8"))
                title = data.get("sharpened", {}).get("title", b["id"])
                domains = data.get("domains", [])
            except (json.JSONDecodeError, OSError):
                pass
        b["_title"] = title
        b["_domains"] = domains
        enriched.append(b)
    return enriched


# ── Detail view ──────────────────────────────────────────────

def _render_detail(brief_id: str):
    """Render the full detail view for one brief."""
    brief_row = run_async(_fetch_brief(brief_id))
    if not brief_row:
        st.error(f"Brief {brief_id} introuvable.")
        return

    md_content, json_data, domains = _load_brief_files(brief_row)

    # ── Section 1: Header ──────────────────────────────────
    title = brief_id
    if json_data:
        title = json_data.get("sharpened", {}).get("title", brief_id)

    st.markdown(f"## {title}")
    st.caption(f"`{brief_id}` · généré le {brief_row.get('created_at', '?')[:10]}")

    # Badges row
    badge_cols = st.columns([2, 1, 1, 1])
    with badge_cols[0]:
        if domains:
            st.markdown(f"**🧪 Domaines:** {' × '.join(domains)}")
    with badge_cols[1]:
        nov = brief_row.get("novelty_score")
        if nov is not None:
            color = score_color(nov, (0.4, 0.7))
            st.markdown(f"**✨ Novelty:** :{color}[{nov:.2f}]")
    with badge_cols[2]:
        panel = brief_row.get("panel_consensus_score")
        if panel is not None:
            color = "green" if panel >= 7 else ("orange" if panel >= 5 else "red")
            st.markdown(f"**📊 Panel:** :{color}[{panel:.1f}/10]")
    with badge_cols[3]:
        verdict = brief_row.get("panel_verdict", "?")
        st.markdown(f"**Verdict:** {verdict_badge(verdict)}")

    # Human Override buttons
    st.markdown("### Human Override")
    ov_cols = st.columns([1, 1, 1, 2])
    current_status = brief_row.get("status", "complete")
    with ov_cols[0]:
        if st.button("✅ Approve", use_container_width=True, key=f"app_{brief_id}"):
            run_async(_update_brief_status(brief_id, "approved"))
            st.success("Approuvé!")
            st.rerun()
    with ov_cols[1]:
        if st.button("❌ Reject", use_container_width=True, key=f"rej_{brief_id}"):
            run_async(_update_brief_status(brief_id, "rejected"))
            st.warning("Rejeté.")
            st.rerun()
    with ov_cols[2]:
        if st.button("🔍 Flag", use_container_width=True, key=f"flg_{brief_id}"):
            run_async(_update_brief_status(brief_id, "flagged"))
            st.info("Marqué pour review.")
            st.rerun()
    with ov_cols[3]:
        st.caption(f"Statut actuel: **{status_icon(current_status)} {current_status}**")

    # Download buttons
    dl_cols = st.columns(2)
    if md_content:
        with dl_cols[0]:
            st.download_button(
                "📥 Télécharger Markdown",
                data=md_content,
                file_name=f"{brief_id}.md",
                mime="text/markdown",
                use_container_width=True,
                key=f"dl_md_{brief_id}",
            )
    if json_data:
        with dl_cols[1]:
            st.download_button(
                "📥 Télécharger JSON",
                data=json.dumps(json_data, indent=2, ensure_ascii=False),
                file_name=f"{brief_id}.json",
                mime="application/json",
                use_container_width=True,
                key=f"dl_json_{brief_id}",
            )

    st.markdown("---")

    # ── Section 2: Panel Review ────────────────────────────
    if json_data:
        panel = json_data.get("panel", {})
        reviews = panel.get("reviews", [])
        meta = panel.get("meta_review", {})
        if reviews:
            st.subheader("🎭 Panel Review")
            reviewer_panel(reviews, meta)
            st.markdown("---")

    # ── Section 3: Protocol Timeline ───────────────────────
    if json_data:
        protocol = json_data.get("protocol", {})
        if protocol:
            st.subheader("📋 Protocole expérimental")
            protocol_timeline(protocol)
            st.markdown("---")

    # ── Section 4: Full brief markdown ─────────────────────
    st.subheader("📄 Brief complet")
    if md_content:
        with st.expander("Afficher le brief markdown complet", expanded=False):
            st.markdown(md_content, unsafe_allow_html=False)
    elif json_data:
        st.json(json_data)
    else:
        st.warning("Aucun contenu de brief disponible.")


# ── List view ────────────────────────────────────────────────

def render():
    """Render the Research Briefs page (list + optional detail)."""
    st.title("📄 Research Briefs")
    st.caption("Briefs de recherche générés par le pipeline post-🔥")

    briefs = run_async(_load_briefs())
    if not briefs:
        st.info("Aucun brief généré pour le moment. Lancez le pipeline post-fire sur une hypothèse 🔥.")
        return

    briefs = _enrich_briefs(briefs)

    # ── Filters ──────────────────────────────────────────────
    with st.expander("🔍 Filtres", expanded=False):
        f1, f2, f3, f4 = st.columns(4)

        all_domains = set()
        for b in briefs:
            for d in b.get("_domains", []):
                all_domains.add(d)

        with f1:
            selected_domains = st.multiselect("Domaines", sorted(all_domains))
        with f2:
            min_score = st.slider("Score panel min", 0.0, 10.0, 0.0, 0.5)
        with f3:
            all_verdicts = sorted({b.get("panel_verdict") for b in briefs if b.get("panel_verdict")})
            selected_verdicts = st.multiselect("Verdicts", all_verdicts)
        with f4:
            all_statuses = sorted({b.get("status") or "complete" for b in briefs})
            selected_statuses = st.multiselect("Statut humain", all_statuses)

    # Apply filters
    filtered = briefs
    if selected_domains:
        filtered = [b for b in filtered if set(b.get("_domains", [])) & set(selected_domains)]
    if min_score > 0:
        filtered = [b for b in filtered if (b.get("panel_consensus_score") or 0) >= min_score]
    if selected_verdicts:
        filtered = [b for b in filtered if b.get("panel_verdict") in selected_verdicts]
    if selected_statuses:
        filtered = [b for b in filtered if (b.get("status") or "complete") in selected_statuses]

    # Sort by date desc
    filtered.sort(key=lambda b: b.get("created_at") or "", reverse=True)

    st.markdown(f"**{len(filtered)}** brief(s)")

    # ── Table ────────────────────────────────────────────────
    rows = []
    for b in filtered:
        title = b.get("_title", b["id"])
        if len(title) > 60:
            title = title[:57] + "..."
        domains = " × ".join(b.get("_domains", [])[:2])
        nov = b.get("novelty_score")
        panel = b.get("panel_consensus_score")
        verdict = b.get("panel_verdict", "?")
        status = b.get("status") or "complete"
        created = (b.get("created_at") or "")[:10]
        rows.append({
            "ID": b["id"],
            "Titre": title,
            "Domaines": domains,
            "Novelty": f"{nov:.2f}" if nov is not None else "—",
            "Panel": f"{panel:.1f}" if panel is not None else "—",
            "Verdict": verdict,
            "Date": created,
            "Statut": f"{status_icon(status)} {status}",
        })

    df = pd.DataFrame(rows)

    # Display as selectable list
    selected_id = st.session_state.get("selected_brief_id")

    for b in filtered:
        brief_id = b["id"]
        title = b.get("_title", brief_id)
        if len(title) > 60:
            title = title[:57] + "..."
        domains = " × ".join(b.get("_domains", [])[:2])
        nov = b.get("novelty_score")
        panel = b.get("panel_consensus_score")
        verdict = b.get("panel_verdict", "?")
        status = b.get("status") or "complete"

        is_selected = (brief_id == selected_id)
        marker = "▶ " if is_selected else ""

        with st.container():
            cols = st.columns([0.2, 2.2, 1.2, 0.7, 0.7, 1.2, 0.7, 0.8])
            cols[0].markdown(f"`{brief_id[-4:]}`")
            cols[1].markdown(f"{marker}**{title}**")
            cols[2].caption(domains if domains else "—")
            if nov is not None:
                color = score_color(nov, (0.4, 0.7))
                cols[3].markdown(f":{color}[{nov:.2f}]")
            else:
                cols[3].caption("—")
            if panel is not None:
                p_color = "green" if panel >= 7 else ("orange" if panel >= 5 else "red")
                cols[4].markdown(f":{p_color}[{panel:.1f}]")
            else:
                cols[4].caption("—")
            cols[5].markdown(verdict_badge(verdict))
            cols[6].caption(f"{status_icon(status)}")
            if cols[7].button("Détail" if not is_selected else "Masquer", key=f"btn_{brief_id}"):
                if is_selected:
                    st.session_state.pop("selected_brief_id", None)
                else:
                    st.session_state["selected_brief_id"] = brief_id
                st.rerun()

    # ── Inline detail view ──────────────────────────────────
    if selected_id:
        st.markdown("---")
        _render_detail(selected_id)
