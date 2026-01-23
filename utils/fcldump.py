#!/usr/bin/env python3
import os, sys
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import subprocess
import re
from utils.prod_utils import *
from utils.samweb_wrapper import list_definitions
from utils.datasetFileList import get_dataset_files, get_definition_files

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

def find_matching_jobdef(jobdefs, desc, input_type=None):
    """Find the matching job definition by examining output files.
    
    Args:
        jobdefs: List of job definition names
        desc: Description to match (third field in filename)
        input_type: Optional input file type prefix (e.g., 'dig', 'sim') to prioritize matches
    
    Returns:
        Path to matching tarball, or None if no match found
    """
    matches = []
    
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
                output_type = output_parts[0]  # e.g., 'dig', 'mcs', 'rec'
                matches.append((jobdef, tarball_path, output_file, output_type))
    
    if not matches:
        return None
    
    # Find exact type match - input_type should always be set in normal operation
    if not input_type:
        raise ValueError("input_type must be specified")
    
    for jobdef, tarball_path, output_file, output_type in matches:
        if output_type == input_type:
            print(f"Found match in output files (type priority): {jobdef}")
            print(f"Output file: {output_file}")
            return tarball_path
    
    # No type match found - this indicates a problem with the dataset/jobdef naming
    return None

def locate_tarball(jobdef):
    print(f"Using datasetFileList to locate: {jobdef}")

    try:
        try:
            file_paths = get_dataset_files(jobdef)
        except RuntimeError as e:
            if "No files with dh.dataset" in str(e):
                file_paths = get_definition_files(jobdef)
            else:
                raise
        
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
        
        input_type = parts[0]  # e.g., 'dig', 'sim', 'mcs'
        dsconf = parts[3]
        desc = parts[2]
        
        # Get job definitions and find the match
        jobdefs = list_jobdefs(dsconf)
        if not jobdefs:
            p.error(f"No job definitions found for dsconf: {dsconf}")
        
        tarball_path = find_matching_jobdef(jobdefs, desc, input_type)
        if not tarball_path:
            p.error(f"No matching job definition found for source description: {desc}")
        
        # Generate FCL
        try:
            write_fcl(tarball_path, args.loc, args.proto, args.index, args.target)
        except RuntimeError as e:
            p.error(str(e))

if __name__ == '__main__':
    main()