#!/usr/bin/env python3
import os
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.prod_utils import create_index_definition

def main():
    p = argparse.ArgumentParser(description='List JSON job definitions')
    p.add_argument('--map', required=True, help='Input jobdef JSON file')
    p.add_argument('--prod', action='store_true', help='Create SAM index definitions')
    args = p.parse_args()
    
    with open(args.map, 'r') as f:
        jobdefs = json.load(f)
    
    total_jobs = sum(j['njobs'] for j in jobdefs)
    
    for i, j in enumerate(jobdefs):
        outputs = ", ".join(f"{o['dataset']}→{o['location']}" for o in j['outputs'])
        print(f"[{i}] {j['tarball']}: {j['njobs']} jobs, input={j['inloc']}, outputs={outputs}")
    
    print(f"\nTotal: {total_jobs} jobs")
    
    if args.prod:
        map_stem = Path(args.map).stem
        create_index_definition(map_stem, total_jobs, "etc.mu2e.index.000.txt")

if __name__ == '__main__':
    main()
