"""Abstract LLM client with multi-provider support.

Supports:
- Anthropic (Claude models)
- DeepSeek (OpenAI-compatible API)

Usage:
    client = get_llm_client("synthesis")  # Gets client based on genome config
    response = await client.complete(messages, max_tokens=1000, node="synthesis")
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from llm.errors import (
    LLMOutputIncomplete,
    LLMOutputTruncated,
    LLMResourceExhausted,
)
from llm.telemetry import record_llm_call
from logging_config import get_logger

logger = get_logger("llm_client")

#: Seule fin de génération dont le texte est exploitable.
FINISH_STOP = "stop"

#: Fin de génération par atteinte du plafond de sortie.
FINISH_LENGTH = "length"

#: Interruption transitoire côté fournisseur (DeepSeek).
FINISH_RESOURCE = "insufficient_system_resource"

#: Normalisation des motifs Anthropic vers le vocabulaire OpenAI/DeepSeek.
_ANTHROPIC_FINISH_REASONS = {
    "end_turn": FINISH_STOP,
    "stop_sequence": FINISH_STOP,
    "max_tokens": FINISH_LENGTH,
}


def normalize_anthropic_stop_reason(stop_reason: str | None) -> str:
    """Traduit un ``stop_reason`` Anthropic en motif normalisé.

    ``end_turn`` et ``stop_sequence`` deviennent ``stop``, ``max_tokens``
    devient ``length``. Toute autre valeur est conservée telle quelle : elle
    sera refusée comme motif inconnu plutôt que réinterprétée.

    Args:
        stop_reason: Valeur renvoyée par l'API Anthropic, éventuellement absente.

    Returns:
        Motif normalisé ; ``unknown`` si l'API n'en a pas renvoyé.
    """
    if not stop_reason:
        return "unknown"
    return _ANTHROPIC_FINISH_REASONS.get(stop_reason, stop_reason)


@dataclass
class LLMResponse:
    """Unified response from any LLM provider.

    Attributes:
        content: Texte produit.
        input_tokens: Jetons d'entrée facturés.
        output_tokens: Jetons produits.
        model: Modèle **renvoyé par l'API**, et non le nom configuré : c'est la
            seule façon de voir un changement de routage côté fournisseur.
        provider: Fournisseur ayant servi l'appel.
        cache_hit: Cache d'entrée touché (DeepSeek).
        finish_reason: Motif de fin de génération, normalisé.
        requested_model: Nom demandé, issu du genome. Clé de tarification du
            ``TokenTracker`` — ne jamais lui substituer ``model``.
        system_fingerprint: Empreinte de configuration renvoyée par l'API,
            ``None`` si le fournisseur n'en expose pas.
        latency_ms: Durée de l'appel, mesurée par le client.
    """

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    cache_hit: bool = False  # For DeepSeek cache tracking
    finish_reason: str = FINISH_STOP
    requested_model: str = ""
    system_fingerprint: str | None = None
    latency_ms: int = 0


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    provider: str = "unknown"

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4000,
        temperature: float = 0.7,
        system: str | None = None,
        json_mode: bool = False,
        *,
        node: str,
        attempt: int = 1,
    ) -> LLMResponse:
        """Send a completion request to the LLM, measure it, and gate its end.

        Squelette commun à tous les clients : l'appel réseau est délégué à
        ``_complete``, puis la réponse est mesurée (``llm_calls``) et contrôlée.
        Le contrôle est ici, et non chez les appelants, pour qu'aucun chemin —
        parsing JSON, normalisation de langue, traduction — ne puisse utiliser
        le texte d'une génération qui ne s'est pas terminée d'elle-même.

        L'ordre compte : la mesure est écrite **avant** le contrôle, sinon les
        appels tronqués, les seuls qui intéressent, seraient les seuls absents
        de la table.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            system: Optional system prompt
            json_mode: Ask the provider to constrain the output to valid JSON.
                Opt-in per call, never global: DeepSeek's JSON mode requires
                the word "json" in the prompt and errors otherwise, and not
                every SPORE prompt is a JSON prompt (L0/L1 agents included).
                Only callers whose prompt already asks for JSON may set it.
            node: Nœud appelant, obligatoire. Sert de sujet à la mesure et aux
                erreurs ; sans lui, une troncature ne désigne personne.
            attempt: Numéro de tentative pour ce nœud, à partir de 1. Le client
                ne peut pas le déduire : c'est l'appelant qui possède la boucle
                de rejeu.

        Returns:
            LLMResponse dont ``finish_reason`` vaut ``stop``.

        Raises:
            LLMOutputTruncated: Génération coupée par le plafond de sortie.
            LLMResourceExhausted: Interruption transitoire côté fournisseur.
            LLMOutputIncomplete: Tout autre motif de fin.
        """
        started = time.perf_counter()
        response = await self._complete(
            messages, max_tokens, temperature, system, json_mode
        )
        response.latency_ms = int((time.perf_counter() - started) * 1000)

        await record_llm_call(
            response, node=node, attempt=attempt, max_tokens=max_tokens
        )
        _enforce_finish_reason(
            response, node=node, attempt=attempt, max_tokens=max_tokens
        )
        return response

    @abstractmethod
    async def _complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
        json_mode: bool,
    ) -> LLMResponse:
        """Effectue l'appel réseau, sans mesure ni contrôle.

        Args:
            messages: Messages de la requête.
            max_tokens: Plafond de sortie.
            temperature: Température d'échantillonnage.
            system: Prompt système optionnel.
            json_mode: Mode JSON natif du fournisseur.

        Returns:
            LLMResponse renseignée, ``latency_ms`` exclu.
        """


def _enforce_finish_reason(
    response: LLMResponse,
    *,
    node: str,
    attempt: int,
    max_tokens: int,
) -> None:
    """Refuse toute réponse qui ne s'est pas terminée d'elle-même.

    Args:
        response: Réponse à contrôler.
        node: Nœud appelant.
        attempt: Numéro de tentative.
        max_tokens: Plafond demandé.

    Raises:
        LLMOutputTruncated: ``finish_reason`` vaut ``length``.
        LLMResourceExhausted: ``finish_reason`` vaut
            ``insufficient_system_resource``.
        LLMOutputIncomplete: tout autre motif, ``unknown`` compris — une
            réponse dont le fournisseur ne dit pas comment elle s'est terminée
            n'est pas une réponse complète.
    """
    if response.finish_reason == FINISH_STOP:
        return

    fields: dict[str, Any] = {
        "node": node,
        "provider": response.provider,
        "model": response.model,
        "finish_reason": response.finish_reason,
        "max_tokens": max_tokens,
        "output_tokens": response.output_tokens,
        "attempt": attempt,
    }

    if response.finish_reason == FINISH_LENGTH:
        logger.error("llm_output_truncated", **fields)
        raise LLMOutputTruncated("sortie tronquée par le plafond", **fields)
    if response.finish_reason == FINISH_RESOURCE:
        logger.warning("llm_resource_exhausted", **fields)
        raise LLMResourceExhausted("ressources fournisseur indisponibles", **fields)

    logger.error("llm_output_incomplete", **fields)
    raise LLMOutputIncomplete("fin de génération inattendue", **fields)


class AnthropicClient(LLMClient):
    """Anthropic Claude client."""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def _complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
        json_mode: bool,
    ) -> LLMResponse:
        # ``json_mode`` is accepted and deliberately NOT forwarded here.
        #
        # Anthropic has no free-form "any valid JSON" mode: structured output
        # goes through ``output_config={"format": {"type": "json_schema",
        # "schema": ...}}``, which needs a per-agent JSON Schema. SPORE has
        # none — the agents describe their shape in prose inside the prompt.
        # Writing five schemas to serve a fallback path that fires only when
        # DeepSeek is down is not the trade to make today; the shared parser
        # (llm/json_parse.py) is the net for this path, which is exactly why
        # it stays necessary even with DeepSeek's JSON mode on.
        #
        # The parameter is available on the installed SDK (anthropic 0.97.0
        # exposes ``output_config`` and ``messages.parse``), so the day those
        # schemas exist this is a small change, not a migration.
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            # Sonnet 5 runs adaptive thinking by default when ``thinking`` is
            # omitted, which emits thinking blocks (billed, and pushed ahead of
            # the text block).
            # This client is a fallback for the non-thinking DeepSeek primary,
            # so disable thinking to keep behaviour and cost equivalent.
            "thinking": {"type": "disabled"},
        }

        if system:
            kwargs["system"] = system

        # ``temperature`` is intentionally NOT forwarded: Sonnet 5 rejects any
        # non-default sampling parameter with a 400. The signature
        # keeps the arg for interface parity with the DeepSeek client, but
        # steering the fallback happens through the prompt, not temperature.

        response = await self.client.messages.create(**kwargs)

        # Extract the first text block. With thinking disabled the response is
        # text-first, but iterate defensively so a leading non-text block
        # (e.g. if thinking is ever re-enabled) does not break extraction.
        content = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            "",
        )

        return LLMResponse(
            content=content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            # Modèle servi, pas le modèle demandé : l'API le renvoie, on le garde.
            model=getattr(response, "model", "") or self.model,
            provider=self.provider,
            cache_hit=False,
            finish_reason=normalize_anthropic_stop_reason(
                getattr(response, "stop_reason", None)
            ),
            requested_model=self.model,
            # Anthropic n'expose pas d'empreinte de configuration.
            system_fingerprint=None,
        )


class DeepSeekClient(LLMClient):
    """DeepSeek client (OpenAI-compatible API)."""

    provider = "deepseek"

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self.model = model

    async def _complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
        json_mode: bool,
    ) -> LLMResponse:
        # DeepSeek uses OpenAI format - system is a message
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        # S3/C17b — JSON mode natif. Vérifié en direct sur deepseek-v4-flash :
        # ``response_format={"type": "json_object"}`` est compatible avec le
        # ``thinking: disabled`` ci-dessous (6/6 sorties parsables), alors que
        # le mode JSON SEUL, thinking actif, a rendu un ``content`` vide — le
        # cas que la doc DeepSeek signale. Les deux vont donc ensemble.
        #
        # Pas de mode par schéma : ``{"type": "json_schema"}`` renvoie
        # 400 « This response_format type is unavailable now ». Le mode
        # contraint la SYNTAXE, pas la forme — d'où le parseur partagé qui
        # reste derrière pour le résidu, et la validation de forme qui reste
        # chez chaque agent.
        #
        # Le mode exige le mot « json » dans le prompt ; c'est pourquoi il est
        # opt-in par appel et non activé globalement.
        extra: dict[str, Any] = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **extra,
            # Les modèles V4 raisonnent PAR DÉFAUT quand ``thinking`` est omis :
            # la réponse arrive alors avec ``reasoning_content`` rempli et
            # ``content`` potentiellement vide si max_tokens est atteint pendant
            # le raisonnement — ce qui casserait le parsing JSON de tous les
            # agents. L'alias historique ``deepseek-chat`` routait vers
            # v4-flash NON-thinking : on désactive explicitement pour garder ce
            # comportement. ``thinking`` n'est pas un paramètre OpenAI, il doit
            # passer par ``extra_body``.
            extra_body={"thinking": {"type": "disabled"}},
        )

        # Check for cache hit (DeepSeek reports this in usage)
        cache_hit = False
        prompt_tokens = response.usage.prompt_tokens
        if hasattr(response.usage, "prompt_cache_hit_tokens"):
            cache_hit = response.usage.prompt_cache_hit_tokens > 0

        choice = response.choices[0]

        return LLMResponse(
            content=choice.message.content or "",
            input_tokens=prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            # Modèle servi, pas le modèle demandé : un changement de routage
            # côté DeepSeek (l'alias ``deepseek-chat`` en a déjà connu un)
            # n'est visible que par cette valeur.
            model=getattr(response, "model", "") or self.model,
            provider=self.provider,
            cache_hit=cache_hit,
            # Absent chez certains proxys : traité comme ``unknown``, donc
            # refusé, plutôt que supposé terminé.
            finish_reason=getattr(choice, "finish_reason", None) or "unknown",
            requested_model=self.model,
            system_fingerprint=getattr(response, "system_fingerprint", None),
        )


class FallbackClient(LLMClient):
    """Client with automatic fallback from primary to secondary provider."""

    provider = "fallback"

    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ):
        self.primary = primary
        self.fallback = fallback
        self.max_retries = max_retries
        self.base_delay = base_delay

    async def _complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
        json_mode: bool,
    ) -> LLMResponse:
        """Jamais appelé : ce client n'émet aucune requête lui-même.

        Args:
            messages: Messages de la requête.
            max_tokens: Plafond de sortie.
            temperature: Température d'échantillonnage.
            system: Prompt système optionnel.
            json_mode: Mode JSON natif du fournisseur.

        Raises:
            NotImplementedError: Toujours. ``complete`` est redéfini au-dessus
                et délègue aux clients enfants, qui mesurent et contrôlent
                l'appel qu'ils ont réellement passé.
        """
        raise NotImplementedError(
            "FallbackClient délègue à ses clients enfants ; _complete n'est pas utilisé"
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4000,
        temperature: float = 0.7,
        system: str | None = None,
        json_mode: bool = False,
        *,
        node: str,
        attempt: int = 1,
    ) -> LLMResponse:
        """Appelle le primaire, avec backoff, puis le secondaire.

        Ce client ne réimplémente ni la mesure ni le contrôle de fin de
        génération : il délègue à ``complete`` de l'enfant, qui écrit la ligne
        ``llm_calls`` de l'appel qu'il a réellement passé. Une seule ligne par
        appel réseau, donc, et le fournisseur y est celui qui a répondu.

        Args:
            messages: Messages de la requête.
            max_tokens: Plafond de sortie.
            temperature: Température d'échantillonnage.
            system: Prompt système optionnel.
            json_mode: Mode JSON natif du fournisseur.
            node: Nœud appelant, transmis tel quel.
            attempt: Numéro de tentative de l'appelant, transmis tel quel. Les
                réessais internes de ce client ne l'incrémentent pas : ils
                portent sur la même tentative logique.

        Returns:
            LLMResponse dont ``finish_reason`` vaut ``stop``.

        Raises:
            LLMOutputTruncated: Propagée sans réessai ni repli.
            LLMOutputIncomplete: Propagée sans réessai ni repli.
            Exception: L'erreur du secondaire si le primaire et lui échouent.
        """
        # Try primary with exponential backoff
        last_error = None
        for retry in range(self.max_retries):
            try:
                response = await self.primary.complete(
                    messages,
                    max_tokens,
                    temperature,
                    system,
                    json_mode,
                    node=node,
                    attempt=attempt,
                )
                return response
            except (LLMOutputTruncated, LLMOutputIncomplete):
                # Le prompt et le plafond sont identiques d'un essai à l'autre :
                # rejouer ne produirait qu'une troncature de plus, et basculer
                # sur le secondaire la produirait au tarif Sonnet. Le plafond
                # se corrige d'un cran plus haut (``complete_json``), le
                # contenu ne se corrige pas ici.
                raise
            except LLMResourceExhausted as e:
                # Transitoire côté fournisseur : c'est exactement le cas que
                # le backoff ci-dessous sert à absorber.
                last_error = e
                delay = self.base_delay * (2**retry)
                logger.warning(
                    "primary_provider_exhausted",
                    provider=self.primary.provider,
                    node=node,
                    attempt=retry + 1,
                    max_retries=self.max_retries,
                    retry_delay=delay,
                )
                if retry < self.max_retries - 1:
                    await asyncio.sleep(delay)
            except Exception as e:
                last_error = e
                delay = self.base_delay * (2**retry)
                logger.warning(
                    "primary_provider_failed",
                    provider=self.primary.provider,
                    node=node,
                    attempt=retry + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                    retry_delay=delay,
                )
                if retry < self.max_retries - 1:
                    await asyncio.sleep(delay)

        # Fallback to secondary provider
        logger.warning(
            "falling_back_to_secondary",
            primary=self.primary.provider,
            fallback=self.fallback.provider,
            node=node,
            last_error=str(last_error),
        )

        response = await self.fallback.complete(
            messages,
            max_tokens,
            temperature,
            system,
            json_mode,
            node=node,
            attempt=attempt,
        )

        # Mark that we used fallback
        response.provider = f"{self.fallback.provider}(fallback)"
        return response


def get_provider_for_agent(agent_name: str) -> tuple[str, str]:
    """Get the provider and model for an agent from genome config.

    Args:
        agent_name: Name of the agent (e.g., 'synthesis', 'gate')

    Returns:
        Tuple of (provider, model)
    """
    from config import get_genome

    genome = get_genome()
    agent_config = genome.agents.get(agent_name, {})

    provider = agent_config.get("provider", "deepseek")
    model = agent_config.get("model", "deepseek-v4-flash")

    return provider, model


def get_llm_client(
    agent_name: str,
    with_fallback: bool = True,
) -> LLMClient:
    """Get an LLM client for a specific agent.

    Uses the genome configuration to determine which provider and model to use.
    Optionally wraps with fallback support.

    Args:
        agent_name: Name of the agent (e.g., 'synthesis', 'gate')
        with_fallback: Whether to wrap with fallback client (default True)

    Returns:
        Configured LLMClient instance
    """
    from config import get_settings

    settings = get_settings()
    provider, model = get_provider_for_agent(agent_name)

    logger.debug(
        "creating_llm_client",
        agent=agent_name,
        provider=provider,
        model=model,
        with_fallback=with_fallback,
    )

    # Create primary client based on provider
    if provider == "deepseek":
        deepseek_key = getattr(settings, "deepseek_api_key", None)
        if not deepseek_key:
            raise ValueError(
                "DEEPSEEK_API_KEY not configured but deepseek provider requested"
            )
        primary = DeepSeekClient(api_key=deepseek_key, model=model)

        # Create Anthropic fallback
        if with_fallback and settings.anthropic_api_key:
            # Map DeepSeek model to equivalent Anthropic model
            fallback_model = _map_to_anthropic_model(model)
            fallback = AnthropicClient(
                api_key=settings.anthropic_api_key,
                model=fallback_model,
            )
            return FallbackClient(primary, fallback)

        return primary

    else:  # anthropic
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not configured but anthropic provider requested. "
                "Update your genome to use 'provider: deepseek' instead."
            )
        return AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=model,
        )


def _map_to_anthropic_model(model: str) -> str:
    """Map a non-Anthropic model to equivalent Anthropic model for fallback.

    Targets the current Sonnet tier (``claude-sonnet-5``): near-Opus quality
    for the scoring/generation workloads SPORE runs, at Sonnet pricing. The
    previous target ``claude-sonnet-4-20250514`` (Sonnet 4) retired on
    2026-06-15 and now 404s — the fallback would have failed exactly when
    DeepSeek was down. ``AnthropicClient.complete`` handles Sonnet 5's API
    differences (no non-default sampling params, thinking disabled) so the
    fallback behaves like the DeepSeek primary it backs up.
    """
    # All non-Anthropic providers map to the current Sonnet tier.
    return "claude-sonnet-5"
