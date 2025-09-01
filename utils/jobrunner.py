#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess

import json
from pathlib import Path
import glob
import shutil

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.prod_utils import run, replace_file_extensions, write_fcl
from utils.jobquery import Mu2eJobPars
from utils.jobiodetail import Mu2eJobIO

def main():
    parser = argparse.ArgumentParser(description="Process some inputs.")
    parser.add_argument("--copy-input", action="store_true", help="Copy input files using mdh")
    parser.add_argument('--dry-run', action='store_true', help='Print commands without actually running pushOutput')
    parser.add_argument('--nevts', type=int, default=-1, help='Number of events to process (-1 for all events, default: -1)')
    parser.add_argument('--jobdefs', required=True, help='Path to the jobdefs_*.json file')
    
    args = parser.parse_args()

    # Load job definition from JSON file
    with open(args.jobdefs, 'r') as f:
        jobdefs_list = json.load(f)
    
    # Get job definition by index from fname environment variable
    fname = os.getenv("fname")
    if not fname:
        print("Error: fname environment variable is not set.")
        sys.exit(1)
    
    try:
        job_index = int(fname.split('.')[4].lstrip('0') or '0')
    except (IndexError, ValueError) as e:
        print(f"Error: Unable to extract job index from filename: {e}")
        sys.exit(1)
    
    # Find which job definition this job index belongs to
    cumulative_jobs = 0
    jobdef = None
    jobdef_index = None
    
    for i, def_entry in enumerate(jobdefs_list):
        if job_index < cumulative_jobs + def_entry['njobs']:
            jobdef = def_entry
            jobdef_index = i
            break
        cumulative_jobs += def_entry['njobs']
    
    if jobdef is None:
        print(f"Error: Job index {job_index} out of range. Total jobs available: {sum(d['njobs'] for d in jobdefs_list)}")
        sys.exit(1)
    
    print(f"Job {job_index} uses definition {jobdef_index}")
    
    # Extract fields from JSON structure
    tarball = jobdef['tarball']
    job_index_num = job_index
    inloc = jobdef['inloc']

    # Copy jobdef to local directory (environment already set up by shell wrapper)
    run(f"mdh copy-file -e 3 -o -v -s disk -l local {tarball}", shell=True)

    # List input files
    job_io = Mu2eJobIO(tarball)
    inputs = job_io.job_inputs(job_index_num)
    # Flatten the dictionary values into a single list
    all_files = []
    for file_list in inputs.values():
        all_files.extend(file_list)
    infiles = " ".join(all_files)
    
    if args.copy_input and infiles.strip():
        # Copy inputs locally
        print(f"Copying input files locally: {infiles}")
        FCL = write_fcl(tarball, f"dir:{os.getcwd()}/indir", 'file', job_index_num)
        run(f"mdh copy-file -e 3 -o -v -s {inloc} -l local {infiles}", shell=True)
        run(f"mkdir indir; mv *.art indir/", shell=True)
    else:
        # Use default settings (no inputs or remote inputs)
        print(f"Using remote inputs from {inloc}")
        FCL = write_fcl(tarball, inloc, 'root', job_index_num)
    
    print(f"FCL file generated: {FCL}")
    
    # Setup command for clean environment to run using setup from jobdef
    try:
        jp = Mu2eJobPars(tarball)
        jobdef_simjob_setup = jp.setup()
        print(f"Job setup script: {jobdef_simjob_setup}")
    except Exception as e:
        print(f"ERROR: Failed to get job setup information from {tarball}")
        print(f"Exception: {e}")
        raise
    

    setup_cmd = f"source {jobdef_simjob_setup}"
    mu2e_cmd = f"mu2e -c {FCL}" + (f" -n {args.nevts}" if args.nevts > 0 else "")

    print(f"Setup command: {setup_cmd}")
    print(f"Mu2e command: {mu2e_cmd}")
    
    # Execute setup and mu2e in the same subshell with real-time output streaming
    combined_cmd = f"{setup_cmd} && {mu2e_cmd}"
    print(f"Executing: {combined_cmd}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"FCL file exists: {os.path.exists(FCL)}")
    
    try:
        print("=== Starting Mu2e execution ===")
        
        # Use subprocess.Popen for real-time output streaming
        process = subprocess.Popen(combined_cmd, shell=True, stdout=subprocess.PIPE, 
                                  stderr=subprocess.STDOUT, text=True, bufsize=1, 
                                  universal_newlines=True)
        
        # Stream output in real-time
        for line in iter(process.stdout.readline, ''):
            print(line.rstrip())
            sys.stdout.flush()  # Ensure immediate output
        
        process.stdout.close()
        return_code = process.wait()
        
        if return_code != 0:
            print(f"=== Mu2e execution failed with exit code {return_code} ===")
            raise subprocess.CalledProcessError(return_code, combined_cmd)
        
        print("=== Mu2e execution completed successfully ===")
        
    except Exception as e:
        print(f"Mu2e execution failed: {e}")
        raise
    
    # Write the list to the file in one line
    parents = infiles.split()  # List of input files
    Path("parents_list.txt").write_text("\n".join(parents) + "\n")
    
    # Handle output files and submission
    if args.dry_run:
        print("[DRY RUN] Would run: pushOutput output.txt")
    else:
        push_data(jobdef['outputs'])
        push_logs(FCL)

def push_data(outputs):
    """Handle data file management and submission using wildcard patterns from JSON outputs."""
    
    out_content = ""
    
    # Process only the files specified in the JSON outputs
    for output in outputs:
        dataset_pattern = output['dataset']
        location = output['location']
        
        # Find files that match the pattern (works for both wildcards and exact names)
        matching_files = glob.glob(dataset_pattern)
        print(f"Pattern '{dataset_pattern}' matched {len(matching_files)} files: {matching_files}")
        
        # Process each matching file
        for filename in matching_files:
            print(f"Processing file '{filename}' -> location: {location}")
            out_content += f"{location} {filename} parents_list.txt\n"

    Path("output.txt").write_text(out_content)

    # Push output using shell command directly (environment already set up by shell wrapper)
    result = run(f"pushOutput output.txt", shell=True)
    if result != 0:
        print(f"Warning: pushOutput returned exit code {result}")

def push_logs(FCL):
    """Handle log file management and submission."""
    # In production mode, copy the job submission log file from jsb_tmp to LOGFILE_LOC.
    LOGFILE = replace_file_extensions(FCL, "log", "log")

    # Copy the jobsub log if JSB_TMP is defined
    jsb_tmp = os.getenv("JSB_TMP")
    if jsb_tmp:
        jobsub_log = "JOBSUB_LOG_FILE"
        src = os.path.join(jsb_tmp, jobsub_log)
        print(f"Copying jobsub log from {src} to {LOGFILE}")
        shutil.copy(src, LOGFILE)

    # Only create log output if the log file actually exists
    if Path(LOGFILE).exists():
        # Create log output content
        log_content = f"disk {LOGFILE} parents_list.txt\n"
        Path("log_output.txt").write_text(log_content)
        print(f"Created log_output.txt with content: {log_content.strip()}")

        # Push log output using shell command directly (environment already set up by shell wrapper)
        result = run(f"pushOutput log_output.txt", shell=True)
        if result != 0:
            print(f"Warning: pushOutput returned exit code {result}")
    else:
        print(f"Warning: Log file {LOGFILE} not found, skipping log push")

if __name__ == "__main__":
    main()
