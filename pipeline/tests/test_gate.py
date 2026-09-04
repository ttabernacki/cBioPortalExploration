"""The refusals are the product. Each one here is a rule from pipeline/CLAUDE.md.

If a test in this file starts passing for the wrong reason — because a refusal was relaxed to get
something working — the guarantee the pipeline advertises is gone.
"""
from __future__ import annotations

import json
import subprocess

from conftest import run

PRE = ["pipeline/preregister.py"]
CONF = ["pipeline/analysis/confirmatory.py"]


def prereg(repo, hid, *extra):
    return run([*PRE, hid, "--endpoint", "OS", "--allow-missing-spec", *extra], cwd=repo)


# ---------------------------------------------------------------- R3: gate on passes

def test_refuses_hypothesis_that_failed_feasibility(ranked):
    r = prereg(ranked, "H-003")
    assert r.returncode == 1
    assert "failed feasibility" in r.stderr


def test_refuses_hypothesis_missing_from_ranked(ranked):
    r = prereg(ranked, "H-404")
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_refuses_unknown_endpoint(ranked):
    r = run([*PRE, "H-001", "--endpoint", "NOPE", "--allow-missing-spec"], cwd=ranked)
    assert r.returncode == 1
    assert "unknown endpoint" in r.stderr


# ---------------------------------------------------------------- R4: immutability

def test_prereg_commits_then_unlocks_in_that_order(ranked):
    assert prereg(ranked, "H-001").returncode == 0
    log = subprocess.run(["git", "log", "--oneline"], cwd=ranked, capture_output=True, text=True).stdout
    lines = [l for l in log.splitlines() if "H-001" in l]
    assert "unlock(H-001)" in lines[0], "unlock must be the LATER commit"
    assert "prereg(H-001)" in lines[1], "prereg must be committed BEFORE the unlock"

    m = json.loads((ranked / "pipeline/locked/test_partition_manifest.json").read_text())
    entry = m["unlock_log"][0]
    assert entry["hypothesis_id"] == "H-001"
    sha = subprocess.run(["git", "cat-file", "-t", entry["prereg_commit_sha"]],
                         cwd=ranked, capture_output=True, text=True)
    assert sha.stdout.strip() == "commit", "unlock must name a commit that exists"


def test_refuses_second_prereg_for_same_hypothesis(ranked):
    assert prereg(ranked, "H-001").returncode == 0
    r = prereg(ranked, "H-001")
    assert r.returncode == 1
    assert "immutable" in r.stderr


def test_supersede_requires_a_reason(ranked):
    assert prereg(ranked, "H-001").returncode == 0
    r = prereg(ranked, "H-001", "--supersede", "prereg_H-001_v1.md")
    assert r.returncode == 1
    assert "--reason" in r.stderr


def test_failed_prereg_leaves_partition_locked(ranked):
    prereg(ranked, "H-003")  # refused at the gate
    m = json.loads((ranked / "pipeline/locked/test_partition_manifest.json").read_text())
    assert m["unlock_log"] == []
    assert all(p["unlocked"] is False for p in m["partitions"].values())


# ---------------------------------------------------------------- R5: one shot

def test_refuses_analysis_without_prereg(ranked, partition):
    r = run([*CONF, "H-001"], cwd=ranked, env={"PIPELINE_LOCKED_DATA_PATH": str(partition["effect"])})
    assert r.returncode == 1
    assert "no pre-registration" in r.stderr


def test_refuses_when_script_hash_changed(ranked, partition):
    assert prereg(ranked, "H-001").returncode == 0
    script = ranked / "pipeline/analysis/confirmatory.py"
    script.write_text(script.read_text() + "\n# cosmetic edit\n")
    r = run([*CONF, "H-001"], cwd=ranked, env={"PIPELINE_LOCKED_DATA_PATH": str(partition["effect"])})
    assert r.returncode == 1
    assert "has changed since" in r.stderr


def test_refuses_second_run_of_same_analysis(ranked, partition):
    assert prereg(ranked, "H-001").returncode == 0
    env = {"PIPELINE_LOCKED_DATA_PATH": str(partition["effect"])}
    assert run([*CONF, "H-001"], cwd=ranked, env=env).returncode == 0
    r = run([*CONF, "H-001"], cwd=ranked, env=env)
    assert r.returncode == 1
    assert "runs once" in r.stderr


def test_refuses_estimand_without_modifier(ranked):
    r = run([*PRE, "H-001", "--endpoint", "OS", "--allow-missing-spec",
             "--estimand", "interaction"], cwd=ranked)
    assert r.returncode == 1
    assert "--interaction-with" in r.stderr


# ---------------------------------------------------------------- R6: FDR over the whole family

def test_refuses_fdr_while_a_hypothesis_is_unanalysed(ranked, partition):
    assert prereg(ranked, "H-001").returncode == 0
    assert prereg(ranked, "H-002").returncode == 0
    assert run([*CONF, "H-001"], cwd=ranked,
               env={"PIPELINE_LOCKED_DATA_PATH": str(partition["effect"])}).returncode == 0
    r = run([*CONF, "--fdr"], cwd=ranked)
    assert r.returncode == 1
    assert "H-002" in r.stderr


def test_fdr_covers_the_full_pre_registered_set(ranked, partition):
    assert prereg(ranked, "H-001").returncode == 0
    assert prereg(ranked, "H-002").returncode == 0
    run([*CONF, "H-001"], cwd=ranked, env={"PIPELINE_LOCKED_DATA_PATH": str(partition["effect"])})
    run([*CONF, "H-002"], cwd=ranked, env={"PIPELINE_LOCKED_DATA_PATH": str(partition["null"])})
    assert run([*CONF, "--fdr"], cwd=ranked).returncode == 0
    fdr = json.loads((ranked / "pipeline/analysis/results/fdr_correction.json").read_text())
    assert fdr["n_hypotheses_in_family"] == 2
    assert fdr["significant_at_q05"] == ["H-001"], "the true null must not clear the threshold"
    assert fdr["q_values"]["H-001"] > 0, "a q-value is never exactly zero"


# ---------------------------------------------------------------- R7: replication

def test_refuses_replication_before_test_result(ranked, partition):
    assert prereg(ranked, "H-001").returncode == 0
    r = run([*CONF, "H-001", "--replicate"], cwd=ranked,
            env={"PIPELINE_HOLDOUT_DATA_PATH": str(partition["holdout"])})
    assert r.returncode == 1
    assert "replication requires" in r.stderr


def test_replication_runs_after_test_result(ranked, partition):
    assert prereg(ranked, "H-001").returncode == 0
    assert run([*CONF, "H-001"], cwd=ranked,
               env={"PIPELINE_LOCKED_DATA_PATH": str(partition["effect"])}).returncode == 0
    assert run([*CONF, "H-001", "--replicate"], cwd=ranked,
               env={"PIPELINE_HOLDOUT_DATA_PATH": str(partition["holdout"])}).returncode == 0
    rep = json.loads((ranked / "pipeline/analysis/results/H-001_replication.json").read_text())
    assert rep["run_type"] == "replication"
    assert rep["partition"] == "temporal_holdout"


# ---------------------------------------------------------------- estimates are correct

def test_recovers_the_simulated_hazard_ratio(ranked, partition):
    assert prereg(ranked, "H-001").returncode == 0
    assert run([*CONF, "H-001"], cwd=ranked,
               env={"PIPELINE_LOCKED_DATA_PATH": str(partition["effect"])}).returncode == 0
    res = json.loads((ranked / "pipeline/analysis/results/H-001.json").read_text())["result"]
    assert res["ci_low"] < 1.6 < res["ci_high"], f"true HR 1.6 outside CI: {res}"


def test_true_null_is_reported_as_null(ranked, partition):
    assert prereg(ranked, "H-002").returncode == 0
    assert run([*CONF, "H-002"], cwd=ranked,
               env={"PIPELINE_LOCKED_DATA_PATH": str(partition["null"])}).returncode == 0
    res = json.loads((ranked / "pipeline/analysis/results/H-002.json").read_text())["result"]
    assert res["p_value"] > 0.05
    assert res["ci_low"] < 1.0 < res["ci_high"]
