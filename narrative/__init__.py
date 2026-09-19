"""Couche narrative SPORE v2 (étage fiction, thèmes, voisines).

La couche s'exécute après ``validate_brief`` : le brief est déjà publié quand
elle démarre, et rien de ce qu'elle fait ne peut le dépublier. Elle écrit
uniquement dans les tables additives ``v2_*`` (``storage/narrative_db.py``).

Modules :

* ``narrative.safety`` — garde des chemins (D-010) et chargement des seules
  clés LLM ;
* ``narrative.config`` — configuration propre à la couche
  (``config/narrative/narrative.yaml``), distincte du genome L0 ;
* ``narrative.llm`` — point d'appel LLM unique (client, mode JSON et parseur
  partagé, rejeu avec backoff, registre de coût ``v2_llm_costs``) ;
* ``narrative.prompting`` / ``narrative.inputs`` — prompts versionnés
  (``narrative/prompts/``) et entrées lues dans la ligne ``briefs`` ;
* ``narrative.writer`` — rédaction FR et traduction EN ;
* ``narrative.checks`` / ``narrative.guard`` — contrôles mécaniques et juge,
  fail-closed ;
* ``narrative.story`` — tentatives persistées dans ``v2_stories`` ;
* ``narrative.themes`` / ``narrative.linking`` / ``narrative.neighbours`` /
  ``narrative.mechanical`` — étapes mécaniques (thèmes, lien d'hypothèse,
  voisines) ;
* ``narrative.graph`` — sous-graphe LangGraph (``NarrativeState``) et nœud
  d'enveloppe ``narrative_layer`` câblé dans le graphe Post-Fire.

Aucun import lourd ici : ``graph.post_fire_pipeline`` importe
``narrative.graph`` à la construction du graphe.
"""
