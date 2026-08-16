"""Submission-entry (`jobdesc`) accessors.

A jobdesc describes one submission. It is stored in both ledger tables
(`campaigns.entry_json`, `submissions.entry_json`), shipped to the
worker as `ops["jobdesc"]`, and read there by `utils/runmu2e.py`:

    {
        "tarball":  "cnf.mu2e.<desc>.<dsconf>.<index>.tar",   # required
        "outputs":  [ {"dataset": "...", "location": "tape|disk|scratch|outstage"}, ... ],  # required
        "njobs":    <int>,                                    # optional
        "inloc":    "tape|disk|resilient|stash|dir:<path>|none",  # optional, defaults 'none'
        "firstjob": <int>,                                    # optional, defaults 0
    }

`firstjob` windows the entry into the cnf's index space: the entry's
njobs slots run cnf indices [firstjob, firstjob+njobs) instead of
[0, njobs). Since baseSeed = 1 + cnf index, this is the mechanism for
extending a dataset with fresh seeds while reusing the existing
tarball (statistics expansion of open-ended resampler/generator cnfs).

These helpers enforce fail-loud access on the required fields and the
documented sentinel defaults on the optional ones. Use them instead of
bare `entry[...]` or `entry.get(...)` so a malformed jobdesc is caught
at the boundary, not as a downstream crash.
"""

import re
from typing import Optional

from utils.job_common import Mu2eName


def tarball_of(entry: dict) -> str:
    """Return the cnf tarball name. Fail loud if missing or not a cnf tarball."""
    if "tarball" not in entry:
        raise ValueError("map entry missing required field: 'tarball'")
    name = entry["tarball"]
    try:
        n = Mu2eName.parse(name)
    except ValueError as exc:
        raise ValueError(f"map entry 'tarball' is not a valid Mu2e name: {name!r}: {exc}")
    if not n.is_tarball:
        raise ValueError(f"map entry 'tarball' is not a cnf tarball: {name!r}")
    return name


def outputs_of(entry: dict) -> list:
    """Return the outputs list. Fail loud if missing."""
    if "outputs" not in entry:
        raise ValueError("map entry missing required field: 'outputs'")
    return entry["outputs"]


def njobs_of(entry: dict, default: Optional[int] = None) -> Optional[int]:
    """Return njobs, or `default` if absent.

    njobs is informational at the submission-map layer (the authoritative count
    comes from the cnf tarball at submission time). Pass an explicit
    default at the call site for diagnostic or dry-run paths.
    """
    return entry.get("njobs", default)


def inloc_of(entry: dict, default: str = "none") -> str:
    """Return inloc, defaulting to the documented 'none' sentinel."""
    return entry.get("inloc", default)


def code_of(entry, default=None):
    """Absolute path to this entry's code tarball, or `default`.

    Present only on an entry built from a `--code` config. Its absence
    means the ordinary case: the cnf names a /cvmfs Musing setup and no
    tarball travels with the job.

    The path lives on the ENTRY rather than in the cnf because a tarball
    can be moved or rebuilt, and because the entry snapshot is what
    later slices and recoveries read. The cnf keeps the digest instead,
    which is what actually has to stay true.
    """
    return entry.get('code', default)


def firstjob_of(entry: dict) -> int:
    """Return the entry's cnf-index window start (default 0).

    Fail loud on a malformed value — a silently-ignored firstjob would
    re-run cnf indices [0, njobs) and duplicate physics (baseSeed = 1 + index).
    """
    firstjob = entry.get("firstjob", 0)
    if isinstance(firstjob, bool) or not isinstance(firstjob, int):
        raise ValueError(f"map entry 'firstjob' must be an integer, got {firstjob!r}")
    if firstjob < 0:
        raise ValueError(f"map entry 'firstjob' must be >= 0, got {firstjob}")
    return firstjob


def is_draining(entry: dict) -> bool:
    """True for a draining (input_pattern) entry/campaign/row snapshot.

    The single-owner kind discriminator for the direct backend: a
    draining entry has `input_pattern` and no index space (no njobs/
    firstjob). Callers must never sniff indices_json content instead.
    """
    return 'input_pattern' in entry


def validate_window(firstjob: int, njobs: Optional[int], capacity: Optional[int]) -> None:
    """Validate a windowed entry (firstjob > 0) against its cnf.

    Single owner of the window rule — called from both the map writer
    (json2jobdef.append_jobdef) and the submit path (_compute_jobset)
    so the two boundaries cannot drift.

    - njobs is required (an open window is meaningless).
    - A closed cnf (capacity > 0) cannot run past its input list;
      capacity 0/None means open-ended — any window is legal.
    """
    if njobs is None:
        raise ValueError("windowed entry (firstjob set) requires an explicit njobs")
    if capacity and firstjob + njobs > capacity:
        raise ValueError(
            f"window [{firstjob}, {firstjob + njobs}) exceeds cnf capacity {capacity}")


RESOURCE_KEYS = ('memory', 'disk', 'expected_lifetime')

# Every entry key whose VALUE validate_entry_value knows how to check.
# Single home, derived by all three boundaries that validate an entry:
# json2jobdef (a campaign is born), submit.enqueue_entry (the safety net
# before a campaign is created), and submission_ledger (a live campaign
# is edited). Three restatements is how `code` reached two of them and
# not the third.
ENTRY_VALUE_KEYS = ('inloc', 'code') + RESOURCE_KEYS


def resources_of(entry: dict) -> dict:
    """Optional per-entry resource requests (subset of RESOURCE_KEYS
    actually present). Values are jobsub-format strings ('4000MB',
    '50GB', '48h'); anything else is a malformed map."""
    res = {}
    for key in RESOURCE_KEYS:
        if key in entry:
            if not isinstance(entry[key], str):
                raise ValueError(
                    f"map entry {key!r} must be a string "
                    f"(jobsub format), got {entry[key]!r}")
            res[key] = entry[key]
    return res


# jobsub_submit's --memory grammar, as the house format uses it
# ('2500MB' in jobsub_argv.DEFAULT_MEMORY, '4000MB' in
# submissions.RECOVERY_MEMORY). Anchored: 'lots' and '3000 MB' are
# rejected rather than passed through to fail at submit time.
# Shared by the memory and disk keys — both take a jobsub size string.
_SIZE_RE = re.compile(r'^\d+(MB|GB)$')
_LIFETIME_RE = re.compile(r'^\d+[smhd]$')

# inloc forms utils/file_resolver.py actually accepts. 'scratch' is one
# of them: FileResolver.locate falls through to a SAM locate preferring
# location_type == inloc, and jobsub_argv._LOCATION_DEFAULT_PROTOCOL
# carries a protocol for it. EXAMPLES.md has always documented it.
INLOC_SIMPLE = ('tape', 'disk', 'scratch', 'resilient', 'stash', 'none')

# Where a job's outputs may go. The first three are pushOutput actions
# (Util/pushOutput.py validActions) — each copies to a dataset path AND
# declares the file to SAM.
#
# 'outstage' is ours, not pushOutput's: the worker copies the file to
# `$MU2EGRID_WFOUTSTAGE/$CLUSTER/$PROCESS` and declares nothing. It is
# for test and study runs whose output should not enter SAM. pushOutput
# offers no such mode — it sets `dosam = True` unconditionally
# (pushOutput.py:268), and its 'scratch' action is a fully declared
# dataset that merely lives on scratch.
OUTSTAGE_LOCATION = 'outstage'
OUTLOC_VALID = ('tape', 'disk', 'scratch', OUTSTAGE_LOCATION)


def validate_outloc(outloc):
    """Reject a malformed `outloc` map at the boundary.

    Nothing checked these values before `outstage` existed, so a
    misspelling ('presistent') survived the whole build, shipped to the
    worker, and failed inside pushOutput after the job had already run.

    Raises ValueError; both callers turn that into a one-line exit.
    """
    if not isinstance(outloc, dict):
        raise ValueError(
            f"outloc must be a dictionary of dataset pattern -> location, "
            f"got {outloc!r}")
    for pattern, location in outloc.items():
        if location not in OUTLOC_VALID:
            raise ValueError(
                f"outloc['{pattern}'] must be one of "
                f"{', '.join(OUTLOC_VALID)}, got {location!r}")


def validate_entry_value(key, value):
    """Reject a malformed entry value at the boundary.

    Single owner of the value grammar, called from BOTH boundaries where
    an entry value enters the system: `json2jobdef.validate_required_fields`
    (where a campaign is born, from the build config) and
    `submission_ledger.set_campaign_entry_key` (where a live campaign is
    edited). Two validators would let an operator enqueue a spelling that
    `set-entry` refuses.

    Written here rather than at submit time because an unparseable value
    would otherwise sit in the entry looking applied and only surface a
    tick later — as a jobsub_submit rejection for the resource keys, or,
    worse, as a SILENT SAM fallback for a misspelled inloc, which reads
    as a working campaign with the wrong provenance.

    Keys other than the ones it knows are ignored, not rejected: an
    entry legitimately carries tarball, outputs, njobs and friends.
    """
    if key not in ('inloc', 'code') + RESOURCE_KEYS:
        return
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string, got {value!r}")
    if key in ('memory', 'disk'):
        if not _SIZE_RE.match(value):
            raise ValueError(
                f"{key} must look like '3000MB' or '4GB', got {value!r}")
    elif key == 'expected_lifetime':
        if not _LIFETIME_RE.match(value):
            raise ValueError(
                f"expected_lifetime must look like '48h' or '3600s', "
                f"got {value!r}")
    elif key == 'inloc':
        if value not in INLOC_SIMPLE and not value.startswith('dir:/'):
            raise ValueError(
                f"inloc must be one of {', '.join(INLOC_SIMPLE)} or "
                f"'dir:/<absolute path>', got {value!r}")
    elif key == 'code':
        # Absolute only: the submit host and the local runner resolve
        # this path from different working directories, and a relative
        # one would silently mean different files to each.
        # No suffix rule — jobdef.validate_code_tarball checks the bzip2
        # magic, so a correctly built tarball is usable under any name.
        if not value.startswith('/'):
            raise ValueError(
                f"code must be an absolute path, got {value!r}")
