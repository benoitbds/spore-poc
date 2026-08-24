# S3 / D1b-2 — Les sidecars `outputs/briefs/`

*Rédigé le 2026-08-24. Déplacement de SPR-2026-52AA appliqué ; mécanisme
proposé, non implémenté, en attente d'arbitrage.*

Contexte produit : ces sidecars deviennent un **format documenté et citable**.
Leur cohérence avec le statut en base cesse d'être un détail d'hygiène et
devient une propriété du format.

Contrainte posée : le symlink `spore-web/public/briefs → outputs/briefs` reste.
C'est un garde-fou du projet, aucune proposition ne le contourne.

---

## Fait déclencheur

Ce n'est pas un oubli de garde-fou, c'est un garde-fou contourné par l'aval.
L'historique git des deux fichiers concernés le dit littéralement :

```
454ffa7  feat: reprocess 11 briefs as iter2 — binary publish/reject verdict
9b44d6b  chore: remove exports for rejected briefs SPR-2026-7C1B / -B172   ← suppression correcte
9728913  data(briefs): reconstruct 2 missing JSON files + apply N1.3 prompt (S4.1)   ← remise en place
```

`9b44d6b` a fait ce qu'il fallait. `9728913`, un script de réparation qui a vu
deux fichiers « manquants » par rapport aux lignes en base, les a reconstruits
sans consulter le statut. Les fichiers absents *étaient* l'état correct.

C'est la même famille de défaut que les quatre constructeurs d'URL de S1 et
que le prédicat de publiabilité dupliqué en cinq exemplaires (S3/D1b-1) : la
règle existe à un endroit, et quatre autres endroits l'ignorent.

---

## Q1 — Combien de fichiers correspondent à des briefs non publiables ?

Réconciliation du disque contre `PUBLISHABLE_BRIEF_SQL` :

| Mesure | Valeur |
|---|---|
| fichiers dans `outputs/briefs/` | 176 |
| ids distincts sur disque | 89 |
| briefs en base | 146 (88 publiables, 58 non publiables) |
| **ids sur disque correspondant à un brief NON publiable** | **2** |
| ids sur disque ne correspondant à aucune ligne en base (orphelins) | 0 |
| ids publiables sans fichier | 1 (SPR-2026-52AA, déplacé au D1b-2) |

Les deux : `SPR-2026-7C1B` et `SPR-2026-B172`, `rejected` tous les deux,
`.json` seul (le `.md` supprimé en `9b44d6b` n'a pas été reconstruit).

Vérification :

```sh
cd /home/baq/Projects/spore-poc
ls outputs/briefs/ | sed -E 's/\.(json|md)$//' | sort -u > /tmp/f.txt
sqlite3 -readonly data/spore.db "SELECT id FROM briefs
  WHERE NOT ((status='complete' AND hypothesis_id IS NOT NULL)
             OR COALESCE(is_stub,0)=1);" | sort > /tmp/nonpub.txt
comm -12 /tmp/f.txt /tmp/nonpub.txt
```

Pourquoi seulement 2 sur 58 : les 56 autres rejets sont les rejets de panel
S9.3, écrits par `node_persist_panel_reject`, qui **n'écrit qu'en base**,
jamais sur disque. Les deux survivants sont des briefs publiés puis dépubliés
*après coup* — exactement le cas de 52AA. C'est la dépublication rétroactive
qui laisse des fichiers derrière elle, pas la production courante.

---

## Q2 — Qu'est-ce qui écrit ces fichiers, et à quel moment du graphe ?

Cinq écrivains, un seul garde-fou.

| # | Écrivain | Moment | Consulte le statut ? |
|---|---|---|---|
| 1 | `agents/research_brief_generator.py:save_brief()` | nœud `research_brief_generator` du graphe post-fire | **oui** — retourne `(None, None)` si `meta_review.verdict == 'reject'` |
| 2 | `api/custom_runner.py:215-218` | fin d'un run à la demande, écriture du stub | non (les stubs sont publiables par construction) |
| 3 | `graph/post_fire_pipeline.py:465` | nœud `translation_hook`, patche le `.json` avec les blocs EN | non |
| 4 | `scripts/backfill_vulgarization.py`, `backfill_body_markdown.py`, `backfill_brief_domains.py` | hors graphe, à la main | non |
| 5 | `scripts/reprocess_briefs_iter2.py` | hors graphe, à la main | non |

Le garde-fou de (1) fonctionne — c'est ce qui explique les 56 rejets sans
fichier. Il ne protège que le chemin nominal de création. Les chemins (3), (4)
et (5) rouvrent et réécrivent des fichiers existants sans jamais reposer la
question. `9728913` appartient à cette famille.

Aucun de ces cinq chemins ne s'exécute au moment d'une **dépublication** :
la dépublication est un `UPDATE` SQL manuel, sans contrepartie disque.

---

## Q3 — Que contiennent les sidecars des briefs `rejected` ?

Un brief fini, qui se présente comme publiable.

```
SPR-2026-7C1B | panel.meta_review.verdict = "publish_brief" | consensus 5.63
              | 5 reviews | grounding.key_papers = 0 | vulgarization_fr présente
SPR-2026-B172 | idem, chiffres identiques
```

Clés : `brief_id`, `generated_at`, `domains`, `original_hypothesis`,
`grounding`, `sharpened`, `protocol`, `panel`, `vulgarization_fr`.
**Aucun champ ne porte le statut**, ni la dépublication, ni la date de rejet.

Deux conséquences directes sous la décision « format citable » :

1. Le fichier **affirme le contraire de la base**. Il porte
   `verdict: publish_brief` là où la ligne dit `rejected`. Un lecteur qui cite
   le sidecar cite un verdict rétracté, sans aucun moyen de le savoir depuis
   le fichier.
2. `key_papers = 0` — la même pathologie que 52AA. Ces deux briefs ont été
   rejetés pour la raison qui a valu à 52AA sa dépublication : un panel qui
   valide une hypothèse sans base bibliographique.

Un format citable doit donc porter son propre statut. Aujourd'hui le JSON n'a
pas le champ pour ça.

---

## Contrainte que la proposition doit respecter

`outputs/briefs/` est **suivi par git** et poussé sur le remote GitHub privé.
Supprimer un fichier du disque ne le retire pas de l'historique. Toute
proposition fondée sur `rm` traite la visibilité HTTP, pas la persistance.

Et surtout : une réconciliation **bidirectionnelle** (« aligner le disque sur
la base ») recréerait aujourd'hui les sidecars de 52AA, puisque 52AA est
encore `complete` en base. Ce serait rejouer `9728913`. Le mécanisme doit être
strictement **unidirectionnel** : il retire ce que le prédicat exclut, il ne
recrée jamais rien.

---

## Proposition (à arbitrer, non implémentée)

### Le principe

Un seul module Python détient le prédicat de publiabilité, miroir exact de
`spore-web/src/lib/brief-visibility.ts`. Rien dans `outputs/briefs/` n'existe
sans que ce prédicat l'autorise. Trois pièces, aucune ne recrée de fichier.

### Pièce 1 — `storage/brief_visibility.py`, la définition unique côté Python

`is_publishable(row) -> bool`, littéralement le même prédicat que
`isPublishableBrief()`. Les cinq écrivains l'importent. Un test compare la
chaîne SQL des deux dépôts pour que la divergence casse le CI plutôt que la
production. C'est le geste appliqué au D1b-1 côté front, transposé.

### Pièce 2 — quarantaine au lieu de suppression

`outputs/unpublished/`, **frère** de `outputs/briefs/` et non fils : c'est
`outputs/briefs/` lui-même qui est la cible du symlink, donc un
sous-répertoire y resterait servi (`/briefs/_unpublished/X.json`). Une
dépublication déplace, ne supprime pas : l'hypothèse d'origine est conservée,
et git garde de toute façon l'historique. Le déplacement est l'opération,
pas `rm`.

Le déplacement doit être **committé**, pas seulement appliqué sur le disque.
Les sidecars sont suivis par git ; un `mv` non committé laisse une suppression
non indexée qu'un `git checkout` ou un changement de branche restaure — soit
exactement `9728913` rejoué par une manip git de routine, sur un dépôt dont le
cron de 04:15 tourne depuis la branche laissée en checkout.

Et le déplacement seul ne suffit pas à changer le code HTTP : voir l'entrée
« quarantaine → 400 » de `TECH_DEBT.md`. La réconciliation doit être suivie
d'un redémarrage du serveur, ou tourner avant son démarrage.

### Pièce 3 — un `spore briefs reconcile`, unidirectionnel, à blanc par défaut

Pour chaque fichier de `outputs/briefs/` :
- brief publiable → ne rien faire ;
- brief non publiable → déplacer vers `_unpublished/` ;
- aucune ligne en base → signaler, ne rien faire (décision humaine) ;
- publiable **sans** fichier → **signaler uniquement, ne jamais générer**.

Cette dernière ligne est celle qui empêche de rejouer `9728913`. `--apply`
pour agir, sinon rapport seul. Branché en post-hook du nœud `translation_hook`
et en fin de chaque script de backfill, plus un passage quotidien après le
cron L0 de 04:15 UTC.

### Pièce 4 — le statut dans le format lui-même

Puisque le sidecar devient citable, le JSON gagne un bloc :

```json
"publication": {
  "status": "complete",
  "publishable": true,
  "as_of": "2026-08-24T08:31:00Z",
  "canonical_url": "https://<site>/fr/briefs/SPR-2026-XXXX"
}
```

Un fichier retrouvé hors contexte devient alors auto-descriptif, et le
`.md` peut porter le même en-tête en front-matter. Sans ça, « format citable »
et « statut en base » restent deux vérités séparées, ce qui est précisément le
défaut relevé ici.

### Ce que la proposition ne fait pas

Elle ne touche pas au symlink. Elle ne supprime aucun fichier. Elle ne
régénère aucun contenu. Elle ne réécrit pas l'historique git.

### Ordre d'application suggéré

1. Pièce 1 (définition unique) — sans effet visible, prérequis des autres.
2. Pièce 3 en mode rapport seul — mesurer la dérive réelle sur plusieurs jours.
3. Pièce 2 + `--apply` — traiter 7C1B et B172, les deux seuls cas actuels.
4. Pièce 4 — changement de format, à faire quand la documentation du format
   citable est écrite, pas avant.
