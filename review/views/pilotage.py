"""Pilotage page — launch runs, manual post-fire, configuration display."""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from helpers import run_async, launch_run_background, get_system_status
from storage import init_database, list_hypotheses, list_briefs
from storage.database import get_connection


PROJECT_ROOT = Path(__file__).parent.parent.parent
CONSTITUTION_PATH = PROJECT_ROOT / "data" / "constitution.yaml"
ENV_PATH = PROJECT_ROOT / ".env"
DOMAINS_DIR = PROJECT_ROOT / "data" / "domains"
PROGRESS_FILE = PROJECT_ROOT / ".spore_progress.json"

STAGE_LABELS = {
    "gate": "🚪 Gate",
    "synthesis": "🧬 Synthesis",
    "critic": "⚔️ Critics",
    "curator": "🏆 Curator",
    "impact": "💥 Impact",
    "reviewer": "👀 Reviewer",
}


def _get_domain_options() -> list[str]:
    """Get list of available domain files."""
    if not DOMAINS_DIR.exists():
        return ["all_science", "materials_science"]
    return sorted(p.stem for p in DOMAINS_DIR.glob("*.json"))


def _get_crontab() -> str:
    """Get current crontab for user."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout if result.returncode == 0 else "(pas de crontab)"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "(crontab indisponible)"


def _get_env_keys() -> list[tuple[str, bool]]:
    """Return (key, is_set) tuples for env vars, without values."""
    if not ENV_PATH.exists():
        return []
    result = []
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            result.append((key.strip(), bool(value.strip())))
    return result


def _read_progress() -> dict | None:
    """Read the live progress JSON; returns None if missing or unreadable."""
    if not PROGRESS_FILE.exists():
        return None
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _fmt_duration(seconds: float) -> str:
    """Format seconds as mm:ss or h:mm:ss."""
    s = int(max(0, seconds))
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 60}m{s % 60:02d}s"


def _render_live_progress() -> None:
    """Render a live progress panel from .spore_progress.json.

    Auto-refreshes every 3 seconds while status == 'running'. Silently
    hides itself when no run has ever been tracked.
    """
    progress = _read_progress()
    if not progress:
        return

    status = progress.get("status", "idle")
    run_id = progress.get("run_id", "?")

    # Header + status chip
    chip_color = {
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "starting": "⏳",
    }.get(status, "⚪")

    st.subheader(f"{chip_color} Run en cours" if status == "running" else f"{chip_color} Dernier run")
    st.caption(f"`{run_id}` — domaine {progress.get('domain', '?')}")

    # Progress bar
    processed = int(progress.get("processed", 0) or 0)
    total = int(progress.get("total_collisions", 0) or 0)
    ratio = min(processed / total, 1.0) if total > 0 else 0.0
    label = f"{processed} / {total} collisions traitées"
    st.progress(ratio, text=label)

    # Current collision + stage
    cc = progress.get("current_collision") or {}
    if cc:
        stage = STAGE_LABELS.get(cc.get("stage", ""), cc.get("stage", "?"))
        da = cc.get("domain_a", "?")
        db_ = cc.get("domain_b", "?")
        dist = float(cc.get("distance", 0.0) or 0.0)
        st.markdown(
            f"**Étape :** {stage}  \n"
            f"**Collision courante :** {da} × {db_} *(distance {dist:.2f})*"
        )
    else:
        # No collision being processed — explorer enrichment phase or done
        if status == "running" and processed == 0:
            st.caption("⏳ Enrichissement des collisions (appels Semantic Scholar / ArXiv)…")

    # KPI row
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Bridges", progress.get("bridges_found", 0))
    k2.metric("No-bridge", progress.get("no_bridge", 0))
    k3.metric("Curated", progress.get("curated", 0))
    k4.metric("Avg score", f"{progress.get('avg_score', 0):.2f}")
    cost = float(progress.get("cost_so_far", 0.0) or 0.0)
    k5.metric("Coût", f"${cost:.3f}" if cost < 1 else f"${cost:.2f}")

    # Timing
    elapsed = float(progress.get("elapsed_seconds", 0.0) or 0.0)
    eta = float(progress.get("estimated_remaining_seconds", 0.0) or 0.0)
    started_at = progress.get("started_at", "")
    time_cols = st.columns(3)
    time_cols[0].caption(f"**Démarré :** {started_at[11:19] if len(started_at) >= 19 else started_at}")
    time_cols[1].caption(f"**Écoulé :** {_fmt_duration(elapsed)}")
    time_cols[2].caption(
        f"**ETA :** {_fmt_duration(eta)}" if status == "running" and eta > 0 else "**ETA :** —"
    )

    # Recent hypotheses (top 5 by score)
    recent = progress.get("recent_hypotheses") or []
    if recent:
        with st.expander(f"🔬 Top {len(recent)} hypothèses (live)", expanded=False):
            for h in recent:
                st.markdown(
                    f"- **{h.get('score', 0):.2f}** — *{h.get('domains', '?')}*  \n"
                    f"  {h.get('one_liner', '')}"
                )

    # Error line
    if status == "failed" and progress.get("error_message"):
        st.error(f"❌ {progress['error_message']}")


async def _load_fire_hypotheses_without_brief() -> list:
    """Load hypotheses with auto_feedback=a_tester that don't yet have a brief."""
    await init_database()
    all_hyps = await list_hypotheses(limit=500)
    briefs = await list_briefs(limit=500)
    with_brief = {b["hypothesis_id"] for b in briefs if b.get("hypothesis_id")}

    candidates = []
    async with get_connection() as conn:
        for h in all_hyps:
            if h.id in with_brief:
                continue
            cursor = await conn.execute(
                "SELECT auto_feedback_json FROM hypotheses WHERE id = ?",
                (h.id,),
            )
            row = await cursor.fetchone()
            if row and row["auto_feedback_json"]:
                try:
                    data = json.loads(row["auto_feedback_json"])
                    if data.get("verdict") == "a_tester":
                        candidates.append(h)
                except (json.JSONDecodeError, TypeError):
                    continue
    return candidates


def render():
    """Render the Pilotage page."""
    st.title("⚙️ Pilotage")
    st.caption("Lancer des runs, forcer un post-fire, consulter la configuration")

    # ── Section 0: Live run progress (only if a progress file exists) ─
    _render_live_progress()
    if PROGRESS_FILE.exists():
        st.markdown("---")

    # ── Section 1: Launch a run ─────────────────────────────
    st.subheader("🚀 Lancer un run")

    # Show current status
    status = get_system_status()
    if status.get("status") == "running":
        st.warning(f"Run en cours: `{status.get('run_id', '?')}`")

    form_col, status_col = st.columns([3, 2])

    with form_col:
        with st.form("launch_run"):
            n_collisions = st.slider("Nombre de collisions", 10, 200, 100, 10)
            domain = st.selectbox("Domaine", _get_domain_options())
            submitted = st.form_submit_button("🚀 Lancer", use_container_width=True, type="primary")

            if submitted:
                if status.get("status") == "running":
                    st.error("Un run est déjà en cours. Attendez qu'il se termine.")
                else:
                    run_id = launch_run_background(n_collisions, domain, cross_ratio=0.7)
                    st.success(f"Run lancé: `{run_id}`")
                    st.rerun()

    with status_col:
        st.caption("**Dernier run:**")
        if status.get("run_id"):
            st.code(status.get("run_id"))
            st.caption(f"Statut: {status.get('status', '?')}")
        else:
            st.caption("Aucun run lancé depuis l'UI.")

    st.markdown("---")

    # ── Section 2: Manual post-fire ─────────────────────────
    st.subheader("🔬 Post-fire manuel")
    st.caption("Générer un brief pour une hypothèse 🔥 qui n'en a pas encore")

    candidates = run_async(_load_fire_hypotheses_without_brief())
    if not candidates:
        st.info("Aucune hypothèse 🔥 en attente de brief.")
    else:
        options = {
            f"{h.id[-12:]} — {h.collision.domain_a.name} × {h.collision.domain_b.name}": h.id
            for h in candidates
        }
        selected_label = st.selectbox("Hypothèse à convertir", list(options.keys()))
        if selected_label:
            hyp_id = options[selected_label]
            # Show the bridge
            selected_hyp = next(h for h in candidates if h.id == hyp_id)
            with st.expander("Aperçu de l'hypothèse"):
                st.markdown(f"**Bridge:** {selected_hyp.bridge.summary}")
                st.markdown(f"**Mécanisme:** {selected_hyp.bridge.mechanism[:300]}...")

            if st.button("🔬 Générer le brief", type="primary", use_container_width=True):
                cmd = [
                    str(PROJECT_ROOT / ".venv" / "bin" / "python"),
                    "cli.py", "post-fire",
                    "--hypothesis-id", hyp_id,
                ]
                try:
                    subprocess.Popen(
                        cmd,
                        cwd=PROJECT_ROOT,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    st.success(f"Post-fire lancé pour `{hyp_id}`. Le brief apparaîtra dans Research Briefs dans ~3-5 min.")
                except Exception as e:
                    st.error(f"Erreur: {e}")

    st.markdown("---")

    # ── Section 3: Configuration ────────────────────────────
    st.subheader("📜 Configuration")

    tab_const, tab_cron, tab_env = st.tabs(["🔒 Constitution", "⏰ Crontab", "🔑 Environnement"])

    with tab_const:
        st.caption("🔒 **Inviolable** — seul l'humain peut modifier ce fichier")
        if CONSTITUTION_PATH.exists():
            st.code(CONSTITUTION_PATH.read_text(encoding="utf-8"), language="yaml")
        else:
            st.warning("Fichier constitution.yaml introuvable.")

    with tab_cron:
        st.caption("Tâches automatiques programmées")
        crontab = _get_crontab()
        st.code(crontab, language="bash")

    with tab_env:
        st.caption("Variables d'environnement (noms uniquement, valeurs masquées)")
        env_keys = _get_env_keys()
        if not env_keys:
            st.info("Fichier .env introuvable ou vide.")
        else:
            for key, is_set in env_keys:
                icon = "✅" if is_set else "❌"
                st.markdown(f"- {icon} `{key}`")

    # ── Auto-refresh while a run is in progress ─────────────
    # Placed at the very end so the whole page renders once before we
    # sleep + rerun. That way the user can still launch new runs, browse
    # config, etc. between ticks.
    progress_live = _read_progress()
    if progress_live and progress_live.get("status") == "running":
        time.sleep(3)
        st.rerun()
