"""``story_writer`` et ``story_translate_en`` : production du texte des récits.

Les deux étapes rendent un objet de même forme (``title``, ``year``,
``place``, ``body_markdown``, ``mechanism``, ``limit_or_risk``), contrôlé
ensuite par ``narrative.guard``. Elles ne décident de rien : une sortie
incomplète passe au garde, qui la rejette.

La traduction reprend le mécanisme existant de ``translation_hook``
(``scripts/translate_brief_*.py``) : même chemin client
(``get_llm_client("translation")``, absent du genome, donc
``deepseek-v4-flash`` avec repli Anthropic), même température (0,2), même
registre britannique ; le prompt est propre au récit et l'appel passe par le
parseur JSON partagé, puisque le récit se traduit en un objet et non champ par
champ.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from narrative.checks import coerce_year, year_window
from narrative.config import NarrativeConfig
from narrative.inputs import StoryInputs, bullets
from narrative.llm import CostMeter, call_json
from narrative.prompting import render_prompt

#: Champs d'un récit, dans l'ordre du contrat.
STORY_FIELDS: tuple[str, ...] = (
    "title",
    "year",
    "place",
    "body_markdown",
    "mechanism",
    "limit_or_risk",
)


@dataclass
class StoryText:
    """Texte d'un récit produit par le rédacteur ou le traducteur.

    Attributes:
        story: Champs normalisés (valeurs brutes conservées si mal typées,
            pour que le garde les voie et les rejette).
        model: Modèle réellement appelé.
        prompt_version: Version du prompt utilisé.
        meter: Coût et jetons de l'étape.
    """

    story: dict[str, Any]
    model: str
    prompt_version: str
    meter: CostMeter


def normalise_story(data: Mapping[str, Any]) -> dict[str, Any]:
    """Ramène la sortie du LLM aux six champs du récit.

    Les chaînes sont débarrassées de leurs blancs de bord ; l'année devient un
    entier quand c'est possible. Un champ absent reste absent : le garde le
    signalera.

    Args:
        data: Objet JSON rendu par le LLM.

    Returns:
        Récit normalisé.
    """
    story: dict[str, Any] = {}
    for name in STORY_FIELDS:
        if name not in data:
            continue
        value = data[name]
        if name == "year":
            year = coerce_year(value)
            story[name] = year if year is not None else value
        elif isinstance(value, str):
            story[name] = value.strip()
        else:
            story[name] = value
    return story


def body_sha256(body: Any) -> str | None:
    """Empreinte SHA-256 du corps (référencée par le jury).

    Args:
        body: Corps Markdown.

    Returns:
        Empreinte hexadécimale, ou ``None`` sans corps textuel.
    """
    if not isinstance(body, str):
        return None
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def writer_prompt(inputs: StoryInputs, config: NarrativeConfig) -> str:
    """Prompt du rédacteur pour un brief.

    Args:
        inputs: Entrées du brief.
        config: Configuration.

    Returns:
        Prompt complet.
    """
    year_min, year_max = year_window(config.year_offset_min, config.year_offset_max)
    min_words, max_words = config.words["fr"]
    return render_prompt(
        config.writer.prompt,
        year_min=year_min,
        year_max=year_max,
        current_year=datetime.now(UTC).year,
        min_words=min_words,
        max_words=max_words,
        title=inputs.title or "(sans titre)",
        formal_statement=inputs.formal_statement or "(absent)",
        domains=", ".join(inputs.domains) or "(non renseignés)",
        causal_chain=bullets(inputs.causal_chain),
        key_assumptions=bullets(inputs.key_assumptions),
        predictions=bullets(inputs.predictions),
        vulgarisation=inputs.vulgarisation or "(absente)",
        limits=bullets(inputs.limits),
        counter_evidence=bullets(inputs.counter_evidence, empty="(aucune contre-preuve)"),
    )


async def write_story(
    inputs: StoryInputs,
    *,
    config: NarrativeConfig,
    db_path: str | Path,
    run_label: str,
    meter: CostMeter | None = None,
) -> StoryText:
    """Rédige le récit FR d'un brief.

    Args:
        inputs: Entrées du brief.
        config: Configuration.
        db_path: Base du registre de coût.
        run_label: Étiquette de coût.
        meter: Compteur de l'appelant (mis à jour même en cas d'échec).

    Returns:
        Récit normalisé, modèle et coût.

    Raises:
        Exception: Appel ou parsing en échec (l'appelant rejette la tentative).
    """
    result = await call_json(
        step=config.writer,
        node="story_writer",
        prompt=writer_prompt(inputs, config),
        config=config,
        db_path=db_path,
        run_label=run_label,
        brief_id=inputs.brief_id,
        meter=meter,
    )
    return StoryText(
        story=normalise_story(result.data),
        model=result.model,
        prompt_version=config.writer.prompt,
        meter=result.meter,
    )


def translate_prompt(source: Mapping[str, Any], config: NarrativeConfig) -> str:
    """Prompt de traduction d'un récit FR publié.

    Args:
        source: Ligne ``v2_stories`` FR (``title``, ``story_place``,
            ``body_md``, ``mechanism``, ``limit_staged``).
        config: Configuration.

    Returns:
        Prompt complet.
    """
    return render_prompt(
        config.translate.prompt,
        title=str(source.get("title") or ""),
        place=str(source.get("story_place") or ""),
        mechanism=str(source.get("mechanism") or ""),
        limit_or_risk=str(source.get("limit_staged") or ""),
        body_markdown=str(source.get("body_md") or ""),
    )


async def translate_story(
    source: Mapping[str, Any],
    *,
    brief_id: str,
    config: NarrativeConfig,
    db_path: str | Path,
    run_label: str,
    meter: CostMeter | None = None,
) -> StoryText:
    """Traduit un récit FR publié en anglais britannique.

    L'année n'est pas retraduite : elle est reprise du récit source.

    Args:
        source: Ligne ``v2_stories`` FR publiée.
        brief_id: Brief concerné.
        config: Configuration.
        db_path: Base du registre de coût.
        run_label: Étiquette de coût.
        meter: Compteur de l'appelant (mis à jour même en cas d'échec).

    Returns:
        Récit anglais normalisé, modèle et coût.

    Raises:
        Exception: Appel ou parsing en échec (l'appelant rejette la tentative).
    """
    result = await call_json(
        step=config.translate,
        node="story_translate",
        prompt=translate_prompt(source, config),
        config=config,
        db_path=db_path,
        run_label=run_label,
        brief_id=brief_id,
        meter=meter,
    )
    story = normalise_story(result.data)
    story["year"] = source.get("story_year")
    return StoryText(
        story=story,
        model=result.model,
        prompt_version=config.translate.prompt,
        meter=result.meter,
    )
