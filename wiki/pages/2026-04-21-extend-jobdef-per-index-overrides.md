---
title: Extend jobdef with per-index linear overrides (event_id_per_index)
tags: [decision, jobdef, jobfcl]
sources: [2026-04-21-pbi-sequence-implementation]
updated: 2026-04-21
---

# Decision: Extend jobdef with `event_id_per_index`

**Date:** 2026-04-21
**Type:** ADR (architecture decision record)
**Status:** Implemented

## Context

Implementing PBI sequence generation (`gen_pbi_sequence`) as a
prodtools utility surfaced a gap: prodtools' jobdef tarball mechanism
supports per-job variation only for `subrunkey` (run subrun, derived
from job index) and seeds (derived as `1 + index`). It does not
support arbitrary per-index overrides of `source.firstEventNumber`
or other event_id fields.

The PBI workflow needs `source.firstEventNumber = index *
events_per_job` so that event numbers are globally unique across all
chunks (the bash script achieves this via cumulative
`nevents += nevents_file`).

## Options considered

1. **Python port, no jobdef integration** — local orchestration,
   duplicates `mu2e -c` + `pushOutput` code paths. Rejected: creates
   a second point of maintenance for execution + SAM push logic.
2. **Dedicated utility reusing `prod_utils` helpers** — uses
   `prod_utils.run()` / `push_data()` / `push_logs()` but orchestrates
   its own per-chunk FCL generation outside the jobdef tarball
   model. Would work, but PBI then diverges from every other prodtools
   workflow's shape.
3. **Extend the jobdef mechanism** (chosen) — add a generic
   `event_id_per_index` field to the tbs schema. Value =
   `offset + index × step` per fcl key. Works for any integer fcl
   key, not just `firstEventNumber`. PBI becomes a normal jobdef
   consumer; `runmu2e` handles execution unchanged.

## Decision

Option 3. Extend `utils/jobdef.py` and `utils/jobfcl.py` with
`event_id_per_index` support. Feature is opt-in (only populated when
config sets it), so existing workflows produce byte-identical tarballs.

## Rationale

- PBI belongs in the jobdef model conceptually — one tarball, N jobs,
  each with bespoke inputs and overrides. Forcing it out of the model
  (option 2) would be working around prodtools' core abstraction.
- The mechanism is general. Any future workflow that needs per-index
  linear overrides gets it for free (e.g. per-chunk offsets in other
  text-driven sources).
- Cost is modest: ~15 lines in jobdef.py + ~10 lines in jobfcl.py, no
  schema bump for existing tarballs.

## Implementation

- `utils/jobdef.py`:
  - Added `PBISequence` to `validation_rules` (requires inputs,
    merge_factor, run_number, events_per_job).
  - Added `PBISequence` branch in tbs construction — sets
    `tbs.inputs = {'source.fileNames': [merge_factor, inputs_list]}`,
    `tbs.event_id = {'source.runNumber': run, 'source.maxEvents':
    events_per_job}`, `tbs.subrunkey = 'source.firstSubRunNumber'`.
  - Config key `event_id_per_index` passes through to
    `tbs.event_id_per_index`.
- `utils/jobfcl.py`:
  - `job_event_settings(index)` now applies per-index linear overrides
    after the base `event_id` and subrun assignment.
  - Schema: `tbs.event_id_per_index = { fcl_key: {offset, step} }`.
  - Evaluation: `result[fcl_key] = offset + index * step`.
  - Applied last so per-index overrides win over fixed event_id
    entries on the same key.

## Consequences

- **Backward compat:** 160/160 existing unit tests pass. No tarballs
  in `data/**/*.json` gain the new field unless explicitly opted in.
- **Perl parity:** the new field is a prodtools-Python extension, not
  in the Perl `mu2ejobdef`. Parity tests don't cover PBISequence
  (it's a new source type), so no regression. Existing source types
  (EmptyEvent, RootInput, SamplingInput) produce identical tarballs.
- **Generalizability:** the mechanism is PBI-motivated but not
  PBI-specific. Future workflows requiring per-index linear values
  (sequencer offsets, batch counts, etc.) can reuse it.

## Related

- [[pbi-sequence-workflow]] — how to use the extension
- Source: [[2026-04-21-pbi-sequence-implementation]] (ingested
  conversation)
