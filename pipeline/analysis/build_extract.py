#!/usr/bin/env python3
"""Materialise the analysis extract from a pre-registered cohort spec.

This closes the last unguarded p-hacking surface in the pipeline. Everything upstream is
outcome-blind and everything downstream runs once — but between them, someone has to turn
"advanced NSCLC on first-line ICI with STK11 loss-of-function" into a dataframe. Doing that by
hand, after unlock, means choosing inclusion criteria with outcomes in view. So the criteria are
fixed in a spec, the spec's hash is recorded in the pre-registration, and this script executes it
without judgement.

    python3 pipeline/analysis/build_extract.py H-011 --raw /path/to/tables --out extract.csv
    python3 pipeline/analysis/build_extract.py H-011 --raw /path/to/tables --dry-run

Refusals:
  - no committed pre-registration for the hypothesis
  - the spec's sha256 differs from the hash the prereg recorded
  - the spec's covariate list differs from the prereg's numbered list
  - the spec names a modifier whose column differs from the prereg's interaction_with

`--dry-run` reports the row counts each filter removes, touching no outcome column. Run it BEFORE
pre-registering, to check the population is what you meant while you still can change it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
PREREG = ROOT / "preregistration"
SPECS = ROOT / "data" / "cohort_specs"

OPS = {
    "eq": lambda s, v: s == v,
    "ne": lambda s, v: s != v,
    "lt": lambda s, v: s < v,
    "lte": lambda s, v: s <= v,
    "gt": lambda s, v: s > v,
    "gte": lambda s, v: s >= v,
    "in": lambda s, v: s.isin(v),
    "not_in": lambda s, v: ~s.isin(v),
}


def die(msg: str) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_prereg(hid: str) -> Path:
    files = sorted(PREREG.glob(f"prereg_{hid}*.md"))
    if not files:
        die(f"no pre-registration for {hid}")
    return files[-1]


def parse_prereg(path: Path) -> dict:
    text = path.read_text()
    block = re.search(r"```yaml\n(.*?)```", text, re.S)
    if not block:
        die(f"{path.name} has no yaml header")
    meta = {}
    for line in block.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    meta["_covariates"] = re.findall(r"^  \d+\. (.+)$", text, re.M)
    return meta


def load_table(raw: Path, name: str):
    import pandas as pd
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv), (".tsv", lambda p: pd.read_csv(p, sep="\t"))):
        p = raw / f"{name}{ext}"
        if p.exists():
            return reader(p)
    die(f"table '{name}' not found in {raw} (looked for .parquet/.csv/.tsv)")


def apply_filter(df, f: dict, label: str, log: list):
    if f["field"] not in df.columns:
        die(f"{label}: field '{f['field']}' not in table '{f['table']}'")
    before = len(df)
    out = df[OPS[f["op"]](df[f["field"]], f["value"])]
    log.append({"step": label, "criterion": f"{f['table']}.{f['field']} {f['op']} {f['value']!r}",
                "rows_before": before, "rows_after": len(out), "removed": before - len(out)})
    return out


def gene_positive(raw: Path, spec: dict) -> set:
    """Patient ids carrying a qualifying alteration. Absence of a call is NOT evidence of
    wild-type — that distinction is left to the caller, which restricts to sequenced patients."""
    gv = load_table(raw, "genomic_variant")
    sel = gv[gv["hugo_symbol"] == spec["gene"]]
    if spec.get("alteration_types"):
        sel = sel[sel["alteration_type"].isin(spec["alteration_types"])]
    if spec.get("oncogenic_annotation_in"):
        sel = sel[sel["oncogenic_annotation"].isin(spec["oncogenic_annotation_in"])]
    return set(sel["patient_id"])


def treated_with(raw: Path, classes: list) -> set:
    tx = load_table(raw, "treatment")
    return set(tx[tx["drug_class"].isin(classes)]["patient_id"])


def derive_binary(raw: Path, spec: dict, patients, log: list, label: str):
    import pandas as pd
    kind = spec["kind"]
    if kind == "genomic_alteration":
        pos = gene_positive(raw, spec)
        col = patients["patient_id"].isin(pos).astype(int)
    elif kind == "treatment_class":
        pos = treated_with(raw, spec["drug_classes"])
        col = patients["patient_id"].isin(pos).astype(int)
    elif kind in ("field_value", "field_threshold"):
        src = load_table(raw, spec["table"])
        keep = src[OPS[spec["op"]](src[spec["field"]], spec["value"])]
        col = patients["patient_id"].isin(set(keep["patient_id"])).astype(int)
    else:
        die(f"{label}: unsupported kind '{kind}'")
    log.append({"step": label, "criterion": f"kind={kind}", "n_positive": int(col.sum()),
                "n_negative": int(len(col) - col.sum())})
    return col


def attach_covariate(raw: Path, cov: dict, patients):
    src = load_table(raw, cov["table"])
    if cov["field"] not in src.columns:
        die(f"covariate '{cov['name']}': field '{cov['field']}' not in table '{cov['table']}'")
    agg = cov.get("aggregate")
    if agg == "baseline":
        if "is_baseline" not in src.columns:
            die(f"covariate '{cov['name']}': aggregate 'baseline' needs an is_baseline column")
        src = src[src["is_baseline"] == True]  # noqa: E712 — explicit, and works for 0/1 too
        agg = "first"
    if agg:
        grouped = src.groupby("patient_id")[cov["field"]]
        s = {"first": grouped.first, "last": grouped.last, "max": grouped.max,
             "min": grouped.min, "mean": grouped.mean}[agg]()
    else:
        dup = src.set_index("patient_id")[cov["field"]]
        if dup.index.has_duplicates:
            die(f"covariate '{cov['name']}': table '{cov['table']}' has multiple rows per patient "
                f"and the spec names no aggregate — pick one rather than letting pandas choose.")
        s = dup
    return patients["patient_id"].map(s)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the pre-registered analysis extract.")
    ap.add_argument("hypothesis_id")
    ap.add_argument("--raw", required=True, help="directory of raw cohort tables")
    ap.add_argument("--spec", help="cohort spec (default: data/cohort_specs/<H-id>.json)")
    ap.add_argument("--out", help="write the extract here")
    ap.add_argument("--dry-run", action="store_true", help="report the attrition table only; no outcome column is touched")
    args = ap.parse_args()

    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        die("pandas is required (pip install pandas)")

    hid = args.hypothesis_id
    raw = Path(args.raw)
    if not raw.is_dir():
        die(f"--raw {raw} is not a directory")
    spec_path = Path(args.spec) if args.spec else SPECS / f"{hid}.json"
    if not spec_path.exists():
        die(f"{spec_path} does not exist")
    spec = json.loads(spec_path.read_text())

    if spec.get("hypothesis_id") != hid:
        die(f"spec declares hypothesis_id '{spec.get('hypothesis_id')}', expected '{hid}'")

    # Gate checks — skipped only for a pre-prereg dry run, which is the point of --dry-run.
    if not args.dry_run:
        meta = parse_prereg(latest_prereg(hid))
        recorded = meta.get("cohort_spec_sha256")
        if not recorded:
            die(f"the pre-registration for {hid} records no cohort_spec_sha256. It was committed "
                f"before cohort specs existed; supersede it rather than building an extract "
                f"whose criteria nothing pinned.")
        if recorded != sha256(spec_path):
            die(f"cohort spec has changed since {meta.get('prereg_version') and latest_prereg(hid).name}.\n"
                f"  prereg records: {recorded}\n  this spec is:   {sha256(spec_path)}\n"
                f"Changing the population after pre-registration is choosing inclusion criteria "
                f"with outcomes in view.")
        spec_covs = [c["name"] for c in spec["covariates"]]
        if spec_covs != meta["_covariates"]:
            die(f"covariate mismatch.\n  prereg: {meta['_covariates']}\n  spec:   {spec_covs}")
        want = meta.get("interaction_with")
        if want and want != "null":
            got = (spec.get("modifier") or {}).get("name")
            if got != want:
                die(f"prereg registers interaction_with '{want}' but the spec's modifier is '{got}'")

    patients = load_table(raw, "patient")
    log: list = []
    log.append({"step": "start", "criterion": "all patients", "rows_before": len(patients),
                "rows_after": len(patients), "removed": 0})

    if spec["population"].get("require_sequencing", True):
        seq = set(load_table(raw, "genomic_sample_level")["patient_id"])
        before = len(patients)
        patients = patients[patients["patient_id"].isin(seq)]
        log.append({"step": "require_sequencing", "criterion": "has a sequenced sample",
                    "rows_before": before, "rows_after": len(patients), "removed": before - len(patients)})

    for i, f in enumerate(spec["population"]["filters"], 1):
        src = load_table(raw, f["table"])
        if f["table"] != "patient":
            keep = set(apply_filter(src, f, f"population[{i}]", log)["patient_id"])
            before = len(patients)
            patients = patients[patients["patient_id"].isin(keep)]
            log[-1].update({"rows_before": before, "rows_after": len(patients), "removed": before - len(patients)})
        else:
            patients = apply_filter(patients, f, f"population[{i}]", log)

    patients = patients.reset_index(drop=True)
    out = patients[["patient_id"]].copy()
    out["exposure"] = derive_binary(raw, spec["exposure"], patients, log, "exposure")

    if spec.get("modifier"):
        m = spec["modifier"]
        if m.get("continuous"):
            src = load_table(raw, m["table"])
            out[m["name"]] = patients["patient_id"].map(src.set_index("patient_id")[m["field"]])
            log.append({"step": "modifier", "criterion": f"{m['table']}.{m['field']} (continuous)",
                        "n_nonnull": int(out[m['name']].notna().sum())})
        else:
            out[m["name"]] = derive_binary(raw, m, patients, log, "modifier")

    for cov in spec["covariates"]:
        out[cov["name"]] = attach_covariate(raw, cov, patients)

    if args.dry_run:
        print(f"ATTRITION — {hid} (dry run; no outcome column touched)\n")
        for row in log:
            if "rows_before" in row:
                print(f"  {row['step']:22s} {row['rows_before']:>7d} -> {row['rows_after']:>7d} "
                      f"(-{row['removed']}) : {row['criterion']}")
            else:
                print(f"  {row['step']:22s} {row}")
        print(f"\n  final analysable rows: {len(out)}")
        miss = out.isna().sum()
        if miss.any():
            print("  missing per column (complete-case will drop these):")
            for c, n in miss[miss > 0].items():
                print(f"    {c}: {n}")
        return 0

    if not args.out:
        die("--out is required unless --dry-run")
    outcomes = load_table(raw, "outcomes")
    merged = out.merge(outcomes, on="patient_id", how="inner")
    if spec["time"].get("left_truncate_at_sequencing", True):
        # Entry time must be on the SAME CLOCK as the endpoint. days_dx_to_sequencing is measured
        # from diagnosis; when the time origin is treatment start, the two differ by the interval
        # from diagnosis to that treatment. Subtracting one from the other is not bookkeeping — mix
        # the origins and the truncation is applied at a meaningless time, which is worse than not
        # truncating at all because it looks correct.
        gv = load_table(raw, "genomic_variant")
        seq = gv.groupby("patient_id")["days_dx_to_sequencing"].min()
        entry = merged["patient_id"].map(seq)

        if spec["time"]["origin"] == "index_treatment_start":
            tx = load_table(raw, "treatment")
            if "start_day_offset" not in tx.columns:
                die("time.origin is index_treatment_start and left truncation is on, but the "
                    "treatment table has no start_day_offset to rebase sequencing time onto. "
                    "Without it the two clocks cannot be reconciled.")
            start = tx.groupby("patient_id")["start_day_offset"].min()
            entry = entry - merged["patient_id"].map(start)

        # Sequenced before follow-up began: no immortal time to remove, so entry is 0.
        merged["entry_day"] = entry.clip(lower=0)
        late = int((merged["entry_day"] >= merged[[c for c in merged.columns if c.endswith("_days")][0]]).sum())
        if late:
            print(f"note: {late} patients were sequenced after their event or censoring time and "
                  f"will be dropped by the confirmatory script's truncation check. That is a real "
                  f"feature of the cohort, not a defect — report it as attrition.", file=sys.stderr)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.out, index=False)
    print(f"wrote {args.out} — {len(merged)} rows, {len(merged.columns)} columns")
    print(f"cohort_spec sha256: {sha256(spec_path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
