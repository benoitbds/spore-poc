"""Lien brief ↔ hypothèse (D-007) : ``v2_brief_hypothesis``.

``briefs.hypothesis_id`` n'est pas exploitable sur l'historique (88 briefs
complets sur 90 pointent sur eux-mêmes). Trois méthodes, par ordre de
confiance :

* ``pipeline_state`` — le nœud narratif lit ``hypothesis_id`` dans l'état du
  post-fire (nouveaux briefs) ;
* ``fk`` — ``briefs.hypothesis_id`` désigne une ligne ``hypotheses`` existante
  (backfill) ;
* ``text_match`` — ``original_hypothesis`` du sidecar JSON du brief égale
  ``bridge_json.summary`` d'une hypothèse (backfill seulement, lecture seule,
  répertoire de sidecars de développement ; jamais ``briefs.brief_json_path``,
  qui contient des chemins absolus de production).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from logging_config import get_logger

logger = get_logger("narrative.linking")


def is_real_hypothesis_id(hypothesis_id: Any, brief_id: str) -> bool:
    """Identifiant d'hypothèse exploitable (ni absent, ni l'identifiant du brief).

    Args:
        hypothesis_id: Valeur lue dans l'état ou la base.
        brief_id: Identifiant du brief.

    Returns:
        ``True`` si l'identifiant désigne vraisemblablement une hypothèse.
    """
    return (
        isinstance(hypothesis_id, str)
        and bool(hypothesis_id.strip())
        and hypothesis_id.strip() != brief_id
    )


@dataclass(frozen=True)
class LinkCandidate:
    """Lien proposé pour un brief.

    Attributes:
        brief_id: Brief.
        hypothesis_id: Hypothèse.
        method: ``fk`` ou ``text_match``.
        ambiguous: Plusieurs hypothèses portaient le même texte.
    """

    brief_id: str
    hypothesis_id: str
    method: str
    ambiguous: bool = False


class SummaryIndex:
    """Texte ``bridge_json.summary`` → hypothèses (ordre déterministe)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Construit l'index depuis ``hypotheses``.

        Args:
            conn: Connexion (lecture seule suffit).
        """
        self._by_text: dict[str, list[tuple[str, str]]] = {}
        self._ids: set[str] = set()
        for row in conn.execute("SELECT id, generated_at, bridge_json FROM hypotheses ORDER BY id"):
            self._ids.add(row[0])
            try:
                summary = json.loads(row[2]).get("summary")
            except (TypeError, ValueError, AttributeError):
                continue
            if isinstance(summary, str) and summary.strip():
                self._by_text.setdefault(summary.strip(), []).append((row[0], row[1] or ""))

    def exists(self, hypothesis_id: str) -> bool:
        """Indique si une hypothèse existe.

        Args:
            hypothesis_id: Identifiant.

        Returns:
            ``True`` si la ligne existe.
        """
        return hypothesis_id in self._ids

    def match(self, text: str, brief_created_at: str | None) -> tuple[str | None, bool]:
        """Hypothèse dont le résumé égale exactement le texte.

        Plusieurs candidates : la plus récente générée avant le brief, sinon
        la plus récente.

        Args:
            text: ``original_hypothesis`` du sidecar.
            brief_created_at: Date de création du brief.

        Returns:
            ``(hypothesis_id, ambigu)`` ; ``(None, False)`` sans correspondance.
        """
        candidates = self._by_text.get(text.strip()) if isinstance(text, str) else None
        if not candidates:
            return None, False
        if len(candidates) == 1:
            return candidates[0][0], False
        ordered = sorted(candidates, key=lambda item: (item[1], item[0]), reverse=True)
        if brief_created_at:
            limit = brief_created_at.replace(" ", "T")
            for hypothesis_id, generated_at in ordered:
                if generated_at.replace(" ", "T") <= limit:
                    return hypothesis_id, True
        return ordered[0][0], True


def read_sidecar_hypothesis(sidecars_dir: Path, brief_id: str) -> str | None:
    """``original_hypothesis`` du sidecar d'un brief (lecture seule).

    Args:
        sidecars_dir: Répertoire des sidecars (``<sortie>/briefs``).
        brief_id: Brief.

    Returns:
        Texte, ou ``None`` si le fichier manque ou n'a pas le champ.
    """
    path = sidecars_dir / f"{brief_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("original_hypothesis") if isinstance(data, Mapping) else None
    return value if isinstance(value, str) and value.strip() else None


def resolve_backfill_link(
    brief: Mapping[str, Any],
    index: SummaryIndex,
    sidecars_dir: Path | None,
) -> LinkCandidate | None:
    """Lien d'un brief historique : ``fk`` puis ``text_match``.

    Args:
        brief: Ligne ``briefs`` (``id``, ``hypothesis_id``, ``created_at``).
        index: Index des résumés d'hypothèses.
        sidecars_dir: Répertoire des sidecars, ou ``None`` pour s'en passer.

    Returns:
        Lien proposé, ou ``None``.
    """
    brief_id = str(brief["id"])
    hypothesis_id = brief.get("hypothesis_id")
    if is_real_hypothesis_id(hypothesis_id, brief_id) and index.exists(str(hypothesis_id)):
        return LinkCandidate(brief_id, str(hypothesis_id), "fk")
    if sidecars_dir is None:
        return None
    text = read_sidecar_hypothesis(sidecars_dir, brief_id)
    if text is None:
        return None
    matched, ambiguous = index.match(text, brief.get("created_at"))
    if matched is None:
        return None
    return LinkCandidate(brief_id, matched, "text_match", ambiguous=ambiguous)
