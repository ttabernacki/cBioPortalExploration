#!/usr/bin/env python3
"""Fixed, deterministic confirmatory analysis. Runs ONCE per pre-registration.

This script contains no free-form reasoning and takes no modelling options. Everything that
could be tuned — covariates, endpoint, population, alpha, correction — is read from the
committed pre-registration document, not from the command line and not from a model's judgement.

    python3 pipeline/analysis/confirmatory.py H-001
    python3 pipeline/analysis/confirmatory.py H-001 --replicate     # temporal holdout
    python3 pipeline/analysis/confirmatory.py --fdr                 # BH across the full set
    python3 pipeline/analysis/confirmatory.py --self-check          # gate logic, no data needed

Refusals, all deliberate:
  - no committed pre-registration for the hypothesis     -> refuse (rule R3/R4)
  - this file's sha256 differs from the prereg's record  -> refuse (rule R5)
  - a result file already exists for this hypothesis     -> refuse (rule R5, no reruns)
  - --replicate before a test-partition result exists    -> refuse (rule R7)

Patient data is never committed to this repository. Point the script at it with
PIPELINE_LOCKED_DATA_PATH / PIPELINE_HOLDOUT_DATA_PATH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
PREREG_DIR = ROOT / "preregistration"
MANIFEST = ROOT / "locked" / "test_partition_manifest.json"
RESULTS = ROOT / "analysis" / "results"
SELF = Path(__file__).resolve()


def die(msg: str) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def self_hash() -> str:
    return hashlib.sha256(SELF.read_bytes()).hexdigest()


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def latest_prereg(hid: str) -> Path:
    files = sorted(PREREG_DIR.glob(f"prereg_{hid}*.md"))
    if not files:
        die(f"no pre-registration for {hid}. Run: python3 pipeline/preregister.py {hid} --endpoint <EP>")
    return files[-1]


def parse_prereg(path: Path) -> dict:
    """Extract the locked analysis plan. The prereg is the source of truth, not this script."""
    text = path.read_text()
    block = re.search(r"```yaml\n(.*?)```", text, re.S)
    if not block:
        die(f"{path.name} has no yaml header block")
    meta = {}
    for line in block.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    if meta.get("status") == "INVALIDATED":
        die(f"{path.name} is marked INVALIDATED — its result would be exploratory, not confirmatory")

    covariates = re.findall(r"^  \d+\. (.+)$", text, re.M)
    alpha = re.search(r"\*\*Alpha:\*\* ([0-9.]+)", text)
    endpoint = re.search(r"\*\*([A-Z_]+) — (.+?)\*\*", text)
    time_field = re.search(r"\*\*Time field:\*\* `(\w+)`", text)
    event_field = re.search(r"\*\*Event field:\*\* `(\w+)`", text)

    missing = [n for n, v in [("covariates", covariates), ("alpha", alpha), ("endpoint", endpoint),
                              ("time field", time_field), ("event field", event_field)] if not v]
    if missing:
        die(f"{path.name} is missing a locked element: {', '.join(missing)}")

    return {
        "prereg_file": path.name,
        "hypothesis_id": meta["hypothesis_id"],
        "prereg_version": meta.get("prereg_version"),
        "script_sha256": meta["confirmatory_script_sha256"],
        "partition": meta.get("partition", "test"),
        "endpoint_id": endpoint.group(1),
        "endpoint_label": endpoint.group(2),
        "time_field": time_field.group(1),
        "event_field": event_field.group(1),
        "covariates": covariates,
        "alpha": float(alpha.group(1)),
    }


def check_gates(plan: dict, hid: str, replicate: bool) -> Path:
    """Every refusal that must happen before any data is touched."""
    if plan["script_sha256"] != self_hash():
        die(
            f"confirmatory.py has changed since {plan['prereg_file']} was committed.\n"
            f"  prereg records: {plan['script_sha256']}\n"
            f"  this file is:   {self_hash()}\n"
            f"Any result from this script would be void (rule R5). Either restore the script or "
            f"supersede the pre-registration — and a supersede after unlock invalidates the hypothesis."
        )

    manifest = json.loads(MANIFEST.read_text())
    entries = [e for e in manifest.get("unlock_log", []) if e["hypothesis_id"] == hid]
    if not entries:
        die(f"{hid} has no unlock entry in the partition manifest — the prereg commit did not authorise access")
    unlock = entries[-1]
    if not git("cat-file", "-t", unlock["prereg_commit_sha"]):
        die(f"unlock entry for {hid} names commit {unlock['prereg_commit_sha'][:12]}, which is not in this repository")

    partition = "temporal_holdout" if replicate else plan["partition"]
    suffix = "_replication" if replicate else ""
    out = RESULTS / f"{hid}{suffix}.json"

    if out.exists():
        prior = json.loads(out.read_text())
        die(
            f"{hid} has already been analysed on the {partition} partition "
            f"({prior.get('run_utc')}). The confirmatory analysis runs once (rule R5). "
            f"Re-running with adjusted covariates is the failure mode this pipeline exists to "
            f"prevent. The existing result stands: {out.relative_to(REPO)}"
        )

    if replicate:
        primary = RESULTS / f"{hid}.json"
        if not primary.exists():
            die(f"replication requires a completed test-partition result first (rule R7); {primary.name} not found")

    return out


def load_cohort(partition: str, plan: dict):
    """Load the locked partition. Nothing here selects, filters, or transforms beyond the prereg."""
    env = "PIPELINE_HOLDOUT_DATA_PATH" if partition == "temporal_holdout" else "PIPELINE_LOCKED_DATA_PATH"
    path = os.environ.get(env)
    if not path:
        die(
            f"{env} is not set. Patient data is not committed to this repository; point this "
            f"variable at the locked {partition} extract."
        )
    p = Path(path)
    if not p.exists():
        die(f"{env}={path} does not exist")
    try:
        import pandas as pd
    except ImportError:
        die("pandas is required to run the confirmatory analysis (pip install pandas lifelines)")
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
    return df


def fit(df, plan: dict) -> dict:
    """Single Cox proportional hazards fit. One call. No refitting, no selection, no interaction
    terms beyond those written into the pre-registered covariate list."""
    try:
        from lifelines import CoxPHFitter
        from lifelines.statistics import proportional_hazard_test
    except ImportError:
        die("lifelines is required (pip install lifelines)")

    t, e = plan["time_field"], plan["event_field"]
    cols = [t, e, "exposure"] + plan["covariates"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        die(f"locked extract is missing pre-registered columns: {', '.join(missing)}")

    n_total = len(df)
    d = df[cols].dropna()
    n_analysed = len(d)

    cph = CoxPHFitter()
    cph.fit(d, duration_col=t, event_col=e)  # the single fit

    row = cph.summary.loc["exposure"]
    ph = proportional_hazard_test(cph, d, time_transform="rank")
    ph_p = float(ph.summary["p"].min())

    return {
        "n_total_in_partition": int(n_total),
        "n_analysed_complete_case": int(n_analysed),
        "n_dropped_missing": int(n_total - n_analysed),
        "n_events": int(d[e].sum()),
        "hazard_ratio": float(row["exp(coef)"]),
        "ci_low": float(row["exp(coef) lower 95%"]),
        "ci_high": float(row["exp(coef) upper 95%"]),
        "se_log_hr": float(row["se(coef)"]),
        "p_value": float(row["p"]),
        "proportional_hazards_test_p": ph_p,
        "proportional_hazards_note": (
            "PH assumption violated (p<0.05) — reported as a diagnostic, NOT corrected by "
            "switching models post hoc." if ph_p < 0.05 else "No PH violation detected."
        ),
    }


def apply_fdr() -> int:
    """Rule R6: Benjamini-Hochberg across the FULL pre-registered set, including nulls."""
    pregistered = {p.stem.split("_v")[0].replace("prereg_", "") for p in PREREG_DIR.glob("prereg_*.md")}
    if not pregistered:
        die("no pre-registrations found")
    results, pending = [], []
    for hid in sorted(pregistered):
        f = RESULTS / f"{hid}.json"
        if f.exists():
            r = json.loads(f.read_text())
            results.append((hid, r["result"]["p_value"]))
        else:
            pending.append(hid)
    if pending:
        die(
            f"FDR correction is applied across the full pre-registered set (rule R6). "
            f"These are pre-registered but unanalysed: {', '.join(pending)}. "
            f"Correcting over only the completed subset inflates the discovery rate."
        )
    m = len(results)
    ordered = sorted(results, key=lambda x: x[1])
    q = {}
    prev = 1.0
    for i in range(m, 0, -1):
        hid, p = ordered[i - 1]
        prev = min(prev, p * m / i)
        q[hid] = prev
    out = {
        "method": "Benjamini-Hochberg",
        "n_hypotheses_in_family": m,
        "computed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "q_values": {hid: round(q[hid], 6) for hid, _ in ordered},
        "significant_at_q05": [hid for hid, _ in ordered if q[hid] < 0.05],
    }
    (RESULTS / "fdr_correction.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    print(f"\nwrote {(RESULTS / 'fdr_correction.json').relative_to(REPO)}", file=sys.stderr)
    return 0


def self_check() -> int:
    print(f"confirmatory.py sha256: {self_hash()}")
    pregs = sorted(PREREG_DIR.glob("prereg_*.md"))
    if not pregs:
        print("no pre-registrations yet — gate has nothing to check")
        return 0
    ok = True
    for p in pregs:
        plan = parse_prereg(p)
        match = plan["script_sha256"] == self_hash()
        ok &= match
        print(f"  {p.name}: script hash {'matches' if match else 'MISMATCH — results would be void'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the pre-registered confirmatory analysis. Once.")
    ap.add_argument("hypothesis_id", nargs="?")
    ap.add_argument("--replicate", action="store_true", help="run on the temporal holdout (rule R7)")
    ap.add_argument("--fdr", action="store_true", help="apply BH correction across the full pre-registered set")
    ap.add_argument("--self-check", action="store_true", help="verify gate logic and script hashes; touches no data")
    args = ap.parse_args()

    if args.self_check:
        return self_check()
    if args.fdr:
        return apply_fdr()
    if not args.hypothesis_id:
        ap.error("hypothesis_id is required unless --fdr or --self-check")

    hid = args.hypothesis_id
    plan = parse_prereg(latest_prereg(hid))
    out = check_gates(plan, hid, args.replicate)
    partition = "temporal_holdout" if args.replicate else plan["partition"]

    df = load_cohort(partition, plan)
    result = fit(df, plan)

    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "hypothesis_id": hid,
        "prereg_file": plan["prereg_file"],
        "prereg_version": plan["prereg_version"],
        "confirmatory_script_sha256": self_hash(),
        "partition": partition,
        "run_type": "replication" if args.replicate else "confirmatory",
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": {"id": plan["endpoint_id"], "label": plan["endpoint_label"]},
        "covariates": plan["covariates"],
        "alpha": plan["alpha"],
        "result": result,
        "interpretation_note": (
            "Reported as-is. The p-value here is uncorrected; the family-wise decision is made by "
            "--fdr across the full pre-registered set (rule R6). A hypothesis is validated only "
            "after replication on an independent or temporal-holdout cohort (rule R7)."
        ),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n")

    r = result
    print(f"{hid} [{partition}] {plan['endpoint_id']}: HR {r['hazard_ratio']:.3f} "
          f"({r['ci_low']:.3f}-{r['ci_high']:.3f}), p={r['p_value']:.4g}, "
          f"n={r['n_analysed_complete_case']}, events={r['n_events']}")
    print(f"wrote {out.relative_to(REPO)}")
    print("next: run --fdr once every pre-registered hypothesis has a result, then commit "
          "(including nulls — they belong in the graveyard, not the bin).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
