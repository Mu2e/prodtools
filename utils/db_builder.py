#!/usr/bin/env python3
"""Build and populate the prodtools SQLite database from POMS JSON files.

Creates the database (if missing), creates tables, clears existing rows,
and ingests POMS jobdesc JSONs into `jobs` and `job_outputs`.
"""

import os
import sys
import glob
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.poms_db import get_db_session, Job, JobOutput, DatasetInfo
from utils.samweb_wrapper import count_files, locate_file, locate_file_full, list_files, list_definition_files, describe_definition
from utils.jobiodetail import Mu2eJobIO
from utils.logparser import process_dataset as parse_logs_for_dataset
import re
from datetime import datetime


def _extract_file_path(location):
    """Extract file path from SAM location result."""
    if isinstance(location, dict):
        file_path = location.get('full_path', '')
        return file_path.split(':', 1)[1] if ':' in file_path else file_path
    elif isinstance(location, str):
        return location.split(':', 1)[1] if ':' in location else location
    return None


def _get_dataset_stats(dataset_name):
    """Get dataset statistics from SAM."""
    try:
        result = list_files(f"dh.dataset={dataset_name}", summary=True)
        if isinstance(result, dict):
            return (
                int(result.get('file_count', 0) or 0),
                int(result.get('total_event_count', 0) or 0),
                int(result.get('total_file_size', 0) or 0)
            )
        return (0, 0, 0)
    except Exception:
        return (0, 0, 0)


def _check_dataset_has_children(dataset_name):
    """Check if a dataset has children by checking if the first file has child files.
    
    Args:
        dataset_name: Dataset name (e.g., "dts.mu2e.FlatePlus.MDC2020bb.art")
    
    Returns:
        bool: True if the dataset has children, False otherwise
    """
    try:
        # Get first file from dataset definition
        files = list_definition_files(dataset_name)
        if not files:
            return False
        
        first_file = files[0]
        
        # Check if any files are children of the first file
        children = list_files(f'ischildof: (file_name {first_file})')
        return len(children) > 0
    except Exception:
        return False


def _jobdef_to_log_dataset(tarball_name):
    """Convert jobdef tarball name to log dataset name.
    
    Args:
        tarball_name: Jobdef tarball name (e.g., "cnf.mu2e.FlatMuMinus.MDC2025ab.0.tar")
    
    Returns:
        str: Log dataset name (e.g., "log.mu2e.FlatMuMinus.MDC2025ab.log")
    """
    if not tarball_name or not tarball_name.startswith('cnf.'):
        return None
    # Remove .tar extension
    if tarball_name.endswith('.tar'):
        base = tarball_name[:-4]
    else:
        base = tarball_name
    # Remove the index part (e.g., ".0")
    parts = base.rsplit('.', 1)
    if len(parts) == 2 and parts[1].isdigit():
        base = parts[0]
    # Replace cnf. with log. and add .log
    if base.startswith('cnf.'):
        return base.replace('cnf.', 'log.', 1) + '.log'
    return None


def _get_dataset_creation_date(dataset_name):
    """Get creation date from SAM definition.
    
    Args:
        dataset_name: Dataset name (e.g., "dts.mu2e.FlatePlus.MDC2020bb.art")
    
    Returns:
        datetime: Creation date as datetime object or None
    """
    try:
        description = describe_definition(dataset_name)
        # Parse "Creation Date: 2025-09-03T11:46:14+00:00"
        match = re.search(r'Creation Date:\s+(.+)', description)
        if match:
            date_str = match.group(1).strip()
            # Parse ISO format datetime (e.g., "2025-09-03T11:46:14+00:00")
            # Remove timezone offset and parse as naive datetime
            # SQLite doesn't store timezone info, so we'll store as UTC naive datetime
            if '+' in date_str:
                date_str = date_str.split('+')[0]
            elif date_str.endswith('Z'):
                date_str = date_str[:-1]
            try:
                return datetime.fromisoformat(date_str)
            except ValueError:
                # Try parsing with common formats
                for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
        return None
    except Exception:
        return None


def _normalize_location(raw: str | None) -> str:
    if not raw:
        return 'N/A'
    if raw.startswith('enstore'):
        return 'enstore'
    if raw.startswith('dcache'):
        return 'dcache'
    return 'N/A'


def _infer_dataset_location(dataset_name):
    try:
        files = list_definition_files(dataset_name)
        if not files:
            return 'N/A'
        first_file = files[0]
        locations = locate_file_full(first_file)
        for entry in locations:
            loc = entry.get('location') or entry.get('location_type')
            if loc:
                return _normalize_location(loc)
            full_path = entry.get('full_path')
            if full_path:
                return _normalize_location(full_path)
    except Exception:
        pass
    return 'N/A'


def _is_output_complete(session, output, njobs):
    """Check if an output dataset is complete (nfiles >= njobs)."""
    info = session.query(DatasetInfo).filter_by(dataset_name=output.dataset).one_or_none()
    return info and info.nfiles and info.nfiles >= njobs


def build_db(pattern: str, db_path: str, poms_dir: str = "/exp/mu2e/app/users/mu2epro/production_manager/poms_map", limit: int = None) -> None:
    """Create and populate the SQLite DB from POMS JSONs matching pattern.

    - Creates DB and tables if missing
    - Updates existing jobs or creates new ones from JSON files
    - Preserves existing metrics (avg_real_h, avg_vmhwm_gb) when updating
    - Resolves template-mode njobs via `defname:` when needed
    """
    session = get_db_session(db_path)

    json_files = sorted(glob.glob(f"{poms_dir}/{pattern}.json"))
    print(f"Loading {len(json_files)} JSON files...")

    # Track tarballs we see in JSON files (to remove jobs that no longer exist)
    seen_tarballs = set()
    count = 0
    
    for json_file in json_files:
        with open(json_file, "r") as f:
            entries = json.load(f)
        for entry in entries:
            tarball = entry.get("tarball")
            if not tarball:
                continue
            
            seen_tarballs.add(tarball)
            
            # Check if job already exists
            existing_job = session.query(Job).filter_by(tarball=tarball).first()
            
            if existing_job:
                # Update existing job, but preserve metrics
                existing_job.fcl_template = entry.get("fcl_template")
                existing_job.indef = entry.get("indef")
                existing_job.njobs = entry.get("njobs", 0)
                existing_job.template_mode = entry.get("template_mode", False)
                existing_job.inloc = entry.get("inloc")
                existing_job.source_file = json_file
                # Preserve avg_real_h and avg_vmhwm_gb if they exist
                job = existing_job
            else:
                # Create new job
                job = Job(
                    tarball=tarball,
                    fcl_template=entry.get("fcl_template"),
                    indef=entry.get("indef"),
                    njobs=entry.get("njobs", 0),
                    template_mode=entry.get("template_mode", False),
                    inloc=entry.get("inloc"),
                    source_file=json_file,
                )
                session.add(job)

            # Resolve template-mode njobs via defname when missing
            if job.fcl_template and job.indef and not job.njobs:
                try:
                    njobs = count_files(f"defname: {job.indef}")
                    if njobs > 0:
                        job.njobs = njobs
                        print(f"  Template mode: {job.indef} -> {njobs} files")
                    else:
                        job.njobs = 0
                        job.template_mode = True
                        print(f"  Template mode: {job.indef} -> dataset not found")
                except Exception as e:
                    print(f"  Warning: Could not count files for {job.indef}: {e}")
                    job.njobs = 0
                    job.template_mode = True

            # Clear existing outputs and recreate from JSON
            # (we'll discover more outputs from tarballs later)
            job.outputs = []
            
            # Skip wildcard outputs (e.g., "*.art") - we'll use exact dataset names from tarball inspection instead
            # Only save non-wildcard outputs if any exist
            for output in entry.get("outputs", []):
                dataset = output.get("dataset")
                if dataset and "*" not in dataset:
                    job.outputs.append(
                        JobOutput(dataset=dataset, location=output.get("location"))
                    )

            count += 1

    # Remove jobs that are no longer in JSON files (cascade deletes JobOutputs automatically)
    if seen_tarballs:
        removed = session.query(Job).filter(~Job.tarball.in_(seen_tarballs)).delete(synchronize_session=False)
        if removed > 0:
            print(f"Removed {removed} jobs no longer in JSON files")

    session.commit()
    print(f"Loaded {count} job definitions\n")

    # Discover derived datasets from tarballs and cache into dataset_info and job_outputs
    discovered = 0
    jobs_query = session.query(Job).filter(Job.tarball.isnot(None)).all()
    if limit:
        jobs_query = jobs_query[:limit]
        print(f"Processing first {limit} jobs only (test mode)\n")
    for job in jobs_query:
        try:
            file_path = _extract_file_path(locate_file(job.tarball))
            if not file_path:
                continue
            full_path = os.path.join(file_path, job.tarball)
            if not os.path.exists(full_path):
                continue

            outputs = Mu2eJobIO(full_path).job_outputs(0)
            if not outputs:
                continue

            # Compute performance metrics once per jobdef (aggregate across outputs)
            # Skip logparser if metrics are already present
            if job.avg_real_h is not None and job.avg_vmhwm_gb is not None:
                print(f"Skipping logparser for {job.tarball} (metrics already present)")
            else:
                job_real_vals = []
                job_vmhwm_vals = []
                
                # Parse logs for the jobdef (convert tarball name to log dataset name)
                log_dataset = _jobdef_to_log_dataset(job.tarball)
                if log_dataset:
                    try:
                        metrics = parse_logs_for_dataset(log_dataset, max_logs=10)
                        if isinstance(metrics, dict):
                            if metrics.get('Real [h]') is not None:
                                job_real_vals.append(float(metrics.get('Real [h]')))
                            if metrics.get('VmHWM [GB]') is not None:
                                job_vmhwm_vals.append(float(metrics.get('VmHWM [GB]')))
                    except Exception:
                        pass
                
                # Save aggregated metrics to the Job (per jobdef)
                if job_real_vals:
                    job.avg_real_h = round(sum(job_real_vals) / len(job_real_vals), 2)
                if job_vmhwm_vals:
                    job.avg_vmhwm_gb = round(sum(job_vmhwm_vals) / len(job_vmhwm_vals), 2)

            for output_file in outputs.values():
                # Extract dataset name from filename (skip /dev/null and non-standard files)
                # Accept both .art and .root files
                if output_file == '/dev/null' or not (output_file.endswith('.art') or output_file.endswith('.root')):
                    continue
                # Format: tier.owner.description.dsconf.sequencer.extension
                # Dataset: tier.owner.description.dsconf.extension (skip sequencer)
                parts = output_file.split('.')
                if len(parts) != 6:
                    continue
                dataset_name = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}.{parts[5]}"

                nfiles, nevts, total_size = _get_dataset_stats(dataset_name)
                has_children = _check_dataset_has_children(dataset_name)
                creation_date = _get_dataset_creation_date(dataset_name)

                # Upsert dataset_info
                info = session.query(DatasetInfo).filter_by(dataset_name=dataset_name).one_or_none()
                if info is None:
                    info = DatasetInfo(dataset_name=dataset_name)
                    session.add(info)
                info.nfiles, info.nevts, info.total_size = nfiles, nevts, total_size
                info.has_children = has_children
                if creation_date:
                    info.creation_date = creation_date
                if not info.location or info.location == 'N/A':
                    info.location = _infer_dataset_location(dataset_name)

                # Ensure job_outputs row exists
                if not session.query(JobOutput).filter_by(job_id=job.id, dataset=dataset_name).first():
                    session.add(JobOutput(
                        job_id=job.id,
                        dataset=dataset_name,
                        location=info.location if info.location and info.location != 'N/A' else None
                    ))
                else:
                    job_output = session.query(JobOutput).filter_by(job_id=job.id, dataset=dataset_name).first()
                    if job_output and not job_output.location:
                        job_output.location = info.location if info.location and info.location != 'N/A' else job_output.location
                discovered += 1

        except Exception:
            continue

    try:
        session.commit()
    except Exception:
        session.rollback()
    if discovered:
        print(f"Discovered and cached {discovered} derived datasets")

    # Compute completion status: job is complete if all outputs have nfiles >= njobs
    print("Computing completion status...")
    complete_count = 0
    for job in session.query(Job).all():
        if not job.tarball or job.njobs == 0:
            job.complete = False
        else:
            job.complete = all(_is_output_complete(session, output, job.njobs) for output in job.outputs if output.dataset)
        if job.complete:
            complete_count += 1
    
    session.commit()
    print(f"Marked {complete_count} jobs as complete\n")


if __name__ == "__main__":
    import argparse

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_db = os.path.join(repo_root, "poms_data.db")

    parser = argparse.ArgumentParser(description="Build/populate prodtools DB from POMS JSONs")
    parser.add_argument("--pattern", default="MDC202*", help="POMS JSON file pattern")
    parser.add_argument("--db", default=default_db, help="SQLite DB file path")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of jobs to process (for testing)")
    args = parser.parse_args()

    build_db(args.pattern, args.db, limit=args.limit)


