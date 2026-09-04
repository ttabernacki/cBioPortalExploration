# cBioPortalExploration

An outcome-blinded, pre-registered hypothesis generation pipeline for oncology clinicogenomic
databases (MSK-CHORD-style: NLP-extracted clinical annotations + structured meds/labs + tumour
variant calls).

The pipeline generates novel, literature-grounded, mechanistically plausible hypotheses about
gene–phenotype–treatment associations, then tests them under a structure designed so that the
people and agents proposing a hypothesis cannot see the data that would confirm it.

**Read `pipeline/CLAUDE.md` before changing anything under `pipeline/`.**

## The idea

Hypothesis generation is separated from hypothesis testing by a one-way gate. Everything upstream
of the gate is blind to outcomes; nothing downstream may be re-run.

```
GENERATION ZONE (outcome-blind)              GATE                TEST ZONE
─────────────────────────────────────   ──────────────   ──────────────────────────
1 literature-mapper    → claim_graph
2 gap-finder           → candidates      /preregister     6 confirmatory-analyst
3 plausibility-filter  → filtered        commits, then      · single Cox fit
4 feasibility-checker  → feasible        unlocks the        · FDR across the full set
5 novelty-scorer       → ranked          test slice         · replication on holdout
```

A hypothesis reaches pre-registration only with a completed plausibility pass *and* a completed
feasibility pass. The pre-registration is committed to git before any outcome is unlocked, and the
unlock entry records the commit SHA that authorised it. The confirmatory analysis runs once.

## Why the boundary is real

Prompt instructions are not an access boundary. This uses three layers:

1. **Tool grants.** `literature-mapper` has no read tool at all. Agents 2–5 have `Read`/`Write`
   but no `Bash`, so they cannot shell around the restriction. Only the test-zone agent has `Bash`.
2. **Harness deny rules.** `.claude/settings.json` denies `Read`, `Glob`, and `Grep` on
   `pipeline/locked/**` for every agent and the main thread alike.
3. **Validator audit.** `pipeline/validate.py` checks `dataset_schema.json` against the locked
   endpoint registry for outcome-column leakage and scans generation-zone artifacts for
   locked-path references.

Layer 2 deliberately does not gate `Bash` — the gate and the confirmatory script read the manifest
through Python. That is precisely why no generation-zone agent may hold `Bash`.

## Running it

```bash
pip install -r pipeline/requirements.txt

# Stage 1-2
# (invoke the literature-mapper subagent on a narrow topic)
python3 pipeline/validate.py claim_graph
python3 pipeline/analyze_claim_graph.py            # review the computed gaps yourself
python3 pipeline/analyze_claim_graph.py --out      # gap_report.json for gap-finder
# (invoke gap-finder, then plausibility-filter, feasibility-checker, novelty-scorer)
python3 pipeline/validate.py                       # after every stage

# The gate
python3 pipeline/preregister.py H-001 --endpoint OS --dry-run   # read it before committing
python3 pipeline/preregister.py H-001 --endpoint OS

# Test zone
export PIPELINE_LOCKED_DATA_PATH=/path/to/test_partition.parquet
python3 pipeline/analysis/confirmatory.py H-001
python3 pipeline/analysis/confirmatory.py --fdr                 # once ALL are analysed
export PIPELINE_HOLDOUT_DATA_PATH=/path/to/holdout.parquet
python3 pipeline/analysis/confirmatory.py H-001 --replicate

# Audit
python3 pipeline/graveyard.py --write && git add -A && git commit
```

**No patient data is committed to this repository.** The locked partitions are external and
referenced by environment variable. `pipeline/locked/test_partition_manifest.json` holds endpoint
definitions and the append-only unlock log, not values.

## What the refusals mean

Both gate scripts refuse rather than warn. Every refusal below is the system working, and none of
them should be worked around:

| Refusal | Rule |
|---|---|
| no plausibility or feasibility pass | R3 |
| a prereg for this hypothesis already exists | R4 (supersede, never edit) |
| `confirmatory.py`'s hash ≠ the hash the prereg recorded | R5 |
| a result already exists for this hypothesis | R5 (runs once) |
| replication attempted before a test-partition result | R7 |
| FDR requested while a pre-registered hypothesis is unanalysed | R6 |
| a generation-zone artifact references a locked path or outcome field | R1/R2 |
| a hypothesis id present at one stage is missing at the next | audit |

Freeze `confirmatory.py` before the first pre-registration: every prereg records its hash, so a
later edit invalidates all outstanding pre-registrations (R5a).

## The graveyard

`pipeline/graveyard/graveyard.json` records every hypothesis that died — rejected for weak
mechanism, rejected or underpowered at feasibility, or pre-registered and tested to a null.
Harvest it with `pipeline/graveyard.py --write` and commit it.

Nulls are commits, not deletions. A pipeline whose git history contains only wins is a pipeline
that has been p-hacked.

## Worked example

The repository carries a full run on one narrow topic: **STK11 (LKB1) co-mutation and
immunotherapy response in NSCLC** — a 50-claim graph, a computed gap report, and the candidate
hypotheses derived from it. See `pipeline/data/`.
