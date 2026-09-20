"""``explainer_flags`` : signalements sur les textes de niveau 2 du pipeline (D-017).

Le site affiche la vulgarisation du pipeline comme l'explication de SPORE
(niveau 2, ``LIGNE_EDITORIALE.md`` §8). Ce module la relit, sans LLM, et
inscrit dans ``v2_vocab_flags`` trois sortes de signalements :

* ``vocab`` : vocabulaire proscrit, motifs de ``vocab_rules.json`` moins les
  littéraux de ``vocab_allow.txt`` (et les citations verrouillées
  ``literals_excluded``), sur tous les champs de niveau 2 ;
* ``statut`` : hypothèse ou protocole prévu présenté comme acquis, motifs de
  ``status_rules.json``, limités aux champs de sa liste ``fields``, avec
  l'exclusion conditionnelle (marqueur de condition placé avant l'occurrence
  dans la même phrase) ;
* ``structure`` : règle de validité du titre (``ARCHITECTURE_INFO.md`` §7.2).

Les trois fichiers de règles sont des copies octet pour octet de
``spore-v2/scripts/v2/checks/`` (le front lit les originaux) : les rangs
``rule_idx`` désignent donc le même motif des deux côtés.

Conventions de la table (à lire avec ``DATA_CONTRACT.md``) :

* ``field`` : chemin dans ``vulgarization_data`` (``fr``) ou
  ``vulgarization_data_en`` (``en``) : ``title_fr`` (FR) ou ``title`` (EN),
  ``hypothesis_in_brief``, ``why_it_matters``, ``imagine_that``,
  ``concretely.<clé>``, ``reviewers_say`` ;
* ``start`` / ``end`` : positions en points de code (index Python) dans la
  valeur brute du champ, celle dont ``field_sha256`` est l'empreinte (UTF-8) ;
  la recherche se fait sur le texte normalisé comme M9 (NFC, traits d'union
  conditionnels et caractères de largeur nulle retirés) et les positions sont
  ramenées au texte brut ;
* ``rule_idx`` (0-based, ordre du fichier) : ``vocab`` FR → rang dans la liste
  ``fr`` ; ``vocab`` EN → rang dans ``en`` puis, à la suite, dans
  ``en_us_spelling`` (``len(en) + i``) ; ``statut`` → rang dans la liste de la
  langue de ``status_rules.json`` ; ``NULL`` pour ``structure`` ;
* ``structure`` : ``start`` = position de la coupe, c'est-à-dire le premier
  point où le champ cesse d'être un titre valide (premier saut de ligne, ou
  141ᵉ caractère de la première ligne si elle est trop longue, le plus tôt des
  deux), ``end`` NULL ;
* titre : vocabulaire et statut ne portent que sur la ligne retenue par la
  règle de validité ; un titre invalide ne reçoit que sa ligne ``structure``
  (le reste du champ n'est affiché nulle part).

Aucune colonne ne recopie le mot détecté, et aucun journal non plus : les
résumés ne portent que des compteurs, des identifiants et des chemins de champ.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from logging_config import get_logger
from narrative.config import NarrativeConfig
from narrative.inputs import load_blob
from storage import narrative_db

logger = get_logger("narrative.explainer_flags")

KIND_VOCAB = "vocab"
KIND_STATUT = "statut"
KIND_STRUCTURE = "structure"

#: Longueur maximale d'un titre de niveau 2, en points de code (§7.2).
TITLE_MAX_CHARS = 140

#: Champ titre de chaque langue (``ARCHITECTURE_INFO.md`` §7.2).
TITLE_FIELD: Mapping[str, str] = {"fr": "title_fr", "en": "title"}

#: Colonne ``briefs`` de la vulgarisation de chaque langue.
VULGARISATION_COLUMN: Mapping[str, str] = {
    "fr": "vulgarization_data",
    "en": "vulgarization_data_en",
}

#: Champs de niveau 2 hors titre et ``concretely`` (§8, « Détection »).
_PLAIN_FIELDS: tuple[str, ...] = ("hypothesis_in_brief", "why_it_matters", "imagine_that")
_CONCRETELY_ORDER: tuple[str, ...] = ("intro", "phase1", "phase2", "phase3")

#: Sauts de ligne qui coupent un titre (§7.2, étape 3).
_LINE_BREAKS: tuple[str, ...] = ("\n", "\r", "\u2028", "\u2029")

#: Caractères retirés avant la recherche (même liste que M9).
_INVISIBLE = frozenset("\u00ad\u200b\u200c\u200d\u2060\ufeff")

#: Fin de phrase pour l'exclusion conditionnelle : ponctuation finale suivie
#: d'un blanc, ou saut de ligne. Une abréviation (« e.g. ») coupe aussi : la
#: phrase retenue est alors plus courte, ce qui ne peut qu'ajouter des
#: signalements (sens prudent).
_SENTENCE_END = re.compile(r"[.!?…](?=\s)|[\n\r\u2028\u2029]")


# ── Règles ──────────────────────────────────────────────────────────


def py_pattern(pattern: str) -> str:
    """Motif JavaScript des fichiers de règles → motif Python (comme ``m9_db.py``).

    Args:
        pattern: Motif sans frontières (``\\p{L}`` admis).

    Returns:
        Motif pour ``re``.
    """
    return pattern.replace(r"\p{L}", r"[^\W\d_]")


def _word_rule(pattern: str) -> re.Pattern[str]:
    """Frontières de mot Unicode et insensibilité à la casse (moteur de M9)."""
    return re.compile(r"(?<!\w)(?:" + py_pattern(pattern) + r")(?!\w)", re.IGNORECASE)


def _literal_rule(literal: str) -> re.Pattern[str]:
    """Littéral admis, borné comme dans ``m9_db.py`` / ``m9_vocab_identity.mjs``."""
    return re.compile(
        r"(?<![\w.\-])" + re.escape(normalise_text(literal)) + r"(?![\w/\-])(?![.:?!,=@#&%+~]\w)"
    )


@dataclass(frozen=True)
class Rule:
    """Motif compilé et son rang.

    Attributes:
        idx: Valeur de ``rule_idx``.
        pattern: Motif compilé.
        not_exact: Formes exactes à ignorer (``"US"``, le pays).
    """

    idx: int
    pattern: re.Pattern[str]
    not_exact: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FlagRules:
    """Règles des trois passes.

    Attributes:
        vocab: Langue → motifs de vocabulaire, rangs compris.
        status: Langue → motifs de statut.
        conditions: Langue → marqueurs de condition.
        status_fields: Champs soumis à la passe de statut.
        allowed: Littéraux admis (``vocab_allow.txt`` et ``literals_excluded``).
        fingerprints: Empreinte SHA-256 de chaque fichier lu.
    """

    vocab: Mapping[str, tuple[Rule, ...]]
    status: Mapping[str, tuple[Rule, ...]]
    conditions: Mapping[str, re.Pattern[str]]
    status_fields: frozenset[str]
    allowed: tuple[re.Pattern[str], ...]
    fingerprints: Mapping[str, str]


def parse_allow_literals(text: str) -> list[str]:
    """Littéraux de ``vocab_allow.txt`` (lignes ``<littéral> | <raison>``).

    Les commentaires, les lignes sans raison et les formes ``email`` / ``attr``
    (qui ne portent pas sur le texte d'un champ) sont écartés.

    Args:
        text: Contenu du fichier.

    Returns:
        Littéraux, dans l'ordre du fichier.
    """
    literals: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        item, _, reason = line.partition("|")
        item, reason = item.strip(), reason.strip()
        if not item or not reason or item.startswith(("email ", "attr ")):
            continue
        literals.append(item)
    return literals


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_rules(
    vocab_rules: Mapping[str, Any], status_rules: Mapping[str, Any], allow_literals: Sequence[str]
) -> FlagRules:
    """Compile les règles à partir des fichiers décodés.

    Args:
        vocab_rules: ``vocab_rules.json``.
        status_rules: ``status_rules.json``.
        allow_literals: Littéraux de ``vocab_allow.txt``.

    Returns:
        Règles compilées.

    Raises:
        TypeError: Liste de motifs mal formée.
        KeyError: Section absente d'un fichier de règles.
    """

    def compiled(items: Any, start: int = 0) -> tuple[Rule, ...]:
        if not isinstance(items, list):
            raise TypeError("liste de motifs attendue")
        return tuple(
            Rule(start + index, _word_rule(str(item["re"])), frozenset(item.get("notExact") or ()))
            for index, item in enumerate(items)
        )

    en = compiled(vocab_rules["en"])
    vocab = {
        "fr": compiled(vocab_rules["fr"]),
        "en": en + compiled(vocab_rules["en_us_spelling"], start=len(en)),
    }
    status = {lang: compiled(status_rules[lang]) for lang in ("fr", "en")}
    conditions = {lang: _word_rule(str(status_rules["conditions"][lang])) for lang in ("fr", "en")}
    literals = [*allow_literals, *(vocab_rules.get("literals_excluded") or [])]
    return FlagRules(
        vocab=vocab,
        status=status,
        conditions=conditions,
        status_fields=frozenset(str(item) for item in status_rules["fields"]),
        allowed=tuple(_literal_rule(item) for item in literals if item),
        fingerprints={},
    )


@lru_cache(maxsize=4)
def _load_rules(paths: tuple[Path, Path, Path], mtimes: tuple[int, int, int]) -> FlagRules:
    del mtimes  # clé de cache seulement : un fichier remplacé est relu
    vocab_path, status_path, allow_path = paths
    rules = build_rules(
        json.loads(vocab_path.read_text(encoding="utf-8")),
        json.loads(status_path.read_text(encoding="utf-8")),
        parse_allow_literals(allow_path.read_text(encoding="utf-8")),
    )
    fingerprints = {path.name: _sha256_file(path) for path in paths}
    return FlagRules(
        vocab=rules.vocab,
        status=rules.status,
        conditions=rules.conditions,
        status_fields=rules.status_fields,
        allowed=rules.allowed,
        fingerprints=fingerprints,
    )


def load_rules(config: NarrativeConfig) -> FlagRules:
    """Règles de la configuration (relues si un fichier change).

    Args:
        config: Configuration (chemins des trois fichiers).

    Returns:
        Règles compilées.
    """
    paths = (config.vocab_rules_path, config.status_rules_path, config.vocab_allow_path)
    return _load_rules(paths, tuple(path.stat().st_mtime_ns for path in paths))


# ── Texte ───────────────────────────────────────────────────────────


def normalise_text(text: str) -> str:
    """Normalisation de M9 : NFC, invisibles retirés.

    Args:
        text: Texte brut.

    Returns:
        Texte normalisé.
    """
    return "".join(ch for ch in unicodedata.normalize("NFC", text) if ch not in _INVISIBLE)


def _normalise_with_map(text: str) -> tuple[str, list[int], list[int]]:
    """Texte normalisé et, pour chacun de ses caractères, sa plage dans le brut.

    La normalisation se fait par groupe (caractère de base suivi de ses
    diacritiques combinants) : un caractère composé renvoie à tout son groupe.

    Args:
        text: Texte brut.

    Returns:
        ``(normalisé, débuts, fins)`` : le caractère ``i`` du texte normalisé
        vient de ``text[débuts[i]:fins[i]]``.
    """
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    index, size = 0, len(text)
    while index < size:
        if text[index] in _INVISIBLE:
            index += 1
            continue
        stop = index + 1
        while stop < size and (unicodedata.combining(text[stop]) or text[stop] in _INVISIBLE):
            stop += 1
        cluster = "".join(ch for ch in text[index:stop] if ch not in _INVISIBLE)
        for ch in unicodedata.normalize("NFC", cluster):
            out.append(ch)
            starts.append(index)
            ends.append(stop)
        index = stop
    return "".join(out), starts, ends


def _strip_edges(text: str) -> tuple[str, int]:
    """Retire les blancs de bord (espaces Unicode, sauts de ligne, BOM).

    Returns:
        ``(texte, nombre de caractères retirés au début)``.
    """

    def blank(ch: str) -> bool:
        return ch.isspace() or ch == "\ufeff"

    start, end = 0, len(text)
    while start < end and blank(text[start]):
        start += 1
    while end > start and blank(text[end - 1]):
        end -= 1
    return text[start:end], start


@dataclass(frozen=True)
class TitleCut:
    """Application de la règle de validité du titre (§7.2).

    Attributes:
        retained: Ligne retenue, ou ``None`` (repli ``idea.fallbackTitle``).
        offset: Position de la ligne retenue dans le champ brut.
        cut: Position de la coupe dans le champ brut, ou ``None`` si le champ,
            débarrassé de ses blancs de bord, est la ligne retenue (ou vide).
    """

    retained: str | None
    offset: int
    cut: int | None

    @property
    def structure(self) -> bool:
        """Le champ appelle une ligne ``structure``."""
        return self.cut is not None


def level2_title(raw: Any) -> TitleCut:
    """Règle de validité d'un titre de niveau 2 (``ARCHITECTURE_INFO.md`` §7.2).

    1. valeur absente ou non textuelle : ``None`` ; 2. blancs de bord retirés ;
    3. première ligne gardée telle quelle ; 4. ligne vide ou de plus de 140
    points de code : ``None``.

    Args:
        raw: Valeur du champ.

    Returns:
        Ligne retenue, sa position et la position de la coupe.
    """
    if not isinstance(raw, str):
        return TitleCut(None, 0, None)
    stripped, lead = _strip_edges(raw)
    if not stripped:
        return TitleCut(None, 0, None)
    breaks = [pos for pos in (stripped.find(mark) for mark in _LINE_BREAKS) if pos >= 0]
    line_end = min(breaks) if breaks else None
    line = stripped if line_end is None else stripped[:line_end]
    too_long = len(line) > TITLE_MAX_CHARS
    candidates = [
        pos for pos in (line_end, TITLE_MAX_CHARS if too_long else None) if pos is not None
    ]
    cut = lead + min(candidates) if candidates else None
    retained = None if too_long or not line else line
    return TitleCut(retained, lead, cut)


# ── Passes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Flag:
    """Un signalement (une ligne ``v2_vocab_flags``, sans le mot détecté).

    Attributes:
        lang: ``fr`` ou ``en``.
        field: Chemin du champ.
        kind: ``vocab``, ``statut`` ou ``structure``.
        start: Début (ou coupe pour ``structure``), points de code.
        end: Fin exclusive, ``None`` pour ``structure``.
        rule_idx: Rang du motif, ``None`` pour ``structure``.
        field_sha256: Empreinte du champ brut.
    """

    lang: str
    field: str
    kind: str
    start: int
    end: int | None
    rule_idx: int | None
    field_sha256: str

    def key(self) -> tuple[str, str, str, int, str]:
        """Clé d'unicité de la table (hors ``brief_id``)."""
        return (self.lang, self.field, self.kind, self.start, self.field_sha256)


def field_sha256(value: str) -> str:
    """Empreinte SHA-256 (UTF-8) d'un champ brut.

    Args:
        value: Valeur du champ.

    Returns:
        Empreinte hexadécimale.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def level2_fields(data: Any, lang: str) -> list[tuple[str, str]]:
    """Champs de niveau 2 présents, dans l'ordre de la page.

    Args:
        data: ``vulgarization_data`` ou ``vulgarization_data_en`` décodé.
        lang: ``fr`` ou ``en``.

    Returns:
        Couples ``(chemin, valeur brute)`` des champs textuels non vides.
    """
    if not isinstance(data, Mapping):
        return []
    out: list[tuple[str, str]] = []

    def add(path: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            out.append((path, value))

    add(TITLE_FIELD[lang], data.get(TITLE_FIELD[lang]))
    for name in _PLAIN_FIELDS:
        add(name, data.get(name))
    concretely = data.get("concretely")
    if isinstance(concretely, Mapping):
        keys = [key for key in _CONCRETELY_ORDER if key in concretely]
        keys += sorted(str(key) for key in concretely if key not in _CONCRETELY_ORDER)
        for key in keys:
            add(f"concretely.{key}", concretely.get(key))
    add("reviewers_say", data.get("reviewers_say"))
    return out


def _allowed_spans(text: str, rules: FlagRules) -> list[tuple[int, int]]:
    return [match.span() for pattern in rules.allowed for match in pattern.finditer(text)]


def _overlaps(start: int, end: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start < span_end and span_start < end for span_start, span_end in spans)


def _conditioned(text: str, start: int, condition: re.Pattern[str]) -> bool:
    """Un marqueur de condition précède l'occurrence dans la même phrase."""
    sentence_start = 0
    for match in _SENTENCE_END.finditer(text, 0, start):
        sentence_start = match.end()
    return condition.search(text, sentence_start, start) is not None


def _scan(
    text: str,
    lang: str,
    *,
    rules: FlagRules,
    vocab: bool,
    status: bool,
) -> list[tuple[str, int, int, int]]:
    """Occurrences dans un texte : ``(kind, début, fin, rang)`` (positions du brut)."""
    norm, starts, ends = _normalise_with_map(text)
    if not norm:
        return []
    found: list[tuple[str, int, int, int]] = []
    if vocab:
        allowed = _allowed_spans(norm, rules)
        for rule in rules.vocab[lang]:
            for match in rule.pattern.finditer(norm):
                if match.group(0) in rule.not_exact or _overlaps(
                    match.start(), match.end(), allowed
                ):
                    continue
                found.append((KIND_VOCAB, starts[match.start()], ends[match.end() - 1], rule.idx))
    if status:
        for rule in rules.status[lang]:
            for match in rule.pattern.finditer(norm):
                if _conditioned(norm, match.start(), rules.conditions[lang]):
                    continue
                found.append((KIND_STATUT, starts[match.start()], ends[match.end() - 1], rule.idx))
    return found


def detect_field(lang: str, path: str, raw: str, rules: FlagRules) -> list[Flag]:
    """Les trois passes sur un champ.

    Args:
        lang: ``fr`` ou ``en``.
        path: Chemin du champ.
        raw: Valeur brute.
        rules: Règles.

    Returns:
        Signalements, triés par position.
    """
    digest = field_sha256(raw)
    flags: list[Flag] = []
    text, offset = raw, 0
    if path == TITLE_FIELD[lang]:
        cut = level2_title(raw)
        if cut.structure:
            flags.append(Flag(lang, path, KIND_STRUCTURE, int(cut.cut or 0), None, None, digest))
        if cut.retained is None:
            return flags
        text, offset = cut.retained, cut.offset
    for kind, start, end, idx in _scan(
        text, lang, rules=rules, vocab=True, status=path in rules.status_fields
    ):
        flags.append(Flag(lang, path, kind, offset + start, offset + end, idx, digest))
    flags.sort(key=lambda flag: (flag.start, flag.kind, flag.rule_idx or 0))
    return flags


@dataclass(frozen=True)
class BriefScan:
    """Résultat des passes sur un brief.

    Attributes:
        flags: Signalements détectés.
        fields: ``(langue, chemin) → empreinte`` de chaque champ lu.
    """

    flags: tuple[Flag, ...]
    fields: Mapping[tuple[str, str], str]


def scan_brief(row: Mapping[str, Any], rules: FlagRules) -> BriefScan:
    """Passes sur la vulgarisation FR et EN d'une ligne ``briefs``.

    Args:
        row: Ligne ``briefs`` (colonnes de vulgarisation).
        rules: Règles.

    Returns:
        Signalements et empreintes des champs lus.
    """
    flags: list[Flag] = []
    fields: dict[tuple[str, str], str] = {}
    for lang, column in VULGARISATION_COLUMN.items():
        for path, raw in level2_fields(load_blob(row.get(column)), lang):
            fields[(lang, path)] = field_sha256(raw)
            flags.extend(detect_field(lang, path, raw, rules))
    return BriefScan(tuple(flags), fields)


def tally(flags: Iterable[Flag]) -> dict[str, int]:
    """Nombre de signalements par sorte.

    Args:
        flags: Signalements.

    Returns:
        ``{kind: n}``.
    """
    return dict(sorted(Counter(flag.kind for flag in flags).items()))


# ── Écriture ────────────────────────────────────────────────────────


def flag_row(
    conn: sqlite3.Connection, row: Mapping[str, Any], rules: FlagRules, *, write: bool
) -> BriefScan:
    """Passes sur une ligne ``briefs`` et synchronisation de la table.

    Args:
        conn: Connexion (écriture si ``write``).
        row: Ligne ``briefs``.
        rules: Règles.
        write: Écrire dans ``v2_vocab_flags``.

    Returns:
        Résultat des passes.
    """
    scan = scan_brief(row, rules)
    if write:
        narrative_db.sync_vocab_flags(
            conn,
            str(row["id"]),
            [
                narrative_db.VocabFlagRow(
                    lang=flag.lang,
                    field=flag.field,
                    kind=flag.kind,
                    start=flag.start,
                    end=flag.end,
                    rule_idx=flag.rule_idx,
                    field_sha256=flag.field_sha256,
                )
                for flag in scan.flags
            ],
            scan.fields,
        )
    return scan


def flag_brief(db_path: str | Path, brief_id: str, config: NarrativeConfig) -> dict[str, Any]:
    """Nœud ``explainer_flags`` : passes et écriture pour un brief publié.

    Args:
        db_path: Base.
        brief_id: Brief.
        config: Configuration (chemins des règles).

    Returns:
        Compteurs par sorte (jamais le texte détecté).
    """
    rules = load_rules(config)
    with narrative_db.connect(db_path) as conn:
        row = narrative_db.fetch_brief(conn, brief_id)
        if not narrative_db.is_full_published_brief(row):
            return {"flags": {}, "fields": 0, "skipped": True}
        scan = flag_row(conn, row, rules, write=True)
    summary = {"flags": tally(scan.flags), "fields": len(scan.fields)}
    logger.info("narrative_explainer_flags_written", brief_id=brief_id, **summary)
    return summary


def flag_all(conn: sqlite3.Connection, config: NarrativeConfig, *, write: bool) -> dict[str, Any]:
    """Passes sur tous les briefs publiés complets (backfill).

    Args:
        conn: Connexion (écriture seulement si ``write``).
        config: Configuration.
        write: Écrire dans ``v2_vocab_flags``.

    Returns:
        Compteurs : briefs, champs lus, signalements par sorte et par langue,
        champs signalés par sorte (identifiants et chemins seulement).
    """
    rules = load_rules(config)
    by_kind: Counter[str] = Counter()
    by_lang: Counter[str] = Counter()
    flagged: dict[str, set[str]] = {}
    briefs = fields = 0
    ids = [row["id"] for row in narrative_db.list_published_briefs(conn) if not row["is_stub"]]
    for brief_id in ids:
        row = narrative_db.fetch_brief(conn, brief_id)
        if not narrative_db.is_full_published_brief(row):
            continue
        scan = flag_row(conn, row, rules, write=write)
        briefs += 1
        fields += len(scan.fields)
        for flag in scan.flags:
            by_kind[flag.kind] += 1
            by_lang[flag.lang] += 1
            flagged.setdefault(flag.kind, set()).add(f"{brief_id}:{flag.lang}:{flag.field}")
    return {
        "written": write,
        "briefs": briefs,
        "fields": fields,
        "flags": dict(sorted(by_kind.items())),
        "by_lang": dict(sorted(by_lang.items())),
        "flagged_fields": {kind: sorted(items) for kind, items in sorted(flagged.items())},
        "rules": dict(rules.fingerprints),
    }
