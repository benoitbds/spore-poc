# SPORE — Synthèse des tests utilisateurs et plan d'action priorisé

**Date** : Avril 2026
**Périmètre** : 4 personas testeurs (Hugo Berthier, Aïcha Mansouri, Christophe Lefèvre, Margaux Tessier-N'Diaye, Robert Cazenave) + 1 rapport synthèse testeur
**Méthode** : prompts persona Claude for Chrome (Sonnet 4.6), navigation complète, création de comptes via yopmail, lecture de briefs, audit technique
**Statut** : référence pour les sprints à venir

---

## 1. Diagnostic central

> *"Ce n'est pas un projet de communication déguisé en science — c'est un projet de science embarrassé par sa propre communication."*
> — Robert Cazenave, retraité prof de philosophie, agrégation 1981

> *"L'écart entre ces deux registres — 8/10 sur ce qui est construit, 2–3/10 sur ce qui est dit — est le problème central et résoluble du projet."*
> — Robert Cazenave, note de légitimité épistémique

Les quatre personas convergent sur un diagnostic unique : **le produit est meilleur que sa promesse**. La rigueur technique du pipeline, la transparence statistique, la qualité du contenu de l'onglet Recherche, l'absence d'hallucination bibliographique sont au-dessus de la moyenne de l'espace IA grand public. Les dimensions sémantiques de surface (vocabulaire, statut déclaré, mise en avant des features différenciantes) sont franchement insuffisantes.

C'est une situation favorable : il y a moins à *construire* qu'à *re-formuler*.

---

## 2. Convergences à travers les 4 personas

### 2.1 Le mot "découverte" — faute épistémique centrale

Trois personas indépendants pointent le même mot. Convergence à 100% à travers les classes sociales et les bagages culturels.

- **Aïcha (38 ans, AVS, Aubervilliers)** : *"'Imaginer des découvertes' sème un doute légitime dès la première seconde : est-ce réel ou fabriqué ? Ce n'est pas clarifié assez vite."*
- **Robert (71 ans, prof de philo retraité)** : *"Ce que SPORE produit n'est pas une découverte : c'est une conjecture structurée, une hypothèse heuristique. Le mot est employé de façon systématiquement et probablement délibérément abusive."*
- **Synthèse testeur** : *"Une faute épistémique systématique et omniprésente."*

**Inventaire des occurrences** (Robert) : titre HTML, sous-titre homepage, rubrique nav principale, badge "LA DERNIÈRE DÉCOUVERTE", section "Les autres trouvailles" → "Voir toutes les découvertes", URLs de briefs (`/discoveries/SPR-2026-XXXX`), navigation inter-briefs ("Découverte précédente / suivante"), compteur ("38 découvertes"), footer, menu mobile.

### 2.2 Le contenu Recherche est meilleur que la couche Comprendre — et il est caché

- **Robert** : *"Ce contenu est remarquable. (...) Le problème majeur est que ce contenu est caché derrière deux clics (onglet Recherche → Télécharger gratuitement), ce qui simule un paywall et décourage l'accès à ce qui est pourtant la vraie valeur du produit."*
- **Margaux** : *"Le CTA d'achat doit être inline dans la brève, au moment du 'Et concrètement ?' — pas sur une page /pricing séparée."*
- **Christophe** (implicite via sa lecture détaillée des kill criteria GO/NO-GO) : c'est sur ce contenu qu'il évalue la rigueur du produit.

### 2.3 Les analogies du quotidien sont devenues un tic LLM reconnaissable

- **Aïcha** : *"Le recours systématique à une analogie du quotidien toutes les deux sections (le chef cuisinier, le système d'arrosage, le jardin...) finit par devenir un tic reconnaissable et prévisible."*
- **Robert** (avec comptage) : *"Sur 15 briefs lus : analogies culinaires (9 cas), domestiques (4 cas), jeu/sport (2 cas)."*
- **Hugo** : note la redondance entre l'analogie home et l'analogie brève (*"je l'ai déjà lu en fait. Bon c'est un peu redondant ça"*).
- **Margaux** : *"L'analogie 'apprendre à faire du vélo' est pédagogiquement efficace mais scientifiquement déplacée. L'hormèse cellulaire est un phénomène biochimique précis — la comparer à un apprentissage moteur n'est pas structurellement homologue."*

### 2.4 Le panel de 5 reviewers a un défaut structurel

- **Margaux (triangulation sur 3 brèves)** : *"L'Industriel et le Stratège Financement partagent le même score (6.5) sur toutes les brèves testées. Ce n'est pas une coïncidence : c'est un pattern. (...) Le Stratège Financement parle d'évaluateurs ANR/ERC — ANR et ERC sont des agences académiques, pas des investisseurs privés. Un vrai stratège life sciences parlerait de Series A biotech, EIC Pathfinder, TRL 3→4, valorisation IP."*
- **Margaux (incohérence de langue)** : *"L'Expert du domaine et l'Avocat du diable écrivent en anglais parce qu'ils sont générés dans un prompt EN, et le Méthodologue, l'Industriel et le Stratège Financement écrivent en français parce qu'ils sont dans un prompt FR. Trois brèves testées, même pattern."*
- **Christophe** : *"J'aurais voulu qu'un 'agronome praticien' soit dans les 5 personas, pas seulement un méthodologue, un expert domaine, un avocat du diable, un industriel et un stratège financement. Ces cinq-là pensent labo et levée de fonds, pas Beauce et couverts végétaux."*
- **Robert** : *"Cinq personas IA qui valident une hypothèse générée par IA ne constituent pas une validation indépendante. Ils peuvent identifier des incohérences internes, des faiblesses de protocole. Ils ne peuvent pas apporter la contradiction que seul le réel apporte."*

---

## 3. Apports uniques par persona

Chaque persona a apporté un angle que les autres n'avaient pas les outils pour formuler.

### Hugo Berthier (23 ans, alternant BTS, Saint-Étienne)
- **Effet "cimetière" de la grille NON PRODUCTIVE en lecture mobile rapide** : *"C'est la première chose que tu vois en scrollant après la brève phare — une grille de trucs que l'IA a essayé et qui ont raté. Pour quelqu'un qui découvre le site en 30 secondes dans le métro, ça fait 'ce site est un cimetière d'idées foirées'."*
- **Titres anglais incompréhensibles sur les stubs** ("Skeletal Muscle × Algebraic Geometry") : aucun hook pour le grand public.
- **Note** 6/10 sur l'envie de revenir. Pas d'achat envisagé. Partage WhatsApp possible, pas Insta.

### Aïcha Mansouri (38 ans, auxiliaire de vie, Aubervilliers)
- **Diagnostic de cohérence inter-layers** : *"Le site dit 'tout le monde' par son design, mais son vocabulaire dit 'chercheurs'. L'esthétique est inclusive, le contenu ne l'est pas encore."*
- **Absence de filtre par univers de vie** : *"Il n'existe aucun filtre par univers de vie — santé, alimentation, environnement, éducation. L'utilisateur lambda doit parcourir un catalogue de recherche sans boussole."*
- **Détection LLM affûtée** (utilisatrice ChatGPT gratuit) : *"Des phrases comme 'pourrait sauver des membres et améliorer la vie de millions de patients' sonnent comme du résumé généré automatiquement — pas comme quelqu'un qui connaît vraiment le sujet."*

### Christophe Lefèvre (56 ans, agriculteur, Eure-et-Loir)
- **Absence d'humain identifié sur le site** : *"Pas un seul auteur humain identifié. 'SPORE Research' — qui ? Quel cursus ? Quel labo ? En recherche, l'anonymat institutionnel, c'est un signal d'alerte."*
- **Demande de modèle d'abonnement thématique** : *"Propose un abonnement thématique, pas seulement des briefs à l'unité. Quelque chose comme '5 brèves/mois sur des domaines que tu choisis — 15€'. C'est le modèle qui ferait revenir quelqu'un comme moi tous les mois."*
- **Demande d'enrichissement domaines agronomiques** : Soil Microbiology, Rhizosphere Ecology, Carbon Sequestration Agronomy, Precision Agriculture, Biogeochemical Cycles.
- **Validation de la collision sur mesure à 25€** : *"C'est l'offre la plus intéressante pour moi, et de loin. 25€ c'est le prix d'un déjeuner d'affaires."*
- **Note crédibilité** 7/10.

### Margaux Tessier-N'Diaye (34 ans, ingénieure R&D Airbus, Toulouse)
- **5 DOIs vérifiés, 5/5 légitimes** : titres correspondant mot pour mot, sources réelles. Le claim "zéro hallucination bibliographique" tient sur cet échantillon.
- **Distinction non signalée Preprint / Conference / Journal** : *"arXiv:2402.17718 est une préprint soumise à une conférence (NAMRC 2024), pas un article peer-reviewed. SPORE l'inclut avec un DOI valide — techniquement correct. Mais l'UI ne signale pas ce niveau de preuve."*
- **Score de nouveauté 0.85 sans légende** : *"Sur quoi est calculé 0.85 ? Quelle est la distribution ? Qu'est-ce que 'inédit' signifie opérationnellement ?"*
- **Concentration thématique du 24 avril déduite des stats** : *"17 briefs publiés en un seul jour — ce n'est pas de la sérendipité, c'est une session de run concentrée sur un domaine."*
- **Note rigueur** 7.5/10. **Note design** 8/10.

### Robert Cazenave (71 ans, prof de philo retraité, Dordogne)
- **Audit JSON-LD/SEO** : *"Le JSON-LD utilise '@type': 'ScholarlyArticle'. Dans le Web sémantique, ce type désigne un article académique peer-reviewed. SPORE produit des hypothèses non testées générées par IA. Google peut classer les briefs dans ses résultats de recherche académique avec des métadonnées suggérant une validation inexistante."*
- **Doublons intra-corpus identifiés** :
  - SPR-2026-66E7 et SPR-2026-3403 (Chemical Biology × Evolutionary Biology, sondes fluorescentes bactériennes)
  - SPR-2026-FBF3 et SPR-2026-7516 (Synthetic Biology × Tissue Regeneration, circuits génétiques conditionnels)
  - Cluster avec SPR-2026-0386 (3 briefs sur le même paradigme de chimie de sondes fluorescentes bactériennes)
- **Glissement "les chercheurs proposent"** : *"Aucune équipe humaine réelle ne porte ces hypothèses. C'est un LLM qui a généré le texte. Un journaliste qui reprend un brief sans lire les mentions légales écrira naturellement 'des chercheurs ont proposé de...' en attribuant une paternité humaine à une production IA."*
- **Absence de contre-preuves bibliographiques structurées** : *"L'avocat du diable critique la méthode et le protocole — il ne cite pas de travaux empiriques qui contrediraient l'hypothèse centrale. Pour un outil qui revendique 'zéro hallucination bibliographique', c'est incohérent avec la promesse de rigueur."*
- **Note légitimité épistémique globale** 5,4/10 (avec proposition de passage à 7,5/10 par les seules corrections R1-R5 — rédactionnelles, < 1 jour de travail).

---

## 4. Plan d'action priorisé

### Niveau 1 — Cette semaine (< 1 jour de travail, impact immédiat)

**N1.1 — Renommer "découverte" → "hypothèse" / "piste" / "brief"**
- Refactoring de chaînes : titre HTML, taglines, rubrique nav, badge "LA DERNIÈRE DÉCOUVERTE", section "Les autres trouvailles", compteur "38 découvertes", footer, menu mobile
- URLs `/discoveries` → `/hypotheses` ou `/briefs` (avec redirection 301 sur l'ancienne URL pour préserver le SEO Google Search Console)
- Navigation inter-briefs : "Hypothèse précédente / suivante" ou "Brief précédent / suivant"
- Aucune logique métier à modifier
- **Fichiers concernés** : composants Next.js (Header, Footer, BriefCard, BriefDetail, Hero), `app/discoveries/page.tsx` à renommer, ajout d'un middleware de redirection
- **Estimation** : 2-3 heures

**N1.2 — Corriger le schema.org JSON-LD**
- Remplacer `"@type": "ScholarlyArticle"` par `"@type": "Article"` (ou `CreativeWork`)
- Ajouter `"genre": "research-hypothesis"` ou `"about"` avec mention explicite du statut hypothétique
- Supprimer tout champ impliquant une peer review (`isPartOf` vers un journal, `publisher` académique)
- **Fichiers concernés** : composant de génération JSON-LD dans le BriefDetail
- **Estimation** : 30 minutes

**N1.3 — Refondre le prompt vulgarization_fr pour casser deux tics**
- **Tic "les chercheurs"** : interdire explicitement "les chercheurs", "les scientifiques", "ils ont proposé". Imposer "SPORE", "le modèle propose", ou voix impersonnelle ("cette hypothèse propose de tester si...", "le protocole suggère d'examiner...").
- **Tic des analogies à répétition** : limiter à UNE analogie filée par brief, pas une par section. Diversifier les registres (pas systématiquement culinaire/domestique).
- Ajouter dans le prompt une contrainte "éviter les phrases conclusives lyriques type 'pourrait sauver des millions de patients', préférer la précision technique".
- **Fichiers concernés** : `spore-poc/agents/vulgarization_fr.py` ou équivalent, prompt template
- **Estimation** : 2-3 heures de prompt engineering + test sur 3-5 briefs

**N1.4 — Ajouter un badge de statut épistémique sur chaque brief**
- Sous le titre, ligne discrète : *"Hypothèse non testée · Générée par IA · Non validée par des pairs"*
- Lien optionnel vers la page À propos / Méthodologie pour le détail
- **Fichiers concernés** : composant BriefDetail
- **Estimation** : 1 heure

**N1.5 — Promouvoir la phrase manifeste en home**
Deux personas indépendants ont identifié *"Une hypothèse nulle bien documentée vaut mieux qu'une fausse promesse d'unification"* comme la meilleure phrase du site. Elle doit remonter en home, soit comme tagline alternative, soit en sous-titre du hero, soit comme citation manifeste dans la section "Comment ça marche".
- **Estimation** : 30 minutes (décision éditoriale + intégration)

**N1.6 — Repositionner la Collision sur mesure**
- Mettre la Collision sur mesure en première position sur `/pricing` (avant Brief unitaire et Pack 5)
- Ajouter "Collision sur mesure" dans la navigation principale (top nav), pas seulement en bas des stub briefs
- Convergence Margaux + Christophe : c'est l'offre la plus différenciante.
- **Estimation** : 1 heure

**N1.7 — Reformuler "100% références vérifiées" → "100% DOIs vérifiés sur Semantic Scholar"**
- Un mot de plus qui évite la confusion entre vérification bibliographique et validation des conclusions.
- **Estimation** : 5 minutes

---

### Niveau 2 — Ce mois (1-3 jours de travail, impact produit et confiance)

**N2.1 — Refondre les prompts Industriel et Stratège Financement**
- **Industriel** : injecter un vocabulaire-cible explicite (time-to-market, capex, contract manufacturing, supply chain, scale-up TRL 6→8, IP landscape) et interdire le vocabulaire académique. Ajouter 1-2 few-shot examples : un vrai post LinkedIn d'un VP R&D ou un extrait d'analyse industrielle.
- **Stratège Financement** : imposer le vocabulaire du capital (TRL, EIC Pathfinder, Series A/B biotech, burn rate, valorisation pré-money, exit multiple). Interdire ANR/ERC sauf si TRL approprié. Few-shot example : un pitch VC réel ou une slide de venture deal.
- Le pattern "score 6.5 systématique" devrait disparaître si les angles sont vraiment différents.
- **Estimation** : 1 jour de prompt engineering + tests croisés sur 5-10 brèves

**N2.2 — Normaliser la langue du panel**
- Décision : tous les reviewers en FR pour la version FR du brief, tous en EN pour la version EN. Pas de panel mixte FR/EN.
- Soit forcer la langue dans le prompt de chaque reviewer (option A : moins coûteux), soit générer deux versions complètes du panel selon la langue (option B : double le coût mais résout pour les bilingues qui switchent).
- **Estimation** : 0.5 jour si option A, 1 jour si option B

**N2.3 — Ajouter une page "À propos" avec un visage humain**
- Ton nom (Benoît Baqué de Sariac), ton parcours, ta démarche, le statut du projet (solo developer, phase de lancement)
- Lien vers tes profils publics (LinkedIn, GitHub, X si pertinent)
- Phrase sur la philosophie du projet (la phrase manifeste ou variante)
- Christophe : *"Mets un humain en bas de page. La transparence sur qui est derrière est un signal de sérieux que je cherche systématiquement."*
- **Estimation** : 0.5 jour

**N2.4 — Tags par "univers de vie" sur les briefs**
- Au-dessus des tags techniques OpenAlex, ajouter une couche de tags grand public : "Santé du quotidien", "Vieillissement", "Alimentation", "Sols et climat", "Énergie", "Cerveau", "Industrie", etc.
- Mapping initial manuel pour les 38 briefs existants (1-2 heures)
- Pour la suite : classifier LLM léger qui assigne 1-3 tags grand public à chaque nouveau brief
- Filtrage sur `/discoveries` par tag grand public en plus du tag domaine
- **Estimation** : 1 jour

**N2.5 — Recherche sémantique sur `/discoveries`**
- Barre de recherche qui calcule la similarité cosinus entre la query et les embeddings concaténés (titre + sharpened hypothesis + vulgarisation FR) de tous les briefs
- Tu as déjà sentence-transformers all-MiniLM-L6-v2 en base — réutiliser
- **Fichiers concernés** : nouvelle route API `/api/search` (Next.js Server Component lit SQLite), composant SearchBar
- **Estimation** : 1 jour

**N2.6 — Détecter et lier les doublons intra-corpus**
- Avant publication d'un nouveau brief : calculer la similarité cosinus avec tous les briefs déjà publiés
- Si > 0.85 (seuil à calibrer) : afficher un encart "Voir aussi : SPR-2026-XXXX — hypothèse voisine" sur les deux briefs
- Pour les doublons existants identifiés par Robert (66E7/3403, FBF3/7516, et le cluster 0386) : lier rétroactivement
- Cela résout le problème de crédibilité du score de nouveauté que Robert pointe (le score compare à la littérature externe, pas au corpus SPORE lui-même).
- **Estimation** : 0.5 jour

**N2.7 — Documenter la métrique de Nouveauté**
- Info-bulle sur le score (au survol desktop, au tap mobile) : *"Score calculé à partir de la distance sémantique entre les deux domaines dans l'espace d'embeddings (sentence-transformers all-MiniLM-L6-v2) et de l'absence de cooccurrence dans le corpus Semantic Scholar des 5 dernières années. Médiane observée sur les 38 briefs : 0.74, écart-type 0.09."*
- Vérifier les chiffres dans tes données réelles avant publication.
- Lien vers une page `/methodology` avec le détail (ou section dans `/how-it-works`).
- **Estimation** : 0.5 jour

**N2.8 — Badges Preprint / Conference / Journal sur les références**
- Récupérable via les métadonnées Semantic Scholar (`publicationVenue.type` ou `externalIds.ArXiv`)
- Badge discret à côté de chaque référence dans la base de preuves
- **Estimation** : 0.5 jour

**N2.9 — Supprimer le double clic vers le contenu Recherche**
- Pour les utilisateurs connectés avec accès gratuit déclaré : afficher le protocole complet directement, sans bouton "Télécharger gratuitement"
- Le bouton en design d'appel à l'action fort simule un paywall que la phase de lancement ne justifie pas.
- **Estimation** : 0.5 jour

**N2.10 — Séparation visuelle stubs / briefs publiés**
- Soit onglet "Tentatives non productives" dédié sur `/discoveries`
- Soit garder la grille mixte mais générer des titres FR pour les stubs (format "Pourquoi le croisement X × Y n'a rien donné")
- Étendre le prompt vulgarization_fr existant pour qu'il génère ces titres aussi sur les stubs
- **Estimation** : 0.5 jour

**N2.11 — Inverser le positionnement de la home**
- Robert : *"La homepage actuelle montre l'exemple avant le concept — ce qui est juste pour la rétention de visiteurs déjà informés, faux pour la conversion des visiteurs froids."*
- Tester : concept en premier (ce qu'est SPORE, ce qu'il produit, pour qui), exemple en second.
- À A/B tester si tu as la patience, sinon arbitrage à l'œil.
- **Estimation** : 0.5 jour + temps d'A/B si applicable

---

### Niveau 3 — À arbitrer en sprint dédié (3-7 jours, impact stratégique)

**N3.1 — Ajouter une section "Contre-preuves" structurée dans chaque brief**
- Dans le pipeline post-fire, étendre le Literature Grounding pour qu'il cherche explicitement des contre-preuves en plus des supports.
- Afficher 1-2 références "contradictoires" annotées avec une explication de pourquoi elles ne tuent pas l'hypothèse centrale.
- C'est la différence entre un brief honnête et un brief de plaidoyer.
- **Effort** : 2-3 jours (modification de l'agent Literature Grounding + UI + test sur quelques briefs)

**N3.2 — Modèle d'abonnement thématique (Substack-style)**
- Suggestion Christophe : *"5 brèves/mois sur des domaines que tu choisis — 15€"*
- Couplé avec les tags par univers de vie (N2.4), permet à un utilisateur de s'abonner à un flux thématique
- Résout aussi un problème de rétention : un Substack à 15€/mois aligne ton revenu avec un usage régulier
- À étudier en parallèle du modèle unitaire 9€ ou en remplacement
- **Effort** : 5-7 jours (Stripe subscriptions, gestion des cycles de facturation, page de gestion d'abonnement, sélection des thèmes par l'utilisateur)

**N3.3 — Enrichissement du corpus de domaines vers les sciences agronomiques et environnementales**
- Christophe : *"Tu dois construire des domaines adaptés : Soil Microbiology, Rhizosphere Ecology, Carbon Sequestration Agronomy, Precision Agriculture, Biogeochemical Cycles."*
- À combiner avec l'item différé "Biology-specific domain enrichment" (qui ciblait Muscle Stem Cells, Satellite Cells, Myogenesis pour Fabien Le Grand).
- Source : OpenAlex level 3 concepts
- **Effort** : 1-2 jours (enrichissement corpus + recompute embeddings + redéploiement)

**N3.4 — 6e reviewer rotatif "praticien terrain" selon le domaine**
- Christophe l'a demandé pour l'agro. Robert pointe que le panel ne peut pas constituer une validation indépendante, mais un reviewer terrain réduirait l'écart entre cohérence interne et fécondité expérimentale.
- Bibliothèque de personas spécialisés : "agronome praticien" (sols/cultures), "clinicien hospitalier" (médical), "ingénieur production" (matériaux/procédés), "ergonome cognitif" (sciences cognitives), etc.
- Sélection automatique selon le domaine principal de la collision, ou ajout en 6e reviewer en plus des 5 actuels.
- **Effort** : 3-5 jours (design des personas, prompts, intégration dans le panel orchestration, calibration sur 10-15 briefs)

**N3.5 — CTA d'achat inline dans la brève au moment de basculement**
- Margaux : *"Mon comportement réel sera : je lis 5 briefs gratuitement, une me frappe sur un sujet que je n'avais pas anticipé, je clique 'Acheter' dans l'élan de lecture."*
- Quand Stripe sera activé : CTA "Débloquer ce brief — 9€" inline dans la brève au point de bascule "Et concrètement ?", idéalement avec un Stripe Checkout en modal pour ne pas faire perdre le contexte.
- **Effort** : 2 jours (Stripe Elements + composant modal + state management)

**N3.6 — Boucle de retour expérimental (R16 de Robert)**
- *"Mécanisme permettant à un utilisateur de signaler 'j'ai testé cette hypothèse' avec résultat (confirmée / infirmée / en cours / pivotée)."*
- Même 5 retours par an permettraient de calibrer ton scoring contre des outcomes réels et de transformer SPORE d'émetteur en système apprenant.
- Ton brief envoyé à Fabien Le Grand est exactement ce mécanisme — il faut le formaliser produit.
- C'est probablement ta vraie roadmap stratégique de fond.
- **Effort** : 5-7 jours pour une V1 minimale (formulaire de retour, base de données outcomes, page publique de suivi)

**N3.7 — Documenter l'architecture des modèles**
- *"Propulsé par DeepSeek · Claude · Semantic Scholar"* sans précision de rôle.
- Section `/architecture` ou encart dans `/how-it-works` qui explique : quel modèle pour quelle étape, comment les personas sont promptées, comment les scores sont calculés.
- Anticipation de l'IA Act européen.
- **Effort** : 1 jour (rédaction + intégration page)

**N3.8 — API publique documentée**
- Robert a noté que `/api/discoveries` et `/api/stats` retournent 404.
- Une API publique permettrait l'intégration dans Zotero, Notion, outils institutionnels, multipliant la surface de diffusion sans coût marketing.
- À aligner avec ta volonté ou non de monétiser l'API.
- **Effort** : 3-5 jours pour une V1 (auth, rate limiting, doc OpenAPI, endpoints `/v1/briefs`, `/v1/briefs/{id}`, `/v1/stats`)

---

## 5. Verdict philosophique de Robert (à conserver comme guide)

> *"SPORE repose sur un pari implicite qu'il faut formuler clairement pour en évaluer la portée réelle : la créativité scientifique serait partiellement réductible à la détection de connexions non exploitées dans un corpus textuel."*

> *"Ce pari a une limite philosophique fondamentale que le site ne formule jamais : la littérature scientifique n'est pas le réel, elle en est une représentation filtrée, retardée et socialement construite. SPORE croise des textes sur des muscles avec des textes sur la chimie analytique — il ne croise pas des muscles avec des molécules. Le modèle ne peut identifier que des lacunes dans le discours scientifique, pas dans la nature."*

> *"SPORE génère des ponts plausibles, pas des ponts vrais. La plausibilité d'une analogie dans l'espace des représentations linguistiques ne dit rien de sa fécondité dans l'espace expérimental."*

> *"Ce que SPORE est réellement — et ce qu'il devrait assumer pleinement — est un moteur de sérendipité structurée. Il ne découvre rien. Il crée des conditions favorables pour que des humains découvrent. Il réduit l'espace de recherche, pointe vers des intersections non explorées, fournit un protocole opérationnel pour tester si l'intersection est fertile. C'est déjà considérable. C'est une fonction heuristique sérieuse, bien conçue, et peu concurrencée. Le problème unique et central du projet est qu'il n'assume pas cette identité — il l'habille d'un vocabulaire emprunté à la découverte scientifique plutôt qu'à la pensée heuristique, et ce choix sabote précisément là où il voudrait convaincre."*

> *"Le fondateur a construit quelque chose d'honnête — plus honnête que son habillage. Le pipeline est rigoureux. Les statistiques sont transparentes. Les collisions non productives documentées sont de la philosophie des sciences appliquée. Tout cela existe et est bien fait. Ce n'est pas un projet de communication déguisé en science — c'est un projet de science embarrassé par sa propre communication."*

> *"La recommandation au fondateur est simple : dire ce que le produit est, avec le même soin que celui apporté à le construire."*

---

## 6. Note sur Sonnet 4.6 vs Opus 4.7

Les 4 retours ont été générés avec Claude for Chrome sur Sonnet 4.6. La qualité de tenue des personas est très haute :
- Hugo reste un alternant tout au long, ne dérive pas vers un consultant UX déguisé.
- Christophe a des références qui sonnent vraies (Arvalis, COMIFER, Glomus rhizophères, #TwittosAgri, Constellium, Novelis).
- Margaux a vérifié 5 DOIs méthodiquement, triangulé un pattern statistique sur 3 brèves, distingué arXiv/journal/conférence.
- Robert a tenu un raisonnement épistémique structuré sur 6 sections, avec audit JSON-LD, comptage des analogies sur 15 briefs, identification de 3 doublons intra-corpus, et verdict philosophique cohérent.

Conclusion : pour ce type de test (jouer un persona, naviguer un site, formuler du feedback subjectif structuré), Sonnet 4.6 est suffisant. Tu peux relancer le test avec confiance dans le futur sans systématiquement passer sur Opus 4.7. Réserve Opus 4.7 pour les tâches où la profondeur de raisonnement est critique (architecture, debugging multi-couche, raisonnement scientifique fin).

---

## 7. Décisions à arbitrer

Quelques choix où tu as les éléments mais pas la décision encore :

**D1 — Identité du produit : outil pour chercheurs ou media de vulgarisation ?**
Robert pose la tension : *"SPORE hésite entre se présenter comme un outil pour chercheurs et un media de vulgarisation. Ces deux identités ne sont pas incompatibles, mais elles ne peuvent pas partager le même vocabulaire."* Question à trancher avant le sprint Niveau 2 : est-ce que tu alignes le contenu sur le design (vulgarisation plus radicale, tags grand public) ou le design sur le contenu (plus austère, plus revue scientifique en ligne) ?

**D2 — Tagline finale**
Plusieurs candidates :
- *"L'IA qui génère les hypothèses que les chercheurs d'aujourd'hui n'ont pas encore explorées"* (Robert)
- *"Une hypothèse nulle bien documentée vaut mieux qu'une fausse promesse d'unification"* (manifeste — déjà identifié comme la meilleure phrase du site par deux personas indépendamment)
- *"Le moteur de sérendipité structurée — proposer les ponts interdisciplinaires que personne n'a encore formulés"*
- Variante à composer.

**D3 — Modèle de pricing : unitaire vs abonnement**
Christophe propose un Substack-style à 15€/mois pour 5 brèves thématiques. Margaux confirme que le 9€ unitaire ne fonctionnera que si le CTA est inline au moment de l'intention. Aïcha et Hugo ne paieront jamais. Question : est-ce que la cible monétisation est seulement Margaux+Christophe (chercheurs/polymathes), ou est-ce que tu construis aussi un modèle pour les profils Aïcha/Hugo (gratuit + monétisation indirecte par partage/viralité) ? Si oui, quel mécanisme ?

**D4 — Boucle de retour expérimental**
C'est la R16 de Robert et probablement ta vraie roadmap stratégique de fond. Mais c'est un travail conséquent (5-7 jours pour une V1). Question : est-ce que tu veux ouvrir ce chantier maintenant ou après avoir stabilisé le pricing et la diversification thématique ?

---

*Fin du document. Ce fichier est destiné à être committé dans `~/Projects/spore-poc/docs/` ou équivalent comme référence pour les sprints à venir.*
