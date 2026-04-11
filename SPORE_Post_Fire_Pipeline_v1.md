# SPORE — Post-🔥 Pipeline: Deep Validation & Research Brief Generation

## Design Document v1.0

**Auteur**: Bac (assisté par Claude)
**Date**: 2026-04-11
**Statut**: Ready to implement
**Dépendances**: SPORE L0 pipeline existant, Semantic Scholar API, DeepSeek V3.2

---

## 1. Vision

Le pipeline post-🔥 transforme une hypothèse brute bien notée en un **Research Brief publication-ready**, ancré sur de la littérature réelle, avec un protocole expérimental concret et un panel de review multi-perspectives.

**Objectif qualité** : un chercheur du domaine qui reçoit le brief doit pouvoir (a) comprendre l'hypothèse en 2 minutes, (b) évaluer sa faisabilité en 10 minutes, (c) démarrer un protocole exploratoire sans travail préparatoire supplémentaire.

**Principe fondamental** : zéro hallucination bibliographique. Chaque référence citée est vérifiée via API. Si une ref ne peut pas être vérifiée, elle est exclue.

---

## 2. Architecture

```
Hypothèse 🔥 (sortie L0 Curator)
        │
        ▼
┌─────────────────────┐
│  Literature Grounding│  ← Semantic Scholar API
│       Agent          │
└────────┬────────────┘
         │  enriched_hypothesis + evidence_base + novelty_report
         ▼
┌─────────────────────┐
│  Hypothesis          │
│  Sharpening Agent    │
└────────┬────────────┘
         │  sharpened_hypothesis (variables, mécanisme, prédictions quantitatives)
         ▼
┌─────────────────────┐
│  Experimental        │
│  Protocol Agent      │
└────────┬────────────┘
         │  protocol (3 phases: in silico → minimal → full)
         ▼
┌─────────────────────┐
│  Multi-Reviewer      │
│  Panel (5 personas)  │
└────────┬────────────┘
         │  panel_verdict + scores + recommandations
         ▼
┌─────────────────────┐
│  Research Brief      │
│  Generator           │
└────────┬────────────┘
         │
         ▼
    Research Brief (MD + PDF)
```

**Intégration LangGraph** : ce pipeline est un sous-graphe déclenché après le Curator quand `reviewer_rating == "🔥"`. Il s'insère comme un nœud conditionnel dans le graphe L0 existant.

---

## 3. Agent 1 — Literature Grounding Agent

### Mission
Ancrer l'hypothèse dans la littérature réelle. Éliminer les hallucinations bibliographiques. Évaluer la nouveauté objective.

### Entrée
```python
{
    "hypothesis": str,           # Hypothèse brute du Curator
    "domains": list[str],        # Les 2+ domaines de la collision
    "mechanisms": str,           # Mécanismes proposés par Synthesis
    "keywords": list[str],       # Extraits par un pré-processing LLM
    "gap_manifest": dict         # Gap manifest existant
}
```

### Pipeline interne

#### Étape 1 — Extraction de requêtes de recherche
Le LLM génère 8-12 requêtes Semantic Scholar optimisées à partir de l'hypothèse :
- 3-4 requêtes sur l'hypothèse elle-même (novelty check)
- 3-4 requêtes sur les mécanismes sous-jacents (evidence base)
- 2-4 requêtes sur les domaines croisés (cross-domain precedents)

#### Étape 2 — Recherche Semantic Scholar
Pour chaque requête :
```python
# Semantic Scholar API — gratuit, 100 req/sec
GET https://api.semanticscholar.org/graph/v1/paper/search
  ?query={query}
  &limit=10
  &fields=title,abstract,year,citationCount,authors,externalIds,tldr
```

Filtres appliqués :
- `year >= 2015` pour l'evidence base (sauf papiers fondateurs)
- `citationCount >= 5` pour éliminer le bruit
- Tri par relevance score Semantic Scholar

#### Étape 3 — Analyse LLM des résultats
Le LLM analyse les papers trouvés et produit :

```python
{
    "novelty_assessment": {
        "score": float,          # 0-1 (1 = totalement nouveau)
        "closest_existing_work": [
            {
                "paper_id": str,
                "title": str,
                "doi": str,
                "year": int,
                "similarity": str,   # "identical" | "very_close" | "related" | "tangential"
                "key_difference": str # En quoi l'hypothèse SPORE diffère
            }
        ],
        "verdict": str            # "novel" | "incremental" | "already_explored" | "already_proven"
    },
    "evidence_base": [
        {
            "paper_id": str,
            "title": str,
            "doi": str,
            "year": int,
            "citation_count": int,
            "support_type": str,   # "direct" | "indirect" | "analogous" | "contradictory"
            "relevance": str,      # Explication en 1-2 phrases
            "key_finding": str     # Le résultat pertinent du papier
        }
    ],
    "counter_evidence": [
        {
            "paper_id": str,
            "title": str,
            "doi": str,
            "finding": str,        # Ce qui contredit l'hypothèse
            "severity": str        # "fatal" | "serious" | "minor" | "addressable"
        }
    ],
    "gap_manifest_update": {
        "closed_gaps": list[str],  # Gaps résolus par la littérature trouvée
        "new_gaps": list[str],     # Nouveaux gaps identifiés
        "data_available": list[str] # Datasets existants identifiés
    }
}
```

### Kill conditions
- `novelty_assessment.verdict == "already_proven"` → STOP, l'hypothèse n'est pas nouvelle
- `counter_evidence` contient un item `severity == "fatal"` → STOP, l'hypothèse est réfutée
- Aucun papier trouvé dans aucun des domaines → WARNING, domaine trop niche ou requêtes mal formulées

### Prompt skeleton
```
Tu es un chercheur bibliographique rigoureux. On te donne une hypothèse scientifique
et un ensemble de papiers trouvés via Semantic Scholar.

RÈGLES ABSOLUES :
- Tu ne cites JAMAIS un papier qui n'est pas dans la liste fournie
- Tu ne fabriques JAMAIS de DOI, titre, ou auteur
- Si tu n'es pas sûr de la pertinence d'un papier, tu le classes "tangential"
- Si aucun papier ne soutient un mécanisme, tu le dis explicitement

HYPOTHÈSE : {hypothesis}
DOMAINES : {domains}
MÉCANISMES PROPOSÉS : {mechanisms}
PAPIERS TROUVÉS : {papers_json}

Produis ton analyse au format JSON suivant : ...
```

### Coût estimé
- Semantic Scholar API : gratuit
- 8-12 requêtes × 10 résultats = 80-120 abstracts à analyser
- ~4000 tokens input + ~2000 tokens output sur DeepSeek
- **~$0.01 par hypothèse**

---

## 4. Agent 2 — Hypothesis Sharpening Agent

### Mission
Transformer l'hypothèse narrative en formulation scientifique rigoureuse avec variables explicites et prédictions quantitatives falsifiables.

### Entrée
L'hypothèse brute + le rapport du Literature Grounding Agent.

### Sortie
```python
{
    "title": str,                    # Titre concis, style publication
    "formal_statement": str,         # Formulation formelle (1-2 phrases)
    "independent_variables": [
        {"name": str, "type": str, "range": str, "unit": str}
    ],
    "dependent_variables": [
        {"name": str, "type": str, "expected_direction": str, "unit": str}
    ],
    "proposed_mechanism": {
        "causal_chain": list[str],   # Étapes du mécanisme causal
        "key_assumptions": list[str], # Hypothèses implicites rendues explicites
        "known_unknowns": list[str]  # Ce qu'on sait ne pas savoir
    },
    "falsifiable_predictions": [
        {
            "prediction": str,       # "Si X, alors Y"
            "quantitative_bound": str, # "réduction de 15-30% sur 1000 cycles à 400°C"
            "measurement_method": str,
            "null_hypothesis": str,
            "statistical_test": str   # "t-test bilatéral, α=0.05"
        }
    ],
    "boundary_conditions": [
        {"condition": str, "justification": str}
    ],
    "theoretical_framework": str     # Cadre théorique de rattachement
}
```

### Prompt skeleton
```
Tu es un épistémologue des sciences rigoureux, spécialisé en méthodologie expérimentale.

Tu reçois une hypothèse scientifique et sa base bibliographique.
Ta mission : la reformuler au standard d'une proposition de recherche publiable.

EXIGENCES :
- Chaque prédiction DOIT être quantitative (pas de "améliore", "augmente")
- Chaque variable DOIT avoir une unité et une plage attendue
- Le mécanisme causal DOIT être décomposé en étapes testables individuellement
- Les hypothèses implicites DOIVENT être rendues explicites
- Les boundary conditions DOIVENT être spécifiées

HYPOTHÈSE BRUTE : {hypothesis}
EVIDENCE BASE : {evidence_base}
COUNTER EVIDENCE : {counter_evidence}

Produis la formulation affinée au format JSON suivant : ...
```

### Coût estimé
- ~3000 tokens input + ~2000 tokens output
- **~$0.008 par hypothèse**

---

## 5. Agent 3 — Experimental Protocol Agent

### Mission
Concevoir un protocole de validation en 3 phases progressives, chacune avec un go/no-go explicite.

### Sortie
```python
{
    "protocol_title": str,
    "overall_timeline": str,         # "6-12 mois"
    "overall_budget_estimate": str,  # "€15k-50k"
    "phases": [
        {
            "phase_number": int,     # 1, 2, 3
            "phase_name": str,       # "In Silico Validation" | "Minimal Experimental" | "Full Protocol"
            "objective": str,
            "methodology": str,      # Description détaillée
            "required_resources": {
                "equipment": list[str],
                "software": list[str],
                "datasets": list[str],
                "competences": list[str],
                "estimated_cost": str,
                "estimated_duration": str
            },
            "expected_outputs": list[str],
            "success_criteria": [
                {"metric": str, "threshold": str, "measurement": str}
            ],
            "go_nogo_decision": {
                "go_if": str,
                "nogo_if": str,
                "pivot_if": str      # Condition pour réorienter plutôt qu'arrêter
            },
            "risks": [
                {"risk": str, "probability": str, "mitigation": str}
            ]
        }
    ],
    "phase_1_quick_start": {
        "can_start_today": bool,
        "first_action": str,         # L'action concrète #1
        "tools_needed": list[str],
        "open_data_sources": list[str]
    }
}
```

### Logique des 3 phases

**Phase 1 — In Silico / Analyse de données existantes**
- Coût : €0-2k
- Durée : 2-8 semaines
- But : tuer l'hypothèse rapidement et à moindre coût
- Exemples : simulation computationnelle, analyse de datasets open, méta-analyse, modélisation mathématique

**Phase 2 — Validation expérimentale minimale**
- Coût : €2k-15k
- Durée : 1-3 mois
- But : le plus petit test physique qui confirme ou infirme le mécanisme central
- Exemples : expérience de bench, prototype minimal, étude pilote N=20

**Phase 3 — Protocole complet**
- Coût : €15k-200k+
- Durée : 6-18 mois
- But : validation rigoureuse publiable
- Exemples : étude randomisée, fabrication + caractérisation complète, essai pré-clinique

### Coût estimé
- ~4000 tokens input + ~3000 tokens output
- **~$0.01 par hypothèse**

---

## 6. Agent 4 — Multi-Reviewer Panel

### Mission
Évaluer l'hypothèse affinée sous 5 angles complémentaires via des personas distinctes.

### Les 5 Reviewers

#### Reviewer 1 — Le Méthodologue
```
Persona : Professeur de méthodologie des sciences, 30 ans d'expérience en design expérimental.
Focus : Le protocole est-il rigoureux ? Les contrôles sont-ils suffisants ? Les biais sont-ils adressés ?
Critères : validité interne, reproductibilité, puissance statistique, biais potentiels.
```

#### Reviewer 2 — Le Domain Expert (simulé)
```
Persona : Chercheur senior dans le domaine principal de l'hypothèse, H-index > 40.
Focus : Est-ce cohérent avec l'état de l'art ? Les mécanismes sont-ils plausibles ?
Critères : cohérence théorique, plausibilité des mécanismes, positionnement vs état de l'art.
```

#### Reviewer 3 — Le Contrarian
```
Persona : Reviewer #2 de journal, celui qui cherche à démolir. Sceptique professionnel.
Focus : Quel est le scénario le plus probable où ça échoue ? Quels sont les angles morts ?
Critères : failles logiques, hypothèses non testées, confounders, effet taille probable.
Output spécial : "Top 3 reasons this will fail" + severity assessment.
```

#### Reviewer 4 — L'Industriel
```
Persona : VP R&D dans une entreprise du secteur, focus ROI et time-to-market.
Focus : Qui paierait pour ça ? Quel est le marché ? Quel avantage compétitif ?
Critères : taille de marché, compétition, barrières à l'entrée, IP potentielle, timeline commerciale.
```

#### Reviewer 5 — Le Funding Strategist
```
Persona : Directeur de programme ANR/ERC, expert en financement de la recherche.
Focus : Quel appel à projets colle ? Comment structurer la demande ?
Critères : fit avec programmes existants (Horizon Europe, ANR, ERC, NIH), maturité TRL, consortiums potentiels.
Output spécial : 3 programmes de financement concrets avec deadlines et taux de succès.
```

### Orchestration
```python
# Exécution parallèle des 5 reviewers (LangGraph fan-out)
reviews = await asyncio.gather(
    run_reviewer("methodologist", sharpened_hypothesis, protocol),
    run_reviewer("domain_expert", sharpened_hypothesis, evidence_base),
    run_reviewer("contrarian", sharpened_hypothesis, counter_evidence),
    run_reviewer("industrialist", sharpened_hypothesis, impact_analysis),
    run_reviewer("funding_strategist", sharpened_hypothesis, protocol),
)

# Meta-reviewer synthétise
meta_verdict = await run_meta_reviewer(reviews)
```

### Sortie par reviewer
```python
{
    "reviewer_persona": str,
    "overall_score": float,       # 0-10
    "verdict": str,               # "strong_accept" | "accept" | "weak_accept" | "weak_reject" | "reject"
    "strengths": list[str],       # 2-3 points forts
    "weaknesses": list[str],      # 2-3 faiblesses
    "critical_questions": list[str], # Questions ouvertes
    "recommendation": str,        # Recommandation actionnable en 2-3 phrases
    "confidence": float           # 0-1, confiance du reviewer dans son évaluation
}
```

### Sortie du Meta-Reviewer
```python
{
    "consensus_score": float,     # Moyenne pondérée par confidence
    "verdict": str,               # "publish_brief" | "revise_and_resubmit" | "reject"
    "key_consensus": list[str],   # Points d'accord
    "key_disagreements": list[str], # Points de désaccord
    "critical_path": str,         # Le facteur le plus déterminant pour le succès/échec
    "final_recommendation": str,  # Synthèse en 3-5 phrases
    "brief_quality_gate": bool    # True = on génère le brief. False = retour au Sharpening.
}
```

### Boucle de révision
Si `verdict == "revise_and_resubmit"` : le Sharpening Agent reçoit les feedbacks et produit une v2. Maximum 2 itérations, puis le meta-reviewer tranche.

### Coût estimé
- 5 reviewers parallèles × ~2000 tokens chacun + meta-reviewer ~1500 tokens
- **~$0.02 par hypothèse** (ou ~$0.04 avec 1 révision)

---

## 7. Agent 5 — Research Brief Generator

### Mission
Compiler toutes les sorties en un document de 4-6 pages, formaté, sourcé, prêt à partager.

### Structure du brief

```markdown
# [Titre de l'hypothèse]

## Metadata
- SPORE ID: SPR-2026-XXXX
- Domaines: [Domain A] × [Domain B]
- Date de génération: YYYY-MM-DD
- Collision score: X.XX
- Panel consensus score: X.X/10
- Novelty score: X.X/1.0

## Abstract (250 mots max)
[Résumé structuré : contexte, hypothèse, mécanisme, impact attendu]

## 1. Hypothèse et mécanisme proposé
### 1.1 Formulation formelle
### 1.2 Variables
### 1.3 Chaîne causale
### 1.4 Conditions aux limites
### 1.5 Cadre théorique

## 2. État de l'art et positionnement
### 2.1 Travaux les plus proches
### 2.2 Base de preuves
### 2.3 Contre-preuves et limitations connues
### 2.4 Évaluation de nouveauté
[Avec références DOI vérifiées — chaque ref est un lien cliquable]

## 3. Prédictions falsifiables
[Tableau : prédiction | borne quantitative | méthode de mesure | H0 | test statistique]

## 4. Protocole expérimental
### 4.1 Phase 1 — Validation in silico
### 4.2 Phase 2 — Expérimentation minimale
### 4.3 Phase 3 — Protocole complet
### 4.4 Quick start : comment démarrer aujourd'hui
[Avec estimations de coûts et timelines]

## 5. Analyse d'impact
### 5.1 Impact scientifique
### 5.2 Applications industrielles et marché
### 5.3 Opportunités de financement
[3 programmes concrets avec deadlines]

## 6. Panel Review Summary
[Tableau : reviewer | score | verdict | point clé]
### 6.1 Consensus
### 6.2 Points de désaccord
### 6.3 Critical path

## 7. Gap Manifest résiduel
### 7.1 Data gaps
### 7.2 Competence gaps
### 7.3 Epistemic gaps

## Références
[Numérotées, avec DOI, triées par pertinence]

## Annexes
- A. Raw collision data
- B. Detailed reviewer reports
- C. Semantic Scholar search queries & results
```

### Formats de sortie
1. **Markdown** — stocké dans `outputs/briefs/SPR-2026-XXXX.md`
2. **PDF** — généré via pandoc avec template LaTeX custom (style academic)
3. **JSON** — version structurée pour l'API future (`outputs/briefs/SPR-2026-XXXX.json`)

### Coût estimé
- ~6000 tokens input (compilation) + ~4000 tokens output
- **~$0.015 par brief**

---

## 8. Coûts totaux du pipeline post-🔥

| Agent | Coût/hypothèse | Appels API externes |
|-------|----------------|---------------------|
| Literature Grounding | ~$0.01 | Semantic Scholar (gratuit) |
| Hypothesis Sharpening | ~$0.008 | — |
| Experimental Protocol | ~$0.01 | — |
| Multi-Reviewer Panel | ~$0.02-0.04 | — |
| Research Brief Generator | ~$0.015 | — |
| **TOTAL** | **~$0.06-0.08** | **gratuit** |

À un taux de 🔥 de ~10% sur 3000 collisions/mois = **~300 hypothèses/mois × $0.07 = ~$21/mois**.

Coût total SPORE mensuel estimé : **$15 (collisions) + $21 (briefs) = ~$36/mois**.

---

## 9. Schéma de données

### Table `briefs` (SQLite)
```sql
CREATE TABLE briefs (
    id TEXT PRIMARY KEY,              -- SPR-2026-XXXX
    hypothesis_id TEXT NOT NULL,      -- FK vers hypotheses
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending',    -- pending | grounding | sharpening | reviewing | complete | killed
    
    -- Literature Grounding
    novelty_score REAL,
    novelty_verdict TEXT,             -- novel | incremental | already_explored | already_proven
    evidence_count INTEGER,
    counter_evidence_count INTEGER,
    kill_reason TEXT,                  -- NULL si pas tué
    
    -- Sharpening
    formal_statement TEXT,
    prediction_count INTEGER,
    
    -- Protocol
    phase1_cost_estimate TEXT,
    phase1_duration TEXT,
    can_start_today BOOLEAN,
    
    -- Panel Review
    panel_consensus_score REAL,
    panel_verdict TEXT,               -- publish_brief | revise_and_resubmit | reject
    revision_count INTEGER DEFAULT 0,
    
    -- Brief
    brief_md_path TEXT,
    brief_pdf_path TEXT,
    brief_json_path TEXT,
    
    -- Full JSON blobs
    grounding_data JSON,
    sharpened_data JSON,
    protocol_data JSON,
    panel_data JSON
);
```

---

## 10. Intégration dans le graphe LangGraph existant

```python
from langgraph.graph import StateGraph, END

# Sous-graphe post-fire
post_fire = StateGraph(PostFireState)

post_fire.add_node("literature_grounding", literature_grounding_agent)
post_fire.add_node("hypothesis_sharpening", hypothesis_sharpening_agent)
post_fire.add_node("experimental_protocol", experimental_protocol_agent)
post_fire.add_node("multi_reviewer_panel", multi_reviewer_panel)
post_fire.add_node("meta_reviewer", meta_reviewer_agent)
post_fire.add_node("research_brief_generator", brief_generator)

post_fire.set_entry_point("literature_grounding")

# Grounding → kill ou continue
post_fire.add_conditional_edges(
    "literature_grounding",
    lambda s: "killed" if s["kill_reason"] else "continue",
    {"killed": END, "continue": "hypothesis_sharpening"}
)

post_fire.add_edge("hypothesis_sharpening", "experimental_protocol")
post_fire.add_edge("experimental_protocol", "multi_reviewer_panel")
post_fire.add_edge("multi_reviewer_panel", "meta_reviewer")

# Meta-reviewer → brief ou révision
post_fire.add_conditional_edges(
    "meta_reviewer",
    lambda s: "revise" if s["panel_verdict"] == "revise_and_resubmit" and s["revision_count"] < 2 else "generate",
    {"revise": "hypothesis_sharpening", "generate": "research_brief_generator"}
)

post_fire.add_edge("research_brief_generator", END)

# Intégration dans le graphe L0 principal
# Après le noeud "curator", ajouter :
main_graph.add_conditional_edges(
    "curator",
    lambda s: "post_fire" if s["reviewer_rating"] == "🔥" else "store",
    {"post_fire": post_fire.compile(), "store": "store_hypothesis"}
)
```

---

## 11. Interface Streamlit — Nouvelles pages

### Page "Research Briefs"
- Liste des briefs générés, triés par consensus score
- Filtres : domaine, novelty score, panel verdict, statut
- Preview inline du brief en markdown
- Téléchargement PDF
- Bouton "Human Review" avec verdict override

### Page "Brief Detail"
- Brief complet rendu en HTML
- Panel review détaillé avec radar chart (5 axes = 5 reviewers)
- Timeline du protocole expérimental (Gantt simplifié)
- Références cliquables (DOI → page du papier)
- Historique des révisions si applicable

### Modifications page "Dashboard"
- Nouveau KPI : nombre de briefs générés ce mois
- Nouveau KPI : taux de conversion 🔥 → brief publié
- Nouveau KPI : novelty score moyen des briefs
- Top 3 briefs du mois

---

## 12. Semantic Scholar API — Notes d'implémentation

### Endpoints utiles
```
# Recherche par mots-clés
GET /graph/v1/paper/search?query=...&limit=10&fields=title,abstract,year,citationCount,authors,externalIds,tldr

# Détail d'un papier (pour vérification DOI)
GET /graph/v1/paper/{paper_id}?fields=title,authors,year,externalIds,abstract,citationCount

# Papiers qui citent un papier (pour explorer les descendants)
GET /graph/v1/paper/{paper_id}/citations?fields=title,year,citationCount&limit=10

# Recommandations basées sur un papier
GET /graph/v1/recommendations/v1/papers/forpaper/{paper_id}?fields=title,abstract,year&limit=5
```

### Rate limits
- 100 requêtes/seconde sans API key
- 1 requête/seconde recommandé pour être courtois
- API key gratuite disponible sur demande pour des limites plus élevées

### Gestion des erreurs
- Retry avec backoff exponentiel (1s, 2s, 4s, max 3 retries)
- Si 0 résultats : reformuler la requête (plus large ou plus spécifique)
- Si API down : skip le grounding, marquer le brief comme "ungrounded" et retry plus tard

---

## 13. Constitution post-🔥 (ajout à constitution.yaml)

```yaml
post_fire_rules:
  # Intégrité bibliographique
  - "NEVER cite a paper that was not found via Semantic Scholar API"
  - "NEVER fabricate a DOI, author name, or publication title"
  - "ALWAYS include DOI when available"
  - "If a mechanism has no supporting evidence, state it explicitly"
  
  # Rigueur scientifique
  - "Every prediction MUST be quantitative with units and bounds"
  - "Every variable MUST have a defined measurement method"
  - "The null hypothesis MUST be explicitly stated for each prediction"
  - "Boundary conditions MUST be specified"
  
  # Honnêteté
  - "If the panel verdict is 'reject', do NOT generate a brief"
  - "Counter-evidence MUST be prominently featured, not buried"
  - "Confidence levels MUST reflect genuine uncertainty"
  - "The Contrarian reviewer's concerns MUST be addressed, not dismissed"
  
  # Qualité
  - "Briefs with fewer than 5 verified references are flagged as 'low-evidence'"
  - "Briefs where novelty_verdict != 'novel' are flagged as 'low-novelty'"
  - "Maximum 2 revision cycles before final verdict"
```

---

## 14. Roadmap d'implémentation

### Sprint 1 (Jour 1-2) — Foundation
- [ ] Créer le module `semantic_scholar.py` (client API avec retry/cache)
- [ ] Créer la table `briefs` dans SQLite
- [ ] Implémenter le Literature Grounding Agent
- [ ] Test : lancer le grounding sur l'hypothèse catalyseurs auto-réparants (🔥 existante)

### Sprint 2 (Jour 3) — Core Agents
- [ ] Implémenter le Hypothesis Sharpening Agent
- [ ] Implémenter l'Experimental Protocol Agent
- [ ] Test end-to-end grounding → sharpening → protocol

### Sprint 3 (Jour 4) — Review & Brief
- [ ] Implémenter les 5 reviewer personas + meta-reviewer
- [ ] Implémenter la boucle de révision
- [ ] Implémenter le Research Brief Generator (markdown)
- [ ] Setup pandoc + template LaTeX pour PDF

### Sprint 4 (Jour 5) — Integration & UI
- [ ] Intégrer le sous-graphe dans le pipeline L0 LangGraph
- [ ] Créer la page Streamlit "Research Briefs"
- [ ] Créer la page "Brief Detail"
- [ ] Mettre à jour le Dashboard
- [ ] Test complet : collision → 🔥 → brief PDF

### Sprint 5 (Jour 6-7) — Calibration & Polish
- [ ] Lancer 10 hypothèses 🔥 à travers le pipeline
- [ ] Calibrer les prompts sur les résultats
- [ ] Ajuster les seuils (novelty, panel scores)
- [ ] Human review des 10 briefs
- [ ] Itérer sur le template du brief basé sur le feedback

---

## 15. Métriques de succès

| Métrique | Cible | Mesure |
|----------|-------|--------|
| Taux de refs vérifiées | 100% | Aucune ref non trouvée sur Semantic Scholar |
| Novelty score moyen des briefs publiés | > 0.7 | Score du Literature Grounding |
| Taux de kill au grounding | 20-40% | Hypothèses tuées par la littérature |
| Panel consensus score moyen | > 6/10 | Moyenne des briefs publiés |
| Temps de génération d'un brief | < 5 min | Horloge end-to-end |
| Coût par brief | < $0.10 | Tracking DeepSeek tokens |
| Qualité perçue (review humain) | > 7/10 | Note Bac sur 10 briefs |
| Taux de conversion brief → intérêt chercheur | > 10% | Phase produit ultérieure |
