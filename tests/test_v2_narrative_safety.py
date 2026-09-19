"""v2 — garde des chemins (D-010) et chargement des seules clés LLM.

* Une base ou un fichier de sortie qui se résout sous
  ``~/Projects/spore-poc`` ou ``~/Projects/spore-web`` est refusé avant toute
  écriture, lien symbolique compris, sauf ``SPORE_V2_PRODUCTION=1`` exact.
* ``load_llm_keys`` ne pose que ``DEEPSEEK_API_KEY`` et
  ``ANTHROPIC_API_KEY`` depuis un fichier ``.env`` (ici synthétique, valeurs
  factices), ne renvoie que des noms et ne journalise aucune valeur.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_narrative_safety
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative import safety
from narrative.safety import (
    UnsafePathError,
    assert_safe_environment,
    assert_safe_write_path,
    load_llm_keys,
)

PROD_POC = Path("/home/baq/Projects/spore-poc")
PROD_WEB = Path("/home/baq/Projects/spore-web")


class PathGuardTests(unittest.TestCase):
    """Refus des chemins de production hors bascule."""

    def setUp(self) -> None:
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(safety.PRODUCTION_FLAG, None)

    def tearDown(self) -> None:
        self._env.stop()

    def test_production_paths_are_refused(self) -> None:
        for path in (
            PROD_POC / "data" / "spore.db",
            PROD_POC,
            PROD_WEB / "data" / "stats.json",
            PROD_POC / "outputs" / ".." / "data" / "x.db",
        ):
            with self.subTest(path=str(path)), self.assertRaises(UnsafePathError):
                assert_safe_write_path(path, what="db")

    def test_symlink_into_production_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "innocent"
            link.symlink_to(PROD_POC / "data")
            with self.assertRaises(UnsafePathError):
                assert_safe_write_path(link / "spore.db", what="db")

    def test_similar_prefix_is_not_production(self) -> None:
        # « spore-poc-old » n'est pas sous « spore-poc ».
        path = Path("/home/baq/Projects/spore-poc-old/data/x.db")
        self.assertEqual(assert_safe_write_path(path, what="db"), path)

    def test_work_paths_are_allowed(self) -> None:
        for path in (
            Path("/home/baq/Projects/spore-v2-data/dev-pipe.db"),
            Path("/home/baq/Projects/spore-v2-poc/data/spore.db"),
            Path("/home/baq/Projects/spore-v2/docs/v2/evidence/spend.json"),
        ):
            with self.subTest(path=str(path)):
                self.assertEqual(assert_safe_write_path(path, what="x"), path.resolve())

    def test_output_paths_are_checked_too(self) -> None:
        with self.assertRaises(UnsafePathError):
            assert_safe_environment(
                "/home/baq/Projects/spore-v2-data/dev.db",
                [Path("/tmp/ok.json"), PROD_WEB / "public" / "briefs" / "x.json"],
            )

    def test_only_the_exact_flag_allows_production(self) -> None:
        for value in ("0", "true", "yes", " 1", "1 "):
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, {safety.PRODUCTION_FLAG: value}),
                self.assertRaises(UnsafePathError),
            ):
                assert_safe_write_path(PROD_POC / "data" / "spore.db", what="db")
        with mock.patch.dict(os.environ, {safety.PRODUCTION_FLAG: "1"}):
            self.assertEqual(
                assert_safe_write_path(PROD_POC / "data" / "spore.db", what="db"),
                PROD_POC / "data" / "spore.db",
            )


class LlmKeysTests(unittest.TestCase):
    """Chargement filtré des clés depuis un ``.env`` synthétique."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env_file = Path(self._tmp.name) / ".env"
        self.env_file.write_text(
            "# commentaire\n"
            "DEEPSEEK_API_KEY=fake-deepseek-value\n"
            'export ANTHROPIC_API_KEY="fake-anthropic-value"\n'
            "STRIPE_SECRET_KEY=fake-stripe-value\n"
            "SPORE_DB_PATH=/home/baq/Projects/spore-poc/data/spore.db\n"
            "JWT_SECRET=fake-jwt\n",
            encoding="utf-8",
        )
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "STRIPE_SECRET_KEY", "SPORE_DB_PATH", "JWT_SECRET"):
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_only_llm_keys_are_loaded(self) -> None:
        # Une variable hors liste demandée explicitement reste ignorée.
        loaded = load_llm_keys(self.env_file, names=("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "STRIPE_SECRET_KEY"))
        self.assertEqual(loaded, ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"])
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "fake-deepseek-value")
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "fake-anthropic-value")
        for name in ("STRIPE_SECRET_KEY", "SPORE_DB_PATH", "JWT_SECRET"):
            self.assertNotIn(name, os.environ)

    def test_existing_values_are_kept_unless_override(self) -> None:
        os.environ["DEEPSEEK_API_KEY"] = "already-set"
        self.assertEqual(load_llm_keys(self.env_file), ["ANTHROPIC_API_KEY"])
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "already-set")
        self.assertIn("DEEPSEEK_API_KEY", load_llm_keys(self.env_file, override=True))

    def test_missing_file_loads_nothing(self) -> None:
        self.assertEqual(load_llm_keys(Path(self._tmp.name) / "absent.env"), [])

    def test_no_value_reaches_the_logs(self) -> None:
        records: list[dict] = []
        with mock.patch.object(safety.logger, "info", lambda event, **kw: records.append({"event": event, **kw})):
            load_llm_keys(self.env_file)
        self.assertTrue(records)
        self.assertNotIn("fake-", repr(records))


if __name__ == "__main__":
    unittest.main()
