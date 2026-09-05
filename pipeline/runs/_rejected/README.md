# Rejected artifacts

## claim_graph_esr1_MALFORMED.json

First literature-mapper attempt on the ESR1 topic. **Rejected: does not conform to
`pipeline/schemas/claim_graph.schema.json`.** The run terminated on a session rate limit, but the
output is not truncated — it is written in a schema the agent invented:

- claims carry `source`, `verified`, `supporting_quote` at the top level rather than in an
  `evidence` block;
- `subject`/`object` are bare entity-ID references rather than typed objects;
- `predicate` is free text (32 distinct values such as `under-detects`,
  `yields_low_eligible_fraction_in`) against the schema's 7-value effect enum;
- `context` carries none of the schema's required `disease` field.

**Why it was not remapped.** The sourcing is real — 44 of 45 claims carry a verbatim quote and a
correct PMID, including the genuine foundational papers (Toy 24185512, Robinson). Only the
serialisation is wrong. But converting 32 free-text predicates into a 7-value directional enum
requires deciding what effect direction each claim asserts, and that is a semantic judgement the
mapper was supposed to derive from reading the source. Making it here — silently, from string
patterns — would substitute the main thread's guess for the agent's reading, which is precisely
the substitution the pipeline exists to prevent. Kept for reference; superseded by a re-run.
