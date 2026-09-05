#!/usr/bin/env python3
"""Validate pipeline handoff artifacts and enforce the outcome-blinding boundary.

Run between every stage. Exits non-zero if any check fails.

    python3 pipeline/validate.py                 # everything present
    python3 pipeline/validate.py claim_graph     # one artifact
    python3 pipeline/validate.py --blinding      # boundary audit only
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SCHEMAS = ROOT / "schemas"

try:
    import jsonschema
except ImportError:  # structural checks still run without it
    jsonschema = None

# artifact -> (path, schema, stage)
ARTIFACTS = {
    "claim_graph": (DATA / "claim_graph.json", "claim_graph.schema.json", None),
    "candidate_hypotheses": (DATA / "candidate_hypotheses.json", "hypothesis_set.schema.json", "candidate"),
    "filtered_hypotheses": (DATA / "filtered_hypotheses.json", "hypothesis_set.schema.json", "filtered"),
    "feasible_hypotheses": (DATA / "feasible_hypotheses.json", "hypothesis_set.schema.json", "feasible"),
    "ranked_hypotheses": (DATA / "ranked_hypotheses.json", "hypothesis_set.schema.json", "ranked"),
}

# Blocks each stage must carry (rule R3).
STAGE_REQUIRED_BLOCKS = {
    "candidate": [],
    "filtered": ["plausibility"],
    "feasible": ["plausibility", "feasibility"],
    "ranked": ["plausibility", "feasibility", "novelty"],
}

# Any generation-zone artifact mentioning these is a blinding leak.
LOCKED_TOKENS = [
    "pipeline/locked",
    "test_partition_manifest",
    "os_days", "ttd_days", "ttnt_days", "pfs_days",
    "death_event", "prog_event", "ttd_event", "ttnt_event",
    "vital_status",
]

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        err(f"{path.name}: invalid JSON — {e}")
        return None


def schema_validate(doc, schema_name: str, label: str) -> None:
    if jsonschema is None:
        warn(f"{label}: jsonschema not installed, skipped schema validation (pip install jsonschema)")
        return
    schema = json.loads((SCHEMAS / schema_name).read_text())
    validator = jsonschema.Draft7Validator(schema)
    for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path) or "<root>"
        err(f"{label}: {loc}: {e.message}")


def check_blinding(path: Path) -> None:
    """R1/R2: no generation-zone artifact may reference locked paths or outcome fields."""
    text = path.read_text()
    for token in LOCKED_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):
            err(
                f"BLINDING VIOLATION: {path.relative_to(ROOT.parent)} references '{token}'. "
                f"Generation-zone artifacts must not reference locked data or outcome fields. Stop and ask."
            )


def check_dataset_schema() -> None:
    """R2: dataset_schema.json must carry no outcome columns."""
    path = DATA / "dataset_schema.json"
    if not path.exists():
        err("dataset_schema.json missing")
        return
    doc = load(path)
    if doc is None:
        return
    if doc.get("outcome_columns_present") is not False:
        err("dataset_schema.json: outcome_columns_present must be false")
    registry = json.loads((DATA / "endpoint_definitions.json").read_text())
    outcome_fields = set()
    for ep in registry.get("endpoint_registry", []):
        outcome_fields.update({ep.get("event_field"), ep.get("time_field")})
    outcome_fields.discard(None)
    for table in doc.get("tables", []):
        for field in table.get("fields", []):
            name = field.get("name")
            if name in outcome_fields:
                err(
                    f"BLINDING VIOLATION: dataset_schema.json table '{table['name']}' exposes outcome "
                    f"field '{name}'. Remove it and commit the removal before running any agent."
                )
            if field.get("tag") == "outcome" or field.get("role") == "outcome":
                err(f"BLINDING VIOLATION: dataset_schema.json field '{name}' is tagged outcome.")
    print("  dataset_schema.json: no outcome columns exposed")


PRIOR_STAGE = {
    "filtered_hypotheses": "candidate_hypotheses",
    "feasible_hypotheses": "filtered_hypotheses",
    "ranked_hypotheses": "feasible_hypotheses",
}


def check_no_silent_drops(name: str, doc) -> None:
    """A hypothesis may die, but it may never vanish.

    Every id present at the previous stage must still be present at this one, carrying
    status 'rejected' if it failed. Deleting a rejected hypothesis instead of marking it is how
    a pipeline quietly stops reporting its denominator — the exact failure the graveyard exists
    to prevent. This is an audit property, not a style preference.
    """
    prior_name = PRIOR_STAGE.get(name)
    if not prior_name:
        return
    prior_path = ARTIFACTS[prior_name][0]
    if not prior_path.exists():
        warn(f"{name}: {prior_name}.json is absent, cannot check for silently dropped hypotheses")
        return
    prior_ids = {h["id"] for h in json.loads(prior_path.read_text()).get("hypotheses", [])}
    here_ids = {h["id"] for h in doc.get("hypotheses", [])}
    dropped = prior_ids - here_ids
    if dropped:
        err(
            f"{name}: {len(dropped)} hypothesis/es present in {prior_name} but missing here: "
            f"{', '.join(sorted(dropped))}. A rejected hypothesis must be carried forward with "
            f"status 'rejected', never deleted — the graveyard needs the denominator."
        )
    invented = here_ids - prior_ids
    if invented:
        err(
            f"{name}: {', '.join(sorted(invented))} appear here but not in {prior_name}. "
            f"Stages after 2 filter and annotate; they do not invent hypotheses."
        )


def check_hypothesis_set(name: str, doc, stage: str) -> None:
    check_no_silent_drops(name, doc)
    if doc.get("stage") != stage:
        err(f"{name}: stage is '{doc.get('stage')}', expected '{stage}'")
    required = STAGE_REQUIRED_BLOCKS[stage]
    seen_ids: set[str] = set()
    for h in doc.get("hypotheses", []):
        hid = h.get("id", "<no id>")
        if hid in seen_ids:
            err(f"{name}: duplicate hypothesis id {hid}")
        seen_ids.add(hid)
        for pne_entry in (h.get("origin", {}).get("prior_negative_evidence") or []):
            if "verified" not in pne_entry:
                warn(
                    f"{name}: {hid} cites prior negative evidence "
                    f"'{pne_entry.get('identifier', '?')}' with no 'verified' flag. Stage 5 "
                    f"discounts novelty from this field; an unretrieved citation there is as "
                    f"load-bearing as an unverified claim and must be marked."
                )
        if "status" not in h:
            err(
                f"{name}: {hid} has no 'status'. Every hypothesis must carry one at every stage — "
                f"the drop/rejection audit is keyed on it, so a missing status silently disables "
                f"the checks that keep the denominator visible."
            )
        if h.get("status") == "rejected":
            continue  # rejected hypotheses are carried for the graveyard, not gated
        for block in required:
            if block not in h:
                err(f"{name}: {hid} is missing required '{block}' block for stage '{stage}' (rule R3)")
        if "plausibility" in h and h["plausibility"].get("verdict") == "reject" and h.get("status") != "rejected":
            err(f"{name}: {hid} failed plausibility but status is '{h.get('status')}', expected 'rejected'")
        if "feasibility" in h and h["feasibility"].get("verdict") in ("reject", "underpowered") and h.get("status") != "rejected":
            err(f"{name}: {hid} failed feasibility but status is '{h.get('status')}', expected 'rejected'")
    if stage == "ranked":
        ranks = [h["novelty"]["rank"] for h in doc.get("hypotheses", []) if "novelty" in h]
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            err(f"{name}: novelty ranks must be a contiguous 1..N sequence, got {sorted(ranks)}")


def check_claim_graph(doc) -> None:
    # Structural guard first. A claim missing a required block must be REPORTED, not raise —
    # a validator that crashes tells you nothing about what is wrong, and it crashes exactly when
    # the input is most broken, which is when you most need it to speak.
    REQUIRED = ("claim_id", "subject", "predicate", "context", "evidence", "confidence")
    malformed = []
    for i, c in enumerate(doc.get("claims", [])):
        missing = [k for k in REQUIRED if k not in c]
        if missing:
            malformed.append((c.get("claim_id", f"<index {i}>"), missing))
    if malformed:
        for cid, missing in malformed[:10]:
            err(f"claim_graph.json: {cid} is missing required field(s): {', '.join(missing)}")
        if len(malformed) > 10:
            err(f"claim_graph.json: ...and {len(malformed) - 10} further malformed claims "
                f"({len(malformed)} of {len(doc.get('claims', []))} total)")
        err("claim_graph.json: structural check failed — per-claim content checks skipped. "
            "This usually means the writer used a different schema than "
            "pipeline/schemas/claim_graph.schema.json, not that the file is truncated.")
        return

    ids = [c["claim_id"] for c in doc.get("claims", [])]
    if len(ids) != len(set(ids)):
        err("claim_graph.json: duplicate claim_id")
    known = set(ids)
    for c in doc.get("claims", []):
        for ref in c.get("conflicts_with", []):
            if ref not in known:
                err(f"claim_graph.json: {c['claim_id']} conflicts_with unknown claim {ref}")
        ev = c["evidence"]
        cit = ev["citation"]
        if ev["verified"] and not any(cit.get(k) for k in ("pmid", "doi", "nct_id", "url")):
            err(f"claim_graph.json: {c['claim_id']} marked verified but carries no resolvable identifier")
        if ev["verified"] and not ev.get("supporting_quote"):
            err(f"claim_graph.json: {c['claim_id']} marked verified but has no supporting_quote")
        if not ev["verified"] and c["confidence"] == "high":
            err(f"claim_graph.json: {c['claim_id']} is unverified and cannot carry confidence 'high'")
    unverified = sum(1 for c in doc.get("claims", []) if not c["evidence"]["verified"])
    if unverified:
        warn(f"claim_graph.json: {unverified}/{len(ids)} claims unverified — downstream agents must discount them")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    blinding_only = "--blinding" in sys.argv

    print("Outcome-blinding audit")
    check_dataset_schema()
    for name, (path, _, stage) in ARTIFACTS.items():
        if path.exists():
            check_blinding(path)
    print(f"  generation-zone artifacts scanned for locked/outcome references")

    if not blinding_only:
        targets = args or list(ARTIFACTS)
        print("\nArtifact validation")
        for name in targets:
            if name not in ARTIFACTS:
                err(f"unknown artifact '{name}'; known: {', '.join(ARTIFACTS)}")
                continue
            path, schema_name, stage = ARTIFACTS[name]
            if not path.exists():
                if args:
                    err(f"{name}: {path} does not exist")
                else:
                    print(f"  {name}: not yet produced, skipped")
                continue
            doc = load(path)
            if doc is None:
                continue
            schema_validate(doc, schema_name, name)
            if name == "claim_graph":
                check_claim_graph(doc)
            else:
                check_hypothesis_set(name, doc, stage)
            print(f"  {name}: validated ({len(doc.get('claims') or doc.get('hypotheses') or [])} records)")

    for w in warnings:
        print(f"\nWARN  {w}")
    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"FAIL  {e}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
