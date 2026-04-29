---
title: PBI sequence generation workflow
tags: [reference, workflow, primary-generation, mixing, reco]
sources: [2026-04-21-pbi-sequence-implementation]
updated: 2026-04-25
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
   (runNumber only), `tbs.subrunkey = ""` (no per-job subrun by default;
   per-index overrides via `event_id_per_index` are supported on
   MDC2025aj+, see Gotchas → "Update 2026-04-22").
5. At job time, `jobfcl --index N` picks `fileNames[N]` from the list
   and emits FCL with the chunk's basename. Runtime resolves the
   basename via `--default-location dir:<chunks-dir>/`.

## Caveats

- **Chunk files are written locally.** If you run the jobs on the
  grid, the chunk text files need to be accessible from the grid node
  (stash or resilient dCache). For local-only runs this is fine.
- **Subrun is the same across chunks** (pre-MDC2025aj). Output uniqueness
  comes from the input chunk basename's sequencer slot (`.00`, `.01`, ...),
  not from per-job subruns. PBISequence's pset validator rejected
  `source.firstSubRunNumber` up to MDC2025ai; MDC2025aj accepts it (see
  Gotchas → "Update 2026-04-22").

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

### PBISequence pset validator rejects common source parameters (pre-MDC2025aj)

**Applies to MDC2025ai and earlier.** Superseded in part by MDC2025aj;
see "Update 2026-04-22" below.

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

#### Update 2026-04-22: MDC2025aj accepts firstSubRun/firstEvent

Offline PR #1799 + Production PR #533 (both merged 2026-04-15) added
`firstSubRunNumber` and `firstEventNumber` as optional `fhicl::Atom<unsigned>`
entries in `PBISequence_source.cc` with default 0. The MDC2025aj SimJob
backing (published 2026-04-22) ships this schema, so PBI jobs built
against `MDC2025aj/setup.sh` accept `event_id_per_index` overrides for
those keys. `source.maxEvents` is still rejected. `data/mdc2025/pbi_sequence.json`
uses this to assign globally-unique event numbers across indices
(verified: index 0 → 0, index 7 → 7000 at step=1000).

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

### MDC2025aj mu2e-trig-config schema drift (2026-04-23)

MDC2025aj Musings ships a `mu2e-trig-config` package whose
`core/filters/trigCalFilters.fcl` uses an older `FilterEcalNNTrigger`
schema than the `backing/Offline/v13_08_00` C++ module expects.
Concretely, `trigCalFilters.fcl:56` sets:

- `caloBkgMVA` (+ `caloBkgMVA.MVAWeights`)

while the C++ `FilterEcalNNTrigger` Config now requires:

- `caloMVACollection: art::InputTag`
- `minRtoTest`, `minTtoTest`, `maxEtoTest`, `maxRtoTest`, `maxTtoTest: float`

Symptom: running any mix FCL on MDC2025aj that exercises the trigger
chain (all of `Production/JobConfig/mixing/Mix.fcl`'s flavors) aborts at
ModuleConstruction with:

```
Module label: CaloMVANNCEFilter
module_type : FilterEcalNNTrigger
  Missing parameters:  caloMVACollection, minRtoTest, …
  Unsupported params:  caloBkgMVA, caloBkgMVA.MVAWeights
```

**Not a prodtools issue.** Fix lives upstream — `mu2e-trig-config`
needs a refresh in the next Musings cut. Until then, mix jobs on aj
cannot run end-to-end in production.

**Validated workaround for local sanity checks (2026-04-23):** source
`muse setup SimJob MDC2025ai` instead of aj and run the same aj-built
FCL. The #include paths resolve against ai's `mu2e-trig-config` (which
matches its own Offline v13_07_00 cleanly), RootInput reads the
aj-tagged dts file transparently, and `mu2e -c ... -n 1` completes
with exit 0 and a full TrigReport. Good enough to confirm overlay
wiring; do not use for production (wrong Offline version).

Hit on 2026-04-23 while validating the stage-2 PBI mix overlay; jobdef
generation + fcldump overlay check were both clean, only the
`mu2e -c -n 1` schema validation tripped on it under aj env.

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

## Stage 2: mixing PBI into dig

The stage-1 outputs (`dts.mu2e.PBI<type>_<docdb>.<dsconf>.art`) contain
`ProtonBunchIntensity` products and nothing else — no primary particles,
no detector steps. They are consumed as *input* to a Mix.fcl variant
that pulls PBI from the file instead of generating it inline.

**Hook fcl:** `Production/JobConfig/mixing/NoPrimaryPBISequence.fcl`.
It includes `mixing/NoPrimary.fcl` (the standard mix-no-primary path)
and overrides `physics.producers.PBISim` with `NullProducer`, so the
per-event PBI values come from the source file's reconstituted
`ProtonBunchIntensity` stream rather than being regenerated.

### mix.json entry

**Cross-version configuration (2026-04-23):** inputs sourced from the
aj stage-1 production (`dts.mu2e.PBINormal_33344.MDC2025aj.art` — in
SAM with 26 files), but the mix step itself runs on MDC2025ai to
sidestep the aj trig-config drift (see Gotchas). PBI values in the
input files are just numbers — reading them under ai's Offline
v13_07_00 is transparent.

```json
{
  "input_data": [
    {"dts.mu2e.PBINormal_33344.MDC2025aj.art": 1},
    {"dts.mu2e.PBIPathological_33344.MDC2025aj.art": 1}
  ],
  "dsconf": ["MDC2025ai_best_v1_3"],
  "simjob_setup": ["/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ai/setup.sh"],
  "fcl": ["Production/JobConfig/mixing/Mix.fcl"],
  "fcl_overrides": [{
    "services.DbService.purpose": "Sim_best",
    "services.DbService.version": "v1_3",
    "#include": "Production/JobConfig/mixing/NoPrimaryPBISequence.fcl",
    "outputs.Output.fileName": "dig.mu2e.{desc}.{dsconf}.sequence.art"
  }]
}
```

Same overlay pattern as the existing NoPrimary mix entries: `Mix.fcl`
as the base, the NoPrimary-flavored variant injected through
`fcl_overrides["#include"]`. Pileup datasets, `pbeam`, `merge_events`
match the sibling NoPrimary entries.

Revert `dsconf`/`simjob_setup` to `MDC2025aj` once the `mu2e-trig-config`
package refreshes upstream and a post-fix aj Musings cut ships.

### Output tier

The mix job emits **dig-tier** directly (`dig.mu2e.*.art`), not dts —
matching the existing NoPrimary mix entries in `data/mdc2025/mix.json`.
No separate `digi.json` step is required for this chain; the Mix +
NoPrimary overlay wires in digitization.

### dsconf / DB version

`MDC2025aj_best_v1_3` was chosen by inheriting ag's most recent
version (`MDC2025ag_best_v1_3`) on the assumption calibrations carry
over to aj unchanged. **This requires `Sim_best / v1_3` to be
registered in DbService for aj** — jobs will fail at conditions lookup
if the version hasn't been cut. Verify before a production push.

### `mixconf`

Running counter across mix.json entries (0, 1, 2 for existing;
3 for the new PBI entry). Likely a random-seed / pileup-offset
discriminator ensuring independence across configs.

### Validation status (2026-04-23)

- Jobdefs build cleanly: `json2jobdef --json data/mdc2025/mix.json
  --dsconf MDC2025aj_best_v1_3` produces 2 tarballs × 26 jobs each
  (`cnf.mu2e.PBI{Normal,Pathological}_33344Mix1BB.MDC2025aj_best_v1_3.0.tar`).
- `fcldump --local-jobdef ... --index 0` confirms the overlay is wired
  in the right order: `Mix.fcl` → `OneBB.fcl` → DB overrides →
  `#include "Production/JobConfig/mixing/NoPrimaryPBISequence.fcl"`.
  Source points at `dts.mu2e.PBINormal_33344.MDC2025aj.art` on tape;
  pileup aux inputs resolve with correct multiplicities.
- **End-to-end `mu2e -c ... -n 1` on aj env** is blocked by an
  unrelated upstream bug — see Gotchas → "MDC2025aj mu2e-trig-config
  schema drift (2026-04-23)".
- **End-to-end validation on MDC2025ai env passes** (2026-04-23, exit 0,
  full TrigReport). The same aj-built FCL run under `muse setup SimJob
  MDC2025ai` completes mixing + trigger + digitization on 1 event.
  RootInput reads the aj-tagged dts file transparently; the ai Offline
  v13_07_00 doesn't exercise aj's PBISequence source schema, so the
  cross-version mix validates cleanly.
- **mix.json entry switched to ai** (2026-04-23): pending the aj
  trig-config fix, the production mix entry is
  `MDC2025ai_best_v1_3` + `simjob_setup: MDC2025ai/setup.sh` with
  aj inputs. `mu2e -c ... -n 1` on the resulting fcl produces
  `dig.mu2e.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.001430_00000001.art`
  (1.57 MB, 1 event).
- **Production push completed (2026-04-24 UTC).** Via
  `/mu2epro-run MDC2025ai json2jobdef --json data/mdc2025/mix.json
  --dsconf MDC2025ai_best_v1_3 --prod --jobdefs
  poms_map/MDC2025-025.json`. Both tarballs are SAM-declared:
  - `cnf.mu2e.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.0.tar` at
    `/pnfs/.../phy-etc/cnf/mu2e/PBINormal_33344Mix1BB/MDC2025ai_best_v1_3/tar/…`
  - `cnf.mu2e.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.0.tar` at
    `/pnfs/.../phy-etc/cnf/mu2e/PBIPathological_33344Mix1BB/MDC2025ai_best_v1_3/tar/09/e5/…`

  POMS map `MDC2025-025.json` extended in place (aj stage-1 × 52 + ai
  mix × 52 = 104 jobs). SAM index `iMDC2025-025` deleted and recreated
  with all 4 jobdef tarballs; POMS will pick up the 52 new mix jobs on
  its next scan.

- **POMS grid run completed (2026-04-24, ~1-hour turnaround).** Verified
  via metacat MCP query on 2026-04-24:
  - Tarballs declared 05:00 UTC → `dig.*` output datasets declared
    06:00 UTC (first file 05:12 UTC, last by 05:30 UTC for one variant).
  - `dig.mu2e.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.art`: **26 files**,
    sizes ranging 0.87–1.92 GB per subrun (size tracks PBI intensity per
    chunk — higher PBI = more detector activity = larger output).
  - `dig.mu2e.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.art`: **26
    files** (same structure).
  - Sibling `log.*` datasets (26 files each) also registered.
  All 52 mix jobs succeeded on the first POMS dispatch.

- **`event_id_per_index` verified end-to-end in production.** Sample
  `dig` file `...001430_00000021.art` from the PBINormal dataset has
  metadata:
  - `rs.first_subrun: 21`, `rs.last_subrun: 21`
  - `rse.first_event: 21001`, `rse.last_event: 22000`, `rse.nevent: 1000`
  These match the formula we set in
  `data/mdc2025/pbi_sequence.json` — subrun offset=0 step=1 →
  index 21 gives subrun 21; event offset=0 step=1000 → index 21
  gives events 21001..22000. Globally unique `(run, subrun, event)`
  tuples confirmed across the dataset. The Offline PR #1799 +
  Production #533 chain is delivering the intended behavior in
  production, not just in dry-run.

## Stage 3: reco of the PBI dig outputs

Adds a `dig → mcs` step using `Production/JobConfig/recoMC/OnSpill.fcl`,
mirroring the ag-style entry pattern in `data/mdc2025/reco.json` (the
`MDC2025ag_best_v1_3` FlatGamma reco entry — non-Triggered dig inputs
through OnSpill).

### reco.json entry

```json
{
    "dsconf": ["MDC2025ai_best_v1_3"],
    "tarball_append": "-reco",
    "fcl": ["Production/JobConfig/recoMC/OnSpill.fcl"],
    "input_data": [
        {"dig.mu2e.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.art": 1},
        {"dig.mu2e.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.art": 1}
    ],
    "fcl_overrides": [{
        "outputs.LoopHelixOutput.fileName": "mcs.owner.{desc}.version.sequencer.art",
        "services.DbService.purpose": "Sim_best",
        "services.DbService.version": "v1_3"
    }],
    "inloc": ["tape"],
    "outloc": [{"*.art": "disk"}],
    "simjob_setup": ["/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ai/setup.sh"]
}
```

Knob rationale:
- **`tarball_append: "-reco"`** — input dig and output mcs share
  `desc` + `dsconf`, so the cnf tarball name would otherwise collide
  with the dig stage's tarball.
- **`simjob_setup: MDC2025ai`** — same reason as Stage 2: aj is still
  blocked by the `mu2e-trig-config` schema drift; reco stays on ai
  matching the dig dsconf.
- **Merge factor 1** — dig files are 0.87–1.92 GB; one dig file
  per reco job is the safe default.
- **`outloc: disk`** — matches existing recoMC entries; mcs goes to
  tape only after QA.

### DbService gotcha (2026-04-25)

Without explicit `services.DbService.purpose: "Sim_best"` and
`services.DbService.version: "v1_3"` overrides, reco fails at
`ProtonBunchTimeFromStrawDigis` with:

```
DbHandle could not get TID (Table ID) from DbEngine for TrkPreampStraw
You are currently using DB calibration set EMPTY -1/-1/-1
```

The existing `MDC2025af_best_v1_3` and `MDC2025ag_best_v1_3` reco
entries in `data/mdc2025/reco.json` **do not** override these — yet
they were the canonical pattern we built against. Two possibilities:

1. The af/ag entries were never smoke-tested with `mu2e -c`
   (only built as jobdefs), and would silently hit the same
   `EMPTY -1/-1/-1` failure if run.
2. Some other code path (POMS env? grid wrapper? a `mu2eOps`
   include?) injects `Sim_best` / `v<N>` based on the `_best_v<N>`
   dsconf suffix, and only the local `mu2e -c` test misses it.

Until that's resolved, **always set DbService overrides explicitly**
for new reco entries — derive purpose from the dsconf base
(`Sim_best`) and version from the suffix (`v1_3` for
`*_best_v1_3`). The mix.json entries already do this, so the
pattern is consistent.

### Local smoke test

```bash
# Generate jobdefs (under user account, no SAM push)
/mu2e-run MDC2025ai json2jobdef --json data/mdc2025/reco.json \
                                --dsconf MDC2025ai_best_v1_3

# FCL for index 0
/mu2e-run MDC2025ai jobfcl \
    --jobdef cnf.<owner>.PBINormal_33344Mix1BB-reco.MDC2025ai_best_v1_3.0.tar \
    --index 0 --default-location tape --default-protocol root > test_reco.fcl

# 1-event run (requires htgettoken for xrootd auth on personal shell)
/mu2e-run MDC2025ai mu2e -c test_reco.fcl -n 1
```

### Validation status (2026-04-25)

- Both jobdef tarballs build cleanly: 26 jobs each (one per dig file).
- 1-event smoke test on PBINormal index 0 (input file
  `dig.mu2e.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.001430_00000021.art`):
  **exit 0**, all reco modules ran (KKDe/Dmu/Ue/Umu, helix finders,
  calo, crv, makeSH/PH, LoopHelixOutput each Visited=1, Passed=1).
  CPU 1.57s, VmPeak 1.98 GB. Output:
  `mcs.<owner>.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.001430_00000021.art`
  (568 KB for 1 event).
- Per-index sequencer carries through from dig to mcs cleanly: dig
  index 21 → mcs `001430_00000021`, preserving the
  `event_id_per_index` chain (subrun 21, events 21001..22000).
- PBIPathological smoke test (index 0, input file
  `dig.mu2e.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.001430_00000012.art`):
  **exit 0**, same reco-module pattern as Normal, CPU 1.55s, VmPeak
  1.98 GB, output
  `mcs.<owner>.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.001430_00000012.art`.
  Both PBI flavors validated under MDC2025ai env.

### Production push (2026-04-25 UTC)

Pushed via — **note: target the existing PBI POMS map
(`MDC2025-025.json`), do not allocate a new map number**. The PBI
chain's stages 1+2 already live in `MDC2025-025`; reco extends that
map in place to keep the entire chain in one POMS scan target.

```bash
/mu2epro-run MDC2025ai json2jobdef \
    --json data/mdc2025/reco.json \
    --dsconf MDC2025ai_best_v1_3 \
    --prod \
    --jobdefs /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-025.json
```

Both tarballs SAM-declared (verified via `samweb list-files`):

- `cnf.mu2e.PBINormal_33344Mix1BB-reco.MDC2025ai_best_v1_3.0.tar`
- `cnf.mu2e.PBIPathological_33344Mix1BB-reco.MDC2025ai_best_v1_3.0.tar`
  → at `/pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/PBIPathological_33344Mix1BB-reco/MDC2025ai_best_v1_3/tar/85/94/...`

`MDC2025-025.json` extended in place from 4 → 6 entries (Stage 1 × 2
+ Stage 2 × 2 + Stage 3 × 2). SAM index `iMDC2025-025` deleted and
recreated by `mkidxdef --prod`, now def_id 218087, dimension
`dh.dataset etc.mu2e.index.000.txt and dh.sequencer < 0000156`
covering all 156 jobs (52 per stage). POMS will pick up the 52
new reco jobs on its next scan.

#### Process note: extend, don't allocate

First push attempt allocated a fresh `MDC2025-026.json` —
incorrect; the PBI chain belongs in one map. Remediation
(2026-04-25 ~11:53 UTC):

1. Re-ran `json2jobdef ... --prod --jobdefs MDC2025-025.json` —
   tarballs already in SAM so pushOutput no-oped (`already exists
   on SAM, skipping push`); entries appended; `iMDC2025-025`
   regenerated.
2. Removed orphan map file `MDC2025-026.json`.
3. Deleted orphan SAM index `iMDC2025-026` (the `samweb
   delete-definition` CLI hit a `RecursionError` deep in
   `urllib`/`socket` under one shell; succeeded under another with
   no apparent state difference — quirk worth a wider note if it
   recurs).

The convention is now codified in the `/poms-push` skill —
auto-detects the right map by scanning existing entries for the
workflow family and prints the recommended `/mu2epro-run`
invocation before any push.

#### Production grid run completed (2026-04-25 ~12:30 UTC)

POMS dispatched and completed all 52 reco jobs within ~30 minutes
of the SAM index recreation. Verified via
`listNewDatasets --completeness`:

```
   COUNT DATASET                                                          COMPLETENESS
   ----- -------                                                          ------------
      26 mcs.mu2e.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.art           26/26
      26 mcs.mu2e.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.art     26/26
```

Sibling `log.*` datasets also landed (26 files each). Full PBI
chain (dts → dig → mcs) is now in production with globally-unique
`(run, subrun, event)` tuples preserved end-to-end.

Expected outputs once POMS dispatches (mirroring the Stage 2 grid
turnaround of ~1 hour):

- `mcs.mu2e.PBINormal_33344Mix1BB.MDC2025ai_best_v1_3.art` (26 files)
- `mcs.mu2e.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.art` (26 files)
- sibling `log.*` datasets (26 files each)

Sequencers will preserve the dig→mcs per-index mapping: e.g. dig
`001430_00000021` → mcs `001430_00000021` (run 1430, subrun 21,
events 21001..22000), keeping globally-unique `(run, subrun, event)`
tuples through the full chain.

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
