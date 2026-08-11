#!/usr/bin/env python3
"""
json2jobdef.py: JSON to jobdef generator.

Usage (from the repo root, with `muse setup ops` sourced):
  - Wrapper:     bin/json2jobdef --help          # sets up the Mu2e env itself
  - As module:   python3 -m utils.json2jobdef --help
  - Direct file: python3 utils/json2jobdef.py --help
"""
import os, sys
import random
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
from pathlib import Path
from utils.prod_utils import *
from utils.mixing_utils import *
from utils.config_utils import cnf_name, get_tarball_desc, prepare_fields_for_job, normalize_input_data
from utils.jobdesc import (
    RESOURCE_KEYS, firstjob_of, validate_entry_value, validate_window)
from utils.job_common import Mu2eName, default_owner
from utils.jobquery import Mu2eJobPars
from utils.jobdef import create_jobdef, get_output_dataset_names
from utils.jobfcl import validate_output_filenames
from utils.samweb_wrapper import (
    list_files,
    locate_file,
    files_in_dataset,
    parents_of_dataset,
    q_dataset,
)


def _write_random_selection(out_f, files, total_needed: int, seed_source: str):
    """Write deterministic pseudo-random selection from a fetched file list."""
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
    lines = src.read_text().splitlines()
    chunk_names = []
    for i in range(0, len(lines), split_lines):
        idx = i // split_lines
        chunk_seq = f"{run:06d}_{idx:08d}"
        chunk_path = chunks_dir / str(Mu2eName.build(
            tier='dts', owner=config['owner'], description=config['desc'],
            dsconf=config['dsconf'], sequencer=chunk_seq, extension='txt'))
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

    _write_sam_inputs(config, input_data, exclude_files)


def _write_sam_inputs(config, input_data, exclude_files=None):
    """Write inputs.txt by resolving each input_data dataset against SAM.

    Each input_data value is either a plain merge_factor (int) or a dict
    `{"count": N, "random": <bool>, "max_nfiles": M}`. With `random: True`,
    a deterministic pseudo-random sample of `count * njobs` files is
    selected; otherwise list_files() returns all matching files.
    `max_nfiles` (optional, positive int) caps the per-dataset file count
    written to inputs.txt — applied as a deterministic prefix slice of the
    sorted file list (non-random branch) or as an upper bound on
    `total_needed` (random branch). njobs is NOT recomputed; the entry
    author is responsible for keeping `merge_factor * njobs <= max_nfiles`.

    `_event_count_positive` flag in `config` toggles a `event_count>0`
    filter on the SAM query (older behavior applied this implicitly; now
    explicit so zero-event files aren't silently dropped).
    """
    event_count_positive = bool(config.get('_event_count_positive'))

    with open('inputs.txt', 'w') as out_f:
        for spec in normalize_input_data(input_data):
            if spec.per_job is None:
                raise ValueError(f"input_data spec for {spec.source} must include 'count' or 'merge_factor' when using dict form")

            query = q_dataset(spec.source, with_events=event_count_positive)

            if spec.random:
                per_job = spec.per_job
                try:
                    njobs = int(config.get('njobs', 1))
                except (TypeError, ValueError):
                    njobs = 1

                # One SAM evaluation serves both the njobs derivation and
                # the selection (previously count_files + list_files ran
                # the same query twice)
                files = list_files(query)
                if not files:
                    raise ValueError(f"No files returned for query: {query}")

                if njobs == -1:
                    njobs = max(1, len(files) // max(per_job, 1))

                total_needed = per_job * max(njobs, 1)
                if spec.max_nfiles is not None:
                    total_needed = min(total_needed, spec.max_nfiles)
                seed_source = (
                    f"{config.get('owner','')}.{config.get('desc','')}.{config.get('dsconf','')}"
                    f".{spec.source}.{per_job}.{njobs}"
                )
                _write_random_selection(out_f, files, total_needed, seed_source)
            else:
                files = list_files(query)
                if spec.max_nfiles is not None:
                    files = sorted(files)[:spec.max_nfiles]
                for filepath in files:
                    if exclude_files and filepath in exclude_files:
                        continue
                    out_f.write(filepath + '\n')

def _next_version(config):
    """Find the next available version number for this job definition tarball.

    Queries SAM for existing files in the tarball dataset and returns
    max(existing versions) + 1, or 0 if none exist.
    """
    dataset = cnf_name(config, 'tar', dataset=True)

    try:
        files = files_in_dataset(dataset)
    except Exception:
        return 0

    if not files:
        return 0

    max_version = -1
    for fname in files:
        try:
            version = Mu2eName.parse(fname).index
        except ValueError:
            continue
        max_version = max(max_version, version)

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
        parents = parents_of_dataset(ds)
        exclude_files.update(parents)
        print(f"  Output dataset {ds}: {len(parents)} already-processed input files")

    new_version = _next_version(config)
    config['version'] = new_version
    print(f"  Auto-incremented version to {new_version}")

    return exclude_files


def get_parfile_name(config):
    """Generate consistent parfile name from config (see config_utils.cnf_name)."""
    return cnf_name(config, 'tar')

def validate_required_fields(config):
    """Validate that config has all required fields, and that the entry
    values it supplies are well formed.

    The value check shares utils/jobdesc.validate_entry_value with
    `submissions set-entry`, so a spelling the operator cannot set on a
    live campaign is also one they cannot enqueue. Unconditional, not
    gated on --enqueue: a misspelled inloc makes file_resolver fall
    through to SAM without complaint, which is just as wrong on a local
    smoke and far harder to notice there.

    Keys are validated only when present — inloc is optional
    (process_single_entry defaults it to 'none'), and the resource keys
    usually come from CLI flags instead.
    """
    for req in ('simjob_setup', 'fcl', 'dsconf', 'outloc'):
        if not config.get(req):
            sys.exit(f"Missing required field: {req}")
    for key in ('inloc',) + RESOURCE_KEYS:
        if key in config:
            try:
                validate_entry_value(key, config[key])
            except ValueError as exc:
                sys.exit(f"Invalid {key}: {exc}")

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
        specs = normalize_input_data(input_data)
        if specs and specs[0].chunk_lines is not None:
            return 'chunk'
    if 'resampler_name' in config:
        return 'resampler'
    elif 'pbeam' in config:
        return 'mixing'
    elif isinstance(input_data, dict):
        return 'merge'
    else:
        return 'stage1'

def build_jobdef(config, job_args):
    # Create jobdef using the embed approach with custom template to preserve fcl_overrides
    # For mixing jobs, template.fcl is already created by build_pileup_args
    # For non-mixing jobs, create the template here
    fcl_path = config['fcl']
    job_type = determine_job_type(config)

    if job_type != 'mixing':
        # Resampler MaxEventsToSkip goes after the overrides (last wins)
        post_lines = []
        if job_type == 'resampler':
            post_lines.append(
                f"physics.filters.{config['resampler_name']}.mu2e.MaxEventsToSkip: {config['_max_events_to_skip']}")
        write_fcl_template(fcl_path, config.get('fcl_overrides', {}),
                           post_lines=post_lines)

    # Perl-equivalent command string. Kept even though create_jobdef echoes
    # its own version: test/parity_test.py consumes this exact string via
    # result['perl_commands'] to run the Perl mu2ejobdef comparison.
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

    # Now create jobdef using the template.fcl
    create_jobdef(config, fcl_path='template.fcl', job_args=job_args, embed=True, quiet=True)

    # Get the parfile name for both modes
    parfile_name = get_parfile_name(config)

    # Build-time guard: ensure every outputs.*.fileName substitutes cleanly.
    # Catches missing fcl_overrides for outputs whose upstream defaults embed
    # a suffix on the desc token (e.g. description-CH) before the cnf is pushed.
    # Skipped for generic tarballs: {desc}/sequencer are deferred to runtime
    # (direct-input mode) by design, so they cannot resolve at build time.
    if not config.get('generic_tarball'):
        try:
            validate_output_filenames(parfile_name)
        except ValueError as e:
            sys.exit(f"json2jobdef: cnf failed output-filename validation: {e}")
    
    # Structured result for machine consumption (parity_test consumes
    # perl_commands to run the Perl mu2ejobdef comparison)
    return {
        'success': True,
        'perl_commands': [
            {
                'type': 'mu2ejobdef',
                'command': ' '.join(cmd_parts),
                'desc': config['desc'],
                'simjob_setup': config['simjob_setup']
            }
        ]
    }

def build_jobdesc(config):
    """Project a build config onto the submission entry (the `jobdesc`).

    Pure: no filesystem writes. The one impure part is the `njobs: -1`
    branch, which asks the freshly-built cnf for its job count.

    Raises ValueError if `outloc` is not a dict — see append_jobdef for
    why that stays a warning on the file-writing path and is fatal on
    the enqueue path.
    """
    parfile_name = get_parfile_name(config)
    is_generic = config.get('generic_tarball', False)

    jobdef_entry = {
        "tarball": parfile_name,
        "inloc": config['inloc'],
        "outputs": []
    }

    # Optional per-entry resource requests pass through to the entry;
    # the submit path reads them via jobdesc.resources_of
    # (CLI flag > entry key > built-in default).
    for key in RESOURCE_KEYS:
        if key in config:
            jobdef_entry[key] = config[key]

    # Draining configuration passes through too, so a draining map comes
    # out of --jobdefs ready to enqueue instead of needing a hand-edit:
    # the submit path reads `input_pattern` (jobdesc.is_draining, the
    # kind discriminator) and `prestage` (submit._validate_draining_entry,
    # and the tape-residency gate in submissions.drain_tick) off the
    # ENTRY, so a value left behind in the JSON config would silently do
    # nothing.
    for key in ('input_pattern', 'prestage'):
        if key in config:
            jobdef_entry[key] = config[key]

    # A draining entry is defined by having an input_pattern and NO index
    # space. Emitting both would leave the entry self-contradictory --
    # is_draining() would say draining while njobs claimed a fixed window
    # -- so refuse rather than write it.
    if 'input_pattern' in config and not is_generic:
        fail("Error: input_pattern requires generic_tarball: true "
             "(a draining entry has no fixed job count)")

    # Optional cnf-index window start (statistics expansion; semantics
    # in utils/jobdesc.py). firstjob_of/validate_window are the single
    # validation authority — shared with the submit path.
    try:
        firstjob = firstjob_of(config)
    except ValueError as e:
        fail(f"Error: {e}")
    if firstjob and is_generic:
        fail("Error: firstjob requires a fixed job count (njobs); "
             "generic tarball entries have no index window")

    # Generic tarballs have no pre-determined job count — omit njobs so
    # runmu2e detects direct-input mode (absence of njobs is the trigger)
    if not is_generic:
        njobs = config['njobs']
        jp = None
        if njobs == -1:
            jp = Mu2eJobPars(parfile_name)
            njobs = jp.njobs()
            print(f"Queried job count: {njobs}")
        jobdef_entry["njobs"] = njobs
        if firstjob:
            capacity = (jp or Mu2eJobPars(parfile_name)).njobs()
            try:
                validate_window(firstjob, njobs, capacity)
            except ValueError as e:
                fail(f"Error: {e} for {parfile_name}")
            jobdef_entry["firstjob"] = firstjob
            print(f"Windowed entry: cnf indices {firstjob}..{firstjob + njobs - 1}")

    outloc = config['outloc']
    if not isinstance(outloc, dict):
        raise ValueError(
            f"outloc must be a dictionary with dataset-specific "
            f"locations for {config.get('desc', 'unknown')}")
    for dataset_name, location in outloc.items():
        jobdef_entry["outputs"].append({
            "dataset": dataset_name,
            "location": location
        })
    return jobdef_entry


def append_jobdef(config, jobdefs_file=None):
    """Append the config's entry to a jobdefs file in JSON format.

    A non-dict outloc warns and skips rather than failing, preserving
    long-standing behaviour on this path. The enqueue path (json2jobdef
    --enqueue) deliberately does NOT swallow it: skipping there would
    push a cnf to SAM and create no campaign, a half-done production
    push that reports success.
    """
    try:
        jobdef_entry = build_jobdesc(config)
    except ValueError as e:
        print(f"Warning: {e}")
        return
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
    
    # Check for duplicate (tarball, firstjob) entries — the same tarball
    # may legitimately appear once per index window (statistics expansion),
    # but the same window must not be dispatched twice.
    tarball_name = jobdef_entry["tarball"]
    new_firstjob = firstjob_of(jobdef_entry)
    for existing in existing_entries:
        if (existing.get("tarball") == tarball_name
                and firstjob_of(existing) == new_firstjob):
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
    p.add_argument('--prod', action='store_true', help='Production mode: enable pushout and print the submission-map summary after generation')
    p.add_argument('--verbose', action='store_true', help='Verbose logging')
    p.add_argument('--no-cleanup', action='store_true', help='Keep temporary files (inputs.txt, template.fcl, *Cat.txt)')
    p.add_argument('--jobdefs', help='Submission-map file to append this entry to; omit to write no map file (no default filename)')
    p.add_argument('--enqueue', action='store_true',
                   help='After pushing the cnf, register the entry as a '
                        'sliced campaign in the ledger (no map file '
                        'written). Requires --prod.')
    p.add_argument('--slice-size', type=int, default=None,
                   help='Jobs per slice for --enqueue (default 1000; '
                        'frozen into the campaign).')
    p.add_argument('--extend', action='store_true',
                   help='Create delta job definition excluding already-processed inputs. '
                        'Auto-increments tarball version.')
    p.add_argument('--event-count-positive', action='store_true',
                   help='When building inputs.txt, require event_count>0 in SAM queries '
                        '(legacy behavior). Default is to include all files.')
    p.add_argument('--ignore-empty', action='store_true',
                   help='Skip entries whose input datasets have no files instead of failing')
    args = p.parse_args()

    if args.enqueue and not args.prod:
        sys.exit("json2jobdef: --enqueue requires --prod (a campaign "
                 "needs the cnf in SAM)")
    if args.slice_size is not None and not args.enqueue:
        sys.exit("json2jobdef: --slice-size requires --enqueue")
    if args.slice_size is None:
        args.slice_size = 1000
    if args.prod and not (args.jobdefs or args.enqueue):
        sys.exit("json2jobdef: --prod requires --jobdefs or --enqueue "
                 "(otherwise a bare --prod pushes the cnf to SAM but "
                 "writes no map file and registers no campaign -- a "
                 "silent no-op)")

    # If --prod is specified, enable pushout
    if args.prod:
        args.pushout = True

    setup_logging(args.verbose)
    
    # Load and expand the JSON configuration once
    expanded_configs = load_json(Path(args.json))
    
    # Bulk mode: dsconf only → every entry at that dsconf
    if args.dsconf and args.desc is None and args.index is None:
        process_all_for_dsconf(expanded_configs, args.dsconf, args)
    else:
        # Scalar modes: --desc + --dsconf, or --index only
        if args.desc and args.dsconf and args.index is None:
            config = find_json_entry(expanded_configs, args.desc, args.dsconf, None)
        elif args.index is not None and args.desc is None and args.dsconf is None:
            config = find_json_entry(expanded_configs, None, None, args.index)
        else:
            sys.exit("Please specify either --desc AND --dsconf, --dsconf only, or --index only")
        config['_event_count_positive'] = args.event_count_positive
        process_single_entry(
            config,
            pushout=args.pushout,
            no_cleanup=args.no_cleanup,
            jobdefs_list=args.jobdefs,
            extend=args.extend,
            ignore_empty=args.ignore_empty,
            enqueue=args.enqueue,
            slice_size=args.slice_size,
            json_path=args.json,
        )

    # If --prod mode, print the submission-map summary after generation
    if args.prod and args.jobdefs:
        jobdefs_file = args.jobdefs
        print(f"\n{'='*60}")
        print(f"Submission-map summary: {jobdefs_file}")
        print(f"{'='*60}")
        summarize_map(jobdefs_file)

def _build_job_args(config):
    """Dispatch on `determine_job_type(config)` and return the per-mode
    `job_args` list passed to `build_jobdef`. Sets transient config keys
    where the job-type wants them (e.g. `_max_events_to_skip` for resampler)."""
    job_type = determine_job_type(config)

    if job_type == 'resampler':
        first_dataset = normalize_input_data(config['input_data'])[0].source
        try:
            config['_max_events_to_skip'] = max_events_to_skip(first_dataset)
        except Exception as e:
            print(f"Warning: Could not calculate MaxEventsToSkip for {first_dataset}: {e}")
        merge_factor = calculate_merge_factor(config)
        return ['--auxinput', f"{merge_factor}:physics.filters.{config['resampler_name']}.fileNames:inputs.txt"]

    if job_type == 'merge':
        merge_factor = calculate_merge_factor(config)
        return ['--inputs', 'inputs.txt', '--merge-factor', str(merge_factor)]

    if job_type == 'chunk':
        # Chunk-on-grid: no inputs.txt, no --merge-factor. Per-job slice
        # is materialized at runtime by runmu2e via tbs.chunk_mode.
        return []

    if job_type == 'mixing':
        merge_factor = calculate_merge_factor(config)
        return ['--inputs', 'inputs.txt', '--merge-factor', str(merge_factor)] + build_pileup_args(config)

    # Stage1 / default: no special args
    return []


def cnf_location(owner):
    """Which storage class the cnf tarball is pushed to.

    Production cnfs live in the persistent `datasets` area
    (/pnfs/mu2e/persistent/datasets/usr-etc/cnf/...), and only the
    production account can write there. An ordinary user's token grants
    `storage.modify:/mu2e/scratch/datasets/usr-etc/cnf/<user>` but NOT
    anything under `/mu2e/persistent/datasets`, so pushing a
    user-owned cnf to 'disk' dies after three gfal retries with
    `DESTINATION MAKE_PARENT HTTP 403 : Permission refused` — which is
    what made `json2jobdef --prod` unusable for anyone but mu2epro.

    Owner 'mu2e' keeps 'disk', so production is bit-for-bit unchanged;
    every other owner gets the scratch datasets area its own token
    actually covers.
    """
    return 'disk' if owner == 'mu2e' else 'scratch'


def _pushout_to_sam(parfile_name, owner):
    """If `parfile_name` exists locally and isn't already in SAM, push it.
    Idempotent — repeat calls are no-ops once SAM has the file."""
    if not Path(parfile_name).exists():
        print(f"Warning: Local file {parfile_name} not found, skipping pushout")
        return

    if locate_file(parfile_name):
        print(f"File {parfile_name} already exists on SAM, skipping push")
        return

    location = cnf_location(owner)
    print(f"Pushing {parfile_name} to SAM ({location})...")
    push_output([(location, parfile_name, 'none')], 'outputs.txt')


def _cleanup_temp_files():
    """Remove the well-known transient files left in the build workdir.
    Catalog names are derived from PILEUP_MIXERS (mixing_utils), the same
    table build_pileup_args writes them from."""
    for temp_file in ('inputs.txt', 'template.fcl',
                      *(f"{mixer_type}Cat.txt" for mixer_type in PILEUP_MIXERS)):
        if Path(temp_file).exists():
            Path(temp_file).unlink()
            print(f"Cleanup: {temp_file}")


def _provenance(json_path, config):
    """Free-text origin recorded as the campaign's map_path. The column
    is never dispatched from — only the MCP status tools echo it — so it
    records where the entry CAME FROM rather than a filename that no
    longer exists."""
    return (f"{json_path}#{config.get('desc', '?')}"
            f"@{config.get('dsconf', '?')}")


def process_single_entry(config, pushout=False, no_cleanup=True,
                         jobdefs_list=None, extend=False, ignore_empty=False,
                         enqueue=False, slice_size=1000, json_path=None):
    """Process a single configuration entry (original behavior)"""
    validate_required_fields(config)
    config['owner'] = config.get('owner', default_owner())
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
    
    # Extend mode: exclude already-processed input files and auto-increment version
    exclude_files = None
    if extend:
        exclude_files = _compute_extend_exclusions(config)
    
    # Create inputs.txt first if needed
    if config.get('input_data'):
        _create_inputs_file(config, exclude_files=exclude_files)

    # Check for empty inputs (count once; one extend summary print)
    remaining = sum(1 for _ in open('inputs.txt')) if Path('inputs.txt').exists() else 0
    if extend and exclude_files is not None:
        print(f"  Extend summary: {len(exclude_files)} excluded, {remaining} remaining input files")
    if Path('inputs.txt').exists() and remaining == 0:
        if ignore_empty:
            print(f"  Skipping {config.get('desc', 'unknown')}: no input files available")
            return None
        elif extend:
            sys.exit("--extend: no new input files to process")

    job_args = _build_job_args(config)

    # build_jobdef handles FCL template creation for non-mixing jobs
    result = build_jobdef(config, job_args)

    # Only write the map file when a --jobdefs path was actually
    # supplied. --enqueue's promise ("no map file written") is only
    # true if a bare --prod --enqueue (no --jobdefs) doesn't fall back
    # to the default jobdefs_list.json filename.
    if jobdefs_list:
        append_jobdef(config, jobdefs_list)
    parfile_name = get_parfile_name(config)

    if pushout:
        _pushout_to_sam(parfile_name, config['owner'])

    # AFTER pushout, always: enqueue_entry resolves the tarball from SAM
    # and check_inputs reads it, so a campaign created before the push
    # would be broken from birth.
    if enqueue:
        from types import SimpleNamespace
        from utils.submit import enqueue_entry, _resolve_ledger_db
        entry = build_jobdesc(config)
        enqueue_entry(
            entry,
            ledger_db=_resolve_ledger_db(SimpleNamespace(ledger_db=None)),
            slice_size=slice_size,
            provenance=_provenance(json_path, config))

    if no_cleanup:
        print("Temporary files kept (--no-cleanup specified)")
    else:
        _cleanup_temp_files()

    return result

def is_already_expanded(configs):
    """Check if the configuration is already expanded (has scalar values, not lists)"""
    if not isinstance(configs, list) or len(configs) == 0:
        return False
    
    # Check all entries, not just the first one
    for i, config in enumerate(configs):
        if not isinstance(config, dict):
            raise ValueError(f"Entry {i} is not a dictionary: {type(config)}")
        
        # If any config has lists, the whole configuration needs expansion
        if any(isinstance(v, list) for v in config.values()):
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
            pushout=args.pushout,
            no_cleanup=True,
            jobdefs_list=args.jobdefs,
            ignore_empty=args.ignore_empty,
            enqueue=args.enqueue,
            slice_size=args.slice_size,
            json_path=args.json,
        )
        
        # Clean up template.fcl for next iteration (since process_single_entry cleans up)
        if Path('template.fcl').exists():
            Path('template.fcl').unlink()

if __name__ == '__main__':
    main()
