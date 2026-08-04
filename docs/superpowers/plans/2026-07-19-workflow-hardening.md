# Direct-Submission Workflow Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the direct-submission workflow for multi-operator use before activation: safe-by-default `submissions` CLI (rename of `recover`), honest exit codes, clean operator errors, pause-note preservation, tmp cleanup, single-backend `submit_map`, and operator docs.

**Architecture:** Rename `utils/recover.py` → `utils/submissions.py` and restructure its CLI into argparse subcommands (bare = read-only status; `run` = the mutating tick; `pause`/`resume`/`cancel` = campaign management). Delete `submit_map`'s Phase-1 mu2ejobsub backend so the direct backend is the only path. Everything else is small, targeted diffs inside the two modules plus docs.

**Tech Stack:** Python 3 stdlib only (argparse, sqlite3, subprocess, fcntl, tempfile, shutil, contextlib) — this code runs as mu2epro in the bare ops environment; no new dependencies. Tests: `unittest` in `test/test_unit.py` with fake runners/subprocess (no network), run via `python3 -m pytest test/test_unit.py -q` or `python3 -m unittest`.

**Spec:** `docs/superpowers/specs/2026-07-19-workflow-hardening-design.md`

## Global Constraints

- The subsystem is NOT activated: no crontab anywhere references these tools; clean cuts are allowed and aliases are forbidden ("any alias or back-compat shim for the old `recover` name" is out of scope).
- Bare `submissions` (no verb) MUST be read-only status. Mutating actions require an explicit verb.
- Verb set exactly: `status` (default), `run` (`--dry-run`, `--row N`, `--max-attempts N`, `--max-queued N`), `pause CAMP_ID [--note TEXT]`, `resume CAMP_ID`, `cancel CAMP_ID`. Global `--db PATH` on the top-level parser (before the verb).
- Locking: `run` (non-dry-run) and `pause`/`resume`/`cancel` take the per-DB flock; `status` and `run --dry-run` never do.
- Exit codes: `run` exits 2 when human attention is needed (held / exhausted / would-exhaust / child-missing / campaign-paused / would-pause-overlap / **count-error** / **paused-campaign**), else 0. `status` exits 0 unless the DB is unreadable. Management verbs: 0 on success, 1 with a one-line error on invalid transitions.
- Operator-reachable errors print one line prefixed `submit_map: ` or `submissions: ` and exit 1 — never a traceback. Internal invariant violations may still traceback.
- Refusal message for the flag contradiction, verbatim: `submit_map: --enqueue registers a campaign in the ledger DB; --no-ledger contradicts it`
- `submit_map` is single-backend (direct). Passing `--backend` anything must be an argparse error (unknown argument).
- The upstream `mu2ejobsub` tool, POMS launch path, `runmu2e` worker-side shim compatibility, and Perl parity tests are UNTOUCHED.
- Resume keeps the campaign's existing note. The note column stays a single value — no history table.
- Tmp cleanup warns and never raises on failure (post-submission never-raise rule).
- Stdlib only; house test style (fakes, no network); suite must be green at every commit. 442 tests today; tasks add 24 and Task 6 removes 6 — expect 460 by the end.
- Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov`
- Run the suite from the repo root: `python3 -m pytest test/test_unit.py -q` (fallback: `python3 -m unittest test.test_unit -v 2>&1 | tail -5`).

---

### Task 1: Rename `recover` → `submissions` with verb structure

**Files:**
- Rename: `utils/recover.py` → `utils/submissions.py` (git mv, then edit `main()` region, lines ~468-564 of the old file)
- Create: `bin/submissions`
- Delete: `bin/recover`
- Rename: `bin/recover_cron` → `bin/submissions_cron` (git mv, then edit)
- Test: `test/test_unit.py` (mechanical rename of ~40 `utils.recover` references + new verb-dispatch tests; existing classes affected: `TestRecoverCap`, `TestTopUp`, `TestSubmitSlice`, `TestManageCampaign`, `TestSubmitLedgerHook`, `TestBuildFileMapsScoped`, `TestRecoverLoop`, `TestRecoverCLI`)

**Interfaces:**
- Consumes: everything already in `utils/recover.py` — public functions keep their names (`resolve_cap`, `queue_state`, `verify_row`, `resubmit`, `total_queued`, `submit_slice`, `top_up`, `manage_campaign`, `process_row`, `print_status`).
- Produces (later tasks rely on these): module `utils/submissions.py`; `build_parser() -> argparse.ArgumentParser`; `_acquire_lock(db_path)`; `_run_pass(args)` (the tick body; Task 2 edits it); `manage_campaign(db_path, camp_id, action)` unchanged signature (Task 4 adds `note=None`); the two `submit_map` child-argv call sites in `resubmit()` and `submit_slice()` still pass `'--backend', 'direct'` (Task 6 removes that).

- [ ] **Step 1: git mv the module and the cron script; create/delete bin entry points**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
git mv utils/recover.py utils/submissions.py
git mv bin/recover_cron bin/submissions_cron
git rm bin/recover
```

Create `bin/submissions` (executable) with exactly:

```bash
#!/bin/bash

# submissions - direct-submission subsystem CLI: status (default verb),
# the hourly verify/resubmit/top-up tick (run), campaign management
# (pause/resume/cancel). Wrapper for utils/submissions.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/../utils/submissions.py"

# Pass help through without environment setup
if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    exec python3 "$PYTHON_SCRIPT" "$@"
fi

# Set up Mu2e environment (needed for samweb, jobsub_q, httokendecode)
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops

exec python3 "$PYTHON_SCRIPT" "$@"
```

```bash
chmod +x bin/submissions
```

- [ ] **Step 2: Edit `bin/submissions_cron` for the new names**

Full new content (changes: header comment name, python module path, log file name, `run` verb on the invocation; everything else byte-identical to the old `recover_cron`):

```bash
#!/bin/bash
# Cron entry point for the direct-submission maintenance tick. Install
# in mu2epro's crontab (hourly), e.g.:
#   17 * * * * /path/to/prodtools/bin/submissions_cron
#
# Order matters:
#   1. locking — handled inside `submissions run` itself (a per-DB
#      lockfile beside the DB, held for the process lifetime); an
#      overlapping run exits 1 with a "another submissions run holds
#      ..." message rather than double-submitting (same nonzero class
#      as the token gate — read the log line to tell them apart).
#   2. Mu2e env (quiet; failures still surface via the python run)
#   3. token gate — no valid bearer token -> report and exit non-zero.
#      NEVER fetch or refresh a token here (standing rule: token
#      problems are reported, not remediated).
#   4. submissions run, appended to a dated log beside the DB
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${MU2E_SUBMISSION_DB:-/exp/mu2e/data/users/mu2epro/prodtools/submissions.db}"
DBDIR="$(dirname "$DB")"
LOG="$DBDIR/submissions-$(date +%Y%m%d).log"

{
    echo "=== submissions_cron $(date -u +%FT%TZ) ==="
    source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh >/dev/null 2>&1 || true
    muse setup ops >/dev/null 2>&1 || true
    if ! httokendecode >/dev/null 2>&1; then
        echo "ERROR: no valid bearer token — not submitting, not remediating."
        exit 1
    fi
    python3 "$REPO/utils/submissions.py" --db "$DB" run
} >>"$LOG" 2>&1
```

- [ ] **Step 3: Mechanical rename in the test file**

```bash
sed -i 's/from utils\.recover import/from utils.submissions import/g; s/from utils import recover/from utils import submissions as recover/g; s/utils\.recover/utils.submissions/g' test/test_unit.py
```

Note the `as recover` alias: existing test bodies reference the local name `recover` (e.g. `recover.process_row`); aliasing the import keeps those ~100 usages untouched while the module itself is cleanly renamed. This alias lives only inside the test file — it is not a back-compat shim in the product.

- [ ] **Step 4: Write the failing verb-dispatch tests**

Append to `test/test_unit.py` (after `TestManageCampaign`):

```python
# ---------------------------------------------------------------------------
# submissions CLI verb structure (utils/submissions.py) — workflow hardening
# ---------------------------------------------------------------------------
class TestSubmissionsVerbs(unittest.TestCase):
    """Safe-by-default CLI: bare invocation is read-only status; the
    mutating tick requires the `run` verb; campaign management verbs
    validate transitions and fail with one-line errors."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.dbdir = tempfile.mkdtemp()
        self.db = os.path.join(self.dbdir, 'sub.db')

    def _campaign(self, tarball='cnf.mu2e.V.C.0.tar', njobs=4):
        return self.sl.create_campaign(
            self.db, tarball=tarball,
            entry={'tarball': tarball, 'njobs': njobs},
            slice_size=2, map_path='m.json')

    def test_bare_invocation_is_status(self):
        from utils import submissions
        import io as _io
        self.sl.record_submission(
            self.db, tarball='cnf.mu2e.V.C.0.tar', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        buf = _io.StringIO()
        with patch('sys.stdout', buf), \
             patch.object(submissions, 'process_row',
                          side_effect=AssertionError('bare must not run')), \
             patch.object(submissions, 'top_up',
                          side_effect=AssertionError('bare must not top up')), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db]):
            submissions.main()
        out = buf.getvalue()
        self.assertIn('queue cap in effect', out)
        self.assertIn('cnf.mu2e.V.C.0.tar', out)
        # read-only: no lock file created
        self.assertFalse(
            os.path.exists(os.path.join(self.dbdir, 'submissions.lock')))

    def test_status_verb_same_as_bare(self):
        from utils import submissions
        import io as _io
        buf = _io.StringIO()
        with patch('sys.stdout', buf), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'status']):
            submissions.main()
        self.assertIn('empty', buf.getvalue().lower())

    def test_run_verb_processes_rows_and_locks(self):
        from utils import submissions
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        with patch.object(submissions, 'process_row',
                          return_value='complete') as pr, \
             patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            submissions.main()
        self.assertEqual(pr.call_count, 1)
        self.assertTrue(
            os.path.exists(os.path.join(self.dbdir, 'submissions.lock')))

    def test_run_dry_run_takes_no_lock(self):
        from utils import submissions
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run', '--dry-run']):
            submissions.main()
        self.assertFalse(
            os.path.exists(os.path.join(self.dbdir, 'submissions.lock')))

    def test_pause_and_resume_verbs(self):
        from utils import submissions
        cid = self._campaign()
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'pause', str(cid)]):
            submissions.main()
        camp = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(camp['state'], 'paused')
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'resume', str(cid)]):
            submissions.main()
        camp = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(camp['state'], 'active')

    def test_cancel_verb(self):
        from utils import submissions
        cid = self._campaign()
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'cancel', str(cid)]):
            submissions.main()
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'],
                         'cancelled')

    def test_invalid_transition_one_line_exit_1(self):
        from utils import submissions
        cid = self._campaign()
        self.sl.set_campaign_state(self.db, cid, 'cancelled')
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'resume', str(cid)]):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        msg = str(cm.exception.code)
        self.assertIn('submissions:', msg)
        self.assertNotIn('\n', msg)

    def test_old_style_flags_rejected(self):
        from utils import submissions
        for bad in (['--status'], ['--dry-run'], ['--pause-campaign', '1']):
            with patch.object(sys, 'argv',
                              ['submissions', '--db', self.db] + bad):
                with self.assertRaises(SystemExit) as cm:
                    submissions.main()
            self.assertNotEqual(cm.exception.code, 0)
```

- [ ] **Step 5: Run the new tests to verify they fail**

Run: `python3 -m pytest test/test_unit.py -k TestSubmissionsVerbs -q`
Expected: FAIL/ERROR — `submissions.main()` still parses the old flat flags (`argparse` errors on the `run`/`status` positional, `submissions.lock` never created).

- [ ] **Step 6: Rewrite the CLI region of `utils/submissions.py`**

Replace the whole `main()` function (old lines ~468-564, from `def main():` through the end of its body — keep the trailing `if __name__ == '__main__':` block) with:

```python
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
    # Bare invocation (no verb) IS status — an explicit default, not a
    # hidden fallthrough (spec Change 1).
    p.set_defaults(verb='status')
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
    resume = sub.add_parser('resume',
                            help='Reactivate a paused campaign')
    resume.add_argument('camp_id', type=int)
    cancel = sub.add_parser('cancel',
                            help='Cancel a campaign (already-submitted '
                                 'rows still get recovered)')
    cancel.add_argument('camp_id', type=int)
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

    if summary:
        print("submissions summary: "
              + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    if (summary.get('held') or summary.get('exhausted')
            or summary.get('would-exhaust') or summary.get('child-missing')
            or summary.get('campaign-paused')
            or summary.get('would-pause-overlap')):
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
            manage_campaign(args.db, args.camp_id, verb)
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        return

    # verb == 'run'
    if not args.dry_run:
        _acquire_lock(args.db)
    _run_pass(args)
```

Also update the module docstring's first line from "Verify-and-resubmit recovery loop for direct-backend submissions." to:

```python
"""Direct-submission subsystem CLI (`submissions`): read-only status,
the verify-and-resubmit + sliced-campaign top-up tick (`run`), and
campaign management verbs.
```

(keep the rest of the docstring unchanged).

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `python3 -m pytest test/test_unit.py -k TestSubmissionsVerbs -q`
Expected: PASS (8 tests)

- [ ] **Step 8: Fix fallout in existing CLI tests, then run the full suite**

`TestRecoverCLI` (and any other test patching `sys.argv` for `main()`) must move to verb spellings: `['recover', '--db', self.db]` → `['submissions', '--db', self.db, 'run']`; `['recover', '--db', self.db, '--dry-run']` → `['submissions', '--db', self.db, 'run', '--dry-run']`. Any assertion on the string `recover summary` becomes `submissions summary`.

Run: `python3 -m pytest test/test_unit.py -q`
Expected: all tests pass (450 = 442 + 8)

- [ ] **Step 9: Commit**

```bash
git add -A utils/submissions.py bin/submissions bin/submissions_cron test/test_unit.py
git commit -m "feat: rename recover -> submissions with safe-by-default verb CLI"
```

---

### Task 2: Exit-code honesty — count failure and lingering pause

**Files:**
- Modify: `utils/submissions.py` (`_run_pass` only)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `_run_pass(args)` from Task 1; `submission_ledger.all_campaigns(db)`; `top_up`'s existing `count-error` summary key (already emitted when the queue count fails).
- Produces: two new exit-2 summary keys — `count-error` (now in the exit set) and `paused-campaign` (count of campaigns observed paused).

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
class TestSubmissionsExitHonesty(unittest.TestCase):
    """A stalled loop must not impersonate a healthy one: queue-count
    failure and lingering paused campaigns exit 2 every tick."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')

    def test_count_error_exits_2(self):
        from utils import submissions
        with patch.object(submissions, 'top_up',
                          return_value={'count-error': 1}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        self.assertEqual(cm.exception.code, 2)

    def test_lingering_paused_campaign_exits_2(self):
        from utils import submissions
        cid = self.sl.create_campaign(
            self.db, tarball='cnf.mu2e.P.C.0.tar',
            entry={'tarball': 'cnf.mu2e.P.C.0.tar', 'njobs': 4},
            slice_size=2)
        self.sl.set_campaign_state(self.db, cid, 'paused',
                                   note='paused on a PREVIOUS tick')
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        self.assertEqual(cm.exception.code, 2)

    def test_lingering_paused_exits_2_under_dry_run(self):
        from utils import submissions
        cid = self.sl.create_campaign(
            self.db, tarball='cnf.mu2e.P2.C.0.tar',
            entry={'tarball': 'cnf.mu2e.P2.C.0.tar', 'njobs': 4},
            slice_size=2)
        self.sl.set_campaign_state(self.db, cid, 'paused')
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run', '--dry-run']):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        self.assertEqual(cm.exception.code, 2)

    def test_clean_run_still_exits_0(self):
        from utils import submissions
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            submissions.main()  # no SystemExit
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_unit.py -k TestSubmissionsExitHonesty -q`
Expected: `test_count_error_exits_2`, `test_lingering_paused_campaign_exits_2`, `test_lingering_paused_exits_2_under_dry_run` FAIL (main returns instead of exiting 2); `test_clean_run_still_exits_0` passes.

- [ ] **Step 3: Implement in `_run_pass`**

In `utils/submissions.py`, inside `_run_pass`, replace the block from `if args.row is None:` through the `sys.exit(2)` with:

```python
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
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python3 -m pytest test/test_unit.py -k TestSubmissionsExitHonesty -q` → PASS (4 tests)
Run: `python3 -m pytest test/test_unit.py -q` → all pass (454). If an existing `TestRecoverCLI`-family test seeded a paused campaign and asserted exit 0, update it to expect 2 — that behavior change is this task's point.

- [ ] **Step 5: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "feat: exit 2 on queue-count failure and lingering paused campaigns"
```

---

### Task 3: Clean enqueue errors + refuse `--enqueue --no-ledger`

**Files:**
- Modify: `utils/submit.py` (`_enqueue_entries` ~line 292; enqueue validation block in `main()` ~line 827)
- Test: `test/test_unit.py` (`TestEnqueue` additions/updates)

**Interfaces:**
- Consumes: `_enqueue_entries(entries_to_submit, map_path, opts)`; `submission_ledger.create_campaign` (raises `ValueError` on duplicate live campaign, `sqlite3.Error` on DB problems).
- Produces: operator-reachable enqueue failures exit via `sys.exit('submit_map: <one line>')`; new argument refusal in `main()`.

- [ ] **Step 1: Write the failing tests**

Append inside `test/test_unit.py` (new class after `TestEnqueue`):

```python
class TestEnqueueErrorStyle(unittest.TestCase):
    """Operator-reachable enqueue failures are one-line submit_map:
    messages, not tracebacks; --enqueue --no-ledger is refused."""

    def setUp(self):
        import tempfile
        from types import SimpleNamespace
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, 'sub.db')
        self.opts = SimpleNamespace(
            ledger_db=self.db, slice_size=10, dry_run=False,
            memory=None, disk=None, expected_lifetime=None)

    def _entry(self, tarball='cnf.mu2e.E.C.0.tar'):
        return {'tarball': tarball, 'njobs': 50}

    def test_duplicate_enqueue_one_line_no_traceback(self):
        from utils import submit
        submit._enqueue_entries([(0, self._entry())], 'm.json', self.opts)
        with self.assertRaises(SystemExit) as cm:
            submit._enqueue_entries([(0, self._entry())], 'm.json',
                                    self.opts)
        msg = str(cm.exception.code)
        self.assertTrue(msg.startswith('submit_map: '), msg)
        self.assertNotIn('\n', msg)
        self.assertNotIn('Traceback', msg)

    def test_db_error_one_line(self):
        from utils import submit
        self.opts.ledger_db = os.path.join(self.tmp, 'no', 'such',
                                           'dir', 'sub.db')
        with self.assertRaises(SystemExit) as cm:
            submit._enqueue_entries([(0, self._entry())], 'm.json',
                                    self.opts)
        self.assertTrue(str(cm.exception.code).startswith('submit_map: '))

    def test_enqueue_no_ledger_refused(self):
        from utils import submit
        import io as _io
        buf = _io.StringIO()
        with patch('sys.stdout', buf), \
             patch.object(sys, 'argv',
                          ['submit_map', '--map', 'nonexistent.json',
                           '--backend', 'direct', '--enqueue',
                           '--no-ledger']):
            with self.assertRaises(SystemExit) as cm:
                submit.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn('--no-ledger contradicts it', buf.getvalue())
```

(Note: `--backend direct` still exists at this task — Task 6 removes it; this test is updated there.)

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_unit.py -k TestEnqueueErrorStyle -q`
Expected: `test_duplicate_enqueue_one_line_no_traceback` and `test_db_error_one_line` FAIL with raw `ValueError`/`sqlite3.OperationalError` tracebacks; `test_enqueue_no_ledger_refused` FAILs (exits later with "map file not found" instead of the refusal).

- [ ] **Step 3: Implement**

In `utils/submit.py` add `import sqlite3` to the imports, then replace `_enqueue_entries` with:

```python
def _enqueue_entries(entries_to_submit, map_path, opts):
    """Register entries as sliced-submission campaigns (cursor 0) —
    submits NOTHING; the submissions cron feeds slices while the
    mu2epro queue is under its cap. Nothing has been submitted when
    this fails, so failures are hard errors — but operator-reachable
    ones (duplicate live campaign, bad njobs, DB trouble) exit with a
    ONE-LINE submit_map: message, never a traceback. Returns new
    campaign ids."""
    ids = []
    for idx, entry in entries_to_submit:
        njobs = njobs_of(entry)
        if njobs is None:
            sys.exit(f"submit_map: entry {idx} has no njobs (generic "
                     f"tarball) — a campaign needs a job count to slice")
        if njobs < 1:
            sys.exit(f"submit_map: entry {idx} has njobs={njobs} — "
                     f"a campaign needs a positive job count")
        snap = _snapshot_entry(entry, _effective_resources(entry, opts))
        if opts.dry_run:
            print(f"[DRY RUN] would enqueue entry {idx}: "
                  f"{tarball_of(entry)} njobs={njobs_of(entry)} "
                  f"slice={opts.slice_size}")
            continue
        try:
            camp_id = submission_ledger.create_campaign(
                opts.ledger_db, tarball=tarball_of(entry), entry=snap,
                slice_size=opts.slice_size, map_path=map_path)
        except (ValueError, sqlite3.Error) as e:
            sys.exit(f"submit_map: {e}")
        print(f"Enqueued campaign {camp_id}: {tarball_of(entry)} "
              f"njobs={njobs_of(entry)} slice={opts.slice_size} "
              f"(db {opts.ledger_db})")
        ids.append(camp_id)
    return ids
```

In `main()`, inside the existing `if args.enqueue:` validation block, add as the FIRST check:

```python
        if args.no_ledger:
            print("submit_map: --enqueue registers a campaign in the "
                  "ledger DB; --no-ledger contradicts it")
            sys.exit(1)
```

- [ ] **Step 4: Run new tests, fix message-fragment fallout, run suite**

Run: `python3 -m pytest test/test_unit.py -k "TestEnqueueErrorStyle or TestEnqueue" -q`
Existing `TestEnqueue` tests that assert on the old `Error: entry ...` prefix must be updated to the `submit_map: entry ...` prefix (the fragments "no njobs (generic" and "positive job count" are unchanged).
Run: `python3 -m pytest test/test_unit.py -q` → all pass (457).

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "feat: one-line operator errors for enqueue; refuse --enqueue --no-ledger"
```

---

### Task 4: Pause-note preservation + `pause --note`

**Files:**
- Modify: `utils/submission_ledger.py` (`set_campaign_state`, ~line 255)
- Modify: `utils/submissions.py` (`build_parser` pause verb; `manage_campaign`; the pause/resume/cancel dispatch in `main()`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `set_campaign_state(db_path, camp_id, state, note=None)`; `manage_campaign(db_path, camp_id, action)` from Task 1.
- Produces: `manage_campaign(db_path, camp_id, action, note=None)`; resume (`state='active'`) leaves the stored note untouched.

- [ ] **Step 1: Write the failing tests**

```python
class TestPauseNotePreservation(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        self.cid = sl.create_campaign(
            self.db, tarball='cnf.mu2e.N.C.0.tar',
            entry={'tarball': 'cnf.mu2e.N.C.0.tar', 'njobs': 4},
            slice_size=2)

    def _note(self):
        return self.sl.all_campaigns(self.db)[0]['note']

    def test_resume_preserves_pause_note(self):
        self.sl.set_campaign_state(self.db, self.cid, 'paused',
                                   note='crash-window suspected')
        self.sl.set_campaign_state(self.db, self.cid, 'active')
        self.assertEqual(self._note(), 'crash-window suspected')

    def test_resume_clears_closed_utc(self):
        self.sl.set_campaign_state(self.db, self.cid, 'paused', note='x')
        self.sl.set_campaign_state(self.db, self.cid, 'active')
        self.assertIsNone(self.sl.all_campaigns(self.db)[0]['closed_utc'])

    def test_pause_verb_custom_note(self):
        from utils import submissions
        with patch.object(sys, 'argv',
                          ['submissions', '--db', self.db, 'pause',
                           str(self.cid), '--note', 'draining for O2']):
            submissions.main()
        self.assertEqual(self._note(), 'draining for O2')

    def test_pause_verb_default_note(self):
        from utils import submissions
        with patch.object(sys, 'argv',
                          ['submissions', '--db', self.db, 'pause',
                           str(self.cid)]):
            submissions.main()
        self.assertEqual(self._note(), 'operator pause')
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_unit.py -k TestPauseNotePreservation -q`
Expected: `test_resume_preserves_pause_note` FAILs (note is `operator resume`/None after resume); `test_pause_verb_custom_note` errors (`--note` unknown argument). The other two may already pass — fine.

- [ ] **Step 3: Implement**

In `utils/submission_ledger.py`, replace the UPDATE block at the end of `set_campaign_state` (the `closed = ...` line and `con.execute(...)` call) with:

```python
        if state == 'active':
            # Resume: KEEP the note that explains why it was paused —
            # the operator clearing the pause is exactly who needs it.
            con.execute(
                'UPDATE campaigns SET state = ?, closed_utc = NULL '
                'WHERE id = ?', (state, camp_id))
        else:
            con.execute(
                'UPDATE campaigns SET state = ?, closed_utc = ?, '
                'note = ? WHERE id = ?',
                (state, _now(), note, camp_id))
        con.commit()
```

and update its docstring to:

```python
    """Validated campaign transition (see _CAMPAIGN_TRANSITIONS).
    Reactivating a paused campaign clears closed_utc and PRESERVES the
    existing note (the pause reason); other transitions overwrite the
    note."""
```

In `utils/submissions.py`:

1. `build_parser()`: after `pause.add_argument('camp_id', type=int)` add:

```python
    pause.add_argument('--note', default=None,
                       help='Reason recorded on the campaign (default: '
                            '"operator pause")')
```

2. Replace `manage_campaign` with:

```python
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
```

3. In `main()`, the management dispatch becomes:

```python
    if verb in ('pause', 'resume', 'cancel'):
        _acquire_lock(args.db)
        try:
            manage_campaign(args.db, args.camp_id, verb,
                            note=getattr(args, 'note', None))
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        return
```

- [ ] **Step 4: Run new tests + full suite**

Run: `python3 -m pytest test/test_unit.py -k TestPauseNotePreservation -q` → PASS (4)
Run: `python3 -m pytest test/test_unit.py -q` → all pass (461). If an existing ledger test asserts resume writes `operator resume` into the note, update it — preservation is this task's point.

- [ ] **Step 5: Commit**

```bash
git add utils/submission_ledger.py utils/submissions.py test/test_unit.py
git commit -m "feat: preserve pause notes across resume; pause --note"
```

---

### Task 5: Shared scratch-dir helper with cleanup

**Files:**
- Modify: `utils/submissions.py` (`resubmit` ~line 119, `submit_slice` ~line 166, new helper + imports)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `resubmit(row, missing, db_path, dry_run=False, runner=...)` and `submit_slice(camp, n, db_path, runner=...)` — signatures unchanged.
- Produces: `_scratch_map_dir(prefix)` context manager in `utils/submissions.py`.

- [ ] **Step 1: Write the failing tests**

```python
class TestScratchDirCleanup(unittest.TestCase):
    """Hourly cron must not accumulate /tmp scratch dirs: the child
    submit_map's map/indices files are removed after it returns,
    success or failure."""

    def setUp(self):
        import tempfile
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        self.camp = {'id': 1, 'cursor': 0, 'slice_size': 5,
                     'tarball': 'cnf.mu2e.S.C.0.tar',
                     'entry': {'tarball': 'cnf.mu2e.S.C.0.tar',
                               'njobs': 10}}
        self.row = {'id': 7, 'tarball': 'cnf.mu2e.S.C.0.tar',
                    'entry': {'tarball': 'cnf.mu2e.S.C.0.tar',
                              'njobs': 10}}

    def _run_and_capture_dir(self, fn, rc):
        from utils import submissions
        import types
        seen = {}
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(*a, **k):
            seen['dir'] = real_mkdtemp(*a, **k)
            return seen['dir']

        runner = lambda cmd, **k: types.SimpleNamespace(returncode=rc)
        with patch.object(submissions.tempfile, 'mkdtemp', spy_mkdtemp):
            fn(runner)
        return seen['dir']

    def test_submit_slice_cleans_up_on_success(self):
        from utils import submissions
        d = self._run_and_capture_dir(
            lambda r: submissions.submit_slice(self.camp, 5, self.db,
                                               runner=r), 0)
        self.assertFalse(os.path.exists(d))

    def test_submit_slice_cleans_up_on_failure(self):
        from utils import submissions
        d = self._run_and_capture_dir(
            lambda r: submissions.submit_slice(self.camp, 5, self.db,
                                               runner=r), 1)
        self.assertFalse(os.path.exists(d))

    def test_resubmit_cleans_up(self):
        from utils import submissions
        d = self._run_and_capture_dir(
            lambda r: submissions.resubmit(self.row, [2, 4], self.db,
                                           runner=r), 0)
        self.assertFalse(os.path.exists(d))
```

`import tempfile` is already at the top of the test file's stdlib imports (verify; add if missing).

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_unit.py -k TestScratchDirCleanup -q`
Expected: all three FAIL — the dirs still exist.

- [ ] **Step 3: Implement**

In `utils/submissions.py` add to the imports: `import contextlib`, `import shutil`. Add the helper above `resubmit`:

```python
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
```

Rewrite `resubmit`'s body to use it (docstring unchanged):

```python
    entry = {k: v for k, v in row['entry'].items() if k != 'firstjob'}
    with _scratch_map_dir('recover-') as tmpdir:
        map_path = tmpdir / 'recovery-map.json'
        map_path.write_text(json.dumps([entry], indent=2) + '\n')
        idx_path = tmpdir / 'indices.txt'
        idx_path.write_text(f"# {row['tarball']}\n"
                            + '\n'.join(str(i) for i in missing) + '\n')
        cmd = [str(SUBMIT_MAP), '--map', str(map_path), '--backend',
               'direct', '--indices-file', str(idx_path),
               '--ledger-parent', str(row['id']),
               '--ledger-db', str(db_path)]
        if dry_run:
            cmd.append('--dry-run')
        print(f"  resubmit: {' '.join(cmd)}")
        res = runner(cmd)
    return res.returncode == 0
```

Rewrite `submit_slice`'s body the same way (docstring unchanged):

```python
    with _scratch_map_dir('campaign-') as tmpdir:
        map_path = tmpdir / 'campaign-map.json'
        map_path.write_text(json.dumps([camp['entry']], indent=2) + '\n')
        cmd = [str(SUBMIT_MAP), '--map', str(map_path), '--backend',
               'direct', '--first', str(camp['cursor']),
               '--num', str(n), '--ledger-db', str(db_path)]
        print(f"  campaign {camp['id']}: slice first={camp['cursor']} "
              f"num={n}: {' '.join(cmd)}")
        res = runner(cmd)
    return res.returncode == 0
```

(`'--backend', 'direct'` still present here — Task 6 removes it.)

- [ ] **Step 4: Run new tests + full suite**

Run: `python3 -m pytest test/test_unit.py -k TestScratchDirCleanup -q` → PASS (3)
Run: `python3 -m pytest test/test_unit.py -q` → all pass (464)

- [ ] **Step 5: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "feat: clean up child-submit scratch dirs (cron /tmp hygiene)"
```

---

### Task 6: Retire `submit_map`'s mu2ejobsub backend

**Files:**
- Modify: `utils/submit.py` (delete `build_mu2ejobsub_argv` ~45-107, `submit_entry` dispatch ~586-599, `_submit_entry_mu2ejobsub` ~602-636, `--backend` flag ~751-756; docstring; help texts)
- Modify: `utils/submissions.py` (drop `'--backend', 'direct'` from the two child argvs)
- Modify: `utils/runmu2e.py` (~line 768, the non-normal-mode error message)
- Modify: `bin/submit_map` (comment header)
- Test: `test/test_unit.py` (delete `TestMu2ejobsubProtocol` (4 tests) and `TestMu2ejobsubArgvFirstjob` (2 tests); add rejection test; update Task 3's `test_enqueue_no_ledger_refused` argv)

**Interfaces:**
- Consumes: `submit_entry_direct(entry, idx, opts)` — unchanged.
- Produces: `submit_map()` calls `submit_entry_direct` directly; no `submit_entry`, no `build_mu2ejobsub_argv`, no `_submit_entry_mu2ejobsub`, no `--backend` argument anywhere.

- [ ] **Step 1: Write the failing test**

```python
class TestSingleBackend(unittest.TestCase):
    """submit_map is single-backend (direct): --backend is gone and
    rejected loudly as an unknown argument."""

    def test_backend_flag_rejected(self):
        from utils import submit
        with patch.object(sys, 'argv',
                          ['submit_map', '--map', 'x.json',
                           '--backend', 'direct']):
            with self.assertRaises(SystemExit) as cm:
                submit.main()
        self.assertEqual(cm.exception.code, 2)  # argparse usage error

    def test_mu2ejobsub_helpers_gone(self):
        from utils import submit
        self.assertFalse(hasattr(submit, 'build_mu2ejobsub_argv'))
        self.assertFalse(hasattr(submit, '_submit_entry_mu2ejobsub'))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_unit.py -k TestSingleBackend -q`
Expected: both FAIL (`--backend` still accepted; helpers still exist).

- [ ] **Step 3: Implement the deletions in `utils/submit.py`**

1. Delete the functions `build_mu2ejobsub_argv`, `submit_entry`, and `_submit_entry_mu2ejobsub` entirely.
2. In `submit_map()`, change the per-entry call from `result = submit_entry(entry, idx, opts)` to `result = submit_entry_direct(entry, idx, opts)`.
3. In `main()`: delete the `--backend` argument definition; delete the `if args.backend != 'direct': ... sys.exit(1)` check inside the enqueue validation block; change the parser description to `'Submit Mu2e grid jobs from a POMS-map JSON via the direct jobsub backend'`; change `--verbose` help to `'Verbose output'`.
4. Remove the `[direct] ` prefix from every help string that carries it (`--first`, `--num`, `--indices`, `--indices-file`, `--ledger-db`, `--ledger-parent`, `--no-ledger`, `--enqueue`, `--slice-size`, `--wftop`, `--prodtools-tar`) — with one backend the qualifier is noise.
5. Replace the module docstring's "Two backends:" section with:

```python
"""
Direct-submit driver for Mu2e grid jobs (single backend).

Builds the `jobsub_submit` argv directly and ships prodtools as a
dropbox tarball. Worker bootstraps `bin/runjob.sh` →
`utils/runmu2e.py` direct mode → per-job pushOutput. The Phase-1
mu2ejobsub backend was retired 2026-07-19 (spec
2026-07-19-workflow-hardening-design.md): template/direct_input/g4bl
entries and HPC submission run via POMS campaigns or the upstream
mu2ejobsub/mu2eg4bl CLIs, never through submit_map.

Plans:
- wiki/pages/2026-04-29-remove-poms-from-submit-loop.md (Phase 1, POMS removal)
- wiki/pages/2026-04-30-phase2-direct-jobsub-implementation.md (Phase 2, direct)
"""
```

6. In `_run_submit`'s docstring change "the result dict both backends share" to "the result dict"; in `_ensure_local_tarball`'s docstring change "Shared by both backends." to "".
7. Verify nothing references the old backend machinery:

```bash
grep -n "backend\|mu2ejobsub" utils/submit.py
```

Expected: hits only in the module docstring (the retirement note) — no `args.backend`, no function names.

- [ ] **Step 4: Update the call sites and messages elsewhere**

1. `utils/submissions.py`: in `resubmit` and `submit_slice` (as written in Task 5), remove the two argv elements `'--backend', 'direct'` — e.g. `cmd = [str(SUBMIT_MAP), '--map', str(map_path), '--indices-file', ...]` and `cmd = [str(SUBMIT_MAP), '--map', str(map_path), '--first', str(camp['cursor']), ...]`.
2. `utils/runmu2e.py` ~line 768: replace the error message:

```python
        print(f"ERROR: direct mode supports normal-mode jobdescs only, "
              f"got '{mode}'. template/direct_input/g4bl entries run "
              f"via POMS campaigns or the upstream mu2ejobsub/mu2eg4bl "
              f"CLIs, not through submit_map.")
```

3. `bin/submit_map` header comment: change the two mu2ejobsub mentions:

```bash
# submit_map - Submit Mu2e grid jobs from a POMS-map JSON (direct
# jobsub backend). Wrapper for utils/submit.py
```

and `# Set up Mu2e environment (needed for mu2ejobsub, mdh, httokendecode)` → `# Set up Mu2e environment (needed for jobsub, mdh, httokendecode)`.

- [ ] **Step 5: Delete the dead tests, fix argv fallout**

1. Delete classes `TestMu2ejobsubProtocol` and `TestMu2ejobsubArgvFirstjob` from `test/test_unit.py`.
2. Update `TestEnqueueErrorStyle.test_enqueue_no_ledger_refused` (Task 3): remove `'--backend', 'direct'` from its argv.
3. Grep for any other test argv carrying `--backend`:

```bash
grep -n "'--backend'" test/test_unit.py
```

Remove the flag pair from each (the behavior under test is unchanged — direct is now implicit).

- [ ] **Step 6: Run the suite**

Run: `python3 -m pytest test/test_unit.py -q`
Expected: all pass (464 − 6 removed + 2 new = 460).

- [ ] **Step 7: Commit**

```bash
git add utils/submit.py utils/submissions.py utils/runmu2e.py bin/submit_map test/test_unit.py
git commit -m "feat: retire submit_map's mu2ejobsub backend — single-backend direct"
```

---

### Task 7: Docs — wiki runbook respell + operator quickstart, EXAMPLES regen

**Files:**
- Modify: `wiki/pages/2026-07-18-direct-recovery-loop.md`
- Modify: `docs/EXAMPLES_schema.md`
- Regenerate: `EXAMPLES.md` (per the schema — treat as derived artifact, full regen of the affected sections)
- Modify: `wiki/log.md` (append entry)

**Interfaces:**
- Consumes: the final CLI shapes from Tasks 1-6 (verify every command you document against `utils/submissions.py build_parser()` and `utils/submit.py main()` argparse — do not document from memory).

- [ ] **Step 1: Respell the wiki runbook**

In `wiki/pages/2026-07-18-direct-recovery-loop.md`:
- Every `recover` invocation → the verb spelling (`recover --status` → `submissions` or `submissions status`; `recover --dry-run` → `submissions run --dry-run`; `recover --pause-campaign N` → `submissions pause N`; bare `recover` → `submissions run`; `recover_cron` → `submissions_cron`; crontab line → `17 * * * * <prodtools>/bin/submissions_cron`; cron log `recover-YYYYMMDD.log` → `submissions-YYYYMMDD.log`; lock file `recover.lock` → `submissions.lock`).
- The 5-item pre-activation checklist: keep the items, update spellings (e.g. "real `recover --dry-run` on a genuine drained row" → "real `submissions run --dry-run` on a genuine drained row").
- Update the exit-2 list to include the two new causes (queue-count failure; lingering paused campaign, which repeats every tick).

- [ ] **Step 2: Add the operator quickstart section to the same page**

New section `## Operator quickstart` containing:
1. **Decision tree**: POMS-owned entries (launched by POMS campaigns; recovery via `mkrecovery`) vs submit_map/direct entries (ledger-watched; recovery automatic). template/direct_input/g4bl entries and HPC: POMS campaigns or upstream `mu2ejobsub`/`mu2eg4bl` CLIs — never `submit_map`. One warning line: never submit the same entry through both paths — identical deterministic payloads run twice; ask how an entry was submitted before resubmitting anything.
2. **Human ksu environment** for running `submit_map` as mu2epro (from the `/mu2epro-submit` skill): after `ksu mu2epro`, re-export `USER=mu2epro LOGNAME=mu2epro HOME=$(getent passwd mu2epro | cut -d: -f6)` and set `XDG_RUNTIME_DIR=/run/user/$(id -u)` before submitting, else `condor_vault_storer` fails or the wrong submitter is recorded; verify with `jobsub_q --user mu2epro` after submitting.
3. **Reading `submissions` output**: the ledger table (id/state/attempt/parent/#idx), the campaigns table (id/state/cursor/njobs/slice), the `queue cap in effect` line.
4. **Exit-2 playbook** — one line per cause: held → inspect with `jobsub_q --jobid`, release or rm, then next tick handles it; exhausted → human root-cause (deterministic payloads: retry won't fix); child-missing → verify the indices manually, chain is unwatched; campaign paused (submit failure) → check `submit-YYYYMMDD.log` + `jobsub_q`, then `submissions resume N`; campaign paused (crash-window) → reconcile cursor vs ledger before resuming; count-error → `jobsub_q` itself is broken, campaigns are starving; paused-campaign (lingering) → someone must resume or cancel.

- [ ] **Step 3: Update `docs/EXAMPLES_schema.md`**

- Replace the `recover` tool block with `submissions`: name, one-line role ("direct-submission subsystem CLI — status/run/pause/resume/cancel"), the verb table, flags per verb, the read-only guarantees (`status` and `run --dry-run`), the extended exit-2 list.
- `submit_map` block: remove `--backend` from the flag list; note single-backend direct; the `--enqueue --no-ledger` refusal; drop the "direct backend only" qualifier from the resource-keys tribal bullet (now unconditional).
- Tribal-knowledge bullets: update "recover" spellings; add one bullet: "template/direct_input/g4bl/HPC entries are not submittable via submit_map — POMS campaigns or upstream mu2ejobsub/mu2eg4bl CLIs".

- [ ] **Step 4: Regenerate the affected EXAMPLES.md sections**

Follow `docs/EXAMPLES_schema.md` (EXAMPLES.md is a derived artifact — never hand-edit against the schema). Regenerate the `submit_map` and `submissions` sections and the tools list line (`recover` → `submissions`) from the CURRENT argparse definitions. Spot-check per the schema's rules: every flag you document must exist in `utils/submit.py` / `utils/submissions.py` argparse — grep before including.

- [ ] **Step 5: Append the wiki log entry**

Append to `wiki/log.md` a dated `## [2026-07-19] update | workflow hardening — submissions CLI, single-backend submit_map` entry summarizing: the rename + verbs (safe-by-default), the two new exit-2 causes, the flag refusals, pause-note preservation, tmp cleanup, backend retirement (with the boundary: upstream mu2ejobsub/POMS untouched), and that the ownership key was decided against (pointer to the spec's "Decided against" section).

- [ ] **Step 6: Verify and commit**

```bash
grep -rn "bin/recover\b\|recover_cron\|recover --status\|recover --dry-run\|--pause-campaign\|--backend" EXAMPLES.md wiki/pages/2026-07-18-direct-recovery-loop.md docs/EXAMPLES_schema.md
```

Expected: no hits (except historical/archival pages quoting the old design docs, which are NOT updated — history stays true).

Run: `python3 -m pytest test/test_unit.py -q` → all pass (460).

```bash
git add wiki/pages/2026-07-18-direct-recovery-loop.md docs/EXAMPLES_schema.md EXAMPLES.md wiki/log.md
git commit -m "docs: submissions CLI + single-backend submit_map — runbook respell, operator quickstart, EXAMPLES regen"
```
