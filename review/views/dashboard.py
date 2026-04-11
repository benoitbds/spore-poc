"""Dashboard Page - SPORE Overview and Key Metrics."""

import sys
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from datetime import datetime

from helpers import run_async, get_system_status, get_status_emoji, format_cost, format_rate
from storage import init_database, list_hypotheses, get_metrics
from storage.database import get_connection
from models.hypothesis import HumanFeedback
from config import get_genome


async def get_all_runs():
    """Get all runs from database."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_aggregate_stats():
    """Get aggregate statistics from database."""
    async with get_connection() as conn:
        # Total hypotheses
        cursor = await conn.execute("SELECT COUNT(*) as count FROM hypotheses")
        total_hypotheses = (await cursor.fetchone())["count"]

        # Average score
        cursor = await conn.execute("""
            SELECT AVG(json_extract(scores_json, '$.composite')) as avg_score
            FROM hypotheses
            WHERE scores_json IS NOT NULL
        """)
        row = await cursor.fetchone()
        avg_score = row["avg_score"] or 0

        # Feedback distribution
        cursor = await conn.execute("""
            SELECT human_feedback, COUNT(*) as count
            FROM hypotheses
            GROUP BY human_feedback
        """)
        feedback_rows = await cursor.fetchall()
        feedback_dist = {row["human_feedback"]: row["count"] for row in feedback_rows}

        # Total cost from runs
        cursor = await conn.execute("""
            SELECT SUM(total_cost_usd) as total_cost
            FROM runs
            WHERE status = 'completed'
        """)
        row = await cursor.fetchone()
        total_cost = row["total_cost"] or 0

        # Average bridge rate
        cursor = await conn.execute("""
            SELECT AVG(bridge_rate) as avg_bridge_rate
            FROM runs
            WHERE bridge_rate IS NOT NULL
        """)
        row = await cursor.fetchone()
        avg_bridge_rate = row["avg_bridge_rate"] or 0

        # Last run
        cursor = await conn.execute("""
            SELECT * FROM runs ORDER BY started_at DESC LIMIT 1
        """)
        last_run = await cursor.fetchone()

        return {
            "total_hypotheses": total_hypotheses,
            "avg_score": avg_score,
            "feedback_dist": feedback_dist,
            "total_cost": total_cost,
            "avg_bridge_rate": avg_bridge_rate,
            "last_run": dict(last_run) if last_run else None,
        }


async def get_runs_for_chart():
    """Get run data for time series chart."""
    async with get_connection() as conn:
        cursor = await conn.execute("""
            SELECT
                id,
                started_at,
                bridge_rate,
                total_cost_usd,
                hypotheses_generated
            FROM runs
            WHERE status = 'completed'
            ORDER BY started_at ASC
            LIMIT 30
        """)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_top_gaps():
    """Get most frequent gaps from gap manifests."""
    async with get_connection() as conn:
        cursor = await conn.execute("""
            SELECT gap_manifest_json FROM hypotheses
            WHERE gap_manifest_json IS NOT NULL
            LIMIT 100
        """)
        rows = await cursor.fetchall()

        # Parse and count gaps
        gap_counts: dict[str, int] = {}
        for row in rows:
            try:
                import json
                manifest = json.loads(row["gap_manifest_json"])
                for gap_type in ["data_gaps", "competence_gaps", "epistemic_gaps"]:
                    for gap in manifest.get(gap_type, []):
                        desc = gap.get("description", gap.get("zone", "Unknown"))
                        if desc:
                            # Truncate for grouping
                            key = desc[:80]
                            gap_counts[key] = gap_counts.get(key, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue

        # Return top 5
        sorted_gaps = sorted(gap_counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_gaps[:5]


async def _get_brief_stats():
    """Get brief statistics for the dashboard KPIs."""
    try:
        async with get_connection() as conn:
            # Total briefs this month
            cursor = await conn.execute("""
                SELECT COUNT(*) as count FROM briefs
                WHERE created_at >= date('now', 'start of month')
            """)
            row = await cursor.fetchone()
            briefs_this_month = row["count"] if row else 0

            # Total briefs overall
            cursor = await conn.execute("SELECT COUNT(*) as count FROM briefs")
            row = await cursor.fetchone()
            total_briefs = row["count"] if row else 0

            # Average novelty score
            cursor = await conn.execute("""
                SELECT AVG(novelty_score) as avg_novelty FROM briefs
                WHERE novelty_score IS NOT NULL
            """)
            row = await cursor.fetchone()
            avg_novelty = row["avg_novelty"] if row else None

            # Fire → brief conversion (fire = auto_feedback a_tester)
            cursor = await conn.execute("""
                SELECT COUNT(*) as count FROM hypotheses
                WHERE auto_feedback_json LIKE '%a_tester%'
            """)
            row = await cursor.fetchone()
            fire_count = row["count"] if row else 0

            conversion = (total_briefs / fire_count * 100) if fire_count > 0 else 0

            return {
                "briefs_this_month": briefs_this_month,
                "total_briefs": total_briefs,
                "avg_novelty": avg_novelty,
                "fire_count": fire_count,
                "conversion_rate": conversion,
            }
    except Exception:
        # Table may not exist yet
        return {
            "briefs_this_month": 0,
            "total_briefs": 0,
            "avg_novelty": None,
            "fire_count": 0,
            "conversion_rate": 0,
        }


def render():
    """Render the dashboard page."""
    st.title("📊 Dashboard")
    st.caption("Vue d'ensemble de SPORE")

    # Initialize database
    run_async(init_database())

    # Get stats
    stats = run_async(get_aggregate_stats())
    runs_data = run_async(get_runs_for_chart())
    top_gaps = run_async(get_top_gaps())
    brief_stats = run_async(_get_brief_stats())

    # System status banner
    status = get_system_status()
    if status["status"] == "running":
        st.info(f"🔄 **Run en cours:** `{status.get('run_id', 'unknown')}` - Lancé à {status.get('started_at', 'N/A')}")

    # ============== KEY METRICS ==============
    st.markdown("### 📈 Métriques clés")

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "Total hypothèses",
            stats["total_hypotheses"],
            help="Nombre total d'hypothèses générées"
        )

    with col2:
        st.metric(
            "Bridge rate",
            format_rate(stats["avg_bridge_rate"]),
            help="Pourcentage moyen de collisions produisant une hypothèse"
        )

    with col3:
        st.metric(
            "Score moyen",
            f"{stats['avg_score']:.3f}" if stats["avg_score"] else "N/A",
            help="Score composite moyen des hypothèses"
        )

    with col4:
        st.metric(
            "Coût cumulé",
            format_cost(stats["total_cost"]),
            help="Coût total en USD de tous les runs"
        )

    with col5:
        last_run = stats["last_run"]
        if last_run:
            last_date = datetime.fromisoformat(last_run["started_at"]).strftime("%d/%m %Hh%M")
            st.metric("Dernier run", last_date)
        else:
            st.metric("Dernier run", "Aucun")

    # ============== BRIEF KPIS ==============
    if brief_stats and brief_stats["total_briefs"] > 0:
        st.markdown("### Research Briefs")

        bc1, bc2, bc3 = st.columns(3)

        with bc1:
            st.metric(
                "Briefs ce mois",
                brief_stats["briefs_this_month"],
                help="Nombre de briefs generes ce mois",
            )
        with bc2:
            conv = brief_stats["conversion_rate"]
            st.metric(
                "Conversion fire -> brief",
                f"{conv:.0f}%" if conv else "N/A",
                help="Pourcentage d'hypotheses fire converties en brief",
            )
        with bc3:
            avg_nov = brief_stats["avg_novelty"]
            st.metric(
                "Novelty score moyen",
                f"{avg_nov:.2f}" if avg_nov else "N/A",
                help="Score de nouveaute moyen des briefs generes",
            )

    st.markdown("---")

    # ============== CHARTS ==============
    chart_col, feedback_col = st.columns([2, 1])

    with chart_col:
        st.markdown("### 📉 Évolution dans le temps")

        if runs_data:
            import pandas as pd

            df = pd.DataFrame(runs_data)
            df["date"] = pd.to_datetime(df["started_at"])

            # Bridge rate chart
            if not df["bridge_rate"].isna().all():
                st.markdown("**Bridge rate par run**")
                chart_data = df[["date", "bridge_rate"]].dropna()
                # Filter out infinite values and convert to percentage
                chart_data = chart_data[chart_data["bridge_rate"].apply(lambda x: x is not None and not pd.isna(x) and abs(x) != float('inf'))]
                if not chart_data.empty:
                    chart_data["bridge_rate"] = chart_data["bridge_rate"] * 100
                    st.line_chart(chart_data.set_index("date")["bridge_rate"])

            # Cost chart
            if not df["total_cost_usd"].isna().all():
                st.markdown("**Coût par run ($)**")
                cost_data = df[["date", "total_cost_usd"]].dropna()
                # Filter out infinite values
                cost_data = cost_data[cost_data["total_cost_usd"].apply(lambda x: x is not None and not pd.isna(x) and abs(x) != float('inf'))]
                if not cost_data.empty:
                    st.bar_chart(cost_data.set_index("date")["total_cost_usd"])
        else:
            st.info("Aucun run complété. Lancez un run pour voir les graphiques.")

    with feedback_col:
        st.markdown("### 🗳️ Feedbacks humains")

        feedback_dist = stats["feedback_dist"]
        if feedback_dist:
            # Calculate counts
            trash = feedback_dist.get("trash", 0)
            interesting = feedback_dist.get("interesting", 0)
            want_test = feedback_dist.get("want_to_test", 0)
            pending = feedback_dist.get(None, 0)

            total_reviewed = trash + interesting + want_test

            if total_reviewed > 0:
                # Create pie chart data
                import pandas as pd

                pie_data = pd.DataFrame({
                    "Feedback": ["🗑️ Poubelle", "🤔 Intéressant", "🔥 À tester"],
                    "Count": [trash, interesting, want_test]
                })

                # Simple bar representation
                st.markdown(f"**🗑️ Poubelle:** {trash}")
                st.progress(trash / max(total_reviewed, 1))

                st.markdown(f"**🤔 Intéressant:** {interesting}")
                st.progress(interesting / max(total_reviewed, 1))

                st.markdown(f"**🔥 À tester:** {want_test}")
                st.progress(want_test / max(total_reviewed, 1))

                st.caption(f"Total reviewé: {total_reviewed} | En attente: {pending}")
            else:
                st.info("Aucune hypothèse reviewée")
        else:
            st.info("Aucune hypothèse")

    st.markdown("---")

    # ============== GAPS & GENOME ==============
    gaps_col, genome_col = st.columns(2)

    with gaps_col:
        st.markdown("### 🕳️ Top 3 gaps récurrents")

        if top_gaps:
            for i, (gap_desc, count) in enumerate(top_gaps[:3], 1):
                st.markdown(f"**{i}.** {gap_desc}")
                st.caption(f"Occurrences: {count}")
        else:
            st.info("Pas assez de données pour identifier les gaps récurrents")

    with genome_col:
        st.markdown("### 🧬 Genome actuel")

        try:
            genome = get_genome()
            st.markdown(f"**Version:** `{genome.version}`")

            # Key parameters
            randomness = genome.randomness
            st.markdown(f"**Distance range:** {randomness.get('distance_min', 'N/A')} - {randomness.get('distance_max', 'N/A')}")
            st.markdown(f"**Cross-discipline ratio:** {randomness.get('cross_discipline_ratio', 'N/A')}")
            st.markdown(f"**Chaos floor:** {randomness.get('chaos_floor', 'N/A')}")

            # Last mutation
            genome_data = genome.to_dict()
            if genome_data.get("last_mutated"):
                st.caption(f"Dernière mutation: {genome_data['last_mutated']}")
                if genome_data.get("mutated_by"):
                    st.caption(f"Par: {genome_data['mutated_by']}")
        except Exception as e:
            st.error(f"Erreur lecture genome: {e}")

    st.markdown("---")

    # ============== RECENT RUNS ==============
    st.markdown("### 🕐 Runs récents")

    runs = run_async(get_all_runs())

    if runs:
        # Show last 5 runs in a table
        import pandas as pd

        runs_df = pd.DataFrame(runs[:10])

        # Format columns
        display_df = runs_df[[
            "id", "started_at", "status", "collisions_requested",
            "hypotheses_generated", "bridge_rate", "total_cost_usd"
        ]].copy()

        display_df.columns = [
            "Run ID", "Date", "Statut", "Collisions",
            "Hypothèses", "Bridge Rate", "Coût"
        ]

        # Format values
        display_df["Bridge Rate"] = display_df["Bridge Rate"].apply(
            lambda x: f"{x*100:.1f}%" if x else "N/A"
        )
        display_df["Coût"] = display_df["Coût"].apply(
            lambda x: f"${x:.4f}" if x else "N/A"
        )
        display_df["Date"] = pd.to_datetime(display_df["Date"]).dt.strftime("%d/%m %H:%M")

        st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        st.info("Aucun run enregistré. Lancez un run depuis la page 'Lancer un run'.")

    # Footer
    st.markdown("---")
    st.caption("🍄 SPORE Dashboard")
