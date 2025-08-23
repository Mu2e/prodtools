# Mu2e Production Tools - Usage Examples

This document provides practical examples for using the Python-based Mu2e production tools.

## Quick Navigation

- **[Environment Setup](#environment-setup)** - Required Mu2e environment configuration
- **[Job Definition Creation](#1-creating-job-definitions)** - Generate job definition tarballs
- **[FCL Generation](#2-fcl-configuration-generation)** - Create FCL files from jobdefs
- **[Mixing Jobs](#3-mixing-job-definitions)** - Complete guide to mixing jobs
- **[JSON Expansion](#4-json-configuration-expansion)** - Parameter space exploration
- **[Production Execution](#5-production-job-execution)** - Run production workflows

## Overview

The `prodtools` package provides Python implementations of key Mu2e production tools:

- `json2jobdef.py` - Create job definition tarballs from JSON configs
- `fcl_maker.py` - Generate FCL configurations from jobdefs or datasets
- `jobdefs_runner.py` - Execute production jobs from job definitions
- `json_expander.py` - Generate parameter combinations from templates

## 1. Creating Job Definitions

### A. JSON-Based Configuration (Recommended)

Create job definitions using JSON configuration files:

```json
[
  {
    "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh",
    "fcl": "Production/JobConfig/cosmic/S2Resampler.fcl",
    "dsconf": "MDC2020az",
    "desc": "CosmicCORSIKALow",
    "outloc": "disk",
    "owner": "mu2e",
    "njobs": 1,
    "run": 1203,
    "events": 500000,
    "input_data": "sim.mu2e.CosmicDSStopsCORSIKALow.MDC2020aa.art"
  }
]
```

**Usage:**
```bash
# Create job definition from JSON
python3 json2jobdef.py --json config.json --index 0

# Keep temporary files for debugging
python3 json2jobdef.py --json config.json --index 0 --no-cleanup
```

**Output:**
- `cnf.mu2e.CosmicCORSIKALow.MDC2020az.0.tar` (job definition tarball)
- `cnf.mu2e.CosmicCORSIKALow.MDC2020az.0.fcl` (FCL configuration)

### B. Direct Job Definition Creation

For more control, use the `jobdef.py` utility directly:

```bash
# Basic job definition creation
python3 utils/jobdef.py --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh \
    --dsconf MDC2020az --desc CosmicCORSIKALow --dsowner mu2e \
    --embed Production/JobConfig/cosmic/S2Resampler.fcl

# With run number and events per job
python3 utils/jobdef.py --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh \
    --dsconf MDC2020az --desc CosmicCORSIKALow --dsowner mu2e \
    --embed Production/JobConfig/cosmic/S2Resampler.fcl \
    --run-number 1203 --events-per-job 500000

# Mixing job with auxiliary inputs
python3 utils/jobdef.py --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh \
    --dsconf MDC2020az --desc MixingJob --dsowner mu2e \
    --embed Production/JobConfig/mixing/Mix.fcl \
    --auxinput "1:physics.filters.MuBeamFlashMixer.fileNames:mubeam.txt" \
    --auxinput "25:physics.filters.EleBeamFlashMixer.fileNames:elebeam.txt"
```

### C. FCL Template and Overrides

The system now uses a smart template approach that creates a `template.fcl` file containing:

```fcl
#include "Production/JobConfig/cosmic/ExtractedCRY.fcl"
services.GeometryService.bFieldFile: "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt"
```

**Key Benefits:**
- **No FCL expansion**: Base FCL files are not expanded, keeping templates clean
- **Overrides included**: FCL overrides are directly embedded in the template
- **Minimal templates**: `template.fcl` contains just the include directive + overrides
- **Perfect parity**: Python and Perl versions produce identical output

**How it works:**
1. **Template creation**: `write_fcl_template()` creates `template.fcl` with include + overrides
2. **Embedding**: `--embed template.fcl` embeds this content into the final `mu2e.fcl`
3. **Result**: Clean, minimal FCL files with overrides included but base FCL not expanded

## 2. FCL Configuration Generation

### A. New Template-Based Approach

The system now uses a smart template approach that creates minimal, clean FCL files:

**Template Creation:**
```python
# The write_fcl_template() function creates template.fcl with:
#include "Production/JobConfig/cosmic/ExtractedCRY.fcl"
services.GeometryService.bFieldFile: "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt"
```

**Job Definition Generation:**
```bash
# Uses --embed template.fcl to embed the template content
mu2ejobdef --setup /path/to/setup.sh --dsconf MDC2020av --desc ExtractedCRY \
    --dsowner mu2e --embed template.fcl
```

**Result:**
- **template.fcl**: Contains include directive + overrides (not expanded)
- **mu2e.fcl**: Contains embedded template content with overrides included
- **Clean output**: No base FCL expansion, just the template content embedded

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

### B. Quick FCL Generation with `fcl_maker.py`

Generate FCL files directly from dataset names:

```bash
# Generate FCL from dataset name - automatically finds and downloads jobdef
./fcl_maker.py --dataset dts.mu2e.RPCExternalPhysical.MDC2020az.art

# This will:
# 1. Find the corresponding jobdef: cnf.mu2e.RPCExternalPhysical.MDC2020az.0.tar
# 2. Download it using mdh copy-file
# 3. Generate: cnf.mu2e.RPCExternalPhysical.MDC2020az.0.fcl
```

**Example Output:**
```fcl
#include "Production/JobConfig/primary/RPCExternalPhysical.fcl"
services.GeometryService.bFieldFile: "Offline/Mu2eG4/geom/bfgeom_no_tsu_ps_v01.txt"
physics.filters.TargetPiStopResampler.mu2e.MaxEventsToSkip: 10636

#----------------------------------------------------------------
# Code added by mu2ejobfcl for job index 0:
source.firstRun: 1202
source.maxEvents: 1000000
source.firstSubRun: 0
physics.filters.TargetPiStopResampler.fileNames: [
    "xroot://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/mu2e/tape/phy-sim/sim/mu2e/PhysicalPionStops/MDC2020ay/art/be/25/sim.mu2e.PhysicalPionStops.MDC2020ay.001202_00001637.art"
]
outputs.PrimaryOutput.fileName: "dts.mu2e.RPCExternalPhysical.MDC2020az.001202_00000000.art"
services.SeedService.baseSeed: 1
# End code added by mu2ejobfcl:
#----------------------------------------------------------------
```

## 3. Mixing Job Definitions

### A. Basic Mixing Configuration

Mixing jobs combine signal events with pileup backgrounds from multiple sources:

```json
{
  "input_data": ["dts.mu2e.CeEndpoint.MDC2020ar.art"],
  "mubeam_dataset": ["dts.mu2e.MuBeamFlashCat.MDC2020p.art"],
  "elebeam_dataset": ["dts.mu2e.EleBeamFlashCat.MDC2020p.art"], 
  "neutrals_dataset": ["dts.mu2e.NeutralsFlashCat.MDC2020p.art"],
  "mustop_dataset": ["dts.mu2e.MuStopPileupCat.MDC2020p.art"],
  "mubeam_count": [1],
  "elebeam_count": [25],
  "neutrals_count": [50],
  "mustop_count": [2],
  "dsconf": ["MDC2020aw_best_v1_3"],
  "pbeam": ["Mix1BB", "Mix2BB"],
  "simjob_setup": ["/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020aw/setup.sh"],
  "fcl": ["Production/JobConfig/mixing/Mix.fcl"],
  "merge_events": [2000],
  "owner": ["mu2e"],
  "inloc": ["tape"],
  "outloc": ["tape"]
}
```

### B. Generate Mixing Job Definitions

```bash
# 1. Expand the mixing template to individual configurations
./json_expander.py --json data/mix.json --output expanded_mix.json --mixing

# 2. Generate jobdef for a specific mixing configuration
./json2jobdef.py --json data/mix.json --index 0

# 3. Generate multiple jobdefs in batch
for i in {0..7}; do
  ./json2jobdef.py --json data/mix.json --index $i
done
```

### C. What Mixing Jobs Generate

Each mixing job definition creates:

**1. Job Definition Tarball (`cnf.*.tar`)**
- `jobpars.json`: Contains auxiliary files for maximum flexibility
- FCL templates with mixing-specific configurations
- Setup scripts and metadata

**2. FCL Configuration (`cnf.*.fcl`)**
- Uses only the **requested counts** from JSON (e.g., 25 elebeam files)
- Includes proper `xroot://` paths for grid access
- Applies beam-specific configurations (OneBB.fcl, TwoBB.fcl)

### D. Mixing Job Types

Different `pbeam` configurations generate different mixing scenarios:

- **`Mix1BB`**: Single bunch beam configuration → `OneBB.fcl`
- **`Mix2BB`**: Two bunch beam configuration → `TwoBB.fcl`

### E. Successful Mixing Examples

#### MDC2020ba_best_v1_3 Configuration

The following mixing configurations have been successfully tested and verified:

```bash
# Set up environment first
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops

# Generate all 8 mixing configurations for MDC2020ba_best_v1_3
python3 json2jobdef.py --json ../Production/data/mix.json --dsconf MDC2020ba_best_v1_3
```

**Successfully Generated Configurations:**
1. **ensembleMDS2bMix1BB** ✅
2. **ensembleMDS2bMix2BB** ✅  
3. **DIOtail95Mix1BB** ✅
4. **DIOtail95Mix2BB** ✅
5. **CePLeadingLogMix1BB** ✅
6. **CePLeadingLogMix2BB** ✅
7. **CeMLeadingLogMix1BB** ✅
8. **CeMLeadingLogMix2BB** ✅

**Example Output for DIOtail95Mix1BB:**
```
Processing configuration 2: DIOtail95Mix1BB
Input data: dts.mu2e.DIOtail95.MDC2020at.art
Merge events: 2000
Pileup mixers:
  MuBeam: 1 file
  EleBeam: 25 files  
  Neutrals: 50 files
  MuStop: 2 files
Generated: cnf.oksuzian.DIOtail95Mix1BB.MDC2020ba_best_v1_3.0.tar
Generated: cnf.oksuzian.DIOtail95Mix1BB.MDC2020ba_best_v1_3.0.fcl
```

**Key Features Confirmed:**
- ✅ **baseSeed handling** - No errors across all configurations
- ✅ **MaxEventsToSkip** - Correctly propagated from template.fcl
- ✅ **Merge factor calculation** - Accurate file selection based on event counts
- ✅ **Auxiliary file handling** - Proper pileup file catalogs
- ✅ **FCL generation** - Complete configurations ready for production

## 4. JSON Configuration Expansion

### A. Parameter Space Exploration

Generate multiple job configurations from templates with parameter variations:

```bash
# Basic expansion - generate all combinations of list parameters
./json_expander.py --json data/mix.json --output expanded_configs.json

# With mixing-specific enhancements
./json_expander.py --json data/mix.json --output mixing_configs.json --mixing
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

### C. Production Workflow Integration

```bash
# 1. Generate all job configurations
./json_expander.py --json campaign_template.json --output all_jobs.json --mixing

# 2. Process each configuration
for i in $(seq 0 23); do
  python3 json2jobdef.py --json all_jobs.json --index $i
done

# 3. Create batch execution list
ls cnf.*.tar > batch_jobdefs.txt
```

## 5. Production Job Execution

### A. Basic Execution

Execute production workflows from job definition files:

```bash
# First, set up the required environment
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops

# Set the job index environment variable (required for production)
export fname=etc.mu2e.index.000.0000000.txt

# Run a production job with dry-run mode
./jobdefs_runner.py --jobdefs jobdefs_MDC2020aw.txt --ignore-jobdef-setup --dry-run --nevts 5
```

### B. What `jobdefs_runner.py` Does

1. **Token Validation** - Verifies grid authentication
2. **Job Parsing** - Extracts parameters from jobdefs file using the `fname` index
3. **File Download** - Downloads job definition tarball using `mdh copy-file`
4. **FCL Generation** - Creates FCL with proper XrootD protocol for input files
5. **Job Execution** - Runs `mu2e` with the generated configuration
6. **Output Management** - Handles output files and prepares for SAM submission

### C. Command Line Options

```bash
./jobdefs_runner.py --help

Options:
  --jobdefs JOBDEFS         Path to the jobdefs_*.txt file (required)
  --ignore-jobdef-setup     Skip the SimJob environment setup
  --dry-run                 Print commands without running pushOutput
  --nevts NEVTS            Number of events to process (-1 for all)
  --copy-input             Copy input files locally using mdh
```

### D. Example jobdefs File Format

```text
cnf.mu2e.RPCInternal.MDC2020aw.0.tar 2000 tape tape
```

Format: `{tarball_name} {total_jobs} {input_location} {output_location}`

### E. Production Grid Usage

For actual grid submission (without `--dry-run`):

```bash
# Set up grid environment
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops

# Production execution
export fname=etc.mu2e.index.000.0000042.txt  # Job index from grid system
./jobdefs_runner.py --jobdefs jobdefs_MDC2020aw.txt --nevts -1

# This will:
# 1. Process all events (-1 means no limit)
# 2. Generate output .art files
# 3. Automatically run pushOutput to submit to SAM
# 4. Handle log file management
```

## 6. Advanced Examples

### A. Multi-job Production Setup

```json
[
  {
    "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh",
    "fcl": "Production/JobConfig/beam/BeamResampler.fcl",
    "dsconf": "MDC2020az",
    "desc": "BeamResampling",
    "outloc": "tape",
    "owner": "mu2e",
    "njobs": 100,
    "run": 1001,
    "events": 1000000,
    "input_data": "sim.mu2e.BeamData.MDC2020aa.art"
  },
  {
    "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh",
    "fcl": "Production/JobConfig/cosmic/CosmicResampler.fcl",
    "dsconf": "MDC2020az", 
    "desc": "CosmicResampling",
    "outloc": "tape",
    "owner": "mu2e",
    "njobs": 50,
    "run": 1002,
    "events": 500000,
    "input_data": "sim.mu2e.CosmicData.MDC2020aa.art"
  }
]
```

### B. Custom FCL Overrides

The new template-based approach handles FCL overrides elegantly:

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

**Problem**: `samweb: command not found`
```bash
# Solution: Set up Mu2e environment
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
```

**Problem**: `fhicl-get: command not found`
```bash
# Solution: Ensure OfflineOps is set up
muse setup ops
which fhicl-get
```

#### baseSeed Errors

**Problem**: `can't obtain string from nil` during mu2e execution
```bash
# Solution: This has been fixed in the current version
# The tools now correctly handle SeedService.baseSeed
# Verify with: grep "baseSeed" cnf.*.fcl
```

#### File Access Issues

**Problem**: `mdh: command not found`
```bash
# Solution: Set up Mu2e environment
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
```

## 8. Key Features

### A. Production-Ready Tools

- ✅ **Complete mixing job support** with auxiliary file catalogs
- ✅ **JSON-based configuration** with parameter expansion
- ✅ **XrootD path generation** for proper file access
- ✅ **Production parity** verified against existing production tools
- ✅ **Debugging support** with `--no-cleanup` option
- ✅ **Environment setup** with proper Mu2e tools integration
- ✅ **Smart FCL templates** with `--embed template.fcl` approach
- ✅ **Perfect parity testing** between Python and Perl implementations

### B. Parity Testing

The Python implementation has been thoroughly tested for parity with the Perl `mu2ejobdef` tool:

```bash
# Run parity test to verify Python and Perl versions produce identical output
python3 parity_test.py --json first_entry.json

# This will:
# 1. Generate job definition using Python json2jobdef.py
# 2. Generate job definition using Perl mu2ejobdef
# 3. Compare both outputs for byte-for-byte parity
# 4. Report success/failure with detailed error information
```

**Parity Test Results:**
- ✅ **jobpars.json**: Identical content between Python and Perl versions
- ✅ **mu2e.fcl**: Identical content between Python and Perl versions  
- ✅ **Template handling**: Both use `--embed template.fcl` approach
- ✅ **FCL overrides**: Correctly applied in both versions
- ✅ **Environment handling**: Both versions work with same Mu2e setup

### C. Successfully Tested Workflows

- ✅ **Complete mixing job support** with auxiliary file catalogs
- ✅ **JSON-based configuration** with parameter expansion
- ✅ **XrootD path generation** for proper file access
- ✅ **Production parity** verified against existing production tools
- ✅ **Debugging support** with `--no-cleanup` option
- ✅ **Environment setup** with proper Mu2e tools integration

### B. Successfully Tested Workflows

- ✅ **Mixing jobdef generation** from JSON templates
- ✅ **FCL generation** with correct auxiliary file counts  
- ✅ **JSON expansion** for parameter space exploration
- ✅ **Batch processing** for production campaigns
- ✅ **Jobpars.json verification** against production files
- ✅ **MDC2020ba_best_v1_3** mixing configurations (all 8 types)
- ✅ **baseSeed handling** - No more mu2e execution errors
- ✅ **Environment integration** with muse setup ops

The Python implementations provide complete production functionality with better maintainability and debugging capabilities. All mixing configurations for MDC2020ba_best_v1_3 have been successfully tested and verified.

## Environment Setup

### Required Mu2e Environment

Before using the production tools, you must set up the Mu2e environment:

```bash
# 1. Source the Mu2e setup script
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh

# 2. Set up the OfflineOps environment
muse setup ops

# 3. Verify the environment is ready
which samweb
which fhicl-get
which mdh
```

**Important**: The `muse setup ops` command is essential for accessing SAMweb, FHiCL tools, and other Mu2e utilities.

### Environment Verification

After setup, verify these tools are available:
- `samweb` - Scientific Asset Management client
- `fhicl-get` - FHiCL configuration parser
- `mdh` - Mu2e Data Handling utilities
- `python3` - Python 3 interpreter
