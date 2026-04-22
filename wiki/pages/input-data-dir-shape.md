---
title: dir:<path> inloc mode for cvmfs-resident inputs
tags: [reference, json2jobdef, jobfcl]
sources: [2026-04-21-pbi-sequence-implementation]
updated: 2026-04-21
---

# Using `inloc: dir:<path>` for cvmfs-resident inputs

Use when a primary input file lives at a grid-readable path that is
**not** a SAM dataset (e.g. under `/cvmfs/...`). The config points at
the containing directory via `inloc`; input_data keys are filenames
relative to that directory.

## JSON shape

```json
"input_data": {
    "PBI_Normal_33344.txt": 1
},
"inloc": "dir:/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/"
```

- `input_data` keys are **basenames**, not absolute paths.
- The value `1` is the merge factor (one file per job).
- `inloc` must start with `dir:` — the prefix tells `jobfcl` to resolve
  basenames against the given directory at runtime.
- `json2jobdef` detects `inloc.startswith('dir:')` and writes the keys
  to `inputs.txt` verbatim (no SAM lookup).

## Output parent tracking

For jobs consuming non-SAM inputs (cvmfs files under `dir:<path>`),
`push_data` writes `none` in the third column of `output.txt`
instead of pointing at a `parents_list.txt` file. `printJson --parents`
can only resolve SAM-registered parents — if it gets a cvmfs path it
exits 25, which cascades to `KeyError: 'checksum'` inside
`pushOutput.copyFile` (metadata dict never populated).

Implementation:

- `prod_utils.process_jobdef` returns `inloc` as a 5th tuple element.
- `prod_utils.push_data(..., track_parents: bool)` — when False, writes
  `none` in `output.txt` and skips `parents_list.txt` entirely. `push_data`
  itself is policy-free: the caller decides whether parents are
  trackable.
- `runmu2e` computes `track_parents = not inloc.startswith('dir:')` and
  passes it to `push_data`. Other modes (`template`, `direct_input`)
  default to `track_parents=True`.

Verified by fixing a grid-job failure on 2026-04-22 where PBI outputs
failed to register in SAM with the `KeyError: 'checksum'` cascade.

## Runtime path resolution

`runmu2e` passes the jobdefs entry's `inloc` string to `jobfcl`. When
`jobfcl._locate_file(filename)` sees `self.inloc.startswith('dir:')`,
it returns `"<dir>/<filename>"` — no SAM query, no protocol rewriting.

`runmu2e` uses the **`file` protocol** (direct POSIX read) when inloc
is `dir:<path>`, rather than its default `root` (xroot). The xroot
path-rewriting logic in jobfcl only handles `/pnfs/...` paths, so
forcing `root` on a cvmfs path raises
`ValueError: root protocol requested but a file pathname does not start with /pnfs`.
Fix is in `prod_utils.process_jobdef` — protocol is chosen based on
`inloc.startswith('dir:')`.

For local testing:

```bash
jobfcl --jobdef <tar> --index 0 \
       --default-location dir:/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/
```

## When to use

- The source file is on `/cvmfs/...`, which is universally mounted on
  grid nodes — no staging needed.
- You don't need SAM registration for the input (it's reference data).
- All input files share a common parent directory.

## When NOT to use

- Input is already a SAM dataset — use the normal merge / resampler
  shapes.
- You need N output files from one source for downstream parallelism —
  use the `split_lines` shape (generates N chunks + N jobs from one
  source file).
- Input files live in *different* directories — `dir:` takes a single
  prefix. If multi-dir becomes a real need, we'd revisit (a `literal`
  mode was tried on 2026-04-21 and removed as unused; see log entry).

## Related mechanism: sequencer derivation for non-Mu2e-named inputs

Basenames like `PBI_Normal_33344.txt` don't parse as standard Mu2e
filenames (`<tier>.<owner>.<desc>.<dsconf>.<seq>.<ext>`), so
`jobfcl.sequencer()` falls back to the run-number short-circuit:
returns `f"{run:06d}_{index:08d}"` whenever `event_id` contains a run
key. Recognized keys:

- `source.firstRun` — EmptyEvent / RootInput
- `source.run` — SamplingInput
- `source.runNumber` — PBISequence

When using `dir:` with a non-Mu2e-named input, set
`run` in the config (producing `source.runNumber` for PBISequence),
and use a templated `outputs.PrimaryOutput.fileName` like
`dts.owner.<desc>.version.sequencer.art` — jobfcl resolves the
placeholders per-job.

## Related
- [[pbi-sequence-workflow]] — primary consumer
- [[input-data-chunk-mode]] — on-the-fly chunking alternative when
  a single large text file should fan out to N parallel jobs
- [[2026-04-21-fold-pbi-into-json2jobdef]] — the split_lines complement
