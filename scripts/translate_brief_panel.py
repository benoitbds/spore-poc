"""Translate the FR panel review of a research brief into Nature-grade EN.

Reads ``panel_data`` (FR prose) from the briefs table, sends each prose
field — list items translated as ``---``-separated blocks — through an
LLM with a strict scientific-translation prompt, reconstructs the JSON
preserving every backend token verbatim (``reviewer_persona``,
``verdict``, scores), and writes the result to ``panel_data_en``.

Mirror of ``translate_brief_vulgarization.py`` (S7.4 Phase 1+2). Same
validation heuristics (UK spelling, no contractions, no
discover/discovery, length ratio, residual French = STOP). Idempotent:
a brief that already has a non-NULL ``panel_data_en`` is skipped unless
``--force`` is passed.

Voice: ALL fields use the formal Nature-grade passive register typical
of scientific peer-review prose. No active second-person variant
(unlike the vulgarisation ``imagine_that`` field).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import get_llm_client
from logging_config import get_logger, get_token_tracker, reset_token_tracker
from storage import init_database
from storage.database import get_connection

logger = get_logger("translate_brief_panel")


# ── Prompt ─────────────────────────────────────────────────────────────

BASE_PROMPT = """You are a scientific translator specialising in academic peer-review prose. Translate the following French text into English following these strict rules:

REGISTER: Nature editorial — precise, economical, formal authority. No contractions ("do not" not "don't"). No marketing-speak. No "we" (use "the panel", "the reviewer", "the meta-reviewer", or rephrase impersonally).

SPELLING: Use British English consistently. Examples:
- "favourable" not "favorable"
- "analyse" / "analysed" / "analysing" not "analyze"
- "organise" / "organised" not "organize"
- "behaviour" not "behavior"
- "colour" not "color"
- "modelled" / "modelling" not "modeled"
- "centred" not "centered"
- "fibre" not "fiber"
- "metre" not "meter" (the unit)
- "-ise" verb endings, not "-ize" (recognise, characterise, summarise)
- Date format: "1 May 2026" not "May 1, 2026"

If you produce a US spelling, you must self-correct.

VOCABULARY:
- "découverte" / "discovery" : FORBIDDEN. Use "finding", "advance", or rephrase.
- "kill rate" : keep as-is (product term).
- "brief" / "briefs" : keep as-is.
- "panel review" / "panel reviewer" / "the panel" / "the meta-reviewer" : preferred panel vocabulary.
- "collision" : keep for domain meetings.
- "domain" : use for SPORE's scientific domains.
- "hypothesis" / "hypotheses" / "researcher" / "yields" / "verified through Semantic Scholar" : preferred terms.

PRESERVATION:
- Preserve all proper names (people, places, institutions, equipment, software).
- Preserve all numbers, units, dates as-is.
- Preserve all phase references verbatim: "Phase 1", "Phase 2", "Phase 3".
- Preserve all technical terms in the source (chemical names, statistical tests, equations, parameter symbols, DOIs, citation tokens like "[2025]" or "(P450cam, 1DZ8)").
- Preserve markdown formatting (bold, italics, lists) — but do not invent markdown that is not in the source.
- If a French expression has no clean English equivalent, prefer scientific clarity over literal translation.

VOICE: Use PASSIVE voice and impersonal constructions throughout ("the panel notes that...", "the protocol is structured...", "the hypothesis is judged..."). Avoid second-person address. Maintain the formal Nature-grade register typical of academic peer-review prose."""


def _build_string_prompt(french_text: str) -> str:
    """Compose a per-call prompt for a single string field."""
    return (
        f"{BASE_PROMPT}\n\n"
        f"INPUT: {french_text}\n\n"
        "OUTPUT: ONLY the English translation, nothing else. "
        "No preamble, no explanation, no quotes around the translation."
    )


def _build_list_prompt(items: list[str]) -> str:
    """Compose a per-call prompt for a list of strings.

    The items are joined with ``\\n---\\n`` separators; the model is
    asked to return the same separator-delimited structure with
    translated items in the same order. Preserves item count exactly.
    """
    body = "\n---\n".join(items)
    return (
        f"{BASE_PROMPT}\n\n"
        f"You will translate {len(items)} items separated by lines containing only `---`. "
        "Return the same structure: each translated item separated by a line containing only `---`. "
        "Do NOT add or remove items. Do NOT renumber. Translate each item in order.\n\n"
        f"INPUT ({len(items)} items):\n"
        f"{body}\n\n"
        "OUTPUT: ONLY the translated items separated by `---`, in the same order, "
        "no preamble, no explanation, no item count header."
    )


# ── Output JSON shape ──────────────────────────────────────────────────
#
# panel_data (FR source) and panel_data_en (this script writes) share the
# same shape — only the prose is translated; tokens, scores and structure
# are copied verbatim:
#
# {
#   reviews: [
#     {
#       reviewer_persona  COPY (token EN)
#       overall_score     COPY (number)
#       verdict           COPY (token EN)
#       confidence        COPY (number)
#       strengths[]       TRANSLATE (list of FR prose -> EN prose)
#       weaknesses[]      TRANSLATE
#       critical_questions[]  TRANSLATE
#       recommendation    TRANSLATE (string)
#       funding_programs[] COPY (only on funding_strategist; tokens + EN agency names)
#     }
#   ],
#   meta_review: {
#     verdict             COPY (token EN)
#     consensus_score     COPY (number)
#     key_consensus[]     TRANSLATE
#     key_disagreements[] TRANSLATE
#     critical_path       TRANSLATE
#     final_recommendation TRANSLATE
#     revision_guidance   TRANSLATE (optional)
#     brief_quality_gate  COPY (token / boolean)
#     llm_verdict         COPY (token)
#     llm_consensus_score COPY (number)
#     verdict_override_reason COPY (string, may be FR but rarely shown)
#   }
# }


REVIEWER_LIST_FIELDS = ["strengths", "weaknesses", "critical_questions"]
REVIEWER_STRING_FIELDS = ["recommendation"]
# revision_guidance is an array of bullet strings in every published
# brief; key_consensus / key_disagreements likewise. critical_path and
# final_recommendation are single-paragraph strings.
META_LIST_FIELDS = ["key_consensus", "key_disagreements", "revision_guidance"]
META_STRING_FIELDS = ["critical_path", "final_recommendation"]


# ── Placeholder detection ──────────────────────────────────────────────
#
# Some panel_data entries carry pipeline placeholder strings — manual-
# review markers, parser-error messages, or rubric prompts that leaked
# from the LLM template. Feeding these to the translator triggers
# hallucinations: the model fabricates 1000+ chars of plausible
# scientific prose from a 20-char placeholder. We detect placeholders
# pre-translation and either skip them or pass through a fixed EN
# equivalent that preserves the operator signal.

_PLACEHOLDER_MAP: dict[str, str] = {
    "manual review needed": "Manual review needed.",
    "manual review needed.": "Manual review needed.",
    "recommandation actionnable en 2-3 phrases.": "Actionable recommendation in 2-3 sentences.",
    "unable to parse review": "Unable to parse review.",
    "review parsing failed": "Review parsing failed.",
}


def _placeholder_passthrough(text: str) -> str | None:
    """Return the EN equivalent for a known placeholder, or None.

    Matching is case-insensitive on whitespace-stripped input. Used to
    short-circuit translation for pipeline error/marker strings — these
    cause LLM hallucinations because the input is too short to anchor
    a faithful translation.
    """
    if not text:
        return None
    key = text.strip().lower()
    return _PLACEHOLDER_MAP.get(key)


# ── Validation ─────────────────────────────────────────────────────────

_FORBIDDEN_BARE = re.compile(r"\b(discover|discovery|discoveries|discovered|discovering)\b", re.IGNORECASE)
_FORBIDDEN_NEGATION = re.compile(
    r"\b(not\s+(?:a\s+)?(?:discover|discovery|discoveries|discovered|discovering))\b",
    re.IGNORECASE,
)

_CONTRACTIONS = re.compile(
    r"\b("
    r"don't|doesn't|didn't|can't|won't|wouldn't|shouldn't|couldn't|"
    r"isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't|"
    r"I'm|you're|we're|they're|it's|that's|there's|here's|"
    r"I'll|you'll|we'll|they'll|he'll|she'll|"
    r"I've|you've|we've|they've|"
    r"I'd|you'd|we'd|they'd|he'd|she'd"
    r")\b",
    re.IGNORECASE,
)

_US_SPELLINGS = re.compile(
    r"\b("
    r"analyze|analyzes|analyzed|analyzing|"
    r"organize|organizes|organized|organizing|"
    r"recognize|recognizes|recognized|recognizing|"
    r"characterize|characterizes|characterized|characterizing|"
    r"summarize|summarizes|summarized|summarizing|"
    r"realize|realizes|realized|realizing|"
    r"emphasize|emphasizes|emphasized|emphasizing|"
    r"color|colors|colored|coloring|"
    r"favor|favors|favored|favoring|favorable|"
    r"behavior|behaviors|"
    r"modeled|modeling|"
    r"centered|centering|"
    r"fiber|fibers"
    r")\b",
    re.IGNORECASE,
)

_FR_FRAGMENT_INLINE = re.compile(
    r"\b("
    r"c'est|qu'il|qu'elle|qu'on|qu'ils|"
    r"pourquoi|parce que|c'est-à-dire|"
    r"hypothèse|hypothèses|"
    r"propriétés?|prédictions?|expérience|expériences|"
    r"l'hypothèse|l'analogie|"
    r"très|déjà|aussi|toujours|"
    r"recommande|estime|souligne|considère|"
    r"protocole|découverte|méthodologue|relecteur|relecteurs"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


class FrenchInOutputError(Exception):
    """Raised when the LLM output appears to still be in French."""


def _validate_text(field_path: str, fr_text: str, en_text: str) -> list[str]:
    """Run quality checks on a single translated field.

    Returns warnings; raises ``FrenchInOutputError`` on residual French.
    """
    warnings: list[str] = []

    fr_inline = _FR_FRAGMENT_INLINE.findall(en_text)
    if fr_inline:
        raise FrenchInOutputError(
            f"{field_path}: French fragment(s) detected in EN output: "
            f"{fr_inline[:5]!r}"
        )

    bare_matches = _FORBIDDEN_BARE.findall(en_text)
    if bare_matches:
        negated = _FORBIDDEN_NEGATION.findall(en_text)
        if len(bare_matches) > len(negated):
            warnings.append(
                f"{field_path}: forbidden 'discover/discovery' usage outside negation: "
                f"{bare_matches}"
            )

    contractions = _CONTRACTIONS.findall(en_text)
    if contractions:
        warnings.append(
            f"{field_path}: contractions detected ({contractions[:5]}); "
            "register requires written-out forms"
        )

    us_hits = _US_SPELLINGS.findall(en_text)
    if us_hits:
        warnings.append(
            f"{field_path}: US spelling(s) detected ({us_hits[:5]}); "
            "register requires British English"
        )

    fr_len = max(len(fr_text), 1)
    ratio = len(en_text) / fr_len
    if not 0.70 <= ratio <= 1.25:
        warnings.append(
            f"{field_path}: EN/FR length ratio {ratio:.2f} outside 0.70-1.25"
        )

    return warnings


# ── Translation primitives ─────────────────────────────────────────────


def _strip_wrapping_bold(text: str) -> str:
    stripped = text.strip()
    if not (stripped.startswith("**") and stripped.endswith("**")):
        return text
    inner = stripped[2:-2]
    if "**" in inner:
        return text
    return inner.strip()


def _strip_wrappers(text: str) -> str:
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and len(text) > 2:
        text = text[1:-1]
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    return _strip_wrapping_bold(text)


# "discover/discovery" is FORBIDDEN by the SPORE EN style guide. The LLM
# occasionally lets it slip despite the prompt. Map each form to a
# context-neutral replacement post-translation; the validator will still
# warn if the family appears in negation contexts (which we leave
# alone).
_DISCOVER_REPLACEMENTS = [
    (re.compile(r"\bdiscoveries\b"), "findings"),
    (re.compile(r"\bdiscovery\b"), "finding"),
    (re.compile(r"\bdiscovering\b"), "identifying"),
    (re.compile(r"\bdiscovered\b"), "identified"),
    (re.compile(r"\bdiscovers\b"), "identifies"),
    (re.compile(r"\bdiscover\b"), "identify"),
    (re.compile(r"\bDiscoveries\b"), "Findings"),
    (re.compile(r"\bDiscovery\b"), "Finding"),
    (re.compile(r"\bDiscovering\b"), "Identifying"),
    (re.compile(r"\bDiscovered\b"), "Identified"),
    (re.compile(r"\bDiscovers\b"), "Identifies"),
    (re.compile(r"\bDiscover\b"), "Identify"),
]


def _replace_forbidden_discover(text: str) -> str:
    """Apply the discover->finding/identify replacement.

    Skipped when the surrounding context is an explicit negation
    ("not a discovery") so the /about precedent stays intact.
    """
    if not text:
        return text
    if _FORBIDDEN_NEGATION.search(text):
        # If there's any negation in the text, skip — keeping the safe
        # path. The validator's bare-vs-negated diff will tell us if we
        # left a non-negated occurrence behind.
        return text
    for pat, repl in _DISCOVER_REPLACEMENTS:
        text = pat.sub(repl, text)
    return text


async def _llm_call(client, prompt: str, max_tokens: int = 2500) -> tuple[str, dict[str, int]]:
    """Single LLM call with cost tracking. Returns (text, usage)."""
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )

    tracker = get_token_tracker()
    cost = tracker.log_call(
        agent="translate_panel",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )
    text = _strip_wrappers(response.content)
    text = _replace_forbidden_discover(text)
    return text, {
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": cost,
    }


async def _translate_string(
    client,
    field_path: str,
    fr_text: str,
) -> tuple[str, dict[str, int], list[str]]:
    """Translate a single string. Returns (en_text, usage, warnings)."""
    if not fr_text or not fr_text.strip():
        return "", {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, []
    # Pipeline placeholders short-circuit the LLM call to avoid
    # hallucinations on minimal-context inputs.
    placeholder_en = _placeholder_passthrough(fr_text)
    if placeholder_en is not None:
        logger.info(
            "translation_placeholder_passthrough",
            field=field_path,
            fr=fr_text[:40],
            en=placeholder_en,
        )
        return placeholder_en, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, []
    prompt = _build_string_prompt(fr_text)
    en_text, usage = await _llm_call(client, prompt)
    warnings = _validate_text(field_path, fr_text, en_text)
    return en_text, usage, warnings


async def _translate_list(
    client,
    field_path: str,
    items: list[str],
) -> tuple[list[str], dict[str, int], list[str]]:
    """Translate a list of strings as one ``---``-separated block.

    If the LLM returns a different item count, the script falls back to
    translating each item individually (more calls, higher cost, but
    preserves correctness).
    """
    items = [s for s in items if isinstance(s, str)]
    if not items:
        return [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, []

    # Per-item placeholder check before composing the LLM prompt. Any
    # item that maps to a known placeholder is passed through and the
    # remaining items are sent to the LLM. Result is reassembled in
    # original order. A list whose items are ALL placeholders short-
    # circuits entirely.
    en_items_resolved: list[str | None] = [None] * len(items)
    items_to_translate: list[tuple[int, str]] = []
    for i, fr_item in enumerate(items):
        placeholder_en = _placeholder_passthrough(fr_item)
        if placeholder_en is not None:
            en_items_resolved[i] = placeholder_en
            logger.info(
                "translation_placeholder_passthrough",
                field=f"{field_path}[{i}]",
                fr=fr_item[:40],
                en=placeholder_en,
            )
        else:
            items_to_translate.append((i, fr_item))

    if not items_to_translate:
        return (
            [s or "" for s in en_items_resolved],
            {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            [],
        )

    # Build the prompt only on the non-placeholder subset.
    items = [fr for _, fr in items_to_translate]
    prompt = _build_list_prompt(items)
    en_text, usage = await _llm_call(client, prompt, max_tokens=3500)

    # Split on lines containing only ``---`` (allow surrounding whitespace).
    parts = re.split(r"\n\s*---\s*\n", en_text.strip())
    parts = [p.strip() for p in parts if p.strip()]

    total_in = usage["input_tokens"]
    total_out = usage["output_tokens"]
    total_cost = usage["cost_usd"]

    if len(parts) != len(items):
        # Fallback: per-item translation. Logged so the operator can see
        # the LLM split-marker mismatch and tune the prompt if it happens
        # often.
        logger.warning(
            "list_split_mismatch_fallback",
            field=field_path,
            expected=len(items),
            received=len(parts),
        )
        all_warnings: list[str] = []
        for sub_idx, (orig_idx, fr_item) in enumerate(items_to_translate):
            sub_path = f"{field_path}[{orig_idx}]"
            en_item, sub_usage, sub_warnings = await _translate_string(
                client, sub_path, fr_item
            )
            en_items_resolved[orig_idx] = en_item
            all_warnings.extend(sub_warnings)
            total_in += sub_usage["input_tokens"]
            total_out += sub_usage["output_tokens"]
            total_cost += sub_usage["cost_usd"]
        return (
            [s or "" for s in en_items_resolved],
            {
                "input_tokens": total_in,
                "output_tokens": total_out,
                "cost_usd": total_cost,
            },
            all_warnings,
        )

    # Reassemble translated parts back into the original-indexed list.
    all_warnings: list[str] = []
    for (orig_idx, fr_item), en_item in zip(items_to_translate, parts):
        en_items_resolved[orig_idx] = en_item
        all_warnings.extend(
            _validate_text(f"{field_path}[{orig_idx}]", fr_item, en_item)
        )

    return (
        [s or "" for s in en_items_resolved],
        {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_usd": total_cost,
        },
        all_warnings,
    )


# ── Per-brief translation orchestration ────────────────────────────────


async def translate_panel(
    brief_id: str,
    fr_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, float]]:
    """Translate a single brief's panel_data payload.

    Returns (en_payload, warnings, usage_summary).
    Raises FrenchInOutputError on a STOP signal (residual French).
    """
    client = get_llm_client("translation")

    en_payload: dict[str, Any] = {}
    all_warnings: list[str] = []
    total_in = 0
    total_out = 0
    total_cost = 0.0

    def _accum(usage: dict[str, int]) -> None:
        nonlocal total_in, total_out, total_cost
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)
        total_cost += usage.get("cost_usd", 0.0)

    # ── reviews[] ──────────────────────────────────────────────────────
    fr_reviews = fr_payload.get("reviews") or []
    en_reviews: list[dict[str, Any]] = []
    for ridx, fr_r in enumerate(fr_reviews):
        if not isinstance(fr_r, dict):
            en_reviews.append(fr_r)
            continue

        en_r: dict[str, Any] = {}
        # Copy verbatim: backend tokens + numbers + opaque blobs.
        for k, v in fr_r.items():
            if k in REVIEWER_LIST_FIELDS or k in REVIEWER_STRING_FIELDS:
                continue  # translated below
            en_r[k] = v

        persona = fr_r.get("reviewer_persona", f"reviewer_{ridx}")

        for field in REVIEWER_LIST_FIELDS:
            items = fr_r.get(field) or []
            field_path = f"reviews[{ridx}/{persona}].{field}"
            en_items, usage, w = await _translate_list(client, field_path, items)
            en_r[field] = en_items
            _accum(usage)
            all_warnings.extend(w)
            logger.info(
                "translated_reviewer_field",
                brief_id=brief_id,
                persona=persona,
                field=field,
                items=len(en_items),
            )

        for field in REVIEWER_STRING_FIELDS:
            text = fr_r.get(field) or ""
            field_path = f"reviews[{ridx}/{persona}].{field}"
            en_text, usage, w = await _translate_string(client, field_path, text)
            en_r[field] = en_text
            _accum(usage)
            all_warnings.extend(w)
            logger.info(
                "translated_reviewer_field",
                brief_id=brief_id,
                persona=persona,
                field=field,
                en_preview=en_text[:80],
            )

        en_reviews.append(en_r)
    en_payload["reviews"] = en_reviews

    # ── meta_review ────────────────────────────────────────────────────
    fr_meta = fr_payload.get("meta_review") or {}
    en_meta: dict[str, Any] = {}
    for k, v in fr_meta.items():
        if k in META_LIST_FIELDS or k in META_STRING_FIELDS:
            continue
        en_meta[k] = v

    for field in META_LIST_FIELDS:
        items = fr_meta.get(field) or []
        en_items, usage, w = await _translate_list(
            client, f"meta_review.{field}", items
        )
        en_meta[field] = en_items
        _accum(usage)
        all_warnings.extend(w)
        logger.info(
            "translated_meta_field",
            brief_id=brief_id,
            field=field,
            items=len(en_items),
        )

    for field in META_STRING_FIELDS:
        text = fr_meta.get(field) or ""
        en_text, usage, w = await _translate_string(
            client, f"meta_review.{field}", text
        )
        en_meta[field] = en_text
        _accum(usage)
        all_warnings.extend(w)
        logger.info(
            "translated_meta_field",
            brief_id=brief_id,
            field=field,
            en_preview=en_text[:80],
        )

    en_payload["meta_review"] = en_meta

    return (
        en_payload,
        all_warnings,
        {
            "cost_usd": total_cost,
            "input_tokens": total_in,
            "output_tokens": total_out,
        },
    )


# ── DB helpers ─────────────────────────────────────────────────────────


async def fetch_target_briefs(
    *,
    brief_id: str | None,
    missing_only: bool,
    process_all: bool,
) -> list[tuple[str, str | None, str | None]]:
    async with get_connection() as conn:
        if brief_id:
            cursor = await conn.execute(
                "SELECT id, panel_data, panel_data_en "
                "FROM briefs WHERE id = ?",
                (brief_id,),
            )
            rows = await cursor.fetchall()
            if not rows:
                raise SystemExit(f"brief not found: {brief_id}")
            return [
                (r["id"], r["panel_data"], r["panel_data_en"]) for r in rows
            ]

        base = (
            "SELECT id, panel_data, panel_data_en "
            "FROM briefs WHERE panel_data IS NOT NULL"
        )
        if missing_only:
            base += " AND panel_data_en IS NULL"
        elif not process_all:
            raise SystemExit(
                "no target specified — pass --brief-id, --missing-only, or --all"
            )
        base += " ORDER BY id"
        cursor = await conn.execute(base)
        rows = await cursor.fetchall()
        return [(r["id"], r["panel_data"], r["panel_data_en"]) for r in rows]


async def write_translation(brief_id: str, en_payload: dict[str, Any]) -> None:
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE briefs SET panel_data_en = ? WHERE id = ?",
            (json.dumps(en_payload, ensure_ascii=False), brief_id),
        )
        await conn.commit()


# ── CLI ────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Translate brief panel review FR -> EN and write to "
            "panel_data_en."
        ),
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--brief-id",
        type=str,
        default=None,
        metavar="SPR-XXXX-YYYY",
        help="Translate only this brief.",
    )
    target.add_argument(
        "--all",
        action="store_true",
        help="Translate every brief that has a FR panel_data payload.",
    )
    target.add_argument(
        "--missing-only",
        action="store_true",
        help="Translate only briefs where panel_data_en IS NULL.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the EN translation but do NOT write to the DB.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-translate even if panel_data_en is already populated. "
            "Default: skip rows that already have an EN payload."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Print extra per-brief progress lines: per-brief duration, "
            "running total cost, total elapsed wall time."
        ),
    )
    return parser


async def main() -> None:
    import time

    args = _build_arg_parser().parse_args()

    await init_database()
    reset_token_tracker()

    rows = await fetch_target_briefs(
        brief_id=args.brief_id,
        missing_only=args.missing_only,
        process_all=args.all,
    )
    if not rows:
        print("nothing to do — no briefs match the selection")
        return

    print(f"found {len(rows)} brief{'s' if len(rows) != 1 else ''} to process")

    sep = "─" * 78
    ok = 0
    skipped = 0
    failed = 0
    halted = False
    running_cost = 0.0
    batch_started_at = time.monotonic()

    for i, (brief_id, fr_json, en_json) in enumerate(rows, 1):
        print(f"\n[{i}/{len(rows)}] {brief_id}")
        if not fr_json:
            print(f"  skip {brief_id}: no FR panel_data")
            skipped += 1
            continue
        if en_json and not args.force:
            print(f"  skip {brief_id}: already has panel_data_en (use --force to redo)")
            skipped += 1
            continue

        try:
            fr_payload = json.loads(fr_json)
        except Exception as exc:
            print(f"  FAIL {brief_id}: cannot parse FR JSON ({exc})")
            failed += 1
            continue

        brief_started_at = time.monotonic()
        try:
            en_payload, warnings, usage = await translate_panel(brief_id, fr_payload)
        except FrenchInOutputError as exc:
            print(f"  STOP {brief_id}: {exc}")
            print("  Halting batch — fix prompt calibration before continuing.")
            failed += 1
            halted = True
            break
        except Exception as exc:
            print(f"  FAIL {brief_id}: {exc}")
            failed += 1
            continue
        brief_elapsed = time.monotonic() - brief_started_at
        running_cost += usage["cost_usd"]

        print(f"  translated {brief_id}")
        print(f"  cost ${usage['cost_usd']:.4f} ({usage['input_tokens']:,} in / {usage['output_tokens']:,} out)")

        if args.verbose:
            elapsed_total = time.monotonic() - batch_started_at
            print(
                f"  [verbose] this brief: {brief_elapsed:.1f}s | "
                f"running cost: ${running_cost:.4f} | "
                f"total elapsed: {int(elapsed_total // 60)}m {int(elapsed_total % 60)}s"
            )

        if warnings:
            print(f"  WARNINGS ({len(warnings)}):")
            for w in warnings[:8]:
                print(f"    - {w}")
            if len(warnings) > 8:
                print(f"    ... and {len(warnings) - 8} more")

        if args.dry_run:
            print(f"\n{sep}\n[DRY-RUN] {brief_id} — EN payload:\n{sep}")
            print(json.dumps(en_payload, indent=2, ensure_ascii=False))
            print(sep)
            print(f"  [DRY-RUN] {brief_id}: not writing to DB")
        else:
            await write_translation(brief_id, en_payload)
            print(f"  wrote panel_data_en for {brief_id}")

        ok += 1

    print()
    label = "translated (dry-run)" if args.dry_run else "written"
    print(f"done: {ok} {label}, {skipped} skipped, {failed} failed")
    if halted:
        print("⚠ batch halted — French detected in EN output")

    tracker = get_token_tracker()
    summary = tracker.summary()
    print(
        f"LLM cost total: ${summary['total_cost_usd']:.4f} "
        f"({summary['total_input_tokens']:,} in / "
        f"{summary['total_output_tokens']:,} out, "
        f"{summary['total_calls']} calls)"
    )


if __name__ == "__main__":
    asyncio.run(main())
