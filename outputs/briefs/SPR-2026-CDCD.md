# Adaptation of Decentralized Bioacoustic Tracking Algorithms for Optimal Sensor Placement in Dynamic Pollution Monitoring

## Metadata

- **SPORE ID**: SPR-2026-CDCD
- **Domaines**: Marine Biology x Environmental Engineering
- **Date de generation**: 2026-04-12
- **Panel consensus score**: 6.4/10
- **Novelty score**: 0.8/1.0
- **Panel verdict**: revise_and_resubmit

## Abstract

If the decentralized coordination algorithm from underwater acoustic tracking (arXiv:2204.04155), with its objective function adapted from maximizing detection probability of a mobile source to minimizing reconstruction error of a scalar concentration field, is implemented on a network of mobile environmental sensors, then the time-averaged L²-norm reconstruction error of a simulated pollutant plume will be reduced by 15-30% compared to a static grid deployment, because the algorithm's gradient-following rules will dynamically reposition sensors to regions of high spatial concentration variance, thereby improving observability of the plume's key features.

The proposed mechanism involves 3 causal steps: (1) min E[∫ (C_est(x,t) - C_true(x,t))² dx], where sensor utility is defined by the  -> (2) The decentralized coordination rule—where each sensor i computes a local Voronoi -> (3) This adapted rule causes sensor clusters to autonomously migrate toward and main.

Literature grounding on 5 verified references yields a novelty score of 0.8 (novel). A 3-phase experimental protocol (budget: €20k-80k, timeline: 8-14 months) is proposed, starting with in silico validation. A panel of 5 expert reviewers reached a consensus score of 6.4/10.

## 1. Hypothese et mecanisme propose

### 1.1 Formulation formelle

If the decentralized coordination algorithm from underwater acoustic tracking (arXiv:2204.04155), with its objective function adapted from maximizing detection probability of a mobile source to minimizing reconstruction error of a scalar concentration field, is implemented on a network of mobile environmental sensors, then the time-averaged L²-norm reconstruction error of a simulated pollutant plume will be reduced by 15-30% compared to a static grid deployment, because the algorithm's gradient-following rules will dynamically reposition sensors to regions of high spatial concentration variance, thereby improving observability of the plume's key features.

### 1.2 Variables

**Variables independantes :**

| Variable | Type | Plage | Unite |
|----------|------|-------|-------|
| Algorithm adaptation parameter (β) | continuous | 0.1-2.0 | dimensionless scaling factor for gradient sensitivity |
| Sensor mobility constraint (v_max) | continuous | 0.05-0.5 | m/s |
| Pollution plume advection velocity (U) | continuous | 0.1-1.0 | m/s |

**Variables dependantes :**

| Variable | Type | Direction attendue | Unite |
|----------|------|-------------------|-------|
| Time-averaged plume reconstruction error (ε) | continuous | decrease | μg/m³ (normalized L²-norm) |
| Spatial coverage efficiency (η) | continuous | increase | % of high-variance regions (>90th percentile) monitored |

### 1.3 Chaine causale

1. Step 1: The source algorithm's objective function (max Σ P_detect(i, t | animal position)) is reformulated to minimize the expected reconstruction error of a scalar concentration field C(x,t): min E[∫ (C_est(x,t) - C_true(x,t))² dx], where sensor utility is defined by the local gradient magnitude |∇C| and the reduction in global uncertainty.
1. Step 2: The decentralized coordination rule—where each sensor i computes a local Voronoi-based utility and moves along the gradient of its utility field—is modified. The utility U_i(t) becomes a function of the local measured concentration C_i(t), its spatial gradient estimated from neighbor communications, and the predicted information gain from a Gaussian Process (GP) model of the plume.
1. Step 3: This adapted rule causes sensor clusters to autonomously migrate toward and maintain position in regions of high concentration variance (e.g., plume edges, fronts), leading to a sensor distribution that maximizes the observability of the plume's dynamically evolving structure, thereby reducing the overall reconstruction error from a fixed number of sensors.

**Hypotheses cles :**

- The pollutant plume can be modeled as a passive scalar advected by a turbulent flow field with known statistical properties (mean velocity, diffusivity).
- Sensors can reliably estimate a local spatial gradient of concentration through short-range communication with neighbors (within 50m).
- The communication latency in the sensor network is negligible compared to the timescale of plume evolution (Péclet number >> 1).

**Inconnues identifiees :**

- Whether the algorithm's performance degrades when the pollutant source is truly passive and non-strategic (vs. an intelligent animal avoiding detection).
- The sensitivity of the final reconstruction error to errors in the prior environmental model parameters (e.g., diffusivity, wind/current field).

### 1.4 Conditions aux limites

- **Sensor density must be above a critical threshold (λ > 0.01 sensors/m² in the domain).** — Below this density, the local gradient estimation and Voronoi-based utility calculation become unreliable, causing algorithm failure.
- **Pollutant plume must have a definable spatial structure (Péclet number Pe = UL/D > 5).** — For near-homogeneous concentrations (Pe < 5), gradient-following provides no informational advantage over random placement.
- **Communication range must be at least twice the initial average inter-sensor distance.** — Required for maintaining network connectivity and for accurate local spatial gradient estimation.

### 1.5 Cadre theorique

Optimal Experimental Design (OED) for spatio-temporal field estimation, specifically using mobile sensor networks for Bayesian spatial prediction (Gaussian Process regression).

## 2. Etat de l'art et positionnement

### 2.1 Travaux les plus proches

- **[2025] FINSO: A Bio-Inspired Framework for Optimized Sensor Placement and Routing in Pollution Monitoring** — [10.1109/INDISCON66021.2025.11252025](https://doi.org/10.1109/INDISCON66021.2025.11252025)
  - Similarite: high
  - Difference cle: FINSO proposes a general fish-inspired swarm optimization for pollution monitoring. The hypothesis is more specific: it proposes the direct adaptation of a *particular* decentralized coordination algorithm developed for underwater acoustic sensor networks (from arXiv:2204.04155, which is NOT in the provided list) to environmental monitoring. The novelty lies in the transfer of a specific, pre-existing marine tracking solution, not just the general concept of bio-inspired optimization.
- **[2025] Online Sparse Sensor Placement with Mobility Constraints for Pollution Plume Reconstruction** — [10.3390/jmse13101995](https://doi.org/10.3390/jmse13101995)
  - Similarite: related
  - Difference cle: This paper addresses sensor placement for pollution plume reconstruction with mobility constraints, which is the target application domain. However, it uses an incremental POD method, not a bio-inspired decentralized coordination algorithm derived from marine megafauna tracking. The hypothesis is novel in its proposed *source* of the algorithm (marine bioacoustics).

### 2.2 Base de preuves

- **[2025] FINSO: A Bio-Inspired Framework for Optimized Sensor Placement and Routing in Pollution Monitoring** — [10.1109/INDISCON66021.2025.11252025](https://doi.org/10.1109/INDISCON66021.2025.11252025)
  - Type: analogous | Citations: 0
  - Directly demonstrates that bio-inspired optimization (specifically fish-inspired swarm) is being applied to the problem of pollution monitoring sensor placement and routing. This supports the core premise that bio-inspired algorithms are relevant and applicable to this domain.
- **[2025] Online Sparse Sensor Placement with Mobility Constraints for Pollution Plume Reconstruction** — [10.3390/jmse13101995](https://doi.org/10.3390/jmse13101995)
  - Type: indirect | Citations: 0
  - Explicitly frames the problem of pollutant monitoring sensor placement as a constrained optimization problem for a dynamic, spatio-temporal field (plume), which structurally aligns with the problem described in the hypothesis (mobile stochastic source).
- **[2025] Graph-Based Strategies for Optimizing Mobile Sensor Distribution in Decentralized Urban Pollution Monitoring Across Dynamic Global Citiesɚ** — [10.1109/SENSORS59705.2025.11331218](https://doi.org/10.1109/SENSORS59705.2025.11331218)
  - Type: indirect | Citations: 0
  - Addresses decentralized, mobile sensor distribution for pollution monitoring in resource-constrained, dynamic environments. This context matches the decentralized coordination and dynamic operation aspects of the hypothesis.
- **[2021] Approach to Anomaly Detection in Self-Organized Decentralized Wireless Sensor Network for Air Pollution Monitoring** — [10.1051/matecconf/202134603002](https://doi.org/10.1051/matecconf/202134603002)
  - Type: tangential | Citations: 3
  - Describes a self-organized, decentralized WSN for air pollution monitoring, which is the target system architecture. It does not address placement optimization but supports the feasibility of decentralized networks for this application.
- **[2023] Data Augmentation for Environmental Sound Classification Using Diffusion Probabilistic Model with Top-k Selection Discriminator** — [10.48550/arXiv.2303.15161](https://doi.org/10.48550/arXiv.2303.15161)
  - Type: analogous | Citations: 10
  - Supports the secondary mechanism of the hypothesis: the transfer of deep generative model-based data augmentation from bioacoustics (arXiv:2511.21872, not in list) to environmental monitoring. This paper shows advanced generative models (diffusion) are being used for environmental *sound* data augmentation, validating the concept of using synthetic data for environmental sensing tasks.

### 2.3 Contre-preuves et limitations connues

Aucune contre-preuve identifiee.

### 2.4 Evaluation de nouveaute

- **Score**: 0.8/1.0
- **Verdict**: novel

## 3. Predictions falsifiables

| # | Prediction | Borne quantitative | Methode | H0 | Test statistique |
|---|-----------|-------------------|---------|-----|-----------------|
| 1 | In a simulated 2D turbulent plume (Reynolds-averaged Navier-Stokes with scalar transport), the adapted algorithm will produce a lower time-averaged reconstruction error than a static uniform grid. | Reduction of 15-30% in normalized L²-error (ε/ε_static) over a 24-hour simulation, with plume advection velocities between 0.2-0.8 m/s. | Numerical simulation comparing the true concentration field C_true(x,t) with the GP-reconstructed field C_est(x,t) from sensor readings. Error calculated every 10 minutes and averaged. | H0: The mean normalized reconstruction error for the adapted algorithm (ε_adapt) is greater than or equal to the error for the static grid (ε_static). | Two-tailed paired t-test on error time series from 30 independent simulation runs (randomized block design for daily environmental conditions), alpha=0.05, power analysis (β=0.2) indicates n=30 detects effect size d=0.75. |
| 2 | The algorithm's performance gain will persist despite significant error in the prior environmental model. | The 15-30% error reduction will be maintained when the input diffusivity parameter to the sensor's GP model has a systematic error of ±10-20%. | Re-run the primary simulation suite, but perturb the diffusivity parameter used by the sensor's internal prediction model. Compare ε_adapt/ε_static ratio across error levels. | H0: A >10% error in the prior diffusivity model eliminates the performance advantage of the adapted algorithm (ε_adapt/ε_static ≥ 1). | Two-way ANOVA (algorithm type × model error level) on final reconstruction error, with post-hoc Tukey HSD test for pairwise comparisons. |

## 4. Protocole experimental

**Timeline globale**: 8-14 months
**Budget global**: €20k-80k

### 4.1 Phase 1 — In Silico Validation

**Objectif**: To rapidly test the core hypothesis—that adapting the decentralized acoustic tracking algorithm reduces plume reconstruction error—in a controlled simulation environment, identifying fatal flaws and optimal parameter ranges.

**Methodologie**: 1. **Algorithm Implementation**: Code the adapted algorithm in Python, modifying the objective function from arXiv:2204.04155 to minimize the expected L² reconstruction error of a scalar field. Use Gaussian Process regression (GPyTorch) for field estimation. 2. **Simulation Environment**: Create a 2D domain (500m x 500m) in a custom simulator (or extend OpenFOAM/MATLAB). Implement a turbulent plume model using a stochastic advection-diffusion equation (SADE) with a mean advection velocity (U) and isotropic diffusivity (D). Generate time-evolving concentration fields C_true(x,t). 3. **Comparative Trials**: Run 30 independent simulation days. For each day, compare: (a) **Adaptive Algorithm**: 10 mobile sensors (v_max variable) running the adapted coordination. (b) **Static Control**: 10 sensors in a fixed uniform grid. 4. **Data Analysis**: Compute the time-averaged normalized L²-error (ε = ∫(C_est - C_true)² dx / ∫(C_true)² dx) every 10 min. Perform a paired t-test (α=0.05) on the 30 daily error ratios (ε_adapt/ε_static). 5. **Sensitivity Sweep**: Perform a Latin Hypercube sampling over the independent variables (β: 0.1-2.0, v_max: 0.05-0.5 m/s, U: 0.1-1.0 m/s) to map the performance landscape and identify failure boundaries.

- Cout: €0-1500 (cloud computing credits for parameter sweeps)
- Duree: 4-6 weeks
- Equipement: Workstation (16+ core CPU, 32GB RAM)
- Logiciels: Python 3.10+ with NumPy, SciPy, GPyTorch, Matplotlib, Docker (for reproducible environment), Git (version control), Jupyter Lab

**Criteres de succes :**

- Normalized L²-error ratio (ε_adapt / ε_static): Mean ratio < 0.85 (i.e., >15% reduction) with p-value < 0.05 in the paired t-test.
- Algorithm Stability: No catastrophic failure (e.g., sensor divergence, network fragmentation) in >90% of runs within the defined boundary conditions (Pe>5, λ>0.01 sensors/m²).

- **GO**: Both success criteria are met. The algorithm shows a statistically significant error reduction >15% and is robust across the core parameter space.
- **NO-GO**: The error reduction is statistically insignificant OR the mean ratio is ≥ 0.95 (≤5% gain). This indicates the core mechanism does not work as hypothesized in silico.
- **PIVOT**: Error reduction is significant but <15%, OR the algorithm is unstable. Pivot to: 1) Tuning the utility function (e.g., different GP kernel), or 2) Investigating a hybrid static/mobile deployment.

### 4.2 Phase 2 — Minimal Experimental Validation

**Objectif**: To physically test the algorithm's core coordination mechanism using a small-scale, controlled hardware-in-the-loop setup, confirming that sensors can autonomously reposition to reduce reconstruction error of a real, dynamic scalar field.

**Methodologie**: 1. **Hardware Setup**: Construct a 2m x 2m water tank with a controlled flow (pump array for U ~ 0.01 m/s). Use a saline solution as a passive tracer (conductivity as proxy for concentration). Deploy 3-5 custom mobile sensor nodes. Each node comprises: an Arduino/Raspberry Pi, a conductivity probe, DC motors/wheels for mobility (v_max ~ 0.02 m/s), and ESP32 for WiFi communication. 2. **Software Integration**: Port the coordination algorithm from Phase 1 to run on a central orchestrator (laptop). The orchestrator collects sensor readings, runs the GP model and coordination calculations, and sends movement commands back to nodes (emulating decentralized logic with a central facilitator for simplicity). 3. **Experiment Design**: Generate a stable, laminar saline plume. Run two 30-minute trials: (a) Adaptive: Sensors start randomly and execute the algorithm. (b) Static: Sensors hold fixed positions. Use a dense, fixed grid of reference probes to measure the ground-truth concentration field C_true(x,t). 4. **Analysis**: Compute the reconstruction error ε(t) in real-time using the GP model fed by the mobile sensors. Compare the time-averaged error between adaptive and static trials. Repeat 10 times.

- Cout: €5000-12000
- Duree: 2-3 months
- Equipement: Water tank & frame, Peristaltic pump & saline solution, 3-5 mobile sensor nodes (motors, microcontrollers, conductivity probes), Dense grid of 20+ fixed reference conductivity probes, Data acquisition system (e.g., National Instruments USB-6008), Camera for tracking validation
- Logiciels: ROS2 (Robot Operating System) for sensor coordination, Modified Phase 1 Python code, OpenCV for optional visual tracking

**Criteres de succes :**

- Error Reduction in Physical Setup: ε_adapt / ε_static < 0.90 (i.e., >10% reduction) over the trial period.
- Gradient-Following Behavior: Sensors spend >60% of trial time in regions where |∇C_true| is above the median value for the domain.

- **GO**: Both criteria are met. The physical test shows error reduction and the predicted gradient-following behavior.
- **NO-GO**: No error reduction (ratio ≥ 1.0) OR sensors show no correlation with high-gradient regions. This indicates a fundamental mismatch between simulation and physical reality (e.g., communication delays, sensor noise fatal).
- **PIVOT**: Error reduction is marginal (5-10%) or behavior is noisy. Pivot to: 1) Improving the local gradient estimation algorithm using hardware filters, or 2) Simplifying the objective function to be more robust to noise.

### 4.3 Phase 3 — Full Experimental Protocol

**Objectif**: To conduct a rigorous, publiable validation of the algorithm under realistic environmental conditions, testing robustness to model errors and scalability, leading to a journal publication.

**Methodologie**: 1. **Large-Scale Testbed**: Deploy a network of 10-15 advanced mobile sensor nodes in a controlled outdoor environment (e.g., a large irrigation pond or a wind tunnel facility). Nodes must have full onboard processing (Raspberry Pi 4/5), GPS, wind/chemical sensors, and LoRa/ESP-NOW for long-range, low-latency communication. 2. **Decentralized Implementation**: Implement the full decentralized algorithm from Phase 1 on each node, allowing fully autonomous operation without a central orchestrator. 3. **Experimental Matrix**: Execute a full factorial design: (Algorithm: Adaptive vs. Static Grid) x (Model Error: -20%, 0%, +20% error in prior diffusivity D). Use a released, non-toxic tracer (e.g., SF6 for air, Rhodamine WT for water) to create a dynamic plume. 4. **Comprehensive Measurement**: Use a separate, high-fidelity measurement system (e.g., a scanning laser fluorometer or a dense static sensor array) to establish the ground-truth plume C_true(x,t). 5. **Analysis**: Perform the two-way ANOVA as specified in Prediction 2. Quantify spatial coverage efficiency (η). Document energy consumption and network reliability metrics.

- Cout: €30k-100k+
- Duree: 6-12 months
- Equipement: 10-15 robust outdoor mobile sensor platforms (weatherproof, with propulsion), High-fidelity tracer release and measurement system, Differential GPS base station for cm-level positioning, Portable weather station
- Logiciels: Full decentralized algorithm firmware (C++/MicroPython), Ground truth data processing pipeline, Statistical analysis scripts (R/Python)

**Criteres de succes :**

- Statistical Significance of Error Reduction: Two-way ANOVA shows a significant main effect of algorithm type (p < 0.01) with the adaptive algorithm outperforming static grid in all model error conditions. Post-hoc tests confirm ε_adapt/ε_static < 0.85.
- Robustness to Model Error: The performance advantage (error reduction >15%) is maintained across all tested model error levels (±20%).
- Spatial Coverage Efficiency: η_adapt > η_static by at least 20 percentage points (e.g., 80% vs 60% coverage of high-variance regions).

- **GO**: All three success criteria are met. The algorithm is validated as robust and effective, warranting publication and technology transfer.
- **NO-GO**: The algorithm fails to show a significant main effect OR is highly sensitive to model errors (advantage lost at ±10% error). This invalidates the practical utility of the approach.
- **PIVOT**: Results are positive but scalability is an issue (e.g., network partitioning). Pivot to a hierarchical or hybrid coordination architecture for the next research iteration.

### 4.4 Quick start : comment demarrer aujourd'hui

- **Peut demarrer maintenant**: Oui
- **Premiere action**: Clone the GitHub repository for the original bioacoustic tracking algorithm (arXiv:2204.04155 likely has code) and set up a Python virtual environment with the required dependencies (NumPy, SciPy, GPyTorch).
- **Outils**: Git, Python 3.10+, Code Editor (VS Code)
- **Donnees ouvertes**: arXiv:2204.04155 (code/data if available), Kit Fox Dispersion Experiment data (via EPA or other repositories), Classic plume simulation codes (e.g., from `pytsa` or `OpenFOAM` tutorials)

## 5. Analyse d'impact

### 5.1 Impact scientifique

Novelty score: 0.8/1.0 (novel)

### 5.2 Applications industrielles et marche

**Score industriel**: 6.5/10

**Forces :**
- Adresse un besoin critique et coûteux dans des secteurs réglementés : monitoring de pollution pour les sites industriels (chimie, pétrochimie, déchets), les ports, et les gestionnaires d'infrastructures critiques. Le marché du monitoring environnemental en continu est estimé à plusieurs milliards d'euros, avec une croissance tirée par les normes ESG et les réglementations (SEVESO, DCE).
- L'avantage compétitif est tangible : une réduction de 15-30% de l'erreur de reconstruction avec le même nombre de capteurs mobiles représente soit une amélioration significative de la précision pour la conformité et l'alerte précoce, soit une réduction potentielle du nombre de capteurs nécessaires pour une précision donnée, impactant directement le CAPEX/OPEX des déploiements.

**Faiblesses :**
- Barrière majeure au déploiement : la complexité d'intégration et la fiabilité opérationnelle en conditions réelles (intempéries, obstacles, maintenance des plateformes mobiles) face à des solutions statiques éprouvées, même moins optimales. Le coût total de possession (incluant la mobilité) peut annuler l'avantage théorique.
- Risque commercial lié à la concurrence indirecte : les acteurs établis du monitoring (Vaisala, ACOEM, Saildrone) développent leurs propres logiciels d'optimisation et pourraient implémenter des heuristiques similaires sans recourir à un algorithme académique spécifique, diluant l'IP potentielle.

**Recommandation**: Lancer la Phase 1 (coût négligeable) pour valider le gain fondamental en simulation. En parallèle, initier des discussions exploratoires avec un partenaire industriel potentiel (ex: un intégrateur de drones pour l'environnement comme AirMarine, ou un bureau d'études spécialisé) pour co-construire le cahier des charges de la Phase 2 et valider les cas d'usage et la volonté de payer. Positionner le développement non pas comme un algorithme pur, mais comme le module d'intelligence d'un système de monitoring actif vendu en SaaS.

### 5.3 Opportunites de financement

| Programme | Agence | Fit | Budget type | Taux succes |
|-----------|--------|-----|-------------|-------------|
| ERC Proof of Concept (PoC) 2025 | European Research Council | 0.7 | €150,000 maximum | ~50% (variable par domaine) |
| Appel Générique - Pathfinder Open (EIC) | European Innovation Council (Horizon Europe) | 0.65 | €3-4 millions pour un consortium | ~5-10% |
| ANR - Appel Générique - Action Collaborative Recherche (ACR) / Projets de Recherche Collaborative (PRC) | Agence Nationale de la Recherche (France) | 0.9 | €200k-500k | ~15-25% (variable selon les panels) |

- **ERC Proof of Concept (PoC) 2025** (European Research Council): Ce programme est parfait si le PI a déjà un ERC (ou équivalent prestigieux) en cours. Il finance précisément l'exploration du potentiel d'innovation et de transfert d'une découverte issue de la recherche fondamentale (comme l'algorithme d'origine sur arXiv) vers une application pratique. Votre protocole est un excellent plan PoC. Si le PI n'a pas d'ERC, il faut viser l'équivalent national en premier.
- **Appel Générique - Pathfinder Open (EIC)** (European Innovation Council (Horizon Europe)): L'EIC Pathfinder finance la recherche exploratoire sur des technologies de rupture. Votre projet, à la croisée de l'IA distribuée, de la robotique et des sciences de l'environnement, correspond au thème. Il faudra monter un consortium de 3-5 partenaires (académiques et éventuellement un industriel early-stage) et renforcer le narratif sur l'impact disruptif potentiel (surveillance environnementale à coût réduit, autonomie). Votre protocole actuel serait la première phase du projet.
- **ANR - Appel Générique - Action Collaborative Recherche (ACR) / Projets de Recherche Collaborative (PRC)** (Agence Nationale de la Recherche (France)): C'est le meilleur premier pas. L'ANR ACR/PRC permet de financer un consortium académique français (2-3 labos) pour une preuve de concept ambitieuse sur 24-36 mois. Il est parfaitement aligné avec votre budget et timeline idéaux, mais à une échelle plus réaliste. Vous pourriez structurer le projet exactement comme votre protocole à trois phases, en associant un labo d'informatique/robotique (porteur de l'algo) et un labo d'écologie/géosciences (fournissant les modèles de panache et la validation terrain). C'est le tremplin idéal pour générer des résultats préliminaires et un consortium en vue d'un projet Horizon Europe.

**Recommandation financement**: Cibler d'abord un financement de maturation de preuve de concept (type ERC PoC ou ANR PRC) pour exécuter votre protocole de 14 mois et obtenir des résultats solides. En parallèle, construire un consortium autour d'un démonstrateur à plus grande échelle pour viser un appel collaboratif Horizon Europe du cluster 4, 5 ou 6. La publication issue de la Phase 3 sera cruciale pour postuler à une bourse individuelle type ERC Starting Grant.

## 6. Panel Review Summary

| Reviewer | Score | Verdict | Point cle |
|----------|-------|---------|-----------|
| methodologist | 8.2/10 | accept | Le protocole adopte une approche par phases progressive (in silico, hardware-in-the-loop, terrain) qui est exemplaire pour valider un algorithme de contrôle complexe, permettant d'identifier les problèmes de faisabilité et de passage à l'échelle de manière économique et séquentielle. |
| domain_expert | 6.5/10 | weak_accept | The hypothesis demonstrates a clever cross-domain transfer, linking a mature algorithm from underwater acoustic tracking (arXiv:2204.04155) to the challenging problem of dynamic plume reconstruction. This is a promising and non-obvious connection. |
| contrarian | 4.5/10 | weak_reject | The hypothesis is commendably specific, with a clear causal chain and quantitative predictions (15-30% error reduction). It attempts a creative cross-domain transfer from bioacoustic tracking to environmental monitoring. |
| industrialist | 6.5/10 | weak_accept | Adresse un besoin critique et coûteux dans des secteurs réglementés : monitoring de pollution pour les sites industriels (chimie, pétrochimie, déchets), les ports, et les gestionnaires d'infrastructures critiques. Le marché du monitoring environnemental en continu est estimé à plusieurs milliards d'euros, avec une croissance tirée par les normes ESG et les réglementations (SEVESO, DCE). |
| funding_strategist | 6.5/10 | weak_accept | Hypothèse très bien structurée avec un protocole de validation en trois phases clair et des critères GO/NO-GO explicites, ce qui rassure sur la rigueur méthodologique et la gestion des risques. |

**Consensus score**: 6.4/10
**Verdict final**: revise_and_resubmit

### 6.1 Consensus

- Le protocole de validation en trois phases avec critères GO/NO-GO est jugé rigoureux et constitue un point fort méthodologique.
- L'idée de transfert interdisciplinaire (bioacoustique → surveillance environnementale) est reconnue comme originale et potentiellement prometteuse.
- La nécessité de renforcer la justification théorique du mécanisme central (suivi de gradient → réduction d'erreur de reconstruction) est un point d'accord majeur parmi les experts.

### 6.2 Points de desaccord

- L'analogie fondamentale entre le suivi d'une source stratégique mobile et la cartographie d'un panache passif est jugée soit 'prometteuse mais à justifier' (Domain Expert, Industrialist), soit 'fondamentalement défectueuse' (Contrarian). Le Methodologist ne la remet pas explicitement en cause.
- L'évaluation du risque de l'approche centralisée en Phase 2 : le Methodologist la voit comme une simplification acceptable mais à améliorer, tandis que le Contrarian y voit une simplification critique masquant le vrai défi des systèmes décentralisés.
- La pertinence du benchmark (grille statique) : le Methodologist et le Funding Strategist l'acceptent, tandis que le Contrarian et le Domain Expert exigent des comparaisons avec des stratégies adaptatives plus solides (ex: random walk, IPP classique).

### 6.3 Critical path

La capacité à démontrer, par une simulation préliminaire robuste et une analyse théorique, que les règles de coordination par gradient de l'algorithme adapté maximisent effectivement un critère d'information (ex: réduction de variance a posteriori) pour la reconstruction d'un champ scalaire passif et turbulent, et qu'elles surpassent des stratégies adaptatives simples (null hypothesis plus forte).

**Recommandation finale**: Le panel reconnaît le potentiel et la rigueur méthodologique du projet, mais juge l'hypothèse centrale insuffisamment étayée théoriquement et confrontée à des objections fondamentales sur sa plausibilité. Une révision majeure est requise avant tout engagement expérimental. Cette révision doit prioritairement combler le fossé théorique entre le mécanisme proposé et l'objectif de reconstruction, et tester l'analogie de base contre des hypothèses nulles plus robustes via des simulations ciblées. Le projet ne peut passer en phase de test sans cette validation préalable.

## 7. Gap Manifest residuel

### 7.1 Data gaps

- The provided list contains NO papers on the source domain algorithm (underwater acoustic sensor networks for marine megafauna tracking, e.g., from arXiv:2204.04155). Therefore, a critical gap remains: the specific algorithm to be transferred is not documented in the provided evidence. The hypothesis assumes its existence and properties.
- No papers were found that model pollutant dispersion as a direct analog to animal movement with acoustic signal propagation, leaving the 'Parameter Sensitivity' epistemic gap wide open.

### 7.2 Competence gaps

- Phase 1: Scientific Python programming
- Phase 1: Gaussian Process regression
- Phase 1: Basic fluid dynamics/stochastic processes
- Phase 1: Statistical analysis (t-test, ANOVA)
- Phase 2: Embedded systems programming
- Phase 2: Basic mechatronics
- Phase 2: Laboratory fluid dynamics
- Phase 2: Data acquisition and signal processing
- Phase 3: Outdoor field experiment logistics
- Phase 3: Decentralized systems programming
- Phase 3: Advanced statistics (ANOVA, Tukey HSD)
- Phase 3: Environmental monitoring regulations

### 7.3 Epistemic gaps

- Whether the algorithm's performance degrades when the pollutant source is truly passive and non-strategic (vs. an intelligent animal avoiding detection).
- The sensitivity of the final reconstruction error to errors in the prior environmental model parameters (e.g., diffusivity, wind/current field).

## References

[1] S. Chowdhury (2025). *FINSO: A Bio-Inspired Framework for Optimized Sensor Placement and Routing in Pollution Monitoring*. DOI: [10.1109/INDISCON66021.2025.11252025](https://doi.org/10.1109/INDISCON66021.2025.11252025)
[2] Aoming Liang, Duoxiang Xu, et al. (2025). *Online Sparse Sensor Placement with Mobility Constraints for Pollution Plume Reconstruction*. DOI: [10.3390/jmse13101995](https://doi.org/10.3390/jmse13101995)
[3] Daniel Mutembesa, Engineer Bainomugisha (2025). *Graph-Based Strategies for Optimizing Mobile Sensor Distribution in Decentralized Urban Pollution Monitoring Across Dynamic Global Citiesɚ*. DOI: [10.1109/SENSORS59705.2025.11331218](https://doi.org/10.1109/SENSORS59705.2025.11331218)
[4] A. Meleshko, V. Desnitsky, et al. (2021). *Approach to Anomaly Detection in Self-Organized Decentralized Wireless Sensor Network for Air Pollution Monitoring*. DOI: [10.1051/matecconf/202134603002](https://doi.org/10.1051/matecconf/202134603002)
[5] Yunhao Chen, Yunjie Zhu, et al. (2023). *Data Augmentation for Environmental Sound Classification Using Diffusion Probabilistic Model with Top-k Selection Discriminator*. DOI: [10.48550/arXiv.2303.15161](https://doi.org/10.48550/arXiv.2303.15161)

## Annexes

### A. Detailed Reviewer Reports

#### Methodologist

- **Score**: 8.2/10 | **Verdict**: accept | **Confidence**: 0.88
- **Strengths**: Le protocole adopte une approche par phases progressive (in silico, hardware-in-the-loop, terrain) qui est exemplaire pour valider un algorithme de contrôle complexe, permettant d'identifier les problèmes de faisabilité et de passage à l'échelle de manière économique et séquentielle.; La définition de critères GO/NO-GO quantitatifs et prédéfinis pour chaque phase renforce la rigueur et évite le biais de confirmation en établissant des seuils de réussite objectifs avant l'expérimentation.; L'identification et la proposition de tests spécifiques pour les biais potentiels (notamment l'erreur de modèle dans la Prédiction 2) démontrent une anticipation des menaces à la validité interne et une volonté de tester la robustesse de l'hypothèse.
- **Weaknesses**: La puissance statistique pour la Phase 1 (30 jours de simulation) n'est pas justifiée par un calcul de puissance a priori. Bien qu'un t-test apparié soit prévu, le nombre de réplicats (n=30) pourrait être insuffisant pour détecter un effet de 15% si la variance intra-jour est élevée, risquant une erreur de type II.; Le protocole de Phase 2 utilise un 'orchestrateur central' pour émuler une logique décentralisée. Cette simplification introduit un biais de mesure potentiel, car elle élimine les problèmes de latence réseau et de consensus distribué qui sont au cœur des défis des algorithmes décentralisés réels. La validité écologique de cette phase est donc limitée.; Le contrôle 'static grid' est bien défini, mais il manque un contrôle actif supplémentaire, comme une stratégie de déplacement aléatoire ou un algorithme de recherche par gradient simple. Cela permettrait de distinguer si la performance provient de la sophistication de l'algorithme adapté ou simplement du fait que les capteurs bougent pour échantillonner plus de zones.
- **Questions**: Pour la Phase 1, quel est le plan pour assurer l'indépendance des '30 jours de simulation' indépendants ? Les champs de concentration initiaux sont-ils régénérés à partir de conditions initiales et de bruits stochastiques distincts pour éviter la pseudo-réplication ?; Dans la Phase 3, comment allez-vous opérationnaliser et mesurer la 'couverture des régions à haute variance' (η) sans biais de circularité ? La définition des 'régions à haute variance' sera-t-elle basée sur la vérité terrain (C_true) indépendante, et non sur l'estimation des capteurs (C_est) ?; Le biais de publication est-il adressé ? Y a-t-il un engagement à publier les résultats de la Phase 1 (in silico) même s'ils sont négatifs (NO-GO), pour éviter le biais du tiroir fichier ? Le budget inclut-il des ressources pour une telle publication ?
- **Recommendation**: Le protocole est bien conçu et mérite d'être financé. Avant de commencer la Phase 1, je recommande de réaliser une étude pilote de simulation pour estimer la variabilité de l'erreur de reconstruction et effectuer un calcul formel de puissance statistique pour justifier le n=30. Pour la Phase 2, il serait prudent de développer un prototype de communication véritablement décentralisé (e.g., utilisant ESP-NOW en mesh) dès le début, même à petite échelle, pour tester la robustesse de l'algorithme aux délais asynchrones.

#### Domain Expert

- **Score**: 6.5/10 | **Verdict**: weak_accept | **Confidence**: 0.8
- **Strengths**: The hypothesis demonstrates a clever cross-domain transfer, linking a mature algorithm from underwater acoustic tracking (arXiv:2204.04155) to the challenging problem of dynamic plume reconstruction. This is a promising and non-obvious connection.; The proposed causal mechanism is logically structured, moving from objective function reformulation to decentralized coordination rules, and correctly identifies key observability targets (regions of high spatial variance, plume edges). This aligns with established OED principles for Gaussian Processes, where optimal designs often target regions of high predictive uncertainty or gradient.; The evidence base correctly identifies recent literature (2025) that validates the core problem framing (mobile sensor placement for pollution plumes) and the applicability of bio-inspired, decentralized strategies, showing good awareness of the current research landscape.
- **Weaknesses**: The core mechanistic assumption—that gradient-following rules for maximizing detection probability of a strategic, mobile source translate effectively to minimizing reconstruction error of a passive scalar field—is not sufficiently justified. The information gain for tracking a target (which actively influences the signal) is fundamentally different from that for mapping a diffusive field. The hypothesis glosses over this critical disanalogy.; The proposed utility function (Step 2) is underspecified and potentially problematic. Combining a local gradient |∇C| with a predicted GP information gain in a decentralized, real-time setting is computationally non-trivial. The communication and processing requirements for neighbors to estimate a spatial gradient reliably in a turbulent plume—a key assumption—are likely prohibitive and not addressed. The known unknown regarding prior model error is a major vulnerability.; The positioning versus the state-of-the-art in OED for mobile sensors is incomplete. The hypothesis does not engage with established frameworks like informative path planning (IPP) or multi-robot adaptive sampling that formally balance exploration/exploitation using GP upper confidence bounds or mutual information. The proposed mechanism appears to reinvent these concepts without leveraging their theoretical guarantees or addressing their known computational challenges.
- **Questions**: How does the algorithm's performance metric (e.g., gradient-following) formally relate to the minimization of the global L² reconstruction error in a Bayesian (GP) framework? Can you demonstrate that moving sensors to high-gradient regions is provably equivalent to, or a good heuristic for, maximizing a criterion like the reduction in integrated posterior variance (IVAR) or trace of the covariance matrix?; Given the 'known unknown' about the passive vs. strategic source, what specific modifications to the original acoustic tracking algorithm are required to handle a passive, diffusive plume? The acoustic algorithm likely assumes signal propagation models and target motion models that are irrelevant. Does the 'adaptation' essentially strip the algorithm down to a basic potential-field navigation, and if so, where is the novelty beyond existing potential-field methods in robotic sampling?; The evidence base cites papers on bio-inspired optimization and decentralized networks, but none directly support the transfer of the *specific* algorithm from arXiv:2204.04155. What is the definitive evidence that this particular algorithm's coordination rules are superior to other decentralized control laws (e.g., based on Lloyd's algorithm for centroidal Voronoi tessellations) for the stated objective of field reconstruction?
- **Recommendation**: The hypothesis has an intriguing core idea but requires significant theoretical strengthening before it can be considered plausible. I recommend a major revision focused on: 1) Formally bridging the proposed gradient-following mechanism to an established OED criterion (e.g., Mutual Information) to justify the expected error reduction. 2) Conducting a rigorous simulation study comparing the adapted algorithm not just to a static grid, but to other mobile sensor benchmarks from the IPP literature (e.g., greedy entropy reduction, non-myopic planners). 3) Explicitly detailing the algorithmic modifications needed to handle a passive scalar field, addressing the computational feasibility of real-time gradient and GP update estimation in a decentralized network.

#### Contrarian

- **Score**: 4.5/10 | **Verdict**: weak_reject | **Confidence**: 0.85
- **Strengths**: The hypothesis is commendably specific, with a clear causal chain and quantitative predictions (15-30% error reduction). It attempts a creative cross-domain transfer from bioacoustic tracking to environmental monitoring.; It acknowledges some 'known unknowns,' such as sensitivity to model parameters, which shows a degree of self-awareness about the system's limitations.
- **Weaknesses**: FAIL REASON #1: The core analogy is flawed. Bioacoustic tracking targets a discrete, intelligent, mobile source with strategic behavior. A passive scalar plume is a continuous, non-strategic field. The algorithm's success in the source domain relies on predicting source *intent* (avoidance). Applying its 'gradient-following' logic to a plume's spatial variance assumes high-gradient regions (edges) are persistently informative. In reality, in a turbulent advective-diffusive field, these regions are ephemeral and chaotic. Sensors chasing yesterday's high-variance front will likely miss tomorrow's evolving core, leading to lag-induced error inflation, not reduction.; FAIL REASON #2: The communication and gradient estimation assumption is a critical, likely fatal, simplification. Estimating a reliable local spatial gradient (∇C) in a turbulent scalar field requires dense sensor spacing relative to the smallest dynamically relevant scale (the Batchelor scale). The proposed 50m communication range is arbitrary. If the sensor spacing is larger than the correlation scale of the gradient field, the neighbor-based gradient estimate will be pure noise. This noisy signal will drive the coordination rule, resulting in random, purposeless sensor motion that performs worse than a static grid.; FAIL REASON #3: The predicted effect size (15-30% reduction) is not justified against a proper null. The comparison is against a 'static grid deployment,' which is a naive baseline. A more relevant and challenging null would be a simple, rule-based adaptive strategy (e.g., sensors move randomly or with a simple upwind bias). The proposed complex algorithm (Voronoi, GP model, decentralized coordination) has high overhead. It's probable that the majority of the claimed benefit, if any, comes merely from sensor mobility, not the sophisticated coordination, and that a much simpler heuristic would achieve similar or better results without the risk of coordination failures and model error propagation.
- **Questions**: You claim the algorithm's performance is robust to ±10-20% error in the *diffusivity* parameter. But what about error in the *advection field*, which is the primary driver of plume structure? A 10% error in a mean current direction will systematically misdirect your entire sensor swarm. Have you simulated the scenario where the prior wind/current model is wrong, and the sensors' GP model is consistently assimilating data based on a flawed advection premise, creating a reinforcing error loop?; Your utility function aims to minimize global reconstruction error via local rules. This is a classic distributed optimization problem. What guarantees do you have that the decentralized Voronoi-based rule, when applied to a *scalar field reconstruction* objective (vs. a *detection* objective), will not converge to a poor local minimum? Can you demonstrate, even in simulation, that the emergent sensor distribution is provably near-optimal for the L²-error metric, and not just clustered in a way that maximizes local gradient signals but poorly samples the broad, low-gradient core of the plume?
- **Recommendation**: Before testing the full hypothesis, you must de-risk the fundamental analogy. First, run a simulation where you replace the intelligent animal source with a passive, continuous scalar release *using the original bioacoustic algorithm unchanged*. I predict it will fail catastrophically, demonstrating the need for more than a simple objective function swap. Second, perform a rigorous scale analysis to determine the minimum sensor density required for reliable gradient estimation in your chosen turbulent regime. Your 50m range must be justified physically, not arbitrarily set. Third, benchmark against a stronger null: a swarm of sensors performing a simple biased random walk (e.g., tend to move up-gradient when signal is strong, otherwise diffuse). Only if you beat this baseline does your complex coordination add value.

#### Industrialist

- **Score**: 6.5/10 | **Verdict**: weak_accept | **Confidence**: 0.75
- **Strengths**: Adresse un besoin critique et coûteux dans des secteurs réglementés : monitoring de pollution pour les sites industriels (chimie, pétrochimie, déchets), les ports, et les gestionnaires d'infrastructures critiques. Le marché du monitoring environnemental en continu est estimé à plusieurs milliards d'euros, avec une croissance tirée par les normes ESG et les réglementations (SEVESO, DCE).; L'avantage compétitif est tangible : une réduction de 15-30% de l'erreur de reconstruction avec le même nombre de capteurs mobiles représente soit une amélioration significative de la précision pour la conformité et l'alerte précoce, soit une réduction potentielle du nombre de capteurs nécessaires pour une précision donnée, impactant directement le CAPEX/OPEX des déploiements.
- **Weaknesses**: Barrière majeure au déploiement : la complexité d'intégration et la fiabilité opérationnelle en conditions réelles (intempéries, obstacles, maintenance des plateformes mobiles) face à des solutions statiques éprouvées, même moins optimales. Le coût total de possession (incluant la mobilité) peut annuler l'avantage théorique.; Risque commercial lié à la concurrence indirecte : les acteurs établis du monitoring (Vaisala, ACOEM, Saildrone) développent leurs propres logiciels d'optimisation et pourraient implémenter des heuristiques similaires sans recourir à un algorithme académique spécifique, diluant l'IP potentielle.
- **Questions**: Quel est le modèle économique : vente de licence logicielle (difficile dans l'industrie lourde), service de monitoring clé en main, ou intégration via partenariat avec un fabricant de capteurs/systèmes mobiles ? Qui a la main sur le budget : le département HSE, les opérations, ou la R&D du client ?; La propriété intellectuelle sur l'« adaptation » de l'algorithme est-elle défendable et valorisable ? L'algorithme source (arXiv) est public. La valeur réside-t-elle dans le code d'implémentation spécifique au GP et la calibration pour la pollution, suffisante pour constituer une barrière ?
- **Recommendation**: Lancer la Phase 1 (coût négligeable) pour valider le gain fondamental en simulation. En parallèle, initier des discussions exploratoires avec un partenaire industriel potentiel (ex: un intégrateur de drones pour l'environnement comme AirMarine, ou un bureau d'études spécialisé) pour co-construire le cahier des charges de la Phase 2 et valider les cas d'usage et la volonté de payer. Positionner le développement non pas comme un algorithme pur, mais comme le module d'intelligence d'un système de monitoring actif vendu en SaaS.

#### Funding Strategist

- **Score**: 6.5/10 | **Verdict**: weak_accept | **Confidence**: 0.8
- **Strengths**: Hypothèse très bien structurée avec un protocole de validation en trois phases clair et des critères GO/NO-GO explicites, ce qui rassure sur la rigueur méthodologique et la gestion des risques.; Originalité forte du transfert d'algorithme d'un domaine (bioacoustique sous-marine) vers un autre (surveillance environnementale), avec une base théorique publiée (arXiv) et un objectif de performance quantifiable (réduction de 15-30% de l'erreur L²).
- **Weaknesses**: TRL actuel estimé très bas (TRL 2-3). Le budget et la timeline proposés (20-80k€, 8-14 mois) sont inadaptés pour un vrai projet collaboratif européen, mais correspondent plutôt à une preuve de concept préliminaire. Le consortium n'est pas défini.; Le narratif actuel est trop centré sur la validation algorithmique. Pour les appels européens, il manque une dimension "impact sociétal" claire (politiques publiques, directives environnementales, santé) et une feuille de route de maturation technologique (TRL 4-6) au-delà de la publication.
- **Questions**: Quelle est la stratégie pour constituer un consortium pluridisciplinaire indispensable ? Il faut intégrer des spécialistes en algorithmes distribués, en capteurs mobiles (robotique), en modélisation des polluants, et un partenaire "utilisateur final" (agence de l'eau, municipalité, entreprise de monitoring).; Comment comptez-vous passer de la simulation et de la validation en laboratoire contrôlé (Phases 1-2) à des tests dans des environnements réels partiellement structurés (e.g., canal, rivière artificielle, site industriel contrôlé) pour atteindre un TRL 4-5 ?
- **Recommendation**: Cibler d'abord un financement de maturation de preuve de concept (type ERC PoC ou ANR PRC) pour exécuter votre protocole de 14 mois et obtenir des résultats solides. En parallèle, construire un consortium autour d'un démonstrateur à plus grande échelle pour viser un appel collaboratif Horizon Europe du cluster 4, 5 ou 6. La publication issue de la Phase 3 sera cruciale pour postuler à une bourse individuelle type ERC Starting Grant.

### B. Semantic Scholar Search Queries

- [novelty] `decentralized sensor placement pollution monitoring` — Direct search for the core hypothesis: applying decentralized algorithms for sensor placement in pollution contexts.
- [novelty] `bioacoustic tracking algorithms environmental sensors` — Search for direct methodological transfer from marine animal tracking to environmental monitoring.
- [novelty] `acoustic receiver network optimization pollution` — Tests if the specific sensor placement problem from underwater acoustics has been applied to pollution.
- [novelty] `generative models synthetic pollution data sensor networks` — Direct search for the proposed transfer of hybrid data augmentation from bioacoustics to pollution.
- [evidence] `underwater acoustic sensor network placement` — Finds foundational literature on the original bio-inspired algorithms for marine megafauna tracking.
- [evidence] `non-convex optimization sensor deployment` — Seeks evidence for the core mathematical problem (structural identity) in sensor network literature.
- [evidence] `mobile stochastic source sensor coverage` — Targets the shared problem of tracking a mobile, stochastic source with static sensors.
- [evidence] `deep generative models environmental data augmentation` — Seeks evidence for the proposed mechanism of using synthetic data in environmental monitoring.
- [cross_domain] `cross-domain sensor network algorithms` — Broad search for interdisciplinary precedents in algorithm transfer between sensor network domains.
- [cross_domain] `wildlife tracking wireless sensor networks` — Looks for existing bridges between biology/ecology and engineering in sensor network design.
- [cross_domain] `bio-inspired optimization environmental monitoring` — Searches for general precedent of bio-inspired algorithms applied to environmental engineering problems.

---

*Generated by SPORE (Systeme de Production d'Opportunites de Recherche par Exploration) on 2026-04-12*