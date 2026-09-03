# Graveyard

Append an entry here whenever a hypothesis dies, then commit. Entry shape:

```json
{
  "id": "H-007",
  "statement": "...",
  "died_at_stage": "plausibility | feasibility | novelty | confirmatory | replication",
  "reason": "...",
  "recorded_utc": "2026-09-03T00:00:00Z",
  "artifacts": ["pipeline/preregistration/prereg_H-007_v1.md", "pipeline/analysis/results/H-007.json"],
  "result_if_tested": { "hazard_ratio": 1.04, "ci_low": 0.88, "ci_high": 1.23, "p_value": 0.61, "q_value": 0.71 }
}
```

A pre-registered hypothesis that dies here keeps its prereg and its result file. Nothing is
deleted — the point of the graveyard is that the denominator stays visible.
