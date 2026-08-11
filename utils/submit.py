#!/usr/bin/env python3
"""
Direct-submit driver for Mu2e grid jobs (single backend).

Builds the `jobsub_submit` argv directly and ships prodtools as a
dropbox tarball. Worker bootstraps `bin/runjob.sh` →
`utils/runmu2e.py` direct mode → per-job pushOutput. The Phase-1
mu2ejobsub backend was retired 2026-07-19 (spec
2026-07-19-workflow-hardening-design.md): template/direct_input/g4bl
entries and HPC submission run via the upstream mu2ejobsub/mu2eg4bl
CLIs, never through submit_map.

Plans:
- wiki/pages/2026-04-29-remove-poms-from-submit-loop.md (Phase 1, POMS removal)
- wiki/pages/2026-04-30-phase2-direct-jobsub-implementation.md (Phase 2, direct)
"""

import argparse
import fnmatch
import getpass
import json
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.prod_utils import _fetch_file_local
from utils.job_common import (Mu2eName, log_storage_location,
                              expected_outputs_for)
from utils.jobdesc import (RESOURCE_KEYS, tarball_of, outputs_of, njobs_of,
                           inloc_of, firstjob_of, validate_window,
                           resources_of, is_draining, validate_entry_value)
from utils import jobsub_argv as _jobsub_argv
from utils import submission_ledger
from utils.check_inputs import check_inputs, format_report, Problem

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNJOB_SH = REPO_ROOT / 'bin' / 'runjob.sh'
DEFAULT_PRODTOOLS_TAR = Path('/tmp') / f'prodtools-{getpass.getuser()}.tar'


class SubmitOptions(NamedTuple):
    """Everything submit_entry needs beyond the entry itself.

    Replaces the argparse namespace the engine used to reach into, so
    utils/submissions.py can call it directly instead of serialising an
    entry to a temp file and spawning bin/submit_map.

    One object rather than loose keyword arguments because the value is
    threaded on to _reserve_in_ledger, _attach_cluster, _fail_reservation
    and _log_submission — re-expanding it at every hop would be worse
    than the namespace it replaces.

    `first`/`num` are NOT the retired operator flags: submit_slice feeds
    every campaign slice through them (see _compute_jobset).

    `origin` is free-text provenance recorded on the ledger row. Nothing
    dispatches from it; only the MCP status tools echo it back.
    """
    ledger_db: str
    dry_run: bool = False
    first: Optional[int] = None
    num: Optional[int] = None
    indices: Optional[list] = None
    files: Optional[list] = None
    origin: Optional[str] = None
    ledger_parent: Optional[int] = None
    prodtools_tar: Optional[str] = None
    role: Optional[str] = None
    wftop: Optional[str] = None
    wfproject: Optional[str] = None
    memory: Optional[str] = None
    disk: Optional[str] = None
    expected_lifetime: Optional[str] = None


def _ensure_local_tarball(tarball_name):
    """Fetch the cnf tarball into cwd if not already local; return its
    resolved path."""
    tarball_path = Path(tarball_name).resolve()
    if not tarball_path.is_file():
        print(f"Fetching tarball: {tarball_name}")
        _fetch_file_local(tarball_name)
        tarball_path = Path(tarball_name).resolve()
    return tarball_path


def _run_submit(cmd, tarball_name, njobs):
    """Run a submission command, echo its output, and return the result
    dict (tarball/cluster_id/njobs/status)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    raw_output = (result.stdout or '') + (result.stderr or '')

    if result.returncode != 0:
        print(f"ERROR: {cmd[0]} failed with exit code {result.returncode}")
        return {
            'tarball': tarball_name,
            'cluster_id': None,
            'njobs': njobs,
            'status': 'failed',
            'raw_output': raw_output,
        }

    cluster_id = _parse_cluster_id(result.stdout)
    if not cluster_id:
        # jobsub_lite can exit 0 even when its internal condor_submit failed
        # (seen 2026-07-10: condor_vault_storer permission failure under ksu
        # printed "Error: condor_submit exited with failed status code 1" yet
        # jobsub returned 0). A run with no parseable cluster ID is
        # unconfirmed — report it failed rather than claim success. Verify
        # with jobsub_q before resubmitting: a retry after a genuinely
        # partial submit would double-run indices (duplicate seeds).
        print(f"ERROR: {cmd[0]} exited 0 but no cluster ID found in its "
              f"output — treating as failed. Verify with jobsub_q before "
              f"resubmitting.")
        return {
            'tarball': tarball_name,
            'cluster_id': None,
            'njobs': njobs,
            'status': 'failed',
            'raw_output': raw_output,
        }

    print(f"Submitted cluster: {cluster_id}")
    return {
        'tarball': tarball_name,
        'cluster_id': cluster_id,
        'jobsub_id': _parse_jobsub_id(result.stdout),
        'njobs': njobs,
        'status': 'submitted',
        'raw_output': raw_output,
    }


def _parse_cluster_id(stdout):
    """Parse condor cluster ID from mu2ejobsub / jobsub_submit output.

    jobsub_submit prints lines like:
        submitted to cluster 12345678
    or:
        Use job id 12345678.0@jobsub01.fnal.gov to retrieve output
    """
    for line in stdout.splitlines():
        m = re.search(r'submitted.*?cluster\s+(\d+)', line, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r'job\s+id\s+(\d+)\.', line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _parse_jobsub_id(stdout):
    """Full jobsub id (cluster.proc@schedd) from the 'Use job id ...'
    line. The numeric cluster alone can't be drain-checked — jobsub_q
    needs the schedd."""
    m = re.search(r'job\s+id\s+(\d+(?:\.\d+)?@\S+)', stdout, re.IGNORECASE)
    return m.group(1) if m else None


def _ledger_payload(firstjob, jobset, files=None):
    """Absolute cnf indices, or the FILENAME list for a draining batch.
    jobset is entry-relative; the ledger stores absolute (firstjob + i).
    For --indices submissions jobset is already absolute and firstjob is
    0, so the same expression holds."""
    return (list(files) if files is not None
            else [firstjob + i for i in jobset])


def _reserve_in_ledger(entry, firstjob, jobset, options, files=None):
    """Claim this window BEFORE jobsub_submit. Returns the row id.

    RAISES on failure, deliberately: an unrecordable window must not be
    submitted, because nothing would then stop the next tick from
    re-sending the same deterministic payload. This is also what makes a
    self-submission fail fast rather than launching jobs and only then
    discovering it cannot write the ledger.

    options.ledger_db is expected already resolved (see
    _resolve_ledger_db, called once in main()): a DERIVED path arrives
    with its directory already created, an explicit --ledger-db arrives
    exactly as given. Creating it again here would defeat the point of
    resolving once — an explicit path pointing at a missing directory
    must fail here, not get silently mkdir'd.
    """
    return submission_ledger.reserve_submission(
        options.ledger_db,
        tarball=entry['tarball'],
        entry=entry,
        indices=_ledger_payload(firstjob, jobset, files),
        map_path=options.origin,
        parent_id=options.ledger_parent)


def _attach_cluster(row_id, result, options):
    """Fill in the cluster on a reserved row. Never raises: the
    submission already happened, so a ledger failure is reported with
    everything needed to fix the row by hand."""
    if row_id is None:
        return
    try:
        submission_ledger.attach_cluster(
            options.ledger_db, row_id,
            jobsub_id=result.get('jobsub_id'),
            cluster_id=result['cluster_id'])
        print(f"Ledger: row {row_id} attached to cluster "
              f"{result['cluster_id']} in {options.ledger_db}")
    except Exception as e:
        print(f"WARNING: ledger attach failed ({e}) — the submission DID "
              f"go through (cluster {result['cluster_id']}). Row {row_id} "
              f"is still 'submitting'; set it active by hand: "
              f"jobsub_id={result.get('jobsub_id')} db={options.ledger_db}")


def _fail_reservation(row_id, result, options):
    """Close a reserved row after a definitively failed submit. Never
    raises."""
    if row_id is None:
        return
    try:
        submission_ledger.fail_reservation(
            options.ledger_db, row_id,
            f"submit failed (status={result.get('status')}); window NOT "
            f"proven free — check jobsub_q before reusing these indices")
        print(f"Ledger: row {row_id} marked failed in {options.ledger_db}")
    except Exception as e:
        print(f"WARNING: could not mark row {row_id} failed ({e}); it "
              f"remains 'submitting' in {options.ledger_db}")


def _submission_log_path(ledger_db):
    """Dated submission log beside the ledger DB (one file per UTC day,
    plain appends, no rotation — cleanup is manual)."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d')
    return os.path.join(os.path.dirname(ledger_db) or '.',
                        f'submit-{stamp}.log')


def _log_submission(firstjob, jobset, result, options, files=None):
    """Append a human-readable record of a submission attempt — success
    AND failure (failures are exactly what gets debugged). Covers every
    origin (manual, cron slice, recovery resubmit): they all pass
    through here. Never raises: the attempt already happened; a log
    problem must not crash the submit."""
    try:
        if files is not None:
            idx_line = (f"files: {len(files)} "
                        f"[{files[0]} .. {files[-1]}]")
        else:
            absolute = [firstjob + i for i in jobset]
            idx_line = (f"indices: {len(absolute)} absolute "
                        f"[{absolute[0]}..{absolute[-1]}]"
                        if absolute else "indices: none")
        block = '\n'.join([
            f"=== {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"user={getpass.getuser()} status={result['status']}",
            f"origin={options.origin} tarball={result['tarball']}",
            idx_line,
            f"cluster={result['cluster_id']} "
            f"jobsub_id={result.get('jobsub_id')}",
            "--- jobsub output ---",
            result.get('raw_output', '').rstrip(),
            "=== end",
            "",
        ])
        with open(_submission_log_path(options.ledger_db), 'a') as fh:
            fh.write(block + '\n')
    except Exception as e:
        print(f"WARNING: submit-log write failed ({e}) — submission "
              f"outcome unaffected (status={result['status']})")


def _effective_resources(entry, options):
    """Resource precedence: CLI flag > entry key > None (None lets
    jobsub_argv apply its built-in defaults)."""
    res = resources_of(entry)
    return {
        'memory': options.memory or res.get('memory'),
        'disk': options.disk or res.get('disk'),
        'expected_lifetime': (options.expected_lifetime
                              or res.get('expected_lifetime')),
    }


def _snapshot_entry(entry, resources):
    """Entry snapshot for ledger/campaign rows: effective resource
    values merged in, so recoveries and cron slices reproduce what the
    jobs actually ran with (a CLI --memory must not silently downgrade
    to the built-in default on resubmit)."""
    snap = dict(entry)
    for key, val in resources.items():
        if val is not None:
            snap[key] = val
    return snap


def _resolve_ledger_db(opts):
    """Writer ledger path, resolved ONCE in main(). A DEFAULTED (derived)
    path gets its directory created (submission_ledger.ensure_ledger_dir);
    an operator-supplied --ledger-db never does — a typo there must fail
    loudly rather than silently make a stray database."""
    if opts.ledger_db:
        return opts.ledger_db
    return submission_ledger.ensure_ledger_dir(submission_ledger.ledger_for())


def _validate_draining_entry(entry):
    """Shape check for an input_pattern (draining) map entry. Returns an
    error string or None. njobs/firstjob are index-mode concepts — a
    draining campaign has no index space; draining state lives in SAM
    and the submissions rows, never in a cursor."""
    if 'njobs' in entry:
        return "has both input_pattern and njobs — pick one mode"
    if 'firstjob' in entry:
        return "has input_pattern and firstjob — draining has no index space"
    for key in ('tarball', 'inloc', 'outputs'):
        if not entry.get(key):
            return f"draining entry missing required key {key!r}"
    pattern = entry['input_pattern']
    fields = pattern.split('.')
    if len(fields) != 5 or not all(fields):
        return (f"input_pattern {pattern!r} is not a 5-field "
                f"tier.owner.desc.dsconf.ext pattern")
    # An outputs glob that matches the input pattern would make the worker
    # declare the fetched input copy as an output (push_data globs cwd),
    # and pushOutput's orphan recovery then tries to delete the production
    # input at its own dataset path. Heuristic gate (fnmatch of the pattern
    # string, % treated as a literal); the worker also excludes its inputs
    # as the authoritative defense.
    for out in entry['outputs']:
        out_glob = out.get('dataset', '')
        if out_glob and fnmatch.fnmatchcase(pattern, out_glob):
            return (f"outputs dataset glob {out_glob!r} matches "
                    f"input_pattern {pattern!r} — the worker would push "
                    f"input files back to their own dataset; use a "
                    f"tier-specific glob (e.g. 'mcs.*.art')")
    excl = entry.get('exclude_desc', [])
    if not (isinstance(excl, list)
            and all(isinstance(d, str) for d in excl)):
        return "exclude_desc must be a list of desc strings"
    age = entry.get('min_age_minutes', 60)
    if not (isinstance(age, int) and not isinstance(age, bool) and age >= 0):
        return "min_age_minutes must be a non-negative integer"
    if not isinstance(entry.get('prestage', False), bool):
        return "prestage must be true or false"
    return None


def _validate_entry_values(entry):
    """Reject a malformed inloc / resource value in an ENTRY before any
    ledger row exists.

    json2jobdef validates the build config it reads, but this is also
    the boundary check `enqueue_entry` applies before any campaign is
    created, and `json2jobdef --enqueue` is the only remaining caller.

    It matters most for `inloc`: a misspelled location does not fail, it
    degrades. file_resolver.locate finds no such location and falls
    through to SAM, so the campaign runs to completion reading from the
    wrong place.

    The CLI overrides (--memory/--disk/--expected-lifetime) are NOT
    checked here -- they are validated in main(), where they are read.
    Checking the merged result instead would mean re-validating the
    entry's own values on every path that merges.
    """
    for key in ('inloc',) + RESOURCE_KEYS:
        if key in entry:
            try:
                validate_entry_value(key, entry[key])
            except ValueError as e:
                sys.exit(f"submit_map: {e}")


def enqueue_entry(entry, *, ledger_db, slice_size, dry_run=False,
                  resources=None, provenance=None):
    """Register ONE entry as a sliced-submission campaign (cursor 0);
    submit nothing. Returns the new campaign id, or None under dry_run.

    Single owner of the enqueue preflight, shared by `submit_map
    --enqueue` and `json2jobdef --enqueue`: inputs are checked before
    any ledger row is written, so a campaign is never created for a
    tarball with unreadable inputs.

    Nothing has been submitted when this fails, so failures are hard
    errors — but operator-reachable ones (duplicate live campaign, bad
    njobs, DB trouble) exit with a ONE-LINE message, never a traceback.

    sys.exit is retained deliberately: converting submit.py's error
    protocol to exceptions restructures the path that launches every
    production job and belongs in its own change. Both callers are CLIs,
    so inheriting the exit codes is correct.

    `provenance` is free-text recorded as the campaign's map_path. It is
    never dispatched from — only the MCP status tools echo it back.
    """
    resources = resources or {}
    _validate_entry_values(entry)
    if is_draining(entry):
        err = _validate_draining_entry(entry)
        if err:
            sys.exit(f"submit_map: {err}")
        _ensure_local_tarball(tarball_of(entry))
        # No check_inputs: a generic cnf bakes no inputs — the tick
        # gates every batch (residency + settling age) at dispatch.
        snap = _snapshot_entry(entry, resources)
        if dry_run:
            print(f"[DRY RUN] would enqueue draining campaign: "
                  f"{tarball_of(entry)} "
                  f"pattern={entry['input_pattern']} "
                  f"slice={slice_size}")
            return None
        try:
            camp_id = submission_ledger.create_campaign(
                ledger_db, tarball=tarball_of(entry), entry=snap,
                slice_size=slice_size, map_path=provenance)
        except (ValueError, sqlite3.Error) as e:
            sys.exit(f"submit_map: {e}")
        print(f"Enqueued draining campaign {camp_id}: "
              f"{tarball_of(entry)} pattern={entry['input_pattern']} "
              f"slice={slice_size} (db {ledger_db})")
        return camp_id

    tarball_path = _ensure_local_tarball(tarball_of(entry))
    ok, problems = check_inputs(str(tarball_path), inloc_of(entry))
    if not ok:
        print(format_report(str(tarball_path), problems))
        print(f"submit_map: inputs not ready "
              f"({len(problems)} problem(s)) — fix and re-run; "
              f"no campaign created")
        sys.exit(2)
    njobs = njobs_of(entry)
    if njobs is None:
        sys.exit("submit_map: entry has no njobs (generic tarball) — "
                 "a campaign needs a job count to slice")
    if njobs < 1:
        sys.exit(f"submit_map: entry has njobs={njobs} — "
                 f"a campaign needs a positive job count")
    snap = _snapshot_entry(entry, resources)
    if dry_run:
        print(f"[DRY RUN] would enqueue entry: "
              f"{tarball_of(entry)} njobs={njobs} "
              f"slice={slice_size}")
        return None
    try:
        camp_id = submission_ledger.create_campaign(
            ledger_db, tarball=tarball_of(entry), entry=snap,
            slice_size=slice_size, map_path=provenance)
    except (ValueError, sqlite3.Error) as e:
        sys.exit(f"submit_map: {e}")
    print(f"Enqueued campaign {camp_id}: {tarball_of(entry)} "
          f"njobs={njobs} slice={slice_size} (db {ledger_db})")
    return camp_id


def _bundle_prodtools(out_path=DEFAULT_PRODTOOLS_TAR):
    """Tar `utils/` + `bin/` from this repo into a worker-shippable bundle.

    Used by submit_entry: the worker bootstraps `runjob.sh`, which
    extracts this tarball under `$_CONDOR_SCRATCH_DIR/prodtools/` and execs
    `utils/runmu2e.py` from there. Avoids depending on a cvmfs-published
    prodtools version that might not yet contain our changes.

    Skips tarring if `out_path` is already newer than every Python source
    file under utils/ — keeps repeated submissions cheap.
    """
    out = Path(out_path)
    sources = list((REPO_ROOT / 'utils').rglob('*.py')) + \
        list((REPO_ROOT / 'bin').glob('*'))
    if out.is_file():
        out_mtime = out.stat().st_mtime
        if all(s.stat().st_mtime <= out_mtime for s in sources if s.is_file()):
            return out

    print(f"Bundling prodtools → {out}")
    with tarfile.open(out, 'w') as tar:
        for sub in ('utils', 'bin'):
            src_dir = REPO_ROOT / sub
            for f in sorted(src_dir.rglob('*')):
                if not f.is_file():
                    continue
                if '__pycache__' in f.parts or f.suffix == '.pyc':
                    continue
                arcname = Path('prodtools') / f.relative_to(REPO_ROOT)
                tar.add(f, arcname=str(arcname))
    return out


def _read_cnf_facts(tarball_path):
    """One Mu2eJobPars parse per cnf, returning the three facts the direct
    backend needs: (njobs, input_datasets, output_filenames_index0).

    - njobs is authoritative from the cnf, not the submission map (the map field
      can be stale or absent for direct-input mode).
    - output filenames (index 0) feed the per-(area, tier, owner) token
      scope derivation; templates that resolved to a path (`/dev/null`)
      are skipped.
    """
    from utils.jobquery import Mu2eJobPars
    jp = Mu2eJobPars(str(tarball_path))
    out = jp.job_outputs(0) or {}
    return (jp.njobs(), jp.input_datasets(),
            [v for v in out.values() if v and "/" not in v])


def _parse_indices(spec, path):
    """Parse --indices / --indices-file into a sorted unique list of ints.

    Returns None when neither is given. Accepts comma- and/or
    whitespace-separated values; in a file, `#` starts a comment (so
    an index dump that headers each tarball with `# <tarball>` pipes
    straight in).
    """
    if spec and path:
        raise ValueError("--indices and --indices-file are mutually exclusive")
    if spec:
        raw = spec.replace(',', ' ').split()
    elif path:
        raw = []
        for line in Path(path).read_text().splitlines():
            line = line.split('#', 1)[0]
            raw.extend(line.replace(',', ' ').split())
    else:
        return None
    try:
        parsed = {int(x) for x in raw}
    except ValueError as e:
        raise ValueError(f"--indices: not an integer ({e})")
    if not parsed:
        raise ValueError("--indices: no indices given")
    return sorted(parsed)


def _parse_files(path):
    """Parse --files: one Mu2e art filename per line; `#` comments and
    blank lines allowed. Returns a sorted unique list, or None when no
    path was given. Every name must parse as a 6-field Mu2e file name —
    fail loud at the CLI, not on a grid worker."""
    if path is None:
        return None
    names = set()
    for line in Path(path).read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        n = Mu2eName.parse(line)
        if not n.is_file:
            raise ValueError(f"--files: not a Mu2e file name: {line}")
        names.add(line)
    if not names:
        raise ValueError("--files: no filenames given")
    return sorted(names)


def _compute_jobset(options, njobs_total, firstjob=0, entry_njobs=None):
    """Resolve --first/--num/--indices into the list of job indices to submit.

    Indices are entry-relative (PROCESS space, starting at 0) — a windowed
    entry's `firstjob` offset is applied worker-side by `resolve_map_index`
    (the entry ships in ops['jobdesc']), not here. A window is sized by the
    entry's njobs and validated against the cnf capacity (njobs_total,
    0 = open-ended) via jobdesc.validate_window.

    Default: every index 0..size-1 (== mu2ejobsub --all).
    --first N alone: 1 job at index N.
    --first N --num M: indices [N, N+M).
    --indices K1,K2,...: exactly those ABSOLUTE cnf indices (recovery). Only
      valid on a non-windowed entry, because the values ARE the cnf indices
      (the caller ships firstjob=0 so worker-side `local == global`); a
      contiguous window cannot express a scattered set.
    """
    if options.indices is not None:
        if firstjob:
            raise ValueError(
                "--indices takes absolute cnf indices and cannot be combined "
                f"with a windowed entry (firstjob={firstjob}); drop firstjob "
                "from the recovery map entry")
        if options.first is not None or options.num is not None:
            raise ValueError("--indices cannot be combined with --first/--num")
        if options.indices[0] < 0:
            raise ValueError(f"--indices: negative index {options.indices[0]}")
        if njobs_total and options.indices[-1] >= njobs_total:
            raise ValueError(
                f"--indices: {options.indices[-1]} >= cnf capacity {njobs_total}")
        return list(options.indices)
    if firstjob:
        validate_window(firstjob, entry_njobs, njobs_total)
        size = entry_njobs
    else:
        size = njobs_total
    if options.first is None and options.num is None:
        return list(range(size))
    first = options.first or 0
    num = options.num if options.num is not None else 1
    end = min(first + num, size)
    if first < 0 or first >= size or end <= first:
        raise ValueError(
            f"--first {first} --num {num} out of range for jobset size={size}"
        )
    return list(range(first, end))


def _preflight_inputs(entry, tarball_path):
    """Verify a cnf's baked inputs before submitting. Returns
    (ok, problems).

    Mirrors the gate enqueue_entry applies, so the DIRECT path
    (--first/--num and every recovery resubmit) gets it too — it is
    exactly the bulk-death failure check_inputs exists to prevent.
    A draining/generic cnf bakes no inputs and is skipped, the same
    carve-out enqueue_entry makes.
    """
    if is_draining(entry):
        return True, []
    return check_inputs(str(tarball_path), inloc_of(entry))


def submit_entry(entry, idx, options):
    """Submit one entry: build jobsub_submit argv via utils.jobsub_argv,
    ship prodtools as a dropbox tarball, run `runjob.sh` on the worker.

    Returns the same dict shape (tarball/cluster_id/njobs/status).
    """
    tarball_name = tarball_of(entry)
    desc = _jobsub_argv.description_from_tarball(tarball_name)
    files = options.files

    # Tarball must be locally accessible to ship via -f dropbox://.
    # Files mode always needs the REAL cnf (even on a dry run): the
    # output-name mapping (expected_outputs_for) comes from parsing it,
    # so the nonexistent-stand-in shortcut below must not apply.
    if options.dry_run and files is None and not Path(tarball_name).resolve().is_file():
        tarball_path = Path('/tmp') / tarball_name
    else:
        tarball_path = _ensure_local_tarball(tarball_name)

    # njobs from the cnf is authoritative; the map's field is informational.
    # output_filenames feeds the per-(area, tier, owner) token scope derivation
    # so pushOutput can MAKE_PARENT in `/pnfs/mu2e/<area>/datasets/...`.
    if files is not None:
        # Draining batch: one direct-input job per file. A generic cnf
        # has no index capacity — the jobset is positions into the
        # batch. Scope granularity is (area, tier, owner), but the AREA
        # itself is resolved per-output by fnmatching output_filenames
        # against outputs[].dataset globs (output_storage_dirs) — a
        # desc-discriminating glob (e.g. one desc to tape, another to
        # disk) picks a different area per desc. So every distinct desc
        # in the batch must contribute its mapped outputs, not just the
        # first file's (expected_outputs_for is the worker's own
        # substitution, so the names are exact).
        from utils.jobquery import Mu2eJobPars
        jp = Mu2eJobPars(str(tarball_path))
        njobs_total = len(files)
        input_datasets = sorted({str(Mu2eName.parse(f).dataset)
                                 for f in files})
        seen_descs, output_filenames = set(), []
        for f in files:
            d = Mu2eName.parse(f).description
            if d in seen_descs:
                continue
            seen_descs.add(d)
            output_filenames.extend(expected_outputs_for(f, jp))
        firstjob = 0
        jobset = list(range(len(files)))
    elif options.dry_run and not tarball_path.is_file():
        # Capacity stand-in when the cnf isn't inspectable: the window end
        # (== njobs for plain entries), so validate_window never spuriously
        # fails a dry run. --indices addresses cnf indices far past the
        # recovery entry's own njobs, so widen the stand-in to cover them —
        # otherwise the real capacity check below rejects a valid dry run.
        njobs_total = firstjob_of(entry) + njobs_of(entry, default=1)
        if options.indices is not None:
            njobs_total = max(njobs_total, options.indices[-1] + 1)
        input_datasets = []
        output_filenames = []
        firstjob = firstjob_of(entry)
        jobset = _compute_jobset(options, njobs_total, firstjob=firstjob,
                                 entry_njobs=njobs_of(entry))
    else:
        njobs_total, input_datasets, output_filenames = _read_cnf_facts(tarball_path)
        firstjob = firstjob_of(entry)
        jobset = _compute_jobset(options, njobs_total, firstjob=firstjob,
                                 entry_njobs=njobs_of(entry))

    print(f"\n{'='*60}")
    if files is not None:
        print(f"Entry {idx}: {desc} (draining batch of {len(files)})")
        print(f"  tarball: {tarball_name}")
        print(f"  inloc:   {inloc_of(entry)}")
        print(f"  files:   {files[0]} .. {files[-1]}")
    else:
        print(f"Entry {idx}: {desc} (cnf njobs={njobs_total}, submitting {len(jobset)})")
        print(f"  tarball: {tarball_name}")
        print(f"  inloc:   {inloc_of(entry)}")
        if firstjob and jobset:
            print(f"  window:  cnf indices {firstjob + jobset[0]}..{firstjob + jobset[-1]} (firstjob={firstjob})")
        if options.indices is not None and jobset:
            print(f"  indices: {len(jobset)} absolute cnf indices (recovery), "
                  f"{jobset[0]}..{jobset[-1]}")
        print(f"  jobset:  {jobset if len(jobset) <= 10 else f'[{jobset[0]}..{jobset[-1]}] ({len(jobset)} indices)'}")
    print(f"{'='*60}")

    # `--indices` values ARE cnf indices, but the worker reaches a cnf index via
    # resolve_map_index (`local = global + firstjob`, gated on `global <
    # njobs`). So the SHIPPED entry must sit at firstjob=0 and span past the
    # largest index for `local == global` to hold. Only the ops copy is
    # rewritten — the on-disk map keeps its own njobs, so a recovery map
    # never has to store the "bare submit re-runs everything" njobs.
    ops_entry = entry
    if options.indices is not None:
        ops_entry = {**entry, 'firstjob': 0, 'njobs': jobset[-1] + 1}

    # Synthesize ops JSON (jobs[] + inspec + jobdesc) and write to /tmp.
    # /tmp is the same FS jobsub_lite uses for its dropbox staging, so
    # this is fine for both local-test and mu2epro runs.
    ops = _jobsub_argv.build_ops_json(
        entry=ops_entry,
        jobset=jobset,
        input_datasets=input_datasets,
        files=files,
    )
    ops_path = Path('/tmp') / f'ops-{getpass.getuser()}-{desc}-{os.getpid()}.json'
    ops_path.write_text(json.dumps(ops, indent=2) + '\n')
    print(f"Wrote ops JSON: {ops_path}")

    # Bundle prodtools so the worker has our patched runmu2e.py.
    prodtools_tar = _bundle_prodtools(options.prodtools_tar or DEFAULT_PRODTOOLS_TAR)

    # Compute effective resources (CLI flag > entry key > None/builtin).
    resources = _effective_resources(entry, options)

    # Build the jobsub_submit argv. submitter is the effective UNIX user;
    # role auto-defaults to Production for mu2epro per jobsub_argv.role_for_user.
    submitter = getpass.getuser()
    # Token scopes for direct-mode pushOutput (CB1):
    #   - per data output: /mu2e/<area>/datasets/<owner-class>-<tier>/<tier>/<owner>
    #   - per log: same scheme with tier=log, but logs go to persistent disk
    #     regardless of the data location (see log_storage_location), so a
    #     tape campaign needs BOTH a tape data scope and a disk log scope.
    extra_scopes = list(_jobsub_argv.output_storage_dirs(
        output_filenames, outputs_of(entry)))
    if output_filenames:
        log_location = log_storage_location(entry)
        try:
            first_out = Mu2eName.parse(output_filenames[0])
        except ValueError:
            first_out = None
        if first_out is not None and first_out.is_file:
            log_fname = str(first_out.as_tier('log').with_extension('log'))
            log_scope = _jobsub_argv.storage_scope_for_file(log_fname, log_location)
            if log_scope:
                extra_scopes.append(log_scope)

    argv = _jobsub_argv.build_jobsub_argv(
        entry=entry,
        jobset=jobset,
        jobdef_path=str(tarball_path),
        ops_json_path=str(ops_path),
        prodtools_tar_path=str(prodtools_tar),
        worker_script_path=str(DEFAULT_RUNJOB_SH),
        submitter=submitter,
        extra_storage_modify=extra_scopes,
        role=options.role,
        wftop=options.wftop,
        wfproject=options.wfproject,
        disk=resources['disk'],
        memory=resources['memory'],
        expected_lifetime=resources['expected_lifetime'],
    )

    cmd = ['jobsub_submit'] + argv
    print(f"\nCommand: {' '.join(cmd)}")

    if options.dry_run:
        print("[DRY RUN] Not submitting.")
        ops_path.unlink(missing_ok=True)
        return {
            'tarball': tarball_name,
            'cluster_id': None,
            'njobs': len(jobset),
            'status': 'dry_run',
        }

    ok, problems = _preflight_inputs(entry, tarball_path)
    if not ok:
        print(format_report(str(tarball_path), problems))
        raise SystemExit(
            f"input pre-flight FAILED for {tarball_name} — refusing to "
            f"submit. Fix the inputs (or stage them) and retry.")

    row_id = _reserve_in_ledger(_snapshot_entry(entry, resources), firstjob,
                                jobset, options, files=files)
    result = _run_submit(cmd, tarball_name, len(jobset))
    _log_submission(firstjob, jobset, result, options, files=files)
    if result['status'] == 'submitted':
        _attach_cluster(row_id, result, options)
    else:
        _fail_reservation(row_id, result, options)
    return result


def _check_token():
    """Pre-flight token check. Returns True if valid, False otherwise."""
    try:
        result = subprocess.run(
            ['httokendecode'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print("WARNING: httokendecode failed — token may be missing or expired")
            return False
        print("Token check: OK")
        return True
    except FileNotFoundError:
        print("WARNING: httokendecode not found — skipping token check")
        return True


def submit_map(map_path, options):
    """Submit all (or selected) entries from a submission-map JSON.

    Args:
        map_path: path to the submission-map JSON
        options: a SubmitOptions

    Returns:
        list of result dicts (tarball/cluster_id/njobs/status) from
        submit_entry
    """
    with open(map_path) as f:
        entries = json.load(f)

    if not isinstance(entries, list):
        print(f"Error: {map_path} should contain a JSON array")
        sys.exit(1)

    if not entries:
        print(f"Error: {map_path} is empty")
        sys.exit(1)

    if len(entries) != 1:
        print(f"Error: {map_path} must contain exactly one entry "
              f"(got {len(entries)}) — multi-entry maps were removed with "
              f"the map workflow; use json2jobdef --dsconf to enqueue a set")
        sys.exit(1)
    entries_to_submit = [(0, entries[0])]

    # A draining entry has no index space — it cannot be submitted via the
    # ordinary indexed path. --files lets a caller hand it a concrete batch;
    # without it, refuse loudly rather than silently drop into
    # submit_entry with a missing njobs.
    for idx, entry in entries_to_submit:
        if is_draining(entry) and options.files is None:
            print(f"Error: entry {idx} is a draining entry "
                  f"(input_pattern) — use json2jobdef --enqueue "
                  f"(tick-fed) or --files <list>")
            sys.exit(1)

    if options.files is not None:
        if not is_draining(entries_to_submit[0][1]):
            print("Error: --files requires a draining (input_pattern) "
                  "entry")
            sys.exit(1)

    print(f"Map: {map_path}")
    print(f"Entries to submit: {len(entries_to_submit)}")
    print(f"Total jobs: {sum(njobs_of(e, default=0) for _, e in entries_to_submit)}")

    # Pre-flight token check
    if not options.dry_run:
        _check_token()

    submitter = getpass.getuser()
    print(f"Submitter: {submitter}")

    results = []
    for idx, entry in entries_to_submit:
        result = submit_entry(entry, idx, options)
        results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    submitted = [r for r in results if r['status'] == 'submitted']
    failed = [r for r in results if r['status'] == 'failed']
    dry_run = [r for r in results if r['status'] == 'dry_run']

    if dry_run:
        print(f"  Dry run:   {len(dry_run)} entries")
    if submitted:
        print(f"  Submitted: {len(submitted)} entries")
        for r in submitted:
            print(f"    cluster {r['cluster_id']}: {_jobsub_argv.description_from_tarball(r['tarball'])} ({r['njobs']} jobs)")
    if failed:
        print(f"  Failed:    {len(failed)} entries")
        for r in failed:
            print(f"    {_jobsub_argv.description_from_tarball(r['tarball'])}: FAILED")

    return results


def main():
    parser = argparse.ArgumentParser(
        description='Submit Mu2e grid jobs from a submission-map JSON via the direct jobsub backend'
    )
    parser.add_argument('--map', required=True,
                        help='Path to submission-map JSON (e.g., MDC2025-001.json)')
    parser.add_argument('--first', type=int, default=None,
                        help='First job index to submit. With --num '
                             'submits a contiguous range; without --num '
                             'submits one job at this index.')
    parser.add_argument('--num', type=int, default=None,
                        help='Number of consecutive jobs from --first.')
    parser.add_argument('--indices', default=None,
                        help='Comma/space-separated ABSOLUTE cnf indices '
                             'to submit (recovery) — one cluster, one job per '
                             'index. --first/--num can only carve a contiguous '
                             'range; this expresses a scattered set. Requires a '
                             'non-windowed entry (no firstjob).')
    parser.add_argument('--indices-file', default=None,
                        help='File of ABSOLUTE cnf indices, whitespace/'
                             'comma separated; `#` comment lines (e.g. '
                             'per-tarball headers) are ignored.')
    parser.add_argument('--files', default=None,
                        help='File of input art filenames (one per line, '
                             '`#` comments) for a draining '
                             '(input_pattern) entry: one 1:1 direct-'
                             'input job per file. Written by the '
                             'submissions drain tick; also the operator '
                             'path for re-dispatching parked files.')
    parser.add_argument('--ledger-db', default=None,
                        help='Submission-ledger sqlite DB (default: your '
                             'own ledger; for mu2epro that IS the '
                             'production ledger — resolved once in main() '
                             'via _resolve_ledger_db, which also creates '
                             "the default's directory). Every direct "
                             'submission is recorded for the recovery loop '
                             '(`submissions run`).')
    parser.add_argument('--ledger-parent', type=int, default=None,
                        help='Ledger row id this submission '
                             'recovers (set by the recovery loop; chains '
                             'attempt counting).')
    parser.add_argument('--wftop', default=None,
                        help='Outstage top dir (default: '
                             '/pnfs/mu2e/persistent/users for Production, '
                             '/pnfs/mu2e/scratch/users otherwise)')
    parser.add_argument('--wfproject', default=None,
                        help='Workflow project name (default: extracted from tarball dsconf)')
    parser.add_argument('--role', default=None,
                        help='Grid role (default: auto — Production for mu2epro)')
    parser.add_argument('--disk', default=None,
                        help='Disk request (default: 30GB)')
    parser.add_argument('--memory', default=None,
                        help='Memory request (default: 2500MB)')
    parser.add_argument('--expected-lifetime', default=None,
                        help='Expected lifetime (default: 24h)')
    parser.add_argument('--prodtools-tar', default=None,
                        help='Path for the prodtools bundle '
                             f'(default: {DEFAULT_PRODTOOLS_TAR}). Reused if '
                             'newer than every utils/*.py source file.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print the submission command without running it')

    args = parser.parse_args()

    def _parse_or_exit(fn, *fn_args):
        try:
            return fn(*fn_args)
        except (ValueError, OSError) as e:
            print(f"Error: {e}")
            sys.exit(1)

    # Resource flags are checked HERE, where they are read, rather than
    # in enqueue_entry: _snapshot_entry merges them into the campaign's
    # frozen entry, so `--memory "3000 MB"` would otherwise sit in the
    # ledger looking applied and only surface a tick later as a
    # jobsub_submit rejection.
    for _key in RESOURCE_KEYS:
        _val = getattr(args, _key, None)
        if _val is not None:
            _parse_or_exit(validate_entry_value, _key, _val)

    args.indices = _parse_or_exit(_parse_indices, args.indices, args.indices_file)
    args.files = _parse_or_exit(_parse_files, args.files)
    if args.files is not None and (
            args.first is not None or args.num is not None
            or args.indices is not None):
        print("Error: --files cannot be combined with "
              "--first/--num/--indices/--indices-file")
        sys.exit(1)

    # Resolved ONCE here, before anything writes to the ledger: the rest
    # of the flow sees a plain string, already pointed at a directory
    # that exists if it was defaulted.
    args.ledger_db = _resolve_ledger_db(args)

    if not Path(args.map).is_file():
        print(f"Error: map file not found: {args.map}")
        sys.exit(1)

    options = SubmitOptions(
        ledger_db=args.ledger_db,
        dry_run=args.dry_run,
        first=args.first,
        num=args.num,
        indices=args.indices,
        files=args.files,
        origin=args.map,
        ledger_parent=args.ledger_parent,
        prodtools_tar=args.prodtools_tar,
        role=args.role,
        wftop=args.wftop,
        wfproject=args.wfproject,
        memory=args.memory,
        disk=args.disk,
        expected_lifetime=args.expected_lifetime,
    )
    results = submit_map(args.map, options)

    failed = [r for r in results if r['status'] == 'failed']
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
