#!/usr/bin/env python3
"""List recently created datasets from SAM database."""

import os
import sys
import glob
import time
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from samweb_wrapper import list_files


DEFAULT_POMS_DIR = "/exp/mu2e/app/users/mu2epro/production_manager/poms_map"


def _default_db_path() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "poms_data.db")


def _db_is_stale(db_path: str, poms_dir: str, lookback_days: int) -> Tuple[bool, str]:
    """Return (stale, reason). DB is stale if any POMS map within the
    lookback window has been modified since the DB file's mtime."""
    if not os.path.exists(db_path):
        return True, "DB does not exist"
    db_mtime = os.path.getmtime(db_path)
    cutoff = time.time() - lookback_days * 86400
    newer = []
    for f in glob.glob(os.path.join(poms_dir, "MDC202*.json")):
        m = os.path.getmtime(f)
        if m > db_mtime and m > cutoff:
            newer.append(os.path.basename(f))
    if newer:
        sample = ", ".join(newer[:3]) + (f", +{len(newer) - 3} more" if len(newer) > 3 else "")
        return True, f"{len(newer)} map(s) newer than DB ({sample})"
    return False, ""


def _ensure_db_fresh(db_path: str, poms_dir: str, days: int, no_rebuild: bool) -> None:
    """If the DB is stale, do an incremental rebuild covering the lookback
    window. No-op when fresh. With no_rebuild, prints a warning instead."""
    stale, reason = _db_is_stale(db_path, poms_dir, days)
    if not stale:
        return
    if no_rebuild:
        print(f"WARNING: DB stale ({reason}); --no-rebuild specified, completeness may be inaccurate.")
        return
    print(f"DB stale: {reason}; running incremental rebuild...")
    from db_builder import build_db
    cutoff_dt = datetime.now() - timedelta(days=days)
    t0 = time.time()
    build_db("MDC202*", db_path, poms_dir=poms_dir, since=cutoff_dt)
    print(f"Rebuild took {time.time() - t0:.1f}s.")


class DatasetLister:
    """List and summarize recently created datasets from SAM."""
    
    def __init__(self, filetype: str = "art", days: int = 7,
                 user: str = "mu2epro", show_size: bool = False,
                 custom_query: Optional[str] = None,
                 completeness: bool = False, no_rebuild: bool = False,
                 db_path: Optional[str] = None,
                 poms_dir: str = DEFAULT_POMS_DIR):
        self.filetype = filetype
        self.days = days
        self.user = user
        self.show_size = show_size
        self.custom_query = custom_query
        self.ext = f".{filetype}"
        self.completeness = completeness
        self.no_rebuild = no_rebuild
        self.db_path = db_path or _default_db_path()
        self.poms_dir = poms_dir
        self._db_session = None  # opened lazily in run() if completeness enabled
        
    def build_query(self) -> str:
        if self.custom_query:
            print(f"Using custom query: {self.custom_query}")
            return self.custom_query
        
        older_date = (datetime.now() - timedelta(days=self.days)).strftime("%Y-%m-%d")
        print(f"Checking for {self.filetype} files created after: {older_date} for user: {self.user}")
        
        query = f"Create_Date > {older_date} and file_format {self.filetype} and user {self.user}"
        return query
    
    def extract_dataset_name(self, filename: str) -> str:
        """Extract dataset name: first 4 dot-separated fields + extension from actual filename."""
        parts = filename.split('.')
        if len(parts) >= 4:
            # Use the actual extension from the filename, not self.ext
            # This handles cases where custom queries search for different file types (e.g., .fcl)
            actual_ext = '.' + parts[-1] if len(parts) > 1 else ''
            return f"{'.'.join(parts[:4])}{actual_ext}"
        return filename
    
    def get_average_filesize(self, dataset: str) -> str:
        """Return average file size in MB, or 'N/A' if unavailable."""
        query = f"dh.dataset {dataset}"
        result = list_files(query, summary=True)
        
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

    def _get_completeness(self, dataset: str) -> str:
        """Look up <actual>/<expected> for a dataset in the POMS DB.
        Returns a short formatted string suitable for the table column."""
        if self._db_session is None:
            return "-"
        try:
            from poms_db import Job, JobOutput, DatasetInfo
        except ImportError:
            return "-"
        out = self._db_session.query(JobOutput).filter_by(dataset=dataset).first()
        if not out:
            return "—"  # not produced via POMS
        job = self._db_session.query(Job).filter_by(id=out.job_id).first()
        info = self._db_session.query(DatasetInfo).filter_by(dataset_name=dataset).first()
        if not job or job.njobs == 0 or info is None or info.nfiles is None:
            return "?"
        marker = "" if info.nfiles >= job.njobs else " INCOMPLETE"
        return f"{info.nfiles}/{job.njobs}{marker}"

    def run(self):
        # Refresh the POMS DB before SAM queries if completeness is requested.
        # Cheap when the DB is fresh; only does work when a map changed.
        if self.completeness:
            try:
                import sqlalchemy  # noqa: F401
            except ImportError:
                print("WARNING: SQLAlchemy not found (run 'pyenv ana' after "
                      "'muse setup ops'); completeness column disabled.")
                self.completeness = False
        if self.completeness:
            _ensure_db_fresh(self.db_path, self.poms_dir, self.days, self.no_rebuild)
            try:
                from poms_db import get_db_session
                self._db_session = get_db_session(self.db_path)
            except Exception as e:
                print(f"WARNING: could not open POMS DB ({e}); completeness column disabled.")
                self.completeness = False

        query = self.build_query()
        files = list_files(query)

        if not files:
            print("No files found matching query.")
            return

        dataset_counts = self.group_files_by_dataset(files)
        sorted_datasets = sorted(dataset_counts.items())

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
                        help='Append nfiles/expected column from POMS DB; auto-rebuilds DB if stale')
    parser.add_argument('--no-rebuild', action='store_true',
                        help='With --completeness, skip auto-rebuild even if DB is stale (warn only)')
    parser.add_argument('--db', default=None, help='POMS SQLite DB path (default: <repo>/poms_data.db)')
    parser.add_argument('--poms-dir', default=DEFAULT_POMS_DIR,
                        help=f'POMS map directory (default: {DEFAULT_POMS_DIR})')
    args = parser.parse_args()

    lister = DatasetLister(filetype=args.filetype, days=args.days, user=args.user,
                           show_size=args.size, custom_query=args.query,
                           completeness=args.completeness, no_rebuild=args.no_rebuild,
                           db_path=args.db, poms_dir=args.poms_dir)

    lister.run()


if __name__ == '__main__':
    main()

