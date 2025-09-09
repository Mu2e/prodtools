#!/usr/bin/env python3
"""
Python port of mu2eDatasetFileList functionality.
Locates files in Mu2e datasets.
"""

import os
import sys
from typing import List

# Handle both module and standalone imports
try:
    from .samweb_wrapper import get_samweb_wrapper
except ImportError:
    # When running as standalone script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.samweb_wrapper import get_samweb_wrapper

class Mu2eDatasetFileList:
    """Python port of mu2eDatasetFileList functionality."""
    
    def __init__(self):
        """Initialize with SAM web client."""
        self.samweb = get_samweb_wrapper()
    

    def locate_all_dataset_files(self, dataset_name: str) -> List[str]:
        """
        Locate all files in a dataset (equivalent to mu2eDatasetFileList).
        
        Args:
            dataset_name: Dataset name to query as metadata (dh.dataset=dataset_name)
            
        Returns:
            List of full paths to all files, or empty list if not found
        """
        # Use metadata query like the original mu2eDatasetFileList
        files = self.samweb.list_files(f'dh.dataset={dataset_name}')
        if not files:
            print(f"No files with dh.dataset={dataset_name} are registered in SAM.", file=sys.stderr)
            return []
        
        # Locate all files
        file_paths = []
        for filename in files:
            try:
                location = self.samweb.locate_file(filename)
                if not location or not isinstance(location, dict):
                    continue
                
                full_path = location.get('full_path', '')
                if not full_path:
                    continue
                
                # Remove dcache: prefix if present
                if full_path.startswith('dcache:'):
                    full_path = full_path[7:]  # Remove 'dcache:'
                
                # Construct the full file path
                file_paths.append(f"{full_path}/{filename}")
                
            except Exception as e:
                # Skip individual files that can't be located, but continue with others
                print(f"Warning: Could not locate file {filename}: {e}", file=sys.stderr)
                continue
        
        return file_paths


def locate_all_dataset_files(dataset_name: str) -> List[str]:
    """Convenience function to locate all dataset files."""
    filelist = Mu2eDatasetFileList()
    return filelist.locate_all_dataset_files(dataset_name)

def main():
    """Main function for command-line usage."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Python port of mu2eDatasetFileList')
    parser.add_argument('dataset_name', help='Dataset name')
    parser.add_argument('--basename', action='store_true', help='Print basename only')
    
    args = parser.parse_args()
    
    # Get all files to match original behavior
    results = locate_all_dataset_files(args.dataset_name)
    if results:
        for result in results:
            if args.basename:
                print(os.path.basename(result))
            else:
                print(result)
    else:
        print(f"File not found for dataset: {args.dataset_name}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
