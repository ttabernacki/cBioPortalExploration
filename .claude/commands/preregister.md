---
description: Pre-register a ranked hypothesis, commit it immutably, and unlock its locked test slice
argument-hint: <hypothesis_id> [--endpoint OS|TTD|TTNT|PFS_RAD] [--dry-run]
allowed-tools: Bash(python3 pipeline/preregister.py:*), Bash(git log:*), Bash(git status:*), Read
---

Pre-register hypothesis `$1` and open the corresponding locked slice.

This is the gate between the outcome-blind generation zone and the outcome-aware test zone.
**Do not hand-write a pre-registration document and do not edit one after it is committed.**
The gate logic is deterministic and lives in `pipeline/preregister.py`; your job is to run it,
read what it says, and stop if it refuses.

## Steps

1. **Show the plan first.** Run with `--dry-run` and show the rendered document to the user:

   ```bash
   python3 pipeline/preregister.py $ARGUMENTS --dry-run
   ```

   If no `--endpoint` was given, ask which endpoint from the registry to pre-register against
   (`OS`, `TTD`, `TTNT`, `PFS_RAD`) rather than guessing. The endpoint choice is the single most
   p-hackable decision in the pipeline and must be made deliberately, before unlock.

2. **Confirm with the user.** State plainly what is about to become irreversible:
   the document commits immutably, and the test slice unlocks. Ask for explicit go-ahead.

3. **Commit and unlock.** On go-ahead, run without `--dry-run`. The script commits the prereg,
   captures the commit SHA, and only then appends the unlock entry to the partition manifest.
   If the commit fails, the partition stays locked — that is intended, not a bug to route around.

4. **Report** the prereg filename, its commit SHA, the endpoint, and the exact next command.

## Refusals you must not work around

The script refuses when the hypothesis has no plausibility or feasibility pass (rule R3), when a
prereg for it already exists (rule R4), or when `confirmatory.py` is missing. **Every one of
these refusals is the pipeline working.** Report the refusal to the user and stop. Do not:

- edit `ranked_hypotheses.json` to add a passing verdict;
- delete or edit an existing prereg to get around immutability;
- append to the manifest's `unlock_log` by hand;
- pick a different endpoint after seeing a refusal, unless the user asks for it.

If the user wants to revise a committed pre-registration, use
`--supersede <file> --reason "<why>"`. Warn them first if the hypothesis already has an unlock
entry: superseding after unlock invalidates the hypothesis and its result becomes exploratory.
