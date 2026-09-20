"""Bout-en-bout réel de la couche narrative (``scripts.v2.e2e_narrative``).

Le passage lui-même appelle le modèle et n'est pas rejouable en test. Ce qui
l'est, et qui porte tout le risque de la preuve, l'est ici :

- le **choix du brief**, contrôlé avant le moindre appel LLM (un brief
  inéligible ferait rendre ``brief_not_published_full`` à la couche et le
  passage serait perdu) ;
- la **relecture en base**, seule source du fichier de preuve, puisque le nœud
  câblé avale ses exceptions et rend toujours ``{}`` ;
- l'**exactitude des coûts** : ``cost_usd`` doit valoir ``SUM(cost_usd)`` sur
  les lignes, au dixième de nanodollar près, sinon la preuve ne se recalcule
  pas depuis la base ;
- le **contrôle d'identité du nœud** : le script doit refuser de se présenter
  comme un bout-en-bout si ``graph/post_fire_pipeline.py`` ne câble plus
  ``node_narrative_layer``.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.v2.calibrate_narrative import CALIBRATION_SET
from scripts.v2.e2e_narrative import (
    E2EError,
    _wired_node_is_the_graph_node,
    pick_brief,
    read_costs,
    read_stories,
)
from storage import narrative_db
from tests.v2_narrative_support import insert_brief

SCHEMA = """
CREATE TABLE briefs (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT,
    status TEXT,
    is_stub INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    panel_consensus_score REAL,
    sharpened_data TEXT,
    vulgarization_data TEXT,
    vulgarization_data_en TEXT,
    grounding_data TEXT,
    panel_data TEXT
);
"""


class E2ESelectionTests(unittest.TestCase):
    """Choix du brief et relecture de la base."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "e2e.db"
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def dated(self, brief_id: str, created_at: str, **changes: object) -> None:
        """Insère un brief et lui impose une date.

        Args:
            brief_id: Identifiant.
            created_at: Date de création.
            **changes: Arguments passés à ``insert_brief``.
        """
        insert_brief(self.db, brief_id, **changes)  # type: ignore[arg-type]
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE briefs SET created_at = ? WHERE id = ?", (created_at, brief_id))
            conn.commit()
        finally:
            conn.close()

    def test_most_recent_eligible_is_chosen(self) -> None:
        self.dated("SPR-2026-0001", "2026-01-01 00:00:00")
        self.dated("SPR-2026-0002", "2026-09-01 00:00:00")
        chosen = pick_brief(self.db, None)
        self.assertEqual(chosen["brief_id"], "SPR-2026-0002")
        self.assertEqual(chosen["candidates"], 2)
        self.assertEqual(
            chosen["domains"], ["Hydrology", "Materials Chemistry"]
        )

    def test_calibration_set_is_excluded(self) -> None:
        self.dated(CALIBRATION_SET[0], "2026-09-01 00:00:00")
        self.dated("SPR-2026-0003", "2026-01-01 00:00:00")
        chosen = pick_brief(self.db, None)
        self.assertEqual(chosen["brief_id"], "SPR-2026-0003")
        self.assertEqual(chosen["candidates"], 1)
        with self.assertRaises(E2EError):
            pick_brief(self.db, CALIBRATION_SET[0])

    def test_stub_pending_and_unlinked_are_excluded(self) -> None:
        self.dated("SPR-2026-0004", "2026-09-04 00:00:00", is_stub=1)
        self.dated("SPR-2026-0005", "2026-09-05 00:00:00", status="pending")
        self.dated("SPR-2026-0006", "2026-09-06 00:00:00")
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE briefs SET hypothesis_id = NULL WHERE id = 'SPR-2026-0006'")
            conn.commit()
        finally:
            conn.close()
        self.dated("SPR-2026-0007", "2026-01-01 00:00:00")
        chosen = pick_brief(self.db, None)
        self.assertEqual(chosen["brief_id"], "SPR-2026-0007")
        self.assertEqual(chosen["candidates"], 1)

    def test_brief_with_a_story_is_excluded(self) -> None:
        self.dated("SPR-2026-0008", "2026-09-08 00:00:00")
        self.dated("SPR-2026-0009", "2026-01-01 00:00:00")
        with narrative_db.connect(self.db) as conn:
            narrative_db.ensure_narrative_schema(conn)
            narrative_db.insert_story(
                conn, brief_id="SPR-2026-0008", lang="fr", attempt=1, status="rejected"
            )
        chosen = pick_brief(self.db, None)
        self.assertEqual(chosen["brief_id"], "SPR-2026-0009")

    def test_existing_story_allowed_when_rebuilding_evidence(self) -> None:
        # ``--evidence-only`` relit un passage déjà fait : le brief porte alors
        # forcément des récits, et l'exclusion ne doit pas s'appliquer.
        self.dated("SPR-2026-0012", "2026-09-12 00:00:00")
        with narrative_db.connect(self.db) as conn:
            narrative_db.ensure_narrative_schema(conn)
            narrative_db.insert_story(
                conn, brief_id="SPR-2026-0012", lang="fr", attempt=1, status="published"
            )
        with self.assertRaises(E2EError):
            pick_brief(self.db, "SPR-2026-0012")
        chosen = pick_brief(self.db, "SPR-2026-0012", allow_existing_story=True)
        self.assertEqual(chosen["brief_id"], "SPR-2026-0012")

    def test_missing_schema_means_no_story(self) -> None:
        # Base fraîchement copiée : ``v2_stories`` n'existe pas encore, et la
        # lecture est en lecture seule (elle ne doit pas tenter de la créer).
        self.dated("SPR-2026-0010", "2026-09-10 00:00:00")
        self.assertEqual(pick_brief(self.db, None)["brief_id"], "SPR-2026-0010")

    def test_no_eligible_brief_refused(self) -> None:
        with self.assertRaises(E2EError):
            pick_brief(self.db, None)

    def test_unknown_imposed_brief_refused(self) -> None:
        self.dated("SPR-2026-0011", "2026-09-11 00:00:00")
        with self.assertRaises(E2EError) as caught:
            pick_brief(self.db, "SPR-2026-FFFF")
        self.assertIn("éligible", str(caught.exception))


class E2EReadbackTests(unittest.TestCase):
    """Relecture des récits et des coûts, seule source de la preuve."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "e2e.db"
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        with narrative_db.connect(self.db) as conn:
            narrative_db.ensure_narrative_schema(conn)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def story(self, **fields: object) -> int:
        """Insère une tentative.

        Args:
            **fields: Colonnes de ``v2_stories``.

        Returns:
            Identifiant de la ligne.
        """
        with narrative_db.connect(self.db) as conn:
            return narrative_db.insert_story(conn, **fields)

    def cost(self, node: str, cost_usd: float, *, brief_id: str = "SPR-2026-0001") -> None:
        """Insère une ligne de coût.

        Args:
            node: Nœud.
            cost_usd: Coût.
            brief_id: Brief.
        """
        with narrative_db.connect(self.db) as conn, conn:
            conn.execute(
                "INSERT INTO v2_llm_costs (run_label, brief_id, node, model, tokens_in, "
                "tokens_out, cost_usd, created_at) VALUES ('e2e', ?, ?, 'deepseek-flash', "
                "1, 1, ?, '2026-09-20T00:00:00Z')",
                (brief_id, node, cost_usd),
            )

    def test_published_attempt_wins_over_later_rejects(self) -> None:
        self.story(
            brief_id="SPR-2026-0001",
            lang="fr",
            attempt=1,
            status="rejected",
            writer_model="deepseek-flash",
            guard_model="deepseek-flash",
            prompt_version="story_writer_v2",
            guard_prompt_version="story_guard_v1",
        )
        published = self.story(
            brief_id="SPR-2026-0001",
            lang="fr",
            attempt=2,
            status="published",
            writer_model="deepseek-flash",
            guard_model="deepseek-flash",
            prompt_version="story_writer_v2",
            guard_prompt_version="story_guard_v1",
        )
        read = read_stories(self.db, "SPR-2026-0001")
        self.assertEqual(read["statuses"]["fr"], "published")
        self.assertEqual(read["story_ids"]["fr"], published)
        self.assertEqual(read["attempts"]["fr"], 2)
        self.assertEqual(read["writer_model"], "deepseek-flash")
        self.assertEqual(read["prompt_versions"]["guard"], ["story_guard_v1"])

    def test_all_attempts_rejected_is_reported_as_rejected(self) -> None:
        for attempt in (1, 2, 3):
            self.story(
                brief_id="SPR-2026-0001", lang="en", attempt=attempt, status="rejected"
            )
        read = read_stories(self.db, "SPR-2026-0001")
        self.assertEqual(read["statuses"]["en"], "rejected")
        self.assertEqual(read["attempts"]["en"], 3)
        # Aucune tentative française : la preuve le dit, elle n'invente rien.
        self.assertIsNone(read["statuses"]["fr"])
        self.assertIsNone(read["story_ids"]["fr"])

    def test_models_are_scalars_unless_the_fallback_fired(self) -> None:
        # ``v2_stories.writer_model`` est une colonne TEXT : un passage normal
        # rend une chaîne. La liste ne doit apparaître que si deux modèles
        # différents ont réellement écrit — un fait à ne pas aplatir.
        self.story(
            brief_id="SPR-2026-0001",
            lang="fr",
            attempt=1,
            status="published",
            writer_model="deepseek-flash",
        )
        self.assertEqual(read_stories(self.db, "SPR-2026-0001")["writer_model"], "deepseek-flash")
        self.story(
            brief_id="SPR-2026-0001",
            lang="en",
            attempt=1,
            status="published",
            writer_model="claude-sonnet-5",
        )
        self.assertEqual(
            read_stories(self.db, "SPR-2026-0001")["writer_model"],
            ["claude-sonnet-5", "deepseek-flash"],
        )

    def test_no_story_at_all(self) -> None:
        read = read_stories(self.db, "SPR-2026-0001")
        self.assertEqual(read["statuses"], {"fr": None, "en": None})
        self.assertEqual(read["attempts"], {"fr": 0, "en": 0})
        self.assertIsNone(read["writer_model"])

    def test_cost_matches_the_sum_over_the_rows(self) -> None:
        values = [0.000365330, 0.0000817684, 0.0002745652, 0.0002748452, 0.0002748452]
        for index, value in enumerate(values):
            self.cost("story_writer" if index == 0 else "story_translate", value)
        read = read_costs(self.db, "SPR-2026-0001", 0)
        with narrative_db.connect(self.db, readonly=True) as conn:
            total = float(
                conn.execute(
                    "SELECT SUM(cost_usd) FROM v2_llm_costs WHERE brief_id = 'SPR-2026-0001'"
                ).fetchone()[0]
            )
        self.assertAlmostEqual(read["cost_usd"], total, places=10)
        self.assertEqual(read["llm_calls"], 5)
        self.assertEqual(read["run_labels"], ["e2e"])

    def test_baseline_excludes_earlier_costs(self) -> None:
        self.cost("story_writer", 1.0)
        with narrative_db.connect(self.db, readonly=True) as conn:
            baseline = narrative_db.max_cost_id(conn)
        self.cost("story_writer", 0.5)
        read = read_costs(self.db, "SPR-2026-0001", baseline)
        self.assertAlmostEqual(read["cost_usd"], 0.5, places=10)
        self.assertEqual(read["llm_calls"], 1)

    def test_costs_of_other_briefs_are_excluded(self) -> None:
        self.cost("story_writer", 0.25)
        self.cost("story_writer", 9.0, brief_id="SPR-2026-9999")
        read = read_costs(self.db, "SPR-2026-0001", 0)
        self.assertAlmostEqual(read["cost_usd"], 0.25, places=10)


class WiredNodeTests(unittest.TestCase):
    """Le nœud appelé est bien celui que le graphe câble."""

    def test_pipeline_still_wires_the_node(self) -> None:
        self.assertTrue(_wired_node_is_the_graph_node())


if __name__ == "__main__":
    unittest.main()
