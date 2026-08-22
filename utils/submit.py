#!/usr/bin/env python3
"""
Direct-submit driver for Mu2e grid jobs (single backend).

Builds the `jobsub_submit` argv directly and ships prodtools as a
dropbox tarball. Worker bootstraps `bin/runjob.sh` -> `utils/runmu2e.py`
direct mode -> per-job pushOutput. The Phase-1 mu2ejobsub backend was
retired 2026-07-19: template/direct_input/g4bl entries and HPC
submission run via the upstream mu2ejobsub/mu2eg4bl CLIs, never here.

Plans:
- wiki/pages/2026-04-29-remove-poms-from-submit-loop.md (Phase 1, POMS removal)
- wiki/pages/2026-04-30-phase2-direct-jobsub-implementation.md (Phase 2, direct)
"""

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
from utils.jobdesc import (ENTRY_VALUE_KEYS, tarball_of, outputs_of, njobs_of,
                           inloc_of, firstjob_of, validate_window,
                           resources_of, is_draining, validate_entry_value,
                           OUTSTAGE_LOCATION, code_of)
from utils import jobsub_argv as _jobsub_argv
from utils import submission_ledger
from utils.check_inputs import (check_inputs, check_code_tarball,
                                format_report, Problem)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNJOB_SH = REPO_ROOT / 'bin' / 'runjob.sh'
DEFAULT_PRODTOOLS_TAR = Path('/tmp') / f'prodtools-{getpass.getuser()}.tar'


class SubmitOptions(NamedTuple):
    """Everything submit_entry needs beyond the entry itself.

    Replaces the argparse namespace the old single-purpose submission CLI
    reached into, so utils/submissions.py can call submit_entry directly
    instead of serialising an entry to a temp file and spawning a
    subprocess.

    `first`/`num` are NOT the retired operator flags: submit_slice feeds
    every campaign slice through them (see _compute_jobset). `origin` is
    free-text provenance on the ledger row, echoed back only by MCP
    status tools.
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
        # jobsub_lite can exit 0 even when its internal condor_submit
        # failed (seen 2026-07-10: condor_vault_storer permission failure
        # under ksu). Treat a run with no parseable cluster ID as failed,
        # not success — a retry after a genuinely partial submit would
        # double-run indices (duplicate seeds), so verify with jobsub_q
        # before resubmitting.
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
    submitted, or nothing stops the next tick from re-sending the same
    deterministic payload.

    options.ledger_db is expected already resolved (see
    _resolve_ledger_db): a DERIVED path arrives with its directory
    already created; an explicit --ledger-db pointing at a missing
    directory must fail here, not get silently mkdir'd.
    """
    return submission_ledger.reserve_submission(
        options.ledger_db,
        tarball=entry['tarball'],
        entry=entry,
        indices=_ledger_payload(firstjob, jobset, files),
        origin=options.origin,
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
    except (sqlite3.Error, OSError) as e:
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
    except (sqlite3.Error, OSError) as e:
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
    AND failure (failures are exactly what gets debugged), across every
    origin (manual, cron slice, recovery resubmit). Never raises: the
    attempt already happened, so a log problem must not crash the
    submit."""
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
    try:
        with open(_submission_log_path(options.ledger_db), 'a') as fh:
            fh.write(block + '\n')
    except OSError as e:
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
    """Writer ledger path, resolved ONCE by the sole caller, `json2jobdef`
    (submit.py is a library, not a CLI). A DEFAULTED (derived) path gets
    its directory created; an operator-supplied --ledger-db never does —
    a typo there must fail loudly, not silently make a stray database."""
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
    # An outputs glob matching the input pattern would make the worker
    # declare the fetched input as an output, and pushOutput's orphan
    # recovery would then try to delete the production input. Heuristic
    # gate (fnmatch of the pattern string); the worker's own input
    # exclusion is the authoritative defense.
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
    ledger row exists. The boundary check `enqueue_entry` applies before
    a campaign is created. Matters most for `inloc`: a misspelled
    location doesn't fail, it degrades — file_resolver.locate finds
    nothing and falls through to SAM, so the campaign runs to completion
    reading from the wrong place.

    CLI overrides (--memory/--disk/--expected-lifetime) are NOT checked
    here — validated in main(), where they're read.
    """
    for key in ENTRY_VALUE_KEYS:
        if key in entry:
            try:
                validate_entry_value(key, entry[key])
            except ValueError as e:
                sys.exit(f"json2jobdef: {e}")


def _gate_code_tarball(entry, tarball_path, note=None):
    """Refuse to create a campaign whose code tarball no longer matches
    the cnf. Exits 2; returns only when the entry passes.

    Draining and normal entries share one gate rather than branch-local
    copies differing only by the trailing note — exactly the kind of
    difference that drifts into a real one.
    """
    ok, problems = check_code_tarball(entry, str(tarball_path))
    if ok:
        return
    print(format_report(str(tarball_path), problems))
    if note:
        print(note)
    sys.exit(2)


def _refuse_outstage_campaign(entry):
    """An outstage entry cannot be a campaign.

    Outstage outputs are never declared to SAM, and verify_row is
    fail-closed against SAM: with nothing declared, every index reads as
    missing, so each tick would recover the whole row against files that
    already exist — forever. Build and submit an outstage entry by hand.
    """
    for output in entry.get('outputs') or []:
        if output.get('location') == OUTSTAGE_LOCATION:
            sys.exit(
                "json2jobdef: outstage outputs are not declared to SAM, so "
                "campaign verification cannot see them and every slice "
                "would recover forever. An outstage entry cannot be "
                "enqueued — submit it by hand.")


def enqueue_entry(entry, *, ledger_db, slice_size, dry_run=False,
                  resources=None, provenance=None):
    """Register ONE entry as a sliced-submission campaign (cursor 0);
    submit nothing. Returns the new campaign id, or None under dry_run.

    Single owner of the enqueue preflight (`json2jobdef --enqueue` is the
    only caller), so a campaign is never created for a tarball with
    unreadable inputs. Nothing has been submitted when this fails, so
    operator-reachable errors exit with a ONE-LINE message, never a
    traceback (sys.exit kept deliberately — both callers are CLIs, so
    inheriting exit codes is correct).

    `provenance` is free-text recorded as the campaign's origin; nothing
    dispatches from it, only the MCP status tools echo it back.
    """
    resources = resources or {}
    _validate_entry_values(entry)
    _refuse_outstage_campaign(entry)
    if is_draining(entry):
        return _enqueue_draining(entry, ledger_db=ledger_db,
                                 slice_size=slice_size, dry_run=dry_run,
                                 resources=resources, provenance=provenance)

    tarball_path = _ensure_local_tarball(tarball_of(entry))
    ok, problems = check_inputs(str(tarball_path), inloc_of(entry))
    if not ok:
        print(format_report(str(tarball_path), problems))
        print(f"json2jobdef: inputs not ready "
              f"({len(problems)} problem(s)) — fix and re-run; "
              f"no campaign created")
        sys.exit(2)
    _gate_code_tarball(entry, tarball_path,
                       note="json2jobdef: code tarball does not match "
                            "the cnf — no campaign created")
    njobs = njobs_of(entry)
    if njobs is None:
        sys.exit("json2jobdef: entry has no njobs (generic tarball) — "
                 "a campaign needs a job count to slice")
    if njobs < 1:
        sys.exit(f"json2jobdef: entry has njobs={njobs} — "
                 f"a campaign needs a positive job count")
    snap = _snapshot_entry(entry, resources)
    if dry_run:
        print(f"[DRY RUN] would enqueue entry: "
              f"{tarball_of(entry)} njobs={njobs} "
              f"slice={slice_size}")
        return None
    camp_id = _create_campaign(ledger_db, entry, snap, slice_size, provenance)
    print(f"Enqueued campaign {camp_id}: {tarball_of(entry)} "
          f"njobs={njobs} slice={slice_size} (db {ledger_db})")
    return camp_id


def _enqueue_draining(entry, *, ledger_db, slice_size, dry_run,
                      resources, provenance):
    """Draining-entry tail of enqueue_entry (generic cnf + input_pattern)."""
    err = _validate_draining_entry(entry)
    if err:
        sys.exit(f"json2jobdef: {err}")
    tarball_path = _ensure_local_tarball(tarball_of(entry))
    # No check_inputs: a generic cnf bakes no inputs — the tick gates
    # each batch (residency + settling age) at dispatch. Code
    # tarball still has to match, though.
    _gate_code_tarball(entry, tarball_path)
    snap = _snapshot_entry(entry, resources)
    if dry_run:
        print(f"[DRY RUN] would enqueue draining campaign: "
              f"{tarball_of(entry)} "
              f"pattern={entry['input_pattern']} "
              f"slice={slice_size}")
        return None
    camp_id = _create_campaign(ledger_db, entry, snap, slice_size, provenance)
    print(f"Enqueued draining campaign {camp_id}: "
          f"{tarball_of(entry)} pattern={entry['input_pattern']} "
          f"slice={slice_size} (db {ledger_db})")
    return camp_id


def _create_campaign(ledger_db, entry, snap, slice_size, provenance):
    """Single home of the create_campaign call and its one-line exit on
    a ledger/validation error (nothing has been submitted yet)."""
    try:
        return submission_ledger.create_campaign(
            ledger_db, tarball=tarball_of(entry), entry=snap,
            slice_size=slice_size, origin=provenance)
    except (ValueError, sqlite3.Error) as e:
        sys.exit(f"json2jobdef: {e}")


def _bundle_prodtools(out_path=DEFAULT_PRODTOOLS_TAR):
    """Tar `utils/` + `bin/` from this repo into a worker-shippable bundle.

    `runjob.sh` extracts this under `$_CONDOR_SCRATCH_DIR/prodtools/`
    and execs `utils/runmu2e.py` from there, avoiding a dependency on a
    cvmfs-published prodtools version that might not have our changes.

    Skips tarring if `out_path` is already newer than every Python
    source file under utils/ — keeps repeated submissions cheap.
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

    njobs is authoritative from the cnf, not the submission map (the map
    field can be stale or absent for direct-input mode). Output
    filenames (index 0) feed the per-(area, tier, owner) token scope
    derivation; templates that resolved to a path (`/dev/null`) are
    skipped.
    """
    from utils.jobquery import Mu2eJobPars
    jp = Mu2eJobPars(str(tarball_path))
    out = jp.job_outputs(0) or {}
    return (jp.njobs(), jp.input_datasets(),
            [v for v in out.values() if v and "/" not in v])


def parse_indices(spec, path):
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


def parse_files(path):
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

    Indices are entry-relative (PROCESS space, starting at 0) — a
    windowed entry's `firstjob` offset is applied worker-side by
    `resolve_entry_index`, not here; window size comes from the entry's
    njobs, validated against cnf capacity via jobdesc.validate_window.

    Default: every index 0..size-1 (the whole cnf).
    --first N alone: 1 job at index N.
    --first N --num M: indices [N, N+M).
    --indices K1,K2,...: exactly those ABSOLUTE cnf indices (recovery),
      valid only on a non-windowed entry — a contiguous window can't
      express a scattered set.
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
    """Verify a cnf's baked inputs before submitting. Returns (ok, problems).

    Mirrors the gate enqueue_entry applies, so the DIRECT path
    (--first/--num and every recovery resubmit) gets it too — exactly
    the bulk-death failure check_inputs exists to prevent. A
    draining/generic cnf bakes no inputs, same carve-out as enqueue_entry.

    check_code_tarball runs FIRST, above the draining early-return: it's
    the digest gate binding a code-mode campaign to its Offline build,
    and since enqueue_entry runs once per campaign, every later
    slice/recovery would otherwise ship unverified bytes. Short-circuits
    for a Musing entry (no `code`) — one cheap cnf-parse, no sha256.
    """
    ok, problems = check_code_tarball(entry, str(tarball_path))
    if not ok:
        return ok, problems
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
    # output_filenames feeds the per-(area, tier, owner) token scope
    # derivation so pushOutput can MAKE_PARENT under /pnfs/mu2e/<area>/...
    if files is not None:
        # Draining batch: one direct-input job per file; a generic cnf has
        # no index capacity, so the jobset is positions into the batch.
        # Scope AREA is resolved per-output via fnmatch against
        # outputs[].dataset globs — a desc-discriminating glob picks a
        # different area per desc, so every distinct desc must contribute
        # its own mapped outputs, not just the first file's.
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
        # Capacity stand-in when the cnf isn't inspectable, so
        # validate_window never spuriously fails a dry run; widened below
        # to cover any --indices past the recovery entry's own njobs.
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

    # `--indices` values ARE cnf indices, but the worker reaches one via
    # resolve_entry_index (`local = global + firstjob`), so the SHIPPED
    # entry must sit at firstjob=0 and span past the largest index. Only
    # the ops copy is rewritten — the on-disk map keeps its own njobs.
    ops_entry = entry
    if options.indices is not None:
        ops_entry = {**entry, 'firstjob': 0, 'njobs': jobset[-1] + 1}

    # Synthesize ops JSON (jobs[] + inspec + jobdesc) to /tmp — same FS
    # jobsub_lite uses for dropbox staging, fine for local-test and
    # mu2epro alike.
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

    resources = _effective_resources(entry, options)

    # submitter is the effective UNIX user; role auto-defaults to
    # Production for mu2epro per jobsub_argv.role_for_user.
    submitter = getpass.getuser()
    # Token scopes for direct-mode pushOutput (CB1):
    #   - per data output: /mu2e/<area>/datasets/<owner-class>-<tier>/<tier>/<owner>
    #   - per log: same scheme with tier=log, but logs go to persistent disk
    #     regardless of data location (log_storage_location), so a tape
    #     campaign needs BOTH a tape data scope and a disk log scope.
    extra_scopes = list(_jobsub_argv.output_storage_dirs(
        output_filenames, outputs_of(entry)))
    if output_filenames:
        log_location = log_storage_location(entry)
        # A cnf output that does not parse is a broken cnf: a silently
        # skipped log scope surfaces as a 403 on the worker's log push.
        first_out = Mu2eName.parse(output_filenames[0])
        if first_out.is_file:
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
        code_tarball=code_of(entry),
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

