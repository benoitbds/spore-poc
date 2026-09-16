"""Bornes des plafonds de sortie LLM.

B.1 n'y pose que le plafond absolu, utilisé par le rejeu à plafond doublé de
``llm.json_parse.complete_json``. B.3 y ajoutera les planchers par nœud et la
lecture du genome.
"""

from __future__ import annotations

#: Plafond absolu de ``max_tokens``, tous nœuds confondus.
#:
#: Un rejeu ne double jamais au-delà : au-dessus, une sortie encore coupée ne
#: relève plus du plafond mais du contenu demandé, et doubler ne ferait que
#: multiplier le coût d'un appel condamné.
MAX_TOKENS_CEILING = 32000
