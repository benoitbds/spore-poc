"""Évolution page — L1 mutation timeline, current genome, observation reports."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st


PROJECT_ROOT = Path(__file__).parent.parent.parent
GENOME_PATH = PROJECT_ROOT / "data" / "l0_genome.yaml"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


MUTATION_COLORS = {
    "score_weight_adjustment": "#3B82F6",       # blue
    "parameter_change": "#10B981",              # green
    "prompt_modification": "#8B5CF6",           # violet
}

MUTATION_ICONS = {
    "score_weight_adjustment": "⚖️",
    "parameter_change": "🔧",
    "prompt_modification": "📝",
}


def _parse_mutations_from_git(limit: int = 20) -> list[dict]:
    """Extract L1 mutation commits from git log."""
    try:
        result = subprocess.run(
            ["git", "log", f"-n{limit}", "--grep=^[[]L1[]]", "--format=%H|%ai|%s"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []

    mutations = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        sha, date, subject = parts
        # Subject format: "[L1] <type>: <target_path>"
        m_subject = subject.strip()
        if not m_subject.startswith("[L1]"):
            continue
        rest = m_subject[4:].strip()
        if ":" in rest:
            mtype, target = rest.split(":", 1)
            mtype = mtype.strip()
            target = target.strip()
        else:
            mtype, target = rest, ""
        mutations.append({
            "sha": sha[:8],
            "date": date[:16],
            "type": mtype,
            "target": target,
            "subject": subject,
        })
    return mutations


def _latest_observation_report() -> dict | None:
    """Find the latest observation_L1-*.json in outputs/."""
    if not OUTPUTS_DIR.exists():
        return None
    files = sorted(OUTPUTS_DIR.glob("observation_L1-*.json"), reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def render():
    """Render the Évolution page."""
    st.title("🧬 Évolution (L1)")
    st.caption("Mutations du génome L0 par le pipeline L1")

    # ── Section 1: Mutation timeline ────────────────────────
    st.subheader("Dernières mutations")
    mutations = _parse_mutations_from_git(limit=20)

    if not mutations:
        st.info("Aucune mutation L1 trouvée dans l'historique git.")
    else:
        for m in mutations:
            color = MUTATION_COLORS.get(m["type"], "#6B7280")
            icon = MUTATION_ICONS.get(m["type"], "🔀")
            with st.container(border=True):
                c1, c2, c3 = st.columns([1, 4, 1])
                with c1:
                    st.markdown(f"### {icon}")
                    st.caption(m["date"][:10])
                with c2:
                    st.markdown(f"**{m['type']}**")
                    st.caption(f"Cible: `{m['target']}`")
                with c3:
                    st.caption(f"`{m['sha']}`")

    st.markdown("---")

    # ── Section 2: Current genome ───────────────────────────
    st.subheader("Génome L0 actuel")
    if GENOME_PATH.exists():
        content = GENOME_PATH.read_text(encoding="utf-8")
        st.code(content, language="yaml")
    else:
        st.warning(f"Génome introuvable: {GENOME_PATH}")

    st.markdown("---")

    # ── Section 3: Last observation report ──────────────────
    st.subheader("Dernière observation L1")
    obs = _latest_observation_report()
    if not obs:
        st.info("Aucun rapport d'observation disponible.")
        return

    # Metrics
    oc1, oc2, oc3, oc4 = st.columns(4)
    with oc1:
        br = obs.get("bridge_rate", 0)
        st.metric("Bridge rate", f"{br*100:.1f}%" if br else "N/A")
    with oc2:
        n_hyp = obs.get("total_hypotheses", 0)
        st.metric("Hypothèses analysées", n_hyp)
    with oc3:
        n_coll = obs.get("total_collisions", 0)
        st.metric("Collisions", n_coll)
    with oc4:
        cost = obs.get("total_cost_usd", 0)
        st.metric("Coût", f"${cost:.2f}")

    # Issues / Opportunities
    ic1, ic2 = st.columns(2)
    with ic1:
        issues = obs.get("issues", [])
        if issues:
            st.markdown("**⚠️ Issues détectées:**")
            for issue in issues:
                st.markdown(f"- {issue}")
    with ic2:
        opps = obs.get("opportunities", [])
        if opps:
            st.markdown("**💡 Opportunités:**")
            for opp in opps:
                st.markdown(f"- {opp}")

    # Feedback distribution
    dist = obs.get("feedback_distribution", {})
    if dist:
        st.markdown("**Distribution du feedback humain:**")
        dc = st.columns(len(dist))
        for i, (fb, count) in enumerate(dist.items()):
            with dc[i]:
                st.metric(fb, count)
