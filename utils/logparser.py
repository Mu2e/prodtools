#!/usr/bin/env python3
"""
anaTimeReport - Analyze Mu2e log performance metrics
"""

import sys, argparse, re, json, os
from concurrent.futures import ThreadPoolExecutor, as_completed

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.datasetFileList import get_dataset_files

# Regex patterns
TIMEREPORT_REGEX = re.compile(r"TimeReport CPU = ([0-9]*\.?[0-9]+) Real = ([0-9]*\.?[0-9]+)")
MEMREPORT_REGEX = re.compile(r"MemReport\s+VmPeak\s*=\s*([0-9]*\.?[0-9]+)\s+VmHWM\s*=\s*([0-9]*\.?[0-9]+)")

def get_log_files(dataset, max_files=None):
    """Get log files for a SAM dataset (e.g., log.mu2e.X.Y.log).
    
    Args:
        dataset: SAM dataset name (must be a registered dataset with dh.dataset metadata)
        max_files: Maximum number of log files to return
    
    Returns:
        List of log file paths, or empty list if dataset not found
    """
    try:
        # Use get_dataset_files() which constructs paths directly without
        # locate_files() calls; max_files caps path construction at the
        # source (a log dataset holds one file per job — up to 100k names
        # for a 10-log sample otherwise).
        return get_dataset_files(dataset, max_files=max_files)

    except Exception as e:
        print(f"Warning: get_log_files failed for {dataset}: {e}", file=sys.stderr)
        return []

def parse_log_file(filepath):
    """Extract CPU, Real, VmPeak and VmHWM from a log file."""
    cpu = real = vmp = vmh = None

    with open(filepath, 'r', errors='ignore') as f:
        for line in f:
            if m := TIMEREPORT_REGEX.search(line):
                cpu, real = float(m.group(1)) / 3600, float(m.group(2)) / 3600
            if m := MEMREPORT_REGEX.search(line):
                vmp, vmh = float(m.group(1)) / 1024, float(m.group(2)) / 1024
            if all(x is not None for x in [cpu, real, vmp, vmh]):
                break


    return {'CPU [h]': round(cpu, 2) if cpu else None, 
            'Real [h]': round(real, 2) if real else None,
            'VmPeak [GB]': round(vmp, 2) if vmp else None, 
            'VmHWM [GB]': round(vmh, 2) if vmh else None}

def process_dataset(dataset, max_logs, max_workers=10):
    """Process one dataset and return metrics.
    
    Args:
        dataset: Dataset name to process
        max_logs: Maximum number of log files to process
        max_workers: Number of threads for parallel log file parsing (default: 10)
    """
    print(f"Processing {dataset}", file=sys.stderr)
    
    log_files = get_log_files(dataset, max_logs)
    if not log_files:
        return {'dataset': dataset, 'CPU [h]': None, 'CPU_max [h]': None,
                'Real [h]': None, 'Real_max [h]': None, 'VmPeak [GB]': None,
                'VmPeak_max [GB]': None, 'VmHWM [GB]': None, 'VmHWM_max [GB]': None}
    
    # Parse all log files in parallel using thread pool
    file_metrics = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all parsing tasks
        future_to_file = {executor.submit(parse_log_file, log_file): log_file 
                         for log_file in log_files}
        
        # Collect results as they complete
        for future in as_completed(future_to_file):
            try:
                result = future.result()
                file_metrics.append(result)
            except Exception as e:
                log_file = future_to_file[future]
                print(f"Warning: Error parsing {log_file}: {e}", file=sys.stderr)
                # Add empty result to maintain order
                file_metrics.append({
                    'file': os.path.basename(log_file),
                    'full_path': log_file,
                    'date': 'N/A',
                    'CPU [h]': None,
                    'Real [h]': None,
                    'VmPeak [GB]': None,
                    'VmHWM [GB]': None
                })
    
    # Collect metrics for statistics
    metrics = {'CPU': [], 'Real': [], 'VmPeak': [], 'VmHWM': []}
    for fm in file_metrics:
        if fm['CPU [h]'] is not None: metrics['CPU'].append(fm['CPU [h]'])
        if fm['Real [h]'] is not None: metrics['Real'].append(fm['Real [h]'])
        if fm['VmPeak [GB]'] is not None: metrics['VmPeak'].append(fm['VmPeak [GB]'])
        if fm['VmHWM [GB]'] is not None: metrics['VmHWM'].append(fm['VmHWM [GB]'])
    
    # Calculate statistics
    def mean(lst): return round(sum(lst)/len(lst), 2) if lst else None
    def max_val(lst): return round(max(lst), 2) if lst else None
    
    return {
        'dataset': dataset,
        'CPU [h]': mean(metrics['CPU']), 'CPU_max [h]': max_val(metrics['CPU']),
        'Real [h]': mean(metrics['Real']), 'Real_max [h]': max_val(metrics['Real']),
        'VmPeak [GB]': mean(metrics['VmPeak']), 'VmPeak_max [GB]': max_val(metrics['VmPeak']),
        'VmHWM [GB]': mean(metrics['VmHWM']), 'VmHWM_max [GB]': max_val(metrics['VmHWM'])
    }

def main():
    parser = argparse.ArgumentParser(description="Analyze Mu2e log performance")
    parser.add_argument('datasets', nargs='+', help='Dataset names to analyze')
    parser.add_argument('-n', '--max-logs', type=int, default=None, help='Max logs per dataset (default: all)')
    args = parser.parse_args()

    # Process all datasets
    results = [process_dataset(dataset, args.max_logs) for dataset in args.datasets]
    
    # Output results
    for result in results:
        print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()