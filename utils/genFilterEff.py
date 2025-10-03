#!/usr/bin/env python3
"""
genFilterEff - Compute overall filter efficiency for Mu2e datasets

Python implementation of mu2eGenFilterEff
Calculates the ratio of passed events to generated events for simulation datasets.

Author: Converted from Perl version by A.Gaponenko, 2016
"""

import sys
import argparse
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from samweb_wrapper import get_samweb_wrapper


class DatasetEffSummary:
    """Summary of efficiency statistics for a dataset."""
    
    def __init__(self, dsname):
        self.dsname = dsname
        self.nfiles = 0
        self.genevents = 0
        self.passedevents = 0
    
    def fill(self, metadata):
        """Add file metadata to the summary.
        
        Args:
            metadata: Dictionary with file metadata from SAM
        """
        self.nfiles += 1
        
        if 'dh.gencount' not in metadata:
            raise ValueError(f"Error: no dh.gencount in metadata for file {metadata.get('file_name', 'unknown')}")
        
        self.genevents += metadata['dh.gencount']
        
        # SAM bug workaround: event_count can be missing for zero
        self.passedevents += metadata.get('event_count', 0)
    
    def efficiency(self):
        """Calculate efficiency ratio."""
        if self.genevents == 0:
            return 0.0
        return self.passedevents / self.genevents


def process_dataset(dsname, samweb, chunk_size=100, max_files=None, verbosity=2):
    """Process a dataset and compute its efficiency.
    
    Args:
        dsname: Dataset name
        samweb: SAMWeb wrapper instance
        chunk_size: Number of metadata to request per SAM transaction
        max_files: Maximum number of files to process (None for all)
        verbosity: Verbosity level (0=quiet, 1=minimal, 2=verbose)
    
    Returns:
        DatasetEffSummary object with results
    """
    summary = DatasetEffSummary(dsname)
    
    # Get list of files in dataset
    file_list = samweb.list_files(f"dh.dataset={dsname} with availability anylocation")
    
    num_files_total = len(file_list)
    
    if num_files_total == 0:
        raise ValueError(f"Error: there are no records matching dataset name {dsname}")
    
    num_files_to_use = max_files if max_files is not None else num_files_total
    num_files_to_use = min(num_files_to_use, num_files_total)
    
    if verbosity > 0:
        print(f"Processing dataset  {dsname}, using {num_files_to_use} out of {num_files_total} files")
    
    # Process files in chunks
    for num_processed in range(0, num_files_to_use, chunk_size):
        end_idx = min(num_processed + chunk_size, num_files_to_use)
        chunk = file_list[num_processed:end_idx]
        
        # Get metadata for chunk
        for filename in chunk:
            try:
                metadata = samweb.get_metadata(filename)
                summary.fill(metadata)
            except Exception as e:
                print(f"Warning: Error processing file {filename}: {e}", file=sys.stderr)
                continue
        
        if verbosity > 1:
            eff = summary.efficiency()
            print(f"\teff = {eff:.4f} ({summary.passedevents} / {summary.genevents}) "
                  f"after processing {summary.nfiles} files of {summary.dsname}")
    
    return summary


def write_output(summaries, outfile, header='TABLE SimEfficiencies2', use_full_name=False):
    """Write efficiency results to output file in Proditions format.
    
    Args:
        summaries: List of DatasetEffSummary objects
        outfile: Output file path
        header: First line of output file
        use_full_name: If True, use full dataset name; otherwise use description field
    """
    # Check if file exists
    if os.path.exists(outfile):
        raise FileExistsError(f"Error creating {outfile}: File exists")
    
    with open(outfile, 'w') as f:
        f.write(header + '\n')
        
        for summary in summaries:
            # Extract dataset description (process field from dataset name)
            # Format: type.mu2e.process.version.art
            parts = summary.dsname.split('.')
            if use_full_name or len(parts) < 3:
                dstag = summary.dsname
            else:
                # Use process field (index 2)
                dstag = parts[2] if len(parts) > 2 else summary.dsname
            
            eff = summary.efficiency()
            
            # Proditions table format:
            # Row(std::string tag, unsigned long numerator, unsigned long denominator, double eff)
            f.write(f"{dstag},\t{summary.passedevents},\t{summary.genevents},\t{eff}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Compute and print out the overall filter efficiency for a dataset, '
                    'which is the ratio of the number of events in the dataset to '
                    'the total number of events generated in the initial stage of the '
                    'simulation, in the jobs that ran with the EmptyEvent source.',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('datasets', nargs='+', metavar='DatasetName',
                        help='Dataset name(s) to process')
    
    parser.add_argument('--out', '--outfile', dest='outfile', required=True,
                        help='Output file for Proditions-formatted results')
    
    parser.add_argument('--firstLine', default='TABLE SimEfficiencies2',
                        help='Text for the first line of the file (default: TABLE SimEfficiencies2)')
    
    parser.add_argument('--writeFullDatasetName', action='store_true',
                        help='Write full dataset names instead of description field')
    
    parser.add_argument('--chunksize', '--chunkSize', type=int, default=100, dest='chunksize',
                        help='Number of metadata to request per SAMWEB transaction (default: 100)')
    
    parser.add_argument('--maxFilesToProcess', type=int, default=None,
                        help='Maximum number of files to process per dataset')
    
    parser.add_argument('--verbosity', type=int, default=2,
                        help='Verbosity level: 0=quiet, 1=minimal, 2=verbose (default: 2)')
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.maxFilesToProcess is not None and args.maxFilesToProcess <= 0:
        parser.error(f"ERROR: Illegal maxFilesToProcess = {args.maxFilesToProcess}")
    
    # Initialize SAMWeb wrapper
    samweb = get_samweb_wrapper()
    
    # Process all datasets
    summaries = []
    for dataset in args.datasets:
        try:
            summary = process_dataset(
                dataset,
                samweb,
                chunk_size=args.chunksize,
                max_files=args.maxFilesToProcess,
                verbosity=args.verbosity
            )
            summaries.append(summary)
        except Exception as e:
            print(f"Error processing dataset {dataset}: {e}", file=sys.stderr)
            sys.exit(1)
    
    # Write output
    try:
        write_output(summaries, args.outfile, args.firstLine, args.writeFullDatasetName)
    except Exception as e:
        print(f"Error writing output: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

