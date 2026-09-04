#!/usr/bin/env python3
"""Merge a stage's additions into the full hypothesis artifact.

Stages 4 and 5 add one block per hypothesis. Requiring the agent to rewrite all fifteen
hypotheses verbatim to do that is expensive, grows with every stage, and invites transcription
errors — an agent retyping a rejected hypothesis's reject_reason can quietly alter it.

So each stage writes only a PATCH keyed by hypothesis id, and this script merges it. The merge is
mechanical, which makes the audit guarantee stronger than it was: a stage now physically cannot
drop a hypothesis or alter a field written by an earlier stage, because it never rewrites them.

    python3 pipeline/apply_stage.py feasible   # merges _feasible_patch.json -> feasible_hypotheses.json
    python3 pipeline/apply_stage.py ranked     # merges _ranked_patch.json   -> ranked_hypotheses.json

Patch format:
    {"stage": "feasible",
     "stage_notes": "...",
     "blocks": {"H-001": {"feasibility": {...}, "status": "active"}, ...}}
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

# stage -> (prior artifact, output artifact, the ONLY block this stage may add)
SPEC = {
    "filtered": ("candidate_hypotheses.json", "filtered_hypotheses.json", "plausibility"),
    "feasible": ("filtered_hypotheses.json", "feasible_hypotheses.json", "feasibility"),
    "ranked": ("feasible_hypotheses.json", "ranked_hypotheses.json", "novelty"),
}
ALLOWED_KEYS = {"status"}  # in addition to the stage's own block


def die(msg: str) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=sorted(SPEC))
    ap.add_argument("--patch", help="patch file (default: data/_<stage>_patch.json)")
    args = ap.parse_args()

    prior_name, out_name, block = SPEC[args.stage]
    prior_path, out_path = DATA / prior_name, DATA / out_name
    patch_path = Path(args.patch) if args.patch else DATA / f"_{args.stage}_patch.json"

    if not prior_path.exists():
        die(f"{prior_path.relative_to(REPO)} does not exist — run the previous stage first")
    if not patch_path.exists():
        die(f"{patch_path.relative_to(REPO)} does not exist — the stage agent writes this")

    prior = json.loads(prior_path.read_text())
    patch = json.loads(patch_path.read_text())
    if patch.get("stage") != args.stage:
        die(f"patch declares stage '{patch.get('stage')}', expected '{args.stage}'")

    blocks = patch.get("blocks") or {}
    prior_ids = {h["id"] for h in prior["hypotheses"]}
    unknown = set(blocks) - prior_ids
    if unknown:
        die(f"patch references ids absent from {prior_name}: {', '.join(sorted(unknown))}. "
            f"Stages after 2 annotate; they do not invent hypotheses.")

    for hid, add in blocks.items():
        stray = set(add) - {block} - ALLOWED_KEYS
        if stray:
            die(f"{hid}: patch sets {', '.join(sorted(stray))}, but stage '{args.stage}' may only "
                f"write '{block}' and 'status'. Earlier stages' fields are not rewritable.")

    out = dict(prior)
    out["stage"] = args.stage
    out["source_artifact"] = f"pipeline/data/{prior_name}"
    out["generated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if patch.get("stage_notes"):
        out["stage_notes"] = patch["stage_notes"]

    merged, annotated = [], 0
    for h in prior["hypotheses"]:
        h = dict(h)  # carried forward verbatim
        add = blocks.get(h["id"])
        if add:
            h.update(add)
            annotated += 1
        merged.append(h)
    out["hypotheses"] = merged

    out_path.write_text(json.dumps(out, indent=2) + "\n")
    skipped = len(merged) - annotated
    print(f"merged {annotated} {block} block(s) into {out_path.relative_to(REPO)} "
          f"({len(merged)} hypotheses carried, {skipped} unannotated)")
    print(f"next: python3 pipeline/validate.py {out_name.replace('.json', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
