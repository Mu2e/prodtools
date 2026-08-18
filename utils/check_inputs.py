"""Pre-flight verification of a campaign's input files.

See wiki/pages/2026-07-21-input-preflight-check.md.
Read-only: reports problems, never remediates. Blocks (exit 2) when any
input is unreadable so a slice of jobs is not launched to die in bulk.
"""
import argparse
import os
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

# Allow running as a script (bin/check_inputs execs `python3 utils/check_inputs.py`,
# which puts utils/ on sys.path, not the repo root). Matches submit.py/submissions.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.jobquery import Mu2eJobPars
from utils.job_common import Mu2eName, sha256_file
from utils.jobdesc import code_of
from utils.file_resolver import resilient_path, infer_dataset_location
# NB: utils.samweb_wrapper (→ samweb_client) is imported lazily inside
# check_inputs, so `--help` and the unit tests can load this module
# without the Mu2e environment on PATH.


@dataclass(frozen=True)
class Problem:
    dataset: str
    filename: str
    kind: str      # 'truncated' | 'missing' | 'nearline' | 'query_error' | 'code_mismatch'
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
    dataset. Primary = tbs.inputs + tbs.samplinginput (the resampler's
    primary input, routed like any other primary — tape/disk locality,
    never the resilient size check). Pileup = tbs.auxin. Frozen in the
    tarball — no per-index reconstruction."""
    jp = Mu2eJobPars(tarball_path)
    tbs = jp.json_data.get('tbs', {})
    primary = _group_by_dataset(_section_files(tbs, 'inputs')
                                + _section_files(tbs, 'samplinginput'))
    auxin = _group_by_dataset(_section_files(tbs, 'auxin'))
    return (primary, auxin)


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
    equals the SAM-recorded size. Returns a list of Problems.

    Fails closed: if the SAM size lookup itself raises (e.g. a SAM
    outage), every file in this dataset becomes a query_error Problem
    rather than letting the exception escape the enqueue gate."""
    try:
        expected = sam_sizes(dataset)      # {filename: int}
    except Exception as e:
        return [Problem(dataset, f, 'query_error',
                        f'SAM size lookup failed: {e}') for f in files]
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


_DISK_LOCS = ('disk', 'scratch')

# Files are looked up one at a time (mdh.query_dcache is per-file), so a
# dataset spread over several areas resolves correctly. Threads hide the
# per-file HTTPS round-trip; the calls are independent reads.
_LOCALITY_WORKERS = 16
_QUERY_ATTEMPTS = 3       # transport retries per area (not for 404s)
_QUERY_BACKOFF = 0.5      # seconds, multiplied by attempt number


def _file_locality(client, mdh_loc, filename, attempts=_QUERY_ATTEMPTS):
    """Locality of one file, searching `mdh_loc` first then the disk areas.

    A file absent from the tape area but present on disk/persistent is
    ONLINE by construction: those areas have no tape copy, so there is
    nothing to stage and no recall to avoid. Only a file found in NO area
    is MISSING.

    Transport failures are retried: under concurrency a transient HTTPS
    error would otherwise fail the gate closed and block a whole campaign
    over a blip. A 404 (RuntimeError) is definitive and never retried.
    """
    for loc in (mdh_loc,) + tuple(l for l in _DISK_LOCS if l != mdh_loc):
        for attempt in range(attempts):
            try:
                info = client.query_dcache(filename, location=loc)
            except RuntimeError:
                break             # 404 in this area — try the next area
            except Exception:
                if attempt + 1 == attempts:
                    return 'ERROR'    # persistent failure: fail closed
                time.sleep(_QUERY_BACKOFF * (attempt + 1))
                continue
            state = (info or {}).get('fileLocality')
            return state if state in _LOCALITY_TOKENS else 'ERROR'
    return 'MISSING'


def _default_locality(mdh_loc, filenames):
    """{filename: state} via the mdh Python API, one lookup per file.

    Uses `mdh.MdhClient.query_dcache` rather than shelling out to
    `mdh query-dcache`: the CLI aborts at the FIRST file absent from the
    queried area, so one persistent-resident file in a tape dataset
    truncated the output and forced a fail-closed ERROR for every file —
    including thousands already confirmed ONLINE_AND_NEARLINE.
    """
    filenames = list(filenames)
    try:
        import mdh
        client = mdh.MdhClient()
    except Exception:
        return {f: 'ERROR' for f in filenames}   # fail closed

    result = {}
    with ThreadPoolExecutor(max_workers=_LOCALITY_WORKERS) as pool:
        futures = {pool.submit(_file_locality, client, mdh_loc, f): f
                   for f in filenames}
        for fut in as_completed(futures):
            f = futures[fut]
            try:
                result[f] = fut.result()
            except Exception:
                result[f] = 'ERROR'
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
                 sam_sizes=None,
                 disk_size=_default_disk_size,
                 locality=_default_locality,
                 dataset_location=infer_dataset_location):
    """Verify a campaign's inputs are readable. Returns (ok, problems).

    Pileup (tbs.auxin) staged to resilient is checked by direct size vs
    SAM (mdh cannot see resilient); everything else — the primary, and
    pileup under a non-resilient inloc — is checked by tape/disk locality.
    Read-only: never remediates. Callers exit 2 when ok is False.

    sam_sizes defaults to the real SAM size lister, imported lazily so
    this module loads without the Mu2e environment (for `--help`/tests);
    tests inject their own callable.
    """
    if sam_sizes is None:
        from utils.samweb_wrapper import file_sizes_in_dataset
        sam_sizes = file_sizes_in_dataset
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


def check_code_tarball(entry, cnf_path):
    """Verify the entry's code tarball is still the one the cnf was
    built against. Returns (ok, problems), same shape as check_inputs.

    Deliberately NOT folded into check_inputs: that function means one
    thing — input-data residency — and this is a different question
    about a different artifact.

    Sidecar delivery means the build's bytes are not in the cnf, so
    without this gate a rebuilt or replaced tarball would ship silently
    and the campaign's outputs would carry provenance that is simply
    wrong. mu2eprodsys binds nothing here; we can, cheaply.

    Hashing ~1 GB costs a few seconds, negligible beside the RCDS
    publish that follows.
    """
    code = code_of(entry)
    try:
        ref = Mu2eJobPars(cnf_path).json_data.get('code_ref')
    except (tarfile.TarError, FileNotFoundError, OSError, ValueError) as e:
        return (False, [Problem(
            dataset='code', filename=cnf_path, kind='query_error',
            detail=f"could not read cnf {cnf_path}: {e}")])

    if code is None and ref is None:
        return (True, [])
    if code is None or ref is None:
        return (False, [Problem(
            dataset='code', filename=str(code or cnf_path),
            kind='code_mismatch',
            detail=("entry and cnf disagree about code mode: "
                    f"entry code={code!r}, cnf code_ref="
                    f"{'present' if ref else 'absent'}. Rebuild the cnf "
                    f"from the config you are enqueueing."))])
    if not os.path.isfile(code):
        return (False, [Problem(
            dataset='code', filename=code, kind='missing',
            detail="code tarball named by the entry no longer exists")])

    digest, size = sha256_file(code)
    if digest != ref.get('sha256'):
        return (False, [Problem(
            dataset='code', filename=code, kind='code_mismatch',
            detail=(f"sha256 {digest[:12]} does not match the cnf's "
                    f"code_ref {str(ref.get('sha256'))[:12]} "
                    f"({size} bytes now, {ref.get('size')} at build). "
                    f"Rebuild the cnf, or point the entry at the "
                    f"original tarball."))])
    return (True, [])


def format_report(tarball_path, problems):
    """Human-readable report for one tarball, grouped by dataset."""
    lines = [f"=== {os.path.basename(tarball_path)}"]
    if not problems:
        lines.append("  OK: all inputs present, sized, and staged")
        return "\n".join(lines)
    by_ds = {}
    for p in problems:
        by_ds.setdefault(p.dataset, []).append(p)
    for ds in sorted(by_ds):
        lines.append(f"  {ds}: {len(by_ds[ds])} problem(s)")
        for p in by_ds[ds]:
            lines.append(f"    [{p.kind}] {p.filename}: {p.detail}")
    return "\n".join(lines)


def main(argv=None):
    """CLI: check one or more cnf tarballs. Returns 0 (all clean) or 2."""
    ap = argparse.ArgumentParser(
        description="Pre-flight check that a campaign's inputs are readable "
                    "(resilient pileup present+sized, tape inputs staged). "
                    "Read-only; run /prestage to fix NEARLINE inputs.")
    ap.add_argument('--inloc', default='resilient',
                    help="input location the jobs read from (default: "
                         "resilient, the mixing default)")
    ap.add_argument('tarballs', nargs='+', help="cnf.*.tar file(s)")
    args = ap.parse_args(argv)

    worst = 0
    for tb in args.tarballs:
        ok, problems = check_inputs(tb, args.inloc)
        print(format_report(tb, problems))
        if not ok:
            worst = 2
    return worst


if __name__ == '__main__':
    sys.exit(main())
