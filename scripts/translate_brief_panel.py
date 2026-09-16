"""Translate the panel review of a research brief between French and English.

Two sister entry points, one per direction, each with its own output
validator:

* ``translate_panel`` — FR -> Nature-grade EN. Reads ``panel_data`` (FR
  prose) from the briefs table, sends each prose field — list items
  translated as ``---``-separated blocks — through an LLM with a strict
  scientific-translation prompt, reconstructs the JSON preserving every
  backend token verbatim (``reviewer_persona``, ``verdict``, scores), and
  writes the result to ``panel_data_en``. Raises ``FrenchInOutputError``
  on residual French.
* ``translate_panel_to_fr`` — EN -> FR (S10-A). Repairs reviewer cards and
  meta-review prose that the panel wrote in English inside ``panel_data``,
  whose contract is French. Only the targeted cards are translated; the
  others are copied verbatim. Raises ``EnglishInOutputError`` when the
  output still reads as English.

The two validators are deliberately separate functions: they are
symmetric in intent but opposite in what they look for, and mixing them
behind a ``direction`` flag would make both fragile. The list-splitting
and fallback mechanics are shared.

Both directions:

* keep the ``FAIL REASON #n:`` marker out of the LLM's reach — it is split
  off before translation and re-prepended verbatim, because
  ``graph/panel_coherence.py`` reads it as its structural reserve marker;
* strip prompt scaffolding the model sometimes echoes at the head of a
  field (``INPUT: <source>\\n\\nOUTPUT: <translation>``, observed on
  SPR-2026-2FD9) and surface each strip as a warning.

Mirror of ``translate_brief_vulgarization.py`` (S7.4 Phase 1+2) for the
FR -> EN path. Same validation heuristics (UK spelling, no contractions,
no discover/discovery, length ratio, residual French = STOP). Idempotent:
a brief that already has a non-NULL ``panel_data_en`` is skipped unless
``--force`` is passed.

Voice (FR -> EN): ALL fields use the formal Nature-grade passive register
typical of scientific peer-review prose. No active second-person variant
(unlike the vulgarisation ``imagine_that`` field).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import get_llm_client
from logging_config import get_logger, get_token_tracker, reset_token_tracker
from storage import init_database
from storage.database import get_connection

logger = get_logger("translate_brief_panel")

# Per-call and per-brief LLM usage: token counts are ints, cost_usd a float.
UsageSummary = dict[str, int | float]


# ── Technical glossary (shared by both directions) ─────────────────────
#
# S10-C — closed list of methodological and statistical terms with an
# established French rendering. Built from the English prose of the panel
# corpus (49 public briefs with English cards + the 9 S10-B briefs), where
# these terms are the most frequent, and from the S10-B EN -> FR outputs,
# which rendered some of them inconsistently: « effet de taille » next to
# « taille d'effet », « étalonnage » and « calibration » used
# interchangeably, « confondant » next to « facteur de confusion ».
#
# One table feeds both prompts so the two directions cannot drift apart:
# EN -> FR reads it left to right, FR -> EN right to left. The optional
# third column is the British form required by the FR -> EN register when
# it differs from the (usually US) spelling found in the source. Keep it short
# and closed — terms without an established French rendering (in silico,
# GO/NO-GO, statistical test names) are preserved by the PRESERVATION
# rules, not listed here.
TECHNICAL_GLOSSARY: tuple[tuple[str, str, str | None], ...] = (
    ("effect size", "taille d'effet", None),
    ("sample size", "taille d'échantillon", None),
    ("statistical power", "puissance statistique", None),
    ("power analysis", "analyse de puissance", None),
    ("underpowered", "de puissance statistique insuffisante", None),
    ("confounder / confounding factor", "facteur de confusion", None),
    ("confounding (bias)", "biais de confusion", None),
    ("baseline (model, method)", "référence (modèle de référence, méthode de référence)", None),
    ("baseline (measurement)", "mesure initiale", None),
    ("endpoint / primary outcome", "critère de jugement / critère de jugement principal", None),
    ("proof of concept", "preuve de concept", None),
    ("pilot study", "étude pilote", None),
    ("ground truth", "vérité terrain", None),
    ("false positive / false negative", "faux positif / faux négatif", None),
    ("overfitting", "surapprentissage", None),
    ("cross-validation", "validation croisée", None),
    ("held-out set", "jeu de test réservé", None),
    ("batch effect", "effet de lot", None),
    ("signal-to-noise ratio", "rapport signal sur bruit", None),
    ("selection bias", "biais de sélection", None),
    ("multiple comparisons", "comparaisons multiples", None),
    ("confidence interval", "intervalle de confiance", None),
    ("null hypothesis", "hypothèse nulle", None),
    ("positive control / negative control", "contrôle positif / contrôle négatif", None),
    ("blinding", "mise en aveugle", None),
    ("dose-response", "dose-réponse", None),
    ("calibration (of a model or of probabilities)", "calibration", None),
    ("calibration (of an instrument or a measurement)", "étalonnage", None),
    ("reproducibility", "reproductibilité", None),
    ("generalizability", "généralisabilité", "generalisability"),
    ("scalability / scale-up", "passage à l'échelle", None),
    ("state of the art", "état de l'art", None),
    ("bottleneck", "goulot d'étranglement", None),
    ("addressable market", "marché adressable", None),
    ("barrier to entry", "barrière à l'entrée", None),
)


def _glossary_en_to_fr() -> str:
    """Render the glossary for the EN -> FR prompt.

    Returns:
        A prompt section listing « English » → « French » pairs.
    """
    lines = [f'- "{en}" → « {fr} »' for en, fr, _ in TECHNICAL_GLOSSARY]
    return (
        "GLOSSAIRE TECHNIQUE (liste fermée, rendus obligatoires) :\n"
        + "\n".join(lines)
        + "\nJamais « effet de taille » pour \"effect size\". Pour un terme de la liste, "
        "utilise exactement ce rendu, y compris au pluriel ; la précision entre "
        "parenthèses indique le contexte, elle ne se traduit pas."
    )


def _glossary_fr_to_en() -> str:
    """Render the glossary for the FR -> EN prompt.

    Returns:
        A prompt section listing French → English pairs.
    """
    lines = [f'- « {fr} » → "{en_gb or en}"' for en, fr, en_gb in TECHNICAL_GLOSSARY]
    return (
        "TECHNICAL GLOSSARY (closed list, mandatory renderings):\n"
        + "\n".join(lines)
        + "\nFor a listed term, use exactly this rendering, plural included; "
        "the parenthesised context is guidance only and is not translated."
    )


# ── Prompts: FR -> EN ──────────────────────────────────────────────────

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

VOICE: Use PASSIVE voice and impersonal constructions throughout ("the panel notes that...", "the protocol is structured...", "the hypothesis is judged..."). Avoid second-person address. Maintain the formal Nature-grade register typical of academic peer-review prose.

""" + _glossary_fr_to_en()


def _build_string_prompt(french_text: str) -> str:
    """Compose a per-call FR -> EN prompt for a single string field.

    Args:
        french_text: The French source text.

    Returns:
        The full prompt.
    """
    return (
        f"{BASE_PROMPT}\n\n"
        f"INPUT: {french_text}\n\n"
        "OUTPUT: ONLY the English translation, nothing else. "
        "No preamble, no explanation, no quotes around the translation. "
        "Do not repeat the input and do not write labels such as INPUT or OUTPUT."
    )


def _build_list_prompt(items: list[str]) -> str:
    """Compose a per-call FR -> EN prompt for a list of strings.

    The items are joined with ``\\n---\\n`` separators; the model is
    asked to return the same separator-delimited structure with
    translated items in the same order. Preserves item count exactly.

    Args:
        items: The French source items.

    Returns:
        The full prompt.
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
        "no preamble, no explanation, no item count header. "
        "Do not repeat the input and do not write labels such as INPUT or OUTPUT."
    )


# ── Prompts: EN -> FR ──────────────────────────────────────────────────

BASE_PROMPT_FR = """Tu es un traducteur scientifique spécialisé dans la prose d'évaluation par les pairs. Traduis en français le texte anglais fourni, en respectant strictement ces règles :

REGISTRE : éditorial scientifique — précis, sobre, formel. Pas de familiarité, pas de ton publicitaire. Reste fidèle au sens et au degré de certitude du texte source : une réserve reste une réserve, une recommandation reste une recommandation. N'ajoute rien, ne résume rien.

LANGUE : la traduction est entièrement en français. Aucune phrase ne reste en anglais.

VOCABULAIRE :
- "finding" / "findings" : « résultat », « constat ». Ne traduis pas par « découverte ».
- "brief" / "briefs" : garder tel quel.
- "panel", "reviewer", "meta-reviewer" : « le panel », « le relecteur », « le méta-relecteur ».
- "hypothesis" : « hypothèse » ; "collision" : garder tel quel.
- "assumption" : « postulat » ou « hypothèse de départ » ; "likely" : « probable » ou « probablement » ; "unlikely" : « improbable » ; "cannot" : « ne peut pas » ou « impossible ».

PRÉSERVATION :
- Conserver tous les noms propres (personnes, lieux, institutions, entreprises, équipements, logiciels).
- Conserver tels quels les nombres, unités, dates, symboles, équations, DOI et marqueurs de citation comme "[2025]" ou "(P450cam, 1DZ8)".
- Conserver tels quels les références de phase : "Phase 1", "Phase 2", "Phase 3".
- Conserver en anglais les titres d'articles cités.
- Conserver les noms officiels des programmes de financement et des agences (ERC Starting Grant, Horizon Europe, EIC Pathfinder, NIH R21…) et les sigles d'usage (TRL, ROI, IP, TAM, CAGR, GO/NO-GO).
- Conserver les termes techniques sans équivalent français établi (noms de tests statistiques, de molécules, de gènes, de méthodes).
- Conserver la mise en forme markdown présente dans le source, sans en inventer.

SORTIE : uniquement la traduction. Ne recopie jamais le texte source. N'écris aucune étiquette (« TEXTE SOURCE », « TRADUCTION », « INPUT », « OUTPUT »), aucun préambule, aucune explication, aucun guillemet autour de la traduction.

""" + _glossary_en_to_fr()


def _build_string_prompt_fr(english_text: str) -> str:
    """Compose a per-call EN -> FR prompt for a single string field.

    Args:
        english_text: The English source text.

    Returns:
        The full prompt.
    """
    return (
        f"{BASE_PROMPT_FR}\n\n"
        f"TEXTE SOURCE (anglais) :\n{english_text}\n\n"
        "Réponds UNIQUEMENT par la traduction française de ce texte."
    )


def _build_list_prompt_fr(items: list[str]) -> str:
    """Compose a per-call EN -> FR prompt for a list of strings.

    Same ``---`` separator contract as ``_build_list_prompt``.

    Args:
        items: The English source items.

    Returns:
        The full prompt.
    """
    body = "\n---\n".join(items)
    return (
        f"{BASE_PROMPT_FR}\n\n"
        f"Tu vas traduire {len(items)} éléments séparés par des lignes contenant uniquement `---`. "
        "Rends la même structure : chaque élément traduit, séparé du suivant par une ligne contenant uniquement `---`. "
        "N'ajoute ni ne retire aucun élément. Ne renumérote pas. Traduis chaque élément dans l'ordre.\n\n"
        f"TEXTE SOURCE ({len(items)} éléments, anglais) :\n"
        f"{body}\n\n"
        "Réponds UNIQUEMENT par les éléments traduits séparés par `---`, dans le même ordre, "
        "sans en-tête de décompte."
    )


# ── Output JSON shape ──────────────────────────────────────────────────
#
# panel_data (FR source) and panel_data_en (this script writes) share the
# same shape — only the prose is translated; tokens, scores and structure
# are copied verbatim. The EN -> FR direction uses the same field lists:
#
# {
#   reviews: [
#     {
#       reviewer_persona  COPY (token EN)
#       overall_score     COPY (number)
#       verdict           COPY (token EN)
#       confidence        COPY (number)
#       strengths[]       TRANSLATE (list of prose)
#       weaknesses[]      TRANSLATE ("FAIL REASON #n:" marker kept verbatim)
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
# pre-translation and either skip them or pass through a fixed
# equivalent that preserves the operator signal.

_PLACEHOLDER_MAP: dict[str, str] = {
    "manual review needed": "Manual review needed.",
    "manual review needed.": "Manual review needed.",
    "recommandation actionnable en 2-3 phrases.": "Actionable recommendation in 2-3 sentences.",
    "unable to parse review": "Unable to parse review.",
    "review parsing failed": "Review parsing failed.",
}

# EN -> FR: the English fallback strings written by
# agents/multi_reviewer_panel.py when a reviewer or the meta-reviewer
# fails, mapped to fixed French equivalents.
_PLACEHOLDER_MAP_FR: dict[str, str] = {
    "manual review needed": "Revue manuelle nécessaire.",
    "manual review needed.": "Revue manuelle nécessaire.",
    "actionable recommendation in 2-3 sentences.": "Recommandation actionnable en 2-3 phrases.",
    "recommandation actionnable en 2-3 phrases.": "Recommandation actionnable en 2-3 phrases.",
    "unable to parse review": "Impossible d'analyser l'évaluation.",
    "unable to parse review.": "Impossible d'analyser l'évaluation.",
    "review parsing failed": "Échec de l'analyse de l'évaluation.",
    "review parsing failed.": "Échec de l'analyse de l'évaluation.",
    "review failed": "Évaluation en échec.",
    "review failed due to error": "Évaluation en échec à la suite d'une erreur.",
    "meta-review parsing failed": "Échec de l'analyse de la méta-évaluation.",
}


def _placeholder_passthrough(
    text: str,
    mapping: Mapping[str, str] = _PLACEHOLDER_MAP,
) -> str | None:
    """Return the fixed equivalent for a known placeholder, or None.

    Matching is case-insensitive on whitespace-stripped input. Used to
    short-circuit translation for pipeline error/marker strings — these
    cause LLM hallucinations because the input is too short to anchor
    a faithful translation.

    Args:
        text: Source text.
        mapping: Placeholder table of the translation direction.

    Returns:
        The replacement string, or None when ``text`` is not a placeholder.
    """
    if not text:
        return None
    key = text.strip().lower()
    return mapping.get(key)


# ── FAIL REASON marker ─────────────────────────────────────────────────
#
# ``FAIL REASON #n:`` is a technical token, not prose: prompts/
# reviewer_contrarian.txt prescribes it and graph/panel_coherence.py reads
# it as the structural reserve marker behind 89.8 % of its decisions on
# negative cards. A translator that renders it « RAISON D'ÉCHEC n° 1 »
# would silently move briefs from one gate to another. It is therefore
# split off before the LLM call and re-prepended verbatim afterwards — a
# deterministic guarantee rather than a prompt instruction.

#
# Separator variants measured on the corpus: « FAIL REASON #n: » (the
# prompt's form, ~95 %) and « FAIL REASON #n — » (SPR-2026-059C and 5
# other cards). Whatever separator the source uses is kept verbatim.
_FAIL_REASON_PREFIX_RE = re.compile(
    r"^\s*(FAIL\s*REASON\s*(?:#\s*)?\d*(?:\s*[:\-–—.)])?)\s*",
    re.IGNORECASE,
)


def _split_fail_reason(text: str) -> tuple[str, str]:
    """Split a leading ``FAIL REASON #n:`` marker from the prose.

    Args:
        text: A prose item.

    Returns:
        Tuple of (marker, body). ``marker`` is the verbatim marker followed
        by one space, or "" when the item carries none.
    """
    match = _FAIL_REASON_PREFIX_RE.match(text)
    if not match:
        return "", text
    return f"{match.group(1).strip()} ", text[match.end():]


# ── Prompt scaffolding echo ────────────────────────────────────────────
#
# SPR-2026-2FD9, reviews[4/funding_strategist].recommendation: the source
# was already English, and the model answered with
# ``INPUT: <source>\n\nOUTPUT: <translation>`` — hence an EN/FR length
# ratio of 2.08. Labels from both directions' prompts are recognised.

_INPUT_LABELS = r"INPUT|TEXTE\s+SOURCE|ENTR[ÉE]E"
_OUTPUT_LABELS = (
    r"OUTPUT|(?:ENGLISH\s+|FRENCH\s+)?TRANSLATION|TRADUCTION(?:\s+FRAN[ÇC]AISE)?|SORTIE"
)
_LABEL_SUFFIX = r"(?:\s*\([^)\n]{0,40}\))?\s*:"
_LEAD_INPUT_RE = re.compile(rf"^\s*(?:{_INPUT_LABELS}){_LABEL_SUFFIX}\s*", re.IGNORECASE)
_LEAD_OUTPUT_RE = re.compile(rf"^\s*(?:{_OUTPUT_LABELS}){_LABEL_SUFFIX}\s*", re.IGNORECASE)
_INNER_OUTPUT_RE = re.compile(rf"\n\s*(?:{_OUTPUT_LABELS}){_LABEL_SUFFIX}\s*", re.IGNORECASE)


def _strip_scaffold(field_path: str, text: str) -> tuple[str, list[str]]:
    """Remove prompt scaffolding echoed at the head of an LLM output.

    Two shapes are handled: an echoed source (``INPUT: <source>`` followed
    later by ``OUTPUT: <translation>``, keep what follows the last output
    label) and a bare leading label (``OUTPUT: <translation>``). Every
    strip is logged as a warning and returned, so it never passes silently.

    Args:
        field_path: Field identifier for logging.
        text: Raw LLM output (or one item of it).

    Returns:
        Tuple of (cleaned text, warnings).
    """
    warnings: list[str] = []
    cleaned = text

    lead_input = _LEAD_INPUT_RE.match(cleaned)
    if lead_input:
        inner = list(_INNER_OUTPUT_RE.finditer(cleaned, lead_input.end()))
        if inner:
            cleaned = cleaned[inner[-1].end():]
            kind = "echoed_source"
        else:
            cleaned = cleaned[lead_input.end():]
            kind = "input_label"
        warnings.append(f"{field_path}: prompt scaffolding stripped ({kind})")

    while True:
        lead_output = _LEAD_OUTPUT_RE.match(cleaned)
        if not lead_output:
            break
        cleaned = cleaned[lead_output.end():]
        warnings.append(f"{field_path}: prompt scaffolding stripped (output_label)")

    if warnings:
        logger.warning(
            "translation_scaffold_stripped",
            field=field_path,
            kinds=[w.rsplit("(", 1)[-1].rstrip(")") for w in warnings],
            preview=text[:80],
        )
    return cleaned.strip(), warnings


# ── Validation: FR -> EN ───────────────────────────────────────────────

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
    """Run quality checks on a single FR -> EN translated field.

    Args:
        field_path: Field identifier for messages.
        fr_text: French source.
        en_text: English output.

    Returns:
        Warnings (style, vocabulary, length drift).

    Raises:
        FrenchInOutputError: On residual French in the output.
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


# ── Validation: EN -> FR ───────────────────────────────────────────────
#
# NOT a mirror of _validate_text. A symmetric list of "English words"
# would fire on every legitimate French card: panel prose carries English
# by contract — article titles, funding programme names (« ERC Starting
# Grant »), product terms (« proof of concept », « open source »), the
# FAIL REASON marker, persona and verdict tokens. The discriminator is
# graph.lang_guard.detect, the very function behind check_panel_language:
# the translator and the gate then share one notion of "still English",
# so a field that passes here cannot fail validate_brief for language on
# its own. detect() abstaining (short or technical text) means accept.
#
# Verbatim tokens and quoted spans (article titles) are removed before
# detection so that they never count as English signal.

_PERSONA_TOKENS = (
    "methodologist", "domain_expert", "contrarian", "industrialist",
    "funding_strategist", "meta_reviewer",
)
_VERDICT_TOKENS = (
    "strong_accept", "weak_accept", "weak_reject", "accept", "reject",
    "publish_brief", "revise_and_resubmit",
)
_VERBATIM_TOKEN_RE = re.compile(
    r"FAIL\s*REASON\s*#?\s*\d*\s*:?"
    r"|\b(?:%s)\b"
    r"|[-+]?\d+(?:[.,]\d+)*\s*%%?"
    % "|".join(re.escape(t) for t in (*_PERSONA_TOKENS, *_VERDICT_TOKENS)),
    re.IGNORECASE,
)
_QUOTED_SPAN_RE = re.compile(r"«[^»]*»|“[^”]*”|\"[^\"\n]*\"|\*[^*\n]+\*")

# FR/EN character-length ratio of EN -> FR output, recalibrated in S10-C on
# real translations: 374 prose fields (list items and strings) translated
# EN -> FR by this function during the S10-B replay, source read from the
# pre-replay backup. Median 1.20, 1st percentile 1.03, 99th percentile
# 1.42, observed range 0.91-1.48 — French is structurally longer, questions
# the most (the 1.48 is a faithful critical_questions item on SPR-2026-27B2).
#
# The previous window (0.80-1.45) had been derived from the FR -> EN corpus
# (median 1.065), i.e. from the other direction: its upper bound sat inside
# the normal tail and produced warnings on correct translations. The window
# is kept as a detector of real failures — truncation or omission below,
# echo of the source or added content above (an echoed source doubles the
# length) — with margin around the observed range. The FR -> EN window of
# _validate_text (0.70-1.25) is left unchanged: 530 fields measured on the
# same replay span 0.73-1.11, none outside it.
_FR_EN_RATIO_MIN = 0.85
_FR_EN_RATIO_MAX = 1.60


class EnglishInOutputError(Exception):
    """Raised when an EN -> FR output still reads as English."""


def _strip_verbatim_tokens(text: str) -> str:
    """Remove tokens that stay English by contract before language detection.

    Args:
        text: A French output field.

    Returns:
        The text without FAIL REASON markers, persona and verdict tokens,
        numbers, and quoted or italicised spans.
    """
    without_quotes = _QUOTED_SPAN_RE.sub(" ", text)
    return _VERBATIM_TOKEN_RE.sub(" ", without_quotes)


def _validate_text_fr(field_path: str, en_text: str, fr_text: str) -> list[str]:
    """Run quality checks on a single EN -> FR translated field.

    Args:
        field_path: Field identifier for messages.
        en_text: English source.
        fr_text: French output.

    Returns:
        Warnings (length drift, output identical to the source).

    Raises:
        EnglishInOutputError: When the output, verbatim tokens removed, is
            detected as English.
    """
    # Imported here, not at module level: graph/__init__ imports the L0
    # pipeline, which imports post_fire_pipeline, which imports
    # agents.translation, which imports this module — a top-level import
    # would close that cycle whenever agents.translation is imported first.
    from graph.lang_guard import detect

    warnings: list[str] = []

    detected = detect(_strip_verbatim_tokens(fr_text))
    if detected == "en":
        raise EnglishInOutputError(
            f"{field_path}: FR output detected as English: {fr_text[:120]!r}"
        )

    if fr_text.strip() == en_text.strip() and len(en_text.split()) >= 5:
        warnings.append(f"{field_path}: FR output identical to the EN source")

    en_len = max(len(en_text), 1)
    ratio = len(fr_text) / en_len
    if not _FR_EN_RATIO_MIN <= ratio <= _FR_EN_RATIO_MAX:
        warnings.append(
            f"{field_path}: FR/EN length ratio {ratio:.2f} outside "
            f"{_FR_EN_RATIO_MIN:.2f}-{_FR_EN_RATIO_MAX:.2f}"
        )

    return warnings


# ── Translation primitives ─────────────────────────────────────────────


def _strip_wrapping_bold(text: str) -> str:
    """Remove a single bold wrapper around the whole text."""
    stripped = text.strip()
    if not (stripped.startswith("**") and stripped.endswith("**")):
        return text
    inner = stripped[2:-2]
    if "**" in inner:
        return text
    return inner.strip()


def _strip_wrappers(text: str) -> str:
    """Remove quotes, code fences and a bold wrapper around the whole text."""
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
# alone). FR -> EN only: « découverte » is legitimate French scientific
# vocabulary and is never rewritten on the EN -> FR path.
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


def _identity(text: str) -> str:
    """Post-processing no-op for the EN -> FR direction."""
    return text


@dataclass(frozen=True)
class _Direction:
    """Everything that differs between the two translation directions.

    Attributes:
        name: Direction label for logs ("fr_to_en" or "en_to_fr").
        agent: Token-tracker agent label.
        build_string_prompt: Prompt builder for a single string.
        build_list_prompt: Prompt builder for a ``---``-separated list.
        validate: Per-field validator ``(field_path, source, output)``
            returning warnings and raising on wrong-language output.
        placeholders: Placeholder passthrough table.
        postprocess: Output post-processing applied after unwrapping.
    """

    name: str
    agent: str
    build_string_prompt: Callable[[str], str]
    build_list_prompt: Callable[[list[str]], str]
    validate: Callable[[str, str, str], list[str]]
    placeholders: Mapping[str, str]
    postprocess: Callable[[str], str]


_FR_TO_EN = _Direction(
    name="fr_to_en",
    agent="translate_panel",
    build_string_prompt=_build_string_prompt,
    build_list_prompt=_build_list_prompt,
    validate=_validate_text,
    placeholders=_PLACEHOLDER_MAP,
    postprocess=_replace_forbidden_discover,
)

_EN_TO_FR = _Direction(
    name="en_to_fr",
    agent="translate_panel_to_fr",
    build_string_prompt=_build_string_prompt_fr,
    build_list_prompt=_build_list_prompt_fr,
    validate=_validate_text_fr,
    placeholders=_PLACEHOLDER_MAP_FR,
    postprocess=_identity,
)


def _zero_usage() -> UsageSummary:
    """Usage summary of a field that needed no LLM call."""
    return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


async def _llm_call(
    client: Any,
    prompt: str,
    direction: _Direction,
    max_tokens: int = 2500,
) -> tuple[str, UsageSummary]:
    """Single LLM call with cost tracking.

    Args:
        client: LLM client from ``get_llm_client``.
        prompt: Full prompt.
        direction: Translation direction (tracker label, post-processing).
        max_tokens: Output token cap.

    Returns:
        Tuple of (unwrapped text, usage). Scaffolding is NOT stripped here:
        callers strip it with a field path so the warning can name it.
    """
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
        node=direction.agent,
    )

    tracker = get_token_tracker()
    cost = tracker.log_call(
        agent=direction.agent,
        model=response.requested_model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )
    text = _strip_wrappers(response.content)
    text = direction.postprocess(text)
    return text, {
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": cost,
    }


async def _translate_string(
    client: Any,
    field_path: str,
    source_text: str,
    direction: _Direction = _FR_TO_EN,
) -> tuple[str, UsageSummary, list[str]]:
    """Translate a single string.

    Args:
        client: LLM client.
        field_path: Field identifier for logs and warnings.
        source_text: Text in the source language.
        direction: Translation direction.

    Returns:
        Tuple of (translated text, usage, warnings).

    Raises:
        FrenchInOutputError: FR -> EN output still French.
        EnglishInOutputError: EN -> FR output still English.
    """
    if not source_text or not source_text.strip():
        return "", _zero_usage(), []

    marker, body = _split_fail_reason(source_text)
    if not body.strip():
        return marker.strip(), _zero_usage(), []

    # Pipeline placeholders short-circuit the LLM call to avoid
    # hallucinations on minimal-context inputs.
    placeholder = _placeholder_passthrough(body, direction.placeholders)
    if placeholder is not None:
        logger.info(
            "translation_placeholder_passthrough",
            field=field_path,
            direction=direction.name,
            source=body[:40],
            target=placeholder,
        )
        return marker + placeholder, _zero_usage(), []

    prompt = direction.build_string_prompt(body)
    raw, usage = await _llm_call(client, prompt, direction)
    translated, warnings = _strip_scaffold(field_path, raw)
    warnings.extend(direction.validate(field_path, body, translated))
    return marker + translated, usage, warnings


async def _translate_list(
    client: Any,
    field_path: str,
    items: list[str],
    direction: _Direction = _FR_TO_EN,
) -> tuple[list[str], UsageSummary, list[str]]:
    """Translate a list of strings as one ``---``-separated block.

    If the LLM returns a different item count, the script falls back to
    translating each item individually (more calls, higher cost, but
    preserves correctness).

    Args:
        client: LLM client.
        field_path: Field identifier for logs and warnings.
        items: Items in the source language; non-strings are dropped.
        direction: Translation direction.

    Returns:
        Tuple of (translated items, usage, warnings).

    Raises:
        FrenchInOutputError: FR -> EN output still French.
        EnglishInOutputError: EN -> FR output still English.
    """
    items = [s for s in items if isinstance(s, str)]
    if not items:
        return [], _zero_usage(), []

    # Per-item resolution before composing the LLM prompt: the FAIL
    # REASON marker is split off, empty bodies and known placeholders are
    # resolved without the LLM, and the remaining bodies are sent in one
    # call. Result is reassembled in original order. A list whose items
    # all resolve locally short-circuits entirely.
    resolved: list[str | None] = [None] * len(items)
    markers: list[str] = [""] * len(items)
    to_translate: list[tuple[int, str]] = []
    for i, source_item in enumerate(items):
        marker, body = _split_fail_reason(source_item)
        markers[i] = marker
        if not body.strip():
            resolved[i] = marker.strip()
            continue
        placeholder = _placeholder_passthrough(body, direction.placeholders)
        if placeholder is not None:
            resolved[i] = marker + placeholder
            logger.info(
                "translation_placeholder_passthrough",
                field=f"{field_path}[{i}]",
                direction=direction.name,
                source=body[:40],
                target=placeholder,
            )
        else:
            to_translate.append((i, body))

    if not to_translate:
        return [s or "" for s in resolved], _zero_usage(), []

    bodies = [body for _, body in to_translate]
    prompt = direction.build_list_prompt(bodies)
    raw, usage = await _llm_call(client, prompt, direction, max_tokens=3500)

    all_warnings: list[str] = []
    translated_block, block_warnings = _strip_scaffold(field_path, raw)
    all_warnings.extend(block_warnings)

    # Split on lines containing only ``---`` (allow surrounding whitespace).
    parts = re.split(r"\n\s*---\s*\n", translated_block.strip())
    parts = [p.strip() for p in parts if p.strip()]

    total_in = int(usage["input_tokens"])
    total_out = int(usage["output_tokens"])
    total_cost = float(usage["cost_usd"])

    if len(parts) != len(bodies):
        # Fallback: per-item translation. Logged so the operator can see
        # the LLM split-marker mismatch and tune the prompt if it happens
        # often.
        logger.warning(
            "list_split_mismatch_fallback",
            field=field_path,
            direction=direction.name,
            expected=len(bodies),
            received=len(parts),
        )
        for orig_idx, body in to_translate:
            sub_path = f"{field_path}[{orig_idx}]"
            translated_item, sub_usage, sub_warnings = await _translate_string(
                client, sub_path, body, direction
            )
            resolved[orig_idx] = markers[orig_idx] + translated_item
            all_warnings.extend(sub_warnings)
            total_in += int(sub_usage["input_tokens"])
            total_out += int(sub_usage["output_tokens"])
            total_cost += float(sub_usage["cost_usd"])
    else:
        # Reassemble translated parts back into the original-indexed list.
        for (orig_idx, body), part in zip(to_translate, parts):
            item_path = f"{field_path}[{orig_idx}]"
            cleaned_part, part_warnings = _strip_scaffold(item_path, part)
            all_warnings.extend(part_warnings)
            all_warnings.extend(direction.validate(item_path, body, cleaned_part))
            resolved[orig_idx] = markers[orig_idx] + cleaned_part

    return (
        [s or "" for s in resolved],
        {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_usd": total_cost,
        },
        all_warnings,
    )


# ── Per-brief translation orchestration ────────────────────────────────


class _UsageAccumulator:
    """Running total of the usage of every call made for one payload."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0

    def add(self, usage: UsageSummary) -> None:
        """Add one field's usage to the total."""
        self.input_tokens += int(usage.get("input_tokens", 0))
        self.output_tokens += int(usage.get("output_tokens", 0))
        self.cost_usd += float(usage.get("cost_usd", 0.0))

    def summary(self) -> UsageSummary:
        """Usage summary in the shape returned by the public functions."""
        return {
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


async def _translate_review_card(
    client: Any,
    brief_id: str,
    ridx: int,
    card: dict[str, Any],
    direction: _Direction,
    usage: _UsageAccumulator,
) -> tuple[dict[str, Any], list[str]]:
    """Translate the prose fields of one reviewer card.

    Args:
        client: LLM client.
        brief_id: Brief identifier, for logs.
        ridx: Card index in ``reviews``.
        card: The source card.
        direction: Translation direction.
        usage: Accumulator receiving the card's usage.

    Returns:
        Tuple of (translated card, warnings). Backend tokens, numbers and
        opaque blobs are copied verbatim.
    """
    translated: dict[str, Any] = {}
    for key, value in card.items():
        if key in REVIEWER_LIST_FIELDS or key in REVIEWER_STRING_FIELDS:
            continue  # translated below
        translated[key] = copy.deepcopy(value)

    persona = card.get("reviewer_persona", f"reviewer_{ridx}")
    warnings: list[str] = []

    for field in REVIEWER_LIST_FIELDS:
        items = card.get(field) or []
        field_path = f"reviews[{ridx}/{persona}].{field}"
        out_items, field_usage, field_warnings = await _translate_list(
            client, field_path, items, direction
        )
        translated[field] = out_items
        usage.add(field_usage)
        warnings.extend(field_warnings)
        logger.info(
            "translated_reviewer_field",
            brief_id=brief_id,
            direction=direction.name,
            persona=persona,
            field=field,
            items=len(out_items),
        )

    for field in REVIEWER_STRING_FIELDS:
        text = card.get(field) or ""
        field_path = f"reviews[{ridx}/{persona}].{field}"
        out_text, field_usage, field_warnings = await _translate_string(
            client, field_path, text, direction
        )
        translated[field] = out_text
        usage.add(field_usage)
        warnings.extend(field_warnings)
        logger.info(
            "translated_reviewer_field",
            brief_id=brief_id,
            direction=direction.name,
            persona=persona,
            field=field,
            preview=out_text[:80],
        )

    return translated, warnings


async def _translate_meta_review(
    client: Any,
    brief_id: str,
    meta: dict[str, Any],
    direction: _Direction,
    usage: _UsageAccumulator,
) -> tuple[dict[str, Any], list[str]]:
    """Translate the prose fields of the meta-review.

    Args:
        client: LLM client.
        brief_id: Brief identifier, for logs.
        meta: The source meta-review.
        direction: Translation direction.
        usage: Accumulator receiving the meta-review's usage.

    Returns:
        Tuple of (translated meta-review, warnings). Non-prose keys are
        copied verbatim.
    """
    translated: dict[str, Any] = {}
    for key, value in meta.items():
        if key in META_LIST_FIELDS or key in META_STRING_FIELDS:
            continue
        translated[key] = copy.deepcopy(value)

    warnings: list[str] = []

    for field in META_LIST_FIELDS:
        items = meta.get(field) or []
        out_items, field_usage, field_warnings = await _translate_list(
            client, f"meta_review.{field}", items, direction
        )
        translated[field] = out_items
        usage.add(field_usage)
        warnings.extend(field_warnings)
        logger.info(
            "translated_meta_field",
            brief_id=brief_id,
            direction=direction.name,
            field=field,
            items=len(out_items),
        )

    for field in META_STRING_FIELDS:
        text = meta.get(field) or ""
        out_text, field_usage, field_warnings = await _translate_string(
            client, f"meta_review.{field}", text, direction
        )
        translated[field] = out_text
        usage.add(field_usage)
        warnings.extend(field_warnings)
        logger.info(
            "translated_meta_field",
            brief_id=brief_id,
            direction=direction.name,
            field=field,
            preview=out_text[:80],
        )

    return translated, warnings


async def translate_panel(
    brief_id: str,
    fr_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str], UsageSummary]:
    """Translate a single brief's panel_data payload FR -> EN.

    Args:
        brief_id: Brief identifier, for logs.
        fr_payload: ``panel_data`` with French prose.

    Returns:
        Tuple of (en_payload, warnings, usage_summary).

    Raises:
        FrenchInOutputError: On a STOP signal (residual French).
    """
    client = get_llm_client("translation")
    usage = _UsageAccumulator()
    all_warnings: list[str] = []
    en_payload: dict[str, Any] = {}

    en_reviews: list[Any] = []
    for ridx, fr_card in enumerate(fr_payload.get("reviews") or []):
        if not isinstance(fr_card, dict):
            en_reviews.append(fr_card)
            continue
        en_card, warnings = await _translate_review_card(
            client, brief_id, ridx, fr_card, _FR_TO_EN, usage
        )
        en_reviews.append(en_card)
        all_warnings.extend(warnings)
    en_payload["reviews"] = en_reviews

    en_meta, warnings = await _translate_meta_review(
        client, brief_id, fr_payload.get("meta_review") or {}, _FR_TO_EN, usage
    )
    en_payload["meta_review"] = en_meta
    all_warnings.extend(warnings)

    return en_payload, all_warnings, usage.summary()


async def translate_panel_to_fr(
    brief_id: str,
    en_payload: dict[str, Any],
    *,
    review_indices: Collection[int] | None = None,
    include_meta: bool = True,
) -> tuple[dict[str, Any], list[str], UsageSummary]:
    """Translate English prose of a panel payload EN -> FR.

    Sister of ``translate_panel``. Used to repair ``panel_data`` — whose
    contract is French — when reviewer cards or the meta-review came back
    in English. Selective: only the cards at ``review_indices`` (and the
    meta-review when ``include_meta``) go through the LLM; every other part
    of the payload is copied verbatim, so cards already in French are
    never touched.

    Args:
        brief_id: Brief identifier or trace label, for logs. The briefs row
            may not exist yet when this is called from the post-fire graph.
        en_payload: ``panel_data``-shaped dict with ``reviews`` and
            ``meta_review``.
        review_indices: Indices in ``reviews`` of the cards to translate.
            ``None`` translates every card; an empty collection none.
        include_meta: Whether to translate the meta-review prose.

    Returns:
        Tuple of (fr_payload, warnings, usage_summary). ``fr_payload`` is a
        new dict with the same keys as ``en_payload``.

    Raises:
        EnglishInOutputError: When a translated field still reads as
            English. Nothing is partially applied: the caller receives the
            exception instead of a half-translated payload.
    """
    reviews = en_payload.get("reviews") or []
    targets = (
        set(range(len(reviews))) if review_indices is None else set(review_indices)
    )

    fr_payload: dict[str, Any] = {
        key: copy.deepcopy(value)
        for key, value in en_payload.items()
        if key not in ("reviews", "meta_review")
    }
    usage = _UsageAccumulator()
    all_warnings: list[str] = []

    needs_client = bool(targets & set(range(len(reviews)))) or (
        include_meta and bool(en_payload.get("meta_review"))
    )
    client = get_llm_client("translation") if needs_client else None

    fr_reviews: list[Any] = []
    for ridx, card in enumerate(reviews):
        if ridx not in targets or not isinstance(card, dict):
            fr_reviews.append(copy.deepcopy(card))
            continue
        fr_card, warnings = await _translate_review_card(
            client, brief_id, ridx, card, _EN_TO_FR, usage
        )
        fr_reviews.append(fr_card)
        all_warnings.extend(warnings)
    fr_payload["reviews"] = fr_reviews

    meta = en_payload.get("meta_review")
    if include_meta and isinstance(meta, dict) and meta:
        fr_meta, warnings = await _translate_meta_review(
            client, brief_id, meta, _EN_TO_FR, usage
        )
        fr_payload["meta_review"] = fr_meta
        all_warnings.extend(warnings)
    else:
        fr_payload["meta_review"] = copy.deepcopy(meta) if meta is not None else {}

    return fr_payload, all_warnings, usage.summary()


# ── DB helpers ─────────────────────────────────────────────────────────


async def fetch_target_briefs(
    *,
    brief_id: str | None,
    missing_only: bool,
    process_all: bool,
    include_stubs: bool = False,
) -> list[tuple[str, str | None, str | None]]:
    """Return the rows we should process: list of (id, fr_json, en_json).

    S2c/C15 — stub briefs are excluded by default, matching
    ``node_translation_hook``, which has always skipped them. Today no
    stub carries a ``panel_data`` payload (``SELECT COUNT(*) FROM briefs
    WHERE is_stub=1 AND panel_data IS NOT NULL`` is 0), so this filter is
    defensive: it closes the same blind spot that let
    ``translate_brief_vulgarization.py`` publish a fabricated EN payload
    on the 16 stubs, before a stray panel backfill can repeat it here.
    """
    async with get_connection() as conn:
        if brief_id:
            cursor = await conn.execute(
                "SELECT id, is_stub, panel_data, panel_data_en "
                "FROM briefs WHERE id = ?",
                (brief_id,),
            )
            rows = await cursor.fetchall()
            if not rows:
                raise SystemExit(f"brief not found: {brief_id}")
        else:
            base = (
                "SELECT id, is_stub, panel_data, panel_data_en "
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

        return _drop_stubs(rows, include_stubs=include_stubs)


def _drop_stubs(rows, *, include_stubs: bool) -> list[tuple[str, str | None, str | None]]:
    """Filter out is_stub=1 rows and log what was dropped."""
    kept: list[tuple[str, str | None, str | None]] = []
    skipped: list[str] = []
    for r in rows:
        if not include_stubs and r["is_stub"]:
            skipped.append(r["id"])
            continue
        kept.append((r["id"], r["panel_data"], r["panel_data_en"]))
    if skipped:
        print(
            f"skipping {len(skipped)} stub brief(s) — a stub never went "
            f"through the panel: {', '.join(skipped)}",
            file=sys.stderr,
        )
        logger.info(
            "translate_panel_stubs_skipped",
            count=len(skipped),
            brief_ids=skipped,
        )
    elif include_stubs:
        print("--include-stubs: stub briefs are NOT being filtered out", file=sys.stderr)
    return kept


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
        "--include-stubs",
        action="store_true",
        help=(
            "Process is_stub=1 briefs too. Default: skip them, matching "
            "node_translation_hook. A stub never went through the panel, "
            "so any panel payload on it is fabricated."
        ),
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
        include_stubs=args.include_stubs,
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
