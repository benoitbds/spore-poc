"""Critic Agents for SPORE.

Implements the adversarial debate with Devil's Advocate and Angel's Advocate.
"""

import asyncio
import json
from typing import Any

from config import get_genome
from llm import get_llm_client
from llm.json_parse import complete_json
from llm.limits import max_tokens_for
from models.hypothesis import Hypothesis, Scores
from agents.base import PipelineState, DebateLog, load_prompt, format_predictions, format_context
from logging_config import get_logger, get_token_tracker
from progress import get_progress_tracker

logger = get_logger("critic_agent")


async def run_devil_advocate(
    hypothesis: Hypothesis,
    context_a: list[str],
    context_b: list[str],
) -> dict[str, Any]:
    """Run the Devil's Advocate critic.

    Args:
        hypothesis: The hypothesis to critique
        context_a: Context for domain A
        context_b: Context for domain B

    Returns:
        Parsed critique response
    """
    client = get_llm_client("critic_devil")
    prompt_template = load_prompt("devil_advocate")

    # Format predictions
    predictions_text = format_predictions([
        {
            "statement": p.statement,
            "metric": p.metric,
            "expected_range": p.expected_range,
        }
        for p in hypothesis.predictions
    ])

    prompt = prompt_template.format(
        bridge_summary=hypothesis.bridge.summary,
        bridge_mechanism=hypothesis.bridge.mechanism,
        bridge_type=hypothesis.bridge.type.value,
        kill_condition=hypothesis.kill_condition,
        predictions=predictions_text,
        domain_a_name=hypothesis.collision.domain_a.name,
        domain_a_context=format_context(context_a),
        domain_b_name=hypothesis.collision.domain_b.name,
        domain_b_context=format_context(context_b),
    )

    tracker = get_token_tracker()

    # S4A-3 — fail-closed. Ce bloc retournait cinq scores fabriqués
    # (0.5/0.5/0.5/0.5/0.3) et ``verdict: "flawed"`` quand le parsing
    # échouait. Ce n'était pas un défaut neutre : le devil note bas PAR
    # FONCTION, donc 0.5 le surclasse mécaniquement. Mesuré sur 206
    # hypothèses — composite réel moyen 0,430 — la fabrication produisait
    # ~0,50, soit le 79e percentile. Plus le critique faisait bien son
    # travail, plus son échec avantageait l'hypothèse.
    #
    # Constaté : 11 hypothèses curatées sur cette base, 8 notées a_tester,
    # 1 brief publié (SPR-2026-CD79) dont la critique adverse n'a jamais eu
    # lieu. Voir docs/S4A_devil_diagnostic.md.
    #
    # S4A-1 — max_tokens 2000 → 8000, aligné sur hypothesis_sharpening et
    # experimental_protocol. Cinq des douze échecs étaient des troncatures :
    # ``Unterminated string`` entre 8490 et 10162 caractères, soit exactement
    # le plafond de 2000 tokens. Consommation réelle mesurée : 1199 à 1593
    # tokens, donc une marge de 20 à 40 % qu'une critique verbeuse franchit.
    #
    # S4A-2 — complete_json apporte le mode JSON natif (qui devrait traiter
    # les 6 erreurs de délimiteur, des guillemets non échappés) et le rejeu à
    # température 0, que les critiques n'avaient pas du tout.
    try:
        data, _response = await complete_json(
            client,
            [{"role": "user", "content": prompt}],
            node="critic_devil",
            max_tokens=max_tokens_for("critic_devil"),
            temperature=0.7,
            tracker=tracker,
        )
    except json.JSONDecodeError as e:
        # S4A-4 — error, pas warning ; hypothesis_id systématique ; payload
        # tronqué. Les 12 échecs historiques n'avaient rien de tout ça, d'où
        # une reconstruction par proximité temporelle dans le diagnostic.
        logger.error(
            "devil_json_parse_failed",
            hypothesis_id=hypothesis.id,
            error=str(e),
            raw=str(getattr(e, "doc", ""))[:500],
        )
        raise ValueError(f"Devil advocate output unparseable: {e}") from e

    return data


async def run_angel_advocate(
    hypothesis: Hypothesis,
    context_a: list[str],
    context_b: list[str],
) -> dict[str, Any]:
    """Run the Angel's Advocate supporter.

    Args:
        hypothesis: The hypothesis to support
        context_a: Context for domain A
        context_b: Context for domain B

    Returns:
        Parsed support response
    """
    client = get_llm_client("critic_angel")
    prompt_template = load_prompt("angel_advocate")

    predictions_text = format_predictions([
        {
            "statement": p.statement,
            "metric": p.metric,
            "expected_range": p.expected_range,
        }
        for p in hypothesis.predictions
    ])

    prompt = prompt_template.format(
        bridge_summary=hypothesis.bridge.summary,
        bridge_mechanism=hypothesis.bridge.mechanism,
        bridge_type=hypothesis.bridge.type.value,
        kill_condition=hypothesis.kill_condition,
        predictions=predictions_text,
        domain_a_name=hypothesis.collision.domain_a.name,
        domain_a_context=format_context(context_a),
        domain_b_name=hypothesis.collision.domain_b.name,
        domain_b_context=format_context(context_b),
    )

    tracker = get_token_tracker()

    # S4A-1/2/3/4 — même traitement que le devil, mêmes raisons. Voir le bloc
    # de run_devil_advocate ci-dessus et docs/S4A_devil_diagnostic.md.
    # L'angel fabriquait ``verdict: "moderate_support"`` et les mêmes cinq
    # scores ; un seul de ses échecs est au dossier (2026-04-29), mais le
    # mécanisme est identique et son effet est symétrique : il gonflait la
    # moyenne au lieu de la neutraliser.
    try:
        data, _response = await complete_json(
            client,
            [{"role": "user", "content": prompt}],
            node="critic_angel",
            max_tokens=max_tokens_for("critic_angel"),
            temperature=0.7,
            tracker=tracker,
        )
    except json.JSONDecodeError as e:
        logger.error(
            "angel_json_parse_failed",
            hypothesis_id=hypothesis.id,
            error=str(e),
            raw=str(getattr(e, "doc", ""))[:500],
        )
        raise ValueError(f"Angel advocate output unparseable: {e}") from e

    return data


def aggregate_scores(devil: dict[str, float], angel: dict[str, float]) -> Scores:
    """Aggregate scores from devil and angel advocates.

    Takes the average of both perspectives for a balanced view.

    Args:
        devil: Devil's scores
        angel: Angel's scores

    Returns:
        Aggregated Scores object
    """
    # S4A-3 — une clé absente est une erreur, pas une valeur à 0,5.
    #
    # Ce ``.get(key, 0.5)`` était le second site de la même fabrication : même
    # si les critiques cessaient de produire des scores inventés, l'agrégation
    # les resubstituait ici. Retirer le repli d'un seul des deux endroits
    # aurait été un no-op — d'où les trois sites traités ensemble.
    #
    # KeyError plutôt qu'un défaut : un pôle du contradictoire qui n'a pas
    # statué ne se remplace pas par une moyenne. L'appelant fail-close.
    def avg(key: str) -> float:
        try:
            d = devil[key]
            a = angel[key]
        except KeyError as exc:
            raise ValueError(
                f"aggregate_scores: score {exc.args[0]!r} absent "
                f"(devil={sorted(devil)}, angel={sorted(angel)})"
            ) from exc
        return (d + a) / 2

    scores = Scores(
        novelty=avg("novelty"),
        coherence=avg("coherence"),
        testability=avg("testability"),
        impact_potential=avg("impact_potential"),
        hallucination_risk=avg("hallucination_risk"),
    )

    # Compute composite score
    scores.compute_composite()

    return scores


# S4A-5 — plafond de taille du debate_log persisté.
#
# 16 Ko : quatre fois la taille d'un débat réaliste mesuré (~3,9 Ko), donc
# aucune troncature en régime normal, et une borne dure si un critique part en
# vrille. Au-delà, les listes de criticisms/evidence sont élaguées — les
# verdicts et les trois jeux de scores, qui sont le cœur de la traçabilité,
# sont toujours conservés.
DEBATE_LOG_MAX_BYTES = 16 * 1024


def _serialize_debate_log(debate_log: DebateLog) -> str:
    """Sérialise le debate_log pour la colonne, sous plafond de taille.

    L'élagage retire d'abord le contenu long (criticisms, evidence) et garde
    les verdicts et les scores : un débat tronqué reste interprétable, un débat
    absent ne l'est pas.
    """
    blob = json.dumps(debate_log, ensure_ascii=False)
    if len(blob.encode("utf-8")) <= DEBATE_LOG_MAX_BYTES:
        return blob

    trimmed = dict(debate_log)
    n_crit = len(trimmed.get("devil_criticisms") or [])
    n_evid = len(trimmed.get("angel_evidence") or [])
    trimmed["devil_criticisms"] = []
    trimmed["angel_evidence"] = []
    trimmed["_truncated"] = {
        "reason": f"debate log exceeded {DEBATE_LOG_MAX_BYTES} bytes",
        "original_bytes": len(blob.encode("utf-8")),
        "dropped_criticisms": n_crit,
        "dropped_evidence": n_evid,
    }
    logger.warning(
        "debate_log_truncated",
        hypothesis_id=trimmed.get("hypothesis_id"),
        original_bytes=len(blob.encode("utf-8")),
        cap=DEBATE_LOG_MAX_BYTES,
    )
    return json.dumps(trimmed, ensure_ascii=False)


async def critique_hypothesis(
    hypothesis: Hypothesis,
    collisions: list,  # To find context
) -> tuple[Hypothesis, DebateLog]:
    """Run both critics on a hypothesis in parallel.

    Args:
        hypothesis: The hypothesis to critique
        collisions: List of collisions to find context

    Returns:
        Tuple of (updated hypothesis with scores, debate log)
    """
    # Find matching collision for context
    context_a: list[str] = []
    context_b: list[str] = []

    for collision in collisions:
        if (collision.domain_a.id == hypothesis.collision.domain_a.id and
            collision.domain_b.id == hypothesis.collision.domain_b.id):
            context_a = collision.context_a
            context_b = collision.context_b
            break

    logger.info(
        "critiquing_hypothesis",
        id=hypothesis.id,
        domain_a=hypothesis.collision.domain_a.name,
        domain_b=hypothesis.collision.domain_b.name,
    )

    # Run both critics in parallel
    devil_task = run_devil_advocate(hypothesis, context_a, context_b)
    angel_task = run_angel_advocate(hypothesis, context_a, context_b)

    devil_result, angel_result = await asyncio.gather(devil_task, angel_task)

    # Aggregate scores
    devil_scores = devil_result.get("scores", {})
    angel_scores = angel_result.get("scores", {})
    final_scores = aggregate_scores(devil_scores, angel_scores)

    # Update hypothesis with scores
    hypothesis.scores = final_scores

    # Create debate log
    debate_log: DebateLog = {
        "hypothesis_id": hypothesis.id,
        "devil_verdict": devil_result.get("verdict", "unknown"),
        "devil_criticisms": devil_result.get("criticisms", []),
        "devil_scores": devil_scores,
        "angel_verdict": angel_result.get("verdict", "unknown"),
        "angel_evidence": angel_result.get("supporting_evidence", []),
        "angel_scores": angel_scores,
        "final_scores": {
            "novelty": final_scores.novelty,
            "coherence": final_scores.coherence,
            "testability": final_scores.testability,
            "impact_potential": final_scores.impact_potential,
            "hallucination_risk": final_scores.hallucination_risk,
            "composite": final_scores.composite or 0,
        },
    }

    # S4A-5 — persister la trace du contradictoire.
    #
    # Le champ ``critic_debate_log`` n'avait JAMAIS été câblé : ce debate_log
    # était rangé dans ``state["debate_logs"]``, que rien ne relit, et
    # ``hypothesis.critic_debate_log`` n'était assigné nulle part — la colonne
    # valait None sur les 207 lignes. Ce n'était pas une perte de données,
    # c'était un branchement jamais fait. constitution.yaml pose « all
    # hypotheses include full source tracing » ; la trace du débat adverse
    # n'existait nulle part.
    #
    # Le design doc évoque un chemin de fichier ("debate_0042.json") ; la
    # colonne est plus simple et suffit. Volume mesuré sur un debate_log
    # réaliste : ~3,9 Ko, soit ~0,8 Mo pour les 207 lignes historiques et
    # ~14 Mo/an au rythme actuel, contre une base de 71 Mo. Non prohibitif.
    hypothesis.critic_debate_log = _serialize_debate_log(debate_log)

    logger.info(
        "critique_complete",
        id=hypothesis.id,
        devil_verdict=devil_result.get("verdict"),
        angel_verdict=angel_result.get("verdict"),
        composite_score=f"{final_scores.composite:.2f}" if final_scores.composite else "N/A",
        debate_log_bytes=len(hypothesis.critic_debate_log or ""),
    )

    return hypothesis, debate_log


async def critic_agent(state: PipelineState) -> PipelineState:
    """Critic Agent: runs adversarial debate on hypotheses.

    This agent:
    1. Takes each hypothesis
    2. Runs Devil and Angel advocates in parallel
    3. Aggregates scores
    4. Creates debate logs

    Args:
        state: Current pipeline state with hypotheses

    Returns:
        Updated state with scored hypotheses and debate logs
    """
    hypotheses = state.get("hypotheses", [])
    collisions = state.get("collisions", [])

    if not hypotheses:
        logger.info("no_hypotheses_to_critique")
        state["debate_logs"] = []
        return state

    logger.info("critic_starting", n_hypotheses=len(hypotheses))

    # Get progress tracker
    progress_tracker = get_progress_tracker()

    scored_hypotheses: list[Hypothesis] = []
    debate_logs: list[DebateLog] = []
    errors = state.get("errors", [])
    n_dropped = 0  # S4A-3 — hypothèses abandonnées faute de critique

    # Process hypotheses (could parallelize more, but respect rate limits)
    for hypothesis in hypotheses:
        # Update progress stage to critics
        progress_tracker.set_current_collision(
            domain_a=hypothesis.collision.domain_a.name,
            domain_b=hypothesis.collision.domain_b.name,
            distance=hypothesis.collision.distance_score,
            stage="critics",
        )

        try:
            scored_hyp, debate_log = await critique_hypothesis(
                hypothesis=hypothesis,
                collisions=collisions,
            )
            scored_hypotheses.append(scored_hyp)
            debate_logs.append(debate_log)

            # Update hypothesis score in progress
            if scored_hyp.scores and scored_hyp.scores.composite:
                progress_tracker.hypothesis_scored(
                    hypothesis_id=scored_hyp.id,
                    score=scored_hyp.scores.composite,
                )

        except Exception as e:
            # S4A-3 — l'hypothèse est ABANDONNÉE pour ce cycle, pas conservée.
            #
            # Avant : ``scored_hypotheses.append(hypothesis)`` la gardait sans
            # scores. Elle poursuivait alors sa route jusqu'au reviewer, où
            # ``composite = ... else 0.5`` (reviewer.py) lui rendait un
            # composite au-dessus du seuil d'override ``composite < 0.35``.
            # Fail-closer les critiques sans fermer ce chemin aurait déplacé
            # la fabrication d'un cran, pas supprimée.
            #
            # Ce qu'il en reste : la ligne n'est jamais persistée — le
            # curator ne la voit pas, save_hypothesis n'est pas appelé. Reste
            # une entrée dans ``state["errors"]``, comptée dans le run, et une
            # ligne de log ``critique_failed`` de niveau error portant
            # l'hypothesis_id. La collision est perdue pour ce cycle ; elle
            # peut être retirée puisque l'explorer en tire de nouvelles à
            # chaque run, mais CETTE hypothèse-là n'est pas rejouable.
            n_dropped += 1
            logger.error(
                "critique_failed",
                hypothesis_id=hypothesis.id,
                error=str(e),
                outcome="hypothesis_dropped",
                domain_a=hypothesis.collision.domain_a.name,
                domain_b=hypothesis.collision.domain_b.name,
            )
            errors.append({
                "agent": "critic",
                "hypothesis_id": hypothesis.id,
                "error": str(e),
                "outcome": "hypothesis_dropped",
            })

    logger.info(
        "critic_complete",
        hypotheses_scored=len(scored_hypotheses),
        debate_logs=len(debate_logs),
        hypotheses_dropped=n_dropped,
        n_input=len(hypotheses),
    )

    state["hypotheses"] = scored_hypotheses
    state["debate_logs"] = debate_logs
    state["errors"] = errors

    return state
