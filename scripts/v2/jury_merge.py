"""Fusion des fiches du jury en verdicts combinés (``DATA_CONTRACT.md``, section « Jury »).

Chaque récit relu par le jury produit deux fiches, une par lentille :
``<brief_id>__<lang>__a<attempt>__lecteur.json`` et ``…__chercheur.json``. Le
contrat de données attend un **seul** fichier par récit,
``<brief_id>__<lang>__a<attempt>.json``, portant les deux relectures et le
verdict combiné : ``accept`` seulement si les deux lentilles acceptent.

Le script ne juge rien et ne réécrit aucun texte : il apparie, contrôle et
recopie. Les contrôles sont fermants (aucun fichier écrit si l'un échoue) :

1. le nom du fichier doit s'accorder avec son contenu (``brief_id``, ``lang``,
   ``attempt``) ;
2. chaque fiche doit avoir sa jumelle : une lentille seule est une erreur, pas
   un demi-verdict ;
3. les deux lentilles doivent avoir lu le **même** texte : ``story_id`` et
   ``body_sha256`` identiques, sinon les verdicts ne portent pas sur le même
   récit ;
4. ``verdict`` vaut ``accept`` ou ``reject``, rien d'autre.

Le champ libre des fiches (``reasons``) devient ``notes`` dans le verdict
combiné, et ``lens`` disparaît : ce sont les deux seuls écarts de forme entre
une fiche et sa moitié de verdict.

Aucune itération ni aucun chemin de calibration n'est câblé ici : le même
script sert la calibration et le backfill.

Usage (depuis la racine du clone) ::

    PYTHONPATH=. python -m scripts.v2.jury_merge \\
        --parts /home/baq/Projects/spore-v2/docs/v2/reviews/jury/calibration/iter2 \\
        --out   /home/baq/Projects/spore-v2/docs/v2/reviews/jury/calibration/iter2

``--parts`` et ``--out`` peuvent désigner le même répertoire : les fiches se
terminent par ``__lecteur.json`` / ``__chercheur.json``, les verdicts non.
``--dry-run`` contrôle et compte sans rien écrire. Sortie : un résumé JSON sur
stdout ; journaux sur stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import structlog

from narrative.safety import UnsafePathError, assert_safe_write_path, resolve_path
from scripts.v2.backfill_narrative import _configure_logging

logger = structlog.get_logger("scripts.v2.jury_merge")

#: Lentilles du jury, dans l'ordre du contrat de données.
LENSES: tuple[str, ...] = ("lecteur", "chercheur")

#: Champs recopiés tels quels dans chaque moitié de verdict, par lentille.
#: ``reasons`` est traité à part (il devient ``notes``).
LENS_FIELDS: Mapping[str, tuple[str, ...]] = {
    "lecteur": ("understood", "wants_more", "knew_fiction"),
    "chercheur": ("fidelity", "overpromise", "separation_ok", "limits_honest"),
}

#: Champs d'identité, qui doivent concorder entre les deux lentilles.
IDENTITY_FIELDS: tuple[str, ...] = ("brief_id", "lang", "attempt", "story_id", "body_sha256")

#: ``<brief_id>__<lang>__a<attempt>__<lens>.json``
PART_NAME = re.compile(
    r"^(?P<brief_id>[A-Za-z0-9-]+)__(?P<lang>[a-z]{2})__a(?P<attempt>\d+)__(?P<lens>[a-z]+)\.json$"
)

VERDICTS: frozenset[str] = frozenset({"accept", "reject"})


class JuryMergeError(RuntimeError):
    """Fiches incohérentes, dépareillées ou illisibles : aucun fichier écrit."""


def build_parser() -> argparse.ArgumentParser:
    """Arguments de la ligne de commande.

    Returns:
        Analyseur.
    """
    parser = argparse.ArgumentParser(
        prog="scripts.v2.jury_merge",
        description="Fusionne les fiches du jury en verdicts combinés (DATA_CONTRACT, « Jury »).",
    )
    parser.add_argument(
        "--parts",
        type=Path,
        required=True,
        help="répertoire des fiches __lecteur.json / __chercheur.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="répertoire des verdicts combinés (peut être le même que --parts)",
    )
    parser.add_argument("--dry-run", action="store_true", help="contrôler et compter sans écrire")
    return parser


def _load_part(path: Path) -> dict[str, Any]:
    """Lit une fiche et contrôle son accord avec son nom de fichier.

    Args:
        path: Fiche ``…__<lens>.json``.

    Returns:
        Contenu de la fiche.

    Raises:
        JuryMergeError: JSON illisible, nom non conforme, ou désaccord entre le
            nom du fichier et le contenu.
    """
    match = PART_NAME.match(path.name)
    if not match:
        raise JuryMergeError(f"nom de fiche non conforme : {path.name}")
    lens = match.group("lens")
    if lens not in LENSES:
        raise JuryMergeError(f"lentille inconnue dans {path.name} : {lens!r}")
    try:
        part = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JuryMergeError(f"fiche illisible {path.name} : {exc}") from exc
    if not isinstance(part, dict):
        raise JuryMergeError(f"fiche {path.name} : objet JSON attendu")

    expected = {
        "brief_id": match.group("brief_id"),
        "lang": match.group("lang"),
        "attempt": int(match.group("attempt")),
        "lens": lens,
    }
    for field, value in expected.items():
        if part.get(field) != value:
            raise JuryMergeError(
                f"fiche {path.name} : {field} vaut {part.get(field)!r}, "
                f"le nom du fichier annonce {value!r}"
            )
    verdict = part.get("verdict")
    if verdict not in VERDICTS:
        raise JuryMergeError(f"fiche {path.name} : verdict {verdict!r} hors accept/reject")
    for field in LENS_FIELDS[lens]:
        if field not in part:
            raise JuryMergeError(f"fiche {path.name} : champ {field!r} absent")
    return part


def _half(part: Mapping[str, Any], lens: str) -> dict[str, Any]:
    """Moitié de verdict, au format du contrat de données.

    Args:
        part: Fiche d'une lentille.
        lens: ``lecteur`` ou ``chercheur``.

    Returns:
        Bloc ``lecteur`` ou ``chercheur`` : verdict, champs de la lentille,
        puis ``notes`` (le champ libre ``reasons`` de la fiche).
    """
    half: dict[str, Any] = {"verdict": part["verdict"]}
    for field in LENS_FIELDS[lens]:
        half[field] = part[field]
    half["notes"] = part.get("reasons", part.get("notes", ""))
    return half


def merge_pair(parts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Verdict combiné d'un récit, à partir des deux fiches.

    Args:
        parts: Fiches par lentille (``lecteur`` et ``chercheur``).

    Returns:
        Verdict combiné, au format de la section « Jury » du contrat.

    Raises:
        JuryMergeError: Les deux lentilles n'ont pas lu le même récit.
    """
    lecteur, chercheur = parts["lecteur"], parts["chercheur"]
    for field in IDENTITY_FIELDS:
        if lecteur.get(field) != chercheur.get(field):
            raise JuryMergeError(
                f"{lecteur.get('brief_id')} {lecteur.get('lang')} a{lecteur.get('attempt')} : "
                f"{field} diffère entre les lentilles "
                f"({lecteur.get(field)!r} / {chercheur.get(field)!r})"
            )
    verdicts = {lecteur["verdict"], chercheur["verdict"]}
    return {
        "brief_id": lecteur["brief_id"],
        "lang": lecteur["lang"],
        "attempt": lecteur["attempt"],
        "story_id": lecteur.get("story_id"),
        "body_sha256": lecteur.get("body_sha256"),
        "lecteur": _half(lecteur, "lecteur"),
        "chercheur": _half(chercheur, "chercheur"),
        "verdict": "accept" if verdicts == {"accept"} else "reject",
    }


def collect(parts_dir: Path) -> dict[tuple[str, str, int], dict[str, dict[str, Any]]]:
    """Apparie les fiches d'un répertoire par récit.

    Args:
        parts_dir: Répertoire des fiches (non récursif).

    Returns:
        Fiches par ``(brief_id, lang, attempt)``, puis par lentille.

    Raises:
        JuryMergeError: Répertoire absent, fiche en double, lentille seule, ou
            aucune fiche trouvée.
    """
    if not parts_dir.is_dir():
        raise JuryMergeError(f"répertoire des fiches absent : {parts_dir}")
    pairs: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for path in sorted(parts_dir.iterdir()):
        if not path.is_file() or not PART_NAME.match(path.name):
            continue
        part = _load_part(path)
        key = (part["brief_id"], part["lang"], int(part["attempt"]))
        bucket = pairs.setdefault(key, {})
        lens = part["lens"]
        if lens in bucket:
            raise JuryMergeError(f"fiche {lens} en double pour {key}")
        bucket[lens] = part
    if not pairs:
        raise JuryMergeError(f"aucune fiche du jury dans {parts_dir}")
    orphans = sorted(
        f"{brief_id}__{lang}__a{attempt} (sans {', '.join(sorted(set(LENSES) - set(bucket)))})"
        for (brief_id, lang, attempt), bucket in pairs.items()
        if set(bucket) != set(LENSES)
    )
    if orphans:
        raise JuryMergeError(f"fiches dépareillées : {'; '.join(orphans)}")
    return pairs


def merge_directory(parts_dir: Path, out_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Fusionne un répertoire de fiches et écrit les verdicts combinés.

    Tous les contrôles passent avant la première écriture : un lot incohérent
    ne laisse aucun fichier derrière lui.

    Args:
        parts_dir: Répertoire des fiches.
        out_dir: Répertoire des verdicts combinés.
        dry_run: Contrôler et compter sans écrire.

    Returns:
        Résumé : ``parts``, ``verdicts``, ``accept``, ``reject``, ``by_lang``,
        ``disagreements`` (récits où les deux lentilles divergent), ``written``.

    Raises:
        JuryMergeError: Un contrôle a échoué.
    """
    pairs = collect(parts_dir)
    merged = {key: merge_pair(parts) for key, parts in sorted(pairs.items())}

    by_lang: Counter[str] = Counter()
    disagreements: list[str] = []
    for (brief_id, lang, attempt), verdict in merged.items():
        if verdict["verdict"] == "accept":
            by_lang[lang] += 1
        if verdict["lecteur"]["verdict"] != verdict["chercheur"]["verdict"]:
            accepting = (
                "lecteur" if verdict["lecteur"]["verdict"] == "accept" else "chercheur"
            )
            disagreements.append(f"{brief_id}__{lang}__a{attempt} (accept : {accepting})")

    written: list[str] = []
    if not dry_run:
        assert_safe_write_path(out_dir, what="out")
        out_dir.mkdir(parents=True, exist_ok=True)
        for (brief_id, lang, attempt), verdict in merged.items():
            target = out_dir / f"{brief_id}__{lang}__a{attempt}.json"
            target.write_text(
                json.dumps(verdict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            written.append(target.name)

    accepted = sum(1 for verdict in merged.values() if verdict["verdict"] == "accept")
    summary = {
        "parts_dir": str(parts_dir),
        "out_dir": str(out_dir),
        "parts": sum(len(bucket) for bucket in pairs.values()),
        "verdicts": len(merged),
        "accept": accepted,
        "reject": len(merged) - accepted,
        "accept_by_lang": dict(sorted(by_lang.items())),
        "disagreements": disagreements,
        "written": len(written),
        "dry_run": dry_run,
    }
    logger.info("jury_merge_done", **{k: v for k, v in summary.items() if k != "disagreements"})
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée.

    Args:
        argv: Arguments (``sys.argv[1:]`` par défaut).

    Returns:
        Code de sortie : 0 si les verdicts sont écrits, 2 sur refus.
    """
    args = build_parser().parse_args(argv)
    _configure_logging()
    try:
        summary = merge_directory(
            resolve_path(args.parts), resolve_path(args.out), dry_run=args.dry_run
        )
    except (JuryMergeError, UnsafePathError) as exc:
        logger.error("jury_merge_refused", error=str(exc))
        return 2
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
