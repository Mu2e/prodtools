#!/usr/bin/env python3
import os, sys
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import subprocess
import re
from utils.prod_utils import *
from utils.samweb_wrapper import list_definitions

def list_jobdefs(dsconf):
    """List all job definitions for a given dsconf using samweb_wrapper."""
    # Use SAMWeb server-side filtering with % wildcard
    pattern = f"cnf.mu2e.%.{dsconf}.tar"
    print(f"Searching for job definitions with pattern: cnf.mu2e.*.{dsconf}.tar")
    
    try:
        definitions = list_definitions(defname=pattern)
        
        if not definitions:
            print(f"No job definitions found for dsconf: {dsconf}")
            return []
        
        print(f"Found {len(definitions)} job definitions:")
        for definition in definitions:
            if definition.strip():
                print(f"  {definition}")
        
        return definitions
        
    except Exception as e:
        print(f"Error accessing SAM: {e}")
        return []

def find_matching_jobdef(jobdefs, desc):
    """Find the matching job definition by examining output files."""
    
    for jobdef in jobdefs:
        # Locate the tarball first
        tarball_path = locate_tarball(jobdef)
        
        # Use Mu2eJobIO class to get output files
        from utils.jobiodetail import Mu2eJobIO
        job_io = Mu2eJobIO(tarball_path)
        outputs = job_io.job_outputs(0)
        
        # Check for exact match: desc should be the third field in output filename
        for output_file in outputs.values():
            output_parts = output_file.split('.')
            if len(output_parts) == 6 and output_parts[2] == desc:
                print(f"Found match in output files: {jobdef}")
                print(f"Output file: {output_file}")
                return tarball_path
    
    return None

def locate_tarball(jobdef):
    print(f"Using datasetFileList to locate: {jobdef}")

    try:
        from utils.datasetFileList import get_dataset_files
        file_paths = get_dataset_files(jobdef)
        
        if not file_paths:
            raise RuntimeError(f"Tarball not found for: {jobdef}")
        
        # Get the first tarball found
        tarball_path = file_paths[0]
        if not os.path.exists(tarball_path):
            raise RuntimeError(f"Tarball not found for: {jobdef}")
        
        print(f"Found tarball at: {tarball_path}")
        return tarball_path
        
    except Exception as e:
        raise RuntimeError(f"Error locating tarball for {jobdef}: {e}")


def main():
    p = argparse.ArgumentParser(description='Generate FCL from dataset name or target file')
    p.add_argument('--dataset', help='Dataset name (art: dts.mu2e.RPCInternalPhysical.MDC2020az.art or jobdef: cnf.mu2e.ExtractedCRY.MDC2020av.tar)')
    p.add_argument('--proto', default='root')
    p.add_argument('--loc', default='tape')
    p.add_argument('--index', type=int, default=0)
    p.add_argument('--target', help='Target file (e.g., dts.mu2e.RPCInternalPhysical.MDC2020az.001202_00000296.art)')
    p.add_argument('--local-jobdef', help='Direct path to local job definition file')
    p.add_argument('--list-dsconf', help='List all job definitions for a given dsconf (e.g., MDC2020ba_best_v1_3)')
    args = p.parse_args()

    # Handle --list-dsconf option
    if args.list_dsconf:
        list_jobdefs(args.list_dsconf)
        return
    
    # Require either dataset or target, unless using --local-jobdef
    if not args.dataset and not args.target and not args.local_jobdef:
        p.error("Either --dataset or --target must be provided, or use --local-jobdef")

    if args.local_jobdef:
        # Local mode: work with existing local files
        jobdef = args.local_jobdef
        if not os.path.exists(jobdef):
            p.error(f"Job definition file not found: {jobdef}")
        
        print(f"Using local job definition: {jobdef}")
        write_fcl(jobdef, args.loc, args.proto, args.index, args.target)
        
    else:
        source = args.dataset or args.target
        
        # Parse dataset name
        parts = source.split('.')
        if len(parts) < 5:
            p.error(f"Invalid dataset: {source}")
        
        dsconf = parts[3]
        desc = parts[2]
        
        # Get job definitions and find the match
        jobdefs = list_jobdefs(dsconf)
        if not jobdefs:
            p.error(f"No job definitions found for dsconf: {dsconf}")
        
        tarball_path = find_matching_jobdef(jobdefs, desc)
        if not tarball_path:
            p.error(f"No matching job definition found for source description: {desc}")
        
        # Generate FCL
        try:
            write_fcl(tarball_path, args.loc, args.proto, args.index, args.target)
        except RuntimeError as e:
            p.error(str(e))

if __name__ == '__main__':
    main()