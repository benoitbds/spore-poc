# S8.2 — Cron user changes (2026-05-09)

User crontab modifications applied as part of Sprint S8.2 stabilisation.
Crontab is not under version control; this document is the canonical
record of what changed and how to roll back.

## Change applied

The L1 evolve cron (daily 04:00) is **commented out** with prefix
``# DISABLED S8.2 (pending L1 fitness redesign):``. The L0 autopilot
cron (daily 03:00) is **untouched** and continues to run.

### Before

```
0 3 * * * cd /home/baq/Projects/spore-poc && /home/baq/Projects/spore-poc/.venv/bin/python cli.py autopilot -n 100 --domain all_science >> /var/log/spore.log 2>&1 ; ... notify.py --type l0
0 4 * * * cd /home/baq/Projects/spore-poc && /home/baq/Projects/spore-poc/.venv/bin/python cli.py evolve >> /var/log/spore_l1.log 2>&1 ; ... notify.py --type l1
```

### After

```
0 3 * * * cd /home/baq/Projects/spore-poc && /home/baq/Projects/spore-poc/.venv/bin/python cli.py autopilot -n 100 --domain all_science >> /var/log/spore.log 2>&1 ; ... notify.py --type l0
# DISABLED S8.2 (pending L1 fitness redesign): 0 4 * * * cd /home/baq/Projects/spore-poc && /home/baq/Projects/spore-poc/.venv/bin/python cli.py evolve >> /var/log/spore_l1.log 2>&1 ; ... notify.py --type l1
```

## How the change was applied

```bash
# 1. Backup (kept at /tmp/crontab_backup_pre_s8_2.txt)
crontab -l > /tmp/crontab_backup_pre_s8_2.txt

# 2. Comment the L1 line in place
crontab -l | sed 's|^\(0 4 \* \* \* .*evolve.*\)$|# DISABLED S8.2 (pending L1 fitness redesign): \1|' | crontab -

# 3. Verify
crontab -l | grep -E "spore|cli.py|evolve|autopilot|DISABLED"
```

## How to roll back

Two options.

### Restore from backup

```bash
crontab /tmp/crontab_backup_pre_s8_2.txt
crontab -l | grep evolve    # confirm the L1 line is uncommented
```

### Or uncomment manually

```bash
crontab -l | sed 's|^# DISABLED S8.2 (pending L1 fitness redesign): ||' | crontab -
```

## Rationale (short form, see BACKLOG S8.2 for full context)

The L1 cron mutated the L0 genome 2x/day for 17 days in a direction that
degraded L0 critic scores: temperature 0.7 -> 0.95, distance_min 0.30 ->
0.15, distance_max 0.85 -> 0.90, top_percent oscillated 0.10 / 0.12 /
0.08 / 0.15. Production collapsed to 0 a_tester verdicts since 24 April
and 0 briefs published from the daily cron over the same window.

S8.2 reverts the genome to the productive 22 April state and pauses
the L1 cron until the fitness function can be redesigned. The L0 cron
continues to run on the reverted genome.

## Re-enabling

Roll back the crontab change once Sprint S8.3 lands the redesigned
fitness function. The roll-back command is one-liner above; pair it
with a tag on spore-poc (``post-s8-3-l1-fitness-redesigned``) for
audit traceability.
