"""The cohort spec and extract builder — R5c.

The extract is where inclusion criteria get chosen. If these guarantees lapse, someone picks the
population after unlock and nothing records that they did.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import run

BUILD = ["pipeline/analysis/build_extract.py"]
PRE = ["pipeline/preregister.py"]


@pytest.fixture
def raw(tmp_path):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    d = tmp_path / "raw"
    d.mkdir()
    r = np.random.default_rng(5)
    n = 1200
    pid = [f"P{i:05d}" for i in range(n)]
    pd.DataFrame({"patient_id": pid, "sex": r.choice(["female", "male"], n),
                  "age_at_dx_years": r.normal(66, 10, n)}).to_csv(d / "patient.csv", index=False)
    pd.DataFrame({"patient_id": pid,
                  "oncotree_code": r.choice(["LUAD", "BRCA"], n, p=[.6, .4]),
                  "stage_at_dx": r.choice(["III", "IV"], n, p=[.3, .7])}).to_csv(d / "diagnosis.csv", index=False)
    seq = r.random(n) < 0.9
    pd.DataFrame({"sample_id": [f"S{i}" for i in range(n)], "patient_id": pid,
                  "tmb_mut_per_mb": np.round(r.gamma(3, 3, n), 2)})[seq].to_csv(
        d / "genomic_sample_level.csv", index=False)
    rows = []
    for i, p in enumerate(pid):
        if not seq[i]:
            continue
        gene = "STK11" if r.random() < 0.2 else "TP53"
        rows.append({"patient_id": p, "hugo_symbol": gene,
                     "alteration_type": r.choice(["nonsense", "missense"]),
                     "oncogenic_annotation": "oncogenic",
                     "days_dx_to_sequencing": int(r.integers(5, 60))})
    pd.DataFrame(rows).to_csv(d / "genomic_variant.csv", index=False)
    pd.DataFrame({"patient_id": pid, "drug_class": r.choice(["anti_PD1", "platinum"], n),
                  "start_day_offset": r.integers(0, 30, n)}).to_csv(d / "treatment.csv", index=False)
    t = r.exponential(24, n)
    c = r.exponential(40, n)
    pd.DataFrame({"patient_id": pid, "os_days": np.minimum(t, c) * 30,
                  "death_event": (t <= c).astype(int)}).to_csv(d / "outcomes.csv", index=False)
    return d


def spec_dict(hid="H-001"):
    return {
        "schema_version": "1.0", "hypothesis_id": hid,
        "population": {"require_sequencing": True, "filters": [
            {"table": "diagnosis", "field": "oncotree_code", "op": "eq", "value": "LUAD"},
            {"table": "diagnosis", "field": "stage_at_dx", "op": "eq", "value": "IV"}]},
        "exposure": {"kind": "genomic_alteration", "gene": "STK11",
                     "alteration_types": ["nonsense"], "oncogenic_annotation_in": ["oncogenic"]},
        "covariates": [{"name": "age at index", "table": "patient", "field": "age_at_dx_years"},
                       {"name": "sex", "table": "patient", "field": "sex"}],
        "time": {"origin": "index_treatment_start", "left_truncate_at_sequencing": True},
    }


def write_spec(repo, spec, hid="H-001"):
    p = repo / "pipeline" / "data" / "cohort_specs" / f"{hid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(spec, indent=2) + "\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "spec"], cwd=repo, check=True, capture_output=True)
    return p


def test_spec_validates_against_its_schema():
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(__file__).resolve().parent.parent
    schema = json.loads((root / "schemas" / "cohort_spec.schema.json").read_text())
    jsonschema.Draft7Validator(schema).validate(spec_dict())


def test_dry_run_touches_no_outcome_column(ranked, raw):
    write_spec(ranked, spec_dict())
    r = run([*BUILD, "H-001", "--raw", str(raw), "--dry-run"], cwd=ranked)
    assert r.returncode == 0
    assert "ATTRITION" in r.stdout
    assert "os_days" not in r.stdout and "death_event" not in r.stdout


def test_prereg_refuses_without_a_cohort_spec(ranked):
    r = run([*PRE, "H-001", "--endpoint", "OS"], cwd=ranked)
    assert r.returncode == 1
    assert "no cohort spec" in r.stderr


def test_build_refuses_after_spec_is_edited(ranked, raw):
    p = write_spec(ranked, spec_dict())
    assert run([*PRE, "H-001", "--endpoint", "OS"], cwd=ranked).returncode == 0
    spec = json.loads(p.read_text())
    spec["population"]["filters"][1]["value"] = "III"  # quietly widen the population
    p.write_text(json.dumps(spec, indent=2))
    r = run([*BUILD, "H-001", "--raw", str(raw), "--out", str(ranked / "e.csv")], cwd=ranked)
    assert r.returncode == 1
    assert "outcomes in view" in r.stderr


def test_build_refuses_on_covariate_mismatch(ranked, raw):
    p = write_spec(ranked, spec_dict())
    assert run([*PRE, "H-001", "--endpoint", "OS"], cwd=ranked).returncode == 0
    # Same hash-relevant content? No — editing changes the hash, so this asserts the hash guard
    # fires first, which is the stronger of the two checks.
    spec = json.loads(p.read_text())
    spec["covariates"].append({"name": "smoking", "table": "patient", "field": "sex"})
    p.write_text(json.dumps(spec, indent=2))
    r = run([*BUILD, "H-001", "--raw", str(raw), "--out", str(ranked / "e.csv")], cwd=ranked)
    assert r.returncode == 1


def test_prereg_refuses_modifier_mismatch(ranked):
    s = spec_dict()
    s["modifier"] = {"name": "tmb_mut_per_mb", "kind": "field_value",
                     "table": "genomic_sample_level", "field": "tmb_mut_per_mb", "continuous": True}
    write_spec(ranked, s)
    r = run([*PRE, "H-001", "--endpoint", "OS", "--estimand", "interaction",
             "--interaction-with", "something_else"], cwd=ranked)
    assert r.returncode == 1
    assert "same modifier" in r.stderr


def test_full_chain_builds_and_analyses(ranked, raw):
    write_spec(ranked, spec_dict())
    assert run([*PRE, "H-001", "--endpoint", "OS"], cwd=ranked).returncode == 0
    out = ranked / "extract.csv"
    assert run([*BUILD, "H-001", "--raw", str(raw), "--out", str(out)], cwd=ranked).returncode == 0
    r = run(["pipeline/analysis/confirmatory.py", "H-001"], cwd=ranked,
            env={"PIPELINE_LOCKED_DATA_PATH": str(out)})
    assert r.returncode == 0, r.stderr
    res = json.loads((ranked / "pipeline/analysis/results/H-001.json").read_text())["result"]
    assert res["left_truncated"] is True
    assert res["categorical_encoding"]["sex"]["reference_level"] == "female"
    assert res["n_analysed_complete_case"] > 0


def test_left_truncation_is_recorded_as_unchecked_ph(ranked, raw):
    """A left-truncated fit has no Schoenfeld residuals. That must be reported, not skipped —
    and never obtained by refitting without truncation."""
    write_spec(ranked, spec_dict())
    assert run([*PRE, "H-001", "--endpoint", "OS"], cwd=ranked).returncode == 0
    out = ranked / "extract.csv"
    run([*BUILD, "H-001", "--raw", str(raw), "--out", str(out)], cwd=ranked)
    run(["pipeline/analysis/confirmatory.py", "H-001"], cwd=ranked,
        env={"PIPELINE_LOCKED_DATA_PATH": str(out)})
    res = json.loads((ranked / "pipeline/analysis/results/H-001.json").read_text())["result"]
    assert res["proportional_hazards_test_p"] is None
    assert "UNCHECKED" in res["proportional_hazards_note"]
