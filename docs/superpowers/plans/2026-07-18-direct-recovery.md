# Direct-Backend Recovery Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automated verify-and-resubmit recovery for `submit_map --backend direct` submissions: a submission ledger written at submit time, a `recover` loop that drain-checks via jobsub_q, SAM-verifies outputs, and resubmits missing indices with an attempt cap, run hourly from mu2epro's crontab.

**Architecture:** `submit_map --backend direct` appends one row per successful submission to a stdlib-sqlite3 ledger (entry snapshot + absolute cnf indices + full jobsub id). `bin/recover` processes each `active` row: skip while jobs are queued (report held jobs, never act on them), verify the row's indices against SAM using `mkrecovery`'s file-map machinery, then close as `complete`, resubmit exactly the missing indices through the `submit_map` CLI (child row, attempt+1), or close as `exhausted` at the cap. `bin/recover_cron` wraps it with flock + env + token check for mu2epro's crontab.

**Tech Stack:** Python 3 stdlib only in new modules (`sqlite3`, `json`, `subprocess`, `argparse`); existing `utils/` helpers (`Mu2eJobPars`, `mkrecovery.build_file_maps`, `samweb_wrapper.files_in_dataset`); bash for `bin/` wrappers; `unittest` in `test/test_unit.py`.

**Spec:** `docs/superpowers/specs/2026-07-18-direct-recovery-design.md` (approved). Deviation noted there → here: the spec's `chain_attempt(id)` API is realized as the `attempt` column, computed at `record_submission` time from the parent row — no separate query function.

## Global Constraints

- **Stdlib only in the submit path.** `utils/submission_ledger.py` and everything `utils/submit.py` imports must not import SQLAlchemy or anything outside the Python stdlib + existing `utils/` modules (bare ops env, no `pyenv ana`).
- **Default DB path exactly** `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, overridable by env var `MU2E_SUBMISSION_DB` and by `--ledger-db` / `--db` flags.
- **States exactly** `active | complete | recovered | exhausted`. No other values ever stored.
- **Ledger indices are ABSOLUTE cnf indices** (`firstjob + entry-relative`), stored sorted.
- **Reconstructed recovery entries drop `firstjob`** — `submit_map --indices` rejects windowed entries; absolute indices already carry the window.
- **A ledger write failure after a successful submission must never raise** — print a loud warning containing every field needed to insert the row manually.
- **The loop never runs `condor_rm`/`condor_release`** — held jobs are reported and skipped.
- **No token fetching or refreshing anywhere** — a missing/invalid token is reported (non-zero exit) and nothing is submitted.
- **Fail loudly, no fallbacks**: verification errors (unlocatable tarball, no output datasets, SAM failure) keep the row `active` and are reported; a row is never guessed complete.
- **Test suite must pass standalone**: `python3 test/test_unit.py` → `Ran <N> tests / OK` with no network, no SAM, no jobsub. All new tests use injected fakes; none may contact external services. (The suite had 344 tests at branch start; counts in steps are indicative, not binding.)
- **Do not hand-edit `EXAMPLES.md`** except via the full-regeneration procedure in `docs/EXAMPLES_schema.md` (Task 6).
- Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov`

## File Structure

- Create `utils/submission_ledger.py` — sqlite3 store; owns schema, states, DEFAULT_DB.
- Modify `utils/submit.py` — full-jobsub-id parsing, ledger hook (direct backend only), `--ledger-db`/`--ledger-parent`/`--no-ledger` flags.
- Modify `utils/mkrecovery.py` — optional `indices=` scope on `build_file_maps` (single home for the expected-files scan).
- Create `utils/recover.py` — queue_state / verify_row / process_row / resubmit / status / CLI.
- Create `bin/recover` — env wrapper (pattern of `bin/submit_map`).
- Create `bin/recover_cron` — flock + env + token gate + logging, for mu2epro's crontab.
- Modify `test/test_unit.py` — four new test classes (no sqlalchemy needed; sqlite3 is stdlib).
- Modify `docs/EXAMPLES_schema.md`, regenerate `EXAMPLES.md`; create `wiki/pages/2026-07-18-direct-recovery-loop.md`; append `wiki/log.md`.

---

### Task 1: Submission ledger module

**Files:**
- Create: `utils/submission_ledger.py`
- Test: `test/test_unit.py` (append class `TestSubmissionLedger`)

**Interfaces:**
- Consumes: nothing from other tasks (stdlib only).
- Produces (used by Tasks 2, 4, 5):
  - `DEFAULT_DB: str` — `os.environ.get('MU2E_SUBMISSION_DB', '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db')`
  - `STATES: tuple` — `('active', 'complete', 'recovered', 'exhausted')`
  - `record_submission(db_path, *, tarball, entry, indices, jobsub_id, cluster_id, map_path=None, parent_id=None) -> int` (new row id; `attempt` = parent's attempt + 1, else 1; raises `ValueError` on unknown parent)
  - `open_rows(db_path) -> list[dict]` — active rows, oldest first; each dict has keys `id, created_utc, state, attempt, parent_id, map_path, tarball, entry (parsed dict), indices (parsed list), jobsub_id, cluster_id, closed_utc, note`
  - `all_rows(db_path) -> list[dict]` — same shape, every state
  - `close_row(db_path, row_id, state, note=None) -> None` — raises `ValueError` on invalid state or if the row isn't `active`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py` (before the trailing `if __name__ == '__main__':` block, matching file conventions):

```python
# ---------------------------------------------------------------------------
# Submission ledger (utils/submission_ledger.py) — direct-backend recovery
# ---------------------------------------------------------------------------
class TestSubmissionLedger(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 5, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}

    def _record(self, indices=(0, 1, 2), parent=None):
        return self.sl.record_submission(
            self.db, tarball=self.entry['tarball'], entry=self.entry,
            indices=list(indices), jobsub_id='12345678.0@jobsub03.fnal.gov',
            cluster_id='12345678', map_path='/tmp/map.json', parent_id=parent)

    def test_record_and_read_roundtrip(self):
        rid = self._record()
        rows = self.sl.open_rows(self.db)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['id'], rid)
        self.assertEqual(row['state'], 'active')
        self.assertEqual(row['attempt'], 1)
        self.assertIsNone(row['parent_id'])
        self.assertEqual(row['indices'], [0, 1, 2])
        self.assertEqual(row['entry'], self.entry)
        self.assertEqual(row['jobsub_id'], '12345678.0@jobsub03.fnal.gov')
        self.assertEqual(row['cluster_id'], '12345678')

    def test_indices_stored_sorted(self):
        self._record(indices=(7, 2, 5))
        self.assertEqual(self.sl.open_rows(self.db)[0]['indices'], [2, 5, 7])

    def test_child_attempt_increments(self):
        rid = self._record()
        child = self._record(indices=(2,), parent=rid)
        rows = {r['id']: r for r in self.sl.open_rows(self.db)}
        self.assertEqual(rows[child]['attempt'], 2)
        self.assertEqual(rows[child]['parent_id'], rid)

    def test_unknown_parent_rejected(self):
        with self.assertRaises(ValueError):
            self._record(parent=999)

    def test_close_row_removes_from_open(self):
        rid = self._record()
        self.sl.close_row(self.db, rid, 'complete', note='all verified')
        self.assertEqual(self.sl.open_rows(self.db), [])
        allr = self.sl.all_rows(self.db)
        self.assertEqual(allr[0]['state'], 'complete')
        self.assertEqual(allr[0]['note'], 'all verified')
        self.assertIsNotNone(allr[0]['closed_utc'])

    def test_close_invalid_state_rejected(self):
        rid = self._record()
        with self.assertRaises(ValueError):
            self.sl.close_row(self.db, rid, 'bogus')
        with self.assertRaises(ValueError):
            self.sl.close_row(self.db, rid, 'active')

    def test_close_nonactive_row_rejected(self):
        rid = self._record()
        self.sl.close_row(self.db, rid, 'complete')
        with self.assertRaises(ValueError):
            self.sl.close_row(self.db, rid, 'exhausted')

    def test_missing_db_dir_fails_loudly(self):
        import sqlite3
        with self.assertRaises(sqlite3.OperationalError):
            self.sl.record_submission(
                '/nonexistent-dir-recovery-test/sub.db', tarball='t',
                entry={}, indices=[0], jobsub_id=None, cluster_id='1')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestSubmissionLedger -v` (from repo root)
Expected: every test ERRORs with `ModuleNotFoundError: No module named 'utils.submission_ledger'`

- [ ] **Step 3: Write the implementation**

Create `utils/submission_ledger.py`:

```python
"""Submission ledger for the direct backend (recovery loop state).

One row per `submit_map --backend direct` submission, including the
recovery loop's own resubmissions (chained via parent_id, attempt+1).
POMS-backend submissions never touch this ledger, so the recovery loop
cannot race POMS by construction.

Stdlib sqlite3 ONLY — this module is imported by the submit path, which
runs as mu2epro in the bare ops environment (no pyenv ana, no
SQLAlchemy; see reference_pyenv_ana_for_db).

The DB lives at a stable absolute path: mu2epro submissions run from
throwaway /tmp workdirs, so a repo-relative path would scatter state.
The parent directory is a one-time ops creation — a missing directory
is surfaced (sqlite3.OperationalError), never silently mkdir'd over.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get(
    'MU2E_SUBMISSION_DB',
    '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db')

STATES = ('active', 'complete', 'recovered', 'exhausted')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_utc  TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'active',
  attempt      INTEGER NOT NULL DEFAULT 1,
  parent_id    INTEGER REFERENCES submissions(id),
  map_path     TEXT,
  tarball      TEXT NOT NULL,
  entry_json   TEXT NOT NULL,
  indices_json TEXT NOT NULL,
  jobsub_id    TEXT,
  cluster_id   TEXT,
  closed_utc   TEXT,
  note         TEXT
)
"""


def _connect(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def record_submission(db_path, *, tarball, entry, indices, jobsub_id,
                      cluster_id, map_path=None, parent_id=None):
    """Append one submission row; return its id.

    entry is snapshotted verbatim (recovery must survive map edits and
    vanished /tmp workdirs); indices are ABSOLUTE cnf indices, stored
    sorted. attempt = parent's attempt + 1 (1 for an original
    submission); an unknown parent_id is a ValueError.
    """
    con = _connect(db_path)
    try:
        attempt = 1
        if parent_id is not None:
            parent = con.execute(
                'SELECT attempt FROM submissions WHERE id = ?',
                (parent_id,)).fetchone()
            if parent is None:
                raise ValueError(f"ledger has no row {parent_id} (parent)")
            attempt = parent['attempt'] + 1
        cur = con.execute(
            'INSERT INTO submissions '
            '(created_utc, state, attempt, parent_id, map_path, tarball, '
            ' entry_json, indices_json, jobsub_id, cluster_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (_now(), 'active', attempt, parent_id, map_path, tarball,
             json.dumps(entry), json.dumps(sorted(indices)),
             jobsub_id, cluster_id))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _to_dict(row):
    d = dict(row)
    d['entry'] = json.loads(d.pop('entry_json'))
    d['indices'] = json.loads(d.pop('indices_json'))
    return d


def open_rows(db_path):
    """Active rows, oldest first, entry/indices JSON parsed."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM submissions WHERE state = 'active' ORDER BY id"
        ).fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        con.close()


def all_rows(db_path):
    """Every row regardless of state, oldest first."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            'SELECT * FROM submissions ORDER BY id').fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        con.close()


def close_row(db_path, row_id, state, note=None):
    """Move an active row to a terminal state. Closing a non-active row
    (or to a non-terminal state) is a ValueError — state transitions are
    active → {complete, recovered, exhausted}, nothing else."""
    if state not in STATES or state == 'active':
        raise ValueError(f"invalid closing state: {state}")
    con = _connect(db_path)
    try:
        cur = con.execute(
            'UPDATE submissions SET state = ?, closed_utc = ?, note = ? '
            "WHERE id = ? AND state = 'active'",
            (state, _now(), note, row_id))
        if cur.rowcount != 1:
            raise ValueError(f"no active row {row_id} to close")
        con.commit()
    finally:
        con.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestSubmissionLedger -v`
Expected: 8 tests, all PASS. Then the whole suite: `python3 test/test_unit.py 2>&1 | grep -E "^(OK|FAILED|Ran )"` → `Ran <N> tests` / `OK`.

- [ ] **Step 5: Commit**

```bash
git add utils/submission_ledger.py test/test_unit.py
git commit -m "feat: submission ledger for direct-backend recovery

Stdlib-sqlite3 store, one row per direct submission; entry snapshot,
absolute indices, attempt chains via parent_id. States:
active|complete|recovered|exhausted.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 2: submit_map ledger hook + full jobsub id

**Files:**
- Modify: `utils/submit.py` (imports ~line 36; `_run_submit` ~119-163; `_parse_cluster_id` ~165-180; `submit_entry_direct` tail ~434; argparse in `main()` ~590-641)
- Test: `test/test_unit.py` (append class `TestSubmitLedgerHook`)

**Interfaces:**
- Consumes: `submission_ledger.record_submission`, `submission_ledger.DEFAULT_DB` (Task 1).
- Produces (relied on by Task 4's resubmit): CLI flags `--ledger-db PATH`, `--ledger-parent ID`, `--no-ledger` on `submit_map`; `_run_submit` result dict gains key `jobsub_id` (full `cluster.proc@schedd` or None); module function `_parse_jobsub_id(stdout) -> str|None`; `_record_in_ledger(entry, firstjob, jobset, result, opts) -> None` (never raises).

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
class TestSubmitLedgerHook(unittest.TestCase):
    """Direct-backend ledger hook in utils/submit.py."""

    def test_parse_jobsub_id_full_form(self):
        from utils.submit import _parse_jobsub_id
        out = ("Transferring files...\n"
               "1 job(s) submitted to cluster 12345678.\n"
               "Use job id 12345678.0@jobsub03.fnal.gov to retrieve output\n")
        self.assertEqual(_parse_jobsub_id(out),
                         '12345678.0@jobsub03.fnal.gov')

    def test_parse_jobsub_id_absent(self):
        from utils.submit import _parse_jobsub_id
        self.assertIsNone(_parse_jobsub_id("submitted to cluster 12345678\n"))

    def test_run_submit_carries_jobsub_id(self):
        from utils import submit
        fake = MagicMock(
            returncode=0, stderr='',
            stdout='1 job(s) submitted to cluster 12345678.\n'
                   'Use job id 12345678.0@jobsub03.fnal.gov to retrieve output\n')
        with patch('utils.submit.subprocess.run', return_value=fake):
            r = submit._run_submit(['jobsub_submit'], 'cnf.tar', 3)
        self.assertEqual(r['status'], 'submitted')
        self.assertEqual(r['jobsub_id'], '12345678.0@jobsub03.fnal.gov')

    def _opts(self, db, parent=None):
        import argparse
        return argparse.Namespace(ledger_db=db, ledger_parent=parent,
                                  no_ledger=False, map='/tmp/m.json')

    def test_record_in_ledger_absolute_indices(self):
        import tempfile
        from utils import submit, submission_ledger
        db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        entry = {'tarball': 'cnf.mu2e.T.C.0.tar', 'njobs': 3, 'firstjob': 100}
        result = {'tarball': 'cnf.mu2e.T.C.0.tar', 'cluster_id': '1',
                  'jobsub_id': '1.0@js.fnal.gov', 'njobs': 3,
                  'status': 'submitted'}
        submit._record_in_ledger(entry, 100, [0, 1, 2], result, self._opts(db))
        row = submission_ledger.open_rows(db)[0]
        self.assertEqual(row['indices'], [100, 101, 102])
        self.assertEqual(row['entry'], entry)
        self.assertEqual(row['jobsub_id'], '1.0@js.fnal.gov')
        self.assertEqual(row['map_path'], '/tmp/m.json')

    def test_record_in_ledger_parent_chains(self):
        import tempfile
        from utils import submit, submission_ledger
        db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        rid = submission_ledger.record_submission(
            db, tarball='t', entry={}, indices=[0, 1],
            jobsub_id='1.0@js', cluster_id='1')
        result = {'tarball': 't', 'cluster_id': '2', 'jobsub_id': '2.0@js',
                  'njobs': 1, 'status': 'submitted'}
        submit._record_in_ledger({}, 0, [1], result, self._opts(db, parent=rid))
        rows = submission_ledger.open_rows(db)
        self.assertEqual(rows[1]['attempt'], 2)
        self.assertEqual(rows[1]['parent_id'], rid)

    def test_ledger_failure_does_not_raise(self):
        from utils import submit
        result = {'tarball': 't', 'cluster_id': '1', 'jobsub_id': None,
                  'njobs': 1, 'status': 'submitted'}
        # nonexistent directory → sqlite3.OperationalError inside, warning out
        submit._record_in_ledger(
            {}, 0, [0], result,
            self._opts('/nonexistent-dir-recovery-test/s.db'))  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestSubmitLedgerHook -v`
Expected: FAIL/ERROR — `_parse_jobsub_id` and `_record_in_ledger` do not exist; `_run_submit` result has no `jobsub_id` key.

- [ ] **Step 3: Implement in `utils/submit.py`**

3a. Add the import after the existing `from utils import jobsub_argv as _jobsub_argv` (line 36):

```python
from utils import submission_ledger
```

3b. Add `_parse_jobsub_id` directly below `_parse_cluster_id` (after line 180):

```python
def _parse_jobsub_id(stdout):
    """Full jobsub id (cluster.proc@schedd) from the 'Use job id ...'
    line. The numeric cluster alone can't be drain-checked — jobsub_q
    needs the schedd."""
    m = re.search(r'job\s+id\s+(\d+(?:\.\d+)?@\S+)', stdout, re.IGNORECASE)
    return m.group(1) if m else None
```

3c. In `_run_submit`, extend the success dict (lines 156-162) to:

```python
    print(f"Submitted cluster: {cluster_id}")
    return {
        'tarball': tarball_name,
        'cluster_id': cluster_id,
        'jobsub_id': _parse_jobsub_id(result.stdout),
        'njobs': njobs,
        'status': 'submitted',
    }
```

(The two `'status': 'failed'` dicts are unchanged — the hook only fires on `submitted`.)

3d. Add `_record_in_ledger` directly below `_run_submit`:

```python
def _record_in_ledger(entry, firstjob, jobset, result, opts):
    """Append a ledger row for a successful direct submission.

    jobset is entry-relative; the ledger stores ABSOLUTE cnf indices
    (firstjob + i). For --indices submissions jobset is already absolute
    and firstjob is 0, so the same expression holds.

    Never raises: the submission already happened, so a ledger failure
    is reported with everything needed to insert the row manually.
    """
    absolute = [firstjob + i for i in jobset]
    try:
        row_id = submission_ledger.record_submission(
            opts.ledger_db,
            tarball=result['tarball'],
            entry=entry,
            indices=absolute,
            jobsub_id=result.get('jobsub_id'),
            cluster_id=result['cluster_id'],
            map_path=opts.map,
            parent_id=opts.ledger_parent,
        )
        print(f"Ledger: row {row_id} recorded in {opts.ledger_db}")
    except Exception as e:
        print(f"WARNING: ledger write failed ({e}) — the submission DID "
              f"go through (cluster {result['cluster_id']}). Record "
              f"manually: tarball={result['tarball']} indices={absolute} "
              f"jobsub_id={result.get('jobsub_id')} "
              f"parent={opts.ledger_parent} db={opts.ledger_db}")
```

3e. In `submit_entry_direct`, replace the final line (`return _run_submit(cmd, tarball_name, len(jobset))`, line 434) with:

```python
    result = _run_submit(cmd, tarball_name, len(jobset))
    if result['status'] == 'submitted' and not opts.no_ledger:
        _record_in_ledger(entry, firstjob, jobset, result, opts)
    return result
```

3f. In `main()`, after the `--indices-file` argument (line 619), add:

```python
    parser.add_argument('--ledger-db', default=submission_ledger.DEFAULT_DB,
                        help='[direct] Submission-ledger sqlite DB '
                             f'(default: {submission_ledger.DEFAULT_DB}, '
                             'env MU2E_SUBMISSION_DB). Every successful '
                             'direct submission is recorded for the '
                             'recovery loop (bin/recover).')
    parser.add_argument('--ledger-parent', type=int, default=None,
                        help='[direct] Ledger row id this submission '
                             'recovers (set by bin/recover; chains '
                             'attempt counting).')
    parser.add_argument('--no-ledger', action='store_true',
                        help='[direct] Do not record this submission in '
                             'the ledger (ad-hoc/test submissions the '
                             'recovery loop must not watch).')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestSubmitLedgerHook -v` → 6 PASS.
Then full suite: `python3 test/test_unit.py 2>&1 | grep -E "^(OK|FAILED|Ran )"` → `OK`.
Then a dry-run sanity check that argparse wiring is coherent (no submission, no ledger write — dry_run returns before `_run_submit`):
`python3 utils/submit.py --help | grep -A2 ledger` → the three new flags print.

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "feat: record direct submissions in the ledger

_run_submit parses the full cluster.proc@schedd jobsub id; every
successful --backend direct submission appends a ledger row (absolute
indices, entry snapshot). --ledger-parent chains recovery rounds;
--no-ledger opts out. Ledger failure after submission warns, never
raises.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 3: Scoped index scan in mkrecovery

**Files:**
- Modify: `utils/mkrecovery.py:16-37` (`build_file_maps`)
- Test: `test/test_unit.py` (append class `TestBuildFileMapsScoped`)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Task 4): `build_file_maps(job_io, datasets, njobs, firstjob=0, indices=None)` — when `indices` is given, scan exactly those indices (`job_outputs(firstjob + idx)`, map values are the indices as passed) instead of `range(njobs)`. Default behavior byte-identical to today.

- [ ] **Step 1: Write the failing test**

Append to `test/test_unit.py`:

```python
class TestBuildFileMapsScoped(unittest.TestCase):
    def test_scoped_scan_matches_windowed_scan(self):
        from utils.jobquery import Mu2eJobPars
        from utils.mkrecovery import build_file_maps
        files = [f"sim.mu2e.In.C.00000000_{i:08d}.art" for i in range(6)]
        tar = _make_tarball(_root_input_jobpars(files))
        try:
            jp = Mu2eJobPars(tar)
            ds = 'sim.mu2e.TestDesc.TestConf.art'
            full = build_file_maps(jp, [ds], njobs=6)[ds]
            self.assertEqual(len(full), 6)
            scoped = build_file_maps(jp, [ds], njobs=0, indices=[1, 4])[ds]
            expect = {f: i for f, i in full.items() if i in (1, 4)}
            self.assertEqual(scoped, expect)
            self.assertEqual(sorted(set(scoped.values())), [1, 4])
        finally:
            os.unlink(tar)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test.test_unit.TestBuildFileMapsScoped -v`
Expected: ERROR — `build_file_maps() got an unexpected keyword argument 'indices'`

- [ ] **Step 3: Implement**

In `utils/mkrecovery.py`, change the `build_file_maps` signature and loop (lines 16-37); docstring paragraph 1 gains the scope sentence, the loop iterates the scope:

```python
def build_file_maps(job_io, datasets, njobs, firstjob=0, indices=None):
    """One pass over the cnf's index window building, for each dataset in
    `datasets`, its {filename: window-relative index} map. job_outputs
    returns every output stream per call, so a single scan serves all of
    an entry's datasets (previously one full njobs-scan per dataset —
    and one fresh tarball parse each, megabytes for mixing cnfs).

    With `indices` given, scan exactly those indices instead of
    range(njobs) — map values are the indices as passed (the recovery
    loop passes ABSOLUTE cnf indices with firstjob=0, so values come
    back in the caller's own index space). njobs is ignored in that
    case.

    Structured dataset compare — a substring test would false-match
    sibling dsconfs where one is a prefix of the other (e.g. ..._v1_4 vs
    ..._v1_4-000).
    """
    wanted = set(datasets)
    maps = {ds: {} for ds in datasets}
    scope = indices if indices is not None else range(njobs)
    for job_idx in scope:
        for filename in job_io.job_outputs(firstjob + job_idx).values():
            try:
                ds = str(Mu2eName.parse(filename).dataset)
            except ValueError:
                continue
            if ds in wanted:
                maps[ds][filename] = job_idx
    return maps
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestBuildFileMapsScoped -v` → PASS.
Full suite: `python3 test/test_unit.py 2>&1 | grep -E "^(OK|FAILED|Ran )"` → `OK` (existing mkrecovery callers unaffected — default path unchanged).

- [ ] **Step 5: Commit**

```bash
git add utils/mkrecovery.py test/test_unit.py
git commit -m "feat: optional scoped index scan in build_file_maps

indices= iterates an explicit index set instead of range(njobs); the
recovery loop verifies a ledger row's scattered absolute indices
without a full-window scan. Default behavior unchanged.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 4: Recovery loop core (utils/recover.py)

**Files:**
- Create: `utils/recover.py` (core functions only; CLI wiring is Task 5)
- Test: `test/test_unit.py` (append class `TestRecoverLoop`)

**Interfaces:**
- Consumes: `submission_ledger.open_rows/close_row/record_submission/DEFAULT_DB` (Task 1); `submit_map --ledger-parent/--ledger-db/--indices-file` (Task 2); `build_file_maps(..., indices=)` (Task 3); existing `mkrecovery.locate_tarball`, `mkrecovery.extract_datasets_from_tarball`, `jobquery.Mu2eJobPars`, `samweb_wrapper.files_in_dataset`.
- Produces (used by Task 5):
  - `queue_state(jobsub_id, runner=subprocess.run) -> 'drained'|'held'|'running'|'error'`
  - `verify_row(row, sam_lister=files_in_dataset) -> (missing: list[int], partial: list[int])` (raises on verification impossibility)
  - `resubmit(row, missing, db_path, dry_run=False, runner=subprocess.run) -> bool`
  - `process_row(row, db_path, max_attempts, dry_run=False, queue_state_fn=..., verify_fn=..., resubmit_fn=...) -> str` (action label)
  - `DEFAULT_MAX_ATTEMPTS = 3`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
class TestRecoverLoop(unittest.TestCase):
    """utils/recover.py — drain gate, verify, cap semantics."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        self.entry = {'tarball': 'cnf.mu2e.T.C.0.tar', 'njobs': 3}
        self.rid = sl.record_submission(
            self.db, tarball='cnf.mu2e.T.C.0.tar', entry=self.entry,
            indices=[0, 1, 2], jobsub_id='1.0@js.fnal.gov', cluster_id='1')
        self.row = sl.open_rows(self.db)[0]

    def _process(self, qstate='drained', missing=(), partial=(),
                 resub_ok=True, max_attempts=3, dry_run=False,
                 verify_exc=None):
        from utils import recover
        calls = {}

        def fake_verify(row):
            if verify_exc:
                raise verify_exc
            return list(missing), list(partial)

        def fake_resubmit(row, miss, db_path):
            calls['resubmit'] = (row['id'], list(miss), db_path)
            if resub_ok:
                self.sl.record_submission(
                    db_path, tarball=row['tarball'], entry=row['entry'],
                    indices=list(miss), jobsub_id='2.0@js.fnal.gov',
                    cluster_id='2', parent_id=row['id'])
            return resub_ok

        action = recover.process_row(
            self.row, self.db, max_attempts, dry_run=dry_run,
            queue_state_fn=lambda jid: qstate,
            verify_fn=fake_verify, resubmit_fn=fake_resubmit)
        return action, calls

    def test_running_skips(self):
        action, calls = self._process(qstate='running')
        self.assertEqual(action, 'running')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_held_reports_and_skips(self):
        action, calls = self._process(qstate='held')
        self.assertEqual(action, 'held')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_queue_error_skips(self):
        action, _ = self._process(qstate='error')
        self.assertEqual(action, 'queue-error')
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_complete_closes_row(self):
        action, _ = self._process(missing=())
        self.assertEqual(action, 'complete')
        self.assertEqual(self.sl.all_rows(self.db)[0]['state'], 'complete')

    def test_missing_resubmits_and_marks_recovered(self):
        action, calls = self._process(missing=(1,))
        self.assertEqual(action, 'resubmitted')
        self.assertEqual(calls['resubmit'], (self.rid, [1], self.db))
        rows = self.sl.all_rows(self.db)
        self.assertEqual(rows[0]['state'], 'recovered')
        self.assertEqual(rows[1]['state'], 'active')
        self.assertEqual(rows[1]['attempt'], 2)
        self.assertEqual(rows[1]['indices'], [1])

    def test_cap_exhausts_without_resubmit(self):
        action, calls = self._process(missing=(1,), max_attempts=1)
        self.assertEqual(action, 'exhausted')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.all_rows(self.db)[0]['state'], 'exhausted')

    def test_dry_run_never_submits(self):
        action, calls = self._process(missing=(1,), dry_run=True)
        self.assertEqual(action, 'would-resubmit')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_verify_error_keeps_row_active(self):
        action, _ = self._process(verify_exc=RuntimeError('no tarball'))
        self.assertEqual(action, 'verify-error')
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_resubmit_failure_keeps_row_active(self):
        action, _ = self._process(missing=(1,), resub_ok=False)
        self.assertEqual(action, 'resubmit-error')
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_missing_jobsub_id_reported(self):
        rid2 = self.sl.record_submission(
            self.db, tarball='t2', entry={}, indices=[0],
            jobsub_id=None, cluster_id='9')
        row2 = [r for r in self.sl.open_rows(self.db) if r['id'] == rid2][0]
        from utils import recover
        action = recover.process_row(
            row2, self.db, 3,
            queue_state_fn=lambda jid: self.fail('must not be called'),
            verify_fn=lambda r: ([], []),
            resubmit_fn=lambda r, m, d: self.fail('must not be called'))
        self.assertEqual(action, 'queue-error')

    def test_queue_state_parsing(self):
        from utils import recover
        def r(stdout, rc=0):
            return MagicMock(returncode=rc, stdout=stdout, stderr='')
        self.assertEqual(
            recover.queue_state('x', runner=lambda *a, **k: r('')), 'drained')
        self.assertEqual(
            recover.queue_state('x', runner=lambda *a, **k: r('2\n1\n')),
            'running')
        self.assertEqual(
            recover.queue_state('x', runner=lambda *a, **k: r('2\n5\n')),
            'held')
        self.assertEqual(
            recover.queue_state('x', runner=lambda *a, **k: r('', rc=1)),
            'error')

    def test_verify_row_missing_and_partial(self):
        from utils import recover
        files = [f"sim.mu2e.In.C.00000000_{i:08d}.art" for i in range(3)]
        jpars = _root_input_jobpars(files)
        jpars['tbs']['outfiles']['outputs.SecondOutput.fileName'] = \
            "dig.mu2e.TestDesc.TestConf.sequencer.art"
        tar = _make_tarball(jpars)
        try:
            row = {'id': 1, 'tarball': 'cnf.mu2e.T.C.0.tar',
                   'indices': [0, 1, 2], 'entry': {}, 'attempt': 1,
                   'jobsub_id': 'x'}
            dig_ds = 'dig.mu2e.TestDesc.TestConf.art'
            from utils.jobquery import Mu2eJobPars
            jp = Mu2eJobPars(tar)

            def fake_lister(ds):
                out = []
                for i in (0, 1, 2):
                    for f in jp.job_outputs(i).values():
                        if str(Mu2eName.parse(f).dataset) != ds:
                            continue
                        if i == 2:
                            continue          # idx 2: nothing landed
                        if i == 1 and ds == dig_ds:
                            continue          # idx 1: dig stream missing
                        out.append(f)
                return out

            with patch.object(recover, 'locate_tarball', return_value=tar):
                missing, partial = recover.verify_row(
                    row, sam_lister=fake_lister)
            self.assertEqual(missing, [1, 2])
            self.assertEqual(partial, [1])
        finally:
            os.unlink(tar)

    def test_verify_row_unlocatable_tarball_raises(self):
        from utils import recover
        row = {'id': 1, 'tarball': 'cnf.mu2e.gone.C.0.tar',
               'indices': [0], 'entry': {}, 'attempt': 1, 'jobsub_id': 'x'}
        with patch.object(recover, 'locate_tarball', return_value=None):
            with self.assertRaises(RuntimeError):
                recover.verify_row(row, sam_lister=lambda ds: [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestRecoverLoop -v`
Expected: ERROR — `No module named 'utils.recover'`

- [ ] **Step 3: Implement**

Create `utils/recover.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestRecoverLoop -v` → 13 PASS.
Full suite: `python3 test/test_unit.py 2>&1 | grep -E "^(OK|FAILED|Ran )"` → `OK`.

- [ ] **Step 5: Commit**

```bash
git add utils/recover.py test/test_unit.py
git commit -m "feat: recovery-loop core — drain gate, SAM verify, capped resubmit

process_row: jobsub_q gate (held reported, never touched), scoped
SAM verification of a ledger row's indices, resubmission via the
submit_map CLI with --ledger-parent chaining, exhausted at the cap.
Deps injected for tests; no network in the suite.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 5: recover CLI + bin wrappers

**Files:**
- Modify: `utils/recover.py` (append `print_status` and `main`)
- Create: `bin/recover` (executable)
- Create: `bin/recover_cron` (executable)
- Test: `test/test_unit.py` (append class `TestRecoverCLI`)

**Interfaces:**
- Consumes: `process_row`, `submission_ledger.open_rows/all_rows/DEFAULT_DB` (Tasks 1, 4).
- Produces: `recover` CLI — `--db PATH` (default `submission_ledger.DEFAULT_DB`), `--status`, `--dry-run`, `--row N`, `--max-attempts N` (default `DEFAULT_MAX_ATTEMPTS`); exit 0 normally, exit 2 if any row is `held` or newly `exhausted` (cron-visible "needs human"); `print_status(db_path)`.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
class TestRecoverCLI(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')

    def test_print_status_empty(self):
        from utils import recover
        import io as _io
        buf = _io.StringIO()
        with patch('sys.stdout', buf):
            recover.print_status(self.db)
        self.assertIn('empty', buf.getvalue().lower())

    def test_print_status_lists_rows(self):
        from utils import recover
        import io as _io
        rid = self.sl.record_submission(
            self.db, tarball='cnf.mu2e.T.C.0.tar', entry={}, indices=[0, 1],
            jobsub_id='1.0@js', cluster_id='1')
        self.sl.close_row(self.db, rid, 'complete')
        self.sl.record_submission(
            self.db, tarball='cnf.mu2e.T2.C.0.tar', entry={}, indices=[3],
            jobsub_id='2.0@js', cluster_id='2')
        buf = _io.StringIO()
        with patch('sys.stdout', buf):
            recover.print_status(self.db)
        out = buf.getvalue()
        self.assertIn('complete', out)
        self.assertIn('active', out)
        self.assertIn('cnf.mu2e.T2.C.0.tar', out)

    def test_main_exit_2_on_attention(self):
        from utils import recover
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        with patch.object(recover, 'process_row', return_value='held'), \
             patch.object(sys, 'argv', ['recover', '--db', self.db]):
            with self.assertRaises(SystemExit) as cm:
                recover.main()
        self.assertEqual(cm.exception.code, 2)

    def test_main_exit_0_when_clean(self):
        from utils import recover
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        with patch.object(recover, 'process_row', return_value='complete'), \
             patch.object(sys, 'argv', ['recover', '--db', self.db]):
            recover.main()  # returns without SystemExit
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestRecoverCLI -v`
Expected: ERROR — `recover` has no attribute `print_status` / `main`.

- [ ] **Step 3: Implement CLI in `utils/recover.py`**

Append:

```python
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
```

- [ ] **Step 4: Create `bin/recover`**

```bash
#!/bin/bash

# recover - verify-and-resubmit loop for direct-backend submissions
# Wrapper for utils/recover.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/../utils/recover.py"

# Pass help through without environment setup
if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    exec python3 "$PYTHON_SCRIPT" "$@"
fi

# Set up Mu2e environment (needed for samweb, jobsub_q, httokendecode)
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops

exec python3 "$PYTHON_SCRIPT" "$@"
```

Then: `chmod +x bin/recover`

- [ ] **Step 5: Create `bin/recover_cron`**

```bash
#!/bin/bash
# Cron entry point for the direct-backend recovery loop. Install in
# mu2epro's crontab (hourly), e.g.:
#   17 * * * * /path/to/prodtools/bin/recover_cron
#
# Order matters:
#   1. flock — overlapping runs must not double-submit
#   2. Mu2e env (quiet; failures still surface via the python run)
#   3. token gate — no valid bearer token -> report and exit non-zero.
#      NEVER fetch or refresh a token here (standing rule: token
#      problems are reported, not remediated).
#   4. recover, appended to a dated log beside the DB
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${MU2E_SUBMISSION_DB:-/exp/mu2e/data/users/mu2epro/prodtools/submissions.db}"
DBDIR="$(dirname "$DB")"
LOCK="$DBDIR/recover.lock"
LOG="$DBDIR/recover-$(date +%Y%m%d).log"

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "recover_cron: another run holds $LOCK — exiting" >&2
    exit 0
fi

{
    echo "=== recover_cron $(date -u +%FT%TZ) ==="
    source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh >/dev/null 2>&1 || true
    muse setup ops >/dev/null 2>&1 || true
    if ! httokendecode >/dev/null 2>&1; then
        echo "ERROR: no valid bearer token — not submitting, not remediating."
        exit 1
    fi
    python3 "$REPO/utils/recover.py" --db "$DB"
} >>"$LOG" 2>&1
```

Then: `chmod +x bin/recover_cron`

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestRecoverCLI -v` → 4 PASS.
Full suite: `python3 test/test_unit.py 2>&1 | grep -E "^(OK|FAILED|Ran )"` → `OK`.
Smoke the wrappers without env side effects:
`python3 utils/recover.py --help` → usage prints, exit 0.
`bash -n bin/recover bin/recover_cron` → no syntax errors.
`python3 utils/recover.py --status --db /tmp/recover-smoke-$$.db` → `Ledger is empty` (creates a throwaway DB; remove it after: `rm -f /tmp/recover-smoke-$$.db`).

- [ ] **Step 7: Commit**

```bash
git add utils/recover.py bin/recover bin/recover_cron test/test_unit.py
git commit -m "feat: recover CLI + bin wrappers + mu2epro cron entry point

recover --status/--dry-run/--row/--max-attempts; exit 2 when held or
exhausted rows need a human. recover_cron: flock, quiet env, token
gate (report-only, never remediate), dated log beside the DB.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 6: Documentation — EXAMPLES, wiki, runbook

**Files:**
- Modify: `docs/EXAMPLES_schema.md` (Additional Tools list, ops-scripts line, tribal-knowledge list)
- Modify: `EXAMPLES.md` (FULL regeneration per the schema — never a hand-edit)
- Create: `wiki/pages/2026-07-18-direct-recovery-loop.md`
- Modify: `wiki/log.md` (append entry)

**Interfaces:**
- Consumes: the CLI surfaces exactly as produced by Tasks 2 and 5 (verify every documented flag against the current argparse before writing it).
- Produces: nothing downstream.

- [ ] **Step 1: Update `docs/EXAMPLES_schema.md`**

In section "11. Additional Tools", extend the tool list to include `recover` (insert after `submit_map`):

```
    `submit_map`, `recover`, `copy_to_stash`. Ops scripts
    (`install_prodtools.sh`, `update_pomsmonitor_web`, `recover_cron`)
    get a one-line mention.
```

In "Tribal knowledge to preserve", append:

```
- Every successful `submit_map --backend direct` submission is recorded
  in the submission ledger (default
  `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, env
  `MU2E_SUBMISSION_DB`); `recover` drain-checks via jobsub_q, verifies
  outputs against SAM, and resubmits only missing indices (attempt cap,
  then `exhausted` for a human). POMS-backend stages are never in the
  ledger — POMS owns their recovery (`mkrecovery`).
```

- [ ] **Step 2: Regenerate `EXAMPLES.md`**

Follow the regeneration procedure in `docs/EXAMPLES_schema.md` end-to-end: fresh overwrite, every flag verified against current argparse (`utils/submit.py` now has `--ledger-db/--ledger-parent/--no-ledger`; `utils/recover.py` has `--db/--status/--dry-run/--row/--max-attempts`), contiguous numbering, `recover` subsection added under Additional Tools with 1-3 real invocations:

```bash
recover --status                 # read-only chain table (any account)
recover --dry-run                # verify + report, no submissions
recover                          # full pass (mu2epro; cron entry point)
```

- [ ] **Step 3: Write `wiki/pages/2026-07-18-direct-recovery-loop.md`**

Front-matter and sections (follow `wiki/SCHEMA.md` conventions):

```markdown
---
title: Direct-backend recovery loop — ledger + recover + cron
tags: [decision, recovery, direct-backend, submit_map, operations]
sources: [docs/superpowers/specs/2026-07-18-direct-recovery-design.md]
updated: 2026-07-18
---
```

Body must cover (write each as prose, not placeholders):
1. **What/why** — direct backend had no automated recovery; POMS chains vs JustIN file-state comparison, link [[justin-vs-prodtools]] and [[poms-reference]]; only SAM-output verification is trusted ([[2026-07-05-run1ban-mix-recovery-data-loss]]).
2. **Architecture** — ledger schema summary, states, attempt chains, the firstjob-drop rule for resubmission, jobsub-id (cluster@schedd) requirement.
3. **Install runbook** — one-time: `mkdir -p /exp/mu2e/data/users/mu2epro/prodtools` as mu2epro; crontab line `17 * * * * <prodtools>/bin/recover_cron` in mu2epro's crontab on a GPVM; logs at `recover-YYYYMMDD.log` beside the DB; `recover --status` from any account.
4. **Pre-activation checklist** (must be checked off before the cron goes live — these need live services and are NOT covered by unit tests):
   - Duplicate-declare behavior: re-run one index whose outputs partially exist (or trace the worker pushOutput/declare path) and record what happens to the already-declared file.
   - One real `recover --dry-run` pass over a genuine ledger row on a drained cluster.
   - `jobsub_q --jobid <id> -af JobStatus` passthrough confirmed against a real jobsub_lite installation (the loop's drain gate assumes condor_q autoformat passthrough).
5. **Semantics and limits** — deterministic re-runs (systematic failures re-fail; exhausted = human), held jobs never touched, ad-hoc submissions with `--no-ledger` are invisible to the loop, POMS entries out of scope by construction.

- [ ] **Step 4: Append to `wiki/log.md`**

One entry in the file's existing format, dated 2026-07-18: direct-recovery loop implemented (ledger + recover + cron), spec/plan paths, pre-activation checklist pending.

- [ ] **Step 5: Verify docs**

- `grep -n "recover" EXAMPLES.md` — subsection present, ops one-liner present.
- Spot-check 5 commands from the regenerated `EXAMPLES.md` against argparse (per the regen procedure).
- `python3 test/test_unit.py 2>&1 | grep -E "^(OK|FAILED|Ran )"` → `OK` (docs task must not break code).

- [ ] **Step 6: Commit**

```bash
git add docs/EXAMPLES_schema.md EXAMPLES.md wiki/pages/2026-07-18-direct-recovery-loop.md wiki/log.md
git commit -m "docs: recovery loop — EXAMPLES regen, wiki page + runbook

recover/recover_cron in the schema tool lists; ledger tribal-knowledge
bullet; wiki page with install runbook and pre-activation checklist
(duplicate-declare verify, live dry-run, jobsub_q -af passthrough).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

## Post-plan notes (for the controller, not a task)

- The pre-activation checklist in the wiki page is the launch gate: the cron must not be installed in mu2epro's crontab until its three items are checked off by the operator. The plan deliberately does NOT install anything into any crontab.
- Memory updates (`reference_recovery_two_paths` gains the loop; a new project memory for activation state) are the controller's own post-merge bookkeeping, not a subagent task.
