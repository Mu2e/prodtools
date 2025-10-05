#!/usr/bin/env python3
"""
Exact Python port of mu2eDatasetFileList Perl script.
Lists files in a Mu2e dataset with the same behavior as the original.
"""

import os
import sys
import argparse
from typing import List, Optional
from pathlib import Path

# Handle both module and standalone imports
try:
    from .samweb_wrapper import get_samweb_wrapper
except ImportError:
    # When running as standalone script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.samweb_wrapper import get_samweb_wrapper

class Mu2eFilename:
    """Parse Mu2e filenames to get relative path components."""
    
    def __init__(self, filename: str):
        self.filename = filename
    
    def relpathname(self) -> str:
        """Get relative pathname like the Perl Mu2eFilename->relpathname()."""
        # Generate hash-based subdirectory using SHA256 (matches original Perl behavior)
        import hashlib
        hash_obj = hashlib.sha256(self.filename.encode())
        hash_hex = hash_obj.hexdigest()
        hash_path = f"{hash_hex[:2]}/{hash_hex[2:4]}"
        return f"{hash_path}/{self.filename}"

class Mu2eDSName:
    """Parse Mu2e dataset names."""
    
    def __init__(self, dsname: str):
        self.dsname = dsname
    
    def absdsdir(self, location: str) -> str:
        """Get absolute dataset directory for a location."""
        # Determine the correct base path based on dataset type
        if self.dsname.startswith('log.'):
            base_path = "phy-etc"
        elif self.dsname.startswith('sim.') or self.dsname.startswith('dts.'):
            base_path = "phy-sim"
        else:
            base_path = "phy-etc"  # default
        
        # Standard Mu2e dataset locations (tape has different structure)
        if location == 'disk':
            return f"/pnfs/mu2e/persistent/datasets/{base_path}/{self.dsname.replace('.', '/')}"
        elif location == 'tape':
            return f"/pnfs/mu2e/tape/{base_path}/{self.dsname.replace('.', '/')}"
        elif location == 'scratch':
            return f"/pnfs/mu2e/scratch/datasets/{base_path}/{self.dsname.replace('.', '/')}"
        else:
            return ""
    
    def location_root(self, location: str) -> str:
        """Get location root path."""
        # Determine the correct base path based on dataset type
        if self.dsname.startswith('log.'):
            base_path = "phy-etc"
        elif self.dsname.startswith('sim.') or self.dsname.startswith('dts.'):
            base_path = "phy-sim"
        else:
            base_path = "phy-etc"  # default
        
        # Return the full path structure like the original Perl script
        if location == 'disk':
            return f"/pnfs/mu2e/persistent/datasets/{base_path}/{self.dsname.replace('.', '/')}"
        elif location == 'tape':
            return f"/pnfs/mu2e/tape/{base_path}/{self.dsname.replace('.', '/')}"
        elif location == 'scratch':
            return f"/pnfs/mu2e/scratch/datasets/{base_path}/{self.dsname.replace('.', '/')}"
        else:
            return ""

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
    
    # Check option consistency
    location_options = [args.disk, args.tape, args.scratch]
    used_opts = sum(location_options)
    
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

def get_dataset_files(dataset_name: str, location: Optional[str] = None) -> List[str]:
    """
    Get all files in a dataset as a list of full paths.
    
    Args:
        dataset_name: Dataset name to query
        location: Optional location ('disk', 'tape', 'scratch'). If None, auto-detects.
        
    Returns:
        List of full paths to all files in the dataset
        
    Raises:
        RuntimeError: If dataset not found or multiple locations exist
    """
    # Standard locations
    stdloc = ['disk', 'tape', 'scratch']
    
    # Get files from SAM
    samweb = get_samweb_wrapper()
    fns = samweb.list_definition_files(dataset_name)
    
    if not fns:
        raise RuntimeError(f"No files with dh.dataset={dataset_name} are registered in SAM.")
    
    ds = Mu2eDSName(dataset_name)
    
    # Determine location
    if location:
        # User specified a location - verify it exists
        dir_path = ds.absdsdir(location)
        if not os.path.isdir(dir_path):
            raise RuntimeError(f"Dataset {dataset_name} is not present in location '{location}'")
        fileloc = location
    else:
        # Auto-detect location
        found = []
        for loc in stdloc:
            dir_path = ds.absdsdir(loc)
            if os.path.isdir(dir_path):
                found.append(loc)
        
        if len(found) == 1:
            fileloc = found[0]
        elif len(found) > 1:
            raise RuntimeError(f"Dataset {dataset_name} exists in multiple locations: {', '.join(found)}")
        else:
            raise RuntimeError(f"Dataset {dataset_name} not found in any standard location")
    
    # Construct paths
    locroot = ds.location_root(fileloc)
    file_paths = []
    
    for f in sorted(fns):
        relpath = Mu2eFilename(f).relpathname()
        full_path = f"{locroot}/{relpath}"
        file_paths.append(full_path)
    
    return file_paths

def main():
    """Main function that replicates the exact behavior of the Perl script."""
    args = parse_args()
    dsname = args.dataset
    
    # Handle --basename mode (just print filenames)
    if args.basename:
        try:
            samweb = get_samweb_wrapper()
            fns = samweb.list_definition_files(dsname)
            for f in sorted(fns):
                try:
                    print(f)
                except BrokenPipeError:
                    break
        except Exception as e:
            print(f"Error querying SAM: {e}", file=sys.stderr)
            sys.exit(1)
        return
    
    # Handle --defname mode (use samweb.locate_files)
    if args.defname:
        try:
            samweb = get_samweb_wrapper()
            fns = samweb.list_definition_files(dsname)
            for f in sorted(fns):
                try:
                    locations = samweb.locate_files([f])
                    if f in locations and locations[f]:
                        for location_info in locations[f]:
                            if isinstance(location_info, dict) and 'full_path' in location_info:
                                full_path = location_info['full_path']
                                # Remove storage system prefixes
                                if full_path.startswith('enstore:'):
                                    full_path = full_path[8:]
                                elif full_path.startswith('dcache:'):
                                    full_path = full_path[7:]
                                
                                if full_path.startswith('/'):
                                    final_path = os.path.join(full_path, f)
                                    print(final_path)
                                    break
                except BrokenPipeError:
                    break
                except Exception:
                    continue
        except Exception as e:
            print(f"Error querying SAM: {e}", file=sys.stderr)
            sys.exit(1)
        return
    
    # Regular mode - use get_dataset_files()
    try:
        # Determine location from command-line args
        location = None
        if args.disk:
            location = 'disk'
        elif args.tape:
            location = 'tape'
        elif args.scratch:
            location = 'scratch'
        
        # Get files using the core function
        file_paths = get_dataset_files(dsname, location)
        
        # Print results
        for full_path in file_paths:
            try:
                print(full_path)
            except BrokenPipeError:
                break
                
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
