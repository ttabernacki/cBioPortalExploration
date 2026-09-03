#!/usr/bin/env python3
"""Mechanical gap analysis over claim_graph.json.

Computes the structural gaps that should NOT be left to model judgement: which claims actually
contradict, which context cells have no coverage, which claims rest on small old cohorts, and
which entities are never studied in combination. gap-finder reads this report and does the part
that genuinely needs reasoning — deciding which gaps are worth a testable hypothesis.

    python3 pipeline/analyze_claim_graph.py                 # human-readable
    python3 pipeline/analyze_claim_graph.py --json          # machine-readable to stdout
    python3 pipeline/analyze_claim_graph.py --out           # write data/gap_report.json

Touches only pipeline/data/claim_graph.json. No dataset access, no outcome access.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GRAPH = ROOT / "data" / "claim_graph.json"
REPORT = ROOT / "data" / "gap_report.json"

STALE_BEFORE = 2020
SMALL_COHORT = 300
OPPOSING = {
    ("increases", "decreases"), ("decreases", "increases"),
    ("confers_sensitivity", "confers_resistance"), ("confers_resistance", "confers_sensitivity"),
    ("increases", "no_effect"), ("no_effect", "increases"),
    ("decreases", "no_effect"), ("no_effect", "decreases"),
    ("confers_resistance", "no_effect"), ("no_effect", "confers_resistance"),
    ("confers_sensitivity", "no_effect"), ("no_effect", "confers_sensitivity"),
}


def norm(s: str | None) -> str:
    return (s or "").strip().lower()


def subject_key(c: dict) -> str:
    s = c["subject"]
    return f"{norm(s['name'])}|{norm(s.get('state'))}"


def contradictions(claims: list[dict]) -> list[dict]:
    """Explicit conflicts_with links, plus implicit ones the mapper may not have cross-linked."""
    out = []
    seen: set[tuple[str, str]] = set()

    for c in claims:
        for ref in c.get("conflicts_with", []):
            pair = tuple(sorted((c["claim_id"], ref)))
            if pair in seen:
                continue
            seen.add(pair)
            out.append({"kind": "declared", "claims": list(pair)})

    for a, b in itertools.combinations(claims, 2):
        pair = tuple(sorted((a["claim_id"], b["claim_id"])))
        if pair in seen:
            continue
        if subject_key(a) != subject_key(b):
            continue
        if norm(a["predicate"]["on"]) != norm(b["predicate"]["on"]):
            continue
        if (a["predicate"]["effect"], b["predicate"]["effect"]) not in OPPOSING:
            continue
        seen.add(pair)
        same_ctx = norm(a["context"]["disease"]) == norm(b["context"]["disease"]) and \
            norm(a["context"].get("treatment")) == norm(b["context"].get("treatment"))
        out.append({
            "kind": "undeclared",
            "claims": list(pair),
            "subject": a["subject"]["name"],
            "measured_on": a["predicate"]["on"],
            "effects": [a["predicate"]["effect"], b["predicate"]["effect"]],
            "same_context": same_ctx,
            "note": (
                "Same subject and endpoint, opposing directions, same context — a genuine "
                "contradiction worth re-testing at scale."
                if same_ctx else
                "Opposing directions in DIFFERENT contexts — likely an effect modifier, not a "
                "contradiction. This is the shape of a missing interaction term."
            ),
        })
    return out


def population_gaps(claims: list[dict]) -> dict:
    """Which context cells the literature covers, and which it does not."""
    dims = {
        "smoking_status": lambda c: norm(c["context"].get("population", {}).get("smoking_status")),
        "ancestry_reported": lambda c: str(c["context"].get("population", {}).get("ancestry_reported", False)),
        "line_of_therapy": lambda c: norm(c["context"].get("line_of_therapy")),
        "treatment": lambda c: norm(c["context"].get("treatment")),
        "model_system": lambda c: norm(c["context"].get("model_system")),
        "stage": lambda c: norm(c["context"].get("stage")),
    }
    coverage = {}
    for name, fn in dims.items():
        counts = Counter(fn(c) or "<unspecified>" for c in claims)
        coverage[name] = dict(counts.most_common())

    human = [c for c in claims if norm(c["context"].get("model_system")).startswith("human")]
    ancestry_reported = sum(
        1 for c in human if c["context"].get("population", {}).get("ancestry_reported") is True
    )
    return {
        "coverage": coverage,
        "human_clinical_claims": len(human),
        "human_claims_reporting_ancestry": ancestry_reported,
        "ancestry_blind_fraction": round(1 - ancestry_reported / len(human), 3) if human else None,
    }


def temporal_staleness(claims: list[dict]) -> list[dict]:
    """Small, old, clinical claims — the ones worth re-testing in a large modern cohort."""
    out = []
    for c in claims:
        if not norm(c["context"].get("model_system")).startswith("human"):
            continue
        n = (c["context"].get("population") or {}).get("n")
        year = c["evidence"]["year"]
        if year < STALE_BEFORE and (n is None or n < SMALL_COHORT):
            out.append({
                "claim_id": c["claim_id"],
                "year": year,
                "n": n,
                "design": c["evidence"]["design"],
                "subject": c["subject"]["name"],
                "effect": c["predicate"]["effect"],
                "on": c["predicate"]["on"],
                "reason": f"{year}, n={n if n is not None else 'unreported'} — underpowered by modern standards",
            })
    return sorted(out, key=lambda x: x["year"])


def interaction_gaps(claims: list[dict]) -> dict:
    """Entities studied alone but never jointly. The classic missing-interaction shape."""
    solo, joint = set(), set()
    for c in claims:
        s = c["subject"]
        names = [n.strip() for n in s["name"].replace("/", "+").split("+") if n.strip()]
        if s.get("state") == "co_mutation" or s["type"] == "gene_combination" or len(names) > 1:
            joint.add(tuple(sorted(norm(n) for n in names)))
        else:
            solo.add(norm(s["name"]))

    studied_pairs = {p for p in joint if len(p) == 2}
    untested = [
        list(p) for p in itertools.combinations(sorted(solo), 2) if tuple(sorted(p)) not in studied_pairs
    ]
    return {
        "entities_studied_alone": sorted(solo),
        "combinations_studied": [list(p) for p in sorted(joint)],
        "untested_pairs": untested[:40],
        "untested_pair_count": len(untested),
        "note": (
            "Untested pairs are candidate interaction terms, NOT hypotheses. gap-finder must "
            "supply a mechanistic reason why a specific pair would interact before proposing it — "
            "enumerating pairs is exactly the pattern-matching stage 3 exists to reject."
        ),
    }


def evidence_quality(claims: list[dict]) -> dict:
    unverified = [c["claim_id"] for c in claims if not c["evidence"]["verified"]]
    return {
        "n_claims": len(claims),
        "verified": len(claims) - len(unverified),
        "unverified": unverified,
        "by_design": dict(Counter(c["evidence"]["design"] for c in claims).most_common()),
        "by_confidence": dict(Counter(c["confidence"] for c in claims).most_common()),
        "by_model_system": dict(Counter(norm(c["context"].get("model_system")) or "<unspecified>" for c in claims).most_common()),
        "year_range": [min(c["evidence"]["year"] for c in claims), max(c["evidence"]["year"] for c in claims)] if claims else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--out", action="store_true",
                    help="write data/gap_report.json for gap-finder to read (it has no Bash)")
    args = ap.parse_args()

    if not GRAPH.exists():
        print(f"{GRAPH.relative_to(ROOT.parent)} does not exist — run literature-mapper first", file=sys.stderr)
        return 1
    doc = json.loads(GRAPH.read_text())
    claims = doc["claims"]

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topic": doc["topic"],
        "evidence_quality": evidence_quality(claims),
        "contradictions": contradictions(claims),
        "population_gaps": population_gaps(claims),
        "temporal_staleness": temporal_staleness(claims),
        "interaction_gaps": interaction_gaps(claims),
        "coverage_notes_from_mapper": doc.get("coverage_notes", ""),
    }

    if args.out:
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {REPORT.relative_to(ROOT.parent)}")
        if not args.json:
            return 0
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    eq = report["evidence_quality"]
    print(f"Topic: {report['topic']}")
    print(f"\nEvidence: {eq['n_claims']} claims, {eq['verified']} verified, years {eq['year_range']}")
    print(f"  designs: {eq['by_design']}")
    print(f"  systems: {eq['by_model_system']}")
    if eq["unverified"]:
        print(f"  UNVERIFIED (discount these): {', '.join(eq['unverified'])}")

    print(f"\nContradictions: {len(report['contradictions'])}")
    for c in report["contradictions"]:
        if c["kind"] == "declared":
            print(f"  [declared]   {' vs '.join(c['claims'])}")
        else:
            flag = "SAME CONTEXT" if c["same_context"] else "different context -> effect modifier?"
            print(f"  [undeclared] {' vs '.join(c['claims'])}: {c['subject']} {c['effects']} on {c['measured_on']} ({flag})")

    pg = report["population_gaps"]
    print(f"\nPopulation coverage ({pg['human_clinical_claims']} human clinical claims):")
    print(f"  ancestry reported in {pg['human_claims_reporting_ancestry']} "
          f"({pg['ancestry_blind_fraction']} ancestry-blind)")
    for dim, counts in pg["coverage"].items():
        print(f"  {dim}: {counts}")

    print(f"\nTemporally stale claims (pre-{STALE_BEFORE}, n<{SMALL_COHORT}): {len(report['temporal_staleness'])}")
    for t in report["temporal_staleness"]:
        print(f"  {t['claim_id']}: {t['subject']} {t['effect']} on {t['on']} — {t['reason']}")

    ig = report["interaction_gaps"]
    print(f"\nInteraction gaps: {len(ig['entities_studied_alone'])} entities studied alone, "
          f"{len(ig['combinations_studied'])} combinations studied, "
          f"{ig['untested_pair_count']} untested pairs")
    print(f"  NOTE: {ig['note']}")

    if report["coverage_notes_from_mapper"]:
        print(f"\nMapper's coverage notes:\n  {report['coverage_notes_from_mapper']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
