# S4-A — Le devil fabrique des scores : diagnostic

*2026-08-24. Phase 1, lecture seule. Aucune correction appliquée, aucune
écriture SQLite. Phase 2 non engagée.*

Sur échec de parsing, `run_devil_advocate` (`agents/critic.py:85-99`) retourne
`verdict: "flawed"` et **cinq scores inventés** — `novelty 0.5, coherence 0.5,
testability 0.5, impact_potential 0.5, hallucination_risk 0.3`. Ces scores
alimentent `aggregate_scores` → `hypothesis.scores.composite` → le classement
du curator. `run_angel_advocate` (`:166-180`) fait de même avec
`verdict: "moderate_support"`.

Le mécanisme est structurel : le devil produit des scores **bas par fonction**,
donc un défaut neutre à 0,5 le surclasse mécaniquement. Plus le critique fait
bien son travail, plus son échec avantage l'hypothèse.

---

## 1a — Étendue

### Méthode

Le log **ne porte aucun `hypothesis_id`** sur ces événements. Clés
disponibles : `timestamp`, `event`, `level`, `error`. Rien d'autre — ni
payload, ni identifiant.

Le lien a donc été reconstruit par **proximité temporelle** : le premier
événement `critique_complete` suivant l'échec dans le flux du log. La
corrélation est corroborée par la signature attendue — `devil_verdict:
"flawed"` et un composite dans l'étroite bande 0,49–0,54, exactement ce que
produit la fabrication. Onze des douze la portent. La méthode reste indirecte ;
c'est le constat 1c qui la rend nécessaire.

### Les 12 occurrences

| Date | Agent | Hypothèse (corrélée) | Composite | Devenir |
|---|---|---|---|---|
| 2026-04-29 | angel | *non persistée* | 0,16 | jamais curatée |
| 2026-05-04 | devil | SPORE-2026-05-04-e1c3b07c | 0,526 | `intéressant` |
| 2026-07-31 | devil | SPORE-2026-07-31-41bcd7d0 | 0,487 | 🔥 → rejeté par le panel |
| 2026-08-02 | devil | SPORE-2026-08-02-3aa8cd01 | 0,500 | 🔥 → rejeté par le panel |
| 2026-08-03 | devil | SPORE-2026-08-03-f3b9cd6a | 0,500 | `poubelle` |
| 2026-08-17 | devil | SPORE-2026-08-17-03d89017 | 0,540 | 🔥 → **SPR-2026-CD79, publié** |
| 2026-08-17 | devil | SPORE-2026-08-17-22c50d82 | 0,488 | 🔥 → rejeté par le panel |
| 2026-08-18 | devil | SPORE-2026-08-18-7ca54ec5 | 0,542 | 🔥 → rejeté par le panel |
| 2026-08-18 | devil | SPORE-2026-08-18-35511473 | 0,507 | 🔥 → rejeté par le panel |
| 2026-08-20 | devil | SPORE-2026-08-20-1026914b | 0,495 | 🔥 → rejeté par le gate S9.3 |
| 2026-08-21 | devil | SPORE-2026-08-21-b7371f1b | 0,505 | `poubelle` |
| 2026-08-24 | devil | SPORE-2026-08-24-8667e8a3 | 0,532 | 🔥 → rejeté par le panel |

La dernière date d'**aujourd'hui**, run de 04:37. Le défaut est actif.

### La chaîne

```
12 échecs de parsing
 → 11 hypothèses persistées et curatées   (1 jamais persistée)
 →  8 notées a_tester par le reviewer
 →  8 entrées en pipeline post-fire        (log : fire_hypothesis_detected)
 →  7 arrêtées en aval : 6 par le panel, 1 par le gate de sélection S9.3
 →  1 brief publié : SPR-2026-CD79, servi 200 en production
```

### Ce que ça dit

**Le classement est bien perverti.** Composite réel sur 206 hypothèses :
min 0,265 / moyenne 0,430 / max 0,551. La fabrication produit ~0,50, soit le
**79ᵉ percentile**. Un échec de parsing ne neutralise pas l'hypothèse, il la
promeut dans le quintile supérieur. Les onze composites observés
(0,487–0,542) confirment la mesure sur données réelles.

**Les gardes en aval ont tenu 7 fois sur 8.** Le panel et le gate S9.3
travaillent sur des entrées indépendantes du composite ; ils ne rattrapent pas
le défaut, mais ils l'absorbent souvent.

**Un brief est passé.** SPR-2026-CD79 : consensus 6,48, 12 articles de preuve,
nouveauté 0,80. Ce n'est pas un faux brief — sa littérature est réelle et le
panel l'a validé sur pièces. C'est un brief dont **la critique adverse n'a
jamais eu lieu** : le devil qui devait le contester n'a pas été lu. Aucune
action proposée ici ; le rejouer poserait la même question que 52AA et 8FDE —
reconstruire rétroactivement une décision.

---

## 1b — Le même défaut ailleurs à L0

| Agent | Sur échec de parsing | Influence une décision ? |
|---|---|---|
| `gate` | replis en cascade (regex, puis texte), et en dernier ressort `plausible = False` + « defaulting to REJECT » | **non** — fail-closed |
| `explorer` | *aucun appel LLM* | — |
| `synthesis` | `NoBridgeFound(reason="Failed to parse…")` | **non** — inerte, aucun score produit |
| **`critic_devil`** | **5 scores à 0,5/0,3 + `verdict: "flawed"`** | **OUI — composite, classement curator** |
| **`critic_angel`** | **5 scores à 0,5/0,3 + `verdict: "moderate_support"`** | **OUI — composite, classement curator** |
| `curator` | *aucun appel LLM* — classe sur `composite` | — (consommateur du défaut) |
| `impact` | `return None` | **non** — inerte |
| `reviewer` | fail-closed : `verdict="poubelle"`, scores à 0,0, `override_reason` | **non** — fail-closed |

**Deux agents sur huit fabriquent une valeur qui décide, et ce sont les deux
déjà nommés.** Les six autres lèvent, rendent une valeur inerte, ou
fail-closent. Le devil et l'angel sont les seuls écarts.

### Un cas adjacent, hors grille

`reviewer.py:333` :

```python
composite = hypothesis.scores.composite if hypothesis.scores and \
            hypothesis.scores.composite is not None else 0.5
```

Ce n'est pas un repli de parsing, mais c'est un `0.5` par défaut qui alimente
`evaluate_override(composite, …)`, donc un **seuil** — l'override mécanique
`composite < 0.35 → poubelle`. Une hypothèse sans scores échappe à cet
override en héritant d'un composite au-dessus du seuil. Signalé au titre de
« tout défaut qui alimente un score, un classement ou un seuil ».

---

## 1c — Traçabilité : `critic_debate_log`

**Le champ n'a jamais été alimenté. Ce n'est pas une perte de données, c'est un
câblage jamais fait.**

La chaîne, telle qu'elle est dans le code :

1. `critic.py:261` construit bien un `debate_log` complet — verdicts, scores du
   devil, scores de l'angel, scores finaux.
2. `critic.py:368` le range dans `state["debate_logs"]`.
3. `state["debate_logs"]` est initialisé à `[]` dans `graph/pipeline.py:326` et
   `api/custom_runner.py:118`, et **n'est relu nulle part**. Aucun consommateur
   dans tout le dépôt.
4. `hypothesis.critic_debate_log` n'est **assigné nulle part** — aucune
   occurrence hors de la définition du modèle et de la persistance.
5. `save_hypothesis` (`storage/database.py:409`) écrit donc fidèlement `None`
   en colonne, pour les 207 lignes.

```sh
sqlite3 -readonly data/spore.db \
  "SELECT COUNT(*) total, SUM(critic_debate_log IS NOT NULL AND critic_debate_log<>'') rempli
     FROM hypotheses;"
-- 207 | 0
```

Le design doc (`:292`) le montre comme `critic_debate_log: "debate_0042.json"`,
et la description du champ dit « Reference to debate log file ». L'intention
était donc un **chemin de fichier**. Aucun fichier de ce type n'est écrit non
plus.

`data/constitution.yaml:11` pose : `transparency: "all hypotheses include full
source tracing"`. Le débat adverse est la trace du contradictoire ; il n'est
conservé nulle part.

**Conséquence directe sur S4-A** : la sévérité par pôle — que coûte
exactement la perte du devil seul, de l'angel seul — **n'est pas mesurable sur
l'historique**, précisément parce que ce champ n'a jamais été écrit. C'est ce
qui empêche de trancher la phase 2 sur données plutôt que sur raisonnement.

---

## 1d — Le parseur

### Les critiques peuvent-ils utiliser `llm/json_parse.py` tel quel ?

**Oui, sans adaptation.** Ils appellent `get_llm_client("critic_devil")` puis
`client.complete(...)`, exactement le chemin que `complete_json` enveloppe. Le
génome les route vers `deepseek-v4-flash`, le même modèle que les cinq agents
post-fire. Leur extracteur actuel (`critic.py:79-84`) est la sixième et
septième copie de la fonction que C17b a supprimée ailleurs — octet pour
octet.

Le mode JSON de C17b s'applique donc aussi : même client, même provider. Le
prérequis DeepSeek — le mot « json » dans le prompt — est satisfait
(`devil_advocate.txt` et `angel_advocate.txt` le contiennent).

### Mais le parseur ne réparerait qu'un échec sur douze

C'est le résultat qui compte, et il contredit l'attente naturelle :

| Classe d'erreur | n | Offset | Réparable par `_repair` ? |
|---|---|---|---|
| `Unterminated string` | 5 | 8490 – 10162 | **non** — troncature, il faudrait inventer du contenu |
| `Expecting ',' delimiter` | 6 | 1062 – 6608 | **non** — guillemet non échappé dans une chaîne |
| `Invalid control character` | 1 | 1878 | **oui** |

Vérifié en exécutant `extract_json` sur les trois classes reconstruites : seul
le caractère de contrôle passe.

### Les cinq troncatures sont un problème de plafond, pas de format

`max_tokens=2000` pour le devil (`critic.py:62`). À ~4,2 caractères par token,
2000 tokens ≈ 8400 caractères — **exactement la bande où tombent les cinq
`Unterminated string`** (8490 à 10162).

Mesure sur trois appels réels du devil, sur des hypothèses réelles de la base :

| Hypothèse | output_tokens / 2000 |
|---|---|
| SPORE-2026-08-24-8667e8a3 | 1593 (80 %) |
| SPORE-2026-08-24-3e566e4e | 1199 (60 %) |
| SPORE-2026-08-24-c549ecc4 | 1260 (63 %) |

La marge nominale est de 20 à 40 %. Ce n'est donc pas systémique — mais une
critique un peu verbeuse la franchit, et **5 des 12 échecs (42 %) l'ont
franchie**.

Précédent dans le dépôt : `hypothesis_sharpening.py:92-95` et
`experimental_protocol.py:129-132` portent tous deux un commentaire sur des
troncatures « mid-JSON » et ont été relevés à 8000 pour cette raison. **Le
devil est resté à 2000.**

### Ce qui traiterait quoi

| Levier | Traite | Ne traite pas |
|---|---|---|
| Mode JSON natif (C17b) | vraisemblablement les 6 `delimiter` — il contraint la syntaxe à la génération | les 5 troncatures : la doc DeepSeek dit explicitement « Set `max_tokens` appropriately to prevent truncation mid-JSON » |
| Réparation (`_repair`) | 1 sur 12 | 11 sur 12 |
| **Retry à température basse** (niveau 3 de C17b) | **potentiellement les 12** — c'est un rééchantillonnage, pas une réparation | un modèle qui échoue de façon déterministe |
| **Relever `max_tokens`** | la cause dominante des 5 troncatures | les 6 `delimiter` |

Le levier le plus efficace n'est ni le mode JSON ni la réparation : c'est le
**retry**, que les critiques L0 n'ont pas du tout aujourd'hui.

---

## Ce que le diagnostic change à la phase 2

*Position à valider. Aucun code écrit.*

### Le piège : « aucun score » ne suffit pas

`aggregate_scores` (`critic.py:196-199`) :

```python
def avg(key: str) -> float:
    d = devil.get(key, 0.5)
    a = angel.get(key, 0.5)
    return (d + a) / 2
```

Si le devil ne rend **aucun** score, `avg` **resubstitue 0,5**. La fabrication
revient par l'agrégation, à l'identique. Retirer le repli du critique seul
serait un **no-op** : `aggregate_scores` doit changer dans le même commit.

### L'analogie avec `compute_consensus_score` ne tient pas tout à fait

Exclure un reviewer à confidence nulle retire **1 avis sur 5** ; les quatre
autres restent représentatifs. Exclure le devil retire **un des deux pôles
contradictoires**, et laisse l'avocat seul. Or le raisonnement même qui fonde
S4-A — « le devil produit des scores bas par fonction » — implique qu'une
moyenne angel-seul est **plus généreuse** que 0,5, pas moins. « Absence + que
l'agrégation se débrouille » risque donc d'être pire que le défaut qu'il
remplace, si la règle d'agrégation est « prends le survivant ».

### Trois options, à arbitrer

1. **Fail-closed** — l'hypothèse est abandonnée. Simple, cohérent avec D2 et
   avec le reviewer, qui fail-close déjà. Jette du travail pour une virgule.
   Coût réel mesuré : 12 hypothèses sur ~5 mois.
2. **Rééchantillonner** — brancher `complete_json`, dont le niveau 3 rejoue
   l'appel à température 0. C'est le levier qui adresse le plus de cas (les 12,
   en principe), et il existe déjà. Ne coûte qu'un appel supplémentaire, rare.
3. **Absence + règle d'agrégation explicite** — le pôle manquant ne vote pas,
   et `aggregate_scores` applique une règle assumée : pas « la moyenne du
   survivant », mais par exemple ne rendre aucun composite, ce qui renvoie au
   comportement du curator face à `composite is None`.

Ces options ne s'excluent pas : (2) réduit la fréquence, (1) ou (3) traite le
résidu. Et **relever `max_tokens`** est un quatrième levier indépendant des
trois, qui adresse la cause dominante.

Aucune n'est engagée. Le choix appartient à l'humain.
