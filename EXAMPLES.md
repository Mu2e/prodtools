# Mu2e Production Tools — Usage Examples

Python-based tools for building, running, and monitoring Mu2e production
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

`muse setup ops` provides Python 3, `samweb`, and `fhicl-get`. `muse setup
SimJob <tag>` is optional for most tools; only `muse setup ops` is
required. Building job definitions (`json2jobdef`, `jobdef`) needs an
Offline environment for `fhicl-get`, so source the SimJob musing that the
entry's `simjob_setup` names.

Tools that read the POMS SQLite database (`pomsMonitor`,
`listNewDatasets --completeness`) additionally need SQLAlchemy:

```bash
source /cvmfs/mu2e.opensciencegrid.org/bin/pyenv.sh ana
```

## 2. Overview

Core production tools:

- `json2jobdef` — build cnf jobdef tarballs from JSON configs (recommended path)
- `jobdef` — build a single jobdef directly from CLI flags
- `jobfcl` — generate the per-index FCL from a jobdef tarball
- `fcldump` — resolve a dataset/target to its producing cnf and dump the FCL
- `runmu2e` — worker entry point: FCL generation, `mu2e` execution, pushOutput
- `submit_map` — submit all entries of a POMS-map JSON to the grid (single-backend direct)
- `mkrecovery` — find job indices whose outputs are missing from SAM
- `submissions` — status/run/pause/resume/cancel CLI for the direct-submission ledger (verify-and-resubmit recovery + sliced-campaign top-up)

Analysis / diagnostic tools:

- `jobquery` — inspect a cnf tarball (njobs, inputs, outputs, setup)
- `famtree` — dataset ancestry as a Mermaid diagram
- `logparser` — aggregate metrics from production log files
- `genFilterEff` — filter efficiencies in Proditions table format
- `datasetFileList` — physical file paths for a dataset or SAM definition
- `listNewDatasets` — recently produced datasets, with completeness
- `latestDatasets` — latest dsconf per description; chain-emit configs
- `pomsMonitor` — campaign status from the POMS DB
- `copy_to_stash` — copy a dataset into stash (CVMFS) or resilient dCache

## 3. Creating Job Definitions (`json2jobdef`, `jobdef`)

### JSON-based (recommended)

```bash
# One entry, selected by desc + dsconf
json2jobdef --json data/Run1B/stage1.json --desc PiBeam --dsconf Run1Bah

# Bulk: every entry at a dsconf
json2jobdef --json data/Run1B/mix.json --dsconf Run1Ban_best_v1_5-000

# By index into the flattened entry expansion
json2jobdef --json data/Run1B/primary_muon.json --index 0

# Production push: registers the cnf in SAM and refreshes the index definition
json2jobdef --json data/mdc2025/evntuple.json --desc CosmicSignalOffSpillTriggered-LH \
    --dsconf MDC2025-003 --prod \
    --jobdefs /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-032.json
```

Flags: `--json` (required), `--desc`, `--dsconf`, `--index`, `--pushout`,
`--prod`, `--jobdefs FILE`, `--extend`, `--ignore-empty`,
`--event-count-positive`, `--no-cleanup`, `--verbose`.

Notes:

- `--index N` indexes the *flattened* (entry × list-field) expansion, not
  the JSON array position. Prefer `--dsconf` (bulk) or `--desc --dsconf`.
- `--prod` implies `--pushout` and creates the SAM index definitions after
  generation. Re-running `--prod` is idempotent — use it to finish a
  partially-failed push.
- `--extend` excludes input files already consumed by the previous version
  of the same jobdef and auto-increments the tarball version.
- List-valued fields expand combinatorially: an entry with two `dsconf`
  values and three `desc` values yields six jobs.

Required JSON fields per entry: `simjob_setup`, `fcl`, `dsconf`, `outloc`.
`desc` is derived from `input_data` when omitted; `owner` defaults to the
current user (mapped to `mu2e` for mu2epro); `inloc` defaults to `none`;
`njobs: -1` means "derive from the input file list".

Stage-1 (generator) entry:

```json
{
  "desc": "PiBeam",
  "dsconf": "Run1Bah",
  "fcl": "Production/JobConfig/beam/POT_infinitepion.fcl",
  "njobs": 5000,
  "events": 200000,
  "run": 1450,
  "outloc": { "*.art": "disk" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Bah/setup.sh",
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
(`{"dts.mu2e.NoPrimary.Run1Ban.art": 10}` = 10 input files per job).
The dict value form accepts `count`/`merge_factor`, plus `random` and
`max_nfiles` (section 4), or `split_lines` for chunking a local text file.

`inloc` accepts `disk`, `tape`, `scratch`, `resilient`, `stash`, `none`,
or `dir:<path>` (locally-mounted FS, e.g. cvmfs). There is no `auto`.
`resilient` reads via xrootd, `stash` reads via CVMFS, and `dir:` reads
via direct POSIX (the `file` protocol is forced).

Other consumed keys: `sequencer_from_index` (default true: output
sequencer = run + job index; set `false` to inherit the input file's
sequencer) and `generic_tarball` (build a reusable direct-input cnf with
`{desc}` deferred to runtime).

Optional per-entry resource requests — `"memory"`, `"disk"`,
`"expected_lifetime"` (jobsub-format strings, e.g. `"4000MB"`,
`"50GB"`, `"48h"`):

```json
{
  "desc": "MuStopPileup",
  "dsconf": "Run1Ban",
  "fcl": "Production/JobConfig/pileup/MuStopPileup.fcl",
  "njobs": 5000,
  "memory": "4000MB",
  "outloc": { "*.art": "tape" },
  "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Ban/setup.sh",
  "owner": "mu2e"
}
```

`json2jobdef` copies any of the three keys present in the config into
the map entry verbatim (`append_jobdef`). `submit_map` (section 11)
reads them back at submission time: a CLI flag always overrides the
entry key, which overrides the built-in default (`2000MB` / `30GB` /
`24h`).

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

Flags: `--setup` or `--code` (one required), `--dsconf`, `--dsowner`
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
    --source dts.mu2e.NoPrimary.Run1Ban.001470_00000000.art
```

Flags: `--jobdef` (required), one of `--index N` / `--target FILE` /
`--source FILE`, `--default-location` (default `tape`),
`--default-protocol` (default `file`; use `root` for xrootd URLs).

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
fcldump --local-jobdef cnf.mu2e.reco.Run1Ban_best_v1_4-000.0.tar \
    --fname mcs.mu2e.NoPrimaryMix1BBTriggered-KL.Run1Ban_best_v1_4-000.001470_00000042.art

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
  "input_data": [ { "dts.mu2e.NoPrimary.Run1Ban.art": 10 } ],
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

- Each pileup dataset maps to its mixer automatically by name pattern:
  `*MuBeam*` → `MuBeamFlashMixer`, `*EleBeam*` → `EleBeamFlashMixer`,
  `*Neutral*` → `NeutralsFlashMixer`, `*MuStop*` → `MuStopPileupMixer`.
  The value is the per-job file count for that mixer.
- `pbeam` selects the intensity include (`Mix1BB` → `mixing/OneBB.fcl`,
  `Mix2BB` → `TwoBB.fcl`, `MixLow` → `LowIntensity.fcl`, `MixSeq` →
  `NoPrimaryPBISequence.fcl`, `MixFlat` → `FlatPBI.fcl`) and is appended
  to the desc (`NoPrimary` → `NoPrimaryMix1BB`).
- `MaxEventsToSkip` per mixer is computed from the first dataset's event
  count and written before `fcl_overrides`, so overrides can still adjust
  it.
- `input_data` merge factor > 1 is supported (e.g. 10 primaries per job).

```bash
json2jobdef --json data/Run1B/mix.json --dsconf Run1Ban_best_v1_5-000
```

## 7. Production Execution (`runmu2e`)

Grid workers run `runmu2e`, which generates the FCL for this job's index,
runs `mu2e`, and pushes outputs. In POMS mode the job index arrives in the
`fname` environment variable:

```bash
fname=etc.mu2e.index.000.0000042.txt runmu2e --jobdesc jobdefs_list.json
fname=etc.mu2e.index.000.0000042.txt runmu2e --jobdesc jobdefs_list.json --dry-run --nevts 10
```

Flags: `--jobdesc FILE` (required in POMS mode), `--dry-run` (print
pushOutput commands without running them), `--nevts N` (default -1 = all),
`--mu2e-options "..."` (extra `mu2e` arguments), `--copy-input` (stage
inputs locally with `mdh` instead of streaming).

- The `etc.mu2e.index.000.NNNNNNN.txt` filename encodes the job index:
  the seventh-field `NNNNNNN` (the sequencer) is the global job index,
  zero-padded to 7 digits. `mkrecovery` writes these as
  `etc.mu2e.index.000.{idx:07d}.txt`. The `000` field is a fixed
  description placeholder, not the index.
- The global index is mapped across the entries of the jobdesc JSON in
  order; each entry consumes `njobs` indices. Within an entry,
  `local = global - cumulative + firstjob`.
- Direct mode (no `fname`): `submit_map` sets `MU2EGRID_JOBDEF` and
  related environment variables; workers derive the index from
  `$PROCESS` via the ops JSON's `jobs` lookup table. See section 11,
  `submit_map`.

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

### `pomsMonitor`

Campaign status from the POMS-map SQLite DB (default: `poms_data.db` at
the repo root; needs SQLAlchemy — see section 1).

```bash
pomsMonitor --campaign MDC2025ap --outputs --incomplete
pomsMonitor --build-db --list
pomsMonitor --needs-processing
```

Key flags: `--pattern`, `--db`, `--build-db`, `--list`, `--campaign`,
`--outputs`, `--complete`, `--incomplete`, `--datasets-only`, `--sort`,
`--since DURATION`, `--needs-processing`, `--ignore DATASET`
(`--ignore-reason`), `--unignore DATASET`, `--list-ignored`,
`--uniformity` (`--target`, `--round`).

The static production dashboard is rendered from the same DB by
`update_pomsmonitor_web` (see the ops scripts note below).

### `famtree`

Dataset ancestry as a Mermaid diagram (auto-excludes `etc*.txt` files):

```bash
famtree dts.mu2e.MuStopPileupCat.Run1Ban.art
famtree mcs.mu2e.CeEndpointMix1BBTriggered.Run1Ban_best_v1_5-000.art --stats --max-files 20
```

Flags: `--png`, `--svg` (require `mmdc`), `--stats`, `--max-files N`.

### `logparser`

Aggregate metrics (CPU, memory, throughput) from production logs:

```bash
logparser log.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.log
logparser log.mu2e.PiBeam.Run1Bah.log -n 50
```

Flags: `-n/--max-logs N` (default: all logs in the dataset).

### `genFilterEff`

Filter efficiencies in Proditions format (`TABLE SimEfficiencies2`):

```bash
genFilterEff sim.mu2e.PiTargetStops.Run1Bah.art --out SimEfficiencies2_Run1B.txt
```

Flags: `--out` (required), `--firstLine`, `--writeFullDatasetName`,
`--chunksize N`, `--maxFilesToProcess N`, `--verbosity N`.

### `datasetFileList`

Physical /pnfs paths for a dataset or SAM definition:

```bash
datasetFileList dts.mu2e.NoPrimary.Run1Ban.art
datasetFileList dts.mu2e.NoPrimary.Run1Ban.art --tape --basename
datasetFileList idsrecovery_xyz --defname
```

Flags: `--basename`, `--disk`, `--tape`, `--scratch`, `--defname`.

### `listNewDatasets`

Recently produced datasets, optionally with completeness against the
POMS DB:

```bash
listNewDatasets --days 1 --completeness
listNewDatasets --query "dh.dataset like '%.Run1Ban_best_v1_5-000.%'" --no-rebuild
```

Flags: `--filetype`, `--days N`, `--user`, `--size`, `--query`,
`--completeness`, `--no-rebuild`, `--db`, `--poms-dir`.

### `latestDatasets`

Latest dsconf per description; also emits ready-to-run json2jobdef
configs for the next chain stage from `templates/<campaign>/<stage>.json`:

```bash
latestDatasets --defname 'dig.mu2e.%.MDC2025%.art' --show-count
latestDatasets --emit reco --campaign MDC2025ap --skip-produced
```

Flags: `--defname`, `--user`, `--stdin`, `--show-count`,
`--emit {digi,reco,ntuple,mix}`, `--campaign`, `--templates-dir`,
`--dsconf`, `--complete-only`, `--skip-produced`, `-v/--verbose`.

### `mkrecovery`

Find job indices whose outputs are missing from SAM. Expected filenames
come from the cnf itself, diffed against the dataset in SAM — so it is
robust to naming and multi-output stages in a way a filename scan is not.

```bash
# Whole POMS-map JSON — creates a recovery SAM definition
mkrecovery /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-032.json --jobdesc

# Single tarball, windowed entry
mkrecovery cnf.mu2e.MuStopPileup.Run1Ban-001.0.tar \
    --dataset dts.mu2e.MuStopPileup.Run1Ban-001.art --njobs 5000 --firstjob 15000

# Print the missing cnf indices instead (read-only — makes no SAM writes)
mkrecovery /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-032.json \
    --jobdesc --print-indices > gaps.txt
```

Flags: `input` (tarball path or jobdesc JSON), `--dataset` and `--njobs`
(both required in single-tarball mode), `--firstjob F` (cnf-index window
start, default 0), `--jobdesc`, `--print-indices`.

Two index spaces — pick the one your submission path consumes:

- Default writes `etc.mu2e.index.000.{idx:07d}.txt` entries into a
  `<name>-recovery` SAM definition carrying **global** indices (cumulative
  across jobdesc entries), for the POMS `fname` path (section 7).
- `--print-indices` prints **absolute cnf** indices (`firstjob + relative`),
  one per line under a `# <tarball>` header, for `submit_map
  --indices-file`. Diagnostics go to stderr so stdout stays pipeable.

This is also the machinery `submissions run` (below) reuses internally
to SAM-verify a ledger row — `mkrecovery` itself is unchanged and
remains POMS's own recovery path (POMS-launched entries are never in
the ledger; `submit_map` is single-backend direct).

### `jobquery`

Inspect a cnf tarball:

```bash
jobquery --njobs cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar
jobquery --input-datasets --output-datasets cnf.mu2e.NoPrimaryMix1BB.Run1Ban_best_v1_5-000.0.tar
```

Flags: `--jobname`, `--njobs`, `--input-datasets`, `--input-files`,
`--output-datasets`, `--output-files DATASET[:size]`, `--codesize`,
`--extract-code`, `--setup`.

`--njobs` reports the cnf's own capacity from `tbs.njobs`; `0` means
open-ended (the POMS-map entry is authoritative).

### `submit_map`

Submit all (or selected) entries of a POMS-map JSON via the direct
jobsub backend — single-backend, no `--backend` flag. No `mu2ejobsub`
involved: `submit_map` builds the `jobsub_submit` argv itself, ships
the repo's `utils/` + `bin/` as a dropbox tarball, and runs per-job
`pushOutput` on the worker:

```bash
submit_map --map MDC2025-032.json --dry-run
submit_map --map MDC2025-032.json --entry 3
submit_map --map MDC2025-032.json --first 0 --num 10

# Recovery: exactly these cnf indices, one cluster, one job per index
submit_map --map Run1Ban-pileuprecover.json \
    --indices-file gaps.txt --expected-lifetime 48h --memory 4000MB

# Register a sliced campaign — submits nothing; `submissions run` feeds it
submit_map --map MDC2025-032.json \
    --enqueue --slice-size 2000
```

Flags: `--map` (required), `--entry N`, `--first N` / `--num M`,
`--indices K1,K2,...` / `--indices-file FILE`, `--ledger-db PATH`
(default `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, env
`MU2E_SUBMISSION_DB`), `--ledger-parent ID`, `--no-ledger`, `--enqueue`
(register a sliced campaign instead of submitting), `--slice-size N`
(default 1000, only meaningful with `--enqueue`), `--wftop`,
`--wfproject`, `--role`, `--disk` (default `30GB`), `--memory` (default
`2000MB`), `--expected-lifetime` (default `24h`), `--prodtools-tar`,
`--dry-run`, `--verbose`.

Entries `submit_map` cannot submit — `template`/`direct_input`/`g4bl`
modes, and HPC — go through POMS campaigns or the upstream
`mu2ejobsub`/`mu2eg4bl` CLIs directly; `submit_map` never touches those.

Every successful submission (one that produced a cluster ID) is
recorded in the submission ledger (sqlite3, `--ledger-db`) — the
tarball, a verbatim entry snapshot, and the ABSOLUTE cnf indices
submitted. `submissions run` (below) reads this ledger to drain-check,
SAM-verify, and resubmit missing indices. `--ledger-parent ID` is set
automatically by `submissions run` when it resubmits (chains the
attempt count for that recovery lineage); `--no-ledger` opts an ad-hoc
or test submission out of the ledger entirely — the recovery loop then
never sees it. Entries launched via POMS or the upstream
`mu2ejobsub`/`mu2eg4bl` CLIs never touch this ledger — they never go
through `submit_map` at all.

`--enqueue` combined with `--no-ledger` is refused (`submit_map:
--enqueue registers a campaign in the ledger DB; --no-ledger
contradicts it`) — a campaign has nowhere to track its cursor without
the ledger.

Every submission **attempt** — manual, cron-fed slice, or recovery
resubmit, success or failure — also appends a block to
`submit-YYYYMMDD.log` beside the ledger DB (UTC day, plain appends, no
rotation): timestamp, user, map, tarball, requested range or indices,
outcome, and the raw `jobsub_submit` output. `--no-ledger` skips this
log too; `--dry-run` and `--enqueue` write nothing (nothing was
submitted).

Resource requests (`--disk`/`--memory`/`--expected-lifetime`) resolve as
CLI flag > entry key (section 3: `"memory"`/`"disk"`/
`"expected_lifetime"`) > built-in default. Whatever resolves is what
gets recorded in the ledger/campaign snapshot, so a `submissions run`
resubmit or a cron-fed slice reruns with the same resources the
original jobs had — a CLI `--memory 4000MB` no longer downgrades to the
2000MB built-in default on resubmit.

Sliced campaigns (`--enqueue`): snapshots the selected entries (all, or
`--entry N`) into the campaigns table at cursor 0 and submits nothing.
`--slice-size` is frozen into the campaign row. Mutually exclusive with
`--first`/`--num`/`--indices`/`--indices-file`. An entry with no fixed
`njobs`, or `njobs < 1`, (a `generic_tarball` entry, or `njobs: 0`)
cannot be enqueued — a campaign needs a positive job count to slice.
Enqueueing a tarball that already has an *active or paused* campaign is
a hard error — a paused campaign still owns its index space, so pausing
does not free the tarball for a new campaign; only `submissions cancel
<ID>` does (see Troubleshooting). Before a campaign row is written,
`--enqueue` runs the `check_inputs` pre-flight (below) on the entry's
tarball: if a resilient pileup file is missing/truncated or a tape
input is not staged, the entry is refused (exit 2) with a grouped
report and no campaign is created — fix the inputs (e.g. `/prestage`)
and re-run. `submissions run`'s top-up phase
(below) then feeds whole slices to the grid on its own, hourly, until
the campaign is fully submitted. Before every slice, top-up also checks
the ledger for indices that already cover the slice's absolute window
(any ledger state counts as proof of submission) — an overlap means a
crash likely happened between a prior submission and its own
ledger/cursor write, so the campaign is paused with a crash-window note
instead of resubmitting. A campaign whose cursor already equals its
`njobs` but is still `active` (the same class of crash, between the
last slice's cursor advance and its completion write) self-heals to
`complete` on the next tick rather than staying stuck forever.

Statistics expansion (`firstjob` windows):

- The per-job seed is `baseSeed = 1 + cnf index` (flat — no version, run,
  or dsconf term). To extend a dataset's statistics, reuse the existing
  tarball at fresh indices via a window: a POMS-map entry with
  `"firstjob": F, "njobs": M` runs cnf indices `[F, F+M)`, giving fresh
  seeds `F+1..` and fresh sequencers.
- Do NOT bump `version`/`run` for a same-input expansion — that restarts
  the cnf index at 0 and duplicates physics.
- Only open-ended cnfs (no `tbs.njobs` cap) can be windowed past their
  original count; closed cnfs are capacity-checked.

Job selection within an entry:

- `--first`/`--num` carve a contiguous slice, **entry-relative**: the
  entry's `firstjob` is added worker-side, so `--first 944` on a
  `firstjob=15000` entry runs cnf index 15944. Sliced campaigns submit
  their slices this way, so a windowed entry's `firstjob` survives
  untouched in the campaign snapshot.
- `--indices` takes **absolute cnf indices** for a scattered recovery set
  that no contiguous range can express, and requires a non-windowed entry.
  It submits one cluster with one job per index.

### `submissions`

Direct-submission subsystem CLI: read-only status (default verb — no
verb needed), the hourly verify/resubmit/top-up tick (`run`), and
campaign management (`pause`/`resume`/`cancel`). Reads the submission
ledger (same sqlite3 DB `submit_map` writes to).

```bash
submissions                       # read-only ledger + campaigns + cap (any account)
submissions status                 # same, explicit form
submissions run --dry-run          # verify + top-up report, no submissions
submissions run                    # full pass (mu2epro; cron entry point)
submissions run --row 42 --max-attempts 5
submissions run --max-queued 5000  # override the top-up cap for this pass
submissions pause 7 --note "investigating OOM"
submissions resume 7               # paused -> active; preserves the pause note
submissions cancel 7               # close; already-submitted rows still recovered
```

Global flag: `--db PATH` (default: the submission-ledger path above,
env `MU2E_SUBMISSION_DB`), valid before the verb.

Verbs:

- `status` (the default when no verb is given) — print the ledger
  table, the campaigns table, and the resolved top-up queue cap, then
  exit. Read-only: takes no lock, makes no submissions.
- `run` — the tick: a recovery pass over active ledger rows (drain-check
  via `jobsub_q`; report and skip held jobs — the loop never runs
  `condor_rm`/`condor_release`; SAM-verify the row's cnf indices using
  the cnf's own expected output filenames, `mkrecovery`'s file-map
  machinery scoped to the row's indices; then close `complete`,
  resubmit the missing indices as a child row (`attempt`+1, via
  `submit_map`), or mark `exhausted` at the attempt cap), followed by
  campaign top-up (counts total mu2epro idle+running jobs via `jobsub_q
  --user mu2epro -af JobStatus`, then round-robins whole slices to
  active campaigns, oldest first, while `count + slice <= cap`; skipped
  entirely when there is no active campaign, and for `--row`). Flags:
  `--dry-run` (report would-* actions only; no submissions, no state
  changes; also takes no lock), `--row N` (process only this ledger row
  id, skips top-up), `--max-attempts N` (default 3; a row closes
  `exhausted` once its attempt count reaches this cap), `--max-queued N`
  (top-up cap for this pass; default: env `MU2E_MAX_QUEUED`, then
  `10000`).
- `pause CAMP_ID [--note TEXT]` — pause an active campaign (default
  note: `"operator pause"`).
- `resume CAMP_ID` — reactivate a paused campaign; the note recorded
  when it was paused is preserved, not cleared.
- `cancel CAMP_ID` — cancel a campaign; already-submitted ledger rows
  still get recovered normally.

- `status` and `run --dry-run` are the only read-only invocations —
  safe under any account, no lock, no grid writes.
- `run` (without `--dry-run`) and `pause`/`resume`/`cancel` all take the
  same per-DB lock (`submissions.lock` beside the DB); an overlapping
  mutating run exits with "another submissions run holds ... —
  exiting" instead of racing.
- `run` exits 2 when anything this pass needed human attention — a
  cron-visible "needs a look" signal — and 0 otherwise. The
  needs-attention set: a row with **held** jobs; a row that went (or,
  under `--dry-run`, would go) **exhausted** at the attempt cap; a
  **child-missing** row (a resubmit succeeded but no child ledger row
  was recorded); a campaign **paused** this tick by a submit failure or
  the crash-window overlap guard (or would be, under `--dry-run`); a
  **queue-count failure** (`jobsub_q` itself unreadable — top-up is
  skipped, not just under-counted); or a **lingering paused campaign**
  — any campaign still `paused` when `run` executes, not just the tick
  that paused it, so the signal repeats every tick until a human
  `resume`s or `cancel`s it. `status` never exits 2 — it is a display,
  not a monitor.
- Deterministic cnf payloads re-run identical events, so a systematic
  failure re-fails every attempt; `exhausted` is where a human takes
  over, not something blind retry fixes.
- POMS-launched entries never appear in the ledger and are out of scope
  by construction — `submit_map` is single-backend direct, so a
  POMS-launched job (or one submitted via the upstream `mu2ejobsub`/
  `mu2eg4bl` CLIs) simply never passes through it; POMS owns its own
  recovery via `mkrecovery`.
- Campaign states: `active` (loop feeds it) → `complete` (fully
  submitted; jobs may still be running — verification continues per
  ledger row), `paused` (submit failure, crash-window overlap, or
  `pause`; a human clears it with `resume` or `cancel`), or `cancelled`
  (`cancel`; already-submitted rows still get recovered).
- Cap resolution is `--max-queued` flag > `MU2E_MAX_QUEUED` env >
  `10000`, resolved once per invocation; nothing persists between runs
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
check_inputs cnf.mu2e.RMCPhaseSpace0NExternalMix1BB.MDC2025ar_best_v1_3.0.tar
check_inputs --inloc resilient cnf.mu2e.RMCPhaseSpace1NExternalMix1BB.MDC2025ar_best_v1_3.0.tar
```

Flags: `--inloc {resilient,...}` (input location the jobs read from,
default `resilient` — the mixing default), and one or more positional
`cnf.*.tar` tarballs. Needs no mu2epro — it is a status check, safe to
run as yourself. `submit_map --enqueue` runs this same check
automatically as a gate, so a campaign is never created with unreadable
inputs; run it by hand before launching, or when a monthly resilient
purge is suspected mid-campaign (the enqueue gate only fires at campaign
creation, not per slice).

### `copy_to_stash`

Copy a dataset into stash (CVMFS-readable) or resilient dCache:

```bash
copy_to_stash --dataset dts.mu2e.MuBeamFlashCat.Run1Ban.art --dest resilient
copy_to_stash --dataset dts.mu2e.CeEndpoint.Run1Bab.art --source disk --limit 10 --dry-run
copy_to_stash --list dts.mu2e.CeEndpoint.Run1Bab.art
```

Flags: `--dataset`, `--dest {stash,resilient}`, `--source {disk,tape}`,
`--limit N`, `--dry-run`, `--list DATASET`, `--quiet`. Writing under
resilient requires production (mu2epro) permissions for new dsconf
directories.

### `install_prodtools.sh` / `update_pomsmonitor_web` / `submissions_cron`

Operations scripts: `install_prodtools.sh` packages a versioned prodtools
release for cvmfs publication; `update_pomsmonitor_web` rebuilds the POMS
DB and regenerates the static dashboard site (the dashboard is a static
page — `web/pomsMonitor/render_static.py` stamps `monitor_static.html`
and builds `jobs.json` directly from the DB); `submissions_cron` sets up
a quiet Mu2e environment, checks for a valid bearer token (report-only —
it never fetches or refreshes one), then runs `submissions run` (the
per-DB lock is taken inside `run` itself, not by the cron wrapper) for
mu2epro's crontab, appending output to a `submissions-YYYYMMDD.log`
beside the ledger DB. Drives both the recovery pass and the
sliced-campaign top-up pass every tick. Not installed into any crontab
by this repo — that is a one-time operator step (section 11
`submissions`, wiki page `2026-07-18-direct-recovery-loop`).

## 12. Troubleshooting

- `Missing required field: <name>` — the JSON entry lacks one of
  `simjob_setup`, `fcl`, `dsconf`, `outloc`.
- `Please specify either --desc AND --dsconf, --dsconf only, or --index only`
  — json2jobdef entry selection is exactly one of those three forms.
- `njobs=N exceeds the M jobs supported by the input file list` — the
  declared `njobs` is larger than `ceil(nfiles / merge_factor)`; fix
  `njobs` or the input selection.
- `contains unsubstituted placeholder` (from jobfcl / the build-time
  guard) — an `outputs.*.fileName` still carries a literal
  `description`/`owner`/`version`/`sequencer` token after substitution;
  add an explicit per-output `fcl_overrides` entry (typical for suffixed
  outputs like `{desc}-CH`).
- `window [F, F+M) exceeds cnf capacity N` — a `firstjob` window runs past
  a closed cnf's `tbs.njobs`. Only open-ended cnfs (capacity 0) accept any
  window.
- `--first N --num M out of range for jobset size=S` — the carve falls
  outside the entry's window (`size` is the entry's `njobs` when windowed,
  else the cnf capacity).
- `--indices takes absolute cnf indices and cannot be combined with a
  windowed entry` — drop `firstjob` from the recovery map entry; `--indices`
  values are already absolute.
- `Could not locate file: <name>` — SAM has no location for an input
  file; check the entry's `inloc` against where the files actually live
  (`samweb locate-file <name>`).
- `error: SQLAlchemy not found. Run 'pyenv ana' after 'muse setup ops'.` —
  run `source /cvmfs/mu2e.opensciencegrid.org/bin/pyenv.sh ana` after
  `muse setup ops` (needed by pomsMonitor and listNewDatasets
  --completeness).
- `submissions run`: `row N: no full jobsub id recorded — cannot
  drain-check` — the ledger row's `jobsub_id` lacks a schedd
  (numeric-only cluster parse); update the row manually, `submissions
  run` will not guess a schedd.
- `submissions run`: `row N: HELD jobs ... human decision needed` — the
  loop never releases or removes held jobs; resolve with
  `condor_release`/`condor_rm` (or `jobsub_rm`) yourself, then re-run
  `submissions run`.
- `another submissions run holds <path>/submissions.lock — exiting` —
  an overlapping mutating invocation (manual `run`/`pause`/`resume`/
  `cancel` racing the cron, or two cron ticks overlapping); let the
  first one finish, then retry.
- `Error: --enqueue submits nothing — it cannot be combined with
  --first/--num/--indices` — enqueue and immediate submission are
  mutually exclusive; drop `--enqueue` to submit now, or drop the
  selection flags to register a campaign.
- `submit_map: --enqueue registers a campaign in the ledger DB;
  --no-ledger contradicts it` — a campaign has nowhere to track its
  cursor without the ledger; drop one of the two flags.
- `submit_map: entry N has no njobs (generic tarball) — a campaign
  needs a job count to slice` — `--enqueue` requires a fixed-`njobs`
  entry; `generic_tarball` entries have no pre-determined job count.
- `submit_map: entry N has njobs=0 — a campaign needs a positive job
  count` — `--enqueue` also refuses `njobs: 0` (and any non-positive
  value): a zero-job campaign cannot be sliced.
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
- `campaign N: ledger already covers indices in this slice — PAUSED
  (crash-window suspected...)` — top-up found ledger rows for this
  campaign's tarball whose indices already fall inside the next slice
  window, meaning a prior submission likely succeeded but its cursor
  advance or ledger write was lost to a crash. Reconcile manually:
  compare the ledger rows for the tarball (`submissions status` /
  `sqlite3`) against the campaign's `cursor`, adjust the cursor if
  needed, then `submissions resume <ID>`. Do not resume blind —
  resuming without reconciling can still double-submit.
- `MU2E_MAX_QUEUED is not an integer: '<value>'` — the env var must
  parse as an int; unset it or fix the value, or pass `--max-queued`
  directly to override it for one run.
