"""Étapes mécaniques de la couche : thèmes, lien d'hypothèse, voisines.

Sans LLM, idempotentes, rejouables à volonté : elles tournent après les
étapes « récit » dans le sous-graphe, et encore une fois, sous leur propre
délai, si le sous-graphe a échoué ou dépassé son délai global. Fonctions
synchrones (``sqlite3``), appelées par ``asyncio.to_thread`` depuis le
sous-graphe et directement depuis le backfill.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from logging_config import get_logger
from narrative.config import NarrativeConfig
from narrative.linking import is_real_hypothesis_id
from narrative.neighbours import (
    IdeaNode,
    NeighbourSettings,
    compute_neighbours,
    idea_vector,
    load_embedding_index,
    summarise,
)
from narrative.themes import (
    ParentIndex,
    ThemeMapping,
    build_parent_index,
    load_theme_mapping,
    tag_domains,
)
from storage import narrative_db

logger = get_logger("narrative.mechanical")


@lru_cache(maxsize=4)
def _mapping(path: Path, mtime_ns: int) -> ThemeMapping:
    del mtime_ns  # clé de cache seulement : un fichier remplacé est relu
    return load_theme_mapping(path)


def theme_mapping(config: NarrativeConfig) -> ThemeMapping:
    """Table des thèmes de la configuration (relue si le fichier change).

    Args:
        config: Configuration.

    Returns:
        Table validée.
    """
    path = config.themes_path
    return _mapping(path, path.stat().st_mtime_ns)


def parent_index(config: NarrativeConfig, conn: sqlite3.Connection | None) -> ParentIndex:
    """Index ``domaine → parent_domain`` (cartes du cœur, puis collisions).

    Args:
        config: Configuration (chemins des cartes).
        conn: Connexion pour les collisions enregistrées.

    Returns:
        Index.
    """
    return build_parent_index(config.domain_map_paths, conn)


def tag_brief(db_path: str | Path, brief_id: str, config: NarrativeConfig) -> list[tuple[str, str]]:
    """``theme_tagger`` : calcule et enregistre les thèmes d'un brief.

    Args:
        db_path: Base.
        brief_id: Brief.
        config: Configuration.

    Returns:
        Couples ``(slug, source)`` écrits.
    """
    mapping = theme_mapping(config)
    with narrative_db.connect(db_path) as conn:
        row = narrative_db.fetch_brief(conn, brief_id)
        if row is None:
            return []
        themes = tag_domains(narrative_db.brief_domains(row), mapping, parent_index(config, conn))
        narrative_db.replace_brief_themes(conn, brief_id, themes, mapping.version)
    logger.info("narrative_themes_tagged", brief_id=brief_id, themes=[slug for slug, _ in themes])
    return themes


def tag_all(conn: sqlite3.Connection, config: NarrativeConfig, *, write: bool) -> dict[str, list[tuple[str, str]]]:
    """Thèmes de tous les briefs publiés non-stubs (backfill).

    Les stubs n'ont pas de thème (D-015).

    Args:
        conn: Connexion (écriture seulement si ``write``).
        config: Configuration.
        write: Enregistrer les thèmes.

    Returns:
        ``{brief_id: [(slug, source), …]}``.
    """
    mapping = theme_mapping(config)
    parents = parent_index(config, conn)
    result: dict[str, list[tuple[str, str]]] = {}
    for row in narrative_db.list_published_briefs(conn):
        if row["is_stub"]:
            continue
        themes = tag_domains(narrative_db.brief_domains(row), mapping, parents)
        result[row["id"]] = themes
        if write:
            narrative_db.replace_brief_themes(conn, row["id"], themes, mapping.version)
    return result


def link_brief(db_path: str | Path, brief_id: str, hypothesis_id: Any) -> str | None:
    """``brief_link`` : lien brief ↔ hypothèse lu dans l'état du post-fire.

    Rien n'est écrit si l'identifiant manque ou vaut celui du brief (repli du
    générateur, qui produirait le lien auto-référent de l'historique).

    Args:
        db_path: Base.
        brief_id: Brief.
        hypothesis_id: ``hypothesis_id`` de ``PostFireState``.

    Returns:
        Méthode écrite (``pipeline_state``) ou ``None``.
    """
    if not is_real_hypothesis_id(hypothesis_id, brief_id):
        logger.info("narrative_link_skipped", brief_id=brief_id, reason="no_hypothesis_id")
        return None
    with narrative_db.connect(db_path) as conn:
        narrative_db.upsert_brief_hypothesis(
            conn, brief_id, str(hypothesis_id).strip(), "pipeline_state", overwrite=True
        )
    logger.info("narrative_link_written", brief_id=brief_id, method="pipeline_state")
    return "pipeline_state"


def neighbour_settings(config: NarrativeConfig) -> NeighbourSettings:
    """Bornes du voisinage de la configuration.

    Args:
        config: Configuration.

    Returns:
        Bornes.
    """
    return NeighbourSettings(
        target=config.neighbours_target,
        min_out=config.neighbours_min,
        max_out=config.neighbours_max,
        min_inbound=config.neighbours_min_inbound,
        stub_ring=config.stub_ring,
    )


def load_ideas(conn: sqlite3.Connection, config: NarrativeConfig) -> list[IdeaNode]:
    """Idées publiées, avec domaines, thèmes et centroïdes.

    Les thèmes sont recalculés par la règle mécanique (identique à
    ``v2_brief_themes``), pour ne pas dépendre de l'état de la table.

    Args:
        conn: Connexion.
        config: Configuration.

    Returns:
        Idées publiées, ordonnées par identifiant.
    """
    mapping = theme_mapping(config)
    parents = parent_index(config, conn)
    embeddings = load_embedding_index(conn)
    links = narrative_db.links_by_brief(conn)
    ideas: list[IdeaNode] = []
    for row in narrative_db.list_published_briefs(conn):
        domains = tuple(narrative_db.brief_domains(row))
        is_stub = bool(row["is_stub"])
        themes = () if is_stub else tuple(slug for slug, _ in tag_domains(domains, mapping, parents))
        vector = None if is_stub else idea_vector(row["id"], domains, links, embeddings)
        ideas.append(IdeaNode(row["id"], is_stub, domains, themes, vector))
    return ideas


def refresh_neighbours(db_path: str | Path, config: NarrativeConfig, *, write: bool = True) -> dict[str, Any]:
    """``neighbours_refresh`` : recalcule tout le maillage des idées publiées.

    Args:
        db_path: Base.
        config: Configuration.
        write: Remplacer ``v2_brief_neighbours`` (sinon calcul seul).

    Returns:
        Indicateurs du maillage.
    """
    with narrative_db.connect(db_path, readonly=not write) as conn:
        ideas = load_ideas(conn, config)
        edges = compute_neighbours(ideas, neighbour_settings(config))
        if write:
            narrative_db.replace_all_neighbours(conn, (edge.as_row() for edge in edges))
    summary = summarise(ideas, edges)
    logger.info("narrative_neighbours_refreshed", written=write, **summary)
    return summary
