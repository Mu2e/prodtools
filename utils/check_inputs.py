"""Pre-flight verification of a campaign's input files.

See docs/superpowers/specs/2026-07-21-input-preflight-check-design.md.
Read-only: reports problems, never remediates. Blocks (exit 2) when any
input is unreadable so a slice of jobs is not launched to die in bulk.
"""
import os
import subprocess
from dataclasses import dataclass

from utils.jobquery import Mu2eJobPars
from utils.job_common import Mu2eName
from utils.file_resolver import resilient_path, infer_dataset_location
from utils.samweb_wrapper import file_sizes_in_dataset


@dataclass(frozen=True)
class Problem:
    dataset: str
    filename: str
    kind: str      # 'truncated' | 'missing' | 'nearline' | 'query_error'
    detail: str


def _section_files(tbs, section):
    """Flatten the file lists of one tbs section (inputs/auxin).

    Each entry is `[merge_factor, [file, ...]]`; the file list is value[1].
    """
    files = []
    for value in tbs.get(section, {}).values():
        if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], list):
            files.extend(value[1])
    return files


def _group_by_dataset(files):
    """Group filenames by their dataset (tier.owner.desc.dsconf.art),
    order-preserving and deduplicated."""
    out = {}
    for f in dict.fromkeys(files):          # dedup, preserve order
        ds = str(Mu2eName.parse(f).with_extension('art').dataset)
        out.setdefault(ds, []).append(f)
    return out


def split_inputs(tarball_path):
    """(primary_by_ds, auxin_by_ds): distinct input files grouped by
    dataset, from the tarball's tbs.inputs (primary) and tbs.auxin
    (pileup). Frozen in the tarball — no per-index reconstruction."""
    jp = Mu2eJobPars(tarball_path)
    tbs = jp.json_data.get('tbs', {})
    return (_group_by_dataset(_section_files(tbs, 'inputs')),
            _group_by_dataset(_section_files(tbs, 'auxin')))


def _default_disk_size(pnfs_path):
    """Actual size of a resilient/disk file, or None if absent. Resilient
    is a flat /pnfs path, POSIX-statable on interactive nodes; stat does
    not trigger a tape recall."""
    try:
        return os.path.getsize(pnfs_path)
    except OSError:
        return None


def check_resilient(dataset, files, sam_sizes, disk_size):
    """Verify pileup files staged to resilient: each present AND its size
    equals the SAM-recorded size. Returns a list of Problems."""
    expected = sam_sizes(dataset)          # {filename: int}
    problems = []
    for f in files:
        path = resilient_path(f)
        actual = disk_size(path)
        if actual is None:
            problems.append(Problem(dataset, f, 'missing',
                                    f'absent from resilient: {path}'))
        elif f not in expected:
            problems.append(Problem(dataset, f, 'query_error',
                                    f'no SAM size for {f}'))
        elif actual != expected[f]:
            problems.append(Problem(dataset, f, 'truncated',
                                    f'{actual} bytes on disk, SAM expects '
                                    f'{expected[f]}'))
    return problems


_LOC_TO_MDH = {'enstore': 'tape', 'dcache': 'disk'}
_LOCALITY_TOKENS = ('ONLINE', 'NEARLINE', 'ONLINE_AND_NEARLINE')


def _default_locality(mdh_loc, filenames):
    """{filename: state} via `mdh query-dcache -o -l <mdh_loc>`.

    stdout: one locality token per FOUND file, in input order.
    stderr: 'Error: File not found in dCache: <path>' per missing file.
    Reconcile missing files by basename, map the rest positionally, and
    fail closed (all ERROR) on any count mismatch or subprocess failure.
    """
    filenames = list(filenames)
    cmd = ['mdh', 'query-dcache', '-o', '-l', mdh_loc] + filenames
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (subprocess.SubprocessError, OSError):
        return {f: 'ERROR' for f in filenames}

    missing = set()
    for line in proc.stderr.splitlines():
        if 'File not found in dCache' in line:
            missing.add(os.path.basename(line.split('dCache:')[-1].strip()))

    states = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    found = [f for f in filenames if f not in missing]
    if len(states) != len(found):
        return {f: 'ERROR' for f in filenames}   # fail closed

    result = {f: 'MISSING' for f in missing}
    for f, s in zip(found, states):
        result[f] = s if s in _LOCALITY_TOKENS else 'ERROR'
    return result


def check_tape(dataset, files, locality, dataset_location):
    """Verify tape/persistent inputs are readable without a tape recall.
    NEARLINE (evicted) → block with a /prestage hint. Returns Problems."""
    loc = dataset_location(dataset)
    mdh_loc = _LOC_TO_MDH.get(loc)
    if mdh_loc is None:
        return [Problem(dataset, f, 'query_error',
                        f'unknown storage location {loc!r} for {dataset}')
                for f in files]
    states = locality(mdh_loc, files)
    problems = []
    for f in files:
        st = states.get(f, 'ERROR')
        if st in ('ONLINE', 'ONLINE_AND_NEARLINE'):
            continue
        if st == 'NEARLINE':
            problems.append(Problem(dataset, f, 'nearline',
                                    f'not staged (NEARLINE); run '
                                    f'/prestage {dataset}'))
        elif st == 'MISSING':
            problems.append(Problem(dataset, f, 'missing',
                                    f'absent from dCache {mdh_loc}'))
        else:
            problems.append(Problem(dataset, f, 'query_error',
                                    f'locality query failed for {f}'))
    return problems


def check_inputs(tarball_path, inloc, *,
                 sam_sizes=file_sizes_in_dataset,
                 disk_size=_default_disk_size,
                 locality=_default_locality,
                 dataset_location=infer_dataset_location):
    """Verify a campaign's inputs are readable. Returns (ok, problems).

    Pileup (tbs.auxin) staged to resilient is checked by direct size vs
    SAM (mdh cannot see resilient); everything else — the primary, and
    pileup under a non-resilient inloc — is checked by tape/disk locality.
    Read-only: never remediates. Callers exit 2 when ok is False.
    """
    primary, auxin = split_inputs(tarball_path)
    problems = []
    for ds, files in auxin.items():
        if inloc == 'resilient':
            problems += check_resilient(ds, files, sam_sizes, disk_size)
        else:
            problems += check_tape(ds, files, locality, dataset_location)
    for ds, files in primary.items():
        problems += check_tape(ds, files, locality, dataset_location)
    return (not problems, problems)
