# Mu2e Production Tools — Usage Examples

Practical examples for the Python-based Mu2e production tools. Every
command here is a real invocation you can paste into a shell once your
Mu2e environment is set up.

## Quick Navigation

- [1. Environment Setup](#1-environment-setup)
- [2. Overview](#2-overview)
- [3. Creating Job Definitions](#3-creating-job-definitions)
- [4. Random Sampling in Input Data](#4-random-sampling-in-input-data)
- [5. FCL Generation](#5-fcl-generation)
- [6. Mixing Jobs](#6-mixing-jobs)
- [7. JSON Expansion](#7-json-expansion)
- [8. Production Execution](#8-production-execution)
- [9. Sequential vs. Pseudo-Random Auxiliary Input Selection](#9-sequential-vs-pseudo-random-auxiliary-input-selection)
- [10. FCL Overrides](#10-fcl-overrides)
- [11. Parity Tests](#11-parity-tests)
- [12. Additional Tools](#12-additional-tools)
- [13. Troubleshooting](#13-troubleshooting)

## 1. Environment Setup

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
```

`muse setup SimJob` is optional for most tools — only `muse setup ops`
is required. A few commands (parity tests, `json2jobdef`) need SimJob.

This exposes:

- `fhicl-get` — FHiCL parser
- `mu2ejobdef` — Perl reference (used by parity tests)
- `samweb_client` — Python SAM client
- `mdh` — Mu2e data handling

### Optional: add prodtools to PATH

```bash
source bin/setup.sh
```

Adds `prodtools/bin/` to `PATH` and `prodtools/` to `PYTHONPATH`, so you
can run `json2jobdef`, `fcldump`, `runmu2e`, etc. from anywhere. For
Run1B workflows that need local `fcl/` resolved by `MU2E_SEARCH_PATH`,
use `source bin/setup_run1b.sh` instead.

## 2. Overview

**Core production tools:**

- `json2jobdef` — create job definition tarballs from JSON configs
- `jobdef` — low-level job definition creation (direct flags)
- `jobfcl` — generate FCL for a specific index in a jobdef tarball
- `fcldump` — generate FCL from dataset name, target file, or local tarball
- `runmu2e` — execute production jobs from job definitions
- `jsonexpander` — expand template JSONs into parameter cross-products
- `jobquery` — inspect job parameter tarballs
- `mkidxdef` — create SAM index definitions from a jobdefs list

**Analysis and diagnostics:**

- `pomsMonitor` — analyze POMS job definitions via a persistent SQLite DB
- `pomsMonitorWeb` — Flask UI for the monitoring DB
- `famtree` — dataset parentage trees with Mermaid diagrams
- `logparser` — parse job performance metrics from log files
- `genFilterEff` — compute generation filter efficiency per dataset
- `datasetFileList` — list files in a dataset or SAM definition
- `listNewDatasets` — recently created datasets in SAM
- `mkrecovery` — build a recovery SAM definition for missing files
- `copy_to_stash` — copy a dataset into StashCache or resilient dCache
- `listMcsDefs` / `listRelatedDefs` — enumerate related mcs/dig/dts defs
- `plot_logs` — visualize log metrics merged with NERSC job counts

## 3. Creating Job Definitions

### A. JSON-based (recommended): `json2jobdef`

Stage-1 example (`data/Run1B/stage1.json`):

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
    "outloc": {"*.art": "disk"},
    "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ac/setup.sh",
    "owner": "mu2e"
}
```

```bash
# Single entry by index
json2jobdef --json data/Run1B/stage1.json --index 0

# By (desc, dsconf) pair
json2jobdef --json data/Run1B/stage1.json --desc POT_Run1_a --dsconf MDC2025ac

# All entries matching a dsconf
json2jobdef --json data/Run1B/stage1.json --dsconf MDC2025ac
```

**Outputs:**

- `cnf.<owner>.<desc>.<dsconf>.0.tar` — job definition tarball
- `cnf.<owner>.<desc>.<dsconf>.0.fcl` — test FCL for index 0
- `jobdefs_list.json` — list of generated jobdefs (use with `runmu2e`)

**Useful flags:**

- `--verbose` — print the underlying `mu2ejobdef` / `jobdef` command
- `--no-cleanup` — keep `inputs.txt`, `template.fcl`, `*Cat.txt`
- `--jobdefs <file>` — custom filename for the jobdefs list
- `--prod` — enable `pushout` and run `mkidxdef` after generation
- `--pushout` — register the tarball in SAM via `pushOutput`
- `--json-output` — emit structured JSON instead of human-readable text
- `--extend` — create a delta job definition excluding already-processed
  inputs; auto-increments the tarball version
- `--ignore-empty` — skip entries whose input datasets have no files
  instead of failing

### B. Resampler shape

Resamplers use a single `input_data` dataset with a merge factor and a
`resampler_name`:

```json
{
    "desc": "EleBeamFlash",
    "dsconf": "Run1Baa",
    "fcl": "Production/JobConfig/pileup/EleBeamResampler.fcl",
    "resampler_name": "beamResampler",
    "input_data": {"sim.mu2e.EleBeamCat.Run1Baa.art": 1},
    "njobs": 5000,
    "events": 1000000,
    "run": 1440,
    "inloc": "disk",
    "outloc": {"*.art": "tape"},
    "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Baa/setup.sh",
    "owner": "mu2e",
    "sequential_aux": true
}
```

### C. Low-level: `jobdef`

For full control or debugging, bypass JSON and call `jobdef` directly:

```bash
jobdef --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ac/setup.sh \
       --dsconf MDC2025ac --desc ExtractedCRY --dsowner mu2e \
       --run-number 1205 --events-per-job 500000 \
       --include Production/JobConfig/cosmic/ExtractedCRY.fcl
```

Key flags: `--setup` or `--code`, `--dsconf`, `--desc` or
`--auto-description`, `--dsowner`, `--embed <fcl>` or `--include <fcl>`,
`--run-number`, `--events-per-job`, `--inputs <file>`, `--merge-factor`,
`--auxinput <SPEC>`, `--samplinginput <SPEC>`.

Use `json2jobdef --verbose --json <cfg> --index <i>` to see the
underlying `jobdef` command for any JSON entry.

## 4. Random Sampling in Input Data

Deterministic pseudo-random file selection is available for resampler
and artcat jobs by giving `input_data` a dict with `count` and `random`:

```json
{
    "desc": "NeutralsFlashCat",
    "dsconf": "MDC2025ad",
    "sequencer_from_index": true,
    "fcl": "Production/JobConfig/common/artcat.fcl",
    "input_data": {
        "dts.mu2e.NeutralsFlash.MDC2025ac.art": {
            "count": 5000,
            "random": true
        }
    },
    "njobs": 1000,
    "inloc": "disk",
    "outloc": {"*.art": "tape"},
    "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ad/setup.sh",
    "owner": "mu2e"
}
```

The seed is derived from `(owner, desc, dsconf, dataset, count, njobs)`,
so the same inputs always produce the same file selection. Selected
files are written to `inputs.txt` (keeping `jobpars.json` small).

Non-random form (all files, repeated to reach the count):

```json
"input_data": {"sim.mu2e.PiTargetStops.MDC2025ac.art": 10}
```

## 5. FCL Generation

### A. `jobfcl` — generate FCL from a jobdef tarball

```bash
# By job index
jobfcl --jobdef cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar --index 4900

# Override default input location / protocol
jobfcl --jobdef cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar --index 0 \
       --default-location tape --default-protocol root

# By target output filename (jobfcl finds the right index)
jobfcl --jobdef cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar \
       --target dts.mu2e.NeutralsFlash.MDC2025ac.001430_00000428.art
```

Defaults: `--default-location tape`, `--default-protocol file`. Either
`--index` or `--target` must be given.

### B. `fcldump` — generate FCL from a dataset or target

```bash
# From dataset name (finds and downloads the jobdef tarball)
fcldump --dataset dts.mu2e.RPCInternalPhysical.MDC2020az.art

# From a specific output file (computes the correct index)
fcldump --target dig.mu2e.DIOtail95Mix1BBTriggered.MDC2020ba_best_v1_3.001202_00000428.art

# Using a local tarball (skip the mdh download)
fcldump --local-jobdef cnf.mu2e.DIOtail95Mix1BB.MDC2020ba_best_v1_3.0.tar \
        --target dig.mu2e.DIOtail95Mix1BBTriggered.MDC2020ba_best_v1_3.001202_00000428.art

# List all SAM job definitions for a given dsconf
fcldump --list-dsconf MDC2020ba_best_v1_3

# Direct-input mode: supply your own art input via --fname
fcldump --local-jobdef cnf.mu2e.Reco.MDC2025af_best_v1_3.tar \
        --fname mcs.mu2e.Foo.MDC2025af_best_v1_3.001450_00000100.art
```

Flag defaults: `--proto root`, `--loc tape`, `--index 0`.

### C. Targeting a specific output

`--target` is for debugging missing files or reproducing a specific
job. It parses the sequencer (e.g. `001202_00000428`) out of the
filename, maps it to the corresponding job index, and emits FCL with the
exact input files and seeds for that job. Use it when you know the
output filename but not the index.

## 6. Mixing Jobs

Mixing combines primary events with pileup backgrounds. The config is
array-valued so `jsonexpander` can produce combinations. From
`data/mdc2025/mix.json`:

```json
{
    "input_data": [
        {"dts.mu2e.CePlusEndpoint.MDC2025ac.art": 1},
        {"dts.mu2e.CeEndpoint.MDC2025ac.art": 1},
        {"dts.mu2e.CosmicSignal.MDC2025ac.art": 1}
    ],
    "pileup_datasets": [{
        "dts.mu2e.MuBeamFlashCat.MDC2025ac.art": 1,
        "dts.mu2e.EleBeamFlashCat.MDC2025ac.art": 25,
        "dts.mu2e.NeutralsFlashCat.MDC2025ad.art": 1,
        "dts.mu2e.MuStopPileupCat.MDC2025ac.art": 2
    }],
    "dsconf": ["MDC2025af_best_v1_1"],
    "mixconf": [0],
    "pbeam": ["Mix1BB"],
    "owner": ["mu2e"],
    "simjob_setup": ["/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025af/setup.sh"],
    "fcl": ["Production/JobConfig/mixing/Mix.fcl"],
    "merge_events": [500],
    "inloc": ["tape"],
    "outloc": [{"dig.mu2e.*.art": "tape"}],
    "fcl_overrides": [{
        "services.DbService.purpose": "Sim_best",
        "services.DbService.version": "v1_1"
    }]
}
```

Key points:

- `input_data` is a list of single-key dicts — each key is a primary
  dataset, the value is its merge factor.
- `pileup_datasets` is a list containing a dict mapping each catalog
  dataset to its per-job file count.
- Pileup datasets are mapped to mixer names automatically
  (`MuBeamFlashMixer`, `EleBeamFlashMixer`, …) from the dataset name.

### Generate mixing jobdefs

```bash
# Single configuration
json2jobdef --json data/mdc2025/mix.json --index 0

# All configurations for a dsconf
json2jobdef --json data/mdc2025/mix.json --dsconf MDC2025af_best_v1_1

# Or: expand first, inspect, then generate per entry
jsonexpander --json data/mdc2025/mix.json --output expanded_mix.json
```

## 7. JSON Expansion

Any array-valued key in a JSON config becomes a dimension of a cross
product. `jsonexpander` flattens the template into one entry per
combination:

```bash
jsonexpander --json data/mdc2025/mix.json --output expanded_mix.json
jsonexpander --json data/mdc2025/mix.json --output expanded_mix.json --mixing
```

The `--mixing` flag adds mixing-specific fields (mixer names, pileup
counts) to each expanded entry.

## 8. Production Execution

`runmu2e` runs a single job from a jobdefs list. The job index is
selected via the `fname` environment variable, encoded as
`etc.mu2e.index.NNN.NNNNNNN.txt` — `NNN` is the job index (zero-padded);
the last field is a per-index sub-counter used by POMS.

```bash
# fname selects job index 0 out of the jobdefs list
export fname=etc.mu2e.index.000.0000000.txt

# Dry run, 5 events
runmu2e --jobdesc jobdefs_list.json --dry-run --nevts 5

# Real run (no dry-run)
runmu2e --jobdesc jobdefs_list.json --nevts -1
```

Flags: `--jobdesc` (required), `--dry-run`, `--nevts` (default -1 = all),
`--copy-input` (use `mdh copy-file` to stage inputs locally when needed),
`--mu2e-options` (extra flags passed through to `mu2e`).

### What `runmu2e` does

1. Parses the job index from `fname`, picks the matching entry from the
   jobdefs file.
2. Downloads the jobdef tarball with `mdh copy-file` (unless already
   present).
3. Generates the FCL via `jobfcl` with the correct input location /
   protocol.
4. Runs `mu2e -c <fcl> -n <nevts>`.
5. Pushes outputs with `pushOutput` (skipped under `--dry-run`).

### `inloc` values

`inloc` selects how input files are referenced:

- `tape`, `disk`, `scratch` — explicit dCache locations
- `auto` — resolve per-file via SAMWeb
- `resilient` — files on resilient dCache, read via xrootd on the grid
- `stash` — files on StashCache, read via CVMFS on the grid
- `none` — no input files (primary generation)

### Example jobdefs entry

`runmu2e` consumes entries like this one from `jobdefs_list.json`:

```json
{
    "tarball": "cnf.mu2e.CeMLeadingLogMix2BB.MDC2020ba_best_v1_3.0.tar",
    "njobs": 2000,
    "inloc": "tape",
    "outputs": [
        {"dataset": "dig.mu2e.*.art", "location": "tape"}
    ]
}
```

## 9. Sequential vs. Pseudo-Random Auxiliary Input Selection

For resampler jobs, `sequential_aux` at the top level of the JSON
config controls how auxiliary input files are distributed across jobs:

```json
{
    "desc": "EleBeamFlash",
    "dsconf": "Run1Baa",
    "resampler_name": "beamResampler",
    "input_data": {"sim.mu2e.EleBeamCat.Run1Baa.art": 1},
    "sequential_aux": true
}
```

- `true` — files are assigned sequentially with rollover (job 0 → file
  1, job 1 → file 2, …; wraps when jobs exceed files). Each file is
  used the same number of times; easy to reproduce.
- `false` or omitted — deterministic pseudo-random assignment.

Internally, `jobdef` places this under `tbs.sequential_aux` in the
tarball; the top-level JSON field is the user-facing form.

## 10. FCL Overrides

`fcl_overrides` is a flat dict of FHiCL paths → values. They are
injected as a short template FCL that is `--embed`'ed into the tarball,
so the base FCL is never expanded:

```json
{
    "fcl_overrides": {
        "services.GeometryService.bFieldFile": "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt",
        "services.DbService.purpose": "MDC2020_best",
        "services.DbService.version": "v1_3",
        "outputs.PrimaryOutput.compressionLevel": 1,
        "services.SeedService.baseSeed": 12345
    }
}
```

The generated template looks like:

```fcl
#include "Production/JobConfig/cosmic/ExtractedCRY.fcl"
services.GeometryService.bFieldFile: "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt"
services.DbService.purpose: "MDC2020_best"
services.DbService.version: "v1_3"
outputs.PrimaryOutput.compressionLevel: 1
services.SeedService.baseSeed: 12345
```

A special `"#include"` key can add extra `#include` lines to the
template; see `data/mdc2025/mix.json` for real-world usage.

## 11. Parity Tests

Parity tests validate byte-for-byte equivalence between this Python
implementation and the Perl `mu2ejobdef` reference across stage1,
resampler, and mixing configurations.

```bash
cd test
./parity_test.sh           # run only index 0 (default)
./parity_test.sh --all     # run every configuration
./compare_tarballs.sh      # re-run just the comparison step
```

Requires `MUSE_WORK_DIR` set via `muse setup SimJob`.

## 12. Additional Tools

### `pomsMonitor` — POMS analysis with a persistent SQLite DB

```bash
# Rebuild the DB from POMS JSONs
pomsMonitor --build-db --pattern 'MDC202*'

# List all job definitions
pomsMonitor --list

# Filter by campaign, show output datasets
pomsMonitor --campaign MDC2025ac --outputs

# Only complete / incomplete datasets
pomsMonitor --campaign MDC2025ac --outputs --complete
pomsMonitor --campaign MDC2025ac --outputs --incomplete

# Print only dataset names (e.g. for piping)
pomsMonitor --campaign MDC2025ac --datasets-only

# Datasets created in the last 7 days
pomsMonitor --outputs --since 7d

# Mark / unmark datasets as ignored (persisted in the DB)
pomsMonitor --ignore dig.mu2e.Foo.MDC2025ac.art --ignore-reason "bad config"
pomsMonitor --unignore dig.mu2e.Foo.MDC2025ac.art
pomsMonitor --list-ignored
```

Key flags: `--pattern`, `--db`, `--build-db`, `--list`, `--campaign`,
`--outputs`, `--sort`, `--complete`, `--incomplete`, `--datasets-only`,
`--since`, `--needs-processing`, `--ignore{,-reason}`, `--unignore`,
`--list-ignored`. Default DB path: `~/.prodtools/poms_data.db`.

### `pomsMonitorWeb` — Flask UI

```bash
pomsMonitorWeb
# open http://localhost:5000
```

Runs on `0.0.0.0:5000`. Provides an interactive dashboard, dataset
status view, JSON editor, and a "reload DB from POMS JSONs" action.

### `famtree` — dataset family trees

```bash
# Mermaid diagram for a file's parentage
famtree mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001114.art

# With efficiency statistics
famtree dig.mu2e.CePLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001999.art \
        --stats --max-files 5

# Render a PNG (or --svg) directly via mmdc
famtree dig.mu2e.CePLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001999.art --png
```

Flags: `--stats`, `--max-files` (default 10), `--png`, `--svg`.
`etc*.txt` files are excluded from diagrams automatically.

### `logparser` — per-dataset performance summary

```bash
# One dataset
logparser log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log

# Multiple datasets
logparser log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log \
          log.mu2e.CePLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log

# Cap log files scanned per dataset
logparser log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log --max-logs 100
```

Emits JSON with `CPU [h]`, `CPU_max [h]`, `Real [h]`, `Real_max [h]`,
`VmPeak [GB]`, `VmPeak_max [GB]`, `VmHWM [GB]`, `VmHWM_max [GB]`.

### `genFilterEff` — generation filter efficiency

```bash
genFilterEff --out SimEff.txt --chunksize 100 \
    sim.mu2e.MuBeamCat.MDC2025ac.art \
    sim.mu2e.EleBeamCat.MDC2025ac.art \
    sim.mu2e.NeutralsCat.MDC2025ac.art

# Limit files per dataset
genFilterEff --out SimEff.txt --maxFilesToProcess 1000 sim.mu2e.MuBeamCat.MDC2025ac.art

# Quiet
genFilterEff --out SimEff.txt --verbosity 0 sim.mu2e.MuBeamCat.MDC2025ac.art
```

Output is Proditions-compatible (`TABLE SimEfficiencies2`). Flags:
`--out`/`--outfile`, `--chunksize`/`--chunkSize` (default 100),
`--maxFilesToProcess`, `--verbosity` (0/1/2, default 2),
`--writeFullDatasetName`, `--firstLine`.

### `datasetFileList` — pnfs paths for a dataset or SAM definition

```bash
datasetFileList log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log | head
datasetFileList --defname log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log
datasetFileList --basename dts.mu2e.EleBeamFlash.Run1Baa.art
datasetFileList --disk dts.mu2e.EleBeamFlash.Run1Baa.art
```

Flags: `--basename`, `--disk`, `--tape`, `--scratch`, `--defname`.

### `listNewDatasets` — recent SAM datasets

```bash
listNewDatasets                       # art files, last 7 days, mu2epro
listNewDatasets --filetype log --days 14
listNewDatasets --user oksuzian
listNewDatasets --size                # include average file size
listNewDatasets --query "dh.dataset sim.mu2e.%.MDC2025ac%"
```

Flags: `--filetype`, `--days`, `--user`, `--size`, `--query`.

### `mkrecovery` — recovery SAM definitions for missing files

Single tarball mode:

```bash
mkrecovery /pnfs/.../cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar \
           --dataset dts.mu2e.EarlyNeutralsFlash.MDC2025ac.art \
           --njobs 40000
```

Multi-job mode from a `jobdefs_list.json`:

```bash
mkrecovery jobdefs_list.json --jobdesc
```

Creates a SAM definition named `<dataset>-recovery` containing
`etc.mu2e.index.NNN.NNNNNNN.txt` files for the missing jobs, which you
can submit through POMS.

### `mkidxdef` — SAM index definitions from a jobdefs list

```bash
mkidxdef --jobdefs jobdefs_list.json           # preview
mkidxdef --jobdefs jobdefs_list.json --prod    # create in SAM
```

### `jobquery` — inspect a jobdef tarball

```bash
jobquery --jobname  cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
jobquery --njobs    cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
jobquery --input-datasets   cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
jobquery --input-files      cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
jobquery --output-datasets  cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
jobquery --output-files dig.mu2e.Foo.MDC2025ac.art:100 cnf.mu2e.Foo.MDC2025ac.0.tar
jobquery --codesize         cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
jobquery --setup            cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
jobquery --extract-code     cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar
```

### `copy_to_stash` — copy a dataset into StashCache or resilient dCache

```bash
# Default: copy into $MU2E_STASH_WRITE (use inloc: stash in configs)
copy_to_stash --dataset dts.mu2e.CeEndpoint.Run1Bab.art

# Into $MU2E_RESILIENT (use inloc: resilient)
copy_to_stash --dataset dts.mu2e.CeEndpoint.Run1Bab.art --dest resilient

# First 10 files only, for testing
copy_to_stash --dataset dts.mu2e.CeEndpoint.Run1Bab.art --source disk --limit 10

# Dry run / just list target paths
copy_to_stash --dataset dts.mu2e.CeEndpoint.Run1Bab.art --dry-run
copy_to_stash --list dts.mu2e.CeEndpoint.Run1Bab.art
```

Flags: `--dataset`, `--dest` (`stash`|`resilient`, default `stash`),
`--source` (`disk`|`tape`, default `disk`), `--limit`, `--dry-run`,
`--list`, `--quiet`.

### `listMcsDefs` / `listRelatedDefs` — enumerate related SAM defs

```bash
listRelatedDefs mcs MDC2025af
listRelatedDefs dig Run1Bai
listRelatedDefs dts MDC2020ba
```

For every `<type>.*.<pattern>*.art` SAM definition, prints its `cnf`
(tarball) and `log` siblings. `listMcsDefs` is an alias.

### `plot_logs` — visualize log metrics with NERSC job counts

Export NERSC job counts from the [Fermilab batch monitoring dashboard](https://fifemon.fnal.gov/monitor/d/000000053/experiment-batch-details)
(panel 10, "Running Jobs"), then:

```bash
pyenv ana
python3 utils/plot_logs.py log.mu2e.PiBeam.MDC2025ac.csv data/nersc_runjobs.csv
```

Produces a three-panel PNG (running jobs on NERSC-Perlmutter-CPU, CPU /
real time from job logs, and memory metrics) with correlation statistics
printed to stdout.

## 13. Troubleshooting

### `samweb: command not found` / `fhicl-get: command not found`

Re-run section 1. Verify:

```bash
which fhicl-get
which mu2ejobdef
python3 -c "import samweb_client; print(samweb_client.__file__)"
```

### `mdh: command not found`

Same fix — `muse setup ops` puts `mdh` on the path.

### `json2jobdef`: "Mu2e SimJob environment not set up"

Some `json2jobdef` paths need `muse setup SimJob` in addition to
`muse setup ops`. Run it and retry.

### Parity tests: "MUSE_WORK_DIR environment variable is not set"

Set up the SimJob environment before running parity tests:

```bash
muse setup SimJob
cd test && ./parity_test.sh
```

### `runmu2e` does nothing useful / picks the wrong job

`runmu2e` reads the job index from `fname`. Check the value:

```bash
echo "$fname"
# should be etc.mu2e.index.NNN.NNNNNNN.txt — NNN is the job index
```
