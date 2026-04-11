"""Digests Page - View and generate research digests."""

import sys
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from datetime import datetime


def get_digest_files() -> list[Path]:
    """Get all digest files from outputs/digests/."""
    project_root = Path(__file__).parent.parent.parent
    digests_dir = project_root / "outputs" / "digests"

    if not digests_dir.exists():
        return []

    # Find all markdown files
    files = list(digests_dir.glob("*.md"))
    # Sort by modification time (newest first)
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    return files


def render():
    """Render the digests page."""
    st.title("📰 Digests")
    st.caption("Digests de recherche générés par SPORE")

    # Get digest files
    digest_files = get_digest_files()

    if not digest_files:
        st.info("""
        Aucun digest trouvé.

        Pour générer un digest, lancez:
        ```bash
        python -m autopilot.digest
        ```

        Ou utilisez le bouton ci-dessous après avoir complété un run.
        """)

        # Generate digest button
        if st.button("📝 Générer un digest du dernier run", use_container_width=True):
            with st.spinner("Génération du digest en cours..."):
                try:
                    import subprocess
                    project_root = Path(__file__).parent.parent.parent
                    venv_python = project_root / ".venv" / "bin" / "python"
                    python_cmd = str(venv_python) if venv_python.exists() else "python"

                    result = subprocess.run(
                        [python_cmd, "-m", "autopilot.digest"],
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )

                    if result.returncode == 0:
                        st.success("Digest généré avec succès!")
                        st.rerun()
                    else:
                        st.error(f"Erreur: {result.stderr}")

                except subprocess.TimeoutExpired:
                    st.error("Timeout lors de la génération du digest")
                except Exception as e:
                    st.error(f"Erreur: {e}")

        return

    # ============== DIGEST LIST ==============
    st.markdown("### 📚 Digests disponibles")

    # Sidebar for selection
    st.sidebar.header("📂 Sélection")

    # Create options list
    digest_options = []
    for f in digest_files:
        mod_time = datetime.fromtimestamp(f.stat().st_mtime)
        label = f"{f.stem} ({mod_time.strftime('%d/%m %H:%M')})"
        digest_options.append((label, f))

    # Select digest
    if digest_options:
        selected_label = st.sidebar.selectbox(
            "Digest",
            [opt[0] for opt in digest_options],
            label_visibility="collapsed",
        )

        # Find selected file
        selected_file = None
        for label, f in digest_options:
            if label == selected_label:
                selected_file = f
                break

        if selected_file:
            # Show metadata
            mod_time = datetime.fromtimestamp(selected_file.stat().st_mtime)
            file_size = selected_file.stat().st_size

            st.sidebar.markdown("---")
            st.sidebar.markdown(f"**Fichier:** `{selected_file.name}`")
            st.sidebar.markdown(f"**Modifié:** {mod_time.strftime('%Y-%m-%d %H:%M')}")
            st.sidebar.markdown(f"**Taille:** {file_size / 1024:.1f} KB")

            # Read and display content
            try:
                content = selected_file.read_text(encoding="utf-8")

                # Render markdown
                st.markdown(content)

            except Exception as e:
                st.error(f"Erreur lecture du fichier: {e}")

            # Download button
            st.sidebar.markdown("---")
            st.sidebar.download_button(
                label="⬇️ Télécharger",
                data=content,
                file_name=selected_file.name,
                mime="text/markdown",
                use_container_width=True,
            )

    # Generate new digest button
    st.sidebar.markdown("---")
    if st.sidebar.button("📝 Nouveau digest", use_container_width=True):
        with st.spinner("Génération..."):
            try:
                import subprocess
                project_root = Path(__file__).parent.parent.parent
                venv_python = project_root / ".venv" / "bin" / "python"
                python_cmd = str(venv_python) if venv_python.exists() else "python"

                result = subprocess.run(
                    [python_cmd, "-m", "autopilot.digest"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if result.returncode == 0:
                    st.sidebar.success("Digest généré!")
                    st.rerun()
                else:
                    st.sidebar.error(f"Erreur")

            except Exception as e:
                st.sidebar.error(f"Erreur: {e}")

    # Footer
    st.markdown("---")
    st.caption("🍄 SPORE Dashboard")
