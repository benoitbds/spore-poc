"""Contrôle mécanique de cohérence score ↔ sentiment sur les cartes reviewer.

Motivation — SPR-2026-1A7E : l'avocat du diable note 3,5/10, rend un verdict
``weak_reject``, et la carte affichée sur le site porte un commentaire
entièrement favorable (« The hypothesis is clear and testable… »). Le lecteur
voit une note basse à côté d'un éloge, sans la réserve qui la justifie.

La cause n'est pas une donnée corrompue : ``weaknesses`` contient bien les
FAIL REASON. C'est ``ReviewerPanel.tsx`` qui affiche ``strengths[0]`` quel que
soit le verdict. Ce module vérifie donc le texte **effectivement affiché**,
pas le blob complet — un panel dont les réserves existent mais ne sont jamais
montrées produit exactement l'incohérence signalée.

Pas de LLM : lexique + expressions régulières, conformément au pattern des
autres gates du projet (seuils du méta-reviewer, override Python du consensus).
Fail-closed : à la moindre incohérence, le brief ne promeut pas.
"""

from __future__ import annotations

import re
from typing import Any

# Un verdict négatif ou une note sous ce plancher exige une réserve visible.
SCORE_FLOOR = 5.0
NEGATIVE_VERDICTS = frozenset({"weak_reject", "reject"})

# Le lexique est bilingue : plusieurs cartes sont rédigées en anglais sur des
# pages françaises (cf. SPR-2026-D460 / SPR-2026-7626), donc un lexique
# monolingue laisserait passer une bonne part du corpus.
#
# Marqueur STRUCTUREL. Dérivé du corpus, pas supposé : sur les 88 cartes
# négatives en base, 79 (89,8 %) ouvrent par « FAIL REASON #n: », et aucune
# des 307 cartes positives ne contient la formule. C'est le discriminant le
# plus fort du corpus, et il porte le bon sens : le relecteur a énoncé un
# motif explicite.
#
# COUPLAGE À CONNAÎTRE : la formule est prescrite par
# ``prompts/reviewer_contrarian.txt`` (lignes 47-49), et par ce prompt
# SEULEMENT — d'où le fait que les 9 cartes négatives sans « FAIL REASON »
# soient presque toutes du persona industrialist, qui suit un autre gabarit.
# Modifier ce prompt, ou changer de modèle, déplace directement le taux de
# blocage de ce gate. Le lexique ci-dessous est le filet de sécurité.
_FAIL_REASON_RE = re.compile(r"FAIL\s*REASON", re.IGNORECASE)

# Marqueurs LEXICAUX, relevés un par un sur les 9 cartes négatives qui
# n'emploient pas « FAIL REASON » — presque toutes du persona industrialist,
# qui suit un autre gabarit et ouvre par sa réserve :
#   « Taille de marché adressable extrêmement limitée »   (61D6)
#   « Barrière majeure au déploiement »                   (E212, 66E7)
#   « Marche adressable trop niche et difficile a monetiser » (2D9D)
#   « Le marché adressable immédiat est quasi inexistant »    (B7A1)
#   « Marche aval quasi-inexistant a court terme »            (3A81)
#   « Incohérence mécanistique fondamentale »                 (7ED4)
#   « Barrière commerciale majeure : le marché est trop étroit » (072C)
#   « Barrière d'entrée majeure »                             (94CA)
# Chaque entrée ci-dessous est traçable à l'une de ces cartes ; rien n'est
# ajouté « au cas où ».
_RESERVE_MARKERS = [
    # relevés sur les 9 cartes ci-dessus
    "barrière", "barrières", "limitée", "limité", "limités", "limitées",
    "niche", "inexistant", "inexistante", "incohérence", "étroit", "étroite",
    "difficile", "difficiles", "majeure", "majeur",
    # termes à lift élevé et sémantiquement des réserves, mesurés sur le
    # corpus (fréquence en carte négative / en carte positive) :
    # assumption 27 %/0 %, likely 23 %/0 %, irréaliste 9 %/0 %, naïve 8 %/0 %,
    # fatal 8 %/0 %, cannot 7 %/0 %, assumes 7 %/0 %, ignore 11 %/0,3 %,
    # faux 8 %/0,7 %.
    "assumption", "assumptions", "assumes", "likely", "unlikely",
    "irréaliste", "naïve", "naive", "fatal", "fatale", "cannot", "ignore",
    "faux", "fausse",
]

# Frontières de mots, pour éviter qu'un marqueur ne matche à l'intérieur d'un
# autre terme (« ignore » dans « ignorent », « fatal » dans « fatalement »).
# \b ne couvre pas les lettres accentuées avec `re` en mode ASCII : on borne
# explicitement sur la plage latine étendue.
_MARKER_RE = re.compile(
    r"(?<![\wÀ-ɏ])(?:%s)(?![\wÀ-ɏ])"
    % "|".join(sorted((re.escape(m) for m in _RESERVE_MARKERS), key=len, reverse=True)),
    re.IGNORECASE | re.UNICODE,
)


def has_reserve_marker(text: str) -> bool:
    """True si le texte porte une réserve explicite.

    Deux voies, dans l'ordre de leur pouvoir discriminant mesuré : le motif
    structurel « FAIL REASON » (89,8 % des cartes négatives, 0 % des
    positives), puis le lexique relevé sur les 9 cartes négatives qui ne
    l'emploient pas.
    """
    if not text:
        return False
    if _FAIL_REASON_RE.search(text):
        return True
    return bool(_MARKER_RE.search(text))


def is_negative(review: dict[str, Any]) -> bool:
    """Carte dont la note ou le verdict appelle une réserve visible."""
    try:
        score_val = float(review.get("overall_score"))
    except (TypeError, ValueError):
        return True
    verdict = (review.get("verdict") or "").strip().lower()
    return score_val < SCORE_FLOOR or verdict in NEGATIVE_VERDICTS


def displayed_comment(review: dict[str, Any]) -> str:
    """Le texte que le site affiche pour cette carte.

    Miroir de ``ReviewerPanel.tsx`` : une carte négative montre
    ``weaknesses[0]``, les autres ``strengths[0]``, avec repli sur
    ``recommendation``. Si cette règle change côté front, celle-ci doit
    changer avec elle — c'est ce couplage qui donne son sens au contrôle.
    """
    primary = "weaknesses" if is_negative(review) else "strengths"
    items = review.get(primary) or []
    first = items[0] if items else ""
    return (first or review.get("recommendation") or "").strip()


def check_review(review: dict[str, Any]) -> str | None:
    """Retourne le motif d'incohérence, ou None si la carte est cohérente.

    Prédicat : ``(score < 5.0 OU verdict ∈ {weak_reject, reject})
    ET le texte affiché ne contient aucun marqueur de réserve``.
    """
    score = review.get("overall_score")
    verdict = (review.get("verdict") or "").strip().lower()

    try:
        score_val = float(score)
    except (TypeError, ValueError):
        # Score illisible : on ne peut pas prouver la cohérence → fail-closed.
        return "unreadable_score"

    negative = score_val < SCORE_FLOOR or verdict in NEGATIVE_VERDICTS
    if not negative:
        return None

    if has_reserve_marker(displayed_comment(review)):
        return None

    return (
        f"score={score_val:.1f} verdict={verdict or 'unknown'} "
        f"but displayed comment carries no reserve marker"
    )


def check_panel(panel_data: Any) -> list[dict[str, Any]]:
    """Toutes les incohérences d'un blob ``panel_data``.

    Retourne une liste vide quand le panel est cohérent. Un panel illisible
    ou sans revues remonte une anomalie plutôt que de passer silencieusement
    — fail-closed jusqu'au bout.
    """
    if isinstance(panel_data, str):
        import json

        try:
            panel_data = json.loads(panel_data)
        except (ValueError, TypeError):
            return [{"reviewer": "?", "reason": "unparsable_panel_data"}]

    if not isinstance(panel_data, dict):
        return [{"reviewer": "?", "reason": "unparsable_panel_data"}]

    reviews = panel_data.get("reviews")
    if not isinstance(reviews, list) or not reviews:
        return [{"reviewer": "?", "reason": "no_reviews"}]

    problems: list[dict[str, Any]] = []
    for review in reviews:
        if not isinstance(review, dict):
            problems.append({"reviewer": "?", "reason": "unparsable_review"})
            continue
        reason = check_review(review)
        if reason:
            problems.append(
                {
                    "reviewer": review.get("reviewer_persona", "?"),
                    "score": review.get("overall_score"),
                    "verdict": review.get("verdict"),
                    "reason": reason,
                    "comment": displayed_comment(review)[:200],
                }
            )
    return problems
