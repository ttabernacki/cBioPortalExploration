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
- **R5a.** **Freeze `confirmatory.py` before the first pre-registration is committed.** Each
  prereg records the script's sha256, and the script refuses to run when its own hash no longer
  matches. That is the intended protection, but it means an innocuous later edit — a formatting
  fix, a clearer message — invalidates every outstanding pre-registration and forces a supersede,
  which after unlock marks the hypothesis exploratory. Make script changes while
  `pipeline/preregistration/` is empty; after that, treat the file as frozen.
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

### How the boundary is actually enforced

Three layers, because prompt text alone is not an access boundary:

1. **Tool grants** (`tools:` in each agent's frontmatter). `literature-mapper` has no read tool at
   all. Agents 2–5 get `Read`/`Write` but no `Bash`, so they cannot shell out to reach anything.
2. **Harness deny rules** (`.claude/settings.json`). `Read`, `Glob`, and `Grep` on
   `pipeline/locked/**` are denied project-wide — for subagents and the main thread alike. Verified
   by attempting the read and being refused.
3. **Validator** (`pipeline/validate.py`). Audits `dataset_schema.json` against the locked endpoint
   registry for outcome-column leakage, and scans generation-zone artifacts for locked-path or
   outcome-field references.

Layer 2 does not gate `Bash`: a shell can still `cat pipeline/locked/`, which is deliberate — the
pre-registration gate and the confirmatory script read the manifest that way. Generation-zone
agents are kept off that path by layer 1 (no `Bash` grant), not by layer 2. If you ever grant a
generation-zone agent `Bash`, you have removed the boundary; don't.

---

## 2. Data handoffs

Agents communicate only through JSON files in `pipeline/data/`. Each file has a JSON Schema in
`pipeline/schemas/`. **Validate before the next agent runs:**

```bash
python3 pipeline/validate.py            # validate every data file present
python3 pipeline/validate.py claim_graph
```

Between stages 1 and 2, compute the gap report. `gap-finder` has no `Bash`, so this is run for it:

```bash
python3 pipeline/analyze_claim_graph.py          # human-readable, for your own review
python3 pipeline/analyze_claim_graph.py --out    # writes data/gap_report.json for gap-finder
```

The analyzer does the arithmetic that must not be left to model judgement: which claims actually
contradict (and whether they do so in the *same* context — a real contradiction — or a *different*
one, which is an effect modifier), coverage per context dimension, which claims rest on small old
cohorts, and which entities are never studied jointly. `gap-finder` supplies judgement on top of
it, not recomputation.

Stage order and artifacts:

| # | Agent | Reads | Writes |
|---|-------|-------|--------|
| 1 | `literature-mapper` | web only | `data/claim_graph.json` |
| 2 | `gap-finder` | `claim_graph.json`, `gap_report.json`, web | `data/candidate_hypotheses.json` |
| 3 | `plausibility-filter` | `candidate_hypotheses.json` | `data/filtered_hypotheses.json` |
| 4 | `feasibility-checker` | `filtered_hypotheses.json`, `dataset_schema.json` | `data/feasible_hypotheses.json` |
| 5 | `novelty-scorer` | `feasible_hypotheses.json` | `data/ranked_hypotheses.json` |
| — | `/preregister <id>` | `ranked_hypotheses.json` | `preregistration/prereg_<id>.md` |
| 6 | `confirmatory-analyst` | prereg + `locked/` | `analysis/results/<id>.json` |
| 7 | replication | prereg + holdout cohort | `analysis/results/<id>_replication.json` |

A hypothesis carries the same `id` from candidate through to result. IDs are of the form
`H-<NNN>` and are never reused, including for rejected hypotheses.

**Hypotheses die, but they never vanish.** Every id present at one stage must still be present at
the next, carrying `status: "rejected"` if it failed. `validate.py` fails a stage that drops an id,
and fails one that invents an id after stage 2. Deleting a rejected hypothesis rather than marking
it is how a pipeline quietly stops reporting its denominator.

---

## 3. The graveyard

Every hypothesis that dies is recorded, with the stage and reason, in
`pipeline/graveyard/graveyard.json`. Harvest it from the stage artifacts and results rather than
by hand:

```bash
python3 pipeline/graveyard.py            # show what would be recorded
python3 pipeline/graveyard.py --write    # record it, then commit
```

It is idempotent and preserves each entry's original burial date. It classifies a tested
hypothesis as null on the **FDR q-value** where one has been computed across the full
pre-registered set, falling back to the uncorrected p only when FDR has not been run — and it says
which basis it used. This includes hypotheses rejected for weak mechanism,
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
