"""KPI Card component — a colored metric card."""

import streamlit as st


def kpi_card(
    label: str,
    value: str,
    delta: str | None = None,
    delta_color: str = "normal",
    help_text: str | None = None,
) -> None:
    """Render a KPI card with label, value, and optional delta.

    Args:
        label: Metric label.
        value: Main value to display.
        delta: Optional delta string (e.g. "+12%").
        delta_color: "normal" | "inverse" | "off".
        help_text: Optional tooltip text.
    """
    st.metric(
        label=label,
        value=value,
        delta=delta,
        delta_color=delta_color,
        help=help_text,
    )


def verdict_badge(verdict: str) -> str:
    """Return a colored markdown badge for a verdict string."""
    mapping = {
        "publish_brief": (":green[PUBLISH]", "#10B981"),
        "revise_and_resubmit": (":orange[REVISE]", "#F59E0B"),
        "reject": (":red[REJECT]", "#EF4444"),
        "a_tester": (":green[🔥 a_tester]", "#10B981"),
        "intéressant": (":orange[🤔 intéressant]", "#F59E0B"),
        "interessant": (":orange[🤔 intéressant]", "#F59E0B"),
        "poubelle": (":red[🗑️ poubelle]", "#EF4444"),
        "novel": (":green[NOVEL]", "#10B981"),
        "incremental": (":orange[INCREMENTAL]", "#F59E0B"),
        "already_explored": (":orange[EXPLORED]", "#F59E0B"),
        "already_proven": (":red[PROVEN]", "#EF4444"),
    }
    return mapping.get(verdict, (f":gray[{verdict}]",))[0]


def score_color(score: float | None, thresholds: tuple[float, float] = (0.4, 0.7)) -> str:
    """Return a Streamlit color token for a score."""
    if score is None:
        return "gray"
    if score >= thresholds[1]:
        return "green"
    if score >= thresholds[0]:
        return "orange"
    return "red"


def status_icon(status: str | None) -> str:
    """Return emoji icon for a human override status."""
    return {
        "approved": "✅",
        "rejected": "❌",
        "flagged": "🔍",
        "complete": "⏳",
        "pending": "⏳",
    }.get(status or "pending", "⏳")
