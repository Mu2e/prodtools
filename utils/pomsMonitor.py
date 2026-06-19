#!/usr/bin/env python3
"""Analyze POMS jobdesc JSON files using the cached prodtools database."""

import os
import sys
import argparse
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_builder import build_db
from utils.db_analyzer import list_jobs, get_default_db_path, ignore_dataset, unignore_dataset, list_ignored
from utils.poms_db import get_db_session


def _parse_since(since_str):
    """Parse --since argument into a datetime cutoff.

    Accepts:
      - Nd  (e.g. 7d)  → N days ago
      - Nw  (e.g. 1w)  → N weeks ago
      - YYYY-MM-DD     → specific date
    """
    s = since_str.strip()
    if s.endswith('d') and s[:-1].isdigit():
        return datetime.utcnow() - timedelta(days=int(s[:-1]))
    if s.endswith('w') and s[:-1].isdigit():
        return datetime.utcnow() - timedelta(weeks=int(s[:-1]))
    try:
        return datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid --since value '{since_str}'. Use Nd, Nw, or YYYY-MM-DD."
        )


def uniformity_report(session, campaign, target, round_to=1000):
    """Recommend events-per-job so each primary yields ~`target` passed
    events/file, using measured filter efficiency (passed/generated) from the
    DB's gencount column. Requires a gencount-populated DB (--build-db).

    For each dts primary of `campaign`: eff = nevts/gencount;
    events_per_job = target/eff, rounded to the nearest `round_to`.
    """
    from utils.poms_db import DatasetInfo
    if not campaign:
        sys.exit("--uniformity requires --campaign")

    like = f"dts.mu2e.%.{campaign}.art"
    rows = (session.query(DatasetInfo)
            .filter(DatasetInfo.dataset_name.like(like))
            .filter(DatasetInfo.nfiles > 0)
            .all())
    prim = [r for r in rows if r.gencount and r.gencount > 0 and r.nevts is not None]
    missing = [r for r in rows if not (r.gencount and r.gencount > 0)]

    if not prim:
        print(f"No gencount-populated dts primaries for {campaign}. "
              f"Run --build-db first (gencount is captured during build).")
        return

    print(f"Uniformity plan for {campaign}: target {target:,} passed events/file"
          f" (events/job rounded to {round_to:,})")
    print(f"{'primary':<24}{'eff':>9}{'pass/file':>11}{'events/job':>14}")
    for r in sorted(prim, key=lambda x: x.filter_eff):
        eff = r.filter_eff
        raw = target / eff if eff > 0 else 0
        ev = max(round_to, round(raw / round_to) * round_to) if eff > 0 else 0
        desc = r.dataset_name.split('.')[2]
        print(f"{desc:<24}{eff:>9.4f}{r.nevts / r.nfiles:>11,.0f}{ev:>14,}")

    for r in missing:
        print(f"# no gencount (skipped): {r.dataset_name.split('.')[2]} "
              f"(dts not produced, or no dh.gencount)", file=sys.stderr)


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
    parser.add_argument('--since', metavar='DURATION',
                        help='Show only datasets created after this cutoff. '
                             'Examples: 7d (7 days), 1w (1 week), 2026-03-10')
    parser.add_argument('--needs-processing', action='store_true',
                        help='Show only complete datasets that have no downstream children '
                             '(i.e., ready for the next production step). '
                             'Highlights them in yellow when used with --outputs.')
    parser.add_argument('--ignore', metavar='DATASET',
                        help='Mark a dataset as ignored for --needs-processing checks')
    parser.add_argument('--ignore-reason', metavar='REASON',
                        help='Optional reason to record when using --ignore')
    parser.add_argument('--unignore', metavar='DATASET',
                        help='Remove the ignored flag from a dataset')
    parser.add_argument('--list-ignored', action='store_true',
                        help='List all datasets currently marked as ignored')
    parser.add_argument('--uniformity', action='store_true',
                        help='Recommend events-per-job per primary so each yields '
                             '~--target passed events/file (needs gencount; --build-db). '
                             'Requires --campaign.')
    parser.add_argument('--target', type=int, default=2000,
                        help='Target passed events per file for --uniformity (default: 2000)')
    parser.add_argument('--round', type=int, default=1000, dest='round_to',
                        help='Round events-per-job to nearest this for --uniformity (default: 1000)')
    args = parser.parse_args()

    since_dt = None
    if args.since:
        since_dt = _parse_since(args.since)

    session = get_db_session(args.db)

    # Handle ignore management commands (these exit early)
    if args.ignore:
        if ignore_dataset(session, args.ignore, reason=args.ignore_reason):
            print(f"Ignored: {args.ignore}")
        return

    if args.unignore:
        if unignore_dataset(session, args.unignore):
            print(f"Unignored: {args.unignore}")
        else:
            print(f"Dataset not found in DB: {args.unignore}")
        return

    if args.list_ignored:
        list_ignored(session)
        return

    if args.datasets_only:
        args.outputs = True

    if args.build_db:
        build_db(args.pattern, args.db, since=since_dt)

    if args.uniformity:
        uniformity_report(session, args.campaign, args.target, args.round_to)
        return

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
        since=since_dt,
        needs_processing=args.needs_processing,
    )


if __name__ == '__main__':
    main()

