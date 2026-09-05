# H-001 — Findings

**STK11 loss-of-function and immunotherapy in advanced NSCLC: is the marker prognostic or predictive?**

MSK-CHORD (msk_chord_2024) · pre-registered `prereg_H-001_v1.md`, commit `e4cac1b32dbb` · single confirmatory run

---

## Result

| | |
|---|---|
| **Estimand** | Ratio of the STK11 hazard ratio in the ICI arm to that in the platinum arm (treatment-by-STK11 interaction on overall survival) |
| **Result** | **1.66 (95% CI 1.15 – 2.41)** |
| **p / q** | 0.0069 / 0.0069 (BH across the pre-registered set, n=1) |
| **Cohort** | 1,139 patients · 832 deaths (73% event fraction) |
| **Cells** | ICI: 93 STK11-mut / 587 wild-type · Platinum: 74 / 385 |

**The pre-registered prediction was wrong.** H-001 predicted the interaction would be *null* — that STK11
is a marker of generally worse disease (prognostic) rather than of immunotherapy failure specifically.
The confidence interval excludes 1.0. Under decision rule (b), fixed before any outcome was read, this
supports the **predictive** reading: the STK11 penalty is ~1.66× larger on immunotherapy than on
platinum chemotherapy.

This is the outcome the pipeline's own literature analysis argued against. The prediction was committed
in writing, with the margin and decision rule fixed, before the outcome column was ever opened.

## What this does and does not establish

**Does not overturn the randomized evidence.** KEYNOTE-042 showed pembrolizumab OS benefit *retained*
in STK11-mutants (HR 0.37 vs 0.83 in wild-type). This is observational, single-institution, and cannot
outrank a randomized comparison. Where they conflict, the randomized result carries more weight.

**Conflicts with the strongest prior observational null.** Papillon-Cavanagh (2020, n=2,276) reported no
interaction (OS ratio 1.13, 0.76–1.67). Our interval (1.15–2.41) barely overlaps theirs and is *wider* —
so this does not narrow published uncertainty. It is an independent modern-era estimate that disagrees,
not a resolution. Pre-specified: an interval wider than 0.76–1.67 would be reported as exactly that.

## The threat that most plausibly explains this result

**Confounding by indication, unadjustable in this cohort.** In the chemo-immunotherapy era, receiving
first-line platinum *without* an ICI is increasingly an atypical choice — often driven by autoimmune
disease, poor performance status, organ transplant, or other ICI contraindications. If the platinum arm
is systematically enriched for such patients, the STK11 effect there is measured in a different
population, and the interaction term absorbs that difference.

**ECOG performance status is absent from this cohort.** It is the single covariate that most directly
proxies this bias, and there is no substitute. This was recorded in the pre-registration as the most
serious residual limitation *before* the analysis ran — not offered afterwards to explain an
inconvenient result.

Adjusted for: age, sex, smoking, histology, TMB, and KRAS/KEAP1/TP53 co-mutation (KEAP1 matters — it
co-occurs with STK11 in ~50% of altered cases).

## Other limitations, all pre-recorded

- **Proportional hazards is UNCHECKED.** Scaled Schoenfeld residuals are undefined for a left-truncated
  fit. It was not obtained by refitting without truncation, which would be a different model.
- **Immortal time**: survival here is measured from *sequencing*, not diagnosis — established
  empirically, not assumed. Entry is left-truncated at treatment start; 70% of the wider cohort began
  therapy before sequencing and the incident-user window exists to exclude that selection.
- **Panel coverage**: STK11 callability varies across IMPACT341/410/468/505 and no gene-by-panel map was
  available, so co-mutation ascertainment is missing-not-at-random.
- **One cohort, one institution.** No replication has been run. Under the pipeline's own rules a
  hypothesis is not validated until it replicates on an independent or temporal holdout.

## Standing of this result

**Hypothesis-generating, not practice-changing.** A pre-registered observational interaction that
contradicts both the pre-registered prediction *and* the randomized trial data is a reason to look
harder, not a reason to withhold immunotherapy from an STK11-mutant patient.

The immediate next step is replication on a temporal holdout, then a design that can address
confounding by indication directly — which requires ECOG and PD-L1 TPS, neither of which this cohort has.
