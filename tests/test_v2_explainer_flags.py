"""v2 — nœud ``explainer_flags`` et table ``v2_vocab_flags`` (D-017, sans LLM).

Couvre : règle de validité du titre (vecteurs de ``ARCHITECTURE_INFO.md``
§7.2) ; passes vocabulaire (rangs, pronoms, orthographes américaines,
littéraux admis), statut (portée, exclusion conditionnelle) et structure ;
positions en points de code du champ brut malgré la normalisation ; écriture
sans recopier le mot détecté, idempotente, statut posé par l'opérateur
conservé, lignes périmées réconciliées ; nœud dans la queue mécanique du
sous-graphe (récits désactivés compris) et dans la queue de secours ;
backfill ``--no-stories`` ; relevé de la copie de base du 19/09
(``LIGNE_EDITORIALE.md`` §8) ; copies octet pour octet des fichiers de règles.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_explainer_flags
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import sys
import unicodedata
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative import explainer_flags as ef
from narrative import graph as narrative_graph
from narrative.config import DEFAULT_CONFIG_PATH, load_config, override_config
from narrative.graph import run_narrative_layer
from scripts.v2 import backfill_narrative
from storage import narrative_db
from tests.test_s11_llm_contract import TempDatabase
from tests.v2_narrative_support import FakeScript, insert_brief, make_config, rows, use_script

REPO = Path(__file__).resolve().parent.parent
RULE_FILES = ("vocab_rules.json", "status_rules.json", "vocab_allow.txt")

#: Copie de base du 19/09 (D-004), sur laquelle le relevé du §8 a été fait.
BASE_DB = Path(
    os.environ.get(
        "SPORE_V2_BASE_DB", "/home/baq/Projects/spore-v2-data/spore.base-20260919T074424Z.db"
    )
)

SOFT_HYPHEN = chr(0x00AD)
COMBINING_ACUTE = chr(0x0301)

BRIEF = "SPR-2026-0F01"

VULG_FR: dict[str, Any] = {
    "title_fr": "Et si notre digue respirait",
    "hypothesis_in_brief": "Une idée simple sur un mur poreux.",
    "why_it_matters": "Pour le découvrir, il faut une maquette.",
    "imagine_that": "Imaginez un mur qui laisse passer l'eau.",
    "concretely": {
        "intro": "Trois phases.",
        "phase1": "On mesure la porosité.",
        "phase2": "On compare deux mortiers.",
        "phase3": "Si l'effet était confirmé, on passerait à l'échelle.",
    },
    "reviewers_say": "Les relecteurs doutent de la tenue dans le temps.",
}

VULG_EN: dict[str, Any] = {
    "title": "A breathing wall\nThis finding offers a result that has been verified.",
    "hypothesis_in_brief": "A simple idea about a porous wall.",
    "why_it_matters": "It would matter to the World Health Organization.",
    "imagine_that": "Imagine a wall that lets water through.",
    "concretely": {
        "intro": "Three phases.",
        "phase1": "The porosity is measured.",
        "phase2": "A minimal test was conducted with eight samples.",
        "phase3": "If the effect were confirmed, a larger trial would follow.",
    },
    "reviewers_say": "Reviewers doubt it; the method was tested elsewhere.",
}

#: Mots détectés dans les vulgarisations ci-dessus : aucun ne doit entrer en base.
DETECTED_WORDS = ("notre", "découvrir", "Organization", "conducted", "finding", "verified")


def set_vulgarisation(db_path: Path, brief_id: str, fr: Any, en: Any) -> None:
    """Remplace la vulgarisation FR et EN d'un brief.

    Args:
        db_path: Base.
        brief_id: Brief.
        fr: ``vulgarization_data`` (``None`` pour l'effacer).
        en: ``vulgarization_data_en``.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE briefs SET vulgarization_data = ?, vulgarization_data_en = ? WHERE id = ?",
            (
                json.dumps(fr, ensure_ascii=False) if fr is not None else None,
                json.dumps(en, ensure_ascii=False) if en is not None else None,
                brief_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def flag_rows(db_path: Path, brief_id: str | None = None) -> list[dict[str, Any]]:
    """Lignes ``v2_vocab_flags``, ordonnées.

    Args:
        db_path: Base.
        brief_id: Brief, ou tous.

    Returns:
        Lignes.
    """
    sql = 'SELECT id, brief_id, lang, field, kind, start, "end", rule_idx, field_sha256, status, detected_at FROM v2_vocab_flags'
    params: list[Any] = []
    if brief_id:
        sql += " WHERE brief_id = ?"
        params.append(brief_id)
    return rows(db_path, sql + " ORDER BY brief_id, lang, field, start, kind", params)


def summary_keys(flags: list[ef.Flag]) -> set[tuple[str, str, str]]:
    """``(langue, champ, sorte)`` des signalements."""
    return {(flag.lang, flag.field, flag.kind) for flag in flags}


class TitleRuleTests(unittest.TestCase):
    """Vecteurs de test de ``ARCHITECTURE_INFO.md`` §7.2."""

    def test_title_vectors_of_section_7_2(self) -> None:
        first = "A reversible molecular clamp: palladium as a chemical lock"
        self.assertEqual(len(first), 58)
        cut = ef.level2_title(first + "\n" + "x" * 710)
        self.assertEqual((cut.retained, cut.cut, cut.structure), (first, 58, True))

        for size in (117, 125, 140):
            line = "t" * size
            self.assertEqual(ef.level2_title(line), ef.TitleCut(line, 0, None))

        too_long = ef.level2_title("t" * 141)
        self.assertEqual((too_long.retained, too_long.cut), (None, 140))
        long_then_paragraph = ef.level2_title("t" * 150 + "\nUn paragraphe.")
        self.assertEqual((long_then_paragraph.retained, long_then_paragraph.cut), (None, 140))

        self.assertEqual(ef.level2_title("Titre\n"), ef.TitleCut("Titre", 0, None))
        for empty in ("", "   \n  ", None, 42):
            self.assertEqual(ef.level2_title(empty), ef.TitleCut(None, 0, None))

        # Blancs de bord : la position de la ligne et de la coupe renvoie au champ brut.
        indented = ef.level2_title("  Titre\r\nsuite")
        self.assertEqual((indented.retained, indented.offset, indented.cut), ("Titre", 2, 7))
        separator = ef.level2_title("Titre" + chr(0x2028) + "suite")
        self.assertEqual((separator.retained, separator.cut), ("Titre", 5))


class DetectionTests(unittest.TestCase):
    """Passes sur des champs synthétiques, règles réelles du dépôt."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.rules = ef.load_rules(cls.config)
        cls.vocab_rules = json.loads(cls.config.vocab_rules_path.read_text(encoding="utf-8"))
        cls.status_rules = json.loads(cls.config.status_rules_path.read_text(encoding="utf-8"))

    def en_rank(self, rule_id: str) -> int:
        """Rang ``rule_idx`` d'un motif anglais (``en`` puis ``en_us_spelling``)."""
        ordered = [*self.vocab_rules["en"], *self.vocab_rules["en_us_spelling"]]
        return next(index for index, item in enumerate(ordered) if item["id"] == rule_id)

    def test_vocab_fr_positions_and_rule_index(self) -> None:
        raw = "Dans notre cas, la colonie ne découvre rien."
        flags = ef.detect_field("fr", "imagine_that", raw, self.rules)
        self.assertEqual([flag.kind for flag in flags], ["vocab", "vocab"])
        self.assertEqual([raw[flag.start : flag.end] for flag in flags], ["notre", "découvre"])
        ids = [self.vocab_rules["fr"][flag.rule_idx]["id"] for flag in flags]
        self.assertEqual(ids, ["notre / nos (éditorial)", "découverte / découvrir"])
        self.assertEqual({flag.field_sha256 for flag in flags}, {ef.field_sha256(raw)})

    def test_vocab_en_pronouns_and_us_spelling_ranks(self) -> None:
        raw = "In our model the US grid shows odd behavior; let us see."
        flags = ef.detect_field("en", "why_it_matters", raw, self.rules)
        found = {(raw[flag.start : flag.end], flag.rule_idx) for flag in flags}
        self.assertEqual(
            found,
            {
                ("our", self.en_rank("our / ours (editorial)")),
                ("behavior", self.en_rank("behavior*")),
                ("us", self.en_rank("us (editorial)")),
            },
        )
        # Le pays (« US » en capitales) n'est pas le pronom ; « trust » ne contient pas le mot « us ».
        self.assertEqual(ef.detect_field("en", "why_it_matters", "The US trust it.", self.rules), [])

    def test_allow_literals_are_removed_before_the_search(self) -> None:
        raw = "It would matter to the World Health Organization, and to any organization."
        rules = ef.build_rules(self.vocab_rules, self.status_rules, ["World Health Organization"])
        flags = ef.detect_field("en", "why_it_matters", raw, rules)
        self.assertEqual([raw[flag.start : flag.end] for flag in flags], ["organization"])
        # Sans le littéral, le nom propre est signalé comme le reste.
        without = ef.detect_field("en", "why_it_matters", raw, self.rules)
        self.assertEqual(len(without), 2)
        # Littéral du fichier du dépôt (libellé de domaine, D-016) et citation verrouillée.
        label = "Collisions with Ant Colony Optimization are rare."
        self.assertEqual(ef.detect_field("en", "imagine_that", label, self.rules), [])
        self.assertEqual(len(ef.detect_field("en", "imagine_that", "Colony optimization.", self.rules)), 1)

    def test_positions_are_raw_code_points_despite_normalisation(self) -> None:
        hidden = f"La dé{SOFT_HYPHEN}couverte attend."
        (flag,) = ef.detect_field("fr", "why_it_matters", hidden, self.rules)
        self.assertEqual(hidden[flag.start : flag.end], f"dé{SOFT_HYPHEN}couverte")
        decomposed = unicodedata.normalize("NFD", "Une révolutionnaire et une découverte.")
        self.assertNotEqual(decomposed, unicodedata.normalize("NFC", decomposed))
        spans = [decomposed[f.start : f.end] for f in ef.detect_field("fr", "why_it_matters", decomposed, self.rules)]
        self.assertEqual(
            [unicodedata.normalize("NFC", span) for span in spans], ["révolutionnaire", "découverte"]
        )
        self.assertTrue(spans[0].endswith("e") and COMBINING_ACUTE in spans[0])

    def test_status_patterns_and_condition_exclusion(self) -> None:
        raw = (
            "A minimal laboratory test was conducted with 8 antennas. "
            "It was verified that two solutions were obtained. "
            "If this link were confirmed, the model would change. "
            "If needed. The result was then validated."
        )
        flags = ef.detect_field("en", "concretely.phase2", raw, self.rules)
        self.assertEqual({flag.kind for flag in flags}, {"statut"})
        spans = sorted({raw[flag.start : flag.end] for flag in flags})
        self.assertEqual(
            spans,
            ["It was verified", "was conducted", "was then validated", "was verified", "were obtained"],
        )
        self.assertNotIn("were confirmed", spans)  # condition dans la même phrase, avant l'occurrence
        fr = "Une validation a été réalisée sur 15 cancers. Si le lien a été confirmé, on élargira."
        spans_fr = [fr[f.start : f.end] for f in ef.detect_field("fr", "concretely.phase3", fr, self.rules)]
        self.assertEqual(spans_fr, ["a été réalisée"])

    def test_status_only_on_scoped_fields(self) -> None:
        raw = "Bayesian emulation has proven itself in climate models."
        self.assertEqual(ef.detect_field("en", "reviewers_say", raw, self.rules), [])
        self.assertEqual(ef.detect_field("en", "why_it_matters", raw, self.rules), [])
        self.assertEqual(
            [flag.kind for flag in ef.detect_field("en", "hypothesis_in_brief", raw, self.rules)],
            ["statut"],
        )

    def test_invalid_title_only_gets_structure_row(self) -> None:
        raw = "A clamp\nA recent advance has yielded a clamp that has been verified. This finding offers hope."
        flags = ef.detect_field("en", "title", raw, self.rules)
        self.assertEqual(
            [(f.kind, f.start, f.end, f.rule_idx) for f in flags], [("structure", 7, None, None)]
        )
        # Ligne retenue signalée : vocabulaire et statut portent sur elle, positions du champ brut.
        raw = "  Our clamp was verified\nsuite"
        flags = ef.detect_field("en", "title", raw, self.rules)
        self.assertEqual({f.kind for f in flags}, {"structure", "vocab", "statut"})
        self.assertEqual(
            sorted(raw[f.start : f.end] for f in flags if f.end is not None), ["Our", "was verified"]
        )
        long_line = ef.detect_field("en", "title", "Our " + "t" * 140, self.rules)
        self.assertEqual([f.kind for f in long_line], ["structure"])

    def test_scan_brief_covers_both_languages_and_all_level2_fields(self) -> None:
        row = {
            "id": BRIEF,
            "vulgarization_data": json.dumps(VULG_FR),
            "vulgarization_data_en": json.dumps(VULG_EN),
        }
        scan = ef.scan_brief(row, self.rules)
        self.assertEqual(len(scan.fields), 18)
        self.assertEqual(
            summary_keys(list(scan.flags)),
            {
                ("fr", "title_fr", "vocab"),
                ("fr", "why_it_matters", "vocab"),
                ("en", "title", "structure"),
                ("en", "why_it_matters", "vocab"),
                ("en", "concretely.phase2", "statut"),
            },
        )
        self.assertEqual(ef.scan_brief({"id": BRIEF}, self.rules).flags, ())

    def test_rule_files_are_byte_identical_to_the_front(self) -> None:
        config = load_config()
        self.assertEqual(
            [config.vocab_rules_path, config.status_rules_path, config.vocab_allow_path],
            [REPO / "config" / "narrative" / name for name in RULE_FILES],
        )
        candidates = [
            Path(os.environ["SPORE_V2_FRONT_CHECKS"]) if os.environ.get("SPORE_V2_FRONT_CHECKS") else None,
            Path("/home/baq/Projects/spore-v2/scripts/v2/checks"),
            Path("/home/baq/Projects/spore-web/scripts/v2/checks"),
        ]
        front = next(
            (path for path in candidates if path and all((path / name).is_file() for name in RULE_FILES)),
            None,
        )
        if front is None:
            self.fail("règles du front introuvables (poser SPORE_V2_FRONT_CHECKS)")
        for name in RULE_FILES:
            with self.subTest(name=name):
                self.assertEqual(
                    (REPO / "config" / "narrative" / name).read_bytes(), (front / name).read_bytes()
                )


EXPECTED_VOCAB_FIELDS = {
    # LIGNE_EDITORIALE.md §8, table des corrections (11 entrées).
    "SPR-2026-9A56:fr:imagine_that",
    "SPR-2026-0C63:fr:imagine_that",
    "SPR-2026-0C63:en:imagine_that",
    "SPR-2026-F0FA:fr:concretely.phase3",
    "SPR-2026-E212:fr:title_fr",
    "SPR-2026-E212:fr:imagine_that",
    "SPR-2026-3CBD:fr:hypothesis_in_brief",
    "SPR-2026-B7A1:fr:why_it_matters",
    "SPR-2026-0E36:fr:hypothesis_in_brief",
    "SPR-2026-303D:fr:hypothesis_in_brief",
    # Nom propre : signalé tant que « World Health Organization » n'est pas dans vocab_allow.txt.
    "SPR-2026-260F:en:why_it_matters",
}
EXPECTED_STATUT_FIELDS = [
    "SPR-2026-3A8D:en:concretely.phase3",
    "SPR-2026-3CBD:en:concretely.phase2",
    "SPR-2026-8B20:en:concretely.phase2",
    "SPR-2026-961D:en:concretely.phase2",
]


def _require_base(test: unittest.TestCase) -> None:
    # Échec explicite plutôt qu'un saut : M1 (verify) compte un test ignoré comme un échec,
    # autant que le message nomme la dépendance.
    if not BASE_DB.is_file():
        test.fail(f"copie de base absente : {BASE_DB} (poser SPORE_V2_BASE_DB)")


class BaseCopySurveyTests(unittest.TestCase):
    """Relevé de la copie de base du 19/09, ouverte en lecture seule."""

    def test_base_copy_flags_match_the_editorial_survey(self) -> None:
        _require_base(self)
        with narrative_db.connect(BASE_DB, readonly=True) as conn:
            conn.execute("PRAGMA query_only = 1")
            summary = ef.flag_all(conn, load_config(), write=False)
        self.assertEqual(summary["briefs"], 90)
        self.assertEqual(summary["flagged_fields"]["statut"], EXPECTED_STATUT_FIELDS)
        self.assertEqual(summary["flagged_fields"]["structure"], ["SPR-2026-3676:en:title"])
        self.assertEqual(set(summary["flagged_fields"]["vocab"]), EXPECTED_VOCAB_FIELDS)
        # 10 occurrences en FR (8 idées), 2 en EN (2 idées) ; rien d'autre.
        self.assertEqual(summary["flags"]["vocab"], 12)
        self.assertEqual(summary["flags"]["structure"], 1)
        self.assertEqual(set(summary["flags"]), {"vocab", "statut", "structure"})


class BaseCopyRowsTests(TempDatabase):
    """Lignes écrites pour de vrais champs de la copie de base (recopiés dans une base temporaire)."""

    BRIEFS = ("SPR-2026-3676", "SPR-2026-8B20", "SPR-2026-260F", "SPR-2026-E212", "SPR-2026-3CBD")

    async def test_base_rows_are_written_without_the_detected_text(self) -> None:
        _require_base(self)
        with narrative_db.connect(BASE_DB, readonly=True) as source:
            source.execute("PRAGMA query_only = 1")
            picked = [narrative_db.fetch_brief(source, brief_id) for brief_id in self.BRIEFS]
        conn = sqlite3.connect(self.db_path)
        try:
            for row in picked:
                assert row is not None
                conn.execute(
                    "INSERT INTO briefs (id, hypothesis_id, status, is_stub, sharpened_data, "
                    "vulgarization_data, vulgarization_data_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["id"],
                        row["hypothesis_id"],
                        row["status"],
                        row["is_stub"],
                        row["sharpened_data"],
                        row["vulgarization_data"],
                        row["vulgarization_data_en"],
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        config = load_config()
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            ef.flag_all(conn, config, write=True)
        written = flag_rows(self.db_path)
        by_brief = {row["id"]: row for row in picked if row}
        self.assertEqual(
            [(r["kind"], r["start"], r["end"]) for r in written if r["brief_id"] == "SPR-2026-3676"],
            [("structure", 58, None)],
        )
        statut = {r["field"] for r in written if r["brief_id"] == "SPR-2026-8B20" and r["kind"] == "statut"}
        self.assertEqual(statut, {"concretely.phase2"})
        rules = ef.load_rules(config)
        for flag in written:
            with self.subTest(flag=flag["id"]):
                self.assertEqual(flag["status"], "open")
                column = ef.VULGARISATION_COLUMN[flag["lang"]]
                data = json.loads(by_brief[flag["brief_id"]][column])
                raw = data
                for part in flag["field"].split("."):
                    raw = raw[part]
                self.assertEqual(flag["field_sha256"], ef.field_sha256(raw))
                if flag["kind"] == "structure":
                    continue
                span = raw[flag["start"] : flag["end"]]
                table = rules.vocab if flag["kind"] == "vocab" else rules.status
                self.assertTrue(table[flag["lang"]][flag["rule_idx"]].pattern.fullmatch(span))
                # Aucune colonne ne recopie le passage détecté.
                for value in flag.values():
                    if isinstance(value, str):
                        self.assertNotIn(span.casefold(), value.casefold())


class FlagsTableTests(TempDatabase):
    """Table, nœud et intégrations, sur une base temporaire au schéma v1."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.config = make_config(Path(self._tmp.name))
        insert_brief(self.db_path, BRIEF, domains=("Hydrology", "Materials Chemistry"))
        set_vulgarisation(self.db_path, BRIEF, VULG_FR, VULG_EN)
        for index in range(3):
            insert_brief(self.db_path, f"SPR-2026-0F1{index}", domains=("Hydrology", "Oceanography"))
        insert_brief(self.db_path, "SPR-2026-0F20", is_stub=1, hypothesis_id="cus_1")

    def flag(self) -> dict[str, Any]:
        with override_config(self.config):
            return ef.flag_brief(self.db_path, BRIEF, self.config)

    async def test_schema_is_additive_and_idempotent(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
            narrative_db.ensure_narrative_schema(conn)
            self.assertTrue(narrative_db.narrative_schema_present(conn))
            columns = [row[1] for row in conn.execute("PRAGMA table_info(v2_vocab_flags)")]
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(v2_vocab_flags)")}
        self.assertEqual(
            columns,
            ["id", "brief_id", "lang", "field", "kind", "start", "end", "rule_idx", "field_sha256", "status", "detected_at"],
        )
        self.assertIn("ux_v2_vocab_flags_key", indexes)
        self.assertIn("v2_vocab_flags", narrative_db.NARRATIVE_TABLES)

    async def test_node_writes_rows_without_the_word_and_is_idempotent(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
        summary = self.flag()
        self.assertEqual(summary["flags"], {"statut": 1, "structure": 1, "vocab": 3})
        first = flag_rows(self.db_path, BRIEF)
        self.assertEqual(
            {(r["lang"], r["field"], r["kind"]) for r in first},
            {
                ("fr", "title_fr", "vocab"),
                ("fr", "why_it_matters", "vocab"),
                ("en", "title", "structure"),
                ("en", "why_it_matters", "vocab"),
                ("en", "concretely.phase2", "statut"),
            },
        )
        structure = next(r for r in first if r["kind"] == "structure")
        self.assertEqual((structure["start"], structure["end"], structure["rule_idx"]), (16, None, None))
        self.assertEqual({r["status"] for r in first}, {"open"})
        for row in first:
            for value in row.values():
                if isinstance(value, str):
                    for word in DETECTED_WORDS:
                        self.assertNotIn(word.casefold(), value.casefold())
        # Deuxième passe : rien ne bouge (ni doublon, ni nouvel horodatage).
        self.flag()
        self.assertEqual(flag_rows(self.db_path, BRIEF), first)

    async def test_operator_status_survives_and_stale_rows_are_reconciled(self) -> None:
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
        self.flag()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE v2_vocab_flags SET status = 'corrected' WHERE field = 'title_fr' AND kind = 'vocab'"
        )
        conn.commit()
        conn.close()

        # Nouveau texte EN : la ligne de statut de l'ancien texte disparaît.
        fixed = json.loads(json.dumps(VULG_EN))
        fixed["concretely"]["phase2"] = "A minimal test is run with eight samples."
        set_vulgarisation(self.db_path, BRIEF, VULG_FR, fixed)
        self.flag()
        after = flag_rows(self.db_path, BRIEF)
        self.assertFalse([r for r in after if r["kind"] == "statut"])
        self.assertEqual(
            [r["status"] for r in after if r["field"] == "title_fr"], ["corrected"]
        )

        # Le nom propre entre dans vocab_allow.txt : sa ligne passe à « allowed ».
        allow = Path(self._tmp.name) / "vocab_allow.txt"
        allow.write_text(
            self.config.vocab_allow_path.read_text(encoding="utf-8")
            + "World Health Organization | nom propre (test)\n",
            encoding="utf-8",
        )
        self.config = self.config.with_changes(vocab_allow_path=allow)
        self.flag()
        who = [r for r in flag_rows(self.db_path, BRIEF) if r["lang"] == "en" and r["field"] == "why_it_matters"]
        self.assertEqual([r["status"] for r in who], ["allowed"])
        self.assertEqual(
            [r["status"] for r in flag_rows(self.db_path, BRIEF) if r["field"] == "title_fr"], ["corrected"]
        )

        # Le littéral quitte vocab_allow.txt : la ligne redevient « open » (fail-closed) ;
        # la correction de l'opérateur (« corrected ») ne bouge pas.
        self.config = self.config.with_changes(vocab_allow_path=load_config().vocab_allow_path)
        self.flag()
        who = [r for r in flag_rows(self.db_path, BRIEF) if r["lang"] == "en" and r["field"] == "why_it_matters"]
        self.assertEqual([r["status"] for r in who], ["open"])
        self.assertEqual(
            [r["status"] for r in flag_rows(self.db_path, BRIEF) if r["field"] == "title_fr"], ["corrected"]
        )

    async def test_layer_runs_flags_in_the_mechanical_tail_without_stories(self) -> None:
        script = FakeScript({})
        with use_script(script), override_config(self.config):
            summary = await run_narrative_layer(
                brief_id=BRIEF, db_path=self.db_path, write_stories=False, config=self.config
            )
        self.assertEqual(script.calls, [])
        self.assertEqual(summary["vocab_flags"], {"statut": 1, "structure": 1, "vocab": 3})
        events = summary["events"]
        order = [
            next(i for i, e in enumerate(events) if e.startswith(prefix))
            for prefix in ("theme_tagger:", "explainer_flags:done", "brief_link:", "neighbours_refresh:")
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(len(flag_rows(self.db_path, BRIEF)), 5)

    async def test_recovery_tail_writes_flags_after_a_subgraph_crash(self) -> None:
        broken = mock.Mock()
        broken.ainvoke = mock.AsyncMock(side_effect=RuntimeError("boom"))
        with (
            mock.patch.object(narrative_graph, "compiled_narrative_graph", return_value=broken),
            use_script(FakeScript({})),
            override_config(self.config),
        ):
            summary = await run_narrative_layer(brief_id=BRIEF, db_path=self.db_path, config=self.config)
        self.assertEqual(summary["failed"], "layer_failed:RuntimeError")
        self.assertEqual(len(flag_rows(self.db_path, BRIEF)), 5)

    async def test_stub_and_unpublished_briefs_get_no_rows(self) -> None:
        insert_brief(self.db_path, "SPR-2026-0F30", status="pending")
        set_vulgarisation(self.db_path, "SPR-2026-0F30", VULG_FR, VULG_EN)
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
        for brief_id in ("SPR-2026-0F30", "SPR-2026-0F20"):
            with override_config(self.config):
                self.assertTrue(ef.flag_brief(self.db_path, brief_id, self.config)["skipped"])
        self.assertEqual(flag_rows(self.db_path), [])

    async def backfill(self, *extra: str) -> dict[str, Any]:
        """Lance le backfill (dans un fil : il a sa propre boucle) et décode son résumé."""
        root = Path(self._tmp.name)
        raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        raw["paths"]["identity_denylist"] = str(self.config.identity_denylist_path)
        raw["backfill"]["rate_limit_s"] = 0.0
        config_path = root / "narrative.yaml"
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        (root / "sidecars").mkdir(exist_ok=True)
        argv = [
            "--db", str(self.db_path),
            "--config", str(config_path),
            "--spend-json", str(root / "evidence" / "spend.json"),
            "--sidecars-dir", str(root / "sidecars"),
            *extra,
        ]  # fmt: skip
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}), contextlib.redirect_stdout(out):
            code = await asyncio.to_thread(backfill_narrative.main, argv)
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    async def test_backfill_dry_run_counts_and_no_stories_writes(self) -> None:
        dry = await self.backfill("--dry-run")
        self.assertEqual(dry["explainer_flags"]["flags"], {"statut": 1, "structure": 1, "vocab": 3})
        self.assertFalse(dry["explainer_flags"]["written"])
        self.assertEqual(rows(self.db_path, "SELECT name FROM sqlite_master WHERE name = 'v2_vocab_flags'"), [])

        script = FakeScript({})
        with use_script(script):
            done = await self.backfill("--no-stories", "--no-spend-update")
        self.assertEqual(script.calls, [])
        self.assertTrue(done["explainer_flags"]["written"])
        self.assertEqual(done["stories"]["planned"], 0)
        self.assertEqual(len(flag_rows(self.db_path, BRIEF)), 5)
        # Idempotent : un second passage ne duplique rien.
        await self.backfill("--no-stories", "--no-spend-update")
        self.assertEqual(len(flag_rows(self.db_path)), 5)


if __name__ == "__main__":
    unittest.main()
