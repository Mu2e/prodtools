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
    process_jobdef,
    push_data,
    push_logs
)

def main():
    parser = argparse.ArgumentParser(description="Execute production jobs from job definitions.")
    parser.add_argument("--copy-input", action="store_true", help="Copy input files using mdh")
    parser.add_argument('--dry-run', action='store_true', help='Print commands without actually running pushOutput')
    parser.add_argument('--nevts', type=int, default=-1, help='Number of events to process (-1 for all events, default: -1)')
    parser.add_argument('--mu2e-options', type=str, default='', help='Extra options to pass to mu2e command (e.g., "--no-timing --debug")')
    parser.add_argument('--jobdesc', required=True, help='Path to the job descriptions JSON file (e.g., jobdefs_list.json)')
    
    args = parser.parse_args()

    # Load and validate job descriptions from JSON file
    with open(args.jobdesc, 'r') as f:
        jobdesc = json.load(f)
    
    is_template_mode = validate_jobdesc(jobdesc)
    
    # Get job definition by index from fname environment variable
    fname = os.getenv("fname")
    if not fname:
        print("Error: fname environment variable is not set.")
        sys.exit(1)
    
    # Process job based on mode
    if is_template_mode:
        fcl, simjob_setup = process_template(jobdesc[0], fname)
        infiles = fname  # In template mode, input is just the fname
        outputs = jobdesc[0]['outputs'] # patern for output location
    else:
        fcl, simjob_setup, infiles, outputs = process_jobdef(jobdesc, fname, args)
    
    setup_cmd = f"source {simjob_setup}"
    mu2e_cmd = f"mu2e -c {fcl}"
    if args.nevts > 0:
        mu2e_cmd += f" -n {args.nevts}"
    if args.mu2e_options.strip():
        mu2e_cmd += f" {args.mu2e_options.strip()}"

    combined_cmd = f"{setup_cmd} && {mu2e_cmd}"
    print(f"Executing: {combined_cmd}")
    print(f"Working dir: {os.getcwd()}, FCL exists: {os.path.exists(fcl)}")
    
    print("=== Starting Mu2e execution ===")
    job_failed = False
    try:
        run(combined_cmd, shell=True)
        print("=== Mu2e execution completed successfully ===")
    except subprocess.CalledProcessError as e:
        job_failed = True
        print(f"=== Mu2e execution failed with exit code {e.returncode} ===")
        # Don't re-raise - we still want to upload logs and outputs
    
    # Handle output files and submission (even if job failed)
    if not args.dry_run:
        if not job_failed:
            push_data(outputs, infiles, simjob_setup=simjob_setup)
        else:
            print("Job failed - skipping data file push, but uploading logs")
        # Always upload logs, even on failure
        push_logs(fcl, simjob_setup=simjob_setup)
    else:
        print("[DRY RUN] Would run: pushOutput output.txt")
    
    # Exit with error code if job failed
    if job_failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
