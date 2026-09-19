"""v2 — thèmes (règle mécanique) et maillage des idées voisines.

* ``theme_tagger`` : domaine exact → ``parent_domain`` → repli ; un ou deux
  thèmes dédoublonnés ; index des parents sur les deux formes de cartes.
* Voisinage : sur des graphes synthétiques (aléatoires, en étoile, en
  grappes, petits) et sur un gabarit dérivé de la copie de base
  (``tests/fixtures/v2_neighbours_db_fixture.json``, 90 idées + 16 stubs) :
  chaque idée complète reçoit au moins 3 liens entrants, émet 3 à 5 liens,
  ne se lie jamais à elle-même ; chaque stub reçoit exactement 3 liens
  d'autres stubs ; aucun lien entre stubs et idées complètes ; résultat
  déterministe, quel que soit l'ordre d'entrée.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_narrative_mesh
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative.neighbours import (
    IdeaNode,
    NeighbourEdge,
    NeighbourSettings,
    compute_neighbours,
    default_score,
    inbound_counts,
    normalise_vector,
)
from narrative.themes import (
    ParentIndex,
    ThemeMapping,
    ThemesFileError,
    build_parent_index,
    load_theme_mapping,
    tag_domains,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v2_neighbours_db_fixture.json"
REPO = Path(__file__).resolve().parent.parent
SETTINGS = NeighbourSettings()


class ThemeTaggerTests(unittest.TestCase):
    """Règle mécanique du contrat de données."""

    def setUp(self) -> None:
        self.mapping = ThemeMapping(
            version="themes_test",
            slugs=("vivant", "matiere", "terre"),
            domains={"Epigenetics": "vivant"},
            parent_domains={"Physics": "matiere", "Biology": "vivant"},
            fallback="terre",
        )
        self.parents = ParentIndex()
        self.parents.add("Quantum Mechanics", "Physics", "domain_map")
        self.parents.add("Genomics", "Biology", "domain_map")

    def test_exact_then_parent_then_fallback(self) -> None:
        self.assertEqual(tag_domains(["Epigenetics"], self.mapping, self.parents), [("vivant", "domain")])
        self.assertEqual(
            tag_domains(["Quantum Mechanics"], self.mapping, self.parents), [("matiere", "parent_domain")]
        )
        self.assertEqual(tag_domains(["Unknown Field"], self.mapping, self.parents), [("terre", "fallback")])

    def test_one_theme_per_domain_deduplicated(self) -> None:
        both = tag_domains(["Quantum Mechanics", "Epigenetics"], self.mapping, self.parents)
        self.assertEqual(both, [("matiere", "parent_domain"), ("vivant", "domain")])
        same = tag_domains(["Epigenetics", "Genomics"], self.mapping, self.parents)
        self.assertEqual(same, [("vivant", "domain")])
        # Au plus deux domaines (une collision), repli si aucun.
        self.assertEqual(len(tag_domains(["Epigenetics", "Quantum Mechanics", "Unknown"], self.mapping, self.parents)), 2)
        self.assertEqual(tag_domains([], self.mapping, self.parents), [("terre", "fallback")])

    def test_versioned_file_is_valid(self) -> None:
        # Vaut pour le fichier provisoire comme pour le fichier figé qui le
        # remplacera : aucune assertion sur le nombre de thèmes.
        mapping = load_theme_mapping(REPO / "config" / "narrative" / "themes_v1.json")
        self.assertEqual(mapping.version, "themes_v1")
        self.assertTrue(mapping.slugs)
        self.assertEqual(len(mapping.slugs), len(set(mapping.slugs)))
        self.assertIn(mapping.fallback, mapping.slugs)
        declared = set(mapping.slugs)
        self.assertTrue(set(mapping.domains.values()) <= declared)
        self.assertTrue(set(mapping.parent_domains.values()) <= declared)

    def test_unknown_slug_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "themes.json"
            path.write_text(
                json.dumps({"version": "v", "themes": [{"slug": "a", "position": 1}], "domains": {"X": "b"}, "fallback": "a"}),
                encoding="utf-8",
            )
            with self.assertRaises(ThemesFileError):
                load_theme_mapping(path)

    def test_parent_index_reads_both_domain_map_shapes(self) -> None:
        index = build_parent_index(
            [REPO / "data" / "domains" / "all_science.json", REPO / "data" / "domains" / "materials_science.json"]
        )
        self.assertEqual(index.get("Quantum Mechanics"), "Physics")
        self.assertEqual(index.get("Crystallography"), "materials_science")
        self.assertEqual(index.get("Physics"), "Physics")


def check_mesh(test: unittest.TestCase, ideas: list[IdeaNode], edges: list[NeighbourEdge]) -> None:
    """Invariants du maillage (D-008, D-015).

    Args:
        test: Cas de test.
        ideas: Idées.
        edges: Liens calculés.
    """
    full = {idea.brief_id for idea in ideas if not idea.is_stub}
    stubs = {idea.brief_id for idea in ideas if idea.is_stub}
    pairs = [(edge.brief_id, edge.neighbour_id) for edge in edges]
    test.assertEqual(len(pairs), len(set(pairs)), "lien en double")
    test.assertFalse([p for p in pairs if p[0] == p[1]], "lien vers soi-même")
    for source, target in pairs:
        test.assertEqual(source in stubs, target in stubs, "lien entre stub et idée complète")
    inbound = inbound_counts(edges)
    outbound = Counter(edge.brief_id for edge in edges)
    if len(full) >= 4:
        test.assertGreaterEqual(min(inbound[item] for item in full), 3)
        test.assertGreaterEqual(min(outbound[item] for item in full), 3)
    if len(full) >= 13:
        test.assertLessEqual(max(outbound[item] for item in full), 5)
    if len(stubs) >= 4:
        test.assertEqual({inbound[item] for item in stubs}, {3})
        test.assertEqual({outbound[item] for item in stubs}, {3})
    # Rangs consécutifs à partir de 1, par idée.
    ranks: dict[str, list[int]] = {}
    for edge in edges:
        ranks.setdefault(edge.brief_id, []).append(edge.rank)
    for values in ranks.values():
        test.assertEqual(sorted(values), list(range(1, len(values) + 1)))


def random_ideas(seed: int, n_full: int, n_stubs: int, dim: int = 8, clustered: bool = False) -> list[IdeaNode]:
    """Idées aléatoires reproductibles.

    Args:
        seed: Graine.
        n_full: Idées complètes.
        n_stubs: Stubs.
        dim: Dimension des vecteurs.
        clustered: Vecteurs groupés autour de trois centres (entrants très inégaux).

    Returns:
        Idées.
    """
    rng = random.Random(seed)
    centres = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(3)]
    ideas = []
    for index in range(n_full):
        if clustered:
            centre = centres[index % 3]
            raw = [c + rng.gauss(0, 0.05) for c in centre]
        else:
            raw = [rng.gauss(0, 1) for _ in range(dim)]
        vector = normalise_vector(raw) if rng.random() > 0.05 else None
        ideas.append(IdeaNode(f"F{index:03d}", False, (f"D{rng.randrange(6)}",), (f"T{rng.randrange(3)}",), vector))
    ideas.extend(IdeaNode(f"S{index:03d}", True) for index in range(n_stubs))
    return ideas


class MeshTests(unittest.TestCase):
    """Invariants sur graphes synthétiques."""

    def test_random_graphs(self) -> None:
        for seed in range(12):
            for n_full in (4, 5, 7, 13, 30, 90):
                with self.subTest(seed=seed, n=n_full):
                    ideas = random_ideas(seed, n_full, n_stubs=seed % 7)
                    check_mesh(self, ideas, compute_neighbours(ideas, SETTINGS))

    def test_clustered_graphs(self) -> None:
        for seed in range(6):
            with self.subTest(seed=seed):
                ideas = random_ideas(seed, 60, 16, clustered=True)
                check_mesh(self, ideas, compute_neighbours(ideas, SETTINGS))

    def test_hub_graph(self) -> None:
        # Tout le monde est « proche » des mêmes quatre idées : sans correction,
        # les autres ne recevraient aucun lien.
        def hub_score(a: IdeaNode, b: IdeaNode) -> tuple[float, str]:
            hubs = {"F000", "F001", "F002", "F003"}
            return (0.9 if a.brief_id in hubs or b.brief_id in hubs else 0.1), "domain_embedding"

        ideas = [IdeaNode(f"F{index:03d}", False) for index in range(40)]
        edges = compute_neighbours(ideas, SETTINGS, score_fn=hub_score)
        check_mesh(self, ideas, edges)
        self.assertIn("inbound_patch", {edge.method for edge in edges})

    def test_no_embedding_falls_back_on_shared_domains_and_themes(self) -> None:
        ideas = [
            IdeaNode("A", False, ("Hydrology",), ("terre",)),
            IdeaNode("B", False, ("Hydrology",), ("terre",)),
            IdeaNode("C", False, ("Optics",), ("terre",)),
            IdeaNode("D", False, ("Optics",), ("matiere",)),
            IdeaNode("E", False, ("Genomics",), ("vivant",)),
        ]
        self.assertEqual(default_score(ideas[0], ideas[1])[1], "domain")
        self.assertEqual(default_score(ideas[0], ideas[2])[1], "theme")
        check_mesh(self, ideas, compute_neighbours(ideas, SETTINGS))

    def test_tiny_graphs_do_not_loop(self) -> None:
        for n in range(4):
            ideas = [IdeaNode(f"F{index}", False) for index in range(n)] + [IdeaNode(f"S{index}", True) for index in range(n)]
            edges = compute_neighbours(ideas, SETTINGS)
            self.assertEqual(len({(e.brief_id, e.neighbour_id) for e in edges}), len(edges))

    def test_deterministic_and_order_independent(self) -> None:
        ideas = random_ideas(3, 50, 10)
        first = compute_neighbours(ideas, SETTINGS)
        shuffled = list(ideas)
        random.Random(1).shuffle(shuffled)
        self.assertEqual(first, compute_neighbours(shuffled, SETTINGS))


class DatabaseFixtureTests(unittest.TestCase):
    """Gabarit dérivé en lecture seule de la copie de base du 19/09."""

    def test_min_inbound_on_db_fixture(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        full = data["full"]
        methods = {"e": "domain_embedding", "d": "domain", "t": "theme"}
        scores: dict[tuple[str, str], tuple[float, str]] = {}
        position = 0
        for a in range(len(full)):
            for b in range(a + 1, len(full)):
                value = (data["scores_upper"][position], methods[data["methods_upper"][position]])
                scores[(full[a], full[b])] = value
                scores[(full[b], full[a])] = value
                position += 1

        ideas = [IdeaNode(item, False) for item in full] + [IdeaNode(item, True) for item in data["stubs"]]
        edges = compute_neighbours(ideas, SETTINGS, score_fn=lambda a, b: scores[(a.brief_id, b.brief_id)])
        check_mesh(self, ideas, edges)
        self.assertEqual(len(full), 90)
        self.assertEqual(len(data["stubs"]), 16)
        inbound = inbound_counts(edges)
        self.assertGreaterEqual(min(inbound[item] for item in full), 3)


if __name__ == "__main__":
    unittest.main()
