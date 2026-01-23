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
    from .job_common import get_samweb_wrapper
except ImportError:
    # When running as standalone script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.job_common import get_samweb_wrapper

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
    
    def _get_base_path(self) -> str:
        """Determine the correct base path based on dataset type."""
        if self.dsname.startswith(('sim.', 'dts.', 'dig.', 'mcs.')):
            return "phy-sim"
        elif self.dsname.startswith('nts.'):
            return "phy-nts"
        return "phy-etc"
    
    def _build_path(self, location: str) -> str:
        """Build the full path for a given location."""
        base_path = self._get_base_path()
        ds_path = self.dsname.replace('.', '/')
        
        location_map = {
            'disk': f"/pnfs/mu2e/persistent/datasets/{base_path}/{ds_path}",
            'tape': f"/pnfs/mu2e/tape/{base_path}/{ds_path}",
            'scratch': f"/pnfs/mu2e/scratch/datasets/{base_path}/{ds_path}"
        }
        
        return location_map.get(location, "")
    
    def absdsdir(self, location: str) -> str:
        """Get absolute dataset directory for a location."""
        return self._build_path(location)
    
    def location_root(self, location: str) -> str:
        """Get location root path (same as absdsdir)."""
        return self._build_path(location)

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
    fns = samweb.list_files(f"dh.dataset {dataset_name}")
    
    if not fns:
        raise RuntimeError(f"No files with dh.dataset={dataset_name} are registered in SAM.")
    
    ds = Mu2eDSName(dataset_name)
    
    # Determine location
    if location:
        fileloc = location
    else:
        # Auto-detect: check which location directory exists
        fileloc = None
        for loc in stdloc:
            if os.path.isdir(ds.absdsdir(loc)):
                fileloc = loc
                break
        if not fileloc:
            raise RuntimeError(f"Dataset {dataset_name} not found in any standard location")
    
    # Construct paths
    locroot = ds.location_root(fileloc)
    file_paths = []
    
    for f in sorted(fns):
        relpath = Mu2eFilename(f).relpathname()
        full_path = f"{locroot}/{relpath}"
        file_paths.append(full_path)
    
    return file_paths

def get_definition_files(definition_name: str) -> List[str]:
    """
    Get file paths for a SAM definition.
    
    Args:
        definition_name: SAM definition name (e.g., log.mu2e.X.Y.log)
    
    Returns:
        List of full file paths
    """
    samweb = get_samweb_wrapper()
    fns = samweb.list_definition_files(definition_name)
    
    file_paths = []
    for f in sorted(fns):
        try:
            locations = samweb.locate_files([f])
            
            if f not in locations or not locations[f]:
                continue
            
            for location_info in locations[f]:
                if not isinstance(location_info, dict) or 'full_path' not in location_info:
                    continue
                
                full_path = location_info['full_path']
                
                # Remove storage system prefixes
                from utils.job_common import remove_storage_prefix
                full_path = remove_storage_prefix(full_path)
                
                if full_path.startswith('/'):
                    final_path = os.path.join(full_path, f)
                    file_paths.append(final_path)
                    break  # Take first valid location
                    
        except Exception:
            continue
    
    return file_paths

def main():
    """Main function that replicates the exact behavior of the Perl script."""
    args = parse_args()
    dsname = args.dataset
    
    # Handle --basename mode (just print filenames)
    if args.basename:
        samweb = get_samweb_wrapper()
        fns = samweb.list_files(f"dh.dataset {dsname}")
        for f in sorted(fns):
            try:
                print(f)
            except BrokenPipeError:
                break
        return
    
    # Handle --defname mode (use get_definition_files helper)
    if args.defname:
        file_paths = get_definition_files(dsname)
        for final_path in file_paths:
            try:
                    print(final_path)
            except BrokenPipeError:
                break
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
