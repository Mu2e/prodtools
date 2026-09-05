"""Submission-entry (`jobdesc`) accessors.

A jobdesc describes one submission. It is stored in both ledger tables
(`campaigns.entry_json`, `submissions.entry_json`), shipped to the worker as
`ops["jobdesc"]`, and read there by `utils/runmu2e.py`:

    {
        "tarball":  "cnf.mu2e.<desc>.<dsconf>.<index>.tar",   # required
        "outputs":  [ {"dataset": "...", "location": "tape|disk|scratch|outstage"}, ... ],  # required
        "njobs":    <int>,                                    # optional
        "inloc":    "tape|disk|resilient|stash|dir:<path>|none",  # optional, defaults 'none'
        "firstjob": <int>,                                    # optional, defaults 0
    }

`firstjob` windows the entry into the cnf's index space: the entry's njobs
slots run cnf indices [firstjob, firstjob+njobs) instead of [0, njobs).
Since baseSeed = 1 + cnf index, this extends a dataset with fresh seeds
while reusing the existing tarball (statistics expansion of open-ended
resampler/generator cnfs).

These helpers enforce fail-loud access on required fields and the
documented sentinel defaults on optional ones. Use them instead of bare
`entry[...]`/`entry.get(...)` so a malformed jobdesc is caught at the
boundary, not as a downstream crash.
"""

import re
from typing import Optional

from utils.job_common import Mu2eName, sha256_file


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

    Present only on an entry built from a `--code` config; absent means
    the ordinary case, a /cvmfs Musing setup with no tarball shipped.

    Lives on the ENTRY, not the cnf, because a tarball can be moved or
    rebuilt and the entry snapshot is what later slices/recoveries read.
    The cnf keeps only the digest, which is what actually has to stay true.
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
# Single home, shared by all three boundaries that validate an entry:
# json2jobdef (campaign born), submit.enqueue_entry (safety net before a
# campaign is created), and submission_ledger (live campaign edited).
# Three restatements is how `code` reached two of them and not the third.
ENTRY_VALUE_KEYS = ('inloc', 'code', 'prodtools_dir') + RESOURCE_KEYS

# The cvmfs release tree every grid job runs prodtools from. A campaign
# records the CONCRETE version dir (the `current` symlink resolved at
# enqueue), so every slice and every recovery runs the same code and the
# ledger says which. The one other way for worker code to reach a job is
# the explicit dev-checkout opt-in (prodtools_tar_of): a tarball built
# once at enqueue and digest-pinned to the entry — never a fallback.
PRODTOOLS_CVMFS_CURRENT = '/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current'
PRODTOOLS_CVMFS_ROOT = PRODTOOLS_CVMFS_CURRENT.rsplit('/', 1)[0]
PRODTOOLS_WORKER_FILES = ('bin/setup.sh', 'bin/runjob.sh', 'utils/runmu2e.py')


def prodtools_dir_of(entry: dict) -> str:
    """The prodtools release dir this entry's jobs run from. Required:
    an entry without one predates the cvmfs bootstrap and must be given
    one with `submissions set-entry <id> prodtools_dir <dir>
    --include-open-rows` before it can be (re)submitted."""
    import os
    try:
        value = entry['prodtools_dir']
    except KeyError:
        raise ValueError(
            "entry has no prodtools_dir: it predates the cvmfs worker "
            "bootstrap. Set one with `submissions set-entry <campaign> "
            "prodtools_dir <dir> --include-open-rows`, <dir> being a "
            f"release under {os.path.dirname(PRODTOOLS_CVMFS_CURRENT)}") from None
    validate_entry_value('prodtools_dir', value)
    return value


def resolve_prodtools_dir(path: str) -> str:
    """Absolute, symlink-free release dir: `.../current` becomes
    `.../v3.2.0`, so what the ledger records is a version, not a pointer
    that moves under a running campaign."""
    import os
    resolved = os.path.realpath(path)
    validate_entry_value('prodtools_dir', resolved)
    return resolved


def is_cvmfs_prodtools_dir(path: str) -> bool:
    """True for a published release under PRODTOOLS_CVMFS_ROOT — the
    worker can run it in place. Anything else is a checkout that only
    reaches a worker as a shipped tarball (prodtools_tar_of)."""
    return path.startswith(PRODTOOLS_CVMFS_ROOT + '/')


def prodtools_tar_of(entry: dict) -> Optional[str]:
    """Dev-checkout mode, opted into with `json2jobdef --enqueue
    --prodtools-dir <checkout>`: the absolute path of the prodtools
    tarball shipped to every job, or None for a cvmfs release entry.

    The tar was built ONCE at enqueue (utils.submit.bundle_prodtools) and
    its digest recorded as `prodtools_ref`. Re-hashed here, before every
    submit — slice, direct, recovery — so a tar rebuilt or replaced under
    a live campaign is refused, not shipped with stale provenance. Same
    rule `code_ref` applies to an Offline build."""
    import os
    tar = entry.get('prodtools_tar')
    if tar is None:
        return None
    ref = entry.get('prodtools_ref')
    if not isinstance(ref, dict) or not ref.get('sha256'):
        raise ValueError(
            f"entry has prodtools_tar {tar!r} but no prodtools_ref with a "
            f"sha256; re-enqueue from the checkout")
    if not os.path.isfile(tar):
        raise ValueError(
            f"prodtools_tar {tar!r} no longer exists; re-enqueue from the "
            f"checkout")
    digest, size = sha256_file(tar)
    if digest != ref['sha256']:
        raise ValueError(
            f"prodtools_tar {tar!r} sha256 {digest[:12]} does not match "
            f"the entry's prodtools_ref {ref['sha256'][:12]} ({size} bytes "
            f"now, {ref.get('size')} at enqueue): the tarball changed under "
            f"the campaign. Re-enqueue from the checkout")
    return tar


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


# jobsub_submit's --memory grammar ('2500MB' in jobsub_argv.DEFAULT_MEMORY,
# '4000MB' in submissions.RECOVERY_MEMORY). Anchored so 'lots' and
# '3000 MB' are rejected here rather than passed through to fail at submit
# time. Shared by memory and disk — both take a jobsub size string.
_SIZE_RE = re.compile(r'^\d+(MB|GB)$')
_LIFETIME_RE = re.compile(r'^\d+[smhd]$')

# inloc forms utils/file_resolver.py actually accepts. 'scratch' is one:
# FileResolver computes a path under the scratch dataset root, and
# jobsub_argv._LOCATION_DEFAULT_PROTOCOL carries a protocol for it.
# EXAMPLES.md has always documented it.
INLOC_SIMPLE = ('tape', 'disk', 'scratch', 'resilient', 'stash', 'none')


def is_dir_inloc(inloc):
    """True for the local-dir inloc shape (`dir:<path>`).

    Names files on a mounted filesystem never declared to SAM (chained
    intermediate outputs, cvmfs data files), so every SAM-keyed lookup —
    dataset queries, locality checks, parentage tracking — must be
    skipped for it, not attempted-and-failed.

    The single home of the `dir:` test: json2jobdef, check_inputs,
    file_resolver, runmu2e and jobsub_argv all route through here (and
    through dir_inloc_path for the path). Do not hand-roll
    `startswith('dir:')` or `inloc[4:]` at a call site.
    """
    return isinstance(inloc, str) and inloc.startswith('dir:')


def dir_inloc_path(inloc):
    """The filesystem path a `dir:` inloc names."""
    return inloc[len('dir:'):]

# Where a job's outputs may go. The first three are pushOutput actions
# (Util/pushOutput.py validActions) — each copies to a dataset path AND
# declares the file to SAM.
#
# 'outstage' is ours, not pushOutput's: the worker copies the file to
# `$MU2EGRID_WFOUTSTAGE/$CLUSTER/$PROCESS` and declares nothing, for test
# and study runs whose output should not enter SAM. pushOutput has no such
# mode — it sets `dosam = True` unconditionally (pushOutput.py:268), and
# its 'scratch' action is a fully declared dataset that merely lives there.
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

    Single owner of the value grammar, called from BOTH boundaries where a
    value enters the system: `json2jobdef.validate_required_fields` (campaign
    born, from the build config) and `submission_ledger.set_campaign_entry_key`
    (live campaign edited). Two validators would let an operator enqueue a
    spelling that `set-entry` refuses.

    Checked here rather than at submit time, because an unparseable value
    would otherwise sit in the entry looking applied and only surface a
    tick later — a jobsub_submit rejection for the resource keys, or worse,
    a SILENT SAM fallback for a misspelled inloc that reads as a working
    campaign with the wrong provenance.

    Keys other than the ones it knows are ignored, not rejected: an entry
    legitimately carries tarball, outputs, njobs and friends.
    """
    if key not in ('inloc', 'code', 'prodtools_dir') + RESOURCE_KEYS:
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
        # Absolute only: submit host and local runner resolve this path from
        # different working directories, so a relative one would silently
        # mean different files to each. No suffix rule — jobdef.
        # validate_code_tarball checks the bzip2 magic instead.
        if not value.startswith('/'):
            raise ValueError(
                f"code must be an absolute path, got {value!r}")
    elif key == 'prodtools_dir':
        # A release tree, checked on the submit host (cvmfs is mounted
        # there too). Missing worker files here means a whole cluster of
        # setup-phase exit 1s on the grid.
        import os
        if not value.startswith('/'):
            raise ValueError(
                f"prodtools_dir must be an absolute path, got {value!r}")
        missing = [f for f in PRODTOOLS_WORKER_FILES
                   if not os.path.isfile(os.path.join(value, f))]
        if missing:
            raise ValueError(
                f"prodtools_dir {value!r} is not a prodtools release: "
                f"missing {', '.join(missing)}")
        # Releases up to v3.2.0 have a runjob.sh that untars a dropbox
        # tarball nothing ships any more; every job would die in setup.
        with open(os.path.join(value, 'bin', 'runjob.sh')) as fh:
            if 'MU2EGRID_PRODTOOLS_DIR' not in fh.read():
                raise ValueError(
                    f"prodtools_dir {value!r} predates the cvmfs worker "
                    f"bootstrap (its bin/runjob.sh does not read "
                    f"MU2EGRID_PRODTOOLS_DIR); publish a newer release "
                    f"and pin that")
