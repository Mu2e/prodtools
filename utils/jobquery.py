#!/usr/bin/env python3
"""
Python equivalent of mu2ejobquery Perl script.
Extracts information from Mu2e job parameter files (.tar files containing jobpars.json).
"""

import argparse
import os
import sys

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import Mu2eJobBase, Mu2eName

class Mu2eJobPars(Mu2eJobBase):
    """Python equivalent of Mu2eJobPars.pm"""
    
    def __init__(self, parfile):
        """Initialize with a job parameter file (.tar)"""
        super().__init__(parfile)

    def jobname(self):
        """Get the job name"""
        return self.json_data.get('jobname', '')
    
    def input_datasets(self):
        """Get list of input datasets"""
        # Check for explicit input_datasets field first
        if 'input_datasets' in self.json_data:
            return self.json_data['input_datasets']
        
        # Extract from TBS inputs and auxin sections
        tbs = self.json_data.get('tbs', {})
        datasets = set()

        def extract_dataset_from_files(file_list):
            """Extract dataset name from first file in list (always .art ext)."""
            if not file_list:
                return None
            try:
                n = Mu2eName.parse(file_list[0])
            except ValueError:
                return None
            return str(n.with_extension('art').dataset)

        for section in ('inputs', 'auxin'):
            for value in tbs.get(section, {}).values():
                if isinstance(value, list) and len(value) >= 2:
                    _, file_list = value
                    dataset = extract_dataset_from_files(file_list)
                    if dataset:
                        datasets.add(dataset)

        return list(datasets)
    
    def input_files(self):
        """Get list of all input files across inputs, samplinginput, and auxin"""
        tbs = self.json_data.get('tbs', {})
        files = []
        for section in ('inputs', 'samplinginput', 'auxin'):
            for key, value in tbs.get(section, {}).items():
                if isinstance(value, list) and len(value) >= 2:
                    file_list = value[1]
                    if isinstance(file_list, list):
                        files.extend(file_list)
        return files
    
    def output_datasets(self):
        """Get list of output datasets"""
        return self.json_data.get('output_datasets', [])
    
    def codesize(self):
        """Bytes of code embedded in this cnf: always 0.

        prodtools ships an Offline build as a jobsub sidecar
        (--tar_file_name), never inside the cnf, so nothing is embedded
        and 0 is the honest answer rather than a placeholder. The build
        a code-mode cnf was made against is recorded in `code_ref`;
        `--recipe` prints it.
        """
        return 0

    def recipe(self):
        """Reconstruct this cnf's build config as human-readable text.

        A cnf in SAM is sometimes the only surviving record of how it was
        built — the MDC2025ar generic reco/evnt entries were never committed
        to data/, so the tarball was the source of truth. The embedded
        mu2e.fcl IS the json2jobdef entry's `fcl` + `fcl_overrides`, and
        jobpars.json carries the setup script and output name patterns.
        Neither the other queries nor fcldump exposed the override block.
        """
        lines = [f"# recipe: {self.jobname()}",
                 f"setup: {self.setup()}"]

        code_ref = self.json_data.get('code_ref')
        if code_ref:
            lines.append(f"code: {code_ref.get('source_path')}")
            lines.append(f"code sha256: {code_ref.get('sha256')}"
                         f"    # ships via jobsub --tar_file_name, "
                         f"not embedded")
        try:
            lines.append(f"njobs: {self.njobs()}    # 0 = generic / open-ended")
        except ValueError as e:
            # njobs is informational here; report the breakage rather than
            # letting it suppress the rest of the recipe.
            lines.append(f"njobs: <unavailable: {e}>")

        outfiles = self.json_data.get('tbs', {}).get('outfiles', {})
        if outfiles:
            lines.append("outfiles:")
            for key, pattern in outfiles.items():
                lines.append(f"    {key} = {pattern}")

        lines.append("")
        lines.append("# embedded mu2e.fcl  (= json2jobdef `fcl` + `fcl_overrides`)")
        try:
            fcl = self._extract_member('mu2e.fcl').decode()
        except ValueError:
            lines.append("# (no embedded mu2e.fcl — code-tarball job definition)")
        else:
            lines.append(fcl.rstrip('\n'))
        return '\n'.join(lines)

    def output_files(self, dataset_name, list_size=None):
        """List output files belonging to the given dataset, computed
        through the canonical job_outputs()/sequencer() arithmetic."""
        if list_size is None:
            list_size = self.njobs()

        if list_size == 0:
            raise ValueError("Cannot determine list size for unlimited job sets")

        target = str(Mu2eName.parse(dataset_name).dataset)
        files = []
        for i in range(list_size):
            for filename in self.job_outputs(i).values():
                try:
                    name = Mu2eName.parse(filename)
                except ValueError:
                    continue
                if name.is_file and str(name.dataset) == target:
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
    --input-files       List of all input files used by the job set.
    --output-datasets   List of all datasets created by the job set.
    --output-files <dsname>[:listsize]
        List of output files belonging to the given dataset.
    --codesize    The size of the compressed code tarball, in bytes.
        Always 0 — code ships as a jobsub sidecar, never embedded in
        the cnf. --recipe prints the code_ref for a code-mode cnf.
    --setup       Prints the name of the setup file.
    --recipe      The build config: setup, njobs, output patterns, and the
        embedded mu2e.fcl (the json2jobdef `fcl` + `fcl_overrides`).
"""


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Extract information from Mu2e job parameter files')
    parser.add_argument('--jobname', action='store_true', help='Get job name')
    parser.add_argument('--njobs', action='store_true', help='Get number of jobs')
    parser.add_argument('--input-datasets', action='store_true', help='List input datasets')
    parser.add_argument('--input-files', action='store_true', help='List all input files')
    parser.add_argument('--output-datasets', action='store_true', help='List output datasets')
    parser.add_argument('--output-files', help='List output files for dataset (format: dataset[:size])')
    parser.add_argument('--codesize', action='store_true', help='Get code size')
    parser.add_argument('--setup', action='store_true', help='Get setup file path')
    parser.add_argument('--recipe', action='store_true',
                        help='Build config: setup, njobs, output patterns, and the '
                             'embedded mu2e.fcl (json2jobdef fcl + fcl_overrides)')
    parser.add_argument('parfile', help='Job parameter file (.tar)')

    args = parser.parse_args()

    # Check that exactly one query is specified
    queries = [args.jobname, args.njobs, args.input_datasets, args.input_files,
               args.output_datasets, args.output_files is not None,
               args.codesize, args.setup, args.recipe]
    
    if sum(queries) != 1:
        print("Error: Exactly one query must be specified")
        print(usage())
        sys.exit(1)
    
    # Check that parfile exists
    if not os.path.exists(args.parfile):
        print(f"Error: File not found: {args.parfile}")
        sys.exit(1)
    
    try:
        jp = Mu2eJobPars(args.parfile)
        
        if args.jobname:
            print(jp.jobname())
        
        elif args.njobs:
            print(jp.njobs())
        
        elif args.input_datasets:
            for dataset in jp.input_datasets():
                print(dataset)
        
        elif args.input_files:
            for f in jp.input_files():
                print(f)
        
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

        elif args.setup:
            print(jp.setup())

        elif args.recipe:
            print(jp.recipe())
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
