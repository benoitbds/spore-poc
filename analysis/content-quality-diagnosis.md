# SPORE — Diagnostic qualité du funnel & plan de remise en production

Daté 2026-07-20. Backe `analysis/funnel_analysis.py`. Objectif fixé par
l'opérateur : **maximiser la qualité des briefs publiés, sous contrainte
d'un plancher de ≥1 brief publiable / jour**, afin de réactiver le cron L0
(désactivé manuellement car plus aucun brief n'atteignait le statut
publiable depuis fin avril).

## 1. Symptôme

- Cron L0 quotidien actif jusqu'au 18 mai, puis désactivé manuellement.
- 0 brief organique publié depuis ~24 avril. Les « 3 briefs de mai » en
  base sont des runs custom/manuels (leur `hypothesis_id` ne pointe vers
  aucune hypothèse L0 — champ jamais peuplé correctement, cf. §6).
- Le cron L1 était déjà désactivé (S8.2, 9 mai) pour une raison distincte.

## 2. Où meurt le funnel

En **un seul point** : le gate de promotion `intéressant → a_tester` dans
`agents/reviewer.py::evaluate_override` (règle S8.1-bis) :

```
promote si composite >= 0.40 AND hallucination_risk <= 0.45
```

Le post-fire ne se déclenche que sur les `a_tester`. Pas de `a_tester` →
pas de post-fire → pas de brief. Le panel post-fire lui-même n'est PAS le
goulot : quand une hypothèse l'atteint, elle publie à **93 %** (25/27).

## 3. Preuve — dérive mono-axe sur hallucination_risk

Moyennes des scores critic, avril vs mai (hypothèses curées) :

| Axe | Avril | Mai | |
|-----|-------|-----|---|
| novelty | 0.484 | 0.513 | ↑ |
| coherence | 0.597 | 0.547 | ~ |
| testability | 0.664 | 0.657 | plat |
| impact_potential | 0.527 | 0.507 | plat |
| **hallucination_risk** | 0.432 | **0.483** | ↑ (au-dessus du plafond 0.45) |
| composite | 0.426 | 0.407 | ~ |

Un seul axe a dérivé dans le mauvais sens : `hallucination_risk`. La
novelty a *monté*. Résultat : a_tester passe de 16 (avril) à 1 (mai),
briefs de 22 à ~0 organique.

- Le fallback critic par défaut est **0.3**, pas 0.5 → la montée n'est PAS
  un artefact de parse-fail (ceux-ci baisseraient halluc).
- Le revert de génome (S8.2, 9 mai) n'a rien corrigé : halluc post-9-mai
  (0.505) est *pire* que pré-9-mai (0.453). **La dérive est hors-génome**
  (prompt devil et/ou dérive du modèle DeepSeek sur cet axe subjectif).

## 4. Vérité terrain — le composite discrimine, halluc est du bruit

17 hypothèses ont un feedback humain. En comparant au verdict machine sur
la **même** hypothèse :

| Feedback humain | Verdict machine |
|-----------------|-----------------|
| `trash` (×4) | poubelle ×4 ✓ |
| `want_to_test` (×8) | poubelle ×3, intéressant ×5, **a_tester ×0** |

La machine reconnaît bien les déchets, mais son gate `a_tester` **ne capte
aucune** des 8 hypothèses que l'humain voulait tester. En regardant les
scores :

- `trash` humaines : composite **0.27–0.31** | halluc 0.53–0.60
- `want_to_test` humaines : composite **0.40–0.53** | halluc **0.20–0.58**

→ Sur **composite**, fossé net entre trash (≤0.31) et good (≥0.40) : c'est
  l'axe qui discrimine.
→ Sur **halluc**, recouvrement total (trash 0.53–0.60 ⊂ good 0.20–0.58) :
  **axe non discriminant**. Le gate dur `halluc ≤ 0.45` filtre du bruit et
  bloque 4 des 8 hypothèses human-valorisées.

Note : 2 des meilleures want_to_test (0.526/0.25, 0.486/0.20) sont restées
« intéressant » uniquement parce qu'elles précèdent le déploiement
S8.1-bis (11 mai) ; sous le code actuel elles seraient promues.

De plus, halluc est **déjà** pénalisé dans le composite (poids −0.15). Le
gate dur le **double-compte**.

## 5. Plafond de débit — le « ≥1/jour » est au-dessus du record historique

- Avril (mois sain) : 25 jours de cron → 22 briefs = **0,88 brief/jour**.
  Le moteur organique n'a **jamais** tenu 1/jour de façon fiable.
- Deuxième collapse : candidats curés/jour ~4,7 (avril) → **~1** (mai),
  car `curator.top_percent = 0.10` sur ~8 bridges = 1 hypothèse/jour.
- Conséquence : même gate grand ouvert + panel à 93 %, plafond mécanique
  ≈ 1 brief/jour **sans marge**. Le moindre no_bridge/kill passe sous le
  plancher.

→ Le plancher « ≥1/jour » exige d'**élargir le funnel** (plus de candidats
  curés/jour), pas seulement de débloquer le gate.

## 6. Le panel n'est pas un filtre qualité

- Publie 93 % (25/27), consensus 5.63–7.0 (moy 6.43).
- Le contrarian est le score min dans 25/27 briefs et dit *toujours*
  `weak_reject` ; le methodologist dit *toujours* `accept` (moy 8.04). Ces
  deux personas n'ont quasi aucun pouvoir discriminant individuel.
- Consensus = moyenne pondérée-confiance des 5 → le contrarian, dilué à
  1/5, ne fait baisser que de ~0.4. Il est battu 4-contre-1.
- **Donc le gate `a_tester` (composite) est aujourd'hui le seul vrai
  filtre qualité.** Tout ce qui le passe publiera.

Bug annexe : `briefs.hypothesis_id` non peuplé de façon fiable
(« auto-registered », « test-catalyst-hypothesis », ou l'id du brief). Le
lien brief→hypothèse source est cassé → impossible de tracer un brief vers
son feedback humain. À réparer pour toute analyse qualité future.

## 7. Plan (qualité-first, plancher ≥1/jour garanti)

Trois leviers en tension, séquencés pour **valider la qualité avant
d'ouvrir le volume** :

1. **Débloquer (prouvé, pas un tapis roulant)** — retirer le gate dur
   `halluc ≤ 0.45` de la promotion a_tester ; garder uniquement le
   kill-switch extrême (`halluc > 0.65`) et s'appuyer sur `composite`, qui
   sépare empiriquement trash (≤0.31) de good (≥0.40). Optionnel :
   ré-ancrer le prompt devil (rubrique 0.3/0.5/0.7) pour re-stabiliser la
   distribution halluc à la source.
2. **Élargir le débit** — monter `curator.top_percent` (0.10 → ~0.25)
   et/ou `explorer.collisions_per_cycle`, jusqu'à ~3–4 candidats/jour en
   post-fire, pour une marge au-dessus du plancher.
3. **Durcir le panel (cœur du "qualité-first")** — donner du poids réel au
   contrarian OU pénaliser la dispersion (consensus doit vouloir dire
   accord), pour qu'ouvrir le débit n'inonde pas le site de briefs
   médiocres. Backtestable **hors-ligne** sur les 27 `panel_data` stockés,
   sans appel API.

Puis : **observation 3–4 jours** cron réactivé, en surveillant
briefs/jour ET consensus moyen, avant de déclarer stable.

Garde-fou : le durcissement du panel s'applique aux **nouveaux** briefs ;
les 27 existants ne sont pas dépubliés sans revue nominative par
l'opérateur.
