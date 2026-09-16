# S11 — B.0.7 : d'où vient la dérive de volume ?

Relevé en lecture seule, 16/09/2026. Rapport seulement, aucune correction dans
S11. Complète `docs/S11A_diagnostic_troncature.md` (point A.4) et
`docs/S11B_verifications.md` (point B.0.1).

**Réponse : du modèle, pas des prompts du post-fire ni de la matière première
bibliographique. L'événement est daté au 10 septembre 2026.**

## Quatre séries indépendantes marchent le même jour

Deux d'entre elles sont entièrement hors du post-fire, ce qui est l'argument
décisif : un changement propre au post-fire ne peut pas les déplacer.

| Série | Avant le 10/09 | Après | Hors post-fire ? |
|---|---|---|---|
| Taux de passage du gate L0 | 24-30 % (4-9/09) | 41-52 % (10-16/09) | **oui** |
| `runs.total_tokens_out` par hypothèse | 5 000-5 800 | 6 400-8 244 | **oui** |
| `critic_debate_log` (L0), médiane | ~6 500 | ~10 000 | **oui** |
| Prédictions du sharpening / protocole | 2,5 préd., ~11 800 car. | 5-7 préd., ~20 300 car. | non |

Le prompt du gate (`prompts/gate.txt`) n'a pas bougé depuis le 11 avril.

## Ce que le relevé écarte

- **Un changement local.** Aucun commit entre le 29/08 et le 14/09. Aucun
  fichier suivi de `agents/`, `graph/`, `llm/`, `prompts/`, `data/*.yaml` n'a
  de mtime entre le 30/08 et le 13/09. `.env` date du 01/05 (13 variables,
  jeu de noms inchangé). `genome_version` = `l0_v1` sur les 109 hypothèses de
  la fenêtre. Dernière mutation L1 : 09/05, et le cron L1 est désactivé.
- **Plus de matière première.** `all_papers` est plat (50,5 → 63,5, sans
  tendance, et 52,5 le jour même du saut), `search_queries` plat (11 → 12),
  et les 429 de Semantic Scholar suivent le volume de requêtes sans tendance
  monotone — leur pic est en S34, pas en S37/S38. La hausse d'`evidence_base`
  (9 → 14) est une sélection plus large **dans** un corpus constant : de la
  verbosité en aval, pas plus d'entrée.
- **Un effet de format général.** Les sorties structurées et courtes n'ont pas
  bougé : `scores_json` (~135), `impact_analysis_json` (~2 200),
  `bridge_json` (+6,5 %), et l'entrée `collision_json` (~26-30 k). Seules les
  sorties en **prose libre non contrainte** ont grossi.

## Deux nuances qui corrigent B.0.1

1. **Le protocole ne dépasse pas son niveau d'août.** B.0.1 comparait le creux
   de S35/S36 à S37/S38. S33/S34 étaient déjà à ~18 600 caractères : le 10/09
   *ramène* le protocole à son niveau d'août. Le creux du 25/08 au 09/09 est,
   lui, expliqué par un commit — `1b345b3` du 24/08, mode JSON natif DeepSeek,
   qui compacte les sorties. Le dépassement réel porte sur le **nombre de
   prédictions** (3 → 5, +67 %) et sur `sharpened_data` (~8 000 → ~11 800).
2. **Le taux de passage du gate, lui, dépasse tout ce qu'on observe** dans la
   fenêtre (29-37 % avant, 41-52 % après). Ce n'est pas un retour à un niveau
   antérieur. Le gate rejette désormais **moins**, ce qui éloigne d'autant la
   recalibration évoquée dans `CLAUDE.md`.

Piège écarté au passage : `critic_debate_log` est à 0 en S33/S34 parce que la
colonne n'est peuplée qu'à partir du 25/08 (`1776827`). C'est une absence de
fonctionnalité, pas un rétrécissement ; le ratio S33→S38 sur cette colonne
n'a aucun sens. De même, les `predictions_json` de L0 (médiane 2, plate) et
les `falsifiable_predictions` du sharpening (3 → 5) sont deux champs produits
par deux nœuds différents.

## Ce qui manque pour conclure, et ce qui le fournira

L'inférence « révision du modèle côté fournisseur le 10/09 » est la mieux
soutenue — date observée directement, causes locales exclues une à une — mais
**elle n'est pas prouvée**. La preuve directe aurait été l'empreinte du modèle
servi ce jour-là : `response_model` et `system_fingerprint`. Ces colonnes
existent depuis aujourd'hui seulement (`300d73b`), donc la table `llm_calls`
est vide pour le 10/09.

Elle ne le sera plus au prochain événement de ce type. Le premier relevé
montre d'ailleurs que l'écart est réel : SPORE demande `deepseek-v4-flash` et
l'API répond `deepseek-flash`, empreinte `aeb56401…`.

## Limites du relevé

- La crontab est lisible (`crontab -l`) mais **non datable** :
  `/var/spool/cron/crontabs/` est en accès refusé. C'est le seul point de la
  mesure 3 resté ouvert.
- `briefs` n'a pas de colonne `updated_at` : l'affirmation « les rejeux S10-B
  et S10-C n'ont touché ni `grounding_data`, ni `sharpened_data`, ni
  `protocol_data` » est vraie par construction du code de rejeu, mais n'est
  pas vérifiable en base *a posteriori*. Elle ne porte de toute façon aucune
  conclusion : les séries témoins L0 sont indépendantes de `briefs`.
