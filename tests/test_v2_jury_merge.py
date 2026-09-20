"""Fusion des fiches du jury en verdicts combinés (``scripts.v2.jury_merge``).

Le script ne fait qu'apparier, contrôler et recopier : les tests portent donc
sur la forme du verdict combiné (section « Jury » de ``DATA_CONTRACT.md``), sur
la règle du verdict (``accept`` seulement si les deux lentilles acceptent) et
sur les quatre refus fermants — nom de fichier en désaccord avec le contenu,
lentille seule, lentilles qui n'ont pas lu le même récit, verdict hors
``accept``/``reject``. Un refus ne doit laisser aucun fichier derrière lui.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.v2.jury_merge import JuryMergeError, main, merge_directory

BRIEF = "SPR-2026-0669"
SHA = "a" * 64


def part(
    lens: str,
    *,
    verdict: str = "accept",
    brief_id: str = BRIEF,
    lang: str = "fr",
    attempt: int = 1,
    story_id: int = 35,
    body_sha256: str = SHA,
    **extra: Any,
) -> dict[str, Any]:
    """Fiche d'une lentille, au format réellement produit par le jury.

    Args:
        lens: ``lecteur`` ou ``chercheur``.
        verdict: Verdict de la lentille.
        brief_id: Brief.
        lang: Langue.
        attempt: Tentative.
        story_id: Récit relu.
        body_sha256: Empreinte du corps relu.
        **extra: Champs à remplacer ou à ajouter.

    Returns:
        Fiche.
    """
    fiche: dict[str, Any] = {
        "brief_id": brief_id,
        "lang": lang,
        "attempt": attempt,
        "story_id": story_id,
        "body_sha256": body_sha256,
        "lens": lens,
        "verdict": verdict,
        "reasons": f"notes du {lens}",
    }
    if lens == "lecteur":
        fiche |= {"understood": True, "wants_more": False, "knew_fiction": True}
    else:
        fiche |= {"fidelity": 8, "overpromise": False, "separation_ok": True, "limits_honest": True}
    fiche |= extra
    return fiche


class JuryMergeTests(unittest.TestCase):
    """Appariement, forme du verdict et refus fermants."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.parts = Path(self._tmp.name) / "parts"
        self.out = Path(self._tmp.name) / "out"
        self.parts.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, fiche: dict[str, Any], *, name: str | None = None) -> Path:
        """Écrit une fiche, nommée d'après son contenu sauf indication.

        Args:
            fiche: Fiche à écrire.
            name: Nom de fichier imposé (pour les cas de désaccord).

        Returns:
            Chemin écrit.
        """
        target = self.parts / (
            name
            or f"{fiche['brief_id']}__{fiche['lang']}__a{fiche['attempt']}__{fiche['lens']}.json"
        )
        target.write_text(json.dumps(fiche, ensure_ascii=False), encoding="utf-8")
        return target

    def pair(self, **changes: Any) -> None:
        """Écrit les deux fiches d'un récit.

        Args:
            **changes: Champs communs aux deux fiches.
        """
        self.write(part("lecteur", **changes))
        self.write(part("chercheur", **changes))

    # ── Forme et règle du verdict ───────────────────────────────────

    def test_combined_file_matches_data_contract(self) -> None:
        self.pair()
        summary = merge_directory(self.parts, self.out)
        self.assertEqual(summary["verdicts"], 1)
        self.assertEqual(summary["written"], 1)
        combined = json.loads((self.out / f"{BRIEF}__fr__a1.json").read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(combined),
            sorted(
                [
                    "brief_id",
                    "lang",
                    "attempt",
                    "story_id",
                    "body_sha256",
                    "lecteur",
                    "chercheur",
                    "verdict",
                ]
            ),
        )
        self.assertEqual(combined["verdict"], "accept")
        self.assertEqual(combined["story_id"], 35)
        self.assertEqual(
            sorted(combined["lecteur"]),
            sorted(["verdict", "understood", "wants_more", "knew_fiction", "notes"]),
        )
        self.assertEqual(
            sorted(combined["chercheur"]),
            sorted(
                ["verdict", "fidelity", "overpromise", "separation_ok", "limits_honest", "notes"]
            ),
        )
        # ``reasons`` devient ``notes`` ; ``lens`` disparaît.
        self.assertEqual(combined["lecteur"]["notes"], "notes du lecteur")
        self.assertNotIn("lens", combined["lecteur"])
        self.assertNotIn("reasons", combined["lecteur"])

    def test_accept_requires_both_lenses(self) -> None:
        for lecteur, chercheur, expected in (
            ("accept", "accept", "accept"),
            ("accept", "reject", "reject"),
            ("reject", "accept", "reject"),
            ("reject", "reject", "reject"),
        ):
            with self.subTest(lecteur=lecteur, chercheur=chercheur):
                for stale in self.parts.iterdir():
                    stale.unlink()
                self.write(part("lecteur", verdict=lecteur))
                self.write(part("chercheur", verdict=chercheur))
                merge_directory(self.parts, self.out)
                combined = json.loads(
                    (self.out / f"{BRIEF}__fr__a1.json").read_text(encoding="utf-8")
                )
                self.assertEqual(combined["verdict"], expected)

    def test_disagreements_are_reported(self) -> None:
        self.write(part("lecteur", verdict="reject"))
        self.write(part("chercheur", verdict="accept"))
        summary = merge_directory(self.parts, self.out, dry_run=True)
        self.assertEqual(summary["disagreements"], [f"{BRIEF}__fr__a1 (accept : chercheur)"])
        self.assertEqual(summary["accept"], 0)

    def test_accept_counted_by_lang(self) -> None:
        self.pair(lang="fr")
        self.pair(lang="en")
        self.write(part("lecteur", lang="en", attempt=2, verdict="reject"))
        self.write(part("chercheur", lang="en", attempt=2))
        summary = merge_directory(self.parts, self.out)
        self.assertEqual(summary["verdicts"], 3)
        self.assertEqual(summary["accept_by_lang"], {"en": 1, "fr": 1})

    def test_dry_run_writes_nothing(self) -> None:
        self.pair()
        summary = merge_directory(self.parts, self.out, dry_run=True)
        self.assertEqual(summary["verdicts"], 1)
        self.assertEqual(summary["written"], 0)
        self.assertFalse(self.out.exists())

    def test_merge_is_idempotent(self) -> None:
        self.pair()
        first = merge_directory(self.parts, self.out)
        target = self.out / f"{BRIEF}__fr__a1.json"
        content = target.read_text(encoding="utf-8")
        second = merge_directory(self.parts, self.out)
        self.assertEqual(first, second)
        self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_parts_and_out_may_be_the_same_directory(self) -> None:
        self.pair()
        merge_directory(self.parts, self.parts)
        self.assertTrue((self.parts / f"{BRIEF}__fr__a1.json").exists())
        # Le verdict combiné n'est pas repris pour une fiche au tour suivant.
        summary = merge_directory(self.parts, self.parts)
        self.assertEqual(summary["parts"], 2)
        self.assertEqual(summary["verdicts"], 1)

    # ── Refus fermants ──────────────────────────────────────────────

    def test_lone_lens_refused(self) -> None:
        self.write(part("lecteur"))
        with self.assertRaises(JuryMergeError) as caught:
            merge_directory(self.parts, self.out)
        self.assertIn("dépareillées", str(caught.exception))
        self.assertIn("chercheur", str(caught.exception))
        self.assertFalse(self.out.exists())

    def test_lenses_on_different_texts_refused(self) -> None:
        self.write(part("lecteur", body_sha256="b" * 64))
        self.write(part("chercheur"))
        with self.assertRaises(JuryMergeError) as caught:
            merge_directory(self.parts, self.out)
        self.assertIn("body_sha256", str(caught.exception))
        self.assertFalse(self.out.exists())

    def test_lenses_on_different_story_ids_refused(self) -> None:
        self.write(part("lecteur", story_id=35))
        self.write(part("chercheur", story_id=36))
        with self.assertRaises(JuryMergeError) as caught:
            merge_directory(self.parts, self.out)
        self.assertIn("story_id", str(caught.exception))

    def test_filename_must_match_content(self) -> None:
        self.write(part("lecteur"), name=f"{BRIEF}__fr__a2__lecteur.json")
        self.write(part("chercheur"))
        with self.assertRaises(JuryMergeError) as caught:
            merge_directory(self.parts, self.out)
        self.assertIn("attempt", str(caught.exception))

    def test_unknown_verdict_refused(self) -> None:
        self.write(part("lecteur", verdict="peut-être"))
        self.write(part("chercheur"))
        with self.assertRaises(JuryMergeError) as caught:
            merge_directory(self.parts, self.out)
        self.assertIn("verdict", str(caught.exception))

    def test_missing_lens_field_refused(self) -> None:
        fiche = part("chercheur")
        del fiche["fidelity"]
        self.write(part("lecteur"))
        self.write(fiche)
        with self.assertRaises(JuryMergeError) as caught:
            merge_directory(self.parts, self.out)
        self.assertIn("fidelity", str(caught.exception))

    def test_unreadable_part_refused(self) -> None:
        self.write(part("lecteur"))
        (self.parts / f"{BRIEF}__fr__a1__chercheur.json").write_text("{ pas du json", encoding="utf-8")
        with self.assertRaises(JuryMergeError) as caught:
            merge_directory(self.parts, self.out)
        self.assertIn("illisible", str(caught.exception))

    def test_nothing_written_when_one_pair_is_broken(self) -> None:
        self.pair(brief_id="SPR-2026-072C")
        self.write(part("lecteur", body_sha256="c" * 64))
        self.write(part("chercheur"))
        with self.assertRaises(JuryMergeError):
            merge_directory(self.parts, self.out)
        self.assertFalse(self.out.exists())

    def test_empty_and_missing_directories_refused(self) -> None:
        with self.assertRaises(JuryMergeError):
            merge_directory(self.parts, self.out)
        with self.assertRaises(JuryMergeError):
            merge_directory(self.parts / "absent", self.out)

    def test_foreign_files_are_ignored(self) -> None:
        self.pair()
        (self.parts / "summary.json").write_text("{}", encoding="utf-8")
        (self.parts / "notes.md").write_text("texte", encoding="utf-8")
        summary = merge_directory(self.parts, self.out)
        self.assertEqual(summary["parts"], 2)
        self.assertEqual(summary["verdicts"], 1)

    # ── Ligne de commande ───────────────────────────────────────────

    def test_main_returns_two_on_refusal(self) -> None:
        self.write(part("lecteur"))
        self.assertEqual(main(["--parts", str(self.parts), "--out", str(self.out)]), 2)
        self.assertFalse(self.out.exists())

    def test_main_returns_zero_and_writes(self) -> None:
        self.pair()
        self.assertEqual(main(["--parts", str(self.parts), "--out", str(self.out)]), 0)
        self.assertTrue((self.out / f"{BRIEF}__fr__a1.json").exists())


if __name__ == "__main__":
    unittest.main()
