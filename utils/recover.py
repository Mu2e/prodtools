#!/usr/bin/env python3
"""Verify-and-resubmit recovery loop for direct-backend submissions.

Processes ledger rows written by `submit_map --backend direct`
(utils/submission_ledger.py). Per active row: skip while jobs are still
in the queue (held jobs are reported, never touched), SAM-verify the
row's indices via the cnf's expected output names, then close the row
as complete, resubmit exactly the missing indices (child row,
attempt+1), or close as exhausted at the attempt cap.

Only SAM output-file existence is trusted (the Run1Ban lesson:
consumption-status recovery re-dispatches finished work). Deterministic
payloads re-run identical events, so systematic failures re-fail every
round — `exhausted` is where a human takes over.

Design: docs/superpowers/specs/2026-07-18-direct-recovery-design.md
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import submission_ledger
from utils.jobquery import Mu2eJobPars
from utils.mkrecovery import (build_file_maps, extract_datasets_from_tarball,
                              locate_tarball)
from utils.samweb_wrapper import files_in_dataset

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBMIT_MAP = REPO_ROOT / 'bin' / 'submit_map'
DEFAULT_MAX_ATTEMPTS = 3


def queue_state(jobsub_id, runner=subprocess.run):
    """'drained' | 'held' | 'running' | 'error' for a jobsub id.

    Uses condor_q autoformat passthrough (`-af JobStatus`): one numeric
    HTCondor state per queued job (1 idle, 2 running, 5 held, ...).
    Only an empty, successful query counts as drained — anything
    unexpected is conservative (running/error), never drained.
    """
    res = runner(['jobsub_q', '--jobid', jobsub_id, '-af', 'JobStatus'],
                 capture_output=True, text=True)
    if res.returncode != 0:
        return 'error'
    states = res.stdout.split()
    if not states:
        return 'drained'
    if '5' in states:
        return 'held'
    return 'running'


def verify_row(row, sam_lister=files_in_dataset):
    """SAM-verify one ledger row's indices.

    Returns (missing, partial): absolute cnf indices with ANY expected
    output file absent from SAM, and the subset where only SOME streams
    are absent (flagged: a re-run re-pushes the streams that already
    landed — see the duplicate-declare item in the design spec).

    Raises on anything that prevents verification (unlocatable tarball,
    no output datasets, SAM failure): the caller keeps the row active
    and reports. A row is never guessed complete.
    """
    tarball_path = locate_tarball(row['tarball'])
    if not tarball_path or not os.path.exists(tarball_path):
        raise RuntimeError(f"cannot locate tarball {row['tarball']}")
    job_io = Mu2eJobPars(tarball_path)
    indices = row['indices']
    datasets = extract_datasets_from_tarball(job_io, len(indices))
    if not datasets:
        raise RuntimeError(f"no output datasets in {row['tarball']}")
    maps = build_file_maps(job_io, datasets, 0, indices=indices)
    expected = {}    # idx -> expected stream count
    missing_ct = {}  # idx -> missing stream count
    for ds in datasets:
        actual = set(sam_lister(ds))
        for fname, idx in maps[ds].items():
            expected[idx] = expected.get(idx, 0) + 1
            if fname not in actual:
                missing_ct[idx] = missing_ct.get(idx, 0) + 1
    missing = sorted(missing_ct)
    partial = sorted(i for i in missing_ct if missing_ct[i] < expected[i])
    return missing, partial


def resubmit(row, missing, db_path, dry_run=False, runner=subprocess.run):
    """Resubmit missing indices through the submit_map CLI — one
    battle-tested submit path (token check, argv build, child ledger row
    via --ledger-parent). Returns True on submit success.

    The reconstructed entry DROPS firstjob: --indices values are
    absolute cnf indices, and submit_map rejects --indices on windowed
    entries (the worker-side firstjob+index resolution must degenerate
    to the identity). The original windowed entry stays in the parent
    row's snapshot.
    """
    entry = {k: v for k, v in row['entry'].items() if k != 'firstjob'}
    tmpdir = tempfile.mkdtemp(prefix='recover-')
    map_path = Path(tmpdir) / 'recovery-map.json'
    map_path.write_text(json.dumps([entry], indent=2) + '\n')
    idx_path = Path(tmpdir) / 'indices.txt'
    idx_path.write_text(f"# {row['tarball']}\n"
                        + '\n'.join(str(i) for i in missing) + '\n')
    cmd = [str(SUBMIT_MAP), '--map', str(map_path), '--backend', 'direct',
           '--indices-file', str(idx_path),
           '--ledger-parent', str(row['id']),
           '--ledger-db', str(db_path)]
    if dry_run:
        cmd.append('--dry-run')
    print(f"  resubmit: {' '.join(cmd)}")
    res = runner(cmd)
    return res.returncode == 0


def process_row(row, db_path, max_attempts, dry_run=False,
                queue_state_fn=queue_state, verify_fn=verify_row,
                resubmit_fn=resubmit):
    """Drive one ledger row through the gate/verify/act sequence.

    Returns the action taken: 'running' | 'held' | 'queue-error' |
    'verify-error' | 'complete' | 'resubmitted' | 'resubmit-error' |
    'exhausted' | 'would-resubmit'.
    """
    rid = row['id']
    if not row['jobsub_id']:
        print(f"row {rid}: no full jobsub id recorded — cannot "
              f"drain-check; update the row manually")
        return 'queue-error'
    state = queue_state_fn(row['jobsub_id'])
    if state == 'running':
        print(f"row {rid}: jobs still in queue — skip")
        return 'running'
    if state == 'held':
        print(f"row {rid}: HELD jobs in {row['jobsub_id']} — human "
              f"decision needed (release or rm); loop will not act")
        return 'held'
    if state == 'error':
        print(f"row {rid}: jobsub_q failed — skip")
        return 'queue-error'
    try:
        missing, partial = verify_fn(row)
    except Exception as e:
        print(f"row {rid}: verify failed: {e} — row stays active")
        return 'verify-error'
    if partial:
        print(f"row {rid}: PARTIAL outputs at indices {partial} — some "
              f"streams landed; a re-run re-pushes the existing files")
    if not missing:
        submission_ledger.close_row(
            db_path, rid, 'complete',
            note=f"{len(row['indices'])} indices verified")
        print(f"row {rid}: complete ({len(row['indices'])} indices)")
        return 'complete'
    print(f"row {rid}: {len(missing)}/{len(row['indices'])} indices "
          f"missing outputs")
    if row['attempt'] >= max_attempts:
        submission_ledger.close_row(
            db_path, rid, 'exhausted',
            note=f"{len(missing)} indices missing after attempt "
                 f"{row['attempt']}: {missing[:50]}")
        print(f"row {rid}: EXHAUSTED after attempt {row['attempt']} — "
              f"human takes over. Missing: {missing}")
        return 'exhausted'
    if dry_run:
        print(f"row {rid}: would resubmit {len(missing)} indices "
              f"(attempt {row['attempt'] + 1})")
        return 'would-resubmit'
    if resubmit_fn(row, missing, db_path):
        submission_ledger.close_row(
            db_path, rid, 'recovered',
            note=f"{len(missing)} indices -> child row")
        return 'resubmitted'
    print(f"row {rid}: resubmit FAILED — row stays active")
    return 'resubmit-error'


def print_status(db_path):
    """Read-only ledger table (safe under any account — status checks
    never need mu2epro)."""
    rows = submission_ledger.all_rows(db_path)
    if not rows:
        print(f"Ledger is empty ({db_path}).")
        return
    print(f"{'id':>4} {'state':<10} {'att':>3} {'parent':>6} {'#idx':>5}  "
          f"{'created':<20} tarball")
    for r in rows:
        print(f"{r['id']:>4} {r['state']:<10} {r['attempt']:>3} "
              f"{str(r['parent_id'] or ''):>6} {len(r['indices']):>5}  "
              f"{r['created_utc']:<20} {r['tarball']}")


def main():
    p = argparse.ArgumentParser(
        description='Verify-and-resubmit recovery loop for direct-backend '
                    'submissions (ledger written by submit_map).')
    p.add_argument('--db', default=submission_ledger.DEFAULT_DB,
                   help=f'Submission-ledger sqlite DB (default: '
                        f'{submission_ledger.DEFAULT_DB}, env '
                        f'MU2E_SUBMISSION_DB)')
    p.add_argument('--status', action='store_true',
                   help='Print the ledger table and exit (read-only)')
    p.add_argument('--dry-run', action='store_true',
                   help='Drain-check + verify + report; no submissions, '
                        'no row state changes')
    p.add_argument('--row', type=int, default=None,
                   help='Process only this ledger row id')
    p.add_argument('--max-attempts', type=int, default=DEFAULT_MAX_ATTEMPTS,
                   help=f'Attempt cap per chain (default '
                        f'{DEFAULT_MAX_ATTEMPTS}); at the cap the row is '
                        f'marked exhausted for a human')
    args = p.parse_args()

    if args.status:
        print_status(args.db)
        return

    rows = submission_ledger.open_rows(args.db)
    if args.row is not None:
        rows = [r for r in rows if r['id'] == args.row]
        if not rows:
            sys.exit(f"no active row {args.row} in {args.db}")
    if not rows:
        print(f"No active submissions ({args.db}).")
        return

    summary = {}
    for row in rows:
        action = process_row(row, args.db, args.max_attempts,
                             dry_run=args.dry_run)
        summary[action] = summary.get(action, 0) + 1
    print("recover summary: "
          + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    if summary.get('held') or summary.get('exhausted'):
        sys.exit(2)


if __name__ == '__main__':
    main()
