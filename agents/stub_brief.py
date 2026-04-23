"""Stub-brief generator for custom collisions that Synthesis refused to bridge.

The custom-runner promises in its docstring to "deliver a brief regardless"
for paying users. When Synthesis legitimately decides a pair has no
mechanistic bridge (only lexical/metaphorical), we honor that promise by
producing a short, honest analysis instead of leaving the user with a
generic failure message.

The stub brief is rendered as Markdown, ~1 page, explaining:
  1. Why no hypothesis was produced (user-friendly translation of the
     Synthesis verdict).
  2. The disciplinary obstacles identified.
  3. 2-3 alternative domain pairings that would likely bridge better.
  4. A footer framing the honesty-over-forced-synthesis stance.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from llm import get_llm_client
from logging_config import get_logger, get_token_tracker

logger = get_logger("stub_brief")


_STUB_PROMPT_TEMPLATE = """Tu es SPORE, un système de génération d'hypothèses scientifiques disruptives.

Un utilisateur a demandé une collision sur mesure entre deux domaines :
  * Domaine A : {domain_a}
  * Domaine B : {domain_b}

Ton agent de Synthèse a analysé cette paire et a conclu qu'il n'y avait **pas
de pont mécanistique exploitable** entre ces deux champs — uniquement des
similitudes lexicales ou métaphoriques. Voici le verdict du Synthesis
(souvent en anglais, technique) :

---
{no_bridge_reason}
---

Ta mission : rédiger une analyse courte, honnête et utile en français, au
format Markdown, adressée à un chercheur curieux mais non-spécialiste des
deux domaines. Longueur cible : ~800-1200 mots.

Structure OBLIGATOIRE (respecte les titres exactement) :

# Analyse d'une collision non productive : {domain_a} × {domain_b}

## Pourquoi cette collision n'a pas produit d'hypothèse

Traduis le verdict du Synthesis ci-dessus en français accessible. Explique
les différences de physique, d'échelles temporelles/spatiales, ou de
mathématiques sous-jacentes qui rendent le pont forcé. 2-3 paragraphes.

## Les obstacles identifiés

Liste à puces de 3-5 obstacles concrets (différences d'échelle, de
formalisme, d'objets d'étude, de méthodologie expérimentale…). Chaque
bullet : une phrase, précis.

## Pistes de recombinaison

Propose 2-3 domaines alternatifs qui croiseraient MIEUX avec **{domain_a}**
(le domaine du chercheur). Pour chaque suggestion : nom du domaine
alternatif + une phrase expliquant pourquoi ce croisement serait plus
fertile qu'avec {domain_b}.

## Note de SPORE

Termine par un petit paragraphe (~4 lignes) expliquant que SPORE privilégie
l'honnêteté scientifique à une synthèse forcée, et que cette analyse est
elle-même une contribution : elle documente une frontière disciplinaire
réelle et aide à recalibrer la recherche de collisions productives.

Contraintes :
- Pas de jargon gratuit — un M1 biologie ou physique doit pouvoir suivre.
- Pas d'invention : ne cite pas d'expériences imaginaires, ne forge pas de
  noms de théories.
- Ton : honnête, curieux, pas défensif. On documente, on n'excuse pas.
"""


async def generate_stub_brief(
    domain_a: str,
    domain_b: str,
    no_bridge_reason: str,
    stub_reason: str = "no_bridge_found",
) -> dict[str, Any]:
    """Build a stub brief for an unbridgeable custom collision.

    Args:
        domain_a: User-supplied domain (the one they care about).
        domain_b: Partner domain that couldn't be bridged.
        no_bridge_reason: Text explanation produced by Synthesis —
            typically in English, technical. The LLM will translate
            and vulgarize it.
        stub_reason: Short machine tag stored on the brief row and
            surfaced to the frontend for conditional rendering.
            Default ``"no_bridge_found"``.

    Returns:
        Dict with keys:
          - ``brief_id``: freshly minted ``SPR-YYYY-XXXX`` identifier.
          - ``markdown``: the rendered Markdown body.
          - ``title``: extracted from the first ``# `` line.
          - ``is_stub``: always True.
          - ``stub_reason``: passed through.
          - ``domain_a``, ``domain_b``: echoed for convenience.
          - ``generated_at``: ISO timestamp.

    The caller is responsible for persisting the markdown to disk and
    inserting the brief row via ``storage.save_stub_brief``.
    """
    brief_id = f"SPR-{datetime.utcnow().strftime('%Y')}-{uuid4().hex[:4].upper()}"

    prompt = _STUB_PROMPT_TEMPLATE.format(
        domain_a=domain_a,
        domain_b=domain_b,
        no_bridge_reason=no_bridge_reason,
    )

    logger.info(
        "stub_brief_generation_starting",
        brief_id=brief_id,
        domain_a=domain_a,
        domain_b=domain_b,
        stub_reason=stub_reason,
    )

    client = get_llm_client("research_brief")
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3000,
        temperature=0.6,
    )

    get_token_tracker().log_call(
        "stub_brief",
        response.model,
        response.input_tokens,
        response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    markdown = (response.content or "").strip()
    title = _extract_title(markdown, fallback=f"{domain_a} × {domain_b}")

    logger.info(
        "stub_brief_generated",
        brief_id=brief_id,
        title=title,
        length_chars=len(markdown),
    )

    return {
        "brief_id": brief_id,
        "markdown": markdown,
        "title": title,
        "is_stub": True,
        "stub_reason": stub_reason,
        "domain_a": domain_a,
        "domain_b": domain_b,
        "generated_at": datetime.utcnow().isoformat(),
    }


def _extract_title(markdown: str, fallback: str) -> str:
    """Return the first Markdown H1 line (without the hashes), or fallback."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def stub_brief_to_json(stub: dict[str, Any]) -> str:
    """Serialize a stub brief to the JSON shape the frontend expects.

    Mirrors the keys used by regular briefs so the brief-detail page can
    reuse a single renderer when ``is_stub`` is True.
    """
    payload = {
        "brief_id": stub["brief_id"],
        "title": stub["title"],
        "domains": [stub["domain_a"], stub["domain_b"]],
        "generated_at": stub["generated_at"],
        "is_stub": True,
        "stub_reason": stub.get("stub_reason"),
        "body_markdown": stub["markdown"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
