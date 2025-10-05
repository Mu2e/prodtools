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

def main():
    """Main function that replicates the exact behavior of the Perl script."""
    args = parse_args()
    dsname = args.dataset
    
    # Standard locations (same as Perl version)
    stdloc = ['disk', 'tape', 'scratch']
    
    # Check if user specified a location and verify it exists
    fileloc = None
    if not args.basename:
        if args.disk:
            fileloc = 'disk'
        elif args.tape:
            fileloc = 'tape'
        elif args.scratch:
            fileloc = 'scratch'
        
        # If user specified a location, verify it exists
        if fileloc:
            ds = Mu2eDSName(dsname)
            dir_path = ds.absdsdir(fileloc)
            if not os.path.isdir(dir_path):
                print(f"Error: dataset {dsname} is not present in the specified location '{fileloc}'", file=sys.stderr)
                sys.exit(1)
    
    # Get files from SAM
    try:
        samweb = get_samweb_wrapper()
        # Use SAM definition (works for both datasets and explicit definitions)
        fns = samweb.list_definition_files(dsname)
    except Exception as e:
        print(f"Error querying SAM: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not fns:
        print(f"No files with dh.dataset={dsname} are registered in SAM.", file=sys.stderr)
        sys.exit(1)
    
    if args.basename:
        # Print just the filenames
        for f in sorted(fns):
            try:
                print(f)
            except BrokenPipeError:
                # Handle broken pipe gracefully (e.g., when using 'head')
                break
    elif args.defname:
        # For SAM definitions, locate files using samweb wrapper
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
                                # Append filename to directory path
                                final_path = os.path.join(full_path, f)
                                print(final_path)
                                break  # Take first valid location
            except BrokenPipeError:
                # Handle broken pipe gracefully (e.g., when using 'head')
                break
            except Exception:
                # Skip files that can't be located
                continue
    else:
        # Print full paths for regular datasets
        ds = Mu2eDSName(dsname)
        
        if fileloc is None:
            # Figure out what location to print (auto-detect)
            found = []
            for loc in stdloc:
                dir_path = ds.absdsdir(loc)
                if os.path.isdir(dir_path):
                    found.append(loc)
            
            if len(found) == 1:
                fileloc = found[0]
            elif len(found) > 1:
                print(f"Dataset {dsname} seems to exist in multiple locations: {', '.join(found)}. Please use a command line option to specify which one to use.", file=sys.stderr)
                sys.exit(1)
            else:
                # Files not found in standard locations
                print(f"Dataset {dsname} does not exist in any of the standard locations {', '.join(stdloc)}. You can use the --basename option to print out just SAM filenames.", file=sys.stderr)
                print("If this is an 'old' dataset uploaded via FTS, you can try 'setup dhtools; samToPnfs {dsname}' to get a list of files.", file=sys.stderr)
                sys.exit(1)
        
        # Print full paths using the exact same logic as the original Perl script
        locroot = ds.location_root(fileloc)
        
        # Use the same path construction as the Perl version:
        # $locroot . '/' . Mu2eFilename->parse($f)->relpathname()
        for f in sorted(fns):
            try:
                # Construct path exactly like the Perl version
                relpath = Mu2eFilename(f).relpathname()
                full_path = f"{locroot}/{relpath}"
                print(full_path)
            except BrokenPipeError:
                # Handle broken pipe gracefully (e.g., when using 'head')
                break

def locate_all_dataset_files(dataset_name: str) -> List[str]:
    """
    Locate all files in a dataset using existing datasetFileList.py functionality.
    
    Args:
        dataset_name: Dataset name to query as metadata (dh.dataset=dataset_name)
        
    Returns:
        List of full paths to all files, or empty list if not found
    """
    try:
        samweb = get_samweb_wrapper()
        if not samweb:
            return []
        
        # Use metadata query like the original mu2eDatasetFileList
        # Query: dh.dataset = dataset_name
        query = f"dh.dataset = {dataset_name}"
        files = samweb.client.listFiles(query)
        
        if not files:
            return []
        
        # Use existing functionality from main() - find the best location
        ds = Mu2eDSName(dataset_name)
        stdloc = ['disk', 'tape', 'scratch']
        
        # Find the first available location
        fileloc = None
        for location in stdloc:
            try:
                dir_path = ds.absdsdir(location)
                if os.path.isdir(dir_path):
                    fileloc = location
                    break
            except Exception:
                continue
        
        if not fileloc:
            return []
        
        # Use existing path construction logic from main()
        locroot = ds.location_root(fileloc)
        
        # Construct full paths using the same logic as main()
        file_paths = []
        for f in sorted(files):
            try:
                # Use the exact same path construction as main()
                relpath = Mu2eFilename(f).relpathname()
                full_path = f"{locroot}/{relpath}"
                
                if os.path.exists(full_path):
                    file_paths.append(full_path)
                    
            except Exception as e:
                print(f"Error constructing path for {f}: {e}")
                continue
        
        return file_paths
        
    except Exception as e:
        print(f"Error locating dataset files for {dataset_name}: {e}")
        return []

if __name__ == '__main__':
    main()
