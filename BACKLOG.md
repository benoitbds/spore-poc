# SPORE — Backlog produit

Source de vérité pour le suivi des actions produit/technique sur SPORE.
Dérivé des tests utilisateurs (`docs/SPORE_USER_TESTS_SYNTHESIS_V1.md`) et enrichi au fil des sprints.

---

## Conventions

### Nomenclature

- **Items produit** (issus du doc tests utilisateurs) : `N1.x` (Niveau 1), `N2.x` (Niveau 2), `N3.x` (Niveau 3), `D1-D4` (décisions stratégiques)
- **Sprints code** (livraisons techniques) : `S1`, `S2`, `S2.1`, `S3`...
- Un item `N1.x` peut être livré par un ou plusieurs sprints `Sx`
- Un sprint `Sx` peut couvrir un ou plusieurs items `Nx.y`

### Statuts

- ✅ **Done** — livré en prod, validé
- 🔧 **Actionable** — prêt à être lancé en sprint chirurgical
- 📋 **Backlog** — identifié mais pas planifié
- 🤔 **Decision** — bloqué sur arbitrage stratégique
- 🚫 **Won't do** — décidé qu'on ne fera pas

### Tags Git de rollback

Chaque sprint majeur crée un tag `pre-Sx` sur master avant merge, pour rollback instantané.

---

## Sprints livrés

### S1 — Rebrand `/discoveries → /briefs`
- **Date** : 27 avril 2026
- **Tag rollback** : `pre-n1-1` (sur les 2 repos spore-web et spore-poc)
- **Branche** : `feat/n1-1-rename-discoveries` (mergée + supprimée)
- **Livre** : N1.1 (entièrement)
- **Détails** : 8 étapes, 12 commits, redirections 308 sur 38 URLs, sitemap soumis Google Search Console
- **Bonus méthodologique** : règle "audit oversight" codifiée dans `CLAUDE.md`

### S2 — Fix JSON-LD @type
- **Date** : 27 avril 2026
- **Tag rollback** : `pre-n1-2`
- **Branche** : `feat/n1-2-fix-jsonld-type` (mergée + supprimée)
- **Livre** : N1.2 (partiellement — voir S2.1 pour la finition)
- **Détails** : `ScholarlyArticle` → `Article`, author en `SoftwareApplication` avec `applicationCategory: ResearchApplication`

### S2.1 — Compléter validation Google Rich Results
- **Date** : 27 avril 2026
- **Tag rollback** : `pre-n1-2-1`
- **Branche** : `feat/n1-2-1-jsonld-google-validation` (mergée + supprimée)
- **Livre** : N1.2 (finition)
- **Détails** : ajout `operatingSystem: 'Web'`, `image`, helper `toIsoUtc` pour timezones ISO 8601
- **Validation Google** : 2 éléments valides (Article + SoftwareApplication), 0 erreur critique

### S3 — Niveau 1 quick wins
- **Date** : 27 avril 2026
- **Tag rollback** : `pre-s3`
- **Branche** : `feat/s3-niveau-1-quickwins` (mergée + supprimée)
- **Livre** : N1.4, N1.5, N1.7
- **Détails** : badge épistémique sous le H1 des briefs, phrase manifeste promue en tagline principale de la home, "DOIs vérifiés sur Semantic Scholar" dans le copy pricing
- **Commit unique** : `b9bd2dd` (merge `bc83ed6`)

### S4 — Refonte prompt vulgarization_fr + re-vulgarisation batch
- **Date** : 28 avril 2026
- **Tag rollback** : `pre-s4`
- **Branche** : `feat/s4-vulgarization-prompt-refonte` (mergée + supprimée)
- **Livre** : N1.3
- **Détails** : refonte du prompt avec 3 nouvelles règles (voix impersonnelle, 1 analogie max dans `imagine_that`, anti-lyrisme). Extension du script `scripts/backfill_vulgarization.py` avec flags `--dry-run`, `--brief-id`, `--diff-against-current`. Re-vulgarisation de 22 vrais briefs (DB + JSON files synchronisés). 16 stubs (is_stub=1) skippés volontairement (relèvent du pipeline `stub_brief.py`, traités plus tard via N2.10).
- **Coût LLM** : ~$0.013 sur DeepSeek pour le batch
- **Commits** : `364ea85` (prompt+script) + `7d0d2e8` (data) + `bdf46a1` (merge)
- **Backup DB** : `data/spore.db.pre-s4.bak` à supprimer après J+7 (vers le 5 mai)

### S4.1 — Résolution dette N1.3 (2 briefs MISSING)
- **Date** : 28 avril 2026
- **Branche** : `feat/s4-1-debt-2-missing-briefs` (mergée + supprimée)
- **Livre** : N1.3 (complétion 22/24 → 24/24)
- **Détails** : reconstruction des 2 JSON files manquants depuis la DB (SPR-2026-7C1B "Cuisiner des matériaux quantiques sur mesure avec de la lumière", SPR-2026-B172 "Quand une plante stressée bricole ses racines : le détour inattendu de l'acide salicylique"). Champs `original_hypothesis` et `domains` laissés vides — ces 2 briefs étant antérieurs au format actuel, l'info n'est pas récupérable depuis la DB. Application du nouveau prompt N1.3 via le script de backfill.
- **Commits** : `9728913` (data) + `d5fd8ce` (merge)

### S5.A — Promouvoir Collision sur mesure (N1.6)
- **Date** : 1er mai 2026
- **Tag rollback** : `pre-n1-6` (sur spore-web)
- **Branche** : `feat/n1-6-custom-collision-priority` (mergée + supprimée)
- **Livre** : N1.6
- **Détails** : refonte zone primaire de `/pricing` en grille 2 colonnes ("Brief gratuit" + "Collision sur mesure offerte"), badge "★ L'offre signature de SPORE" sur la 2e carte, CTA "Réserver ma collision" → `/custom`. Lien "Collision sur mesure" ajouté dans la nav principale (desktop + mobile) pointant vers `/custom`. Section "Bientôt disponible" préservée intacte (refonte différée à l'activation Stripe).
- **Commits** : `2c01e55` (pricing) + `033b33f` (nav) + `d192fb2` (merge)

### S5.B — Page À propos (N2.3)
- **Date** : 1er mai 2026
- **Tag rollback** : `pre-n2-3` (sur spore-web)
- **Branche** : `feat/n2-3-about-page` (à merger + supprimer)
- **Livre** : N2.3
- **Détails** : création de la route `/about` (server component, pattern aligné sur `how-it-works/page.tsx`). Contenu en 7 sections (qui / pourquoi / ce que SPORE fait / ce que SPORE n'est pas / limites assumées / soutien / phrase manifeste). Mention nominative (Benoît Baqué de Sariac), email contact, lien GitHub `spore-web`, statut SoBaq micro-entreprise. Lien "À propos" ajouté en première position de la rangée institutionnelle du Footer (avant Mentions légales / Confidentialité). Metadata SEO complète : `title: "À propos"` (template `%s | SPORE`), description, `canonical: /about`, OpenGraph `type: article` avec `og-default.png`, Twitter card `summary_large_image`.
- **Fichiers** : `src/app/about/page.tsx` (nouveau), `src/components/Footer.tsx`
- **Commits** : `43ce3d9`

### S5.C — Capture email newsletter (N4.1)
- **Date** : 1er mai 2026
- **Tags rollback** : `pre-n4-1` (sur spore-poc et spore-web)
- **Branches** : `feat/n4-1-newsletter-capture` (sur les deux repos, à merger + supprimer)
- **Livre** : N4.1
- **Détails** : nouveau router FastAPI `api/newsletter.py` (POST subscribe / GET confirm / GET unsubscribe), table `newsletter_subscribers` ajoutée à `storage/database.py` (auto-créée via le lifespan FastAPI nouvellement câblé sur `init_database()`), `send_newsletter_confirmation()` ajouté à `api/emails.py` avec en-têtes RFC 8058 one-click. Côté frontend : composant `<NewsletterOptIn />` monté en fin de chaque page brief, helper `subscribeNewsletter` dans `src/lib/api.ts`, 3 pages statiques `/newsletter/{confirmed,unsubscribed,error}` (noindex). Architecture Python backend + UI thin client, alignée sur le pattern `api/auth.py` (token-based + Resend). 10 tests fonctionnels verts en local sur instance uvicorn dédiée (port 8043) avec yopmail.
- **Commits spore-poc** : `1ef523e` (schema) + `2c293a2` (API + lifespan)
- **Commits spore-web** : `93a81b4` (component) + `5f91993` (brief integration) + `ca44ba4` (static pages)
- **Effort réel** : ~1.5j (estimation initiale 0.5j sous-estimait — le périmètre RGPD-clean + migration cross-repo était plus large)

### S6.1 — Workflow outreach researcher-to-researcher (N4.4)
- **Date** : 1er mai 2026
- **Tag rollback** : `pre-s6-1` (sur spore-poc)
- **Branche** : `feat/s6-1-outreach-workflow` (à merger + supprimer)
- **Livre** : N4.4
- **Détails** : pas d'envoi automatique — le sprint livre l'outillage de génération de brouillons et le tracking. Script `scripts/outreach_extract.py` (430 lignes, sqlite3 readonly, dédoublonnage intra-brief par citation_count), template FR `templates/outreach_email.md` (placeholders : first_name / last_name / paper_title / year / brief_title / domain_a / domain_b / topic_short / key_finding_short / brief_url), doc `scripts/README_outreach.md` (sources d'email, règles déontologiques, cadence 5-10/semaine, métriques). `outputs/outreach/` gitignoré.
- **Commits** : `69ca04b` (template) + `afdb973` (script) + `88bf3fd` (docs + gitignore)
- **Service impacté** : aucun (script CLI standalone, lecture seule sur la DB)

### S6.2 — Page Méthodologie + tooltip Nouveauté (N2.7)
- **Date** : 1er mai 2026
- **Tag rollback** : `pre-n2-7` (sur spore-web)
- **Branche** : `feat/n2-7-methodology-page` (à merger + supprimer)
- **Livre** : N2.7
- **Détails** : décision tranchée — transparence radicale sur la nature heuristique du score Nouveauté plutôt que prétendre à une formule. Page `/methodology` créée (server component, pattern aligné sur `/about`, 6 sections, anchors `#novelty`, `#panel`, `#kill-rate`, `#references`, `#costs`, `#stack`). Composant `<NoveltyScoreTooltip />` (client component, pas de dep tooltip ajoutée — pure CSS + useState + useEffect) wired sur 4 emplacements (home MiniStat avg + HeroBadge featured brief, /stats QualityCard, /briefs sort selector conditional sur sort=novelty, brief detail RechercheSections). Lien Méthodologie ajouté au footer (entre À propos et Mentions légales). Callout "page Méthodologie" inséré au bas de `/how-it-works` avant le CTA. N2.7-bis ajouté en backlog pour un futur score algorithmique (embeddings + absence de cooccurrence).
- **Commits** : `508873f` (page) + `6ada002` (tooltip + wiring) + `8efc5d5` (footer/how-it-works links)

### S6.3 — Lead magnet PDF Anthologie (N4.2)
- **Date** : 1er mai 2026
- **Tags rollback** : `pre-n4-2` (sur spore-poc et spore-web)
- **Branches** : `feat/n4-2-anthology-pdf` (sur les deux repos, à merger + supprimer)
- **Livre** : N4.2
- **Détails** : générateur PDF via WeasyPrint + Jinja2 (`scripts/generate_anthology.py` + `templates/anthology/anthology.{html.j2,css}`) → 8 briefs FR vulgarisés sur ~12 pages A4 (cover noir + préambule + TOC + 8 sections + page de fin avec phrase manifeste). Endpoint `POST /api/anthology/request` réutilise `newsletter_subscribers` avec `source='anthology_download'`, branche sur 3 états (new/unconfirmed/confirmed), envoie email Resend avec lien PDF public + lien confirmation newsletter (uniquement si subscriber non confirmé). Page `/anthology` (preview 8 titres + form), `/anthology/sent` (noindex). Bandeau footer site-wide. Bullet dans `/about`. URL publique du PDF servie statiquement par Next à `/downloads/spore-anthology-2026.pdf` (option α — pas de token gating).
- **Commits spore-poc** : `bea47e7` (script + templates) + `cbc84c5` (API endpoint + email)
- **Commits spore-web** : `4815192` (UI + footer) + `ec731b3` (about)
- **Effort réel** : ~1.5j

### S0 (méta) — Setup remote git GitHub
- **Date** : 27 avril 2026
- **Livre** : pas un item produit, mais une dette infra critique
- **Détails** : repos privés `benoitbds/spore-web` et `benoitbds/spore-poc` créés, push initial complet, tracking upstream configuré, doc CLAUDE.md ajoutée

---

## Niveau 1 — Quick wins (cette semaine)

### ✅ N1.1 — Renommer "découverte" → "hypothèse"/"piste"/"brief"
- **Statut** : Done
- **Livré par** : S1
- **Commit principal** : `0ec5387` (merge S1 sur spore-web)
- **Note** : URLs `/discoveries` redirigent en 308 vers `/briefs`, taxonomie FR alignée

### ✅ N1.2 — Corriger schema.org JSON-LD
- **Statut** : Done
- **Livré par** : S2 + S2.1
- **Commits principaux** : `e09d225` (S2) + `11660dd` (S2.1)
- **Note** : `Article` + author `SoftwareApplication`. Les warnings restants (offers, aggregateRating) sont volontairement laissés — mettre des valeurs serait du fake structured data

### ✅ N1.3 — Refondre prompt vulgarization_fr
- **Statut** : Done (24/24 vrais briefs)
- **Livré par** : S4
- **Commits** : `364ea85` (prompt+script) + `7d0d2e8` (data)
- **Note** : 3 règles intégrées au prompt (voix impersonnelle, 1 analogie max dans `imagine_that`, anti-lyrisme). Pré-validation sur SPR-2026-0386 puis batch sur 22 briefs. Vérification post-batch : 0 occurrence des tics énonciatifs interdits sur 4 briefs samples en prod.
- **Fichier modifié** : `prompts/vulgarization_fr.txt` (réécrit), `scripts/backfill_vulgarization.py` (étendu)

### ✅ N1.4 — Badge statut épistémique sur chaque brief
- **Statut** : Done
- **Livré par** : S3
- **Commit** : `b9bd2dd`
- **Note** : badge "Hypothèse générée par IA · Pré-publication · À tester expérimentalement" sous le H1 de chaque brief. Formulation positive (option 2 vs triple négation initiale).
- **Fichier modifié** : `src/app/briefs/[id]/BriefDetailClient.tsx`

### ✅ N1.5 — Promouvoir la phrase manifeste en home
- **Statut** : Done
- **Livré par** : S3
- **Commit** : `b9bd2dd`
- **Note** : la phrase manifeste était déjà présente en sous-titre discret. Inversion avec la phrase descriptive : manifeste en tagline primaire, descriptive en sous-titre.
- **Fichier modifié** : `src/app/page.tsx` (lignes 172-180)

### ✅ N1.6 — Repositionner Collision sur mesure
- **Statut** : Done
- **Livré par** : S5
- **Commits** : `2c01e55` (pricing) + `033b33f` (nav)
- **Note** : Custom Collision promue en zone primaire de `/pricing` (grille 2 colonnes avec carte "Brief gratuit"), badge "★ L'offre signature de SPORE". Lien "Collision sur mesure" ajouté dans nav principale (desktop + mobile) pointant vers `/custom` (flow de réservation actionnable). Section "Bientôt disponible" préservée intacte (refonte avec activation Stripe).
- **Fichiers modifiés** : `src/app/pricing/PricingClient.tsx`, `src/components/Header.tsx`

### ✅ N1.7 — "DOIs vérifiés sur Semantic Scholar"
- **Statut** : Done
- **Livré par** : S3
- **Commit** : `b9bd2dd`
- **Note** : remplacé "références vérifiées" par "DOIs vérifiés sur Semantic Scholar" dans le copy pricing et la meta description.
- **Fichiers modifiés** : `src/app/pricing/PricingClient.tsx:24`, `src/app/pricing/page.tsx:7`

---

## Niveau 2 — Sprints dédiés (1-3 jours chacun, à étaler sur le mois)

### 📋 N2.1 — Refondre prompts Industriel et Stratège Financement
- **Effort** : 1 jour prompt engineering + tests croisés sur 5-10 briefs
- **Cibles** : vocabulaire-cible explicite (TRL, capex, EIC Pathfinder, Series A/B), few-shot examples industriels/VC

### 📋 N2.2 — Normaliser la langue du panel
- **Effort** : 0.5 jour (option A) ou 1 jour (option B)
- **Décision pendante** : option A (forcer la langue dans le prompt) vs option B (générer 2 versions complètes)

### ✅ N2.3 — Page "À propos" avec un visage humain
- **Statut** : Done
- **Livré par** : S5.B
- **Commit** : `43ce3d9`
- **Note** : page `/about` créée avec contenu complet (qui / pourquoi / ce que SPORE fait / ce que SPORE n'est pas / limites assumées / soutien / phrase manifeste). Inclut nom (Benoît Baqué de Sariac), email contact (`benoit@spore-research.com`), lien GitHub `spore-web`, mention SoBaq micro-entreprise. Lien "À propos" ajouté en première position des liens institutionnels du Footer (avant Mentions légales / Confidentialité). Metadata SEO complète (title, description, OpenGraph type:article, Twitter card summary_large_image).
- **Fichiers modifiés** : `src/app/about/page.tsx` (nouveau), `src/components/Footer.tsx`

### 📋 N2.4 — Tags par "univers de vie"
- **Effort** : 1 jour
- **Cibles** : "Santé du quotidien", "Vieillissement", "Alimentation", "Sols et climat", "Énergie", "Cerveau", "Industrie"
- **Mapping initial** : manuel pour les 38 briefs existants
- **Pour la suite** : classifier LLM léger sur chaque nouveau brief
- **Prérequis pour** : N3.2 (abonnement thématique)

### 📋 N2.5 — Recherche sémantique sur `/briefs`
- **Effort** : 1 jour
- **Stack** : sentence-transformers all-MiniLM-L6-v2 déjà en base, nouvelle route API `/api/search`

### 📋 N2.6 — Détecter et lier les doublons intra-corpus
- **Effort** : 0.5 jour
- **Cible** : encart "Voir aussi : SPR-XXXX — hypothèse voisine" si similarité cosinus > 0.85
- **Bonus** : lier rétroactivement les doublons identifiés par Robert (66E7/3403, FBF3/7516, cluster 0386)

### ✅ N2.7 — Documenter la métrique de Nouveauté
- **Statut** : Done
- **Livré par** : S6.2
- **Commits** : `508873f` (page) + `6ada002` (tooltip + wiring) + `8efc5d5` (footer/how-it-works links)
- **Note** : page `/methodology` créée avec transparence radicale sur la nature heuristique du score (auto-évaluation LLM, pas une métrique calculée). 6 sections : Nouveauté, Consensus du panel, kill rate, vérification bibliographique, coûts publics, stack. Composant réutilisable `<NoveltyScoreTooltip />` (CSS+state pur, sans nouvelle dep) wired sur 4 emplacements (home avg + featured brief, /stats QualityCard, /briefs sort selector quand sort=novelty, brief detail Novelty section). Lien dans footer + callout au bas de /how-it-works pointant vers `/methodology`. Cette page **désamorce la critique court terme** (Margaux : « sur quoi est calculé 0.85 ? ») mais ne remplace pas un vrai score algorithmique → voir N2.7-bis.

### 📋 N2.7-bis — Implémenter un score Nouveauté algorithmique
- **Effort** : 2-3 jours
- **Cible** : remplacer ou compléter l'auto-évaluation LLM par un score basé sur (a) distance sémantique entre les deux domaines via embeddings sentence-transformers déjà en stack, (b) absence de cooccurrence dans le corpus Semantic Scholar des 5 dernières années
- **Contexte** : le sprint S6.2 (N2.7) a documenté la nature heuristique du score actuel sur `/methodology`. La page mentionne explicitement ce sprint comme évolution prévue ("un sprint futur N2.7-bis implémentera un score algorithmique…").
- **Priorité** : moyenne (le sprint S6.2 désamorce la critique court terme, mais un vrai score serait un argument fort pour les profils Margaux qui attendent une métrique objective)

### 📋 N2.8 — Badges Preprint/Conference/Journal sur les références
- **Effort** : 0.5 jour
- **Source** : `publicationVenue.type` ou `externalIds.ArXiv` Semantic Scholar

### 📋 N2.9 — Supprimer le double clic vers contenu Recherche
- **Effort** : 0.5 jour
- **Cible** : pour les utilisateurs avec accès gratuit, afficher protocole complet sans bouton intermédiaire

### 📋 N2.10 — Séparation visuelle stubs / briefs publiés
- **Effort** : 0.5 jour
- **Options** : onglet "Tentatives non productives" dédié OU titres FR générés pour les stubs

### 📋 N2.11 — Inverser positionnement de la home (concept avant exemple)
- **Effort** : 0.5 jour + temps d'A/B
- **Demande** : Robert. À A/B tester si patience suffisante.

---

## Niveau 3 — Sprints stratégiques (3-7 jours)

### 📋 N3.1 — Section "Contre-preuves" structurée dans chaque brief
- **Effort** : 2-3 jours
- **Impact** : différencie "brief honnête" de "brief de plaidoyer"

### 🤔 N3.2 — Modèle d'abonnement thématique (Substack-style)
- **Statut** : Decision pendante (lié à D3 modèle pricing)
- **Effort** : 5-7 jours
- **Prérequis** : N2.4 (tags grand public)

### 📋 N3.3 — Enrichir corpus domaines (sciences agro/environnementales)
- **Effort** : 1-2 jours
- **Cibles** : Soil Microbiology, Rhizosphere Ecology, Carbon Sequestration Agronomy, Precision Agriculture, Biogeochemical Cycles
- **Bonus à coupler** : Muscle Stem Cells, Satellite Cells, Myogenesis (pour Fabien Le Grand)

### 📋 N3.4 — 6e reviewer rotatif "praticien terrain"
- **Effort** : 3-5 jours
- **Cibles** : agronome praticien, clinicien hospitalier, ingénieur production, ergonome cognitif
- **Bénéfice** : réduit l'écart entre cohérence interne et fécondité expérimentale

### 📋 N3.5 — CTA d'achat inline au point de bascule
- **Effort** : 2 jours
- **Activation** : quand Stripe sera activé
- **Cible** : Stripe Checkout en modal au point "Et concrètement ?"

### 🤔 N3.6 — Boucle de retour expérimental (R16 Robert)
- **Statut** : Decision pendante (lié à D4)
- **Effort** : 5-7 jours pour V1
- **Note** : "probablement ta vraie roadmap stratégique de fond" (Robert)

### 📋 N3.7 — Documenter l'architecture des modèles
- **Effort** : 1 jour
- **Cible** : section `/architecture` ou encart `/how-it-works` (quel modèle pour quelle étape, anticipation IA Act EU)

### 📋 N3.8 — API publique documentée
- **Effort** : 3-5 jours pour V1
- **Endpoints** : `/v1/briefs`, `/v1/briefs/{id}`, `/v1/stats`
- **Stack** : auth, rate limiting, doc OpenAPI

---

## Décisions stratégiques à arbitrer

### 🤔 D1 — Identité produit : outil chercheurs vs media de vulgarisation
- **Source** : Robert
- **Tension** : *"Ces deux identités ne sont pas incompatibles, mais elles ne peuvent pas partager le même vocabulaire"*
- **À trancher avant** : sprint Niveau 2

### 🤔 D2 — Tagline finale
- **Candidates** :
  - *"L'IA qui génère les hypothèses que les chercheurs d'aujourd'hui n'ont pas encore explorées"* (Robert)
  - *"Une hypothèse nulle bien documentée vaut mieux qu'une fausse promesse d'unification"* (manifeste, 2 personas indépendants)
  - *"Le moteur de sérendipité structurée — proposer les ponts interdisciplinaires que personne n'a encore formulés"*

### ✅ D3 — Modèle pricing (tranchée 1er mai 2026)
- **Décision** : modèle multi-flux. Unitaire 9€ maintenu (canal d'acquisition), 
  abonnement Substack-style 15€/mois prioritaire (levier MRR principal), 
  tier B2B en préparation passive (lead capture inbound, pas d'outreach actif).
- **Pack 5 brèves à 29€** : supprimé — trop proche du unitaire, pas assez 
  engageant pour la rétention.
- **Justification** : cadrage humain "side project qui doit générer des revenus, 
  ne limitons pas". B2B serait le plus gros levier économique mais cycle de 
  vente long incompatible avec un side project. Abonnement Substack-style est 
  le levier MRR compatible avec le bandwidth disponible. Unitaire reste comme 
  achat impulsif d'entrée.
- **Implications** : 
  - N3.2 (abonnement thématique) devient priorité haute
  - N3.5 (CTA inline) devient priorité haute pour activation Stripe
  - N4.x (trafic) devient priorité immédiate (pas de revenu sans trafic)

### 🤔 D4 — Boucle de retour expérimental
- **Lien** : N3.6
- **Question** : ouvrir maintenant ou après pricing + diversification thématique ?

---

## Niveau 4 — Trafic et acquisition (cycle continu)

### ✅ N4.1 — Capture email newsletter sur chaque brief
- **Statut** : Done
- **Livré par** : S5.C
- **Commits** :
  - spore-poc : `1ef523e` (db schema) + `2c293a2` (API endpoints + lifespan)
  - spore-web : `93a81b4` (component) + `5f91993` (brief integration) + `ca44ba4` (static pages)
- **Note** : système complet de capture email en place. Composant `<NewsletterOptIn />` réutilisable monté en fin de chaque page brief (visible sur les deux onglets Comprendre / Recherche). Stockage SQLite (table `newsletter_subscribers`, 11 colonnes, 3 indexes). Double opt-in via Resend avec en-têtes RFC 8058 (`List-Unsubscribe-Post: One-Click`) pour Gmail / Apple Mail. Tokens uniques pour confirmation et désinscription (UUID hex). Confirmation et désinscription idempotentes (replay safe). Pages statiques `/newsletter/confirmed`, `/newsletter/unsubscribed`, `/newsletter/error` en place (noindex). Conforme RGPD (lien désinscription dans chaque email, mention transparente, `auth: false` côté client). 10 tests fonctionnels verts (subscribe / DB / confirm / re-subscribe 409 / unsubscribe / token invalide / email invalide). FastAPI lifespan ajouté à `api/main.py` qui appelle `init_database()` au boot — la table se crée automatiquement après merge + restart spore-api.
- **Métrique à suivre** : taux d'opt-in sur les visiteurs de brief (cible > 3%), nombre d'inscrits confirmés/jour
- **Stack** : Resend (déjà en place côté `api/emails.py`), table `newsletter_subscribers` créée via le pattern `init_database()`

### ✅ N4.2 — Lead magnet PDF "Anthologie SPORE"
- **Statut** : Done
- **Livré par** : S6.3
- **Commits** :
  - spore-poc : `bea47e7` (PDF script + templates) + `cbc84c5` (API endpoint + email)
  - spore-web : `4815192` (UI + footer banner) + `ec731b3` (about update)
- **Note** : workflow complet de lead-magnet. PDF généré par `scripts/generate_anthology.py` (WeasyPrint + Jinja2) à partir de 8 briefs sélectionnés (mix top panel × top novelty validé en audit S6) → `public/downloads/spore-anthology-2026.pdf` (~360 ko, A4 print-ready, 12+ pages : cover noir + préambule + TOC + 8 briefs vulgarisés + page de fin avec phrase manifeste). Endpoint `POST /api/anthology/request` réutilise la table `newsletter_subscribers` (source='anthology_download'), gère 3 cas (new/unconfirmed/confirmed), envoie un email Resend avec lien direct PDF + lien optionnel de confirmation newsletter. Page `/anthology` (preview des 8 titres + form) et `/anthology/sent` (confirmation, noindex). Bandeau emerald discret en haut du Footer (📕) sur toutes les pages. Bullet ajouté dans `/about`. PDF en URL publique (option α) — la capture email qualifie le lead, ne le bloque pas.
- **Stack** : WeasyPrint 68.1 + markdown-it-py 4.0.0 (ajoutés à pyproject.toml via `uv add`). Libs système déjà présentes (cairo, pango, gdk-pixbuf, libffi, shared-mime-info). uv.lock gitignoré (note dans .gitignore pour passer en tracked plus tard).
- **Tests** : 3 tests endpoint verts (subscribe → DB row source='anthology_download' / email envoyé via Resend / pages frontend rendues) sur uvicorn dédié port 8043 + dev frontend port 3050. PDF rendu : 8 briefs présents, stats correctes (collisions = SUM(runs.collisions_processed) = 2895, kill rate 98,7 %), aucun marqueur Jinja non résolu.
- **Métrique à suivre** : 50+ téléchargements dans le premier mois (compteur = COUNT distinct emails dans `newsletter_subscribers WHERE source='anthology_download'`)
- **Effort réel** : ~1.5j (estimation initiale 0.5j sous-estimait — typo PDF + templates Jinja + 5 fichiers UI + tests croisés)

### 📋 N4.3 — Newsletter SPORE V1
- **Effort** : 1.5-2 jours (setup + premier numéro)
- **Stack** : Substack ou Beehiiv (gratuit, pas de friction)
- **Cadence cible** : bi-mensuelle au démarrage, hebdomadaire si traction
- **Format type** : 1 brief mis en avant + 2-3 brefs en bref + 1 collision 
  non productive du mois
- **Cross-link** : bouton "S'abonner à la newsletter" en home et /briefs
- **Métrique de succès** : 100 abonnés à la fin du premier mois

### ✅ N4.4 — Workflow outreach researcher-to-researcher
- **Statut** : Done
- **Livré par** : S6.1
- **Commits** : `69ca04b` (template) + `afdb973` (script) + `88bf3fd` (docs + gitignore)
- **Note** : workflow semi-automatique. Script `scripts/outreach_extract.py` lit la base SQLite en read-only (`mode=ro`), extrait pour chaque brief publié les auteurs cités dans `grounding_data.evidence_base[*].authors` (cap à 3 par paper), dédoublonne par `(brief, author)` en gardant le paper avec le plus de citations, génère un brouillon d'email personnalisé par auteur dans `outputs/outreach/{brief_id}/{lastname}_{doi_short}.md`. Tracking append-only `outputs/outreach/_tracking.csv` avec 14 colonnes (envoi, date, email_address, response, follow-up, notes) — préserve les annotations manuelles entre runs grâce au skip des `(brief, author)` déjà présents. Modes `--brief-id` ou `--all-published`. Template `templates/outreach_email.md` non commercial (transparence projet, kill rate 98%, mention humain in the loop). Doc complète `scripts/README_outreach.md` (sources d'email, déontologie, cadence, métriques). `outputs/outreach/` gitignoré (PII auteurs).
- **Test** : run sur SPR-2026-816D — 6 brouillons générés à partir de 2 papers (3 auteurs chacun, 0 doublon intra-brief). Re-run idempotent (6 skipped, CSV inchangé). Email vérifié : tous les placeholders résolus, FR vulgarisé, domaines depuis `sharpened_data.domains`.
- **Métrique à suivre** : taux de réponse > 8% (`response_received / email_sent` dans le CSV), 1+ témoignage public d'expert par mois
- **Étape suivante humaine** : envoi manuel des emails (5-10/semaine), recherche d'email d'auteur sur Google Scholar / page institutionnelle, mise à jour du CSV après envoi

### 📋 N4.5 — Calendrier LinkedIn structuré
- **Effort** : 0.5 jour pour template + 30 min par post
- **Cadence** : 4 posts par mois (1 par semaine), chacun mettant en avant un 
  brief avec : titre punchy, analogie centrale, lien vers le brief, hashtags 
  niche scientifique
- **Métrique de succès** : impressions cumulées > 10 000/mois à 3 mois

### 📋 N4.6 — Notification "alerte nouvelle hypothèse dans votre univers"
- **Effort** : 1 jour
- **Prérequis** : N2.4 (tags univers de vie)
- **Cible** : opt-in lors de l'inscription, cron qui envoie un digest hebdo 
  des nouvelles hypothèses dans les univers cochés par l'utilisateur
- **Métrique de succès** : taux d'ouverture > 35%, taux de retour sur le site 
  via cette notif > 15%

---

## Niveau 7 — Internationalisation (FR + EN)

### 🔄 S7.1 — i18n Foundation (validée 2 mai 2026)
- next-intl 4.11 installé, configuration routing en place (`localePrefix: 'always'`, locales `fr` + `en`, defaultLocale `fr`)
- Middleware avec **matcher restreint** `/(fr|en)/:path*` (Option α validée pendant le sprint pour ne pas casser les pages existantes)
- Branche logique de détection navigateur (Accept-Language → `/fr` ou `/en`) **wired mais unreachable** côté code — sera activée en S7.2 quand toutes les pages auront leur version `[locale]/`
- Structure parallèle `src/app/[locale]/` avec layout (NextIntlClientProvider only, pas de `<html>`/`<body>`) + `[locale]/test/page.tsx` validant la mécanique
- Pages existantes (/, /about, /briefs, /custom, /pricing, /methodology, /anthology, /how-it-works, /stats…) **intactes** pour le moment, servies par l'arbre `src/app/...` non localisé
- Tests : 5/5 verts (`/fr/test`, `/en/test`, lien switch FR↔EN, /, /about) + build production passe
- Commits : `a3158eb` (foundation) + `c93ae06` (middleware) + `2ce0ab0` (test page)

### 🔄 S7.2 — Migration pages structurelles (validée 2 mai 2026, partielle)
**Livré** :
- [x] 12 routes migrées sous `src/app/[locale]/` : `/`, `/about`, `/methodology`, `/how-it-works`, `/anthology` (page seule), `/briefs`, `/briefs/[id]`, `/stats`, `/pricing`, `/privacy`, `/legal`, `/custom` (page seule)
- [x] Root layout refactor : chrome (Header/Footer/LaunchBanner) déplacé dans `[locale]/layout.tsx`. `app/layout.tsx` minimal (HTML shell + fonts + AuthProvider). `<html lang="fr">` reste hardcoded au root (TODO S7.3 pour devenir locale-aware via headers ou root group)
- [x] Middleware élargi : matcher `'/((?!api|_next|_vercel|.*\\..*).*)'`, smart root redirect active (`/` → `/fr` ou `/en` selon Accept-Language). SKIP_LOCALE_PATHS pour `auth/`, `newsletter/`, `payment/`, `account`, `anthology/sent`, `custom/[id]/status` — preserve les emails et redirects Stripe legacy
- [x] LanguageSwitcher monté dans le Header (desktop + mobile drawer)
- [x] Chrome localisé : Header (6 nav items + aria), Footer (Explore + About + institutional links + anthology banner + manifeste), LaunchBanner (offre + close)
- [x] Widgets localisés : NewsletterOptIn (heading, subtitle, form labels, états, privacy note), NoveltyScoreTooltip (aria, body, link)
- [x] Sitemap bilingue avec annotations `xhtml:link rel="alternate" hreflang="…"` (Google-recommended) — 22 entries statiques + 76 brief entries
- [x] Tests : build green (105 pages SSG dont 76 brief paths localisés × 2), 11/11 fonctionnels verts (root redirect 307, /fr/* + /en/*, LanguageSwitcher reciprocal, skip-list intacte)

**Routes laissées à la racine (S7.2-bis)** : auth/verify, newsletter/{confirmed,unsubscribed,error}, payment/{success,cancel}, custom/[id]/status, anthology/sent, account. Voir S7.2-bis ci-dessous.

**Reporté à S7.3** (étiqueté pour clarté) :
- [ ] **Hreflangs dans `generateMetadata` de chaque page** (alternates.languages) — actuellement seul le sitemap les porte ; pour SEO complet il faut aussi les avoir dans le `<head>` de chaque page
- [ ] **Page-level UI strings** : home metrics labels, briefs sort UI, brief detail tabs ("💡 Comprendre" / "🔬 Recherche"), anthology preview headings, pricing cards, custom form, stats cards
- [ ] **Migration `next/link` → `@/i18n/routing` Link** dans les pages migrées (préservation auto du locale lors des navigations internes ; actuellement les Links FR sortent du locale ce qui force le middleware à re-redirect)
- [ ] **Long-form editorial content** : déjà planifié comme S7.3 contenu

**Commits** : `f441825` (root layout) + `5f36b6e` (middleware + LanguageSwitcher) + `d44c843` (chrome strings) + `63fa17d` (widget strings) + `a2d09a1` (sitemap)

### 📋 S7.2-bis — Migration des routes transactionnelles (à planifier)
- Routes à migrer : `/auth/verify`, `/newsletter/{confirmed,unsubscribed,error}`, `/payment/{success,cancel}`, `/custom/[id]/status`, `/anthology/sent`, `/account`
- **Pré-requis cross-repo** :
  1. Modifier `api/emails.py` (spore-poc) pour pointer vers les nouvelles URLs `/{locale}/...`
  2. Updater Stripe dashboard avec les nouvelles `success_url`/`cancel_url`
  3. Coordonner deployment frontend + backend
- **Stratégie de redirection legacy** :
  - Ajouter dans `next.config.js` des redirects 301 :
    - `/auth/verify` → `/fr/auth/verify` (ou détection cookie locale)
    - `/newsletter/confirmed` → `/fr/newsletter/confirmed`
    - etc.
  - Garder ces redirects 6+ mois pour ne pas casser les emails legacy
- **Question ouverte** : quelle locale choisir pour un user qui clique un email avant qu'on ait stocké sa préférence ? Options :
  (a) Toujours `/fr` (default historique du site)
  (b) Détection navigateur Accept-Language
  (c) Stocker la locale dans le `subscriber_id` ou le token
- **Effort estimé** : 0.5-1 jour avec coordination spore-poc + spore-web
- **Priorité** : moyenne (à faire avant que le compteur de subscribers passe 100)

### 🔄 S7.3 — Migration pages éditoriales (foundation 2 mai 2026 ; éditorial reporté en S7.3-bis)
**Livré** (foundation architecturale + SEO) :
- [x] **`<html lang>` dynamique** : root layout (`app/layout.tsx`) est désormais async, lit la locale via `getLocale()` de next-intl/server. `/fr/*` rend `<html lang="fr">`, `/en/*` rend `<html lang="en">`, et les routes skip-list (auth, newsletter, payment) restent en `lang="fr"` par fallback du routing default. Vérifié via curl.
- [x] **Migration `next/link` → `@/i18n/routing` Link** dans 9 fichiers `.tsx` sous `src/app/[locale]/` : navigation interne préserve le locale automatiquement (1 redirect en moins par clic).
- [x] **Tagline officielle EN** adoptée : `"SPORE — A research collision engine"` (remplace la traduction littérale).
- [x] **Manifeste officiel EN** adopté : `"A well-documented dead end is worth more than a glib unification."` (remplace la traduction littérale "null hypothesis").
- [x] **Bandeau bilingual sur `/en/anthology`** : composant Server Component `<BilingualNotice />` qui rend une note explicative au-dessus des 8 titres FR (conservés comme décidé en S7.2). `/fr/anthology` ne rend rien d'extra.
- [x] **Helper `src/lib/i18n-seo.ts`** : `localeAlternates(locale, path)` pour générer les `alternates: { canonical, languages: { fr, en, x-default } }` que chaque `generateMetadata` doit attacher. Wiring cross-pages reporté en S7.3-bis.
- [x] **Build green** sur la branche, tests fondations verts (lang dynamique, manifesto/tagline, bandeau bilingual)

**Reporté en S7.3-bis** (le gros morceau éditorial) :
- [ ] **Traduction éditoriale ciselée** (~2000 mots, niveau Nature) :
  - Home `[locale]/page.tsx` (manifeste home + FeaturedHero + sections "Comment ça marche en bref")
  - `/[locale]/about/page.tsx` (~600 mots, signature personnelle)
  - `/[locale]/methodology/page.tsx` (~700 mots, prose technique)
  - `/[locale]/how-it-works/page.tsx` (~250 mots, 3 principes + funnel + CTA)
  - `/[locale]/anthology/page.tsx` (preview text autour des titres FR)
  - `/[locale]/custom/CustomClient.tsx` (form labels, status messages)
  - `/[locale]/pricing/PricingClient.tsx` (3 cartes + FAQ + manifesto reprise)
  - `/[locale]/privacy/page.tsx`, `/[locale]/legal/page.tsx` (textes légaux courts)
- [ ] **Page-level UI strings restantes** (~80-120 strings) : briefs sort (Panel/Nouveauté/Date), brief detail tabs ("💡 Comprendre" / "🔬 Recherche"), home metrics labels, stats cards, anthology preview headings, brief sections internes
- [ ] **BriefDetailClient.tsx** strings (~1100 lignes, ~80 strings FR à clé) — overlap avec S7.4 briefs bilingues
- [ ] **Hreflangs dans chaque `generateMetadata`** via le helper `localeAlternates()` (pour l'instant le sitemap global porte les hreflangs au niveau site, mais chaque page devrait aussi les avoir dans son `<head>`)
- [ ] **Markdown collocation** pour les longs contenus éditoriaux (about, methodology) si on bascule en `src/content/{page}.{locale}.md` avec react-markdown — décision à arbitrer (Inline JSON vs Markdown)

**Commits S7.3 foundation** : `6c2009d` (link migration) + `7e97aae` (html lang) + `776f1a9` (tagline + manifesto + bilingual notice + helper)

**Note de cadrage** : le sprint S7.3 spec original demandait ~5-8h de travail éditorial soigné. La foundation architecturale (lang dynamique, link migration, helper SEO, bandeau, alignements officiels FR/EN) a été livrée en ~1.5h. La traduction ciselée des ~2000 mots éditoriaux (about + methodology + home + how-it-works) demande un sprint dédié (S7.3-bis) pour atteindre la qualité Nature-grade que le spec exige. **Ne pas la rusher** — un mauvais EN sur /about coûte plus cher en crédibilité qu'un FR temporaire visible sur /en/.

### 🔄 S7.3-bis — Traduction éditoriale ciselée (3 pages livrées 2 mai 2026 ; reste reporté en S7.3-residual)
**Livré** :
- [x] **`/about`** intégralement traduit EN — 7 sections (who/why/what/what-not/limits/support/last-thing), ~660 mots EN, signature personnelle préservée, citations historiques (penicillin/CRISPR/PageRank/AlphaFold) conservées, manifeste officiel "A well-documented dead end is worth more than a glib unification."
- [x] **`/methodology`** intégralement traduit EN — 6 sections (novelty/panel/kill-rate/refs/costs/stack) + intro + footer, ~720 mots EN, callouts amber "Acknowledged limitations" et cyan "Meta-Reviewer verdict" préservés, vocabulaire produit conservé (kill rate, brief, panel review, collision, domain)
- [x] **`/how-it-works`** intégralement traduit EN — funnel + 3 principles + methodology pointer + CTA, ~250 mots EN, "SPORE proposes; humans dispose" comme principe 1
- [x] **Hreflangs per-page** sur les 3 pages traduites via `localeAlternates()` (canonical + fr/en/x-default)
- [x] Style guide respecté : registre formel, no contractions, "researcher" pas "scientist", "domain" réservé au sens produit, "discover/discovery" évité (sauf section "Not a scientific discovery tool" où c'est la négation explicite du label), "revolutionary"/"AI-driven" évités, manifesto + tagline officiels
- [x] Ratio mots EN/FR : 0.94× (dans la cible 0.85-1.0×)
- [x] Translation notes file archivé dans `docs/i18n-translation-decisions/s7-3-bis.md` (post-review) — ~25 choix non-triviaux conservés pour cohérence des sprints i18n suivants
- [x] **Commits livraison** : `4aadcea` (deps remark-gfm) + `6c64ece` (bundles fr/en) + `23825fe` (pages wired)
- [x] **Commits S7.3-bis-fix** (review humaine appliquée 3 mai 2026) : `61d12bf` (fixes 8/8) + `d4fec8a` (notes archivées)
  - Bug fix : 11 étapes pipeline `/how-it-works` traduites EN (PipelineAnimation refactor + namespace `howItWorks.steps`), corpus 200→500 domaines mis à jour FR+EN
  - 6 corrections éditoriales : "Industrial reviewer" → "Industry reviewer", italiques `<em>productive/plausible</em>` réintégrés (split aboutPage.section5_p1 en 5 clés), guillemets « » → " " sur 4 occurrences en.json, reformulation Why SPORE exists (born from / yielding), "linguistic representational space" réintégré, "empirical contradiction" remplace "contradiction by reality"
  - 9/9 tests fonctionnels verts post-fix

**Reporté en S7.3-residual** (volume non-tractable en une session de qualité Nature-grade) :
- [ ] **Home `[locale]/page.tsx`** FeaturedHero + "Comment ça marche en bref" — manifeste/tagline déjà alignés en S7.3-foundation, reste ~30 strings éditoriaux
- [ ] **`/anthology` editorial text** — "Au sommaire" header, preview block, copy autour des 8 titres (~15 strings)
- [ ] **`/custom` CustomClient form** — 457 lignes, ~30 strings (form labels, status messages, error messages)
- [ ] **`/pricing` PricingClient cards** — 3 plan cards + FAQ + manifesto reprise (~30 strings)
- [ ] **`/privacy`, `/legal`** — textes légaux courts mais nécessitent review legal pour version EN
- [ ] **BriefDetailClient.tsx** strings (~80 strings dans 1100 lignes) — overlap avec S7.4 briefs bilingues
- [ ] **Page-level UI strings** restants : briefs sort UI, brief detail tabs, stats cards, anthology preview headings
- [ ] **Hreflangs sur toutes les autres pages** (10 pages restantes) via `generateMetadata` async + `localeAlternates()`

**Note de cadrage** : Le sprint S7.3-bis original demandait ~5-8h de travail pour livrer TOUT en un seul livrable. La portion delivrée (~1.5h) couvre les 3 pages éditoriales les plus visibles et les plus importantes pour la crédibilité (about, methodology, how-it-works). Le reste représente ~3-4h additionnelles pour les pages secondaires + UI strings page-level + hreflangs systématiques. Privilégier cette qualité sur les 3 pages clés et trancher au coup par coup pour les autres est plus défendable qu'un sprint étendu où la qualité dérive.

### 🔄 S7.3-residual Lot 1 — Pages outreach-critical traduites (3 mai 2026)
**Livré** :
- [x] **Home `[locale]/page.tsx`** intégralement traduit (chrome + featured-hero badges + "Other briefs" section + "How SPORE works" pitch + 3 step cards + metrics labels + empty state). Featured-hero brief title et hook restent FR par design (stopgap jusqu'à S7.4 EN brief content). Page passée en async server component avec `setRequestLocale` + `getTranslations` + hreflangs via `localeAlternates('/')`.
- [x] **`/briefs` listing page** : metadata async avec stat-aware locale-specific description, hreflangs `/briefs`. H1 + intro + CTA traduits. `BriefsClient` (client component) wired pour sort UI (Panel/Novelty/Date), search placeholder + aria, count copy avec ICU plural rules (`{n, plural, =0 {no brief} one {# brief published} other {# briefs published}}`), reset link, no-results empty state.
- [x] **`/briefs/[id]` chrome** : `BriefDetailClient` tabs `💡 Comprendre` / `🔬 Recherche` → `💡 Understand` / `🔬 Research`, badge épistémique "AI-generated hypothesis · Pre-publication · To be tested experimentally". Section labels deeper (Hypothesis/Predictions/Protocol/References inside RechercheSections) **deferred** S7.4 puisque le contenu brief lui-même reste FR.
- [x] **`/anthology` AnthologyClient form** : label, email placeholder, button states (Idle/Loading), error messages (invalid email + fallback), GDPR privacy note.
- [x] **6 nouveaux namespaces messages bundles** (FR + EN) : `home.*` étendu, `anthologyPage.*`, `customPage.*`, `briefsPage.*`, `briefDetailPage.*` (~80 keys total).
- [x] **Hreflangs per-page via `localeAlternates()`** sur `/`, `/briefs` (en plus des 3 pages S7.3-bis : `/about`, `/methodology`, `/how-it-works`).
- [x] **Translation notes** archivées dans `docs/i18n-translation-decisions/s7-3-residual-lot1.md` (~229 lignes documentant 15+ choix non-triviaux : featured-hero stopgap, "Inside this anthology", plural=0 stiffness, "industry reviewer" continuité, GDPR-compliant, etc.)
- [x] **Commits** : `da8280f` (namespaces) + `7b1ab79` (home) + `a78b0a4` (briefs list + page) + `eba54d8` (brief detail tabs) + `9757d19` (anthology form) + `750ac92` (translation notes)

**Reporté en S7.3-residual Lot 2** :
- [ ] **`/anthology` page.tsx chrome** : kicker, intro, "Inside this anthology" header, "What you will find in the PDF" + 3 bullets — translations existent en JSON mais page non wirée
- [ ] **`/custom` CustomClient.tsx** (457 lignes, ~30 strings) — form labels, status messages
- [ ] **`/pricing` PricingClient.tsx** (~30 strings) — 3 plan cards + FAQ
- [ ] **`/privacy`, `/legal`** — textes légaux courts (legal review requise pour version EN)
- [ ] **`/stats` UI strings** — cards labels
- [ ] **Brief detail deeper section labels** dans `RechercheSections` / `ComprendreTab` — overlap S7.4
- [ ] **Hreflangs sur `/anthology`, `/custom`, `/pricing`, `/privacy`, `/legal`, `/stats`, `/briefs/[id]`** — Le `/briefs/[id]` necessite refactor du metadata pour gérer 76 routes SSG localisées avec `briefMetaTitle` helper FR-only — risqué, à scoper ensemble avec S7.4

### 📋 S7.3-residual Lot 2 — Pages secondaires (~2-3h, à venir)
- Wiring des translations existantes (anthology page, custom, pricing) + traduction des pages restantes (privacy, legal, stats)
- Hreflangs systématiques sur les 6 pages restantes
- Effort estimé : 2-3h pour pages secondaires + 1h pour `/briefs/[id]` metadata (risque modéré sur le refactor SSG)
- Priorité : moyenne (pages moins traffiquées que home / briefs / brief detail)
- Priorité : haute pour `/anthology`, `/custom`, `/pricing` (visiteurs EN les rencontrent vite), moyenne pour `/privacy` `/legal` (legal review requis)
- Bloque : rien ; le site fonctionne en EN sur les pages structurellement clés

### 📋 S7.3 — Migration pages éditoriales (legacy, voir 🔄 S7.3 ci-dessus)
- [ ] `/about`, `/methodology`, `/how-it-works`, `/anthology`, `/custom`
- [ ] Manifeste home (« Une hypothèse nulle bien documentée… ») — décider si on traduit ou si on garde la version FR comme signature
- [ ] Traduction LLM + review humaine sur les textes longs
- [ ] **hreflangs SEO** sur toutes les pages traduites (lien réciproque FR↔EN dans le `<head>`)

### 🔄 S7.4 — Briefs bilingues (en cours)
- [x] **Phase 1 ✅ — Infrastructure traduction vulgarization (3 mai 2026)**
  - Migration DB : colonne `vulgarization_data_en JSON` ajoutée à `briefs` (try/except idempotent dans `init_database()`)
  - Script `scripts/translate_brief_vulgarization.py` : modes `--brief-id` / `--missing-only` / `--all` / `--dry-run` / `--force`, 9 calls LLM par brief (un par champ feuille), DeepSeek primary + Anthropic fallback via `get_llm_client('translation')`
  - Validation qualité par champ : forbidden discover/discovery, contractions, ratio longueur 0.70-1.20, fragments FR résiduels (STOP la batch)
  - Stratégie retenue : option (b) traduire la vulgarisation FR existante via LLM (pas regénérer ni garder FR-only) — préserve la signature éditoriale et l'analogie déjà calibrée
  - Schema retenu : sous-objet EN parallèle au FR avec clés neutres (`title` au lieu de `title_en`, parce que la colonne porte déjà le suffixe `_en`)
  - Prototype validé sur SPR-2026-816D : titre EN « Metalloproteins come clean: a quantum method to decode their electronic secrets » + 9 champs traduits proprement, 0 warning, 0 fragment FR détecté
  - Coût observé prototype : **$0.0004** (vs estimation $0.005) — DeepSeek prompt cache divise par 10× après le premier call. Coût batch projeté pour 38 briefs : **~$0.02**
  - Commits : `06d3a17` (DB), `9755e4c` (script), `0618964` (README)
- [x] **Phase 1-bis ✅ — Recalibration prompt UK + mix voix (3 mai 2026)**
  - Phase 1 produisait un mix UK/US (« favourable » + « analyzed ») et appliquait le même registre passif partout. Fix en 2 axes :
  - **British English** : ajout d'un bloc SPELLING explicite dans le prompt (favourable / analyse / organise / behaviour / colour / modelled / centred / fibre / haemoglobin / -ise endings / date format « 1 May 2026 »). Validation Python ajoutée pour flagger les spellings US résiduels (warning, fallback opérationnel = post-process Python si le LLM ignore la consigne ; pas nécessaire en pratique sur 816D)
  - **Voix différentielle** : `BASE_PROMPT` + `FIELD_VOICE_GUIDANCE` dict ; `imagine_that` → voix ACTIVE deuxième personne (« you measure », « you cannot », « you must »), tout le reste → voix PASSIVE Nature-grade. Architecture extensible : ajouter une clé au dict pour un nouveau champ avec voix custom
  - Re-traduction de SPR-2026-816D validée : 0 spelling US (validateur strict), UK present (haemoglobin, behaviour, analysed, favourable), `you` présent dans `imagine_that` uniquement, voix passive intacte sur `hypothesis_in_brief` / `why_it_matters` / `reviewers_say` / `concretely.*`
  - Coût Phase 1-bis (re-translation) : **$0.0008** (cache miss sur le nouveau prompt → premier call non caché)
  - Commits : `86d2319` (UK spelling), `f18c33f` (voice differential), `5f86917` (README)
- [x] **Phase 2 ✅ — Backfill batch sur les 39 autres briefs (3 mai 2026)**
  - Ajout du flag `--verbose` au script (durée par brief + coût cumulé + temps total écoulé) pour la visibilité batch
  - Run via `.venv/bin/python scripts/translate_brief_vulgarization.py --missing-only --verbose`
  - **39 briefs traduits, 0 skipped, 0 failed** (le décompte initial du sprint disait 37 ; 2 briefs supplémentaires accumulés depuis)
  - **Coût total : $0.0158** (vs estimation $0.02) — 351 calls LLM, 226 K input / 22 K output ; le prompt cache DeepSeek a divisé le coût d'environ 4× après le premier brief
  - **Durée wall-clock : 9 min** (vs estimation 10 min)
  - **Validation UK** : 0 spelling US sur les 40 briefs (regex strict avec word boundaries)
  - **Validation voix** : 0 brief sans marqueur deuxième-personne dans `imagine_that` (en élargissant le détecteur à `imagine|you|your|yours|yourself` pour couvrir les analogies en troisième-personne dans l'impératif « Imaginez X »), 0 fuite `you/your` dans les champs passifs (`hypothesis_in_brief` / `why_it_matters` / `reviewers_say`) sur les 40 briefs
  - **3 warnings** length-ratio sur des titres (3B42 / 9463 / CDCD ; ratios 0.62-0.69) — compressions éditoriales valides (questions rhétoriques FR → titres Nature-grade EN) ; pas un signal de qualité, simplement le seuil 0.70 est légèrement trop strict pour les titres
  - Sample human review en chat (5 briefs aléatoires : 28B2 / B151 / 6FEB / 0929 / 35F1) — qualité OK
  - Commits Phase 2 : `508d15d` (--verbose flag)
- [x] **Phase 3 ✅ — Frontend wiring spore-web (3 mai 2026)**
  - Branche `feat/s7-4-phase3-frontend-wiring` sur spore-web (tag `pre-s7-4-phase3`)
  - **Backend types + DB adapter** : `VulgarizationEn` interface (clés neutres : `title` au lieu de `title_fr`), `BriefRow.vulgarization_data_en` ajouté à `JSON_COLUMNS`, `briefRowToBrief` parse, `briefToTeaser` forward au teaser
  - **SEO helpers locale-aware** : `briefMetaTitle` / `briefMetaDescription` / `briefOgDescription` acceptent `locale` et préfèrent `vulgarization_en` quand `locale='en'`, fallback en cascade sur FR puis `sharpened.formal_statement`
  - **BriefDetailClient bilingue** :
    - Default tab = `recherche` sur `/en/`, `comprendre` sur `/fr/`
    - Default lang = `en` sur `/en/`, `fr` sur `/fr/`
    - Header title résolu via la langue active du payload
    - ComprendreTab a 3 branches : EN (depuis `vulgarization_en`, 5 sections), FR (existant), fallback summary-based pour briefs legacy
    - `effectiveLang` : flip automatique sur le payload disponible si la langue choisie manque ; toggle conserve le choix utilisateur ; chip « FR fallback » / « EN fallback » signale la substitution
  - **EditorialBriefCard** : `vulg_en.title` / `imagine_that` sur `/en/`, fallback vers FR puis sharpened pour briefs legacy
  - **BriefsClient haystack** : indexe FR + EN ensemble — recherche `métalloprotéines` sur `/en/briefs` ET `metalloproteins` sur `/fr/briefs` matchent le bon brief
  - **Sitemap** : déjà bilingue avant ce sprint (S7.2 + S7.3-residual-fix) ; vérifié 38 briefs × 2 locales = 76 URLs avec `xhtml:link rel="alternate"` complets
  - **6 tests fonctionnels validés** via curl sur build local : H1 EN, OG title EN, JSON payload contient `vulgarization_en`, Recherche default sur `/en/`, FR sections sur `/fr/` (regression), cards EN sur `/en/briefs`, sitemap counts OK, 3 briefs random (28B2 / B151 / 6FEB) rendent EN propre
  - **Commits spore-web** : `2de425d` (backend), `1d9a796` (BriefDetailClient), `e98c880` (cards + haystack), `8204c79` (translation docs)
  - **Décisions notables** archivées dans `docs/i18n-translation-decisions/s7-4.md` (spore-web)
  - **Bug content noté pour follow-up** : `SPR-2026-FBF3` a son titre EN wrappé dans `**...**` markdown (déviation translator). Fix : strip `^\*\*` / `\*\*$` dans `translate_brief_vulgarization.py` puis re-traduire avec `--force`. Hors scope Phase 3 (le rendering est correct, c'est la donnée qui déraille)
- [x] **Phase 3-fix ✅ — Strip bold leak + neighbour titles localisés (3 mai 2026)**
  - **(spore-poc) Helper `_strip_wrapping_bold(text)`** ajouté à `_translate_one_field` (défense en profondeur ; n'agit que sur les wrappers complets, préserve le bold inline)
  - 8 cas unitaires couverts (wrap simple, whitespace autour, bold inline, multiple `**`, no-wrap)
  - Audit complet des 40 briefs × 9 champs feuille : seul `SPR-2026-FBF3.title` était affecté
  - Re-traduction `--force` sur FBF3 : titre clean (« Intelligent stem cells for repairing arteries without causing damage », sans `**`)
  - **(spore-web) Neighbour titles localisés** : sur `/en/briefs/[id]`, les previews previous/next affichaient encore les titres FR (« Des bactéries qui s'allument… ») malgré le contenu EN au-dessus. `neighborTitle(b, locale)` accepte le locale et choisit `vulg_en.title` sur `/en/`, fallback en cascade sur FR puis `sharpened.title`. Branche `feat/s7-4-phase3-fix-neighbours` sur spore-web (non poussée).
  - Coût Phase 3-fix : **$0.0004**
  - Commits : `a4205dd` (spore-poc strip helper), `3e57ddb` (spore-web neighbour titles)
- [ ] **Phase 4 📋 — Pipeline post-fire pour briefs futurs + PDF anthologie EN**
  - Modifier `agents/vulgarization.py` (ou ajouter `agents/translation.py` post-fire) pour produire EN nativement à la création
  - Adapter le pipeline LangGraph post-fire pour appeler la traduction après vulgarization FR
  - Variante EN du PDF anthologie (template `templates/pdf/anthology_en.tex` ou équivalent)
- **Coût estimé total** : ~$0.02 pour les 38 briefs existants ; ~$0.0005 par nouveau brief
- **Bloque** : Phase 3 dépend de Phase 2 (backfill) ; Phase 4 indépendant et peut être priorisé séparément

---

## Maintenance et dette technique

### ✅ S6.4 — Recalibrer ReviewerAgent override (2 mai 2026)
- **Diagnostic** : 12 hypothèses sur 13 forcées en `poubelle` depuis le 24 avril → 0 brief publié pendant 8 jours. Distribution réelle 73/20/8 (poubelle/intéressant/a_tester) vs cible historique 20/63/17.
- **Cause** : drift de la calibration LLM (DeepSeek) sur l'attribution de `hallucination_risk`, ou fallout de l'expansion corpus 200→500 domaines qui a déplacé la distribution. Pas un bug, une calibration qui n'est plus alignée.
- **Fix** : règle conjuguée plus défendable épistémiquement (kill si composite très bas OU composite moyen + halluc haut OU halluc extrême). Logique extraite dans `agents.reviewer.evaluate_override(composite, hallucination_risk) -> Optional[(verdict, reason)]` pour tester sans LLM.
- **Backtest** sur les 13 hypothèses depuis le 24 avril :
  - Ancienne règle : 12 poubelle, 1 intéressant
  - Nouvelle règle : 3 poubelle (les vraiment faibles), 10 intéressant
- **Tests** : 9 tests unitaires verts (`tests/test_reviewer_override.py`), couvrent les 3 paths de kill + les boundaries (composite=0.35, halluc=0.65) + un cas de régression (SPORE-2026-04-26-de5ee40d).
- **Suivi** : observer la distribution sur 5-7 prochains runs cron, ajuster si dérive.
- **Commits** : `6bc95f4` (fix + extraction helper) + `13046c1` (tests)

### ✅ Hotfix S6.1-bis — Outreach workflow fixes (1er mai 2026)
- **Tracking CSV** : création automatique au premier run garantie via `ensure_tracking_csv()` appelée en début de `main()`. Bug d'origine : le script ne créait le fichier que via la branche append, donc une exécution sans nouveau draft (stub sans evidence_base, ou run idempotent où tout est skip) laissait le fichier inexistant.
- **Template par défaut basculé en EN** : l'écrasante majorité des chercheurs cités sont non-francophones (Max Planck, USA, Italie, Japon, Chine). EN désormais default ; FR via `--lang fr` pour les équipes francophones identifiées (CNRS, INRAE, INSERM, UCLouvain, UQ, etc.).
- **Variante FR conservée** : ancien template renommé en `templates/outreach_email_fr.md`, nouveau `templates/outreach_email_en.md`. Variable `{brief_title_en}` ajoutée (pioche dans `sharpened.title` qui est toujours en EN, fallback sur `title_fr` avec warning stderr).
- **Idempotence** : dédoublonnage toujours par `(brief_id, author_name)` indépendamment de la langue — basculement de langue post-extraction nécessite suppression manuelle de la row CSV (documenté dans `scripts/README_outreach.md`).
- **Commits** : `c16a676` (fix CSV + lang infra dans le script) + `bac43a7` (template EN) + `782f93f` (README)

### 📋 Méta-bug architecture L1↔runtime (identifié S6)
- **Découvert par** : sprints S5/S6 (audits pipeline)
- **Symptôme** : ≥3 paramètres du génome `data/l0_genome.yaml` sont mutés par L1 mais jamais consommés par le pipeline runtime. Le L1 fait des modifications cosmétiques sans effet fonctionnel.
- **Paramètres fantômes confirmés** : `randomness.distance_max`, `randomness.distance_min`, `randomness.strategy_weights.semantic_distance`. Probablement d'autres (audit non exhaustif).
- **Bug secondaire** : l'auto-rollback L1 (commit `b42f5bb` du 23 avril) n'a jamais déclenché en 5 jours malgré une chute apparente de production. Hypothèse : ses signaux d'observation surveillent `hypotheses_generated` (resté stable à 5-11/jour) et pas `briefs_published_per_day` (tombé à 0/jour pour les briefs L0).
- **Conséquences** : auto-tuning partiellement décoratif, faux signal de "système qui s'améliore", investigation S5/S6 partie sur fausses pistes (mutations L1 fantômes accusées à tort).
- **Sprint candidat** : S6b — audit complet du génome (chaque clé : consommée ou fantôme), décisions par clé (supprimer / wire au runtime / déplacer en config legacy), fix de l'auto-rollback observer pour qu'il surveille les bonnes métriques.
- **Effort estimé** : 1-2 jours
- **Priorité** : moyenne (n'affecte pas les utilisateurs directement, mais l'auto-tuning est inutilisable en l'état)

### ⏳ Cleanup `data/spore.db.pre-s4.bak`
- **Échéance** : 5 mai 2026 (J+7 après S4)
- **Action** : `rm data/spore.db.pre-s4.bak` après confirmation que les nouvelles vulgarizations en prod n'ont pas créé de régression utilisateur

### ⏳ Cleanup `data/spore.db.pre-n1-1.bak`
- **Échéance** : 4 mai 2026 (J+7 après S1)
- **Action** : `rm data/spore.db.pre-n1-1.bak` après confirmation que rollback ne sera plus nécessaire

### 📋 Stub briefs robustness
- **Bug latent** : BriefJsonLd pourrait crasher si `brief.domains` vide ou champs manquants pour un stub
- **Priorité** : faible (pas constaté en prod)

### 📋 Image JSON-LD spécifique par brief
- **Actuellement** : `og-default.png` générique pour tous les briefs
- **Cible** : image dédiée par brief (ou par paire de domaines)
- **Lié à** : amélioration SEO/partage social

### 📋 Surveillance Search Console post-S1
- **Action** : vérifier H+24, H+48, H+72 que les redirections 308 sont bien indexées sans erreur explosive
- **Échéance** : déclenchée le 27 avril, fenêtre jusqu'au 30 avril

### 📋 Setup remote git pour autres projets perso
- **Statut** : décision pendante (besoin réel à valider)
- **Sprint hypothétique** : étendre la logique de S0 aux autres repos

---

## Méta — comment maintenir ce backlog

1. À la fin de chaque sprint : marquer les items `N` couverts comme `✅ Done`, ajouter le sprint dans la section "Sprints livrés"
2. Les nouvelles découvertes pendant un sprint (bugs latents, idées) : ajouter en `📋 Backlog` immédiatement, ne pas perdre la trace
3. Avant chaque sprint : vérifier que ce backlog est à jour, choisir 1-3 items, créer une branche `feat/sX-{slug}`
4. Le doc `docs/SPORE_USER_TESTS_SYNTHESIS_V1.md` reste **immuable** — c'est l'archive du test utilisateur. Ce backlog évolue.
