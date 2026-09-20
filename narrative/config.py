"""Configuration de la couche narrative (``config/narrative/narrative.yaml``).

Fichier distinct du genome L0. Chargé une fois par processus ; les tests
posent une configuration de remplacement avec ``override_config``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from narrative.safety import REPO_ROOT

#: Fichier de configuration par défaut.
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "narrative" / "narrative.yaml"

#: Variable qui désigne un autre fichier de configuration.
CONFIG_ENV = "SPORE_NARRATIVE_CONFIG"

#: Variable qui remplace l'étiquette de coût par défaut.
RUN_LABEL_ENV = "SPORE_V2_RUN_LABEL"

#: Règles du nœud ``explainer_flags`` (copies octet pour octet de
#: ``spore-v2/scripts/v2/checks/``), section ``explainer_flags`` du YAML.
DEFAULT_FLAG_RULES: dict[str, Path] = {
    "vocab_rules": REPO_ROOT / "config" / "narrative" / "vocab_rules.json",
    "status_rules": REPO_ROOT / "config" / "narrative" / "status_rules.json",
    "vocab_allow": REPO_ROOT / "config" / "narrative" / "vocab_allow.txt",
}


class NarrativeConfigError(ValueError):
    """Configuration de la couche narrative absente ou invalide."""


@dataclass(frozen=True)
class LLMStepConfig:
    """Réglages d'une étape LLM (rédaction, garde, traduction).

    Attributes:
        agent: Nom passé à ``get_llm_client``.
        provider: Fournisseur explicite, ou ``None`` pour le chemin standard.
        model: Modèle explicite, ou ``None`` pour le chemin standard.
        prompt: Nom du fichier de prompt dans ``narrative/prompts`` (sans
            extension) ; sert aussi de version de prompt.
        temperature: Température d'échantillonnage.
        max_tokens: Plafond de sortie explicite (jamais ``max_tokens_for``).
    """

    agent: str
    provider: str | None
    model: str | None
    prompt: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class GuardConfig:
    """Réglages du garde (mécanique et juge).

    Attributes:
        llm: Étape LLM du juge.
        score_min: Note minimale de l'échelle.
        score_max: Note maximale de l'échelle.
        threshold: Seuil par défaut (note strictement inférieure = rejet).
        thresholds: Seuils par critère, prioritaires sur le défaut.
        skip_judge_on_mechanical_failure: Ne pas appeler le juge quand les
            contrôles mécaniques ont déjà rejeté.
        strict_first_person_plural: Rejeter toute première personne du
            pluriel (« nous », « we »…), dialogues compris.
        require_controls: Exiger le bloc ``controles`` du juge (prompts
            ``story_guard_v4`` et suivants) et en déduire le rejet en Python.
            Faux pour les prompts antérieurs, qui n'en produisent pas.
    """

    llm: LLMStepConfig
    score_min: int
    score_max: int
    threshold: int
    thresholds: Mapping[str, int]
    skip_judge_on_mechanical_failure: bool
    strict_first_person_plural: bool = True
    require_controls: bool = False

    def threshold_for(self, criterion: str) -> int:
        """Seuil d'un critère.

        Args:
            criterion: Nom du critère.

        Returns:
            Seuil propre au critère, sinon seuil par défaut.
        """
        return int(self.thresholds.get(criterion, self.threshold))


@dataclass(frozen=True)
class NarrativeConfig:
    """Configuration complète de la couche.

    Attributes:
        version: Version du fichier.
        run_label: Étiquette de coût par défaut.
        layer_timeout_s: Délai global du sous-graphe.
        llm_call_timeout_s: Délai d'un appel LLM.
        tail_timeout_s: Délai des étapes mécaniques de secours.
        retry_max_attempts: Essais d'un appel externe.
        retry_base_delay_s: Premier délai de backoff.
        retry_max_delay_s: Délai de backoff maximal.
        recursion_limit: Limite d'étapes du sous-graphe.
        max_attempts: Tentatives de récit FR par brief.
        translate_max_attempts: Tentatives de récit EN par brief.
        year_offset_min: Décalage minimal de l'année du récit.
        year_offset_max: Décalage maximal de l'année du récit.
        words: Bornes de longueur par langue.
        title_max_chars: Longueur maximale du titre.
        writer: Étape de rédaction.
        guard: Garde.
        translate: Étape de traduction.
        themes_path: Table de correspondance domaine → thème.
        neighbours_target: Voisines sortantes visées.
        neighbours_min: Voisines sortantes minimales.
        neighbours_max: Voisines sortantes maximales.
        neighbours_min_inbound: Liens entrants minimaux.
        stub_ring: Liens de l'anneau des stubs.
        identity_denylist_path: Denylist d'identité (non versionnée).
        constitution_path: Constitution (lecture seule).
        domain_map_paths: Cartes des domaines (``parent_domain``).
        backfill_rate_limit_s: Pause entre deux briefs du backfill.
        backfill_default_cap_usd: Plafond de dépense du run.
        backfill_estimated_usd_per_brief: Estimation prudente par brief.
        backfill_capped_labels: Étiquettes comptées dans le plafond.
        backfill_spend_json: Consolidation de la dépense du run.
        vocab_rules_path: Motifs de vocabulaire proscrit (copie de
            ``spore-v2/scripts/v2/checks/vocab_rules.json``).
        status_rules_path: Motifs d'erreur de statut (copie de
            ``status_rules.json``).
        vocab_allow_path: Littéraux admis (copie de ``vocab_allow.txt``).
    """

    version: str
    run_label: str
    layer_timeout_s: float
    llm_call_timeout_s: float
    tail_timeout_s: float
    retry_max_attempts: int
    retry_base_delay_s: float
    retry_max_delay_s: float
    recursion_limit: int
    max_attempts: int
    translate_max_attempts: int
    year_offset_min: int
    year_offset_max: int
    words: Mapping[str, tuple[int, int]]
    title_max_chars: int
    writer: LLMStepConfig
    guard: GuardConfig
    translate: LLMStepConfig
    themes_path: Path
    neighbours_target: int
    neighbours_min: int
    neighbours_max: int
    neighbours_min_inbound: int
    stub_ring: int
    identity_denylist_path: Path
    constitution_path: Path
    domain_map_paths: tuple[Path, ...]
    backfill_rate_limit_s: float
    backfill_default_cap_usd: float
    backfill_estimated_usd_per_brief: float
    backfill_capped_labels: tuple[str, ...]
    backfill_spend_json: Path
    extra: Mapping[str, Any] = field(default_factory=dict)
    vocab_rules_path: Path = DEFAULT_FLAG_RULES["vocab_rules"]
    status_rules_path: Path = DEFAULT_FLAG_RULES["status_rules"]
    vocab_allow_path: Path = DEFAULT_FLAG_RULES["vocab_allow"]

    def effective_run_label(self) -> str:
        """Étiquette de coût, variable d'environnement prioritaire.

        Returns:
            ``SPORE_V2_RUN_LABEL`` s'il est posé, sinon ``run_label``.
        """
        return os.environ.get(RUN_LABEL_ENV) or self.run_label

    def with_changes(self, **changes: Any) -> NarrativeConfig:
        """Copie modifiée (tests, scripts).

        Args:
            **changes: Champs à remplacer.

        Returns:
            Nouvelle configuration.
        """
        return replace(self, **changes)


def _anchor(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def _step(raw: Mapping[str, Any], name: str) -> LLMStepConfig:
    section = raw.get(name)
    if not isinstance(section, Mapping):
        raise NarrativeConfigError(f"section {name!r} absente")
    try:
        return LLMStepConfig(
            agent=str(section["agent"]),
            provider=section.get("provider") or None,
            model=section.get("model") or None,
            prompt=str(section["prompt"]),
            temperature=float(section["temperature"]),
            max_tokens=int(section["max_tokens"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NarrativeConfigError(f"section {name!r} invalide : {exc}") from exc


def parse_config(raw: Mapping[str, Any]) -> NarrativeConfig:
    """Construit la configuration à partir du YAML décodé.

    Args:
        raw: Contenu du fichier.

    Returns:
        Configuration typée.

    Raises:
        NarrativeConfigError: Champ manquant ou invalide.
    """
    try:
        timeouts = raw["timeouts"]
        retry = raw["retry"]
        story = raw["story"]
        guard_raw = raw["guard"]
        neighbours = raw["neighbours"]
        paths = raw["paths"]
        backfill = raw["backfill"]
        flag_rules = raw.get("explainer_flags") or {}
        if not isinstance(flag_rules, Mapping):
            raise NarrativeConfigError("section 'explainer_flags' invalide")
        words = {
            lang: (int(bounds[0]), int(bounds[1])) for lang, bounds in story["words"].items()
        }
        guard = GuardConfig(
            llm=_step(raw, "guard"),
            score_min=int(guard_raw["score_min"]),
            score_max=int(guard_raw["score_max"]),
            threshold=int(guard_raw["threshold"]),
            thresholds={str(k): int(v) for k, v in (guard_raw.get("thresholds") or {}).items()},
            skip_judge_on_mechanical_failure=bool(
                guard_raw.get("skip_judge_on_mechanical_failure", True)
            ),
            strict_first_person_plural=bool(guard_raw.get("strict_first_person_plural", True)),
            require_controls=bool(guard_raw.get("require_controls", False)),
        )
        config = NarrativeConfig(
            version=str(raw["version"]),
            run_label=str(raw["run_label"]),
            layer_timeout_s=float(timeouts["layer_total_s"]),
            llm_call_timeout_s=float(timeouts["llm_call_s"]),
            tail_timeout_s=float(timeouts["mechanical_tail_s"]),
            retry_max_attempts=int(retry["max_attempts"]),
            retry_base_delay_s=float(retry["base_delay_s"]),
            retry_max_delay_s=float(retry["max_delay_s"]),
            recursion_limit=int(raw.get("recursion_limit", 50)),
            max_attempts=int(story["max_attempts"]),
            translate_max_attempts=int(story["translate_max_attempts"]),
            year_offset_min=int(story["year_offset_min"]),
            year_offset_max=int(story["year_offset_max"]),
            words=words,
            title_max_chars=int(story["title_max_chars"]),
            writer=_step(raw, "writer"),
            guard=guard,
            translate=_step(raw, "translate"),
            themes_path=_anchor(raw["themes"]["path"]),
            neighbours_target=int(neighbours["target"]),
            neighbours_min=int(neighbours["min"]),
            neighbours_max=int(neighbours["max"]),
            neighbours_min_inbound=int(neighbours["min_inbound"]),
            stub_ring=int(neighbours["stub_ring"]),
            identity_denylist_path=_anchor(paths["identity_denylist"]),
            constitution_path=_anchor(paths["constitution"]),
            domain_map_paths=tuple(_anchor(item) for item in paths["domain_maps"]),
            backfill_rate_limit_s=float(backfill["rate_limit_s"]),
            backfill_default_cap_usd=float(backfill["default_cap_usd"]),
            backfill_estimated_usd_per_brief=float(backfill["estimated_usd_per_brief"]),
            backfill_capped_labels=tuple(str(x) for x in backfill["capped_labels"]),
            backfill_spend_json=Path(backfill["spend_json"]),
            vocab_rules_path=_anchor(flag_rules.get("vocab_rules") or DEFAULT_FLAG_RULES["vocab_rules"]),
            status_rules_path=_anchor(flag_rules.get("status_rules") or DEFAULT_FLAG_RULES["status_rules"]),
            vocab_allow_path=_anchor(flag_rules.get("vocab_allow") or DEFAULT_FLAG_RULES["vocab_allow"]),
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, NarrativeConfigError):
            raise
        raise NarrativeConfigError(f"configuration narrative invalide : {exc}") from exc

    if not 1 <= config.max_attempts <= 3 or not 1 <= config.translate_max_attempts <= 3:
        # Le contrat de données borne ``attempt`` à 1, 2 ou 3.
        raise NarrativeConfigError("max_attempts doit être compris entre 1 et 3")
    if not config.neighbours_min <= config.neighbours_target <= config.neighbours_max:
        raise NarrativeConfigError("bornes de voisinage incohérentes")
    if config.year_offset_min > config.year_offset_max:
        raise NarrativeConfigError("fenêtre d'année incohérente")
    return config


def load_config(path: str | os.PathLike[str] | None = None) -> NarrativeConfig:
    """Lit et valide un fichier de configuration.

    Args:
        path: Fichier à lire ; par défaut ``SPORE_NARRATIVE_CONFIG`` ou
            ``config/narrative/narrative.yaml``.

    Returns:
        Configuration typée.

    Raises:
        NarrativeConfigError: Fichier absent ou invalide.
    """
    target = Path(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    if not target.is_file():
        raise NarrativeConfigError(f"configuration narrative introuvable : {target}")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise NarrativeConfigError(f"configuration narrative illisible : {target}")
    return parse_config(raw)


_cached: NarrativeConfig | None = None
_override: NarrativeConfig | None = None


def get_config() -> NarrativeConfig:
    """Configuration courante (remplacement de test prioritaire, puis cache).

    Returns:
        Configuration.
    """
    global _cached
    if _override is not None:
        return _override
    if _cached is None:
        _cached = load_config()
    return _cached


@contextmanager
def override_config(config: NarrativeConfig) -> Iterator[NarrativeConfig]:
    """Pose une configuration de remplacement le temps d'un bloc (tests).

    Args:
        config: Configuration à utiliser.

    Yields:
        La configuration posée.
    """
    global _override
    previous = _override
    _override = config
    try:
        yield config
    finally:
        _override = previous
