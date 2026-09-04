"""Outcome-blinding and audit-trail guarantees.

These test the claims the README makes. A regression here means the pipeline advertises a
boundary it no longer has.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import run

REPO = Path(__file__).resolve().parent.parent.parent
PIPELINE = REPO / "pipeline"
AGENTS = REPO / ".claude" / "agents"

GENERATION_ZONE = ["literature-mapper", "gap-finder", "plausibility-filter",
                   "feasibility-checker", "novelty-scorer"]


def frontmatter(name: str) -> dict:
    text = (AGENTS / f"{name}.md").read_text()
    block = text.split("---")[1]
    return {k.strip(): v.strip() for k, v in
            (l.split(":", 1) for l in block.splitlines() if ":" in l)}


def tools(name: str) -> set:
    return {t.strip() for t in frontmatter(name)["tools"].split(",")}


# ---------------------------------------------------------------- tool grants are the boundary

def test_no_generation_agent_has_bash():
    """The deny rule does not gate Bash — the gate and the confirmatory script need Python to read
    the manifest. A generation agent holding Bash could cat pipeline/locked/ and the boundary is
    gone. This is the load-bearing assumption behind the whole three-layer design."""
    for name in GENERATION_ZONE:
        assert "Bash" not in tools(name), f"{name} must not have Bash"


def test_literature_mapper_has_no_read_tool():
    assert tools("literature-mapper").isdisjoint({"Read", "Glob", "Grep"})


def test_only_the_test_zone_agent_has_bash():
    assert "Bash" in tools("confirmatory-analyst")


def test_deny_rules_cover_locked_path():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    deny = settings["permissions"]["deny"]
    for tool in ("Read", "Glob", "Grep"):
        assert any(d.startswith(f"{tool}(") and "pipeline/locked" in d for d in deny), \
            f"{tool} on pipeline/locked/** must be denied"


# ---------------------------------------------------------------- no outcomes upstream

def test_dataset_schema_exposes_no_outcome_field():
    schema = json.loads((PIPELINE / "data" / "dataset_schema.json").read_text())
    registry = json.loads((PIPELINE / "data" / "endpoint_definitions.json").read_text())
    outcome_fields = {f for ep in registry["endpoint_registry"]
                      for f in (ep["time_field"], ep["event_field"])}
    present = {f["name"] for t in schema["tables"] for f in t["fields"]}
    assert not (present & outcome_fields), f"outcome fields leaked: {present & outcome_fields}"
    assert schema["outcome_columns_present"] is False


def test_locked_manifest_holds_no_endpoint_definitions():
    """Definitions are design information, not data. Keeping them behind the gate forced an
    exception to the locked-path rule; they were split out so no exception is needed."""
    m = json.loads((PIPELINE / "locked" / "test_partition_manifest.json").read_text())
    assert "endpoint_registry" not in m
    assert "endpoint_registry_moved_to" in m


def test_no_patient_data_is_committed():
    for pattern in ("*.csv", "*.parquet", "*.tsv"):
        found = list((PIPELINE / "locked").glob(pattern))
        assert not found, f"patient data committed to pipeline/locked/: {found}"


def test_generation_artifacts_reference_no_outcome_fields(tmp_path):
    r = run(["pipeline/validate.py", "--blinding"], cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- audit trail

def test_validator_rejects_a_dropped_hypothesis(repo):
    data = repo / "pipeline" / "data"
    base = {"schema_version": "1.0", "generated_utc": "2026-09-04T00:00:00Z"}
    from conftest import hypothesis
    (data / "candidate_hypotheses.json").write_text(json.dumps({
        **base, "stage": "candidate", "source_artifact": "pipeline/data/claim_graph.json",
        "hypotheses": [hypothesis("H-001"), hypothesis("H-002")]}, indent=2))
    (data / "filtered_hypotheses.json").write_text(json.dumps({
        **base, "stage": "filtered", "source_artifact": "pipeline/data/candidate_hypotheses.json",
        "hypotheses": [hypothesis("H-001")]}, indent=2))
    r = run(["pipeline/validate.py", "filtered_hypotheses"], cwd=repo)
    assert r.returncode == 1
    assert "H-002" in r.stdout and "denominator" in r.stdout


def test_validator_rejects_status_missing(repo):
    from conftest import hypothesis
    h = hypothesis("H-001")
    del h["status"]
    (repo / "pipeline" / "data" / "candidate_hypotheses.json").write_text(json.dumps({
        "schema_version": "1.0", "stage": "candidate", "generated_utc": "2026-09-04T00:00:00Z",
        "source_artifact": "pipeline/data/claim_graph.json", "hypotheses": [h]}, indent=2))
    r = run(["pipeline/validate.py", "candidate_hypotheses"], cwd=repo)
    assert r.returncode == 1
    assert "no 'status'" in r.stdout


def test_merger_refuses_to_rewrite_an_earlier_stage(repo):
    from conftest import hypothesis
    data = repo / "pipeline" / "data"
    (data / "filtered_hypotheses.json").write_text(json.dumps({
        "schema_version": "1.0", "stage": "filtered", "generated_utc": "2026-09-04T00:00:00Z",
        "source_artifact": "pipeline/data/candidate_hypotheses.json",
        "hypotheses": [hypothesis("H-001")]}, indent=2))
    (data / "_feasible_patch.json").write_text(json.dumps({
        "stage": "feasible",
        "blocks": {"H-001": {"plausibility": {"verdict": "pass"}}}}))
    r = run(["pipeline/apply_stage.py", "feasible"], cwd=repo)
    assert r.returncode == 1
    assert "not rewritable" in r.stderr


def test_merger_refuses_an_invented_hypothesis(repo):
    from conftest import hypothesis
    data = repo / "pipeline" / "data"
    (data / "filtered_hypotheses.json").write_text(json.dumps({
        "schema_version": "1.0", "stage": "filtered", "generated_utc": "2026-09-04T00:00:00Z",
        "source_artifact": "pipeline/data/candidate_hypotheses.json",
        "hypotheses": [hypothesis("H-001")]}, indent=2))
    (data / "_feasible_patch.json").write_text(json.dumps({
        "stage": "feasible", "blocks": {"H-099": {"feasibility": {"verdict": "pass"}}}}))
    r = run(["pipeline/apply_stage.py", "feasible"], cwd=repo)
    assert r.returncode == 1
    assert "do not invent" in r.stderr


def test_graveyard_records_a_null_on_the_q_value_basis(repo):
    from conftest import hypothesis
    data = repo / "pipeline" / "data"
    results = repo / "pipeline" / "analysis" / "results"
    (data / "feasible_hypotheses.json").write_text(json.dumps({
        "schema_version": "1.0", "stage": "feasible", "generated_utc": "2026-09-04T00:00:00Z",
        "source_artifact": "pipeline/data/filtered_hypotheses.json",
        "hypotheses": [hypothesis("H-001")]}, indent=2))
    (results / "H-001.json").write_text(json.dumps({
        "hypothesis_id": "H-001", "run_utc": "2026-09-04T00:00:00Z",
        "result": {"hazard_ratio": 1.04, "ci_low": 0.88, "ci_high": 1.23, "p_value": 0.61,
                   "n_analysed_complete_case": 900, "n_events": 500}}))
    (results / "fdr_correction.json").write_text(json.dumps({"q_values": {"H-001": 0.71}}))
    assert run(["pipeline/graveyard.py", "--write"], cwd=repo).returncode == 0
    entries = json.loads((repo / "pipeline/graveyard/graveyard.json").read_text())["entries"]
    assert len(entries) == 1
    assert entries[0]["verdict"] == "null"
    assert "q-value" in entries[0]["reason"]
