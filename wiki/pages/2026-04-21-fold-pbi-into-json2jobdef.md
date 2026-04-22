---
title: Fold PBI sequence generation into json2jobdef
tags: [decision, json2jobdef, refactor]
sources: [2026-04-21-pbi-sequence-implementation]
updated: 2026-04-21
---

# Decision: Fold PBI sequence generation into `json2jobdef`

**Date:** 2026-04-21
**Type:** ADR
**Status:** Implemented
**Supersedes aspects of:** [[2026-04-21-extend-jobdef-per-index-overrides]]
(which still stands on the tbs-extension side; only the dedicated PBI
utility was removed).

## Context

Initial implementation of PBI sequence generation added a dedicated
`utils/pbi_sequence.py` + `bin/gen_pbi_sequence` entry point. Review
surfaced that the only PBI-specific work the utility did was:
(1) read a local text file, (2) split it into N-line chunks, (3)
write basenames to `inputs.txt`. Everything else was vanilla jobdef
creation that `json2jobdef` already does.

## Decision

Delete the PBI-specific utility. Teach `json2jobdef` to recognize a
new `input_data` value shape:

```json
"input_data": {
    "<local-or-cvmfs-text-file-path>": {"split_lines": N}
}
```

When matched, `_create_inputs_file` delegates to a new helper
`_split_text_file_input(config)` that performs steps (1)–(3) above.
`calculate_merge_factor` recognizes `split_lines` as implying
`merge_factor=1` (one chunk per job). The rest of the pipeline
(template generation, create_jobdef, source-type detection,
PBISequence tbs branch, output registration) flows unchanged.

## Rationale

- **Single entry point.** All jobdef creation goes through
  `json2jobdef`. Users don't need to learn a separate tool for a
  workflow that's otherwise identical.
- **No auto-injection magic.** The previous utility silently injected
  `fcl_overrides` for `surfaceStepTag` and output filename. Moving
  those into the JSON config makes them visible and reviewable.
- **Reusable primitive.** Any future workflow that needs to split a
  text file into chunks for per-job input can use `split_lines`
  directly — no new utility needed.
- **Fewer points of maintenance.** Consistent with the same
  motivation behind the
  [[2026-04-21-extend-jobdef-per-index-overrides]] decision: extend
  shared abstractions rather than fork into workflow-specific
  utilities.

## Implementation

- `utils/json2jobdef.py`:
  - New helper `_split_text_file_input(config)` that splits the named
    text file into `split_lines`-sized chunks in `chunks/`, writes
    basenames to `inputs.txt`.
  - Chunks are named with Mu2e-standard sequencers
    (`<RRRRRR>_<SSSSSSSS>`) so that outputs follow convention when
    combined with `sequencer_from_index`.
  - Helper auto-injects `sequencer_from_index: True` so per-job
    outputs get unique Mu2e-format sequencers (run from chunk
    basename, subrun from job index).
  - `_create_inputs_file` detects the `split_lines` shape at the
    start and delegates before the SAM-based flow runs.
- `utils/prod_utils.py`:
  - `calculate_merge_factor` treats `split_lines` as implying
    `merge_factor = 1`.
- `utils/jobdef.py`:
  - PBISequence `validation_rules`: `events_per_job` moved from
    `required` to `allowed` (not needed at jobdef time since the
    PBISequence branch doesn't populate `source.maxEvents`).
- Deleted: `utils/pbi_sequence.py`, `bin/gen_pbi_sequence`.
- Updated: `data/mdc2025/pbi_sequence.json` uses the new shape;
  `fcl_overrides` now explicit (previously auto-injected).

## Consequences

- **Backward compat:** 160/160 unit tests pass; existing workflows
  unaffected. The `split_lines` shape is new and opt-in.
- **User-visible:** invocation changes from
  `gen_pbi_sequence --json data/mdc2025/pbi_sequence.json --index 0`
  to
  `json2jobdef --json data/mdc2025/pbi_sequence.json --index 0`.
  Same args, different binary.
- **Verified end-to-end:** `json2jobdef` → `jobfcl` → `mu2e` runs 1000
  events, produces `dts.mu2e.PBINormal_33344.MDC2025ai.00.art`, art
  exit status 0.

## Related

- [[pbi-sequence-workflow]] — how to use the refactored path
- [[2026-04-21-extend-jobdef-per-index-overrides]] — the tbs /
  per-index override extension (unchanged by this refactor)
