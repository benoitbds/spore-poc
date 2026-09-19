"""Contrôles mécaniques du garde des récits (Python pur, fail-closed).

Six contrôles du contrat de données (``schema``, ``length``, ``year_window``,
``no_citation_patterns``, ``no_proscribed_vocab``, ``identity_denylist``),
plus deux pour l'anglais (``british_spelling``, ``no_french_residue``). Un
contrôle qui ne peut pas s'exécuter échoue.

La denylist d'identité (``.identity_denylist``, non versionnée) n'est jamais
recopiée : le rapport ne contient que la présence du fichier et un nombre
d'occurrences, pas les chaînes ni leur position.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Listes existantes du mécanisme de traduction (cœur, importées sans
# modification) : orthographe américaine et résidus de français.
from scripts.translate_brief_vulgarization import (
    _FR_FRAGMENT_INLINE as FR_FRAGMENT_INLINE,
)
from scripts.translate_brief_vulgarization import (
    _US_SPELLINGS as US_SPELLINGS_TRANSLATION,
)

#: Champs textuels d'un récit (sortie du rédacteur ou du traducteur).
TEXT_FIELDS: tuple[str, ...] = ("title", "place", "body_markdown", "mechanism", "limit_or_risk")

#: Champs obligatoires et type attendu.
REQUIRED_FIELDS: dict[str, type] = {
    "title": str,
    "year": int,
    "place": str,
    "body_markdown": str,
    "mechanism": str,
    "limit_or_risk": str,
}


def normalise(text: str) -> str:
    """Minuscules, sans accents, apostrophes et tirets typographiques unifiés.

    Args:
        text: Texte brut.

    Returns:
        Texte normalisé pour les motifs insensibles aux accents.
    """
    text = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    text = text.replace("‑", "-").replace("‐", "-")
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def count_words(text: str) -> int:
    """Nombre de mots (jetons séparés par des blancs contenant une lettre ou un chiffre).

    Args:
        text: Texte Markdown.

    Returns:
        Nombre de mots ; « l'homme » compte pour un, un tiret de dialogue pour zéro.
    """
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))


# ── Motifs de citation ──────────────────────────────────────────────

#: Motifs appliqués au texte normalisé (minuscules, sans accents).
_CITATION_NORMALISED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("doi", re.compile(r"\b10\.\d{4,9}/\S+|\bdoi\s*[:.]")),
    ("url", re.compile(r"https?://|\bwww\.|\b[a-z0-9-]+\.(?:com|org|net|edu|gov|io|fr)\b")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    # Point ou parenthèse exigés : « Sam et Al partirent » n'est pas une citation.
    ("et_al", re.compile(r"\bet al\.|\bet al\s*\(")),
    (
        "selon_une_etude",
        re.compile(
            r"\b(?:selon|d'apres)\s+(?:une|des|plusieurs|la|les|cette|ces|de recentes?|une recente)"
            r"\s+(?:\w+\s+)?(?:etudes?|publications?|recherches?|travaux|articles?)\b"
        ),
    ),
    ("etude_publiee", re.compile(r"\b(?:une|l')\s*etude\s+(?:publiee|parue)\b")),
    (
        "according_to_a_study",
        re.compile(
            r"\baccording to\s+(?:a|the|one|several|recent|new|many|some)?\s*(?:\w+\s+)?"
            r"(?:study|studies|paper|papers|research|article|report)\b"
        ),
    ),
    ("study_published", re.compile(r"\b(?:a|the)\s+(?:recent\s+|new\s+)?study\s+published\b")),
    ("published_in_journal", re.compile(r"\bpublished in (?:the )?(?:journal|revue)\b")),
)

#: Motifs appliqués au texte brut (sensibles à la casse). L'année d'une
#: référence auteur-date est passée (1900-2029) : « (Port-Salant, 2046) » est
#: un repère de lieu et de date du récit, que la fenêtre d'années place
#: toujours après 2035.
_CITATION_RAW: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "author_year",
        re.compile(
            r"\(\s*[A-ZÀ-Ý][\w'’\-]+(?:\s+(?:et al\.?|and|&|et)\s+[A-ZÀ-Ý]?[\w'’\-.]*)?"
            r"\s*,?\s*(?:19\d{2}|20[0-2]\d)[a-z]?\s*\)"
        ),
    ),
    ("numeric_reference", re.compile(r"\[\s*\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\s*\]")),
)


def citation_hits(text: str) -> list[str]:
    """Catégories de motifs de citation trouvées.

    Args:
        text: Texte à contrôler.

    Returns:
        Catégories trouvées, sans doublon, dans l'ordre des motifs.
    """
    norm = normalise(text)
    hits = [name for name, pattern in _CITATION_NORMALISED if pattern.search(norm)]
    hits.extend(name for name, pattern in _CITATION_RAW if pattern.search(text))
    return hits


# ── Vocabulaire proscrit ────────────────────────────────────────────

#: Français, sur texte normalisé. « decouv » couvre découverte, découvrir,
#: découvert, redécouvrir… ; « revolutionn » couvre révolutionnaire et
#: révolutionner.
PROSCRIBED_FR: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("decouverte", re.compile(r"decouv")),
    ("revolutionnaire", re.compile(r"revolutionn")),
    (
        "propulse_par_l_ia",
        re.compile(r"propulse\w*\s+par\s+(?:l'\s*)?(?:ia|intelligence artificielle)\b"),
    ),
)

#: Anglais, sur texte normalisé. « discover » couvre discovery, discovered,
#: undiscovered, rediscover…
PROSCRIBED_EN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("discover", re.compile(r"discover")),
    ("revolutionary", re.compile(r"revolutionar|revolutioni[sz]")),
    ("powered_by_ai", re.compile(r"powered\s+by\s+(?:ai|artificial intelligence)\b|\bai-powered\b")),
)

#: Première personne du pluriel (« nous » éditorial et équivalents).
FIRST_PERSON_PLURAL: dict[str, re.Pattern[str]] = {
    "fr": re.compile(r"\b(?:nous|notre|nos)\b"),
    "en": re.compile(r"\b(?:we|our|ours|us|ourselves|let's)\b"),
}


def proscribed_hits(text: str, lang: str, *, strict_first_person_plural: bool = True) -> list[str]:
    """Catégories de vocabulaire proscrit trouvées.

    Args:
        text: Texte à contrôler.
        lang: ``fr`` ou ``en``.
        strict_first_person_plural: Compter la première personne du pluriel.

    Returns:
        Catégories trouvées, sans doublon.
    """
    norm = normalise(text)
    patterns = PROSCRIBED_FR if lang == "fr" else PROSCRIBED_EN
    hits = [name for name, pattern in patterns if pattern.search(norm)]
    if strict_first_person_plural and FIRST_PERSON_PLURAL[lang].search(norm):
        hits.append("first_person_plural")
    return hits


# ── Orthographe britannique et résidus de français (EN) ─────────────

#: Compléments à la liste du mécanisme de traduction.
_US_SPELLINGS_EXTRA = re.compile(
    r"\b(?:"
    r"center|centers|centered|centering|"
    r"gray|grays|defense|defenses|offense|aging|"
    r"labeled|labeling|traveled|traveling|traveler|travelers|"
    r"canceled|canceling|fueled|fueling|signaled|signaling|leveled|leveling|"
    r"modeled|modeling|modeler|"
    r"neighbor|neighbors|neighborhood|neighborhoods|neighboring|"
    r"honor|honors|honored|labor|labors|labored|harbor|harbors|rumor|rumors|"
    r"humor|flavor|flavors|vapor|vapors|tumor|tumors|odor|odors|"
    r"fiber|fibers|liter|liters|"
    r"catalog|catalogs|dialog|"
    r"esophagus|estrogen|hemoglobin|anemia|pediatric|pediatrician|"
    # « sulfur » absent à dessein : graphie IUPAC, admise en anglais britannique.
    r"aluminum"
    r")\b"
)

#: Verbes en -ize / -yze et dérivés.
_US_IZE = re.compile(r"\b[a-z]+(?:iz|yz)(?:e|es|ed|ing|ation|ations|er|ers)\b")

#: Mots anglais légitimes qui finissent en -ize (pas une orthographe américaine).
_IZE_ALLOWED = frozenset(
    {
        "size", "sizes", "sized", "sizing", "resize", "resized", "resizing",
        "downsize", "downsized", "downsizing", "oversize", "oversized", "outsize",
        "outsized", "undersized", "prize", "prizes", "prized", "seize", "seizes",
        "seized", "seizing", "capsize", "capsized", "capsizing", "maize", "baize",
        "assize", "assizes", "belize",
    }
)


def us_spelling_hits(text: str) -> list[str]:
    """Formes d'orthographe américaine trouvées (anglais).

    Args:
        text: Texte anglais.

    Returns:
        Formes trouvées, en minuscules, sans doublon, triées.
    """
    lower = text.casefold()
    found = {match.group(0).casefold() for match in US_SPELLINGS_TRANSLATION.finditer(text)}
    found.update(match.group(0) for match in _US_SPELLINGS_EXTRA.finditer(lower))
    found.update(
        match.group(0) for match in _US_IZE.finditer(lower) if match.group(0) not in _IZE_ALLOWED
    )
    return sorted(found)


def french_residue_hits(text: str) -> int:
    """Nombre de fragments de français dans un texte anglais.

    Args:
        text: Texte anglais.

    Returns:
        Nombre d'occurrences (liste du mécanisme de traduction).
    """
    return len(FR_FRAGMENT_INLINE.findall(text))


# ── Denylist d'identité ─────────────────────────────────────────────


@dataclass(frozen=True)
class IdentityResult:
    """Résultat du contrôle d'identité, sans aucune chaîne de la denylist.

    Attributes:
        present: Fichier trouvé et non vide.
        hits: Nombre d'entrées trouvées dans le texte.
    """

    present: bool
    hits: int

    @property
    def passed(self) -> bool:
        """Contrôle réussi : fichier présent et aucune occurrence."""
        return self.present and self.hits == 0


def _load_denylist(path: Path) -> list[str]:
    """Entrées normalisées de la denylist (jamais journalisées).

    Args:
        path: Fichier (une chaîne par ligne, ``#`` pour un commentaire).

    Returns:
        Entrées normalisées non vides ; liste vide si le fichier manque.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.append(normalise(stripped))
    return entries


def identity_check(texts: Iterable[str], denylist_path: Path) -> IdentityResult:
    """Cherche les entrées de la denylist dans les textes.

    Correspondance insensible à la casse et aux accents, bornée par des
    caractères non alphanumériques (un nom ne se cache pas dans un mot plus
    long). Fichier absent ou vide : échec (fail-closed).

    Args:
        texts: Textes à contrôler.
        denylist_path: Fichier ``.identity_denylist``.

    Returns:
        Présence du fichier et nombre d'occurrences, sans les chaînes.
    """
    entries = _load_denylist(denylist_path)
    if not entries:
        return IdentityResult(present=False, hits=0)
    haystack = "\n".join(normalise(text) for text in texts)
    hits = 0
    for entry in entries:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(entry) + r"(?![a-z0-9])")
        hits += len(pattern.findall(haystack))
    return IdentityResult(present=True, hits=hits)


# ── Contrôle complet ────────────────────────────────────────────────


@dataclass(frozen=True)
class MechanicalSettings:
    """Paramètres des contrôles mécaniques.

    Attributes:
        lang: ``fr`` ou ``en``.
        min_words: Longueur minimale du corps.
        max_words: Longueur maximale du corps.
        year_min: Année minimale du récit.
        year_max: Année maximale du récit.
        title_max_chars: Longueur maximale du titre.
        denylist_path: Denylist d'identité.
        strict_first_person_plural: Rejeter la première personne du pluriel.
    """

    lang: str
    min_words: int
    max_words: int
    year_min: int
    year_max: int
    title_max_chars: int
    denylist_path: Path
    strict_first_person_plural: bool = True


def year_window(offset_min: int, offset_max: int, *, now: datetime | None = None) -> tuple[int, int]:
    """Fenêtre d'années admise pour un récit.

    Args:
        offset_min: Décalage minimal par rapport à l'année courante.
        offset_max: Décalage maximal.
        now: Instant de référence (UTC) ; maintenant par défaut.

    Returns:
        ``(année minimale, année maximale)`` inclusives.
    """
    year = (now or datetime.now(UTC)).year
    return year + offset_min, year + offset_max


def coerce_year(value: Any) -> int | None:
    """Année entière, ou ``None`` si la valeur n'en est pas une.

    Args:
        value: Valeur du champ ``year``.

    Returns:
        Entier, y compris depuis une chaîne de chiffres ; ``None`` sinon.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def schema_problems(story: Mapping[str, Any], title_max_chars: int) -> list[str]:
    """Écarts de schéma d'un récit.

    Args:
        story: Sortie du rédacteur ou du traducteur.
        title_max_chars: Longueur maximale du titre.

    Returns:
        Champs absents, vides ou mal typés (vide si le schéma est respecté).
    """
    problems: list[str] = []
    for name, expected in REQUIRED_FIELDS.items():
        value = story.get(name)
        if expected is int:
            if coerce_year(value) is None:
                problems.append(name)
        elif not isinstance(value, str) or not value.strip():
            problems.append(name)
    title = story.get("title")
    if isinstance(title, str) and len(title.strip()) > title_max_chars:
        problems.append("title_too_long")
    return problems


def run_mechanical_checks(story: Mapping[str, Any], settings: MechanicalSettings) -> dict[str, Any]:
    """Exécute tous les contrôles mécaniques.

    Args:
        story: Récit (``title``, ``year``, ``place``, ``body_markdown``,
            ``mechanism``, ``limit_or_risk``).
        settings: Paramètres.

    Returns:
        ``{"passed": bool, "checks": {...}, "reasons": [...]}`` ; ``reasons``
        est une liste de codes (jamais de texte du récit ni de la denylist).
    """
    checks: dict[str, Any] = {}
    reasons: list[str] = []

    problems = schema_problems(story, settings.title_max_chars)
    checks["schema"] = not problems
    if problems:
        reasons.append("mechanical:schema:" + ",".join(problems))

    body = story.get("body_markdown") if isinstance(story.get("body_markdown"), str) else ""
    words = count_words(body)
    length_ok = settings.min_words <= words <= settings.max_words
    checks["length"] = {
        "passed": length_ok,
        "words": words,
        "bounds": [settings.min_words, settings.max_words],
    }
    if not length_ok:
        reasons.append(f"mechanical:length:{words}")

    year = coerce_year(story.get("year"))
    year_ok = year is not None and settings.year_min <= year <= settings.year_max
    checks["year_window"] = year_ok
    if not year_ok:
        reasons.append(f"mechanical:year_window:{year}")

    texts: Sequence[str] = [
        story.get(name) for name in TEXT_FIELDS if isinstance(story.get(name), str)
    ]
    joined = "\n".join(texts)

    citations = citation_hits(joined)
    checks["no_citation_patterns"] = not citations
    if citations:
        reasons.append("mechanical:citation:" + ",".join(citations))

    vocab = proscribed_hits(
        joined, settings.lang, strict_first_person_plural=settings.strict_first_person_plural
    )
    checks["no_proscribed_vocab"] = not vocab
    if vocab:
        reasons.append("mechanical:proscribed_vocab:" + ",".join(vocab))

    identity = identity_check(texts, settings.denylist_path)
    checks["identity_denylist"] = identity.passed
    if not identity.present:
        reasons.append("mechanical:identity_denylist:missing")
    elif identity.hits:
        reasons.append(f"mechanical:identity_denylist:hits={identity.hits}")

    if settings.lang == "en":
        spellings = us_spelling_hits(joined)
        checks["british_spelling"] = not spellings
        if spellings:
            reasons.append("mechanical:us_spelling:" + ",".join(spellings[:10]))
        residue = french_residue_hits(joined)
        checks["no_french_residue"] = residue == 0
        if residue:
            reasons.append(f"mechanical:french_residue:{residue}")

    passed = all(
        value["passed"] if isinstance(value, dict) else bool(value) for value in checks.values()
    )
    return {"passed": passed, "checks": checks, "reasons": reasons}
