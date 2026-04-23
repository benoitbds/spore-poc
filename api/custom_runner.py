"""Custom collision runner for paying customers.

A custom request is a user-supplied domain pair that skips the random
Explorer draw entirely. The flow is:

  1. Light enrichment — fetch 2-3 top abstracts per domain from
     Semantic Scholar. Without this, Synthesis has no literature
     substrate and produces generic output.
  2. Synthesis → Critic → Curator → Impact (reuse existing agents).
  3. Reviewer — record the auto-verdict but *ignore* it for routing:
     the customer has paid, we deliver a brief regardless.
  4. Post-fire — always triggered. If the reviewer flagged the
     hypothesis as 'poubelle', the brief is tagged ``low_confidence``
     in its metadata so the frontend can warn the reader.
  5. Email the customer with the brief URL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from agents.base import PipelineState
from agents.constitution_guard import _domain_is_excluded
from agents.critic import critic_agent
from agents.curator import curator_agent
from agents.impact import impact_agent
from agents.reviewer import review_hypothesis
from agents.synthesis import synthesis_agent
from config import get_constitution
from graph.post_fire_pipeline import run_post_fire_pipeline
from knowledge.semantic_scholar import get_semantic_scholar_client
from logging_config import get_logger
from models.collision import Collision, CollisionPair, CollisionStrategy
from models.domain import Domain
from storage.database import (
    init_database,
    save_hypothesis,
    update_hypothesis_auto_feedback,
    update_brief,
)
from api.db import (
    get_custom_request,
    get_user,
    update_custom_request,
)
from api.emails import send_brief_ready

logger = get_logger("api.custom_runner")


CUSTOM_DISTANCE = 0.60
"""Nominal distance for custom collisions — halfway between gate min/max."""

MAX_ABSTRACTS_PER_DOMAIN = 3
"""Few abstracts is enough; more slows down the run."""


async def _enrich_domain(name: str) -> Domain:
    """Fetch 2-3 Semantic Scholar abstracts for ``name`` and wrap it in a Domain.

    On any API failure we still return a Domain — Synthesis can cope
    with empty context_abstracts, just at reduced quality.
    """
    dom_id = f"D-CUS-{uuid4().hex[:8]}"
    try:
        ss = get_semantic_scholar_client()
        abstracts = await ss.get_context(
            domain_name=name,
            key_concepts=[],
            max_papers=MAX_ABSTRACTS_PER_DOMAIN,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("semantic_scholar_enrichment_failed", domain=name, error=str(exc))
        abstracts = []

    return Domain(
        id=dom_id,
        name=name,
        taxonomy_source="custom",
        context_abstracts=abstracts,
    )


def _build_collision(domain_a: Domain, domain_b: Domain) -> Collision:
    """Wrap two enriched domains into the Collision shape Synthesis expects."""
    return Collision(
        pair=CollisionPair(
            domain_a=domain_a,
            domain_b=domain_b,
            strategy=CollisionStrategy.SEMANTIC_DISTANCE,
            distance_score=CUSTOM_DISTANCE,
        ),
        context_a=domain_a.context_abstracts,
        context_b=domain_b.context_abstracts,
        enrichment_source="semantic_scholar",
    )


def _initial_state(collision: Collision, run_id: str) -> PipelineState:
    """Build the minimal PipelineState a custom run needs."""
    return {  # type: ignore[typeddict-item]
        "n_collisions": 1,
        "distance_min": 0.4,
        "distance_max": 0.7,
        "domain": "custom",
        "run_id": run_id,
        "collision_pairs": [collision.pair],
        "collisions": [collision],
        "gated_collisions": [collision],  # bypass gate — paying user
        "gate_results": [],
        "rejected_collisions": [],
        "hypotheses": [],
        "no_bridges": [],
        "debate_logs": [],
        "curated_hypotheses": [],
        "gap_manifests": [],
        "metrics": {},
        "errors": [],
        "constitution": get_constitution().to_dict(),
        "killed": False,
        "kill_reason": "",
    }


async def _pick_surprise_partner(
    request_id: str, domain_a: str,
) -> tuple[str, Optional[float]]:
    """Pick a partner for ``domain_a`` using the L0 genome's fertile zone.

    If the user's domain is in the domain map, use the precomputed
    distance matrix. Otherwise, embed the string on the fly and pick
    from the same fertile zone — this replaces the old uniform-random
    fallback that produced incoherent pairs (e.g. GRN × Quantum Optics).

    Returns:
        Tuple of (partner_name, cosine_distance_or_None). Distance is
        None only when the genome's chaos_floor path fires on a
        free-form input where we can't recover the distance.

    Raises:
        RuntimeError: domain map empty or user domain too far from
            everything known (surface as a "please reformulate" to the user).
    """
    from config import get_genome
    from knowledge.domain_map import get_domain_map, DomainNotInterpretableError

    genome = get_genome()
    randomness = getattr(genome, "randomness", {}) or {}
    distance_min = float(randomness.get("distance_min", 0.4))
    distance_max = float(randomness.get("distance_max", 0.7))
    chaos_floor = float(randomness.get("chaos_floor", 0.0))

    domain_map = get_domain_map("all_science")
    try:
        pick = domain_map.get_random_partner_for(
            domain_name=domain_a,
            distance_min=distance_min,
            distance_max=distance_max,
            chaos_floor=chaos_floor,
        )
    except DomainNotInterpretableError as exc:
        err = str(exc)
        logger.error(
            "surprise_partner_uninterpretable",
            request_id=request_id,
            domain_a=domain_a,
            detail=err,
        )
        await update_custom_request(
            request_id, status="failed", error_message=err, completed=True,
        )
        raise RuntimeError(err) from exc

    if pick is None:
        err = "No domains available for surprise partner selection"
        logger.error("surprise_partner_unavailable", request_id=request_id, detail=err)
        await update_custom_request(
            request_id, status="failed", error_message=err, completed=True,
        )
        raise RuntimeError(err)

    partner, distance = pick
    return partner.name, distance


async def _validate_domains(request_id: str, request: dict[str, Any]) -> None:
    """Reject the run if either domain hits the constitution hard-kill list."""
    excluded = [
        "weapons_development",
        "surveillance_technology",
        "any domain with dual-use concerns without human approval",
    ]
    for field in ("domain_a", "domain_b"):
        name = request[field]
        hit = _domain_is_excluded(name, excluded)
        if hit:
            err = f"Domain '{name}' blocked by constitution (rule '{hit}')"
            logger.error("custom_domain_blocked", request_id=request_id, detail=err)
            await update_custom_request(
                request_id, status="failed", error_message=err, completed=True,
            )
            raise ValueError(err)


async def run_custom_request(request_id: str) -> dict[str, Any]:
    """Execute a paid custom_request end-to-end.

    Returns a summary dict with hypothesis_id, brief_id, reviewer_verdict,
    low_confidence flag. Persists progress in the custom_requests table
    so ``GET /api/custom/{id}/status`` can report intermediate state.
    """
    request = await get_custom_request(request_id)
    if request is None:
        raise ValueError(f"unknown custom request {request_id}")
    if request["status"] == "complete":
        logger.info("custom_already_complete", request_id=request_id)
        return {
            "hypothesis_id": request["hypothesis_id"],
            "brief_id": request["brief_id"],
            "reviewer_verdict": None,
            "low_confidence": False,
        }

    # Surprise mode: domain_b was persisted as an empty-string sentinel.
    # Resolve it now against the domain map and patch the row so the
    # status endpoint starts showing the real partner immediately.
    if not request.get("domain_b"):
        picked_name, picked_distance = await _pick_surprise_partner(
            request_id, request["domain_a"],
        )
        await update_custom_request(request_id, domain_b=picked_name)
        request["domain_b"] = picked_name
        logger.info(
            "surprise_domain_selected",
            request_id=request_id,
            domain_a=request["domain_a"],
            domain_b=picked_name,
            distance=picked_distance,
        )

    await _validate_domains(request_id, request)
    await update_custom_request(request_id, status="running")

    await init_database()

    run_id = f"custom-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    logger.info(
        "custom_run_starting",
        request_id=request_id, run_id=run_id,
        domain_a=request["domain_a"], domain_b=request["domain_b"],
    )

    # 1. Light enrichment — detect total SS failure
    dom_a = await _enrich_domain(request["domain_a"])
    dom_b = await _enrich_domain(request["domain_b"])
    total_abstracts = len(dom_a.context_abstracts) + len(dom_b.context_abstracts)
    grounding_degraded = total_abstracts == 0
    logger.info(
        "custom_enrichment_done",
        request_id=request_id,
        abstracts_a=len(dom_a.context_abstracts),
        abstracts_b=len(dom_b.context_abstracts),
        grounding_degraded=grounding_degraded,
    )
    if grounding_degraded:
        logger.warning(
            "custom_enrichment_degraded",
            request_id=request_id,
            detail="Semantic Scholar returned 0 abstracts for both domains — "
                   "pipeline will run without literature grounding",
        )

    collision = _build_collision(dom_a, dom_b)
    state = _initial_state(collision, run_id)

    # 2. Synthesis → Critic → Curator → Impact
    try:
        state = await synthesis_agent(state)
        if state.get("hypotheses"):
            state = await critic_agent(state)
        state = await curator_agent(state)
        if state.get("curated_hypotheses"):
            state = await impact_agent(state)
    except Exception as exc:  # noqa: BLE001
        logger.error("custom_pipeline_failed", request_id=request_id, error=str(exc))
        await update_custom_request(
            request_id, status="failed", error_message=str(exc), completed=True,
        )
        raise

    curated = state.get("curated_hypotheses", [])
    if not curated:
        err = "Pipeline produced no curated hypothesis for this collision."
        logger.error("custom_no_hypothesis", request_id=request_id, detail=err)
        await update_custom_request(
            request_id, status="failed", error_message=err, completed=True,
        )
        raise RuntimeError(err)

    hypothesis = curated[0]
    await save_hypothesis(hypothesis)

    # 3. Reviewer — record verdict, but we don't route on it.
    reviewer_verdict: Optional[str] = None
    low_confidence = False
    try:
        feedback = await review_hypothesis(hypothesis)
        reviewer_verdict = feedback.verdict
        low_confidence = feedback.verdict == "poubelle"
        await update_hypothesis_auto_feedback(hypothesis.id, feedback)
    except Exception as exc:  # noqa: BLE001 — non-fatal for delivery
        logger.error("custom_reviewer_failed", request_id=request_id, error=str(exc))

    # 4. Post-fire — always, regardless of verdict. Paying user = delivery.
    #    If SS was down, skip literature grounding (degraded mode).
    try:
        pf_state = await run_post_fire_pipeline(
            hypothesis=hypothesis.bridge.summary,
            domains=[hypothesis.collision.domain_a.name, hypothesis.collision.domain_b.name],
            mechanisms=hypothesis.bridge.mechanism,
            keywords=[],
            gap_manifest=hypothesis.gap_manifest.model_dump() if hypothesis.gap_manifest else {},
            grounding_degraded=grounding_degraded,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("custom_post_fire_failed", request_id=request_id, error=str(exc))
        await update_custom_request(
            request_id, status="failed",
            error_message=f"post-fire failed: {exc}", completed=True,
            hypothesis_id=hypothesis.id,
        )
        raise

    brief_id = pf_state.get("brief_id")
    if not brief_id:
        err = pf_state.get("kill_reason") or "post-fire did not produce a brief"
        logger.error("custom_no_brief", request_id=request_id, detail=err)
        await update_custom_request(
            request_id, status="failed", error_message=err, completed=True,
            hypothesis_id=hypothesis.id,
        )
        raise RuntimeError(err)

    # Flag low_confidence and/or low_evidence on the brief row.
    if low_confidence:
        await update_brief(brief_id, low_confidence=1)
    if grounding_degraded:
        await update_brief(brief_id, low_evidence=1)

    await update_custom_request(
        request_id,
        status="complete",
        hypothesis_id=hypothesis.id,
        brief_id=brief_id,
        completed=True,
    )

    # 5. Email the customer.
    user = await get_user(request["user_id"])
    if user is not None:
        title = getattr(hypothesis.bridge, "summary", "") or "Votre brief SPORE"
        degraded_notice = (
            "\n\nNote : ce brief a été généré avec un accès limité à la "
            "littérature scientifique. Une version enrichie vous sera "
            "envoyée sous 7 jours."
        ) if grounding_degraded else ""
        try:
            send_brief_ready(
                user["email"], brief_id, title[:120] + degraded_notice,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("custom_email_failed", request_id=request_id, error=str(exc))

    logger.info(
        "custom_run_complete",
        request_id=request_id,
        hypothesis_id=hypothesis.id,
        brief_id=brief_id,
        reviewer_verdict=reviewer_verdict,
        low_confidence=low_confidence,
    )
    return {
        "hypothesis_id": hypothesis.id,
        "brief_id": brief_id,
        "reviewer_verdict": reviewer_verdict,
        "low_confidence": low_confidence,
        "grounding_degraded": grounding_degraded,
    }
