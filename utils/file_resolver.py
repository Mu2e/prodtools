#!/usr/bin/env python3
"""
file_resolver.py: given a Mu2e filename and an inloc, where does it
live and how do I read it.

Owns the dCache/CVMFS path grammar (stash, resilient, dataset dirs,
token scopes) and the per-inloc location logic that used to be spread
across jobfcl, stash_utils, datasetFileList, and jobsub_argv. SAM
access goes exclusively through samweb_wrapper.

Import cost: pure at import time (no samweb_client / gfal2); those are
lazily imported on first use, so pure-function consumers (jobsub_argv,
unit tests) and dir:-mode resolution work without the Mu2e ops env.
"""

import os
import re
import sys
from typing import Optional

from .job_common import Mu2eName, remove_storage_prefix

# xrootd door prefixes: fcl read URLs use `xroot://`, gfal2 stat uses
# `root://`. Both predate this module; kept as-is — worker fcl output
# must stay byte-identical.
XROOT_READ_PREFIX = 'xroot://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/'
XROOT_STAT_PREFIX = 'root://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/'

# SAM location records may carry a trailing "(2290@fm4794l8)" suffix.
# Compiled once — url() runs per file in the worker inner loop.
_LOCATION_SUFFIX_RE = re.compile(r'\([^)]+\)$')


# ---------------------------------------------------------------------------
# Roots (env-overridable)
# ---------------------------------------------------------------------------

def stash_read_root() -> str:
    """StashCache CVMFS read root (used by grid jobs in FCL)."""
    return os.environ.get(
        "MU2E_STASH_READ",
        "/cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash"
    )


def stash_write_root() -> str:
    """StashCache dCache write root (used when copying files in)."""
    return os.environ.get("MU2E_STASH_WRITE", "/pnfs/mu2e/persistent/stash")


def resilient_root() -> str:
    """Resilient dCache root (write and direct-read on interactive nodes)."""
    return os.environ.get("MU2E_RESILIENT", "/pnfs/mu2e/resilient")


# ---------------------------------------------------------------------------
# Path grammar
# ---------------------------------------------------------------------------

def dataset_subpath(filename: str) -> str:
    """Dataset-derived sub-path for a file, relative to a stash/resilient
    root: datasets/<tier>/<owner>/<description>/<dsconf>/<ext>/<filename>."""
    ds_path = str(Mu2eName.parse(filename).dataset).replace('.', '/')
    return f"datasets/{ds_path}/{filename}"


def stash_read_path(filename: str) -> str:
    """Full CVMFS read path for a file on stash."""
    return f"{stash_read_root()}/{dataset_subpath(filename)}"


def stash_write_path(filename: str) -> str:
    """Full dCache write path for a file on stash."""
    return f"{stash_write_root()}/{dataset_subpath(filename)}"


def resilient_path(filename: str) -> str:
    """Full /pnfs/ path for a file in resilient storage."""
    return f"{resilient_root()}/{dataset_subpath(filename)}"


def dataset_dir(dsname: str, location: str) -> str:
    """Absolute /pnfs directory for a Mu2e dataset at the given location.

    Tape has no `datasets/` component, unlike disk/scratch (and unlike
    the token-scope paths — see storage_scope). Returns '' for unknown
    locations.
    """
    n = Mu2eName.parse(dsname)
    owner_prefix = "phy" if n.owner == "mu2e" else "usr"
    base_path = f"{owner_prefix}-{n.tier_class}"
    ds_path = dsname.replace('.', '/')
    if location == 'disk':
        return f"/pnfs/mu2e/persistent/datasets/{base_path}/{ds_path}"
    if location == 'tape':
        return f"/pnfs/mu2e/tape/{base_path}/{ds_path}"
    if location == 'scratch':
        return f"/pnfs/mu2e/scratch/datasets/{base_path}/{ds_path}"
    return ""


# Mu2e standard location → dCache area name (under `/pnfs/mu2e/<area>/`).
# Mirrors Mu2eFNBase::location_root values.
LOCATION_AREA = {
    "tape": "tape",
    "disk": "persistent",
    "scratch": "scratch",
    "resilient": "resilient",
}


def storage_scope(filename: str, location) -> Optional[str]:
    """Narrowest dCache token scope covering writes of `filename` to
    `location`: /mu2e/<area>/datasets/<owner-class>-<tier>/<tier>/<owner>.

    The scope path is the PHYSICAL path with `/pnfs` stripped, nothing
    else changed — upstream mu2ejobsub's `token_request_dirname` is
    exactly `s|^/pnfs/mu2e|/mu2e|`, so it must inherit dataset_dir's
    layout asymmetry: disk/scratch carry a `datasets/` component, tape
    does not.

    This used to insert `datasets/` unconditionally, making the tape
    scope name a path nothing lives at — it granted nothing. Writes
    still worked via the separate broad `storage.create:/mu2e`, but
    under WLCG that permits upload, NOT overwrite/delete, so
    pushOutput's `recover` path could never remove a stale target and
    403'd on every retry, permanently (CeMLeadingLog 2/418, 2026-07-27).
    Passing --need-storage-modify at all replaces the role's default
    broad `storage.modify:/mu2e` (what POMS jobs keep, why POMS
    recoveries can overwrite) — a wrong path here is a real downgrade,
    not a no-op.

    Why narrowest: htvault rejects `--need-storage-modify
    /mu2e/scratch/datasets` as too broad (`PermissionError: Unable to
    add 'storage.modify:...' scope given initial scope '[...]'`).
    Scopes are pre-allocated per (area, tier, owner) tuple.

    Returns None for `dir:<path>` locations, unknown locations, or
    unparseable filenames.
    """
    if not location or str(location).startswith("dir:"):
        return None
    area = LOCATION_AREA.get(location)
    if not area:
        return None
    try:
        n = Mu2eName.parse(filename)
    except ValueError:
        return None
    if n.is_dataset:
        return None
    owner_prefix = "phy" if n.owner == "mu2e" else "usr"
    leaf = f"{owner_prefix}-{n.tier_class}/{n.tier}/{n.owner}"
    # Mirrors dataset_dir's tape/disk asymmetry.
    # TestStorageScopeCoversPhysicalPath pins the invariant.
    if location == "tape":
        return f"/mu2e/{area}/{leaf}"
    return f"/mu2e/{area}/datasets/{leaf}"


def xroot_read_url(pnfs_path: str) -> str:
    """Rewrite a /pnfs/ path to the xrootd read URL used in worker fcl."""
    return pnfs_path.replace('/pnfs/', XROOT_READ_PREFIX, 1)


# ---------------------------------------------------------------------------
# SAM location records → physical paths
# ---------------------------------------------------------------------------

def path_from_sam_location(filename, location):
    """Physical path from one SAM location record: full_path → strip the
    storage prefix and any trailing '(pool@node)' suffix → append the
    basename if the record path is a directory. Raises ValueError on an
    empty or malformed record."""
    if not isinstance(location, dict):
        raise ValueError(f"malformed SAM location record for {filename}: {location!r}")
    path = remove_storage_prefix(location.get('full_path', ''))
    path = _LOCATION_SUFFIX_RE.sub('', path)
    if not path:
        raise ValueError(f"empty path in SAM location record for {filename}")
    if not path.endswith(filename):
        path = f"{path.rstrip('/')}/{filename}"
    return path


def path_from_sam_locations(filename, locations, prefer_location=None):
    """Pick a record from a locate result (preferring `prefer_location`,
    else the first record) and return its physical path — the
    record-selection half of sam_physical_path, shared with batch-locate
    consumers (stash copy, dashboards). Raises ValueError on an empty
    locations list or a malformed record."""
    if not locations:
        raise ValueError(f"no SAM locations for {filename}")
    chosen = locations[0]
    if prefer_location:
        preferred = [loc for loc in locations
                     if loc.get('location_type') == prefer_location]
        if preferred:
            chosen = preferred[0]
    return path_from_sam_location(filename, chosen)


def sam_physical_path(filename, prefer_location=None):
    """Readable physical path for `filename` from its SAM locations (one
    locate call). Prefers `prefer_location`'s location_type, else the
    first record. Raises ValueError when SAM has no usable location.

    Single home of the locate → full_path → cleanup grammar for scripted
    consumers (stash copy, recovery, dashboards). FileResolver.url() has
    its own per-proto variant for worker fcl — kept separate because its
    output must stay byte-identical.
    """
    from .samweb_wrapper import locate_file_strict
    return path_from_sam_locations(filename, locate_file_strict(filename),
                                   prefer_location)


def sam_physical_path_or_none(filename, prefer_location=None):
    """sam_physical_path, but returns None instead of raising when SAM
    has no usable location. For swallow-and-skip consumers (submissions
    verify loop, MCP status). NOT jobdef_lookup.locate_tarball, which
    takes a cnf DEFNAME and raises."""
    try:
        return sam_physical_path(filename, prefer_location)
    except (ValueError, RuntimeError):
        return None


def classify_sam_location(raw: Optional[str]) -> str:
    """Normalize a SAM location string (location / location_type /
    full_path) to the storage system it names: 'enstore', 'dcache', or
    'N/A' for anything else."""
    if not raw:
        return 'N/A'
    if raw.startswith('enstore'):
        return 'enstore'
    if raw.startswith('dcache'):
        return 'dcache'
    return 'N/A'


# Sentinel for first_file: "not supplied" (fetch it) is distinct from
# None ("known to have no files" — skip the fetch).
_UNSET = object()


def infer_dataset_location(dataset_name, first_file=_UNSET) -> str:
    """Normalized storage location (dcache/enstore/N/A) of a dataset from
    its first file's SAM location records. Pass first_file to reuse one
    the caller already fetched. Fail-soft for SAM-raised errors and
    malformed names only: dashboard consumers treat an unknown location
    as 'N/A', not fatal. Anything else propagates."""
    from .samweb_wrapper import first_file_in_definition, locate_file_strict
    from samweb_client.exceptions import Error as SAMError  # type: ignore
    try:
        if first_file is _UNSET:
            first_file = first_file_in_definition(dataset_name)
        if not first_file:
            return 'N/A'
        for entry in locate_file_strict(first_file):
            loc = entry.get('location') or entry.get('location_type')
            if loc:
                return classify_sam_location(loc)
            full_path = entry.get('full_path')
            if full_path:
                return classify_sam_location(full_path)
    except (SAMError, ValueError) as e:
        print(f"Warning: infer_dataset_location failed for {dataset_name}: {e}",
              file=sys.stderr)
    return 'N/A'


# ---------------------------------------------------------------------------
# Existence probes
# ---------------------------------------------------------------------------

_gfal2_ctx = None


def resilient_file_exists(pnfs_path: str) -> bool:
    """Check if a resilient /pnfs/ file exists via gfal2 xrootd.

    gfal2 gives reliable xrootd access on both interactive and grid
    worker nodes (no POSIX dCache required). Returns False if gfal2 is
    unavailable or the stat fails, so the caller falls through to SAM.

    The context is created once and reused — creation loads plugins and
    dominates the cost of a per-file stat (a resilient mixing job checks
    ~90 files).
    """
    global _gfal2_ctx
    xroot_url = pnfs_path.replace('/pnfs/', XROOT_STAT_PREFIX, 1)
    try:
        if _gfal2_ctx is None:
            import gfal2
            _gfal2_ctx = gfal2.creat_context()
        _gfal2_ctx.stat(xroot_url)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class FileResolver:
    """Resolve Mu2e filenames to physical paths / read URLs for a fixed
    (inloc, proto) pair — the per-jobdef configuration jobfcl runs with.

    locate() and url() reproduce the historical Mu2eJobFCL behavior
    exactly (the worker's inner loop is production-critical):
    - dir:<path>  → literal join, no existence check
    - stash       → CVMFS path if present, else SAM fallback
    - resilient   → /pnfs path if gfal2-stat succeeds, else SAM fallback
    - disk/tape   → SAM locate, preferring the requested location_type
    """

    def __init__(self, inloc: str = 'tape', proto: str = 'file'):
        self.inloc = inloc
        self.proto = proto
        # filename -> SAM locations, filled by prefetch(); misses fall
        # through to a per-file locate, so error semantics are unchanged.
        self._location_cache = {}

    def _sam_always_used(self) -> bool:
        """True when locate() goes to SAM for every file: non-dir:,
        non-stash, non-resilient inloc (those probe CVMFS/gfal2 first),
        with a proto that needs a physical path."""
        return (not self.inloc.startswith('dir:')
                and self.inloc not in ('stash', 'resilient')
                and self.proto in ('file', 'root'))

    def prefetch(self, filenames) -> None:
        """Batch-locate `filenames` in one SAM round-trip (vs one per file
        — a mixing job resolves ~90). Best-effort: on failure the cache
        stays empty and per-file resolution proceeds as before. No-op for
        inlocs that don't deterministically hit SAM, so e.g. fully
        resilient jobs don't pay a SAM call they never made."""
        if not self._sam_always_used():
            return
        todo = [f for f in filenames if f not in self._location_cache]
        if not todo:
            return
        from .samweb_wrapper import locate_files_strict
        try:
            result = locate_files_strict(todo)
        except (ValueError, RuntimeError, KeyError):
            return
        if not isinstance(result, dict):
            return
        self._location_cache.update(
            {fname: locs for fname, locs in result.items()
             if isinstance(locs, list)})

    def locate(self, filename: str) -> str:
        """Physical path for a file (no protocol formatting)."""
        if self.inloc.startswith('dir:'):
            local_dir = self.inloc[4:].rstrip('/')
            return f"{local_dir}/{filename}"

        # No SAM involved; falls back to SAM lookup if not found on stash.
        if self.inloc == 'stash':
            stash_path = stash_read_path(filename)
            if os.path.exists(stash_path):
                return stash_path

        if self.inloc == 'resilient':
            res_path = resilient_path(filename)
            if resilient_file_exists(res_path):
                return res_path

        return self._locate_via_sam(filename)

    def _locate_via_sam(self, filename: str) -> str:
        locations = self._location_cache.get(filename)
        if locations is None:
            from .samweb_wrapper import locate_file_strict
            try:
                locations = locate_file_strict(filename)
            except Exception as e:
                raise ValueError(f"Could not locate file: {filename}: {e}")

        if not locations:
            raise ValueError(f"Could not locate file: {filename}")

        # Prefer the requested location type (disk/tape), else first.
        preferred = [loc for loc in locations
                     if loc.get('location_type') == self.inloc]
        selected = preferred[0] if preferred else locations[0]

        path = selected.get('full_path', '')
        if not path:
            raise ValueError(f"Could not determine path for file: {filename}")
        return path

    def url(self, filename: str) -> str:
        """Read path/URL for a file, formatted per the resolver's proto."""
        # Stash paths are always plain CVMFS, ignoring proto. If the file
        # fell back to SAM, apply the root protocol below instead.
        if self.inloc == 'stash':
            path = self.locate(filename)
            if path.startswith(stash_read_root()):
                return path
            physical_path = path
        elif self.inloc == 'resilient':
            # No CVMFS mirror for resilient disk — always use xrootd.
            physical_path = self.locate(filename)
        elif self.proto == 'file':
            return self.locate(filename)
        elif self.proto != 'root':
            return filename
        else:
            physical_path = self.locate(filename)

        clean_path = remove_storage_prefix(physical_path)

        # Strip a trailing location suffix like (2290@fm4794l8), if any.
        clean_path = _LOCATION_SUFFIX_RE.sub('', clean_path)

        if not clean_path.endswith(filename):
            clean_path = clean_path + '/' + filename

        if clean_path.startswith('/pnfs/'):
            return xroot_read_url(clean_path)

        raise ValueError(
            f"Error: root protocol requested but a file pathname does not start with /pnfs: {clean_path}"
        )
