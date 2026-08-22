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
# Run directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
from pathlib import Path
from utils.prod_utils import *
from utils.mixing_utils import *
from utils.config_utils import cnf_name, get_tarball_desc, prepare_fields_for_job, normalize_input_data
from utils.jobdesc import (
    ENTRY_VALUE_KEYS, RESOURCE_KEYS, firstjob_of, is_dir_inloc,
    validate_entry_value,
    validate_outloc,
    validate_window)
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


def _random_selection(files, total_needed: int, seed_source: str):
    """Deterministic pseudo-random selection from a fetched file list."""
    ordered = sorted(files)  # sort first: deterministic regardless of SAM order
    rng = random.Random(seed_source)
    rng.shuffle(ordered)
    count = len(ordered)
    return [ordered[i % count] for i in range(total_needed)]

def _configure_chunk_mode(config):
    """Handle `input_data = {"<path>": {"chunk_lines": N}}`.

    Doesn't pre-split. Records the source path + chunk size in
    `config['chunk_mode']` so the tarball carries it into jobpars; at grid
    runtime, `runmu2e` extracts the per-job slice from the cvmfs source
    before invoking mu2e. njobs = ceil(lines/chunk_lines), computed here and
    carried into the submission entry.

    Every job's FCL points at the same local filename (default `chunk.txt`)
    via fcl_overrides; the per-job content is created fresh on the worker.
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
    # No inputs.txt: no SAM-tracked inputs; runmu2e materializes the
    # per-job chunk from cvmfs at job time.


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

    # Sequencer is <RRRRRR>_<SSSSSSSS> (run_subrun, zero-padded), Mu2e
    # convention. With sequencer_from_index, each output inherits the run
    # from its chunk's basename and substitutes job index as the subrun,
    # e.g. dts.mu2e.PBINormal_33344.MDC2025ai.001430_00000000.art
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

    # split_lines needs per-job sequencers from the job index, else every job
    # output collides on chunk 00's sequencer. Opt out with sequencer_from_index: false.
    config.setdefault('sequencer_from_index', True)


def _is_dir_inloc(config):
    """True if `config['inloc']` is the local-dir shape (`dir:<path>`).

    For that shape, `input_data` keys are bare file basenames written
    verbatim by `_create_inputs_file` (see its docstring below) — never SAM
    dataset names — so any SAM-dataset-name lookup keyed off the first
    `input_data` entry (e.g. resampler MaxEventsToSkip auto-computation)
    must be skipped rather than attempted.
    """
    return is_dir_inloc(config.get('inloc', ''))


def _create_inputs_file(config, exclude_files=None):
    """Create inputs.txt from input_data. `exclude_files` (used by --extend)
    omits already-processed filenames.

    input_data values may be a dict `{"count": N, "random": bool}` for
    (optionally random) SAM sampling, e.g.
    `{"sim.mu2e.NeutralsFlash.MDC2025ac.art": {"count": 100, "random": true}}`,
    or `{"split_lines": N}` to split a local text file into N-line chunks
    with basenames written to inputs.txt, e.g.
    `{"/cvmfs/.../DataFiles/PBI/PBI_Normal_33344.txt": {"split_lines": 1000}}`.
    """
    input_data = config.get('input_data')
    if not isinstance(input_data, dict):
        raise ValueError(f"input_data must be a dict, got {type(input_data)}")

    first_value = next(iter(input_data.values()), None)

    # Chunk-on-grid: {"<path>": {"chunk_lines": N}}. No pre-split, no
    # inputs.txt — runmu2e extracts each job's slice at runtime.
    if isinstance(first_value, dict) and 'chunk_lines' in first_value:
        _configure_chunk_mode(config)
        return

    # Text-file split: pre-split into chunks at submit time.
    if isinstance(first_value, dict) and 'split_lines' in first_value:
        _split_text_file_input(config)
        return

    # dir:<path> inloc: input_data keys are basenames, written verbatim (no
    # SAM lookup); jobfcl prepends the directory prefix at runtime. For
    # cvmfs-resident inputs not in SAM, e.g.
    #     "inloc": "dir:/cvmfs/.../DataFiles/PBI/",
    #     "input_data": {"PBI_Normal_33344.txt": 1}
    if _is_dir_inloc(config):
        with open('inputs.txt', 'w') as f:
            for key in input_data.keys():
                f.write(key + '\n')
        return

    _write_sam_inputs(config, input_data, exclude_files)


def _write_sam_inputs(config, input_data, exclude_files=None):
    """Write inputs.txt by resolving each input_data dataset against SAM.

    Each input_data value is a plain merge_factor (int) or a dict
    `{"count": N, "random": <bool>, "max_nfiles": M}`. `random: True` picks a
    deterministic pseudo-random sample of `count * njobs` files; otherwise
    all matching files are used. `max_nfiles` caps the per-dataset file
    count — a sorted prefix slice (non-random) or an upper bound on
    `total_needed` (random). njobs is NOT recomputed; the entry author must
    keep `merge_factor * njobs <= max_nfiles`.

    `config['_event_count_positive']` adds an explicit `event_count>0` SAM
    filter (older behavior applied this implicitly) so zero-event files
    aren't silently dropped.
    """
    event_count_positive = bool(config.get('_event_count_positive'))

    with open('inputs.txt', 'w') as out_f:
        for spec in normalize_input_data(input_data):
            if spec.per_job is None:
                raise ValueError(f"input_data spec for {spec.source} must include 'count' or 'merge_factor' when using dict form")

            query = q_dataset(spec.source, with_events=event_count_positive)
            if spec.random:
                files = _random_files(config, spec, query)
            else:
                files = _ordered_files(spec, query, exclude_files)
            for filepath in files:
                out_f.write(filepath + '\n')


def _random_files(config, spec, query):
    """Deterministic pseudo-random sample of `per_job * njobs` files (capped by
    `max_nfiles`), cycling the sorted+shuffled list when it is shorter."""
    per_job = spec.per_job
    raw_njobs = config.get('njobs', 1)
    try:
        njobs = int(raw_njobs)
    except (TypeError, ValueError):
        raise ValueError(
            f"njobs must be an integer for random input selection of "
            f"{spec.source}; got {raw_njobs!r}")

    # One SAM query serves njobs derivation and selection
    # (previously count_files + list_files ran it twice).
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
    return _random_selection(files, total_needed, seed_source)


def _ordered_files(spec, query, exclude_files=None):
    """All matching files in SAM order (sorted prefix when `max_nfiles` caps
    them), minus `exclude_files`."""
    files = list_files(query)
    if spec.max_nfiles is not None:
        files = sorted(files)[:spec.max_nfiles]
    if exclude_files:
        files = [f for f in files if f not in exclude_files]
    return files

def _next_version(config):
    """Find the next available version number for this job definition tarball.

    Queries SAM for existing files in the tarball dataset and returns
    max(existing versions) + 1, or 0 if none exist.
    """
    dataset = cnf_name(config, 'tar', dataset=True)

    files = files_in_dataset(dataset)
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
    """Validate required fields, and that supplied entry values are well formed.

    Value checks share utils/jobdesc.validate_entry_value with `submissions
    set-entry`, so a spelling the operator can't set on a live campaign is
    also one they can't enqueue. Unconditional, not gated on --enqueue: a
    misspelled inloc silently falls through to SAM, which is just as wrong
    on a local smoke and harder to notice there.

    Keys are validated only when present — inloc defaults to 'none'
    (process_single_entry), and the resource keys usually come from CLI flags.
    """
    for req in ('fcl', 'dsconf', 'outloc'):
        if not config.get(req):
            sys.exit(f"Missing required field: {req}")
    # Exactly one source of Offline (mu2ejobdef's own rule): a /cvmfs Musing
    # setup script, or a code tarball that travels with the job.
    if bool(config.get('simjob_setup')) == bool(config.get('code')):
        sys.exit("Exactly one of 'simjob_setup' and 'code' is required")
    try:
        for key in ENTRY_VALUE_KEYS:
            if key in config:
                validate_entry_value(key, config[key])
        validate_outloc(config['outloc'])
    except ValueError as exc:
        sys.exit(f"json2jobdef: {exc}")

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
    # Embed template.fcl to preserve fcl_overrides. Mixing jobs already have
    # it (written by build_pileup_args); non-mixing jobs create it here.
    fcl_path = config['fcl']
    job_type = determine_job_type(config)

    if job_type != 'mixing':
        # Resampler MaxEventsToSkip goes after the overrides (last wins).
        # Skipped for dir:-inloc resamplers: _build_job_args never computes
        # `_max_events_to_skip` for them (no SAM dataset to query — see
        # _is_dir_inloc), so there is nothing to emit here, and the entry's
        # own fcl_overrides (or the base FCL's) stands undisturbed.
        post_lines = []
        if job_type == 'resampler' and not _is_dir_inloc(config):
            post_lines.append(
                f"physics.filters.{config['resampler_name']}.mu2e.MaxEventsToSkip: {config['_max_events_to_skip']}")
        write_fcl_template(fcl_path, config.get('fcl_overrides', {}),
                           post_lines=post_lines)

    # Perl-equivalent command string. Kept even though create_jobdef echoes
    # its own version: test/parity_test.py consumes this exact string via
    # result['perl_commands'] to run the Perl mu2ejobdef comparison.
    cmd_parts = [
        'mu2ejobdef',
        '--setup' if config.get('simjob_setup') else '--code',
        config.get('simjob_setup') or config['code'],
        '--dsconf', config['dsconf'],
        '--desc', config['desc'],
        '--dsowner', config['owner']
    ]

    if 'run' in config:
        cmd_parts.extend(['--run-number', str(config['run'])])

    if 'events' in config:
        cmd_parts.extend(['--events-per-job', str(config['events'])])

    cmd_parts.extend(job_args)
    cmd_parts.extend(['--embed', 'template.fcl'])

    create_jobdef(config, fcl_path='template.fcl', job_args=job_args, embed=True, quiet=True)

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
                'simjob_setup': config.get('simjob_setup')
            }
        ]
    }

def build_jobdesc(config):
    """Project a build config onto the submission entry (the `jobdesc`).

    Pure except the `njobs: -1` branch, which asks the freshly-built cnf
    for its job count.

    Raises ValueError if `outloc` is malformed (validate_outloc owns the
    grammar). Fatal, not a warning, because the only caller is the enqueue
    path: skipping there would push a cnf to SAM and create no campaign — a
    half-done production push that reports success. This is a backstop;
    validate_required_fields already checked the same config earlier.
    """
    parfile_name = get_parfile_name(config)
    is_generic = config.get('generic_tarball', False)

    jobdef_entry = {
        "tarball": parfile_name,
        "inloc": config['inloc'],
        "outputs": []
    }

    # Optional per-entry resource requests, read at submit time via
    # jobdesc.resources_of (CLI flag > entry key > built-in default).
    for key in RESOURCE_KEYS:
        if key in config:
            jobdef_entry[key] = config[key]

    # Draining config passes through too: submit reads `input_pattern`
    # (jobdesc.is_draining, the kind discriminator) and `prestage`
    # (submit._validate_draining_entry / submissions.drain_tick's
    # tape-residency gate) off the ENTRY, not the JSON config, so a value
    # left only in the JSON would silently do nothing.
    for key in ('input_pattern', 'prestage'):
        if key in config:
            jobdef_entry[key] = config[key]

    # Code tarball path travels on the entry, not the cnf: submit reads it
    # via jobdesc.code_of for jobsub's --tar_file_name, and the snapshot is
    # what later slices reuse.
    if config.get('code'):
        jobdef_entry['code'] = config['code']

    # A draining entry has input_pattern and NO index space; emitting both
    # would self-contradict (is_draining() true while njobs claims a fixed
    # window), so refuse rather than write it.
    if 'input_pattern' in config and not is_generic:
        fail("Error: input_pattern requires generic_tarball: true "
             "(a draining entry has no fixed job count)")

    # Optional cnf-index window start (statistics expansion; see
    # utils/jobdesc.py). firstjob_of/validate_window are shared with the
    # submit path as the single validation authority.
    try:
        firstjob = firstjob_of(config)
    except ValueError as e:
        fail(f"Error: {e}")
    if firstjob and is_generic:
        fail("Error: firstjob requires a fixed job count (njobs); "
             "generic tarball entries have no index window")

    # Generic tarballs have no pre-determined job count; omitting njobs is
    # what tells runmu2e to use direct-input mode.
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
    validate_outloc(outloc)
    for dataset_name, location in outloc.items():
        jobdef_entry["outputs"].append({
            "dataset": dataset_name,
            "location": location
        })
    return jobdef_entry


def main():
    p = argparse.ArgumentParser(description='Generate Mu2e job definitions from JSON configuration')
    p.add_argument('--json', required=True, help='Input JSON file')
    p.add_argument('--desc', type=str, help='Dataset descriptor')
    p.add_argument('--dsconf', type=str, help='Dataset configuration')
    p.add_argument('--index', type=int, help='Entry index in JSON list')
    p.add_argument('--pushout', action='store_true', help='Enable SAM pushOutput')
    p.add_argument('--prod', action='store_true', help='Production mode: enable pushout (SAM registration). Requires --enqueue, which registers a sliced-submission campaign in the ledger and prints its campaign id.')
    p.add_argument('--verbose', action='store_true', help='Verbose logging')
    p.add_argument('--no-cleanup', action='store_true', help='Keep temporary files (inputs.txt, template.fcl, *Cat.txt)')
    p.add_argument('--enqueue', action='store_true',
                   help='After pushing the cnf, register the entry as a '
                        'sliced campaign in the ledger. Requires --prod.')
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
    if args.prod and not args.enqueue:
        sys.exit("json2jobdef: --prod requires --enqueue (otherwise a "
                 "bare --prod pushes the cnf to SAM and registers no "
                 "campaign -- a silent no-op)")

    if args.prod:
        args.pushout = True

    setup_logging(args.verbose)

    expanded_configs = load_json(Path(args.json))

    # Bulk mode: dsconf only -> every entry at that dsconf
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
            extend=args.extend,
            ignore_empty=args.ignore_empty,
            enqueue=args.enqueue,
            slice_size=args.slice_size,
            json_path=args.json,
        )

def _build_job_args(config):
    """Dispatch on `determine_job_type(config)` and return the per-mode
    `job_args` list passed to `build_jobdef`. Sets transient config keys
    where the job-type wants them (e.g. `_max_events_to_skip` for resampler)."""
    job_type = determine_job_type(config)

    if job_type == 'resampler':
        # dir:-inloc resamplers key input_data by bare basenames, not SAM
        # dataset names (see _is_dir_inloc), so skip the auto-computation
        # rather than feed a basename into a SAM lookup that can only fail.
        # build_jobdef mirrors this guard when emitting post_lines.
        if not _is_dir_inloc(config):
            # A resampler cnf without MaxEventsToSkip is a physics bug (the
            # resampler silently re-reads the same leading events), so a
            # failed lookup is fatal, not a warning.
            first_dataset = normalize_input_data(config['input_data'])[0].source
            try:
                config['_max_events_to_skip'] = max_events_to_skip(first_dataset)
            except Exception as e:
                fail(f"Error: Could not calculate MaxEventsToSkip for {first_dataset}: {e}")
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
    (/pnfs/mu2e/persistent/datasets/usr-etc/cnf/...), writable only by the
    production account. An ordinary user's token grants
    `storage.modify:/mu2e/scratch/datasets/usr-etc/cnf/<user>` but nothing
    under `/mu2e/persistent/datasets`, so pushing a user-owned cnf to 'disk'
    dies after three gfal retries with `DESTINATION MAKE_PARENT HTTP 403 :
    Permission refused` — which made `json2jobdef --prod` unusable for
    anyone but mu2epro.

    Owner 'mu2e' keeps 'disk' (production unchanged); every other owner
    gets the scratch datasets area its own token actually covers.
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
    """Free-text origin recorded as the campaign's origin column. It is
    never dispatched from — only the MCP status tools echo it — so it
    records where the entry CAME FROM rather than a filename that no
    longer exists."""
    return (f"{json_path}#{config.get('desc', '?')}"
            f"@{config.get('dsconf', '?')}")


def process_single_entry(config, pushout=False, no_cleanup=True,
                         extend=False, ignore_empty=False,
                         enqueue=False, slice_size=1000, json_path=None):
    """Process a single configuration entry."""
    validate_required_fields(config)
    config['owner'] = config.get('owner', default_owner())
    config['inloc'] = config.get('inloc', 'none')
    config['njobs'] = config.get('njobs', -1)

    # Generic tarball mode: no input_data, {desc} deferred for runtime resolution
    if config.get('generic_tarball'):
        config['_defer_keys'] = {'desc'}
        config['njobs'] = 0

    # Auto-generate desc from input_data (3rd field of the dataset name,
    # e.g. "ensembleMDS3a" from "dts.mu2e.ensembleMDS3a.MDC2025af.art")
    if not config.get('desc'):
        config = prepare_fields_for_job(config, job_type='standard')

    exclude_files = None
    if extend:
        exclude_files = _compute_extend_exclusions(config)

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
    result = build_jobdef(config, job_args)

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
    """True if every entry already has scalar values (no lists to expand)."""
    if not isinstance(configs, list) or len(configs) == 0:
        return False

    for i, config in enumerate(configs):
        if not isinstance(config, dict):
            raise ValueError(f"Entry {i} is not a dictionary: {type(config)}")
        if any(isinstance(v, list) for v in config.values()):
            return False

    return True

#: Campaign-wide defaults, read from this file beside the stage files.
COMMON_JSON = 'common.json'

def _override_dicts(config):
    """Every fcl_overrides dict on an entry, creating one if absent.
    Expansion normally collapses the list-wrapped mixing shape `[{...}]`
    to a dict, but is_already_expanded can hand back a raw config."""
    overrides = config.setdefault('fcl_overrides', {})
    if isinstance(overrides, list):
        return [o for o in overrides if isinstance(o, dict)]
    return [overrides]

def apply_common_overlay(configs, json_path):
    """Overlay `<campaign>/common.json` onto the entries `json_path` loaded.

    The overlay is a DEFAULT, not an override: its includes go to
    COMMON_INCLUDE_KEY, which write_fcl_template emits before everything
    else, so an entry that pins a value still wins. That direction is the
    whole safety property — reversed, the campaign default would move the
    42 frozen Run1B entries (v01/v03/v06) onto the current geometry.

    common.json states the defaults two ways, both carrying that same
    direction: `'#include'`, a FCL holding the settings (preferred once it
    exists — one file states the campaign, Production owns it), or plain
    keys written out directly and applied via setdefault, so an entry that
    states the key keeps it and dict order never decides the outcome. Plain
    keys are what's used while the FCL is still an unmerged Production PR —
    an include would abort every build with a fhicl search_path error, and
    no entry-level override can suppress an include.

    common.json also states its own scope, since a campaign directory isn't
    uniform: `applies_to` lists the stage files it covers (merge, catalog
    and ntuple stages are excluded — artcat.fcl configures no
    GeometryService, and a geometry default there would construct a service
    the job has no use for), and the optional `dsconf_prefix` filters by
    dsconf (data/Run1B holds 15 MDC2025* entries that must not take a
    Run1B default).

    A default is only safe where it's redundant: before listing a stage file
    here, check every entry that leaves a key to the base FCL's own
    epilog — those take the default and change. `pileup/epilog.fcl` sets
    bfgeom_no_tsu_ps_v01 and `beam/POT.fcl` sets bfgeom_no_ds_v01, so six
    Run1B entries had to pin their inherited value explicitly first.
    """
    common_path = json_path.parent / COMMON_JSON
    if json_path.name == COMMON_JSON or not common_path.exists():
        return configs

    common = json.loads(common_path.read_text())
    if json_path.name not in common.get('applies_to', []):
        return configs

    common_overrides = common.get('fcl_overrides', {})
    includes = common_overrides.get('#include', [])
    defaults = {k: v for k, v in common_overrides.items() if k != '#include'}
    if not includes and not defaults:
        return configs
    prefix = common.get('dsconf_prefix')

    for config in configs:
        dsconf = config.get('dsconf')
        if isinstance(dsconf, list):
            dsconf = dsconf[0] if dsconf else None
        if prefix and not str(dsconf or '').startswith(prefix):
            continue
        for overrides in _override_dicts(config):
            # Idempotent: expansion may hand several entries one dict.
            kept = [i for i in overrides.get(COMMON_INCLUDE_KEY, [])
                    if i not in includes]
            if includes:
                overrides[COMMON_INCLUDE_KEY] = list(includes) + kept
            # setdefault, never assignment: an entry that states the key
            # keeps its own value, the same way it beats the include.
            for key, val in defaults.items():
                overrides.setdefault(key, val)
    return configs

def load_json(json_path):
    """Load and expand JSON configuration if needed"""
    json_text = json_path.read_text()
    configs = json.loads(json_text)

    if not is_already_expanded(configs):
        # mixing vs standard is determined per config from content (e.g. pbeam)
        configs = expand_configs(configs)

    return apply_common_overlay(configs, json_path)

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
    """Process every entry matching `dsconf` (exact match), building a job
    definition for each."""
    matching_configs = [config for config in expanded_configs if config.get('dsconf', '') == dsconf]

    if not matching_configs:
        sys.exit(f"No entries found matching dsconf: {dsconf}")

    print(f"Found {len(matching_configs)} entries matching dsconf: {dsconf}")

    skipped = []
    for i, config in enumerate(matching_configs):
        # get_tarball_desc handles tarball_append; fall back to input_data extraction
        display_desc = get_tarball_desc(config) or config.get('desc')
        if not display_desc:
            temp_config = prepare_fields_for_job(config, job_type='standard')
            display_desc = temp_config.get('desc', 'Unknown')
        print(f"\nProcessing entry {i+1}/{len(matching_configs)}: {display_desc}")

        try:
            validate_required_fields(config)
        except SystemExit as e:
            print(f"Warning: {e}, skipping entry")
            skipped.append(f"{display_desc}: {e}")
            continue

        config['_event_count_positive'] = args.event_count_positive

        process_single_entry(
            config,
            pushout=args.pushout,
            no_cleanup=True,
            ignore_empty=args.ignore_empty,
            enqueue=args.enqueue,
            slice_size=args.slice_size,
            json_path=args.json,
        )

        # process_single_entry runs with no_cleanup=True here, so template.fcl
        # is removed by hand before the next iteration writes its own.
        if Path('template.fcl').exists():
            Path('template.fcl').unlink()

    # A bulk run that silently dropped entries must NOT report success: the
    # per-entry warning scrolls past in a long log, and the MCP write server
    # (and any cron) reads only the exit code, so a typo'd inloc in entry 7
    # of 22 would be reported as "all 22 done". Entries that DID process are
    # left alone (already in SAM/ledger; undoing them is not this call).
    if skipped:
        print(f"\n{len(skipped)} of {len(matching_configs)} entries were "
              f"SKIPPED and no campaign exists for them:", file=sys.stderr)
        for note in skipped:
            print(f"  - {note}", file=sys.stderr)
        sys.exit(2)

if __name__ == '__main__':
    main()
