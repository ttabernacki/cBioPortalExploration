# cBioPortalExploration

This repository hosts an AI-driven hypothesis generation pipeline for oncology
clinicogenomic databases. The pipeline, its agents, and its operating rules live in
`pipeline/`.

**Read `pipeline/CLAUDE.md` before touching anything under `pipeline/`.** The four
non-negotiable rules, restated here because they are easy to violate by accident:

1. **Outcome-blinding.** The hypothesis-generation agents (`literature-mapper`, `gap-finder`,
   `plausibility-filter`, `feasibility-checker`, `novelty-scorer`) may never read
   `pipeline/locked/` or any outcome column. `dataset_schema.json` contains no outcome columns
   by construction.
2. **No prereg without passes.** A hypothesis reaches `pipeline/preregistration/` only after a
   completed plausibility pass *and* a completed feasibility pass.
3. **Preregs are immutable once committed.** Supersede with a new versioned file; never edit.
4. **One shot.** Confirmatory analysis runs once, through the fixed deterministic script, with
   no covariate adjustment after the fact and no manual reruns.

If a task appears to require a generation-stage agent to read locked or outcome data, stop and
ask. That is a design violation, not a blocker to work around.
