"""v2 — garde des récits : contrôles mécaniques et juge LLM (simulé).

Chaque contrôle mécanique est exercé seul ; le juge rejette sur échec de
parsing, doute, verdict autre que ``accept``, note absente, hors échelle ou
sous le seuil ; la denylist absente fait échouer le contrôle d'identité sans
que son contenu apparaisse dans le rapport.

Usage:
    DEEPSEEK_API_KEY=dummy python -m unittest tests.test_v2_narrative_guard
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative.checks import (
    PROSCRIBED_EN,
    PROSCRIBED_FR,
    citation_hits,
    count_words,
    identity_check,
    normalise,
    proscribed_hits,
    run_mechanical_checks,
    us_spelling_hits,
)
from narrative.guard import (
    JUDGE_CONTROLS,
    JUDGE_REASON_MAX_CHARS,
    JUDGE_REASONS_MAX,
    evaluate_verdict,
    guard_story,
    mechanical_settings,
)
from narrative.inputs import extract_inputs
from storage import narrative_db
from tests.test_s11_llm_contract import TempDatabase
from tests.v2_narrative_support import (
    DENYLIST_ENTRY,
    YEAR,
    FakeScript,
    good_story_en,
    good_story_fr,
    judge_verdict,
    make_config,
    rows,
    sharpened,
    use_script,
)


class MechanicalChecksTests(unittest.TestCase):
    """Un contrôle à la fois, sur un récit par ailleurs valide."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))
        self.fr = mechanical_settings(self.config, "fr")
        self.en = mechanical_settings(self.config, "en")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, story: dict, lang: str = "fr") -> dict:
        """Contrôles mécaniques d'un récit.

        Args:
            story: Récit.
            lang: Langue.

        Returns:
            Résultat.
        """
        return run_mechanical_checks(story, self.fr if lang == "fr" else self.en)

    def test_valid_stories_pass(self) -> None:
        self.assertTrue(self.check(good_story_fr())["passed"])
        self.assertTrue(self.check({**good_story_en(), "year": YEAR}, "en")["passed"])

    def test_schema(self) -> None:
        story = good_story_fr()
        del story["mechanism"]
        result = self.check(story)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["schema"])
        self.assertFalse(self.check(good_story_fr(year="dans vingt ans"))["checks"]["schema"])
        self.assertFalse(self.check(good_story_fr(title="x" * 200))["checks"]["schema"])
        self.assertFalse(self.check(good_story_fr(limit_or_risk="   "))["checks"]["schema"])

    def test_length(self) -> None:
        short = self.check(good_story_fr(body_markdown="Trop court. " * 20))
        self.assertFalse(short["checks"]["length"]["passed"])
        long = self.check(good_story_fr(body_markdown=good_story_fr()["body_markdown"] * 2))
        self.assertFalse(long["checks"]["length"]["passed"])
        self.assertEqual(count_words("— Tu sens ? demanda l'homme."), 4)

    def test_year_window(self) -> None:
        self.assertFalse(self.check(good_story_fr(year=YEAR - 10))["checks"]["year_window"])
        self.assertFalse(self.check(good_story_fr(year=YEAR + 20))["checks"]["year_window"])
        self.assertTrue(self.check(good_story_fr(year=str(YEAR)))["checks"]["year_window"])

    def test_citation_patterns(self) -> None:
        samples = {
            "doi": "Le résultat 10.1038/s41586-020-1234 reste fragile.",
            "url": "Tout est sur https://exemple.org/page.",
            "url_bare": "Voir portsalant.fr pour la suite.",
            "email": "Écrire à lina@port-salant.org demain.",
            "et_al": "Comme l'avaient montré Varesse et al. autrefois.",
            "selon": "Selon une étude récente, la digue tiendrait.",
            "d_apres": "D'après une étude, rien ne bouge.",
            "according": "According to a study, the wall holds.",
            "author_year": "La porosité change (Varesse, 2021) avec le sel.",
            "numeric": "La porosité change avec le sel [12].",
        }
        for name, sentence in samples.items():
            with self.subTest(name=name):
                story = good_story_fr(body_markdown=good_story_fr()["body_markdown"] + "\n\n" + sentence)
                self.assertFalse(self.check(story)["checks"]["no_citation_patterns"], sentence)
        # Repère de lieu et de date du récit, prénom « Al » : pas des citations.
        self.assertEqual(citation_hits(f"(Port-Salant, {YEAR})"), [])
        self.assertEqual(citation_hits("Sam et Al partirent."), [])

    def test_proscribed_vocabulary_fr(self) -> None:
        for word in ("découverte", "DÉCOUVRIR", "decouvert", "révolutionnaire", "propulsé par l'IA", "nous", "Notre"):
            with self.subTest(word=word):
                story = good_story_fr(mechanism=f"Le mécanisme ({word}) reste à éprouver.")
                self.assertFalse(self.check(story)["checks"]["no_proscribed_vocab"])
        self.assertIn("first_person_plural", proscribed_hits("Nous verrons.", "fr"))
        self.assertEqual(proscribed_hits("Nous verrons.", "fr", strict_first_person_plural=False), [])

    def test_proscribed_vocabulary_en(self) -> None:
        for word in ("discovery", "undiscovered", "Revolutionary", "powered by AI", "AI-powered", "we", "our", "us"):
            with self.subTest(word=word):
                story = {**good_story_en(mechanism=f"The mechanism ({word}) remains untested."), "year": YEAR}
                self.assertFalse(self.check(story, "en")["checks"]["no_proscribed_vocab"])

    def test_british_spelling_and_french_residue(self) -> None:
        story = {**good_story_en(limit_or_risk="The behavior of the center must be analyzed."), "year": YEAR}
        result = self.check(story, "en")
        self.assertFalse(result["checks"]["british_spelling"])
        self.assertEqual(us_spelling_hits("The colour and size of the prize."), [])
        self.assertIn("stabilized", us_spelling_hits("It stabilized."))
        residue = {**good_story_en(mechanism="C'est déjà très clair."), "year": YEAR}
        self.assertFalse(self.check(residue, "en")["checks"]["no_french_residue"])

    def test_us_spelling_list_follows_the_run_closed_list(self) -> None:
        # Règle « aluminum / sulfur » de scripts/v2/checks/vocab_rules.json
        # (M9) : la graphie IUPAC « sulfur » est une orthographe américaine
        # pour ce run, le garde doit la rejeter comme M9 la relèverait.
        for word in ("sulfur", "sulfates", "sulfide", "aluminum"):
            with self.subTest(word=word):
                story = {**good_story_en(mechanism=f"The {word} layer stays thin."), "year": YEAR}
                self.assertFalse(self.check(story, "en")["checks"]["british_spelling"])
        # Les graphies britanniques passent, et les dérivés hors de la règle
        # (frontière de mot de M9) ne sont pas ajoutés au garde.
        self.assertEqual(us_spelling_hits("Sulphur, sulphates and aluminium."), [])
        self.assertEqual(us_spelling_hits("sulfuric acid"), [])

    def test_identity_denylist(self) -> None:
        story = good_story_fr(place=f"La maison de {DENYLIST_ENTRY.upper()}")
        result = self.check(story)
        self.assertFalse(result["checks"]["identity_denylist"])
        # Le rapport ne contient jamais la chaîne interdite.
        self.assertNotIn(DENYLIST_ENTRY.casefold(), json.dumps(result).casefold())
        # Un mot plus long qui contient l'entrée n'est pas une occurrence.
        self.assertEqual(identity_check(["Zorblax Quendimorix"], self.config.identity_denylist_path).hits, 0)

    def test_missing_denylist_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            config = make_config(Path(other), denylist=False)
            result = run_mechanical_checks(good_story_fr(), mechanical_settings(config, "fr"))
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["identity_denylist"])
        self.assertIn("mechanical:identity_denylist:missing", result["reasons"])


class VerdictTests(unittest.TestCase):
    """Lecture du verdict du juge (sans appel)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_accept(self) -> None:
        self.assertTrue(evaluate_verdict(judge_verdict(), self.config)["passed"])

    def test_doubt_rejects(self) -> None:
        section = evaluate_verdict(judge_verdict(doubts=["Le nom du port ressemble à une ville réelle."]), self.config)
        self.assertFalse(section["passed"])
        self.assertIn("judge:doubt", section["reasons"])

    def test_score_under_threshold_rejects(self) -> None:
        section = evaluate_verdict(judge_verdict(overrides={"fidelity": 6}), self.config)
        self.assertFalse(section["passed"])
        self.assertIn("judge:below_threshold:fidelity", section["reasons"])
        # Seuil propre plus strict sur les entités réelles (9).
        strict = evaluate_verdict(judge_verdict(overrides={"no_real_entities": 8}), self.config)
        self.assertFalse(strict["passed"])

    def test_verdict_other_than_accept_rejects(self) -> None:
        self.assertFalse(evaluate_verdict(judge_verdict(verdict="reject"), self.config)["passed"])
        self.assertFalse(evaluate_verdict(judge_verdict(verdict="accept probably"), self.config)["passed"])

    def test_malformed_scores_reject(self) -> None:
        missing = judge_verdict()
        del missing["scores"]["limit_present"]
        self.assertFalse(evaluate_verdict(missing, self.config)["passed"])
        self.assertFalse(evaluate_verdict(judge_verdict(overrides={"fidelity": "9"}), self.config)["passed"])
        self.assertFalse(evaluate_verdict(judge_verdict(overrides={"fidelity": 11}), self.config)["passed"])
        self.assertFalse(evaluate_verdict(judge_verdict(overrides={"fidelity": True}), self.config)["passed"])
        self.assertFalse(evaluate_verdict(["not", "an", "object"], self.config)["passed"])

    def test_constats_kept_for_audit(self) -> None:
        section = evaluate_verdict(judge_verdict(), self.config)
        self.assertEqual(section["constats"]["objet_du_brief"], "un matériau poreux")


class ControlTests(unittest.TestCase):
    """Contrôles fermés du juge, décidés en Python (``require_controls``)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # Le drapeau est posé ici, et non hérité de ``narrative.yaml`` : la
        # configuration livrée suit la version du prompt du garde (seul
        # ``story_guard_v4`` demande au juge le bloc « controles »), et la
        # calibration a retenu ``story_guard_v1``. La machinerie testée ici
        # doit l'être quelle que soit la version retenue.
        base = make_config(Path(self._tmp.name))
        self.config = base.with_changes(guard=replace(base.guard, require_controls=True))
        self.assertTrue(self.config.guard.require_controls)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_control_can_reject(self) -> None:
        for name, (unfavourable, _) in JUDGE_CONTROLS.items():
            with self.subTest(control=name):
                section = evaluate_verdict(judge_verdict(controls={name: unfavourable}), self.config)
                self.assertFalse(section["passed"])
                self.assertIn(f"judge:control_failed:{name}", section["reasons"])

    def test_missing_block_rejects(self) -> None:
        section = evaluate_verdict(judge_verdict(drop_controls=True), self.config)
        self.assertFalse(section["passed"])
        self.assertIn("judge:controls_missing", section["reasons"])

    def test_unreadable_answer_rejects(self) -> None:
        section = evaluate_verdict(judge_verdict(controls={"raison_donnee": "peut-être"}), self.config)
        self.assertFalse(section["passed"])
        self.assertIn("judge:control_unreadable:raison_donnee", section["reasons"])
        self.assertIsNone(section["controles"]["raison_donnee"])

    def test_not_applicable_only_where_allowed(self) -> None:
        allowed = evaluate_verdict(
            judge_verdict(controls={"attente_medicale_bornee": "sans objet"}), self.config
        )
        self.assertTrue(allowed["passed"])
        refused = evaluate_verdict(judge_verdict(controls={"fait_qui_change": "Sans objet."}), self.config)
        self.assertFalse(refused["passed"])
        self.assertIn("judge:control_not_applicable:fait_qui_change", refused["reasons"])

    def test_answers_are_normalised(self) -> None:
        section = evaluate_verdict(
            judge_verdict(controls={"raison_donnee": " OUI ", "contradiction": "Non."}), self.config
        )
        self.assertTrue(section["passed"])
        self.assertEqual(section["controles"]["raison_donnee"], "oui")
        self.assertEqual(section["controles"]["contradiction"], "non")

    def test_controls_ignored_when_not_required(self) -> None:
        legacy = self.config.with_changes(guard=replace(self.config.guard, require_controls=False))
        section = evaluate_verdict(judge_verdict(drop_controls=True), legacy)
        self.assertTrue(section["passed"])
        self.assertNotIn("controles", section)


class JudgeFreeTextTests(unittest.TestCase):
    """Raisons libres du juge : filtrées avant d'entrer dans le rapport (MUST 9, D-017)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def section(self, reasons: list[str]) -> dict:
        """Section ``judge`` d'un verdict par ailleurs valide.

        Args:
            reasons: Raisons libres rendues par le juge.

        Returns:
            Section ``judge`` du rapport.
        """
        return evaluate_verdict({**judge_verdict(), "reasons": reasons}, self.config)

    def test_clean_reason_is_stored_verbatim(self) -> None:
        text = "Le mécanisme du récit suit la chaîne causale du brief, sans l'élargir."
        self.assertEqual(self.section([text])["judge_reasons"], [text])

    def test_proscribed_reason_is_replaced_by_its_code(self) -> None:
        dirty = "Limite mise en scène : dérive de la fibre, découverte par un contrôle sans cellules."
        stored = self.section(["propre", dirty])["judge_reasons"]
        self.assertEqual(stored[0], "propre")
        self.assertEqual(
            stored[1], {"code": "reason_redacted", "rule": "vocab_fr_decouverte", "index": 1}
        )

    def test_english_reason_is_caught_in_a_french_report(self) -> None:
        # ``guard_report_json`` n'est pas une colonne de prose : M9 y applique
        # les règles FR ET EN, quelle que soit la langue de la ligne.
        stored = self.section(["The judge discovered a gap."])["judge_reasons"]
        self.assertEqual(stored[0]["code"], "reason_redacted")
        self.assertIn("vocab_en_discover", stored[0]["rule"])

    def test_nothing_of_the_prose_survives_but_the_count_and_the_rule(self) -> None:
        dirty = "Une révolutionnaire percée, découverte hier."
        stored = self.section(["propre", dirty, "propre aussi"])["judge_reasons"]
        self.assertEqual(len(stored), 3)  # le rang des raisons est conservé
        self.assertNotIn("percée", json.dumps(stored, ensure_ascii=False))
        self.assertEqual(
            sorted(stored[1]["rule"].split(",")), ["vocab_fr_decouverte", "vocab_fr_revolutionnaire"]
        )

    def test_rule_identifier_cannot_be_read_as_an_occurrence(self) -> None:
        # Le code est un jeton technique : le terme y est précédé d'un
        # souligné, qui est un caractère de mot. Les règles du run (M9,
        # scripts/v2/checks/vocab_rules.json, et m9_db.py qui les borne par
        # des frontières de mot) n'y voient donc aucune occurrence — c'est la
        # propriété qui compte, puisque c'est ce contrôle qui lit la colonne.
        # Le filtre du garde, lui, cherche des radicaux dans de la prose sans
        # frontières (« decouv ») : volontairement plus large, et jamais
        # appliqué à un code déjà stocké (le script de reprise ne touche
        # qu'aux chaînes).
        for rule in ("vocab_fr_decouverte", "vocab_en_discover", "vocab_fr_revolutionnaire"):
            for name, pattern in (*PROSCRIBED_FR, *PROSCRIBED_EN):
                bounded = re.compile(rf"(?<!\w)(?:{pattern.pattern})(?!\w)")
                with self.subTest(rule=rule, term=name):
                    self.assertIsNone(bounded.search(normalise(rule)))

    def test_rest_of_the_report_is_untouched(self) -> None:
        clean = self.section(["propre"])
        dirty = self.section(["une découverte"])
        self.assertNotEqual(clean["judge_reasons"], dirty["judge_reasons"])
        for section in (clean, dirty):
            del section["judge_reasons"]
        self.assertEqual(clean, dirty)
        # Les champs du contrat sont tous là et le verdict n'a pas bougé.
        self.assertTrue(clean["passed"])
        self.assertEqual(clean["reasons"], [])
        for key in ("scores", "threshold", "thresholds", "doubts", "raw_verdict", "constats"):
            self.assertIn(key, clean)

    def test_reasons_are_still_bounded(self) -> None:
        stored = self.section(["x" * 500] + [f"raison {i}" for i in range(20)])["judge_reasons"]
        self.assertEqual(len(stored), JUDGE_REASONS_MAX)
        self.assertEqual(len(stored[0]), JUDGE_REASON_MAX_CHARS)

    def test_filter_reads_the_truncated_text(self) -> None:
        # Un terme proscrit repoussé au-delà de la troncature n'est pas stocké,
        # donc pas signalé : le filtre lit les octets réellement écrits.
        stored = self.section(["x" * JUDGE_REASON_MAX_CHARS + " découverte"])["judge_reasons"]
        self.assertEqual(stored, ["x" * JUDGE_REASON_MAX_CHARS])

class GuardCallTests(TempDatabase):
    """``guard_story`` avec un juge simulé, registre de coût compris."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.config = make_config(Path(self._tmp.name))
        with narrative_db.connect(self.db_path) as conn:
            narrative_db.ensure_narrative_schema(conn)
        self.inputs = extract_inputs(
            {"id": "SPR-2026-0001", "sharpened_data": json.dumps(sharpened(["Hydrology", "Chemistry"]))}
        )

    async def guard(self, script: FakeScript, story: dict | None = None) -> object:
        """Exécute le garde FR avec un script.

        Args:
            script: Réponses du juge.
            story: Récit (valide par défaut).

        Returns:
            Issue du garde.
        """
        with use_script(script):
            return await guard_story(
                story or good_story_fr(),
                self.inputs,
                lang="fr",
                config=self.config,
                db_path=self.db_path,
                run_label="pipeline",
                node="story_guard",
            )

    async def test_accept_publishes_and_records_cost(self) -> None:
        outcome = await self.guard(FakeScript({"story_guard": [judge_verdict()]}))
        self.assertEqual(outcome.decision, "published")
        self.assertEqual(outcome.judge_model, "mock")
        self.assertEqual(outcome.report["decision"], "published")
        self.assertTrue(outcome.report["mechanical"]["passed"])
        costs = rows(self.db_path, "SELECT * FROM v2_llm_costs")
        self.assertEqual(len(costs), 1)
        self.assertEqual((costs[0]["node"], costs[0]["run_label"]), ("story_guard", "pipeline"))
        self.assertGreater(costs[0]["cost_usd"], 0.0)

    async def test_parse_failure_rejects(self) -> None:
        script = FakeScript({"story_guard": ["ceci n'est pas du JSON"]})
        outcome = await self.guard(script)
        self.assertEqual(outcome.decision, "rejected")
        self.assertIn("judge:call_failed:JSONDecodeError", outcome.report["reasons"])
        # complete_json rejoue une fois à température nulle, pas davantage.
        self.assertEqual(script.count("story_guard"), 2)
        self.assertEqual(len(rows(self.db_path, "SELECT * FROM v2_llm_costs")), 2)

    async def test_doubt_rejects(self) -> None:
        outcome = await self.guard(FakeScript({"story_guard": [judge_verdict(doubts=["Doute."])]}))
        self.assertEqual(outcome.decision, "rejected")
        self.assertIn("judge:doubt", outcome.report["reasons"])

    async def test_score_under_threshold_rejects(self) -> None:
        outcome = await self.guard(FakeScript({"story_guard": [judge_verdict(overrides={"limit_present": 3})]}))
        self.assertEqual(outcome.decision, "rejected")
        self.assertIn("judge:below_threshold:limit_present", outcome.report["reasons"])

    async def test_call_error_rejects(self) -> None:
        outcome = await self.guard(FakeScript({"story_guard": [ValueError("clé absente")]}))
        self.assertEqual(outcome.decision, "rejected")

    async def test_mechanical_failure_skips_the_judge(self) -> None:
        script = FakeScript({"story_guard": [judge_verdict()]})
        outcome = await self.guard(script, good_story_fr(year=1999))
        self.assertEqual(outcome.decision, "rejected")
        self.assertTrue(outcome.report["judge"]["skipped"])
        self.assertEqual(script.count("story_guard"), 0)


if __name__ == "__main__":
    unittest.main()
