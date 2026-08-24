"""Extraction JSON partagée pour les agents qui parsent une sortie LLM (C17b).

Remplace cinq copies octet pour octet de ``_extract_json`` (literature_grounding,
hypothesis_sharpening, experimental_protocol, multi_reviewer_panel,
vulgarization), toutes strictes : fences markdown puis ``json.loads``, sans
aucune tolérance. DeepSeek V4 émet occasionnellement une virgule finale avant
l'accolade fermante, ce qui suffisait à faire échouer le nœud.

Trois niveaux, dans cet ordre, journalisés distinctement pour qu'on puisse
mesurer si la réparation devient fréquente :

1. ``json_parse_direct``  — ``json.loads`` réussit tel quel. Rien n'a été touché.
2. ``json_parse_repaired``— une réparation **strictement syntaxique** a suffi.
3. ``json_parse_retried`` — nouvel appel LLM à température plus basse.

Périmètre : syntaxe uniquement. Ce module ne complète JAMAIS un contenu
manquant, n'invente aucune clé, ne fournit aucune valeur par défaut. Un objet
qu'on n'arrive pas à parser lève — c'est à l'appelant de décider s'il tombe en
panne (literature_grounding, hypothesis_sharpening, experimental_protocol) ou
s'il applique son propre repli de conception (le panel, avec confidence=0.0).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from logging_config import get_logger

logger = get_logger(__name__)

# Le retry de dernier recours rejoue l'appel à cette température. 0.0 plutôt
# qu'un simple décrément : la sortie devient déterministe, donc un second échec
# signale un vrai problème de format et pas un tirage malheureux.
RETRY_TEMPERATURE = 0.0


def _strip_fences(text: str) -> str:
    """Retire un bloc de code markdown enveloppant.

    Tolère ```json, ``` nu, et une fence de fermeture absente (réponse
    tronquée au plafond de max_tokens).
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    # Coupe la première ligne (```json / ```), quelle que soit l'étiquette.
    newline = s.find("\n")
    if newline == -1:
        return s
    s = s[newline + 1 :]
    closing = s.rfind("```")
    if closing != -1:
        s = s[:closing]
    return s.strip()


def _slice_outermost(text: str) -> str:
    """Isole l'objet (ou le tableau) le plus externe.

    Traite le texte parasite avant et après : « Voici le JSON demandé : {...} »,
    ou un commentaire ajouté après l'accolade fermante. On repère le premier
    ``{`` ou ``[`` et son partenaire, en respectant les frontières de chaînes —
    une accolade DANS une chaîne ne compte pas.
    """
    start = -1
    opener = closer = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start, opener = i, ch
            closer = "}" if ch == "{" else "]"
            break
    if start == -1:
        return text

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # Pas de partenaire : réponse tronquée. On rend tout à partir de l'ouvrant,
    # json.loads produira une erreur parlante plutôt qu'un faux positif.
    return text[start:]


# Caractères de contrôle que JSON interdit nus dans une chaîne, et leur
# échappement canonique.
_CONTROL_ESCAPES = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _repair(text: str) -> tuple[str, list[str]]:
    """Réparations syntaxiques, en un seul passage conscient des chaînes.

    Deux corrections, toutes deux observées en production :

    * **virgule finale** avant ``}`` ou ``]``. C'est le défaut V4 courant. La
      regex naïve ``,\\s*}`` est refusée ici : elle matche à l'intérieur d'une
      chaîne et corrompt le littéral. ``{"note": "trailing, }", "n": 1,}`` doit
      perdre exactement une virgule, la dernière — pas celle du texte.
    * **caractère de contrôle nu** dans une chaîne (``Invalid control character
      at:`` dans les logs). Un saut de ligne littéral au milieu d'un littéral
      est échappé plutôt que de faire échouer tout l'objet.

    Returns:
        Le texte réparé et la liste des réparations appliquées (vide si aucune).
    """
    out: list[str] = []
    repairs: list[str] = []
    in_string = False
    escaped = False

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if in_string:
            if escaped:
                escaped = False
                out.append(ch)
            elif ch == "\\":
                escaped = True
                out.append(ch)
            elif ch == '"':
                in_string = False
                out.append(ch)
            elif ch in _CONTROL_ESCAPES:
                out.append(_CONTROL_ESCAPES[ch])
                if "control_char" not in repairs:
                    repairs.append("control_char")
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
                if "control_char" not in repairs:
                    repairs.append("control_char")
            else:
                out.append(ch)
            i += 1
            continue

        # Hors chaîne.
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == ",":
            # Virgule finale ? On regarde le prochain caractère significatif.
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                # On laisse tomber la virgule, on garde l'espacement.
                if "trailing_comma" not in repairs:
                    repairs.append("trailing_comma")
                i += 1
                continue

        out.append(ch)
        i += 1

    return "".join(out), repairs


def extract_json(content: str, *, agent: str) -> tuple[dict[str, Any], str]:
    """Niveaux 1 et 2 : parse direct, puis réparation syntaxique.

    Args:
        content: Réponse brute du LLM.
        agent: Nom de l'agent appelant, pour la journalisation.

    Returns:
        Tuple ``(données, niveau)`` où niveau vaut ``"direct"`` ou ``"repaired"``.

    Raises:
        json.JSONDecodeError: Si même après réparation le texte n'est pas du
            JSON valide. L'appelant décide de la suite.
    """
    candidate = _slice_outermost(_strip_fences(content))

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as first_error:
        repaired, repairs = _repair(candidate)
        if not repairs:
            # Rien à réparer : inutile de prétendre qu'on a essayé.
            raise
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            logger.warning(
                "json_parse_repair_insufficient",
                agent=agent,
                repairs=repairs,
                first_error=str(first_error),
                content_len=len(content),
            )
            raise first_error from None
        logger.warning(
            "json_parse_repaired",
            agent=agent,
            repairs=repairs,
            original_error=str(first_error),
            content_len=len(content),
        )
        return _require_mapping(data, agent), "repaired"

    logger.debug("json_parse_direct", agent=agent, content_len=len(content))
    return _require_mapping(data, agent), "direct"


def _require_mapping(data: Any, agent: str) -> dict[str, Any]:
    """Un agent attend un objet ; un tableau ou un scalaire est une erreur."""
    if not isinstance(data, dict):
        raise json.JSONDecodeError(
            f"expected a JSON object, got {type(data).__name__}", "", 0
        )
    return data


async def complete_json(
    client: Any,
    messages: list[dict[str, str]],
    *,
    agent: str,
    max_tokens: int,
    temperature: float,
    system: Optional[str] = None,
    tracker: Any = None,
) -> tuple[dict[str, Any], Any]:
    """Appelle le LLM en mode JSON et parse, avec les trois niveaux.

    Niveau 3 (dernier recours) : un unique nouvel appel à
    ``RETRY_TEMPERATURE``. Un seul — si une sortie déterministe échoue elle
    aussi, réessayer ne fera que brûler des jetons.

    Args:
        client: Client LLM (``llm.client.LLMClient``).
        messages: Messages de la requête.
        agent: Nom de l'agent, pour la journalisation et le tracker.
        max_tokens: Plafond de sortie.
        temperature: Température du premier appel.
        system: Prompt système optionnel.
        tracker: Token tracker optionnel ; chaque appel LLM y est enregistré,
            retry compris — sinon le coût d'un retry serait invisible.

    Returns:
        Tuple ``(données, dernière réponse LLM)``. La réponse est rendue parce
        que les appelants journalisent ``output_tokens`` sur échec.

    Raises:
        json.JSONDecodeError: Les trois niveaux ont échoué.
    """

    async def _call(temp: float) -> Any:
        response = await client.complete(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temp,
            system=system,
            json_mode=True,
        )
        if tracker is not None:
            tracker.log_call(
                agent=agent,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                provider=response.provider,
                cache_hit=response.cache_hit,
            )
        return response

    response = await _call(temperature)
    try:
        data, _level = extract_json(response.content, agent=agent)
        return data, response
    except json.JSONDecodeError as exc:
        logger.warning(
            "json_parse_retrying",
            agent=agent,
            error=str(exc),
            output_tokens=response.output_tokens,
            retry_temperature=RETRY_TEMPERATURE,
        )

    retry_response = await _call(RETRY_TEMPERATURE)
    data, level = extract_json(retry_response.content, agent=agent)
    logger.warning("json_parse_retried", agent=agent, level_after_retry=level)
    return data, retry_response
