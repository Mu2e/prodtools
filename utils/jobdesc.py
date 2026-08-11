"""Submission-entry (`jobdesc`) accessors.

A jobdesc describes one submission. It is stored in both ledger tables
(`campaigns.entry_json`, `submissions.entry_json`), shipped to the
worker as `ops["jobdesc"]`, and read there by `utils/runmu2e.py`:

    {
        "tarball":  "cnf.mu2e.<desc>.<dsconf>.<index>.tar",   # required
        "outputs":  [ {"dataset": "...", "location": "tape|disk|scratch"}, ... ],  # required
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
