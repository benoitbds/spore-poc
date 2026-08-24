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

---

## Un 404 de brief porte encore les en-têtes `Link` hreflang

*Relevé au sprint S3 (D2), 2026-08-24. Sans conséquence connue. Aucune correction appliquée.*

Une page de brief non publiable répond bien 404, mais la réponse porte quand
même les alternates de la page qui n'existe pas :

```sh
curl -s -D - -o /dev/null https://<site>/fr/briefs/SPR-2026-52AA | grep -i '^link'
# link: <.../fr/briefs/SPR-2026-52AA>; rel="alternate"; hreflang="fr",
#       <.../en/briefs/SPR-2026-52AA>; rel="alternate"; hreflang="en",
#       <.../briefs/SPR-2026-52AA>;    rel="alternate"; hreflang="x-default"
```

Origine : l'en-tête est émis par `createMiddleware` de `next-intl` (v4.11)
dans `src/middleware.ts`. Le middleware s'exécute **avant** le rendu de la
route ; il ne peut pas savoir que la page va appeler `notFound()`. Ce n'est
donc pas un oubli dans `generateMetadata`, qui fait bien sa part : la branche
non publiable y retourne déjà `robots: { index: false, follow: false }`.

Pourquoi c'est sans conséquence : un `Link: rel="alternate"` sur une réponse
404 désigne des URL qui répondent elles aussi 404. Google ignore les alternates
d'une page non indexable, et le 404 reste le signal dominant. Aucune des trois
URL annoncées n'est indexable.

Pourquoi on ne corrige pas : il faudrait que le middleware connaisse la
publiabilité, donc lise SQLite dans le middleware — un accès base sur le
chemin de toutes les requêtes, pour supprimer un en-tête inerte. Le coût est
sans rapport avec l'enjeu. À revoir si `next-intl` expose un jour un moyen de
conditionner l'émission depuis la route.

---

## Sortir un fichier de `public/` sans redémarrer renvoie 400, pas 404

*Relevé au sprint S3 (quarantaine 7C1B/B172), 2026-08-24. Contourné par un redémarrage. À intégrer au mécanisme de réconciliation.*

Next 14 (`next start`) indexe l'arborescence de `public/` **au démarrage du
serveur**, pas à chaque requête. Un fichier retiré après le démarrage reste
donc dans l'index : la requête matche, l'envoi échoue sur `ENOENT`, et Next
répond **400 Bad Request** — pas 404.

Observé sur le serveur de prod (démarré 08:40) après le déplacement de
7C1B/B172 en quarantaine à 09:07 :

| URL | Fichier déplacé | Code |
|---|---|---|
| `/briefs/SPR-2026-52AA.json` | avant le démarrage 08:40 | 404 |
| `/briefs/SPR-2026-7C1B.json` | après le démarrage | **400** |
| `/briefs/SPR-2026-NOPE.json` | n'a jamais existé | 404 |

Le contenu n'est plus servi dans les deux cas — la quarantaine fait son
travail. Mais 400 n'est pas un signal de retrait pour un moteur de recherche,
là où 404 en est un. Conséquence directe pour la pièce 3 de
`docs/S3_D1b2_sidecars.md` : une commande `reconcile` qui déplace des fichiers
doit être suivie d'un redémarrage du serveur, ou tourner avant son démarrage.
Un déplacement à chaud laisse des 400 jusqu'au prochain `next start`.

---

## Un `novelty_score` NULL fait tomber `/fr/briefs` en 500

*Relevé au sprint S3 (D2), 2026-08-24. Défaut spore-web, préexistant à D2. Aucune correction appliquée — hors périmètre du sprint.*

`EditorialBriefCard.tsx:103` fait `novelty.toFixed(2)` sur
`grounding.novelty_assessment.score`. Le type TypeScript déclare
`score: number` (`types.ts:30`), donc le compilateur ne voit rien ; à
l'exécution un `null` lève `TypeError: Cannot read properties of null
(reading 'toFixed')` **dans un composant serveur**. La carte est rendue dans
la boucle de la liste : **un seul brief concerné met toute la page
`/fr/briefs` en 500**, pas seulement sa carte.

Reproduction (base copiée, aucune écriture réelle) :

```sh
sqlite3 -readonly data/spore.db "VACUUM INTO '/tmp/sim.db'"
sqlite3 /tmp/sim.db "PRAGMA journal_mode=WAL;
UPDATE briefs SET novelty_score=NULL,
  grounding_data=json_set(grounding_data,'\$.novelty_assessment.score',json('null'))
WHERE id='SPR-2026-F2F4';"
SPORE_DB_PATH=/tmp/sim.db npx next dev -p 5097
curl -s -o /dev/null -w '%{http_code}\n' localhost:5097/fr/briefs   # 500
curl -s -o /dev/null -w '%{http_code}\n' localhost:5097/fr/briefs/SPR-2026-F2F4  # 200
```

La page de détail survit : `db.ts:308` coalesce (`nov.score ?? 0`) sur le
chemin du teaser. Ce n'est pas non plus le défaut C14 des cartes stub (des
NULL affichés « 0.00 » via `defaultGrounding()`) : `defaultGrounding()` ne
s'applique que si `grounding_data` est absent, et `EditorialBriefCard:103` est
gardé par `!is_stub`. Ici `grounding_data` est présent et le brief n'est pas un
stub — donc ni le défaut, ni le garde.

### Ce défaut précède D2

`node_skip_grounding` (`graph/post_fire_pipeline.py:224`) émet déjà
`score: None` / `verdict: "unavailable"` quand Semantic Scholar est coupé. La
route existe donc depuis le mode dégradé ; elle n'a simplement jamais publié
de brief :

```sh
sqlite3 -readonly data/spore.db \
  "SELECT COUNT(*) FROM briefs WHERE status='complete' AND low_evidence=1;"   -- 0
sqlite3 -readonly data/spore.db \
  "SELECT COUNT(*) FROM briefs WHERE status='complete'
     AND COALESCE(is_stub,0)=0 AND novelty_score IS NULL;"                    -- 0
```

D2 ajoute une **seconde** route au même crash latent (`not all_papers`), il ne
le crée pas.

### Atteignabilité

Les deux routes exigent que le gate S9.2 laisse passer. Or il rejette un
`evidence_base` vide **sauf** si `grounding_degraded`. Il faut donc
simultanément le circuit Semantic Scholar ouvert et un score de panel
publiable. Rare, mais un run de 04:15 peut y arriver.

### Correction, quand elle sera faite (spore-web)

Passer `NoveltyAssessment.score` à `number | null` — le compilateur désignera
alors les quatre sites concernés (`EditorialBriefCard.tsx:103`,
`[locale]/page.tsx:317`, `BriefDetailClient.tsx:1108`,
`BriefsClient.tsx:221-222`, ce dernier faisant de l'arithmétique de tri). Ne
PAS coalescer vers 0 : c'est exactement la fabrication que C14 a retirée des
cartes stub. Masquer la métrique, comme le fait déjà le garde `!is_stub`.

---

## Le cron exécute la branche laissée en checkout

*Relevé au sprint S3, 2026-08-24. Constat, aucune correction.*

Le cron L0 de 04:15 UTC lance le pipeline depuis
`/home/baq/Projects/spore-poc` sans se positionner sur une référence git. Il
exécute donc **le code de la branche en checkout au moment où il se
déclenche**, quelle qu'elle soit.

Conséquences observées :

* la production tourne depuis `feat/s9-3-relative-selection-gate` depuis le
  2026-07-21, pas depuis `master` ;
* toute manipulation git laissant une autre branche en checkout change
  silencieusement le code qui tournera à 04:15 ;
* c'est le mécanisme de l'incident du cherry-pick C15 : le correctif a dû être
  reporté sur `master` alors que la branche en checkout le portait déjà. Les
  deux commits (`447a9b2` sur master, `2d71131` sur la branche) ont un diff
  identique, mais l'historique porte deux fois le même travail.

Aucune correction ici. Ce qu'une correction supposerait : que le cron
s'exécute depuis une référence explicite (`git -C … rev-parse`, un worktree
dédié, ou un déploiement figé), ce qui est un changement de modèle de
déploiement, pas un correctif.
