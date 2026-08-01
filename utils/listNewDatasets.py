#!/usr/bin/env python3
"""List recently created datasets from SAM database."""

import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from samweb_wrapper import (list_files, dataset_summary, dataset_file_count,
                            q_recent_files)
from job_common import Mu2eName
from submissions import ledger_expected
from submission_ledger import DEFAULT_DB

_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"


class DatasetLister:
    """List and summarize recently created datasets from SAM."""
    
    def __init__(self, filetype: str = "art", days: int = 7,
                 user: str = "mu2epro", show_size: bool = False,
                 custom_query: Optional[str] = None,
                 completeness: bool = False,
                 ledger_db: Optional[str] = None,
                 color: str = "auto"):
        self.filetype = filetype
        self.days = days
        self.user = user
        self.show_size = show_size
        self.custom_query = custom_query
        self.ext = f".{filetype}"
        self.completeness = completeness
        self.ledger_db = ledger_db or DEFAULT_DB
        self.color = color       # 'auto' | 'always' | 'never', ls/grep convention
        self._expected = {}      # dataset -> expected njobs, built in run()

    def build_query(self) -> str:
        if self.custom_query:
            print(f"Using custom query: {self.custom_query}")
            return self.custom_query
        
        older_date = (datetime.now() - timedelta(days=self.days)).strftime("%Y-%m-%d")
        print(f"Checking for {self.filetype} files created after: {older_date} for user: {self.user}")
        
        return q_recent_files(self.filetype, self.user, older_date)
    
    def extract_dataset_name(self, filename: str) -> str:
        """Extract dataset name: drop the sequencer field from a file name.

        Lenient: returns filename unchanged if it isn't a parseable Mu2e name.
        """
        try:
            return str(Mu2eName.parse(filename).dataset)
        except ValueError:
            return filename
    
    def get_average_filesize(self, dataset: str) -> str:
        """Return average file size in MB, or 'N/A' if unavailable."""
        # A size column is cosmetic: a SAM hiccup degrades to 'N/A'
        # rather than killing the whole report.
        try:
            result = dataset_summary(dataset)
        except Exception:
            return "N/A"

        if isinstance(result, dict):
            file_count = result.get('file_count', 0)
            total_size = result.get('total_file_size', 0)
            
            if file_count and total_size:
                avg_mb = total_size // file_count // 1024 // 1024
                return str(avg_mb)
        
        return "N/A"
    
    def group_files_by_dataset(self, files: List[str]) -> Dict[str, int]:
        """Group files by dataset name and return counts."""
        dataset_counts = defaultdict(int)
        for filename in files:
            dataset = self.extract_dataset_name(filename)
            dataset_counts[dataset] += 1
        return dict(dataset_counts)

    def _total_files(self, dataset: str) -> int:
        """Total files in the dataset. NOT the windowed COUNT column: a
        campaign that started before the lookback window would otherwise be
        scored against a full-campaign denominator with a partial numerator."""
        try:
            return dataset_file_count(dataset)
        except Exception:
            return 0

    def _get_completeness(self, dataset: str) -> str:
        """<landed>/<expected> for a dataset produced by a direct campaign.

        '—' when no known campaign produced it. There is deliberately no
        per-dataset '?': the dataset name comes FROM the cnf tarball, so an
        unresolvable tarball leaves its dataset unidentifiable. Those failures
        are reported once on stderr by run() instead.

        Incomplete rows (landed < expected) are flagged, but how depends on
        self.color, the ls/grep-style --color flag:
        - 'auto' (default): red on a tty, dropping the ' INCOMPLETE' suffix
          (colour alone signals it); plain '<landed>/<expected> INCOMPLETE'
          with no escape codes otherwise, so piped/redirected consumers
          (grep, awk) aren't corrupted by codes they can't strip.
        - 'always': red with no suffix regardless of tty-ness — this is
          what makes `| grep` usable with colour, since grep's own
          --color=always only colours grep's match, it can't retroactively
          add colour we already suppressed.
        - 'never': plain text with the suffix regardless of tty-ness, for
          reproducible captures.
        Complete rows are never coloured or marked, in any mode."""
        expected = self._expected.get(dataset)
        if expected is None:
            return "—"
        landed = self._total_files(dataset)
        text = f"{landed}/{expected}"
        if landed >= expected:
            return text
        colourize = self.color == "always" or (
            self.color == "auto" and sys.stdout.isatty())
        if colourize:
            return f"{_ANSI_RED}{text}{_ANSI_RESET}"
        return f"{text} INCOMPLETE"

    def run(self):
        query = self.build_query()
        files = list_files(query)

        if not files:
            print("No files found matching query.")
            return

        dataset_counts = self.group_files_by_dataset(files)
        sorted_datasets = sorted(dataset_counts.items())

        if self.completeness:
            dsconfs = set()
            for ds, _ in sorted_datasets:
                try:
                    dsconfs.add(Mu2eName.parse(ds).dsconf)
                except ValueError:
                    continue
            try:
                self._expected, failures = ledger_expected(self.ledger_db,
                                                           dsconfs=dsconfs)
            except Exception as e:
                print(f"WARNING: could not read ledger {self.ledger_db} ({e}); "
                      "completeness column disabled.", file=sys.stderr)
                self.completeness = False
                failures = {}
            for tarball, reason in sorted(failures.items()):
                print(f"WARNING: no expected count for {tarball}: {reason}",
                      file=sys.stderr)

        # Print header
        print("------------------------------------------------")
        header = f"{'COUNT':>8} {'DATASET':<100}"
        divider = f"{'-----':>8} {'-------':<100}"
        if self.show_size:
            header += f" {'FILE SIZE':>10}"
            divider += f" {'--------':>10}"
        if self.completeness:
            header += f" {'COMPLETENESS':<22}"
            divider += f" {'------------':<22}"
        print(header)
        print(divider)

        # Print datasets
        for dataset, count in sorted_datasets:
            line = f"{count:>8} {dataset:<100}"
            if self.show_size:
                avg_size = self.get_average_filesize(dataset)
                size_str = f"{avg_size:>7} MB" if avg_size != "N/A" else f"{'N/A':>10}"
                line += f" {size_str}"
            if self.completeness:
                line += f" {self._get_completeness(dataset):<22}"
            print(line)

        print("------------------------------------------------")


def main():
    parser = argparse.ArgumentParser(description="List recently created datasets from SAM database")
    parser.add_argument('--filetype', default='art', help='File format (default: art)')
    parser.add_argument('--days', type=int, default=7, help='Days to look back (default: 7)')
    parser.add_argument('--user', default='mu2epro', help='Username filter (default: mu2epro)')
    parser.add_argument('--size', action='store_true', help='Show average file sizes')
    parser.add_argument('--query', help='Custom SAM query')
    parser.add_argument('--completeness', action='store_true',
                        help='Append a <landed>/<expected> column, with expected '
                             'read from the submission ledger; datasets from no '
                             'known campaign show an em dash')
    parser.add_argument('--ledger-db', default=DEFAULT_DB,
                        help=f'Submission ledger SQLite path (default: {DEFAULT_DB})')
    parser.add_argument('--color', choices=['auto', 'always', 'never'], default='auto',
                        help='Colour the COMPLETENESS column red for incomplete rows: '
                             'auto (default) colours only on a tty and drops the '
                             'INCOMPLETE suffix there; always colours regardless of '
                             'tty (for piping into grep --color); never disables '
                             'colour regardless of tty')
    args = parser.parse_args()

    lister = DatasetLister(filetype=args.filetype, days=args.days, user=args.user,
                           show_size=args.size, custom_query=args.query,
                           completeness=args.completeness,
                           ledger_db=args.ledger_db,
                           color=args.color)

    lister.run()


if __name__ == '__main__':
    main()

