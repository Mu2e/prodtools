#!/usr/bin/env python3
"""
Python equivalent of mu2ejobquery Perl script.
Extracts information from Mu2e job parameter files (.tar files containing jobpars.json).
"""

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path

from .job_common import Mu2eJobBase

class Mu2eJobPars(Mu2eJobBase):
    """Python equivalent of Mu2eJobPars.pm"""
    
    def __init__(self, parfile):
        """Initialize with a job parameter file (.tar)"""
        super().__init__(parfile)
        self.parfile = parfile  # Keep for backward compatibility
        self.json_data = self._extract_json()
    

    
    def jobname(self):
        """Get the job name"""
        return self.json_data.get('jobname', '')
    
    def njobs(self):
        """Get the number of jobs (calculated from input files and merge factor)"""
        # Check if njobs is explicitly set
        if 'njobs' in self.json_data:
            return self.json_data['njobs']
        
        # Calculate njobs from inputs like Perl version does
        tbs = self.json_data.get('tbs', {})
        inputs = tbs.get('inputs', {})
        
        if not inputs:
            return 0
        
        # Get the primary input count (usually source.fileNames)
        source_files = inputs.get('source.fileNames')
        if source_files and isinstance(source_files, list) and len(source_files) >= 2:
            merge_factor, file_list = source_files
            # Extract merge factor from job definition (like Perl does)
            # merge_factor is the first element, file_list is the second element
            if not isinstance(merge_factor, int) or merge_factor <= 0:
                # Fallback to default if merge_factor is invalid (like Perl does)
                merge_factor = 1
            
            total_files = len(file_list) if file_list else 0
            if total_files == 0:
                return 0
            
            # Use same calculation logic as Perl calculate_njobs function
            # njobs = total_files / merge_factor + (remainder ? 1 : 0)
            njobs = total_files // merge_factor
            if total_files % merge_factor:
                njobs += 1
            
            return njobs
        
        # Check for samplinginput (like Perl does)
        samplinginput = tbs.get('samplinginput', {})
        if samplinginput:
            # Get the first samplinginput entry (like Perl does with 'each')
            for key, value in samplinginput.items():
                if isinstance(value, list) and len(value) >= 2:
                    merge_factor, file_list = value
                    # Extract merge factor from job definition
                    if not isinstance(merge_factor, int) or merge_factor <= 0:
                        # Fallback to default if merge_factor is invalid (like Perl does)
                        merge_factor = 1
                    
                    total_files = len(file_list) if file_list else 0
                    if total_files == 0:
                        return 0
                    
                    # Use same calculation logic as Perl calculate_njobs function
                    njobs = total_files // merge_factor
                    if total_files % merge_factor:
                        njobs += 1
                    
                    return njobs
                break  # Only process the first entry like Perl does
        
        return 0
    
    def input_datasets(self):
        """Get list of input datasets"""
        # Check for explicit input_datasets field first
        if 'input_datasets' in self.json_data:
            return self.json_data['input_datasets']
        
        # Extract from TBS inputs and auxin sections like Perl does
        tbs = self.json_data.get('tbs', {})
        datasets = set()
        
        def extract_dataset_from_files(file_list):
            """Extract dataset name from first file in list."""
            if not file_list:
                return None
            first_file = file_list[0]
            parts = first_file.split('.')
            if len(parts) >= 4:
                return '.'.join(parts[:4]) + '.art'
            return None
        
        # Get datasets from inputs
        inputs = tbs.get('inputs', {})
        for key, value in inputs.items():
            if isinstance(value, list) and len(value) >= 2:
                _, file_list = value
                dataset = extract_dataset_from_files(file_list)
                if dataset:
                    datasets.add(dataset)
        
        # Get datasets from auxiliary inputs
        auxin = tbs.get('auxin', {})
        for key, value in auxin.items():
            if isinstance(value, list) and len(value) >= 2:
                _, file_list = value
                dataset = extract_dataset_from_files(file_list)
                if dataset:
                    datasets.add(dataset)
        
        return list(datasets)
    
    def output_datasets(self):
        """Get list of output datasets"""
        return self.json_data.get('output_datasets', [])
    
    def setup(self):
        """Get the setup file path"""
        return self.json_data.get('setup', '')
    
    def codesize(self):
        """Get the size of the compressed code tarball"""
        # This would need to check for embedded code in the tar file
        # For now, return 0 as placeholder
        return 0
    
    def extract_code(self):
        """Extract embedded code tarball to current directory"""
        with tarfile.open(self.parfile, 'r') as tar:
            # Look for embedded code files
            for member in tar.getmembers():
                if member.name.startswith('code/') or member.name.endswith('.tar'):
                    tar.extract(member)
                    print(f"Extracted: {member.name}")
    
    def sequencer(self, index):
        """Get sequencer for job index"""
        # This would need the actual sequencer logic
        # For now, return a simple format
        return f"seq_{index:06d}"
    
    def output_files(self, dataset_name, list_size=None):
        """Get list of output files for a dataset"""
        if list_size is None:
            list_size = self.njobs()
        
        if list_size == 0:
            raise ValueError("Cannot determine list size for unlimited job sets")
        
        # Generate output file names based on dataset and sequencer
        files = []
        for i in range(list_size):
            sequencer = self.sequencer(i)
            # This is a simplified version - actual logic would be more complex
            filename = f"{dataset_name}.{sequencer}.art"
            files.append(filename)
        
        return files


def usage():
    """Print usage information"""
    script_name = os.path.basename(__file__)
    return f"""
Usage:
    {script_name} [-h|--help] <query> cnf.tar

This script extracts and prints out information from the job parameter
file cnf.tar. The possible queries are:

    --jobname     The name of the job set.
    --njobs       The number of jobs in the set, zero means unlimited.
    --input-datasets    List of all datasets used by the job set.
    --output-datasets   List of all datasets created by the job set.
    --output-files <dsname>[:listsize]
        List of output files belonging to the given dataset.
    --codesize    The size of the compressed code tarball, in bytes.
    --extract-code    Extracts embedded code tarball to current directory.
    --setup       Prints the name of the setup file.
"""


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Extract information from Mu2e job parameter files')
    parser.add_argument('--jobname', action='store_true', help='Get job name')
    parser.add_argument('--njobs', action='store_true', help='Get number of jobs')
    parser.add_argument('--input-datasets', action='store_true', help='List input datasets')
    parser.add_argument('--output-datasets', action='store_true', help='List output datasets')
    parser.add_argument('--output-files', help='List output files for dataset (format: dataset[:size])')
    parser.add_argument('--codesize', action='store_true', help='Get code size')
    parser.add_argument('--extract-code', action='store_true', help='Extract embedded code')
    parser.add_argument('--setup', action='store_true', help='Get setup file path')
    parser.add_argument('parfile', help='Job parameter file (.tar)')
    
    args = parser.parse_args()
    
    # Check that exactly one query is specified
    queries = [args.jobname, args.njobs, args.input_datasets, args.output_datasets, 
               args.output_files is not None, args.codesize, args.extract_code, args.setup]
    
    if sum(queries) != 1:
        print("Error: Exactly one query must be specified")
        print(usage())
        sys.exit(1)
    
    # Check that parfile exists
    if not os.path.exists(args.parfile):
        print(f"Error: File not found: {args.parfile}")
        sys.exit(1)
    
    # Print Perl equivalent command
    perl_cmd = "mu2ejobquery"
    if args.jobname:
        perl_cmd += " --jobname"
    elif args.njobs:
        perl_cmd += " --njobs"
    elif args.input_datasets:
        perl_cmd += " --input-datasets"
    elif args.output_datasets:
        perl_cmd += " --output-datasets"
    elif args.output_files:
        perl_cmd += f" --output-files {args.output_files}"
    elif args.codesize:
        perl_cmd += " --codesize"
    elif args.extract_code:
        perl_cmd += " --extract-code"
    elif args.setup:
        perl_cmd += " --setup"
    perl_cmd += f" {args.parfile}"
    
    print(f"Running Perl equivalent of:")
    print(f"{perl_cmd}")
    
    try:
        jp = Mu2eJobPars(args.parfile)
        
        if args.jobname:
            print(jp.jobname())
        
        elif args.njobs:
            print(jp.njobs())
        
        elif args.input_datasets:
            for dataset in jp.input_datasets():
                print(dataset)
        
        elif args.output_datasets:
            for dataset in jp.output_datasets():
                print(dataset)
        
        elif args.output_files:
            # Parse dataset:size format
            if ':' in args.output_files:
                dataset_name, size_str = args.output_files.split(':', 1)
                try:
                    list_size = int(size_str)
                except ValueError:
                    print(f"Error: Invalid list size: {size_str}")
                    sys.exit(1)
            else:
                dataset_name = args.output_files
                list_size = None
            
            # Validate dataset exists
            if dataset_name not in jp.output_datasets():
                print(f"Error: Dataset {dataset_name} is not produced by the job set")
                sys.exit(1)
            
            # Get output files
            try:
                files = jp.output_files(dataset_name, list_size)
                for filename in files:
                    print(filename)
            except ValueError as e:
                print(f"Error: {e}")
                sys.exit(1)
        
        elif args.codesize:
            print(jp.codesize())
        
        elif args.extract_code:
            jp.extract_code()
        
        elif args.setup:
            print(jp.setup())
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
