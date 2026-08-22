#!/usr/bin/env python3
"""
genFilterEff - Compute overall filter efficiency for Mu2e datasets

Python port of mu2eGenFilterEff: ratio of passed events to generated events
for simulation datasets. Converted from the Perl version by A.Gaponenko, 2016.
"""

import sys
import argparse
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Package-first import with bare fallback: keeps one module identity when
# loaded as utils.genFilterEff, while still supporting bin/ stubs that put
# utils/ itself on the path.
try:
    from utils.samweb_wrapper import get_samweb_wrapper
    from utils.job_common import Mu2eName
except ImportError:
    from samweb_wrapper import get_samweb_wrapper
    from job_common import Mu2eName


class DatasetEffSummary:
    """Summary of efficiency statistics for a dataset."""
    
    def __init__(self, dsname):
        self.dsname = dsname
        self.nfiles = 0
        self.genevents = 0
        self.passedevents = 0
    
    def fill(self, metadata):
        """Add one file's SAM metadata dict to the summary. A file with no
        dh.gencount raises before it is counted, so nfiles stays the
        denominator of files actually summed."""
        if 'dh.gencount' not in metadata:
            raise ValueError(f"Error: no dh.gencount in metadata for file {metadata.get('file_name', 'unknown')}")
        self.nfiles += 1

        self.genevents += metadata['dh.gencount']

        # SAM bug workaround: event_count can be missing for zero
        self.passedevents += metadata.get('event_count', 0)
    
    def efficiency(self):
        """Calculate efficiency ratio."""
        if self.genevents == 0:
            return 0.0
        return self.passedevents / self.genevents


def process_dataset(dsname, samweb, chunk_size=100, max_files=None, verbosity=2):
    """Process a dataset and return a DatasetEffSummary of its efficiency.

    chunk_size: files per SAM metadata transaction. max_files: cap (None=all).
    verbosity: 0=quiet, 1=minimal, 2=verbose.
    """
    summary = DatasetEffSummary(dsname)
    file_list = samweb.files_in_dataset(dsname, availability='anylocation')
    
    num_files_total = len(file_list)
    
    if num_files_total == 0:
        raise ValueError(f"Error: there are no records matching dataset name {dsname}")
    
    num_files_to_use = max_files if max_files is not None else num_files_total
    num_files_to_use = min(num_files_to_use, num_files_total)
    
    if verbosity > 0:
        print(f"Processing dataset  {dsname}, using {num_files_to_use} out of {num_files_total} files")
    
    # Process files in chunks — one SAM round-trip per chunk
    for num_processed in range(0, num_files_to_use, chunk_size):
        end_idx = min(num_processed + chunk_size, num_files_to_use)
        chunk = file_list[num_processed:end_idx]

        try:
            chunk_metadata = samweb.metadata_for_files(chunk)
        except Exception as e:
            # Batch failed (SAM hiccup): fall back to per-file fetches so
            # one bad chunk doesn't lose the whole sample
            print(f"Warning: batch metadata failed ({e}); retrying per file", file=sys.stderr)
            chunk_metadata = [samweb.get_metadata(f) for f in chunk]

        for metadata in chunk_metadata:
            try:
                summary.fill(metadata)
            except ValueError as e:
                print(f"Warning: Error processing file "
                      f"{metadata.get('file_name', 'unknown')}: {e}", file=sys.stderr)
                continue

        if verbosity > 1:
            eff = summary.efficiency()
            print(f"\teff = {eff:.4f} ({summary.passedevents} / {summary.genevents}) "
                  f"after processing {summary.nfiles} files of {summary.dsname}")
    
    return summary


def write_output(summaries, outfile, header='TABLE SimEfficiencies2', use_full_name=False):
    """Write DatasetEffSummary results to outfile in Proditions format.

    header is the first line; use_full_name writes the full dataset name
    instead of just its description field.
    """
    if os.path.exists(outfile):
        raise FileExistsError(f"Error creating {outfile}: File exists")
    
    with open(outfile, 'w') as f:
        f.write(header + '\n')
        
        for summary in summaries:
            # description field of tier.owner.description.dsconf.ext
            if use_full_name:
                dstag = summary.dsname
            else:
                try:
                    dstag = Mu2eName.parse(summary.dsname).description
                except ValueError:
                    dstag = summary.dsname
            
            eff = summary.efficiency()

            # Proditions Row(tag, numerator, denominator, eff)
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

    if args.maxFilesToProcess is not None and args.maxFilesToProcess <= 0:
        parser.error(f"ERROR: Illegal maxFilesToProcess = {args.maxFilesToProcess}")

    samweb = get_samweb_wrapper()

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
        except ValueError as e:
            print(f"Error processing dataset {dataset}: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        write_output(summaries, args.outfile, args.firstLine, args.writeFullDatasetName)
    except (FileExistsError, OSError) as e:
        print(f"Error writing output: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

