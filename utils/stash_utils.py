#!/usr/bin/env python3
"""
stash_utils.py: Utilities for copying Mu2e datasets to StashCache.

StashCache paths
----------------
Write (dCache/pnfs, accessible on interactive nodes):
    MU2E_STASH_WRITE  (default: /pnfs/mu2e/persistent/stash)

Read (CVMFS, accessible on grid worker nodes):
    MU2E_STASH_READ   (default: /cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash)

Layout convention
-----------------
Both roots share the same sub-path:
    datasets/<tier>/<owner>/<description>/<dsconf>/<ext>/<filename>

This mirrors the dataset name with dots replaced by slashes.  For example:
    dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art
    → datasets/dts/mu2e/CeEndpoint/Run1Bab/art/dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art

Usage
-----
    from utils.stash_utils import copy_dataset_to_stash
    copy_dataset_to_stash("dts.mu2e.CeEndpoint.Run1Bab.art", source_loc="disk")
"""

import os
import subprocess
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import Mu2eFilename, remove_storage_prefix
from utils.samweb_wrapper import list_files, locate_file_full


# ---------------------------------------------------------------------------
# Root path helpers
# ---------------------------------------------------------------------------

def stash_read_root() -> str:
    """Return the StashCache CVMFS read root (used by grid jobs in FCL)."""
    return os.environ.get(
        "MU2E_STASH_READ",
        "/cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash"
    )


def stash_write_root() -> str:
    """Return the StashCache dCache write root (used when copying files in)."""
    return os.environ.get(
        "MU2E_STASH_WRITE",
        "/pnfs/mu2e/persistent/stash"
    )


# ---------------------------------------------------------------------------
# Path builders
# ---------------------------------------------------------------------------

def _subpath(filename: str) -> str:
    """
    Return the dataset-derived sub-path for a file, relative to the stash root.

    Format: datasets/<tier>/<owner>/<description>/<dsconf>/<ext>/<filename>
    """
    fn = Mu2eFilename(filename)
    dataset = f"{fn.tier}.{fn.owner}.{fn.description}.{fn.dsconf}.{fn.extension}"
    ds_path = dataset.replace('.', '/')
    return f"datasets/{ds_path}/{filename}"


def read_path_for_file(filename: str) -> str:
    """Return the full CVMFS read path for a file (used in FCL on the grid)."""
    return f"{stash_read_root()}/{_subpath(filename)}"


def write_path_for_file(filename: str) -> str:
    """Return the full dCache write path for a file (copy destination)."""
    return f"{stash_write_root()}/{_subpath(filename)}"


def list_expected_paths(dataset: str) -> List[str]:
    """
    Return the expected stash read paths for all files in a SAM dataset.

    This is useful for verifying that all files have been copied before
    submitting jobs with inloc='stash'.
    """
    files = list_files(f"dh.dataset {dataset}")
    return sorted(read_path_for_file(f) for f in files)


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def copy_dataset_to_stash(
    dataset: str,
    source_loc: str = "disk",
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> int:
    """
    Copy all files in a SAM dataset to their stash write locations.

    Files are copied with `cp`.  The source path is obtained from SAM for
    the requested source_loc ('disk' or 'tape').  For tape sources the file
    must already be staged to disk (dcache); this function does not trigger
    staging.

    Parameters
    ----------
    dataset    : SAM dataset name, e.g. "dts.mu2e.CeEndpoint.Run1Bab.art"
    source_loc : SAM location type to read from ('disk' or 'tape')
    limit      : If set, copy at most this many files
    dry_run    : If True, print what would be done without copying
    verbose    : If True, print progress for each file

    Returns
    -------
    Number of files successfully copied.
    """
    files = list_files(f"dh.dataset {dataset}")
    if not files:
        raise ValueError(f"No files found in SAM for dataset: {dataset}")

    files = sorted(files)
    if limit is not None:
        files = files[:limit]

    n_ok = 0
    n_fail = 0

    for filename in files:
        dest = write_path_for_file(filename)
        dest_dir = os.path.dirname(dest)

        # Get source path from SAM, filtering by requested location type
        try:
            locations = locate_file_full(filename)
            preferred = [loc for loc in locations if loc.get('location_type') == source_loc]
            chosen = preferred[0] if preferred else (locations[0] if locations else None)
            if not chosen:
                raise ValueError(f"no locations returned")
            src = remove_storage_prefix(chosen.get('full_path', ''))
            if not src:
                raise ValueError(f"empty path in location record")
            if not src.endswith(filename):
                src = f"{src.rstrip('/')}/{filename}"
        except Exception as e:
            print(f"  SKIP {filename}: could not locate ({e})", file=sys.stderr)
            n_fail += 1
            continue

        if verbose or dry_run:
            action = "would cp" if dry_run else "cp"
            print(f"  {action}: {src} -> {dest}")

        if dry_run:
            n_ok += 1
            continue

        # Create destination directory
        os.makedirs(dest_dir, exist_ok=True)

        # Copy file
        result = subprocess.run(["cp", src, dest], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  FAIL {filename}: {result.stderr.strip()}", file=sys.stderr)
            n_fail += 1
        else:
            n_ok += 1

    if verbose:
        status = "dry-run" if dry_run else "done"
        print(f"\n{status}: {n_ok} copied, {n_fail} failed out of {len(files)} files")

    return n_ok


# ---------------------------------------------------------------------------
# Resilient disk support
# ---------------------------------------------------------------------------

def resilient_root() -> str:
    """Return the resilient dCache root path (write and direct-read on interactive nodes)."""
    return os.environ.get("MU2E_RESILIENT", "/pnfs/mu2e/resilient")


def resilient_path_for_file(filename: str) -> str:
    """Return the full /pnfs/ path for a file in resilient storage."""
    return f"{resilient_root()}/{_subpath(filename)}"


def list_resilient_paths(dataset: str) -> List[str]:
    """Return the expected resilient /pnfs/ paths for all files in a SAM dataset."""
    files = list_files(f"dh.dataset {dataset}")
    return sorted(resilient_path_for_file(f) for f in files)


def copy_dataset_to_resilient(
    dataset: str,
    source_loc: str = "disk",
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> int:
    """
    Copy all files in a SAM dataset to their resilient dCache locations.

    Parameters
    ----------
    dataset    : SAM dataset name, e.g. "dts.mu2e.CeEndpoint.Run1Bab.art"
    source_loc : SAM location type to read from ('disk' or 'tape')
    limit      : If set, copy at most this many files
    dry_run    : If True, print what would be done without copying
    verbose    : If True, print progress for each file

    Returns
    -------
    Number of files successfully copied.
    """
    files = list_files(f"dh.dataset {dataset}")
    if not files:
        raise ValueError(f"No files found in SAM for dataset: {dataset}")

    files = sorted(files)
    if limit is not None:
        files = files[:limit]

    n_ok = 0
    n_fail = 0

    for filename in files:
        dest = resilient_path_for_file(filename)
        dest_dir = os.path.dirname(dest)

        try:
            locations = locate_file_full(filename)
            preferred = [loc for loc in locations if loc.get('location_type') == source_loc]
            chosen = preferred[0] if preferred else (locations[0] if locations else None)
            if not chosen:
                raise ValueError("no locations returned")
            src = remove_storage_prefix(chosen.get('full_path', ''))
            if not src:
                raise ValueError("empty path in location record")
            if not src.endswith(filename):
                src = f"{src.rstrip('/')}/{filename}"
        except Exception as e:
            print(f"  SKIP {filename}: could not locate ({e})", file=sys.stderr)
            n_fail += 1
            continue

        if verbose or dry_run:
            action = "would cp" if dry_run else "cp"
            print(f"  {action}: {src} -> {dest}")

        if dry_run:
            n_ok += 1
            continue

        os.makedirs(dest_dir, exist_ok=True)

        result = subprocess.run(["cp", src, dest], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  FAIL {filename}: {result.stderr.strip()}", file=sys.stderr)
            n_fail += 1
        else:
            n_ok += 1

    if verbose:
        status = "dry-run" if dry_run else "done"
        print(f"\n{status}: {n_ok} copied, {n_fail} failed out of {len(files)} files")

    return n_ok
