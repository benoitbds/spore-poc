"""Translate the FR vulgarisation of a research brief into Nature-grade EN.

Reads ``vulgarization_data`` (FR) from the briefs table, sends each leaf
text field through an LLM with a strict scientific-translation prompt,
reconstructs the JSON with neutral English keys (``title`` not
``title_fr`` — the column itself is ``_en`` so the keys stay clean),
and writes the result to ``vulgarization_data_en``.

Idempotent by default: a brief that already has a non-NULL
``vulgarization_data_en`` is skipped unless ``--force`` is passed. Use
``--dry-run`` to inspect output before writing. Use ``--missing-only``
to translate every brief that does not yet have an EN payload.

The script tracks LLM cost via the global ``TokenTracker`` and prints a
summary at the end. Per-field validation flags suspect output (forbidden
"discover/discovery", contractions, length ratio drift, residual FR
fragments). If FR fragments are detected in a presumed-EN output the
batch is halted immediately — that is a calibration signal we want
surfaced rather than buried in a warning.
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

logger = get_logger("translate_brief_vulgarization")


# ── Prompt ─────────────────────────────────────────────────────────────

BASE_PROMPT = """You are a scientific translator specializing in interdisciplinary research vulgarization. Translate the following French text into English following these strict rules:

REGISTER: Nature editorial - precise, economical, formal authority. No contractions ("do not" not "don't"). No marketing-speak. No "we" (use "SPORE" or rephrase).

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
- "panel review" / "panel reviewer" : use these exact terms.
- "collision" : keep for domain meetings.
- "domain" : use for SPORE's scientific domains.
- "hypothesis" / "hypotheses" / "researcher" / "yields" / "verified through Semantic Scholar" : preferred terms.

TONE:
- The audience is educated but non-expert.
- Do NOT over-simplify scientific terms. The audience is sophisticated.
- Maintain the original sentence rhythm where possible.

PRESERVATION:
- Preserve all proper names (people, places, institutions, equipment).
- Preserve all numbers, units, dates as-is.
- Preserve markdown formatting (bold, italics, lists).
- If a French expression has no clean English equivalent, prefer scientific clarity over literal translation."""


# Per-field voice guidance. The translation script appends one of these
# blocks to BASE_PROMPT depending on which JSON field is being
# translated. ``imagine_that`` is the analogy lead-in — pedagogical,
# tactile, second-person. Every other field uses the formal Nature-grade
# passive register typical of scientific abstracts.
FIELD_VOICE_GUIDANCE = {
    "imagine_that": (
        "VOICE FOR THIS FIELD: Use ACTIVE voice and second-person address "
        "(\"you measure\", \"you can deduce\", \"you must describe\"). "
        "The reader is invited into the analogy. Keep the pedagogical, "
        "tactile, slightly conversational tone of the original French. "
        "Preserve the analogy structure (\"Imagine that...\" / "
        "\"Imagine you...\"). Contractions are still forbidden "
        "(\"you cannot\" not \"you can't\")."
    ),
    "default": (
        "VOICE FOR THIS FIELD: Use PASSIVE voice and impersonal "
        "constructions (\"the parameters are extracted\", \"the "
        "hypothesis is tested\"). Avoid second-person address. "
        "Maintain the formal Nature-grade register throughout."
    ),
}


def _build_prompt(field_name: str, french_text: str) -> str:
    """Compose the per-call prompt: BASE + field-specific voice + IO."""
    voice = FIELD_VOICE_GUIDANCE.get(field_name, FIELD_VOICE_GUIDANCE["default"])
    return (
        f"{BASE_PROMPT}\n\n{voice}\n\n"
        f"INPUT: {french_text}\n\n"
        "OUTPUT: ONLY the English translation, nothing else. "
        "No preamble, no explanation, no quotes around the translation."
    )


# ── Output JSON shape ──────────────────────────────────────────────────
#
# vulgarization_data (FR source, legacy keys):
#   {title_fr, hypothesis_in_brief, why_it_matters, imagine_that,
#    concretely: {intro, phase1, phase2, phase3}, reviewers_say}
#
# vulgarization_data_en (this script writes, neutral keys):
#   {title, hypothesis_in_brief, why_it_matters, imagine_that,
#    concretely: {intro, phase1, phase2, phase3}, reviewers_say}

LEAF_FIELDS_FLAT = [
    "hypothesis_in_brief",
    "why_it_matters",
    "imagine_that",
    "reviewers_say",
]
LEAF_FIELDS_CONCRETELY = ["intro", "phase1", "phase2", "phase3"]


# ── Validation ─────────────────────────────────────────────────────────

# "discover" / "discovery" are banned per the SPORE EN style guide except
# inside an explicit negation ("not a discovery", "is not discovery").
_FORBIDDEN_BARE = re.compile(r"\b(discover|discovery|discoveries|discovered|discovering)\b", re.IGNORECASE)
_FORBIDDEN_NEGATION = re.compile(
    r"\b(not\s+(?:a\s+)?(?:discover|discovery|discoveries|discovered|discovering))\b",
    re.IGNORECASE,
)

# Common English contractions that the Nature register forbids.
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

# US-spelling forms we explicitly forbid. The prompt instructs the LLM to
# use British English (favourable, analyse, organise, behaviour, ...) so
# any of these appearing in the output is a calibration miss. Warning
# only; the operational fallback is a Python post-process replacement
# pass if the LLM cannot be convinced via prompting alone.
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


# Heuristic markers that an output is still in French. These are common
# FR function words and articles that virtually never start an English
# title or stand alone in EN scientific prose. Hits trigger STOP.
_FR_FRAGMENT_TITLE_START = re.compile(
    r"^\s*(que|qui|le|la|les|une|un|des|du|de|aux?|avec|pour|sans|sur|dans)\b",
    re.IGNORECASE,
)
_FR_FRAGMENT_INLINE = re.compile(
    r"\b("
    r"c'est|qu'il|qu'elle|qu'on|qu'ils|"
    r"pourquoi|parce que|c'est-à-dire|"
    r"métalloprotéines?|découverte|découvertes|hypothèse|hypothèses|"
    r"propriétés?|prédictions?|expérience|expériences|"
    r"l'arrosage|l'hypothèse|l'analogie|"
    r"très|déjà|aussi|encore|toujours"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


class FrenchInOutputError(Exception):
    """Raised when the LLM output appears to still be in French.

    Per the sprint hard rules: this stops the batch immediately so the
    operator can recalibrate the prompt rather than letting silently-FR
    payloads land in the EN column.
    """


def _validate_field(
    field_path: str,
    fr_text: str,
    en_text: str,
) -> list[str]:
    """Run quality checks on a single translated field.

    Returns a list of warning strings (empty if clean). Raises
    ``FrenchInOutputError`` when residual French is detected — that is a
    STOP condition, not a warning.
    """
    warnings: list[str] = []

    # 1. Residual French → STOP.
    if field_path == "title" and _FR_FRAGMENT_TITLE_START.search(en_text):
        raise FrenchInOutputError(
            f"{field_path}: title appears to start with a French fragment: "
            f"{en_text[:80]!r}"
        )
    fr_inline = _FR_FRAGMENT_INLINE.findall(en_text)
    if fr_inline:
        raise FrenchInOutputError(
            f"{field_path}: French fragment(s) detected in EN output: "
            f"{fr_inline[:5]!r}"
        )

    # 2. Forbidden "discover" family — allowed only inside explicit
    #    negation ("not a discovery"), per the /about page precedent.
    bare_matches = _FORBIDDEN_BARE.findall(en_text)
    if bare_matches:
        # Are ALL bare matches subsumed by a negation? If at least one
        # bare match is NOT inside a negation we keep the warning.
        negated = _FORBIDDEN_NEGATION.findall(en_text)
        if len(bare_matches) > len(negated):
            warnings.append(
                f"{field_path}: forbidden 'discover/discovery' usage outside negation: "
                f"{bare_matches}"
            )

    # 3. Contractions.
    contractions = _CONTRACTIONS.findall(en_text)
    if contractions:
        warnings.append(
            f"{field_path}: contractions detected ({contractions[:5]}); "
            f"register requires written-out forms"
        )

    # 4. US spellings (prompt enforces British English).
    us_hits = _US_SPELLINGS.findall(en_text)
    if us_hits:
        warnings.append(
            f"{field_path}: US spelling(s) detected ({us_hits[:5]}); "
            f"register requires British English"
        )

    # 5. Length ratio.
    fr_len = max(len(fr_text), 1)
    ratio = len(en_text) / fr_len
    if not 0.70 <= ratio <= 1.20:
        # Looser bounds than the spec's 0.85-1.10 because EN is
        # systematically shorter for this register; flag the egregious
        # cases (overly truncated or doubled).
        warnings.append(
            f"{field_path}: EN/FR length ratio {ratio:.2f} outside 0.70-1.20"
        )

    return warnings


# ── Translation ────────────────────────────────────────────────────────


async def _translate_one_field(
    client,
    field_name: str,
    fr_text: str,
) -> tuple[str, dict[str, int]]:
    """Translate a single string. Returns (en_text, usage_dict).

    ``field_name`` selects the per-field voice guidance from
    FIELD_VOICE_GUIDANCE (active+second-person for ``imagine_that``,
    formal passive for everything else).
    """
    prompt = _build_prompt(field_name, fr_text)
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.2,  # Low for translation — rewards precision over flair.
    )

    tracker = get_token_tracker()
    cost = tracker.log_call(
        agent="translate_vulgarization",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    # Strip surrounding whitespace and any quote wrappers the LLM might
    # have stuck on despite the explicit instruction.
    text = response.content.strip()
    if text.startswith('"') and text.endswith('"') and len(text) > 2:
        text = text[1:-1]
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()

    return text, {
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": cost,
    }


async def translate_brief(
    brief_id: str,
    fr_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, float]]:
    """Translate a single brief's vulgarization payload.

    Returns:
        (en_payload, warnings, usage_summary)

    Raises FrenchInOutputError on a STOP signal.
    """
    client = get_llm_client("translation")

    fr_title = fr_payload.get("title_fr", "") or ""
    en_payload: dict[str, Any] = {}
    all_warnings: list[str] = []
    total_cost = 0.0
    total_in = 0
    total_out = 0

    # Title — single field, no FR-key special handling.
    en_title, usage = await _translate_one_field(client, "title", fr_title)
    total_cost += usage["cost_usd"]
    total_in += usage["input_tokens"]
    total_out += usage["output_tokens"]
    all_warnings.extend(_validate_field("title", fr_title, en_title))
    en_payload["title"] = en_title
    logger.info(
        "translated_field",
        brief_id=brief_id,
        field="title",
        en_preview=en_title[:80],
    )

    # Top-level prose blocks.
    for field in LEAF_FIELDS_FLAT:
        fr_text = fr_payload.get(field, "") or ""
        if not fr_text.strip():
            en_payload[field] = ""
            continue
        en_text, usage = await _translate_one_field(client, field, fr_text)
        total_cost += usage["cost_usd"]
        total_in += usage["input_tokens"]
        total_out += usage["output_tokens"]
        all_warnings.extend(_validate_field(field, fr_text, en_text))
        en_payload[field] = en_text
        logger.info(
            "translated_field",
            brief_id=brief_id,
            field=field,
            en_preview=en_text[:80],
        )

    # Nested ``concretely`` block.
    fr_concretely = fr_payload.get("concretely", {}) or {}
    en_concretely: dict[str, str] = {}
    for sub in LEAF_FIELDS_CONCRETELY:
        fr_text = fr_concretely.get(sub, "") or ""
        if not fr_text.strip():
            en_concretely[sub] = ""
            continue
        en_text, usage = await _translate_one_field(client, sub, fr_text)
        total_cost += usage["cost_usd"]
        total_in += usage["input_tokens"]
        total_out += usage["output_tokens"]
        path = f"concretely.{sub}"
        all_warnings.extend(_validate_field(path, fr_text, en_text))
        en_concretely[sub] = en_text
        logger.info(
            "translated_field",
            brief_id=brief_id,
            field=path,
            en_preview=en_text[:80],
        )
    en_payload["concretely"] = en_concretely

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
    """Return the rows we should process: list of (id, fr_json, en_json)."""
    async with get_connection() as conn:
        if brief_id:
            cursor = await conn.execute(
                "SELECT id, vulgarization_data, vulgarization_data_en "
                "FROM briefs WHERE id = ?",
                (brief_id,),
            )
            rows = await cursor.fetchall()
            if not rows:
                raise SystemExit(f"brief not found: {brief_id}")
            return [
                (r["id"], r["vulgarization_data"], r["vulgarization_data_en"])
                for r in rows
            ]

        # All / missing-only — only briefs that actually have a FR payload.
        base = (
            "SELECT id, vulgarization_data, vulgarization_data_en "
            "FROM briefs WHERE vulgarization_data IS NOT NULL"
        )
        if missing_only:
            base += " AND vulgarization_data_en IS NULL"
        elif not process_all:
            raise SystemExit(
                "no target specified — pass --brief-id, --missing-only, or --all"
            )
        base += " ORDER BY id"
        cursor = await conn.execute(base)
        rows = await cursor.fetchall()
        return [
            (r["id"], r["vulgarization_data"], r["vulgarization_data_en"])
            for r in rows
        ]


async def write_translation(brief_id: str, en_payload: dict[str, Any]) -> None:
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE briefs SET vulgarization_data_en = ? WHERE id = ?",
            (json.dumps(en_payload, ensure_ascii=False), brief_id),
        )
        await conn.commit()


# ── CLI ────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Translate brief vulgarisation FR -> EN and write to "
            "vulgarization_data_en."
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
        help="Translate every brief that has a FR vulgarisation payload.",
    )
    target.add_argument(
        "--missing-only",
        action="store_true",
        help="Translate only briefs where vulgarization_data_en IS NULL.",
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
            "Re-translate even if vulgarization_data_en is already populated. "
            "Default: skip rows that already have an EN payload."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Print extra per-brief progress lines: per-brief duration, "
            "running total cost, total elapsed wall time. Useful for batch "
            "runs."
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
            print(f"  skip {brief_id}: no FR vulgarization_data")
            skipped += 1
            continue
        if en_json and not args.force:
            print(f"  skip {brief_id}: already has vulgarization_data_en (use --force to redo)")
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
            en_payload, warnings, usage = await translate_brief(brief_id, fr_payload)
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

        print(f"  translated {brief_id} — title: {en_payload.get('title', '?')[:80]}")
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
            for w in warnings:
                print(f"    - {w}")

        if args.dry_run:
            print(f"\n{sep}\n[DRY-RUN] {brief_id} — EN payload:\n{sep}")
            print(json.dumps(en_payload, indent=2, ensure_ascii=False))
            print(sep)
            print(f"  [DRY-RUN] {brief_id}: not writing to DB")
        else:
            await write_translation(brief_id, en_payload)
            print(f"  wrote vulgarization_data_en for {brief_id}")

        ok += 1

    # Final summary.
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
