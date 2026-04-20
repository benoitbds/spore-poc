# SPORE — Architecture réelle (production srv-nexus)

Snapshot daté du **2026-04-20**. Ce document décrit l'état réel du code et de l'infrastructure, pas la vision du design doc. Toute divergence entre `SPORE_Design_Doc_v1.md` et ce fichier reflète ce qui a été effectivement implémenté vs imaginé.

Host : `srv-nexus` (UTC). Projet racine : `/home/baq/Projects/spore-poc`. Site web : `/home/baq/Projects/spore-web` (repo séparé, Next.js 14.2.35).

---

## 1. Topologie système

### 1.1 Processus persistants (hors systemd, démarrage manuel en tmux / shell)

| Process | PID (snapshot) | Cmd | Port | Durée |
|---|---|---|---|---|
| API SPORE (FastAPI/Uvicorn) | 4091944 | `.venv/bin/python -m api.run` | 8042 | 3j+ |
| Streamlit review | 3224808 | `.venv/bin/python -m streamlit run app.py` (cwd=`review/`) | 8501 | 6j+ |
| Next.js spore-web | 1288410 | `next-server (v14.2.35)` | 3012 | redémarré 19/04 |
| Reverse proxy | conteneur Docker | `nginx-proxy-manager` | 80/81/443 | 3 sem+ |

Aucun restart policy : si un process meurt, il faut le relancer à la main. Pas de systemd unit, pas de supervisor, pas de pm2.

### 1.2 Cron (crontab utilisateur `baq`)

Sortie verbatim de `crontab -l` (lignes SPORE) :

```
0 4 * * 0 /usr/bin/docker system prune -a -f --filter "until=24h" > /dev/null 2>&1
0 6 * * * cd /home/baq/Projects/spore-poc && /home/baq/Projects/spore-poc/.venv/bin/python cli.py autopilot -n 100 --domain all_science >> /var/log/spore.log 2>&1 ; /home/baq/Projects/spore-poc/.venv/bin/python /home/baq/Projects/spore-poc/scripts/notify.py --type l0
0 7 * * * cd /home/baq/Projects/spore-poc && /home/baq/Projects/spore-poc/.venv/bin/python cli.py evolve >> /var/log/spore_l1.log 2>&1 ; /home/baq/Projects/spore-poc/.venv/bin/python /home/baq/Projects/spore-poc/scripts/notify.py --type l1
```

Chemins absolus partout pour le binaire Python (safe cron PATH). Le séparateur `;` (et non `&&`) entre le job et `notify.py` garantit que la notif tourne même si le job crash → email "NO DATA" en cas d'échec.

### 1.3 Stockage & logs

- **SQLite** : `data/spore.db` (gitignored). Seule DB relationnelle du système. Toutes les données (L0, L1, API users, briefs, purchases) y vivent.
- **Genomes & config** : `data/l0_genome.yaml` (muté par L1), `data/constitution.yaml` (humain seul).
- **Outputs disque** : `outputs/briefs/*.{json,md}`, `outputs/digests/*.md`, `outputs/observation_L1-*.json`, `outputs/strategy_L1-*.json`, `outputs/SPORE-YYYY-MM-DD-*.yaml` (hypothèses individuelles).
- **Logs** : `/var/log/spore.log` (L0, ~4 Mo), `/var/log/spore_l1.log` (L1, ~45 Ko). Format JSON-line structlog, avec passages de rendu Rich intercalés (barres de progression, panels). Le parser de `scripts/notify.py` ignore les lignes non-JSON.

---

## 2. Pipeline L0 — génération d'hypothèses

**Trigger** : cron 06:00 UTC, ou `cli.py autopilot -n <N> --domain <X>` manuel.

**Entrypoint** : `cli.py` → `spore_autopilot.py:run_autopilot()` → `graph/pipeline.py:run_pipeline()`.

**Structure LangGraph** (`graph/pipeline.py`) : 9 nœuds, 4 arêtes conditionnelles. État partagé = `PipelineState` (`agents/base.py`).

### 2.1 Séquence des nœuds

| # | Nœud | Fichier | Rôle | APIs externes |
|---|---|---|---|---|
| 1 | **Explorer** | `agents/explorer.py:134` | Génère N collisions depuis `knowledge/domain_map`, enrichit chaque couple avec contexte littérature (3 papiers max par domaine) | Semantic Scholar (`knowledge/semantic_scholar.py`), ArXiv (`knowledge/arxiv_client.py`) |
| 2 | **Constitution Guard** | `agents/constitution_guard.py:95` | Hard-kill si excluded domains / budget journalier > $50 ; soft-warn si `chaos_floor` sous-constitution | Lit `data/constitution.yaml` + SQLite `runs` pour cumul coût |
| 3 | **Gate** | `agents/gate.py:153` | Filtre plausibilité LLM des N collisions → K retenues (target 40-60%, aujourd'hui 30%) | DeepSeek (prompt `gate_v1`) |
| 4 | **Synthesis** | `agents/synthesis.py:231` | Pour chaque collision K : propose bridge + predictions + gap_manifest, ou renvoie `NoBridgeFound` | DeepSeek (prompt `synthesis_v1`) |
| 5 | **Critic** (devil + angel en parallèle) | `agents/critic.py:290` | Devil liste failles, Angel liste points forts, scoring `(novelty, coherence, testability, impact_potential, hallucination_risk)` moyenné | DeepSeek × 2 (prompts `devil_v1`, `angel_v1`) |
| 6 | **Curator** | `agents/curator.py:74` | Rule-based (pas de LLM) : trie par `composite` desc, garde top `curator.top_percent` du genome (actuel 0.15) | — |
| 7 | **Impact** | `agents/impact.py:134` | Traduit chaque hypothèse curée en vulgarisation + industries impactées + score_impact + one_liner | DeepSeek (prompt `impact_v1`) |
| 8 | **Reviewer + Post-Fire** | `graph/pipeline.py:111` + `agents/reviewer.py:139` | Verdict auto-feedback `poubelle`/`intéressant`/`a_tester`. **Post-processing overrides hardcodés** : si `composite < 0.35` → poubelle ; si `hallucination_risk > 0.40` → poubelle. Si verdict = `a_tester` : invoque post-fire pipeline inline. | DeepSeek (prompt `reviewer_v1`) |

### 2.2 Composite score

Calculé dans `critic.py` via `genome.score_weights` (aujourd'hui) :
```
composite = 0.45·coherence + 0.5·testability + 0.2·novelty + 0.15·impact_potential − 0.15·hallucination_risk
```

### 2.3 Persistance

À la fin du pipeline, `pipeline.run_pipeline()` :
1. `save_hypothesis()` (SQLite `hypotheses`, INSERT OR REPLACE)
2. `update_hypothesis_auto_feedback()` APRÈS — le update évite que INSERT OR REPLACE écrase le JSON de verdict
3. `save_metric()` par métrique run (bridge_rate, gate_pass_rate, cost, etc.)
4. `update_run()` pour clôturer la row `runs`

Export YAML optionnel par `cli.py run` (pas par autopilot cron) via `hypothesis.to_yaml_dict()` → `outputs/SPORE-*.yaml`.

### 2.4 Digest

`spore_autopilot.py` génère un markdown long-form à `outputs/digests/digest_YYYY-MM-DD.md` après chaque run autopilot (top 5 hypothèses, gaps récurrents, lien vers review). L'envoi email du digest (`send_digest_email`, SMTP `SPORE_DIGEST_RECIPIENTS`) requiert le flag `--send-email` qui **n'est pas activé dans le cron actuel** → digest écrit sur disque uniquement, pas expédié. Complémentaire à `scripts/notify.py` (résumé scannable post-cron).

---

## 3. Pipeline L1 — évolution du genome

**Trigger** : cron 07:00 UTC, ou `cli.py evolve` manuel (options `--dry-run`, `--observe-only`).

**Entrypoint** : `cli.py:evolve` → `graph/l1_pipeline.py:run_l1_cycle()`.

### 3.1 Séquence (4 phases linéaires)

| Phase | Fichier | Inputs | Outputs | Persistance |
|---|---|---|---|---|
| **Observer** | `agents/l1_observer.py:488` (`generate_observation_report`) | Requêtes SQLite sur `runs` (5 dernières) + `hypotheses` : distributions scores, feedback counts, gaps récurrents, fertilité par domaine, détection de sur-exploitation | `ObservationReport` (modèle Pydantic) | `outputs/observation_L1-YYYYMMDD-HHMMSS.json` |
| **Strategist** | `agents/l1_strategist.py:120` | `ObservationReport` + genome courant + constitution | `StrategyProposal` avec 1-3 `Mutation` (max 3 / cycle, limite constitution) | `outputs/strategy_L1-YYYYMMDD-HHMMSS.json` (contient `old_value` de chaque mutation) |
| **Critic** | `agents/l1_critic.py:172` | `StrategyProposal` + recent_mutations (TODO : actuellement liste vide, pas de contexte conflit inter-cycles) | Sous-ensemble validé | — (in-memory, pas de table SQLite) |
| **Executor** | `agents/l1_executor.py:execute_proposal` (~ligne 247) | Mutations validées | Genome YAML modifié, 1 commit git par mutation, `mutation_applied` / `mutation_blocked_by_lock` log events | `data/l0_genome.yaml` + `git log` |

LLM : DeepSeek pour Strategist et Critic (le Critic fait aussi un `quick_constitution_check` code-level sur `chaos_floor` et `excluded_domains`).

### 3.2 Mutation locks (implémenté 2026-04-19, commits `a40230e` + `2bf3530`)

Section `mutation_locks` dans `data/l0_genome.yaml` :
```yaml
mutation_locks:
- locked_until: '2026-04-22'
  path: agents.synthesis.parameters.temperature
  reason: '...'
- locked_until: '2026-04-22'
  path: score_weights.hallucination_risk
  reason: '...'
```

Helper `_load_active_locks()` dans `agents/l1_executor.py:81-127` : lit les entrées, filtre par `locked_until > now()`, retourne `{path: (datetime, reason)}`. Check dans `execute_proposal()` avant apply.

**Limitation connue** (TODO commenté lignes 265-269 de `l1_executor.py`) : le check matche les `target_path` exacts seulement. Une mutation sur un path parent (ex : `score_weights`) contourne un lock sur un enfant (ex : `score_weights.hallucination_risk`). **Observé en live le 20/04** : L1 a muté `score_weights` (dict complet) alors que `score_weights.hallucination_risk` était locké ; la valeur lockée n'a pas changé par chance.

### 3.3 Rollback

`rollback_mutation()` + `check_for_rollback()` (`l1_executor.py`) : compare metrics `bridge_rate`, `avg_composite_score`, `curation_rate` avant/après, rollback auto si dégradation > 15%. **Dormant aujourd'hui** : appelé nulle part dans le pipeline L1 actuel. Code présent, pas câblé.

---

## 4. Pipeline Post-Fire — validation profonde d'une hypothèse

**Trigger** :
- **Inline** : à la fin de L0, pour chaque hypothèse verdict=`a_tester`, `graph/pipeline.py:reviewer_and_post_fire` (ligne 111) appelle `run_post_fire_pipeline()` directement.
- **Manuel unitaire** : `cli.py post-fire --hypothesis-id <ID>`.
- **Batch manuel** : `scripts/batch_post_fire.sh` itère sur toutes les hypothèses `verdict='a_tester'` sans brief, invoque la CLI par ID, log dans `/tmp/batch_post_fire.log`. Pas de cron.

**Entrypoint LangGraph** : `graph/post_fire_pipeline.py:run_post_fire_pipeline()`.

### 4.1 Séquence

| Agent | Fichier | Rôle | APIs externes |
|---|---|---|---|
| **Literature Grounding** | `agents/literature_grounding.py` | 3 étapes : (1) LLM extrait queries, (2) Semantic Scholar cherche papiers (≥5 citations, sauf type `novelty`), (3) LLM analyse → `novelty_assessment`, `evidence_base`, `counter_evidence`. Kill si `already_proven` ou `counter_evidence.severity='fatal'` | DeepSeek + Semantic Scholar |
| **Hypothesis Sharpening** | `agents/hypothesis_sharpening.py` | Formalise : statement, independent/dependent variables, mécanisme causal, predictions quantitatives falsifiables | DeepSeek |
| **Experimental Protocol** | `agents/experimental_protocol.py` | Protocole 3 phases : quick-start (phase 1 démarrable tout de suite), phase 2 (validation), phase 3 (scale). Budget + durée estimés | DeepSeek |
| **Multi-Reviewer Panel** | `agents/multi_reviewer_panel.py` | 5 personas parallèles : `methodologist`, `domain_expert`, `contrarian`, `industrialist`, `funding_strategist`. Meta-reviewer synthétise, applique logique Python de verdict par seuils. | DeepSeek × 6 |
| **Research Brief Generator** | `agents/research_brief_generator.py` | Compile brief markdown (template long-form) + JSON structuré | — (pas d'LLM, templating) |
| **Vulgarization FR** | `agents/vulgarization.py` | Version grand public française (title_fr, why_it_matters, imagine_that, concretely) ; patchée dans le JSON et la DB | DeepSeek (prompt `vulgarization_fr_v1`) |

### 4.2 Loop de revise_and_resubmit

Routeur conditionnel `grounding_router` à l'entrée ; `meta_reviewer.verdict` au milieu décide :
- `publish_brief` → continue vers brief generator
- `revise_and_resubmit` ET `revision_count < 2` → boucle retour vers `hypothesis_sharpening` avec guidance
- `reject` → END (pas de brief)
- À l'itération 2+, pas de nouveau revise : soit publish (seuil abaissé à 6.0 vs 7.0 à l'iter 1), soit reject.

### 4.3 Sortie

- **Disque** : `outputs/briefs/SPR-YYYY-XXXX.{md,json}` (écrit par `research_brief_generator.save_brief()`)
- **SQLite** : table `briefs`, champs `grounding_data`, `sharpened_data`, `protocol_data`, `panel_data`, `vulgarization_data` (tous JSON), + métadonnées `novelty_score`, `panel_consensus_score`, `panel_verdict`, `brief_md_path`, etc.
- **Briefs rejetés** : DB row persistée avec `status='rejected'`, MAIS `.md`/`.json` **pas écrits sur disque** (logique explicite : évite de leaker du contenu rejeté au site public via symlink).

### 4.4 Propagation vers le site web

```
/home/baq/Projects/spore-web/data/briefs  -> /home/baq/Projects/spore-poc/outputs/briefs  (symlink)
```

Toute écriture dans `outputs/briefs/` est **immédiatement visible** côté spore-web. Par contre, la liste des briefs est **prérendue à la compilation Next** (SSG), donc il faut **rebuild + restart** la prod pour que les nouveaux briefs apparaissent dans l'index. Le mode dev (`next dev`) reloderait automatiquement mais il n'est pas exposé publiquement.

Le fichier `data/stats.json` (global KPIs) est écrit manuellement via `scripts/export_stats.py` → `/home/baq/Projects/spore-web/data/stats.json`. Pas de cron.

---

## 5. API publique (`api/` — FastAPI, port 8042)

Entry : `api/run.py` → `api/main.py`. CORS ouvert à `spore-research.com` + `localhost:3000`. Reverse proxy NPM devant.

### 5.1 Routes

| Méthode | Path | Auth | Rôle |
|---|---|---|---|
| POST | `/api/auth/magic-link` | — | Email magic-link (expire 24 h), envoi via Resend |
| GET | `/api/auth/verify?token=...` | — | Échange token → JWT (HS256, 30 j). Idempotent |
| GET | `/api/auth/me` | JWT | Profil utilisateur |
| GET | `/api/briefs/{id}/full` | JWT | Livre brief complet. Mode launch = tous gratuits. Sinon check purchases/credits |
| POST | `/api/stripe/checkout` | JWT | Crée session Checkout Stripe, pré-insère `purchases` |
| POST | `/api/stripe/webhook` | signature Stripe | `checkout.session.completed` → marque paid, crédite, enqueue custom run |
| POST | `/api/custom/free` | JWT | Mode launch : 1 custom collision gratuite / utilisateur à vie. Valide domaines vs constitution, enqueue via BackgroundTask |
| GET | `/api/custom/{id}/status` | JWT (owner) | Polling progression custom run (404 si pas owner, pas de leak d'existence) |
| POST | `/api/custom/{id}/run` | JWT (owner) | Retry manuel run échoué/payé |
| GET | `/api/account/briefs` | JWT | Briefs débloqués (ownership + free + credits) |
| GET | `/api/account/custom-requests` | JWT | Historique custom requests |
| GET | `/api/account/purchases` | JWT | Historique achats |
| GET | `/api/health` | — | Healthcheck |

### 5.2 Mode launch actif

Tous les briefs sont livrés **gratuits après auth email**. Le code de gestion de crédits existe mais est court-circuité. Les prix Stripe en base (`single`=9€, `pack_5`=29€, `custom`=25€) sont là pour quand on désactivera `launch_free` / `launch_custom_free`.

### 5.3 Custom collision runner (`api/custom_runner.py`)

Exécuté en BackgroundTask (async, hors requête HTTP). Charge row `custom_requests`, passe `status='running'`, lance pipeline L0 avec domain_a + domain_b spécifiés (mode `targeted`) ou domain_b random (mode `surprise`), puis post-fire. Sauvegarde `hypothesis_id` et `brief_id`. Notification Resend au user quand terminé.

---

## 6. Interface review Streamlit (`review/` — port 8501)

Entry : `review/app.py`. Lue en local seulement (pas exposée via NPM).

### 6.1 Pages

| Icône | Label | Fichier | Rôle |
|---|---|---|---|
| 📊 | Dashboard | `views/dashboard.py` | KPIs (briefs/mois, fire_rate, panel_consensus moy), tendances |
| 📄 | Research Briefs | `views/briefs.py` | Liste briefs, vue MD + JSON, badges verdict inline, panel reviewers |
| 🔬 | Hypothèses | `views/hypotheses.py` | Filtre par status, override verdict (poubelle/intéressant/a_tester), lien vers briefs |
| 📰 | Digests | `views/digests.py` | Rendu des `outputs/digests/*.md`, liens hypothesis ID → brief |
| 🧬 | Évolution | `views/evolution.py` | Timeline mutations L1 depuis `git log` sur `data/l0_genome.yaml`, observations |
| ⚙️ | Pilotage | `views/pilotage.py` | Lance runs L0, trigger post-fire manuel, affiche constitution/domain config, crontab |

### 6.2 Boucle de feedback humain

La page **Hypothèses** écrit sur `hypotheses.human_feedback` (+ `human_feedback_comment`). Ce champ est lu par le L1 Observer (analyse `score_feedback_correlation`) à la prochaine exécution L1. C'est le **seul canal** par lequel le jugement humain influence l'évolution du système.

---

## 7. Site public spore-web (repo `/home/baq/Projects/spore-web`, port 3012)

Next.js 14.2.35 en mode SSG (`next build` + `next start`). Sert `spore-research.com` via NPM. Pas dans le repo `spore-poc`.

**Sources de données** :
- `data/briefs/` → **symlink vers `spore-poc/outputs/briefs/`**
- `data/stats.json` → écrit manuellement par `scripts/export_stats.py`

**Limitation SSG** : la liste des briefs est figée au build. Ajout d'un nouveau brief → invisible tant que pas de rebuild + restart (constaté 17-19/04 lors du bug revert, corrigé en relançant `npm run build`).

---

## 8. Couche données

### 8.1 SQLite `data/spore.db` — 9 tables (dump live)

| Table | PK | Rôle | Écrit par | Lu par |
|---|---|---|---|---|
| `hypotheses` | id (TEXT) | Hypothèses L0 avec scores, feedback, impact | `pipeline.py`, backfill scripts, `review/hypotheses.py` | Tout (L1 observer, API, review, post-fire) |
| `briefs` | id (TEXT, `SPR-YYYY-XXXX`) | Briefs post-fire (grounding, sharpening, protocol, panel, vulgarisation, paths) | `research_brief_generator.py`, `vulgarization.py` | API, review, spore-web via JSON files |
| `runs` | id (TEXT) | Historique exécutions L0 (metrics, coût, status) | `pipeline.py` | L1 observer, constitution guard |
| `metrics` | id (INT) | Métriques granulaires par run (name, value) | `pipeline.py` | L1 observer |
| `users` | id (TEXT, `usr_…`) | Utilisateurs API (email, stripe_customer_id, free_brief_used, credits) | `api/auth.py` | Partout API |
| `magic_links` | token (TEXT) | Tokens magic-link one-shot (flip atomique `used`) | `api/auth.py` | `api/auth.py` verify |
| `purchases` | id (TEXT, `pur_…`) | Historique achats Stripe (pending → paid → refunded) | `api/stripe_routes.py` | `api/briefs.py`, `api/account_routes.py` |
| `custom_requests` | id (TEXT, `cus_…`) | Demandes collision custom (pending → running → complete/failed) | `api/custom.py`, `api/custom_runner.py` | `api/custom.py` status, review |
| `semantic_scholar_cache` | — | Cache HTTP de Semantic Scholar (30 j TTL, cf. `ss_cache_hit` dans logs) | `knowledge/semantic_scholar.py` | idem |

Colonnes ajoutées via `ALTER TABLE` successifs (pas de système de migration formel) : `impact_analysis_json`, `auto_feedback_json` sur `hypotheses` ; colonnes vulgarisation sur `briefs`.

### 8.2 Arborescence fichiers clés

```
/home/baq/Projects/spore-poc/
├── cli.py                          # Entry Click: run, autopilot, evolve, post-fire, review, bootstrap, stats, config
├── config.py                       # Pydantic-settings (charge .env)
├── bootstrap.py                    # Calibration vs known discoveries
├── spore_autopilot.py              # Autopilot + digest markdown + SMTP envoi (dormant: flag --send-email non activé)
├── run_impact.py                   # One-off re-run impact sur curated (dormant en prod)
├── logging_config.py               # structlog JSON-line + TokenTracker
├── data/
│   ├── l0_genome.yaml              # Config L0 mutable par L1 (+ section mutation_locks)
│   ├── constitution.yaml           # Règles immuables (humain seul)
│   ├── spore.db                    # SQLite (gitignored)
│   ├── domains/                    # Cache distance sémantique par domaine (.json)
│   └── bootstrap/                  # Known discoveries pour calibration
├── outputs/
│   ├── briefs/                     # Briefs .json + .md (symlink depuis spore-web)
│   ├── digests/                    # digest_YYYY-MM-DD.md (autopilot)
│   ├── observation_L1-*.json       # Rapports L1 observer
│   ├── strategy_L1-*.json          # Propositions L1 strategist (avec old_value)
│   └── SPORE-YYYY-MM-DD-*.yaml     # Hypothèses exportées via cli.py run
├── agents/                         # 21 fichiers (L0: 7, L1: 4, post-fire: 6, base+guard: 2)
├── graph/                          # pipeline.py (L0), l1_pipeline.py, post_fire_pipeline.py
├── knowledge/                      # domain_map, semantic_scholar (avec cache), arxiv_client
├── llm/                            # client.py (FallbackClient wrap primary + fallback)
├── models/                         # Pydantic: collision, domain, hypothesis, gap_manifest, mutation
├── prompts/                        # 17 prompts .txt (1 par agent ou persona reviewer)
├── storage/                        # database.py (aiosqlite, init_database, helpers CRUD)
├── api/                            # 10 fichiers FastAPI + custom_runner
├── review/                         # Streamlit (app.py + 6 views + components)
├── scripts/                        # 11 utilitaires one-off (batch_post_fire, backfill*, enrich*, notify, export_stats, etc.)
└── tests/                          # Tests script-style (pas pytest) : sprint{2,3,4}, calibration{,v2}, literature_grounding, mutation_locks
```

---

## 9. Services externes

| Service | Consommateurs | Auth | Usage |
|---|---|---|---|
| **DeepSeek** | Tous les agents LLM (L0 : gate, synthesis, critic×2, impact, reviewer ; L1 : strategist, critic ; post-fire : grounding, sharpening, protocol, panel×6, vulgarisation) | `DEEPSEEK_API_KEY` | Primary LLM. Model `deepseek-chat`. Coût ~$0.10 / run L0 (100 collisions), ~$0.003 / cycle L1 |
| **Anthropic** | Fallback via `llm/client.py:FallbackClient` | `ANTHROPIC_API_KEY` | Retry avec backoff exponentiel si DeepSeek échoue. Non sollicité en nominal |
| **Semantic Scholar** | Explorer (enrichissement), Literature Grounding (recherche papiers) | `SEMANTIC_SCHOLAR_API_KEY` (optionnel, mais permet 1000 req/5min vs 100) | Avec cache SQLite `semantic_scholar_cache` (30 j TTL). Rate limit erreurs fréquentes dans logs (`ss_api_retryable_error`, `ss_api_exhausted_retries`) |
| **ArXiv** | Explorer | — (public) | Rate limit 3s local. Utilisé pour contexte domaine, pas pour recherche citée |
| **OpenAlex** | — | — | **Non utilisé** (ni dans le code, ni dans `.env`) |
| **Stripe** | `api/stripe_routes.py` | `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` (hard-fail si manquants) | Checkout sessions EUR, webhook `checkout.session.completed` idempotent |
| **Resend** | `api/emails.py` | `RESEND_API_KEY` (hard-fail) | Magic-link, brief-ready, purchase-confirmation (tous en FR) |
| **SMTP Gmail** | `spore_autopilot.py`, `scripts/notify.py` | `SPORE_SMTP_*` (optionnel ; skip silencieux si absent) | Digest markdown (autopilot, pas activé en cron) + synthèses post-cron (notify.py, activé depuis df33ca1) |

---

## 10. Flux de données inter-composants

| Source | Cible | Donnée | Mécanisme | Trigger |
|---|---|---|---|---|
| Cron 06:00 | L0 pipeline | — | `cli.py autopilot` | 06:00 UTC /jour |
| L0 Reviewer (verdict=`a_tester`) | Post-fire pipeline | hypothesis_id | Appel direct inline (`pipeline.py:143`) | Au fil de L0 |
| L0 | SQLite `hypotheses`, `runs`, `metrics` | Hypothèses scored + feedback auto + run metrics | aiosqlite writes | Fin de chaque L0 |
| L0 | `outputs/digests/digest_YYYY-MM-DD.md` | Digest markdown | `spore_autopilot.generate_digest_markdown` | Fin de chaque autopilot |
| L0 | `/var/log/spore.log` | Events JSON-line + stdout Rich | `>> /var/log/spore.log 2>&1` | En continu pendant run |
| Cron 06:00 (post-job) | Email utilisateur | Résumé L0 | `scripts/notify.py --type l0` (SMTP Gmail) | Juste après L0 |
| Cron 07:00 | L1 pipeline | — | `cli.py evolve` | 07:00 UTC /jour |
| L1 Observer | SQLite `runs`, `hypotheses` | Queries lecture | aiosqlite | Phase 1 L1 |
| L1 Observer → Strategist → Critic | `outputs/observation_L1-*.json`, `outputs/strategy_L1-*.json` | Rapports intermédiaires | `json.dump` | À chaque phase |
| L1 Executor | `data/l0_genome.yaml` + `git` | Mutation YAML + 1 commit par mutation | `yaml.dump` + `subprocess git commit` | Phase 4 |
| Cron 07:00 (post-job) | Email utilisateur | Résumé L1 (mutations + blocked) | `scripts/notify.py --type l1` | Juste après L1 |
| Review UI (humain) | SQLite `hypotheses.human_feedback` | Verdict + comment manuel | Streamlit + `update_hypothesis_feedback` | Asynchrone, par l'utilisateur |
| L1 Observer (cycle suivant) | — | Lit `human_feedback` pour calculer `score_feedback_correlation` | SQLite read | 07:00 J+1 |
| Post-fire pipeline | `outputs/briefs/SPR-*.{json,md}` | Briefs fichiers | `research_brief_generator.save_brief` | Fin post-fire |
| Post-fire pipeline | SQLite `briefs` | Brief métadonnées + données JSON | `save_brief_db` | Fin post-fire |
| `outputs/briefs/` | `spore-web/data/briefs/` | Fichiers briefs | **Symlink** (automatique) | Immédiat |
| `scripts/export_stats.py` (manuel) | `spore-web/data/stats.json` | KPIs globaux | Écriture fichier | Manuel |
| `spore-web` build | Index briefs site public | Prérendu SSG | `npm run build` + restart | **Manuel** après nouveaux briefs |
| API custom runner | L0 + post-fire (programmatique) | custom collision → brief | `api/custom_runner.run_custom_request` en BackgroundTask | POST `/api/custom/free` ou webhook Stripe |
| Stripe webhook | SQLite `purchases`, `custom_requests` | Paid + credit + enqueue | `_handle_checkout_completed` | Payment user |

---

## 11. Opérations manuelles vs automatisées

| Opération | Mode | Commande |
|---|---|---|
| L0 autopilot 100 collisions all_science | **Cron 06:00** | — |
| L1 evolve cycle | **Cron 07:00** | — |
| Email synthèse post-L0/L1 | **Cron (après job via `;`)** | — |
| Docker prune hebdo | **Cron dim 04:00** | — |
| Post-fire d'une hypothèse précise | Manuel | `cli.py post-fire --hypothesis-id <ID>` |
| Post-fire batch sur toutes `a_tester` pending | Manuel | `scripts/batch_post_fire.sh` |
| Rebuild + restart spore-web après nouveaux briefs | **Manuel** | `cd ~/Projects/spore-web && npm run build` puis kill + restart `npm start` |
| Export stats vers spore-web | Manuel | `python scripts/export_stats.py` |
| Review / override verdicts | Manuel via Streamlit | Interface sur `:8501` |
| Backfill reviewer sur hypothèses NULL | Manuel | `python scripts/backfill_reviewer.py` |
| Backfill vulgarisation FR | Manuel | `python scripts/backfill_vulgarization.py` |
| Enrich briefs quand SS était down | Manuel | `python scripts/enrich_degraded_briefs.py` |
| Fix runs bloqués > 6h | Manuel (appelé aussi auto au début de chaque L0) | `python scripts/fix_stale_runs.py` |
| Retry custom runs failed | Manuel | `python scripts/retry_failed_customs.py` |
| Restart API / Streamlit | **Manuel** (pas de systemd) | relance à la main |
| Modifier constitution | Humain seul | Édition `data/constitution.yaml` + commit |

---

## 12. Boucles de feedback

**Boucle courte — intra-L0** : Critic debate (devil+angel) → Curator (filtre top 15%) → Reviewer (override si composite/hallucination trop mauvais). Pas d'itération.

**Boucle moyenne — L0 vers L1 (24h)** : L0 écrit `hypotheses` + `metrics` + `runs` en SQLite → L1 Observer les lit le lendemain 07:00, calcule dérives (bridge_rate, feedback distribution, corrélation scores/feedback humain, domaines sur-exploités), propose mutations → L1 Executor mute le genome → L0 J+1 utilise le genome muté.

**Boucle longue — humain dans la boucle (asynchrone)** : Utilisateur review les hypothèses via Streamlit, pose un verdict humain → `human_feedback` en DB → L1 Observer l'utilise à sa prochaine exécution pour calculer `score_feedback_correlation` (Pearson entre composite et feedback sentiment), qui influence ensuite les propositions du Strategist.

**Boucle post-fire** : dans le multi-reviewer panel, `revise_and_resubmit` reboucle vers `hypothesis_sharpening` avec la guidance du meta-reviewer, max 2 itérations avant reject ou publish forcé.

---

## 13. Code dormant / pas câblé en prod

| Composant | État | Commentaire |
|---|---|---|
| `spore_autopilot.py:send_digest_email` | Code présent, jamais appelé en prod | Le cron n'active pas `--send-email`. La fonction marche (SMTP identique à notify.py) mais `SPORE_DIGEST_RECIPIENTS` dans `.env.example` pas configuré |
| `run_impact.py` | Script one-off | Pas dans cron, pas dans CLI commands. Appelable manuellement |
| `agents/l1_executor.py:rollback_mutation` + `check_for_rollback` | Code présent, jamais appelé | Le graph L1 ne compare pas les metrics avant/après mutation ; pas d'auto-rollback |
| `l1_critic.py:recent_mutations` | Toujours passé en liste vide | TODO dans `l1_pipeline.py:110` — pas de table SQLite `mutations` encore |
| `outputs/run_logs/` | N'existe pas | Référencé dans aucun code actif (agent hallucination confirmée) |
| Mode crédits API | Court-circuité | Flag launch_mode ON → tout gratuit |
| ArXiv dans post-fire | Non utilisé | Seulement dans l'enrichissement Explorer L0 |
| OpenAlex | Non utilisé | Aucune référence code, aucune var d'env |
| Anthropic API en primary | Non utilisée | DeepSeek primary ; Anthropic en fallback uniquement (backoff) |

---

## 14. Limitations connues (production, 2026-04-20)

1. **Locks L1 bypass parent-path** (`l1_executor.py:265-269` TODO) — observé en live le 20/04 : L1 a muté `score_weights` alors que `score_weights.hallucination_risk` était locké, sans déclencher le lock.
2. **Pas de table `mutations` en SQLite** — L1 Critic ne peut pas détecter les conflits inter-cycles (il reçoit `recent_mutations=[]` systématiquement).
3. **Processes long-running sans restart policy** — API et Streamlit tournent depuis plusieurs jours sans superviseur. Crash = downtime jusqu'à restart manuel.
4. **Spore-web SSG figé au build** — nouveaux briefs invisibles tant que pas de rebuild + kill + `npm start` manuel.
5. **Reviewer post-processing `hallucination_risk > 0.40`** hardcoded en ligne 229 de `reviewer.py` (pas dans le genome, pas dans la constitution). Le default critic fallback était à 0.50 (passait systématiquement le seuil) → corrigé à 0.30 par commit `89c7526` aujourd'hui, sera effectif au prochain run.
6. **Cascade de pannes SS** — `ss_api_exhausted_retries` 10× aujourd'hui dans le log, fallback silencieux vers evidence_base vide → dégradation du novelty_assessment. Script `enrich_degraded_briefs.py` permet récupération a posteriori.
7. **Pas de migration DB formelle** — le schéma évolue par `ALTER TABLE` dans `init_database()`. Risque divergence entre devs / environnements.
8. **Single host, single point of failure** — tout tourne sur srv-nexus : DB, pipelines, API, web. Pas de redondance.

---

## Annexe A — Commandes utiles (srv-nexus)

```bash
# Observer l'état live
crontab -l                                                      # cron actuel
ps -p 4091944,3224808,1288410                                   # API, Streamlit, Next
ss -ltnp | grep -E "3012|8042|8501"                             # ports SPORE
tail -f /var/log/spore.log                                      # logs L0 en temps réel
tail -f /var/log/spore_l1.log                                   # logs L1

# Interroger la DB
sqlite3 data/spore.db ".tables"
sqlite3 data/spore.db "SELECT id, status, panel_verdict FROM briefs ORDER BY created_at DESC LIMIT 10"

# Lancer ponctuellement
.venv/bin/python cli.py autopilot -n 10 --domain all_science    # mini run test
.venv/bin/python cli.py evolve --dry-run                         # L1 sans modif
.venv/bin/python cli.py post-fire --hypothesis-id <ID>
.venv/bin/python scripts/notify.py --type l0 --dry-run           # preview email
./scripts/batch_post_fire.sh                                     # post-fire en masse

# Rebuild site
cd ~/Projects/spore-web && npm run build
```
