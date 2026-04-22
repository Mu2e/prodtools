---
title: PBI sequence generation workflow
tags: [reference, workflow, primary-generation]
sources: [2026-04-21-pbi-sequence-implementation]
updated: 2026-04-21
---

# PBI sequence generation workflow

How to produce `dts.mu2e.PBI<type>_<docdb>.<dsconf>.art` files in
prodtools. Goes through the standard `json2jobdef` → `runmu2e`
pipeline; no PBI-specific utility.

## Source corpus

Text files on cvmfs:
```
/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/PBI_Normal_33344.txt
/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/PBI_Pathological_33344.txt
```
Each ~25,439 lines. Frozen since Oct 2021. DocDB 33344 is the only
PBI set currently.

## JSON config shape — current default: `chunk_mode` (N jobs, on-the-fly)

The PBI source file on cvmfs is already grid-readable. The canonical
path is `chunk_lines` (see [[input-data-chunk-mode]]) — each grid
worker extracts its own slice from cvmfs at job start. No
pre-splitting, no staging, N-way parallelism for downstream mixing
fan-out.

```json
{
  "desc": "PBINormal_33344",
  "dsconf": "MDC2025ai",
  "fcl": "Production/JobConfig/primary/NoPrimaryPBISequence.fcl",
  "input_data": {
    "/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/PBI_Normal_33344.txt": {
      "chunk_lines": 1000
    }
  },
  "run": 1430,
  "owner": "mu2e",
  "inloc": "none",
  "outloc": {"*.art": "tape"},
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ai/setup.sh",
  "fcl_overrides": {
    "physics.producers.compressDetStepMCs.surfaceStepTag": "FindMCPrimary",
    "outputs.PrimaryOutput.fileName": "dts.owner.PBINormal_33344.version.sequencer.art"
  }
}
```

Produces 26 jobs per entry (25,438 lines / 1000 per chunk). Each job
processes 1000 events from its own slice of the source file.

**Submit-time effect:**
- `njobs: 26` in the jobdefs_list entry
- `tbs.chunk_mode = {source, lines, local_filename: "chunk.txt"}` in
  jobpars
- `fcl_overrides["source.fileNames"] = ["chunk.txt"]` auto-injected
  so every job's FCL references the local slice
- No `inputs.txt`, no `tbs.inputs`

**Grid-time effect per job:** `runmu2e` sees `tbs.chunk_mode`, runs
`sed -n "start,end p" <cvmfs-source> > chunk.txt`, FCL points at
`chunk.txt`, mu2e reads the slice.

### Alternative: `dir:<path>` inloc (one job, no chunking)

If you want a single job reading the entire file (~50s wall clock),
see [[input-data-dir-shape]]. Less parallelism, but simpler tbs shape.

### Alternative: `split_lines` (pre-split at submit)

### Alternative: `split_lines` (many jobs, pre-split at submit, chunks on stash)

If you need a SAM dataset of many PBI art files (e.g. for mixing
parallelism over the dataset), use the `split_lines` shape instead:

```json
"input_data": {
  "/cvmfs/.../PBI_Normal_33344.txt": {"split_lines": 1000}
}
```

This splits the source file into `chunks/` locally and creates N
jobs, each consuming one chunk. Chunks are local-only — for grid
execution they must be staged to stash or resilient dCache via
`copy_to_stash`. Use when downstream mixing fan-out matters more than
implementation simplicity.

## Invocation (`dir:` path)

### Generate the jobdef tarball

```bash
json2jobdef --json data/mdc2025/pbi_sequence.json --index 0
```

Produces:
- `cnf.<owner>.<desc>.<dsconf>.0.tar` — jobdef tarball with
  `source.fileNames = [PBI_<type>_<docdb>.txt]` (basename) and
  `inloc: "dir:/cvmfs/.../PBI/"` in the jobdefs entry
- `jobdefs_list.json` — entry ready for `runmu2e`

### Local test

```bash
jobfcl --jobdef cnf.mu2e.PBINormal_33344.MDC2025ai.0.tar --index 0 \
       --default-location dir:/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/ > test.fcl
mu2e -c test.fcl
# → dts.mu2e.PBINormal_33344.MDC2025ai.001430_00000000.art (~2.5 MB)
```

### Production push

```bash
/mu2epro-run MDC2025ai json2jobdef \
    --json data/mdc2025/pbi_sequence.json \
    --index 0 --pushout
```

Pushed tarball lands at
`/pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/PBINormal_33344/MDC2025ai/tar/...`
and is SAM-declared as
`cnf.mu2e.PBINormal_33344.MDC2025ai.0.tar`.

### Run the jobs

```bash
# Local testing
runmu2e --jobdesc jobdefs_list.json --nevts -1

# Production (SAM registration + dCache upload)
/mu2epro-run runmu2e --jobdesc jobdefs_list.json --pushout
```

## Job count

`N = ceil(lines / events_per_job)`. For the two canonical inputs:

| PBI type | Lines | events_per_job=1000 | 2000 | 5000 |
|---|---|---|---|---|
| Normal | 25,438 | 26 | 13 | 6 |
| Pathological | 25,439 | 26 | 13 | 6 |

Wall clock is seconds per job (reading a ~15KB text chunk, emitting
PBI objects into art). 26 jobs runs in minutes locally.

## How it works under the hood

1. `json2jobdef` reads the JSON config; detects `input_data` value
   shape `{<path>: {split_lines: N}}` and routes through
   `_split_text_file_input`.
2. Splits the source file into N-line chunks, writes them to
   `chunks/` under cwd, writes basenames to `inputs.txt`.
3. Continues through the standard `merge` job_type path with
   `--inputs inputs.txt --merge-factor 1`.
4. `create_jobdef` detects `source.module_type: PBISequence` in the
   FCL, applies the `PBISequence` validation + tbs construction
   branch: sets `tbs.inputs` (fileNames list), `tbs.event_id`
   (runNumber only), `tbs.subrunkey = ""` (no per-job subrun — rejected
   by PBISequence's pset validator).
5. At job time, `jobfcl --index N` picks `fileNames[N]` from the list
   and emits FCL with the chunk's basename. Runtime resolves the
   basename via `--default-location dir:<chunks-dir>/`.

## Caveats

- **Chunk files are written locally.** If you run the jobs on the
  grid, the chunk text files need to be accessible from the grid node
  (stash or resilient dCache). For local-only runs this is fine.
- **Subrun is the same across chunks.** Output uniqueness comes from
  the input chunk basename's sequencer slot (`.00`, `.01`, ...), not
  from per-job subruns. PBISequence's pset validator rejects
  `source.firstSubRunNumber` (see Gotchas).

## Gotchas discovered 2026-04-21

Running the workflow end-to-end surfaced several traps. Recording them
so future sessions don't re-hit them.

### inputs.txt must hold BASENAMES, not absolute paths

`jobfcl --default-location dir:<path>` *prepends* the dir onto every
entry in `source.fileNames`. If `inputs.txt` contains absolute paths,
you get doubled paths (`//tmp/X//tmp/X/foo.txt`). Current
`pbi_sequence.py` writes basenames; runtime resolves with
`--default-location dir:<chunks-dir>/`.

### jobfcl resolves local files via `--default-location dir:<path>`

Default jobfcl behavior treats `source.fileNames` entries as SAM-known
dataset files, which 404s for our chunks (they're not in SAM). Use
`dir:<path>` to route through the local filesystem.

### PBISequence pset validator rejects common source parameters

The PBISequence C++ module accepts only: `fileNames`, `runNumber`,
`reconstitutedModuleLabel`, `integratedSummary`, `verbosity`,
`module_type`. Passing `source.maxEvents`, `source.firstSubRunNumber`,
or `source.firstEventNumber` results in "Unsupported parameters"
errors. The legacy bash script
(`Mu2e/Production/Scripts/gen_NoPrimaryPBISequence.sh`) sets all three
— it is almost certainly broken on current Offline as well.

The prodtools PBI branch in `utils/jobdef.py` was updated to set only
`source.runNumber` and explicit empty `subrunkey`. The
`event_id_per_index` extension (generic mechanism for
`offset + index × step` values) was left in place but is NOT used
for PBI — it remains available for any future workflow that needs
per-index linear overrides on keys the target module actually
accepts.

### `mu2e -n <N>` injects maxEvents, which PBISequence rejects

Passing `-n` on the `mu2e` command line causes art to inject
`source.maxEvents`, which PBISequence rejects. Workarounds:
- Run without `-n` (PBISequence consumes one event per input line, so
  `N` is implicitly the chunk size).
- Set maxEvents via a different code path (not currently supported).

### NoPrimary.fcl is out of sync with current CompressDetStepMCs

The `NoPrimary.fcl` in `MDC2025ac` Musings lacks
`surfaceStepTag: "FindMCPrimary"`, which current `CompressDetStepMCs`
requires. Fix (applied automatically by `pbi_sequence.py`):

```
"fcl_overrides": {
  "physics.producers.compressDetStepMCs.surfaceStepTag": "FindMCPrimary"
}
```

### Output filename desc is hardcoded in NoPrimary.fcl

`NoPrimary.fcl` sets `outputs.PrimaryOutput.fileName:
"dts.owner.NoPrimary.version.sequencer.art"` with `NoPrimary` as a
literal — so without override, PBI outputs would be named
`dts.mu2e.NoPrimary.MDC2025ac.<seq>.art`. Fix (applied
automatically by `pbi_sequence.py`):

```
"fcl_overrides": {
  "outputs.PrimaryOutput.fileName":
    "dts.owner.<config-desc>.version.sequencer.art"
}
```

### Use a recent enough campaign — MDC2025ac is stale

Initial test against `MDC2025ac` hit two Offline-side blockers:
1. `NoPrimary.fcl` missing `surfaceStepTag` — worked around by
   fcl_override.
2. `CompressDetStepMCs` failing on event 1 with
   `ProductNotFound: std::vector<mu2e::SurfaceStep>` — not
   workaroundable from prodtools.

**Resolution:** use `MDC2025ai` (or newer). Its `NoPrimary.fcl` adds
`surfaceStepTag: "FindMCPrimary"` natively AND adds `genCounter`
producer to `physics.PrimaryPath`, which together resolve both
issues. End-to-end test with `MDC2025ai` on 2026-04-21:

```
TrigReport    1000    1000    1000       0    0   FindMCPrimary
TrigReport    1000    1000    1000       0    0   compressDetStepMCs
TrigReport    1000    1000    1000       0    0   PrimaryOutput
Art has completed and will exit with status 0.
```

Output: `dts.mu2e.PBINormal_33344.MDC2025ai.00.art` (~202 KB for 1000
events).

**Takeaway:** if a downstream FCL chain fails with
`ProductNotFound` / pset validator errors and the campaign dsconf is
more than a few months old, check whether a newer Musings (higher
letter suffix on MDC20XX) has the fix before working around it
locally.

## Open questions

- If PBI job event numbering needs to be globally unique across
  chunks (currently every chunk has events starting at the same
  internal number), we could offset `source.runNumber` per-chunk via
  `event_id_per_index` — the mechanism is in place, PBISequence
  accepts `runNumber`. Not currently needed — the output files have
  unique sequencers from input-chunk basenames, so collisions would
  only matter if someone concatenated the art files into one stream.

## Status as of 2026-04-21

Full production chain proven end-to-end with `MDC2025ai` via the
`dir:` inloc shape:

1. **`json2jobdef --json data/mdc2025/pbi_sequence.json --index 0 --pushout`**
   (as mu2epro) → tarball registered in SAM at
   `/pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/PBINormal_33344/MDC2025ai/tar/...`
   as `cnf.mu2e.PBINormal_33344.MDC2025ai.0.tar`.
2. **`runmu2e --jobdesc jobdefs_list.json --dry-run`** (as mu2epro)
   → pulls tarball from SAM via `mdh copy-file`, generates per-job
   FCL with correct cvmfs path, runs `mu2e -c`, produces
   `dts.mu2e.PBINormal_33344.MDC2025ai.001430_00000000.art`
   (~2.5 MB, 25,438 events).

### Production push via POMS map (`--prod`)

For full production dispatch, push tarballs AND create the SAM index
definition that POMS discovers. The POMS map file is
`MDC2025-NNN.json` under
`/exp/mu2e/app/users/mu2epro/production_manager/poms_map/` — each N
is a batch number, increment by one for a fresh batch.

```bash
/mu2epro-run MDC2025ai json2jobdef \
    --json data/mdc2025/pbi_sequence.json \
    --dsconf MDC2025ai \
    --prod \
    --jobdefs /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-025.json
```

`--dsconf MDC2025ai` matches both entries (Normal + Pathological) in
the config. `--prod` = `--pushout` + `mkidxdef --prod`:
- `pushOutput` copies each tarball to
  `/pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/<desc>/<dsconf>/tar/...`
  and registers in SAM. Already-existing v.0 tarballs are tolerated
  (no error) — the re-push is a no-op for unchanged content. If the
  tarball content actually differs, retire v.0 first or use
  `--extend` to bump to v.1.
- `mkidxdef --prod` creates the SAM index definition
  `iMDC2025-NNN` from the new map file — this is what POMS scans to
  discover new jobs.

Verified 2026-04-21: produced map `MDC2025-025.json` with 2 entries,
both tarballs landed in dCache, `iMDC2025-025` declared in SAM.

### Running via runmu2e + SAM pull

Once the tarball is in SAM, `runmu2e` consumes a `jobdefs_list.json`
that references it by name. The jobdefs entry for PBI:

```json
[
  {
    "tarball": "cnf.mu2e.PBINormal_33344.MDC2025ai.0.tar",
    "inloc": "dir:/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/",
    "outputs": [{"dataset": "*.art", "location": "tape"}],
    "njobs": 1
  }
]
```

Invocation:

```bash
export fname=etc.mu2e.index.000.0000000.txt
runmu2e --jobdesc jobdefs_list.json --dry-run     # no -n → safe
# or, for real production with SAM registration:
runmu2e --jobdesc jobdefs_list.json               # --pushout happens inside runmu2e
```

**Critical: do NOT pass `--nevts <N>`.** The default `--nevts -1`
tells runmu2e to skip the `-n` flag when invoking mu2e. Passing a
positive `--nevts` causes mu2e to inject `source.maxEvents`, which
PBISequence's pset validator rejects (see Gotchas above).

**Harness caveat for local testing:** if you run `runmu2e` via
`/mu2epro-run <version> runmu2e ...`, the skill pre-sources
`muse setup SimJob <version>`, which conflicts with runmu2e's
internal `source <simjob_setup>` — "Muse already setup" error. On a
real grid node this can't happen (env starts clean). For local test
through ksu, run runmu2e in a clean-env bash invocation
(`muse setup ops` + `setup OfflineOps` only; no `muse setup SimJob`).

**Architecture note:** an earlier version of this workflow had a
dedicated `utils/pbi_sequence.py` + `bin/gen_pbi_sequence` utility.
That was refactored into `json2jobdef` on 2026-04-21 via the
`split_lines` input_data shape — see
[[2026-04-21-fold-pbi-into-json2jobdef]].

## Notes for future change

- `event_id_per_index` extension available but not needed for PBI
  itself (PBISequence rejects firstEventNumber); ready for any future
  workflow that needs per-index linear overrides on accepted keys.
- v.0 in SAM was initially pushed with the now-removed `literal` inloc
  form, then retired by the production team and re-pushed with the
  current `dir:` form. If you need to replace v.0 again, use
  `json2jobdef --extend` to auto-increment to v.1.

## Related

- [[2026-04-21-extend-jobdef-per-index-overrides]] — the jobdef/jobfcl
  mechanism change that made this possible
- Source: [[2026-04-21-pbi-sequence-implementation]]
