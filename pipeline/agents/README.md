# Agent definitions

The canonical, loadable subagent definitions live in `.claude/agents/*.md` — that is the only
path Claude Code reads them from, and their `tools:` frontmatter is the pipeline's real access
boundary, not documentation.

| Stage | Agent | File | Tool grant | Why |
|-------|-------|------|-----------|-----|
| 1 | `literature-mapper` | `.claude/agents/literature-mapper.md` | `WebSearch, WebFetch, Write` | No read tool at all, so `pipeline/locked/` is unreachable. |
| 2 | `gap-finder` | `.claude/agents/gap-finder.md` | `Read, Write, WebSearch, WebFetch` | No `Bash`, so it cannot shell past the deny rule; gap report is precomputed for it. |
| 3 | `plausibility-filter` | `.claude/agents/plausibility-filter.md` | `Read, Write` | `pipeline/data/` only; no web, no `Bash`. |
| 4 | `feasibility-checker` | `.claude/agents/feasibility-checker.md` | `Read, Write` | `pipeline/data/` incl. `dataset_schema.json` (non-outcome fields + aggregate counts). |
| 5 | `novelty-scorer` | `.claude/agents/novelty-scorer.md` | `Read, Write` | `pipeline/data/` only. Last outcome-blind stage. |
| 6 | `confirmatory-analyst` | `.claude/agents/confirmatory-analyst.md` | `Bash, Read` | Test zone. Invokes the fixed script only; explicitly forbidden from computing a statistic by any other route. |

Per the build plan, agents are added one at a time with manual inspection of each stage's output
before the next is built. See `pipeline/CLAUDE.md` for the rules each agent operates under.

## Why no agent below gets `Bash`

`.claude/settings.json` denies `Read`, `Glob`, and `Grep` on `pipeline/locked/**` project-wide,
but it does not gate `Bash` — the pre-registration gate and confirmatory script need to read the
manifest through Python. A generation-zone agent holding `Bash` could therefore `cat` the locked
manifest and the boundary would be gone. Granting one `Bash` silently removes the guarantee, so
anything a generation agent needs from a script is precomputed into `pipeline/data/` for it to
`Read` instead — that is why `gap_report.json` exists.

## Subagent registration

Claude Code loads `.claude/agents/*.md` at session start. An agent added mid-session is not
spawnable until the session restarts.
