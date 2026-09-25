"""v2.1 B2 — notes grand public de l'étage 2 (``narrative.addendum``, E-018).

Aucun appel réseau : le client LLM est scripté (``tests.v2_narrative_support``).

Ce que ces tests protègent, dans l'ordre d'importance :
  1. le garde est fail-closed : une note qui chiffre ce que le brief ne chiffre pas, cite une
     référence, emploie « nous » ou un terme proscrit, ou sort un français sans accents, est
     rejetée sans que le juge soit appelé ;
  2. le juge décide seul de la publication : un doute, un verdict autre que « accept », une note
     sous le seuil rejettent ;
  3. chaque tentative est écrite, publiée ou rejetée, avec son rapport ; deux au plus ;
  4. l'opération est idempotente : des notes publiées ne sont jamais réécrites.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative import addendum
from storage import narrative_db
from tests.test_s11_llm_contract import TempDatabase
from tests.v2_narrative_support import DENYLIST_ENTRY, FakeScript, make_config, use_script

GOOD_FR = {
    "tuer": (
        "L'idée suppose que les sédiments laissent passer l'eau salée jusqu'aux pores du béton. "
        "Si les grains fins bouchent ces pores dès la première tempête, l'effet disparaîtrait, "
        "et les relevés sur la digue le montreraient sans ambiguïté."
    ),
    "ignore": (
        "Personne ne sait encore combien de temps le matériau garderait cette capacité à s'ouvrir, "
        "ni s'il réagirait de la même façon dans un estuaire moins salé que celui de l'essai. "
        "Ces deux questions décident de l'intérêt pratique de l'idée."
    ),
    "tester": (
        "Il faudrait comparer deux portions de digue, l'une faite de ce béton, l'autre d'un béton "
        "ordinaire, et suivre la pression de l'eau au fil des marées. Si la première ne se comporte "
        "pas autrement que la seconde, l'idée tombe."
    ),
}

JUDGE_OK = {
    "scores": {"fidelity": 9, "no_new_facts": 9, "status_honest": 9, "plain_language": 8},
    "doubts": [],
    "verdict": "accept",
    "reasons": ["Fidèle aux sources."],
}


def _grounding() -> dict:
    return {
        "counter_evidence": [
            {"finding": "Fine sediments clog the pores within weeks.", "severity": "serious", "doi": "10.1/x", "title": "T"}
        ],
        "gap_manifest_update": {"new_gaps": ["Long-term durability of the porous response is unknown."]},
    }


def _sharpened() -> dict:
    return {
        "title": "Porous concrete",
        "formal_statement": "A salinity-responsive porous concrete lowers wave pressure on sea walls by at least 20%.",
        "proposed_mechanism": {"known_unknowns": ["Behaviour in low-salinity estuaries."]},
        "falsifiable_predictions": [{"prediction": "Pressure drops at high salinity.", "quantitative_bound": "at least 20%"}],
        "boundary_conditions": [{"condition": "Salinity above 25 g/L."}],
        "domains": ["Hydrology", "Materials Chemistry"],
    }


def _panel() -> dict:
    return {
        "reviews": [
            {"reviewer_persona": "contrarian", "weaknesses": ["FAIL REASON #1: sediment clogging is ignored."],
             "critical_questions": ["How fast do pores clog?"]},
            {"reviewer_persona": "methodologist", "weaknesses": [], "critical_questions": ["What sample size?"]},
        ]
    }


def _protocol() -> dict:
    return {"phases": [{"phase_name": "Phase 1", "objective": "Tank tests", "required_resources": {"estimated_duration": "3 months"}}]}


def insert_full_brief(db_path: Path, brief_id: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO briefs (id, hypothesis_id, status, is_stub, sharpened_data, grounding_data, panel_data, protocol_data)"
            " VALUES (?, ?, 'complete', 0, ?, ?, ?, ?)",
            (brief_id, brief_id, json.dumps(_sharpened()), json.dumps(_grounding()),
             json.dumps(_panel()), json.dumps(_protocol())),
        )
        conn.commit()
    finally:
        conn.close()


def rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM v2_explainer_addendum ORDER BY id")]
    finally:
        conn.close()


class SourcesTests(TempDatabase):
    async def test_sources_carry_no_reference_and_strip_the_fail_reason_prefix(self) -> None:
        row = {"id": "SPR-T", "sharpened_data": json.dumps(_sharpened()), "grounding_data": json.dumps(_grounding()),
               "panel_data": json.dumps(_panel()), "protocol_data": json.dumps(_protocol())}
        src = addendum.extract_sources(row, kill_condition="If pressure does not drop, the idea fails.")
        blob = src.all_text()
        self.assertNotIn("10.1/x", blob)
        self.assertEqual(src.objection, "sediment clogging is ignored.")
        self.assertIn("How fast do pores clog?", src.questions)
        self.assertTrue(src.has_substance())

    async def test_numbers_are_compared_in_canonical_form(self) -> None:
        self.assertEqual(addendum.normalise_number("0,70"), "0.7")
        self.assertEqual(addendum.normalise_number("007"), "7")
        self.assertEqual(addendum.normalise_number("20"), "20")
        self.assertEqual(addendum.numbers_in("entre 2,5 et 20 %"), {"2.5", "20"})


class MechanicalTests(TempDatabase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.config = make_config(Path(self._tmp.name))
        row = {"id": "SPR-T", "sharpened_data": json.dumps(_sharpened()), "grounding_data": json.dumps(_grounding()),
               "panel_data": json.dumps(_panel()), "protocol_data": json.dumps(_protocol())}
        self.sources = addendum.extract_sources(row)

    def check(self, **changes: str) -> dict:
        texts = {**GOOD_FR, **changes}
        return addendum.mechanical_check(texts, self.sources, "fr", self.config.addendum, self.config.identity_denylist_path)

    async def test_good_notes_pass(self) -> None:
        self.assertEqual(self.check()["reasons"], [])

    async def test_a_number_absent_from_the_sources_rejects(self) -> None:
        text = GOOD_FR["tester"] + " Le gain atteindrait 35 % en six mois."
        self.assertIn("tester:number_not_in_sources", self.check(tester=text)["reasons"])

    async def test_a_number_from_the_sources_is_allowed(self) -> None:
        text = GOOD_FR["tester"] + " La baisse attendue est d'au moins 20 %."
        self.assertNotIn("tester:number_not_in_sources", self.check(tester=text)["reasons"])

    async def test_a_reference_rejects(self) -> None:
        text = GOOD_FR["tuer"] + " (Durand et al., 2021)"
        self.assertIn("tuer:citation", self.check(tuer=text)["reasons"])

    async def test_editorial_we_and_banned_words_reject(self) -> None:
        self.assertIn("ignore:vocabulary", self.check(ignore=GOOD_FR["ignore"] + " Nous le saurons bientôt.")["reasons"])
        self.assertIn("tuer:vocabulary", self.check(tuer=GOOD_FR["tuer"] + " Une découverte possible.")["reasons"])

    async def test_unaccented_french_rejects(self) -> None:
        flat = ("Il faudrait comparer deux portions de digue et suivre la pression de l'eau au fil des marees, "
                "puis verifier si la premiere se comporte autrement que la seconde.")
        self.assertIn("tester:unaccented_french", self.check(tester=flat)["reasons"])

    async def test_missing_or_short_notes_reject(self) -> None:
        self.assertIn("ignore:missing", self.check(ignore="")["reasons"])
        self.assertIn("tuer:length", self.check(tuer="Trop court.")["reasons"])

    async def test_identity_hit_rejects_and_missing_denylist_fails_closed(self) -> None:
        self.assertIn("identity_denylist:hit", self.check(tuer=GOOD_FR["tuer"] + f" {DENYLIST_ENTRY}.")["reasons"])
        cfg = make_config(Path(self._tmp.name) / "none", denylist=False)
        (Path(self._tmp.name) / "none").mkdir(exist_ok=True)
        result = addendum.mechanical_check(GOOD_FR, self.sources, "fr", cfg.addendum, cfg.identity_denylist_path)
        self.assertIn("identity_denylist:missing", result["reasons"])


class JudgeTests(TempDatabase):
    async def test_accept_passes_and_any_doubt_or_low_score_rejects(self) -> None:
        cfg = make_config(Path(self._tmp.name)).addendum
        self.assertTrue(addendum.evaluate_judgement(JUDGE_OK, cfg)["passed"])
        doubt = {**JUDGE_OK, "doubts": ["Le chiffre semble exagéré."]}
        self.assertIn("judge:doubt", addendum.evaluate_judgement(doubt, cfg)["reasons"])
        low = {**JUDGE_OK, "scores": {**JUDGE_OK["scores"], "no_new_facts": 5}}
        self.assertIn("judge:below_threshold:no_new_facts", addendum.evaluate_judgement(low, cfg)["reasons"])
        self.assertIn("judge:verdict_not_accept", addendum.evaluate_judgement({**JUDGE_OK, "verdict": "reject"}, cfg)["reasons"])
        self.assertFalse(addendum.evaluate_judgement("pas un objet", cfg)["passed"])


class ProduceTests(TempDatabase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.config = make_config(Path(self._tmp.name))
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
        insert_full_brief(self.db_path, "SPR-T")

    async def test_publishes_on_first_attempt_and_is_idempotent(self) -> None:
        script = FakeScript({"addendum_writer": [GOOD_FR], "addendum_guard": [JUDGE_OK]})
        with use_script(script):
            first = await addendum.produce_addendum(self.db_path, "SPR-T", "fr", config=self.config, run_label="e2e")
            again = await addendum.produce_addendum(self.db_path, "SPR-T", "fr", config=self.config, run_label="e2e")
        self.assertEqual(first["status"], "published")
        self.assertTrue(again.get("existing"))
        self.assertEqual(script.count("addendum_writer"), 1)
        written = rows(self.db_path)
        self.assertEqual([(r["attempt"], r["status"]) for r in written], [(1, "published")])
        self.assertEqual(written[0]["tuer"], GOOD_FR["tuer"])
        self.assertTrue(written[0]["body_sha256"])

    async def test_mechanical_failure_skips_the_judge_and_retries_once(self) -> None:
        bad = {**GOOD_FR, "tester": GOOD_FR["tester"] + " Le gain atteindrait 35 % en six mois."}
        script = FakeScript({"addendum_writer": [bad, GOOD_FR], "addendum_guard": [JUDGE_OK]})
        with use_script(script):
            result = await addendum.produce_addendum(self.db_path, "SPR-T", "fr", config=self.config, run_label="e2e")
        self.assertEqual(result["status"], "published")
        self.assertEqual(script.count("addendum_guard"), 1)
        statuses = [(r["attempt"], r["status"]) for r in rows(self.db_path)]
        self.assertEqual(statuses, [(1, "rejected"), (2, "published")])
        report = json.loads(rows(self.db_path)[0]["guard_report_json"])
        self.assertIn("tester:number_not_in_sources", report["reasons"])

    async def test_two_rejections_exhaust_and_nothing_more_is_attempted(self) -> None:
        script = FakeScript({"addendum_writer": [GOOD_FR], "addendum_guard": [{**JUDGE_OK, "verdict": "reject"}]})
        with use_script(script):
            result = await addendum.produce_addendum(self.db_path, "SPR-T", "fr", config=self.config, run_label="e2e")
            later = await addendum.produce_addendum(self.db_path, "SPR-T", "fr", config=self.config, run_label="e2e")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(later["status"], "exhausted")
        self.assertEqual(script.count("addendum_writer"), 2)
        self.assertEqual([r["status"] for r in rows(self.db_path)], ["rejected", "rejected"])

    async def test_a_call_failure_is_a_rejected_attempt_not_a_crash(self) -> None:
        # Un seul essai par appel : la panne n'est pas absorbée par le rejeu de call_json, elle
        # atteint la tentative, qui doit être écrite comme rejetée et suivie de la suivante.
        config = self.config.with_changes(retry_max_attempts=1)
        script = FakeScript({"addendum_writer": [RuntimeError("panne"), GOOD_FR], "addendum_guard": [JUDGE_OK]})
        with use_script(script):
            result = await addendum.produce_addendum(self.db_path, "SPR-T", "fr", config=config, run_label="e2e")
        self.assertEqual(result["status"], "published")
        first = json.loads(rows(self.db_path)[0]["guard_report_json"])
        self.assertTrue(first["reasons"][0].startswith("addendum:failed:"))

    async def test_a_stub_or_unknown_brief_is_skipped(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO briefs (id, hypothesis_id, status, is_stub) VALUES ('SPR-S', 'H-S', 'complete', 1)")
        conn.commit()
        conn.close()
        with use_script(FakeScript({"addendum_writer": [GOOD_FR], "addendum_guard": [JUDGE_OK]})) as script:
            stub = await addendum.produce_addendum(self.db_path, "SPR-S", "fr", config=self.config, run_label="e2e")
            unknown = await addendum.produce_addendum(self.db_path, "SPR-ZZZZ", "fr", config=self.config, run_label="e2e")
        self.assertEqual((stub["status"], unknown["status"]), ("skipped", "skipped"))
        self.assertEqual(script.count("addendum_writer"), 0)

    async def test_costs_are_logged_under_the_run_label(self) -> None:
        with use_script(FakeScript({"addendum_writer": [GOOD_FR], "addendum_guard": [JUDGE_OK]})):
            await addendum.produce_addendum(self.db_path, "SPR-T", "fr", config=self.config, run_label="e2e")
        conn = sqlite3.connect(self.db_path)
        nodes = [r[0] for r in conn.execute("SELECT node FROM v2_llm_costs WHERE run_label = 'e2e' ORDER BY id")]
        conn.close()
        self.assertEqual(nodes, ["addendum_writer", "addendum_guard"])


if __name__ == "__main__":
    import unittest

    unittest.main()
