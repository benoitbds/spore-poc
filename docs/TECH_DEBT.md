# SPORE — Dette technique

Constats structurels relevés en cours de sprint, dont la correction dépasse
le périmètre du sprint qui les a découverts. Chaque entrée porte : ce qui est
observé, comment le vérifier, ce que ça casse aujourd'hui, et ce qui est
explicitement décidé de ne PAS faire dans l'immédiat.

Ce fichier n'est pas un backlog produit — voir `BACKLOG.md` pour ça. Il ne
recense que des écarts entre le design documenté et le comportement réel.

---

## `briefs.hypothesis_id` — clé étrangère qui ne référence rien

*Relevé au sprint S3 (D1b-3), 2026-08-24. Aucune correction appliquée.*

### Constat

`SPORE_Post_Fire_Pipeline_v1.md:525` déclare la colonne comme une clé
étrangère :

```sql
hypothesis_id TEXT NOT NULL,      -- FK vers hypotheses
```

Elle n'en est pas une. Sur les 146 briefs en base :

| Mesure | Valeur |
|---|---|
| briefs total | 146 |
| dont `hypothesis_id` résout vers une ligne `hypotheses` | **0** |
| dont `hypothesis_id` = l'id du brief lui-même | 128 |
| dont `hypothesis_id` est un autre littéral | 18 |
| dont `hypothesis_id` est NULL | 0 |

Les 18 « autres » sont des marqueurs, pas des références : `auto-registered`,
`test-catalyst-hypothesis`, et des identifiants client `cus_*` pour les
collisions à la demande.

Vérification :

```sh
sqlite3 -readonly data/spore.db "
SELECT COUNT(*) AS total,
       SUM((SELECT COUNT(*) FROM hypotheses h WHERE h.id = b.hypothesis_id)) AS resolus,
       SUM(b.hypothesis_id IS NULL) AS nuls
FROM briefs b;"
-- attendu aujourd'hui : 146 | 0 | 0
```

Origine : `node_research_brief` et `node_persist_panel_reject`
(`graph/post_fire_pipeline.py`) écrivent `state.get("hypothesis_id", brief_id)`.
Le pipeline post-fire est presque toujours lancé sans `hypothesis_id` en
état, donc la valeur par défaut — l'id du brief — est ce qui est persisté.

### Trois conséquences, toutes vérifiées

**1. Aucun brief n'est rejouable par `cli.py post-fire`.**
La commande fait `get_hypothesis(hypothesis_id)` puis abandonne sur `None`
(`cli.py:581-584`). Comme aucun `hypothesis_id` ne résout, la commande ne peut
rejouer aucun des 146 briefs — pas seulement ceux qui posent problème. Un
rejeu exige aujourd'hui un script ad hoc appelant `run_post_fire_pipeline()`
directement, avec des entrées reconstruites depuis le sidecar JSON — lequel
porte `original_hypothesis` et `domains`, mais **pas** `mechanisms`, pourtant
argument requis.

**2. La traçabilité vers la collision d'origine est rompue.**
Rien en base ne relie un brief publié à l'hypothèse L0 qui l'a produit, donc
ni aux domaines entrés en collision, ni au génome actif à ce moment, ni au run
L0 correspondant. Le lien n'existe plus que dans le sidecar sur disque, sous
forme de texte libre. Toute analyse « quel type de collision produit les
meilleurs briefs » est impossible en SQL aujourd'hui.

**3. Le prédicat du front teste une condition qui ne filtre rien.**
`src/lib/brief-visibility.ts` (`PUBLISHABLE_BRIEF_SQL`) teste
`status = 'complete' AND hypothesis_id IS NOT NULL`. La seconde moitié est
inerte : zéro ligne a la colonne à NULL. Le test est conservé tel quel pour
que la version SQL et son miroir TypeScript `isPublishableBrief()` restent
littéralement identiques — les faire diverger coûterait plus cher que de
garder une condition sans effet. À retirer des deux côtés en même temps que
la colonne sera réparée.

### Décidé : ne rien corriger maintenant

Aucune modification de schéma, aucune migration, aucun backfill. Une
correction supposerait de retrouver le lien brief → hypothèse pour
l'historique, ce qui n'est possible que par rapprochement textuel depuis les
sidecars, avec un taux d'erreur non nul sur 146 lignes. Sprint dédié.

Ce qu'un tel sprint devra traiter :
- passer `hypothesis_id` dans l'état du pipeline post-fire pour que les
  nouveaux briefs soient correctement liés ;
- décider du sort de l'historique (rapprochement, ou `NULL` assumé) ;
- ajouter la contrainte `FOREIGN KEY` que le design doc décrit déjà ;
- retirer le test inerte du prédicat de visibilité.

---

## Sidecars `outputs/briefs/` — aucun lien avec le statut en base

*Relevé au sprint S3 (D1b-2), 2026-08-24. Déplacement ponctuel appliqué, mécanisme non implémenté.*

`agents/research_brief_generator.py:save_brief()` écrit `{id}.md` et
`{id}.json` au moment du nœud `research_brief_generator`. Rien ne les
revisite ensuite : un brief dépublié plus tard garde ses fichiers en place, et
`spore-web/public/briefs` est un symlink vers ce répertoire, donc ils restent
servis publiquement.

Décision produit prise au S3 : ces sidecars deviennent un **format documenté
et citable**, ce qui rend leur cohérence avec le statut nécessaire et non plus
optionnelle. Le mécanisme reste à arbitrer — inventaire, cause racine et
proposition dans `docs/S3_D1b2_sidecars.md`.
