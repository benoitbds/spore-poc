"""Configuration Page - View and edit SPORE settings."""

import sys
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import yaml
from datetime import datetime

from config import get_genome, get_constitution, get_settings


def render():
    """Render the configuration page."""
    st.title("⚙️ Configuration")
    st.caption("Paramètres du système SPORE")

    # Tabs for different config sections
    tab1, tab2, tab3, tab4 = st.tabs([
        "📜 Constitution",
        "🧬 Genome L0",
        "📧 Email/SMTP",
        "⏰ Schedule",
    ])

    # ============== CONSTITUTION ==============
    with tab1:
        st.markdown("### 📜 Constitution")
        st.warning("⚠️ **Read-only** - La constitution est inviolable par design")

        try:
            constitution = get_constitution()
            const_data = constitution.to_dict()

            # Ethics section
            st.markdown("#### 🤝 Éthique")
            ethics = const_data.get("ethics", {})

            st.markdown("**Domaines exclus:**")
            for domain in ethics.get("excluded_domains", []):
                st.markdown(f"- ❌ `{domain}`")

            st.markdown(f"**Transparence:** {ethics.get('transparency', 'N/A')}")
            st.markdown(f"**Attribution:** {ethics.get('attribution', 'N/A')}")

            # Safety section
            st.markdown("#### 🛡️ Sécurité")
            safety = const_data.get("safety", {})

            safety_cols = st.columns(3)
            with safety_cols[0]:
                st.metric("Chaos floor", safety.get("chaos_floor", "N/A"))
            with safety_cols[1]:
                st.metric("Budget max/jour", f"${safety.get('max_budget_per_day', 'N/A')}")
            with safety_cols[2]:
                st.metric("Rollback threshold", safety.get("rollback_threshold", "N/A"))

            st.markdown("**Actions nécessitant approbation humaine:**")
            for action in safety.get("human_approval_required_for", []):
                st.markdown(f"- 👤 `{action}`")

            # Philosophy
            st.markdown("#### 🎯 Philosophie")
            philosophy = const_data.get("philosophy", {})
            st.info(philosophy.get("purpose", ""))
            st.markdown(f"*\"{philosophy.get('stance', '')}\"*")
            st.caption(philosophy.get("humility", ""))

            # Show raw YAML
            with st.expander("📄 YAML complet"):
                yaml_str = yaml.dump(const_data, allow_unicode=True, default_flow_style=False)
                st.code(yaml_str, language="yaml")

        except Exception as e:
            st.error(f"Erreur lecture constitution: {e}")

    # ============== GENOME ==============
    with tab2:
        st.markdown("### 🧬 Genome L0")
        st.warning("⚠️ **Attention:** Modifier manuellement le genome bypasse le système L1")

        try:
            genome = get_genome()
            genome_data = genome.to_dict()

            # Version info
            st.markdown(f"**Version:** `{genome_data.get('genome_version', 'N/A')}`")
            st.markdown(f"**Dernière mutation:** {genome_data.get('last_mutated', 'N/A')}")
            st.markdown(f"**Muté par:** {genome_data.get('mutated_by', 'N/A')}")

            st.markdown("---")

            # Agents configuration
            st.markdown("#### 🤖 Agents")

            agents = genome_data.get("agents", {})
            for agent_name, agent_config in agents.items():
                with st.expander(f"**{agent_name}**", expanded=False):
                    st.markdown(f"- **Provider:** `{agent_config.get('provider', 'anthropic')}`")
                    st.markdown(f"- **Model:** `{agent_config.get('model', 'N/A')}`")
                    st.markdown(f"- **Prompt version:** `{agent_config.get('prompt_version', 'N/A')}`")

                    params = agent_config.get("parameters", {})
                    if params:
                        st.markdown("**Paramètres:**")
                        for k, v in params.items():
                            st.markdown(f"  - {k}: `{v}`")

            st.markdown("---")

            # Randomness parameters
            st.markdown("#### 🎲 Randomness")
            randomness = genome_data.get("randomness", {})

            rand_cols = st.columns(3)
            with rand_cols[0]:
                st.metric("Distance min", randomness.get("distance_min", "N/A"))
                st.metric("Distance max", randomness.get("distance_max", "N/A"))
            with rand_cols[1]:
                st.metric("Cross-discipline ratio", randomness.get("cross_discipline_ratio", "N/A"))
                st.metric("Chaos floor", randomness.get("chaos_floor", "N/A"))
            with rand_cols[2]:
                st.metric("Temperature", randomness.get("temperature", "N/A"))

            st.markdown("---")

            # Score weights
            st.markdown("#### ⚖️ Score weights")
            weights = genome_data.get("score_weights", {})

            weight_cols = st.columns(5)
            weight_items = list(weights.items())
            for i, (name, value) in enumerate(weight_items):
                with weight_cols[i % 5]:
                    st.metric(name, f"{value:.2f}")

            st.markdown("---")

            # Edit genome
            st.markdown("#### ✏️ Éditer le genome")

            if st.checkbox("J'ai compris les risques de modifier manuellement le genome"):
                genome_path = Path(__file__).parent.parent.parent / "data" / "l0_genome.yaml"

                current_yaml = yaml.dump(genome_data, allow_unicode=True, default_flow_style=False, sort_keys=False)

                edited_yaml = st.text_area(
                    "YAML du genome",
                    value=current_yaml,
                    height=400,
                    key="genome_editor",
                )

                col1, col2 = st.columns(2)

                with col1:
                    if st.button("💾 Sauvegarder", type="primary", use_container_width=True):
                        try:
                            # Validate YAML
                            new_data = yaml.safe_load(edited_yaml)

                            # Backup current genome
                            backup_dir = genome_path.parent / "genome_backups"
                            backup_dir.mkdir(exist_ok=True)
                            backup_path = backup_dir / f"l0_genome_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"

                            with open(backup_path, "w") as f:
                                yaml.dump(genome_data, f, allow_unicode=True, default_flow_style=False)

                            # Update mutation metadata
                            new_data["last_mutated"] = datetime.now().isoformat()
                            new_data["mutated_by"] = "manual_edit"

                            # Save new genome
                            with open(genome_path, "w") as f:
                                yaml.dump(new_data, f, allow_unicode=True, default_flow_style=False)

                            st.success(f"Genome sauvegardé! Backup: `{backup_path.name}`")

                            # Reload genome
                            from config import reset_config
                            reset_config()
                            st.rerun()

                        except yaml.YAMLError as e:
                            st.error(f"Erreur YAML: {e}")
                        except Exception as e:
                            st.error(f"Erreur: {e}")

                with col2:
                    if st.button("↩️ Annuler", use_container_width=True):
                        st.rerun()

        except Exception as e:
            st.error(f"Erreur lecture genome: {e}")

    # ============== EMAIL/SMTP ==============
    with tab3:
        st.markdown("### 📧 Configuration Email")
        st.caption("Paramètres pour l'envoi des digests par email")

        try:
            settings = get_settings()

            st.markdown("**Configuration actuelle:**")

            smtp_cols = st.columns(2)

            with smtp_cols[0]:
                st.text_input("SMTP Host", value=settings.smtp_host or "", disabled=True)
                st.text_input("SMTP Port", value=str(settings.smtp_port), disabled=True)

            with smtp_cols[1]:
                st.text_input("SMTP User", value=settings.smtp_user or "", disabled=True)
                st.text_input("SMTP Password", value="***" if settings.smtp_password else "", disabled=True)

            st.text_input("Recipients", value=settings.digest_recipients or "", disabled=True)

            st.info("""
            Pour modifier ces paramètres, éditez le fichier `.env` :

            ```
            SPORE_SMTP_HOST=smtp.gmail.com
            SPORE_SMTP_PORT=587
            SPORE_SMTP_USER=your-email@gmail.com
            SPORE_SMTP_PASSWORD=your-app-password
            SPORE_DIGEST_RECIPIENTS=recipient1@example.com,recipient2@example.com
            ```
            """)

        except Exception as e:
            st.error(f"Erreur lecture settings: {e}")

    # ============== SCHEDULE ==============
    with tab4:
        st.markdown("### ⏰ Schedule")
        st.caption("Configuration du cron pour les runs automatiques")

        try:
            genome = get_genome()
            schedule = genome.to_dict().get("schedule", {})

            st.markdown(f"**Fréquence:** `{schedule.get('frequency', 'manual')}`")
            st.markdown(f"**Heures actives:** `{schedule.get('active_hours', '00:00-23:59')}`")

            st.info("""
            Le schedule est géré par le système cron. Pour configurer des runs automatiques:

            ```bash
            # Éditer le crontab
            crontab -e

            # Ajouter une entrée (exemple: tous les jours à 3h du matin)
            0 3 * * * cd /path/to/spore-poc && python -m cli run --collisions 100
            ```

            Le mode `manual` signifie que les runs sont déclenchés uniquement via l'interface ou le CLI.
            """)

        except Exception as e:
            st.error(f"Erreur: {e}")

    # Footer
    st.markdown("---")
    st.caption("🍄 SPORE Dashboard")
