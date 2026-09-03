# Agent definitions

The canonical, loadable subagent definitions live in `.claude/agents/*.md` — that is the only
path Claude Code reads them from, and their `tools:` frontmatter is the pipeline's real access
boundary, not documentation.

| Stage | Agent | File | Tool grant | Why |
|-------|-------|------|-----------|-----|
| 1 | `literature-mapper` | `.claude/agents/literature-mapper.md` | `WebSearch, WebFetch, Write` | No read tool at all, so `pipeline/locked/` is unreachable. |
| 2 | `gap-finder` | _not yet built_ | planned: `Read, WebSearch, WebFetch, Write` | Read scoped to `claim_graph.json`; no locked access. |
| 3 | `plausibility-filter` | _not yet built_ | planned: `Read, Write` | `pipeline/data/` only. |
| 4 | `feasibility-checker` | _not yet built_ | planned: `Read, Write` | `pipeline/data/` incl. `dataset_schema.json` (non-outcome fields only). |
| 5 | `novelty-scorer` | _not yet built_ | planned: `Read, Write` | `pipeline/data/` only. |
| 6 | `confirmatory-analyst` | _not yet built_ | planned: `Bash, Read` | Test zone. Runs the fixed script only; no free-form analysis. |

Per the build plan, agents are added one at a time with manual inspection of each stage's output
before the next is built. See `pipeline/CLAUDE.md` for the rules each agent operates under.
