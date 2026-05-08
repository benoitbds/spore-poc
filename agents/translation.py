"""Translation helpers for FR -> EN post-fire content.

Thin re-export of the pure functions implemented in
``scripts/translate_brief_vulgarization.py`` and
``scripts/translate_brief_panel.py``. The public surface is two
async functions that take a JSON payload (``vulgarization_data``
or ``panel_data``) and return the translated payload + warnings +
LLM usage summary. No DB access — persistence is the caller's
responsibility (the ``translation_hook`` node in the post-fire
pipeline handles it).

Both helpers raise ``FrenchInOutputError`` if the LLM output still
contains French fragments (validation STOP), surfaced from the
underlying script implementation.

S7.4 Phase 4 — integration of the existing translation logic into
the LangGraph post-fire subgraph.
"""

from __future__ import annotations

from typing import Any

from logging_config import get_logger

# Re-export the pure helpers from the existing scripts. ``scripts/``
# is a package (``scripts/__init__.py``) so this import resolves
# cleanly from any caller in the project tree. The scripts'
# ``sys.path.insert`` lines at module import are no-ops in the
# package context (project root already on the import path).
from scripts.translate_brief_vulgarization import (
    translate_brief as _translate_vulgarization_impl,
)
from scripts.translate_brief_panel import (
    translate_panel as _translate_panel_impl,
)

# Re-export the FrenchInOutputError from one of the scripts (both
# define equivalent classes; we pick vulgarization arbitrarily —
# isinstance checks against this re-export catch either path because
# downstream callers use ``except Exception`` anyway).
from scripts.translate_brief_vulgarization import FrenchInOutputError  # noqa: F401

log = get_logger("agents.translation")


async def translate_vulgarization_data(
    brief_id: str,
    fr_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Translate vulgarization_data FR -> EN (Nature-grade UK English).

    Pure function: input FR JSON, output EN JSON. No DB access.

    Args:
        brief_id: Brief identifier (logged for tracing only).
        fr_payload: vulgarization_data dict with FR prose
            (title_fr, hypothesis_in_brief, why_it_matters,
            imagine_that, concretely.{intro, phase1, phase2, phase3},
            reviewers_say).

    Returns:
        Tuple of (en_payload, warnings, usage_summary):
          * en_payload — same shape with neutral keys (``title``,
            ``imagine_that``, etc.); ready for ``vulgarization_data_en``.
          * warnings — list of validation warnings (forbidden
            ``discover``, US spellings, length-ratio drift, etc.).
            Per-field; empty when the translation passes every check.
          * usage_summary — dict with cost_usd / input_tokens /
            output_tokens for the per-brief LLM cost.

    Raises:
        FrenchInOutputError when validation detects residual French in
        the EN output. The caller (translation_hook) catches and
        leaves the brief FR-only with a logged error rather than
        crashing the pipeline.
    """
    return await _translate_vulgarization_impl(brief_id, fr_payload)


async def translate_panel_data(
    brief_id: str,
    fr_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Translate panel_data FR -> EN (Nature-grade UK English, passive).

    Pure function: input FR JSON, output EN JSON. No DB access.

    Args:
        brief_id: Brief identifier (logged for tracing only).
        fr_payload: panel_data dict with FR prose
            (reviews[].{strengths, weaknesses, critical_questions,
            recommendation} per reviewer + meta_review.{key_consensus,
            key_disagreements, critical_path, final_recommendation,
            revision_guidance}). Backend tokens (reviewer_persona,
            verdict, scores) are copied verbatim.

    Returns:
        Tuple of (en_payload, warnings, usage_summary). See
        ``translate_vulgarization_data`` for the field semantics.

    Raises:
        FrenchInOutputError when validation detects residual French in
        the EN output. The caller (translation_hook) catches and
        leaves the brief FR-only with a logged error rather than
        crashing the pipeline.
    """
    return await _translate_panel_impl(brief_id, fr_payload)


__all__ = [
    "translate_vulgarization_data",
    "translate_panel_data",
    "FrenchInOutputError",
]
