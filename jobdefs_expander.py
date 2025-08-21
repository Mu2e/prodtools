#!/usr/bin/env python3
import os, sys
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from pathlib import Path
from utils.prod_utils import *

def main():
    p = argparse.ArgumentParser(description='Expand job definitions into individual job entries')
    p.add_argument('--map', required=True, help='Input jobdef map file')
    p.add_argument('--prod', action='store_true', help='Create SAM index definitions')
    args = p.parse_args()
    
    jobdefs_list = make_jobdefs_list(Path(args.map))
    print('\n'.join(jobdefs_list[:2] + ['...'] + jobdefs_list[-2:]))

    if args.prod:
        create_index_definition(Path(args.map).stem, len(jobdefs_list))

if __name__ == '__main__':
    main()
