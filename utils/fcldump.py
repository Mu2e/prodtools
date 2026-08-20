#!/usr/bin/env python3
import os, sys
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from utils.prod_utils import write_fcl, write_direct_input_fcl
from utils.job_common import Mu2eName
from utils.jobfcl import Mu2eJobFCL
# Dataset→cnf resolution lives in jobdef_lookup so other tools (latestDatasets
# --complete-only) can reuse it without importing this entry point.
from utils.jobdef_lookup import (list_jobdefs, find_matching_jobdef, set_verbose,
                                 is_generic_cnf, derive_generic_input,
                                 sample_dataset_target)


def write_fcl_direct_input(tarball, fname, loc='tape', proto='root'):
    """Generate FCL for direct-input mode: generic tarball + specific input file.

    Debug view of the worker's direct-input FCL (write_direct_input_fcl):
    the input resolves to a full xroot/file URL and overridden base lines
    are stripped so no unresolved {desc} placeholders show.
    """
    job_fcl = Mu2eJobFCL(tarball, inloc=loc, proto=proto)
    return write_direct_input_fcl(job_fcl, fname,
                                  format_input=True, filter_base=True)


def main():
    p = argparse.ArgumentParser(description='Generate FCL from dataset name or target file')
    p.add_argument('--dataset', help='Dataset name (art: dts.mu2e.RPCInternalPhysical.MDC2020az.art or jobdef: cnf.mu2e.ExtractedCRY.MDC2020av.tar)')
    p.add_argument('--proto', default='root')
    p.add_argument('--loc', default='tape')
    p.add_argument('--index', type=int, default=0)
    p.add_argument('--target', help='Target file (e.g., dts.mu2e.RPCInternalPhysical.MDC2020az.001202_00000296.art)')
    p.add_argument('--local-jobdef', help='Direct path to local job definition file')
    p.add_argument('--fname', help='Input art file for direct-input mode (use with --local-jobdef for generic tarballs)')
    p.add_argument('--list-dsconf', help='List all job definitions for a given dsconf (e.g., MDC2020ba_best_v1_3)')
    args = p.parse_args()
    set_verbose(True)  # fcldump is an interactive tool: keep its resolution trace

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
        if args.fname:
            # Direct-input mode: generic tarball + specific input file
            write_fcl_direct_input(jobdef, args.fname, args.loc, args.proto)
        elif args.target and is_generic_cnf(jobdef):
            # Generic tarball + --target output file: derive the input, generate.
            fname = derive_generic_input(jobdef, args.target)
            print(f"Generic cnf: target {args.target} -> input {fname}")
            write_fcl_direct_input(jobdef, fname, args.loc, args.proto)
        else:
            write_fcl(jobdef, args.loc, args.proto, args.index, args.target)
        
    else:
        source = args.dataset or args.target
        
        # Parse dataset name
        try:
            src = Mu2eName.parse(source)
        except ValueError:
            p.error(f"Invalid dataset: {source}")

        input_type = src.tier  # e.g., 'dig', 'sim', 'mcs'
        dsconf = src.dsconf
        desc = src.description
        
        # Get job definitions and find the match
        jobdefs = list_jobdefs(dsconf)
        if not jobdefs:
            p.error(f"No job definitions found for dsconf: {dsconf}")
        
        tarball_path = find_matching_jobdef(jobdefs, desc, input_type)
        if not tarball_path:
            p.error(f"No matching job definition found for source description: {desc}")

        # A generic cnf defers {desc}/sequencer to runtime. With a --target output
        # file we can derive the input and generate; a bare --dataset has no
        # sequencer, so report how to generate instead of crashing in write_fcl.
        if is_generic_cnf(tarball_path):
            print(f"Matched generic cnf: {tarball_path}")
            target = args.target
            if not target and args.dataset and src.tier != 'cnf':
                # No sequencer of our own — borrow one from a file of the output
                # dataset. Sorted, so the same command picks the same file.
                target = sample_dataset_target(args.dataset, args.index)
                if target:
                    print(f"No --target given; using {target} "
                          f"(index {args.index} of the dataset, sorted by name); "
                          f"pass --target or --index to pick another.")
            if target:
                fname = derive_generic_input(tarball_path, target)
                print(f"target {target} -> input {fname}")
                write_fcl_direct_input(tarball_path, fname, args.loc, args.proto)
                return
            print("This is a generic tarball (output desc deferred as {desc}); a bare "
                  "--dataset has no sequencer to resolve it.")
            print("Generate by passing the output file via --target, or an input via --fname:")
            print(f"  fcldump --dataset <output dataset> --target <output art file>")
            print(f"  fcldump --local-jobdef {tarball_path} --fname <input art file>")
            return

        # Generate FCL
        try:
            write_fcl(tarball_path, args.loc, args.proto, args.index, args.target)
        except RuntimeError as e:
            p.error(str(e))

if __name__ == '__main__':
    main()