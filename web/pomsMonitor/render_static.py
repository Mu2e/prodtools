#!/usr/bin/env python3
"""Render the pomsMonitor dashboard to static files.

Drops two files into ``--out``:

- ``index.html`` — from the ``monitor_static.html`` template next to
  this script (stamp substitution only). The template is a frozen,
  static-native copy of the dashboard: ``/api/jobs`` is a sibling
  ``jobs.json`` fetch (paired with ``lineage.json``), the famtree popup
  walks the pre-rendered lineage cache instead of calling
  ``/api/dataset/<name>``, and write-mode UI (Reload button, JSON
  Editor / JobDesc Generator nav) is stripped. A "Last refreshed"
  banner is baked in under the H1.
- ``jobs.json`` — from ``jobs_payload.build_jobs_payload``.

A separate file, ``lineage.json``, is owned by ``build_lineage.py`` and
holds the SAM-walked dataset topology. This script does NOT touch it.

Cron-friendly: prints what it wrote and exits 0 on success, non-zero
on any failure (including an empty jobs payload).
"""

import argparse
import datetime
import json
import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(_HERE, 'monitor_static.html')
_STAMP = '@@REFRESHED_AT@@'


def render(out_dir: str, prodtools_dir: str, db_path: str) -> None:
    # --prodtools-dir is kept for cron back-compat; utils are imported
    # from the checkout containing this script (jobs_payload pins it).
    sys.path.insert(0, _HERE)
    import jobs_payload

    jobs_data = jobs_payload.build_jobs_payload(db_path)
    if not jobs_data:
        print("WARNING: jobs payload is empty", file=sys.stderr)
    jobs_body = json.dumps(jobs_data, separators=(',', ':')).encode('utf-8')

    with open(_TEMPLATE, encoding='utf-8') as f:
        html = f.read()
    if _STAMP not in html:
        raise SystemExit(f"{_TEMPLATE} lacks the {_STAMP} placeholder")
    refreshed_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()
    html = html.replace(_STAMP, refreshed_at)

    os.makedirs(out_dir, exist_ok=True)
    jobs_path = os.path.join(out_dir, 'jobs.json')
    index_path = os.path.join(out_dir, 'index.html')
    with open(jobs_path, 'wb') as f:
        f.write(jobs_body)
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"wrote {jobs_path} ({len(jobs_body)} bytes, {len(jobs_data)} jobs)")
    print(f"wrote {index_path} ({len(html.encode('utf-8'))} bytes)")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--out",
        required=True,
        help="Output directory (e.g. /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor/)",
    )
    p.add_argument(
        "--prodtools-dir",
        default=os.environ.get(
            "PRODTOOLS_DIR",
            "/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools",
        ),
        help="Prodtools checkout to import from",
    )
    p.add_argument(
        "--db",
        default=os.environ.get(
            "POMS_DB_PATH",
            "/web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db",
        ),
        help="SQLite DB to read",
    )
    args = p.parse_args()
    render(args.out, args.prodtools_dir, args.db)


if __name__ == "__main__":
    main()
