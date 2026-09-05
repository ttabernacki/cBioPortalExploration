# Run: STK11 (LKB1) co-mutation and immunotherapy response in NSCLC

Completed 2026-09-05. Hypothesis IDs **H-001 … H-015** — retired, never to be reused.

| stage | outcome |
|---|---|
| literature-mapper | 50 claims, 49 verified |
| gap-finder | 15 candidates across all six gap types |
| plausibility-filter | 9 pass / 6 reject |
| feasibility-checker | 3 pass / 4 reject / 2 underpowered (re-run against real MSK-CHORD counts) |
| novelty-scorer | ranked; recommended one slot |
| pre-registration | H-001 only, `prereg_H-001_v1.md`, commit `e4cac1b32dbb` |
| confirmatory | interaction 1.66 (1.15–2.41), q=0.0069 — **pre-registered prediction falsified** |

Findings: `pipeline/findings/H-001_findings.md`.
Graveyard entries for H-002 … H-015 remain in `pipeline/graveyard/graveyard.json`.
The pre-registration and result files stay in their canonical locations and are immutable.

Replication on the temporal holdout has NOT been run; under R7 H-001 is not validated.
