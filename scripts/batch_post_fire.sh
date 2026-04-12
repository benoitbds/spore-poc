#!/bin/bash
# Batch post-fire runner — processes all fire hypotheses without brief
# Writes a summary to /tmp/batch_post_fire_summary.txt

set -u
cd /home/baq/Projects/spore-poc

SUMMARY=/tmp/batch_post_fire_summary.txt
LOG=/tmp/batch_post_fire.log

: > "$SUMMARY"
: > "$LOG"

IDS=$(sqlite3 data/spore.db "SELECT id FROM hypotheses WHERE json_extract(auto_feedback_json, '\$.verdict') = 'a_tester' AND id NOT IN (SELECT hypothesis_id FROM briefs) ORDER BY id")

total=$(echo "$IDS" | grep -c .)
echo "[batch] starting post-fire for $total hypotheses" | tee -a "$LOG"
echo "start=$(date -Is) total=$total" >> "$SUMMARY"

i=0
for id in $IDS; do
    i=$((i + 1))
    echo "" | tee -a "$LOG"
    echo "[$i/$total] $id — $(date -Is)" | tee -a "$LOG"
    echo "---" | tee -a "$LOG"

    if .venv/bin/python cli.py post-fire --hypothesis-id "$id" >> "$LOG" 2>&1; then
        echo "[$i/$total] $id done" >> "$SUMMARY"
    else
        echo "[$i/$total] $id FAILED (exit $?)" >> "$SUMMARY"
    fi
done

echo "" | tee -a "$LOG"
echo "[batch] complete at $(date -Is)" | tee -a "$LOG"
echo "end=$(date -Is)" >> "$SUMMARY"
