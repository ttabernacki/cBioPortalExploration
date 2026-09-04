#!/usr/bin/env python3
"""Pipeline state at a glance: what survives, what died where, what is pre-registered and tested.

Reads only committed artifacts. Touches no outcome data beyond the result files that a completed
confirmatory run already produced.

    python3 pipeline/status.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DATA = ROOT / "data"
RESULTS = ROOT / "analysis" / "results"
PREREG = ROOT / "preregistration"
GRAVE = ROOT / "graveyard" / "graveyard.json"

STAGES = [
    ("1 literature-mapper", DATA / "claim_graph.json", "claims"),
    ("2 gap-finder", DATA / "candidate_hypotheses.json", "hypotheses"),
    ("3 plausibility-filter", DATA / "filtered_hypotheses.json", "hypotheses"),
    ("4 feasibility-checker", DATA / "feasible_hypotheses.json", "hypotheses"),
    ("5 novelty-scorer", DATA / "ranked_hypotheses.json", "hypotheses"),
]


def surviving(hs: list[dict]) -> int:
    return sum(1 for h in hs if h.get("status") != "rejected")


def main() -> int:
    print("PIPELINE STATUS\n")
    last = None
    for label, path, key in STAGES:
        if not path.exists():
            print(f"  {label:24s} not run")
            continue
        doc = json.loads(path.read_text())
        items = doc.get(key, [])
        if key == "claims":
            ver = sum(1 for c in items if c["evidence"]["verified"])
            print(f"  {label:24s} {len(items)} claims ({ver} verified)")
        else:
            alive = surviving(items)
            print(f"  {label:24s} {len(items)} carried, {alive} alive, {len(items) - alive} rejected")
            last = items

    if last:
        print("\nSURVIVORS")
        for h in last:
            if h.get("status") == "rejected":
                continue
            nov = h.get("novelty") or {}
            rank = f"#{nov['rank']}" if nov.get("rank") else "  "
            comp = f"{nov['composite_score']:.2f}" if nov.get("composite_score") is not None else "   -"
            plaus = (h.get("plausibility") or {}).get("score", "?")
            feas = (h.get("feasibility") or {}).get("verdict", "?")
            print(f"  {rank:>3s} {comp:>5s}  {h['id']}  plaus={plaus:8s} feas={feas:12s} {h['statement'][:60]}...")

    pregs = sorted(PREREG.glob("prereg_*.md"))
    print(f"\nPRE-REGISTERED: {len(pregs)}")
    for p in pregs:
        print(f"  {p.name}")

    results = sorted(RESULTS.glob("H-*.json"))
    print(f"\nRESULTS: {len(results)}")
    for r in results:
        d = json.loads(r.read_text())
        res = d["result"]
        print(f"  {d['hypothesis_id']} [{d['run_type']}] HR {res['hazard_ratio']:.3f} "
              f"({res['ci_low']:.3f}-{res['ci_high']:.3f}) p={res['p_value']:.4g}")
    fdr = RESULTS / "fdr_correction.json"
    if fdr.exists():
        d = json.loads(fdr.read_text())
        print(f"  FDR over {d['n_hypotheses_in_family']}: significant {d['significant_at_q05'] or 'none'}")

    if GRAVE.exists():
        entries = json.loads(GRAVE.read_text()).get("entries", [])
        print(f"\nGRAVEYARD: {len(entries)}")
        from collections import Counter
        for stage, n in Counter(e["died_at_stage"] for e in entries).most_common():
            print(f"  {stage}: {n}")
        if not entries:
            print("  empty — run pipeline/graveyard.py --write once a stage has rejected something")
    return 0


if __name__ == "__main__":
    sys.exit(main())
