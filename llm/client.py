"""Abstract LLM client with multi-provider support.

Supports:
- Anthropic (Claude models)
- DeepSeek (OpenAI-compatible API)

Usage:
    client = get_llm_client("synthesis")  # Gets client based on genome config
    response = await client.complete(messages, max_tokens=1000)
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from logging_config import get_logger

logger = get_logger("llm_client")


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    cache_hit: bool = False  # For DeepSeek cache tracking


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    provider: str = "unknown"

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4000,
        temperature: float = 0.7,
        system: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a completion request to the LLM.

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

        Returns:
            LLMResponse with content and usage info
        """
        pass


class AnthropicClient(LLMClient):
    """Anthropic Claude client."""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4000,
        temperature: float = 0.7,
        system: str | None = None,
        json_mode: bool = False,
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
            model=self.model,
            provider=self.provider,
            cache_hit=False,
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

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4000,
        temperature: float = 0.7,
        system: str | None = None,
        json_mode: bool = False,
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

        return LLMResponse(
            content=response.choices[0].message.content or "",
            input_tokens=prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            model=self.model,
            provider=self.provider,
            cache_hit=cache_hit,
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

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4000,
        temperature: float = 0.7,
        system: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        # Try primary with exponential backoff
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = await self.primary.complete(
                    messages, max_tokens, temperature, system, json_mode
                )
                return response
            except Exception as e:
                last_error = e
                delay = self.base_delay * (2**attempt)
                logger.warning(
                    "primary_provider_failed",
                    provider=self.primary.provider,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                    retry_delay=delay,
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(delay)

        # Fallback to secondary provider
        logger.warning(
            "falling_back_to_secondary",
            primary=self.primary.provider,
            fallback=self.fallback.provider,
            last_error=str(last_error),
        )

        response = await self.fallback.complete(
            messages, max_tokens, temperature, system, json_mode
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
