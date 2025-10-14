#!/usr/bin/env python3
"""
anaTimeReport - Analyze Mu2e log performance metrics
"""

import sys, subprocess, argparse, re, json, os, csv
from pathlib import Path
from datetime import datetime

# Regex patterns
TIMEREPORT_REGEX = re.compile(r"TimeReport CPU = ([0-9]*\.?[0-9]+) Real = ([0-9]*\.?[0-9]+)")
MEMREPORT_REGEX = re.compile(r"MemReport\s+VmPeak\s*=\s*([0-9]*\.?[0-9]+)\s+VmHWM\s*=\s*([0-9]*\.?[0-9]+)")
JOBSTART_REGEX = re.compile(r"Begin processing the \d+\w+ record.*at (.+)")  # Captures "13-Oct-2025 02:00:59 UTC"

def get_log_files(dataset, max_files=None):
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
        return log_files if max_files is None else log_files[:max_files]
    
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
    return files if max_files is None else files[:max_files]

def parse_log_file(filepath):
    """Extract CPU, Real, VmPeak, VmHWM, and job start time from log file."""
    cpu = real = vmp = vmh = job_date = None
    
    with open(filepath, 'r', errors='ignore') as f:
        for line in f:
            if m := JOBSTART_REGEX.search(line):
                job_date = m.group(1).strip()
            if m := TIMEREPORT_REGEX.search(line):
                cpu, real = float(m.group(1)) / 3600, float(m.group(2)) / 3600
            if m := MEMREPORT_REGEX.search(line):
                vmp, vmh = float(m.group(1)) / 1024, float(m.group(2)) / 1024
            # Continue scanning for job_date even if metrics are found
            if all(x is not None for x in [cpu, real, vmp, vmh]) and job_date is not None:
                break
    
    return {'file': os.path.basename(filepath), 
            'full_path': filepath,
            'date': job_date if job_date else 'N/A',
            'CPU [h]': round(cpu, 2) if cpu else None, 
            'Real [h]': round(real, 2) if real else None,
            'VmPeak [GB]': round(vmp, 2) if vmp else None, 
            'VmHWM [GB]': round(vmh, 2) if vmh else None}

def save_csv(file_metrics, output_path):
    """Save file metrics to CSV."""
    if not file_metrics:
        return
    
    fieldnames = ['file', 'date', 'CPU [h]', 'Real [h]', 'VmPeak [GB]', 'VmHWM [GB]']
    with open(output_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(file_metrics)
    print(f"[INFO] Wrote {output_path}", file=sys.stderr)

def process_dataset(dataset, max_logs, save_csv_files=False):
    """Process one dataset and return metrics."""
    print(f"Processing {dataset}", file=sys.stderr)
    
    log_files = get_log_files(dataset, max_logs)
    if not log_files:
        return {'dataset': dataset, 'CPU [h]': None, 'CPU_max [h]': None,
                'Real [h]': None, 'Real_max [h]': None, 'VmPeak [GB]': None,
                'VmPeak_max [GB]': None, 'VmHWM [GB]': None, 'VmHWM_max [GB]': None}
    
    # Parse all log files
    file_metrics = [parse_log_file(log_file) for log_file in log_files]
    
    # Save all metrics to single CSV file if requested
    if save_csv_files:
        # Create CSV filename from dataset name
        csv_name = dataset.replace('.log', '.csv') if dataset.endswith('.log') else f"{dataset}.csv"
        save_csv(file_metrics, csv_name)
    
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
    parser.add_argument('-J', '--json-output', default='summary.json', help='Output JSON file')
    parser.add_argument('-n', '--max-logs', type=int, default=None, help='Max logs per dataset (default: all)')
    parser.add_argument('--csv', action='store_true', help='Save per-file metrics to CSV file')
    args = parser.parse_args()

    # Process all datasets
    results = [process_dataset(dataset, args.max_logs, args.csv) for dataset in args.datasets]
    
    # Output results
    for result in results:
        print(json.dumps(result, indent=2))
    
    with open(args.json_output, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"[INFO] Wrote {args.json_output}", file=sys.stderr)

if __name__ == '__main__':
    main()