#!/usr/bin/env python3
"""Build the pomsMonitor jobs.json payload straight from the SQLite DB.

Extracted from the retired Flask app's ``/api/jobs`` route so
``render_static.py`` can produce the static dashboard without Flask.
The payload shape (keys and key order) is the route's, unchanged —
``jobs.json`` consumers depend on it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from utils.poms_db import get_db_session, Job
from utils.db_analyzer import build_dataset_info_map
from utils.samweb_wrapper import locate_files_strict
from utils.file_resolver import sam_physical_path, path_from_sam_locations
from utils.jobquery import Mu2eJobPars

# cnf tarballs are immutable, so setup-script extraction is cached for the
# life of the process — once per tarball instead of once per job row.
_setup_cache = {}


def _setup_scripts(tarballs):
    """Resolve the embedded setup script for each cnf tarball. Cache
    misses are located in ONE batch SAM round-trip (vs one per job row),
    falling back to a per-tarball locate if the batch call fails."""
    todo = [t for t in tarballs if t not in _setup_cache]
    locations = {}
    if todo:
        try:
            locations = locate_files_strict(todo)
        except Exception:
            locations = {}
    for tarball in todo:
        setup = ''
        try:
            locs = locations.get(tarball)
            if locs:
                full_path = path_from_sam_locations(tarball, locs)
            else:
                full_path = sam_physical_path(tarball)
            if os.path.exists(full_path):
                setup = Mu2eJobPars(full_path).setup() or ''
        except Exception:
            # Leave setup empty when SAM or the tarball is unavailable.
            pass
        _setup_cache[tarball] = setup
    return _setup_cache


def build_jobs_payload(db_path):
    """Return the dashboard's jobs list (one dict per jobdef)."""
    session = get_db_session(db_path)
    try:
        all_jobs = session.query(Job).all()
        info_map = build_dataset_info_map(session, all_jobs)
        setup_map = _setup_scripts(
            [job.tarball for job in all_jobs if job.tarball])
        jobs = []
        for job in all_jobs:
            njobs = job.njobs or 0
            outputs = []
            for output in job.outputs:
                info = info_map.get(output.dataset)
                nfiles = int(info.nfiles or 0) if info else 0
                nevts = int(info.nevts or 0) if info else 0

                creation_date_str = None
                if info and info.creation_date:
                    if isinstance(info.creation_date, str):
                        creation_date_str = info.creation_date.split('T')[0]
                    else:
                        creation_date_str = info.creation_date.strftime('%Y-%m-%d')

                outputs.append({
                    'name': output.dataset,
                    'nfiles': nfiles,
                    'nevts': nevts,
                    'events_per_file': round(nevts / nfiles, 2) if nfiles > 0 else 0.0,
                    'avg_size_mb': round((info.total_size or 0) / nfiles / 1e6, 2) if nfiles else 0.0,
                    'status': 'OK' if nfiles >= njobs else 'MISSING',
                    'has_children': info.has_children if info else False,
                    'creation_date': creation_date_str,
                    'location': (info.location or 'N/A') if info else 'N/A'
                })

            setup_script = setup_map.get(job.tarball, '') if job.tarball else ''

            jobs.append({
                'njobs': njobs,
                'tarball': job.tarball or '',
                'source_file': job.source_file or '',
                'setup_script': setup_script,
                'complete': job.complete or False,
                'avg_real_h': float(job.avg_real_h) if getattr(job, 'avg_real_h', None) is not None else None,
                'avg_vmhwm_gb': float(job.avg_vmhwm_gb) if getattr(job, 'avg_vmhwm_gb', None) is not None else None,
                'outputs': outputs
            })
        return jobs
    finally:
        session.close()
