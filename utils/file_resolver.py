#!/usr/bin/env python3
"""
file_resolver.py: given a Mu2e filename and an inloc, where does it
live and how do I read it.

This module owns the dCache/CVMFS path grammar (stash, resilient,
dataset dirs, token scopes) and the per-inloc location logic that used
to be spread across jobfcl, stash_utils, datasetFileList, and
jobsub_argv. SAM access goes exclusively through samweb_wrapper.

Import cost: the module itself is pure (no samweb_client / gfal2 at
import time). SAM and gfal2 are imported lazily on first use, so
pure-function consumers (jobsub_argv, unit tests) and dir:-mode
resolution work without the Mu2e ops environment.
"""

import os
import re
import sys
from typing import Optional

from .job_common import Mu2eName, remove_storage_prefix

# xrootd door prefixes. The fcl read URL uses the `xroot://` scheme and
# the gfal2 stat URL uses `root://` — both are accepted by xrootd and
# both predate this module; preserved as-is because worker fcl output
# must stay byte-identical.
XROOT_READ_PREFIX = 'xroot://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/'
XROOT_STAT_PREFIX = 'root://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/'

# SAM location records may carry a trailing "(2290@fm4794l8)" suffix.
# Compiled once — url() runs per input file in the worker inner loop.
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

    Physical layout: tape has no `datasets/` component, unlike
    disk/scratch (and unlike the token-scope paths — see storage_scope).
    Returns '' for unknown locations.
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

    The scope path is the PHYSICAL path with `/pnfs` stripped and nothing
    else changed — upstream mu2ejobsub's `token_request_dirname` is
    exactly `s|^/pnfs/mu2e|/mu2e|`. It therefore has to inherit
    dataset_dir's layout asymmetry: disk and scratch carry a `datasets/`
    component, tape does not.

    This used to insert `datasets/` unconditionally, which made the tape
    scope name a path nothing lives at, so it granted nothing. Writes
    still worked through the separate broad `storage.create:/mu2e`, and
    under the WLCG profile `storage.create` permits upload but NOT
    overwrite or delete — so pushOutput's `recover` path could never
    remove a stale target and 403'd on every retry, permanently
    (CeMLeadingLog 2/418, 2026-07-27). Passing --need-storage-modify at
    all replaces the role's default broad `storage.modify:/mu2e` (which
    is what POMS jobs keep, and why POMS recoveries can overwrite), so a
    wrong path here is a genuine downgrade rather than a no-op.

    Why narrowest: htvault rejects `--need-storage-modify
    /mu2e/scratch/datasets` as too broad with `PermissionError: Unable to
    add 'storage.modify:...' scope given initial scope '[...]'`. Available
    scopes are pre-allocated per (area, tier, owner) tuple.

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
    # Mirror dataset_dir: tape has no `datasets/` component, disk and
    # scratch do. TestStorageScopeCoversPhysicalPath pins the invariant.
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
    """Pick a record from a locate result (preferring `prefer_location`
    when given, else the first record) and return its physical path — the
    record-selection half of sam_physical_path, shared with batch-locate
    consumers (stash copy, dashboards). Raises ValueError when the
    locations list is empty or the record is malformed."""
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
    locate call). Prefers records whose location_type matches
    `prefer_location` when given; otherwise takes the first record.
    Raises ValueError when SAM has no usable location.

    Single home of the locate → full_path → cleanup grammar for scripted
    consumers (stash copy, recovery, dashboards). The worker fcl path has
    its own per-proto variant in FileResolver.url() — kept separate
    because its output must stay byte-identical.
    """
    from .samweb_wrapper import locate_file_strict
    return path_from_sam_locations(filename, locate_file_strict(filename),
                                   prefer_location)


def sam_physical_path_or_none(filename, prefer_location=None):
    """sam_physical_path, but returns None instead of raising when SAM
    has no usable location. For swallow-and-skip consumers (submissions
    verify loop, MCP status) — formerly utils.mkrecovery.locate_tarball.
    NOT the same as jobdef_lookup.locate_tarball, which takes a cnf
    DEFNAME and raises."""
    try:
        return sam_physical_path(filename, prefer_location)
    except Exception:
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


# Sentinel for infer_dataset_location's first_file: "not supplied" (fetch
# it) is distinct from None ("known to have no files" — skip the fetch).
_UNSET = object()


def infer_dataset_location(dataset_name, first_file=_UNSET) -> str:
    """Normalized storage location (dcache/enstore/N/A) of a dataset from
    its first file's SAM location records. Pass first_file to reuse a
    file the caller already fetched. Fail-soft: the dashboard consumers
    treat an unknown location as 'N/A', not fatal."""
    from .samweb_wrapper import first_file_in_definition, locate_file_strict
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
    except Exception as e:
        print(f"Warning: infer_dataset_location failed for {dataset_name}: {e}",
              file=sys.stderr)
    return 'N/A'


# ---------------------------------------------------------------------------
# Existence probes
# ---------------------------------------------------------------------------

_gfal2_ctx = None


def resilient_file_exists(pnfs_path: str) -> bool:
    """Check if a resilient /pnfs/ file exists via gfal2 xrootd.

    Uses gfal2 Python bindings for reliable xrootd access that works on
    both interactive nodes and grid worker nodes (no POSIX dCache
    required). Returns False if gfal2 is unavailable or the stat fails,
    causing the caller to fall through to SAM lookup.

    The gfal2 context is created once and reused — context creation loads
    plugins and dominates the cost of a per-file stat (a resilient mixing
    job checks ~90 files).
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
        # filename -> SAM locations list, filled by prefetch(). Consulted
        # before issuing a per-file locate; misses fall through to the
        # per-file call so error semantics are unchanged.
        self._location_cache = {}

    def _sam_always_used(self) -> bool:
        """True when locate() goes to SAM for every file: non-dir:,
        non-stash, non-resilient inloc (those probe CVMFS/gfal2 first),
        with a proto that needs a physical path at all."""
        return (not self.inloc.startswith('dir:')
                and self.inloc not in ('stash', 'resilient')
                and self.proto in ('file', 'root'))

    def prefetch(self, filenames) -> None:
        """Batch-locate `filenames` in one SAM round-trip (vs one per file
        — a mixing job resolves ~90 files). Best-effort: on any failure the
        cache stays empty and per-file resolution proceeds exactly as
        before. No-op for inlocs that don't deterministically hit SAM, so
        e.g. fully-resilient jobs don't pay a SAM call they never made."""
        if not self._sam_always_used():
            return
        todo = [f for f in filenames if f not in self._location_cache]
        if not todo:
            return
        try:
            from .samweb_wrapper import locate_files_strict
            result = locate_files_strict(todo)
            if isinstance(result, dict):
                for fname, locs in result.items():
                    if isinstance(locs, list):
                        self._location_cache[fname] = locs
        except Exception:
            pass

    def locate(self, filename: str) -> str:
        """Physical path for a file (no protocol formatting)."""
        if self.inloc.startswith('dir:'):
            local_dir = self.inloc[4:].rstrip('/')
            return f"{local_dir}/{filename}"

        # Resolve stash path from filename — no SAM involved.
        # If file not found on stash, fall back to SAM-based lookup.
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

        # Prefer the requested location type (disk/tape); otherwise fall
        # back to the first available location.
        preferred = [loc for loc in locations
                     if loc.get('location_type') == self.inloc]
        selected = preferred[0] if preferred else locations[0]

        path = selected.get('full_path', '')
        if not path:
            raise ValueError(f"Could not determine path for file: {filename}")
        return path

    def url(self, filename: str) -> str:
        """Read path/URL for a file, formatted per the resolver's proto."""
        # Stash paths are always plain CVMFS paths — ignore proto. If the
        # file fell back to SAM, apply the root protocol below.
        if self.inloc == 'stash':
            path = self.locate(filename)
            if path.startswith(stash_read_root()):
                return path
            physical_path = path
        elif self.inloc == 'resilient':
            # Resilient disk has no CVMFS mirror — always use xrootd
            physical_path = self.locate(filename)
        elif self.proto == 'file':
            return self.locate(filename)
        elif self.proto != 'root':
            return filename
        else:
            physical_path = self.locate(filename)

        clean_path = remove_storage_prefix(physical_path)

        # Remove file location suffix like (2290@fm4794l8) if present
        clean_path = _LOCATION_SUFFIX_RE.sub('', clean_path)

        if not clean_path.endswith(filename):
            clean_path = clean_path + '/' + filename

        if clean_path.startswith('/pnfs/'):
            return xroot_read_url(clean_path)

        raise ValueError(
            f"Error: root protocol requested but a file pathname does not start with /pnfs: {clean_path}"
        )
