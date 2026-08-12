import collections
import glob
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from .config_utils import normalize_input_data
from .job_common import Mu2eName
from .jobfcl import Mu2eJobFCL
from .jobdesc import firstjob_of, njobs_of
from .samweb_wrapper import (
    dataset_summary,
    definition_file_count,
)

def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(levelname)s] %(message)s"
    )
    
    # Suppress debug messages from external libraries when verbose is enabled
    if verbose:
        # Suppress requests library debug messages
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
        # Suppress samweb_client debug messages
        logging.getLogger("samweb_client").setLevel(logging.WARNING)

# How many trailing output lines run() keeps for failure classification.
# Enough to span pushOutput's recover/retry block; small enough that
# streaming a full art job costs nothing.
RUN_TAIL_LINES = 200


def run(cmd, shell=False, retries=0, retry_delay=60):
    """
    Run a shell command with real-time output streaming.
    If shell=True, cmd is a string.
    retries: number of retry attempts (0 = no retries, just run once)
    retry_delay: seconds to wait between retries
    Returns the exit code (0 for success) or raises CalledProcessError for failure.
    """
    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Running: {cmd}")

        # Real-time streaming, keeping a bounded tail so a caller can
        # classify the failure (see runmu2e._is_terminal_push_error).
        # Bounded because this same helper streams `mu2e -c`, whose output
        # runs to hundreds of thousands of lines — the failure reason is
        # always near the end.
        tail = collections.deque(maxlen=RUN_TAIL_LINES)
        process = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in iter(process.stdout.readline, ''):
            text = line.rstrip()
            print(text)
            tail.append(text)
            sys.stdout.flush()

        process.stdout.close()
        return_code = process.wait()

        if return_code == 0:
            return return_code

        if attempt < attempts:
            print(f"[{timestamp}] Command failed (attempt {attempt}/{attempts}), retrying in {retry_delay}s...")
            time.sleep(retry_delay)
        else:
            raise subprocess.CalledProcessError(return_code, cmd,
                                                output="\n".join(tail))


def _fetch_file_local(filename, src_location='disk'):
    """Fetch a SAM-registered file from dCache to cwd via `mdh copy-file`.
    No-op if `filename` is already locally present (basename-relative).
    `src_location` defaults to 'disk' (the cnf-tarball convention, matching
    pushOutput's `disk` destination); pass the actual location for input
    data files."""
    if Path(filename).is_file():
        return
    run(f"mdh copy-file -e 3 -o -v -s {src_location} -l local {filename}",
        shell=True, retries=3, retry_delay=60)
    if not Path(filename).is_file():
        raise RuntimeError(f"mdh copy-file did not produce {filename} in cwd")


def fail(msg):
    """Print an error to stdout and exit 1 — the canonical fail-loud exit.
    (stdout, not stderr: grid logs interleave both, and the historical
    print+exit pattern this replaces wrote to stdout.)"""
    print(msg)
    sys.exit(1)


def write_fcl(jobdef, inloc='tape', proto='root', index=0, target=None):
    """
    Generate and write an FCL file using mu2ejobfcl.
    """
    # cnf.<owner>.<desc>.<dsconf>.<seq>.tar -> cnf.<owner>.<desc>.<dsconf>.<index>.fcl
    jobdef_name = Path(jobdef).name  # Get just the filename, not the full path
    fcl = str(Mu2eName.parse(jobdef_name).with_sequencer(str(index)).with_extension('fcl'))
    
    job_fcl = Mu2eJobFCL(jobdef, inloc=inloc, proto=proto)

    if target:
        job_index = job_fcl.find_index(target=target)
    else:
        job_index = job_fcl.find_index(index=index)

    result = job_fcl.generate_fcl(job_index)

    print(f"Wrote {fcl}")
    with open(fcl, 'w') as f:
        f.write(result + '\n')

    print(f"\n--- {fcl} content ---")
    print(result + '\n')

    return fcl

def get_def_counts(dataset):
    """Get file count (events>0 files only) and event count for a dataset.
    Exits when the dataset has no such files."""

    # Count files
    nfiles = definition_file_count(dataset, with_events=True)

    # Count events
    result = dataset_summary(dataset)
    nevts = (result.get('total_event_count') or 0) if isinstance(result, dict) else 0

    if nfiles == 0:
        sys.exit(f"No files found in dataset {dataset}")
    return nfiles, nevts

def max_events_to_skip(dataset):
    """MaxEventsToSkip for a resampler/mixer reading `dataset`: mean events
    per file (floor), so per-job skips stay within one file's budget.
    Single home of the derivation (mixing pre_lines + resampler post_lines)."""
    nfiles, nevts = get_def_counts(dataset)  # exits if nfiles == 0
    return nevts // nfiles

def calculate_merge_factor(fields):
    """Calculate merge factor from input_data dict.
    
    The input_data should be a dict mapping dataset names to merge factors.
    Returns the merge factor from the first dataset in the dict.
    """
    spec = normalize_input_data(fields.get('input_data'))[0]
    if spec.split_lines is not None:
        # split_lines means "split a local text file into N-line chunks;
        # each job consumes one chunk" — merge_factor is implicitly 1.
        return 1
    if spec.per_job is None:
        raise ValueError("input_data dict spec must include 'count', 'merge_factor', or 'split_lines'")
    return spec.per_job

# Removed duplicate find_json_entry; use json2jobdef.load_json + json2jobdef.find_json_entry

def write_fcl_template(base, overrides, pre_lines=(), post_lines=()):
    """
    Write template.fcl — the single writer for every jobdef stage.

    Layout (FHiCL last-wins, so position is semantics):
        #include base / pre_lines / overrides / post_lines

    Args:
        base: Base FCL file to include
        overrides: Dictionary of FCL overrides
        pre_lines: raw FCL lines the config's overrides may still beat
            (mixing pbeam include + per-mixer MaxEventsToSkip)
        post_lines: raw FCL lines that beat the overrides
            (resampler MaxEventsToSkip, computed from SAM)
    """
    with open('template.fcl', 'w') as f:
        # Write just the include directive for the base FCL
        f.write(f'#include "{base}"\n')

        for line in pre_lines:
            f.write(line + '\n')

        # Add overrides
        for key, val in overrides.items():
            if key == '#include':
                includes = val if isinstance(val, list) else [val]
                for inc in includes:
                    f.write(f'#include "{inc}"\n')
            else:
                # Use json.dumps for all values to ensure proper FCL formatting
                # (strings get quotes, lists get proper syntax with double
                # quotes, bools become lowercase true/false as FHiCL requires)
                f.write(f'{key}: {json.dumps(val)}\n')

        for line in post_lines:
            f.write(line + '\n')

def write_direct_input_fcl(job_fcl, fname, format_input=False, filter_base=False):
    """Write the direct-input FCL for `fname` from a generic cnf's base FCL:
    base content + appended source.fileNames and per-output filename
    overrides (FHiCL last-definition-wins).

    Single home for the worker runtime (runmu2e.process_direct_input) and the
    fcldump debug view, which had silently drifted apart. The flags ARE
    that drift, now explicit:
    - format_input: resolve fname to a full xroot/file URL via the resolver
      (fcldump debug view); the worker writes the raw fname it fetched.
    - filter_base: strip base-FCL lines the overrides re-define, so the
      debug view shows no unresolved {desc} placeholders (cosmetic — the
      appended overrides win either way).
    Returns the written fcl filename."""
    n = Mu2eName.parse(Path(fname).name)
    if not n.is_file:
        raise ValueError(
            f"Invalid filename format: {fname}. "
            f"Expected tier.owner.desc.dsconf.sequencer.ext"
        )
    base_fcl = job_fcl._extract_fcl()
    outputs_map = job_fcl.job_outputs(0, override_desc=n.description,
                                      override_seq=n.sequencer)
    source_name = job_fcl._format_filename(fname) if format_input else fname
    if filter_base:
        override_keys = set(outputs_map.keys()) | {'source.fileNames'}
        base_fcl = '\n'.join(
            line for line in base_fcl.splitlines()
            if not any(line.lstrip().startswith(k) for k in override_keys)
        )

    fcl = f"{Path(fname).stem}.fcl"
    with open(fcl, 'w') as f:
        f.write(base_fcl)
        f.write("\n# Direct-input overrides:\n")
        f.write(f'source.fileNames: ["{source_name}"]\n')
        for key, filename in outputs_map.items():
            f.write(f'{key}: "{filename}"\n')

    print(f"Wrote {fcl}")
    print(f"\n--- {fcl} content ---")
    with open(fcl) as f:
        print(f.read())
    return fcl


def resolve_entry_index(entry, job_index):
    """Map a global job index to the entry's cnf-local index.

    `local = job_index + firstjob`, so a windowed entry runs cnf indices
    [firstjob, firstjob+njobs). Window semantics (statistics expansion,
    seed safety): see utils/jobdesc.py. A generic entry (no njobs)
    occupies no index space.

    Returns:
        tuple: (entry, local_job_index), or (None, None) if job_index is
               beyond the entry's njobs.
    """
    njobs = njobs_of(entry)
    if njobs is None or job_index >= njobs:
        return None, None
    return entry, job_index + firstjob_of(entry)


def push_output(output_specs, output_file="output.txt", simjob_setup=None):
    """
    Generic function to push output files.

    Args:
        output_specs: List of tuples (location, filename, parents) — parents
            is the per-file third column ('parents_list.txt' or 'none')
        output_file: Name of the output specification file
        simjob_setup: Path to SimJob setup script for art environment
    
    Returns:
        int: Exit code from pushOutput command
    """

    output_lines = []
    for spec in output_specs:
        location, pattern, parents = spec
        # Handle glob patterns
        matching_files = glob.glob(pattern) if '*' in pattern else [pattern]
        for filename in matching_files:
            if Path(filename).exists():
                output_lines.append(f"{location} {filename} {parents}")
            else:
                print(f"Warning: File not found: {filename}")
    
    if not output_lines:
        print(f"Warning: No files to push for {output_file}")
        return 0
    
    Path(output_file).write_text("\n".join(output_lines) + "\n")
    print(f"Pushing {len(output_lines)} file(s) via {output_file}")
    push_cmd = f"pushOutput {output_file}"
    if simjob_setup:
        push_cmd = f"source {simjob_setup} && {push_cmd}"
    # run() returns 0 or raises CalledProcessError — no nonzero returns
    return run(push_cmd, shell=True)

