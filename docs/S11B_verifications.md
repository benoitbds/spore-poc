# S11 — Phase B.0 : vérifications préalables

Lecture seule, sauf la quarantaine décrite en fin de document (décision humaine du 16/09).
Complète `docs/S11A_diagnostic_troncature.md`.

## B.0.1 — Pourquoi les troncatures se regroupent entre le 11 et le 16/09

Quatre facteurs examinés, un seul explique le regroupement.

| Facteur | Verdict |
|---|---|
| Date d'introduction de `json_parse_retrying` | **Partiel.** Introduit le 24/08 (`1b345b3`). Avant cette date, une troncature ne laissait aucune trace exploitable. Mais entre le 24/08 et le 10/09 inclus, aucune troncature n'est enregistrée : l'instrumentation n'explique pas le trou. |
| Commits du 01 au 12/09 sur prompts, schémas ou boucle de révision | **Aucun.** Le dépôt ne porte aucun commit dans cette fenêtre. Le voisin le plus proche est S10-A le 14/09 (prompts reviewer en français), postérieur aux troncatures du 10-11/09. |
| Part des protocoles à itération ≥ 2 depuis le 01/08 | **Stable.** Entre 78 % et 100 % chaque semaine (S31 100 %, S35 78 %, S37 89 %, S38 100 %). Ce n'est pas le facteur. |
| `model` renvoyé par l'API | **Non journalisé.** `LLMResponse.model` recopie le nom configuré ; la réponse du fournisseur n'est jamais lue. Un changement côté DeepSeek serait invisible. Corrigé en B.1 (`response_model`, `system_fingerprint`). |

**Ce qui a changé est le volume de contenu, pas le format.** Médianes par semaine, sur les
briefs stockés :

| Semaine | Preuves | Prédictions | Risques | Critères | Équipements | Protocole (car.) |
|---|---|---|---|---|---|---|
| S31 (28/07-03/08) | 8 | 3 | 10 | 10 | 11 | 18 589 |
| S33 (11-17/08) | 9 | 3 | 9 | 10 | 12 | 18 658 |
| S35 (25-31/08) | 10 | 3 | 9 | 8 | 9 | 11 861 |
| S36 (01-07/09) | 8 | 3 | 9 | 8 | 11 | 11 571 |
| **S37 (08-14/09)** | **14** | **5** | **12** | **13** | **13** | **19 187** |
| **S38 (15-16/09)** | **12** | **6** | **12** | **14** | **16** | **20 339** |

Le sharpening rend deux fois plus de prédictions qu'en août et le protocole les décline
phase par phase. La cause est en amont du nœud protocole, sans changement de code : elle
fait l'objet de B.0.7.

## B.0.2 — Les deux niveaux de réparation

`llm/json_parse.py`, fonction `_repair()`, un seul passage conscient des frontières de
chaînes :

1. **Virgule finale** avant `}` ou `]`, retirée. Le passage caractère par caractère évite
   la regex naïve `,\s*}`, qui corromprait un littéral contenant « trailing, } ».
2. **Caractère de contrôle nu** dans une chaîne, échappé (`\n`, `\r`, `\t`, `\b`, `\f`, et
   `\uXXXX` en dessous de 0x20).

En amont, `_strip_fences()` tolère une fence de fermeture absente et `_slice_outermost()`
isole l'objet le plus externe.

**Aucun de ces niveaux ne peut rendre parsable une sortie tronquée.** Rien ne referme une
chaîne, un crochet ou une accolade, rien n'extrait un objet interne complet à la place de
l'objet attendu : quand `_slice_outermost` ne trouve pas le partenaire de l'ouvrant, il rend
le texte tel quel pour que `json.loads` échoue franchement plutôt que de produire un faux
positif. Le module le dit lui-même : « Périmètre : syntaxe uniquement ».

Reste un cas théorique : un objet complet suivi d'un commentaire coupé net. Le contenu
serait parsable alors que `finish_reason = length`. C'est précisément ce que B.1 rejette,
et la raison pour laquelle le contrôle se fait avant tout parsing.

## B.0.3 — Artefacts des briefs publiés

91 briefs publiés contrôlés (`status='complete'`, hors stubs) : `protocol_data`,
`panel_data` (cartes et meta-review) comparés aux champs attendus.

### Listes autorisées à être vides

Une liste vide n'est pas une non-conformité quand le contenu le justifie. Sont autorisées :

- `phases[].required_resources.datasets` — une phase peut n'utiliser aucun jeu de données
  (18 briefs sur 187 dans ce cas) ;
- `phases[].required_resources.equipment` — une phase purement in silico n'a pas
  d'équipement ;
- `phases[].required_resources.software` — une phase purement expérimentale n'a pas de
  logiciel ;
- `meta_review.key_disagreements` — un panel peut être unanime ;
- `meta_review.revision_guidance` — absent quand le verdict est `publish_brief`.

**Règle à porter dans le schéma du sprint suivant** : une phase 1 doit comporter **au moins
un jeu de données ou un logiciel**. Une phase 1 sans ni l'un ni l'autre n'est pas
démarrable, ce que le champ `phase_1_quick_start.can_start_today` prétend pourtant.

Toute autre liste vide, et toute clé absente, restent des non-conformités.

### Non-conformités réelles : 3 sur 91

| Brief | Date | Écart | Nature |
|---|---|---|---|
| `SPR-2026-DB14` | 20/07 | `phases[2].expected_outputs` **absente** | Omission du modèle. Le protocole se termine normalement : ce n'est pas une troncature. |
| `SPR-2026-8FDE` | 22/08 | carte `funding_strategist` de repli | `confidence = 0.0`, `recommendation = "Manual review needed."`, aucune question critique. Le 5,0 de cette carte entre dans le consensus publié (6,02). |
| `SPR-2026-6FEB` | 12/04 | meta-review de repli Python | `llm_verdict = parse_failed`, `final_recommendation` = « Meta-review failed to parse. Python consensus 6.84 at iter 2 ». |

### Exhaustivité de la recherche des replis

Recherche par marqueurs sur **tous** les briefs portant un panel (187 lignes, tous statuts),
et non sur le seul schéma :

- carte avec `confidence = 0.0` ou contenant « Manual review needed » ;
- meta-review avec `llm_verdict = parse_failed` ;
- toute occurrence de « failed to parse », « parse_failed », « parse failure ».

Résultat : **deux briefs publiés** concernés, 8FDE et 6FEB — les mêmes que par le schéma.
Onze briefs `rejected` portent aussi une carte de repli ; ils ne sont pas publiés et sortent
du périmètre.

**Piège écarté** : `meta_review.verdict_override_reason` contenant « Python threshold
override » apparaît sur 23 briefs publiés et 40 rejetés. **Ce n'est pas un repli** : c'est le
mécanisme de décision nominal, où les seuils Python arrêtent le verdict binaire à
l'itération 2. Le confondre avec une panne aurait mis en quarantaine un quart du corpus.

## B.0.4 — Liste de rejeu

Deux événements `post_fire_failed` dans tout le log (09/04 → 16/09) :

| Date | `hypothesis_id` | Message | Classement | Abouti depuis ? |
|---|---|---|---|---|
| 11/09 04:49 | `SPORE-2026-09-11-28b15004` | `Unterminated string ... (char 30941)` | **troncature** | Non : `curated`, verdict `a_tester`, aucun brief en base |
| 16/09 04:48 | `SPORE-2026-09-16-8bc4e466` | `Unterminated string ... (char 25085)` | **troncature** | Non : idem |

Aucun cas de JSON invalide, d'erreur API ni d'autre nature. Aucun brief ne porte un
`hypothesis_id` en `SPORE-…` : un post-fire qui échoue avant le panel ne laisse aucune ligne
(voir A.5), ce que B.5 corrige.

## B.0.5 — S10-C

| Chemin du rejeu S10-C | Passe par `complete_json` ? | Plafond modifié en B.3 ? |
|---|---|---|
| `node_normalize_panel_language` → `translate_panel_to_fr` | Non — `client.complete` en direct | Non |
| `node_vulgarization` → `vulgarization_agent` | **Oui** | Non (vulgarisation hors B.3) |
| `node_translation_hook` → traducteurs | Non — `client.complete` en direct | Non |
| `node_validate_brief` | Aucun appel LLM | — |

S10-C ne touche aucun nœud dont le plafond change, mais passe par `complete_json` via la
vulgarisation : après B.1, une vulgarisation tronquée lèvera au lieu d'être parsée, et le
rejeu restaurera le brief. **S10-C reste donc en pause**, et ne reprendra qu'après B.0.6.

---

## Quarantaine du 16/09 — SPR-2026-8FDE et SPR-2026-6FEB

Décision humaine, appliquée selon la procédure d'août (`cda8fb8`, `docs/S3_D1b2_sidecars.md`
pièce 2) : déplacement, jamais suppression.

| Brief | Statut avant | Statut après | Consensus au moment de la quarantaine | Motif |
|---|---|---|---|---|
| `SPR-2026-8FDE` | `complete` | `failed_panel` | 6,02 | Carte `funding_strategist` de repli, entrée dans le consensus |
| `SPR-2026-6FEB` | `complete` | `failed_meta_review` | 6,84 | Meta-review de repli Python après échec de parsing |

Gestes appliqués :

1. Sauvegarde préalable vérifiée : `data/backups/s11-20260916T175438Z` (format 2, 4 fichiers,
   10 blobs), qui permet une restauration ligne et fichiers par
   `scripts/replay_blocked_briefs.py --restore-brief … --from …`.
2. `status` porté à `failed_panel` / `failed_meta_review`. Le prédicat de publication du
   front (`status = 'complete' OR is_stub`) les exclut immédiatement.
3. `.md` et `.json` déplacés de `outputs/briefs/` vers `outputs/unpublished/`. Les fichiers
   de 6FEB étaient suivis par git : `git mv` et commit, sans quoi un `git checkout`
   restaurerait les sidecars sur une branche que le cron de 04:15 exécute.
4. `kill_reason` n'est **pas** renseigné : ce n'est pas un rejet scientifique. La colonne
   `failure_reason` arrive en B.5 et recevra le motif.

**À faire par Baq** : redémarrer le serveur Next. `public/briefs` est un lien symbolique vers
`outputs/briefs/`, et Next indexe l'arborescence au démarrage : jusqu'au redémarrage, les
deux sidecars répondent **400** au lieu de 404 (voir `docs/TECH_DEBT.md`, « Sortir un fichier
de public/ sans redémarrer renvoie 400 »).

**Deux conséquences connues, corrigées en B.5** :

- `scripts/export_stats.py` compte les briefs par `status != 'rejected'` : les lignes
  `failed_*` y entrent encore. Le fichier `data/stats.json` n'est pas régénéré par le cron ;
  la correction (point B.5.4) arrivera avant toute régénération.
- Les deux lignes gardent leur chemin de fichier d'origine, désormais périmé, comme les
  quarantaines d'août. B.6 réécrira ces chemins en republiant, ou les laissera tels quels en
  cas de rejet.

**Suite prévue (B.6)** : rejouer les cinq reviewers et la meta-review à partir du sharpening
et du protocole stockés, sans les régénérer ; appliquer le gate en vigueur ; republier si le
brief passe, `rejected` sinon ; une seule tentative.

## SPR-2026-DB14 — affichage vérifié, aucun rejeu

`phases[2].expected_outputs` est absente. Le site ne casse pas :

- `src/app/[locale]/briefs/BriefsClient.tsx` l. 98 lit `...(ph.expected_outputs ?? [])` — le
  champ absent donne une liste vide ;
- `BriefDetailClient.tsx` n'affiche pas ce champ du tout ;
- les deux locales partagent ces composants, donc FR et EN se comportent à l'identique ;
- `src/lib/types.ts` le déclare requis, mais c'est un type TypeScript, sans effet à
  l'exécution.

Le brief reste publié. La validation de schéma en sortie de nœud ouvre le sprint suivant.

---

# Réalisation B.1 à B.7 — 16/09/2026

Six commits sur master, `300d73b` → `f344455`, poussés sur le backup privé.
Le détail de chaque décision est dans les messages de commit ; ce qui suit ne
retient que ce qui change la lecture des documents précédents.

## Ce que la vérification en direct a corrigé

| Affirmation | Vérification | Résultat |
|---|---|---|
| « DeepSeek refuse `max_tokens` au-delà de 8192 » (commentaire dans `agents/experimental_protocol.py`) | Trois appels réels sur `deepseek-v4-flash` | **Faux.** 16000, 32000 et 64000 sont acceptés, `finish_reason=stop`. Héritage de V3.2. Le nœud protocole a tourné à 8000 pour rien. |
| `model` renvoyé par l'API, jamais journalisé (B.0.1) | Mesuré à chaque appel depuis B.1 | L'API sert **`deepseek-flash`**, nom différent du `deepseek-v4-flash` configuré. `system_fingerprint` : `aeb56401…`. Les deux sont désormais en base. |
| Le protocole tient dans le plafond relevé | Essai réel après B.3 et B.4, hypothèse synthétique | Protocole en 3 phases complet : 4 054 jetons de sortie, `finish_reason=stop`, parsé sans réparation. Marge de 4x sous le plafond. |

## Incident — un test a écrasé un brief publié

`tests/test_sprint4_register_brief.py` réenregistre par conception
`SPR-2026-7626` dans la base **réelle**. Exécuté à 18:08 lors du passage de la
suite, il a écrasé 7 colonnes du brief d'avril : `created_at` remis à
aujourd'hui, `body_markdown`, `vulgarization_data`, `panel_data_en` et
`vulgarization_data_en` mis à NULL, `revision_count` remis à 0,
`sharpened_data` remplacé.

- **Détecté** en vérifiant la fenêtre de sélection S9.3, où 7626 était remonté
  en tête avec un `created_at` du jour.
- **Restauré** depuis `data/backups/s11-20260916T175438Z` : les 32 colonnes sont
  de nouveau identiques à la sauvegarde, le `.md` sur disque n'avait pas bougé.
- **Cause aggravante** : depuis B.1, les doubles de test passent par la vraie
  couche client, donc les tests écrivaient aussi leur mesure dans `llm_calls`
  de production — 378 lignes factices, supprimées.
- **Garde-fou** : `tests/__init__.py` redirige `SPORE_DB_PATH` et
  `SPORE_OUTPUT_DIR` vers un dossier temporaire. Échappatoire explicite :
  `SPORE_TEST_USE_REAL_DB=1`.
- **À ne pas lancer sans intention** : `tests/test_calibration.py`,
  `test_calibration_v2.py` et `test_literature_grounding.py` appellent l'API
  réelle — ce sont des scripts de calibration, pas des tests.

## Fenêtre de sélection S9.3 — contrôle

Les deux briefs mis en quarantaine le 16/09 **ne sont pas** dans la fenêtre des
20 derniers scores : ils datent d'avril et d'août, la fenêtre remonte au 10/09.
Aucune contamination du seuil. `get_recent_consensus_scores` ne filtre que sur
`panel_consensus_score > 0` : les lignes `failed_*` écrites par B.5 portent un
score NULL et en sortent d'elles-mêmes.

## Écarts assumés par rapport à la consigne

1. **B.5.1** — la ligne d'échec est écrite dans le décorateur de nœud, pas à
   l'endroit où `post_fire_failed` est émis. À cet endroit-là, dans
   `graph/pipeline.py`, l'exception a déjà fait perdre l'état du graphe : les
   blobs déjà produits seraient définitivement perdus. `post_fire_failed`
   continue d'être émis au même endroit qu'avant.
2. **B.3** — `data/l0_genome.yaml` porte une quatrième modification non
   demandée : `synthesis.max_tokens` 3000 → 4000. Le code appelait 4000 en
   ignorant le genome ; aligner le genome sur la production évite un
   avertissement de bornage permanent, et rend visible que toute mutation L1
   de ce paramètre était sans effet.
3. **B.5.4** — `scripts/backfill_reviewer.py` filtre la table `hypotheses`, pas
   `briefs` : son `status != 'rejected'` n'est pas concerné, il n'a pas été
   touché.

## À faire hors de ce dépôt

- **Baq** : installer `deploy/logrotate/spore` (commande en tête du fichier).
- **Baq** : redémarrer le serveur Next (quarantaine du 16/09, voir plus haut).
- **spore-web** : le commentaire de `src/lib/brief-visibility.ts` énumère les
  statuts écrits par Python — « quatre statuts et pas d'autres » — et demande
  explicitement qu'on l'y ajoute. Les statuts `failed_*` manquent. Le prédicat
  lui-même est correct (liste d'admission), seul le commentaire est périmé.
