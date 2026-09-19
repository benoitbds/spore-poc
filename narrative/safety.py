"""Garde des chemins et chargement des clés LLM (D-010).

Le code de la v2 tourne depuis un clone (``~/Projects/spore-v2-poc``) avec
l'interpréteur de production. Un ``SPORE_DB_PATH`` mal posé, ou un chemin de
sortie hérité d'une colonne de la base (``briefs.brief_json_path`` pointe vers
la production), suffirait à écrire dans la production. Ce module refuse, avant
toute écriture, un chemin qui se résout sous un arbre de production, sauf si
``SPORE_V2_PRODUCTION=1`` est posé explicitement (bascule).

Il fournit aussi le chargement des seules clés LLM depuis le ``.env`` de
production, par analyse programmatique : aucune autre variable n'est lue dans
l'environnement du processus, et aucune valeur n'est journalisée ni renvoyée.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from logging_config import get_logger

logger = get_logger("narrative.safety")

#: Arbres de production. Toute écriture qui s'y résout est refusée hors bascule.
PRODUCTION_ROOTS: tuple[Path, ...] = (
    Path("/home/baq/Projects/spore-poc"),
    Path("/home/baq/Projects/spore-web"),
)

#: Variable qui autorise l'écriture en production (réservée à la bascule).
PRODUCTION_FLAG = "SPORE_V2_PRODUCTION"

#: Fichier d'environnement de production, source des clés LLM des scripts v2.
PRODUCTION_ENV_FILE = Path("/home/baq/Projects/spore-poc/.env")

#: Seules variables que ``load_llm_keys`` accepte de charger.
LLM_KEY_NAMES: tuple[str, ...] = ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")

#: Racine du dépôt qui porte ce module (le clone en développement).
REPO_ROOT = Path(__file__).resolve().parents[1]


class UnsafePathError(RuntimeError):
    """Un chemin d'écriture se résout sous un arbre de production."""


def is_production_mode() -> bool:
    """Indique si l'écriture en production est explicitement autorisée.

    Returns:
        ``True`` seulement si ``SPORE_V2_PRODUCTION`` vaut exactement ``"1"``.
    """
    return os.environ.get(PRODUCTION_FLAG) == "1"


def resolve_path(path: str | os.PathLike[str]) -> Path:
    """Résout un chemin, liens symboliques compris, sans exiger qu'il existe.

    Args:
        path: Chemin absolu ou relatif (relatif au répertoire courant).

    Returns:
        Chemin absolu résolu.
    """
    return Path(path).expanduser().resolve(strict=False)


def production_root_of(path: str | os.PathLike[str]) -> Path | None:
    """Arbre de production qui contient ``path``, s'il y en a un.

    Args:
        path: Chemin à tester.

    Returns:
        La racine de production concernée, ou ``None``.
    """
    resolved = resolve_path(path)
    for root in PRODUCTION_ROOTS:
        root_resolved = resolve_path(root)
        if resolved == root_resolved or root_resolved in resolved.parents:
            return root_resolved
    return None


def assert_safe_write_path(path: str | os.PathLike[str], *, what: str) -> Path:
    """Refuse un chemin d'écriture situé en production, hors bascule.

    Args:
        path: Chemin de base ou de fichier de sortie.
        what: Nature du chemin, pour le message (``db``, ``spend_json``…).

    Returns:
        Le chemin résolu, s'il est autorisé.

    Raises:
        UnsafePathError: Le chemin se résout sous un arbre de production et
            ``SPORE_V2_PRODUCTION`` ne vaut pas ``"1"``.
    """
    resolved = resolve_path(path)
    root = production_root_of(resolved)
    if root is not None and not is_production_mode():
        logger.error(
            "narrative_unsafe_path_refused",
            what=what,
            production_root=str(root),
        )
        raise UnsafePathError(
            f"{what}: écriture refusée sous {root} (poser {PRODUCTION_FLAG}=1 "
            "uniquement lors de la bascule)"
        )
    return resolved


def assert_safe_environment(
    db_path: str | os.PathLike[str],
    output_paths: Iterable[str | os.PathLike[str]] = (),
) -> Path:
    """Contrôle la base et tous les chemins de sortie avant toute écriture.

    Args:
        db_path: Base SQLite visée.
        output_paths: Fichiers ou répertoires que l'appelant écrira.

    Returns:
        Le chemin de base résolu.

    Raises:
        UnsafePathError: Un des chemins est en production hors bascule.
    """
    resolved_db = assert_safe_write_path(db_path, what="db")
    for index, output in enumerate(output_paths):
        assert_safe_write_path(output, what=f"output[{index}]")
    return resolved_db


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """Analyse une ligne ``NOM=valeur`` d'un fichier d'environnement.

    Args:
        line: Ligne brute.

    Returns:
        ``(nom, valeur)`` ou ``None`` pour un commentaire, une ligne vide ou
        une ligne sans ``=``.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    name, _, value = stripped.partition("=")
    name = name.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return name, value


def load_llm_keys(
    env_file: str | os.PathLike[str] = PRODUCTION_ENV_FILE,
    *,
    names: Iterable[str] = LLM_KEY_NAMES,
    override: bool = False,
) -> list[str]:
    """Charge dans ``os.environ`` les seules clés LLM d'un fichier ``.env``.

    Le fichier est lu par programme ; seules les variables nommées dans
    ``names`` (et parmi ``LLM_KEY_NAMES``) sont retenues. Aucune valeur n'est
    journalisée ni renvoyée : l'appelant ne reçoit que les noms chargés.

    Args:
        env_file: Fichier d'environnement à lire.
        names: Variables voulues ; toute variable hors ``LLM_KEY_NAMES`` est
            ignorée, quoi que demande l'appelant.
        override: Remplacer une variable déjà présente dans l'environnement.

    Returns:
        Noms des variables effectivement posées (triés).
    """
    wanted = {name for name in names if name in LLM_KEY_NAMES}
    path = Path(env_file)
    if not wanted or not path.is_file():
        logger.warning("narrative_llm_keys_unavailable", wanted=sorted(wanted))
        return []

    loaded: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            parsed = _parse_env_line(line)
            if parsed is None:
                continue
            name, value = parsed
            if name not in wanted or not value:
                continue
            if name in os.environ and not override:
                continue
            os.environ[name] = value
            loaded.append(name)

    logger.info("narrative_llm_keys_loaded", names=sorted(loaded))
    return sorted(loaded)
