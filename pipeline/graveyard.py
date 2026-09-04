#!/usr/bin/env python3
"""Harvest dead hypotheses into the graveyard.

Scans the stage artifacts and the confirmatory results, and records every hypothesis that died —
rejected at plausibility, rejected or underpowered at feasibility, or pre-registered and tested to
a null. Null results are commits, not deletions: a pipeline whose git history contains only wins
is a pipeline that has been p-hacked.

    python3 pipeline/graveyard.py            # show what would be recorded
    python3 pipeline/graveyard.py --write    # update graveyard/graveyard.json

Idempotent: re-running updates existing entries rather than duplicating them.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DATA = ROOT / "data"
RESULTS = ROOT / "analysis" / "results"
PREREG = ROOT / "preregistration"
GRAVE = ROOT / "graveyard" / "graveyard.json"

# Later stages win: a hypothesis rejected at stage 4 is described by stage 4's file.
STAGE_FILES = [
    ("filtered", DATA / "filtered_hypotheses.json"),
    ("feasible", DATA / "feasible_hypotheses.json"),
    ("ranked", DATA / "ranked_hypotheses.json"),
]


def death_record(h: dict, stage: str) -> dict | None:
    """Why this hypothesis died, if it did. Returns None for survivors."""
    feas = h.get("feasibility") or {}
    plaus = h.get("plausibility") or {}

    if feas.get("verdict") in ("reject", "underpowered"):
        return {
            "died_at_stage": "feasibility",
            "reason": feas.get("reject_reason") or feas.get("power_note") or f"feasibility verdict: {feas['verdict']}",
            "verdict": feas["verdict"],
        }
    if plaus.get("verdict") == "reject":
        return {
            "died_at_stage": "plausibility",
            "reason": plaus.get("reject_reason") or "rejected at plausibility, no reason recorded",
            "verdict": "reject",
        }
    if h.get("status") == "rejected":
        return {
            "died_at_stage": stage,
            "reason": "marked rejected without a recorded verdict — check the stage output",
            "verdict": "unknown",
        }
    return None


def tested_to_null(hid: str) -> dict | None:
    """A pre-registered hypothesis whose confirmatory result did not clear its own alpha.

    Uses the FDR q-value where one has been computed across the full pre-registered set; falls
    back to the uncorrected p only when FDR has not been run, and says which it used.
    """
    res_path = RESULTS / f"{hid}.json"
    if not res_path.exists():
        return None
    res = json.loads(res_path.read_text())
    r = res["result"]
    fdr_path = RESULTS / "fdr_correction.json"
    q = None
    if fdr_path.exists():
        q = json.loads(fdr_path.read_text()).get("q_values", {}).get(hid)

    basis, value = ("q", q) if q is not None else ("p", r["p_value"])
    if value is None or value < 0.05:
        return None

    rep_path = RESULTS / f"{hid}_replication.json"
    rep = json.loads(rep_path.read_text())["result"] if rep_path.exists() else None
    return {
        "died_at_stage": "confirmatory",
        "reason": (
            f"pre-registered and tested; null on the {basis}-value basis "
            f"({basis}={value:.4g}). Reported as-is, not re-run."
        ),
        "verdict": "null",
        "result_if_tested": {
            "hazard_ratio": r["hazard_ratio"], "ci_low": r["ci_low"], "ci_high": r["ci_high"],
            "p_value": r["p_value"], "q_value": q,
            "n_analysed": r["n_analysed_complete_case"], "n_events": r["n_events"],
            "replication_hazard_ratio": rep["hazard_ratio"] if rep else None,
        },
    }


def collect() -> list[dict]:
    latest: dict[str, tuple[str, dict]] = {}
    for stage, path in STAGE_FILES:
        if not path.exists():
            continue
        for h in json.loads(path.read_text()).get("hypotheses", []):
            latest[h["id"]] = (stage, h)

    entries = []
    for hid, (stage, h) in sorted(latest.items()):
        death = tested_to_null(hid) or death_record(h, stage)
        if not death:
            continue
        artifacts = [str(p.relative_to(REPO)) for p in sorted(PREREG.glob(f"prereg_{hid}*.md"))]
        artifacts += [str(p.relative_to(REPO)) for p in sorted(RESULTS.glob(f"{hid}*.json"))]
        entries.append({
            "id": hid,
            "statement": h["statement"],
            "gap_type": h.get("origin", {}).get("gap_type"),
            **death,
            "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "artifacts": artifacts,
        })
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    entries = collect()
    if not entries:
        print("nothing to bury — no hypothesis has died yet")
        return 0

    for e in entries:
        print(f"{e['id']} [{e['died_at_stage']}/{e['verdict']}] {e['statement'][:70]}...")
        print(f"    {e['reason'][:160]}")

    if not args.write:
        print(f"\n{len(entries)} entries. Re-run with --write to record them.")
        return 0

    doc = json.loads(GRAVE.read_text())
    by_id = {e["id"]: e for e in doc.get("entries", [])}
    for e in entries:
        # Preserve the original burial date; a re-harvest updates the record, not the history.
        if e["id"] in by_id:
            e["recorded_utc"] = by_id[e["id"]].get("recorded_utc", e["recorded_utc"])
        by_id[e["id"]] = e
    doc["entries"] = [by_id[k] for k in sorted(by_id)]
    GRAVE.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"\nwrote {len(doc['entries'])} entries to {GRAVE.relative_to(REPO)}")
    print("commit this — the denominator is part of the audit trail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
