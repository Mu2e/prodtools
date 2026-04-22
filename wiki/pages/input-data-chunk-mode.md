---
title: On-the-fly chunking via chunk_lines (chunk_mode)
tags: [reference, json2jobdef, runmu2e, jobdef]
sources: [2026-04-21-pbi-sequence-implementation]
updated: 2026-04-22
---

# On-the-fly chunking at grid (`chunk_mode`)

Use when a single large text file on cvmfs needs to be processed in
parallel by N grid jobs, each consuming a contiguous slice — without
pre-splitting at submit time, without staging chunks into stash or
resilient dCache. Each grid worker materializes its own slice from the
cvmfs source when the job starts.

Complements the other two shapes for cvmfs-resident inputs:

|              | `dir:<path>` (single file → 1 job)          | `split_lines` (pre-split at submit)      | `chunk_mode` (on-the-fly at grid) |
|---|---|---|---|
| Pre-processing | none                                       | split + stage chunks to stash/resilient  | none                              |
| njobs        | 1 per input file                            | `ceil(lines/split_lines)`                 | `ceil(lines/chunk_lines)`         |
| Fan-out for downstream mixing | poor (1 art per file) | good (N arts per source file)             | good (N arts per source file)     |
| Storage cost | 0 (cvmfs only)                              | N chunk files on stash/resilient          | 0 (cvmfs only; slices are ephemeral on grid workers) |

`chunk_mode` is the sweet spot when the source is already on cvmfs and
you want the N-way downstream fan-out without the chunk-staging
pipeline.

## JSON shape

```json
"input_data": {
    "/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/PBI_Normal_33344.txt": {
        "chunk_lines": 1000
    }
}
```

- Single absolute path as key; value is a dict with `chunk_lines: N`
  where `N >= 1`. Invalid values raise at `json2jobdef` time.
- For `PBISequence` source type specifically, the config must provide
  either `inputs` + `merge_factor` (dir:-mode) or `chunk_mode` (this
  path) — neither set raises at jobdef-creation time instead of
  surfacing as a cryptic `fileNames: @nil` failure inside mu2e.
- `json2jobdef` at submit time:
  - counts lines in the source file, computes `njobs = ceil(lines/chunk_lines)`
  - sets `config['chunk_mode'] = {source, lines, local_filename}` which flows into
    `jobpars.json` → `tbs.chunk_mode`
  - auto-injects `fcl_overrides["source.fileNames"] = ["chunk.txt"]` so every
    job's FCL references the same local filename — contents differ per job
    because each grid worker writes its own slice to that path
  - does not create `inputs.txt`, `tbs.inputs` is unset

## Grid runtime (runmu2e)

On each grid worker, `utils/prod_utils.process_jobdef`:

1. pulls the tarball (`mdh copy-file`)
2. reads `tbs.chunk_mode` from jobpars
3. computes `start = job_index_num * lines + 1`, `end = start + lines - 1`
4. runs `sed -n "${start},${end}p" <source> > <local_filename>` — populates
   the local chunk file with this job's slice
5. invokes `jobfcl` → FCL points at local_filename, already set via
   `fcl_overrides`
6. runs `mu2e -c <fcl>` — mu2e reads the local chunk, produces its output

Each job's output inherits the Mu2e-standard sequencer
(`<run>_<index>` zero-padded) via `source.runNumber` in `event_id` and
the existing `sequencer` short-circuit in `jobfcl`.

## Verification (2026-04-22)

End-to-end on PBI Normal:

```
fname=etc.mu2e.index.000.0000005.txt       (job index 5)
Global job index: 5, Local job index within definition: 5
chunk_mode: extracting lines 5001-6000 of /cvmfs/.../PBI_Normal_33344.txt -> chunk.txt
outputs.PrimaryOutput.fileName: "dts.mu2e.PBINormal_33344.MDC2025ai.001430_00000005.art"
TrigReport Events total = 1000 passed = 1000 failed = 0
Art has completed and will exit with status 0
```

Chunk contents confirmed slice-correct: first line of chunk.txt is
`85599067.99` (value at line 5001 of source), not `0` (which is line 1).

## fname-index gotcha

The grid convention for the index filename is
`etc.mu2e.index.000.<NNNNNNN>.txt` — **field [4] (the 7-digit
zero-padded sub-counter) is the job index**, not field [3] (the
3-digit `000`). Production `mkidxdef` always emits field [3] as
`000`. For local testing with a specific index, set
`fname=etc.mu2e.index.000.0000<NNN>.txt`.

## Related

- [[pbi-sequence-workflow]] — canonical consumer
- [[input-data-dir-shape]] — one-job-per-file alternative (no
  parallelism)
- `split_lines` (documented inline in pbi-sequence-workflow) —
  pre-split + stage alternative
