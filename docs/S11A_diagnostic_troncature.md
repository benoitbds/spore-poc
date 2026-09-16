# S11 — Phase A : diagnostic de la troncature des sorties LLM

Run concerné : `run-20260916-041505-2aec40` (16/09/2026, 04:15 UTC).
Lecture seule : aucune modification de code ni de base.
Logs : `/var/log/spore.log` (JSON, du 2026-04-09 au 2026-09-16, 163 868 lignes, pas de rotation).

**Conclusion : l'hypothèse de travail est confirmée, et le problème dépasse le nœud protocole.**
Les deux appels du nœud protocole se sont terminés à `output_tokens = 8000`, exactement le
plafond configuré, et le second était bien un rejeu à l'identique. Sur les 16 rejeux de
parsing enregistrés dans le log, **14 sont des troncatures au plafond** (protocole 8000,
meta-reviewer et reviewers 2000) et 2 seulement de vraies erreurs de format.

---

## A.1 — Appel du nœud protocole

| Élément | Valeur |
|---|---|
| Fichier / fonction | `agents/experimental_protocol.py` → `experimental_protocol_agent()`, l. 165-174 |
| Appel | `llm.json_parse.complete_json(...)` |
| `max_tokens` | **8000, en dur dans le code** |
| `temperature` | 0.4 (rejeu à 0.0) |
| `response_format` | `{"type": "json_object"}` — `json_mode=True` dans `llm/client.py` l. 183-184 |
| `extra_body` | `{"thinking": {"type": "disabled"}}` |
| Modèle envoyé à l'API | `deepseek-v4-flash` (genome `data/l0_genome.yaml`, `agents.experimental_protocol.model`) |
| Repli | `claude-sonnet-5` via `FallbackClient` (hors sujet ici : aucun repli déclenché) |

Deux remarques :

1. **Le commentaire qui justifie le 8000 est faux aujourd'hui.** Il affirme que « DeepSeek's
   valid range is [1, 8192], so anything above 8192 returns HTTP 400 ». C'était vrai des
   modèles V3.2 ; sur V4 non-thinking, le plafond de l'API est 393 216 et le défaut, en
   l'absence du paramètre, est 8K. Le plafond actuel n'est donc pas une contrainte du
   fournisseur mais un choix hérité.
2. **Le genome porte `parameters.max_tokens: 8000` pour ce nœud, mais personne ne le lit.**
   `get_provider_for_agent()` n'extrait que `provider` et `model` ; la valeur en dur gagne
   toujours. Conséquence : une mutation L1 sur `agents.*.parameters.max_tokens` — que
   `l1_strategist` sait proposer — n'a aucun effet et paraîtrait pourtant appliquée.

## A.2 — Helper partagé et critère de rejeu C17b

`llm/json_parse.py` → `complete_json()` :

- **`finish_reason` n'est jamais lu.** Le champ n'existe pas dans `LLMResponse`
  (`llm/client.py` l. 23-31 : `content`, `input_tokens`, `output_tokens`, `model`,
  `provider`, `cache_hit`), et aucun client ne le récupère de la réponse fournisseur.
  C'est la cause première : rien dans le code ne distingue « le modèle a fini » de
  « le modèle a été coupé ».
- **`usage.completion_tokens` est lu** (`llm/client.py` l. 212) et exposé en
  `output_tokens`. Il n'est journalisé qu'à deux endroits : l'événement `api_call` du
  token tracker (niveau `debug`, donc absent des logs de production qui tournent en `INFO`)
  et l'événement `json_parse_retrying` (niveau `warning`). C'est ce dernier qui fournit la
  preuve exploitée ci-dessous.
- **Critère de rejeu C17b** : une `json.JSONDecodeError` levée par `extract_json` après les
  deux niveaux de réparation. Le rejeu est alors **un appel identique** — mêmes `messages`,
  même `max_tokens`, seule la température change (`RETRY_TEMPERATURE = 0.0`). Sur une
  troncature, cela redemande exactement la sortie qui ne tenait pas, et la coupe au même
  endroit. Un seul rejeu, puis l'exception remonte.

## A.3 — Les appels du run du 16/09

Reconstitution depuis le log (le `run_id` n'est pas répété sur chaque ligne post-fire ;
la corrélation se fait par horodatage, entre `pipeline_starting` à 04:15:05 et
`pipeline_complete` à 04:48:45).

| Heure UTC | Événement | `output_tokens` | `max_tokens` | Lecture |
|---|---|---|---|---|
| 04:47:29 | `designing_protocol` (itération 2) | — | 8000 | appel n° 1 |
| 04:48:04 | `json_parse_retrying` | **8000** | 8000 | tronqué ; `Unterminated string ... (char 25425)` |
| 04:48:40 | `protocol_parse_failed` | (non journalisé) | 8000 | rejeu identique, tronqué ; `Unterminated string ... (char 25085)` |
| 04:48:40 | `post_fire_failed` | — | — | `hypothesis_id = SPORE-2026-09-16-8bc4e466` |

**Troncature confirmée.** `output_tokens` égale le plafond au jeton près, et l'erreur est
une chaîne non terminée, signature d'une coupure en plein milieu d'une valeur. Le rejeu a
produit une sortie de longueur comparable (25 085 caractères contre 25 425), coupée elle
aussi : un appel identique sur une sortie trop longue ne peut pas réussir.

**L'hypothèse concernée** est `SPORE-2026-09-16-8bc4e466` — « Terpènes d'Annonaceae comme
modulateurs dualistes CB2/microtubules dans la CIPN induite par les taxanes ». Elle est en
base, `status = curated`, verdict reviewer `a_tester`. L'échec est survenu à l'itération 2
du panel : l'itération 1 avait produit un protocole valide à 04:46:44.

Le même run porte une troncature antérieure, à 04:43:02, sur l'hypothèse précédente
(« Seismic full-waveform inversion → QSM », `output_tokens = 8000`) : là, le rejeu est
passé. Son protocole est donc le fruit d'un second tirage, pas d'une sortie complète du
premier. Ce brief est `SPR-2026-R0BFB`, rejeté ensuite par le gate de sélection — un
protocole issu d'un rattrapage a servi de base à une note de 5,93 contre un seuil à 6,02.
Deux hypothèses sur trois ont donc rencontré la troncature dans ce seul run.

## A.4 — Tous les nœuds LLM (L0 et post-fire)

**Deux réserves de méthode, à lire avant le tableau.**

1. **Il n'existe aucune mesure par appel de `completion_tokens` sur 30 jours.** L'événement
   `api_call` qui la porte est en `debug` ; la production tourne en `INFO`. Seuls les totaux
   par run sont en base (`runs.total_tokens_in/out`). La colonne de distribution est donc
   une **estimation** à partir de la taille des artefacts stockés, à 3,6 caractères par
   token — ratio calibré sur les sorties tronquées connues (8000 tokens pour 25 à 31 k
   caractères). Marge d'erreur : environ ±15 %.
2. **L'échantillon est censuré.** Un appel tronqué ne laisse pas d'artefact en base : soit
   le rejeu a réussi et l'on mesure le second tirage, soit le run a échoué et il n'y a rien.
   Les distributions ci-dessous **sous-estiment** donc le besoin réel, exactement pour la
   raison qui rend le comptage des appels tronqués obligatoire.

Fenêtre : 30 jours (78 briefs, 90 hypothèses). Les cartes reviewer et les meta-reviews sont
mesurées sur les 69 briefs jamais rejoués par S10-B/S10-C, dont le panel est la sortie LLM
d'origine et non une traduction.

| Nœud | `max_tokens` configuré | `completion_tokens` estimés (p50 / p95 / p99 / max) | Appels terminés en `length` |
|---|---|---|---|
| `experimental_protocol` | 8000 (code) | 4 448 / 7 290 / 8 483 / 8 483 | **4 observés** (11/09, 12/09, 16/09 ×2) — 41 % des sorties au-dessus de 60 % du plafond |
| `reviewer_*` (5 personas) | 2000 | 1 063 / 1 805 / 1 968 / 1 997 | **3 observés** (funding_strategist ×2, contrarian, methodologist) — 36 % au-dessus de 60 % |
| `meta_reviewer` | 2000 | 894 / 1 378 / 1 593 / 1 593 | **5 observés** (11/09 ×2, 12/09, 13/09 ×2) — 9 % au-dessus de 60 % |
| `literature_grounding` (analyse) | 8000 | 4 125 / 6 705 / 7 388 / 7 388 | 0 observé — 24 % au-dessus de 60 % |
| `hypothesis_sharpening` | 8000 | 2 086 / 3 911 / 4 079 / 4 079 | 0 — 0 % |
| `critic` (devil / angel) | 8000 | 2 177 / 3 152 / 3 551 / 3 551 | 0 (2 rejeux, mais à 1 178 et 1 550 tokens : vrai JSON invalide) |
| `synthesis` | 4000 | 479 / 643 / 749 / 749 | 0 — 0 % |
| `vulgarization` | 3000 | 1 020 / 1 153 / 1 195 / 1 195 | 0 — 0 % |
| `impact` | 2000 | 621 / 763 / 914 / 914 | 0 — 0 % |
| `reviewer` L0 (auto-feedback) | 1000 | 213 / 273 / 327 / 327 | 0 — 0 % |
| `gate` | 400 | non stocké | 0 |

« Appels terminés en `length` » se lit ici « appels dont `output_tokens` égale exactement le
plafond », seule trace disponible rétroactivement, et uniquement pour les appels ayant en
plus échoué au parsing. **Un appel tronqué dont le JSON reste parsable ne laisse aucune
trace** : le compte réel est donc supérieur.

Trois nœuds dépassent 60 % de leur plafond en p95 et relèvent du point B.4 :
`experimental_protocol` (91 %), `reviewer_*` (90 %), `literature_grounding` (84 %). Le
`meta_reviewer` est à 69 % en p95 mais totalise cinq troncatures observées, la plus forte
fréquence du corpus : son plafond de 2000 est aussi à revoir.

## A.5 — Ligne `briefs` du run avorté

**Il n'y en a pas.** Aucune ligne n'a été créée pour `SPORE-2026-09-16-8bc4e466`.

La raison est structurelle : `experimental_protocol_agent` lève une `ValueError`, qui
remonte à travers `node_experimental_protocol` et fait échouer le graphe. Or les seuls
nœuds qui écrivent une ligne `briefs` sont `persist_grounding_kill`, `persist_panel_reject`
et `node_research_brief`, tous situés en aval ou sur d'autres branches. Un échec entre le
sharpening et le panel ne laisse donc **aucune trace en base**, seulement dans le log.

Les deux lignes créées par le run appartiennent aux deux autres hypothèses `a_tester` :

| id | hypothesis_id | status | grounding | sharpened | protocol | panel |
|---|---|---|---|---|---|---|
| `SPR-2026-EB08` | `SPR-2026-EB08` | complete | oui | oui | oui | oui |
| `SPR-2026-R0BFB` | `SPR-2026-R0BFB` | rejected | oui | oui | oui | oui |

(`R0BFB` est un rejet du gate de sélection : consensus 5,93 pour un seuil à 6,02.)

## A.6 — Pourquoi le digest affiche « sujet : — »

`scripts/daily_pipeline_digest.py`, fonction `_evt_line()` (l. 253-263) : le sujet est le
premier champ présent parmi `brief_id`, `hypothesis_id`, `request_id`, sinon `—`.

L'alerte provient de `protocol_parse_failed`, émis par `agents/experimental_protocol.py` :

```python
logger.error("protocol_parse_failed", error=str(exc))
```

L'événement ne porte **que** `error`. Il n'a ni `hypothesis_id` ni `brief_id` — ce dernier
n'existe d'ailleurs pas encore à ce stade du pipeline. D'où le tiret.

L'information existe pourtant dans le log, à la même seconde : `post_fire_failed` porte
`hypothesis_id`. Mais ce second événement est étiqueté « post-fire abandonné (la cause a
déjà alerté) » et n'est pas rapproché du premier. Le digest a les deux moitiés de
l'information et ne les recolle pas.

---

## Hors incident

### A.7 — `bridge_rate` sur les 30 derniers runs

**Non, il ne vaut pas systématiquement 100 %.** Sur les 30 derniers runs : 8 à 100 %,
22 en dessous, médiane 0,963, minimum 0,60 (run du 24/08, 12 collisions seulement) puis
0,82 (09/09). Le run du 16/09 est à 100 %, ce qui est le cas favorable, pas la règle.

Sur 30 jours, la synthèse a renvoyé `no_bridge_found` **50 fois** (398 depuis l'origine du
log). Le taux se comporte donc comme un vrai indicateur ; il est simplement élevé.

### A.8 — Le compteur « hypothèses » de `/stats`

`scripts/export_stats.py` l. 27 :

```python
total_hypotheses = q("SELECT COUNT(*) as n FROM hypotheses")[0]["n"]
```

La table `hypotheses` ne contient que les hypothèses **curées** : le pipeline L0 n'appelle
`save_hypothesis()` que sur la branche du Curator (`graph/pipeline.py` l. 362). Le compteur
mesure donc les hypothèses conservées et relues, pas les hypothèses formulées.

| Grandeur | Valeur |
|---|---|
| Affiché par `/stats` (« hypotheses ») | **275** |
| En base, tous statuts confondus | 275 (258 `curated`, 17 `human_reviewed`) |
| Hypothèses formulées, cumulées sur 120 runs (`runs.hypotheses_generated`) | **2 527** |
| Formulées sur 30 jours | 1 013, pour 90 conservées |

L'écart est d'un facteur 9. Toutes les hypothèses stockées ont un `auto_feedback_json`,
donc « curées » et « relues » coïncident : le compteur vaut pour l'un comme pour l'autre,
mais pas pour « formulées ». Effet de bord : `fire_rate` est calculé comme
`fire_count / total_hypotheses`, soit une part des curées et non des formulées — il est
donc mécaniquement plus flatteur.

---

## Ce que la phase B devra trancher, au-delà du périmètre annoncé

1. **La mesure manque.** Le tableau A.4 est une estimation parce qu'aucun
   `completion_tokens` par appel n'est journalisé en production. Le critère d'acceptation
   « tableau A.4 réédité après correctif » n'est tenable que si le wrapper journalise
   `finish_reason`, `completion_tokens` et `max_tokens` au niveau `INFO` à chaque appel.
   C'est à faire dans le même geste que le point B.1.
2. **Le prompt du protocole induit lui-même le volume.** Son exemple de sortie est un JSON
   indenté sur 4 espaces, que le modèle recopie — les erreurs « line 386 column 21 » le
   confirment. Aucune borne de liste n'est donnée. Les deux points sont déjà au programme
   (B.3) ; le premier explique une part de la longueur, à indentation égale la sortie
   compacte tient dans environ 30 % de moins.
3. **Le genome ment sur `max_tokens`.** Tant que `parameters.max_tokens` n'est pas lu, le
   fixer dans le genome n'a pas d'effet. Soit le code le lit, soit la clé disparaît du
   genome — la laisser telle quelle expose L1 à des mutations sans effet.
4. **`reviewer_*` et `meta_reviewer` méritent le même traitement que le protocole.** Huit
   des quatorze troncatures observées viennent d'eux. Leur plafond de 2000 est le plus
   serré du pipeline au regard de leur p95.
