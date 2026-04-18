"""Domain mapping and collision generation for SPORE.

Loads scientific domains, computes embeddings, and generates
random collisions within the fertile distance zone.
"""

import json
import random
from pathlib import Path
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from models.domain import Domain
from models.collision import CollisionPair, CollisionStrategy
from logging_config import get_logger

logger = get_logger("domain_map")


class DomainMap:
    """Manages scientific domains and their embeddings for collision generation."""

    def __init__(
        self,
        domains_path: Path | str = "data/domains/materials_science.json",
        model_name: str = "all-MiniLM-L6-v2",
    ):
        """Initialize the domain map.

        Args:
            domains_path: Path to the domains JSON file
            model_name: Sentence transformer model to use for embeddings
        """
        self.domains_path = Path(domains_path)
        self.model_name = model_name

        self._domains: list[Domain] = []
        self._embeddings: Optional[np.ndarray] = None
        self._model: Optional[SentenceTransformer] = None
        self._distance_matrix: Optional[np.ndarray] = None

    def load(self) -> None:
        """Load domains from JSON and compute embeddings.

        Supports two formats:
        - Flat: {"subdomains": [...]}  (materials_science.json)
        - Nested: {"disciplines": [{"name": "...", "subdomains": [...]}]}  (all_science.json)
        """
        logger.info("loading_domains", path=str(self.domains_path))

        if not self.domains_path.exists():
            raise FileNotFoundError(f"Domains file not found: {self.domains_path}")

        with open(self.domains_path) as f:
            data = json.load(f)

        # Parse domains - support both flat and nested structures
        self._domains = []

        if "disciplines" in data:
            # Nested structure (all_science.json)
            for discipline in data["disciplines"]:
                discipline_name = discipline.get("name", "unknown")
                for subdomain in discipline.get("subdomains", []):
                    domain = Domain(
                        id=subdomain["id"],
                        name=subdomain["name"],
                        parent_domain=subdomain.get("parent_domain", discipline_name),
                        key_concepts=subdomain.get("key_concepts", []),
                        taxonomy_source="custom",
                    )
                    self._domains.append(domain)
        else:
            # Flat structure (materials_science.json)
            for subdomain in data.get("subdomains", []):
                domain = Domain(
                    id=subdomain["id"],
                    name=subdomain["name"],
                    parent_domain=subdomain.get("parent_domain"),
                    key_concepts=subdomain.get("key_concepts", []),
                    taxonomy_source="custom",
                )
                self._domains.append(domain)

        logger.info("domains_loaded", count=len(self._domains))

        # Compute embeddings
        self._compute_embeddings()

    def _compute_embeddings(self) -> None:
        """Compute embeddings for all domains."""
        if not self._domains:
            return

        logger.info("computing_embeddings", model=self.model_name)

        # Lazy load model
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)

        # Create text representations for each domain
        texts = []
        for domain in self._domains:
            # Combine name and key concepts for richer representation
            text = f"{domain.name}: {', '.join(domain.key_concepts)}"
            texts.append(text)

        # Compute embeddings
        self._embeddings = self._model.encode(texts, convert_to_numpy=True)

        # Store embeddings in domain objects
        for i, domain in enumerate(self._domains):
            domain.embedding = self._embeddings[i].tolist()

        # Precompute distance matrix
        self._compute_distance_matrix()

        logger.info("embeddings_computed", shape=self._embeddings.shape)

    def _compute_distance_matrix(self) -> None:
        """Precompute pairwise cosine distances."""
        if self._embeddings is None:
            return

        # Normalize embeddings for cosine similarity
        norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        normalized = self._embeddings / norms

        # Cosine similarity matrix
        similarity_matrix = np.dot(normalized, normalized.T)

        # Convert to distance (1 - similarity)
        self._distance_matrix = 1 - similarity_matrix

        # Clip to [0, 1] range (numerical stability)
        self._distance_matrix = np.clip(self._distance_matrix, 0, 1)

    def get_distance(self, domain_a: Domain, domain_b: Domain) -> float:
        """Get the cosine distance between two domains.

        Args:
            domain_a: First domain
            domain_b: Second domain

        Returns:
            Cosine distance between 0 (identical) and 1 (orthogonal)
        """
        if self._distance_matrix is None:
            raise RuntimeError("Domain map not loaded. Call load() first.")

        idx_a = self._get_domain_index(domain_a.id)
        idx_b = self._get_domain_index(domain_b.id)

        if idx_a is None or idx_b is None:
            raise ValueError("Domain not found in map")

        return float(self._distance_matrix[idx_a, idx_b])

    def _get_domain_index(self, domain_id: str) -> Optional[int]:
        """Get the index of a domain by ID."""
        for i, domain in enumerate(self._domains):
            if domain.id == domain_id:
                return i
        return None

    def get_domain_by_id(self, domain_id: str) -> Optional[Domain]:
        """Get a domain by its ID."""
        for domain in self._domains:
            if domain.id == domain_id:
                return domain
        return None

    def get_domain_by_name(self, name: str) -> Optional[Domain]:
        """Get a domain by its name (case-insensitive)."""
        name_lower = name.lower()
        for domain in self._domains:
            if domain.name.lower() == name_lower:
                return domain
        return None

    def get_pairs_in_range(
        self,
        distance_min: float = 0.4,
        distance_max: float = 0.7,
    ) -> list[tuple[int, int, float]]:
        """Get all domain pairs within the specified distance range.

        Args:
            distance_min: Minimum cosine distance (inclusive)
            distance_max: Maximum cosine distance (inclusive)

        Returns:
            List of (index_a, index_b, distance) tuples
        """
        if self._distance_matrix is None:
            raise RuntimeError("Domain map not loaded. Call load() first.")

        n = len(self._domains)
        pairs = []

        for i in range(n):
            for j in range(i + 1, n):  # Upper triangle only
                dist = self._distance_matrix[i, j]
                if distance_min <= dist <= distance_max:
                    pairs.append((i, j, float(dist)))

        return pairs

    def get_random_collision(
        self,
        distance_min: float = 0.4,
        distance_max: float = 0.7,
        strategy: CollisionStrategy = CollisionStrategy.SEMANTIC_DISTANCE,
    ) -> Optional[CollisionPair]:
        """Generate a random collision pair within the fertile distance zone.

        Args:
            distance_min: Minimum cosine distance (0.4 = moderately related)
            distance_max: Maximum cosine distance (0.7 = quite different but not orthogonal)
            strategy: The collision strategy to record

        Returns:
            A CollisionPair, or None if no valid pairs exist
        """
        pairs = self.get_pairs_in_range(distance_min, distance_max)

        if not pairs:
            logger.warning(
                "no_valid_pairs",
                distance_min=distance_min,
                distance_max=distance_max,
            )
            return None

        # Random selection
        idx_a, idx_b, distance = random.choice(pairs)

        domain_a = self._domains[idx_a]
        domain_b = self._domains[idx_b]

        collision = CollisionPair(
            domain_a=domain_a,
            domain_b=domain_b,
            strategy=strategy,
            distance_score=distance,
        )

        logger.debug(
            "collision_generated",
            domain_a=domain_a.name,
            domain_b=domain_b.name,
            distance=distance,
        )

        return collision

    def get_multiple_collisions(
        self,
        n: int,
        distance_min: float = 0.4,
        distance_max: float = 0.7,
        unique: bool = True,
        cross_discipline_ratio: float = 0.7,
    ) -> list[CollisionPair]:
        """Generate multiple random collision pairs with cross-discipline enforcement.

        Args:
            n: Number of collisions to generate
            distance_min: Minimum cosine distance
            distance_max: Maximum cosine distance
            unique: If True, avoid duplicate pairs
            cross_discipline_ratio: Minimum fraction of cross-discipline pairs (0-1)

        Returns:
            List of CollisionPair objects
        """
        pairs = self.get_pairs_in_range(distance_min, distance_max)

        if not pairs:
            return []

        # Separate cross-discipline and same-discipline pairs
        cross_discipline_pairs = []
        same_discipline_pairs = []

        for idx_a, idx_b, distance in pairs:
            domain_a = self._domains[idx_a]
            domain_b = self._domains[idx_b]

            # Check if domains are from different parent disciplines
            parent_a = domain_a.parent_domain or "unknown"
            parent_b = domain_b.parent_domain or "unknown"

            if parent_a != parent_b:
                cross_discipline_pairs.append((idx_a, idx_b, distance))
            else:
                same_discipline_pairs.append((idx_a, idx_b, distance))

        logger.info(
            "pairs_by_type",
            cross_discipline=len(cross_discipline_pairs),
            same_discipline=len(same_discipline_pairs),
        )

        # Calculate how many of each type we need
        n_cross = int(n * cross_discipline_ratio)
        n_same = n - n_cross

        # Adjust if we don't have enough cross-discipline pairs
        if len(cross_discipline_pairs) < n_cross:
            logger.warning(
                "not_enough_cross_discipline",
                requested=n_cross,
                available=len(cross_discipline_pairs),
            )
            n_cross = len(cross_discipline_pairs)
            n_same = n - n_cross

        # Adjust if we don't have enough same-discipline pairs
        if len(same_discipline_pairs) < n_same:
            n_same = len(same_discipline_pairs)
            # Try to fill the gap with more cross-discipline
            remaining = n - n_cross - n_same
            n_cross = min(n_cross + remaining, len(cross_discipline_pairs))

        # Select pairs
        selected = []

        if n_cross > 0 and cross_discipline_pairs:
            if unique:
                selected.extend(random.sample(
                    cross_discipline_pairs,
                    min(n_cross, len(cross_discipline_pairs))
                ))
            else:
                selected.extend(random.choices(cross_discipline_pairs, k=n_cross))

        if n_same > 0 and same_discipline_pairs:
            if unique:
                selected.extend(random.sample(
                    same_discipline_pairs,
                    min(n_same, len(same_discipline_pairs))
                ))
            else:
                selected.extend(random.choices(same_discipline_pairs, k=n_same))

        # Shuffle to mix cross and same discipline pairs
        random.shuffle(selected)

        collisions = []
        for idx_a, idx_b, distance in selected:
            collision = CollisionPair(
                domain_a=self._domains[idx_a],
                domain_b=self._domains[idx_b],
                strategy=CollisionStrategy.SEMANTIC_DISTANCE,
                distance_score=distance,
            )
            collisions.append(collision)

        # Log stats
        actual_cross = sum(
            1 for c in collisions
            if (c.domain_a.parent_domain or "a") != (c.domain_b.parent_domain or "b")
        )
        logger.info(
            "collisions_generated",
            count=len(collisions),
            cross_discipline=actual_cross,
            cross_ratio=actual_cross / len(collisions) if collisions else 0,
        )

        return collisions

    def get_random_partner_for(
        self,
        domain_name: str,
        distance_min: float = 0.4,
        distance_max: float = 0.7,
    ) -> Optional[tuple[Domain, Optional[float]]]:
        """Pick a random partner for ``domain_name`` in the fertile zone.

        Supports the "surprise me" custom-collision mode: the user
        provides one domain, SPORE picks the partner.

        Behaviour:
          - If ``domain_name`` matches a known domain in the map, the
            pick is filtered by the cosine-distance range and the
            returned tuple's second element is the distance.
          - If the domain is unknown (free-form user input), falls back
            to a uniform random draw from the full domain set; the
            distance is returned as ``None`` since we can't measure it.
          - Returns ``None`` only if the domain map is empty.
        """
        if not self._domains:
            return None

        domain_a = self.get_domain_by_name(domain_name)
        if domain_a is not None and self._distance_matrix is not None:
            idx_a = self._get_domain_index(domain_a.id)
            if idx_a is not None:
                candidates: list[tuple[int, float]] = []
                for j in range(len(self._domains)):
                    if j == idx_a:
                        continue
                    dist = float(self._distance_matrix[idx_a, j])
                    if distance_min <= dist <= distance_max:
                        candidates.append((j, dist))
                if candidates:
                    j, dist = random.choice(candidates)
                    return self._domains[j], dist

        # Fallback: user's domain isn't mapped → uniform random pick.
        partner = random.choice(self._domains)
        if domain_a is not None and partner.id == domain_a.id:
            # Extremely unlikely but guard against picking self.
            candidates_rest = [d for d in self._domains if d.id != partner.id]
            if candidates_rest:
                partner = random.choice(candidates_rest)
        return partner, None

    def force_collision(
        self,
        domain_a_name: str,
        domain_b_name: str,
    ) -> Optional[CollisionPair]:
        """Force a specific collision between two named domains.

        Used for bootstrap testing with known discoveries.

        Args:
            domain_a_name: Name of first domain
            domain_b_name: Name of second domain

        Returns:
            CollisionPair or None if domains not found
        """
        domain_a = self.get_domain_by_name(domain_a_name)
        domain_b = self.get_domain_by_name(domain_b_name)

        if domain_a is None:
            logger.warning("domain_not_found", name=domain_a_name)
            return None

        if domain_b is None:
            logger.warning("domain_not_found", name=domain_b_name)
            return None

        distance = self.get_distance(domain_a, domain_b)

        return CollisionPair(
            domain_a=domain_a,
            domain_b=domain_b,
            strategy=CollisionStrategy.HISTORICAL_TEMPLATE,
            distance_score=distance,
        )

    @property
    def domains(self) -> list[Domain]:
        """Get all loaded domains."""
        return self._domains.copy()

    @property
    def domain_count(self) -> int:
        """Get the number of loaded domains."""
        return len(self._domains)

    def get_statistics(self) -> dict:
        """Get statistics about the domain map."""
        if self._distance_matrix is None:
            return {"loaded": False}

        # Get upper triangle distances (excluding diagonal)
        n = len(self._domains)
        distances = []
        for i in range(n):
            for j in range(i + 1, n):
                distances.append(self._distance_matrix[i, j])

        distances = np.array(distances)

        # Count pairs in fertile zone
        fertile_count = np.sum((distances >= 0.4) & (distances <= 0.7))

        return {
            "loaded": True,
            "domain_count": n,
            "total_pairs": len(distances),
            "fertile_pairs": int(fertile_count),
            "distance_min": float(distances.min()),
            "distance_max": float(distances.max()),
            "distance_mean": float(distances.mean()),
            "distance_std": float(distances.std()),
        }


# Global singleton
_domain_map: Optional[DomainMap] = None
_current_domain: Optional[str] = None


def get_domain_map(domain: str = "materials_science") -> DomainMap:
    """Get the global domain map instance (lazy-loaded).

    Args:
        domain: Domain to load - "materials_science" or "all_science"

    Returns:
        DomainMap instance for the specified domain
    """
    global _domain_map, _current_domain

    # Map domain names to file paths
    domain_files = {
        "materials_science": "data/domains/materials_science.json",
        "all_science": "data/domains/all_science.json",
    }

    domain_file = domain_files.get(domain, domain_files["materials_science"])

    # Reload if domain changed
    if _domain_map is None or _current_domain != domain:
        logger.info("loading_domain_map", domain=domain, file=domain_file)
        _domain_map = DomainMap(domains_path=domain_file)
        _domain_map.load()
        _current_domain = domain

    return _domain_map


def reset_domain_map() -> None:
    """Reset the global domain map (for testing)."""
    global _domain_map, _current_domain
    _domain_map = None
    _current_domain = None


def set_active_domain(domain: str) -> None:
    """Set the active domain for collision generation.

    Args:
        domain: Domain to use - "materials_science" or "all_science"
    """
    # This will reload the domain map with the new domain
    get_domain_map(domain)
