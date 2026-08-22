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
from typing import List, NamedTuple, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_resolver import sam_physical_path, path_from_sam_locations
from utils.samweb_wrapper import (files_in_dataset, file_sizes_in_dataset,
                                  locate_files_strict)
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
    """Expected stash read paths for all files in a SAM dataset. Useful
    for verifying files are copied before submitting jobs with
    inloc='stash'."""
    files = files_in_dataset(dataset)
    return sorted(read_path_for_file(f) for f in files)


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def _already_at_dest(dest: str, want: Optional[int]) -> bool:
    """True when `dest` exists with the SAM-recorded size `want`.
    An unknown size (None) or a missing/unstat-able file is False."""
    if want is None:
        return False
    try:
        return os.path.getsize(dest) == want
    except OSError:
        return False


def _locate_src(filename: str, locations_map, source_loc: str) -> str:
    """Source path for one file: from the batch locate result when it
    has a record, else one per-file SAM locate. Raises ValueError /
    RuntimeError when SAM has no usable location."""
    locs = locations_map.get(filename)
    if locs:
        return path_from_sam_locations(filename, locs,
                                      prefer_location=source_loc)
    return sam_physical_path(filename, prefer_location=source_loc)


class CopyResult(NamedTuple):
    """Outcome of a dataset copy: how many landed, how many did not.

    `failed` is carried out of the copy loop rather than only printed. It
    used to be counted, reported in the summary line, then dropped on
    return — so a caller couldn't tell a complete copy from one that lost
    every file, and bin/copy_to_stash exited 0 either way."""
    copied: int
    failed: int


def _copy_dataset(
    dataset: str,
    dest_path_fn,
    source_loc: str = "disk",
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = True,
    skip_existing: bool = False,
) -> CopyResult:
    """Copy all files in a SAM dataset to the destination given by
    `dest_path_fn(filename)` — the shared engine behind
    copy_dataset_to_stash / copy_dataset_to_resilient.

    Files are copied with `shutil.copyfile`. Source path comes from SAM
    for the requested source_loc ('disk' or 'tape'); for tape sources the
    file must already be staged to disk (dcache) — this function does not
    trigger staging.

    dataset: SAM dataset name, e.g. "dts.mu2e.CeEndpoint.Run1Bab.art".
    dest_path_fn: filename -> absolute destination path.
    source_loc: SAM location type to read from ('disk' or 'tape').
    limit: if set, copy at most this many files.
    dry_run: if True, print what would be done without copying.
    verbose: if True, print progress for each file.
    skip_existing: if True, skip files already at the destination with
    the SAM-recorded size. Without it a partially staged dataset is
    re-copied in full, and each existing file is opened for truncating
    write — which on dCache either fails or, worse, truncates a good file
    if the copy dies midway.

    Returns a CopyResult.
    """
    files = files_in_dataset(dataset)
    if not files:
        raise ValueError(f"No files found in SAM for dataset: {dataset}")

    files = sorted(files)
    if limit is not None:
        files = files[:limit]

    # Expected sizes are only needed to answer "is this file already here";
    # one dataset-wide SAM call, not one per file.
    expected_sizes = file_sizes_in_dataset(dataset) if skip_existing else {}
    n_skip = 0
    if skip_existing:
        keep = [f for f in files
                if not _already_at_dest(dest_path_fn(f), expected_sizes.get(f))]
        n_skip = len(files) - len(keep)
        files = keep
        if verbose:
            print(f"  skipping {n_skip} file(s) already at destination")

    # One batch SAM locate for the whole copy list (vs one HTTP round-trip
    # per file — resilient staging copies 10k+ pileup files). Files missing
    # from the batch result fall back to the per-file call, so error
    # semantics per file are unchanged.
    try:
        locations_map = locate_files_strict(files) if files else {}
    except Exception as e:
        print(f"  WARNING: batch SAM locate failed ({e}); falling back to "
              f"one locate per file", file=sys.stderr)
        locations_map = {}

    n_ok = 0
    n_fail = 0

    for filename in files:
        dest = dest_path_fn(filename)
        dest_dir = os.path.dirname(dest)

        # Source path from SAM, preferring source_loc's location type.
        try:
            src = _locate_src(filename, locations_map, source_loc)
        except (ValueError, RuntimeError) as e:
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

        # copyfile, not copy2/copy: content only, no metadata/permission
        # bits — chmod/utime on dCache (stash/resilient) is unreliable
        # and would fail a copy that in fact succeeded.
        try:
            shutil.copyfile(src, dest)
        except OSError as e:
            print(f"  FAIL {filename}: {e.strerror or e}", file=sys.stderr)
            n_fail += 1
        else:
            n_ok += 1

    if verbose:
        status = "dry-run" if dry_run else "done"
        skipped = f", {n_skip} skipped" if skip_existing else ""
        print(f"\n{status}: {n_ok} copied, {n_fail} failed{skipped} "
              f"out of {len(files) + n_skip} files")

    return CopyResult(copied=n_ok, failed=n_fail)


def copy_dataset_to_stash(
    dataset: str,
    source_loc: str = "disk",
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = True,
    skip_existing: bool = False,
) -> CopyResult:
    """Copy all files in a SAM dataset to their stash write locations."""
    return _copy_dataset(dataset, write_path_for_file, source_loc, limit,
                         dry_run, verbose, skip_existing)


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
    skip_existing: bool = False,
) -> CopyResult:
    """Copy all files in a SAM dataset to their resilient dCache locations."""
    return _copy_dataset(dataset, resilient_path_for_file, source_loc, limit,
                         dry_run, verbose, skip_existing)
