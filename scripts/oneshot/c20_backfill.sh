#!/bin/bash
# C20 — Backfill des vulgarisations FR puis EN de SPR-2026-4469, -FBCA, -A2C5.
#
# À EXÉCUTER PAR L'HUMAIN. Aucune écriture n'a été faite par Claude.
#
# Le script s'arrête à la première erreur (set -e) et marque une pause après
# les dry-runs pour que vous relisiez le texte généré avant qu'il ne soit
# publié. Les corrections en base sont publiques immédiatement, sans rebuild.
set -euo pipefail
cd /home/baq/Projects/spore-poc

IDS=(SPR-2026-4469 SPR-2026-FBCA SPR-2026-A2C5)
PY=.venv/bin/python

# ── 1. Sauvegarde (WAL : checkpoint AVANT .backup) ───────────────────
echo "── 1. sauvegarde"
sqlite3 data/spore.db "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 data/spore.db ".backup 'data/spore.db.pre-c20-backfill.bak'"
tar czf outputs/briefs.pre-c20.tar.gz \
    outputs/briefs/SPR-2026-4469.json \
    outputs/briefs/SPR-2026-FBCA.json \
    outputs/briefs/SPR-2026-A2C5.json
ls -la data/spore.db.pre-c20-backfill.bak outputs/briefs.pre-c20.tar.gz

# ── 2. État avant ────────────────────────────────────────────────────
echo
echo "── 2. état avant (attendu : 1|1 sur les trois lignes)"
sqlite3 data/spore.db "
SELECT id,
       (vulgarization_data    IS NULL OR vulgarization_data='')    AS fr_manquante,
       (vulgarization_data_en IS NULL OR vulgarization_data_en='') AS en_manquante
FROM briefs WHERE id IN ('SPR-2026-4469','SPR-2026-FBCA','SPR-2026-A2C5');"

# ── 3. FR — dry-run ──────────────────────────────────────────────────
echo
echo "── 3. FR, dry-run (génère, n'écrit rien)"
for ID in "${IDS[@]}"; do
  echo "   ── $ID"
  $PY -m scripts.backfill_vulgarization --brief-id "$ID" --dry-run
done

echo
read -r -p "Relisez les trois vulgarisations ci-dessus. Écrire ? [oui/non] " OK
[ "$OK" = "oui" ] || { echo "abandon, rien écrit."; exit 0; }

# ── 4. FR — écriture ─────────────────────────────────────────────────
echo
echo "── 4. FR, écriture"
for ID in "${IDS[@]}"; do
  echo "   ── $ID"
  $PY -m scripts.backfill_vulgarization --brief-id "$ID"
done

# ── 5. EN — dry-run puis écriture ────────────────────────────────────
# L'anglais vient APRÈS le français : la traduction prend la vulgarisation FR
# en entrée.
echo
echo "── 5. EN, dry-run"
for ID in "${IDS[@]}"; do
  echo "   ── $ID"
  $PY -m scripts.translate_brief_vulgarization --brief-id "$ID" --dry-run
done

echo
read -r -p "Écrire les traductions EN ? [oui/non] " OK_EN
[ "$OK_EN" = "oui" ] || { echo "FR écrit, EN abandonné."; exit 0; }

echo
echo "── 6. EN, écriture"
for ID in "${IDS[@]}"; do
  echo "   ── $ID"
  $PY -m scripts.translate_brief_vulgarization --brief-id "$ID"
done

# ── 7. Vérification ──────────────────────────────────────────────────
echo
echo "── 7. vérification"

echo '   a) les trois lignes (attendu : 0|0 partout)'
sqlite3 data/spore.db "
SELECT id,
       (vulgarization_data    IS NULL OR vulgarization_data='')    AS fr_manquante,
       (vulgarization_data_en IS NULL OR vulgarization_data_en='') AS en_manquante
FROM briefs WHERE id IN ('SPR-2026-4469','SPR-2026-FBCA','SPR-2026-A2C5');"

echo '   b) plus aucun brief publié sans vulgarisation FR (attendu : 0)'
sqlite3 data/spore.db "
SELECT COUNT(*) FROM briefs
 WHERE status='complete' AND COALESCE(is_stub,0)=0
   AND (vulgarization_data IS NULL OR vulgarization_data='');"

echo '   c) les sidecars portent le bloc et restent du JSON valide'
for ID in "${IDS[@]}"; do
  $PY -c "
import json
d = json.load(open('outputs/briefs/$ID.json'))
v = d.get('vulgarization_fr') or {}
print(f'      $ID  bloc={bool(v)}  titre_fr={str(v.get(\"title_fr\",\"\"))[:50]!r}')"
done

echo '   d) les pages, sans rebuild'
for ID in "${IDS[@]}"; do
  printf "      /fr/briefs/%s : " "$ID"
  curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:3012/fr/briefs/$ID"
done

echo
echo "── terminé. Si tout est vert, committer le diff des trois sidecars :"
echo "   git add outputs/briefs/SPR-2026-{4469,FBCA,A2C5}.json && git commit"
