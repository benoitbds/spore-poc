# SPORE — Design Document v1.0
## Système de Production d'Opportunités de Recherche par Exploration

**Auteur** : Benoit Baqué de Sariac (Bac)  
**Date** : 4 avril 2026  
**Statut** : Vision & Architecture — Pré-implémentation  
**Version** : 1.0

---

## 1. Vision

SPORE est un organisme cognitif artificiel conçu pour générer des hypothèses scientifiques disruptives par collision aléatoire de domaines éloignés, évaluation critique automatisée, et auto-évolution récursive.

SPORE n'est pas un outil. C'est un **système autopoïétique** (Maturana & Varela) : il se maintient, s'observe, et se reconfigure pour améliorer sa propre capacité d'exploration.

### 1.1 Postulat fondateur

Les découvertes scientifiques les plus disruptives naissent souvent à l'intersection de domaines éloignés. Les LLM actuels excellent dans la recombinaison de connaissances existantes et la détection d'analogies structurelles. En combinant un moteur d'aléatoire intelligent avec une évaluation critique multi-couche et un feedback humain expert, il est possible de générer des hypothèses scientifiques originales, testables, et potentiellement transformatives.

### 1.2 Ce que SPORE n'est PAS

- SPORE ne fait pas d'expériences. Il ne remplace pas le labo.
- SPORE ne "découvre" pas. Il génère des hypothèses que des humains experts évaluent et testent.
- SPORE n'est pas un moteur de recherche. Il crée des connexions qui n'existent pas encore dans la littérature.

### 1.3 Métaphore directrice

SPORE fonctionne comme un écosystème biologique. Les hypothèses sont des spores : disséminées massivement, la plupart meurent, quelques-unes germent dans un terreau fertile (un labo, un chercheur curieux), et celles qui prennent racine peuvent transformer l'écosystème.

---

## 2. Architecture globale — Le modèle récursif à N niveaux

### 2.1 Principe : hiérarchie récursive de contrôle

Inspiré des niveaux logiques d'apprentissage de Bateson et de l'organisation du vivant (ADN → épigénétique → sélection naturelle → reproduction sexuée), SPORE s'organise en **Teams empilées**, chaque Team ayant pour mandat d'optimiser la Team du niveau inférieur.

```
┌─────────────────────────────────────────────────────┐
│  L_HUMAN — Le Miroir                                │
│  L'humain expert. Dernier niveau de récursion.      │
│  Tranche sur le sens, la direction, les objectifs.  │
├─────────────────────────────────────────────────────┤
│  L3 — Les Philosophes         (mensuel)             │
│  Questionne les objectifs et la définition même     │
│  de "découverte prometteuse". Redéfinit la          │
│  fonction de fitness du système.                    │
├─────────────────────────────────────────────────────┤
│  L2 — Les Architectes         (hebdomadaire)        │
│  Évalue si L1 fait les bonnes mutations.            │
│  Peut restructurer L1, changer ses heuristiques.    │
├─────────────────────────────────────────────────────┤
│  L1 — Les Entraîneurs         (quotidien)           │
│  Observe les métriques et gap manifests de L0.      │
│  Propose et applique des mutations sur L0.          │
├─────────────────────────────────────────────────────┤
│  L0 — Les Chercheurs          (horaire)             │
│  Explore, synthétise, critique, curate.             │
│  Produit des hypothèses + gap manifests.            │
└─────────────────────────────────────────────────────┘
```

### 2.2 Principe unificateur : chaque niveau est un réducteur de gaps

Le GapEngine (section 5) est le système nerveux de SPORE. Chaque niveau existe pour combler les gaps que le niveau inférieur ne peut pas combler seul :

- L0 explore et **révèle** ses gaps
- L1 **réduit** les gaps opérationnels de L0
- L2 **réduit** les gaps stratégiques de L1
- L3 **réduit** les gaps épistémiques du système entier
- L'humain **réduit** les gaps que le système ne peut pas voir

### 2.3 Structure homogène des Teams

Chaque Team (sauf L0 qui a des agents spécialisés) partage la même structure interne :

```python
class TeamLevel:
    def __init__(self, level, frequency, target, agents, genome_path):
        self.level = level
        self.frequency = frequency      # "hourly", "daily", "weekly", "monthly"
        self.target = target            # Team du niveau inférieur
        self.agents = agents            # Liste d'agents
        self.genome = load(genome_path) # Config YAML versionnée

# L1, L2, L3 partagent les mêmes rôles d'agents :
# - Observer   : lit les métriques + gap manifests du niveau inférieur
# - Strategist : propose des mutations priorisées par impact sur les gaps
# - Critic     : évalue le risque des mutations proposées
# - Executor   : applique les mutations validées + commit le genome
```

### 2.4 Communication inter-niveaux

Pattern event-driven. Chaque Team publie ses résultats (métriques, gap manifests, mutations) dans un state store partagé (Redis ou Supabase). La Team du niveau supérieur s'abonne et consomme à sa propre fréquence.

### 2.5 Versioning des genomes

Chaque Team possède un **genome file** (YAML) décrivant sa configuration complète : agents, modèles, prompts, paramètres, sources de données. Chaque mutation est un commit Git. L'historique complet de l'évolution du système est traçable et rollbackable.

---

## 3. Team L0 — Les Chercheurs (architecture détaillée)

### 3.1 Agents de L0

```
Aléatoire intelligent ──► ExplorerAgent ──► SynthesisAgent ──► CriticAgent(s) ──► CuratorAgent
                              │                   │                  │                 │
                         Tire des            Construit des      Débat               Score,
                         collisions          ponts inter-       adversarial         classe,
                         de domaines         disciplinaires     (avocat du          filtre
                                                                diable + ange)
                                                    │
                                              GapDetector L0
                                              (gap manifest par hypothèse)
```

#### 3.1.1 ExplorerAgent

**Modèle** : léger (Haiku ou équivalent) — volume élevé, coût bas.

**Input** : la carte des domaines scientifiques (section 4.2) + les paramètres d'aléatoire.

**Output** : des paires de domaines (collision_pair) avec métadonnées.

**Stratégies d'aléatoire** (combinables, sélectionnées par le genome L0) :

| Stratégie | Mécanisme | Force | Faiblesse |
|-----------|-----------|-------|-----------|
| Distance sémantique contrôlée | Tire des paires dans la zone fertile (cosine distance 0.4-0.7) | Équilibre originalité/cohérence | Dépend de la qualité des embeddings |
| Analogie structurelle | Croise des patterns (feedback négatif, cascades, résonance…) plutôt que des domaines | Trouve des isomorphismes profonds | Plus complexe à implémenter |
| Guidé par anomalies | Cible les preprints mentionnant "unexplained", "surprising", "paradox" | Haute pertinence disruptive | Biais vers les domaines très publiés |
| Template historique | Utilise le pattern d'une découverte passée comme modèle de recherche | Exploite la structure de la sérendipité | Risque de sur-fitter l'histoire |

#### 3.1.2 SynthesisAgent

**Modèle** : puissant (Sonnet/Opus) — qualité maximale sur la brique critique.

**Input** : une collision_pair + contexte des deux domaines (abstracts clés, concepts fondamentaux).

**Output** : un objet hypothèse structuré (section 4.3) + un gap manifest (section 5.2).

**Règle critique** : le SynthesisAgent a le DROIT de répondre "no_bridge_found". Un output forcé est pire qu'un output nul. Le taux de "no_bridge" est lui-même une métrique précieuse (indique si l'aléatoire est bien calibré).

#### 3.1.3 CriticAgents (x2 — débat adversarial)

**Modèle** : Sonnet pour les deux.

**Architecture** : deux agents en opposition.

- **DevilAdvocate** : cherche à démolir l'hypothèse. Incohérences, violations de lois fondamentales, précédents qui contredisent, hypothèses cachées non justifiées.
- **AngelAdvocate** : cherche les signaux faibles qui soutiennent l'hypothèse. Précédents partiels, résultats adjacents, tendances émergentes.

**Output** : un debate_log structuré + des scores composites.

#### 3.1.4 CuratorAgent

**Modèle** : léger (Haiku).

**Input** : hypothèses scorées + debate_logs.

**Output** : top 1-2% des hypothèses, enrichies de recommandations de next_steps.

**Rôle additionnel** : agrège les gap manifests de toutes les hypothèses du cycle pour le GapDetector L0.

### 3.2 Cycle opérationnel de L0

```
Toutes les heures (configurable) :
1. ExplorerAgent génère N collisions (N=50-100, configurable)
2. SynthesisAgent traite chaque collision → hypothèse + gap manifest (ou "no_bridge")
3. CriticAgents débattent sur chaque hypothèse survivante
4. CuratorAgent filtre, score, agrège
5. Output :
   - hypotheses_batch.json → state store (pour L1 + humain)
   - gap_aggregate.json → state store (pour L1)
   - metrics.json → state store (pour L1)
```

### 3.3 Métriques de L0

| Métrique | Description | Usage |
|----------|-------------|-------|
| `volume` | Nombre de collisions traitées par cycle | Capacité |
| `bridge_rate` | % de collisions ayant produit un pont (vs "no_bridge") | Calibration de l'aléatoire |
| `survival_rate` | % d'hypothèses survivant au CriticAgent | Qualité du SynthesisAgent |
| `curation_rate` | % d'hypothèses retenues par le CuratorAgent | Sélectivité |
| `human_validation_rate` | % d'hypothèses jugées "intéressantes" par l'humain | Ground truth |
| `novelty_mean` | Score moyen de nouveauté | Originalité |
| `coherence_mean` | Score moyen de cohérence | Fiabilité |
| `gap_total` | Nombre total de gaps détectés | Conscience des limites |
| `gap_recurrence` | Nombre de gaps récurrents (apparaissent >3 fois) | Priorités pour L1 |

---

## 4. Composants transversaux

### 4.1 Moteur d'aléatoire

Le cœur de SPORE. Génère les collisions qui alimentent L0.

**Paramètres mutables** (par L1) :
- `distance_min` / `distance_max` : bornes de la zone fertile dans l'espace d'embeddings
- `strategy_weights` : poids relatifs des 4 stratégies d'aléatoire
- `anomaly_recency_bias` : préférence pour les anomalies récentes vs historiques
- `domain_exclusion_list` : domaines temporairement exclus (sur-explorés)
- `temperature` : degré de chaos dans le tirage (0 = déterministe, 1 = pur aléatoire)

**Propriété fondamentale** : un taux de mutation incompressible. Même quand le système converge vers une zone fertile, une fraction de l'aléatoire reste protégée (chaos irréductible). Sans ça, SPORE perd sa capacité de sérendipité. Ce taux minimum est fixé dans `constitution.yaml` (hors scope de mutation).

### 4.2 Carte des domaines scientifiques (Ontologie)

**Approche hybride** :

1. **Base taxonomique** : OpenAlex concepts (~65K concepts hiérarchisés) comme squelette.
2. **Enrichissement par embeddings** : les abstracts récents (ArXiv, PubMed, Semantic Scholar) sont embeddings et clusterisés. Chaque cluster affine ou crée des "domaines émergents" non présents dans la taxonomie.
3. **Distances** : cosine distance entre embeddings de domaines. Recalculée périodiquement par L1.

**Granularité cible** : ~500-2000 domaines. Ni trop gros (pas de signal), ni trop fin (pas de pont possible par un LLM).

**Format** :
```yaml
domain:
  id: "D-0042"
  name: "Ant Colony Optimization"
  parent: "Swarm Intelligence"
  taxonomy_source: "openalex"
  embedding: [0.12, -0.34, ...]  # vecteur dense
  key_concepts: ["pheromone", "stigmergy", "foraging"]
  recent_anomalies: ["arxiv:2403.xxxxx — unexpected convergence in non-stationary..."]
  papers_count_12m: 342
  last_updated: "2026-04-01"
```

### 4.3 Format d'hypothèse (Hypothesis Schema)

Contrat d'interface de tout le système. Chaque hypothèse est un objet structuré :

```yaml
hypothesis:
  id: "SPORE-2026-04-0042"
  generated_at: "2026-04-04T14:32:00Z"
  genome_version: "l0_v23"
  
  # Collision d'origine
  collision:
    domain_a:
      id: "D-0042"
      name: "Ant Colony Optimization"
    domain_b:
      id: "D-1337"
      name: "5G Spectrum Allocation"
    strategy: "structural_analogy"
    distance_score: 0.58
  
  # Hypothèse
  bridge:
    summary: "description concise du pont"
    mechanism: "explication du mécanisme partagé"
    type: "structural_analogy | causal_transfer | methodological_transfer | conceptual_reframe"
  
  # Évaluation (scorée par CriticAgents)
  scores:
    novelty: 0.72          # vs littérature existante
    coherence: 0.85        # cohérence physique/logique
    testability: 0.91      # protocole expérimental identifiable
    impact_potential: 0.68  # si vrai, quelle magnitude de changement
    hallucination_risk: 0.15
    composite: 0.76        # score pondéré agrégé
  
  # Prédictions testables (falsifiabilité)
  predictions:
    - statement: "description de la prédiction"
      metric: "grandeur mesurable"
      expected_range: "valeur attendue"
      differs_from_consensus: true
  
  # Condition d'élimination
  kill_condition: "critère précis qui invaliderait l'hypothèse"
  
  # Actionnabilité
  next_steps:
    - "action concrète 1"
    - "action concrète 2"
  relevant_labs: ["lab ou chercheur identifié"]
  relevant_datasets: ["dataset public identifié"]
  
  # Traçabilité
  sources_used: ["arxiv:xxxx", "doi:xxxx"]
  critic_debate_log: "debate_0042.json"
  
  # Gap manifest (section 5)
  gap_manifest:
    data_gaps: [...]
    competence_gaps: [...]
    epistemic_gaps: [...]
  
  # Cycle de vie
  status: "generated | curated | human_reviewed | validated | rejected | archived"
  human_feedback: null  # rempli a posteriori
```

### 4.4 Sources de données

| Source | Type | Accès | Priorité MVP |
|--------|------|-------|--------------|
| ArXiv API | Preprints (physique, math, CS, bio quantitative) | Gratuit | ★★★ |
| Semantic Scholar API | Métadonnées, citations, abstracts multi-domaines | Gratuit (rate limited) | ★★★ |
| OpenAlex API | Concepts, auteurs, institutions, works | Gratuit | ★★★ |
| PubMed / Europe PMC | Biomédical | Gratuit | ★★☆ |
| Google Patents API | Brevets | Gratuit | ★☆☆ |
| Retraction Watch DB | Papiers rétractés | Gratuit | ★☆☆ |
| Zenodo / Figshare | Datasets orphelins | Gratuit | ★☆☆ |
| OpenReview | Reviews de papiers | Gratuit | ★☆☆ |

---

## 5. GapEngine — Le système nerveux

### 5.1 Philosophie

Le GapEngine est le mécanisme par lequel SPORE acquiert une **conscience opérationnelle** de ses propres limites. Chaque niveau du système cartographie activement ce qu'il sait, ce qu'il ne sait pas, et ce qu'il ne sait même pas qu'il ne sait pas.

Sans le GapEngine, les Teams L1+ optimisent à l'aveugle. Avec le GapEngine, elles ont un **gradient dirigé** : elles savent dans quelle direction muter pour réduire les gaps les plus critiques.

### 5.2 Trois types de gaps

| Type | Définition | Exemple | Réponse typique de L1 |
|------|-----------|---------|----------------------|
| **Data Gap** | "Je sais que ce domaine existe mais je n'ai pas les données" | Pas d'accès à la base ICSD (cristallographie) | Ajouter une source de données |
| **Competence Gap** | "Je n'ai pas la capacité cognitive pour ce traitement" | Vérification d'un invariant topologique | Ajouter un agent spécialisé ou un outil externe (Wolfram, solveur symbolique) |
| **Epistemic Gap** | "Je ne sais pas ce que je ne sais pas dans cette zone" | Aucune exploration des savoirs traditionnels non occidentaux | Élargir la surface d'exploration (aléatoire) dans la zone aveugle |

### 5.3 Gap Manifest (format)

Attaché à chaque hypothèse par le SynthesisAgent :

```yaml
gap_manifest:
  data_gaps:
    - domain: "crystallography"
      description: "Need access to ICSD database for structural verification"
      criticality: "high"
      recurrence: 3  # nombre de fois que ce gap apparaît
      
  competence_gaps:
    - type: "formal_verification"
      description: "Cannot verify topological invariant of proposed structure"
      workaround_used: "assumed based on analogy with known structures"
      confidence_degradation: 0.25  # impact sur le score de confiance
      
  epistemic_gaps:
    - zone: "traditional_fermentation_practices"
      signal: "found references suggesting relevance but couldn't evaluate depth"
      exploration_priority: "medium"
```

### 5.4 GapDetectors par niveau

| Niveau | Scope du GapDetector | Fréquence | Output |
|--------|---------------------|-----------|--------|
| L0 | **Local** — par hypothèse | Chaque hypothèse | gap_manifest unitaire |
| L1 | **Statistique** — agrège les gap manifests de L0, cherche les patterns récurrents | Quotidien | gap_priorities (top 5 gaps récurrents + impact estimé) |
| L2 | **Structurel** — identifie les gaps que L1 n'arrive pas à combler malgré ses mutations | Hebdomadaire | architectural_gaps (problèmes nécessitant une refonte, pas un tweak) |
| L3 | **Philosophique** — compare la carte d'exploration de SPORE à la carte réelle de la science, identifie les quadrants jamais explorés | Mensuel | epistemic_blind_spots |

### 5.5 Feedback loop

```
L0 produit gap_manifests
    │
    ▼
L1 agrège → identifie gap priorities → mute L0 pour réduire les gaps prioritaires
    │
    ▼
L2 observe : L1 réduit-elle efficacement les gaps ? Sinon → mute L1 ou escalade
    │
    ▼
L3 observe : des zones entières de la science sont-elles ignorées ? → ajuste le scope
    │
    ▼
Humain : les gaps que le système ne peut pas voir → réorientations stratégiques
```

---

## 6. Auto-évolution (EvolutionEngine)

### 6.1 Mécanismes d'évolution

| Mécanisme | Description | Niveau |
|-----------|-------------|--------|
| **Mutation de prompt** | Modification des instructions d'un agent | L1 |
| **Swap de modèle** | Changer le LLM d'un agent (Haiku→Sonnet, Sonnet→Opus) | L1 |
| **Ajout/suppression d'agent** | Introduire un agent spécialisé ou supprimer un agent non performant | L1-L2 |
| **Ajout de source de données** | Connecter une nouvelle API / dataset | L1 |
| **Modification des paramètres d'aléatoire** | Distance cible, stratégie dominante, température | L1 |
| **Fork de Team** | Dupliquer L0 en deux instances concurrentes avec configs différentes | L2 |
| **Redéfinition des métriques** | Changer les poids du score composite, ajouter une métrique | L2-L3 |
| **Redéfinition du scope** | Ajouter/retirer des domaines scientifiques | L3 |

### 6.2 Genome file (exemple L0)

```yaml
# l0_genome.yaml — version 23
genome_version: "l0_v23"
last_mutated: "2026-04-04"
mutated_by: "L1"

agents:
  explorer:
    model: "claude-haiku-4-5-20251001"
    prompt_version: "explorer_v12"
    parameters:
      collisions_per_cycle: 100
      
  synthesis:
    model: "claude-sonnet-4-20250514"
    prompt_version: "synthesis_v8"
    parameters:
      no_bridge_allowed: true
      max_tokens: 2000
      
  critic_devil:
    model: "claude-sonnet-4-20250514"
    prompt_version: "devil_v5"
    
  critic_angel:
    model: "claude-sonnet-4-20250514"
    prompt_version: "angel_v5"
    
  curator:
    model: "claude-haiku-4-5-20251001"
    prompt_version: "curator_v3"
    parameters:
      top_percent: 0.02  # garde le top 2%

randomness:
  strategy_weights:
    semantic_distance: 0.35
    structural_analogy: 0.30
    anomaly_guided: 0.25
    historical_template: 0.10
  distance_min: 0.40
  distance_max: 0.70
  chaos_floor: 0.15  # minimum incompressible d'aléatoire pur
  temperature: 0.7

sources:
  - arxiv
  - semantic_scholar
  - openalex
  
schedule:
  frequency: "hourly"
  active_hours: "00:00-23:59"  # 24/7
```

### 6.3 Circuit breakers

Chaque mutation est soumise à des garde-fous :

1. **Rollback automatique** : si les métriques de sortie se dégradent de >15% dans les 48h suivant une mutation, rollback au genome précédent.
2. **Budget plafond** : coût API maximum par cycle. Le système ne peut pas scaler indéfiniment.
3. **Mutation rate limit** : maximum 3 mutations par cycle de L1. Évite l'instabilité par sur-mutation.
4. **Constitution inviolable** : `constitution.yaml` est hors du scope de mutation de toute Team. Contient les objectifs éthiques, le chaos_floor, les domaines exclus, les limites de budget.

### 6.4 A/B testing intégré

L1 peut exécuter deux versions d'un agent en parallèle (shadow mode). Les outputs des deux versions sont scorés par le CriticAgent. La version gagnante est promue. Sélection naturelle appliquée aux prompts.

---

## 7. Système immunitaire (anti-hallucination)

### 7.1 Filtres

| Filtre | Description | Étape |
|--------|-------------|-------|
| Cohérence thermodynamique | Vérifie que l'hypothèse ne viole pas les lois fondamentales (conservation énergie, entropie, causalité) | CriticAgent |
| Détecteur de "bridges trop beaux" | Si la connexion est trop élégante/symétrique → suspicion d'hallucination. Les vraies connexions sont souvent partielles et laides | CriticAgent |
| Prédiction différentielle | L'hypothèse doit impliquer quelque chose de mesurable qui diffère de la théorie standard. Sinon → rejet | CuratorAgent |
| Test de non-trivialité | L'hypothèse est soumise à 5 instances indépendantes du même LLM. Si toutes convergent → probablement trivial | CuratorAgent (optionnel, coûteux) |

### 7.2 Score de confiance calibré

Pas un score unique mais un vecteur à 5 composantes :

```
[novelty, coherence, testability, impact_potential, hallucination_risk]
```

Le `composite` est une moyenne pondérée configurable dans le genome (les poids sont mutables par L1).

---

## 8. Estimation des coûts

### 8.1 Coût API (L0 seul, par jour)

| Agent | Modèle | Appels/jour | Tokens in/appel | Tokens out/appel | Coût estimé/jour |
|-------|--------|-------------|-----------------|------------------|-------------------|
| ExplorerAgent | Haiku | 100 | 500 | 1000 | ~$0.15 |
| SynthesisAgent | Sonnet | 100 | 2000 | 2000 | ~$3.00 |
| CriticAgent x2 | Sonnet | 60* | 3000 | 1500 | ~$2.70 |
| CuratorAgent | Haiku | 30* | 2000 | 500 | ~$0.08 |
| Web search / grounding | — | 30 | — | — | ~$1.00 |

*après filtre des "no_bridge"

**Total L0 : ~$7/jour ≈ $210/mois**

### 8.2 Coût total estimé (L0-L3)

| Composant | Coût/mois |
|-----------|-----------|
| L0 — Recherche | $210 |
| L1 — Entraîneurs | $30-50 |
| L2 — Architectes | $10-20 |
| L3 — Philosophes | $5-10 |
| Infra (Redis/Supabase, compute) | $20-50 |
| **Total** | **$275-340/mois** |

Tendance : en baisse chaque trimestre avec la réduction des prix des LLM.

---

## 9. Modèle économique (pistes)

| Modèle | Description | Viabilité |
|--------|-------------|-----------|
| SaaS pour labos | Abonnement mensuel, 10-20 hypothèses calibrées/mois dans le domaine du chercheur | Moyen terme — nécessite validation du signal |
| Marketplace d'hypothèses | Les hypothèses prometteuses sont publiées, les labos enchérissent | Long terme — nécessite une masse critique |
| Partenariat éditeurs scientifiques | "AI-generated hypothesis of the month" avec co-attribution | Court terme — vitrine |
| Open core | Framework open source, pipeline premium (sources, mémoire entraînée, fine-tuning CriticAgent) | Moyen terme |

---

## 10. Hypothèses à valider (risques du concept)

| # | Hypothèse | Risque si faux | Comment tester |
|---|-----------|----------------|----------------|
| H1 | Un LLM peut générer des ponts interdisciplinaires non triviaux et utiles | Tout le projet s'effondre | **PoC L0 minimal** (section 11) |
| H2 | L'aléatoire contrôlé par distance sémantique produit un meilleur signal que l'aléatoire naïf | Trop de bruit, pas assez de signal | A/B test dans le PoC |
| H3 | Le débat adversarial (devil/angel) améliore la détection d'hallucinations | CriticAgent unique suffirait | Comparer les deux approches |
| H4 | Le GapEngine fournit un gradient utile à L1 | L1 optimise aussi bien sans | Mesurer l'impact sur le human_validation_rate |
| H5 | Des chercheurs humains trouvent les hypothèses intéressantes | Personne ne regarde les outputs | Feedback précoce avec 3-5 chercheurs |
| H6 | Le coût reste gérable à l'échelle | Explosion des coûts API | Monitoring strict dès le PoC |

---

## 11. Protocole de Proof of Concept

### 11.1 Objectif du PoC

Valider H1 : un LLM peut générer des ponts interdisciplinaires non triviaux. 

Secondairement : calibrer les paramètres d'aléatoire et tester le format d'hypothèse.

### 11.2 Scope du PoC

- **L0 SEUL**. Pas de L1, pas de GapEngine, pas d'auto-évolution.
- **Un seul domaine bac à sable** : Science des matériaux (carrefour physique/chimie/ingénierie, bonne couverture ArXiv, résultats vérifiables).
- **Aléatoire simplifié** : distance sémantique uniquement, pas de stratégies multiples.
- **50 collisions** générées, évaluées par 3-5 humains.

### 11.3 Stack technique PoC

```
Python 3.12+
├── LangGraph          — orchestration des agents
├── Anthropic SDK      — appels LLM (Sonnet pour Synthesis/Critic, Haiku pour Explorer/Curator)
├── sentence-transformers — embeddings pour la carte des domaines
├── Semantic Scholar API — source de données principale
├── ArXiv API          — source complémentaire
├── Supabase (ou SQLite pour le PoC) — stockage des hypothèses et métriques
└── Streamlit (ou CLI) — interface de review humain
```

### 11.4 Livrables du PoC

1. Script Python exécutable en CLI : `python spore.py --collisions 50 --domain materials_science`
2. 50 hypothèses au format YAML standardisé
3. Interface minimale de review (Streamlit ou simple formulaire) pour feedback humain
4. Rapport d'analyse : distribution des scores, taux de bridge, feedback humain agrégé
5. Décision GO/NO-GO pour la phase suivante

### 11.5 Critères de succès du PoC

| Critère | Seuil GO | Seuil KILL |
|---------|----------|------------|
| Bridge rate (% de collisions produisant un pont) | >30% | <10% |
| Human "intéressant" rate | >10% des hypothèses | <3% |
| Au moins 1 hypothèse jugée "je voudrais tester ça" | 1+ | 0 |
| Coût total du run | <$20 | >$100 |

### 11.6 Bootstrap de validation

Avant de lancer les 50 collisions exploratoires, un **test de calibration** sur 10 découvertes interdisciplinaires connues :

| Découverte | Collision à simuler |
|-----------|-------------------|
| Pénicilline | Microbiologie × Chimie des moisissures |
| CRISPR | Immunologie bactérienne × Édition génomique |
| PageRank | Analyse de citations académiques × Algèbre linéaire |
| Réseaux de neurones | Neurobiologie × Optimisation mathématique |
| Artémisinine | Médecine traditionnelle chinoise × Parasitologie |
| Graphène | Chimie du carbone × Physique des matériaux 2D |
| AlphaFold | Bioinformatique structurale × Deep learning |
| Quasicrystaux | Cristallographie × Pavages apériodiques |
| Vulcanisation | Chimie du soufre × Science des polymères |
| Transistor | Physique quantique × Électronique |

SPORE doit "redécouvrir" ≥7/10 de ces ponts pour valider que le mécanisme de base fonctionne.

---

## 12. Roadmap

| Phase | Contenu | Durée estimée | Prérequis |
|-------|---------|---------------|-----------|
| **Phase 0 — Design Doc** | Ce document. Figer la vision. | ✅ Done | — |
| **Phase 1 — PoC L0** | 50 collisions, feedback humain, GO/NO-GO | 2-3 semaines | Claude Code + APIs |
| **Phase 2 — L0 opérationnel** | Scheduler cron, multi-stratégies, dashboard Streamlit, stockage Supabase | 3-4 semaines | GO du PoC |
| **Phase 3 — GapEngine** | Gap manifests dans L0, agrégation statistique | 2-3 semaines | L0 stable |
| **Phase 4 — L1 (Entraîneurs)** | Mutations automatisées guidées par gaps, A/B testing | 3-4 semaines | Métriques L0 stables |
| **Phase 5 — L2-L3** | Architectes et Philosophes. Auto-évolution profonde | 4-6 semaines | L1 stable |
| **Phase 6 — Multi-domaines** | Généralisation au-delà de la science des matériaux | Continu | Signal validé |
| **Phase 7 — Produit** | Interface utilisateur, onboarding chercheurs, modèle économique | 8-12 semaines | Validation marché |

---

## 13. Constitution (inviolable)

```yaml
# constitution.yaml — NON MUTABLE par aucune Team
# Seul l'humain (L_HUMAN) peut modifier ce fichier.

ethics:
  excluded_domains:
    - "weapons_development"
    - "surveillance_technology"  
    - "any domain with dual-use concerns without human approval"
  transparency: "all hypotheses include full source tracing"
  attribution: "SPORE is a hypothesis generator, not an author"

safety:
  chaos_floor: 0.10  # minimum 10% d'aléatoire pur, jamais réductible
  max_budget_per_day: 50  # USD
  rollback_threshold: 0.15  # dégradation max avant rollback auto
  max_mutations_per_cycle: 3
  human_approval_required_for:
    - "scope_change"
    - "new_data_source"
    - "agent_architecture_change"
    - "constitution_modification"

philosophy:
  purpose: "Generate testable, novel, interdisciplinary hypotheses"
  stance: "SPORE proposes, humans dispose"
  humility: "Every hypothesis must declare its own uncertainty and gaps"
```

---

## 14. Glossaire

| Terme | Définition |
|-------|-----------|
| **Collision** | Mise en contact de deux domaines scientifiques éloignés |
| **Bridge** | Connexion hypothétique entre deux domaines (analogie, transfert, reframe) |
| **Gap Manifest** | Cartographie des connaissances manquantes pour une hypothèse donnée |
| **Genome** | Fichier de configuration complet d'une Team, versionné dans Git |
| **Mutation** | Modification d'un genome par la Team du niveau supérieur |
| **Team** | Groupe d'agents opérant à un niveau de la hiérarchie récursive |
| **Chaos Floor** | Taux minimum d'aléatoire pur, protégé par la constitution |
| **Kill Condition** | Critère explicite qui invaliderait une hypothèse |
| **L_HUMAN** | Le chercheur humain, dernier niveau de récursion |
| **Constitution** | Fichier de règles inviolables, modifiable uniquement par l'humain |

---

*"La nature ne fait pas de plans quinquennaux. Elle fait des spores."*
