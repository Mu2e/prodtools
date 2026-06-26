#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import log_storage_location
from utils.prod_utils import (
    run,
    validate_jobdesc,
    process_template,
    process_direct_input,
    process_jobdef,
    process_g4bl_jobdef,
    build_mu2e_cmd,
    push_data,
    push_logs,
    replace_file_extensions,
)


# ============================================================
# Direct mode (jobsub_submit, no POMS) — Phase 2 v1
#
# POMS mode is invoked with --jobdesc <json> and the per-job index
# encoded in the `fname` env var (sequencer field).
# Direct mode is detected by presence of MU2EGRID_JOBDEF in the
# environment (set by the jobsub_submit argv). The submitter ships
# the cnf tarball + an "ops JSON" via dropbox, both landing under
# $CONDOR_DIR_INPUT. Index resolves via ops['jobs'][PROCESS].
# ============================================================


def _is_direct_mode():
    """Direct mode is signalled by env vars set by our jobsub_submit argv."""
    return 'MU2EGRID_JOBDEF' in os.environ


def _direct_input_dir():
    """jobsub_submit lands -f dropbox:// files under $CONDOR_DIR_INPUT.
    Fall back to cwd for local testing."""
    return os.environ.get('CONDOR_DIR_INPUT', '.')


def _load_direct_ops():
    """Load the ops JSON shipped via dropbox. Contains: jobs (PROCESS→index
    array), inspec (dataset → [protocol, location]), jobdesc (a one-element
    list mirroring the POMS-map entry shape for reuse via process_jobdef)."""
    ops_basename = os.environ['MU2EGRID_OPSJSON']
    ops_path = os.path.join(_direct_input_dir(), ops_basename)
    with open(ops_path) as f:
        return json.load(f)


def _resolve_direct_index(ops):
    """PROCESS → real job index via ops['jobs'][PROCESS] (replaces mu2ejobmap)."""
    process = int(os.environ.get('PROCESS', '0'))
    jobs = ops.get('jobs', [])
    if process < 0 or process >= len(jobs):
        raise RuntimeError(
            f"PROCESS={process} out of range for jobset of length {len(jobs)}"
        )
    return jobs[process]


def _synthesize_direct_fname(index):
    """Build a fake fname string that _job_index_from_fname() reverses to
    `index`. process_jobdef and friends only consume the parts[4] sequencer."""
    return f"x.x.x.x.{index:08d}.x"


def _emit_manifest(log_path, manifest_files):
    """Append the SHA256 manifest block to the log file in a format that
    `mu2eClusterCheckAndMove` can parse. Faithful port of `addManifest`
    from mu2egrid::impl/mu2ejobsub.sh:44-56.

    Format (the parser is regex-strict):

        mu2egrid diskUse = <kbytes>
        #================================================================
        # mu2egrid manifest
        # <ls -al line, each prefixed with '# '>
        ...
        #----------------------------------------------------------------
        # algorithm: sha256sum
        <hex>  <file>
        ...
        # mu2egrid manifest selfcheck: <hex>  -

    The selfcheck reads the manifest from stdin (`sha256sum < log`), which
    is why the trailing `  -` is part of the contract.
    """
    log = Path(log_path)

    # diskUse from `du -ks` — keep raw output (kbytes \t path) for byte-exact
    # parity with the bash addManifest reference; regex-based parsers match
    # `^mu2egrid diskUse = (\d+)` regardless of trailing content.
    du = subprocess.run(['du', '-ks'], capture_output=True, text=True, check=False)
    du_out = du.stdout.rstrip('\n') if du.stdout else '0'

    # ls -al with C locale so positional fields are stable
    ls = subprocess.run(['ls', '-al'], capture_output=True, text=True,
                        env={**os.environ, 'LC_ALL': 'C'}, check=False)

    with log.open('a') as f:
        f.write(f"mu2egrid diskUse = {du_out}\n")
        f.write("#" + "=" * 64 + "\n")
        f.write("# mu2egrid manifest\n")
        for line in ls.stdout.splitlines():
            f.write(f"# {line}\n")
        f.write("#" + "-" * 64 + "\n")
        f.write("# algorithm: sha256sum\n")
        for fname in manifest_files:
            if not Path(fname).exists():
                continue
            h = hashlib.sha256()
            with open(fname, 'rb') as g:
                for chunk in iter(lambda: g.read(1 << 20), b''):
                    h.update(chunk)
            f.write(f"{h.hexdigest()}  {fname}\n")

    # Selfcheck: sha256sum of the file's content (everything written so far),
    # emitted in `sha256sum < log` format ("  -" trailer, no filename).
    sc = hashlib.sha256()
    with log.open('rb') as g:
        for chunk in iter(lambda: g.read(1 << 20), b''):
            sc.update(chunk)
    with log.open('a') as f:
        f.write(f"# mu2egrid manifest selfcheck: {sc.hexdigest()}  -\n")


def _push_with_retry(push_fn, *args, retries=3, base_delay=30, **kwargs):
    """Direct-mode wrapper for push_data / push_logs. Retries on
    CalledProcessError with exponential backoff, then raises so condor
    sees a job failure (CB2: don't silently leave files unregistered)."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            push_fn(*args, **kwargs)
            return
        except subprocess.CalledProcessError as e:
            last_exc = e
            if attempt == retries:
                break
            delay = base_delay * (2 ** attempt)
            print(f"[direct] {push_fn.__name__} attempt {attempt + 1}/{retries + 1} "
                  f"failed (rc={e.returncode}); retrying in {delay}s")
            time.sleep(delay)
    raise last_exc


def _direct_dispatch(args, ops, index):
    """Direct-mode equivalent of _dispatch_and_execute(mode='normal'):
    run process_jobdef → mu2e -c → manifest → push (with retries)."""
    # Synthesize POMS-style inputs: jobdesc is shipped in ops JSON,
    # fname encodes the resolved index.
    jobdesc = ops['jobdesc']
    fname = _synthesize_direct_fname(index)

    mode = validate_jobdesc(jobdesc)
    if mode != False:  # noqa: E712 — validate_jobdesc returns False for normal
        print(f"ERROR: direct mode v1 supports normal mode only, got '{mode}'. "
              f"Use --backend mu2ejobsub for template/direct_input/g4bl entries.")
        sys.exit(1)

    fcl, simjob_setup, infiles, outputs, inloc = process_jobdef(jobdesc, fname, args)

    # `dir:<path>` inloc means inputs come from a locally-mounted FS and
    # have no SAM parents — match the POMS-mode logic in _dispatch_and_execute.
    track_parents = not (isinstance(inloc, str) and inloc.startswith('dir:'))

    cmd = build_mu2e_cmd(fcl, simjob_setup, args)
    print(f"[direct] Executing: {cmd}")
    print(f"[direct] cwd={os.getcwd()} fcl_exists={os.path.exists(fcl)}")
    print("=== Starting Mu2e execution ===")

    job_failed = False
    try:
        run(cmd, shell=False)
        print("=== Mu2e execution completed successfully ===")
    except subprocess.CalledProcessError as e:
        job_failed = True
        print(f"=== Mu2e execution failed with exit code {e.returncode} ===")

    # Append SHA256 manifest to the log BEFORE pushing.
    # mu2eClusterCheckAndMove parses the log for `mu2egrid manifest`.
    log_file = replace_file_extensions(fcl, "log", "log")
    if Path(log_file).exists():
        manifest_files = []
        if not job_failed:
            for o in outputs:
                pattern = o['dataset']
                manifest_files.extend(sorted(Path('.').glob(pattern)))
        _emit_manifest(log_file, [str(f) for f in manifest_files])

    # Push outputs only on success; logs always (so failures are debuggable).
    if not job_failed:
        _push_with_retry(push_data, outputs, infiles,
                         simjob_setup=simjob_setup, track_parents=track_parents)
    else:
        print("[direct] mu2e failed — skipping data push, still pushing log")

    # Logs share the first output's location so the worker token's
    # storage.modify scope covers both. Without this, a non-mu2epro account
    # whose data outputs go to `scratch` would still try to push the log
    # to `disk` (push_logs default), which `/mu2e/persistent/datasets/...`
    # doesn't grant. Production runs as mu2epro keep `disk` via the same
    # mechanism — the cnf's outputs[] specifies where data lands.
    log_location = log_storage_location(outputs)
    _push_with_retry(push_logs, fcl, simjob_setup=simjob_setup,
                     location=log_location)

    return job_failed


def _direct_main(args):
    """Entry for direct mode. Resolves index, dispatches via _direct_dispatch."""
    ops = _load_direct_ops()
    index = _resolve_direct_index(ops)

    print(f"[direct] PROCESS={os.environ.get('PROCESS', '0')} → job index {index}")
    print(f"[direct] jobdef={os.environ.get('MU2EGRID_JOBDEF')}")

    # Ensure the cnf tarball is reachable. -f dropbox:// drops it under
    # $CONDOR_DIR_INPUT but process_jobdef expects basename in cwd
    # (it calls _fetch_file_local which is a no-op if already local).
    jobdef_basename = os.environ['MU2EGRID_JOBDEF']
    if not Path(jobdef_basename).is_file():
        src = Path(_direct_input_dir()) / jobdef_basename
        if src.is_file():
            os.symlink(src, jobdef_basename)
        # else: process_jobdef will _fetch_file_local() from SAM as a fallback.

    # process_jobdef stages inputs locally when args.copy_input is set —
    # required in direct mode because there's no POMS pre-staging step.
    args.copy_input = True

    if _direct_dispatch(args, ops, index):
        sys.exit(1)



def _dispatch_and_execute(mode, jobdesc, fname, args):
    """Dispatch on runner mode, prep, execute, push. Returns True iff the
    execute step failed (so main can exit nonzero).

    Encapsulates the per-runner asymmetry in one place:
    - art runners (template / direct_input / normal) return an FCL which
      this function then executes via `mu2e -c`.
    - g4bl runner executes inside `process_g4bl_jobdef`; this function
      only pushes its outputs.
    """
    # G4Beamline: process_g4bl_jobdef both prepares and executes; it
    # streams stdout to a SAM-named .log file. Push data only on success
    # but always push the log if it exists, so failed grid jobs are
    # debuggable in SAM.
    if mode == 'g4bl':
        try:
            outputs, _histo_file, log_file, succeeded = process_g4bl_jobdef(jobdesc[0], fname, args)
        except RuntimeError as e:
            print(f"=== g4bl prep failed: {e} ===")
            return True

        if not succeeded:
            print("=== g4bl execution failed ===")

        if args.dry_run:
            print("[DRY RUN] Would run: pushOutput output.txt")
        else:
            if succeeded:
                push_data(outputs, infiles="", simjob_setup=None, track_parents=False)
            else:
                print("g4bl failed - skipping data push, attempting log push")
            if log_file and Path(log_file).is_file():
                push_logs(log_file=log_file, simjob_setup=None)

        return not succeeded

    # Art runners: prep returns FCL; execute `mu2e -c` here.
    inloc = None  # populated by process_jobdef; None for template/direct_input
    if mode == 'template':
        fcl, simjob_setup = process_template(jobdesc[0], fname)
        infiles = fname
        outputs = jobdesc[0]['outputs']
    elif mode == 'direct_input':
        fcl, simjob_setup, infiles, outputs = process_direct_input(jobdesc, fname, args)
    else:
        fcl, simjob_setup, infiles, outputs, inloc = process_jobdef(jobdesc, fname, args)

    # dir:<path> inloc means inputs are on a locally-mounted filesystem
    # (typically cvmfs) and aren't SAM-registered — skip parent tracking
    # on the push. All other cases (including None for template / direct
    # input) default to tracking parents.
    track_parents = not (isinstance(inloc, str) and inloc.startswith('dir:'))

    cmd = build_mu2e_cmd(fcl, simjob_setup, args)
    print(f"Executing: {cmd}")
    print(f"Working dir: {os.getcwd()}, FCL exists: {os.path.exists(fcl)}")
    print("=== Starting Mu2e execution ===")

    job_failed = False
    try:
        run(cmd, shell=False)
        print("=== Mu2e execution completed successfully ===")
    except subprocess.CalledProcessError as e:
        job_failed = True
        print(f"=== Mu2e execution failed with exit code {e.returncode} ===")
        # Don't re-raise — we still want to upload logs and outputs

    if not args.dry_run:
        if not job_failed:
            push_data(outputs, infiles, simjob_setup=simjob_setup, track_parents=track_parents)
        else:
            print("Job failed - skipping data file push, but uploading logs")
        # Always upload logs, even on failure
        push_logs(fcl, simjob_setup=simjob_setup)
    else:
        print("[DRY RUN] Would run: pushOutput output.txt")

    return job_failed


def main():
    parser = argparse.ArgumentParser(description="Execute production jobs from job definitions.")
    parser.add_argument("--copy-input", action="store_true", help="Copy input files using mdh")
    parser.add_argument('--dry-run', action='store_true', help='Print commands without actually running pushOutput')
    parser.add_argument('--nevts', type=int, default=-1, help='Number of events to process (-1 for all events, default: -1)')
    parser.add_argument('--mu2e-options', type=str, default='', help='Extra options to pass to mu2e command (e.g., "--no-timing --debug")')
    parser.add_argument('--jobdesc', help='Path to the job descriptions JSON file (e.g., jobdefs_list.json). Required for POMS mode; ignored in direct mode (MU2EGRID_JOBDEF set).')

    args = parser.parse_args()

    if _is_direct_mode():
        _direct_main(args)
        return

    if not args.jobdesc:
        print("Error: --jobdesc is required (or set MU2EGRID_JOBDEF for direct mode)")
        sys.exit(1)

    with open(args.jobdesc, 'r') as f:
        jobdesc = json.load(f)
    mode = validate_jobdesc(jobdesc)

    fname = os.getenv("fname")
    if not fname:
        print("Error: fname environment variable is not set.")
        sys.exit(1)

    if _dispatch_and_execute(mode, jobdesc, fname, args):
        sys.exit(1)


if __name__ == "__main__":
    main()
