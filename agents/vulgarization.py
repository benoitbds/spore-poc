"""French vulgarization agent for SPORE post-fire pipeline.

Takes a complete research brief (sharpened hypothesis + protocol + panel)
and produces an accessible French version aimed at a general audience.
"""

import json
from typing import Any, TypedDict

from agents.base import load_prompt
from agents.hypothesis_sharpening import SharpeningOutput
from agents.experimental_protocol import ProtocolOutput
from agents.multi_reviewer_panel import PanelOutput
from llm import get_llm_client
from llm.json_parse import complete_json
from logging_config import get_logger, get_token_tracker

logger = get_logger("vulgarization")


class VulgarizationOutput(TypedDict):
    """Output of the French vulgarization agent."""

    title_fr: str
    hypothesis_in_brief: str
    why_it_matters: str
    imagine_that: str
    concretely: dict[str, str]
    reviewers_say: str


def _format_predictions(predictions: list[dict[str, Any]]) -> str:
    if not predictions:
        return "Aucune prédiction formalisée."
    lines = []
    for i, p in enumerate(predictions[:5], 1):
        lines.append(
            f"{i}. {p.get('prediction', '?')} "
            f"(borne: {p.get('quantitative_bound', '?')})"
        )
    return "\n".join(lines)


def _format_phases(phases: list[dict[str, Any]]) -> str:
    if not phases:
        return "Aucune phase définie."
    lines = []
    for phase in phases:
        pn = phase.get("phase_number", "?")
        name = phase.get("phase_name", "?")
        obj = phase.get("objective", "")
        method = phase.get("methodology", "")[:200]
        lines.append(
            f"Phase {pn} — {name}\n"
            f"  Objectif: {obj}\n"
            f"  Méthode: {method}"
        )
    return "\n".join(lines)


def _format_panel(reviews: list[dict[str, Any]]) -> str:
    if not reviews:
        return "Aucun reviewer."
    lines = []
    for r in reviews:
        persona = r.get("reviewer_persona", "?")
        score = r.get("overall_score", "?")
        verdict = r.get("verdict", "?")
        strengths = r.get("strengths", [])
        weaknesses = r.get("weaknesses", [])
        rec = r.get("recommendation", "")[:150]
        lines.append(
            f"- {persona} (score {score}/10, verdict: {verdict})\n"
            f"    Forces: {'; '.join(strengths[:2]) if strengths else '—'}\n"
            f"    Faiblesses: {'; '.join(weaknesses[:2]) if weaknesses else '—'}\n"
            f"    Recommandation: {rec}"
        )
    return "\n".join(lines)


async def vulgarization_agent(
    sharpened: SharpeningOutput,
    protocol: ProtocolOutput,
    panel: PanelOutput,
    domains: list[str],
    grounding: dict[str, Any],
) -> VulgarizationOutput:
    """Produce a French vulgarized version of the research brief.

    Args:
        sharpened: Sharpened hypothesis output.
        protocol: Experimental protocol output.
        panel: Multi-reviewer panel output.
        domains: Scientific domains of the collision.
        grounding: Literature grounding output.

    Returns:
        VulgarizationOutput with French accessible version.
    """
    client = get_llm_client("vulgarization")
    tracker = get_token_tracker()

    novelty = grounding.get("novelty_assessment", {}) if grounding else {}
    meta = panel["meta_review"]
    reviews = panel["reviews"]
    mechanism = sharpened.get("proposed_mechanism", {}) or {}
    causal_chain = mechanism.get("causal_chain", []) or []
    key_assumptions = mechanism.get("key_assumptions", []) or []

    prompt_template = load_prompt("vulgarization_fr")
    prompt = prompt_template.format(
        title=sharpened.get("title", "?"),
        formal_statement=sharpened.get("formal_statement", "?"),
        domains=" x ".join(domains) if domains else "?",
        causal_chain="\n".join(f"  {i+1}. {s}" for i, s in enumerate(causal_chain)),
        key_assumptions="; ".join(key_assumptions) if key_assumptions else "—",
        predictions=_format_predictions(sharpened.get("falsifiable_predictions", []) or []),
        phases=_format_phases(protocol.get("phases", []) or []),
        panel_summary=_format_panel([dict(r) for r in reviews]),
        verdict=meta.get("verdict", "?"),
        consensus_score=meta.get("consensus_score", "?"),
        final_recommendation=meta.get("final_recommendation", "")[:400],
        key_consensus="; ".join(meta.get("key_consensus", []) or [])[:400],
        key_disagreements="; ".join(meta.get("key_disagreements", []) or [])[:400],
        novelty_score=novelty.get("score", "?"),
        novelty_verdict=novelty.get("verdict", "?"),
    )

    logger.info("vulgarizing_brief", title=sharpened.get("title", "?"))
    try:
        data, _response = await complete_json(
            client,
            [{"role": "user", "content": prompt}],
            agent="vulgarization",
            max_tokens=3000,
            temperature=0.5,
            tracker=tracker,
        )
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("vulgarization_parse_failed", error=str(exc))
        raise ValueError(f"Failed to parse vulgarization output: {exc}") from exc

    concretely_raw = data.get("concretely", {}) or {}
    output = VulgarizationOutput(
        title_fr=data.get("title_fr", sharpened.get("title", "Untitled")),
        hypothesis_in_brief=data.get("hypothesis_in_brief", ""),
        why_it_matters=data.get("why_it_matters", ""),
        imagine_that=data.get("imagine_that", ""),
        concretely={
            "intro": concretely_raw.get("intro", ""),
            "phase1": concretely_raw.get("phase1", ""),
            "phase2": concretely_raw.get("phase2", ""),
            "phase3": concretely_raw.get("phase3", ""),
        },
        reviewers_say=data.get("reviewers_say", ""),
    )

    logger.info(
        "vulgarization_complete",
        title_fr=output["title_fr"],
        length=len(output["hypothesis_in_brief"]) + len(output["why_it_matters"]),
    )

    return output
