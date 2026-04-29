#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess

import json
from pathlib import Path

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
)


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
    parser.add_argument('--jobdesc', required=True, help='Path to the job descriptions JSON file (e.g., jobdefs_list.json)')

    args = parser.parse_args()

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
