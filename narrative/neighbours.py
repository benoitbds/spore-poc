"""Idées voisines (D-008) : ``v2_brief_neighbours``.

Similarité entre deux idées complètes = cosinus entre les centroïdes des
embeddings existants des deux domaines de leur collision
(``hypotheses.collision_json``, 384 dimensions). Centroïde d'une idée :

1. embeddings ``domain_a`` / ``domain_b`` de l'hypothèse liée
   (``v2_brief_hypothesis``) ;
2. sinon, embeddings des domaines du brief (``sharpened_data.domains``)
   retrouvés par nom exact dans l'ensemble des collisions enregistrées ;
3. sinon, pas de centroïde : repli sur les domaines puis les thèmes partagés.

Construction, déterministe (tri par score décroissant puis identifiant) :

* chaque idée complète reçoit ses ``target`` plus proches voisines complètes ;
* correction des entrants : tant qu'une idée reçoit moins de ``min_inbound``
  liens, elle est ajoutée aux voisines de sa plus proche idée qui a de la
  place (≤ ``max``) ; à défaut, elle y remplace le lien le plus faible dont la
  cible garde plus de ``min_inbound`` entrants ; en dernier recours, le
  plafond sortant est dépassé (le minimum d'entrants prime, MUST 10). Chaque
  pas réduit strictement la somme des déficits, sans créer de nouveau
  déficit : la boucle termine ;
* les stubs forment un anneau entre eux (``stub_ring``) : chacun pointe vers
  les ``stub_ring`` suivants dans l'ordre des identifiants, donc reçoit
  exactement ``stub_ring`` entrants ; aucune idée complète ne pointe vers un
  stub, aucun stub vers une idée complète (D-015).
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from logging_config import get_logger

logger = get_logger("narrative.neighbours")

Vector = tuple[float, ...]
ScoreFn = Callable[["IdeaNode", "IdeaNode"], tuple[float, str]]


@dataclass(frozen=True)
class IdeaNode:
    """Une idée publiée, vue par le calcul de voisinage.

    Attributes:
        brief_id: Identifiant du brief.
        is_stub: Collision non productive.
        domains: Domaines de la collision.
        themes: Slugs de thèmes.
        vector: Centroïde normalisé des embeddings de domaines, ou ``None``.
    """

    brief_id: str
    is_stub: bool
    domains: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    vector: Vector | None = None


@dataclass(frozen=True)
class NeighbourEdge:
    """Lien ``brief_id → neighbour_id``.

    Attributes:
        brief_id: Page qui affiche le lien.
        neighbour_id: Idée liée.
        rank: 1 = plus proche.
        score: Similarité (``None`` pour l'anneau des stubs).
        method: ``domain_embedding``, ``domain``, ``theme``, ``inbound_patch``
            ou ``stub_ring``.
    """

    brief_id: str
    neighbour_id: str
    rank: int
    score: float | None
    method: str

    def as_row(self) -> tuple[str, str, int, float | None, str]:
        """Ligne pour ``replace_all_neighbours``.

        Returns:
            ``(brief_id, neighbour_id, rank, score, method)``.
        """
        return (self.brief_id, self.neighbour_id, self.rank, self.score, self.method)


# ── Vecteurs ────────────────────────────────────────────────────────


def normalise_vector(values: Iterable[float]) -> Vector | None:
    """Vecteur de norme 1.

    Args:
        values: Composantes.

    Returns:
        Vecteur normalisé, ou ``None`` s'il est vide, nul ou non fini.
    """
    vector = tuple(float(v) for v in values)
    norm = math.sqrt(sum(v * v for v in vector))
    if not vector or norm == 0.0 or not math.isfinite(norm):
        return None
    return tuple(v / norm for v in vector)


def centroid(vectors: Sequence[Sequence[float]]) -> Vector | None:
    """Centroïde normalisé de vecteurs de même dimension.

    Args:
        vectors: Vecteurs (les dimensions différentes de la première sont
            écartées).

    Returns:
        Centroïde normalisé, ou ``None``.
    """
    usable = [v for v in vectors if v]
    if not usable:
        return None
    dim = len(usable[0])
    usable = [v for v in usable if len(v) == dim]
    sums = [0.0] * dim
    for vector in usable:
        for i, value in enumerate(vector):
            sums[i] += float(value)
    return normalise_vector(value / len(usable) for value in sums)


def cosine(a: Vector, b: Vector) -> float:
    """Cosinus de deux vecteurs normalisés.

    Args:
        a: Premier vecteur.
        b: Second vecteur.

    Returns:
        Produit scalaire, borné à [-1, 1].
    """
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b, strict=False))))


def default_score(a: IdeaNode, b: IdeaNode) -> tuple[float, str]:
    """Similarité de deux idées et méthode employée.

    Args:
        a: Première idée.
        b: Seconde idée.

    Returns:
        ``(score, méthode)`` : cosinus des centroïdes si les deux existent,
        sinon part de domaines puis de thèmes partagés (0 si rien n'est
        partagé : voisine de remplissage, ordre déterministe).
    """
    if a.vector is not None and b.vector is not None:
        return round(cosine(a.vector, b.vector), 6), "domain_embedding"
    shared_domains = len(set(a.domains) & set(b.domains))
    if shared_domains:
        return round(min(1.0, 0.5 + 0.25 * shared_domains), 6), "domain"
    shared_themes = len(set(a.themes) & set(b.themes))
    return round(min(0.45, 0.2 * shared_themes), 6), "theme"


# ── Construction ────────────────────────────────────────────────────


@dataclass(frozen=True)
class NeighbourSettings:
    """Bornes du voisinage.

    Attributes:
        target: Voisines sortantes visées par idée complète.
        min_out: Voisines sortantes minimales.
        max_out: Voisines sortantes maximales (hors dernier recours).
        min_inbound: Entrants minimaux par idée complète.
        stub_ring: Liens de l'anneau des stubs.
    """

    target: int = 4
    min_out: int = 3
    max_out: int = 5
    min_inbound: int = 3
    stub_ring: int = 3


def _order(item: tuple[str, float]) -> tuple[float, str]:
    return (-item[1], item[0])


def compute_full_edges(
    ideas: Sequence[IdeaNode],
    settings: NeighbourSettings,
    score_fn: ScoreFn = default_score,
) -> list[NeighbourEdge]:
    """Voisinage des idées complètes, entrants corrigés.

    Args:
        ideas: Idées complètes (les stubs sont ignorés).
        settings: Bornes.
        score_fn: Similarité ``(a, b) → (score, méthode)``, symétrique.

    Returns:
        Liens classés (rang 1..k par idée).
    """
    full = sorted((idea for idea in ideas if not idea.is_stub), key=lambda idea: idea.brief_id)
    ids = [idea.brief_id for idea in full]
    n = len(full)
    if n < 2:
        return []

    pair: dict[tuple[str, str], tuple[float, str]] = {}
    for i, a in enumerate(full):
        for b in full[i + 1 :]:
            value = score_fn(a, b)
            pair[(a.brief_id, b.brief_id)] = value
            pair[(b.brief_id, a.brief_id)] = value

    def score(a: str, b: str) -> float:
        return pair[(a, b)][0]

    # out[a] : cible → méthode.
    out: dict[str, dict[str, str]] = {}
    take = min(settings.target, n - 1)
    for a in ids:
        ranked = sorted(((b, score(a, b)) for b in ids if b != a), key=_order)
        out[a] = {b: pair[(a, b)][1] for b, _ in ranked[:take]}

    inbound: Counter[str] = Counter()
    for targets in out.values():
        inbound.update(targets.keys())

    unsatisfiable: set[str] = set()
    need = min(settings.min_inbound, n - 1)
    guard = n * need * 4 + 10
    while guard > 0:
        guard -= 1
        deficits = sorted(
            (b for b in ids if inbound[b] < need and b not in unsatisfiable),
            key=lambda b: (inbound[b], b),
        )
        if not deficits:
            break
        d = deficits[0]
        candidates = sorted(
            ((c, score(c, d)) for c in ids if c != d and d not in out[c]), key=_order
        )
        if not candidates:
            unsatisfiable.add(d)
            continue
        placed = False
        # 1. La plus proche idée qui a de la place.
        for c, _ in candidates:
            if len(out[c]) < settings.max_out:
                out[c][d] = "inbound_patch"
                inbound[d] += 1
                placed = True
                break
        # 2. Remplacement du lien le plus faible dont la cible reste pourvue.
        if not placed:
            for c, _ in candidates:
                removable = sorted(
                    (t for t in out[c] if inbound[t] > need),
                    key=lambda t, c=c: (score(c, t), t),
                )
                if removable:
                    victim = removable[0]
                    del out[c][victim]
                    inbound[victim] -= 1
                    out[c][d] = "inbound_patch"
                    inbound[d] += 1
                    placed = True
                    break
        # 3. Dernier recours : le minimum d'entrants prime sur le plafond.
        if not placed:
            c = candidates[0][0]
            out[c][d] = "inbound_patch"
            inbound[d] += 1
            logger.warning("narrative_neighbours_max_out_exceeded", brief_id=c, target=d)

    if unsatisfiable:
        logger.warning("narrative_neighbours_inbound_unsatisfiable", brief_ids=sorted(unsatisfiable))

    edges: list[NeighbourEdge] = []
    for a in ids:
        ranked = sorted(((b, score(a, b)) for b in out[a]), key=_order)
        for rank, (b, value) in enumerate(ranked, start=1):
            edges.append(NeighbourEdge(a, b, rank, float(value), out[a][b]))
    return edges


def compute_stub_ring(ideas: Sequence[IdeaNode], settings: NeighbourSettings) -> list[NeighbourEdge]:
    """Anneau des stubs : chacun pointe vers les ``stub_ring`` suivants.

    Args:
        ideas: Idées publiées (seuls les stubs sont retenus).
        settings: Bornes.

    Returns:
        Liens ``stub_ring`` ; chaque stub en reçoit exactement
        ``min(stub_ring, nombre de stubs - 1)``.
    """
    stubs = sorted(idea.brief_id for idea in ideas if idea.is_stub)
    m = len(stubs)
    width = min(settings.stub_ring, m - 1)
    edges: list[NeighbourEdge] = []
    for i, stub in enumerate(stubs):
        for k in range(1, width + 1):
            edges.append(NeighbourEdge(stub, stubs[(i + k) % m], k, None, "stub_ring"))
    return edges


def compute_neighbours(
    ideas: Sequence[IdeaNode],
    settings: NeighbourSettings | None = None,
    score_fn: ScoreFn = default_score,
) -> list[NeighbourEdge]:
    """Maillage complet (idées complètes puis anneau des stubs).

    Args:
        ideas: Idées publiées.
        settings: Bornes (valeurs par défaut du contrat sinon).
        score_fn: Similarité entre idées complètes.

    Returns:
        Tous les liens.
    """
    settings = settings or NeighbourSettings()
    return compute_full_edges(ideas, settings, score_fn) + compute_stub_ring(ideas, settings)


def inbound_counts(edges: Iterable[NeighbourEdge]) -> Counter[str]:
    """Entrants par idée.

    Args:
        edges: Liens.

    Returns:
        Compteur ``neighbour_id → nombre de liens distincts``.
    """
    seen = {(edge.brief_id, edge.neighbour_id) for edge in edges}
    return Counter(target for _source, target in seen)


def summarise(ideas: Sequence[IdeaNode], edges: Sequence[NeighbourEdge]) -> dict[str, Any]:
    """Indicateurs du maillage (pour les journaux et le backfill).

    Args:
        ideas: Idées publiées.
        edges: Liens calculés.

    Returns:
        Compteurs, entrants et sortants minimaux et maximaux, par population.
    """
    inbound = inbound_counts(edges)
    outbound: Counter[str] = Counter(edge.brief_id for edge in edges)
    full = [idea.brief_id for idea in ideas if not idea.is_stub]
    stubs = [idea.brief_id for idea in ideas if idea.is_stub]

    def span(counter: Counter[str], population: list[str]) -> list[int] | None:
        if not population:
            return None
        values = [counter[item] for item in population]
        return [min(values), max(values)]

    return {
        "ideas": len(full),
        "stubs": len(stubs),
        "edges": len(edges),
        "full_inbound_min_max": span(inbound, full),
        "full_outbound_min_max": span(outbound, full),
        "stub_inbound_min_max": span(inbound, stubs),
        "with_vector": sum(1 for idea in ideas if idea.vector is not None and not idea.is_stub),
        "methods": dict(Counter(edge.method for edge in edges)),
    }


# ── Lecture en base ─────────────────────────────────────────────────


@dataclass
class EmbeddingIndex:
    """Embeddings de domaines lus dans ``hypotheses.collision_json``.

    Attributes:
        by_hypothesis: Hypothèse → embeddings de ses deux domaines.
        by_name: Nom exact de domaine → centroïde normalisé de ses embeddings.
    """

    by_hypothesis: dict[str, list[Vector]]
    by_name: dict[str, Vector]


def load_embedding_index(conn: sqlite3.Connection) -> EmbeddingIndex:
    """Parcourt les collisions enregistrées (lecture seule).

    Args:
        conn: Connexion.

    Returns:
        Index par hypothèse et par nom de domaine.
    """
    by_hypothesis: dict[str, list[Vector]] = {}
    per_name: dict[str, list[Vector]] = {}
    try:
        rows = conn.execute("SELECT id, collision_json FROM hypotheses ORDER BY id").fetchall()
    except sqlite3.Error:
        rows = []
    for row in rows:
        try:
            collision = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        if not isinstance(collision, Mapping):
            continue
        vectors: list[Vector] = []
        for key in ("domain_a", "domain_b"):
            domain = collision.get(key)
            if not isinstance(domain, Mapping):
                continue
            raw = domain.get("embedding")
            if not isinstance(raw, list) or not raw:
                continue
            try:
                vector = tuple(float(value) for value in raw)
            except (TypeError, ValueError):
                continue
            vectors.append(vector)
            name = domain.get("name")
            if isinstance(name, str) and name.strip():
                per_name.setdefault(name.strip(), []).append(vector)
        if vectors:
            by_hypothesis[row[0]] = vectors
    by_name = {
        name: vector
        for name, vectors in per_name.items()
        if (vector := centroid(vectors)) is not None
    }
    return EmbeddingIndex(by_hypothesis=by_hypothesis, by_name=by_name)


def idea_vector(
    brief_id: str,
    domains: Sequence[str],
    links: Mapping[str, tuple[str, str]],
    index: EmbeddingIndex,
) -> Vector | None:
    """Centroïde d'une idée (hypothèse liée, sinon domaines par nom).

    Args:
        brief_id: Brief.
        domains: Domaines du brief.
        links: ``brief_id → (hypothesis_id, méthode)``.
        index: Embeddings connus.

    Returns:
        Centroïde normalisé, ou ``None``.
    """
    link = links.get(brief_id)
    if link is not None:
        vectors = index.by_hypothesis.get(link[0])
        if vectors:
            return centroid(vectors)
    named = [index.by_name[name] for name in domains if name in index.by_name]
    return centroid(named) if named else None
