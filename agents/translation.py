"""Translation helpers for post-fire panel and vulgarization content.

Thin re-export of the pure functions implemented in
``scripts/translate_brief_vulgarization.py`` and
``scripts/translate_brief_panel.py``. The public surface is three
async functions that take a JSON payload (``vulgarization_data``
or ``panel_data``) and return the translated payload + warnings +
LLM usage summary. No DB access — persistence is the caller's
responsibility (the ``translation_hook`` node in the post-fire
pipeline handles it).

FR -> EN helpers raise ``FrenchInOutputError`` if the LLM output still
contains French fragments (validation STOP), surfaced from the
underlying script implementation. The EN -> FR panel helper raises
``EnglishInOutputError`` if its output still reads as English.

S7.4 Phase 4 — integration of the existing translation logic into
the LangGraph post-fire subgraph. S10-A — EN -> FR panel repair used by
the ``normalize_panel_language`` node.
"""

from __future__ import annotations

from collections.abc import Collection
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
    EnglishInOutputError,
    translate_panel as _translate_panel_impl,
    translate_panel_to_fr as _translate_panel_to_fr_impl,
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
            verdict, scores) and the ``FAIL REASON #n:`` marker are
            copied verbatim.

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


async def translate_panel_data_to_fr(
    brief_id: str,
    en_payload: dict[str, Any],
    *,
    review_indices: Collection[int] | None = None,
    include_meta: bool = True,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Translate the English prose of panel_data EN -> FR.

    Pure function: input panel JSON, output panel JSON. No DB access.
    Selective: only the cards at ``review_indices`` and, when
    ``include_meta``, the meta-review are translated; everything else is
    copied verbatim.

    Args:
        brief_id: Brief identifier or trace label (logged for tracing
            only — the briefs row may not exist yet).
        en_payload: panel_data dict whose targeted prose fields are in
            English. Backend tokens (reviewer_persona, verdict, scores)
            and the ``FAIL REASON #n:`` marker are copied verbatim.
        review_indices: Indices of the cards to translate; ``None``
            translates every card.
        include_meta: Whether to translate the meta-review prose.

    Returns:
        Tuple of (fr_payload, warnings, usage_summary). Warnings include
        every prompt-scaffolding strip.

    Raises:
        EnglishInOutputError when a translated field is still detected as
        English by ``graph.lang_guard.detect``. The caller
        (normalize_panel_language) catches and leaves the panel unchanged.
    """
    return await _translate_panel_to_fr_impl(
        brief_id,
        en_payload,
        review_indices=review_indices,
        include_meta=include_meta,
    )


__all__ = [
    "translate_vulgarization_data",
    "translate_panel_data",
    "translate_panel_data_to_fr",
    "FrenchInOutputError",
    "EnglishInOutputError",
]
