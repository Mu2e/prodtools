#!/usr/bin/env python3
"""
Exact Python port of mu2eDatasetFileList Perl script.
Lists files in a Mu2e dataset with the same behavior as the original.
"""

import os
import sys
import argparse
from typing import List, Optional

try:
    from .job_common import Mu2eName
    from .file_resolver import path_from_sam_location
    from .samweb_wrapper import (files_in_dataset, list_definition_files,
                                 locate_files_strict)
except ImportError:
    # Standalone script: not run as a package.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.job_common import Mu2eName
    from utils.file_resolver import path_from_sam_location
    from utils.samweb_wrapper import (files_in_dataset, list_definition_files,
                                      locate_files_strict)


def _dataset_dir(dsname: str, location: str) -> str:
    """Absolute /pnfs directory for a Mu2e dataset at the given location.
    Delegates to file_resolver.dataset_dir (the layout's single home)."""
    from utils.file_resolver import dataset_dir
    return dataset_dir(dsname, location)

def parse_args():
    """Parse command line arguments exactly like the Perl version."""
    parser = argparse.ArgumentParser(
        description='Lists files in a Mu2e dataset.',
        add_help=False
    )
    parser.add_argument('--help', action='store_true', help='Print help message')
    parser.add_argument('--basename', action='store_true', help='Print file basenames instead of absolute /pnfs pathnames')
    parser.add_argument('--disk', action='store_true', help='Print pathnames of files in disk location')
    parser.add_argument('--tape', action='store_true', help='Print pathnames of files in tape location')
    parser.add_argument('--scratch', action='store_true', help='Print pathnames of files in scratch location')
    parser.add_argument('--defname', action='store_true', help='Treat input as SAM definition name instead of dataset name')
    parser.add_argument('dataset', nargs='?', help='Dataset name or SAM definition name')
    
    args = parser.parse_args()
    
    if args.help:
        print_usage()
        sys.exit(0)
    
    if not args.dataset:
        print("ERROR: Exactly one dataset name must be specified.  Try the --help option.", file=sys.stderr)
        sys.exit(1)
    
    used_opts = sum([args.disk, args.tape, args.scratch])
    
    if args.basename and used_opts > 0:
        print("Error: inconsistent options: --basename conflicts with location options", file=sys.stderr)
        sys.exit(1)
    
    if used_opts > 1:
        print("Error: inconsistent options: multiple location options specified", file=sys.stderr)
        sys.exit(1)
    
    return args

def print_usage():
    """Print usage message exactly like the Perl version."""
    script_name = os.path.basename(sys.argv[0])
    print(f"""Usage:
        {script_name} [options] <dsname>

Print out a sorted list of files in a Mu2e dataset.
Options:

        --basename           Print file basenames instead of
                             absolute /pnfs pathnames.

        --disk
        --tape
        --scratch            Print pathnames of files in the given
                             location.  But default the script tries
                             to figure out the location automatically.
                             If that fails, you will be asked to specify
                             a location.

        --defname            Treat input as SAM definition name instead
                             of dataset name.

        --help               Print this message.
""")

def get_dataset_files(dataset_name: str, location: Optional[str] = None,
                      max_files: Optional[int] = None) -> List[str]:
    """Get all files in a dataset as a list of full paths.

    location: 'disk'/'tape'/'scratch', or None to auto-detect.
    max_files: if set, build paths for only the first max_files names
    (sorted order) — capped at the source instead of sliced afterwards.

    Raises RuntimeError if the dataset isn't found or has no standard
    location.
    """
    stdloc = ['disk', 'tape', 'scratch']
    fns = files_in_dataset(dataset_name)

    if not fns:
        raise RuntimeError(f"No files with dh.dataset={dataset_name} are registered in SAM.")

    if location:
        fileloc = location
    else:
        # Auto-detect: first location whose directory exists.
        fileloc = None
        for loc in stdloc:
            if os.path.isdir(_dataset_dir(dataset_name, loc)):
                fileloc = loc
                break
        if not fileloc:
            raise RuntimeError(f"Dataset {dataset_name} not found in any standard location")

    locroot = _dataset_dir(dataset_name, fileloc)
    file_paths = []

    fns = sorted(fns)
    if max_files is not None:
        fns = fns[:max_files]

    for f in fns:
        relpath = Mu2eName.parse(f).relpathname()
        full_path = f"{locroot}/{relpath}"
        file_paths.append(full_path)

    return file_paths

def get_definition_files(definition_name: str) -> List[str]:
    """Get file paths for a SAM definition (e.g. log.mu2e.X.Y.log)."""
    fns = sorted(list_definition_files(definition_name))

    # One SAM round-trip for the whole definition (thousands of files for
    # log datasets) instead of one locate per file. Fail loud: a SAM
    # outage must not masquerade as an empty file list.
    locations_map = locate_files_strict(fns) if fns else {}

    file_paths = []
    for f in fns:
        for location_info in locations_map.get(f) or []:
            try:
                file_paths.append(path_from_sam_location(f, location_info))
                break  # Take first valid location
            except ValueError:
                continue

    return file_paths

def _print_all(lines):
    """Print each line; stop quietly when the reader (e.g. `head`) goes away."""
    for line in lines:
        try:
            print(line)
        except BrokenPipeError:
            break


def main():
    """Main function that replicates the exact behavior of the Perl script."""
    args = parse_args()
    dsname = args.dataset

    if args.basename:
        _print_all(sorted(files_in_dataset(dsname)))
        return

    if args.defname:
        _print_all(get_definition_files(dsname))
        return

    try:
        location = None
        if args.disk:
            location = 'disk'
        elif args.tape:
            location = 'tape'
        elif args.scratch:
            location = 'scratch'

        _print_all(get_dataset_files(dsname, location))
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
