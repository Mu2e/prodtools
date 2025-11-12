#!/usr/bin/env python3
"""Analyze POMS jobdesc JSON files using the cached prodtools database."""

import os
import sys
import argparse

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_builder import build_db
from utils.db_analyzer import list_jobs, get_default_db_path
from utils.poms_db import get_db_session


def main():
    parser = argparse.ArgumentParser(description="Analyze POMS jobdesc JSON files")
    parser.add_argument('--pattern', default='MDC202*', help='POMS JSON file pattern (default: MDC202*)')
    parser.add_argument('--db', default=get_default_db_path(), help='SQLite DB file path')
    parser.add_argument('--build-db', action='store_true', help='Refresh the database before analysis')
    parser.add_argument('--list', action='store_true', help='List all job definitions')
    parser.add_argument('--campaign', help='Filter by campaign (e.g., MDC2025ac)')
    parser.add_argument('--outputs', action='store_true', help='Show output dataset names')
    parser.add_argument('--sort', default='njobs', help='Sort by field (default: njobs)')
    parser.add_argument('--complete', action='store_true', help='Show only complete datasets (requires --outputs)')
    parser.add_argument('--incomplete', action='store_true', help='Show only incomplete datasets (requires --outputs)')
    parser.add_argument('--datasets-only', action='store_true', help='Print only dataset names (implies --outputs)')
    args = parser.parse_args()
    
    if args.datasets_only:
        args.outputs = True

    if args.build_db:
        build_db(args.pattern, args.db)

    session = get_db_session(args.db)

    show_outputs = (
        args.outputs
        or args.datasets_only
        or args.complete
        or args.incomplete
        or not args.list
    )

    list_jobs(
        session,
        pattern=args.pattern,
        campaign=args.campaign,
        sort_by=args.sort,
        show_outputs=show_outputs,
        complete_only=args.complete,
        incomplete_only=args.incomplete,
        datasets_only=args.datasets_only,
    )


if __name__ == '__main__':
    main()

