# Revue éditoriale FR DB post-N1.1 — Décision

**Date** : 27 avril 2026
**Sprint** : Cleanup post-N1.1
**Reviewer** : Bac
**Périmètre** : 41 occurrences de "découverte/découvrir" dans le contenu FR généré par les agents (briefs.body_markdown, briefs.vulgarization_data, briefs.panel_data, hypotheses.impact_analysis_json, hypotheses.auto_feedback_json)

## Décision

**No action.** Aucune des 41 occurrences ne désigne un brief SPORE en tant qu'objet produit. Toutes utilisent "découverte/découvrir" dans son sens français scientifique générique :

- finding scientifique (ex : "découverte d'un mécanisme")
- expression consacrée dans un domaine (ex : "drug discovery" = "découverte de médicaments")
- sens commun du verbe (ex : "découvrir des motifs précurseurs")

Reformuler ces occurrences appauvrirait le contenu scientifique sans gain de cohérence taxonomique.

## Rationale méta

Le rebrand `discoveries → briefs` (sprint N1.1) concernait le wording UI/produit. Le contenu éditorial scientifique généré par les agents relève d'un autre registre où "découverte" est légitime. Cette distinction confirme la pertinence du périmètre du grep d'audit (UI/code) et du choix de NE PAS scanner les colonnes EN externes (grounding_data, collision_json) immutables.

## Suivi

- Aucun sprint follow-up nécessaire
- CSV archivé dans `docs/audits/` pour traçabilité
