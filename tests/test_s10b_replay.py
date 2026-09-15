"""S10-B — rejeu des briefs bloqués par le gate de langue.

Couvre, sans appel LLM réel ni écriture dans la base de production :

* ``rehydrate_state`` : un ``PostFireState`` complet se reconstruit depuis une
  ligne ``briefs`` et le sidecar ; ``domains`` est retiré de ``sharpened`` ;
  l'ancienne vulgarisation et les colonnes EN ne sont jamais injectées ; une
  hypothèse absente ou des ``domains`` divergents lèvent.
* ``rewrite_brief_artifacts`` : le ``brief_id`` est conservé (une seule ligne,
  mêmes fichiers), ``created_at``, le score, le verdict et ``revision_count``
  sont inchangés, la date du brief n'est pas remise à aujourd'hui.
* ``replay_brief`` de bout en bout sur une base et un répertoire de sortie
  temporaires : promotion par ``node_validate_brief``, blocage en 'pending'
  quand la vulgarisation échoue puis reprise, dry-run sans écriture ni appel.
* ``replay_language_problems`` : une traduction EN identique au FR est refusée.

Le traducteur EN→FR réel est exercé ; seul son client LLM est remplacé par le
double de ``tests/test_s10a_panel_language.py``. La vulgarisation et la
traduction FR→EN sont remplacées au niveau des fonctions appelées par les
nœuds du graphe.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s10b_replay
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from agents.research_brief_generator import save_brief as write_brief_files  # noqa: E402
from graph.lang_guard import check_panel_language  # noqa: E402
from graph.post_fire_pipeline import _missing_required_fields  # noqa: E402
from graph.panel_coherence import check_panel  # noqa: E402
from scripts import replay_blocked_briefs as replay  # noqa: E402
from scripts.backup_blocked_briefs import create_backup  # noqa: E402
from storage import init_database  # noqa: E402
from storage import save_brief as save_brief_db  # noqa: E402
from tests.test_s10a_panel_language import (  # noqa: E402
    FakeTranslationClient,
    _patched_client,
    english_panel,
    mixed_panel,
)


BRIEF_ID = "SPR-2026-T0B1"
CREATED_AT = "2026-09-04 04:39:43"
DOMAINS = ["Cell Image Analysis Techniques", "Satellite Image Processing and Photogrammetry"]
HYPOTHESIS = "Les algorithmes de segmentation satellitaire détectent les divisions cellulaires."

# Fixtures à la forme exacte des sorties d'agents stockées en base (squelette
# relevé sur SPR-2026-4B85), contenu synthétique.
_PAPER: dict[str, Any] = {
    "paper_id": "p1",
    "title": "Segmentation of dividing cells",
    "doi": "10.0000/test.1",
    "year": 2020,
    "authors": ["A. Auteur"],
}
GROUNDING: dict[str, Any] = {
    "novelty_assessment": {
        "score": 0.7,
        "closest_existing_work": [
            {**_PAPER, "similarity": "moyenne", "key_difference": "échelle cellulaire"}
        ],
        "verdict": "novel",
    },
    "evidence_base": [
        {
            **_PAPER,
            "citation_count": 12,
            "support_type": "direct",
            "relevance": "haute",
            "key_finding": "La segmentation détecte les mitoses.",
        }
    ],
    "counter_evidence": [
        {**_PAPER, "paper_id": "p2", "finding": "Faux positifs fréquents.", "severity": "modérée"}
    ],
    "gap_manifest_update": {"closed_gaps": [], "new_gaps": ["transfert d'échelle"], "data_available": []},
    "all_papers": [{**_PAPER, "citation_count": 12, "abstract": "Résumé.", "tldr": "Résumé court."}],
    "search_queries": [{"query": "cell segmentation satellite", "type": "direct", "rationale": "base"}],
    "kill_reason": None,
}
SHARPENED: dict[str, Any] = {
    "title": "Segmentation satellitaire appliquée à la mitose",
    "formal_statement": "Si la segmentation satellitaire est appliquée, alors la détection progresse.",
    "independent_variables": [{"name": "algorithme", "type": "catégoriel", "range": "2 niveaux", "unit": "-"}],
    "dependent_variables": [{"name": "rappel", "type": "continu", "expected_direction": "hausse", "unit": "%"}],
    "proposed_mechanism": {
        "causal_chain": ["étape 1", "étape 2"],
        "key_assumptions": ["hypothèse"],
        "known_unknowns": ["inconnue"],
    },
    "falsifiable_predictions": [
        {
            "prediction": "Le rappel augmente.",
            "quantitative_bound": "> 5 %",
            "measurement_method": "annotation manuelle",
            "null_hypothesis": "aucune différence",
            "statistical_test": "test de McNemar",
        }
    ],
    "boundary_conditions": [{"condition": "images 2D", "justification": "algorithme 2D"}],
    "theoretical_framework": "Analyse d'image multi-échelle.",
}
PROTOCOL: dict[str, Any] = {
    "protocol_title": "Validation en trois phases",
    "overall_timeline": "12 mois",
    "overall_budget_estimate": "50 k€",
    "phases": [
        {
            "phase_number": 1,
            "phase_name": "Preuve de concept",
            "objective": "Mesurer le rappel.",
            "methodology": "Comparaison sur jeu annoté.",
            "required_resources": {
                "equipment": ["GPU"],
                "software": ["Python"],
                "datasets": ["jeu public"],
                "competences": ["vision"],
                "estimated_cost": "5 k€",
                "estimated_duration": "3 mois",
            },
            "expected_outputs": ["rapport"],
            "success_criteria": [{"metric": "rappel", "threshold": "> 0,8", "measurement": "annotation"}],
            "go_nogo_decision": {"go_if": "rappel > 0,8", "nogo_if": "rappel < 0,6", "pivot_if": "entre les deux"},
            "risks": [{"risk": "faux positifs", "probability": "moyenne", "mitigation": "filtrage"}],
        }
    ],
    "phase_1_quick_start": {
        "can_start_today": True,
        "first_action": "Télécharger le jeu public.",
        "tools_needed": ["Python"],
        "open_data_sources": ["jeu public"],
    },
}
POLLUTED_VULGARIZATION: dict[str, Any] = {
    "reviewers_say": "The panel thinks that the idea is interesting but the effect is small.",
}
FRENCH_VULGARIZATION: dict[str, Any] = {
    "hypothesis_in_brief": "Une idée venue de l'imagerie satellite pour compter les cellules.",
    "reviewers_say": "Le panel juge l'idée testable, mais l'effet reste à confirmer.",
}
ENGLISH_VULGARIZATION: dict[str, Any] = {
    "hypothesis_in_brief": "An idea from satellite imaging to count cells.",
    "reviewers_say": "The panel finds the idea testable, but the effect is yet to be confirmed.",
}


def _sha(path: Path) -> str:
    """SHA-256 d'un fichier."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(db_path: Path, brief_id: str = BRIEF_ID) -> dict[str, Any]:
    """Relit une ligne ``briefs``."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone())
    finally:
        conn.close()


def _briefs_row(panel: dict[str, Any]) -> dict[str, Any]:
    """Ligne ``briefs`` telle que ``node_research_brief`` l'écrit."""
    return {
        "id": BRIEF_ID,
        "hypothesis_id": BRIEF_ID,
        "created_at": CREATED_AT,
        "status": "pending",
        "panel_verdict": "publish_brief",
        "panel_consensus_score": 6.53,
        "revision_count": 1,
        "kill_reason": None,
        "is_stub": 0,
        "low_evidence": 0,
        "grounding_data": json.dumps(GROUNDING),
        "sharpened_data": json.dumps({**SHARPENED, "domains": DOMAINS}),
        "protocol_data": json.dumps(PROTOCOL),
        "panel_data": json.dumps(panel),
        "vulgarization_data": json.dumps(POLLUTED_VULGARIZATION),
        "panel_data_en": json.dumps(english_panel()),
        "vulgarization_data_en": json.dumps(POLLUTED_VULGARIZATION),
        "brief_md_path": f"/tmp/{BRIEF_ID}.md",
        "brief_json_path": f"/tmp/{BRIEF_ID}.json",
    }


def _sidecar() -> dict[str, Any]:
    """Sidecar ``.json`` d'origine."""
    return {"brief_id": BRIEF_ID, "domains": DOMAINS, "original_hypothesis": HYPOTHESIS}


# ── Rehydratation ───────────────────────────────────────────────────────


class RehydrateStateTests(unittest.TestCase):
    """Reconstruction de l'état depuis une ligne ``briefs``."""

    def test_state_is_complete_for_the_pipeline_tail(self) -> None:
        panel = english_panel()
        state = replay.rehydrate_state(_briefs_row(panel), _sidecar())

        self.assertEqual(state["brief_id"], BRIEF_ID)
        self.assertEqual(state["hypothesis_id"], BRIEF_ID)
        self.assertEqual(state["hypothesis"], HYPOTHESIS)
        self.assertEqual(state["domains"], DOMAINS)
        self.assertEqual(state["grounding"], GROUNDING)
        self.assertEqual(state["sharpened"], SHARPENED)
        self.assertNotIn("domains", state["sharpened"])
        self.assertEqual(state["protocol"], PROTOCOL)
        self.assertEqual(state["panel"], panel)
        self.assertEqual(state["revision_count"], 1)
        self.assertEqual(state["meta_verdict"], "publish_brief")
        self.assertFalse(state["is_stub"])
        self.assertFalse(state["grounding_degraded"])
        self.assertEqual(state["brief_json_path"], f"/tmp/{BRIEF_ID}.json")

    def test_derived_artifacts_are_never_injected(self) -> None:
        state = replay.rehydrate_state(_briefs_row(english_panel()), _sidecar())
        for key in ("vulgarization_fr", "vulgarization_en", "panel_en"):
            self.assertNotIn(key, state)
        # Sans vulgarisation régénérée, la validation retiendrait le brief.
        self.assertIn("vulgarization_fr", _missing_required_fields(state))

    def test_missing_hypothesis_raises(self) -> None:
        sidecar = _sidecar()
        del sidecar["original_hypothesis"]
        with self.assertRaises(replay.RehydrationError):
            replay.rehydrate_state(_briefs_row(english_panel()), sidecar)

    def test_domains_mismatch_raises(self) -> None:
        sidecar = {**_sidecar(), "domains": ["Autre", "Domaine"]}
        with self.assertRaises(replay.RehydrationError):
            replay.rehydrate_state(_briefs_row(english_panel()), sidecar)

    def test_foreign_sidecar_raises(self) -> None:
        sidecar = {**_sidecar(), "brief_id": "SPR-2026-XXXX"}
        with self.assertRaises(replay.RehydrationError):
            replay.rehydrate_state(_briefs_row(english_panel()), sidecar)

    def test_generation_date_comes_from_created_at(self) -> None:
        self.assertEqual(replay.generation_date(_briefs_row(english_panel())), date(2026, 9, 4))

    def test_scope(self) -> None:
        row = _briefs_row(english_panel())
        self.assertIsNone(replay.scope_skip_reason(row))
        self.assertEqual(replay.scope_skip_reason({**row, "status": "complete"}), "already_complete")
        self.assertEqual(replay.scope_skip_reason({**row, "status": "rejected"}), "status_rejected")
        self.assertEqual(replay.scope_skip_reason({**row, "is_stub": 1}), "stub")


class ReplayLanguageProblemsTests(unittest.TestCase):
    """Les trois critères de langue du rejeu."""

    def test_identical_translation_is_refused(self) -> None:
        panel = english_panel()
        problems = replay.replay_language_problems(panel, copy.deepcopy(panel))
        self.assertIn("panel_fr_cards_not_french", problems)
        self.assertIn("panel_en_card_0_identical_to_fr", problems)
        self.assertIn("panel_en_meta_identical_to_fr", problems)

    def test_missing_translation_is_refused(self) -> None:
        self.assertIn("panel_en_missing", replay.replay_language_problems(english_panel(), None))


# ── Écritures sur base temporaire ───────────────────────────────────────


class TempEnvironment(unittest.IsolatedAsyncioTestCase):
    """Base, sortie et sauvegarde temporaires ; settings redirigés."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "spore.db"
        self.output_dir = root / "outputs"
        self.briefs_dir = self.output_dir / "briefs"
        self.backup_root = root / "backups"
        self._env = mock.patch.dict(
            os.environ,
            {"SPORE_DB_PATH": str(self.db_path), "SPORE_OUTPUT_DIR": str(self.output_dir)},
        )
        self._env.start()
        self._saved_settings = config._settings
        config._settings = None
        await init_database()

    async def asyncTearDown(self) -> None:
        config._settings = self._saved_settings
        self._env.stop()
        self._tmp.cleanup()

    async def seed_blocked_brief(self, panel: dict[str, Any]) -> None:
        """Reproduit un brief pré-S10-A : ligne 'pending' + fichiers + EN pollué."""
        md_path, json_path = await write_brief_files(
            brief_id=BRIEF_ID,
            hypothesis=HYPOTHESIS,
            domains=DOMAINS,
            grounding=GROUNDING,
            sharpened=SHARPENED,
            protocol=PROTOCOL,
            panel=panel,
            vulgarization_fr=POLLUTED_VULGARIZATION,
            generated_on=date(2026, 9, 4),
        )
        sidecar = json.loads(json_path.read_text(encoding="utf-8"))
        sidecar["panel_en"] = english_panel()
        sidecar["vulgarization_en"] = POLLUTED_VULGARIZATION
        json_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

        await save_brief_db(
            brief_id=BRIEF_ID,
            hypothesis_id=BRIEF_ID,
            grounding_data=GROUNDING,
            sharpened_data={**SHARPENED, "domains": DOMAINS},
            protocol_data=PROTOCOL,
            panel_data=panel,
            vulgarization_data=POLLUTED_VULGARIZATION,
            status="pending",
            brief_md_path=str(md_path),
            brief_json_path=str(json_path),
            revision_count=1,
            body_markdown=md_path.read_text(encoding="utf-8"),
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE briefs SET created_at = ?, panel_data_en = ?, vulgarization_data_en = ? WHERE id = ?",
            (CREATED_AT, json.dumps(english_panel()), json.dumps(POLLUTED_VULGARIZATION), BRIEF_ID),
        )
        conn.commit()
        conn.close()

    def make_backup(self) -> Path:
        """Sauvegarde S10-B du brief de test."""
        backup_dir = self.backup_root / "s10b-test"
        create_backup(self.db_path, [BRIEF_ID], backup_dir)
        return backup_dir


def _fake_vulgarization(error: Exception | None = None) -> mock.AsyncMock:
    """Double de ``vulgarization_agent``."""
    if error is not None:
        return mock.AsyncMock(side_effect=error)
    return mock.AsyncMock(return_value=copy.deepcopy(FRENCH_VULGARIZATION))


def _patched_tail(vulgarization: mock.AsyncMock) -> list[Any]:
    """Remplace la vulgarisation et la traduction FR→EN appelées par les nœuds."""
    return [
        mock.patch("graph.post_fire_pipeline.vulgarization_agent", vulgarization),
        mock.patch(
            "graph.post_fire_pipeline.translate_panel_data",
            mock.AsyncMock(return_value=(english_panel(), [], {"cost_usd": 0.0})),
        ),
        mock.patch(
            "graph.post_fire_pipeline.translate_vulgarization_data",
            mock.AsyncMock(return_value=(copy.deepcopy(ENGLISH_VULGARIZATION), [], {"cost_usd": 0.0})),
        ),
    ]


class RewriteBriefArtifactsTests(TempEnvironment):
    """Conservation du ``brief_id`` et des colonnes invariantes."""

    async def test_brief_id_and_invariants_are_preserved(self) -> None:
        await self.seed_blocked_brief(english_panel())
        before = _row(self.db_path)
        state = replay.rehydrate_state(before, _sidecar())
        french = mixed_panel()
        state = {**state, "panel": french}

        state = await replay.rewrite_brief_artifacts(state, replay.generation_date(before))

        conn = sqlite3.connect(self.db_path)
        ids = [r[0] for r in conn.execute("SELECT id FROM briefs")]
        conn.close()
        self.assertEqual(ids, [BRIEF_ID])
        self.assertEqual(state["brief_id"], BRIEF_ID)
        self.assertEqual(sorted(p.name for p in self.briefs_dir.iterdir()), [f"{BRIEF_ID}.json", f"{BRIEF_ID}.md"])

        after = _row(self.db_path)
        for col in ("created_at", "panel_consensus_score", "panel_verdict", "revision_count", "hypothesis_id", "status"):
            self.assertEqual(after[col], before[col], col)
        self.assertEqual(json.loads(after["panel_data"]), french)
        md = (self.briefs_dir / f"{BRIEF_ID}.md").read_text(encoding="utf-8")
        self.assertEqual(after["body_markdown"], md)
        self.assertIn("**Date de generation**: 2026-09-04", md)
        self.assertIsNone(after["vulgarization_data"])
        self.assertIsNone(after["panel_data_en"])
        self.assertIsNone(after["vulgarization_data_en"])
        sidecar = json.loads((self.briefs_dir / f"{BRIEF_ID}.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["generated_at"], "2026-09-04")
        self.assertEqual(sidecar["original_hypothesis"], HYPOTHESIS)


class ReplayBriefTests(TempEnvironment):
    """Rejeu complet sur base temporaire."""

    async def test_blocked_brief_is_promoted_with_same_id(self) -> None:
        await self.seed_blocked_brief(english_panel())
        backup_dir = self.make_backup()
        before = _row(self.db_path)
        client = FakeTranslationClient()

        patches = _patched_tail(_fake_vulgarization())
        with _patched_client(client), patches[0], patches[1], patches[2]:
            result = await replay.replay_brief(BRIEF_ID, dry_run=False, backup_dir=backup_dir)

        self.assertEqual(result.outcome, "completed", result)
        self.assertEqual(result.cards_translated, 5)
        self.assertTrue(result.meta_translated)
        self.assertGreater(len(client.calls), 0)

        after = _row(self.db_path)
        self.assertEqual(after["status"], "complete")
        for col in ("id", "created_at", "panel_consensus_score", "panel_verdict", "revision_count"):
            self.assertEqual(after[col], before[col], col)
        panel = json.loads(after["panel_data"])
        self.assertEqual(check_panel_language(panel, "fr"), [])
        self.assertEqual(check_panel(panel), [])
        self.assertEqual(json.loads(after["vulgarization_data"]), FRENCH_VULGARIZATION)
        self.assertEqual(json.loads(after["vulgarization_data_en"]), ENGLISH_VULGARIZATION)
        self.assertEqual(replay.sidecar_mismatches(after, Path(after["brief_json_path"])), [])
        self.assertEqual(
            after["body_markdown"], (self.briefs_dir / f"{BRIEF_ID}.md").read_text(encoding="utf-8")
        )

        # Rejouable : un brief promu n'est pas retraité.
        again = await replay.replay_brief(BRIEF_ID, dry_run=False, backup_dir=backup_dir)
        self.assertEqual((again.outcome, again.reason), ("skipped", "already_complete"))

    async def test_failed_vulgarization_stays_pending_then_resumes(self) -> None:
        await self.seed_blocked_brief(english_panel())
        backup_dir = self.make_backup()

        patches = _patched_tail(_fake_vulgarization(RuntimeError("LLM indisponible")))
        with _patched_client(FakeTranslationClient()), patches[0], patches[1], patches[2]:
            blocked = await replay.replay_brief(BRIEF_ID, dry_run=False, backup_dir=backup_dir)

        self.assertEqual((blocked.outcome, blocked.reason), ("still_blocked", "vulgarization_failed"))
        row = _row(self.db_path)
        self.assertEqual(row["status"], "pending")
        # L'ancienne vulgarisation polluée n'a pas survécu au rejeu partiel.
        self.assertIsNone(row["vulgarization_data"])

        # Le sidecar a été réécrit : la reprise relit l'hypothèse dans la sauvegarde.
        client = FakeTranslationClient()
        patches = _patched_tail(_fake_vulgarization())
        with _patched_client(client), patches[0], patches[1], patches[2]:
            resumed = await replay.replay_brief(BRIEF_ID, dry_run=False, backup_dir=backup_dir)

        self.assertEqual(resumed.outcome, "completed", resumed)
        self.assertEqual(client.calls, [])  # panel déjà normalisé : aucune traduction EN→FR
        self.assertEqual(_row(self.db_path)["status"], "complete")

    async def test_dry_run_writes_nothing_and_calls_nothing(self) -> None:
        await self.seed_blocked_brief(english_panel())
        backup_dir = self.make_backup()
        files = sorted(self.briefs_dir.iterdir())
        hashes = {p.name: _sha(p) for p in files}
        before = _row(self.db_path)
        client = FakeTranslationClient()
        vulgarization = _fake_vulgarization()

        patches = _patched_tail(vulgarization)
        with _patched_client(client), patches[0], patches[1], patches[2]:
            result = await replay.replay_brief(BRIEF_ID, dry_run=True, backup_dir=backup_dir)

        self.assertEqual(result.outcome, "dry_run")
        self.assertGreater(result.cost_usd, 0.0)
        self.assertEqual(client.calls, [])
        vulgarization.assert_not_awaited()
        self.assertEqual(_row(self.db_path), before)
        self.assertEqual({p.name: _sha(p) for p in sorted(self.briefs_dir.iterdir())}, hashes)

    async def test_without_backup_nothing_is_written(self) -> None:
        await self.seed_blocked_brief(english_panel())
        before = _row(self.db_path)
        with mock.patch.object(replay, "BACKUP_ROOT", self.backup_root):
            result = await replay.replay_brief(BRIEF_ID, dry_run=False)
        self.assertEqual((result.outcome, result.reason), ("still_blocked", "no_verified_backup"))
        self.assertEqual(_row(self.db_path), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
