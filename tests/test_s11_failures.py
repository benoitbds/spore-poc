"""S11-B.5 — pannes techniques persistées, exclues, et portées au digest.

Couvre, sans appel LLM réel :

* un nœud qui lève écrit une ligne ``failed_<nœud>`` portant les blobs déjà
  produits, et l'exception poursuit sa route ;
* le panel et la meta-review, qui échouent dans le même nœud, reçoivent des
  statuts distincts ;
* le rejeu est idempotent : une seconde panne met à jour la même ligne, sans
  remettre ``created_at`` à maintenant ni effacer les blobs ;
* une écriture impossible ne masque jamais la panne d'origine ;
* les lignes ``failed_*`` sortent des calculs publics ;
* le digest les classe en quatre familles.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s11_failures
"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import graph.post_fire_pipeline as post_fire  # noqa: E402
from agents.multi_reviewer_panel import MetaReviewFailed, PanelReviewFailed  # noqa: E402
from llm.errors import LLMOutputIncomplete, LLMOutputTruncated  # noqa: E402
from scripts.daily_pipeline_digest import (  # noqa: E402
    classify_failure,
    failed_briefs,
)
from storage import save_failed_brief  # noqa: E402
from storage.database import BRIEF_EXISTS_STATUSES  # noqa: E402
from tests.test_s11_llm_contract import TempDatabase  # noqa: E402

HYPOTHESIS = "SPORE-2026-09-16-8bc4e466"


def truncation() -> LLMOutputTruncated:
    """Troncature représentative de celles de septembre.

    Returns:
        Exception renseignée comme le ferait la couche client.
    """
    return LLMOutputTruncated(
        "sortie tronquée par le plafond",
        node="experimental_protocol",
        provider="deepseek",
        model="deepseek-flash",
        finish_reason="length",
        max_tokens=16000,
        output_tokens=16000,
        attempt=2,
    )


class FailureRowTests(TempDatabase):
    """Ce que le nœud laisse derrière lui quand il échoue."""

    def briefs(self) -> list[sqlite3.Row]:
        """Lit la table des briefs.

        Returns:
            Toutes les lignes.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM briefs").fetchall()
        finally:
            conn.close()

    async def run_failing_node(
        self, node_name: str, exc: BaseException, state: dict[str, Any]
    ) -> None:
        """Exécute un nœud décoré qui lève.

        Args:
            node_name: Nom du nœud.
            exc: Exception à lever.
            state: État transmis au nœud.
        """

        @post_fire.logged_node(node_name)
        async def node(_state: dict[str, Any]) -> dict[str, Any]:
            raise exc

        with self.assertRaises(type(exc)):
            await node(state)

    async def test_a_failing_protocol_leaves_a_row_with_its_blobs(self) -> None:
        await self.run_failing_node(
            "experimental_protocol",
            truncation(),
            {
                "hypothesis_id": HYPOTHESIS,
                "run_id": "run-1",
                "grounding": {"evidence_base": [{"title": "Papier"}]},
                "sharpened": {"title": "Titre affûté"},
            },
        )

        rows = self.briefs()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], HYPOTHESIS)
        self.assertEqual(row["hypothesis_id"], HYPOTHESIS)
        self.assertEqual(row["status"], "failed_protocol")
        self.assertTrue(row["failure_reason"].startswith("LLMOutputTruncated: "))
        self.assertIn("output_tokens=16000", row["failure_reason"])
        # Les blobs déjà produits sont conservés : c'est ce qui rend le rejeu
        # possible sans tout régénérer.
        self.assertEqual(json.loads(row["sharpened_data"]), {"title": "Titre affûté"})
        self.assertIsNotNone(row["grounding_data"])
        self.assertIsNone(row["protocol_data"])
        # La panne n'est pas un rejet scientifique : kill_reason reste vide.
        self.assertIsNone(row["kill_reason"])

    async def test_panel_and_meta_review_get_distinct_statuses(self) -> None:
        await self.run_failing_node(
            "multi_reviewer_panel",
            PanelReviewFailed(persona="contrarian", cause=truncation()),
            {"hypothesis_id": HYPOTHESIS},
        )
        self.assertEqual(self.briefs()[0]["status"], "failed_panel")

        await self.run_failing_node(
            "multi_reviewer_panel",
            MetaReviewFailed(iteration=2, cause=ValueError("illisible")),
            {"hypothesis_id": HYPOTHESIS},
        )
        rows = self.briefs()
        # Même hypothèse, donc toujours une seule ligne — mise à jour.
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failed_meta_review")

    async def test_the_brief_id_is_used_once_it_exists(self) -> None:
        await self.run_failing_node(
            "vulgarization",
            LLMOutputIncomplete(
                "fin inattendue",
                node="vulgarization",
                provider="deepseek",
                model="deepseek-flash",
                finish_reason="content_filter",
                max_tokens=3000,
                output_tokens=12,
                attempt=1,
            ),
            {"hypothesis_id": HYPOTHESIS, "brief_id": "SPR-2026-ABCD"},
        )
        row = self.briefs()[0]
        self.assertEqual(row["id"], "SPR-2026-ABCD")
        self.assertEqual(row["hypothesis_id"], HYPOTHESIS)
        self.assertEqual(row["status"], "failed_vulgarization")

    async def test_replaying_a_failure_updates_the_same_row(self) -> None:
        await save_failed_brief(
            row_id=HYPOTHESIS,
            hypothesis_id=HYPOTHESIS,
            status="failed_protocol",
            failure_reason="LLMOutputTruncated: premier essai",
            sharpened_data={"title": "Titre"},
        )
        first = self.briefs()[0]

        await save_failed_brief(
            row_id=HYPOTHESIS,
            hypothesis_id=HYPOTHESIS,
            status="failed_panel",
            failure_reason="PanelReviewFailed: second essai",
        )
        rows = self.briefs()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "failed_panel")
        self.assertEqual(row["failure_reason"], "PanelReviewFailed: second essai")
        # Ni created_at remis à maintenant, ni blob effacé : c'est ce que
        # ferait INSERT OR REPLACE, et c'est ce qu'on évite (constat S10-B).
        self.assertEqual(row["created_at"], first["created_at"])
        self.assertEqual(json.loads(row["sharpened_data"]), {"title": "Titre"})

    async def test_a_persistence_node_does_not_write_another_row(self) -> None:
        await self.run_failing_node(
            "persist_panel_reject",
            RuntimeError("base indisponible"),
            {"hypothesis_id": HYPOTHESIS},
        )
        self.assertEqual(self.briefs(), [])

    async def test_a_failed_write_never_masks_the_original_error(self) -> None:
        with mock.patch.object(
            post_fire, "save_failed_brief", side_effect=sqlite3.OperationalError("verrou")
        ):
            # C'est bien la troncature qui remonte, pas l'erreur d'écriture.
            await self.run_failing_node(
                "experimental_protocol", truncation(), {"hypothesis_id": HYPOTHESIS}
            )
        self.assertEqual(self.briefs(), [])

    async def test_failed_rows_stay_out_of_the_public_counts(self) -> None:
        await save_failed_brief(
            row_id=HYPOTHESIS,
            hypothesis_id=HYPOTHESIS,
            status="failed_protocol",
            failure_reason="LLMOutputTruncated: x",
        )
        conn = sqlite3.connect(self.db_path)
        try:
            placeholders = ",".join("?" * len(BRIEF_EXISTS_STATUSES))
            counted = conn.execute(
                f"SELECT COUNT(*) FROM briefs WHERE status IN ({placeholders})",
                BRIEF_EXISTS_STATUSES,
            ).fetchone()[0]
            denylist = conn.execute(
                "SELECT COUNT(*) FROM briefs WHERE status != 'rejected'"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(counted, 0)
        # La liste de refus, elle, aurait compté la panne comme un brief.
        self.assertEqual(denylist, 1)
        self.assertNotIn("failed_protocol", BRIEF_EXISTS_STATUSES)


class GraphIntegrationTests(TempDatabase):
    """Le vrai graphe, avec ses arêtes, pas seulement le décorateur."""

    GROUNDING: dict[str, Any] = {
        "evidence_base": [{"title": "Papier"}],
        "counter_evidence": [],
        "novelty_assessment": {"score": 0.8, "verdict": "novel"},
        "kill_reason": None,
    }
    SHARPENED: dict[str, Any] = {
        "title": "Titre",
        "formal_statement": "Énoncé",
        "independent_variables": [],
        "dependent_variables": [],
        "falsifiable_predictions": [],
        "proposed_mechanism": {},
        "boundary_conditions": [],
        "theoretical_framework": "biophysique",
    }

    async def test_a_truncated_protocol_stops_the_run_and_leaves_a_row(self) -> None:
        async def grounding(_inp: Any) -> dict[str, Any]:
            return self.GROUNDING

        async def sharpening(_inp: Any) -> dict[str, Any]:
            return self.SHARPENED

        async def protocol(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise truncation()

        with mock.patch.object(post_fire, "literature_grounding_agent", grounding), \
             mock.patch.object(post_fire, "hypothesis_sharpening_agent", sharpening), \
             mock.patch.object(post_fire, "experimental_protocol_agent", protocol), \
             mock.patch.object(post_fire, "is_ss_circuit_open", lambda: False):
            # LangGraph laisse remonter l'exception du nœud telle quelle : le
            # run s'arrête là, il ne produit pas de brief incomplet.
            with self.assertRaises(LLMOutputTruncated):
                await post_fire.run_post_fire_pipeline(
                    hypothesis="H",
                    domains=["A", "B"],
                    mechanisms="M",
                    run_id="run-probe",
                    hypothesis_id=HYPOTHESIS,
                )

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM briefs").fetchall()
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["id"], row["status"]), (HYPOTHESIS, "failed_protocol"))
        # Les deux nœuds réussis ont laissé leur travail : un rejeu n'aura pas
        # à refaire le grounding, qui coûte des appels à Semantic Scholar.
        self.assertIsNotNone(row["grounding_data"])
        self.assertIsNotNone(row["sharpened_data"])


class DigestTests(TempDatabase):
    """Ce que Baq lit le lendemain matin."""

    def test_the_four_families(self) -> None:
        self.assertEqual(classify_failure("LLMOutputTruncated: coupé"), "sortie tronquée")
        self.assertEqual(classify_failure("JSONDecodeError: Unterminated"), "JSON invalide")
        self.assertEqual(classify_failure("ValueError: Failed to parse"), "JSON invalide")
        self.assertEqual(
            classify_failure("LLMOutputIncomplete: content_filter"), "génération interrompue"
        )
        self.assertEqual(
            classify_failure("LLMResourceExhausted: plus de capacité"),
            "génération interrompue",
        )
        self.assertEqual(classify_failure("RuntimeError: autre chose"), "autre")
        self.assertEqual(classify_failure(None), "autre")

    async def test_only_the_anchor_day_is_reported(self) -> None:
        await save_failed_brief(
            row_id="SPORE-hier",
            hypothesis_id="SPORE-hier",
            status="failed_protocol",
            failure_reason="LLMOutputTruncated: hier",
        )
        await save_failed_brief(
            row_id="SPORE-aujourdhui",
            hypothesis_id="SPORE-aujourdhui",
            status="failed_panel",
            failure_reason="PanelReviewFailed: aujourd'hui",
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE briefs SET created_at = '2026-09-15 04:48:00' WHERE id = 'SPORE-hier'"
        )
        conn.execute(
            "UPDATE briefs SET created_at = '2026-09-16 04:48:00' WHERE id = 'SPORE-aujourdhui'"
        )
        conn.commit()
        conn.close()

        rows = failed_briefs(self.db_path, "2026-09-16")
        self.assertEqual([r["id"] for r in rows], ["SPORE-aujourdhui"])
        self.assertEqual(rows[0]["status"], "failed_panel")

    async def test_published_rows_are_not_reported_as_failures(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO briefs (id, hypothesis_id, status, created_at) "
            "VALUES ('SPR-2026-0001', 'h', 'complete', '2026-09-16 04:50:00')"
        )
        conn.commit()
        conn.close()
        self.assertEqual(failed_briefs(self.db_path, "2026-09-16"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
