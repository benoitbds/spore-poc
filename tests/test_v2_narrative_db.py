"""v2 — migration additive des tables ``v2_*`` (``storage/narrative_db.py``).

Couvre : idempotence (deux passages), tables v1 intactes (schéma et lignes),
index partiel « un seul récit publié par brief et par langue », unicité des
tentatives, statuts finaux non réécrits par un rejet tardif.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_narrative_db
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import narrative_db
from tests.test_s11_llm_contract import TempDatabase
from tests.v2_narrative_support import insert_brief


def schema_snapshot(conn: sqlite3.Connection) -> dict[str, object]:
    """Schéma des objets non-``v2_`` : DDL et colonnes de chaque table.

    Args:
        conn: Connexion.

    Returns:
        Instantané comparable.
    """
    objects = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'v2_%' AND name NOT LIKE 'ux_v2_%' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    columns = {
        row[1]: conn.execute(f"PRAGMA table_info({row[1]})").fetchall()
        for row in objects
        if row[0] == "table"
    }
    return {"objects": [tuple(row) for row in objects], "columns": columns}


class MigrationTests(TempDatabase):
    """``ensure_narrative_schema`` sur une base v1."""

    def connect(self) -> sqlite3.Connection:
        """Connexion à la base temporaire.

        Returns:
            Connexion.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_idempotent_twice(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            first = conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name LIKE '%v2_%' ORDER BY name"
            ).fetchall()
            narrative_db.ensure_narrative_schema(conn)
            second = conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name LIKE '%v2_%' ORDER BY name"
            ).fetchall()
            self.assertTrue(narrative_db.narrative_schema_present(conn))
        self.assertEqual([tuple(r) for r in first], [tuple(r) for r in second])
        names = {row[1] for row in first}
        self.assertTrue(set(narrative_db.NARRATIVE_TABLES) <= names)
        self.assertIn("ux_v2_stories_one_published", names)
        self.assertIn("ux_v2_stories_brief_lang_attempt", names)

    def test_v1_tables_untouched(self) -> None:
        insert_brief(self.db_path, "SPR-2026-AAAA")
        conn = self.connect()
        try:
            before = schema_snapshot(conn)
            rows_before = conn.execute("SELECT * FROM briefs ORDER BY id").fetchall()
        finally:
            conn.close()

        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            narrative_db.ensure_narrative_schema(conn)

        conn = self.connect()
        try:
            after = schema_snapshot(conn)
            rows_after = conn.execute("SELECT * FROM briefs ORDER BY id").fetchall()
        finally:
            conn.close()
        self.assertEqual(before, after)
        self.assertEqual([tuple(r) for r in rows_before], [tuple(r) for r in rows_after])

    def test_one_published_story_per_brief_and_lang(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=1, status="rejected")
            narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=2, status="published")
            with self.assertRaises(sqlite3.IntegrityError):
                narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=3, status="published")
            # Autre langue, autre brief : autorisés.
            narrative_db.insert_story(conn, brief_id="B1", lang="en", attempt=1, status="published")
            narrative_db.insert_story(conn, brief_id="B2", lang="fr", attempt=1, status="published")
            # Une mise à jour ne contourne pas l'index partiel.
            draft = narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=3, status="draft")
            with self.assertRaises(sqlite3.IntegrityError):
                narrative_db.update_story(conn, draft, status="published")

    def test_attempt_is_unique_per_brief_and_lang(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=1, status="rejected")
            with self.assertRaises(sqlite3.IntegrityError):
                narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=1, status="rejected")
            self.assertEqual(narrative_db.next_attempt(conn, "B1", "fr"), 2)
            self.assertEqual(narrative_db.next_attempt(conn, "B1", "en"), 1)

    def test_invalid_values_are_refused(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            with self.assertRaises(ValueError):
                narrative_db.insert_story(conn, brief_id="B1", lang="de", attempt=1, status="draft")
            with self.assertRaises(ValueError):
                narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=1, status="live")
            with self.assertRaises(ValueError):
                narrative_db.upsert_brief_hypothesis(conn, "B1", "H1", "guess")
            with self.assertRaises(ValueError):
                narrative_db.replace_all_neighbours(conn, [("B1", "B1", 1, 0.5, "theme")])

    def test_late_rejection_never_overrides_a_decision(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            published = narrative_db.insert_story(conn, brief_id="B1", lang="fr", attempt=1, status="draft")
            self.assertTrue(narrative_db.finalize_story(conn, published, status="published"))
            draft = narrative_db.insert_story(conn, brief_id="B1", lang="en", attempt=1, status="draft")
            self.assertEqual(narrative_db.reject_open_drafts(conn, "B1", "layer_timeout"), 1)
            # Le garde qui termine après le rejet ne republie pas.
            self.assertFalse(narrative_db.finalize_story(conn, draft, status="published"))
            self.assertEqual(narrative_db.get_story(conn, published)["status"], "published")
            closed = narrative_db.get_story(conn, draft)
            self.assertEqual(closed["status"], "rejected")
            self.assertIn("layer_timeout", closed["guard_report_json"])

    def test_cost_ledger_sums_by_label(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            for label, cost in (("backfill", 0.25), ("pipeline", 1.0), ("canary", 0.5)):
                narrative_db.insert_llm_cost(
                    conn, run_label=label, brief_id="B1", node="story_writer", model="m",
                    tokens_in=1, tokens_out=1, cost_usd=cost,
                )
            self.assertAlmostEqual(narrative_db.sum_costs(conn, ["backfill", "canary"]), 0.75)
            self.assertAlmostEqual(narrative_db.sum_costs(conn), 1.75)
            self.assertEqual(narrative_db.costs_by_label(conn)["pipeline"], 1.0)


if __name__ == "__main__":
    unittest.main()
