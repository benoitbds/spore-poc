"""Entrées du récit, lues dans la ligne ``briefs`` publiée.

Le récit s'appuie sur ce que le brief a déjà publié : hypothèse affûtée,
vulgarisation FR (absente pour trois briefs du corpus), prédictions, limites
et contre-preuves. Rien d'autre n'entre dans le prompt : ni référence
bibliographique (titres, auteurs, DOI des contre-preuves sont écartés), ni
donnée hors du brief.

Il n'existe pas de champ « limites » dans le schéma (recon pipeline §11.3) :
les limites sont reconstituées à partir des conditions de validité, des
inconnues du mécanisme et des désaccords de la méta-relecture.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Longueur maximale d'un élément de liste injecté dans un prompt.
ITEM_MAX_CHARS = 600

#: Nombre maximal d'éléments par liste injectée.
LIST_MAX_ITEMS = 6


def load_blob(raw: Any) -> Any:
    """Décode un blob JSON de la base, sans lever.

    Args:
        raw: Texte JSON, objet déjà décodé ou ``None``.

    Returns:
        L'objet décodé, ou ``None`` si illisible.
    """
    if raw is None or isinstance(raw, dict | list):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int | float):
        return str(value)
    return ""


def _clip(text: str, limit: int = ITEM_MAX_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _strings(values: Any, *, limit: int = LIST_MAX_ITEMS) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence):
        return []
    out: list[str] = []
    for item in values:
        text = _text(item)
        if text:
            out.append(_clip(text))
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class StoryInputs:
    """Ce que le rédacteur et le juge savent du brief.

    Attributes:
        brief_id: Identifiant du brief.
        title: Titre scientifique (hypothèse affûtée).
        formal_statement: Énoncé formel.
        domains: Domaines de la collision.
        causal_chain: Chaîne causale du mécanisme.
        key_assumptions: Hypothèses de travail.
        predictions: Prédictions testables (texte et borne quantitative).
        vulgarisation: Vulgarisation FR mise en texte (vide si absente).
        limits: Conditions de validité, inconnues, désaccords des relecteurs.
        counter_evidence: Constats des contre-preuves, sans référence.
    """

    brief_id: str
    title: str
    formal_statement: str
    domains: tuple[str, ...]
    causal_chain: tuple[str, ...]
    key_assumptions: tuple[str, ...]
    predictions: tuple[str, ...]
    vulgarisation: str
    limits: tuple[str, ...]
    counter_evidence: tuple[str, ...] = field(default_factory=tuple)

    def has_substance(self) -> bool:
        """Indique si le brief porte assez de matière pour un récit.

        Returns:
            ``True`` si l'énoncé formel ou la chaîne causale existe.
        """
        return bool(self.formal_statement or self.causal_chain)


def _vulgarisation_text(data: Any) -> str:
    if not isinstance(data, Mapping):
        return ""
    parts: list[str] = []
    labels = (
        ("title_fr", "Titre"),
        ("hypothesis_in_brief", "L'hypothèse en bref"),
        ("why_it_matters", "Pourquoi c'est important"),
        ("imagine_that", "Imaginez"),
    )
    for key, label in labels:
        text = _text(data.get(key))
        if text:
            parts.append(f"{label} : {_clip(text, 1200)}")
    concretely = data.get("concretely")
    if isinstance(concretely, Mapping):
        steps = [
            _clip(_text(concretely.get(key)), 500)
            for key in ("intro", "phase1", "phase2", "phase3")
            if _text(concretely.get(key))
        ]
        if steps:
            parts.append("Concrètement : " + " / ".join(steps))
    return "\n".join(parts)


def extract_inputs(row: Mapping[str, Any]) -> StoryInputs:
    """Construit les entrées du récit à partir d'une ligne ``briefs``.

    Args:
        row: Ligne ``briefs`` (colonnes ``sharpened_data``,
            ``vulgarization_data``, ``grounding_data``, ``panel_data``).

    Returns:
        Entrées normalisées, bornées en taille.
    """
    sharpened = load_blob(row.get("sharpened_data")) or {}
    if not isinstance(sharpened, Mapping):
        sharpened = {}
    mechanism = sharpened.get("proposed_mechanism")
    if not isinstance(mechanism, Mapping):
        mechanism = {"causal_chain": mechanism} if isinstance(mechanism, str) else {}

    predictions: list[str] = []
    for item in sharpened.get("falsifiable_predictions") or []:
        if isinstance(item, Mapping):
            text = _text(item.get("prediction"))
            bound = _text(item.get("quantitative_bound"))
            if text:
                predictions.append(_clip(f"{text} (borne : {bound})" if bound else text))
        elif _text(item):
            predictions.append(_clip(_text(item)))
        if len(predictions) >= LIST_MAX_ITEMS:
            break

    limits: list[str] = []
    for item in sharpened.get("boundary_conditions") or []:
        if isinstance(item, Mapping):
            condition = _text(item.get("condition"))
            why = _text(item.get("justification"))
            if condition:
                limits.append(_clip(f"Condition de validité : {condition}" + (f" — {why}" if why else "")))
    limits.extend(f"Inconnue : {text}" for text in _strings(mechanism.get("known_unknowns")))
    panel = load_blob(row.get("panel_data")) or {}
    meta = panel.get("meta_review") if isinstance(panel, Mapping) else None
    if isinstance(meta, Mapping):
        limits.extend(
            f"Désaccord des relecteurs : {text}"
            for text in _strings(meta.get("key_disagreements"), limit=3)
        )

    counter: list[str] = []
    grounding = load_blob(row.get("grounding_data")) or {}
    if isinstance(grounding, Mapping):
        for item in grounding.get("counter_evidence") or []:
            # Le constat seulement : titre, auteurs et DOI resteraient des
            # références, que la fiction ne cite jamais.
            if isinstance(item, Mapping) and _text(item.get("finding")):
                severity = _text(item.get("severity"))
                finding = _clip(_text(item.get("finding")))
                counter.append(f"[{severity}] {finding}" if severity else finding)
            if len(counter) >= LIST_MAX_ITEMS:
                break

    domains = tuple(
        _text(item) for item in (sharpened.get("domains") or []) if _text(item)
    )
    return StoryInputs(
        brief_id=str(row.get("id") or ""),
        title=_clip(_text(sharpened.get("title")), 300),
        formal_statement=_clip(_text(sharpened.get("formal_statement") or row.get("formal_statement")), 1500),
        domains=domains,
        causal_chain=tuple(_strings(mechanism.get("causal_chain"))),
        key_assumptions=tuple(_strings(mechanism.get("key_assumptions"), limit=4)),
        predictions=tuple(predictions),
        vulgarisation=_vulgarisation_text(load_blob(row.get("vulgarization_data"))),
        limits=tuple(limits[: LIST_MAX_ITEMS + 3]),
        counter_evidence=tuple(counter),
    )


def bullets(items: Sequence[str], *, empty: str = "(aucun élément fourni)") -> str:
    """Met une liste en puces pour un prompt.

    Args:
        items: Éléments.
        empty: Texte si la liste est vide.

    Returns:
        Lignes préfixées par « - ».
    """
    return "\n".join(f"- {item}" for item in items) if items else empty
