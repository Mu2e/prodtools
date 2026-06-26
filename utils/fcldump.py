#!/usr/bin/env python3
import os, sys
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import subprocess
import re
from pathlib import Path
from utils.prod_utils import write_fcl
from utils.job_common import Mu2eName
from utils.jobfcl import Mu2eJobFCL
# Dataset→cnf resolution lives in jobdef_lookup so other tools (latestDatasets
# --complete-only) can reuse it without importing this entry point.
from utils.jobdef_lookup import list_jobdefs, find_matching_jobdef, set_verbose


def write_fcl_direct_input(tarball, fname, loc='tape', proto='root'):
    """Generate FCL for direct-input mode: generic tarball + specific input file.

    Parses desc and sequencer from fname, resolves output filenames, and writes
    a FCL that appends source.fileNames and output overrides to the base FCL.
    """
    from pathlib import Path
    n = Mu2eName.parse(Path(fname).name)
    if not n.is_file:
        raise ValueError(
            f"Invalid filename format: {fname}. "
            f"Expected tier.owner.desc.dsconf.sequencer.ext"
        )
    desc = n.description
    seq = n.sequencer

    job_fcl = Mu2eJobFCL(tarball, inloc=loc, proto=proto)
    base_fcl = job_fcl._extract_fcl()
    outputs_map = job_fcl.job_outputs(0, override_desc=desc, override_seq=seq)

    # Resolve the input file to a full xroot/file path via SAM
    formatted_fname = job_fcl._format_filename(fname)

    # Strip lines from the base FCL that will be overridden below (avoids
    # showing unresolved {desc} placeholders from the generic tarball)
    override_keys = set(outputs_map.keys()) | {'source.fileNames'}
    filtered_lines = [
        line for line in base_fcl.splitlines()
        if not any(line.lstrip().startswith(k) for k in override_keys)
    ]
    filtered_fcl = '\n'.join(filtered_lines)

    fcl = f"{Path(fname).stem}.fcl"
    with open(fcl, 'w') as f:
        f.write(filtered_fcl)
        f.write("\n# Direct-input overrides:\n")
        f.write(f'source.fileNames: ["{formatted_fname}"]\n')
        for key, filename in outputs_map.items():
            f.write(f'{key}: "{filename}"\n')

    print(f"Wrote {fcl}")
    print(f"\n--- {fcl} content ---")
    with open(fcl) as f:
        print(f.read())
    return fcl


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
        
        # Generate FCL
        try:
            write_fcl(tarball_path, args.loc, args.proto, args.index, args.target)
        except RuntimeError as e:
            p.error(str(e))

if __name__ == '__main__':
    main()