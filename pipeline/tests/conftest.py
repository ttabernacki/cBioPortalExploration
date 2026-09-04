"""Shared fixtures: a throwaway pipeline clone plus synthetic raw tables.

No test touches the real repository, and no test uses real patient data — there is none.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
PIPELINE = REPO / "pipeline"


def run(args, cwd, env=None, **kw):
    import os
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, env=e, **kw)


@pytest.fixture
def repo(tmp_path):
    """An isolated clone of the pipeline with a git repo and no prior state."""
    dst = tmp_path / "work"
    dst.mkdir()
    shutil.copytree(PIPELINE, dst / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__", "tests", ".pytest_cache"))
    for name in ("claim_graph.json", "gap_report.json", "candidate_hypotheses.json",
                 "filtered_hypotheses.json", "feasible_hypotheses.json", "ranked_hypotheses.json"):
        (dst / "pipeline" / "data" / name).unlink(missing_ok=True)
    for p in (dst / "pipeline" / "data").glob("_*_patch.json"):
        p.unlink()
    shutil.rmtree(dst / "pipeline" / "preregistration", ignore_errors=True)
    (dst / "pipeline" / "preregistration").mkdir()
    shutil.rmtree(dst / "pipeline" / "analysis" / "results", ignore_errors=True)
    (dst / "pipeline" / "analysis" / "results").mkdir(parents=True)
    (dst / "pipeline" / "graveyard" / "graveyard.json").write_text(
        json.dumps({"schema_version": "1.0", "description": "test", "entries": []}) + "\n")

    m = json.loads((dst / "pipeline" / "locked" / "test_partition_manifest.json").read_text())
    m["unlock_log"] = []
    for part in m["partitions"].values():
        part["unlocked"] = False
    (dst / "pipeline" / "locked" / "test_partition_manifest.json").write_text(json.dumps(m, indent=2) + "\n")

    for cmd in (["init", "-q", "."], ["config", "user.email", "t@example.com"],
                ["config", "user.name", "Test"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *cmd], cwd=dst, check=True, capture_output=True)
    return dst


def hypothesis(hid, *, plaus="pass", feas="pass", rank=None, status="active"):
    h = {
        "id": hid, "statement": f"Test hypothesis {hid} about an association.",
        "origin": {"gap_type": "contradiction", "supporting_claims": ["C-001"]},
        "exposure": {"description": "gene loss-of-function"},
        "comparator": {"description": "wild-type"},
        "population": {"description": "advanced disease"},
        "proposed_endpoint_concept": "overall survival",
        "proposed_covariates": ["age at index", "sex"],
        "plausibility": {"score": "strong" if plaus == "pass" else "weak",
                         "causal_story": "a chain that could be false",
                         "alternative_explanations": ["confounding by indication"],
                         "confounding_risks": ["prognostic vs predictive"],
                         "verdict": plaus,
                         **({} if plaus == "pass" else {"reject_reason": "no mechanism"})},
        "feasibility": {"verdict": feas, "required_fields": ["hugo_symbol"],
                        **({} if feas == "pass" else {"reject_reason": "insufficient events"})},
        "status": status,
    }
    if rank:
        h["novelty"] = {"composite_score": 4.0 - rank * 0.1, "rank": rank}
    return h


@pytest.fixture
def ranked(repo):
    """A ranked artifact: H-001 and H-002 pass, H-003 underpowered."""
    doc = {"schema_version": "1.0", "stage": "ranked",
           "generated_utc": "2026-09-04T00:00:00Z",
           "source_artifact": "pipeline/data/feasible_hypotheses.json",
           "hypotheses": [hypothesis("H-001", rank=1), hypothesis("H-002", rank=2),
                          hypothesis("H-003", feas="underpowered", status="rejected")]}
    (repo / "pipeline" / "data" / "ranked_hypotheses.json").write_text(json.dumps(doc, indent=2) + "\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "ranked"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def partition(tmp_path):
    """A synthetic analysis partition with a known hazard ratio. NOT patient data."""
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    def make(n, seed, hr):
        r = np.random.default_rng(seed)
        ex = r.binomial(1, 0.3, n)
        age = r.normal(66, 10, n)
        sex = r.binomial(1, 0.5, n)
        lp = np.log(hr) * ex + 0.02 * (age - 66)
        t = r.exponential(np.exp(-lp) * 24)
        c = r.exponential(40, n)
        return pd.DataFrame({"os_days": np.minimum(t, c) * 30, "death_event": (t <= c).astype(int),
                             "exposure": ex, "age at index": age, "sex": sex})

    paths = {}
    for name, (n, seed, hr) in {"effect": (3000, 1, 1.6), "null": (3000, 2, 1.0),
                                "holdout": (1200, 3, 1.6)}.items():
        p = tmp_path / f"{name}.csv"
        make(n, seed, hr).to_csv(p, index=False)
        paths[name] = p
    return paths
