"""Structured logging configuration for SPORE."""

import sys
from typing import Any

import structlog
from structlog.typing import Processor

from config import get_settings


def setup_logging() -> None:
    """Configure structured logging for the application."""
    settings = get_settings()

    # Shared processors for all configurations
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # Development: pretty console output
    # Production: JSON output
    if settings.log_level == "DEBUG":
        processors: list[Processor] = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        processors = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]

    import logging

    # Map log level string to logging constant
    log_level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    log_level = log_level_map.get(settings.log_level.upper(), logging.INFO)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a logger instance."""
    return structlog.get_logger(name)


class TokenTracker:
    """Track token usage and costs across API calls."""

    # Pricing per 1M tokens (as of early 2026)
    # Anthropic pricing
    PRICING = {
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00, "provider": "anthropic"},
        # Current Anthropic fallback target (see llm/client._map_to_anthropic_model).
        # List price $3/$15 per 1M tok (intro $2/$10 through 2026-08-31).
        "claude-sonnet-5": {"input": 3.00, "output": 15.00, "provider": "anthropic"},
        # Retired 2026-06-15 — kept so historical run costs still resolve.
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00, "provider": "anthropic"},
        "claude-opus-4-5-20251101": {"input": 15.00, "output": 75.00, "provider": "anthropic"},
        # DeepSeek pricing (V3.2 - significantly cheaper)
        "deepseek-chat": {"input": 0.28, "output": 0.42, "input_cache": 0.028, "provider": "deepseek"},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19, "input_cache": 0.055, "provider": "deepseek"},
    }

    # Default pricing by provider for unknown models
    DEFAULT_PRICING = {
        "anthropic": {"input": 3.00, "output": 15.00},
        "deepseek": {"input": 0.28, "output": 0.42, "input_cache": 0.028},
    }

    def __init__(self) -> None:
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls: list[dict[str, Any]] = []
        self._logger = get_logger("token_tracker")

    def log_call(
        self,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        provider: str = "anthropic",
        cache_hit: bool = False,
    ) -> float:
        """Log an API call and return its cost in USD.

        Args:
            agent: Agent name (e.g., 'synthesis', 'gate')
            model: Model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            provider: Provider name ('anthropic', 'deepseek')
            cache_hit: Whether this was a cache hit (DeepSeek)

        Returns:
            Cost in USD
        """
        # Get pricing - try model-specific first, then provider default
        pricing = self.PRICING.get(model)
        if pricing is None:
            pricing = self.DEFAULT_PRICING.get(provider, {"input": 3.0, "output": 15.0})

        # Calculate cost with cache discount for DeepSeek
        if cache_hit and "input_cache" in pricing:
            input_cost = (input_tokens / 1_000_000) * pricing["input_cache"]
        else:
            input_cost = (input_tokens / 1_000_000) * pricing["input"]

        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        cost = input_cost + output_cost

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

        call_record = {
            "agent": agent,
            "model": model,
            "provider": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "cache_hit": cache_hit,
        }
        self.calls.append(call_record)

        self._logger.debug(
            "api_call",
            **call_record,
        )

        return cost

    @property
    def total_cost(self) -> float:
        """Calculate total cost of all calls."""
        return sum(call["cost_usd"] for call in self.calls)

    def summary(self) -> dict[str, Any]:
        """Get a summary of all token usage."""
        by_agent: dict[str, dict[str, Any]] = {}
        by_provider: dict[str, dict[str, Any]] = {}
        cache_hits = 0

        for call in self.calls:
            agent = call["agent"]
            provider = call.get("provider", "anthropic")

            # Track by agent
            if agent not in by_agent:
                by_agent[agent] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            by_agent[agent]["calls"] += 1
            by_agent[agent]["input_tokens"] += call["input_tokens"]
            by_agent[agent]["output_tokens"] += call["output_tokens"]
            by_agent[agent]["cost_usd"] += call["cost_usd"]

            # Track by provider
            if provider not in by_provider:
                by_provider[provider] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "cache_hits": 0,
                }
            by_provider[provider]["calls"] += 1
            by_provider[provider]["input_tokens"] += call["input_tokens"]
            by_provider[provider]["output_tokens"] += call["output_tokens"]
            by_provider[provider]["cost_usd"] += call["cost_usd"]
            if call.get("cache_hit"):
                by_provider[provider]["cache_hits"] += 1
                cache_hits += 1

        return {
            "total_calls": len(self.calls),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost,
            "total_cache_hits": cache_hits,
            "by_agent": by_agent,
            "by_provider": by_provider,
        }

    def print_summary(self) -> None:
        """Print a formatted summary to console."""
        summary = self.summary()

        print("\n" + "=" * 60)
        print("TOKEN USAGE SUMMARY")
        print("=" * 60)
        print(f"Total API calls: {summary['total_calls']}")
        print(f"Total input tokens: {summary['total_input_tokens']:,}")
        print(f"Total output tokens: {summary['total_output_tokens']:,}")
        print(f"Total cost: ${summary['total_cost_usd']:.4f}")
        if summary.get("total_cache_hits", 0) > 0:
            print(f"Cache hits: {summary['total_cache_hits']}")
        print("-" * 60)

        # Show by provider
        if summary.get("by_provider"):
            print("\nBY PROVIDER:")
            for provider, stats in summary["by_provider"].items():
                cache_str = f" ({stats['cache_hits']} cache hits)" if stats.get("cache_hits") else ""
                print(f"\n  {provider}:{cache_str}")
                print(f"    Calls: {stats['calls']}")
                print(f"    Tokens: {stats['input_tokens']:,} in / {stats['output_tokens']:,} out")
                print(f"    Cost: ${stats['cost_usd']:.4f}")

        # Show by agent
        print("\nBY AGENT:")
        for agent, stats in summary["by_agent"].items():
            print(f"\n  {agent}:")
            print(f"    Calls: {stats['calls']}")
            print(f"    Tokens: {stats['input_tokens']:,} in / {stats['output_tokens']:,} out")
            print(f"    Cost: ${stats['cost_usd']:.4f}")

        print("=" * 60 + "\n")


# Global token tracker instance
_tracker: TokenTracker | None = None


def get_token_tracker() -> TokenTracker:
    """Get the global token tracker."""
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker()
    return _tracker


def reset_token_tracker() -> None:
    """Reset the global token tracker (for new runs)."""
    global _tracker
    _tracker = TokenTracker()
