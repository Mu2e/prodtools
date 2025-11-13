#!/usr/bin/env python3
"""Read-only analysis utilities over the prodtools SQLite database."""

import os
import sys
import fnmatch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .poms_db import Job, JobOutput, DatasetInfo
from .samweb_wrapper import list_definition_files, locate_file_full


def get_default_db_path() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "poms_data.db")


def _matches_pattern(job: Job, pattern: str | None) -> bool:
    if not pattern:
        return True
    source = os.path.basename(job.source_file) if job.source_file else ''
    return fnmatch.fnmatch(source, f"{pattern}.json")


def _collect_jobs(session, pattern: str | None):
    jobs = session.query(Job).all()
    if pattern:
        jobs = [job for job in jobs if _matches_pattern(job, pattern)]
    return jobs


def _build_dataset_info_map(session, jobs):
    dataset_names = {
        output.dataset
        for job in jobs
        for output in job.outputs
        if output.dataset
    }
    if not dataset_names:
        return {}
    infos = (
        session.query(DatasetInfo)
        .filter(DatasetInfo.dataset_name.in_(dataset_names))
        .all()
    )
    return {info.dataset_name: info for info in infos}


_location_cache: dict[str, str] = {}


def _normalize_location_from_path(path: str) -> str:
    if not path:
        return 'N/A'
    if path.startswith('enstore'):
        return 'enstore'
    if path.startswith('dcache'):
        return 'dcache'
    return 'N/A'


def _infer_location(dataset: str) -> str:
    if dataset in _location_cache:
        return _location_cache[dataset]

    location = 'N/A'
    try:
        files = list_definition_files(dataset)
        if files:
            first_file = files[0]
            locations = locate_file_full(first_file)
            for entry in locations:
                loc = entry.get('location') or entry.get('location_type')
                if loc:
                    location = _normalize_location_from_path(loc)
                    break
                full_path = entry.get('full_path')
                if full_path:
                    location = _normalize_location_from_path(full_path)
                    break
    except Exception:
        location = 'N/A'

    _location_cache[dataset] = location
    return location


def _get_outputs(session, job: Job, info_map: dict[str, DatasetInfo]) -> list:
    outputs = []
    for output in job.outputs:
        dataset = output.dataset
        if not dataset:
            continue
        info = info_map.get(dataset)
        nfiles = info.nfiles if info and info.nfiles is not None else 0
        nevts = info.nevts if info and info.nevts is not None else 0
        total_size = info.total_size if info and info.total_size is not None else 0
        location = output.location or (info.location if info and info.location else None)
        if not location:
            location = _infer_location(dataset)
        if location not in ('enstore', 'dcache', 'N/A') and location:
            location = _normalize_location_from_path(location)
        outputs.append((dataset, nfiles, nevts, total_size, location if location else 'N/A'))
    return outputs


def list_jobs(
    session,
    *,
    pattern: str | None = None,
    campaign: str | None = None,
    print_header: bool = True,
    sort_by: str = "njobs",
    show_outputs: bool = False,
    complete_only: bool = False,
    incomplete_only: bool = False,
    datasets_only: bool = False,
) -> None:
    jobs = _collect_jobs(session, pattern)

    if campaign:
        jobs = [
            job
            for job in jobs
            if (job.tarball and campaign in job.tarball)
            or (job.fcl_template and campaign in job.fcl_template)
            or (job.source_file and campaign in job.source_file)
        ]

    if sort_by == "njobs":
        jobs.sort(key=lambda j: j.njobs or 0, reverse=True)
    elif sort_by == "tarball":
        jobs.sort(key=lambda j: j.tarball or '')
    elif sort_by == "source_file":
        jobs.sort(key=lambda j: j.source_file or '')

    info_map = _build_dataset_info_map(session, jobs)
    total = sum(job.njobs or 0 for job in jobs)

    if campaign:
        print(f"Campaign: {campaign}")
        print(f"Job definitions: {len(jobs)}")
        print(f"Total jobs: {total:,}")
        print()

    if print_header:
        if show_outputs:
            if datasets_only:
                print("DATASET")
            else:
                print(f"{'NJOBS':>8} {'EVENTS':>10} {'FILE SIZE [MB]':>14} {'LOC':<6} {'TARBALL / OUTPUT DATASETS':<100}")
                print(f"{'-----':>8} {'------':>10} {'--------------':>14} {'---':<6} {'-------------------------':<100}")
        else:
            print(f"{'NJOBS':>8} {'INLOC':<8} {'OUTLOC':<8} {'JSON FILE':<25} {'TARBALL':<80}")
            print(f"{'-----':>8} {'-----':<8} {'------':<8} {'---------':<25} {'-------':<80}")

    for job in jobs:
        outputs = _get_outputs(session, job, info_map)
        is_complete = all(
            nfiles >= (job.njobs or 0) for _, nfiles, _, _, _ in outputs
        ) if outputs else False
        if (complete_only and not is_complete) or (incomplete_only and is_complete):
            continue
        first_location = outputs[0][4] if outputs else 'N/A'

        display_name = (job.indef or '') if job.fcl_template else (job.tarball or '')
        if not display_name:
            display_name = 'N/A'
        if show_outputs:
            outloc = first_location
            if datasets_only:
                for dataset_name, _, _, _, _ in outputs:
                    print(dataset_name)
            else:
                print(f"{job.njobs or 0:>8} {'':>10} {'':>14} {'':>6}    {display_name:<80}")
                for dataset_name, nfiles, nevts, total_size, location in outputs:
                    avg_size_mb = (total_size / nfiles / 1e6) if nfiles else 0
                    color = '\033[92m' if nfiles >= (job.njobs or 0) else '\033[91m'
                    reset = '\033[0m'
                    padded_dataset = f"  {dataset_name}"
                    print(
                        f"{nfiles:>8} {nevts:>10.2e} {avg_size_mb:>14.2f} "
                        f"{location or outloc:<6} {color}{padded_dataset:<100}{reset}"
                    )
                print("         " + "-" * 80)
        else:
            source_file = os.path.basename(job.source_file) if job.source_file else 'N/A'
            print(f"{job.njobs or 0:>8} {job.inloc or 'N/A':<8} {first_location:<8} {source_file:<25} {display_name:<80}")


