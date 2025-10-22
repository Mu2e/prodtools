#!/usr/bin/env python3
"""Analyze POMS jobdesc JSON files."""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import glob
import argparse
from collections import defaultdict
from jobiodetail import Mu2eJobIO
from samweb_wrapper import locate_file, list_files, count_files


class PomsMonitor:
    """Analyze POMS production jobdesc files."""
    
    def __init__(self, pattern: str = "MDC202*"):
        self.pattern = pattern
        self.poms_dir = "/exp/mu2e/app/users/mu2epro/production_manager/poms_map"
        self.data = []
        
    def load_files(self):
        """Load all matching JSON files."""
        json_files = sorted(glob.glob(f"{self.poms_dir}/{self.pattern}.json"))
        print(f"Loading {len(json_files)} JSON files...")
        
        for json_file in json_files:
            with open(json_file, 'r') as f:
                entries = json.load(f)
                for entry in entries:
                    entry['source_file'] = json_file.split('/')[-1]
                    self.data.append(entry)
        
        print(f"Loaded {len(self.data)} job definitions\n")
    
    def summary(self):
        """Print summary statistics."""
        total_jobs = sum(entry.get('njobs', 0) for entry in self.data)
        campaigns = defaultdict(int)
        
        for entry in self.data:
            tarball = entry.get('tarball', '')
            if tarball:
                parts = tarball.split('.')
                if len(parts) >= 4:
                    campaign_full = parts[3]
                    # Extract base campaign: MDC2020ba, MDC2025ac, etc.
                    # Split on underscore to remove version suffixes like _best_v1_3
                    campaign = campaign_full.split('_')[0]
                    campaigns[campaign] += entry.get('njobs', 0)
        
        print("=" * 60)
        print("POMS JOBDESC SUMMARY")
        print("=" * 60)
        print(f"Total job definitions: {len(self.data)}")
        print(f"Total jobs planned:    {total_jobs:,}")
        print()
        print("Jobs by campaign:")
        for campaign in sorted(campaigns.keys()):
            print(f"  {campaign}: {campaigns[campaign]:,} jobs")
        print("=" * 60)
    
    def get_output_location(self, entry: dict) -> str:
        """Get output location from POMS jobdesc entry."""
        outputs = entry.get('outputs', [])
        if outputs and len(outputs) > 0:
            return outputs[0].get('location', 'N/A')
        return 'N/A'
    
    def format_avg_size(self, size_bytes: int, nfiles: int) -> str:
        """Format average size per file in MB."""
        if nfiles == 0:
            return "0.00"
        return f"{size_bytes/nfiles/1e6:.2f}"
    
    def get_output_datasets_with_counts(self, tarball: str) -> list:
        """Extract output dataset names with file counts."""
        location = locate_file(tarball)
        if not location:
            return []
        
        # locate_file returns a dict with location info
        if isinstance(location, dict):
            file_path = location.get('full_path', '')
            if ':' in file_path:
                file_path = file_path.split(':', 1)[1]
        elif isinstance(location, str):
            file_path = location.split(':', 1)[1] if ':' in location else location
        else:
            return []
        
        if not file_path:
            return []
        
        # SAM returns directory path, need to append filename
        full_path = os.path.join(file_path, tarball)
        if not os.path.exists(full_path):
            return []
        
        # Extract outputs from tarball using jobiodetail
        job_io = Mu2eJobIO(full_path)
        outputs = job_io.job_outputs(0)

        if not outputs:
            return []
        
        dataset_infos = []
        for output_file in outputs.values():
            # Skip /dev/null and other special outputs
            if output_file == '/dev/null' or not output_file.endswith('.art'):
                continue
            
            parts = output_file.split('.')
            if len(parts) != 6:
                continue
            
            dataset_name = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}.{parts[5]}"
            
            # Get file count, event count, and total size
            nfiles = count_files(f"dh.dataset {dataset_name}")
            result = list_files(f"dh.dataset={dataset_name}", summary=True)
            nevts = result.get('total_event_count', 0) if isinstance(result, dict) else 0
            nevts = nevts or 0
            total_size = result.get('total_file_size', 0) if isinstance(result, dict) else 0
            
            dataset_infos.append((dataset_name, nfiles, nevts, total_size))
        return dataset_infos
        
        return []
    
    def list_all(self, sort_by: str = "njobs", show_outputs: bool = False):
        """List all job definitions."""
        sorted_data = sorted(self.data, key=lambda x: x.get(sort_by, 0), reverse=True)
        
        if show_outputs:
            print(f"{'NJOBS':>8} {'EVENTS':>10} {'FILE SIZE [MB]':>14} {'LOC':<6} {'TARBALL / OUTPUT DATASETS':<80}")
            print(f"{'-----':>8} {'------':>10} {'--------------':>14} {'---':<6} {'-------------------------':<80}")
        else:
            print(f"{'NJOBS':>8} {'INLOC':<8} {'OUTLOC':<8} {'JSON FILE':<25} {'TARBALL':<60}")
            print(f"{'-----':>8} {'-----':<8} {'------':<8} {'---------':<25} {'-------':<60}")
        
        for entry in sorted_data:
            tarball = entry.get('tarball', 'N/A')
            njobs = entry.get('njobs', 0)
            
            if show_outputs:
                outloc = self.get_output_location(entry)
                print(f"{njobs:>8} {'':>10} {'':>14} {'':>6} {tarball}")
                outputs = self.get_output_datasets_with_counts(tarball)
                for dataset_name, nfiles, nevts, total_size in outputs:
                    avg_size_str = self.format_avg_size(total_size, nfiles)
                    if nfiles >= njobs:
                        print(f"{nfiles:>8} {nevts:>10.2e} {avg_size_str:>14} {outloc:<6} \033[92m{dataset_name} ✓\033[0m")
                    else:
                        print(f"{nfiles:>8} {nevts:>10.2e} {avg_size_str:>14} {outloc:<6} \033[91m{dataset_name}\033[0m")
                print("         " + "-" * 80)
            else:
                inloc = entry.get('inloc', 'N/A')
                outloc = entry.get('outputs', [{}])[0].get('location', 'N/A') if entry.get('outputs') else 'N/A'
                source_file = entry.get('source_file', 'N/A')
                print(f"{njobs:>8} {inloc:<8} {outloc:<8} {source_file:<25} {tarball:<60}")
    
    def filter_by_campaign(self, campaign: str, show_outputs: bool = False):
        """Filter and display by campaign."""
        filtered = [e for e in self.data if campaign in e.get('tarball', '')]
        total = sum(e.get('njobs', 0) for e in filtered)
        
        print(f"Campaign: {campaign}")
        print(f"Job definitions: {len(filtered)}")
        print(f"Total jobs: {total:,}")
        print()
        
        if show_outputs:
            print(f"{'NJOBS':>8} {'EVENTS':>10} {'FILE SIZE [MB]':>14} {'LOC':<6} {'TARBALL / OUTPUT DATASETS':<80}")
            print(f"{'-----':>8} {'------':>10} {'--------------':>14} {'---':<6} {'-------------------------':<80}")
        else:
            print(f"{'NJOBS':>8} {'TARBALL':<60}")
            print(f"{'-----':>8} {'-------':<60}")
        
        for entry in sorted(filtered, key=lambda x: x.get('njobs', 0), reverse=True):
            tarball = entry.get('tarball', 'N/A')
            njobs = entry.get('njobs', 0)
            
            if show_outputs:
                outloc = self.get_output_location(entry)
                print(f"{njobs:>8} {'':>10} {'':>14} {'':>6} {tarball}")
                outputs = self.get_output_datasets_with_counts(tarball)
                for dataset_name, nfiles, nevts, total_size in outputs:
                    avg_size_str = self.format_avg_size(total_size, nfiles)
                    if nfiles >= njobs:
                        print(f"{nfiles:>8} {nevts:>10.2e} {avg_size_str:>14} {outloc:<6} \033[92m{dataset_name} ✓\033[0m")
                    else:
                        print(f"{nfiles:>8} {nevts:>10.2e} {avg_size_str:>14} {outloc:<6} \033[91m{dataset_name}\033[0m")
                print("         " + "-" * 80)
            else:
                print(f"{njobs:>8} {tarball:<60}")

def main():
    parser = argparse.ArgumentParser(description="Analyze POMS jobdesc JSON files")
    parser.add_argument('--pattern', default='MDC202*', help='File pattern (default: MDC202*)')
    parser.add_argument('--summary', action='store_true', help='Show summary statistics')
    parser.add_argument('--list', action='store_true', help='List all job definitions')
    parser.add_argument('--campaign', help='Filter by campaign (e.g., MDC2025ac)')
    parser.add_argument('--outputs', action='store_true', help='Show output dataset names')
    parser.add_argument('--sort', default='njobs', help='Sort by field (default: njobs)')
    args = parser.parse_args()
    
    analyzer = PomsMonitor(pattern=args.pattern)
    analyzer.load_files()
    
    if args.campaign:
        analyzer.filter_by_campaign(args.campaign, show_outputs=args.outputs)
    elif args.summary:
        analyzer.summary()
    elif args.list:
        analyzer.list_all(sort_by=args.sort, show_outputs=args.outputs)
    else:
        analyzer.summary()

if __name__ == '__main__':
    main()

