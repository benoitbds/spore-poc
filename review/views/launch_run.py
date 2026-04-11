"""Launch Run Page - Start a new SPORE pipeline run with real-time progress."""

import sys
from pathlib import Path
import time

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import subprocess
from datetime import datetime

from helpers import (
    run_async,
    get_system_status,
    set_system_status,
    format_cost,
)
from storage import init_database
from storage.database import get_connection
from config import get_genome
from progress import get_current_progress, PROGRESS_FILE


# Stage display configuration
STAGES = ["gate", "synthesis", "critics", "curator", "impact"]
STAGE_LABELS = {
    "gate": "🚪 Gate",
    "synthesis": "🧪 Synthesis",
    "critics": "⚔️ Critics",
    "curator": "🏆 Curator",
    "impact": "💥 Impact",
}


async def get_last_run():
    """Get the most recent run."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_run_by_id(run_id: str):
    """Get a specific run."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM runs WHERE id = ?",
            (run_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


def launch_run(n_collisions: int, domain: str):
    """Launch a SPORE run in background."""
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # Update status
    set_system_status(
        status="running",
        run_id=run_id,
        n_collisions=n_collisions,
        domain=domain,
        started_at=datetime.now().isoformat(),
    )

    # Build command - use the CLI
    project_root = Path(__file__).parent.parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"

    # Use venv python if exists, otherwise system python
    python_cmd = str(venv_python) if venv_python.exists() else "python"

    cmd = [
        python_cmd, "-m", "cli", "run",
        "--collisions", str(n_collisions),
        "--domain", domain,
    ]

    # Create a wrapper script that updates status when done
    wrapper_script = f'''
import subprocess
import json
from pathlib import Path
from datetime import datetime

status_file = Path("{project_root}/.spore_status.json")

try:
    result = subprocess.run(
        {cmd},
        cwd="{project_root}",
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        with open(status_file, "w") as f:
            json.dump({{"status": "idle", "run_id": "{run_id}", "completed_at": datetime.now().isoformat()}}, f)
    else:
        with open(status_file, "w") as f:
            json.dump({{"status": "error", "run_id": "{run_id}", "error": result.stderr[:500]}}, f)
except Exception as e:
    with open(status_file, "w") as f:
        json.dump({{"status": "error", "run_id": "{run_id}", "error": str(e)}}, f)
'''

    # Launch the wrapper script
    subprocess.Popen(
        [python_cmd, "-c", wrapper_script],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    return run_id


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def render_stage_indicator(current_stage: str):
    """Render the pipeline stage indicator."""
    stage_html = ""
    for stage in STAGES:
        label = STAGE_LABELS.get(stage, stage)
        if stage == current_stage:
            # Current stage - orange/animated
            stage_html += f'<span style="background: #ff9800; color: white; padding: 4px 8px; border-radius: 4px; margin-right: 4px; font-weight: bold;">● {label}</span>'
        elif STAGES.index(stage) < STAGES.index(current_stage):
            # Past stage - green
            stage_html += f'<span style="background: #4caf50; color: white; padding: 4px 8px; border-radius: 4px; margin-right: 4px;">✓ {label}</span>'
        else:
            # Future stage - gray
            stage_html += f'<span style="background: #e0e0e0; color: #666; padding: 4px 8px; border-radius: 4px; margin-right: 4px;">{label}</span>'

    st.markdown(stage_html, unsafe_allow_html=True)


def render_progress_display(progress: dict):
    """Render the real-time progress display."""
    st.markdown("### 🔄 Run en cours")

    # Progress bar
    total = progress.get("total_collisions", 1)
    processed = progress.get("processed", 0)
    percent = (processed / total) if total > 0 else 0

    col1, col2 = st.columns([3, 1])
    with col1:
        st.progress(percent)
    with col2:
        st.markdown(f"**{processed}/{total}** ({percent*100:.0f}%)")

    # Time info
    elapsed = progress.get("elapsed_seconds", 0)
    remaining = progress.get("estimated_remaining_seconds", 0)

    time_col1, time_col2, time_col3 = st.columns(3)
    with time_col1:
        st.metric("⏱️ Écoulé", format_duration(elapsed))
    with time_col2:
        st.metric("⏳ Restant estimé", format_duration(remaining) if remaining > 0 else "...")
    with time_col3:
        if processed > 0:
            speed = elapsed / processed
            st.metric("⚡ Vitesse", f"{speed:.1f}s/collision")
        else:
            st.metric("⚡ Vitesse", "...")

    st.markdown("---")

    # Current collision
    current = progress.get("current_collision")
    if current:
        st.markdown("### 🔬 Collision en cours")

        domain_a = current.get("domain_a", "?")
        domain_b = current.get("domain_b", "?")
        distance = current.get("distance", 0)
        stage = current.get("stage", "gate")

        st.markdown(f"**🔄 {domain_a} × {domain_b}** (distance: {distance:.2f})")

        # Stage indicator
        render_stage_indicator(stage)

        st.markdown("---")

    # Stats
    st.markdown("### 📊 Stats du run")

    bridges = progress.get("bridges_found", 0)
    no_bridge = progress.get("no_bridge", 0)
    curated = progress.get("curated", 0)
    cost = progress.get("cost_so_far", 0)
    avg_score = progress.get("avg_score", 0)

    stat_cols = st.columns(5)
    with stat_cols[0]:
        bridge_rate = bridges / processed if processed > 0 else 0
        st.metric("🌉 Bridge rate", f"{bridge_rate*100:.0f}%")
    with stat_cols[1]:
        st.metric("✅ Bridges", bridges)
    with stat_cols[2]:
        st.metric("❌ No bridge", no_bridge)
    with stat_cols[3]:
        st.metric("🏆 Curées", curated)
    with stat_cols[4]:
        st.metric("💰 Coût", format_cost(cost))

    if avg_score > 0:
        st.metric("📈 Score moyen", f"{avg_score:.3f}")

    st.markdown("---")

    # Recent hypotheses feed
    recent = progress.get("recent_hypotheses", [])
    if recent:
        st.markdown("### 📝 Dernières hypothèses")

        for hyp in recent[:5]:
            hyp_id = hyp.get("id", "")
            one_liner = hyp.get("one_liner", "")
            score = hyp.get("score", 0)
            domains = hyp.get("domains", "")

            with st.container():
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"**{domains}**")
                    st.caption(one_liner[:150] + "..." if len(one_liner) > 150 else one_liner)
                with col2:
                    if score > 0:
                        st.metric("Score", f"{score:.2f}", label_visibility="collapsed")
                    else:
                        st.caption("En cours...")

        st.markdown("---")


def render_completed_summary(progress: dict):
    """Render summary when run is complete."""
    st.success("✅ **Run terminé!**")

    # Final stats
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Collisions", progress.get("processed", 0))
    with col2:
        bridges = progress.get("bridges_found", 0)
        processed = progress.get("processed", 1)
        st.metric("Bridge rate", f"{bridges/processed*100:.0f}%")
    with col3:
        st.metric("Curées", progress.get("curated", 0))
    with col4:
        st.metric("Coût total", format_cost(progress.get("cost_so_far", 0)))

    elapsed = progress.get("elapsed_seconds", 0)
    st.info(f"⏱️ Durée totale: {format_duration(elapsed)}")

    # Actions hint
    st.info("👉 Utilisez le menu de navigation dans la barre latérale pour accéder aux pages **Review** ou **Digests**")


def render_failed_summary(progress: dict):
    """Render summary when run failed."""
    st.error("❌ **Run échoué**")

    error_msg = progress.get("error_message", "Unknown error")
    st.code(error_msg, language="text")

    # Partial stats
    if progress.get("processed", 0) > 0:
        st.warning("Progression partielle avant échec:")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Collisions traitées", progress.get("processed", 0))
        with col2:
            st.metric("Bridges trouvés", progress.get("bridges_found", 0))
        with col3:
            st.metric("Coût", format_cost(progress.get("cost_so_far", 0)))


def render():
    """Render the launch run page."""
    st.title("🚀 Lancer un run")
    st.caption("Démarrer un nouveau cycle de génération d'hypothèses")

    # Initialize database
    run_async(init_database())

    # Check current status and progress
    status = get_system_status()
    progress = get_current_progress()

    # ============== RUNNING STATE ==============
    if status["status"] == "running" or (progress and progress.get("status") == "running"):
        if progress and progress.get("status") == "running":
            render_progress_display(progress)

            # Manual refresh button
            if st.button("🔄 Rafraîchir", use_container_width=True, key="manual_refresh"):
                st.rerun()

            st.caption("💡 Cliquez sur Rafraîchir pour mettre à jour la progression")

        elif progress and progress.get("status") == "completed":
            render_completed_summary(progress)

        elif progress and progress.get("status") == "failed":
            render_failed_summary(progress)

        else:
            # No progress file yet, show basic status
            st.warning(f"""
            🔄 **Un run est en cours**

            - **Run ID:** `{status.get('run_id', 'unknown')}`
            - **Démarré:** {status.get('started_at', 'N/A')}

            En attente des premières données de progression...
            """)

            if st.button("🔄 Rafraîchir le statut"):
                st.rerun()

        st.markdown("---")

    # ============== COMPLETED STATE ==============
    elif progress and progress.get("status") == "completed":
        render_completed_summary(progress)

        st.markdown("---")

        # Allow launching new run
        st.markdown("### 🆕 Lancer un nouveau run")

    # ============== FAILED STATE ==============
    elif progress and progress.get("status") == "failed":
        render_failed_summary(progress)

        if st.button("🔄 Réinitialiser", use_container_width=True):
            set_system_status("idle")
            # Clear progress file
            if PROGRESS_FILE.exists():
                PROGRESS_FILE.unlink()
            st.rerun()

        st.markdown("---")
        st.markdown("### 🆕 Relancer un run")

    # ============== ERROR STATE (from status file) ==============
    elif status["status"] == "error":
        st.error(f"""
        ❌ **Le dernier run a échoué**

        - **Run ID:** `{status.get('run_id', 'unknown')}`
        - **Erreur:** {status.get('error', 'Unknown error')}
        """)

        if st.button("🔄 Réinitialiser le statut"):
            set_system_status("idle")
            st.rerun()

        st.markdown("---")

    # ============== LAUNCH FORM ==============
    st.markdown("### ⚙️ Configuration du run")

    # Load genome for defaults
    try:
        genome = get_genome()
        default_cross_ratio = genome.randomness.get("cross_discipline_ratio", 0.7)
    except Exception:
        default_cross_ratio = 0.7

    with st.form("launch_form"):
        col1, col2 = st.columns(2)

        with col1:
            n_collisions = st.slider(
                "Nombre de collisions",
                min_value=10,
                max_value=200,
                value=50,
                step=10,
                help="Plus de collisions = plus d'hypothèses potentielles, mais coût plus élevé"
            )

            domain = st.selectbox(
                "Domaine",
                options=["all_science", "materials_science"],
                index=0,
                help="all_science: 200 domaines, 8 disciplines | materials_science: 52 domaines, même discipline"
            )

        with col2:
            cross_ratio = st.slider(
                "Cross-discipline ratio",
                min_value=0.5,
                max_value=1.0,
                value=default_cross_ratio,
                step=0.05,
                help="Proportion de collisions entre disciplines différentes (0.7 = 70%)"
            )

            # Cost estimate
            estimated_cost = n_collisions * 0.05  # Rough estimate $0.05 per collision
            st.markdown(f"**Coût estimé:** ~${estimated_cost:.2f}")
            st.caption("(Estimation basée sur ~$0.05/collision avec Sonnet)")

        # Submit button
        is_running = bool(status["status"] == "running" or (progress and progress.get("status") == "running"))

        submitted = st.form_submit_button(
            "🚀 Lancer le run",
            use_container_width=True,
            type="primary",
            disabled=is_running,
        )

        if submitted:
            if is_running:
                st.error("Un run est déjà en cours!")
            else:
                # Clear old progress
                if PROGRESS_FILE.exists():
                    PROGRESS_FILE.unlink()

                run_id = launch_run(n_collisions, domain)
                st.success(f"✅ Run lancé! ID: `{run_id}`")
                time.sleep(1)
                st.rerun()

    st.markdown("---")

    # ============== LAST RUN INFO ==============
    st.markdown("### 📋 Dernier run (base de données)")

    last_run = run_async(get_last_run())

    if last_run:
        run_col1, run_col2 = st.columns(2)

        with run_col1:
            st.markdown(f"**ID:** `{last_run['id']}`")
            st.markdown(f"**Statut:** {last_run['status']}")

            started = datetime.fromisoformat(last_run['started_at'])
            st.markdown(f"**Démarré:** {started.strftime('%d/%m/%Y %H:%M')}")

            if last_run.get('completed_at'):
                completed = datetime.fromisoformat(last_run['completed_at'])
                duration = (completed - started).total_seconds()
                st.markdown(f"**Terminé:** {completed.strftime('%d/%m/%Y %H:%M')}")
                st.markdown(f"**Durée:** {format_duration(duration)}")

        with run_col2:
            st.metric("Collisions", last_run.get('collisions_requested', 'N/A'))
            st.metric("Hypothèses", last_run.get('hypotheses_generated', 'N/A'))

            bridge_rate = last_run.get('bridge_rate')
            if bridge_rate is not None:
                st.metric("Bridge rate", f"{bridge_rate*100:.1f}%")

            cost = last_run.get('total_cost_usd')
            if cost is not None:
                st.metric("Coût", format_cost(cost))

        if last_run.get('error_message'):
            st.error(f"**Erreur:** {last_run['error_message']}")

    else:
        st.info("Aucun run enregistré. Lancez votre premier run ci-dessus!")

    # ============== QUICK TIPS ==============
    st.markdown("---")
    st.markdown("### 💡 Conseils")

    with st.expander("Comment choisir les paramètres?"):
        st.markdown("""
        **Nombre de collisions:**
        - 10-20: Test rapide (~$0.50-1)
        - 50-100: Run standard (~$2.50-5)
        - 150-200: Run intensif (~$7.50-10)

        **Domaine:**
        - `all_science`: 200 domaines across 8 disciplines (Physics, Chemistry, Biology, etc.)
          - Meilleur pour découvrir des connexions inattendues
          - Plus de diversité cross-disciplinaire

        - `materials_science`: 52 sous-domaines de materials science
          - Focus sur un domaine précis
          - Collisions plus "proches" conceptuellement

        **Cross-discipline ratio:**
        - 0.5: 50% cross-discipline, 50% same-discipline
        - 0.7: 70% cross-discipline (recommandé)
        - 1.0: 100% cross-discipline (maximal novelty)
        """)

    # Footer
    st.markdown("---")
    st.caption("🍄 SPORE Dashboard")
