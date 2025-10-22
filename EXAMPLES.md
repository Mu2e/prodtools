# Mu2e Production Tools - Usage Examples

This document provides practical examples for using the Python-based Mu2e production tools.

## Quick Navigation

- **[Environment Setup](#environment-setup)** - Required Mu2e environment configuration
- **[Job Definition Creation](#1-creating-job-definitions)** - Generate job definition tarballs
- **[FCL Generation](#2-fcl-configuration-generation)** - Create FCL files from jobdefs or target files
- **[Mixing Jobs](#3-mixing-job-definitions)** - Complete guide to mixing jobs
- **[JSON Expansion](#4-json-configuration-expansion)** - Parameter space exploration
- **[Production Execution](#5-production-job-execution)** - Run production workflows
- **[Additional Tools](#additional-tools)** - Family tree visualization, log analysis, filter efficiency, dataset monitoring, recovery datasets

## Environment Setup

**IMPORTANT: Set up the Mu2e environment before using any tools:**

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
# Note: muse setup SimJob is optional for most tools
```

This setup provides access to:
- `fhicl-get` - FHiCL configuration parser
- `mu2ejobdef` - Mu2e job definition tool (for parity testing)
- `samweb_client` - Python library for SAM data access
- `mdh` - Mu2e data handling tools

### Alternative: Automated Environment Setup

For convenience, you can use the included setup script:

```bash
cd prodtools
source bin/setup.sh
```

This script automatically:
- Adds `prodtools/bin/` to your PATH
- Adds `prodtools/` to your PYTHONPATH
- Enables running all commands directly from anywhere

**Note**: You still need to source the Mu2e environment and run `muse setup ops` first.

**After sourcing, you can run commands directly:**
```bash
# Production tools
json2jobdef --json config.json --index 0
fcldump --dataset dts.mu2e.RPCExternal.MDC2020az.art
runjobdef --jobdesc jobdefs_list.json --dry-run
runfcl --fcl template.fcl --nevents 1000 --dry-run

# Test tools (run from test/ directory)
cd test
./parity_test.sh          # Run index 0 only (default)
./parity_test.sh --all    # Run all configurations
./compare_tarballs.sh     # Compare test results
```

## Overview

The `prodtools` package provides implementations of Mu2e production tools:

**Core Production Tools:**
- `json2jobdef` - Create job definition tarballs from JSON configs
- `fcldump` - Generate FCL configurations from jobdefs, datasets, or target files
- `runmu2e` - Execute production jobs from job definitions
- `runfcl` - Execute jobs from FCL templates (one-in-one-out processing)
- `mkidxdef` - Create SAM index definitions
- `jsonexpander` - Generate parameter combinations from templates
- `jobdef` - Create job definitions directly (low-level tool)

**Analysis and Diagnostics:**
- `famtree` - Generate family tree diagrams for data lineage
- `logparser` - Analyze job performance metrics from log files
- `genFilterEff` - Calculate generation filter efficiency for datasets
- `datasetFileList` - List files in datasets with pnfs paths
- `listNewDatasets` - Monitor recently created datasets
- `mkrecovery` - Create recovery dataset definitions for missing files
- `plot_logs` - Visualize log metrics merged with NERSC job counts

## 1. Creating Job Definitions

### A. JSON-Based Configuration (Recommended)

Create job definitions using JSON configuration files:

```json
[
    {
	"desc": "POT_Run1_a",
	"dsconf": "MDC2020ba",
	"fcl": "Production/JobConfig/beam/POT.fcl",
	"fcl_overrides": {
	    "services.GeometryService.bFieldFile": "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt",
		"services.GeometryService.inputFile": "Offline/Mu2eG4/geom/geom_run1_a.txt"
	},
	"njobs": 20000,
	"events": 5000,
	"run": 1431,
	"outloc": {
		"*.art": "disk"
	},
	"simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020ba/setup.sh",
	"owner": "mu2e"
    }
]
```

**Usage:**
```bash
# Create job definition from JSON for 1st entry
json2jobdef --json data/stage1.json --index 0

# Create job definition from JSON for a pair of desc and dsconf
json2jobdef --json data/stage1.json --desc POT_Run1_a --dsconf MDC2020ba

# Create job definitions from JSON for all enrties that match dsconf
json2jobdef --json data/stage1.json --dsconf MDC2020ba

```

**Output:**
- `cnf.mu2e.CosmicCORSIKALow.MDC2020az.0.tar` (job definition tarball)
- `jobdefs_list.json` (descriptions of all job definitions to run over)
- `cnf.mu2e.CosmicCORSIKALow.MDC2020az.0.fcl` (FCL test file)

### B. Direct Job Definition Creation

For more control, use the `jobdef.py` utility directly:

```bash
# Stage-1 example

jobdef --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020av/setup.sh \
	--dsconf MDC2020av --desc ExtractedCRY --dsowner mu2e \
	--run-number 1205 --events-per-job 500000 \
	--include Production/JobConfig/cosmic/ExtractedCRY.fcl

# Resampler example
json2jobdef --json data/resampler.json --index 0 --verbose # to get a command example
jobdef --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020ap/setup.sh \
	--dsconf MDC2020ap --desc RMCFlatGammaStops --dsowner mu2e \
	--run-number 1202 --events-per-job 1000000 \
	--auxinput 1:physics.filters.TargetStopResampler.fileNames:inputs.txt --embed template.fcl

```

### B. From Job Definition Files

Generate FCL configurations from existing job definition tarballs:

```bash
# Generate FCL with xroot protocol for file access
python3 -c "
from utils.jobfcl import Mu2eJobFCL

job_fcl = Mu2eJobFCL('cnf.mu2e.CosmicCORSIKALow.MDC2020az.0.tar', 
                     inloc='tape', proto='root')
fcl_content = job_fcl.generate_fcl(0)
print(fcl_content)
"
```

### B. Quick FCL Generation with `fcldump`

Generate FCL files directly from dataset names or specific target files:

```bash
# Generate FCL from dataset name - automatically finds and downloads jobdef
fcldump --dataset dts.mu2e.RPCExternalPhysical.MDC2020az.art

# Generate FCL for a specific output file (finds the job that produces it)
fcldump --target dig.mu2e.DIOtail95Mix1BBTriggered.MDC2020ba_best_v1_3.001202_00000428.art

# Generate FCL from local job definition file
fcldump --local-jobdef cnf.mu2e.DIOtail95Mix1BB.MDC2020ba_best_v1_3.0.tar --target dig.mu2e.DIOtail95Mix1BBTriggered.MDC2020ba_best_v1_3.001202_00000428.art

# This will:
# 1. Find the corresponding jobdef: cnf.mu2e.RPCExternalPhysical.MDC2020az.0.tar
# 2. Download it using mdh copy-file (unless using --local-jobdef)
# 3. Generate: cnf.mu2e.RPCExternalPhysical.MDC2020az.0.fcl
# 4. When using --target, automatically finds the correct job index and input files
# 5. Sequential auxiliary input selection is controlled by the job definition (tbs.sequential_aux)
```

### C. Understanding the `--target` Option

The `--target` option allows you to generate FCL configurations for specific output files without knowing the job index. This is particularly useful for:

- **Debugging missing files** - Generate FCL for a specific output to understand what went wrong
- **Reproducing specific jobs** - Get the exact configuration that produced a particular file
- **Validation** - Verify that a job definition can produce the expected output

**How it works:**
1. **Parses the target filename** - Extracts sequencer (e.g., `001202_00000428`) from the target
2. **Finds the job index** - Maps the sequencer to the corresponding job index in the job definition
3. **Generates specific FCL** - Creates configuration with the exact input files and settings for that job
4. **Validates output** - Ensures the target file is actually produced by the found job

**Example with missing file:**
```bash
# The file dig.mu2e.DIOtail95Mix1BBTriggered.MDC2020ba_best_v1_3.001202_00000428.art is missing
# Use fcldump to understand what should have produced it:
fcldump --target dig.mu2e.DIOtail95Mix1BBTriggered.MDC2020ba_best_v1_3.001202_00000428.art --proto root --loc tape

# This will:
# - Find job index 180 (from sequencer 001202_00000428)
# - Generate FCL with the specific input file: dts.mu2e.DIOtail95.MDC2020at.001202_00000428.art
# - Include all necessary pileup mixing files
# - Set correct output filenames with the specific sequencer
```

## 3. Mixing Job Definitions

### A. Basic Mixing Configuration

Mixing jobs combine signal events with pileup backgrounds from multiple sources:

```json
{
        "input_data": ["dts.mu2e.CeEndpoint.MDC2020ar.art", "dts.mu2e.CosmicCRYSignalAll.MDC2020ar.art", "dts.mu2e.FlateMinus.MDC2020ar.art"],
        "mubeam_dataset": ["dts.mu2e.MuBeamFlashCat.MDC2020p.art"],
        "elebeam_dataset": ["dts.mu2e.EleBeamFlashCat.MDC2020p.art"],
        "neutrals_dataset": ["dts.mu2e.NeutralsFlashCat.MDC2020p.art"],
        "mustop_dataset": ["dts.mu2e.MuStopPileupCat.MDC2020p.art"],
        "mubeam_count": [1],
        "elebeam_count": [25],
        "neutrals_count": [50],
        "mustop_count": [2],
        "dsconf": ["MDC2020aw_best_v1_3"],
        "mixconf": [0],
        "pbeam": ["Mix1BB", "Mix2BB"],
        "simjob_setup": ["/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020aw/setup.sh"],
        "fcl": ["Production/JobConfig/mixing/Mix.fcl"],
        "merge_events": [2000],
        "inloc": ["tape"],
        "outloc": [{"dig.mu2e.*.art": "tape"}],
        "owner": ["mu2e"],
        "fcl_overrides": [
            {
                "services.DbService.purpose": "MDC2020_best",
                "services.DbService.version": "v1_3",
                "services.DbService.verbose": 2,
                "services.GeometryService.bFieldFile": "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt"
            }
        ]
    }
```

### B. Generate Mixing Job Definitions

```bash
# 1. Expand the mixing template to individual configurations
jsonexpander --json data/mix.json \
	--output expanded_mix.json

# 2. Generate jobdef for a specific mixing configuration
json2jobdef.py --json data/mix.json --index 0

# 3. Generate multiple jobdefs for specific dsconf
json2jobdef.py --json data/mix.json --dsconf MDC2020ba_best_v1_3
```

### B. Input Template Format

The input JSON can contain arrays for any parameter to create combinations:

```json
{
  "primary_dataset": [
    "dts.mu2e.CeEndpoint.MDC2020ar.art",
    "dts.mu2e.CosmicCRYSignalAll.MDC2020ar.art",
    "dts.mu2e.FlateMinus.MDC2020ar.art",
    "dts.mu2e.FlatePlus.MDC2020ar.art"
  ],
  "dbpurpose": ["perfect", "best"],
  "pbeam": ["Mix1BB", "Mix2BB"]
}
```

## 5. Production Job Execution

### A. Template-Based Execution (`runfcl`)

Execute jobs directly from FCL templates (one-in-one-out processing):

```bash
# Basic usage - process a single file with a template
runfcl --fcl template.fcl --nevents 1000

# With database configuration
runfcl --fcl template.fcl --release an --dbpurpose best --dbversion v1_3

# Dry run to test
runfcl --fcl template.fcl --nevents 1000 --dry-run
```

**What `runfcl` does:**
1. **Reads input file** from `fname` environment variable
2. **Generates FCL** from template with database and output configurations
3. **Runs Mu2e** with the generated FCL
4. **Handles outputs** and creates `output.txt` for SAM registration
5. **Runs pushOutput** for file registration

### B. Job Definition Execution (`runmu2e`)

Execute production workflows from job definition files:

```bash
# After setting up the environment (see Environment Setup section above)
# Set the job index environment variable (required for production)
export fname=etc.mu2e.index.000.0000000.txt

# Run a production job with dry-run mode
runmu2e --jobdesc jobdefs_list.json --dry-run --nevts 5
```

**Understanding the `fname` format:**
- `etc.mu2e.index.000.0000000.txt` means job index 0
- `etc.mu2e.index.001.0000000.txt` means job index 1
- The job index determines which job definition to use from the jobdefs file

### C. What `runmu2e` Does

1. **Token Validation** - Verifies grid authentication
2. **Job Parsing** - Extracts parameters from jobdefs file using the `fname` index
3. **File Download** - Downloads job definition tarball using `mdh copy-file`
4. **FCL Generation** - Creates FCL with proper XrootD protocol for input files
   - **Sequential auxiliary input selection** is controlled by the job definition (`tbs.sequential_aux`)
   - **MaxEventsToSkip parameter** is automatically added for resampler jobs
5. **Job Execution** - Runs `mu2e` with the generated configuration
6. **Output Management** - Handles output files and prepares for SAM submission

**Example successful output:**
```
Job 0 uses definition 0
Global job index: 0, Local job index within definition: 0
Running: mdh copy-file -e 3 -o -v -s disk -l local cnf.mu2e.NeutralsFlash.MDC2025ab.0.tar
FCL file generated: cnf.mu2e.NeutralsFlash.MDC2025ab.0.fcl
Job setup script: /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ab/setup.sh
Mu2e command: mu2e -c cnf.mu2e.NeutralsFlash.MDC2025ab.0.fcl -n 5
=== Mu2e execution completed successfully ===
[DRY RUN] Would run: pushOutput output.txt
```

### D. Command Line Options

```bash
runmu2e -h
Usage: runmu2e [options] --jobdesc <jobdesc_file>
  --jobdesc           Path to job descriptions file (required)
  --copy-input        Copy input files using mdh
  --dry-run           Print commands without actually running pushOutput
  --nevts <n>         Number of events to process (-1 for all events)
```

### E. Example jobdefs File Format

The `inloc` field specifies where input files are located. You can use:
- `"disk"`, `"tape"`, or `"scratch"` - explicit location
- `"auto"` - defaults to tape with automatic SAMWeb fallback to available location
- `"none"` - no input files

```json
  {
    "tarball": "cnf.mu2e.CeMLeadingLogMix2BB.MDC2020ba_best_v1_3.0.tar",
    "njobs": 2000,
    "inloc": "tape",
    "outputs": [
      {
        "dataset": "dig.mu2e.*.art",
        "location": "tape"
      }
    ]
  }
```

### B. Sequential Auxiliary Input Selection

For resampler jobs, you can control how auxiliary input files are selected using the `sequential_aux` setting in your JSON configuration:

```json
{
    "desc": "FlateMinus",
    "dsconf": "MDC2025ba",
    "fcl": "Production/JobConfig/primary/RMCFlatGammaStops.fcl",
    "tbs": {
        "auxin": {
            "dts.mu2e.FlateMinus.MDC2025ba.art": [1, ["file1.art", "file2.art", "file3.art"]]
        },
        "sequential_aux": true
    }
}
```

**Sequential vs. Pseudo-Random Selection:**

- **`"sequential_aux": true`** - Files are selected sequentially with rollover (job 0 gets file1, job 1 gets file2, job 2 gets file3, job 3 gets file1, etc.)
- **`"sequential_aux": false`** (default) - Files are selected using deterministic pseudo-random selection

**Benefits of Sequential Selection:**
- **Predictable distribution** - Each file is used exactly the same number of times
- **Better for testing** - Easier to reproduce specific input file combinations
- **Rollover handling** - When job index exceeds file count, selection wraps around to the beginning

### C. Custom FCL Overrides

Template-based approach handles FCL overrides:

```json
{
  "fcl_overrides": {
    "services.GeometryService.bFieldFile": "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt",
    "physics.producers.generate.inputModule": "compressDigiMCs",
    "outputs.PrimaryOutput.fileName": "dts.owner.CustomJob.version.sequencer.art",
    "outputs.PrimaryOutput.compressionLevel": 1,
    "services.SeedService.baseSeed": 12345
  }
}
```

**How FCL Overrides Work:**

1. **Template Creation**: `write_fcl_template()` creates `template.fcl` with:
   ```fcl
   #include "Production/JobConfig/cosmic/ExtractedCRY.fcl"
   services.GeometryService.bFieldFile: "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt"
   physics.producers.generate.inputModule: "compressDigiMCs"
   outputs.PrimaryOutput.fileName: "dts.owner.CustomJob.version.sequencer.art"
   outputs.PrimaryOutput.compressionLevel: 1
   services.SeedService.baseSeed: 12345
   ```

2. **Embedding**: `--embed template.fcl` embeds this complete template into `mu2e.fcl`

3. **Result**: The final `mu2e.fcl` contains the include directive plus all overrides, but the base FCL is never expanded

**Benefits:**
- **Clean templates**: Only include directive + overrides, no base FCL content
- **Overrides included**: All FCL overrides are directly embedded
- **No expansion**: Base FCL files remain unexpanded for maintainability
- **Perfect parity**: Both Python and Perl versions handle overrides identically

## 7. Troubleshooting

### Common Issues and Solutions

#### Environment Setup Problems

**Problem**: `samweb: command not found` or `fhicl-get: command not found`
```bash
# Solution: Follow the Environment Setup section above
# Verify tools are available:
which fhicl-get
which mu2ejobdef
python3 -c "import samweb_client; print('samweb_client is available')"
```

#### File Access Issues

**Problem**: `mdh: command not found`
**Solution**: Follow the Environment Setup section above.

## 8. Running Parity Tests
:
### A. Basic Usage

The parity tests should be run from the `test/` directory to ensure all relative paths work correctly:

```bash
# Navigate to test directory
cd test

# Run only index 0 configurations (default)
./parity_test.sh

# Run all configurations
./parity_test.sh --all

# Compare results manually
./compare_tarballs.sh
```

### B. What Parity Tests Do

1. **Generate job definitions** using both Python (`json2jobdef.py`) and Perl (`mu2ejobdef`) tools
2. **Compare outputs** for byte-for-byte parity between implementations
3. **Test multiple configurations** from stage1, resampler, and mixing job types

### C. Test Coverage

- **Stage1 Jobs**: 5 configurations (cosmic, beam, etc.)
- **Resampler Jobs**: 23 configurations (various resampling scenarios)
- **Mixing Jobs**: 32 configurations (different mixing combinations)

## Additional Tools

### Family Tree Visualization (`famtree`)

Trace the parentage chain of files and generate Mermaid diagrams:

```bash
# Set up environment
mu2einit 
muse setup ops
source /cvmfs/mu2e.opensciencegrid.org/bin/prodtools/v1.3.8/bin/setup.sh 

# Generate family tree diagram
famtree mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001114.art

# Generate with efficiency statistics
famtree dig.mu2e.CePLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001999.art --stats

# Generate PNG with statistics (sample 5 files per dataset)
famtree dig.mu2e.CePLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001999.art --stats --max-files 5 --png

# Convert existing diagram to SVG for viewing
npx -y @mermaid-js/mermaid-cli -i mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.md
firefox mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.md-1.svg &
```

**What `famtree` does:**
1. **Traces parentage** - Follows the chain of input files that led to the creation of a given output file
2. **Groups by dataset** - Shows one representative per dataset to avoid clutter
3. **Generates Mermaid diagram** - Creates a visual family tree showing data lineage
4. **Filters etc files** - Automatically excludes `etc*.txt` files from the tree
5. **Efficiency statistics** - Optionally includes filter efficiency (passed/generated events) for each dataset

**Command-line options:**
- `--stats` - Include efficiency statistics in node labels (e.g., "eff=0.2316 (3474/15000)")
- `--max-files N` - Number of files to sample for statistics (default: 10, faster with lower values)
- `--png` - Automatically convert to PNG using `mmdc`
- `--svg` - Automatically convert to SVG using `mmdc`

### Log Analysis (`logparser`)

Analyze Mu2e job performance metrics from log files:

```bash
$ logparser log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log
Processing log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log
{
  "dataset": "log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log",
  "CPU [h]": 0.2,
  "CPU_max [h]": 0.2,
  "Real [h]": 0.21,
  "Real_max [h]": 0.21,
  "VmPeak [GB]": 1.64,
  "VmPeak_max [GB]": 1.64,
  "VmHWM [GB]": 1.11,
  "VmHWM_max [GB]": 1.11
}
[INFO] Wrote summary.json
```

**What `logparser` extracts:**
- **CPU time** - Total and maximum CPU usage
- **Real time** - Wall clock time for job execution
- **Memory usage** - Peak virtual memory (VmPeak) and high water mark (VmHWM)
- **JSON output** - Machine-readable summary for further analysis

**CSV export for detailed analysis:**
```bash
# Save per-file metrics to CSV
$ logparser log.mu2e.RPCExternalPhysicalMix1BB.MDC2020bc_best_v1_3.log --csv
[INFO] Wrote log.mu2e.RPCExternalPhysicalMix1BB.MDC2020bc_best_v1_3.csv
```

The CSV file contains per-file metrics with columns:
- `file` - Log file basename
- `date` - Job execution date (extracted from log)
- `CPU [h]` - CPU time in hours
- `Real [h]` - Wall clock time in hours
- `VmPeak [GB]` - Peak virtual memory in GB
- `VmHWM [GB]` - Memory high water mark in GB

**Visualizing log metrics with NERSC job counts:**

The NERSC job counts CSV can be exported from the [Fermilab batch monitoring dashboard](https://fifemon.fnal.gov/monitor/d/000000053/experiment-batch-details?from=now-30d&to=now&var-experiment=mu2e&orgId=1&viewPanel=10) (panel 10: "Running Jobs").

```bash
# First activate the Python environment with pandas
$ pyenv ana

# Generate merged plots from log CSV and NERSC job counts CSV
$ python3 utils/plot_logs.py log.mu2e.PiBeam.MDC2025ac.csv data/nersc_runjobs.csv
Log data: 5000 points from 2025-10-07 09:41:49 to 2025-10-08 00:59:06
NERSC data: 920 points from 2025-09-17 17:00:00 to 2025-10-14 01:00:00
Merged: 5000 points

Saved: log.mu2e.PiBeam.MDC2025ac.png

Files: 5000
CPU:  0.94 ± 0.13 h
Real: 1.02 ± 0.26 h
Mem:  2.25 ± 0.00 GB

Correlations with NERSC-Perlmutter-CPU:
  CPU [h]:      -0.445
  Real [h]:     -0.406
  VmPeak [GB]:  -0.012
  VmHWM [GB]:   0.153
```

The visualization script creates a 3-panel plot showing:
- Running jobs on NERSC-Perlmutter-CPU over time (top)
- CPU/Real time metrics from job logs (middle)
- Memory usage (VmPeak/VmHWM) from job logs (bottom)
- Correlation statistics between job counts and performance metrics
- Mean lines with values in legends
- Statistics summary printed to console
- PNG output with same basename as input CSV

### Dataset File Listing (`datasetFileList`)

Python implementation of file listing with exact parity to Perl version:

```bash
# List files in a dataset
$ datasetFileList log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log | head
/pnfs/mu2e/persistent/datasets/phy-etc/log/mu2e/CeMLeadingLogMix1BBTriggered/MDC2020ba_best_v1_3/log/2f/30/log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00000000-1756219665.log
...

# List files using SAM definition names (like samListLocations --defname)
$ datasetFileList --defname log.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.log
...
```

**Features:**
- **Exact Perl parity** - Byte-for-byte identical output to original Perl implementation
- **SHA256 path generation** - Correctly constructs `/pnfs` paths with hash-based subdirectories
- **SAM definition support** - Works with both dataset names and SAM definition names
- **Performance** - Faster execution than the original Perl version

### Generation Filter Efficiency (`genFilterEff`)

Calculate overall filter efficiency for simulation datasets - the ratio of passed events to generated events:

```bash
# Set up environment
mu2einit
muse setup ops

# Calculate efficiency for single dataset
genFilterEff --out=SimEff.txt --chunksize=100 sim.mu2e.Beam.MDC2020p.art

# Calculate for multiple datasets
genFilterEff --out=SimEff.txt --chunksize=100 \
  sim.mu2e.MuBeamCat.MDC2025ac.art \
  sim.mu2e.EleBeamCat.MDC2025ac.art \
  sim.mu2e.NeutralsCat.MDC2025ac.art

# Process only first 1000 files per dataset
genFilterEff --out=SimEff.txt --maxFilesToProcess=1000 sim.mu2e.Beam.MDC2020p.art

# Quiet mode (minimal output)
genFilterEff --out=SimEff.txt --verbosity=0 sim.mu2e.Beam.MDC2020p.art
```

**Example output:**
```
Processing dataset  sim.mu2e.Beam.MDC2020p.art, using 10 out of 50000 files
        eff = 0.1705 (6820 / 40000) after processing 10 files of sim.mu2e.Beam.MDC2020p.art
```

**Output file format (Proditions-compatible):**
```
TABLE SimEfficiencies2
Beam,   6820,   40000,  0.1705
```

**What `genFilterEff` does:**
1. **Queries SAM metadata** - Retrieves `dh.gencount` (generated events) and `event_count` (passed events)
2. **Processes in chunks** - Batches SAM queries for efficiency (default: 100 files per request)
3. **Calculates efficiency** - Computes ratio of passed/generated events
4. **Proditions format** - Outputs in format compatible with Mu2e Proditions database

**Command-line options:**
- `--out OUTFILE` - Output file path (required)
- `--chunksize N` - Number of files to query per SAM transaction (default: 100)
- `--maxFilesToProcess N` - Limit processing to first N files per dataset
- `--verbosity LEVEL` - Control output: 0=quiet, 1=minimal, 2=verbose (default: 2)
- `--writeFullDatasetName` - Use full dataset name instead of description field
- `--firstLine TEXT` - Custom first line for output (default: "TABLE SimEfficiencies2")

**Python implementation:**
- Direct replacement for `mu2eGenFilterEff` Perl tool
- Uses `samweb_wrapper` for SAM queries
- Follows prodtools design patterns

### List New Datasets (`listNewDatasets`)

Monitor and track recently created datasets in the SAM database with file counts and average sizes:

```bash
# Set up environment
mu2einit
muse setup ops

# List art files from last 7 days (default)
listNewDatasets

# List log files from last 14 days
listNewDatasets --filetype log --days 14

# List files for specific user
listNewDatasets --user oksuzian

# Skip file size calculation for faster execution
listNewDatasets --no-size

# Use custom SAM query
listNewDatasets --query "dh.dataset sim.mu2e.%.MDC2025ab%"
```

**Example output:**
```
Checking for art files created after: 2025-10-01 for user: mu2epro
------------------------------------------------
Grouped file counts:
   COUNT DATASET                                                                                              FILE SIZE
   -----  -------                                                                                              --------
      15 sim.mu2e.Beam.MDC2025ab.art                                                                            234 MB
       8 sim.mu2e.Neutrals.MDC2025ab.art                                                                        187 MB
      23 sim.mu2e.CosmicCRY.MDC2025ab.art                                                                       156 MB
------------------------------------------------
```

**What `listNewDatasets` does:**
1. **Queries SAM database** - Searches for files matching criteria (date, user, format)
2. **Groups by dataset** - Extracts dataset names and counts files
3. **Calculates sizes** - Computes average file size per dataset (optional)

**Command-line options:**
- `--filetype TYPE` - File format to search for: art, log, etc. (default: art)
- `--days N` - Number of days to look back (default: 7)
- `--user USERNAME` - Filter by username (default: mu2epro)
- `--no-size` - Skip file size calculation for faster execution
- `--query QUERY` - Custom SAM query (overrides all other parameters)

**Dataset name extraction:**
The tool extracts dataset names from filenames by taking the first 4 dot-separated fields:
- `sim.mu2e.Beam.MDC2025ab.001430_00000000.art` → `sim.mu2e.Beam.MDC2025ab.art`
- Groups all files with the same base dataset name together

**Python implementation:**
- Port of `Production/Scripts/listNewDatasets.sh` bash script
- Uses `samweb` CLI commands for database queries
- Follows prodtools design patterns with class-based structure
- Supports both standalone and module usage

### Create Recovery Datasets (`mkrecovery`)

Identify missing files from a production and create a SAM dataset definition for recovery:

```bash
# Set up environment
mu2einit
muse setup ops

# Create recovery dataset for missing files
mkrecovery /pnfs/mu2e/.../cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar \
           dts.mu2e.EarlyNeutralsFlash.MDC2025ac.art \
           40000
```

**Example output:**
```
Missing: 208 of 40000
Created SAM definition: dts.mu2e.EarlyNeutralsFlash.MDC2025ac-recovery
```

**What `mkrecovery` does:**
1. **Reads job definition** - Extracts expected output file pattern from tarball
2. **Queries SAM dataset** - Gets actual files that were produced
3. **Identifies missing files** - Compares expected vs actual
4. **Creates SAM definition** - Recovery dataset with `etc` input files for resubmission

**To use the recovery dataset:**
```bash
# List files in the recovery dataset
samweb list-definition-files dts.mu2e.EarlyNeutralsFlash.MDC2025ac-recovery

# Count files that need recovery
samweb count-definition-files dts.mu2e.EarlyNeutralsFlash.MDC2025ac-recovery

# Use with job submission (POMS)
# Reference the recovery definition in your campaign configuration
```

The recovery definition contains `etc.mu2e.index.000.JJJJJJJ.txt` files that can be used to resubmit only the missing jobs.
