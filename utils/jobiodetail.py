#!/usr/bin/env python3
"""
Python port of mu2ejobiodetail Perl script.
Provides information about input and output files of Mu2e jobs.
"""

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path
from typing import Dict, List, Optional, Union
import hashlib
import re

from utils.job_common import Mu2eName, Mu2eJobBase

class Mu2eJobIO(Mu2eJobBase):
    """Python port of mu2ejobiodetail functionality."""

    def sequencer(self, index: int) -> str:
        """Get sequencer for job index."""
        primary_inputs = self.job_primary_inputs(index)
        
        if primary_inputs:
            # Get sequencers from primary input files
            sequencers = []
            for dataset, files in primary_inputs.items():
                for filename in files:
                    sequencers.append(Mu2eName.parse(filename).sequencer)

            # Sort and return first
            sequencers.sort()
            return sequencers[0]
        
        # Check for event_id configuration
        tbs = self.json_data.get('tbs', {})
        event_id = tbs.get('event_id')
        
        if event_id:
            run = event_id.get('source.firstRun') or event_id.get('source.run')
            if not run:
                raise ValueError("Error: get_sequencer(): can not get source.firstRun from event_id")
            subrun = index
            return f"{run:06d}_{subrun:08d}"
        
        raise ValueError("Error: get_sequencer(): unsupported JSON content")
    
    def job_outputs(self, index: int) -> Dict[str, str]:
        """Get output files for job index."""
        tbs = self.json_data.get('tbs', {})
        outfiles = tbs.get('outfiles')
        
        if not outfiles:
            return {}
        
        result = {}
        seq = self.sequencer(index)
        
        for key, template in outfiles.items():
            # Skip special files like /dev/null
            if template.startswith('/dev/') or '/' in template:
                result[key] = template
                continue
                
            result[key] = str(Mu2eName.parse(template).with_sequencer(seq))
        
        return result
    
    def jobname(self) -> str:
        """Get job name."""
        jobname = self.json_data.get('jobname')
        if not jobname:
            raise ValueError(f"Error: no jobname in {self.jobdef}")
        return jobname

def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Python port of mu2ejobiodetail - provides information about input and output files of Mu2e jobs'
    )
    parser.add_argument('--jobdef', required=True, help='Job definition file (cnf.tar)')
    parser.add_argument('--index', type=int, help='Job index')
    parser.add_argument('--inputs', action='store_true', help='Print input file basenames')
    parser.add_argument('--outputs', action='store_true', help='Print output file basenames')
    parser.add_argument('--logfile', action='store_true', help='Print log file basename')

    
    args = parser.parse_args()
    
    # Check that exactly one mode is specified
    modes = [args.inputs, args.outputs, args.logfile]
    if sum(modes) != 1:
        print("Error: exactly one mode (--inputs, --outputs, or --logfile) must be specified")
        sys.exit(1)
    
    # Check that index is provided when needed
    if args.logfile and args.index is None:
        print("Error: --index is required for --logfile mode")
        sys.exit(1)
    
    try:
        job_io = Mu2eJobIO(args.jobdef)
        
        if args.inputs:
            inputs = job_io.job_inputs(args.index)
            # Flatten the dictionary values into a single list
            all_files = []
            for file_list in inputs.values():
                all_files.extend(file_list)
            all_files.sort()
            for filename in all_files:
                print(filename)
        
        elif args.outputs:
            outputs = job_io.job_outputs(args.index)
            # Sort output filenames
            output_files = sorted(outputs.values())
            for filename in output_files:
                print(filename)
        
        elif args.logfile:
            jobname = job_io.jobname()
            # jobname is the cnf tarball name; derive log.<owner>.<desc>.<dsconf>.<seq>.log
            seq = job_io.sequencer(args.index)
            log_name = Mu2eName.parse(jobname).as_tier('log').with_sequencer(seq).with_extension('log')
            print(str(log_name))
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
