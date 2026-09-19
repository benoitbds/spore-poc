"""Point d'appel LLM unique de la couche narrative.

Chaque appel passe par ``call_json`` :

* client obtenu par l'abstraction existante (``llm.client.get_llm_client``),
  DeepSeek par défaut — un nom d'agent absent du genome retombe sur
  ``deepseek`` / ``deepseek-v4-flash`` (``get_provider_for_agent``, valeurs
  par défaut de ``agent_config.get``) ;
* mode JSON et parseur partagé (``llm.json_parse.complete_json``), qui rejoue
  une fois une troncature (plafond doublé) et une fois un JSON invalide ;
* rejeu avec backoff exponentiel des pannes transitoires (réseau, fournisseur
  saturé, délai d'appel), borné par la configuration ;
* registre de coût : une ligne ``v2_llm_costs`` par réponse réseau reçue,
  tronquée ou non, coût calculé par la grille du ``TokenTracker`` existant
  (instance locale : le tracker global du run L0 alimente
  ``runs.total_cost_usd`` et le budget L0, que la couche narrative ne doit pas
  influencer).

Le registre voit les réponses tronquées parce que la mesure se branche sur
``_complete`` des clients feuilles (comme ``llm_calls``, écrit avant le
contrôle de fin de génération) : ``complete_json`` ne passe au tracker que
les réponses terminées, ce qui laisserait les troncatures hors du coût.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm.client import FallbackClient, LLMClient, LLMResponse, get_llm_client
from llm.errors import LLMOutputIncomplete, LLMOutputTruncated
from llm.json_parse import complete_json
from logging_config import TokenTracker, get_logger
from narrative.config import LLMStepConfig, NarrativeConfig
from storage import narrative_db

logger = get_logger("narrative.llm")

#: Erreurs qu'un nouvel essai à l'identique ne corrigerait pas. ``ValueError``
#: couvre le JSON invalide (``JSONDecodeError``) après le rejeu de
#: ``complete_json`` et la configuration absente (clé API, pydantic).
NON_RETRYABLE: tuple[type[BaseException], ...] = (
    LLMOutputTruncated,
    LLMOutputIncomplete,
    ValueError,
)

#: Fabrique de client, remplaçable en test (``resolve_client`` par défaut).
ClientFactory = Callable[[LLMStepConfig], LLMClient]


@dataclass
class CostMeter:
    """Cumul des coûts et jetons d'une étape (tous appels, échecs compris).

    Attributes:
        cost_usd: Coût cumulé.
        tokens_in: Jetons d'entrée cumulés.
        tokens_out: Jetons de sortie cumulés.
        calls: Nombre de réponses réseau comptées.
    """

    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0

    def add(self, other: CostMeter) -> None:
        """Ajoute un autre compteur.

        Args:
            other: Compteur à ajouter.
        """
        self.cost_usd += other.cost_usd
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.calls += other.calls


@dataclass
class CallResult:
    """Résultat d'un appel JSON réussi.

    Attributes:
        data: Objet JSON parsé.
        model: Modèle renvoyé par l'API (servi), ``mock`` en test.
        requested_model: Modèle demandé (clé de tarification).
        meter: Coûts et jetons de l'appel, rejeux compris.
    """

    data: dict[str, Any]
    model: str
    requested_model: str
    meter: CostMeter = field(default_factory=CostMeter)


def resolve_client(step: LLMStepConfig) -> LLMClient:
    """Client LLM d'une étape.

    Sans ``provider`` ni ``model`` explicites, passe par
    ``get_llm_client(step.agent)`` (chemin du pipeline, repli Anthropic
    compris si la clé existe). Sinon, construit le client demandé avec les clés
    de ``SporeSettings``, sans toucher au genome.

    Args:
        step: Étape (rédaction, garde, traduction).

    Returns:
        Client prêt à l'emploi.

    Raises:
        ValueError: Fournisseur inconnu ou clé absente.
    """
    if step.provider is None and step.model is None:
        return get_llm_client(step.agent)

    from config import get_settings
    from llm.client import AnthropicClient, DeepSeekClient

    settings = get_settings()
    provider = step.provider or "deepseek"
    if provider == "deepseek":
        primary = DeepSeekClient(
            api_key=settings.deepseek_api_key, model=step.model or "deepseek-v4-flash"
        )
        if settings.anthropic_api_key:
            return FallbackClient(
                primary,
                AnthropicClient(api_key=settings.anthropic_api_key, model="claude-sonnet-5"),
            )
        return primary
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY absente pour un fournisseur anthropic")
        return AnthropicClient(
            api_key=settings.anthropic_api_key, model=step.model or "claude-sonnet-5"
        )
    raise ValueError(f"fournisseur inconnu : {provider!r}")


#: Fabrique courante ; les tests la remplacent par ``set_client_factory``.
_client_factory: ClientFactory = resolve_client


def set_client_factory(factory: ClientFactory | None) -> ClientFactory:
    """Remplace la fabrique de client (tests, calibration).

    Args:
        factory: Nouvelle fabrique, ou ``None`` pour revenir à
            ``resolve_client``.

    Returns:
        La fabrique précédente, pour restauration.
    """
    global _client_factory
    previous = _client_factory
    _client_factory = factory or resolve_client
    return previous


def _instrument(client: LLMClient, sink: list[LLMResponse]) -> None:
    """Branche la mesure sur ``_complete`` des clients feuilles.

    Le client est construit pour cet appel seulement (``get_llm_client`` rend
    une instance neuve), la mesure ne fuit donc pas ailleurs.

    Args:
        client: Client à instrumenter (``FallbackClient`` compris).
        sink: Liste où déposer chaque réponse réseau reçue.
    """
    if isinstance(client, FallbackClient):
        _instrument(client.primary, sink)
        _instrument(client.fallback, sink)
        return

    original: Callable[..., Awaitable[LLMResponse]] = client._complete

    async def measured(*args: Any, **kwargs: Any) -> LLMResponse:
        response = await original(*args, **kwargs)
        sink.append(response)
        return response

    client._complete = measured  # type: ignore[method-assign]


def price(response: LLMResponse, node: str) -> float:
    """Coût d'une réponse, grille du ``TokenTracker`` existant.

    Args:
        response: Réponse réseau.
        node: Nœud appelant (étiquette du tracker).

    Returns:
        Coût en USD.
    """
    tracker = TokenTracker()
    return tracker.log_call(
        agent=node,
        # Nom demandé : clé de tarification du tracker (llm/client.py).
        model=response.requested_model or response.model,
        input_tokens=int(response.input_tokens or 0),
        output_tokens=int(response.output_tokens or 0),
        provider=response.provider,
        cache_hit=bool(response.cache_hit),
    )


def _write_ledger(
    db_path: str | Path,
    records: list[tuple[str, int, int, float]],
    *,
    run_label: str,
    brief_id: str | None,
    node: str,
) -> None:
    with narrative_db.connect(db_path) as conn:
        for model, tokens_in, tokens_out, cost in records:
            narrative_db.insert_llm_cost(
                conn,
                run_label=run_label,
                brief_id=brief_id,
                node=node,
                model=model or "unknown",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            )


async def _flush(
    sink: list[LLMResponse],
    meter: CostMeter,
    *,
    db_path: str | Path,
    run_label: str,
    brief_id: str | None,
    node: str,
) -> None:
    """Écrit au registre les réponses reçues depuis le dernier passage.

    Args:
        sink: Réponses mesurées (vidée ici).
        meter: Compteur à incrémenter.
        db_path: Base du registre.
        run_label: Étiquette de coût.
        brief_id: Brief concerné.
        node: Nœud appelant.
    """
    pending = list(sink)
    sink.clear()
    if not pending:
        return
    records: list[tuple[str, int, int, float]] = []
    for response in pending:
        cost = price(response, node)
        tokens_in = int(response.input_tokens or 0)
        tokens_out = int(response.output_tokens or 0)
        meter.cost_usd += cost
        meter.tokens_in += tokens_in
        meter.tokens_out += tokens_out
        meter.calls += 1
        records.append((response.requested_model or response.model, tokens_in, tokens_out, cost))
    try:
        await asyncio.to_thread(
            _write_ledger,
            db_path,
            records,
            run_label=run_label,
            brief_id=brief_id,
            node=node,
        )
    except Exception as exc:  # noqa: BLE001 — le registre ne fait pas échouer l'étape
        logger.error("narrative_cost_ledger_write_failed", node=node, error=str(exc)[:300])


async def call_json(
    *,
    step: LLMStepConfig,
    node: str,
    prompt: str,
    config: NarrativeConfig,
    db_path: str | Path,
    run_label: str,
    brief_id: str | None,
    meter: CostMeter | None = None,
) -> CallResult:
    """Appelle le LLM en mode JSON, avec rejeu et registre de coût.

    Args:
        step: Étape (agent, modèle, température, plafond).
        node: Nom du nœud, pour ``llm_calls``, les logs et ``v2_llm_costs``.
        prompt: Prompt complet (il contient le mot « JSON »).
        config: Configuration de la couche (délais, backoff).
        db_path: Base où écrire le registre de coût.
        run_label: Étiquette de coût.
        brief_id: Brief concerné.
        meter: Compteur de l'appelant, mis à jour même en cas d'échec.

    Returns:
        Résultat parsé et coûts de l'appel.

    Raises:
        Exception: La dernière erreur, une fois les essais épuisés, ou
            immédiatement pour une erreur non rejouable.
    """
    meter = meter if meter is not None else CostMeter()
    local = CostMeter()
    messages = [{"role": "user", "content": prompt}]
    attempts = max(1, config.retry_max_attempts)
    sink: list[LLMResponse] = []

    for attempt in range(1, attempts + 1):
        try:
            client = _client_factory(step)
            _instrument(client, sink)
            data, response = await asyncio.wait_for(
                complete_json(
                    client,
                    messages,
                    node=node,
                    max_tokens=step.max_tokens,
                    temperature=step.temperature,
                ),
                timeout=config.llm_call_timeout_s,
            )
        except NON_RETRYABLE as exc:
            await _flush(
                sink, local, db_path=db_path, run_label=run_label, brief_id=brief_id, node=node
            )
            meter.add(local)
            logger.warning(
                "narrative_llm_call_failed",
                node=node,
                brief_id=brief_id,
                attempt=attempt,
                retryable=False,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            raise
        except Exception as exc:
            await _flush(
                sink, local, db_path=db_path, run_label=run_label, brief_id=brief_id, node=node
            )
            logger.warning(
                "narrative_llm_call_failed",
                node=node,
                brief_id=brief_id,
                attempt=attempt,
                retryable=attempt < attempts,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            if attempt >= attempts:
                meter.add(local)
                raise
            delay = min(config.retry_base_delay_s * (2 ** (attempt - 1)), config.retry_max_delay_s)
            await asyncio.sleep(delay)
            continue

        await _flush(
            sink, local, db_path=db_path, run_label=run_label, brief_id=brief_id, node=node
        )
        meter.add(local)
        served = getattr(response, "model", "") or ""
        requested = getattr(response, "requested_model", "") or served
        logger.info(
            "narrative_llm_call_done",
            node=node,
            brief_id=brief_id,
            model=served or requested,
            tokens_in=local.tokens_in,
            tokens_out=local.tokens_out,
            cost_usd=round(local.cost_usd, 6),
        )
        return CallResult(
            data=data,
            model=served or requested or "unknown",
            requested_model=requested,
            meter=local,
        )

    raise RuntimeError("unreachable")  # pragma: no cover
