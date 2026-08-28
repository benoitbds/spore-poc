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

# Marqueurs de réserve, FR et EN : plusieurs cartes sont rédigées en anglais
# sur des pages françaises (cf. SPR-2026-D460 / SPR-2026-7626), donc un
# lexique monolingue laisserait passer la moitié du corpus.
_RESERVE_MARKERS = [
    # — FR —
    "mais", "cependant", "toutefois", "néanmoins", "pourtant", "bien que",
    "malgré", "limite", "limites", "limitation", "limitations", "réserve",
    "réserves", "insuffisant", "insuffisante", "insuffisamment", "manque",
    "manquent", "lacune", "lacunes", "faible", "faiblesse", "faiblesses",
    "risque", "risques", "doute", "doutes", "douteux", "incertain",
    "incertaine", "incertitude", "non démontré", "non démontrée", "non étayé",
    "non étayée", "non justifié", "non justifiée", "à valider", "à démontrer",
    "préoccupation", "préoccupations", "problème", "problèmes", "erreur",
    "absence", "absent", "absente", "spéculatif", "spéculative", "fragile",
    "contestable", "discutable", "peu probable", "invalide", "échec",
    # Accords féminin/pluriel et vocabulaire de l'industriel : sans eux, quatre
    # cartes portant des réserves explicites (« marché adressable trop niche »,
    # « quasi-inexistant », « barrière majeure ») passaient pour favorables.
    "limité", "limitée", "limités", "limitées", "barrière", "barrières",
    "obstacle", "obstacles", "frein", "freins", "inexistant", "inexistante",
    "niche", "difficile", "difficiles", "difficulté", "difficultés", "trop",
    "aucun", "aucune", "sans", "long", "longue", "coûteux", "coûteuse",
    # — EN —
    "however", "but", "although", "though", "yet", "despite", "nevertheless",
    "nonetheless", "whereas", "lack", "lacks", "lacking", "insufficient",
    "insufficiently", "weak", "weakness", "weaknesses", "risk", "risks",
    "risky", "doubt", "doubts", "doubtful", "uncertain", "uncertainty",
    "unsubstantiated", "unsupported", "unproven", "unclear", "unjustified",
    "questionable", "concern", "concerns", "concerning", "limitation",
    "limitations", "limited", "flaw", "flaws", "flawed", "gap", "gaps",
    "fail", "fails", "failure", "problem", "problems", "issue", "issues",
    "speculative", "fragile", "implausible", "unlikely", "overstated",
    "missing", "absent", "invalid", "insufficient evidence", "not demonstrated",
    "not established", "no evidence",
]

# Frontières de mots pour éviter que « but » ne matche « contribute » ou
# « attribute », et « gap » ne matche « gaping ». \b ne fonctionne pas sur les
# lettres accentuées avec le module `re` en mode ASCII ; on force l'unicode.
_MARKER_RE = re.compile(
    r"(?<![\wÀ-ɏ])(?:%s)(?![\wÀ-ɏ])"
    % "|".join(sorted((re.escape(m) for m in _RESERVE_MARKERS), key=len, reverse=True)),
    re.IGNORECASE | re.UNICODE,
)


def has_reserve_marker(text: str) -> bool:
    """True si le texte porte au moins un marqueur de réserve explicite."""
    if not text:
        return False
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
