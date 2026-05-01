# Outreach researcher-to-researcher (N4.4)

Workflow semi-automatique pour contacter les auteurs cités dans les briefs
SPORE et leur proposer un retour d'expert sur l'hypothèse formulée.

Pas d'envoi automatique. Le script génère des brouillons d'emails personnalisés
prêts à copier-coller. L'envoi reste manuel — tu choisis quel auteur contacter,
tu trouves son email, tu envoies, tu mets à jour le tracking CSV.

## Lancer le script

Pour un brief précis :

```bash
cd ~/Projects/spore-poc
.venv/bin/python scripts/outreach_extract.py --brief-id SPR-2026-816D
```

Pour tous les briefs publiés (38 actuellement) :

```bash
cd ~/Projects/spore-poc
.venv/bin/python scripts/outreach_extract.py --all-published
```

Output :
- `outputs/outreach/{brief_id}/{author_lastname}_{doi_short}.md` — un fichier par auteur
- `outputs/outreach/_tracking.csv` — ledger central, append-only

Le script est idempotent : relancer ne réécrit pas les rows déjà présentes
dans le CSV (tes annotations manuelles sont préservées). Les fichiers .md
en revanche sont réécrits à chaque run avec le contenu actuel.

## Trouver l'email d'un auteur

Pas d'auto-extraction d'email à ce stade (priorité explicite à l'humain dans
la boucle pour calibrer la qualité). Sources fiables, ordre suggéré :

1. **Google Scholar** — `scholar.google.com/citations?user=<id>` affiche
   souvent l'email institutionnel sur la fiche profil
2. **Page institutionnelle du labo** — souvent listée dans le PDF du paper
   (en-tête, footer, ou section affiliations)
3. **OpenAlex** — `https://api.openalex.org/authors?search=<name>` retourne
   des metadata structurées (institution, ORCID) qui aident à confirmer la
   bonne personne (les homonymes existent)
4. **ResearchGate** — formulaire de contact à défaut d'email direct
5. **Twitter/Bluesky** — DM possible si l'auteur est actif

Une fois l'email trouvé, le saisir dans la colonne `email_address` du CSV
puis flipper `email_sent` à `True` après envoi.

## Cadence et déontologie

**Cadence cible** : 5-10 emails / semaine, jamais plus de 3 par brief
(les auteurs principaux uniquement — le script cap déjà à 3 auteurs par
paper, mais un brief peut citer plusieurs papers, donc en pratique
sélectionner toi-même les 3 plus pertinents par brief).

**Règles à ne pas enfreindre** :

- **JAMAIS plus d'un email par auteur sur la même hypothèse** — si tu
  contactes Frank Neese sur SPR-2026-816D, tu ne le re-contactes pas sur le
  même brief, même après 6 semaines.
- **JAMAIS plus de 2 relances** sur un même brief, espacées d'au moins
  14 jours. Pas de réponse après 2 relances = pas de réponse, on passe.
- **Un même auteur peut être contacté sur plusieurs briefs différents**,
  mais avec un intervalle d'au moins 30 jours entre deux briefs distincts.
  Utiliser le CSV pour vérifier l'historique.
- **Pas de mailing-list, pas de bcc** : un email = un destinataire,
  personnalisé avec le nom et la référence du paper concerné.
- **Pas de pitch commercial** dans le corps de l'email. Le template actuel
  est explicitement non-commercial — ne pas le modifier dans ce sens.

## Marquer un envoi dans le tracking

Le CSV `outputs/outreach/_tracking.csv` se met à jour à la main. Workflow :

1. Tu choisis un brouillon dans `outputs/outreach/{brief_id}/`
2. Tu trouves l'email de l'auteur (cf section ci-dessus)
3. Tu copies-colles dans ton client mail, vérifies, ajoutes l'email, envoies
4. Tu ouvres le CSV (LibreOffice / Excel / VSCode) et tu mets à jour la row
   correspondante :
   - `email_sent` → `True`
   - `email_sent_date` → date du jour ISO (ex `2026-05-08`)
   - `email_address` → l'email utilisé
5. Si tu reçois une réponse, tu remplis :
   - `response_received` → `True`
   - `response_date` → date ISO
   - `response_summary` → 1-2 phrases sur le contenu (positif / négatif /
     demande de précision / décline)
6. Si tu relances après J+14 sans réponse :
   - `followed_up` → `True`

Le CSV est dans `.gitignore` pour ne pas exposer les emails personnels en
public — il reste local sur ta machine.

## Métriques à suivre

À évaluer mensuellement :

- **Taux de réponse** = `response_received=True` / `email_sent=True`
  (cible > 8%, calibrée sur les benchmarks cold email académique)
- **Taux de réponse positive** = réponses qui valident / questionnent /
  proposent une discussion (vs. décline ou OoO automatique)
- **Témoignages publics** = auteurs qui acceptent que tu cites leur retour
  sur le site ou en post LinkedIn (cible : 1+ par mois)

Le CSV est suffisant pour calculer ces métriques avec un coup d'œil. Si la
cadence augmente au-delà de 50 emails/mois, prévoir un dashboard Streamlit
ou Airtable pour l'agrégation (hors scope sprint actuel).

## Limites connues

- Le split first_name / last_name est basique (premier mot vs. reste).
  Échoue mal sur certains noms multi-mots ("Jean-François Le Grand"). Vérifie
  toujours dans le brouillon avant d'envoyer.
- Pas d'extraction automatique de l'email institutionnel — c'est un choix
  délibéré (qualité > volume).
- Pas de detection des doublons inter-briefs au moment de la génération.
  Le CSV permet de les détecter post-hoc en triant sur `author_name`.
