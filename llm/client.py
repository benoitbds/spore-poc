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
    ) -> LLMResponse:
        """Send a completion request to the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            system: Optional system prompt

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
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        if system:
            kwargs["system"] = system

        if temperature != 1.0:
            kwargs["temperature"] = temperature

        response = await self.client.messages.create(**kwargs)

        return LLMResponse(
            content=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=self.model,
            provider=self.provider,
            cache_hit=False,
        )


class DeepSeekClient(LLMClient):
    """DeepSeek client (OpenAI-compatible API)."""

    provider = "deepseek"

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
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
    ) -> LLMResponse:
        # DeepSeek uses OpenAI format - system is a message
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            max_tokens=max_tokens,
            temperature=temperature,
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
    ) -> LLMResponse:
        # Try primary with exponential backoff
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = await self.primary.complete(
                    messages, max_tokens, temperature, system
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
            messages, max_tokens, temperature, system
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
    model = agent_config.get("model", "deepseek-chat")

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
    """Map a non-Anthropic model to equivalent Anthropic model for fallback."""
    # DeepSeek models map to Sonnet for equivalent capability
    if "deepseek" in model.lower():
        return "claude-sonnet-4-20250514"

    # Default fallback
    return "claude-sonnet-4-20250514"
