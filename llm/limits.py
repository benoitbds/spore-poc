"""Plafonds de sortie LLM : une seule source, des bornes explicites.

Le plafond d'un nœud vient du genome (``agents.<nœud>.parameters.max_tokens``),
borné entre un plancher propre au nœud et un plafond commun. Avant S11 chaque
site d'appel portait sa propre constante, le genome disait parfois autre chose,
et personne ne voyait la divergence : le nœud protocole a tourné à 8000 pendant
des mois avec un commentaire affirmant à tort que DeepSeek plafonnait à 8192.

Les planchers des quatre nœuds du post-fire viennent du diagnostic S11-A : à
8000, le protocole était coupé une fois sur deux en septembre. Pour tous les
autres nœuds, le plancher est la valeur en vigueur avant ce sprint — aucun
changement de comportement n'est introduit ici.
"""

from __future__ import annotations

from logging_config import get_logger

logger = get_logger("llm_limits")

#: Plafond absolu de ``max_tokens``, tous nœuds confondus.
#:
#: Un rejeu ne double jamais au-delà : au-dessus, une sortie encore coupée ne
#: relève plus du plafond mais du contenu demandé, et doubler ne ferait que
#: multiplier le coût d'un appel condamné.
MAX_TOKENS_CEILING = 32000

#: Plancher appliqué à un nœud inconnu de la table ci-dessous.
DEFAULT_FLOOR = 4000

#: Plancher par nœud. Les quatre premiers viennent de S11-A ; les suivants
#: reprennent la valeur en vigueur au moment du sprint.
MAX_TOKENS_FLOORS: dict[str, int] = {
    # Post-fire — nœuds tronqués en septembre (S11-A).
    "experimental_protocol": 16000,
    "literature_grounding": 16000,
    "reviewer_methodologist": 4000,
    "reviewer_domain_expert": 4000,
    "reviewer_contrarian": 4000,
    "reviewer_industrialist": 4000,
    "reviewer_funding_strategist": 4000,
    "meta_reviewer": 4000,
    # Post-fire — valeurs inchangées.
    "hypothesis_sharpening": 8000,
    "literature_grounding_queries": 2000,
    "vulgarization": 3000,
    # L0 — valeurs inchangées.
    "gate": 400,
    "synthesis": 4000,
    "critic_devil": 8000,
    "critic_angel": 8000,
    "impact": 2000,
    "reviewer": 1000,
    "stub_brief": 3000,
    # L1 — valeurs inchangées.
    "l1_critic": 2000,
    "l1_strategist": 4000,
}

#: Nœuds servis par une entrée de genome portant un autre nom. Les cinq
#: reviewers et la meta-review partagent la configuration ``multi_reviewer_panel``
#: parce que le genome décrit le panel, pas chaque persona.
GENOME_AGENT_FOR_NODE: dict[str, str] = {
    "reviewer_methodologist": "multi_reviewer_panel",
    "reviewer_domain_expert": "multi_reviewer_panel",
    "reviewer_contrarian": "multi_reviewer_panel",
    "reviewer_industrialist": "multi_reviewer_panel",
    "reviewer_funding_strategist": "multi_reviewer_panel",
    "meta_reviewer": "multi_reviewer_panel",
    # Le premier appel du grounding extrait les requêtes de recherche ; il est
    # court par nature et n'a pas d'entrée propre dans le genome.
    "literature_grounding_queries": "",
}


def genome_max_tokens(node: str) -> int | None:
    """Lit le plafond du genome pour un nœud, sans le borner.

    Args:
        node: Nom du nœud, tel que passé à ``client.complete``.

    Returns:
        Valeur du genome, ou ``None`` si le nœud n'a pas d'entrée, si le genome
        ne fixe pas de ``max_tokens``, ou si la lecture échoue.
    """
    agent = GENOME_AGENT_FOR_NODE.get(node, node)
    if not agent:
        return None
    try:
        from config import get_genome

        parameters = get_genome().agents.get(agent, {}).get("parameters", {})
        value = parameters.get("max_tokens")
    except Exception as exc:  # noqa: BLE001 — un genome illisible ne casse pas l'appel
        logger.warning("genome_max_tokens_unreadable", node=node, error=str(exc))
        return None
    return int(value) if isinstance(value, (int, float)) else None


def max_tokens_for(node: str) -> int:
    """Rend le plafond de sortie d'un nœud, borné et journalisé.

    Une valeur hors bornes est ramenée dans l'intervalle et signalée en
    ``WARNING`` (``max_tokens_clamped``) : une mutation L1 qui passerait le
    protocole sous son plancher redeviendrait une troncature systématique, et
    elle serait invisible si le code corrigeait en silence.

    Args:
        node: Nom du nœud, tel que passé à ``client.complete``.

    Returns:
        Plafond à demander au fournisseur.
    """
    floor = MAX_TOKENS_FLOORS.get(node)
    if floor is None:
        logger.warning("max_tokens_node_unknown", node=node, fallback=DEFAULT_FLOOR)
        floor = DEFAULT_FLOOR

    configured = genome_max_tokens(node)
    if configured is None:
        return floor

    bounded = max(floor, min(configured, MAX_TOKENS_CEILING))
    if bounded != configured:
        logger.warning(
            "max_tokens_clamped",
            node=node,
            configured=configured,
            applied=bounded,
            floor=floor,
            ceiling=MAX_TOKENS_CEILING,
        )
    return bounded
