"""Evolution Page - L1 Meta-learning and genome mutations."""

import sys
from pathlib import Path
import json

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from datetime import datetime
import yaml

from helpers import run_async, get_system_status, set_system_status
from config import get_genome
from graph.l1_pipeline import run_l1_cycle


def get_observation_reports() -> list[Path]:
    """Get observation report files."""
    project_root = Path(__file__).parent.parent.parent
    reports_dir = project_root / "outputs"

    if not reports_dir.exists():
        return []

    files = list(reports_dir.glob("observation_*.json"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def get_mutation_history() -> list[dict]:
    """Get mutation history from genome backups."""
    project_root = Path(__file__).parent.parent.parent
    backups_dir = project_root / "data" / "genome_backups"

    if not backups_dir.exists():
        return []

    history = []
    backup_files = list(backups_dir.glob("l0_genome_*.yaml"))
    backup_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    for f in backup_files[:20]:  # Last 20 backups
        try:
            with open(f) as file:
                data = yaml.safe_load(file)

            history.append({
                "file": f.name,
                "date": datetime.fromtimestamp(f.stat().st_mtime),
                "version": data.get("genome_version", "unknown"),
                "mutated_by": data.get("mutated_by", "unknown"),
                "last_mutated": data.get("last_mutated", "unknown"),
            })
        except Exception:
            continue

    return history


def render():
    """Render the evolution page."""
    st.title("🧬 Évolution (L1)")
    st.caption("Meta-learning et mutations du genome")

    # Check status
    status = get_system_status()
    if status["status"] == "evolving":
        st.warning("🧬 **Un cycle L1 est en cours...**")

    # ============== LAUNCH L1 ==============
    st.markdown("### 🚀 Lancer un cycle L1")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown("""
        Le cycle L1 :
        1. **Observe** les métriques des derniers runs
        2. **Analyse** les patterns de succès/échec
        3. **Propose** des mutations du genome
        4. **Applique** les mutations (avec approbation)
        """)

    with col2:
        if st.button(
            "🧬 Lancer cycle L1",
            use_container_width=True,
            type="primary",
            disabled=(status["status"] in ["running", "evolving"]),
        ):
            with st.spinner("Exécution du cycle L1..."):
                try:
                    set_system_status("evolving")

                    # Run L1 cycle directly
                    result = run_async(run_l1_cycle(
                        limit_runs=5,
                        dry_run=False,
                        save_reports=True,
                    ))

                    set_system_status("idle")

                    if result.get("status") == "completed":
                        mutations_applied = len(result.get("mutations_applied", []))
                        st.success(f"Cycle L1 terminé! {mutations_applied} mutation(s) appliquée(s).")
                        st.rerun()
                    elif result.get("status") == "no_action_needed":
                        st.info("Aucune action nécessaire - le système fonctionne bien.")
                    elif result.get("status") == "no_mutations_proposed":
                        st.info("Aucune mutation proposée par le Strategist.")
                    elif result.get("status") == "all_mutations_rejected":
                        st.warning("Toutes les mutations ont été rejetées par le Critic.")
                    else:
                        st.warning(f"Statut: {result.get('status', 'unknown')}")

                except Exception as e:
                    set_system_status("idle")
                    st.error(f"Erreur: {e}")

    st.markdown("---")

    # ============== LATEST OBSERVATION REPORT ==============
    st.markdown("### 📊 Dernier rapport d'observation")

    reports = get_observation_reports()

    if reports:
        latest_report = reports[0]

        try:
            with open(latest_report) as f:
                report_data = json.load(f)

            # Report metadata
            report_date = datetime.fromtimestamp(latest_report.stat().st_mtime)
            st.caption(f"📅 {report_date.strftime('%Y-%m-%d %H:%M')} | `{latest_report.name}`")

            # Metrics observed
            if "metrics" in report_data:
                st.markdown("**📈 Métriques observées:**")
                metrics = report_data["metrics"]

                metric_cols = st.columns(4)
                with metric_cols[0]:
                    st.metric("Runs analysés", metrics.get("runs_analyzed", "N/A"))
                with metric_cols[1]:
                    br = metrics.get("avg_bridge_rate")
                    st.metric("Bridge rate moyen", f"{br*100:.1f}%" if br else "N/A")
                with metric_cols[2]:
                    st.metric("Score moyen", f"{metrics.get('avg_score', 0):.3f}")
                with metric_cols[3]:
                    st.metric("Coût total", f"${metrics.get('total_cost', 0):.2f}")

            # Opportunities detected
            if "opportunities" in report_data and report_data["opportunities"]:
                st.markdown("**💡 Opportunités détectées:**")
                for opp in report_data["opportunities"]:
                    if isinstance(opp, str):
                        st.markdown(f"- {opp}")
                    else:
                        with st.expander(f"🎯 {opp.get('title', 'Opportunity')}", expanded=False):
                            st.markdown(opp.get("description", ""))
                            if "suggested_action" in opp:
                                st.info(f"**Action suggérée:** {opp['suggested_action']}")

            # Proposed mutations
            if "proposed_mutations" in report_data:
                st.markdown("**🧬 Mutations proposées:**")
                for mut in report_data["proposed_mutations"]:
                    with st.expander(f"✏️ {mut.get('parameter', 'Unknown')}", expanded=True):
                        mut_cols = st.columns(3)
                        with mut_cols[0]:
                            st.markdown(f"**Avant:** `{mut.get('old_value', 'N/A')}`")
                        with mut_cols[1]:
                            st.markdown(f"**Après:** `{mut.get('new_value', 'N/A')}`")
                        with mut_cols[2]:
                            st.markdown(f"**Confiance:** {mut.get('confidence', 'N/A')}")

                        st.markdown(f"**Justification:** {mut.get('justification', '')}")

        except Exception as e:
            st.error(f"Erreur lecture rapport: {e}")
    else:
        st.info("Aucun rapport d'observation. Lancez un cycle L1 pour en générer un.")

    st.markdown("---")

    # ============== MUTATION HISTORY ==============
    st.markdown("### 📜 Historique des mutations")

    history = get_mutation_history()

    if history:
        import pandas as pd

        df = pd.DataFrame(history)
        df["date"] = df["date"].dt.strftime("%Y-%m-%d %H:%M")

        st.dataframe(
            df[["date", "version", "mutated_by"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "date": "Date",
                "version": "Version",
                "mutated_by": "Muté par",
            }
        )
    else:
        st.info("Aucun historique de mutations.")

    st.markdown("---")

    # ============== CURRENT GENOME ==============
    st.markdown("### 🧬 Genome actuel")

    try:
        genome = get_genome()
        genome_data = genome.to_dict()

        # Show as formatted YAML
        yaml_str = yaml.dump(genome_data, allow_unicode=True, default_flow_style=False, sort_keys=False)

        st.code(yaml_str, language="yaml")

    except Exception as e:
        st.error(f"Erreur lecture genome: {e}")

    # Footer
    st.markdown("---")
    st.caption("🍄 SPORE Dashboard")
