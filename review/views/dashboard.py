"""Dashboard Page — SPORE overview: KPIs, activity chart, pipeline health."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import altair as alt
import pandas as pd
import streamlit as st

from helpers import run_async, format_cost
from storage import init_database
from storage.database import get_connection
from components.kpi_card import kpi_card, verdict_badge


# ── Data loaders ─────────────────────────────────────────────

async def _load_kpis():
    """Load the 4 header KPIs."""
    await init_database()

    async with get_connection() as conn:
        # Briefs this month
        cursor = await conn.execute("""
            SELECT COUNT(*) as n FROM briefs
            WHERE created_at >= date('now', 'start of month')
        """)
        briefs_month = (await cursor.fetchone())["n"]

        # Briefs previous month
        cursor = await conn.execute("""
            SELECT COUNT(*) as n FROM briefs
            WHERE created_at >= date('now', 'start of month', '-1 month')
              AND created_at < date('now', 'start of month')
        """)
        briefs_prev = (await cursor.fetchone())["n"]

        # Fire rate: a_tester / total reviewed hypotheses
        cursor = await conn.execute("""
            SELECT COUNT(*) as n FROM hypotheses
            WHERE auto_feedback_json IS NOT NULL
        """)
        total_reviewed = (await cursor.fetchone())["n"]

        cursor = await conn.execute("""
            SELECT COUNT(*) as n FROM hypotheses
            WHERE auto_feedback_json LIKE '%a_tester%'
        """)
        fire_count = (await cursor.fetchone())["n"]

        # Average novelty score
        cursor = await conn.execute("""
            SELECT AVG(novelty_score) as avg FROM briefs
            WHERE novelty_score IS NOT NULL
        """)
        row = await cursor.fetchone()
        avg_novelty = row["avg"]

        # Cost this month
        cursor = await conn.execute("""
            SELECT SUM(total_cost_usd) as total FROM runs
            WHERE started_at >= date('now', 'start of month')
              AND status = 'completed'
        """)
        row = await cursor.fetchone()
        cost_month = row["total"] or 0

    return {
        "briefs_month": briefs_month,
        "briefs_prev": briefs_prev,
        "fire_rate": (fire_count / total_reviewed) if total_reviewed else 0,
        "avg_novelty": avg_novelty,
        "cost_month": cost_month,
    }


async def _load_activity_data(days: int = 30):
    """Get daily collisions and cumulative briefs for the last N days."""
    async with get_connection() as conn:
        # Runs per day
        cursor = await conn.execute("""
            SELECT
                DATE(started_at) as day,
                SUM(collisions_processed) as collisions
            FROM runs
            WHERE started_at >= date('now', '-' || ? || ' days')
              AND status = 'completed'
            GROUP BY DATE(started_at)
            ORDER BY day
        """, (days,))
        runs = [dict(row) for row in await cursor.fetchall()]

        # Briefs per day
        cursor = await conn.execute("""
            SELECT DATE(created_at) as day, COUNT(*) as n
            FROM briefs
            WHERE created_at >= date('now', '-' || ? || ' days')
            GROUP BY DATE(created_at)
            ORDER BY day
        """, (days,))
        briefs = [dict(row) for row in await cursor.fetchall()]

    return runs, briefs


async def _load_latest_brief():
    """Get the most recently created brief."""
    async with get_connection() as conn:
        cursor = await conn.execute("""
            SELECT * FROM briefs
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)


async def _load_pipeline_health():
    """Load gate pass rate, bridge rate, and reviewer distribution."""
    async with get_connection() as conn:
        # Average gate pass rate
        cursor = await conn.execute("""
            SELECT AVG(json_extract(metadata_json, '$.rate')) as rate
            FROM metrics
            WHERE metric_name = 'gate_pass_rate'
            ORDER BY recorded_at DESC
            LIMIT 30
        """)
        row = await cursor.fetchone()
        gate_rate = row["rate"] if row else None

        # Fallback: compute from runs
        if not gate_rate:
            cursor = await conn.execute("""
                SELECT AVG(bridge_rate) as rate FROM runs
                WHERE bridge_rate IS NOT NULL
                ORDER BY started_at DESC
                LIMIT 10
            """)
            row = await cursor.fetchone()
            bridge_rate = row["rate"] if row else None
        else:
            cursor = await conn.execute("""
                SELECT AVG(bridge_rate) as rate FROM runs
                WHERE bridge_rate IS NOT NULL
                ORDER BY started_at DESC
                LIMIT 10
            """)
            row = await cursor.fetchone()
            bridge_rate = row["rate"] if row else None

        # Reviewer distribution (last 30 days)
        cursor = await conn.execute("""
            SELECT auto_feedback_json FROM hypotheses
            WHERE auto_feedback_json IS NOT NULL
              AND created_at >= date('now', '-30 days')
        """)
        rows = await cursor.fetchall()

        counts = {"poubelle": 0, "intéressant": 0, "a_tester": 0}
        for row in rows:
            try:
                data = json.loads(row["auto_feedback_json"])
                v = data.get("verdict", "").lower()
                if v == "poubelle":
                    counts["poubelle"] += 1
                elif v in ("intéressant", "interessant"):
                    counts["intéressant"] += 1
                elif v == "a_tester":
                    counts["a_tester"] += 1
            except (json.JSONDecodeError, TypeError):
                continue

    return {
        "gate_rate": gate_rate,
        "bridge_rate": bridge_rate,
        "reviewer_dist": counts,
    }


# ── Render ───────────────────────────────────────────────────

def render():
    """Render the Dashboard page."""
    st.title("📊 Dashboard")
    st.caption("Vue d'ensemble de SPORE — briefs, hypothèses, santé du pipeline")

    kpis = run_async(_load_kpis())

    # ── Row 1: 4 KPIs ────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        delta = None
        if kpis["briefs_prev"]:
            delta_n = kpis["briefs_month"] - kpis["briefs_prev"]
            delta = f"{'+' if delta_n >= 0 else ''}{delta_n} vs mois préc."
        kpi_card("📄 Briefs ce mois", str(kpis["briefs_month"]), delta=delta)

    with c2:
        rate = kpis["fire_rate"] * 100
        kpi_card("🔥 Taux 🔥", f"{rate:.1f}%", help_text="a_tester / total hypothèses reviewées")

    with c3:
        nov = kpis["avg_novelty"]
        kpi_card("✨ Novelty moyen", f"{nov:.2f}" if nov else "N/A", help_text="Score de nouveauté moyen des briefs")

    with c4:
        kpi_card("💰 Coût ce mois", format_cost(kpis["cost_month"]))

    st.markdown("---")

    # ── Row 2: Activity chart + latest brief ─────────────────
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Activité SPORE — 30 jours")
        runs, briefs = run_async(_load_activity_data(days=30))

        if runs or briefs:
            # Build full date range
            end = datetime.now().date()
            start = end - timedelta(days=30)
            dates = pd.date_range(start=start, end=end, freq="D").date

            runs_df = pd.DataFrame(runs) if runs else pd.DataFrame(columns=["day", "collisions"])
            briefs_df = pd.DataFrame(briefs) if briefs else pd.DataFrame(columns=["day", "n"])

            # Normalize dates
            df = pd.DataFrame({"day": dates})
            df["day"] = pd.to_datetime(df["day"])
            if not runs_df.empty:
                runs_df["day"] = pd.to_datetime(runs_df["day"])
                df = df.merge(runs_df, on="day", how="left")
            else:
                df["collisions"] = 0
            df["collisions"] = df["collisions"].fillna(0)

            if not briefs_df.empty:
                briefs_df["day"] = pd.to_datetime(briefs_df["day"])
                df = df.merge(briefs_df, on="day", how="left")
            else:
                df["n"] = 0
            df["n"] = df["n"].fillna(0)
            df["briefs_cumul"] = df["n"].cumsum()

            # Altair: bars for collisions + line for cumulative briefs
            base = alt.Chart(df).encode(x=alt.X("day:T", title="Date"))
            bars = base.mark_bar(color="#10B981", opacity=0.6).encode(
                y=alt.Y("collisions:Q", title="Collisions/jour", axis=alt.Axis(titleColor="#10B981")),
                tooltip=["day:T", "collisions:Q"],
            )
            line = base.mark_line(color="#F59E0B", point=True, strokeWidth=2).encode(
                y=alt.Y("briefs_cumul:Q", title="Briefs cumulés", axis=alt.Axis(titleColor="#F59E0B")),
                tooltip=["day:T", "briefs_cumul:Q"],
            )
            chart = alt.layer(bars, line).resolve_scale(y="independent").properties(height=280)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Pas encore d'activité à afficher.")

    with right:
        st.subheader("Dernier brief")
        latest = run_async(_load_latest_brief())
        if latest:
            brief_id = latest["id"]
            # Load title from JSON if available
            title = brief_id
            domains = []
            if latest.get("brief_json_path"):
                try:
                    data = json.loads(Path(latest["brief_json_path"]).read_text(encoding="utf-8"))
                    title = data.get("sharpened", {}).get("title", brief_id)
                    domains = data.get("domains", [])
                except (FileNotFoundError, json.JSONDecodeError):
                    pass

            with st.container(border=True):
                st.markdown(f"**{title[:80]}**")
                if domains:
                    st.caption(" × ".join(domains))
                st.caption(f"`{brief_id}`")

                nov = latest.get("novelty_score")
                panel = latest.get("panel_consensus_score")
                verdict = latest.get("panel_verdict", "?")

                mc1, mc2 = st.columns(2)
                with mc1:
                    st.metric("Novelty", f"{nov:.2f}" if nov else "N/A", label_visibility="visible")
                with mc2:
                    st.metric("Panel", f"{panel:.1f}" if panel else "N/A", label_visibility="visible")

                st.markdown(verdict_badge(verdict))

                if st.button("Voir le brief →", use_container_width=True, type="primary"):
                    st.session_state["selected_brief_id"] = brief_id
                    st.session_state["nav"] = "📄 Research Briefs"
                    st.rerun()
        else:
            st.info("Aucun brief généré pour le moment.")

    st.markdown("---")

    # ── Row 3: Pipeline health ───────────────────────────────
    st.subheader("Santé du pipeline")
    health = run_async(_load_pipeline_health())

    h1, h2, h3 = st.columns(3)

    with h1:
        gate = health["gate_rate"]
        gate_pct = gate * 100 if gate else None
        color = "normal"
        help_text = "Cible 30-60%. Actuellement "
        if gate_pct is not None:
            if 30 <= gate_pct <= 60:
                help_text += "dans la cible ✓"
            elif gate_pct < 30:
                help_text += "trop strict"
            else:
                help_text += "trop permissif"
        kpi_card(
            "🚪 Gate pass rate",
            f"{gate_pct:.0f}%" if gate_pct else "N/A",
            help_text=help_text,
        )

    with h2:
        bridge = health["bridge_rate"]
        bridge_pct = bridge * 100 if bridge else None
        kpi_card(
            "🌉 Bridge rate",
            f"{bridge_pct:.0f}%" if bridge_pct else "N/A",
            help_text="Cible > 70%. % collisions produisant une hypothèse.",
        )

    with h3:
        st.caption("**📋 Distribution reviewer (30j)**")
        dist = health["reviewer_dist"]
        total = sum(dist.values())
        if total > 0:
            df = pd.DataFrame([
                {"verdict": "🗑️ poubelle", "count": dist["poubelle"], "color": "#EF4444"},
                {"verdict": "🤔 intéressant", "count": dist["intéressant"], "color": "#F59E0B"},
                {"verdict": "🔥 a_tester", "count": dist["a_tester"], "color": "#10B981"},
            ])
            chart = alt.Chart(df).mark_bar().encode(
                x=alt.X("count:Q", title=""),
                y=alt.Y("verdict:N", sort="-x", title=""),
                color=alt.Color("color:N", scale=None, legend=None),
                tooltip=["verdict", "count"],
            ).properties(height=120)
            st.altair_chart(chart, use_container_width=True)
            st.caption(f"Total: {total} hypothèses")
        else:
            st.info("Aucune donnée reviewer.")
