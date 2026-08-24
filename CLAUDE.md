# SPORE — Context for Claude Code

## What is this project?
SPORE (Système de Production d'Opportunités de Recherche par Exploration) is a multi-agent system that generates disruptive scientific hypotheses by randomly colliding distant scientific domains, then validates them through a deep review pipeline.

## Architecture
- **L0 Pipeline**: Gate → Explorer → Synthesis → Critics (devil/angel) → Curator → Impact → ReviewerAgent
- **L1 Pipeline**: Observer → Strategist → Critic → Executor (mutates L0 genome)
- **Post-Fire Pipeline** (NEW, to implement): Literature Grounding → Hypothesis Sharpening → Experimental Protocol → Multi-Reviewer Panel (5 personas) → Meta-Reviewer → Research Brief Generator
- **Stack**: Python 3.12, LangGraph, DeepSeek V3.2 API (primary), Anthropic (fallback), SQLite, Streamlit, sentence-transformers
- **External APIs**: Semantic Scholar (free, 100 req/s), ArXiv

## Key files
- `SPORE_Design_Doc_v1.md` — Full vision document (14 sections)
- `SPORE_Post_Fire_Pipeline_v1.md` — Post-🔥 pipeline design doc
- `data/constitution.yaml` — Inviolable rules (only human can modify)
- `data/l0_genome.yaml` — Current L0 genome (mutated by L1)
- `src/spore/agents/` — All agent implementations
- `src/spore/graph/pipeline.py` — LangGraph L0 graph
- `src/spore/graph/l1_pipeline.py` — LangGraph L1 graph

## Current state
- L0 operational: daily cron at 6h, 100 collisions all_science
- L1 operational: daily cron at 7h, has applied 3+ mutations
- Provider: DeepSeek V3.2 (~$0.50/100 collisions) with Anthropic fallback
- Gate Agent needs recalibration (99/100 pass, should reject 40-60%)
- ReviewerAgent operational (on 72 hypotheses: 24% 🗑️ poubelle / 60% 🤔 intéressant / 17% 🔥 a_tester — within target range; 16/17 🗑️ come from mechanical overrides composite<0.35 or hallucination_risk>0.40). Verdict field is `auto_feedback_json.verdict` (NOT `$.rating`).
- Post-Fire Pipeline: designed, not yet implemented

## Code style
- Python with type hints everywhere
- Google-style docstrings
- Async by default (LangGraph agents are async)
- Structured logging (no print statements)
- Explicit error handling with retry/backoff for external API calls
- Pydantic v2 for all data models

## What NOT to do
- Do NOT simplify the architecture "to start" — implement the design doc as specified
- Do NOT hallucinate LangGraph function names or APIs — check the docs
- Do NOT produce partial code with ellipses (...) — complete files only
- Do NOT modify constitution.yaml — only the human can do that
- Do NOT modify running L0/L1 agents while a run is in progress

## Agent prompt structure
All SPORE agent prompts follow this pattern:
1. Clear persona (who the agent is)
2. Mission in 1 sentence
3. Absolute rules (NEVER/ALWAYS)
4. Explicit input format
5. JSON output schema
6. Examples if needed
7. Edge cases and fallbacks

## Refactor execution — "audit oversight" rule

When executing a multi-step refactor against an upfront audit (e.g. sprint
N1.1 "découverte → brief / hypothèse / piste"), and grep at execution
time reveals an occurrence not listed in the original audit, apply this
rule INSTEAD of stopping with a STOP arbitrage.

### Auto-include in the current étape commit if all 3 are true
1. **Fix unambigu** — mechanical transformation, no decision (e.g. same
   path replacement applied throughout the diff).
2. **Taxonomie cohérente avec l'étape en cours** — the occurrence
   belongs to the same object class as the étape (e.g. internal app
   link → "internal links" étape ; FR user-facing string → "rebranding"
   étape).
3. **Scope général du refactor** — the occurrence sits inside the
   global perimeter of the chantier (e.g. N1.1 = applicative code +
   scripts + runtime config).

When the 3 hold:
- Include the fix in the étape's commit.
- Add a short note in the commit BODY (not the subject):
  ``Note: <file> initialement non listé dans l'audit, détecté par grep
  de complétude au moment de l'exécution.``
- Brief chat mention for traceability:
  ``Audit oversight inclus dans étape <N> : <chemin/fichier> (ligne X).
  Critères OK : fix unambigu + taxonomie <étape> + scope <refactor>.
  Note ajoutée au body du commit.``
- DO NOT create a STOP.

### STOP required, OR park out of scope, when
- **(i) Hors scope général** of the refactor (e.g. README, docs/, pure
  doc files) → park as a "to-backlog" follow-up commit, do not include
  in the étape commit.
- **(ii) Ambiguous fix** — multiple possible targets, or non-trivial
  transformation → STOP with a proposed arbitrage.
- **(iii) Inconsistent taxonomy** — the occurrence belongs to an étape
  already committed or to a different future étape → STOP to decide
  reclassement (separate commit ? amend ? defer ?).

### Meta — for the next refactor of this caliber

Widen the initial audit grep beyond ``src/``:
- ``scripts/``
- ``*.config.{js,ts,mjs}``
- ``README*``, ``docs/``
- test files (``test_*.sh``, ``*.test.ts``)

Then classify each hit into its étape or explicitly out-of-scope, rather
than discovering oversights at execution time.

## Current priority
Implement the Post-Fire Pipeline as described in SPORE_Post_Fire_Pipeline_v1.md:
1. `semantic_scholar.py` — API client with retry/cache
2. `literature_grounding.py` — Agent that anchors hypotheses in real literature
3. `hypothesis_sharpening.py` — Formalizes hypotheses with quantitative predictions
4. `experimental_protocol.py` — 3-phase validation protocol
5. `multi_reviewer_panel.py` — 5 reviewer personas + meta-reviewer
6. `research_brief_generator.py` — Compiles everything into a 4-6 page brief
7. `graph/post_fire_pipeline.py` — LangGraph subgraph integrating all the above
8. SQLite `briefs` table
9. Streamlit pages: "Research Briefs" + "Brief Detail"

## Remote git

- Remote : `git@github.com:benoitbds/spore-poc.git` (privé)
- Auth : clé SSH `~/.ssh/id_ed25519_github` via `~/.ssh/config`
- Convention : push après chaque sprint mergé sur master, ou immédiatement pour les commits sensibles
- Backup : GitHub privé est le backup distant officiel de SPORE
