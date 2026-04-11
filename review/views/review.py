"""Review Page - Human validation of generated hypotheses."""

import sys
from pathlib import Path
import csv
import io
from datetime import datetime

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from helpers import run_async
from storage import (
    init_database,
    list_hypotheses,
    update_hypothesis_feedback,
    update_hypothesis_auto_feedback,
    get_hypothesis_auto_feedback,
    clear_all_auto_feedback,
)
from storage.database import get_connection
from models.hypothesis import Hypothesis, HumanFeedback, HypothesisStatus
from agents.reviewer import AutoFeedback, review_hypothesis


async def get_distinct_runs():
    """Get distinct run dates/IDs for filtering."""
    async with get_connection() as conn:
        cursor = await conn.execute("""
            SELECT DISTINCT date(generated_at) as run_date
            FROM hypotheses
            ORDER BY run_date DESC
            LIMIT 30
        """)
        rows = await cursor.fetchall()
        return [row["run_date"] for row in rows]


async def get_distinct_disciplines():
    """Get distinct disciplines (parent domains)."""
    async with get_connection() as conn:
        cursor = await conn.execute("""
            SELECT DISTINCT
                json_extract(collision_json, '$.domain_a.parent_domain') as parent_a,
                json_extract(collision_json, '$.domain_b.parent_domain') as parent_b
            FROM hypotheses
        """)
        rows = await cursor.fetchall()

        disciplines = set()
        for row in rows:
            if row["parent_a"]:
                disciplines.add(row["parent_a"])
            if row["parent_b"]:
                disciplines.add(row["parent_b"])

        return sorted(disciplines)


async def get_auto_feedback_map(hypothesis_ids: list[str]) -> dict[str, AutoFeedback]:
    """Load auto-feedback for multiple hypotheses."""
    result = {}
    async with get_connection() as conn:
        for h_id in hypothesis_ids:
            cursor = await conn.execute(
                "SELECT auto_feedback_json FROM hypotheses WHERE id = ?",
                (h_id,),
            )
            row = await cursor.fetchone()
            if row and row["auto_feedback_json"]:
                try:
                    result[h_id] = AutoFeedback.model_validate_json(row["auto_feedback_json"])
                except Exception:
                    pass
    return result


async def has_auto_feedback(hypothesis_id: str) -> bool:
    """Check if a hypothesis has auto-feedback."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT auto_feedback_json FROM hypotheses WHERE id = ?",
            (hypothesis_id,),
        )
        row = await cursor.fetchone()
        return row is not None and row["auto_feedback_json"] is not None


def sort_by_score(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    """Sort hypotheses by composite score descending."""
    def get_score(h: Hypothesis) -> float:
        if h.scores and h.scores.composite is not None:
            return h.scores.composite
        return 0.0
    return sorted(hypotheses, key=get_score, reverse=True)


def generate_csv_export(hypotheses: list[Hypothesis]) -> str:
    """Generate CSV export of review results."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "hypothesis_id", "domain_a", "domain_b", "distance",
        "bridge_summary", "bridge_type", "score_composite",
        "human_feedback", "human_comment", "generated_at",
    ])

    for h in hypotheses:
        writer.writerow([
            h.id,
            h.collision.domain_a.name,
            h.collision.domain_b.name,
            f"{h.collision.distance_score:.3f}",
            h.bridge.summary,
            h.bridge.type.value,
            f"{h.scores.composite:.3f}" if h.scores and h.scores.composite else "",
            h.human_feedback.value if h.human_feedback else "pending",
            h.human_feedback_comment or "",
            h.generated_at.isoformat() if h.generated_at else "",
        ])

    return output.getvalue()


def render_score_bar(score: float, label: str, inverse: bool = False) -> None:
    """Render a score as a progress bar with color coding."""
    if inverse:
        color = "🟢" if score < 0.3 else "🟡" if score < 0.6 else "🔴"
    else:
        color = "🔴" if score < 0.3 else "🟡" if score < 0.6 else "🟢"

    st.markdown(f"{color} **{label}:** {score:.2f}")
    st.progress(score)


def render_auto_scores(auto_feedback: AutoFeedback) -> None:
    """Render auto-feedback scores in a compact format."""
    scores = auto_feedback.scores
    cols = st.columns(4)
    with cols[0]:
        color = "🟢" if scores.originalite >= 0.6 else "🟡" if scores.originalite >= 0.3 else "🔴"
        st.caption(f"{color} Originalité: {scores.originalite:.2f}")
    with cols[1]:
        color = "🟢" if scores.faisabilite >= 0.6 else "🟡" if scores.faisabilite >= 0.3 else "🔴"
        st.caption(f"{color} Faisabilité: {scores.faisabilite:.2f}")
    with cols[2]:
        color = "🟢" if scores.coherence >= 0.6 else "🟡" if scores.coherence >= 0.3 else "🔴"
        st.caption(f"{color} Cohérence: {scores.coherence:.2f}")
    with cols[3]:
        color = "🟢" if scores.impact_realisme >= 0.6 else "🟡" if scores.impact_realisme >= 0.3 else "🔴"
        st.caption(f"{color} Impact: {scores.impact_realisme:.2f}")


def render():
    """Render the review page."""
    st.title("🔬 Review")
    st.caption("Interface de validation humaine des hypothèses générées")

    # Initialize database
    run_async(init_database())

    # Load all hypotheses
    all_hypotheses = run_async(list_hypotheses(limit=500))

    if not all_hypotheses:
        st.warning("Aucune hypothèse trouvée. Lancez un run depuis la page 'Lancer un run'.")
        return

    # Sort by composite score
    all_hypotheses = sort_by_score(all_hypotheses)

    # Load auto-feedback for all hypotheses
    hypothesis_ids = [h.id for h in all_hypotheses]
    auto_feedback_map = run_async(get_auto_feedback_map(hypothesis_ids))

    # Get filter options
    run_dates = run_async(get_distinct_runs())
    disciplines = run_async(get_distinct_disciplines())

    # ============== SIDEBAR FILTERS ==============
    st.sidebar.header("🔧 Filtres")

    # Run date filter
    run_date_options = ["Tous"] + run_dates
    run_filter = st.sidebar.selectbox("Date du run", run_date_options, index=0)

    # Discipline filter
    discipline_options = ["Toutes"] + disciplines
    discipline_filter = st.sidebar.selectbox("Discipline", discipline_options, index=0)

    # Score threshold
    min_score = st.sidebar.slider(
        "Score minimum",
        min_value=0.0,
        max_value=1.0,
        value=0.0,
        step=0.05,
    )

    # Status filter
    status_options = ["Tous", "generated", "curated", "human_reviewed"]
    status_filter = st.sidebar.selectbox("Statut", status_options, index=0)

    # Feedback filter
    feedback_options = ["Tous", "À reviewer", "🗑️ Poubelle", "🤔 Intéressant", "🔥 À tester"]
    feedback_filter = st.sidebar.selectbox("Feedback humain", feedback_options, index=0)

    # Feedback source filter
    st.sidebar.markdown("---")
    source_options = ["Tous", "Humain", "Auto", "Non reviewé"]
    source_filter = st.sidebar.selectbox("Source du feedback", source_options, index=0)

    # Apply filters
    filtered = all_hypotheses.copy()

    # Run date filter
    if run_filter != "Tous":
        filtered = [
            h for h in filtered
            if h.generated_at and h.generated_at.strftime("%Y-%m-%d") == run_filter
        ]

    # Discipline filter
    if discipline_filter != "Toutes":
        filtered = [
            h for h in filtered
            if (h.collision.domain_a.parent_domain == discipline_filter or
                h.collision.domain_b.parent_domain == discipline_filter)
        ]

    # Score filter
    if min_score > 0:
        filtered = [
            h for h in filtered
            if h.scores and h.scores.composite and h.scores.composite >= min_score
        ]

    # Status filter
    if status_filter != "Tous":
        filtered = [h for h in filtered if h.status.value == status_filter]

    # Feedback filter
    if feedback_filter == "À reviewer":
        filtered = [h for h in filtered if h.human_feedback is None]
    elif feedback_filter == "🗑️ Poubelle":
        filtered = [h for h in filtered if h.human_feedback == HumanFeedback.TRASH]
    elif feedback_filter == "🤔 Intéressant":
        filtered = [h for h in filtered if h.human_feedback == HumanFeedback.INTERESTING]
    elif feedback_filter == "🔥 À tester":
        filtered = [h for h in filtered if h.human_feedback == HumanFeedback.WANT_TO_TEST]

    # Source filter
    if source_filter == "Humain":
        # Has human feedback (not auto)
        filtered = [
            h for h in filtered
            if h.human_feedback is not None and not (h.human_feedback_comment or "").startswith("[Auto]")
        ]
    elif source_filter == "Auto":
        # Has auto feedback
        filtered = [h for h in filtered if h.id in auto_feedback_map]
    elif source_filter == "Non reviewé":
        # No feedback at all
        filtered = [h for h in filtered if h.human_feedback is None and h.id not in auto_feedback_map]

    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**{len(filtered)}** hypothèses affichées")
    st.sidebar.markdown(f"(sur {len(all_hypotheses)} total)")

    # CSV Export
    st.sidebar.markdown("---")
    st.sidebar.header("📥 Export")
    csv_data = generate_csv_export(all_hypotheses)
    st.sidebar.download_button(
        label="⬇️ Télécharger CSV",
        data=csv_data,
        file_name=f"spore_review_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    # ============== TOP METRICS ==============
    st.markdown("---")

    total = len(all_hypotheses)
    reviewed = sum(1 for h in all_hypotheses if h.human_feedback is not None)
    auto_reviewed = len(auto_feedback_map)
    pending = total - max(reviewed, auto_reviewed)
    trash_count = sum(1 for h in all_hypotheses if h.human_feedback == HumanFeedback.TRASH)
    interesting_count = sum(1 for h in all_hypotheses if h.human_feedback == HumanFeedback.INTERESTING)
    want_to_test_count = sum(1 for h in all_hypotheses if h.human_feedback == HumanFeedback.WANT_TO_TEST)

    progress_pct = reviewed / total if total > 0 else 0
    st.markdown(f"### 📊 Progression: {reviewed}/{total} hypothèses reviewées ({progress_pct:.0%})")
    st.progress(progress_pct)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📋 Total", total)
    col2.metric("⏳ À reviewer", pending)
    col3.metric("🗑️ Poubelle", trash_count)
    col4.metric("🤔 Intéressant", interesting_count)
    col5.metric("🔥 À tester", want_to_test_count)

    # ============== AUTO-FEEDBACK ALL BUTTON ==============
    st.markdown("---")

    # Count hypotheses without any feedback (human or auto)
    no_feedback_hypotheses = [
        h for h in all_hypotheses
        if h.human_feedback is None and h.id not in auto_feedback_map
    ]

    # Count hypotheses with auto-feedback (for recalibration)
    auto_feedback_hypotheses = [
        h for h in all_hypotheses
        if h.id in auto_feedback_map and h.human_feedback is None
    ]

    auto_col1, auto_col2, auto_col3 = st.columns([2, 2, 2])

    with auto_col1:
        auto_all_disabled = len(no_feedback_hypotheses) == 0
        if st.button(
            f"🤖 Auto-feedback ({len(no_feedback_hypotheses)})",
            use_container_width=True,
            disabled=auto_all_disabled,
            type="primary" if not auto_all_disabled else "secondary",
        ):
            st.session_state["run_auto_all"] = True

    with auto_col2:
        recalibrate_disabled = len(auto_feedback_hypotheses) == 0
        if st.button(
            f"🔄 Recalibrer ({len(auto_feedback_hypotheses)})",
            use_container_width=True,
            disabled=recalibrate_disabled,
        ):
            st.session_state["run_recalibrate"] = True

    with auto_col3:
        # Show stats
        if auto_feedback_map:
            verdicts = [af.verdict.lower() for af in auto_feedback_map.values()]
            n_trash = sum(1 for v in verdicts if v == "poubelle")
            n_interesting = sum(1 for v in verdicts if v in ("intéressant", "interessant"))
            n_test = sum(1 for v in verdicts if v == "a_tester")
            total = len(verdicts)
            st.caption(f"Stats auto: 🗑️{n_trash/total:.0%} 🤔{n_interesting/total:.0%} 🔥{n_test/total:.0%}")

    # Run auto-feedback on all if requested
    if st.session_state.get("run_auto_all"):
        st.session_state["run_auto_all"] = False

        progress_bar = st.progress(0, text="Démarrage de l'auto-review...")
        status_text = st.empty()

        for i, h in enumerate(no_feedback_hypotheses):
            progress = (i + 1) / len(no_feedback_hypotheses)
            progress_bar.progress(progress, text=f"Review {i+1}/{len(no_feedback_hypotheses)}")
            status_text.caption(f"Analyse: {h.collision.domain_a.name} × {h.collision.domain_b.name}")

            try:
                auto_fb = run_async(review_hypothesis(h))
                run_async(update_hypothesis_auto_feedback(h.id, auto_fb))
            except Exception as e:
                st.error(f"Erreur sur {h.id}: {e}")

        progress_bar.progress(1.0, text="Terminé!")
        status_text.success(f"Auto-review terminé pour {len(no_feedback_hypotheses)} hypothèses.")
        st.rerun()

    # Run recalibration on hypotheses with auto-feedback
    if st.session_state.get("run_recalibrate"):
        st.session_state["run_recalibrate"] = False

        progress_bar = st.progress(0, text="Recalibration en cours...")
        status_text = st.empty()

        # Clear all auto-feedback first
        status_text.caption("Suppression des anciens feedbacks auto...")
        run_async(clear_all_auto_feedback())

        # Re-run on all hypotheses that had auto-feedback
        for i, h in enumerate(auto_feedback_hypotheses):
            progress = (i + 1) / len(auto_feedback_hypotheses)
            progress_bar.progress(progress, text=f"Recalibration {i+1}/{len(auto_feedback_hypotheses)}")
            status_text.caption(f"Analyse: {h.collision.domain_a.name} × {h.collision.domain_b.name}")

            try:
                auto_fb = run_async(review_hypothesis(h))
                run_async(update_hypothesis_auto_feedback(h.id, auto_fb))
            except Exception as e:
                st.error(f"Erreur sur {h.id}: {e}")

        progress_bar.progress(1.0, text="Recalibration terminée!")
        status_text.success(f"Recalibration terminée pour {len(auto_feedback_hypotheses)} hypothèses.")
        st.rerun()

    # ============== HYPOTHESIS LIST ==============
    st.markdown("---")
    st.markdown("## 🔬 Hypothèses")

    if not filtered:
        st.info("Aucune hypothèse ne correspond aux filtres.")
        return

    for i, hypothesis in enumerate(filtered):
        is_first_pending = (i == 0 and hypothesis.human_feedback is None)

        # Get auto-feedback for this hypothesis
        auto_feedback = auto_feedback_map.get(hypothesis.id)

        # Expander title
        score_str = f"{hypothesis.scores.composite:.2f}" if hypothesis.scores and hypothesis.scores.composite else "N/A"

        # Determine feedback badge
        feedback_emoji = ""
        if hypothesis.human_feedback:
            feedback_emoji = {
                HumanFeedback.TRASH: " 🗑️",
                HumanFeedback.INTERESTING: " 🤔",
                HumanFeedback.WANT_TO_TEST: " 🔥",
            }.get(hypothesis.human_feedback, "")
        elif auto_feedback:
            # Show auto badge
            auto_verdict_emoji = {
                "poubelle": " 🗑️🤖",
                "intéressant": " 🤔🤖",
                "interessant": " 🤔🤖",
                "a_tester": " 🔥🤖",
            }.get(auto_feedback.verdict.lower(), " 🤖")
            feedback_emoji = auto_verdict_emoji

        if hypothesis.impact_analysis and hypothesis.impact_analysis.one_liner:
            title = hypothesis.impact_analysis.one_liner
            if len(title) > 100:
                title = title[:100] + "..."
            expander_title = f"[{score_str}]{feedback_emoji} {title}"
        else:
            expander_title = f"[{score_str}]{feedback_emoji} {hypothesis.collision.domain_a.name} × {hypothesis.collision.domain_b.name}"

        with st.expander(expander_title, expanded=is_first_pending):
            # Header
            header_cols = st.columns([3, 1, 1])

            with header_cols[0]:
                st.caption(f"🆔 `{hypothesis.id}`")
                st.caption(f"📅 {hypothesis.generated_at.strftime('%Y-%m-%d %H:%M') if hypothesis.generated_at else 'N/A'}")

            with header_cols[1]:
                if hypothesis.scores and hypothesis.scores.composite:
                    st.metric("Score", f"{hypothesis.scores.composite:.3f}")

            with header_cols[2]:
                st.metric("Distance", f"{hypothesis.collision.distance_score:.3f}")

            st.markdown("---")

            # Impact Analysis
            if hypothesis.impact_analysis:
                impact = hypothesis.impact_analysis
                st.markdown(f"### 💡 {impact.one_liner}")

                impact_badge = {
                    "incremental": "🔵 Incrémental",
                    "significatif": "🟢 Significatif",
                    "transformatif": "🟠 Transformatif",
                    "civilisationnel": "🔴 Civilisationnel",
                }.get(impact.score_impact.value, "⚪ N/A")

                imp_cols = st.columns([2, 1, 1])
                with imp_cols[0]:
                    st.info(impact.vulgarisation)
                with imp_cols[1]:
                    st.metric("Impact", impact_badge.split()[1])
                with imp_cols[2]:
                    st.metric("Marché", impact.taille_marche_estimee)

                st.markdown("---")

            # Collision
            st.markdown("### 💥 Collision")
            coll_cols = st.columns(2)
            with coll_cols[0]:
                st.markdown(f"**Domaine A:** {hypothesis.collision.domain_a.name}")
                if hypothesis.collision.domain_a.parent_domain:
                    st.caption(f"Discipline: {hypothesis.collision.domain_a.parent_domain}")
            with coll_cols[1]:
                st.markdown(f"**Domaine B:** {hypothesis.collision.domain_b.name}")
                if hypothesis.collision.domain_b.parent_domain:
                    st.caption(f"Discipline: {hypothesis.collision.domain_b.parent_domain}")

            st.markdown("---")

            # Bridge
            st.markdown("### 🌉 Bridge")
            st.info(hypothesis.bridge.summary)
            st.markdown(f"**Type:** `{hypothesis.bridge.type.value}`")

            with st.expander("📖 Mécanisme détaillé", expanded=False):
                st.markdown(hypothesis.bridge.mechanism)

            st.markdown("---")

            # Predictions
            if hypothesis.predictions:
                st.markdown("### 🎯 Prédictions")
                for j, pred in enumerate(hypothesis.predictions, 1):
                    st.markdown(f"**{j}.** {pred.statement}")
                    st.caption(f"📏 {pred.metric} | 📊 {pred.expected_range}")
                st.markdown("---")

            # Kill condition
            st.markdown("### ⚠️ Kill condition")
            st.warning(hypothesis.kill_condition)

            st.markdown("---")

            # Scores
            if hypothesis.scores:
                st.markdown("### 📈 Scores")
                score_cols = st.columns(3)

                with score_cols[0]:
                    render_score_bar(hypothesis.scores.novelty, "Nouveauté")
                    render_score_bar(hypothesis.scores.coherence, "Cohérence")

                with score_cols[1]:
                    render_score_bar(hypothesis.scores.testability, "Testabilité")
                    render_score_bar(hypothesis.scores.impact_potential, "Impact")

                with score_cols[2]:
                    render_score_bar(hypothesis.scores.hallucination_risk, "Hallucination", inverse=True)

                st.markdown("---")

            # Feedback
            st.markdown("### ✅ Votre feedback")

            # Show current human feedback
            if hypothesis.human_feedback:
                current = {
                    HumanFeedback.TRASH: "🗑️ Poubelle",
                    HumanFeedback.INTERESTING: "🤔 Intéressant",
                    HumanFeedback.WANT_TO_TEST: "🔥 À tester",
                }.get(hypothesis.human_feedback, "")
                st.success(f"Feedback humain: {current}")

            # Show auto feedback if exists (and no human override)
            if auto_feedback and not hypothesis.human_feedback:
                verdict_display = {
                    "poubelle": "🗑️ Poubelle",
                    "intéressant": "🤔 Intéressant",
                    "interessant": "🤔 Intéressant",
                    "a_tester": "🔥 À tester",
                }.get(auto_feedback.verdict.lower(), auto_feedback.verdict)

                st.info(f"**[Auto]** Verdict: {verdict_display}")
                st.caption(f"💬 {auto_feedback.comment}")
                render_auto_scores(auto_feedback)
                st.caption("_Cliquez sur un bouton ci-dessous pour override le verdict auto._")

            # Feedback buttons
            btn_cols = st.columns(5)

            with btn_cols[0]:
                if st.button("🗑️ Poubelle", key=f"trash_{hypothesis.id}", use_container_width=True):
                    run_async(update_hypothesis_feedback(hypothesis.id, HumanFeedback.TRASH))
                    st.rerun()

            with btn_cols[1]:
                if st.button("🤔 Intéressant", key=f"interesting_{hypothesis.id}", use_container_width=True):
                    run_async(update_hypothesis_feedback(hypothesis.id, HumanFeedback.INTERESTING))
                    st.rerun()

            with btn_cols[2]:
                if st.button("🔥 À tester", key=f"test_{hypothesis.id}", use_container_width=True):
                    run_async(update_hypothesis_feedback(hypothesis.id, HumanFeedback.WANT_TO_TEST))
                    st.rerun()

            with btn_cols[3]:
                # Auto-feedback button for single hypothesis
                auto_disabled = auto_feedback is not None
                if st.button(
                    "🤖 Auto",
                    key=f"auto_{hypothesis.id}",
                    use_container_width=True,
                    disabled=auto_disabled,
                ):
                    with st.spinner("Analyse en cours..."):
                        try:
                            auto_fb = run_async(review_hypothesis(hypothesis))
                            run_async(update_hypothesis_auto_feedback(hypothesis.id, auto_fb))
                            st.success(f"Verdict: {auto_fb.verdict}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erreur: {e}")

            with btn_cols[4]:
                if hypothesis.human_feedback:
                    if st.button("↩️ Effacer", key=f"clear_{hypothesis.id}", use_container_width=True):
                        run_async(update_hypothesis_feedback(hypothesis.id, None))
                        st.rerun()

            # Comment
            default_comment = ""
            if auto_feedback and not hypothesis.human_feedback:
                default_comment = f"[Auto] {auto_feedback.comment}"
            elif hypothesis.human_feedback_comment:
                default_comment = hypothesis.human_feedback_comment

            comment = st.text_area(
                "Commentaire",
                value=default_comment,
                key=f"comment_{hypothesis.id}",
                placeholder="Vos remarques...",
            )

            if st.button("💾 Sauvegarder", key=f"save_{hypothesis.id}"):
                feedback = hypothesis.human_feedback or HumanFeedback.INTERESTING
                run_async(update_hypothesis_feedback(hypothesis.id, feedback, comment=comment))
                st.success("Sauvegardé!")
                st.rerun()

    # Footer
    st.markdown("---")
    st.caption("🍄 SPORE Dashboard")
