"""Configuration management for SPORE.

Loads environment variables, genome YAML, and constitution.
"""

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


# Project root directory (where config.py lives) - always resolve to canonical path
PROJECT_ROOT = Path(__file__).parent.resolve()


class SporeSettings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Keys
    anthropic_api_key: Optional[str] = Field(None, alias="ANTHROPIC_API_KEY")
    deepseek_api_key: str = Field(..., alias="DEEPSEEK_API_KEY")
    semantic_scholar_api_key: Optional[str] = Field(None, alias="SEMANTIC_SCHOLAR_API_KEY")

    # Paths - use absolute paths based on project root
    db_path: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "spore.db", alias="SPORE_DB_PATH")
    output_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "outputs", alias="SPORE_OUTPUT_DIR")
    genome_path: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "l0_genome.yaml", alias="SPORE_GENOME_PATH")
    constitution_path: Path = Field(default_factory=lambda: PROJECT_ROOT / "data" / "constitution.yaml", alias="SPORE_CONSTITUTION_PATH")

    # Runtime settings
    log_level: str = Field("INFO", alias="SPORE_LOG_LEVEL")
    max_budget_usd: float = Field(20.0, alias="SPORE_MAX_BUDGET_USD")

    # Email settings for autopilot digest
    smtp_host: Optional[str] = Field(None, alias="SPORE_SMTP_HOST")
    smtp_port: int = Field(587, alias="SPORE_SMTP_PORT")
    smtp_user: Optional[str] = Field(None, alias="SPORE_SMTP_USER")
    smtp_password: Optional[str] = Field(None, alias="SPORE_SMTP_PASSWORD")
    digest_recipients: Optional[str] = Field(None, alias="SPORE_DIGEST_RECIPIENTS")

    @field_validator("db_path", "output_dir", "genome_path", "constitution_path", mode="before")
    @classmethod
    def _anchor_relative_paths(cls, v: Any) -> Any:
        """Resolve relative paths against PROJECT_ROOT so the app is cwd-independent.

        Without this, a value like ``./data/spore.db`` loaded from .env resolves
        against the current working directory — which differs when running
        ``streamlit run review/app.py`` vs. the CLI. Anchoring to PROJECT_ROOT
        makes every component (CLI, autopilot, Streamlit, scripts) see the
        same canonical files.
        """
        if v is None:
            return v
        p = Path(v)
        if not p.is_absolute():
            p = (PROJECT_ROOT / p).resolve()
        return p

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        """Customize settings sources to find .env in project root."""
        from pydantic_settings.sources import DotEnvSettingsSource

        # Find project root (where .env is)
        project_root = Path(__file__).parent
        env_file = project_root / ".env"

        return (
            init_settings,
            env_settings,
            DotEnvSettingsSource(settings_cls, env_file=env_file),
            file_secret_settings,
        )

    model_config = {
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


class Genome:
    """L0 Genome configuration - loaded from YAML."""

    def __init__(self, path: Path | str):
        self.path = Path(path) if isinstance(path, str) else path
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load genome from YAML file."""
        if self.path.exists():
            with open(self.path) as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = self._default_genome()

    def _default_genome(self) -> dict[str, Any]:
        """Default genome configuration."""
        return {
            "genome_version": "l0_v1",
            "last_mutated": None,
            "mutated_by": None,
            "agents": {
                "explorer": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "parameters": {"collisions_per_cycle": 50},
                },
                "synthesis": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "parameters": {
                        "no_bridge_allowed": True,
                        "max_tokens": 4000,
                    },
                },
                "critic_devil": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                },
                "critic_angel": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                },
                "curator": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "parameters": {"top_percent": 0.10},  # Top 10% for PoC
                },
                "impact": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "parameters": {"max_tokens": 2000},
                },
                "reviewer": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "parameters": {"max_tokens": 1000},
                },
            },
            "randomness": {
                "strategy_weights": {
                    "semantic_distance": 1.0,  # PoC: semantic distance only
                    "structural_analogy": 0.0,
                    "anomaly_guided": 0.0,
                    "historical_template": 0.0,
                },
                "distance_min": 0.40,
                "distance_max": 0.70,
                "chaos_floor": 0.15,
                "temperature": 0.7,
            },
            "sources": ["semantic_scholar", "arxiv"],
            "schedule": {
                "frequency": "manual",  # PoC: manual runs
            },
            "score_weights": {
                "novelty": 0.20,
                "coherence": 0.25,
                "testability": 0.25,
                "impact_potential": 0.15,
                "hallucination_risk": -0.15,
            },
        }

    @property
    def version(self) -> str:
        return self._data.get("genome_version", "l0_v1")

    @property
    def agents(self) -> dict[str, Any]:
        return self._data.get("agents", {})

    @property
    def randomness(self) -> dict[str, Any]:
        return self._data.get("randomness", {})

    @property
    def sources(self) -> list[str]:
        return self._data.get("sources", ["semantic_scholar"])

    @property
    def score_weights(self) -> dict[str, float]:
        return self._data.get("score_weights", {})

    def get_agent_model(self, agent_name: str) -> str:
        """Get the model for a specific agent."""
        agent = self.agents.get(agent_name, {})
        return agent.get("model", "deepseek-chat")

    def get_agent_provider(self, agent_name: str) -> str:
        """Get the provider for a specific agent."""
        agent = self.agents.get(agent_name, {})
        return agent.get("provider", "deepseek")

    def get_agent_params(self, agent_name: str) -> dict[str, Any]:
        """Get parameters for a specific agent."""
        agent = self.agents.get(agent_name, {})
        return agent.get("parameters", {})

    def to_dict(self) -> dict[str, Any]:
        """Export genome as dictionary."""
        return self._data.copy()


class Constitution:
    """Immutable constitution - safety limits and ethical rules."""

    def __init__(self, path: Path | str):
        self.path = Path(path) if isinstance(path, str) else path
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load constitution from YAML file."""
        if self.path.exists():
            with open(self.path) as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = self._default_constitution()

    def _default_constitution(self) -> dict[str, Any]:
        """Default constitution."""
        return {
            "ethics": {
                "excluded_domains": [
                    "weapons_development",
                    "surveillance_technology",
                ],
                "transparency": "all hypotheses include full source tracing",
                "attribution": "SPORE is a hypothesis generator, not an author",
            },
            "safety": {
                "chaos_floor": 0.10,
                "max_budget_per_day": 50,
                "rollback_threshold": 0.15,
                "human_approval_required_for": [
                    "scope_change",
                    "new_data_source",
                    "agent_architecture_change",
                    "constitution_modification",
                ],
            },
            "mutation_policy": {
                "min_cycles_between_mutations_same_path": 3,
                "max_mutations_per_cycle": 2,
                "oscillation_detection": {
                    "enabled": True,
                    "window_cycles": 5,
                    "max_reversals_per_path": 1,
                },
            },
            "philosophy": {
                "purpose": "Generate testable, novel, interdisciplinary hypotheses",
                "stance": "SPORE proposes, humans dispose",
                "humility": "Every hypothesis must declare its own uncertainty and gaps",
            },
        }

    @property
    def excluded_domains(self) -> list[str]:
        return self._data.get("ethics", {}).get("excluded_domains", [])

    @property
    def chaos_floor(self) -> float:
        return self._data.get("safety", {}).get("chaos_floor", 0.10)

    @property
    def max_budget_per_day(self) -> float:
        return self._data.get("safety", {}).get("max_budget_per_day", 50.0)

    def to_dict(self) -> dict[str, Any]:
        """Export constitution as dictionary."""
        return self._data.copy()


# Global instances (lazy-loaded)
_settings: Optional[SporeSettings] = None
_genome: Optional[Genome] = None
_constitution: Optional[Constitution] = None


def get_settings() -> SporeSettings:
    """Get application settings (singleton)."""
    global _settings
    if _settings is None:
        _settings = SporeSettings()
    return _settings


def get_genome() -> Genome:
    """Get genome configuration (singleton)."""
    global _genome
    if _genome is None:
        settings = get_settings()
        _genome = Genome(settings.genome_path)
    return _genome


def get_constitution() -> Constitution:
    """Get constitution (singleton)."""
    global _constitution
    if _constitution is None:
        settings = get_settings()
        _constitution = Constitution(settings.constitution_path)
    return _constitution


def reset_config() -> None:
    """Reset all config singletons (for testing)."""
    global _settings, _genome, _constitution
    _settings = None
    _genome = None
    _constitution = None
