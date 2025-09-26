#!/usr/bin/env python3
"""
anaTimeReport - Analyze Mu2e log performance metrics
"""

import sys, subprocess, argparse, re, json, os
from pathlib import Path

# Regex patterns
TIMEREPORT_REGEX = re.compile(r"TimeReport CPU = ([0-9]*\.?[0-9]+) Real = ([0-9]*\.?[0-9]+)")
MEMREPORT_REGEX = re.compile(r"MemReport\s+VmPeak\s*=\s*([0-9]*\.?[0-9]+)\s+VmHWM\s*=\s*([0-9]*\.?[0-9]+)")

def get_log_files(dataset, max_files=1):
    """Get log files for a dataset or SAM definition."""
    script_path = os.path.join(os.path.dirname(__file__), 'datasetFileList.py')
    
    # First try as SAM definition
    proc = subprocess.run(["python3", script_path, "--defname", dataset], 
                         capture_output=True, text=True)
    
    if proc.returncode == 0:
        # Successfully got files from SAM definition
        files = [line.strip() for line in proc.stdout.splitlines() if line.startswith('/')]
        # Filter for log files only
        log_files = [f for f in files if f.endswith('.log')]
        return log_files[:max_files]
    
    # If not a SAM definition, try as dataset name
    # Convert to log dataset (sim.mu2e.X.art -> log.mu2e.X.log)
    parts = dataset.split('.')
    parts[0] = 'log'
    parts[-1] = 'log'
    log_dataset = '.'.join(parts)
    
    # Call datasetFileList for regular dataset
    proc = subprocess.run(["python3", script_path, log_dataset], capture_output=True, text=True)
    
    if proc.returncode != 0:
        return []
    
    files = [line.strip() for line in proc.stdout.splitlines() if line.startswith('/')]
    return files[:max_files]

def parse_log_file(filepath):
    """Extract CPU, Real, VmPeak, VmHWM from log file."""
    cpu = real = vmp = vmh = None
    
    with open(filepath, 'r', errors='ignore') as f:
        for line in f:
            if m := TIMEREPORT_REGEX.search(line):
                cpu, real = float(m.group(1)) / 3600, float(m.group(2)) / 3600
            if m := MEMREPORT_REGEX.search(line):
                vmp, vmh = float(m.group(1)) / 1024, float(m.group(2)) / 1024
            if all(x is not None for x in [cpu, real, vmp, vmh]):
                break
    
    return cpu, real, vmp, vmh

def process_dataset(dataset, max_logs):
    """Process one dataset and return metrics."""
    print(f"Processing {dataset}", file=sys.stderr)
    
    log_files = get_log_files(dataset, max_logs)
    if not log_files:
        return {'dataset': dataset, 'CPU [h]': None, 'CPU_max [h]': None,
                'Real [h]': None, 'Real_max [h]': None, 'VmPeak [GB]': None,
                'VmPeak_max [GB]': None, 'VmHWM [GB]': None, 'VmHWM_max [GB]': None}
    
    # Parse all log files
    metrics = {'CPU': [], 'Real': [], 'VmPeak': [], 'VmHWM': []}
    for log_file in log_files:
        cpu, real, vmp, vmh = parse_log_file(log_file)
        if cpu is not None: metrics['CPU'].append(cpu)
        if real is not None: metrics['Real'].append(real)
        if vmp is not None: metrics['VmPeak'].append(vmp)
        if vmh is not None: metrics['VmHWM'].append(vmh)
    
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
    parser.add_argument('-J', '--json-output', default='summary.json', help='Output JSON file')
    parser.add_argument('-n', '--max-logs', type=int, default=1, help='Max logs per dataset')
    args = parser.parse_args()

    # Process all datasets
    results = [process_dataset(dataset, args.max_logs) for dataset in args.datasets]
    
    # Output results
    for result in results:
        print(json.dumps(result, indent=2))
    
    with open(args.json_output, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"[INFO] Wrote {args.json_output}", file=sys.stderr)

if __name__ == '__main__':
    main()