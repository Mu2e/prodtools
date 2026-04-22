#!/usr/bin/env python3
"""
json2jobdef.py: JSON to jobdef generator.

Usage:
  - As module:   python3 -m mu2e_poms_util.json2jobdef --help
  - Direct file: python3 mu2e_poms_util/json2jobdef.py --help
"""
import os, sys
import random
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import subprocess
from pathlib import Path
from utils.prod_utils import *
from utils.mixing_utils import *
from utils.config_utils import get_tarball_desc, prepare_fields_for_job
from utils.jobquery import Mu2eJobPars
from utils.jobdef import create_jobdef, get_output_dataset_names
from utils.samweb_wrapper import list_files, count_files


def _write_random_selection(out_f, query: str, total_needed: int, seed_source: str):
    """Write deterministic pseudo-random selection of files."""
    files = list_files(query)
    if not files:
        raise ValueError(f"No files returned for query: {query}")

    # Sort before shuffling to make output deterministic independent of SAM order
    ordered = sorted(files)
    rng = random.Random(seed_source)
    rng.shuffle(ordered)

    produced = 0
    idx = 0
    count = len(ordered)
    while produced < total_needed:
        out_f.write(ordered[idx] + '\n')
        produced += 1
        idx = (idx + 1) % count

def _configure_chunk_mode(config):
    """Handle `input_data = {"<path>": {"chunk_lines": N}}`.

    Doesn't pre-split. Records the source path + chunk size in
    `config['chunk_mode']` so the tarball carries it into jobpars;
    at grid runtime, `runmu2e` extracts the per-job slice from the
    cvmfs source before invoking mu2e. njobs = ceil(lines/chunk_lines)
    is computed here and written into the jobdefs_list.json entry.

    Every job's FCL points at the same local filename (default:
    `chunk.txt`) via fcl_overrides; the per-job content is created
    fresh on the grid worker.
    """
    input_data = config['input_data']
    if len(input_data) != 1:
        raise ValueError("chunk_lines input_data must have exactly one source file")

    src_str, spec = next(iter(input_data.items()))
    src = Path(src_str)
    if not src.is_file():
        raise ValueError(f"chunk_lines source file not found: {src}")

    chunk_lines = int(spec['chunk_lines'])
    if chunk_lines < 1:
        raise ValueError(f"chunk_lines must be >= 1, got {chunk_lines}")
    with src.open() as f:
        line_count = sum(1 for _ in f)
    njobs = (line_count + chunk_lines - 1) // chunk_lines

    local_chunk = 'chunk.txt'
    config['njobs'] = njobs
    config.setdefault('fcl_overrides', {})
    config['fcl_overrides'].setdefault('source.fileNames', [local_chunk])
    config['chunk_mode'] = {
        'source': str(src),
        'lines': chunk_lines,
        'local_filename': local_chunk,
    }
    # No inputs.txt — there are no SAM-tracked inputs; runmu2e materializes
    # the per-job chunk directly from cvmfs at job time.


def _split_text_file_input(config):
    """Handle `input_data = {"<path>": {"split_lines": N}}`.

    Splits a local text file from the given path into N-line chunks, writes
    them into a `chunks/` subdirectory of cwd, and writes basenames to
    `inputs.txt`. Runtime must pass `--default-location dir:<cwd>/chunks/`
    to jobfcl so the basenames resolve.

    Used for text-driven primary sources like PBISequence.
    """
    input_data = config['input_data']
    if len(input_data) != 1:
        raise ValueError("split_lines input_data must have exactly one source file")

    src_str, spec = next(iter(input_data.items()))
    src = Path(src_str)
    if not src.is_file():
        raise ValueError(f"split_lines source file not found: {src}")

    split_lines = int(spec['split_lines'])
    chunks_dir = Path('chunks')
    chunks_dir.mkdir(exist_ok=True)

    # Chunk sequencer follows Mu2e convention: <RRRRRR>_<SSSSSSSS> (run_subrun),
    # zero-padded. Combined with sequencer_from_index, each output inherits the
    # run from its chunk's basename and substitutes job index as the subrun,
    # producing standard filenames like
    #     dts.mu2e.PBINormal_33344.MDC2025ai.001430_00000000.art
    run = int(config.get('run', 0))
    slug = f"dts.{config.get('owner', 'mu2e')}.{config['desc']}.{config['dsconf']}"
    lines = src.read_text().splitlines()
    chunk_names = []
    for i in range(0, len(lines), split_lines):
        idx = i // split_lines
        chunk_seq = f"{run:06d}_{idx:08d}"
        chunk_path = chunks_dir / f"{slug}.{chunk_seq}.txt"
        chunk_path.write_text("\n".join(lines[i:i + split_lines]) + "\n")
        chunk_names.append(chunk_path.name)

    with open('inputs.txt', 'w') as f:
        for name in chunk_names:
            f.write(name + '\n')

    # split_lines almost always wants per-job sequencers from the job index
    # (otherwise every job output gets the same sequencer as chunk 00 and they
    # collide). User can set sequencer_from_index: false explicitly to opt out.
    config.setdefault('sequencer_from_index', True)


def _create_inputs_file(config, exclude_files=None):
    """Helper: create inputs.txt file from datasets with merge factors.

    Supports optional random sampling by allowing input_data values to be
    dictionaries with the following keys:
        - count (int): number of files to use (required)
        - random (bool): if True, choose a deterministic pseudo-random sample

    Also supports text-file splitting when the value is a dict with
    `split_lines`: the source file is split locally into N-line chunks and
    inputs.txt is populated with the chunk basenames.

    Args:
        config: Job configuration dictionary.
        exclude_files: Optional set of filenames to omit (used by --extend).

    Example:
        "input_data": {
            "sim.mu2e.NeutralsFlash.MDC2025ac.art": {
                "count": 100,
                "random": true
            }
        }

        "input_data": {
            "/cvmfs/mu2e.opensciencegrid.org/DataFiles/PBI/PBI_Normal_33344.txt": {
                "split_lines": 1000
            }
        }
    """
    input_data = config.get('input_data')
    if not isinstance(input_data, dict):
        raise ValueError(f"input_data must be a dict, got {type(input_data)}")

    first_value = next(iter(input_data.values()), None)

    # Chunk-on-grid shape: {"<path>": {"chunk_lines": N}}. No pre-split,
    # no inputs.txt — runmu2e extracts each job's slice at runtime.
    if isinstance(first_value, dict) and 'chunk_lines' in first_value:
        _configure_chunk_mode(config)
        return

    # Text-file split shape: pre-split into chunks at submit time.
    if isinstance(first_value, dict) and 'split_lines' in first_value:
        _split_text_file_input(config)
        return

    # Local-dir shape: if inloc is "dir:<path>", treat input_data keys as
    # basenames and write them verbatim (no SAM lookup). At runtime,
    # jobfcl prepends the directory prefix. Used for cvmfs-resident
    # inputs that aren't in SAM:
    #     "inloc": "dir:/cvmfs/.../DataFiles/PBI/",
    #     "input_data": {"PBI_Normal_33344.txt": 1}
    inloc = config.get('inloc', '')
    if isinstance(inloc, str) and inloc.startswith('dir:'):
        with open('inputs.txt', 'w') as f:
            for key in input_data.keys():
                f.write(key + '\n')
        return
    
    # By default, include all files in the dataset. Older behavior applied an
    # event_count>0 filter; that is now controlled by an explicit flag so that
    # zero-event files are not silently dropped.
    event_count_positive = bool(config.get('_event_count_positive'))
    query_template = "dh.dataset={}"
    if event_count_positive:
        query_template += " and event_count>0"
    
    with open('inputs.txt', 'w') as out_f:
        for dataset, merge_factor in input_data.items():
            random_spec = {}
            if isinstance(merge_factor, dict):
                random_spec = merge_factor
                merge_factor = merge_factor.get('count') or merge_factor.get('merge_factor')
                if merge_factor is None:
                    raise ValueError(f"input_data spec for {dataset} must include 'count' when using dict form")

            query = query_template.format(dataset)

            if random_spec.get('random'):
                per_job = int(merge_factor)
                try:
                    njobs = int(config.get('njobs', 1))
                except (TypeError, ValueError):
                    njobs = 1

                if njobs == -1:
                    available = count_files(query)
                    njobs = max(1, available // max(per_job, 1))

                total_needed = per_job * max(njobs, 1)
                seed_source = (
                    f"{config.get('owner','')}.{config.get('desc','')}.{config.get('dsconf','')}"
                    f".{dataset}.{per_job}.{njobs}"
                )
                _write_random_selection(out_f, query, total_needed, seed_source)
            else:
                files = list_files(query)
                for filepath in files:
                    if exclude_files and filepath in exclude_files:
                        continue
                    out_f.write(filepath + '\n')

def _next_version(config):
    """Find the next available version number for this job definition tarball.

    Queries SAM for existing files in the tarball dataset and returns
    max(existing versions) + 1, or 0 if none exist.
    """
    desc = get_tarball_desc(config) or config['desc']
    dataset = f"cnf.{config['owner']}.{desc}.{config['dsconf']}.tar"

    try:
        files = list_files(f"dh.dataset={dataset}")
    except Exception:
        return 0

    if not files:
        return 0

    max_version = -1
    for fname in files:
        parts = fname.split('.')
        try:
            version = int(parts[-2])
            max_version = max(max_version, version)
        except (ValueError, IndexError):
            continue

    return max_version + 1


def _compute_extend_exclusions(config):
    """Derive output datasets, query SAM for already-processed parents,
    auto-increment the tarball version, and return the set of files to
    exclude from inputs.txt.

    Side-effect: updates config['version'] to the next available number.
    """
    output_datasets = get_output_dataset_names(config)
    if not output_datasets:
        sys.exit("--extend: could not determine output dataset names from FCL")

    exclude_files = set()
    for ds in output_datasets:
        query = f"isparentof: (dh.dataset {ds})"
        parents = list_files(query)
        exclude_files.update(parents)
        print(f"  Output dataset {ds}: {len(parents)} already-processed input files")

    new_version = _next_version(config)
    config['version'] = new_version
    print(f"  Auto-incremented version to {new_version}")

    return exclude_files


def get_parfile_name(config):
    """Generate consistent parfile name from config."""
    desc = get_tarball_desc(config) or config['desc']
    version = config.get('version', 0)
    return f"cnf.{config['owner']}.{desc}.{config['dsconf']}.{version}.tar"

def get_fcl_name(config):
    """Generate consistent FCL filename from config."""
    desc = get_tarball_desc(config) or config['desc']
    version = config.get('version', 0)
    return f"cnf.{config['owner']}.{desc}.{config['dsconf']}.{version}.fcl"

def validate_required_fields(config, required_fields=None):
    """Validate that config has all required fields."""
    if required_fields is None:
        required_fields = ('simjob_setup', 'fcl', 'dsconf', 'outloc')
    
    for req in required_fields:
        if not config.get(req):
            sys.exit(f"Missing required field: {req}")

def determine_job_type(config):
    """Determine the job type based on config contents.

    Returns:
        'chunk'     - On-the-fly chunking (chunk_lines shape — no inputs.txt)
        'resampler' - Resampling jobs with resampler_name
        'merge'     - File merging jobs with input_data dict
        'mixing'    - Pileup mixing jobs with pbeam
        'stage1'    - Primary simulation jobs (cosmic, beam, etc.)

    Note: Order matters. chunk and resampler must be checked before
    the generic `merge` fallback that only tests for a dict input_data.
    """
    input_data = config.get('input_data')
    if isinstance(input_data, dict):
        first_value = next(iter(input_data.values()), None)
        if isinstance(first_value, dict) and 'chunk_lines' in first_value:
            return 'chunk'
    if 'resampler_name' in config:
        return 'resampler'
    elif 'pbeam' in config:
        return 'mixing'
    elif isinstance(input_data, dict):
        return 'merge'
    else:
        return 'stage1'

def build_jobdef(config, job_args, json_output=False):
    # Create jobdef using the embed approach with custom template to preserve fcl_overrides
    # For mixing jobs, template.fcl is already created by build_pileup_args
    # For non-mixing jobs, create the template here
    fcl_path = config['fcl']
    job_type = determine_job_type(config)

    if job_type != 'mixing':
        write_fcl_template(fcl_path, config.get('fcl_overrides', {}))
    
    # Add MaxEventsToSkip parameter for resampler jobs after template is written
    if job_type == 'resampler':
        with open('template.fcl', 'a') as f:
            f.write(f"physics.filters.{config['resampler_name']}.mu2e.MaxEventsToSkip: {config['_max_events_to_skip']}\n")
    
    # Build the Perl commands that would be equivalent (always build for potential display)
    cmd_parts = [
        'mu2ejobdef',
        '--setup', config['simjob_setup'],
        '--dsconf', config['dsconf'],
        '--desc', config['desc'],
        '--dsowner', config['owner']
    ]
    
    # Only add --run-number if it's present in config
    if 'run' in config:
        cmd_parts.extend(['--run-number', str(config['run'])])
    
    # Only add --events-per-job if it's present in config
    if 'events' in config:
        cmd_parts.extend(['--events-per-job', str(config['events'])])
    
    # Add job_args and template
    cmd_parts.extend(job_args)
    cmd_parts.extend(['--embed', 'template.fcl'])
    
    # Always show the mu2ejobdef equivalent command when verbose logging is enabled
    import logging
    if logging.getLogger().level <= logging.DEBUG:
        print(f"🐪 mu2ejobdef equivalent command: {' '.join(cmd_parts)}")
    
    # Now create jobdef using the template.fcl
    create_jobdef(config, fcl_path='template.fcl', job_args=job_args, embed=True, quiet=json_output)
    
    # Get the parfile name for both modes
    parfile_name = get_parfile_name(config)
    fcl_file = get_fcl_name(config)
    
    if json_output:
        # Return structured data for machine consumption
        result = {
            'success': True,
            'perl_commands': [
                {
                    'type': 'mu2ejobdef',
                    'command': ' '.join(cmd_parts),
                    'desc': config['desc'],
                    'simjob_setup': config['simjob_setup']
                },
                {
                    'type': 'mu2ejobfcl',
                    'command': f"mu2ejobfcl --jobdef {parfile_name} --default-location tape --default-protocol root --index 0 > {fcl_file}",
                    'desc': config['desc'],
                    'index': 0
                }
            ]
        }
        return result
    else:
        # Human-readable output (current behavior)
        print(f"Python mu2ejobdef equivalent command: {' '.join(cmd_parts)}")
        print(f"Running Perl equivalent of: mu2ejobfcl --jobdef {parfile_name} --default-location tape --default-protocol root --index 0 > {fcl_file}")
        return None

def append_jobdef(config, jobdefs_file=None):
    """
    Append job information to a jobdefs file in JSON format.
    Handles both simple and complex outloc structures.
    """
    parfile_name = get_parfile_name(config)
    is_generic = config.get('generic_tarball', False)

    # Create JSON structure for the job definition
    jobdef_entry = {
        "tarball": parfile_name,
        "inloc": config['inloc'],
        "outputs": []
    }

    # Generic tarballs have no pre-determined job count — omit njobs so
    # runmu2e detects direct-input mode (absence of njobs is the trigger)
    if not is_generic:
        njobs = config['njobs']
        if njobs == -1:
            jp = Mu2eJobPars(parfile_name)
            njobs = jp.njobs()
            print(f"Queried job count: {njobs}")
        jobdef_entry["njobs"] = njobs
    
    # Handle outloc - must be dict with dataset-specific locations
    outloc = config['outloc']
    
    if not isinstance(outloc, dict):
        print(f"Warning: outloc must be a dictionary with dataset-specific locations for {config.get('desc', 'unknown')}")
        return
    
    # Add each dataset with its location
    for dataset_name, location in outloc.items():
        jobdef_entry["outputs"].append({
            "dataset": dataset_name,
            "location": location
        })
    
    # Write JSON entry to file
    _write_jobdef_json_entry(jobdef_entry, jobdefs_file)

def _write_jobdef_json_entry(jobdef_entry, jobdefs_file=None):
    """Helper function to write jobdef entries in JSON format."""
    # Use provided jobdefs file or default to jobdefs_list.json
    if jobdefs_file:
        dsconf_file = Path(jobdefs_file)
    else:
        dsconf_file = Path("jobdefs_list.json")
    
    # Check if file exists and load existing entries
    existing_entries = []
    if dsconf_file.exists():
        try:
            existing_content = dsconf_file.read_text()
            if existing_content.strip():
                existing_entries = json.loads(existing_content)
                if not isinstance(existing_entries, list):
                    existing_entries = [existing_entries]
        except json.JSONDecodeError:
            print(f"Warning: Could not parse existing {dsconf_file}, starting fresh")
            existing_entries = []
    
    # Check for duplicate tarball entries
    tarball_name = jobdef_entry["tarball"]
    for existing in existing_entries:
        if existing.get("tarball") == tarball_name:
            print(f"Entry already exists in {dsconf_file}")
            return
    
    # Add new entry and write back to file
    existing_entries.append(jobdef_entry)
    
    with open(dsconf_file, 'w') as f:
        json.dump(existing_entries, f, indent=2)
    
    print(f"Added JSON entry for {tarball_name} to {dsconf_file}")

def main():
    p = argparse.ArgumentParser(description='Generate Mu2e job definitions from JSON configuration')
    p.add_argument('--json', required=True, help='Input JSON file')
    p.add_argument('--desc', type=str, help='Dataset descriptor')
    p.add_argument('--dsconf', type=str, help='Dataset configuration')
    p.add_argument('--index', type=int, help='Entry index in JSON list')
    p.add_argument('--pushout', action='store_true', help='Enable SAM pushOutput')
    p.add_argument('--prod', action='store_true', help='Production mode: enable pushout and run mkidxdef after generation')
    p.add_argument('--verbose', action='store_true', help='Verbose logging')
    p.add_argument('--no-cleanup', action='store_true', help='Keep temporary files (inputs.txt, template.fcl, *Cat.txt)')
    p.add_argument('--jobdefs', help='Custom filename for jobdefs list (default: jobdefs_list.txt)')
    p.add_argument('--json-output', action='store_true', help='Output structured JSON instead of human-readable text')
    p.add_argument('--extend', action='store_true',
                   help='Create delta job definition excluding already-processed inputs. '
                        'Auto-increments tarball version.')
    p.add_argument('--event-count-positive', action='store_true',
                   help='When building inputs.txt, require event_count>0 in SAM queries '
                        '(legacy behavior). Default is to include all files.')
    p.add_argument('--ignore-empty', action='store_true',
                   help='Skip entries whose input datasets have no files instead of failing')
    args = p.parse_args()
    
    # If --prod is specified, enable pushout
    if args.prod:
        args.pushout = True

    setup_logging(args.verbose)
    
    # Load and expand the JSON configuration once
    expanded_configs = load_json(Path(args.json))
    
    # If both desc and dsconf are specified, process single entry
    if args.desc and args.dsconf and args.index is None:
        config = find_json_entry(expanded_configs, args.desc, args.dsconf, None)
        config['_event_count_positive'] = args.event_count_positive
        process_single_entry(
            config,
            json_output=True,
            pushout=args.pushout,
            no_cleanup=args.no_cleanup,
            jobdefs_list=args.jobdefs,
            extend=args.extend,
            ignore_empty=args.ignore_empty,
        )
    # If dsconf is specified but no desc and no index, process all entries for that dsconf
    elif args.dsconf and args.desc is None and args.index is None:
        process_all_for_dsconf(expanded_configs, args.dsconf, args)
    # If only index is specified, process single entry by index
    elif args.index is not None and args.desc is None and args.dsconf is None:
        config = find_json_entry(expanded_configs, None, None, args.index)
        config['_event_count_positive'] = args.event_count_positive
        process_single_entry(
            config,
            json_output=True,
            pushout=args.pushout,
            no_cleanup=args.no_cleanup,
            jobdefs_list=args.jobdefs,
            extend=args.extend,
            ignore_empty=args.ignore_empty,
        )
    else:
        # No filtering specified, show usage
        sys.exit("Please specify either --desc AND --dsconf, --dsconf only, or --index only")
    
    # If --prod mode, create index definition after generation
    if args.prod:
        from utils.prod_utils import create_index_definition
        
        jobdefs_file = args.jobdefs if args.jobdefs else 'jobdefs_list.txt'
        print(f"\n{'='*60}")
        print(f"Creating index definition from {jobdefs_file}")
        print(f"{'='*60}")
        
        # Load jobdefs to calculate total jobs
        with open(jobdefs_file, 'r') as f:
            jobdefs = json.load(f)
        
        total_jobs = sum(j.get('njobs', 0) for j in jobdefs)
        
        # Print summary
        for i, j in enumerate(jobdefs):
            outputs = ", ".join(f"{o['dataset']}→{o['location']}" for o in j['outputs'])
            print(f"[{i}] {j['tarball']}: {j['njobs']} jobs, input={j['inloc']}, outputs={outputs}")
        
        print(f"\nTotal: {total_jobs} jobs")
        
        # Create index definition
        map_stem = Path(jobdefs_file).stem
        create_index_definition(map_stem, total_jobs, "etc.mu2e.index.000.txt")

def process_single_entry(config, json_output=True, pushout=False, no_cleanup=True,
                         jobdefs_list=None, extend=False, ignore_empty=False):
    """Process a single configuration entry (original behavior)"""
    validate_required_fields(config)
    config['owner'] = config.get('owner', os.getenv('USER', 'mu2e').replace('mu2epro', 'mu2e'))
    config['inloc'] = config.get('inloc', 'none')
    config['njobs'] = config.get('njobs', -1)

    # Generic tarball mode: no input_data, {desc} deferred for runtime resolution
    if config.get('generic_tarball'):
        config['_defer_keys'] = {'desc'}
        config['njobs'] = 0
    
    # Auto-generate desc from input_data if desc is missing
    # This extracts the 3rd field from dataset name (e.g., "ensembleMDS3a" from "dts.mu2e.ensembleMDS3a.MDC2025af.art")
    if not config.get('desc'):
        config = prepare_fields_for_job(config, job_type='standard')
    
    # Store the original FCL path for source type detection
    original_fcl_path = config['fcl']
    
    # Extend mode: exclude already-processed input files and auto-increment version
    exclude_files = None
    if extend:
        exclude_files = _compute_extend_exclusions(config)
    
    # Create inputs.txt first if needed
    if config.get('input_data'):
        _create_inputs_file(config, exclude_files=exclude_files)

    # Check for empty inputs
    if Path('inputs.txt').exists():
        remaining = sum(1 for _ in open('inputs.txt'))
        if remaining == 0:
            if extend:
                print(f"  Extend summary: {len(exclude_files)} excluded, 0 remaining input files")
            if ignore_empty:
                print(f"  Skipping {config.get('desc', 'unknown')}: no input files available")
                return None
            elif extend:
                sys.exit("--extend: no new input files to process")

    if extend and exclude_files is not None:
        remaining = sum(1 for _ in open('inputs.txt')) if Path('inputs.txt').exists() else 0
        print(f"  Extend summary: {len(exclude_files)} excluded, {remaining} remaining input files")
    
    job_args = []
    
    job_type = determine_job_type(config)
    
    if job_type == 'resampler':
        # Resampler jobs: calculate MaxEventsToSkip parameter and merge factor
        try:
            # input_data should be a dict, use first dataset for calculation
            input_data = config['input_data']
            if not isinstance(input_data, dict):
                raise ValueError(f"input_data must be a dict, got {type(input_data)}")
            
            first_dataset = list(input_data.keys())[0]
            nfiles, nevts = get_def_counts(first_dataset)
            skip = nevts // nfiles
            # Store skip value for later addition to template
            config['_max_events_to_skip'] = skip
        except Exception as e:
            print(f"Warning: Could not calculate MaxEventsToSkip for {first_dataset}: {e}")
        
        # Get merge_factor from input_data dict
        merge_factor = calculate_merge_factor(config)
        job_args = ['--auxinput', f"{merge_factor}:physics.filters.{config['resampler_name']}.fileNames:inputs.txt"]
        
    elif job_type == 'merge':
        # Merge jobs: simple file merging
        merge_factor = calculate_merge_factor(config)
        job_args = ['--inputs','inputs.txt','--merge-factor', str(merge_factor)]

    elif job_type == 'chunk':
        # Chunk-on-grid: no inputs.txt, no --merge-factor. The per-job
        # slice is materialized at runtime by runmu2e. tbs.chunk_mode
        # carries the source path + chunk size through jobpars.
        job_args = []
        
    elif job_type == 'mixing':
        # Mixing jobs: add MaxEventsToSkip parameter to template
        merge_factor = calculate_merge_factor(config)
        job_args = ['--inputs','inputs.txt','--merge-factor', str(merge_factor)]
        job_args += build_pileup_args(config)
        
    else:
        # Stage1 jobs: no special processing needed
        job_args = []
    
    # build_jobdef handles FCL template creation for non-mixing jobs
    # Always fcl-analyze the ORIGINAL FCL file for job structure understanding
    result = build_jobdef(config, job_args, json_output=json_output)
    
    append_jobdef(config, jobdefs_list)    
    # Get the parfile name for pushout operations
    parfile_name = get_parfile_name(config)
    
    # Handle pushout regardless of json_output setting
    if pushout:
        # First check if the local file exists before attempting SAM operations
        if not Path(parfile_name).exists():
            print(f"Warning: Local file {parfile_name} not found, skipping pushout")
        else:
            # Push file to SAM if it doesn't already exist there
            from utils.samweb_wrapper import locate_file
            
            # Check if file exists on SAM
            loc = locate_file(parfile_name)
            if not loc:
                # File doesn't exist on SAM, push it
                print(f"Pushing {parfile_name} to SAM...")
                with open('outputs.txt', 'w') as f:
                    f.write(f"disk {parfile_name} none\n")
                run('pushOutput outputs.txt', shell=True)
            else:
                # File exists on SAM, don't push
                print(f"File {parfile_name} already exists on SAM, skipping push")
    
    if not json_output:
        # Only output human-readable text when not using JSON output
        print(json.dumps(config, indent=2, sort_keys=True))
        
        write_fcl(parfile_name, config.get('inloc', 'tape'), 'root')
        print(f"Generated: {parfile_name}")
    
    # Clean up temporary files AFTER job definition is created (unless --no-cleanup is specified)
    if no_cleanup:
        print("Temporary files kept (--no-cleanup specified)")
    else:
        temp_files = ['inputs.txt', 'template.fcl', 'mubeamCat.txt', 'elebeamCat.txt', 'neutralsCat.txt', 'mustopCat.txt']
        for temp_file in temp_files:
            if Path(temp_file).exists():
                Path(temp_file).unlink()
                print(f"Cleanup: {temp_file}")
    
    return result

def is_already_expanded(configs):
    """Check if the configuration is already expanded (has scalar values, not lists)"""
    if not isinstance(configs, list) or len(configs) == 0:
        return False
    
    # Check all entries, not just the first one
    for i, config in enumerate(configs):
        if not isinstance(config, dict):
            raise ValueError(f"Entry {i} is not a dictionary: {type(config)}")
        
        # Check if this config has lists (needs expansion)
        values = list(config.values())
        has_lists = any(isinstance(v, list) for v in values)
        
        # If any config has lists, the whole configuration needs expansion
        if has_lists:
            return False
    
    # If no configs have lists, they're all already expanded
    return True

def load_json(json_path):
    """Load and expand JSON configuration if needed"""
    json_text = json_path.read_text()
    configs = json.loads(json_text)
    
    # Check if expansion is needed
    if is_already_expanded(configs):
        return configs
    
    # Expand all configurations; mixing vs standard is determined per config from content (e.g. pbeam)
    from utils.mixing_utils import expand_configs
    return expand_configs(configs)

def find_json_entry(configs, desc=None, dsconf=None, index=None):
    """Find a matching JSON entry from configuration list"""
    if index is not None:
        try: 
            return configs[index]
        except IndexError: 
            sys.exit(f"Index {index} out of range.")
    
    matches = [e for e in configs if e.get('desc') == desc and e.get('dsconf') == dsconf]
    if len(matches) != 1:
        sys.exit(f"Expected 1 match for desc={desc}, dsconf={dsconf}; found {len(matches)}.")
    return matches[0]

def process_all_for_dsconf(expanded_configs, dsconf, args):
    """Process all entries matching the specified dsconf and generate job definitions for all permutations"""
    
    # Filter to only entries matching the specified dsconf (exact match)
    matching_configs = [config for config in expanded_configs if config.get('dsconf', '') == dsconf]
    
    if not matching_configs:
        sys.exit(f"No entries found matching dsconf: {dsconf}")
    
    print(f"Found {len(matching_configs)} entries matching dsconf: {dsconf}")
    
    # Process each matching configuration using the existing process_single_entry function
    for i, config in enumerate(matching_configs):
        # Get display desc: use get_tarball_desc (handles tarball_append), or existing desc, or extract from input_data
        display_desc = get_tarball_desc(config) or config.get('desc')
        if not display_desc:
            # Fall back to extracting from input_data
            temp_config = prepare_fields_for_job(config, job_type='standard')
            display_desc = temp_config.get('desc', 'Unknown')
        print(f"\nProcessing entry {i+1}/{len(matching_configs)}: {display_desc}")
        
        # Check required fields before calling process_single_entry
        try:
            validate_required_fields(config)
        except SystemExit as e:
            print(f"Warning: {e}, skipping entry")
            continue
        
        # Propagate CLI options that affect input selection onto the config
        config['_event_count_positive'] = args.event_count_positive

        # Use the existing process_single_entry function
        process_single_entry(
            config,
            json_output=True,
            pushout=args.pushout,
            no_cleanup=True,
            jobdefs_list=args.jobdefs,
            ignore_empty=args.ignore_empty,
        )
        
        # Clean up template.fcl for next iteration (since process_single_entry cleans up)
        if Path('template.fcl').exists():
            Path('template.fcl').unlink()

if __name__ == '__main__':
    main()
