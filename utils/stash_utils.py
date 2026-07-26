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
import shutil
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_resolver import sam_physical_path, path_from_sam_locations
from utils.samweb_wrapper import files_in_dataset, locate_files_strict
from utils import file_resolver


# ---------------------------------------------------------------------------
# Root path helpers / path builders — grammar lives in file_resolver;
# these names are kept as thin delegates for existing callers.
# ---------------------------------------------------------------------------

def stash_read_root() -> str:
    """Return the StashCache CVMFS read root (used by grid jobs in FCL)."""
    return file_resolver.stash_read_root()


def stash_write_root() -> str:
    """Return the StashCache dCache write root (used when copying files in)."""
    return file_resolver.stash_write_root()


def read_path_for_file(filename: str) -> str:
    """Return the full CVMFS read path for a file (used in FCL on the grid)."""
    return file_resolver.stash_read_path(filename)


def write_path_for_file(filename: str) -> str:
    """Return the full dCache write path for a file (copy destination)."""
    return file_resolver.stash_write_path(filename)


def list_expected_paths(dataset: str) -> List[str]:
    """
    Return the expected stash read paths for all files in a SAM dataset.

    This is useful for verifying that all files have been copied before
    submitting jobs with inloc='stash'.
    """
    files = files_in_dataset(dataset)
    return sorted(read_path_for_file(f) for f in files)


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def _copy_dataset(
    dataset: str,
    dest_path_fn,
    source_loc: str = "disk",
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> int:
    """
    Copy all files in a SAM dataset to the destination given by
    `dest_path_fn(filename)` — the shared engine behind
    copy_dataset_to_stash / copy_dataset_to_resilient.

    Files are copied with `shutil.copyfile`.  The source path is obtained
    from SAM for the requested source_loc ('disk' or 'tape').  For tape
    sources the file must already be staged to disk (dcache); this function
    does not trigger staging.

    Parameters
    ----------
    dataset      : SAM dataset name, e.g. "dts.mu2e.CeEndpoint.Run1Bab.art"
    dest_path_fn : filename -> absolute destination path
    source_loc   : SAM location type to read from ('disk' or 'tape')
    limit        : If set, copy at most this many files
    dry_run      : If True, print what would be done without copying
    verbose      : If True, print progress for each file

    Returns
    -------
    Number of files successfully copied.
    """
    files = files_in_dataset(dataset)
    if not files:
        raise ValueError(f"No files found in SAM for dataset: {dataset}")

    files = sorted(files)
    if limit is not None:
        files = files[:limit]

    # One batch SAM locate for the whole copy list (vs one HTTP round-trip
    # per file — resilient staging copies 10k+ pileup files). Files missing
    # from the batch result fall back to the per-file call, so error
    # semantics per file are unchanged.
    try:
        locations_map = locate_files_strict(files)
    except Exception:
        locations_map = {}

    n_ok = 0
    n_fail = 0

    for filename in files:
        dest = dest_path_fn(filename)
        dest_dir = os.path.dirname(dest)

        # Get source path from SAM, preferring the requested location type
        try:
            locs = locations_map.get(filename)
            if locs:
                src = path_from_sam_locations(filename, locs,
                                              prefer_location=source_loc)
            else:
                src = sam_physical_path(filename, prefer_location=source_loc)
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

        # Copy file. shutil.copyfile, not copy2/copy: content only, no
        # metadata or permission-bit copy — the destination is dCache
        # (stash/resilient), where chmod/utime on a freshly written file
        # is not reliably supported and would fail a copy that in fact
        # succeeded. Failures arrive as OSError with errno/strerror
        # rather than a return code plus scraped stderr.
        try:
            shutil.copyfile(src, dest)
        except OSError as e:
            print(f"  FAIL {filename}: {e.strerror or e}", file=sys.stderr)
            n_fail += 1
        else:
            n_ok += 1

    if verbose:
        status = "dry-run" if dry_run else "done"
        print(f"\n{status}: {n_ok} copied, {n_fail} failed out of {len(files)} files")

    return n_ok


def copy_dataset_to_stash(
    dataset: str,
    source_loc: str = "disk",
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> int:
    """Copy all files in a SAM dataset to their stash write locations."""
    return _copy_dataset(dataset, write_path_for_file, source_loc, limit, dry_run, verbose)


# ---------------------------------------------------------------------------
# Resilient disk support
# ---------------------------------------------------------------------------

def resilient_root() -> str:
    """Return the resilient dCache root path (write and direct-read on interactive nodes)."""
    return file_resolver.resilient_root()


def resilient_path_for_file(filename: str) -> str:
    """Return the full /pnfs/ path for a file in resilient storage."""
    return file_resolver.resilient_path(filename)


def list_resilient_paths(dataset: str) -> List[str]:
    """Return the expected resilient /pnfs/ paths for all files in a SAM dataset."""
    files = files_in_dataset(dataset)
    return sorted(resilient_path_for_file(f) for f in files)


def copy_dataset_to_resilient(
    dataset: str,
    source_loc: str = "disk",
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> int:
    """Copy all files in a SAM dataset to their resilient dCache locations."""
    return _copy_dataset(dataset, resilient_path_for_file, source_loc, limit, dry_run, verbose)
