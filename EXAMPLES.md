# Mu2e Production Tools — Usage Examples

Python-based tools for building, submitting, and running Mu2e production
jobs. Every command below is a real invocation you can paste into a shell
with an active Mu2e environment.

## Quick Navigation

- [1. Environment Setup](#1-environment-setup)
- [2. Overview](#2-overview)
- [3. Creating Job Definitions](#3-creating-job-definitions-json2jobdef-jobdef)
- [4. Random Sampling in Input Data](#4-random-sampling-in-input-data)
- [5. FCL Generation](#5-fcl-generation-jobfcl-fcldump)
- [6. Mixing Jobs](#6-mixing-jobs)
- [7. Production Execution](#7-production-execution-runmu2e)
- [8. Sequential vs. Pseudo-Random Auxiliary Input Selection](#8-sequential-vs-pseudo-random-auxiliary-input-selection)
- [9. FCL Overrides](#9-fcl-overrides)
- [10. Parity Tests](#10-parity-tests)
- [11. Additional Tools](#11-additional-tools)
- [12. Troubleshooting](#12-troubleshooting)

## 1. Environment Setup

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
```

Optional helper:

```bash
source bin/setup.sh   # adds prodtools bin/ to PATH, repo root to PYTHONPATH
```

`muse setup ops` provides Python 3, `samweb`, `mdh`, and `fhicl-get`.
`muse setup SimJob <tag>` is optional for most tools; only `muse setup
ops` is required. Building job definitions (`json2jobdef`, `jobdef`)
needs an Offline environment for `fhicl-get`, so source the SimJob
Musing that the entry's `simjob_setup` names — or, for a cnf built
against a `muse tarball` instead of a Musing (section 3), no `muse
setup SimJob` is needed at all; the build travels with the cnf.

No tool in this repo needs SQLAlchemy or a `pyenv ana` shell — the
submission ledger and the completeness check read plain sqlite3 from the
standard library.

## 2. Overview

Core production tools:

- `json2jobdef` — build cnf jobdef tarballs from JSON configs (recommended path); `--prod --enqueue` also registers a production campaign
- `jobdef` — build a single jobdef directly from CLI flags
- `jobfcl` — generate the per-index FCL from a jobdef tarball
- `fcldump` — resolve a dataset/target to its producing cnf and dump the FCL
- `runmu2e` — worker entry point: FCL generation, `mu2e` execution, pushOutput
- `runlocal` — run cnf jobs on this node, several at a time; nothing is pushed or declared
- `submissions` — status/run/pause/resume/cancel/complete/reconcile/resubmit
  CLI for the submission ledger (verify-and-resubmit recovery +
  sliced-campaign top-up + hand re-firing)
- `check_inputs` — pre-flight readability check on a campaign's inputs

Analysis / diagnostic tools:

- `jobquery` — inspect a cnf tarball (njobs, inputs, outputs, setup, recipe)
- `famtree` — dataset ancestry as a Mermaid diagram
- `logparser` — aggregate metrics from production log files
- `genFilterEff` — filter efficiencies in Proditions table format
- `datasetFileList` — physical file paths for a dataset or SAM definition
- `listNewDatasets` — recently produced datasets, with completeness
- `latestDatasets` — latest dsconf per description; chain-emit configs
- `copy_to_stash` — copy a dataset into stash (CVMFS) or resilient dCache

## 3. Creating Job Definitions (`json2jobdef`, `jobdef`)

### JSON-based (recommended)

```bash
# One entry, selected by desc + dsconf
json2jobdef --json data/Run1B/stage1.json --desc POT_Run1_a --dsconf MDC2025ac

# Bulk: every entry at a dsconf
json2jobdef --json data/Run1B/mix.json --dsconf Run1Ban_best_v1_5-000

# By index into the flattened entry expansion
json2jobdef --json data/Run1B/primary_muon.json --index 0

# Production push + campaign registration -- the whole flow, one command
json2jobdef --json data/mdc2025/evntuple.json --desc evnt \
    --dsconf MDC2025au_best_v1_5 --prod --enqueue --slice-size 1000
```

Flags: `--json` (required), `--desc`, `--dsconf`, `--index`, `--pushout`,
`--prod`, `--enqueue`, `--slice-size N` (default 1000), `--extend`,
`--ignore-empty`, `--event-count-positive`, `--no-cleanup`, `--verbose`.

Notes:

- `--index N` indexes the *flattened* (entry × list-field) expansion, not
  the JSON array position. Prefer `--dsconf` (bulk) or `--desc --dsconf`.
- `--prod` implies `--pushout`. Re-running `--prod` is idempotent — use
  it to finish a partially-failed push.
- **`json2jobdef` writes no file recording the campaign.** There is no
  `--jobdefs` flag: a production campaign lives only in the submission
  ledger, created directly by `--enqueue`. There is no map file anywhere
  in this codebase.
- `--prod` requires `--enqueue` — a bare `--prod` is refused, since
  otherwise the cnf would push to SAM and register no campaign, a silent
  no-op. `--enqueue` requires `--prod` in turn, because enqueue resolves
  the tarball from SAM.
- `inloc` and any `memory`/`disk`/`expected_lifetime`/`code` in the
  config are validated before anything is built, by the same validator
  (`jobdesc.validate_entry_value`) that guards `submissions set-entry`.
  A misspelled `inloc` does not fail at runtime — `file_resolver` finds
  no such location and falls through to SAM, so the jobs run to
  completion reading from the wrong place. That is why it is refused at
  the boundary.
- A bulk `--dsconf X --prod --enqueue` that skips any entry exits **2**
  and lists what it skipped. Entries that already processed are left
  alone — they are in SAM and in the ledger.
- `--enqueue` pushes the cnf to SAM, verifies its inputs are readable
  (`check_inputs`) and — for a code-mode entry — that the code tarball
  still matches the cnf's `code_ref`, then registers the entry directly
  as a sliced-submission campaign in the ledger — no file is written or
  needed. The campaign's `origin` column records provenance as `<json
  path>#<desc>@<dsconf>` — that column is never dispatched from, only
  echoed back by status tooling. `--slice-size` (default 1000, only
  meaningful with `--enqueue`) is frozen into the campaign row.
- Bulk `--dsconf X --prod --enqueue` (no `--desc`) loops over every
  matching entry, pushing and enqueueing each one in turn. A failure
  partway through (e.g. entry 7 of 22) leaves campaigns registered for
  the entries before it and nothing for the rest — the bulk run as a
  whole is **not resumable**. Re-running the identical command then
  dies immediately on the first entry's `active campaign N already
  exists for <tarball>` refusal (section 12) — that message is the
  double-submit guard working correctly, not ledger damage. Recover
  per-entry: re-run just the failed and remaining entries with
  `--desc <D> --dsconf <C> --prod --enqueue`.
- `--extend` excludes input files already consumed by the previous version
  of the same jobdef and auto-increments the tarball version.
- List-valued fields expand combinatorially: an entry with two `dsconf`
  values and three `desc` values yields six jobs.

Required JSON fields per entry: exactly one of `simjob_setup` or `code`,
plus `fcl`, `dsconf`, `outloc`. `desc` is derived from `input_data` when
omitted; `owner` defaults to the current user (mapped to `mu2e` for
mu2epro); `inloc` defaults to `none`; `njobs: -1` means "derive from the
input file list".

Stage-1 (generator) entry:

```json
{
  "desc": "POT_Run1_a",
  "dsconf": "MDC2025ac",
  "fcl": "Production/JobConfig/beam/POT.fcl",
  "fcl_overrides": {
    "services.GeometryService.inputFile": "Offline/Mu2eG4/geom/geom_run1_a.txt"
  },
  "njobs": 20000,
  "events": 5000,
  "run": 1430,
  "outloc": { "*.art": "disk" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ac/setup.sh",
  "owner": "mu2e"
}
```

Resampler entry (`resampler_name` + `input_data`; `MaxEventsToSkip` is
computed automatically from the dataset's event count):

```json
{
  "desc": "STMBeamToVDEle",
  "dsconf": "Run1Ban-001",
  "fcl": "Production/JobConfig/pileup/STM/BeamTo2VD.fcl",
  "resampler_name": "beamResampler",
  "input_data": { "sim.mu2e.EleBeamCat.Run1Bai.art": 1 },
  "njobs": 5000,
  "events": 200000,
  "run": 1470,
  "inloc": "tape",
  "outloc": { "*.art": "disk" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh",
  "owner": "mu2e",
  "sequential_aux": true
}
```

Merge entry: `input_data` maps a dataset to its merge factor
(`{"dts.mu2e.NoPrimary.Run1Ban-001.art": 10}` = 10 input files per job).
The dict value form accepts `count`/`merge_factor`, plus `random` and
`max_nfiles` (section 4); `split_lines` splits a local text file into
per-job chunks, and `chunk_lines` hands each job one N-line slice of a
single local file (no `inputs.txt` — the per-job slice is materialized
on the grid worker at runtime).

`inloc` accepts `disk`, `tape`, `scratch`, `resilient`, `stash`, `none`,
or `dir:<path>` (locally-mounted FS, e.g. cvmfs). There is no `auto`.
`resilient` reads via xrootd, `stash` reads via CVMFS, and `dir:` reads
via direct POSIX (the `file` protocol is forced).

`outloc` values accept `tape`, `disk`, `scratch`, `outstage`, and are
validated when the config is read. The first three are pushOutput
actions: each copies the file to its dataset path **and** declares it to
SAM. pushOutput has no copy-without-declare mode (`dosam` is set
unconditionally), and its `scratch` action is a fully declared dataset
that merely lives on scratch.

`outstage` is this repo's own, for test and study runs whose output
should stay out of SAM. The worker copies matching files to
`$MU2EGRID_WFOUTSTAGE/$CLUSTER/$PROCESS` with `ifdh` and declares
nothing; the log follows the data there, because a declared log would
otherwise name parents SAM has never heard of.

```json
{
  "desc": "STMBeamToVDEle",
  "dsconf": "Run1Ban-001",
  "fcl": "Production/JobConfig/pileup/STM/BeamTo2VD.fcl",
  "resampler_name": "beamResampler",
  "input_data": { "sim.mu2e.EleBeamCat.Run1Bai.art": 1 },
  "njobs": 20,
  "events": 200000,
  "run": 1470,
  "inloc": "tape",
  "outloc": { "*.art": "outstage" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh"
}
```

An outstage entry **cannot be enqueued as a campaign**: campaign
verification is fail-closed against SAM, so with nothing declared every
index reads as missing and each tick would recover the whole row,
forever. Build it and submit it by hand — or run it on this node with
`runlocal` (section 11), which pushes nothing at all.

Other consumed keys: `sequencer_from_index` (default true: output
sequencer = run + job index; set `false` to inherit the input file's
sequencer) and `generic_tarball` (build a reusable direct-input cnf with
`{desc}` deferred to runtime):

```json
{
  "dsconf": "MDC2025af_best_v1_3",
  "desc": "OnSpillTriggeredReco",
  "generic_tarball": true,
  "fcl": "Production/JobConfig/recoMC/OnSpill.fcl",
  "fcl_overrides": {
    "outputs.LoopHelixOutput.fileName": "mcs.owner.{desc}.version.sequencer.art",
    "services.DbService.purpose": "Sim_best",
    "services.DbService.version": "v1_3"
  },
  "inloc": "tape",
  "outloc": { "*.art": "disk" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025af/setup.sh"
}
```

Optional per-entry resource requests — `"memory"`, `"disk"`,
`"expected_lifetime"` (jobsub-format strings, e.g. `"4000MB"`,
`"50GB"`, `"48h"`):

```json
{
  "desc": "MuStopPileup",
  "dsconf": "Run1Ban-001",
  "fcl": "Production/JobConfig/pileup/MuStopPileup.fcl",
  "njobs": 5000,
  "memory": "4000MB",
  "outloc": { "*.art": "tape" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh",
  "owner": "mu2e"
}
```

`json2jobdef` copies any of the three keys present in the config into
the ledger entry verbatim on `--enqueue`. There is no operator-facing
CLI flag for these — the entry key (set here, or retuned on a live
campaign with `submissions set-memory`/`set-entry`, section 11) is the
only way to set them, and it always wins over the built-in default
(`2500MB` / `30GB` / `24h`). The *effective* values are frozen into the
ledger row and campaign snapshot at submission time, so a later recovery
or cron-fed slice reproduces exactly what the jobs originally ran with.

The memory default sits above mu2egrid's `2000MB` deliberately: Mu2e
primaries measure just over that line (VmHWM 2266 MB for
`PiTargetStops`, 2377 MB for the RPC primaries), so entries naming no
memory key were being OOM-held. Prefer leaving the key unset — naming it
forfeits the `4000MB` recovery floor, which applies only when the key is
absent.

The draining keys `input_pattern` and `prestage` also pass straight
through to the submission entry, so a draining campaign is enqueued
directly from its config — same `--enqueue` path as an indexed
campaign, no hand-edit:

```json
{
  "dsconf": "MDC2025au_best_v1_5",
  "desc": "evnt",
  "generic_tarball": true,
  "input_pattern": "mcs.mu2e.%OnSpill.MDC2025au_best_v1_5.art",
  "prestage": true,
  "fcl": "EventNtuple/fcl/from_mcs-mockdata.fcl",
  "fcl_overrides": {
    "services.TFileService.fileName": "nts.owner.{desc}.version.sequencer.root"
  },
  "inloc": "tape",
  "outloc": { "nts.*.root": "tape" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/AnalysisMDC2025/v02_00_00/setup.sh"
}
```

```bash
json2jobdef --json data/mdc2025/evntuple.json --desc evnt \
    --dsconf MDC2025au_best_v1_5 --prod --enqueue --slice-size 500
```

A draining entry names a 5-field dataset pattern (`%` wildcards) and NO
`njobs`: it drains a growing dataset 1:1 through a generic cnf instead
of a fixed index range. `input_pattern` requires `generic_tarball: true`
— an entry cannot claim both an input pattern and a fixed index window.
`enqueue_entry` (the code behind `--enqueue`) detects the draining shape
from `input_pattern` and registers a file-keyed campaign instead of an
index-keyed one. Optional draining keys: `exclude_desc` (exact desc
matches to skip), `min_age_minutes` (default 60 — a SAM `create_date`
age gate before a file is eligible), `prestage` (default false, opt-in
tape recall for tape-only candidates).

A draining entry's `outloc` globs must be tier-specific (`nts.*.root`,
`mcs.*.art` — never `*.art`): a glob that also matches the input pattern
is refused at enqueue, because the worker's push manifest would
otherwise declare the fetched input copy as an output, and pushOutput
would then try to delete the production input at its own dataset path.

### Running against a code tarball instead of a Musing

`simjob_setup` (a `/cvmfs` Musing `setup.sh`) and `code` (an absolute
path to a `muse tarball` build) are mutually exclusive entry keys —
`json2jobdef` requires exactly one:

```json
{
  "desc": "POT_Run1_a",
  "dsconf": "MDC2025ac",
  "fcl": "Production/JobConfig/beam/POT.fcl",
  "njobs": 20,
  "events": 5000,
  "run": 1430,
  "outloc": { "*.art": "disk" },
  "code": "/exp/mu2e/data/users/mu2epro/code_tarballs/Code.tar.bz2",
  "owner": "mu2e"
}
```

```bash
# Direct jobdef invocation takes the same choice as one flag
jobdef --code /exp/mu2e/data/users/$USER/code_tarballs/Code.tar.bz2 \
    --dsconf MDC2025ac --desc CustomBuild --dsowner mu2e \
    --run-number 1430 --events-per-job 5000 --embed template.fcl
```

- `code` must be a `muse tarball` output: a bzip2-compressed tar
  containing `Code/setup.sh`. A plain Muse work directory has no
  `setup.sh` — only `muse tarball` packages one. `json2jobdef` and
  `jobdef --code` refuse a tarball that is unreadable, not
  bzip2-compressed, or missing `Code/setup.sh` at build time, before
  any jobs are created.
- Nothing is embedded in the cnf: this repo ships an Offline build as a
  jobsub sidecar (`--tar_file_name`), never inside the tarball. The
  cnf's `jobpars.json` instead carries `code_ref`
  (`sha256`/`size`/`source_path`) as provenance, and the entry keeps
  the tarball's own path (`code`) so a later slice, recovery, or the
  enqueue gate can find the same file and confirm its digest still
  matches.
- The grid path needs nothing beyond the entry key — submission adds
  jobsub's `--tar_file_name dropbox://<tarball>` automatically from
  `code`; see section 7 for how the worker reads it back.
- For a local smoke run with no grid involved, `bin/runlocal --code
  <tarball>` unpacks the build once into `<workdir>/code/` before any
  job runs (section 11); every spawned child reuses that one unpack via
  `--code-root`.
- `code` is one of the keys `submissions set-entry` can retune on a live
  campaign (`submissions set-entry CAMP_ID code /new/path/Code.tar.bz2`)
  — useful for pointing an existing campaign at the same build after
  moving it to its durable home.
- **A `--prod` code tarball is not in SAM.** Sidecar delivery means the
  bytes never pass through `pushOutput`; only the cnf (and its
  `code_ref` digest) reaches SAM. Delete the tarball a `--prod`
  campaign's `code` key points at and the campaign becomes
  unreproducible even though the cnf survives — the digest proves what
  the build *was*, it cannot regenerate it. Keep a `--prod` code
  tarball on a durable, mu2epro-readable path for the campaign's whole
  lifetime — never personal scratch, never `/tmp`.
- **Do not pass jobsub_submit's `--skip-check rcds` for a code-mode
  submission.** RCDS publication of the sidecar is not instant;
  `--skip-check rcds` lets a submission through before that check would
  otherwise block it — exactly how a job lands on a worker before its
  code has actually propagated. The job starts, finds no build, and
  fails in a way that looks unrelated to code delivery.

### Direct `jobdef` invocation

```bash
# Generator (EmptyEvent)
jobdef --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh \
    --dsconf Run1Ban --desc NoPrimary --dsowner mu2e \
    --run-number 1470 --events-per-job 50000 \
    --embed template.fcl

# Merge (RootInput)
jobdef --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh \
    --dsconf Run1Ban --desc NoPrimaryCat --dsowner mu2e \
    --inputs inputs.txt --merge-factor 10 \
    --embed template.fcl

# Resampler auxinput
jobdef --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh \
    --dsconf Run1Ban-001 --desc STMBeamToVDEle --dsowner mu2e \
    --run-number 1470 --events-per-job 200000 \
    --auxinput "1:physics.filters.beamResampler.fileNames:inputs.txt" \
    --embed template.fcl
```

Flags: `--setup` or `--code` (one required), `--dsconf` and `--dsowner`
(required), `--desc` or `--auto-description`, `--embed FCL` or
`--include FCL` (one required), `--run-number`, `--events-per-job`,
`--inputs FILE`, `--merge-factor N`, `--auxinput SPEC` (repeatable),
`--samplinginput SPEC` (repeatable, `count:dsname:filelist`),
`--output-dir DIR`, `--verbose`.

## 4. Random Sampling in Input Data

Select a deterministic pseudo-random subset of a dataset instead of the
full sorted list:

```json
{
  "desc": "NeutralsFlashCat",
  "dsconf": "MDC2025ad",
  "fcl": "Production/JobConfig/common/artcat.fcl",
  "input_data": {
    "dts.mu2e.NeutralsFlash.MDC2025ac.art": { "count": 5000, "random": true }
  },
  "njobs": 1000,
  "inloc": "disk",
  "outloc": { "*.art": "tape" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ad/setup.sh",
  "owner": "mu2e"
}
```

- The seed is derived from `(owner, desc, dsconf, dataset, count, njobs)`
  — the same inputs always produce the same file selection.
- Optional `"max_nfiles": M` inside the same dict caps the list (positive
  int). The non-random branch slices `sorted(files)[:M]`; the random
  branch bounds `total_needed`. `njobs` is NOT auto-recomputed — set it
  consistently yourself.

## 5. FCL Generation (`jobfcl`, `fcldump`)

From a jobdef tarball:

```bash
jobfcl --jobdef cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar --index 0
jobfcl --jobdef cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar \
    --target dig.mu2e.NoPrimaryMix1BBTriggered.Run1Ban_best_v1_5-000.001470_00000042.art
jobfcl --jobdef cnf.mu2e.NoPrimaryCat.Run1Ban.0.tar \
    --source dts.mu2e.NoPrimary.Run1Ban-001.001470_00000000.art
```

Flags: `--jobdef` (required), one of `--index N` / `--target FILE` /
`--source FILE`, `--default-location` (alias `--default-loc`, default
`tape`), `--default-protocol` (alias `--default-proto`, default `file`;
use `root` for xrootd URLs).

`fcldump` resolves the producing cnf for you and writes the FCL to a
file (defaults: `--loc tape --proto root`):

```bash
# From a local cnf tarball (preferred for smoke tests)
fcldump --local-jobdef cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar

# From an output dataset name (finds the cnf in SAM)
fcldump --dataset dig.mu2e.NoPrimaryMix1BBTriggered.Run1Ban_best_v1_5-000.art

# From a specific target output file
fcldump --target dig.mu2e.NoPrimaryMix1BBTriggered.Run1Ban_best_v1_5-000.001470_00000042.art

# Generic (direct-input) cnf: supply the input file explicitly
fcldump --local-jobdef cnf.mu2e.OnSpillTriggeredReco.MDC2025af_best_v1_3.0.tar \
    --fname dig.mu2e.NoPrimaryMix1BBTriggered.MDC2025af_best_v1_1.001430_00000042.art

# List all cnfs at a dsconf
fcldump --list-dsconf Run1Ban_best_v1_5-000
```

Flags: `--dataset`, `--target`, `--local-jobdef`, `--fname`,
`--list-dsconf`, `--index N` (default 0), `--loc` (default `tape`),
`--proto` (default `root`).

Note: one cnf often produces outputs whose descriptions carry suffixes
glued onto the cnf desc (`Triggered`/`Triggerable` at digi/mix, `-LH`/
`-CH`/`-KL` at reco). `fcldump --dataset` handles the resolution; when it
cannot, strip the suffix to find the parent cnf or use `--local-jobdef`.

## 6. Mixing Jobs

Mixing entries add `pbeam` and `pileup_datasets` (list-of-dict form):

```json
{
  "input_data": [ { "dts.mu2e.NoPrimary.Run1Ban-001.art": 10 } ],
  "pileup_datasets": [ {
    "dts.mu2e.MuBeamFlashCat.Run1Ban.art": 1,
    "dts.mu2e.EleBeamFlashCat.Run1Ban.art": 25,
    "dts.mu2e.NeutralsFlashCat.Run1Ban.art": 1,
    "dts.mu2e.MuStopPileupCat.Run1Ban.art": 2
  } ],
  "pbeam": [ "Mix1BB" ],
  "dsconf": [ "Run1Ban_best_v1_5-000" ],
  "fcl": [ "Production/JobConfig/mixing/Mix.fcl" ],
  "inloc": [ "resilient" ],
  "outloc": [ { "dig.mu2e.*.art": "tape" } ],
  "simjob_setup": [ "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh" ],
  "owner": [ "mu2e" ]
}
```

- Each pileup dataset maps to its mixer automatically, by
  case-insensitive substring of the dataset name: `mubeam`/`muonbeam` →
  `MuBeamFlashMixer`, `elebeam`/`electronbeam` → `EleBeamFlashMixer`,
  `neutral` → `NeutralsFlashMixer`, `mustop`/`muonstop` →
  `MuStopPileupMixer`. The value is the per-job file count for that
  mixer.
- `pbeam` selects the intensity include (`Mix1BB` → `mixing/OneBB.fcl`,
  `Mix2BB` → `TwoBB.fcl`, `MixLow` → `LowIntensity.fcl`, `MixSeq` →
  `NoPrimaryPBISequence.fcl`, `MixFlat` → `FlatPBI.fcl`) and is appended
  to the desc (`NoPrimary` → `NoPrimaryMix1BB`).
- The include is emitted *before* `fcl_overrides`, so overrides still win
  over the intensity settings.
- `MaxEventsToSkip` per mixer is computed from the first dataset's event
  count and written before `fcl_overrides`, so overrides can still adjust
  it.
- `input_data` merge factor > 1 is supported (e.g. 10 primaries per job).

```bash
json2jobdef --json data/Run1B/mix.json --dsconf Run1Ban_best_v1_5-000
```

Mixing pileup is read from `resilient` dCache, so both the `inloc` and
the staged pileup `*Cat` datasets have to be there before submission —
`check_inputs` (section 11) is the pre-flight that proves it.

## 7. Production Execution (`runmu2e`)

`runmu2e` is the grid worker entry point, and it runs only under the
direct backend: it exits immediately unless `MU2EGRID_JOBDEF` is set by
the `jobsub_submit` argv the direct backend builds
(`utils/jobsub_argv.py`, invoked from `utils/submit.py`). There is no
operator-facing invocation — the submission ships the cnf tarball plus
an "ops JSON" via dropbox, both landing under `$CONDOR_DIR_INPUT`, and
the worker resolves its own job index from `$PROCESS` through the ops
JSON's `jobs` lookup table.

To simply run a cnf's jobs on this node — one index or a few dozen, with
no ops JSON and no ledger row — use `runlocal` (section 11); it shares
this worker's prep and stops before the push.

For a local smoke test *of the worker itself*, reuse the ops JSON a
dry-run submission already writes. Since there is no standalone submit
CLI any more, that means dry-running against an existing ledger row —
either a row already on the campaign (`submissions status` lists row
ids), or a fresh one from `json2jobdef --enqueue` (section 3):

```bash
submissions resubmit 4231 --indices 0 --dry-run   # prints "Wrote ops JSON: /tmp/ops-...json"

cd /tmp && MU2EGRID_JOBDEF=cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar \
    MU2EGRID_OPSJSON=ops-$USER-NoPrimaryMix1BB-12345.json PROCESS=0 \
    runmu2e --dry-run --nevts 10
```

Flags: `--dry-run` (print pushOutput commands without running them),
`--nevts N` (default -1 = all), `--mu2e-options "..."` (extra `mu2e`
arguments), `--copy-input` (stage inputs locally with `mdh` instead of
streaming).

- The resolved index is carried internally as an `fname` whose sequencer
  field holds it: `etc.mu2e.index.000.NNNNNNN.txt`, seventh field
  `NNNNNNN` zero-padded to 7 digits. `000` is a fixed description
  placeholder, not the index. Nothing sets `fname` from outside any
  more — the worker synthesizes it.
- Inputs stream via xroot by default. A JSON config entry sets
  `"copy_input": true` to stage inputs locally with `mdh` instead —
  worth it only for descs with fat runtime tails, where a mid-job
  xroot drop wastes the most CPU. The entry key wins over the
  `--copy-input` CLI flag; `stash`/`resilient`/`dir:` inlocs always
  stream regardless.
- Outputs are pushed only when `mu2e` exits 0; the log is pushed always,
  including when the data push itself raises.
- Outputs are partitioned by their entry's `outloc` location. Anything
  bound for `outstage` (section 3) is copied to
  `$MU2EGRID_WFOUTSTAGE/$CLUSTER/$PROCESS` with `ifdh` and never reaches
  pushOutput; `parents_list.txt` is written only when something is
  actually declared.
- `direct_input` entries are not index-submittable — they run as
  draining batches (`submissions resubmit ROW_ID --files LIST.txt`).
- For a code-mode cnf (section 3), `runmu2e` reads the Offline build
  from `$INPUT_TAR_DIR_LOCAL` — the directory jobsub itself populates
  on the worker when `--tar_file_name` was passed — instead of sourcing
  a `/cvmfs` Musing path. `bin/runjob.sh`'s startup diagnostics echo
  `INPUT_TAR_DIR_LOCAL` alongside `CONDOR_DIR_INPUT`: an `unset` value
  there on a failed job is the first thing to check, and it means
  `--tar_file_name` never reached the worker — the RCDS caveat in
  section 3 is the usual reason.

## 8. Sequential vs. Pseudo-Random Auxiliary Input Selection

By default, auxiliary input files (resampler/mixer `fileNames`) are
selected pseudo-randomly per job index. Setting `"sequential_aux": true`
in the entry stores `tbs.sequential_aux` in the cnf and switches to
deterministic sequential slices with rollover — job *i* takes the next
`count` files in list order. Use it when resampled statistics must not
repeat across neighboring jobs (see the STM resampler entry in section 3).

## 9. FCL Overrides

`fcl_overrides` becomes the embedded `template.fcl`: an `#include` of the
base FCL followed by one line per override. The base FCL is never
expanded — workers resolve it from the SimJob release at run time.

```json
"fcl_overrides": {
  "#include": [ "Production/JobConfig/mixing/OneBB.fcl" ],
  "services.DbService.purpose": "Sim_best",
  "services.DbService.version": "v1_5",
  "physics.filters.CaloDtsClusterFilter.NullFilter": false,
  "outputs.Output.fileName": "dig.owner.{desc}.version.sequencer.art"
}
```

- Values are serialized as JSON, which is valid FHiCL for strings, lists,
  numbers, and booleans (`false`, not `False`).
- `tier.owner.{desc}.version.sequencer.ext` placeholders in output
  fileNames are substituted at build time; outputs whose upstream defaults
  glue a suffix onto the desc token (e.g. `description-CH`) need an
  explicit per-output override or the build-time guard rejects the cnf.
- The template is embedded with `--embed`, so the cnf carries the
  override text verbatim.

## 10. Parity Tests

Validate byte-for-byte equivalence against the Perl `mu2ejobdef`
reference implementation:

```bash
test/parity_test.sh          # index-0 configuration only
test/parity_test.sh --all    # all configurations
```

Unit tests: `python3 test/test_unit.py`. Tarball comparison helper:
`test/compare_tarballs.sh <a.tar> <b.tar>`.

## 11. Additional Tools

### `famtree`

Dataset ancestry as a Mermaid diagram (auto-excludes `etc*.txt` files):

```bash
famtree dts.mu2e.MuStopPileupCat.Run1Ban.art
famtree mcs.mu2e.NoPrimaryMix1BBTriggered.MDC2025an_best_v1_3.art --stats --max-files 20
```

Flags: `--png`, `--svg` (require `mmdc`), `--stats`, `--max-files N`
(default 10).

### `logparser`

Aggregate metrics (CPU, memory, throughput) from production logs:

```bash
logparser log.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.log
logparser log.mu2e.POT_Run1_a.MDC2025ac.log -n 50
```

Flags: one or more dataset names (positional), `-n/--max-logs N`
(default: all logs in the dataset).

### `genFilterEff`

Filter efficiencies in Proditions format (`TABLE SimEfficiencies2`):

```bash
genFilterEff sim.mu2e.PiTargetStops.MDC2025ac.art --out SimEfficiencies2_MDC2025.txt
```

Flags: one or more dataset names (positional), `--out`/`--outfile`
(required), `--firstLine` (default `TABLE SimEfficiencies2`),
`--writeFullDatasetName`, `--chunksize N` (default 100),
`--maxFilesToProcess N`, `--verbosity N` (default 2).

### `datasetFileList`

Physical /pnfs paths for a dataset or SAM definition:

```bash
datasetFileList dts.mu2e.NoPrimary.Run1Ban-001.art
datasetFileList dts.mu2e.NoPrimary.Run1Ban-001.art --tape --basename
datasetFileList some_sam_definition --defname
```

Flags: `--basename`, `--disk`, `--tape`, `--scratch`, `--defname`.

### `listNewDatasets`

Recently produced datasets, optionally with a completeness column
computed against the submission ledger:

```bash
listNewDatasets --days 1 --completeness
listNewDatasets --query "dh.dataset like '%.Run1Ban_best_v1_5-000.%'"
```

Flags: `--filetype` (default `art`), `--days N` (default 7), `--user`
(default `mu2epro`), `--size`, `--query`, `--completeness`,
`--ledger-db PATH` (default
`/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, env
`MU2E_SUBMISSION_DB`), `--color {auto,always,never}`.

`--completeness` compares each dataset's SAM file count against the
expected job count recorded in the ledger, so only datasets that went
through the direct backend get a verdict. Legacy POMS-launched datasets
are never in the ledger — the POMS backend was removed 2026-08 (legacy
stages recover from the `pre-poms-removal` git tag).

### `latestDatasets`

Latest dsconf per description; also emits ready-to-run json2jobdef
configs for the next chain stage from `templates/<campaign>/<stage>.json`:

```bash
latestDatasets --defname 'dig.mu2e.%.MDC2025%.art' --show-count
latestDatasets --emit reco --campaign MDC2025ap --skip-produced

# Datasets replaced by a newer dsconf, instead of the latest
latestDatasets --defname 'nts.mu2e.%.MDC2025%.root' --superseded

# Order within a description by SAM creation date instead of dsconf lex order
latestDatasets --defname 'nts.mu2e.CeEndpointMix1BBTriggered.MDC2020%.root' --latest-by time
```

Flags: `--defname`, `--user`, `--stdin`, `--show-count`, `--superseded`,
`--latest-by {dsconf,time}`, `--emit {digi,reco,ntuple,mix}`,
`--campaign`, `--templates-dir` (default `<repo>/templates`), `--dsconf`,
`--complete-only`, `--skip-produced`, `-v/--verbose`.

- `--superseded` prints the inverse of the default listing: every
  non-latest version per description (the datasets a newer dsconf
  replaced), honoring `--show-count` and `--complete-only`. It cannot be
  combined with `--emit` or `--skip-produced`.
- `--latest-by` picks how "latest" is decided within a description.
  `dsconf` (the default) sorts the dsconf field lexicographically —
  correct within a single naming series, and issues zero SAM queries, so
  it is what `--emit` relies on for a fast chain hop. `time` sorts by
  each dataset's SAM definition creation date instead — use it when a
  description spans naming series, where lex order is meaningless (the
  ntuple series `MDC2020-001` sorts BELOW
  `MDC2020aw_best_v1_3_v06_06_00` lexicographically, because `-` < `a`,
  even though it was created six months later). `time` mode queries SAM
  only for contended descriptions (2+ versions).
- `--latest-by time` does NOT apply identically everywhere. `--emit
  ntuple` and lister `--campaign` first narrow the discovered inputs to
  the single latest release (max campaign tag, a dsconf-lexicographic
  operation) before picking latest-per-description; that narrowing is
  dsconf-order logic, so it runs only under `dsconf` mode — under `time`
  mode it would delete newer-by-date datasets before the creation-date
  key ever saw them, silently defeating `--latest-by time`. It is
  therefore skipped in `time` mode for those two call sites.
  `--emit digi`/`mix`/`reco` (family-wide discovery) and plain
  `--defname`/`--stdin` lister mode (including `--superseded` fed from
  either) have no release-narrowing step, so `time` mode there behaves
  as the paragraph above describes with no caveat.

### `jobquery`

Inspect a cnf tarball:

```bash
jobquery --njobs cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar
jobquery --input-datasets --output-datasets cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar
jobquery --recipe cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar
```

Flags: `--jobname`, `--njobs`, `--input-datasets`, `--input-files`,
`--output-datasets`, `--output-files DATASET[:size]`, `--codesize`,
`--setup`, `--recipe`, and the positional `.tar`.

- `--njobs` reports the cnf's own capacity from `tbs.njobs`; `0` means
  open-ended (the ledger entry is authoritative).
- `--recipe` reconstructs the build config — setup, njobs, output
  patterns, and the embedded `mu2e.fcl` (the json2jobdef `fcl` plus its
  `fcl_overrides`). For a code-mode cnf it also prints `code:` and
  `code sha256:` lines sourced from `code_ref`, since a generic
  direct-input tarball has no embedded `mu2e.fcl` to show; an ordinary
  Musing cnf prints neither line.
- `--codesize` always prints `0` — this repo ships an Offline build as
  a jobsub sidecar (section 3), never embedded in the cnf, so `0` is
  the honest answer rather than a placeholder. There is no
  `--extract-code`: it used to pull out any tar member ending in
  `.tar`, which under sidecar delivery is not code at all, and it was
  removed.

### `submissions`

Direct-submission subsystem CLI: read-only status (default verb — no
verb needed), the hourly verify/resubmit/top-up tick (`run`), campaign
management (`pause`/`resume`/`cancel`/`complete`, plus the two retune
verbs), a stuck-row unblock (`reconcile`), and hand re-firing
(`resubmit`). Reads the submission ledger — the same sqlite3 DB
`json2jobdef --enqueue`, a cron-fed slice, or a `resubmit` writes rows
to.

```bash
submissions                        # read-only ledger + campaigns + cap (any account)
submissions status                 # same, explicit form
submissions --mine status          # your own ledger instead of production
submissions run --dry-run          # verify + top-up report, no submissions
submissions run                    # full pass (mu2epro; cron entry point)
submissions run --row 42 --max-attempts 5
submissions run --max-queued 5000  # override the top-up cap for this pass
submissions run --campaign 7       # top up only campaign 7 (recovery pass still runs)
submissions pause 7 --note "investigating OOM"
submissions resume 7               # paused -> active; preserves the pause note
submissions cancel 7               # close; already-submitted rows still recovered
submissions complete 7 --note "upstream production finished"
submissions set-slice 7 500        # retune the batch size from the next tick
submissions set-memory 7 3000MB    # retune the memory request from the next tick
submissions set-entry 7 inloc resilient --include-open-rows  # also fix open rows' recoveries
submissions set-entry 7 code /exp/mu2e/data/users/mu2epro/code_tarballs/Code.tar.bz2
submissions reconcile 123 --note "checked jobsub_q, window free"
submissions resubmit 4231 --indices 4000,4001,4055             # named indices
submissions resubmit 4231 --indices-file gaps.txt --dry-run   # preview first
submissions resubmit 4198 --files parked.txt                  # draining row
```

Global flags (before the verb): `--db PATH` — the ledger to act on
(default: the production ledger
`/exp/mu2e/data/users/mu2epro/prodtools/submissions.db` for `status`,
your own ledger for every mutating verb; env `MU2E_SUBMISSION_DB`
overrides the production default) — and `--mine`, a shorthand for your
own ledger at `/exp/mu2e/data/users/$USER/prodtools/submissions.db`
instead of the per-verb default.

Verbs:

- `status` (the default when no verb is given) — print the ledger
  table, the campaigns table, and the resolved top-up queue cap, then
  exit. Read-only: takes no lock, makes no submissions.
- `run` — the tick: a recovery pass over active ledger rows (drain-check
  via `jobsub_q`; report and skip held jobs — the loop never runs
  `condor_rm`/`condor_release`; SAM-verify the row's cnf indices using
  the cnf's own expected output filenames; then close `complete`,
  resubmit the missing indices as a child row (`attempt`+1, in-process
  via `submit.submit_entry`), or mark `exhausted` at the attempt cap),
  followed by campaign top-up (counts total mu2epro idle+running jobs,
  then round-robins whole slices to active campaigns, oldest first,
  while `count + slice <= cap`; skipped entirely when there is no
  active campaign, and for `--row`) and the draining tick (file-keyed
  batches sharing the same cap). Flags: `--dry-run` (report would-*
  actions only; no submissions, no state changes; also takes no lock),
  `--row N` (process only this ledger row id, skips top-up),
  `--max-attempts N` (default 3; a row closes `exhausted` once its
  attempt count reaches this cap), `--max-queued N` (top-up cap for
  this pass; default: env `MU2E_MAX_QUEUED`, then `5000`),
  `--campaign ID` (top up only this campaign; the recovery pass still
  runs over all rows; omit for the cron behavior of ticking every
  active campaign).
- `pause CAMP_ID [--note TEXT]` — pause an active campaign (default
  note: `"operator pause"`).
- `resume CAMP_ID` — reactivate a paused campaign; the note recorded
  when it was paused is preserved, not cleared.
- `cancel CAMP_ID` — cancel a campaign; already-submitted ledger rows
  still get recovered normally.
- `complete CAMP_ID [--note TEXT]` — the operator close-out for a
  draining campaign (default note: `"operator complete"`).
  Already-submitted rows still get verified and recovered. A draining
  campaign never auto-completes: its input set keeps growing until the
  upstream production finishes, and only the operator knows when that
  point has been reached.
- `set-slice CAMP_ID N` / `set-memory CAMP_ID MEM` — retune a live
  campaign's slice size or memory request. Both take effect on the next
  tick and reach only future slices, never already-submitted rows.
- `set-entry CAMP_ID KEY VALUE [--include-open-rows]` — the general form
  of the two retune verbs above: set one of `inloc`/`memory`/`disk`/
  `expected_lifetime`/`code` on a live campaign's entry. Without
  `--include-open-rows` the change reaches future slices only (same as
  `set-slice`/`set-memory`) — a resubmit builds its options from the
  row's own frozen entry snapshot, not the campaign's current one, so an
  already-submitted row keeps what it was submitted with. With the flag,
  every not-yet-closed row on the campaign's tarball is rewritten too,
  which is what makes an in-flight RECOVERY pick up the new value. The
  flag defaults off because an *unset* `memory` is what earns a recovery
  the `4000MB` floor (section 3) — cascading a memory value would
  forfeit it; an `inloc` fix, which has no floor to lose, normally wants
  the flag on. The value goes through the same validator
  `json2jobdef --enqueue` uses, so a spelling you cannot enqueue is also
  one you cannot set here — a `code` value in particular must be an
  absolute path.
- `reconcile ROW_ID [--note TEXT]` — close a ledger row stuck in
  `failed` or `submitting` so its index window stops blocking a
  campaign's slice progress, marking it `reconciled` (kept for audit,
  never revisited by the recovery pass). By running this you assert you
  have checked `jobsub_q` yourself and the jobs for this window are
  genuinely absent — a `jobsub_submit` that exits non-zero can still
  have created a cluster, and reconciling a window that is actually
  running duplicates physics (deterministic payloads). Never touches a
  campaign's cursor — run `submissions resume <ID>` afterward to
  restart it.
- `resubmit ROW_ID (--indices SPEC | --indices-file F | --files F)
  [--dry-run]` — hand re-fire a named set of indices or input files
  from an *existing* ledger row, as a child submission (attempt+1). The
  entry comes from the row itself — nothing to hand-edit, no file to
  write. `--indices` is a comma/space-separated list of absolute cnf
  indices (`4000,4001,4055`) — integers only, no `N-M` range syntax;
  for a large or scattered set use `--indices-file` instead (same
  grammar, one entry per line, `#`-comments ignored); `--files` is a
  file of input art filenames, one per line, for a draining row (the
  parked-file list a `submissions run` tick writes). `--files` only
  works against a draining (file-keyed) row, and `--indices`/
  `--indices-file` only against an index row — the CLI refuses the
  mismatch by name. REFUSES when any named index/file is still covered
  by an unsettled row for the same tarball: payloads are deterministic,
  so re-sending live work duplicates physics; the refusal names the
  blocking row and points at `submissions reconcile`. The reconstructed
  entry drops any `firstjob` window — `--indices`/`--files` values are
  absolute (cnf index or input filename), not window-relative.

Notes:

- `status` and `run --dry-run` are the only read-only invocations —
  safe under any account, no lock, no grid writes. `resubmit --dry-run`
  also takes no lock and submits nothing.
- `run` (without `--dry-run`), `resubmit` (without `--dry-run`), and
  the mutating verbs all take the same per-DB lock (`submissions.lock`
  beside the DB); an overlapping mutating run exits with "another
  submissions run holds ... — exiting" instead of racing.
- `run` exits 2 when anything this pass needed human attention — a
  cron-visible "needs a look" signal — and 0 otherwise. The
  needs-attention set: a row with **held** jobs; a row that went (or,
  under `--dry-run`, would go) **exhausted** at the attempt cap; a
  **child-missing** row (a resubmit succeeded but no child ledger row
  was recorded); a campaign **paused** this tick by a submit failure or
  the crash-window overlap guard (or would be, under `--dry-run`); a
  **queue-count failure** (the queue query itself unreadable — top-up is
  skipped, not just under-counted); a **drain error**; or a **lingering
  paused campaign** — any campaign still `paused` when `run` executes,
  not just the tick that paused it, so the signal repeats every tick
  until a human `resume`s or `cancel`s it. `status` never exits 2 — it
  is a display, not a monitor.
- Deterministic cnf payloads re-run identical events, so a systematic
  failure re-fails every attempt; `exhausted` is where a human takes
  over, not something blind retry fixes.
- Every submission attempt — a `json2jobdef --enqueue`, a cron-fed
  slice, or a `resubmit` — appends a block to `submit-YYYYMMDD.log`
  beside the ledger DB (one file per UTC day, plain appends, no
  rotation).
- Campaign states: `active` (loop feeds it) → `complete` (fully
  submitted, or operator-closed; jobs may still be running —
  verification continues per ledger row), `paused` (submit failure,
  crash-window overlap, or `pause`; a human clears it with `resume` or
  `cancel`), or `cancelled` (`cancel`; already-submitted rows still get
  recovered).
- `--enqueue` refuses a second campaign for the same tarball while an
  `active` OR `paused` one exists. A paused campaign still owns its
  index space, so "pause then enqueue" is not a workaround — only
  `submissions cancel <ID>` frees the tarball (section 12).
- Before every slice, top-up also checks the ledger for indices already
  covering the slice's window, in any state — evidence that a submission
  happened even if the campaign row's cursor advance was lost to a
  crash. An overlap pauses the campaign with a crash-window note instead
  of resubmitting.
- Draining campaigns track pending work in SAM, not a cursor: pending =
  inputs whose expected outputs (computed per-file from the cnf's own
  `job_outputs` mapping) don't exist yet, minus files already in-flight
  or parked. Nothing counts as done until its output exists.
- Cap resolution is `--max-queued` flag > `MU2E_MAX_QUEUED` env >
  `5000`, resolved once per invocation; nothing persists between runs
  — the effective cap is always readable off the crontab line via
  `submissions status`.

### `check_inputs`

Pre-flight check that a campaign's input files are readable before jobs
launch. Reads the frozen input list from the cnf tarball and verifies
each group at its real read location: resilient pileup (`tbs.auxin`) is
present and byte-size-matches SAM; tape/persistent inputs (`tbs.inputs`)
are staged (not `NEARLINE`). Read-only — it never remediates; a
`NEARLINE` tape input is reported with the `/prestage <dataset>` command
to run. Exits 0 when every input is readable, 2 when any is missing,
truncated, or unstaged.

```bash
check_inputs cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar
check_inputs --inloc resilient cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar
```

Flags: `--inloc LOC` (input location the jobs read from, default
`resilient` — the mixing default), and one or more positional
`cnf.*.tar` tarballs. Needs no mu2epro — it is a status check, safe to
run as yourself. `json2jobdef --enqueue` runs this same check
automatically as a gate — and, for a code-mode entry, also re-verifies
the code tarball's digest against the cnf's `code_ref` — so a campaign
is never created with unreadable inputs or a code mismatch; run it by
hand before launching, or when a monthly resilient purge is suspected
mid-campaign (the enqueue gate only fires at campaign creation, not per
slice).

### `copy_to_stash`

Copy a dataset into stash (CVMFS-readable) or resilient dCache:

```bash
copy_to_stash --dataset dts.mu2e.MuBeamFlashCat.Run1Ban.art --dest resilient
copy_to_stash --dataset dts.mu2e.CeEndpoint.MDC2025ac.art --source disk --limit 10 --dry-run
copy_to_stash --dataset dts.mu2e.MuBeamFlashCat.Run1Ban.art --dest resilient --skip-existing
copy_to_stash --list dts.mu2e.CeEndpoint.MDC2025ac.art
```

Flags: `--dataset`, `--dest {stash,resilient}` (default `stash`),
`--source {disk,tape}` (default `disk`), `--limit N`, `--dry-run`,
`--list DATASET`, `--quiet`, `--skip-existing`. Writing under resilient
requires production (mu2epro) permissions for new dsconf directories.

`--skip-existing` resumes a partial staging: files already at the
destination with the SAM-recorded size are left alone, so a re-run
neither re-copies them nor opens a good file for a truncating write.

### `runlocal`

Run cnf jobs on the current node, several at a time. Outputs stay on
local disk — no pushOutput, no SAM declare, no manifest:

```bash
# Smoke three indices before submitting, 10 events each
runlocal --jobdef cnf.mu2e.STMBeamToVDTarget.MDC2025au.0.tar \
         --inloc tape --first 0 --num 3 -j 3 --nevts 10 \
         --workdir /exp/mu2e/data/users/$USER/localrun

# Produce full-length output for indices 100..107, four at a time
runlocal --jobdef /path/to/cnf.mu2e.CeEndpoint.MDC2025au.0.tar \
         --inloc tape --first 100 --num 8 -j 4

# Rerun exactly the indices a grid pass lost
runlocal --jobdef /path/to/cnf.mu2e.CeEndpoint.MDC2025au.0.tar \
         --inloc tape --indices 0,3,7-9 -j 3

# Smoke a code-mode cnf: unpack the build once, run three indices against it
runlocal --jobdef cnf.mu2e.Custom.MDC2025ac.0.tar \
         --code /exp/mu2e/data/users/$USER/code_tarballs/Code.tar.bz2 \
         --inloc tape --first 0 --num 3 -j 3 --nevts 10
```

Flags: `--jobdef` (required; a path, or a SAM name to fetch once),
`--inloc` (default `tape`), `--first` / `--num` (default `0` / `1`),
`--indices SPEC`, `-j/--parallel` (default 4), `--workdir` (default
`.`), `--nevts` (default `-1` = whatever the FCL says),
`--mu2e-options`, `--copy-input`, `--code TARBALL` (a `muse tarball`
build to run against instead of the cnf's own `/cvmfs` setup, unpacked
once into `<workdir>/code`).

Job prep is the worker's own `process_jobdef`, so a local run exercises
the same tarball fetch, inloc handling and `--copy-input` staging the
grid will — only the push tail is missing. Each job runs as a child
process in `<workdir>/job_<index>/` holding its FCL, art outputs, art
log and `stdout.log`; the separate directories are required, because
`process_jobdef` works in cwd and its copy-input branch runs `mkdir
indir; mv *.art indir/`. A `--code` unpack happens once for the whole
`runlocal` invocation, before any job starts; each spawned child then
takes the already-unpacked tree by its own internal `--code-root` flag
rather than re-extracting several GB per job — that flag is not meant
to be passed by hand.

`--first`/`--num` are cnf indices directly — `baseSeed = 1 + index` and
`firstSubRun = index`, with no `firstjob` second index space to confuse
them with. `--indices` names those same indices one at a time instead
of a window: a comma-separated list of `N` and inclusive `A-B` ranges
(`0,3,7-9` = five jobs), for reruns of the exact indices a grid pass
lost, which are rarely contiguous. The two forms are alternatives —
`--indices` together with `--first`/`--num` is refused rather than
silently clipped to the window — and a malformed spec is rejected
before any job starts, since a typo there would quietly run the wrong
jobs. A failing index does not stop the others; the summary lists
every job's exit code and prints a paste-ready rerun command for each
failure, and the process exits 1 if any job failed. Four concurrent
mu2e processes is roughly 10 GB resident — the driver prints that
arithmetic for the `-j` you chose.

### `install_prodtools.sh` / `submissions_cron`

Operations scripts. `install_prodtools.sh` installs a versioned prodtools
release on CVMFS from a GitHub tag — run it directly on
`cvmfsmu2e@oasiscfs.fnal.gov`; `-n` is a dry run and `-t [DIR]` installs
into a local writable dir instead of touching CVMFS.
`submissions_cron` sets up a quiet Mu2e environment, checks for a valid
bearer token (report-only — it never fetches or refreshes one), then runs
`submissions run` (the per-DB lock is taken inside `run` itself, not by
the cron wrapper) for mu2epro's crontab, appending output to a
`submissions-YYYYMMDD.log` beside the ledger DB. One tick drives the
recovery pass, the sliced-campaign top-up, and the draining tick. Not
installed into any crontab by this repo — that is a one-time operator
step (section 11 `submissions`, wiki page
`2026-07-18-direct-recovery-loop`).

## 12. Troubleshooting

- `Missing required field: <name>` — the JSON entry lacks one of
  `fcl`, `dsconf`, `outloc`.
- `Exactly one of 'simjob_setup' and 'code' is required` — the entry
  set both keys, or neither; a cnf is built from a `/cvmfs` Musing
  setup script or a `muse tarball` code build, never both, never
  neither.
- `Please specify either --desc AND --dsconf, --dsconf only, or --index only`
  — json2jobdef entry selection is exactly one of those three forms.
- `json2jobdef: --prod requires --enqueue (otherwise a bare --prod
  pushes the cnf to SAM and registers no campaign -- a silent no-op)`
  — add `--enqueue`. There is no `--jobdefs` alternative any more.
- `json2jobdef: --enqueue requires --prod (a campaign needs the cnf in
  SAM)` — `--enqueue` resolves the tarball from SAM, so the cnf must
  have been pushed first.
- `json2jobdef: --slice-size requires --enqueue` — `--slice-size` only
  has meaning for the campaign `--enqueue` registers.
- `json2jobdef: inloc must be one of tape, disk, scratch, resilient,
  stash, none or 'dir:/<absolute path>', got '<value>'` — a config
  typo, refused before the cnf is built (same validator fires on
  `submissions set-entry`, prefixed `submissions:` there instead).
  A misspelled `inloc` does NOT fail at runtime — it silently falls
  through to SAM — which is why it is refused at the boundary.
- `code must be an absolute path, got '<value>'` — the `code` entry
  key (or `submissions set-entry ... code ...`) needs an absolute
  path: the submit host and the local runner resolve it from different
  working directories, so a relative path would silently mean
  different files to each.
- `code tarball is not readable: <path>` / `code tarball is not a
  bzip2-compressed tar archive: <path> (<reason>)` / `code tarball has
  no Code/setup.sh: <path> — build it with muse tarball` —
  `json2jobdef`/`jobdef --code` validate the code tarball before
  building anything, so a broken build costs one command instead of a
  thousand grid jobs. The last one is what a hand-tarred Muse work
  directory produces — only `muse tarball` writes `Code/setup.sh`.
- `json2jobdef: code tarball does not match the cnf — no campaign
  created` — the enqueue gate re-hashes the entry's `code` tarball and
  it no longer matches the cnf's `code_ref`; the tarball was rebuilt or
  replaced since the cnf was made. Rebuild the cnf, or point the entry
  back at the original tarball. The same gate also refuses when the
  tarball named by the entry no longer exists at all, or when the
  entry and the cnf disagree about code mode (one has `code`/`code_ref`
  and the other doesn't) — both are reasons to keep a `--prod` code
  tarball on a durable path (section 3) rather than scratch.
- ``json2jobdef: outloc['<pattern>'] must be one of tape, disk, scratch,
  outstage, got '<value>'`` — a misspelled output location, refused
  before the cnf is built. Unlike a bad `inloc`, a bad `outloc` would
  have reached pushOutput on the worker and failed there, after the job
  had already run.
- `json2jobdef: outloc must be a dictionary of dataset pattern ->
  location, got '<value>'` — `outloc` is a map, e.g.
  `{"*.art": "disk"}`, not a bare string.
- `json2jobdef: outstage outputs are not declared to SAM, so campaign
  verification cannot see them and every slice would recover forever. An
  outstage entry cannot be enqueued — submit it by hand.` — drop
  `--enqueue` (and `--prod`) for an outstage entry, or change `outloc`
  to a declared location if you did want a campaign.
- `<N> of <M> entries were SKIPPED and no campaign exists for them`
  (exit 2) — a bulk `--dsconf` run dropped entries. The listed ones
  need fixing and re-running individually with `--desc --dsconf`; the
  others are already done.
- `njobs=N exceeds the M jobs supported by the input file list` — the
  declared `njobs` is larger than `ceil(nfiles / merge_factor)`; fix
  `njobs` or the input selection.
- `Error: input_pattern requires generic_tarball: true (a draining entry
  has no fixed job count)` — a draining config must also set
  `"generic_tarball": true`; an entry cannot claim both an input
  pattern and a fixed index window.
- `outputs dataset glob '<glob>' matches ...` — a draining entry's output
  glob also matches its input pattern; make the glob tier-specific
  (`mcs.*.art`, never `*.art`).
- `contains unsubstituted placeholder` (from jobfcl / the build-time
  guard) — an `outputs.*.fileName` still carries a literal
  `description`/`owner`/`version`/`sequencer` token after substitution;
  add an explicit per-output `fcl_overrides` entry (typical for suffixed
  outputs like `{desc}-CH`).
- `window [F, F+M) exceeds cnf capacity N` — a `firstjob` window runs past
  a closed cnf's `tbs.njobs`. Only open-ended cnfs (capacity 0) accept any
  window.
- `--indices takes absolute cnf indices and cannot be combined with a
  windowed entry (firstjob=F); drop firstjob ...` — drop `firstjob` from
  the JSON config entry before recovering with `--indices`; the values
  are already absolute.
- `Could not locate file: <name>` — SAM has no location for an input
  file; check the entry's `inloc` against where the files actually live
  (`samweb locate-file <name>`).
- `Error: MU2EGRID_JOBDEF is not set. runmu2e runs only as the
  direct-backend worker ...` — `runmu2e` was invoked outside a
  direct-backend job. To run a cnf's jobs locally use `runlocal`
  (section 11); to smoke the worker itself, set the direct-mode
  environment by hand from a dry-run's ops JSON (section 7).
- `submissions run`: `row N: no cluster id recorded — cannot drain-check;
  update the row manually` — the ledger row has no cluster to query;
  `submissions run` will not guess one.
- `submissions run`: `row N: HELD jobs in cluster C — human decision
  needed (release or rm); loop will not act` — the loop never releases or
  removes held jobs; resolve with `condor_release`/`condor_rm` (or
  `jobsub_rm`) yourself, then re-run `submissions run`.
- `another submissions run holds <path>/submissions.lock — exiting` —
  an overlapping mutating invocation (manual `run`/`resubmit`/`pause`/
  `resume`/`cancel` racing the cron, or two cron ticks overlapping); let
  the first one finish, then retry.
- `submissions: no ledger row <N> in <db>` — `resubmit` was given a
  row id that doesn't exist in the target DB; check `submissions
  status` (or pass `--db`/`--mine` if you meant a different ledger).
- `submissions: refusing — row <N> (state=...) already covers part of
  this selection for <tarball>` — `resubmit`'s selection overlaps an
  unsettled row for the same tarball. Check `jobsub_q` yourself, then
  `submissions reconcile <N>` if the window is genuinely free — never
  resubmit past this without checking, since deterministic payloads mean
  live work gets duplicated, not just wasted.
- `submissions: row <N> is a draining (file-keyed) row — use --files,
  not --indices` / `submissions: row <N> is an index row — use
  --indices, not --files` — `resubmit`'s selector must match the row's
  kind.
- `active campaign N already exists for <tarball>` / `paused campaign N
  already exists for <tarball>` — `--enqueue` refuses a second
  active-or-paused campaign for the same tarball. Use `submissions
  cancel <ID>` on the existing one, ONLY — do not pause it and then
  enqueue a replacement; a paused campaign still owns its index space,
  so a paused-then-enqueued pair would double-feed the same indices,
  and the guard refuses a paused tarball for exactly that reason. After
  `submissions cancel <ID>`, the new campaign's cursor starts at 0 with
  no memory of what the cancelled one already fed — check `submissions
  status` (or the ledger directly) for that tarball before
  re-enqueueing, so you don't resubmit indices already covered.
- `campaign N: ledger row R already covers indices in this slice — PAUSED
  (crash-window suspected; check jobsub_q, then submissions reconcile R
  and submissions resume N)` — top-up found a ledger row (`R`) whose
  indices already fall inside the campaign's next slice window, meaning
  a prior submission likely succeeded but its cursor advance or ledger
  write was lost to a crash. Check `jobsub_q` yourself to confirm the
  window is genuinely free (a `jobsub_submit` that exited non-zero can
  still have created a cluster), then `submissions reconcile R` to close
  the blocking row and `submissions resume N` to restart the campaign.
  Do not resume without reconciling first — the same row keeps
  overlapping and the very next tick re-pauses the campaign. If row `R`
  is not in a reconcilable state (`failed`/`submitting`), the message
  instead says to reconcile the campaign cursor by hand before
  resuming.
- `MU2E_MAX_QUEUED is not an integer: '<value>'` — the env var must
  parse as an int; unset it or fix the value, or pass `--max-queued`
  directly to override it for one run.
- `latestDatasets: --superseded cannot be combined with --emit or
  --skip-produced` — `--superseded` is a lister-mode-only listing; drop
  it to use `--emit`/`--skip-produced`, or drop those to list superseded
  datasets.
- `latestDatasets: --latest-by time: SAM has no creation date for:
  <names>` — a contended description (2+ dsconf versions) has a member
  with no SAM definition creation date; `--latest-by time` fails loud
  rather than silently falling back to dsconf order, which would answer
  a time-ordering question with a lexicographic result.
