#!/bin/bash
# Test i18n — curl toutes les pages publiques et cherche des labels EN résiduels

BASE="https://spore-research.com"
ERRORS=0

# Liste des termes anglais qui NE DEVRAIENT PLUS apparaître dans le HTML public
# (hors vue EN délibérée, hors attributs techniques, hors noms de domaines scientifiques)
FORBIDDEN_EN=(
  "Domain Expert"
  "Contrarian"
  "Funding Strategist"
  "Critical path"
  "Quick start"
  "Full protocol"
  "Gap Manifest"
  "Gaps ouverts"
  "Data disponible"
  "devil's advocate"
  "angel's advocate"
  "Literature Grounding"
  "Hypothesis Sharpening"
  "weak_reject"
  "weak_accept"
  "strong_accept"
  "publish_brief"
  "revise_and_resubmit"
  ">Stats<"
  "Novelty moyen"
  "Panel consensus"
  ">Curated<"
  ">Reviewer<"
  ">Gate<"
  ">Synthesis<"
  ">Critics<"
  ">Curator<"
  "bridges"
)

# Liste des termes FR qui DOIVENT apparaître
REQUIRED_FR=(
  "Statistiques"
  "Découvertes"
  "Comment ça marche"
  "Mentions légales"
  "Confidentialité"
  "Offre de lancement"
)

# Pages à tester (publiques, pas besoin d'auth)
PAGES=(
  "/"
  "/briefs"
  "/pricing"
  "/custom"
  "/how-it-works"
  "/stats"
  "/legal"
  "/privacy"
  "/account"
)

echo "=== TEST i18n — Labels FR/EN ==="
echo ""

# Test 1 : Termes anglais interdits
echo "--- Termes anglais interdits ---"
for page in "${PAGES[@]}"; do
  HTML=$(curl -s "${BASE}${page}")
  for term in "${FORBIDDEN_EN[@]}"; do
    # Chercher dans le HTML visible (pas dans les scripts JSON inline)
    # Exclure les blocs <script> pour ne pas matcher le JSON-LD ou Next.js data
    CLEAN=$(echo "$HTML" | perl -0777 -pe 's{<script[^>]*>.*?</script>}{}gs')
    if echo "$CLEAN" | grep -qi "$term"; then
      echo "❌ TROUVÉ '${term}' sur ${page}"
      ERRORS=$((ERRORS + 1))
    fi
  done
done

if [ $ERRORS -eq 0 ]; then
  echo "✅ Aucun terme anglais interdit trouvé"
fi

echo ""

# Test 2 : Termes FR requis sur la homepage
echo "--- Termes FR requis (homepage + nav) ---"
HOMEPAGE=$(curl -s "${BASE}/")
for term in "${REQUIRED_FR[@]}"; do
  if echo "$HOMEPAGE" | grep -q "$term"; then
    echo "✅ '${term}' présent"
  else
    echo "❌ '${term}' ABSENT de la homepage"
    ERRORS=$((ERRORS + 1))
  fi
done

echo ""

# Test 3 : Pages brief — vérifier les labels du teaser (pas besoin d'auth)
echo "--- Labels brief teaser (vue FR) ---"
BRIEF=$(curl -s "${BASE}/briefs/SPR-2026-5301")
BRIEF_CLEAN=$(echo "$BRIEF" | perl -0777 -pe 's{<script[^>]*>.*?</script>}{}gs')

BRIEF_FR_REQUIRED=(
  "HYPOTHÈSE EN QUELQUES MOTS"
  "POURQUOI C'EST IMPORTANT"
  "IMAGINEZ QUE"
  "ET CONCRÈTEMENT"
  "CE QUE DISENT LES RELECTEURS"
  "Comprendre"
  "Recherche"
)

for term in "${BRIEF_FR_REQUIRED[@]}"; do
  if echo "$BRIEF_CLEAN" | grep -qi "$term"; then
    echo "✅ '${term}' présent sur brief"
  else
    echo "❌ '${term}' ABSENT du brief"
    ERRORS=$((ERRORS + 1))
  fi
done

echo ""

# Test 4 : Sitemap et robots.txt
echo "--- SEO ---"
SITEMAP=$(curl -s "${BASE}/sitemap.xml")
if echo "$SITEMAP" | grep -q "spore-research.com"; then
  echo "✅ Sitemap pointe vers spore-research.com"
else
  echo "❌ Sitemap ne pointe pas vers spore-research.com"
  ERRORS=$((ERRORS + 1))
fi

if echo "$SITEMAP" | grep -q "baq.ovh"; then
  echo "❌ Sitemap contient encore baq.ovh"
  ERRORS=$((ERRORS + 1))
else
  echo "✅ Pas de baq.ovh dans le sitemap"
fi

ROBOTS=$(curl -s "${BASE}/robots.txt")
if echo "$ROBOTS" | grep -q "spore-research.com"; then
  echo "✅ robots.txt pointe vers spore-research.com"
else
  echo "❌ robots.txt ne pointe pas vers spore-research.com"
  ERRORS=$((ERRORS + 1))
fi

echo ""

# Test 5 : Canonical et OG
echo "--- Canonical et OG ---"
if echo "$HOMEPAGE" | grep -q 'rel="canonical" href="https://spore-research.com"'; then
  echo "✅ Canonical homepage OK"
else
  echo "❌ Canonical homepage manquant ou incorrect"
  ERRORS=$((ERRORS + 1))
fi

if echo "$HOMEPAGE" | grep -q 'og:image'; then
  echo "✅ og:image présent"
else
  echo "❌ og:image manquant"
  ERRORS=$((ERRORS + 1))
fi

OG_IMG=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/og-default.png")
if [ "$OG_IMG" = "200" ]; then
  echo "✅ og-default.png accessible (200)"
else
  echo "❌ og-default.png inaccessible (${OG_IMG})"
  ERRORS=$((ERRORS + 1))
fi

echo ""

# Test 6 : Mentions légales anonymisées
echo "--- Mentions légales ---"
LEGAL=$(curl -s "${BASE}/legal")
if echo "$LEGAL" | grep -qi "Baqué"; then
  echo "❌ Nom personnel encore visible dans /legal"
  ERRORS=$((ERRORS + 1))
else
  echo "✅ Nom personnel absent de /legal"
fi

if echo "$LEGAL" | grep -qi "Construit par Bac"; then
  echo "❌ 'Construit par Bac' encore visible"
  ERRORS=$((ERRORS + 1))
else
  echo "✅ 'Construit par Bac' absent"
fi

echo ""
echo "=== RÉSULTAT FINAL ==="
if [ $ERRORS -eq 0 ]; then
  echo "✅ TOUT EST BON — 0 erreur"
else
  echo "❌ ${ERRORS} erreur(s) trouvée(s)"
fi
