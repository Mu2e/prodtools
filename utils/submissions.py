#!/usr/bin/env python3
"""Direct-submission subsystem CLI (`submissions`): read-only status,
the verify-and-resubmit + sliced-campaign top-up tick (`run`), and
campaign management verbs.

Processes ledger rows written by `submit_map`
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
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import submission_ledger
from utils.jobquery import Mu2eJobPars
from utils.mkrecovery import (build_file_maps, extract_datasets_from_tarball,
                              locate_tarball)
from utils.poms_entry import njobs_of
from utils.samweb_wrapper import files_in_dataset

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBMIT_MAP = REPO_ROOT / 'bin' / 'submit_map'
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_QUEUED = 10000


def resolve_cap(flag_value):
    """Queue cap for the top-up phase: --max-queued flag >
    MU2E_MAX_QUEUED env > DEFAULT_MAX_QUEUED. Resolved once per
    invocation; nothing persists between runs — the effective cap is
    always readable off the crontab line."""
    if flag_value is not None:
        return flag_value
    env = os.environ.get('MU2E_MAX_QUEUED')
    if env is not None:
        try:
            return int(env)
        except ValueError:
            sys.exit(f"MU2E_MAX_QUEUED is not an integer: {env!r}")
    return DEFAULT_MAX_QUEUED


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
    if any(not s.isdigit() for s in states):
        return 'error'
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
    unverifiable = [i for i in indices if i not in expected]
    if unverifiable:
        raise RuntimeError(
            f"indices {unverifiable} have no expected output files in "
            f"{row['tarball']} — cannot verify (output datasets: {datasets})")
    missing = sorted(missing_ct)
    partial = sorted(i for i in missing_ct if missing_ct[i] < expected[i])
    return missing, partial


@contextlib.contextmanager
def _scratch_map_dir(prefix):
    """Scratch dir for a child submit_map's map/indices files; removed
    after the child completes (success or failure — the child reads
    them before returning). Cleanup failure warns, never raises
    (post-submission never-raise rule)."""
    tmpdir = tempfile.mkdtemp(prefix=prefix)
    try:
        yield Path(tmpdir)
    finally:
        try:
            shutil.rmtree(tmpdir)
        except OSError as e:
            print(f"WARNING: could not remove scratch dir {tmpdir}: {e}")


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
    with _scratch_map_dir('recover-') as tmpdir:
        map_path = tmpdir / 'recovery-map.json'
        map_path.write_text(json.dumps([entry], indent=2) + '\n')
        idx_path = tmpdir / 'indices.txt'
        idx_path.write_text(f"# {row['tarball']}\n"
                            + '\n'.join(str(i) for i in missing) + '\n')
        cmd = [str(SUBMIT_MAP), '--map', str(map_path),
               '--indices-file', str(idx_path),
               '--ledger-parent', str(row['id']),
               '--ledger-db', str(db_path)]
        if dry_run:
            cmd.append('--dry-run')
        print(f"  resubmit: {' '.join(cmd)}")
        res = runner(cmd)
    return res.returncode == 0


def total_queued(user='mu2epro', runner=subprocess.run):
    """Total idle+running jobs for `user` — the top-up throttle gate —
    or None when the count cannot be trusted (caller skips the phase).

    Counts HTCondor states 1 (idle) and 2 (running) via condor_q
    autoformat passthrough; held/removed/other states do not consume
    cap headroom. Covers ALL the user's jobs (POMS-launched included),
    so the cap bounds the account's whole farm footprint."""
    res = runner(['jobsub_q', '--user', user, '-af', 'JobStatus'],
                 capture_output=True, text=True)
    if res.returncode != 0:
        return None
    states = res.stdout.split()
    if any(not s.isdigit() for s in states):
        return None
    return sum(1 for s in states if s in ('1', '2'))


def submit_slice(camp, n, db_path, runner=subprocess.run):
    """Submit the campaign's next slice through the submit_map CLI —
    the same battle-tested path as manual submissions (token check,
    argv build, ledger row, submit log). The snapshot entry ships
    VERBATIM: firstjob is preserved because cursor and --first/--num
    are entry-relative, exactly like a manual windowed submission.
    Returns True on submit success."""
    with _scratch_map_dir('campaign-') as tmpdir:
        map_path = tmpdir / 'campaign-map.json'
        map_path.write_text(json.dumps([camp['entry']], indent=2) + '\n')
        cmd = [str(SUBMIT_MAP), '--map', str(map_path),
               '--first', str(camp['cursor']),
               '--num', str(n), '--ledger-db', str(db_path)]
        print(f"  campaign {camp['id']}: slice first={camp['cursor']} "
              f"num={n}: {' '.join(cmd)}")
        res = runner(cmd)
    return res.returncode == 0


def _slice_overlaps_ledger(db_path, tarball, firstjob, cursor, n):
    """True if any ledger row (ANY state) for `tarball` already has an
    absolute cnf index inside the slice's absolute window
    [firstjob+cursor, firstjob+cursor+n).

    Crash-window guard: a parent `submit_map` process can die after
    `jobsub_submit` succeeds but before its own ledger write (the same
    residual window the recovery pass's resubmits have). Without this
    check, the NEXT top-up tick would re-submit indices already queued
    — deterministic payloads make that a duplicate-physics-events bug,
    not a harmless retry. Also catches a human manually submitting
    `--first/--num` on a tarball that has a live campaign.

    Deliberately not a false-positive source for the recovery loop's
    OWN resubmits: a child row's indices are a subset of an ALREADY
    ADVANCED-PAST parent slice, strictly below the cursor — they can
    never fall inside a slice window that starts at cursor.
    """
    lo = firstjob + cursor
    hi = lo + n
    for row in submission_ledger.all_rows(db_path):
        if row['tarball'] != tarball:
            continue
        if any(lo <= idx < hi for idx in row['indices']):
            return True
    return False


def top_up(db_path, cap, dry_run=False, count_fn=total_queued,
           submit_fn=submit_slice):
    """Feed slices from active campaigns while total idle+running stays
    under the cap. Whole slices only (n = min(slice_size, remaining) is
    short only at end of entry — never clamped to headroom); cycles
    oldest-first, one slice per campaign per cycle; the first slice
    that would exceed the cap stops the tick. Submission failure
    pauses the campaign (no blind retry — deterministic payloads make
    an unverified resubmit the Run1Ban failure mode). Before each
    slice, checks the ledger for indices already covering the slice's
    absolute window (crash-window guard, see _slice_overlaps_ledger) —
    an overlap pauses the campaign rather than submits. A campaign
    whose cursor is already at njobs but is still 'active' (crash
    between advance_campaign and the completion write) self-heals to
    'complete'. Returns an action-count summary in the recovery pass's
    style."""
    summary = {}

    def bump(key):
        summary[key] = summary.get(key, 0) + 1

    camps = submission_ledger.active_campaigns(db_path)
    if not camps:
        return summary
    count = count_fn()
    if count is None:
        print("top-up: queue count failed — top-up skipped this tick")
        bump('count-error')
        return summary
    print(f"top-up: {count} idle+running (cap {cap}), "
          f"{len(camps)} active campaign(s)")
    progressed = True
    while progressed:
        progressed = False
        for camp in camps:
            if camp['state'] != 'active':
                continue
            njobs = njobs_of(camp['entry'])
            remaining = njobs - camp['cursor']
            if remaining <= 0:
                # Cursor already at njobs but the campaign is still
                # 'active': a crash between advance_campaign and
                # set_campaign_state('complete') on a prior tick, or the
                # last slice's completion write never happened. Self-
                # heal rather than leaving it stuck active forever.
                if dry_run:
                    print(f"campaign {camp['id']}: cursor already at "
                          f"njobs — would close complete (self-heal)")
                    bump('would-campaign-complete')
                else:
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'complete',
                        note='fully submitted (self-heal)')
                    print(f"campaign {camp['id']}: cursor already at "
                          f"njobs — closed complete (self-heal)")
                    camp['state'] = 'complete'
                    bump('campaign-complete')
                continue
            n = min(camp['slice_size'], remaining)
            if count + n > cap:
                print(f"top-up: campaign {camp['id']}: {count}+{n} > {cap} "
                      f"— headroom < slice, waiting for next tick")
                bump('cap-wait')
                return summary
            firstjob = camp['entry'].get('firstjob', 0)
            if _slice_overlaps_ledger(db_path, camp['tarball'], firstjob,
                                      camp['cursor'], n):
                if dry_run:
                    print(f"campaign {camp['id']}: ledger already covers "
                          f"indices in [{firstjob + camp['cursor']}.."
                          f"{firstjob + camp['cursor'] + n - 1}] — would "
                          f"pause (crash-window suspected)")
                    bump('would-pause-overlap')
                else:
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'paused',
                        note='ledger already covers indices in this '
                             'slice — crash-window suspected; reconcile '
                             'cursor manually before `submissions resume '
                             '<ID>`')
                    print(f"campaign {camp['id']}: ledger already covers "
                          f"indices in this slice — PAUSED (crash-window "
                          f"suspected; reconcile cursor manually before "
                          f"`submissions resume <ID>`)")
                    camp['state'] = 'paused'
                    bump('campaign-paused')
                continue
            if dry_run:
                print(f"campaign {camp['id']}: would submit slice "
                      f"first={camp['cursor']} num={n}")
                bump('would-slice')
            else:
                if not submit_fn(camp, n, db_path):
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'paused',
                        note='submit failed — check the submit log and '
                             'jobsub_q, then `submissions resume <ID>`')
                    print(f"campaign {camp['id']}: submit FAILED — PAUSED "
                          f"(no blind retry; check the submit log and "
                          f"jobsub_q, then `submissions resume <ID>`)")
                    camp['state'] = 'paused'
                    bump('campaign-paused')
                    continue
                submission_ledger.advance_campaign(
                    db_path, camp['id'], camp['cursor'] + n)
                bump('slice')
            camp['cursor'] += n
            count += n
            progressed = True
            if camp['cursor'] >= njobs:
                if dry_run:
                    print(f"campaign {camp['id']}: would close complete")
                    bump('would-campaign-complete')
                else:
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'complete',
                        note='fully submitted')
                    print(f"campaign {camp['id']}: fully submitted — "
                          f"complete (verification continues per ledger "
                          f"row)")
                    bump('campaign-complete')
                camp['state'] = 'complete'
    return summary


def manage_campaign(db_path, camp_id, action, note=None):
    """Operator switches. cancel closes the campaign only —
    already-submitted ledger rows still get recovered normally. note
    applies to pause/cancel; resume never writes one (the stored pause
    reason is preserved)."""
    target = {'pause': 'paused', 'resume': 'active',
              'cancel': 'cancelled'}[action]
    submission_ledger.set_campaign_state(
        db_path, camp_id, target,
        note=note if note is not None else f'operator {action}')
    print(f"campaign {camp_id}: {action} -> {target}")


def process_row(row, db_path, max_attempts, dry_run=False,
                queue_state_fn=queue_state, verify_fn=verify_row,
                resubmit_fn=resubmit):
    """Drive one ledger row through the gate/verify/act sequence.

    Returns the action taken: 'running' | 'held' | 'queue-error' |
    'verify-error' | 'complete' | 'resubmitted' | 'resubmit-error' |
    'exhausted' | 'would-resubmit' | 'would-complete' | 'would-exhaust' |
    'child-active' | 'child-missing' | 'would-recover'.
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
        if dry_run:
            print(f"row {rid}: would close complete "
                  f"({len(row['indices'])} indices verified)")
            return 'would-complete'
        submission_ledger.close_row(
            db_path, rid, 'complete',
            note=f"{len(row['indices'])} indices verified")
        print(f"row {rid}: complete ({len(row['indices'])} indices)")
        return 'complete'
    print(f"row {rid}: {len(missing)}/{len(row['indices'])} indices "
          f"missing outputs")
    children = [r for r in submission_ledger.open_rows(db_path)
                if r['parent_id'] == rid]
    if children:
        if dry_run:
            print(f"row {rid}: child row {children[0]['id']} already "
                  f"active — would close recovered (crash-window repair)")
            return 'would-recover'
        submission_ledger.close_row(
            db_path, rid, 'recovered',
            note=f"child row {children[0]['id']} already active "
                 f"(crash-window repair)")
        print(f"row {rid}: child row {children[0]['id']} already active — "
              f"closed recovered (crash-window repair)")
        return 'child-active'
    if row['attempt'] >= max_attempts:
        if dry_run:
            print(f"row {rid}: would mark EXHAUSTED (attempt "
                  f"{row['attempt']} at cap; {len(missing)} missing)")
            return 'would-exhaust'
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
        children = [r for r in submission_ledger.open_rows(db_path)
                    if r['parent_id'] == rid]
        if not children:
            submission_ledger.close_row(
                db_path, rid, 'recovered',
                note=f"resubmitted {len(missing)} indices but child ledger "
                     f"row MISSING — chain unwatched, verify manually")
            print(f"row {rid}: resubmit succeeded but NO child ledger row "
                  f"found — the new submission is UNWATCHED; verify "
                  f"manually (indices {missing})")
            return 'child-missing'
        submission_ledger.close_row(
            db_path, rid, 'recovered',
            note=f"{len(missing)} indices -> child row {children[0]['id']}")
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
    camps = submission_ledger.all_campaigns(db_path)
    if camps:
        print(f"\n{'id':>4} {'state':<10} {'cursor':>12} {'slice':>6}  "
              f"{'created':<20} tarball")
        for c in camps:
            njobs = njobs_of(c['entry'])
            print(f"{c['id']:>4} {c['state']:<10} "
                  f"{str(c['cursor']) + '/' + str(njobs):>12} "
                  f"{c['slice_size']:>6}  {c['created_utc']:<20} "
                  f"{c['tarball']}")


def build_parser():
    p = argparse.ArgumentParser(
        prog='submissions',
        description='Direct-submission subsystem CLI: status (default '
                    'verb, read-only), the hourly verify/resubmit/'
                    'top-up tick (run), and campaign management '
                    '(pause/resume/cancel).')
    p.add_argument('--db', default=submission_ledger.DEFAULT_DB,
                   help=f'Submission-ledger sqlite DB (default: '
                        f'{submission_ledger.DEFAULT_DB}, env '
                        f'MU2E_SUBMISSION_DB)')
    sub = p.add_subparsers(dest='verb')

    sub.add_parser('status',
                   help='Print ledger + campaigns + queue cap and exit '
                        '(read-only; the default verb)')

    run = sub.add_parser('run',
                         help='One tick: recovery pass then campaign '
                              'top-up (the cron entry point)')
    run.add_argument('--dry-run', action='store_true',
                     help='Report would-* actions only; no submissions, '
                          'no state changes')
    run.add_argument('--row', type=int, default=None,
                     help='Process only this ledger row id (skips '
                          'top-up)')
    run.add_argument('--max-attempts', type=int,
                     default=DEFAULT_MAX_ATTEMPTS,
                     help=f'Attempt cap per chain (default '
                          f'{DEFAULT_MAX_ATTEMPTS}); at the cap the row '
                          f'is marked exhausted for a human')
    run.add_argument('--max-queued', type=int, default=None,
                     help=f'Total mu2epro idle+running cap for the '
                          f'top-up phase (default: MU2E_MAX_QUEUED env, '
                          f'then {DEFAULT_MAX_QUEUED})')

    pause = sub.add_parser('pause', help='Pause an active campaign')
    pause.add_argument('camp_id', type=int)
    pause.add_argument('--note', default=None,
                       help='Reason recorded on the campaign (default: '
                            '"operator pause")')
    resume = sub.add_parser('resume',
                            help='Reactivate a paused campaign')
    resume.add_argument('camp_id', type=int)
    cancel = sub.add_parser('cancel',
                            help='Cancel a campaign (already-submitted '
                                 'rows still get recovered)')
    cancel.add_argument('camp_id', type=int)

    # Bare invocation (no verb) IS status — an explicit default, not a
    # hidden fallthrough (spec Change 1). Must come AFTER
    # add_subparsers(dest='verb'): the subparsers action sets its own
    # default of None for that dest, which otherwise clobbers this.
    p.set_defaults(verb='status')
    return p


def _acquire_lock(db_path):
    """One mutating pass at a time per DB — guards manual runs racing
    the cron (both passing the drain gate before either closes a row =
    double submit). Read-only modes never call this. The fd is held for
    the process lifetime; released on exit."""
    lock_path = os.path.join(os.path.dirname(db_path) or '.',
                             'submissions.lock')
    _acquire_lock._fh = open(lock_path, 'w')
    try:
        fcntl.flock(_acquire_lock._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"another submissions run holds {lock_path} — exiting")


def _run_pass(args):
    """The tick: recovery pass over active ledger rows, then campaign
    top-up. Exits 2 when anything needs human attention."""
    rows = submission_ledger.open_rows(args.db)
    if args.row is not None:
        rows = [r for r in rows if r['id'] == args.row]
        if not rows:
            sys.exit(f"no active row {args.row} in {args.db}")
    if not rows:
        print(f"No active submissions ({args.db}).")

    summary = {}
    for row in rows:
        action = process_row(row, args.db, args.max_attempts,
                             dry_run=args.dry_run)
        summary[action] = summary.get(action, 0) + 1

    if args.row is None:
        # Top-up AFTER the recovery pass: resubmissions are already in
        # the queue when the count is taken, so the cap covers them.
        for k, v in top_up(args.db, resolve_cap(args.max_queued),
                           dry_run=args.dry_run).items():
            summary[k] = summary.get(k, 0) + v
        # A paused campaign means "waiting on a human" — repeat the
        # exit-2 signal EVERY tick until someone resumes or cancels,
        # not just on the tick that paused it.
        paused = [c for c in submission_ledger.all_campaigns(args.db)
                  if c['state'] == 'paused']
        if paused:
            ids = ', '.join(str(c['id']) for c in paused)
            print(f"ATTENTION: paused campaign(s) awaiting a human: "
                  f"{ids} (submissions resume/cancel to clear)")
            summary['paused-campaign'] = len(paused)

    if summary:
        print("submissions summary: "
              + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    if (summary.get('held') or summary.get('exhausted')
            or summary.get('would-exhaust') or summary.get('child-missing')
            or summary.get('campaign-paused')
            or summary.get('would-pause-overlap')
            or summary.get('count-error')
            or summary.get('paused-campaign')):
        sys.exit(2)


def main():
    args = build_parser().parse_args()
    verb = args.verb

    if verb == 'status':
        print(f"queue cap in effect: {resolve_cap(None)}")
        print_status(args.db)
        return

    if verb in ('pause', 'resume', 'cancel'):
        _acquire_lock(args.db)
        try:
            manage_campaign(args.db, args.camp_id, verb,
                            note=getattr(args, 'note', None))
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        return

    # verb == 'run'
    if not args.dry_run:
        _acquire_lock(args.db)
    _run_pass(args)


if __name__ == '__main__':
    main()
