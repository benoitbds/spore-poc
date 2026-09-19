"""Chargement et rendu des prompts de la couche narrative.

Les prompts vivent dans ``narrative/prompts/<nom>.txt`` (jamais dans
``prompts/``, qui appartient au cœur). Même convention que
``agents.base.load_prompt`` : gabarit ``str.format``, accolades des exemples
JSON doublées. Le nom du fichier sert de version de prompt
(``story_writer_v0``…).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

#: Répertoire des prompts versionnés de la couche.
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class PromptNotFoundError(FileNotFoundError):
    """Prompt narratif introuvable."""


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    """Lit un gabarit de prompt.

    Args:
        name: Nom du fichier, sans extension (``story_writer_v0``).

    Returns:
        Le gabarit brut.

    Raises:
        PromptNotFoundError: Fichier absent.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise PromptNotFoundError(f"prompt narratif introuvable : {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **values: Any) -> str:
    """Rend un gabarit avec ses valeurs.

    Args:
        name: Nom du prompt.
        **values: Champs du gabarit.

    Returns:
        Le prompt complet.

    Raises:
        PromptNotFoundError: Fichier absent.
        KeyError: Champ du gabarit non fourni.
    """
    return load_prompt(name).format(**values)
