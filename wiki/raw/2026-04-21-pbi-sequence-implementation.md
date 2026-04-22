# Raw source: PBI sequence implementation in prodtools (conversational)

**Date:** 2026-04-21
**Type:** conversation / decision log
**Context:** porting Mu2e/Production/Scripts/gen_NoPrimaryPBISequence.sh into prodtools

## Ask

"Can we implement the following in prodtools?" — linked to the bash script. Starting corpus: two fixed text files on cvmfs
(`/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/PBI_{Normal,Pathological}_33344.txt`, ~25,438 lines each, frozen since Oct 2021).

## Options considered

1. **Python port, same shape.** 1:1 translation of the bash script.
   Local runs, no grid integration. Cheapest. Doesn't solve the
   "second point of maintenance for mu2e+pushOutput" concern.
2. **JSON-driven local gen + reuse `prod_utils.py` helpers.** Custom
   orchestration, uses `prod_utils.run()` / `push_data()` /
   `push_logs()` to avoid duplicating mu2e invocation and push
   logic. Doesn't fit the jobdef tarball model.
3. **Full jobdef-model integration.** Package each text chunk as one
   job in a standard jobdef tarball; `runmu2e` handles execution
   unchanged. Gap found: prodtools' jobdef mechanism supports
   per-job variation only for `subrunkey` and seeds, not for
   `source.firstEventNumber` (which the bash script makes globally
   unique across chunks via cumulative counting).

## Chosen

**Option 3, after extending the jobdef mechanism** to support
generic per-index linear overrides (not just subrun/seed). Rationale:
"The jobdef model is the right abstraction — PBI should use it. If
prodtools' jobdef mechanism can't express the workflow, fix the
mechanism rather than work around it."

## What got built

Core extension (backward-compatible, tests pass):
- `utils/jobdef.py`: added `PBISequence` to `validation_rules`; added
  a `PBISequence` branch in tbs construction that populates
  `tbs.inputs` + `tbs.event_id` + `tbs.subrunkey`; passes
  `event_id_per_index` from config to tbs.
- `utils/jobfcl.py`: `job_event_settings(index)` now also applies
  per-index linear overrides (`value = offset + index * step`). Works
  for any fcl key, not just `firstEventNumber`.

Utility + config:
- `utils/pbi_sequence.py` — splits the cvmfs PBI text into N-line
  chunks, writes `inputs.txt`, populates config with
  `event_id_per_index: {"source.firstEventNumber": {offset: 0, step:
  events_per_job}}`, calls existing `create_jobdef(...)`.
- `bin/gen_pbi_sequence` — thin bash wrapper.
- `data/mdc2025/pbi_sequence.json` — sample config (Normal +
  Pathological entries).

## Verification

- 160/160 existing unit tests pass — no regression.
- End-to-end run produced:
  - `cnf.mu2e.PBINormal_33344.MDC2025ac.0.tar` (789 B)
  - `inputs.txt` with 26 chunk paths
  - `jobdefs_list.json` entry (njobs=26)
  - `jobpars.json` inside tarball carries the new
    `event_id_per_index` block correctly.

## Not verified

Whether `jobfcl --index N` actually renders the correct
per-chunk `firstEventNumber` in emitted FCL. Code path exists and
exercises standard TBS machinery; will be confirmed when the full
chain runs (local `runmu2e --jobdesc jobdefs_list.json` or production
`/mu2epro-run runmu2e ... --pushout`).

## Invocation (reference)

```bash
gen_pbi_sequence --json data/mdc2025/pbi_sequence.json --index 0
runmu2e --jobdesc jobdefs_list.json                          # local
# or production:
/mu2epro-run runmu2e --jobdesc jobdefs_list.json --pushout
```
