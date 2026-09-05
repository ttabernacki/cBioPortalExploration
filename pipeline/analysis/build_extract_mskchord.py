#!/usr/bin/env python3
"""Materialise the H-001 analysis extract from the real MSK-CHORD tables.

build_extract.py executes a cohort spec against tables shaped like dataset_schema.json. The real
MSK-CHORD export is not in that shape (a single clinical sample table, a drug-level timeline, a
MAF), so this is the mapping layer for it. It honours the same contract: the cohort spec is the
source of truth, --dry-run touches no outcome column, and the output columns are exactly what
confirmatory.py expects.

    python3 pipeline/analysis/build_extract_mskchord.py --dry-run
    python3 pipeline/analysis/build_extract_mskchord.py --out extract.csv

TIME ORIGIN. OS in this export is measured from the SEQUENCING date (day 0 of the treatment
timeline), established empirically rather than assumed. Entry is therefore left-truncated at
max(0, first_treatment_offset): a patient who starts therapy 200 days after sequencing cannot
contribute those 200 days to the risk set, because surviving them was a precondition of being
treated at all.
"""
from __future__ import annotations
import argparse, csv, json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKED = ROOT / "locked" / "data"
SPEC = ROOT / "data" / "cohort_specs" / "H-001.json"
csv.field_size_limit(10**7)

LOF = {"Nonsense_Mutation","Frame_Shift_Del","Frame_Shift_Ins","Splice_Site","Nonstop_Mutation"}
IO = {"PEMBROLIZUMAB","NIVOLUMAB","ATEZOLIZUMAB","DURVALUMAB","CEMIPLIMAB","AVELUMAB"}
PLAT = {"CISPLATIN","CARBOPLATIN"}
REGIMEN_WINDOW = 45


def load(name, delim="\t"):
    return list(csv.DictReader(open(LOCKED / name), delimiter=delim))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    spec = json.loads(SPEC.read_text())
    lo = next(f["value"] for f in spec["population"]["filters"] if f.get("op") == "gte")
    hi = next(f["value"] for f in spec["population"]["filters"] if f.get("op") == "lte")

    clin, tx, mut = load("clinical_covariates.tsv"), load("treatment_raw.tsv"), load("mutations_raw.tsv")
    s2p = {r["Sample ID"]: r["Patient ID"] for r in clin}
    pt = {r["Patient ID"]: r for r in clin}

    def gene_pts(g, alts=None):
        s = {r["Tumor_Sample_Barcode"] for r in mut if r["Hugo_Symbol"] == g
             and (alts is None or r["Variant_Classification"] in alts)}
        return {s2p[x] for x in s if x in s2p}

    stk11, egfr, alk = gene_pts("STK11", LOF), gene_pts("EGFR"), gene_pts("ALK")
    kras, keap1, tp53 = gene_pts("KRAS"), gene_pts("KEAP1"), gene_pts("TP53")
    sequenced = {s2p[r["Tumor_Sample_Barcode"]] for r in mut if r["Tumor_Sample_Barcode"] in s2p}

    by = defaultdict(list)
    for r in tx:
        by[r["PATIENT_ID"]].append((int(r["START_DATE"]), r["AGENT"]))

    log, keep = [], []
    universe = set(pt)
    def step(label, s):
        log.append((label, len(universe), len(s), len(universe) - len(s))); return s

    cur = step("all patients", {p for p in universe})
    cur = step("NSCLC", {p for p in cur if pt[p]["Cancer Type"] == "Non-Small Cell Lung Cancer"})
    cur = step("Stage 4 (advanced)", {p for p in cur if pt[p]["Stage (Highest Recorded)"] == "Stage 4"})
    cur = step("sequenced (genomic callable)", {p for p in cur if p in sequenced})
    cur = step("EGFR/ALK wild-type", {p for p in cur if p not in egfr and p not in alk})
    cur = step("has systemic therapy record", {p for p in cur if p in by})
    cur = step(f"first therapy within [{lo},{hi}]d of sequencing",
               {p for p in cur if lo <= min(x[0] for x in by[p]) <= hi})

    armed = set()
    for p in cur:
        first = min(x[0] for x in by[p])
        reg = {ag for s, ag in by[p] if first <= s <= first + REGIMEN_WINDOW}
        if reg & IO: armed.add((p, 1))
        elif reg & PLAT: armed.add((p, 0))
    cur = step("first-line regimen is ICI-containing or platinum", {p for p, _ in armed})
    arm = dict(armed)

    for p in sorted(cur):
        r = pt[p]
        first = min(x[0] for x in by[p])
        keep.append({
            "patient_id": p,
            "exposure": 1 if p in stk11 else 0,
            "treatment_is_ici": arm[p],
            "age at index": r["Current Age"],
            "sex": r["Sex"],
            "smoking status": r["Smoking History (NLP)"],
            "histology": r["Cancer Type Detailed"],
            "tmb": r["TMB (nonsynonymous)"],
            "kras_mut": 1 if p in kras else 0,
            "keap1_mut": 1 if p in keap1 else 0,
            "tp53_mut": 1 if p in tp53 else 0,
            "entry_day": max(0, first),
        })

    if a.dry_run:
        print("ATTRITION — H-001 (dry run; no outcome column read)\n")
        for lbl, before, after, removed in log:
            print(f"  {lbl:46s} {after:6d}  (-{removed})")
        cells = defaultdict(int)
        for k in keep:
            cells[(k["treatment_is_ici"], k["exposure"])] += 1
        print(f"\n  final analysable: {len(keep)}")
        print("  cells (arm, STK11-LoF):")
        for (t, e), n in sorted(cells.items(), reverse=True):
            print(f"    {'ICI  ' if t else 'CHEMO'}  STK11-{'mut' if e else 'wt ':3s}  {n}")
        miss = {c: sum(1 for k in keep if k[c] in ("", "NA", None))
                for c in ("age at index","sex","smoking status","histology","tmb")}
        print(f"  missing covariates: {miss}")
        return 0

    if not a.out:
        print("REFUSED: --out required unless --dry-run", file=sys.stderr); return 1
    out = {r["Patient ID"]: r for r in load("outcomes.tsv")}
    rows = []
    for k in keep:
        o = out.get(k["patient_id"])
        if not o or o["Overall Survival (Months)"] in ("", "NA"): continue
        k = dict(k)
        k["os_days"] = float(o["Overall Survival (Months)"]) * 30.44
        k["death_event"] = 1 if o["Overall Survival Status"].startswith("1") else 0
        rows.append(k)
    cols = ["patient_id","os_days","death_event","exposure","treatment_is_ici",
            "age at index","sex","smoking status","histology","tmb",
            "kras_mut","keap1_mut","tp53_mut","entry_day"]
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(f"wrote {a.out} — {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
