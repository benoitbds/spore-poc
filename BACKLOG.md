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

### 🔧 N1.6 — Repositionner Collision sur mesure
- **Effort** : 1h
- **Cibles** :
  - Première position sur `/pricing` (avant Brief unitaire et Pack 5)
  - Lien "Collision sur mesure" dans la nav principale (top nav)
- **Fichiers** : `src/app/pricing/PricingClient.tsx`, `src/components/Header.tsx`

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

### 📋 N2.3 — Page "À propos" avec un visage humain
- **Effort** : 0.5 jour
- **Contenu** : nom (Benoît Baqué de Sariac), parcours, statut solo developer, philosophie, lien LinkedIn/GitHub

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

### 📋 N2.7 — Documenter la métrique de Nouveauté
- **Effort** : 0.5 jour
- **Cible** : info-bulle au survol/tap sur le score, page `/methodology` ou section dans `/how-it-works`

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

### 🤔 D3 — Modèle pricing : unitaire vs abonnement
- **Données** : Christophe pousse Substack 15€/mois, Margaux paiera 9€ unitaire si CTA inline, Aïcha+Hugo ne paieront jamais
- **Question secondaire** : faut-il un mécanisme indirect de monétisation (partage/viralité) pour les profils non-payants ?

### 🤔 D4 — Boucle de retour expérimental
- **Lien** : N3.6
- **Question** : ouvrir maintenant ou après pricing + diversification thématique ?

---

## Maintenance et dette technique

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
