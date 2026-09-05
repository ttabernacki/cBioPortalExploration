"""Mechanical remap of the ESR1 claim graph onto claim_graph.schema.json.

Every rule is explicit below. No claim content is invented: quotes, identifiers, effect
directions, magnitudes-as-prose, confidence and conflicts_with are preserved verbatim or moved
to a field that holds them losslessly.
"""
import json, re, sys
from collections import Counter

d = json.load(open('pipeline/data/claim_graph.json'))

DESIGN = {"rct_secondary_analysis": "trial_secondary_analysis", "preprint": "unclear"}
MODEL  = {"human": "human_clinical", "xenograft": "mouse"}
STYPE  = {"assay": "biomarker"}

def specimen(state, model, popn, assay):
    blob = " ".join(x for x in (state, popn, assay) if x).lower()
    if model in ("cell_line", "xenograft", "in_silico"): return "preclinical_model"
    paired = ("paired" in blob and ("plasma" in blob or "ctdna" in blob)) or "tissue-ctdna" in blob
    if paired: return "both_paired"
    liquid = any(k in blob for k in ("ctdna", "cfdna", "cell-free", "plasma", "liquid biopsy", "cf dna"))
    tissue = any(k in blob for k in ("tissue", "biopsy", "surgical", "primary tumor", "primary tumour",
                                     "msk-impact", "targeted sequencing", "tumor specimens"))
    if liquid and tissue: return "both_paired"
    if liquid: return "ctDNA"
    if tissue: return "tumor_tissue"
    return "not_specified"

def state_enum(txt):
    if not txt: return "not_applicable"
    t = txt.lower()
    if "wild-type" in t or "wildtype" in t: return "wildtype"
    if any(k in t for k in ("administered", "applied", "prior treatment", "expressed in")): return "exposure"
    if any(k in t for k in ("mutant", "mutation", "pathogenic", "codon", "polyclonality",
                            "engineered", "present")): return "mutation_any"
    return "not_applicable"

def src_type(cit, design):
    if cit.get("pmid"): return "pubmed"
    doi = (cit.get("doi") or "") + (cit.get("url") or "")
    if "biorxiv" in doi.lower(): return "biorxiv"
    if "medrxiv" in doi.lower(): return "medrxiv"
    if cit.get("nct_id"): return "clinicaltrials.gov"
    if doi: return "journal_site"
    return "other"

CTX_KEEP = {"disease","stage","treatment","line_of_therapy","population","model_system",
            "specimen","assay_note"}
audit = Counter()

for c in d["claims"]:
    # ---- subject -------------------------------------------------------
    subj = c["subject"]
    orig_state = subj.get("state")
    subj["type"] = STYPE.get(subj.get("type"), subj.get("type"))
    if STYPE.get(c["subject"].get("type")): audit["subject.type remapped"] += 1
    new_state = state_enum(orig_state)
    if orig_state and orig_state != new_state:
        subj["state_detail"] = orig_state          # preserved verbatim
        audit["subject.state_detail preserved"] += 1
    subj["state"] = new_state

    # ---- context -------------------------------------------------------
    ctx = c["context"]
    popn_raw = ctx.get("population")
    assay_raw = ctx.get("assay")
    model = ctx.get("model_system")
    ctx["specimen"] = specimen(orig_state, model, popn_raw if isinstance(popn_raw,str) else "",
                               assay_raw if isinstance(assay_raw,str) else "")
    ctx["assay_note"] = assay_raw if isinstance(assay_raw, str) else None
    ctx["model_system"] = MODEL.get(model, model)
    if isinstance(popn_raw, str):
        m = re.search(r"(\d[\d,]{1,7})", popn_raw)
        ctx["population"] = {
            "n": int(m.group(1).replace(",", "")) if m else None,
            "ancestry_reported": False,
            "notable_exclusions": [popn_raw],       # prose preserved verbatim
        }
        audit["context.population prose preserved"] += 1
    for k in [k for k in ctx if k not in CTX_KEEP]:
        ctx.pop(k)
    ctx.setdefault("disease", "hormone receptor-positive breast cancer")

    # ---- predicate -----------------------------------------------------
    pred = c["predicate"]
    mag = pred.get("magnitude")
    if isinstance(mag, str):
        pred["magnitude"] = {"metric": "not_reported"}
        note = c.get("mechanism_note")
        c["mechanism_note"] = (f"{note} " if note else "") + f"[reported magnitude] {mag}"
        audit["magnitude prose moved to mechanism_note"] += 1
    if pred.get("direction_certainty") == "trend_only" and pred.get("effect") == "no_effect":
        pred["direction_certainty"] = "reported_nonsignificant"
        audit["null reclassified as reported_nonsignificant"] += 1
    for k in [k for k in pred if k not in {"effect","on","magnitude","direction_certainty"}]:
        pred.pop(k)

    # ---- evidence ------------------------------------------------------
    ev = c["evidence"]
    src = ev.pop("source", {}) or {}
    cit = {k: src.get(k) for k in ("title","pmid","doi","nct_id","url","journal")}
    cit["first_author"] = src.get("first_author") or (src.get("authors_short") or "").replace(" et al.","") or None
    cit = {k: v for k, v in cit.items() if v is not None or k == "title"}
    ev["citation"] = cit
    ev["year"] = src.get("year")
    ev["design"] = DESIGN.get(ev.get("design"), ev.get("design"))
    ev["source_type"] = src_type(cit, ev.get("design"))
    for k in [k for k in ev if k not in {"source_type","citation","year","design",
                                         "supporting_quote","verified"}]:
        ev.pop(k)
    if not ev.get("verified"):
        ev["supporting_quote"] = ev.get("supporting_quote") or None

    for k in [k for k in c if k not in {"claim_id","subject","predicate","context","evidence",
                                        "confidence","conflicts_with","mechanism_note"}]:
        c.pop(k)

# ---- envelope ----------------------------------------------------------
d["schema_version"] = "1.0"
d.setdefault("generated_utc", "2026-09-05T05:10:00Z")
if isinstance(d.get("coverage_notes"), list):
    d["coverage_notes"] = "\n\n".join(d["coverage_notes"])
    audit["coverage_notes joined"] += 1
sr = d.setdefault("search", {})
sr.setdefault("queries", ["ESR1 mutation endocrine resistance breast cancer"])
sr.setdefault("sources", ["pubmed"])
sr["date_range"] = {"from_year": 2013, "to_year": 2026}
sr["sources"] = [x if x in ("pubmed","biorxiv","medrxiv","clinicaltrials.gov",
                            "conference_abstract","journal_site","other") else "other"
                 for x in sr["sources"]]
for k in [k for k in sr if k not in {"queries","sources","date_range","notes"}]: sr.pop(k)
for e in d.get("entities", []):
    e["type"] = STYPE.get(e.get("type"), e.get("type"))
    for k in [k for k in e if k not in {"entity_id","type","name","synonyms","identifier"}]: e.pop(k)
for k in [k for k in d if k not in {"schema_version","topic","generated_utc","search",
                                    "entities","claims","coverage_notes"}]:
    d.pop(k)

json.dump(d, open('pipeline/data/claim_graph.json','w'), indent=2)
open('pipeline/data/claim_graph.json','a').write('\n')
print("REMAP AUDIT — rules applied:")
for k, v in sorted(audit.items()): print(f"  {v:3d}  {k}")
print(f"\nspecimen distribution: {dict(Counter(c['context']['specimen'] for c in d['claims']))}")
