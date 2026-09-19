"""``theme_tagger`` : thèmes grand public d'un brief, sans LLM.

Règle mécanique du contrat de données, domaine par domaine de la collision :
nom exact dans ``domains`` → sinon ``parent_domain`` dans ``parent_domains``
→ sinon ``fallback``. Un brief reçoit 1 ou 2 thèmes (un par domaine,
dédoublonnés).

Le ``parent_domain`` d'un domaine vient des cartes de domaines du cœur
(lecture seule), puis, pour les noms absents des cartes, des collisions
enregistrées (``hypotheses.collision_json``).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from logging_config import get_logger

logger = get_logger("narrative.themes")

#: Nombre maximal de domaines pris en compte (une collision en a deux).
MAX_DOMAINS = 2


class ThemesFileError(ValueError):
    """Table de correspondance absente ou invalide."""


@dataclass(frozen=True)
class ThemeMapping:
    """Table ``domaine → thème`` figée (``themes_v1.json``).

    Attributes:
        version: Version de la table (``themes_v1``).
        slugs: Slugs des thèmes, dans l'ordre de ``position``.
        domains: Nom exact de domaine → slug.
        parent_domains: ``parent_domain`` → slug.
        fallback: Slug de repli.
        placeholder: Table provisoire (tests du pipeline).
    """

    version: str
    slugs: tuple[str, ...]
    domains: Mapping[str, str]
    parent_domains: Mapping[str, str]
    fallback: str
    placeholder: bool = False


def load_theme_mapping(path: Path) -> ThemeMapping:
    """Lit et valide la table de correspondance.

    Args:
        path: ``config/narrative/themes_v1.json``.

    Returns:
        Table validée.

    Raises:
        ThemesFileError: Fichier absent, illisible ou incohérent (slug inconnu).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ThemesFileError(f"table des thèmes illisible : {path} ({exc})") from exc
    try:
        themes = sorted(raw["themes"], key=lambda item: item.get("position", 0))
        slugs = tuple(str(item["slug"]) for item in themes)
        mapping = ThemeMapping(
            version=str(raw["version"]),
            slugs=slugs,
            domains={str(k): str(v) for k, v in (raw.get("domains") or {}).items()},
            parent_domains={str(k): str(v) for k, v in (raw.get("parent_domains") or {}).items()},
            fallback=str(raw["fallback"]),
            placeholder=bool(raw.get("placeholder", False)),
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ThemesFileError(f"table des thèmes invalide : {exc}") from exc
    known = set(mapping.slugs)
    unknown = (
        {mapping.fallback}
        | set(mapping.domains.values())
        | set(mapping.parent_domains.values())
    ) - known
    if unknown:
        raise ThemesFileError(f"slugs inconnus dans la table des thèmes : {sorted(unknown)}")
    return mapping


@dataclass
class ParentIndex:
    """Nom exact de domaine → ``parent_domain``.

    Attributes:
        parents: Correspondance.
        sources: Origine de chaque entrée (``domain_map`` ou ``collision``).
    """

    parents: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def add(self, name: Any, parent: Any, source: str) -> None:
        """Ajoute une entrée si le nom n'est pas déjà connu.

        Args:
            name: Nom exact du domaine.
            parent: ``parent_domain``.
            source: Origine.
        """
        if not isinstance(name, str) or not isinstance(parent, str):
            return
        name, parent = name.strip(), parent.strip()
        if name and parent and name not in self.parents:
            self.parents[name] = parent
            self.sources[name] = source

    def get(self, name: str) -> str | None:
        """``parent_domain`` d'un domaine.

        Args:
            name: Nom exact.

        Returns:
            Parent, ou ``None``.
        """
        return self.parents.get(name)


def _walk_domain_map(data: Any, index: ParentIndex, inherited: str | None = None) -> None:
    """Parcourt une carte de domaines, quelle que soit sa forme.

    Formes connues : ``{"disciplines": [{"name", "subdomains": [...]}]}``
    (``all_science.json``) et ``{"subdomains": [...]}``
    (``materials_science.json``). Une discipline est son propre parent.

    Args:
        data: Carte décodée (ou sous-partie).
        index: Index à compléter.
        inherited: Parent hérité de la discipline englobante.
    """
    if isinstance(data, list):
        for item in data:
            _walk_domain_map(item, index, inherited)
        return
    if not isinstance(data, Mapping):
        return
    name = data.get("name")
    parent = data.get("parent_domain") or inherited
    if isinstance(name, str) and parent:
        index.add(name, parent, "domain_map")
    discipline_parent = inherited
    if "subdomains" in data and isinstance(name, str) and not data.get("parent_domain"):
        # Discipline : nom propre = parent de ses sous-domaines.
        index.add(name, name, "domain_map")
        discipline_parent = name
    for key in ("disciplines", "subdomains"):
        if key in data:
            _walk_domain_map(data[key], index, discipline_parent)


def build_parent_index(
    domain_map_paths: Sequence[Path],
    conn: sqlite3.Connection | None = None,
) -> ParentIndex:
    """Index ``domaine → parent_domain`` (cartes, puis collisions en base).

    Args:
        domain_map_paths: Cartes de domaines (lecture seule).
        conn: Connexion pour compléter depuis ``hypotheses.collision_json``.

    Returns:
        Index.
    """
    index = ParentIndex()
    for path in domain_map_paths:
        try:
            _walk_domain_map(json.loads(Path(path).read_text(encoding="utf-8")), index)
        except (OSError, ValueError) as exc:
            logger.warning("narrative_domain_map_unreadable", path=str(path), error=str(exc)[:200])
    if conn is not None:
        try:
            rows = conn.execute("SELECT collision_json FROM hypotheses ORDER BY id").fetchall()
        except sqlite3.Error:
            rows = []
        for row in rows:
            try:
                collision = json.loads(row[0])
            except (TypeError, ValueError):
                continue
            for key in ("domain_a", "domain_b"):
                domain = collision.get(key) if isinstance(collision, Mapping) else None
                if isinstance(domain, Mapping):
                    index.add(domain.get("name"), domain.get("parent_domain"), "collision")
    return index


def tag_domains(
    domains: Iterable[str],
    mapping: ThemeMapping,
    parents: ParentIndex,
) -> list[tuple[str, str]]:
    """Thèmes d'un brief, règle mécanique du contrat.

    Args:
        domains: Domaines de la collision (les deux premiers sont retenus).
        mapping: Table figée.
        parents: Index des ``parent_domain``.

    Returns:
        Couples ``(slug, source)`` dédoublonnés, 1 ou 2 éléments ; le repli
        seul si le brief n'a aucun domaine.
    """
    tagged: list[tuple[str, str]] = []
    seen: set[str] = set()
    names = [name.strip() for name in domains if isinstance(name, str) and name.strip()]
    for name in names[:MAX_DOMAINS]:
        if name in mapping.domains:
            slug, source = mapping.domains[name], "domain"
        else:
            parent = parents.get(name)
            if parent is not None and parent in mapping.parent_domains:
                slug, source = mapping.parent_domains[parent], "parent_domain"
            else:
                slug, source = mapping.fallback, "fallback"
        if slug not in seen:
            seen.add(slug)
            tagged.append((slug, source))
    if not tagged:
        tagged.append((mapping.fallback, "fallback"))
    return tagged


def coverage(
    domain_names: Iterable[str], mapping: ThemeMapping, parents: ParentIndex
) -> dict[str, Any]:
    """Part des domaines résolus par nom exact, par parent et par repli.

    Args:
        domain_names: Noms distincts de domaines des briefs publiés.
        mapping: Table figée.
        parents: Index des parents.

    Returns:
        Compteurs et pourcentage de repli.
    """
    counts = {"domain": 0, "parent_domain": 0, "fallback": 0}
    unique = sorted({name for name in domain_names if name})
    for name in unique:
        _slug, source = tag_domains([name], mapping, parents)[0]
        counts[source] += 1
    total = len(unique)
    return {
        "distinct_domains": total,
        **counts,
        "fallback_pct": round(100.0 * counts["fallback"] / total, 1) if total else 0.0,
    }
