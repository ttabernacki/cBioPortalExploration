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
import re
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


# --- Canonicalisation -------------------------------------------------------
# The mapper writes prose. Without normalising it, every coverage cell looks like n=1 and every
# claim looks like it has a unique subject and endpoint, which makes the contradiction /
# effect-modifier distinction inert. These are heuristics; raw strings are kept alongside.

QUALIFIER_PATTERNS = [
    (r"kras[- ]?(wild ?type|wt)", "kras_wildtype"),
    (r"kras[- ]?mutant", "kras_mutant"),
    (r"pd-?l1[- ]?positive", "pdl1_positive"),
    (r"pd-?l1[- ]?negative", "pdl1_negative"),
    (r"transcriptomic|independent of mutation|expression[- ]based", "transcriptomic_phenotype"),
    (r"squamous", "squamous"),
    (r"non-?squamous", "nonsquamous"),
    (r"never[- ]?smoker|non-?smoker", "never_smoker"),
    (r"neoadjuvant|resectable", "early_stage"),
]

STATE_CLASSES = {
    "loss_of_function": "altered", "mutation_any": "altered", "deletion": "altered",
    "amplification": "altered", "gain_of_function": "altered", "co_mutation": "altered",
    "underexpression": "altered", "overexpression": "altered",
    "wildtype": "wildtype", "exposure": "exposure", "not_applicable": "not_applicable",
}

ENDPOINT_BUCKETS = [
    (r"recurrence[- ]free survival|\brfs\b", "RFS"),
    (r"progression[- ]free survival|\bpfs\b", "PFS"),
    (r"overall survival|\bos\b(?!\w)", "OS"),
    (r"objective response|response rate|\borr\b|pathologic response|\bmpr\b", "response_rate"),
    (r"primary resistance|resistance to", "resistance"),
    (r"pd-?l1", "pdl1_expression"),
    (r"tumor mutational burden|\btmb\b", "TMB"),
    (r"t-?lymphocyte|t-?cell|\btil\b|cd8|foxp3|immunophenotype|immune cell infiltration", "T_cell_infiltration"),
    (r"neutrophil|mdsc|myeloid", "myeloid_infiltration"),
    (r"sting|interferon|dsdna|antigen processing|immunoproteasome|autophag", "innate_immune_signalling"),
    (r"prevalence|frequency|enrichment", "prevalence"),
    (r"tumor regression|efficacy in murine|blockade efficacy", "preclinical_efficacy"),
]


def build_entity_index(doc: dict) -> list[tuple[str, str]]:
    """(lowercased surface form, canonical name) pairs, longest first so 'STK11/LKB1' wins
    over 'STK11'. Disease and clinical-feature entities are excluded — they belong to context,
    not to the subject of a claim."""
    pairs = []
    for e in doc.get("entities", []):
        if e["type"] == "clinical_feature":
            continue
        for surface in [e["name"], *e.get("synonyms", [])]:
            pairs.append((norm(surface), e["name"], e["type"]))
    return sorted(pairs, key=lambda x: -len(x[0]))


def parse_subject(name: str, state: str | None, index: list[tuple[str, str]]) -> tuple[frozenset, str]:
    """Split a prose subject into (canonical entity set, qualifier).

    The qualifier is pulled out FIRST and treated as context, not subject. This is the whole
    point: 'STK11 in KRAS-mutant' and 'STK11 in KRAS-wildtype' are the SAME subject under
    DIFFERENT conditions — an effect modifier — not two different subjects.
    """
    text = norm(name)
    quals = []
    for pattern, label in QUALIFIER_PATTERNS:
        if re.search(pattern, text):
            quals.append(label)
            text = re.sub(pattern, " ", text)
    if "nonsquamous" in quals and "squamous" in quals:
        quals.remove("squamous")

    found, remaining = [], text
    for surface, canonical, etype in index:
        if surface and surface in remaining:
            if canonical not in [f[0] for f in found]:
                found.append((canonical, etype))
            remaining = remaining.replace(surface, " ")

    # A claim about a gene often names the treatment in its subject line ("STK11 mutation under
    # PD-(L)1 blockade"). The treatment is context. Keep drugs in the subject set only when
    # nothing biological was found — i.e. the claim really is about the drug.
    biological = [n for n, t in found if t not in ("drug", "drug_class")]
    names = biological or [n for n, _ in found] or [name.strip()]
    return frozenset(names), "+".join(sorted(quals))


def canon_endpoint(on: str | None) -> str:
    t = norm(on)
    for pattern, bucket in ENDPOINT_BUCKETS:
        if re.search(pattern, t):
            return bucket
    return "other"


def norm(s: str | None) -> str:
    return (s or "").strip().lower()


def subject_key(c: dict) -> str:
    """Identity of what the claim is ABOUT. The qualifier is deliberately excluded — it is
    context, and folding it in here would hide every effect modifier as a subject mismatch."""
    ents, _ = c["_parsed_subject"]
    state = STATE_CLASSES.get(c["subject"].get("state") or "", "altered")
    return "+".join(sorted(ents)) + "|" + state


def canon_treatment(raw: str | None) -> str:
    """Collapse free-text treatment descriptions into comparable classes.

    The mapper writes prose ("PD-(L)1 inhibition", "PD-1/PD-L1 axis blockade", "PD-L1/PD-1
    inhibition" are all one thing). Without this, every coverage cell looks like n=1 and the
    matrix reveals no gaps at all. Heuristic by construction — the raw string is kept alongside.
    """
    t = norm(raw)
    if not t or "not treatment-conditioned" in t:
        return "not_treatment_conditioned"
    io = any(k in t for k in ("pd-1", "pd-l1", "pd-(l)1", "pembrolizumab", "nivolumab",
                              "atezolizumab", "durvalumab", "sintilimab", "immunotherapy",
                              "checkpoint", "icb", "ici"))
    ctla4 = any(k in t for k in ("tremelimumab", "ipilimumab", "ctla-4", "ctla4"))
    chemo = any(k in t for k in ("platinum", "chemo", "pemetrexed", "carboplatin", "cisplatin",
                                 "paclitaxel", "docetaxel"))
    if not io:
        return "chemotherapy_only" if chemo else "other_or_preclinical"
    if ctla4:
        return "chemo_plus_dual_io" if chemo else "dual_io"
    if chemo:
        return "chemo_plus_io"
    return "io_monotherapy"


def canon_stage(raw: str | None) -> str:
    t = norm(raw)
    if not t:
        return "unspecified"
    if any(k in t for k in ("resectable", "early", "resected")):
        return "early_or_resectable"
    if "locally advanced" in t:
        return "locally_advanced"
    if any(k in t for k in ("metasta", "advanced", "iiib", "iiic", "iva", "ivb", "stage iv")):
        return "advanced_metastatic"
    return "unspecified"


def classify_pair(a: dict, b: dict) -> dict:
    """Classify a disagreeing pair. This is the distinction that drives hypothesis type:
    same-context opposition is a contradiction to re-test; different-context opposition is an
    effect modifier, i.e. a missing interaction term."""
    same_subject = subject_key(a) == subject_key(b)
    same_endpoint = canon_endpoint(a["predicate"]["on"]) == canon_endpoint(b["predicate"]["on"])
    opposing = (a["predicate"]["effect"], b["predicate"]["effect"]) in OPPOSING

    ctx_a = (canon_treatment(a["context"].get("treatment")), canon_stage(a["context"].get("stage")),
             norm(a["context"].get("model_system")), a["_parsed_subject"][1])
    ctx_b = (canon_treatment(b["context"].get("treatment")), canon_stage(b["context"].get("stage")),
             norm(b["context"].get("model_system")), b["_parsed_subject"][1])
    same_ctx = ctx_a == ctx_b
    differing = [d for d, x, y in zip(("treatment", "stage", "model_system", "subject_qualifier"), ctx_a, ctx_b) if x != y]

    ents_a, ents_b = a["_parsed_subject"][0], b["_parsed_subject"][0]
    nested = ents_a < ents_b or ents_b < ents_a

    if not same_subject and nested and same_endpoint:
        kind = "comutation_vs_single"
        note = ("One subject is a strict superset of the other (co-mutation vs single alteration) "
                "on the same endpoint — is the signal carried by the gene alone, or only in the "
                "co-mutant context? A missing-interaction hypothesis in its purest form.")
    elif not same_subject:
        kind = "cross_subject"
        note = ("Different subjects — a disagreement about which gene carries the signal, not a "
                "direct contradiction. Candidate for a head-to-head hypothesis.")
    elif not same_endpoint:
        kind = "cross_endpoint"
        note = ("Same subject, different measured endpoint — an endpoint-dissociation, not a "
                "contradiction. Worth a hypothesis only if the dissociation itself is the point.")
    elif opposing and same_ctx:
        kind = "direct_contradiction"
        note = ("Same subject, endpoint and context, opposing directions — a genuine "
                "contradiction worth re-testing at scale.")
    elif opposing:
        kind = "effect_modifier"
        note = (f"Opposing directions, context differs on {', '.join(differing)} — this is the "
                f"shape of a missing interaction term, not a contradiction.")
    elif same_ctx:
        kind = "magnitude_disagreement"
        note = "Same direction and context; the sources disagree on magnitude, not sign."
    else:
        kind = "context_dependent"
        note = f"Same direction, context differs on {', '.join(differing)}."

    return {
        "claims": [a["claim_id"], b["claim_id"]],
        "classification": kind,
        "subjects": [a["subject"]["name"], b["subject"]["name"]],
        "endpoint_bucket": [canon_endpoint(a["predicate"]["on"]), canon_endpoint(b["predicate"]["on"])],
        "measured_on": [a["predicate"]["on"], b["predicate"]["on"]],
        "qualifiers": [a["_parsed_subject"][1], b["_parsed_subject"][1]],
        "effects": [a["predicate"]["effect"], b["predicate"]["effect"]],
        "designs": [a["evidence"]["design"], b["evidence"]["design"]],
        "n": [(a["context"].get("population") or {}).get("n"), (b["context"].get("population") or {}).get("n")],
        "years": [a["evidence"]["year"], b["evidence"]["year"]],
        "context_differs_on": differing,
        "note": note,
    }


def contradictions(claims: list[dict]) -> list[dict]:
    """Every disagreeing pair — links the mapper declared, plus ones it did not cross-link —
    each classified by context. Declared edges are classified too: on a well-cross-linked graph
    they are the overwhelming majority, and leaving them unclassified makes the whole
    contradiction-vs-effect-modifier distinction inert."""
    by_id = {c["claim_id"]: c for c in claims}
    out, seen = [], set()

    for c in claims:
        for ref in c.get("conflicts_with", []):
            pair = tuple(sorted((c["claim_id"], ref)))
            if pair in seen or ref not in by_id:
                continue
            seen.add(pair)
            rec = classify_pair(by_id[pair[0]], by_id[pair[1]])
            rec["source"] = "declared"
            out.append(rec)

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
        rec = classify_pair(a, b)
        rec["source"] = "computed"
        out.append(rec)

    order = {"direct_contradiction": 0, "effect_modifier": 1, "comutation_vs_single": 2,
             "cross_subject": 3, "cross_endpoint": 4, "magnitude_disagreement": 5,
             "context_dependent": 6}
    return sorted(out, key=lambda r: order.get(r["classification"], 9))


def population_gaps(claims: list[dict]) -> dict:
    """Which context cells the literature covers, and which it does not."""
    dims = {
        "smoking_status": lambda c: norm(c["context"].get("population", {}).get("smoking_status")),
        "ancestry_reported": lambda c: str(c["context"].get("population", {}).get("ancestry_reported", False)),
        "line_of_therapy": lambda c: norm(c["context"].get("line_of_therapy")),
        "treatment_class": lambda c: canon_treatment(c["context"].get("treatment")),
        "model_system": lambda c: norm(c["context"].get("model_system")),
        "endpoint_bucket": lambda c: canon_endpoint(c["predicate"]["on"]),
        "subject_qualifier": lambda c: c["_parsed_subject"][1] or "<none>",
        "stage_class": lambda c: canon_stage(c["context"].get("stage")),
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
    """Entities studied alone but never jointly. Uses the canonical parse, not raw names —
    'STK11/LKB1' is one gene under two names, while 'KRAS + STK11 co-mutation' is two."""
    solo, joint = set(), set()
    for c in claims:
        ents = c["_parsed_subject"][0]
        if len(ents) > 1:
            joint.add(tuple(sorted(ents)))
        else:
            solo.update(ents)

    studied_pairs = set()
    for combo in joint:
        studied_pairs.update(tuple(sorted(p)) for p in itertools.combinations(combo, 2))
    universe = sorted(solo | {e for combo in joint for e in combo})
    untested = [list(p) for p in itertools.combinations(universe, 2) if tuple(sorted(p)) not in studied_pairs]
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
    index = build_entity_index(doc)
    for c in claims:
        c["_parsed_subject"] = parse_subject(c["subject"]["name"], c["subject"].get("state"), index)

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

    for c in claims:
        c.pop("_parsed_subject", None)

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

    cons = report["contradictions"]
    by_kind = Counter(c["classification"] for c in cons)
    print(f"\nDisagreeing pairs: {len(cons)}  {dict(by_kind)}")
    for kind in ("direct_contradiction", "effect_modifier", "comutation_vs_single"):
        rows = [c for c in cons if c["classification"] == kind]
        if not rows:
            continue
        print(f"\n  == {kind.replace('_', ' ').upper()} ({len(rows)}) ==")
        for c in rows:
            ns = "/".join(str(x) if x is not None else "?" for x in c["n"])
            print(f"  {' vs '.join(c['claims'])} [{c['source']}]: {c['subjects'][0]} "
                  f"{c['effects']} on {c['measured_on'][0]}")
            print(f"      n={ns} years={c['years']} designs={c['designs']}"
                  + (f" differs_on={c['context_differs_on']}" if c["context_differs_on"] else ""))
    other = [c for c in cons if c["classification"] not in
             ("direct_contradiction", "effect_modifier", "comutation_vs_single")]
    if other:
        print(f"\n  == OTHER ({len(other)}) — read these, they are not straight contradictions ==")
        for c in other:
            print(f"  {' vs '.join(c['claims'])}: {c['classification']} "
                  f"({c['subjects'][0]} vs {c['subjects'][1]})")

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
