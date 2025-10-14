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

from samweb_wrapper import list_files


class DatasetLister:
    """List and summarize recently created datasets from SAM."""
    
    def __init__(self, filetype: str = "art", days: int = 7, 
                 user: str = "mu2epro", show_size: bool = False,
                 custom_query: Optional[str] = None):
        self.filetype = filetype
        self.days = days
        self.user = user
        self.show_size = show_size
        self.custom_query = custom_query
        self.ext = f".{filetype}"
        
    def build_query(self) -> str:
        if self.custom_query:
            print(f"Using custom query: {self.custom_query}")
            return self.custom_query
        
        older_date = (datetime.now() - timedelta(days=self.days)).strftime("%Y-%m-%d")
        print(f"Checking for {self.filetype} files created after: {older_date} for user: {self.user}")
        
        query = f"Create_Date > {older_date} and file_format {self.filetype} and user {self.user}"
        return query
    
    def extract_dataset_name(self, filename: str) -> str:
        """Extract dataset name: first 4 dot-separated fields + extension."""
        parts = filename.split('.')
        if len(parts) >= 4:
            return f"{'.'.join(parts[:4])}{self.ext}"
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
    
    def run(self):
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
        print(header)
        print(divider)
        
        # Print datasets
        for dataset, count in sorted_datasets:
            line = f"{count:>8} {dataset:<100}"
            if self.show_size:
                avg_size = self.get_average_filesize(dataset)
                size_str = f"{avg_size:>7} MB" if avg_size != "N/A" else f"{'N/A':>10}"
                line += f" {size_str}"
            print(line)
        
        print("------------------------------------------------")


def main():
    parser = argparse.ArgumentParser(description="List recently created datasets from SAM database")
    parser.add_argument('--filetype', default='art', help='File format (default: art)')
    parser.add_argument('--days', type=int, default=7, help='Days to look back (default: 7)')
    parser.add_argument('--user', default='mu2epro', help='Username filter (default: mu2epro)')
    parser.add_argument('--size', action='store_true', help='Show average file sizes')
    parser.add_argument('--query', help='Custom SAM query')
    args = parser.parse_args()
    
    lister = DatasetLister(filetype=args.filetype, days=args.days, user=args.user,
                          show_size=args.size, custom_query=args.query)
    
    lister.run()


if __name__ == '__main__':
    main()

