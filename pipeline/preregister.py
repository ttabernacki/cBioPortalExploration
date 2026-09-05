#!/usr/bin/env python3
"""The pre-registration gate.

Deterministic, not LLM-mediated: pulls a hypothesis from ranked_hypotheses.json, refuses it
unless it carries completed plausibility and feasibility passes (rule R3), writes an immutable
pre-registration document, git-commits it, and ONLY THEN appends an unlock entry to the locked
partition manifest.

    python3 pipeline/preregister.py H-001 --endpoint OS
    python3 pipeline/preregister.py H-001 --endpoint OS --supersede prereg_H-001_v1.md --reason "..."
    python3 pipeline/preregister.py H-001 --endpoint OS --dry-run

The order matters and is enforced: no commit, no unlock. A prereg that fails to commit leaves the
partition locked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
RANKED = ROOT / "data" / "ranked_hypotheses.json"
PREREG_DIR = ROOT / "preregistration"
MANIFEST = ROOT / "locked" / "test_partition_manifest.json"
ENDPOINTS = ROOT / "data" / "endpoint_definitions.json"
SCRIPT = ROOT / "analysis" / "confirmatory.py"
SPECS = ROOT / "data" / "cohort_specs"

DEFAULT_ALPHA = 0.05
DEFAULT_CORRECTION = "Benjamini-Hochberg FDR at q<0.05 across the full pre-registered hypothesis set"


def die(msg: str) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_hypothesis(hid: str) -> dict:
    if not RANKED.exists():
        die(f"{RANKED.relative_to(REPO)} does not exist — run stages 1-5 first")
    doc = json.loads(RANKED.read_text())
    for h in doc.get("hypotheses", []):
        if h["id"] == hid:
            return h
    known = ", ".join(h["id"] for h in doc.get("hypotheses", []))
    die(f"{hid} not found in ranked_hypotheses.json (have: {known or 'none'})")


def check_gate(h: dict) -> None:
    """Rule R3: no prereg without completed plausibility AND feasibility passes."""
    plaus = h.get("plausibility")
    feas = h.get("feasibility")
    if not plaus:
        die(f"{h['id']} has no plausibility block — rule R3. Run plausibility-filter first.")
    if not feas:
        die(f"{h['id']} has no feasibility block — rule R3. Run feasibility-checker first.")
    if plaus.get("verdict") != "pass":
        die(f"{h['id']} failed plausibility (verdict={plaus.get('verdict')}): {plaus.get('reject_reason', 'no reason recorded')}")
    if feas.get("verdict") != "pass":
        die(f"{h['id']} failed feasibility (verdict={feas.get('verdict')}): {feas.get('reject_reason', 'no reason recorded')}")
    if not plaus.get("causal_story"):
        die(f"{h['id']} carries no causal story — pattern-matching without a mechanism is not pre-registrable.")
    if h.get("status") == "rejected":
        die(f"{h['id']} is marked rejected.")


def check_immutability(hid: str, supersede: str | None) -> Path:
    """Rule R4: never edit a committed prereg. New version, explicit supersedes field."""
    existing = sorted(PREREG_DIR.glob(f"prereg_{hid}*.md"))
    if not existing:
        return PREREG_DIR / f"prereg_{hid}_v1.md"
    versions = []
    for p in existing:
        stem = p.stem
        versions.append(int(stem.rsplit("_v", 1)[1]) if "_v" in stem else 1)
    latest = max(versions)
    if not supersede:
        die(
            f"a pre-registration for {hid} already exists ({', '.join(p.name for p in existing)}). "
            f"Preregs are immutable (rule R4). To revise, pass --supersede {existing[-1].name} "
            f"--reason '<why>'. Note: superseding after outcomes were unlocked invalidates the "
            f"hypothesis — check the unlock_log first."
        )
    target = PREREG_DIR / supersede
    if not target.exists():
        die(f"--supersede names {supersede}, which does not exist")
    manifest = json.loads(MANIFEST.read_text())
    if any(e["hypothesis_id"] == hid for e in manifest.get("unlock_log", [])):
        print(
            f"WARNING: {hid} already unlocked a test slice. Superseding its prereg after unlock "
            f"invalidates the hypothesis (rule R4). The new prereg will be marked INVALIDATED.",
            file=sys.stderr,
        )
    return PREREG_DIR / f"prereg_{hid}_v{latest + 1}.md"


def endpoint_def(endpoint_id: str) -> dict:
    """Read an endpoint definition.

    These live in pipeline/data/, not pipeline/locked/. They are design information — a name, a
    time origin, a censoring rule — and contain no patient values, so the gate needs no exception
    to the locked-path rule in order to name an endpoint before unlocking anything.
    """
    registry = json.loads(ENDPOINTS.read_text())
    for ep in registry["endpoint_registry"]:
        if ep["endpoint_id"] == endpoint_id:
            return ep
    ids = ", ".join(e["endpoint_id"] for e in registry["endpoint_registry"])
    die(f"unknown endpoint '{endpoint_id}' (registry has: {ids})")


def render(h: dict, ep: dict, args, script_hash: str, now: str, supersedes: str | None, invalidated: bool) -> str:
    cov = ([c.strip() for c in args.covariates.split(";")] if args.covariates
           else h.get("proposed_covariates")) or [
        "age at index", "sex", "ECOG performance status", "stage at diagnosis",
        "smoking status", "line of therapy", "TMB", "days from diagnosis to sequencing",
    ]
    nov = h.get("novelty", {})
    feas = h["feasibility"]
    plaus = h["plausibility"]

    banner = ""
    if invalidated:
        banner = (
            "> **STATUS: INVALIDATED.** This pre-registration supersedes one under which outcome "
            "data was already unlocked. The hypothesis is no longer confirmatory and its result "
            "must be reported as exploratory only.\n\n"
        )

    lines = [
        f"# Pre-registration {h['id']}",
        "",
        banner.rstrip("\n") if banner else "",
        "```yaml",
        f"hypothesis_id: {h['id']}",
        f"prereg_version: {args._version}",
        f"timestamp_utc: {now}",
        f"supersedes: {supersedes or 'null'}",
        f"status: {'INVALIDATED' if invalidated else 'LOCKED'}",
        f"confirmatory_script: pipeline/analysis/confirmatory.py",
        f"confirmatory_script_sha256: {script_hash}",
        f"partition: {args.partition}",
        f"estimand: {args.estimand}",
        f"left_truncate_at_sequencing: {str(args._left_trunc).lower()}",
        f"cohort_spec: {args._spec_file or 'null'}",
        f"cohort_spec_sha256: {args._spec_hash or 'null'}",
        f"interaction_with: {args.interaction_with or 'null'}",
        "```",
        "",
        "**This document is immutable once committed (rule R4).** To revise it, create a new "
        "version that names this file in `supersedes:`. Never edit or delete it.",
        "",
        "## 1. Hypothesis",
        "",
        f"> {h['statement']}",
        "",
        f"**Origin:** {h['origin']['gap_type'].replace('_', ' ')} — {h['origin'].get('rationale', 'n/a')}",
        "",
        f"**Supporting claims:** {', '.join(h['origin'].get('supporting_claims', [])) or 'none recorded'}",
        "",
        "### Mechanistic basis (recorded before any outcome was seen)",
        "",
        plaus["causal_story"],
        "",
    ]
    if plaus.get("mechanism_steps"):
        lines += ["Steps:", ""] + [f"{i}. {s}" for i, s in enumerate(plaus["mechanism_steps"], 1)] + [""]

    lines += [
        "## 2. Design",
        "",
        "| | |",
        "|---|---|",
        f"| **Exposure** | {h['exposure']['description']} |",
        f"| **Genomic criteria** | {h['exposure'].get('genomic_criteria', 'n/a')} |",
        f"| **Treatment criteria** | {h['exposure'].get('treatment_criteria', 'n/a')} |",
        f"| **Comparator** | {h['comparator']['description']} |",
        f"| **Population** | {h['population']['description']} |",
        f"| **Inclusion** | {'; '.join(h['population'].get('inclusion', [])) or 'as above'} |",
        f"| **Exclusion** | {'; '.join(h['population'].get('exclusion', [])) or 'none prespecified'} |",
        f"| **Partition** | `{args.partition}` — locked until this document is committed |",
        f"| **Expected exposed n** | {feas.get('estimated_exposed_n', 'not estimated')} |",
        f"| **Expected comparator n** | {feas.get('estimated_comparator_n', 'not estimated')} |",
        "",
        "## 3. Primary endpoint",
        "",
        f"**{ep['endpoint_id']} — {ep['label']}**",
        "",
        f"- **Definition:** {ep['definition']}",
        f"- **Time origin:** {ep['time_origin']}",
        f"- **Censoring:** {ep['censoring']}",
        f"- **Time field:** `{ep['time_field']}` | **Event field:** `{ep['event_field']}`",
        "",
        "Known biases in this endpoint, prespecified so they cannot be discovered post hoc:",
        "",
    ] + [f"- {b}" for b in ep.get("known_biases", [])] + [
        "",
        "**No secondary endpoints are pre-registered.** Any additional endpoint analysed later is "
        "exploratory and must be reported as such.",
        "",
        "## 4. Statistical analysis plan",
        "",
        f"- **Model:** {args.model_spec}",
        f"- **Covariates (fixed, complete list):**",
        "",
    ] + [f"  {i}. {c}" for i, c in enumerate(cov, 1)] + [
        "",
        f"- **Alpha:** {args.alpha} (two-sided)",
        f"- **Multiple-testing correction:** {args.correction}",
        "- **Missing data:** complete-case for the covariate set above; the missingness count is "
        "reported alongside the estimate. No imputation.",
        "- **Left truncation:** risk set entry at sequencing date where the exposure is genomic, "
        "to avoid immortal-time bias.",
        "- **Proportional hazards:** Schoenfeld residual test is reported as a diagnostic. A PH "
        "violation is reported, not corrected by switching models — switching models after seeing "
        "the data is the p-hacking this structure exists to prevent.",
        "",
        "### Prespecified stopping conditions",
        "",
        "The analysis is **run exactly once** (rule R5). It is void if:",
        "",
        "- the covariate set is altered after this commit;",
        "- the model is refit with an added or removed term;",
        "- the endpoint definition or follow-up window is changed;",
        "- the population filter is changed after any effect estimate is seen;",
        f"- `confirmatory.py` no longer hashes to `{script_hash}`;",
        (f"- the cohort spec no longer hashes to `{args._spec_hash}` — redefining the population "
         f"after this commit is choosing inclusion criteria with outcomes in view."
         if args._spec_hash else
         "- **No cohort spec was registered.** The analysis extract will be built by hand, and "
         "nothing pins its inclusion criteria. Treat the result accordingly."),
        "",
        "## 5. Feasibility (assessed blind to outcomes)",
        "",
        f"- **Required fields:** {', '.join(feas.get('required_fields', [])) or 'not enumerated'}",
        f"- **Missing fields:** {', '.join(feas.get('missing_fields', [])) or 'none'}",
        f"- **Power:** {feas.get('power_note', 'not assessed')}",
        f"- **Minimum detectable effect:** {feas.get('minimum_detectable_effect', 'not computed')}",
        "",
    ]
    if feas.get("measurement_concerns"):
        lines += ["**Measurement concerns:**", ""] + [f"- {m}" for m in feas["measurement_concerns"]] + [""]

    lines += [
        "## 6. Novelty",
        "",
        f"- Literature-gap strength: {nov.get('literature_gap_strength', 'n/a')}",
        f"- Mechanistic plausibility: {nov.get('mechanistic_plausibility_score', 'n/a')} ({plaus['score']})",
        f"- Dataset feasibility: {nov.get('dataset_feasibility_score', 'n/a')}",
        f"- Composite: {nov.get('composite_score', 'n/a')} (rank {nov.get('rank', 'n/a')})",
        "",
    ]
    if h["origin"].get("prior_negative_evidence"):
        lines += ["**Prior negative evidence considered:**", ""]
        lines += [f"- `{p['identifier']}` — {p['summary']}" for p in h["origin"]["prior_negative_evidence"]]
        lines += [""]

    lines += [
        "## 7. Replication requirement",
        "",
        "A result on the `test` partition does not validate this hypothesis (rule R7). The same "
        "locked script must be run against the `temporal_holdout` partition before the hypothesis "
        "is reported as validated. A test-positive, replication-null result is reported as such "
        "and moved to the graveyard.",
        "",
        "## 8. Declaration",
        "",
        "At the time of this commit, no outcome data for this hypothesis's population had been "
        "accessed by any pipeline agent. The generation-stage agents that produced this hypothesis "
        "had no read access to `pipeline/locked/`. The unlock entry for this hypothesis is written "
        "to the partition manifest only after this document's commit SHA exists.",
        "",
    ]
    return "\n".join(l for l in lines if l is not None) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Pre-register a hypothesis and unlock its test slice.")
    p.add_argument("hypothesis_id")
    p.add_argument("--endpoint", required=True, help="endpoint_id from the locked endpoint registry, e.g. OS")
    p.add_argument("--partition", default="test", choices=["test", "temporal_holdout"])
    p.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    p.add_argument("--correction", default=DEFAULT_CORRECTION)
    p.add_argument("--model-spec", default="Cox proportional hazards, single fit, no stepwise or automated selection")
    p.add_argument("--estimand", default="main_effect", choices=["main_effect", "interaction"],
                   help="main_effect: the coefficient on exposure. interaction: the ratio of the "
                        "exposure hazard ratio across strata of --interaction-with. A hypothesis "
                        "about whether a marker is predictive rather than prognostic is an "
                        "interaction and must be registered as one.")
    p.add_argument("--interaction-with", help="column name of the pre-registered effect modifier; required for --estimand interaction")
    p.add_argument("--supersede", help="filename of the prereg this one supersedes (rule R4)")
    p.add_argument("--reason", help="why the supersede is necessary")
    p.add_argument("--covariates", help="semicolon-separated final covariate list, when it must "
                   "differ from the hypothesis's proposed_covariates because the cohort does not "
                   "carry some of them. REQUIRES --covariates-reason; both are written into the "
                   "immutable record so the deviation is auditable rather than silent.")
    p.add_argument("--covariates-reason", help="why the covariate set deviates from the proposal")
    p.add_argument("--cohort-spec", help="path to the machine-executable cohort spec "
                   "(default: data/cohort_specs/<H-id>.json). Its hash is recorded in the prereg so "
                   "the population cannot be redefined after unlock.")
    p.add_argument("--allow-missing-spec", action="store_true",
                   help="pre-register without a cohort spec. The extract must then be built by hand, "
                        "with nothing pinning its inclusion criteria — use only when the analysis "
                        "will not go through build_extract.py.")
    p.add_argument("--dry-run", action="store_true", help="render to stdout, commit nothing, unlock nothing")
    args = p.parse_args()

    hid = args.hypothesis_id
    if not SCRIPT.exists():
        die(f"{SCRIPT.relative_to(REPO)} does not exist — nothing to hash-lock the analysis to")
    if args.supersede and not args.reason:
        die("--supersede requires --reason")
    if args.covariates and not args.covariates_reason:
        die("--covariates requires --covariates-reason; an undocumented covariate change is the "
            "adjustment-shopping this gate exists to prevent")
    if args.estimand == "interaction" and not args.interaction_with:
        die("--estimand interaction requires --interaction-with <column>")
    if args.estimand == "main_effect" and args.interaction_with:
        die("--interaction-with is meaningless for --estimand main_effect")

    spec_path = Path(args.cohort_spec) if args.cohort_spec else SPECS / f"{hid}.json"
    if spec_path.exists():
        args._spec_hash = sha256(spec_path)
        args._spec_file = str(spec_path.relative_to(REPO))
        spec = json.loads(spec_path.read_text())
        if spec.get("hypothesis_id") != hid:
            die(f"{spec_path.name} declares hypothesis_id '{spec.get('hypothesis_id')}', expected '{hid}'")
        if args.estimand == "interaction":
            got = (spec.get("modifier") or {}).get("name")
            if got != args.interaction_with:
                die(f"--interaction-with is '{args.interaction_with}' but the cohort spec's modifier "
                    f"is '{got}'. The prereg and the spec must register the same modifier.")
        args._left_trunc = spec.get("time", {}).get("left_truncate_at_sequencing", True)
    elif args.allow_missing_spec:
        args._spec_hash, args._spec_file, args._left_trunc = None, None, True
    else:
        die(f"no cohort spec at {spec_path.relative_to(REPO)}. The spec is what makes the population "
            f"reproducible: without it the extract is built by hand after unlock, which is choosing "
            f"inclusion criteria with outcomes in view. Write one, or pass --allow-missing-spec if "
            f"this analysis genuinely will not use build_extract.py.")

    h = load_hypothesis(hid)
    check_gate(h)
    out_path = check_immutability(hid, args.supersede)
    args._version = int(out_path.stem.rsplit("_v", 1)[1])
    ep = endpoint_def(args.endpoint)

    manifest = json.loads(MANIFEST.read_text())
    already_unlocked = any(e["hypothesis_id"] == hid for e in manifest.get("unlock_log", []))
    invalidated = bool(args.supersede and already_unlocked)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    script_hash = sha256(SCRIPT)
    doc = render(h, ep, args, script_hash, now, args.supersede, invalidated)

    if args.dry_run:
        print(doc)
        print("--- DRY RUN: nothing committed, nothing unlocked ---", file=sys.stderr)
        return 0

    if git("status", "--porcelain", str(PREREG_DIR.relative_to(REPO))):
        die("pipeline/preregistration/ has uncommitted changes — resolve them before pre-registering")

    PREREG_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc)
    git("add", str(out_path.relative_to(REPO)))
    subject = f"prereg({hid}): lock hypothesis on {args.endpoint}, {args.partition} partition"
    if args.supersede:
        subject = f"prereg({hid}): supersede {args.supersede} — {args.reason}"
    git("commit", "-q", "-m", subject)
    commit_sha = git("rev-parse", "HEAD")
    print(f"committed {out_path.relative_to(REPO)} as {commit_sha[:12]}")

    if invalidated:
        print("INVALIDATED prereg — no new unlock granted.", file=sys.stderr)
        return 0

    # Unlock happens ONLY after the commit exists.
    manifest["unlock_log"].append({
        "hypothesis_id": hid,
        "cohort_spec_sha256": args._spec_hash,
        "prereg_file": out_path.name,
        "prereg_commit_sha": commit_sha,
        "partition": args.partition,
        "endpoint_id": args.endpoint,
        "confirmatory_script_sha256": script_hash,
        "unlocked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unlocked_by": git("config", "user.email", check=False) or "unknown",
    })
    manifest["partitions"][args.partition]["unlocked"] = True
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    git("add", str(MANIFEST.relative_to(REPO)))
    git("commit", "-q", "-m", f"unlock({hid}): {args.partition}/{args.endpoint} slice, authorised by {commit_sha[:12]}")
    print(f"unlocked {args.partition} slice for {hid} on endpoint {args.endpoint}")
    print(f"next: python3 pipeline/analysis/confirmatory.py {hid}   (runs once)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
