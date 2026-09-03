# Oncology Hypothesis Generation Pipeline — Operating Rules

This directory implements a multi-agent pipeline that generates novel, literature-grounded,
mechanistically plausible hypotheses about gene–phenotype–treatment associations in oncology
clinicogenomic data (MSK-CHORD-style: NLP-extracted clinical annotations + structured meds/labs
+ genomic variant calls), then tests them under a pre-registration structure.

**Core design principle: outcome-blinding.** Hypothesis generation must never see outcomes.

---

## 1. The access boundary

The pipeline is split into two zones with a one-way gate between them.

```
GENERATION ZONE (outcome-blind)          |  GATE  |  TEST ZONE (outcome-aware)
-----------------------------------------|--------|---------------------------
literature-mapper   -> claim_graph.json   |        |
gap-finder          -> candidate_*.json   | /prereg|  confirmatory-analyst
plausibility-filter -> filtered_*.json    | ister  |  replication run
feasibility-checker -> feasible_*.json    |        |
novelty-scorer      -> ranked_*.json      |        |
```

**Hard rules:**

- **R1.** `literature-mapper` and `gap-finder` may never read `pipeline/locked/`, nor any column
  tagged `outcome` in `dataset_schema.json`. They have no dataset access at all.
- **R2.** No agent in the generation zone (agents 1–5) may read `pipeline/locked/` under any
  circumstance. `dataset_schema.json` contains **no** outcome columns by construction — if you
  find one there, that is a bug: stop, remove it, and commit the removal before continuing.
- **R3.** No hypothesis proceeds to `pipeline/preregistration/` without a completed
  plausibility pass (agent 3) **and** a completed feasibility pass (agent 4). A hypothesis
  object missing either `plausibility` or `feasibility` block is rejected by the gate.
- **R4.** Files in `pipeline/preregistration/` are **immutable once committed**. To change a
  pre-registration, write a new `prereg_<id>_v<n+1>.md` that names the file it supersedes in its
  `supersedes:` field. Never edit or delete a committed prereg. Superseding a prereg *after*
  outcomes were unlocked invalidates the hypothesis — mark it `INVALIDATED` and move it to the
  graveyard.
- **R5.** Confirmatory analysis runs **once**, via the fixed deterministic script
  `pipeline/analysis/confirmatory.py`. No free-form LLM statistical reasoning. No manual reruns
  with adjusted covariates, added interaction terms, alternate outcome definitions, or altered
  follow-up windows. The script writes its result and a hash of its own source into the result
  file; a result whose script hash does not match the prereg's recorded hash is void.
- **R6.** FDR correction (Benjamini–Hochberg) is applied across the **full pre-registered
  hypothesis set** in the batch, not just the ones that looked promising.
- **R7.** No hypothesis is "validated" until it has also passed the external replication step
  (independent or temporal-holdout cohort) under the same locked script.

**If any proposed hypothesis-generation step would require reading `pipeline/locked/` or an
outcome field — stop and ask the user. That is a design violation, not an obstacle to route
around.**

---

## 2. Data handoffs

Agents communicate only through JSON files in `pipeline/data/`. Each file has a JSON Schema in
`pipeline/schemas/`. **Validate before the next agent runs:**

```bash
python3 pipeline/validate.py            # validate every data file present
python3 pipeline/validate.py claim_graph
```

Stage order and artifacts:

| # | Agent | Reads | Writes |
|---|-------|-------|--------|
| 1 | `literature-mapper` | web only | `data/claim_graph.json` |
| 2 | `gap-finder` | `claim_graph.json`, web | `data/candidate_hypotheses.json` |
| 3 | `plausibility-filter` | `candidate_hypotheses.json` | `data/filtered_hypotheses.json` |
| 4 | `feasibility-checker` | `filtered_hypotheses.json`, `dataset_schema.json` | `data/feasible_hypotheses.json` |
| 5 | `novelty-scorer` | `feasible_hypotheses.json` | `data/ranked_hypotheses.json` |
| — | `/preregister <id>` | `ranked_hypotheses.json` | `preregistration/prereg_<id>.md` |
| 6 | `confirmatory-analyst` | prereg + `locked/` | `analysis/results/<id>.json` |
| 7 | replication | prereg + holdout cohort | `analysis/results/<id>_replication.json` |

A hypothesis carries the same `id` from candidate through to result. IDs are of the form
`H-<NNN>` and are never reused, including for rejected hypotheses.

---

## 3. The graveyard

Every hypothesis that dies is recorded, with the stage and reason, in
`pipeline/graveyard/graveyard.json`. This includes hypotheses rejected for weak mechanism,
insufficient power, non-novelty, and pre-registered hypotheses whose confirmatory test was null.
**Null results are commits, not deletions.** A pipeline whose git history contains only wins is
a pipeline that has been p-hacked.

## 4. Audit trail

Commit after every stage, with the stage in the subject line:

```
stage(literature-mapper): claim graph for STK11 co-mutation / IO response in NSCLC
stage(gap-finder): 14 candidates, 6 from contradiction analysis
prereg(H-007): lock hypothesis, unlock test slice
result(H-007): null, HR 1.04 (0.88-1.23), q=0.71 -> graveyard
```

Never amend or rebase away a stage commit. The history is the audit trail.

## 5. Style

- Claims and hypotheses are stored as structured triples/objects, never prose blobs.
- Every claim carries a provenance record (PMID/DOI/NCT ID + the sentence supporting it).
- Effect directions are explicit (`increases` / `decreases` / `no_effect` / `mixed`), never implied.
- Never invent a PMID, DOI, or NCT ID. If a citation cannot be verified, mark
  `"verified": false` and let the downstream agent discount it.
