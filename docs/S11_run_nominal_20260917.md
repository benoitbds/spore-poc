# S11 — premier run avec B.1 à B.5 en production

`run-20260917-041504-cda424`, cron de 04:15 UTC du 17/09/2026. C'est la
condition d'entrée de B.6 : « après le déploiement de B.1 à B.5 et un run
nominal observé ».

## Issue du run

100 collisions, taux de pont 94,4 %, 3 hypothèses curées, 0,1165 $.
Deux briefs produits :

| Brief | Statut | Consensus | Verdict du panel |
|---|---|---|---|
| `SPR-2026-A08D` | `complete` | 6,04 | `publish_brief` |
| `SPR-2026-R2444` | `rejected` | 5,83 | `publish_brief` |

Le second est rejeté par le gate de sélection relatif S9.3, pas par le panel :
comportement nominal, le seuil du jour était au-dessus de 5,83.

**Aucune ligne `failed_*` créée.** Les deux seules existantes restent celles de
la quarantaine du 16/09.

## Tableau A.4, mesuré et non plus estimé

280 appels, **100 % `finish_reason = stop`**, **0 appel arrivé au plafond**,
**0 rejeu**. Le tableau A.4 du diagnostic estimait ces valeurs à partir de la
taille des artefacts ; elles sont désormais lues dans `llm_calls`.

| Nœud | Appels | Plafond | p50 | p95 | max | marge au plafond |
|---|---:|---:|---:|---:|---:|---:|
| `experimental_protocol` | 4 | 16000 | 4 331 | 5 849 | 6 056 | **2,6×** |
| `literature_grounding` | 2 | 16000 | 3 397 | 3 878 | 3 931 | 4,1× |
| `hypothesis_sharpening` | 4 | 8000 | 2 509 | 3 388 | 3 533 | 2,3× |
| `meta_reviewer` | 4 | 4000 | 1 150 | 1 685 | 1 742 | 2,3× |
| `reviewer_funding_strategist` | 4 | 4000 | 1 742 | 2 112 | 2 158 | 1,9× |
| `reviewer_domain_expert` | 4 | 4000 | 1 394 | 1 659 | 1 691 | 2,4× |
| `reviewer_methodologist` | 4 | 4000 | 1 373 | 1 503 | 1 524 | 2,6× |
| `reviewer_contrarian` | 4 | 4000 | 1 288 | 1 412 | 1 429 | 2,8× |
| `reviewer_industrialist` | 4 | 4000 | 1 216 | 1 352 | 1 363 | 2,9× |
| `synthesis` | 36 | 4000 | 1 387 | 1 774 | 1 939 | 2,1× |
| `critic_devil` | 34 | 8000 | 1 346 | 1 928 | 2 088 | 3,8× |
| `critic_angel` | 34 | 8000 | 1 132 | 1 514 | 1 586 | 5,0× |
| `gate` | 100 | 400 | 138 | 231 | 260 | 1,5× |
| `vulgarization` | 1 | 3000 | 1 165 | — | 1 165 | 2,6× |
| `translate_panel` | 24 | 2500/3500 | 198 | 400 | 432 | 5,8× |

Deux lectures :

1. **Le protocole tenait déjà dans 8000 ce jour-là** (max 6 056). La troncature
   n'est pas systématique : elle frappe les protocoles longs, ceux des
   hypothèses les plus fournies. Le plafond à 16000 ne corrige donc pas un run
   moyen, il supprime la queue de distribution qui cassait.
2. **`gate` est le nœud le plus serré** : 260 jetons produits pour un plafond
   de 400, soit 1,5× seulement. Il n'a jamais tronqué, mais c'est la marge la
   plus faible du pipeline — à surveiller au relevé des sept jours.

Le critère d'acceptation demande cette réédition **après sept jours** :
`.venv/bin/python -m scripts.llm_calls_report --days 7`, à refaire le 24/09.

## Ce que le run confirme du reste du sprint

- **B.2** : chaque `llm_call` porte son `run_id` et son `hypothesis_id`
  (`run-20260917-041504-cda424` / `SPORE-2026-09-17-25c2ff5f`). Les événements
  du fan-out aussi.
- **B.3** : les plafonds appliqués sont ceux du genome, aucun
  `max_tokens_clamped`.
- **B.1** : aucun `llm_output_truncated`, aucun `json_parse_retrying`, aucun
  `llm_call_not_recorded` — la mesure n'a rien perdu et n'a rien bloqué.
- **Le modèle servi diffère toujours du modèle demandé** :
  `deepseek-v4-flash` → `deepseek-flash`, sur les 280 appels.
- Digest de 05:30 : « 0 chose à regarder », donc aucun mail. Nominal.
