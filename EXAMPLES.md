# Mu2e Production Tools - Usage Examples

This document provides practical examples for using the Python-based Mu2e production tools.

## Quick Navigation

- **[Environment Setup](#environment-setup)** - Required Mu2e environment configuration
- **[Job Definition Creation](#1-creating-job-definitions)** - Generate job definition tarballs
- **[FCL Generation](#2-fcl-configuration-generation)** - Create FCL files from jobdefs or target files
- **[Mixing Jobs](#3-mixing-job-definitions)** - Complete guide to mixing jobs
- **[JSON Expansion](#4-json-configuration-expansion)** - Parameter space exploration
- **[Production Execution](#5-production-job-execution)** - Run production workflows

## Environment Setup

**IMPORTANT: Set up the Mu2e environment before using any tools:**

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
muse setup SimJob
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
source setup.sh
```

This script automatically:
- Sources the Mu2e environment
- Sets up muse (`muse setup ops`, `muse setup SimJob`)
- Adds `prodtools/` to your PATH
- Enables running all commands directly from anywhere

**After sourcing, you can run commands directly:**
```bash
# Production tools
json2jobdef --json config.json --index 0
fcldump --dataset dts.mu2e.RPCExternal.MDC2020az.art
runjobdef --jobdefs jobdefs.txt --dry-run
runfcl --fcl template.fcl --nevents 1000 --dry-run

# Test tools (run from test/ directory)
cd test
./parity_test.sh          # Run index 0 only (default)
./parity_test.sh --all    # Run all configurations
./compare_tarballs.sh     # Compare test results
```

## Overview

The `prodtools` package provides implementations of Mu2e production tools:

- `json2jobdef` - Create job definition tarballs from JSON configs
- `fcldump` - Generate FCL configurations from jobdefs, datasets, or target files
- `runjobdef` - Execute production jobs from job definitions
- `runfcl` - Execute jobs from FCL templates (one-in-one-out processing)
- `mkidxdef` - Create SAM index definitions
- `jsonexpander` - Generate parameter combinations from templates
- `jobdef` - Create job definitions directly (low-level tool)

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

### B. Job Definition Execution (`runjobdef`)

Execute production workflows from job definition files:

```bash
# After setting up the environment (see Environment Setup section above)
# Set the job index environment variable (required for production)
export fname=etc.mu2e.index.000.0000000.txt

# Run a production job with dry-run mode
runjobdef --jobdefs jobdefs_list.json --dry-run --nevts 5
```

### C. What `runjobdef` Does

1. **Token Validation** - Verifies grid authentication
2. **Job Parsing** - Extracts parameters from jobdefs file using the `fname` index
3. **File Download** - Downloads job definition tarball using `mdh copy-file`
4. **FCL Generation** - Creates FCL with proper XrootD protocol for input files
   - **Sequential auxiliary input selection** is controlled by the job definition (`tbs.sequential_aux`)
   - **MaxEventsToSkip parameter** is automatically added for resampler jobs
5. **Job Execution** - Runs `mu2e` with the generated configuration
6. **Output Management** - Handles output files and prepares for SAM submission

### D. Command Line Options

```bash
runjobdef -h
Usage: runjobdef [options] --jobdefs <jobdefs_file>
  --jobdefs   Path to job definitions file (required)
  --copy-input        Copy input files using mdh
  --dry-run          Print commands without actually running pushOutput
  --nevts <n>        Number of events to process (-1 for all events)
```

### E. Example jobdefs File Format

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
