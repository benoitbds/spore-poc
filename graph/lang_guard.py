"""Détection de langue mécanique sur le texte des cartes reviewer.

SPR-2026-D460 porte une carte ``domain_expert`` en anglais dans
``panel_data`` (le champ français), SPR-2026-7626 une carte ``contrarian``
dans le même cas : le générateur rend parfois en anglais malgré un prompt
français, et rien ne le détecte avant publication. La passe de traduction
FR→EN n'y change rien — elle produit un ``panel_data_en`` correct et laisse
le champ français tel quel, donc la page FR affiche de l'anglais.

Discriminateur : fréquence des mots outils. Sur de la prose scientifique,
le vocabulaire technique est largement commun aux deux langues ; ce sont les
déterminants, prépositions et auxiliaires qui séparent. Pas de LLM, pas de
dépendance externe (aucune n'est installée dans le venv), conformément au
pattern des autres gates.
"""

from __future__ import annotations

import re
from typing import Any

# Mots outils exclusifs ou très fortement marqués. On évite délibérément les
# formes ambiguës entre les deux langues (« a », « on », « son », « as »,
# « the » n'a pas d'équivalent piège mais « en », « pas », « point » si).
_FR_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "est", "sont",
    "que", "qui", "dans", "pour", "sur", "avec", "par", "plus", "mais",
    "cette", "ce", "ces", "cet", "aux", "au", "être", "avoir", "sa", "ses",
    "leur", "leurs", "nous", "vous", "ils", "elles", "il", "elle", "on",
    "ne", "pas", "peut", "doit", "fait", "très", "aussi", "entre", "sans",
    "sous", "dont", "où", "donc", "car", "alors", "ainsi", "lors", "chez",
    "d'une", "d'un", "l'hypothèse", "n'est", "qu'il", "qu'elle", "s'agit",
}

_EN_STOPWORDS = {
    "the", "and", "is", "are", "of", "to", "in", "for", "with", "that",
    "this", "these", "those", "which", "from", "would", "could", "should",
    "will", "has", "have", "been", "was", "were", "be", "by", "on", "at",
    "as", "an", "it", "its", "their", "there", "but", "not", "however",
    "while", "when", "than", "then", "may", "might", "must", "can", "such",
    "does", "do", "if", "into", "about", "between", "through", "however",
}

_WORD_RE = re.compile(r"[a-zàâäéèêëïîôöùûüçœæ']+", re.IGNORECASE)

# En dessous de ce nombre de mots outils reconnus, l'échantillon est trop
# court pour trancher : on s'abstient plutôt que de deviner.
MIN_SIGNAL = 4
# Marge exigée pour déclarer une langue : le camp gagnant doit rassembler
# plus de 65 % des mots outils reconnus. Entre les deux, on s'abstient.
DOMINANCE = 0.65


def detect(text: str) -> str | None:
    """'fr', 'en', ou None quand le texte ne tranche pas.

    None n'est pas un échec : c'est le cas d'un texte trop court ou trop
    technique pour porter un signal. L'appelant décide quoi en faire.
    """
    if not text:
        return None
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return None

    fr = sum(1 for w in words if w in _FR_STOPWORDS)
    en = sum(1 for w in words if w in _EN_STOPWORDS)
    total = fr + en
    if total < MIN_SIGNAL:
        return None

    if fr / total >= DOMINANCE:
        return "fr"
    if en / total >= DOMINANCE:
        return "en"
    return None


def card_texts(review: dict[str, Any]) -> str:
    """Concatène les champs textuels d'une carte pour donner du signal.

    Un seul champ (``strengths[0]``) est souvent trop court pour dépasser
    MIN_SIGNAL ; l'ensemble de la carte tranche nettement mieux.
    """
    parts: list[str] = []
    for key in ("strengths", "weaknesses", "critical_questions"):
        value = review.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
    rec = review.get("recommendation")
    if rec:
        parts.append(str(rec))
    return " ".join(parts)


def check_panel_language(panel_data: Any, expected: str) -> list[dict[str, Any]]:
    """Cartes dont la langue détectée contredit ``expected`` ('fr' ou 'en').

    Une carte sur laquelle le détecteur s'abstient n'est jamais signalée :
    le contrôle ne remonte que les contradictions franches, pour éviter de
    bloquer un panel sur une carte laconique.
    """
    if isinstance(panel_data, str):
        import json

        try:
            panel_data = json.loads(panel_data)
        except (ValueError, TypeError):
            return []
    if not isinstance(panel_data, dict):
        return []

    problems: list[dict[str, Any]] = []
    for review in panel_data.get("reviews") or []:
        if not isinstance(review, dict):
            continue
        found = detect(card_texts(review))
        if found is not None and found != expected:
            problems.append(
                {
                    "reviewer": review.get("reviewer_persona", "?"),
                    "expected": expected,
                    "detected": found,
                    "excerpt": card_texts(review)[:160],
                }
            )
    return problems
