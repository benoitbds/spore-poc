# `translate_brief_vulgarization.py` — Brief vulgarisation FR→EN

Translates the FR `vulgarization_data` of a research brief into
Nature-grade EN, writes the result to `vulgarization_data_en`. Part
of S7.4 Phase 1 — the infrastructure for bilingual brief content.

## Quick start

```bash
# Dry-run on a single brief — prints output, writes nothing
.venv/bin/python scripts/translate_brief_vulgarization.py --brief-id SPR-2026-816D --dry-run

# Write the EN payload for a single brief
.venv/bin/python scripts/translate_brief_vulgarization.py --brief-id SPR-2026-816D

# Translate every brief that does not yet have an EN payload
.venv/bin/python scripts/translate_brief_vulgarization.py --missing-only

# Re-translate a brief that already has an EN payload
.venv/bin/python scripts/translate_brief_vulgarization.py --brief-id SPR-2026-816D --force
```

`--brief-id`, `--all` and `--missing-only` are mutually exclusive.

## Modes

| Flag | Behaviour |
|---|---|
| `--brief-id <ID>` | Translate exactly one brief. |
| `--missing-only` | Translate every brief where `vulgarization_data IS NOT NULL` and `vulgarization_data_en IS NULL`. The default for batch backfill. |
| `--all` | Translate every brief that has FR vulgarisation data, including those already translated (use with `--force`). |
| `--dry-run` | Print the EN payload + cost summary, do not touch the DB. |
| `--force` | Re-translate even if `vulgarization_data_en` is already populated. Without `--force` the script is idempotent — re-running on an already-translated brief is a no-op. |

## What it translates

`vulgarization_data` JSON (FR source) carries 9 leaf text fields:

```text
vulgarization_data
├── title_fr                                  → title
├── hypothesis_in_brief                       → hypothesis_in_brief
├── why_it_matters                            → why_it_matters
├── imagine_that                              → imagine_that
├── reviewers_say                             → reviewers_say
└── concretely
    ├── intro                                 → concretely.intro
    ├── phase1                                → concretely.phase1
    ├── phase2                                → concretely.phase2
    └── phase3                                → concretely.phase3
```

The EN payload mirrors the same shape but with neutral keys — `title`
not `title_fr`, since the column itself is `_en`. SQL access is
straightforward:

```sql
SELECT json_extract(vulgarization_data_en, '$.title')          FROM briefs WHERE id = ?;
SELECT json_extract(vulgarization_data_en, '$.imagine_that')   FROM briefs WHERE id = ?;
SELECT json_extract(vulgarization_data_en, '$.concretely.phase1') FROM briefs WHERE id = ?;
```

## Cost and duration

DeepSeek V3.2 (primary), Anthropic Claude Sonnet 4 (automatic
fallback). Each leaf field is one LLM call so a brief produces 9
calls (~4 K input tokens, ~700 output tokens).

| Scope | Calls | Cost | Wall time |
|---|---|---|---|
| Single brief | 9 | ~$0.0005 | ~15 s |
| All 38 briefs (batch backfill) | ~340 | **~$0.02** | ~10 min |

The earlier estimate of ~$0.005 per brief / ~$0.20 total was
conservative; observed cost is ~10× lower because DeepSeek's prompt
cache catches the static prompt template after the first call (the
LLM client logs `cache_hit=True` from the second call onward).

## Idempotency

By default the script silently skips briefs that already have a
`vulgarization_data_en` payload. To re-translate a single brief after
adjusting the prompt, the cleanest flow is:

```bash
# Option A: explicit --force
.venv/bin/python scripts/translate_brief_vulgarization.py --brief-id SPR-2026-816D --force

# Option B: clear the column then run normally
sqlite3 ~/Projects/spore-poc/data/spore.db \
  "UPDATE briefs SET vulgarization_data_en = NULL WHERE id = 'SPR-2026-816D';"
.venv/bin/python scripts/translate_brief_vulgarization.py --brief-id SPR-2026-816D
```

To re-translate an entire batch (e.g. after a prompt revision):

```bash
sqlite3 ~/Projects/spore-poc/data/spore.db \
  "UPDATE briefs SET vulgarization_data_en = NULL;"
.venv/bin/python scripts/translate_brief_vulgarization.py --missing-only
```

## Style guide (summary)

The script's prompt enforces SPORE's EN editorial register:

- **Nature editorial**: precise, economical, formal authority. No
  contractions ("do not", not "don't"). No marketing-speak. No
  first-person plural ("SPORE" or rephrase, not "we").
- **Forbidden vocabulary**: "discover" / "discovery" / "discoveries"
  (use "finding", "advance", or rephrase). Allowed only inside an
  explicit negation, e.g. "not a discovery", per the precedent set
  by the `/about` page.
- **Preferred vocabulary**: hypothesis, hypotheses, researcher,
  yields, "verified through Semantic Scholar". Domain (SPORE
  product term), collision (domain meeting), brief (research brief),
  panel review, panel reviewer.
- **Vulgarisation tone**: preserve the analogy ("Imagine that..."),
  educated-but-non-expert audience, no over-simplification.
- **Preservation**: proper names, numbers, units, dates, markdown
  formatting (bold, italics, lists) all carry through verbatim.

The full prompt lives at the top of
`scripts/translate_brief_vulgarization.py` (`TRANSLATION_PROMPT`).

## Quality validation

For each translated field the script runs four heuristic checks:

1. **Forbidden bare "discover/discovery"** — flagged unless every
   occurrence is inside an explicit negation. `WARNING` only; the
   field is still written.
2. **Contractions** (do not, can not, etc.) — flagged. `WARNING`
   only.
3. **Length ratio** — EN char-length divided by FR char-length must
   sit in `[0.70, 1.20]`. The earlier sprint spec called for
   `[0.85, 1.10]` but EN is systematically shorter than FR for this
   register; broadened to flag only egregiously truncated or
   doubled output. `WARNING` only.
4. **Residual French** — title fields starting with FR articles
   (`que`, `qui`, `le`, `la`, `les`, `une`, `un`, `des`, `du`,
   `de`, `aux`, `avec`, `pour`, `sans`, `sur`, `dans`) or any field
   containing FR fragments (`c'est`, `qu'il`, `pourquoi`,
   `parce que`, common FR scientific terms with diacritics, etc.).
   This is a **STOP** condition — the batch halts immediately so
   the operator can recalibrate the prompt before silently-FR
   payloads land in the EN column.

Warnings are printed to stdout per brief with the failing field path
and the offending pattern. They do not block writes.

## Logs

The script uses the project's `structlog` setup. Per-call usage
appears as `api_call agent=translate_vulgarization …` records.
Field-level outcomes appear as `translated_field
brief_id=… field=… en_preview=…` records. Cost summary lands at
the end of the run via the global `TokenTracker.summary()`.

## Related code

- DB column added in `storage/database.py` (S7.4 Phase 1 migration —
  `vulgarization_data_en JSON`).
- FR vulgarisation generation lives in `agents/vulgarization.py`
  (post-fire pipeline). FR backfill via
  `scripts/backfill_vulgarization.py`.
- Frontend wiring (Phase 3) will read `vulgarization_data_en` when
  the active locale is `en`. Until then the column populates but is
  unused.
