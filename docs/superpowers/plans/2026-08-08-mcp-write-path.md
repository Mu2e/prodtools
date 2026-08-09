# Identity-Aware Submission + prodtools-write MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a submission run under the caller's own identity or under `mu2epro`, and expose the push → enqueue → submit chain as typed MCP tools without weakening the gate that protects production.

**Architecture:** Two phases. Phase 1 (Tasks 1-5) makes `utils/` identity-correct and retry-safe — the ledger path derives from the running account, and the ledger row is reserved *before* `jobsub_submit` rather than written after. Phase 2 (Tasks 6-10) adds a separate `prodtools-write` MCP server that is a thin typed façade over that hardened CLI. Safety lives in the CLI so every caller benefits, not only the MCP one.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `argparse`, `subprocess`), FastMCP (`mcp.server.fastmcp`), `unittest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-08-mcp-write-path-design.md`

## Global Constraints

- Test suite is `python3 -u test/test_unit.py`. It must pass at every commit. A trailing `submissions.lock` `ResourceWarning` is pre-existing noise, not a failure.
- `unittest`, not pytest. Follow the existing style: `class TestX(unittest.TestCase)`, temp DBs via `os.path.join(tempfile.mkdtemp(), 'submissions.db')`.
- **Stdlib sqlite3 ONLY** in `utils/submission_ledger.py` — it is imported by the submit path, which runs as `mu2epro` in the bare ops environment (no pyenv, no SQLAlchemy).
- **`ledger_for('mu2epro')` must equal `PRODUCTION_DB` exactly.** This is what makes the change a pure generalization with no migration. Assert it in a test.
- **Never fall back to `PRODUCTION_DB`.** A failure to create or open a personal ledger raises; it never silently writes personal campaigns into the production ledger.
- **Readers keep production; writers get the caller's ledger.** There is one production ledger everyone reads and N personal ledgers each person writes.
- **`run_as` is required with no default** (`"self"` | `"mu2epro"`). `entry` and `campaign_id` are likewise required — no argument defaults to "all".
- **`run_as="mu2epro"` is refused unless `confirm=True`**, independently of any hook.
- **Credentials are never remediated.** A missing `mu2epro` token returns a structured error and stops. Never run `htgettoken`, `getToken`, or any refresh for `mu2epro`.
- **Results are read back from the ledger, never scraped from stdout.**
- The ksu block is copied verbatim from `.claude/commands/mu2epro-submit.md:121-133`.
- Commit footers:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
  ```
- Do NOT `git push`. The user pushes from their own shell.

---

## File Structure

**Modified:**
- `utils/submission_ledger.py` — adds `PRODUCTION_DB`, `ledger_for()`, `ensure_ledger_dir()`, and the two-phase write API (`reserve_submission`, `attach_cluster`, `fail_reservation`, `reserved_rows`). Responsibility unchanged: ledger state, stdlib sqlite3 only.
- `utils/submissions.py` — `--mine`, per-verb DB default, and a docstring correction on `_slice_overlaps_ledger`.
- `utils/submit.py` — writer default flips to `ledger_for()`; `_submit_one` reserves before submitting; `check_inputs` runs on the direct path.
- `test/test_unit.py` — new test classes appended.
- `mcp/scripts/install.sh`, `mcp/pyproject.toml`, `.mcp.json`, `.claude/settings.json`, `CLAUDE.md`.

**Created:**
- `mcp/src/prodtools_mcp_write/__init__.py`
- `mcp/src/prodtools_mcp_write/runner.py` — the only place that knows how to execute as `self` vs `mu2epro`, and the only place that enforces `confirm`. One responsibility: identity dispatch.
- `mcp/src/prodtools_mcp_write/tools.py` — the three tool functions. Thin: argument validation, call `runner`, read results from the ledger.
- `mcp/src/prodtools_mcp_write/server.py` — FastMCP registration and `--check`.
- `mcp/scripts/start_write_mcp.sh`

`runner.py` and `tools.py` are split because the identity/confirm logic is the security-critical part and must be testable without touching MCP plumbing.

---

# PHASE 1 — Identity-correct, retry-safe CLI

### Task 1: Identity-derived ledger paths

**Files:**
- Modify: `utils/submission_ledger.py:12-16` (module docstring), `:29-31` (DEFAULT_DB)
- Test: `test/test_unit.py`

**Interfaces:**
- Produces: `PRODUCTION_DB: str`, `ledger_for(user: str | None = None) -> str`, `ensure_ledger_dir(db_path: str) -> str`, `DEFAULT_DB: str` (unchanged meaning: production).

- [ ] **Step 1: Write the failing test**

Append to `test/test_unit.py`:

```python
class TestLedgerPathResolution(unittest.TestCase):
    def setUp(self):
        from utils import submission_ledger as sl
        self.sl = sl

    def test_mu2epro_reproduces_the_production_path_exactly(self):
        # This is what makes the change a pure generalization: the
        # existing production path IS what the formula yields for
        # mu2epro, so no migration and no cron change.
        self.assertEqual(self.sl.ledger_for('mu2epro'), self.sl.PRODUCTION_DB)

    def test_ledger_for_named_user(self):
        self.assertEqual(
            self.sl.ledger_for('alice'),
            '/exp/mu2e/data/users/alice/prodtools/submissions.db')

    def test_ledger_for_defaults_to_current_account(self):
        with patch('getpass.getuser', return_value='bob'):
            self.assertEqual(
                self.sl.ledger_for(),
                '/exp/mu2e/data/users/bob/prodtools/submissions.db')

    def test_default_db_still_means_production(self):
        # Readers (ledger_ro, the read-only MCP, listNewDatasets,
        # `submissions status`) resolve to DEFAULT_DB and must keep
        # seeing production.
        import importlib
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MU2E_SUBMISSION_DB', None)
            reloaded = importlib.reload(self.sl)
            try:
                self.assertEqual(reloaded.DEFAULT_DB, reloaded.PRODUCTION_DB)
            finally:
                importlib.reload(self.sl)

    def test_ensure_ledger_dir_creates_a_derived_parent(self):
        base = tempfile.mkdtemp()
        db = os.path.join(base, 'prodtools', 'submissions.db')
        self.assertEqual(self.sl.ensure_ledger_dir(db), db)
        self.assertTrue(os.path.isdir(os.path.dirname(db)))

    def test_ensure_ledger_dir_is_idempotent(self):
        base = tempfile.mkdtemp()
        db = os.path.join(base, 'prodtools', 'submissions.db')
        self.sl.ensure_ledger_dir(db)
        self.sl.ensure_ledger_dir(db)   # must not raise

    def test_ensure_ledger_dir_raises_and_never_falls_back(self):
        # A personal ledger that cannot be created must fail loudly.
        # Silently using PRODUCTION_DB would write personal campaigns
        # into the production ledger.
        db = '/proc/cannot/exist/prodtools/submissions.db'
        with self.assertRaises(RuntimeError) as ctx:
            self.sl.ensure_ledger_dir(db)
        self.assertNotIn(self.sl.PRODUCTION_DB, str(ctx.exception))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestLedgerPathResolution 2>&1 | tail -5`
Expected: FAIL — `AttributeError: module 'utils.submission_ledger' has no attribute 'PRODUCTION_DB'`

- [ ] **Step 3: Implement**

In `utils/submission_ledger.py`, add `getpass` and `Path` to the imports, then replace lines 29-31:

```python
PRODUCTION_DB = '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db'


def ledger_for(user=None):
    """Ledger path for a UNIX account.

    There is ONE production ledger everyone reads and N personal
    ledgers each person writes, so readers and writers resolve
    differently (see DEFAULT_DB below). For 'mu2epro' this returns
    PRODUCTION_DB exactly, which is why the split needs no migration
    and leaves the production cron untouched.
    """
    return (f'/exp/mu2e/data/users/{user or getpass.getuser()}'
            f'/prodtools/submissions.db')


def ensure_ledger_dir(db_path):
    """Create the parent directory of a DERIVED ledger path; return it.

    Called ONLY on a ledger_for() path. An operator-supplied path
    (MU2E_SUBMISSION_DB, --ledger-db) is deliberately never mkdir'd:
    a typo there would silently create a stray database instead of
    failing, which is why this module has always surfaced a missing
    directory rather than creating one. A derived path cannot be a
    typo, so the reasoning does not apply to it.

    Raises rather than falling back: writing a personal campaign into
    the production ledger is the worst available outcome.
    """
    parent = Path(db_path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"cannot create ledger directory {parent}: {exc}") from exc
    return db_path


# Readers resolve here and keep seeing production.
DEFAULT_DB = os.environ.get('MU2E_SUBMISSION_DB', PRODUCTION_DB)
```

Then update the module docstring at lines 12-16 to state the new rule:

```python
The DB lives at a stable absolute path: mu2epro submissions run from
throwaway /tmp workdirs, so a repo-relative path would scatter state.
An OPERATOR-SUPPLIED directory (MU2E_SUBMISSION_DB, --ledger-db) is
surfaced when missing (sqlite3.OperationalError), never silently
mkdir'd over — a typo must fail, not create a stray DB. A DERIVED
path from ledger_for() is created by ensure_ledger_dir(), since it
cannot be a typo.
```

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add utils/submission_ledger.py test/test_unit.py
git commit -m "feat(ledger): identity-derived ledger paths

ledger_for('mu2epro') == PRODUCTION_DB exactly, so this is a pure
generalization: no migration, production cron untouched. DEFAULT_DB
keeps meaning production because readers and writers want different
things — one production ledger everyone reads, N personal ledgers
each person writes.

ensure_ledger_dir() creates only DERIVED parents. An operator-supplied
path stays non-created on purpose: a typo must fail rather than make a
stray DB. Never falls back to PRODUCTION_DB."
```

---

### Task 2: Reader vs writer defaults, and `submissions status --mine`

**Files:**
- Modify: `utils/submissions.py:1142-1146` (arg parsing), `utils/submit.py:836`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `submission_ledger.ledger_for`, `PRODUCTION_DB`, `DEFAULT_DB` (Task 1).
- Produces: `submissions.resolve_db(opts) -> str`.

**Rule:** `--db` wins when given. Otherwise `status` (the only read verb) resolves to production, and **every mutating verb resolves to `ledger_for()`**. For `mu2epro` both are the same path, so nothing changes for production. For anyone else, mutating verbs default to the only ledger they can actually write.

- [ ] **Step 1: Write the failing test**

```python
class TestSubmissionsDbResolution(unittest.TestCase):
    def setUp(self):
        from utils import submissions, submission_ledger as sl
        self.submissions = submissions
        self.sl = sl

    def _opts(self, verb, db=None, mine=False):
        return SimpleNamespace(verb=verb, db=db, mine=mine)

    def test_explicit_db_wins_everywhere(self):
        opts = self._opts('status', db='/tmp/explicit.db', mine=True)
        self.assertEqual(self.submissions.resolve_db(opts), '/tmp/explicit.db')

    def test_status_defaults_to_production(self):
        self.assertEqual(self.submissions.resolve_db(self._opts('status')),
                         self.sl.DEFAULT_DB)

    def test_status_mine_selects_personal(self):
        with patch('getpass.getuser', return_value='bob'):
            self.assertEqual(
                self.submissions.resolve_db(self._opts('status', mine=True)),
                '/exp/mu2e/data/users/bob/prodtools/submissions.db')

    def test_mutating_verbs_default_to_personal(self):
        # As a non-mu2epro user you cannot write production at all, so a
        # mutating verb defaulting there is never useful. For mu2epro the
        # two paths are identical.
        with patch('getpass.getuser', return_value='bob'):
            for verb in ('run', 'pause', 'resume', 'cancel', 'complete',
                         'set-slice', 'set-memory'):
                self.assertEqual(
                    self.submissions.resolve_db(self._opts(verb)),
                    '/exp/mu2e/data/users/bob/prodtools/submissions.db',
                    f'verb {verb}')

    def test_mutating_default_is_production_for_mu2epro(self):
        with patch('getpass.getuser', return_value='mu2epro'):
            self.assertEqual(self.submissions.resolve_db(self._opts('run')),
                             self.sl.PRODUCTION_DB)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestSubmissionsDbResolution 2>&1 | tail -5`
Expected: FAIL — `AttributeError: module 'utils.submissions' has no attribute 'resolve_db'`

- [ ] **Step 3: Implement**

In `utils/submissions.py`, add near the other module-level helpers:

```python
# `status` is the only read verb. Every other verb mutates, and a
# non-mu2epro caller cannot write the production ledger at all, so a
# mutating default of "production" would only ever fail. For mu2epro
# ledger_for() IS the production path, so nothing changes there.
_READ_VERBS = ('status',)


def resolve_db(opts):
    """Ledger path for this invocation: explicit --db, else --mine or
    the per-verb default."""
    if getattr(opts, 'db', None):
        return opts.db
    if getattr(opts, 'mine', False):
        return submission_ledger.ledger_for()
    if getattr(opts, 'verb', None) in _READ_VERBS:
        return submission_ledger.DEFAULT_DB
    return submission_ledger.ledger_for()
```

Change the `--db` argument at line 1142 to have no baked default, and add `--mine`:

```python
    p.add_argument('--db', default=None,
                   help='Submission-ledger sqlite DB. Default: the '
                        f'production ledger ({submission_ledger.PRODUCTION_DB}) '
                        'for `status`, your own ledger for every mutating '
                        'verb. Env MU2E_SUBMISSION_DB overrides the '
                        'production default.')
    p.add_argument('--mine', action='store_true',
                   help='Use your own ledger '
                        '(/exp/mu2e/data/users/$USER/prodtools/submissions.db) '
                        'instead of the per-verb default.')
```

Replace every use of `opts.db` (or `args.db`) in the dispatch body with `resolve_db(opts)`, resolving once into a local at the top of `main()`.

In `utils/submit.py:836`, flip the writer default:

```python
    parser.add_argument('--ledger-db',
                        default=submission_ledger.ledger_for(),
                        help='Submission-ledger sqlite DB (default: your '
                             'own ledger; for mu2epro that IS the '
                             'production ledger). Every direct submission '
                             'is recorded for the recovery loop '
                             '(`submissions run`).')
```

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add utils/submissions.py utils/submit.py test/test_unit.py
git commit -m "feat(submissions): --mine, and per-verb ledger defaults

status reads production; every mutating verb defaults to the caller's
own ledger, which is the only one a non-mu2epro account can write. For
mu2epro the two paths are identical, so the production cron is
unaffected."
```

---

### Task 3: Two-phase ledger write API

**Files:**
- Modify: `utils/submission_ledger.py:33` (STATES), plus new functions
- Test: `test/test_unit.py`

**Interfaces:**
- Produces: `reserve_submission(db_path, *, tarball, entry, indices, map_path=None, parent_id=None) -> int`, `attach_cluster(db_path, row_id, *, jobsub_id, cluster_id) -> None`, `fail_reservation(db_path, row_id, note) -> None`, `reserved_rows(db_path) -> list[dict]`.

**Why:** `_slice_overlaps_ledger` (`submissions.py:706`) claims to be a "crash-window guard" for a process that "can die after `jobsub_submit` succeeds but before its own ledger write." It cannot be — it reads `all_rows`, and in that scenario no row exists yet. Reserving the row *before* submitting makes the row exist during the window, which makes the existing guard's documented claim true.

- [ ] **Step 1: Write the failing test**

```python
class TestTwoPhaseLedgerWrite(unittest.TestCase):
    def setUp(self):
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 5, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}

    def _reserve(self, indices=(0, 1, 2)):
        return self.sl.reserve_submission(
            self.db, tarball=self.entry['tarball'], entry=self.entry,
            indices=list(indices), map_path='/tmp/map.json')

    def test_reserved_row_records_indices_before_any_cluster_exists(self):
        rid = self._reserve()
        rows = self.sl.all_rows(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], rid)
        self.assertEqual(rows[0]['state'], 'submitting')
        self.assertEqual(rows[0]['indices'], [0, 1, 2])
        self.assertIsNone(rows[0]['cluster_id'])
        self.assertIsNone(rows[0]['jobsub_id'])

    def test_reserved_row_is_not_an_open_row(self):
        # The recovery loop must not treat a not-yet-submitted window as
        # a live submission to verify.
        self._reserve()
        self.assertEqual(self.sl.open_rows(self.db), [])

    def test_reserved_row_is_visible_to_reserved_rows(self):
        rid = self._reserve()
        self.assertEqual([r['id'] for r in self.sl.reserved_rows(self.db)],
                         [rid])

    def test_attach_cluster_promotes_to_active(self):
        rid = self._reserve()
        self.sl.attach_cluster(self.db, rid, jobsub_id='99.0@jobsub03.fnal.gov',
                               cluster_id='99')
        rows = self.sl.open_rows(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'active')
        self.assertEqual(rows[0]['cluster_id'], '99')
        self.assertEqual(rows[0]['jobsub_id'], '99.0@jobsub03.fnal.gov')
        self.assertEqual(self.sl.reserved_rows(self.db), [])

    def test_attach_cluster_twice_raises(self):
        rid = self._reserve()
        self.sl.attach_cluster(self.db, rid, jobsub_id='99.0@s', cluster_id='99')
        with self.assertRaises(ValueError):
            self.sl.attach_cluster(self.db, rid, jobsub_id='99.0@s',
                                   cluster_id='99')

    def test_fail_reservation_closes_the_row(self):
        rid = self._reserve()
        self.sl.fail_reservation(self.db, rid, 'jobsub_submit returned 1')
        row = self.sl.all_rows(self.db)[0]
        self.assertEqual(row['state'], 'failed')
        self.assertIsNotNone(row['closed_utc'])
        self.assertIn('jobsub_submit', row['note'])
        self.assertEqual(self.sl.open_rows(self.db), [])

    def test_failed_window_still_blocks_reuse(self):
        # jobsub_submit can exit non-zero having already created a
        # cluster, so a failed reservation's window is NOT proven free.
        # It must keep blocking until a human reconciles it.
        from utils.submissions import _slice_overlaps_ledger
        rid = self._reserve(indices=(0, 1, 2))
        self.sl.fail_reservation(self.db, rid, 'submit failed')
        self.assertTrue(_slice_overlaps_ledger(
            self.db, self.entry['tarball'], 0, 0, 3))

    def test_reserved_window_blocks_a_duplicate_slice(self):
        # The crash window itself: reserved, process dies, next tick.
        from utils.submissions import _slice_overlaps_ledger
        self._reserve(indices=(0, 1, 2))
        self.assertTrue(_slice_overlaps_ledger(
            self.db, self.entry['tarball'], 0, 0, 3))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestTwoPhaseLedgerWrite 2>&1 | tail -5`
Expected: FAIL — `AttributeError: module 'utils.submission_ledger' has no attribute 'reserve_submission'`

- [ ] **Step 3: Implement**

Replace `STATES` at line 33:

```python
# 'submitting' is a RESERVED row: its indices are claimed but
# jobsub_submit has not returned yet. It is deliberately not 'active' —
# the recovery loop must not try to verify a window that may never have
# launched. 'failed' is a reservation whose submit definitively failed;
# it stays in the DB because jobsub_submit can exit non-zero having
# already made a cluster, so its window is not proven free.
STATES = ('submitting', 'active', 'complete', 'recovered', 'exhausted',
          'failed')
```

Add after `record_submission`:

```python
def reserve_submission(db_path, *, tarball, entry, indices, map_path=None,
                       parent_id=None):
    """Claim an index window BEFORE jobsub_submit runs; return the row id.

    This is what makes _slice_overlaps_ledger's "crash-window guard"
    claim true. Written after the fact, a row cannot cover the window
    between a successful jobsub_submit and the ledger write — there is
    nothing in the DB to overlap against, and the next tick re-submits
    the same deterministic payload as duplicate physics.

    Raising here is correct and load-bearing: if the window cannot be
    recorded, the submission must not happen.
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
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)',
            (_now(), 'submitting', attempt, parent_id, map_path, tarball,
             json.dumps(entry), json.dumps(sorted(indices))))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def attach_cluster(db_path, row_id, *, jobsub_id, cluster_id):
    """Promote a reserved row to 'active' once its cluster is known."""
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE submissions SET state = 'active', jobsub_id = ?, "
            "cluster_id = ? WHERE id = ? AND state = 'submitting'",
            (jobsub_id, cluster_id, row_id))
        if cur.rowcount != 1:
            raise ValueError(f"no reserved row {row_id} to attach a cluster to")
        con.commit()
    finally:
        con.close()


def fail_reservation(db_path, row_id, note):
    """Close a reserved row whose submit definitively failed.

    The row is kept, not deleted: jobsub_submit can exit non-zero having
    already created a cluster, so the window is not proven free and must
    keep blocking until a human reconciles it.
    """
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE submissions SET state = 'failed', closed_utc = ?, "
            "note = ? WHERE id = ? AND state = 'submitting'",
            (_now(), note, row_id))
        if cur.rowcount != 1:
            raise ValueError(f"no reserved row {row_id} to fail")
        con.commit()
    finally:
        con.close()


def reserved_rows(db_path):
    """Rows still in 'submitting' — claimed windows with no cluster.

    A row that stays here is the needs-reconciliation case: someone must
    check jobsub_q before its window can be reused.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM submissions WHERE state = 'submitting' "
            "ORDER BY id").fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        con.close()
```

`open_rows` already filters `state = 'active'`, so reserved rows are excluded with no change.

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add utils/submission_ledger.py test/test_unit.py
git commit -m "feat(ledger): two-phase write — reserve before submit

_slice_overlaps_ledger documents itself as a crash-window guard for a
process dying between a successful jobsub_submit and its ledger write,
but it reads all_rows and in that scenario no row exists. Reserving the
window first makes the existing guard's claim true.

A failed reservation is kept, not deleted: jobsub_submit can exit
non-zero having already created a cluster, so the window is not proven
free."
```

---

### Task 4: Reserve-before-submit in `submit_map`, and correct the guard docstring

**Files:**
- Modify: `utils/submit.py:137-166` (`_record_in_ledger`), `:665-671` (`_submit_one` tail), `utils/submissions.py:706-724` (docstring)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `reserve_submission`, `attach_cluster`, `fail_reservation` (Task 3).
- Produces: `_reserve_in_ledger(entry, firstjob, jobset, opts, files=None) -> int | None`.

**Behavioural change worth stating:** a ledger that cannot be written now fails *before* submitting instead of after. Today a self-submission launches its grid jobs and only then discovers it cannot write `mu2epro`'s ledger. Fail-closed is the point.

- [ ] **Step 1: Write the failing test**

```python
class TestSubmitReservesBeforeSubmitting(unittest.TestCase):
    """Ordering is the whole contract: the row must exist while
    jobsub_submit is in flight."""

    def setUp(self):
        from utils import submit, submission_ledger as sl
        self.submit = submit
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 5, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}
        self.opts = SimpleNamespace(ledger_db=self.db, map='/tmp/map.json',
                                    ledger_parent=None, no_ledger=False)

    def test_row_exists_and_is_reserved_during_submit(self):
        seen = {}

        def fake_run_submit(*a, **kw):
            rows = self.sl.all_rows(self.db)
            seen['states'] = [r['state'] for r in rows]
            seen['indices'] = rows[0]['indices'] if rows else None
            return {'status': 'submitted', 'cluster_id': '4242',
                    'jobsub_id': '4242.0@jobsub03.fnal.gov',
                    'tarball': self.entry['tarball'], 'njobs': 3}

        rid = self.submit._reserve_in_ledger(
            self.entry, 0, [0, 1, 2], self.opts)
        result = fake_run_submit()
        self.assertEqual(seen['states'], ['submitting'])
        self.assertEqual(seen['indices'], [0, 1, 2])

        self.submit._attach_cluster(rid, result, self.opts)
        self.assertEqual(self.sl.open_rows(self.db)[0]['cluster_id'], '4242')

    def test_failed_submit_marks_the_reservation_failed(self):
        rid = self.submit._reserve_in_ledger(
            self.entry, 0, [0, 1, 2], self.opts)
        self.submit._fail_reservation(
            rid, {'status': 'failed', 'cluster_id': None}, self.opts)
        self.assertEqual(self.sl.all_rows(self.db)[0]['state'], 'failed')

    def test_unwritable_ledger_raises_before_any_submit(self):
        self.opts.ledger_db = '/proc/nope/submissions.db'
        with self.assertRaises(Exception):
            self.submit._reserve_in_ledger(self.entry, 0, [0, 1, 2], self.opts)

    def test_no_ledger_skips_reservation(self):
        self.opts.no_ledger = True
        self.assertIsNone(
            self.submit._reserve_in_ledger(self.entry, 0, [0, 1, 2], self.opts))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestSubmitReservesBeforeSubmitting 2>&1 | tail -5`
Expected: FAIL — `AttributeError: module 'utils.submit' has no attribute '_reserve_in_ledger'`

- [ ] **Step 3: Implement**

In `utils/submit.py`, replace `_record_in_ledger` (lines 137-166) with three functions:

```python
def _ledger_payload(firstjob, jobset, files=None):
    """Absolute cnf indices, or the FILENAME list for a draining batch.
    jobset is entry-relative; the ledger stores absolute (firstjob + i).
    For --indices submissions jobset is already absolute and firstjob is
    0, so the same expression holds."""
    return (list(files) if files is not None
            else [firstjob + i for i in jobset])


def _reserve_in_ledger(entry, firstjob, jobset, opts, files=None):
    """Claim this window BEFORE jobsub_submit. Returns the row id, or
    None when --no-ledger.

    RAISES on failure, deliberately: an unrecordable window must not be
    submitted, because nothing would then stop the next tick from
    re-sending the same deterministic payload. This is also what makes a
    self-submission fail fast rather than launching jobs and only then
    discovering it cannot write the ledger.
    """
    if opts.no_ledger:
        return None
    return submission_ledger.reserve_submission(
        submission_ledger.ensure_ledger_dir(opts.ledger_db),
        tarball=entry['tarball'],
        entry=entry,
        indices=_ledger_payload(firstjob, jobset, files),
        map_path=opts.map,
        parent_id=opts.ledger_parent)


def _attach_cluster(row_id, result, opts):
    """Fill in the cluster on a reserved row. Never raises: the
    submission already happened, so a ledger failure is reported with
    everything needed to fix the row by hand."""
    if row_id is None:
        return
    try:
        submission_ledger.attach_cluster(
            opts.ledger_db, row_id,
            jobsub_id=result.get('jobsub_id'),
            cluster_id=result['cluster_id'])
        print(f"Ledger: row {row_id} attached to cluster "
              f"{result['cluster_id']} in {opts.ledger_db}")
    except Exception as e:
        print(f"WARNING: ledger attach failed ({e}) — the submission DID "
              f"go through (cluster {result['cluster_id']}). Row {row_id} "
              f"is still 'submitting'; set it active by hand: "
              f"jobsub_id={result.get('jobsub_id')} db={opts.ledger_db}")


def _fail_reservation(row_id, result, opts):
    """Close a reserved row after a definitively failed submit. Never
    raises."""
    if row_id is None:
        return
    try:
        submission_ledger.fail_reservation(
            opts.ledger_db, row_id,
            f"submit failed (status={result.get('status')}); window NOT "
            f"proven free — check jobsub_q before reusing these indices")
        print(f"Ledger: row {row_id} marked failed in {opts.ledger_db}")
    except Exception as e:
        print(f"WARNING: could not mark row {row_id} failed ({e}); it "
              f"remains 'submitting' in {opts.ledger_db}")
```

Then rewrite the tail of `_submit_one` (currently lines 665-671):

```python
    row_id = _reserve_in_ledger(_snapshot_entry(entry, resources), firstjob,
                                jobset, opts, files=files)
    result = _run_submit(cmd, tarball_name, len(jobset))
    if not opts.no_ledger:
        _log_submission(firstjob, jobset, result, opts, files=files)
    if result['status'] == 'submitted':
        _attach_cluster(row_id, result, opts)
    else:
        _fail_reservation(row_id, result, opts)
    return result
```

Finally correct the overstated docstring in `utils/submissions.py:711-716`:

```python
    Crash-window guard. Rows are RESERVED before jobsub_submit
    (submission_ledger.reserve_submission), so a process that dies
    anywhere between claiming the window and recording the cluster
    still leaves a row here to overlap against. Without that ordering
    this check could not see the window at all — deterministic payloads
    make a re-send duplicate physics, not a harmless retry. Also catches
    a human manually submitting `--first/--num` on a tarball that has a
    live campaign, and a 'failed' reservation whose window is not proven
    free.
```

Finally, surface stuck reservations — a reserved row that never got a cluster is the needs-reconciliation case, and it is invisible unless `status` prints it. In `submissions.py`'s status output, after the existing row summary:

```python
    stuck = submission_ledger.reserved_rows(db_path)
    if stuck:
        print(f"\nNEEDS RECONCILIATION — {len(stuck)} reserved row(s) with "
              f"no cluster. A submit died mid-flight; check jobsub_q "
              f"before reusing these windows:")
        for row in stuck:
            idx = row['indices']
            span = f"{idx[0]}..{idx[-1]}" if idx else 'none'
            print(f"  row {row['id']}  {row['tarball']}  indices {span}  "
                  f"reserved {row['created_utc']}")
```

with a test:

```python
    def test_status_surfaces_stuck_reservations(self):
        rid = self.sl.reserve_submission(
            self.db, tarball='cnf.mu2e.D.C.0.tar', entry=self.entry,
            indices=[0, 1, 2])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.submissions.print_status(self.db)
        self.assertIn('NEEDS RECONCILIATION', out.getvalue())
        self.assertIn(f'row {rid}', out.getvalue())
```

(`contextlib` is already imported in the suite; if not, add it beside the other stdlib imports at the top of `test/test_unit.py`.)

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py utils/submissions.py test/test_unit.py
git commit -m "fix(submit): reserve the ledger row before jobsub_submit

Closes the window the overlap guard only claimed to cover: the row now
exists while the submit is in flight, so a death between submit and
record no longer lets the next tick re-send the same payload.

An unrecordable window now raises BEFORE submitting rather than after,
which is also what makes a self-submission fail fast instead of
launching jobs it cannot record."
```

---

### Task 5: `check_inputs` on the direct submit path

**Files:**
- Modify: `utils/submit.py` (`_submit_one`, before the reservation)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `utils.check_inputs.check_inputs`, `format_report` (already imported at `submit.py:41`).

**Why:** `check_inputs` runs only inside `_enqueue_entries` (`submit.py:280`), reached only via `--enqueue`. A windowed direct submit (`--first/--num`, and every recovery resubmit) never calls it and can launch against unverified inputs — the bulk-death failure the gate exists to prevent. Note the existing carve-out at `submit.py:298`: a generic cnf bakes no inputs and is skipped; preserve that exactly.

- [ ] **Step 1: Write the failing test**

```python
class TestDirectPathPreflight(unittest.TestCase):
    def setUp(self):
        from utils import submit
        self.submit = submit
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 5, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}

    def test_direct_submit_refuses_on_bad_inputs(self):
        from utils.check_inputs import Problem
        bad = [Problem(dataset='dts.mu2e.X.Y.art', kind='missing',
                       detail='0 files')]
        with patch('utils.submit.check_inputs', return_value=(False, bad)):
            ok, problems = self.submit._preflight_inputs(
                self.entry, '/tmp/cnf.mu2e.TestDesc.TestConf.0.tar')
        self.assertFalse(ok)
        self.assertEqual(problems, bad)

    def test_direct_submit_passes_on_good_inputs(self):
        with patch('utils.submit.check_inputs', return_value=(True, [])):
            ok, problems = self.submit._preflight_inputs(
                self.entry, '/tmp/cnf.mu2e.TestDesc.TestConf.0.tar')
        self.assertTrue(ok)

    def test_generic_cnf_skips_the_check(self):
        # A generic (direct-input) cnf bakes no inputs — there is
        # nothing to pre-flight, and calling check_inputs would fail.
        generic = dict(self.entry)
        generic['input_pattern'] = 'dig.mu2e.%OnSpill.X.art'
        with patch('utils.submit.check_inputs') as chk:
            ok, problems = self.submit._preflight_inputs(
                generic, '/tmp/cnf.mu2e.TestDesc.TestConf.0.tar')
        self.assertTrue(ok)
        chk.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestDirectPathPreflight 2>&1 | tail -5`
Expected: FAIL — `AttributeError: module 'utils.submit' has no attribute '_preflight_inputs'`

- [ ] **Step 3: Implement**

Add to `utils/submit.py`:

```python
def _preflight_inputs(entry, tarball_path):
    """Verify a cnf's baked inputs before submitting. Returns
    (ok, problems).

    Mirrors the gate _enqueue_entries applies, so the DIRECT path
    (--first/--num and every recovery resubmit) gets it too — it is
    exactly the bulk-death failure check_inputs exists to prevent.
    A draining/generic cnf bakes no inputs and is skipped, the same
    carve-out _enqueue_entries makes.
    """
    if is_draining(entry):
        return True, []
    return check_inputs(str(tarball_path), inloc_of(entry))
```

Call it in `_submit_one` immediately before the reservation, skipping under `--dry-run`:

```python
    if not opts.dry_run:
        ok, problems = _preflight_inputs(entry, tarball_path)
        if not ok:
            print(format_report(problems))
            raise SystemExit(
                f"input pre-flight FAILED for {tarball_name} — refusing to "
                f"submit. Fix the inputs (or stage them) and retry.")
```

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "fix(submit): pre-flight inputs on the direct path too

check_inputs ran only via --enqueue, so windowed direct submits and
every recovery resubmit could launch against unverified inputs — the
bulk-death failure the gate exists to prevent. Generic/draining cnfs
keep their existing carve-out."
```

---

# PHASE 2 — the `prodtools-write` MCP server

### Task 6: Package scaffold, launcher, and registration

**Files:**
- Create: `mcp/src/prodtools_mcp_write/__init__.py`, `mcp/src/prodtools_mcp_write/server.py`, `mcp/scripts/start_write_mcp.sh`
- Modify: `mcp/pyproject.toml`, `mcp/scripts/install.sh`, `.mcp.json`

**Interfaces:**
- Produces: `create_write_mcp_server()`, `TOOL_NAMES = ('push_cnf', 'enqueue_campaign', 'run_submissions')`, `get_write_server_info()`.

- [ ] **Step 1: Write the failing test**

```python
class TestWriteServerRegistration(unittest.TestCase):
    def test_advertised_names_match_registered_tools(self):
        import asyncio
        from prodtools_mcp_write.server import create_write_mcp_server, TOOL_NAMES
        server = create_write_mcp_server()
        registered = sorted(t.name for t in asyncio.run(server.list_tools()))
        self.assertEqual(registered, sorted(TOOL_NAMES))

    def test_server_info_declares_the_write_capability(self):
        from prodtools_mcp_write.server import get_write_server_info
        info = get_write_server_info()
        self.assertTrue(info['performs_writes'])
        self.assertIn('mu2epro', info['description'])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestWriteServerRegistration 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp_write'`

- [ ] **Step 3: Implement**

`mcp/src/prodtools_mcp_write/__init__.py`:

```python
"""Write-capable MCP server for prodtools submission.

Deliberately SEPARATE from prodtools_mcp, which advertises that it
performs no writes. That claim is why its tools are called without
deliberation; mixing writes in would turn it into "read-only except
these three", a caveat that erodes. A separate tool namespace also
means one PreToolUse matcher covers every write tool that will ever
exist, instead of an enumeration a future tool can silently escape.
"""
```

`mcp/src/prodtools_mcp_write/server.py`:

```python
"""FastMCP registration for the write server."""
from mcp.server.fastmcp import FastMCP

from prodtools_mcp_write import tools

TOOL_NAMES = ('push_cnf', 'enqueue_campaign', 'run_submissions')


def get_write_server_info():
    return {
        'name': 'prodtools-write',
        'performs_writes': True,
        'description': (
            'Write-capable prodtools submission. run_as="self" needs no '
            'privilege and writes only your own scratch, datasets and '
            'ledger. run_as="mu2epro" registers artifacts in production '
            'SAM and submits production grid jobs; it is refused unless '
            'confirm=true.'),
        'tools': list(TOOL_NAMES),
    }


def create_write_mcp_server():
    mcp = FastMCP('prodtools-write')
    mcp.tool()(tools.push_cnf)
    mcp.tool()(tools.enqueue_campaign)
    mcp.tool()(tools.run_submissions)
    return mcp


def main():
    create_write_mcp_server().run()


if __name__ == '__main__':
    main()
```

**Launcher setup is factored into a shared helper, not duplicated.** Extract the environment preamble of `mcp/scripts/start_mcp.sh` — the guarded CVMFS sourcing, `muse setup ops`, the `PYTHON_BIN` selection, and the `VENV_SITE`/`REPO_ROOT`/ops `PYTHONPATH` ordering — verbatim into `mcp/scripts/_mcp_env.sh`, which ends by exporting `PYTHONPATH` and `PYTHON_BIN`. It is sourced, never executed, so it must not `exec` or `exit` on the success path.

Both launchers then reduce to sourcing it and dispatching:

```bash
#!/usr/bin/env bash
# Start the WRITE-capable prodtools MCP stdio server.
#
# All setup output goes to stderr: stdout is the JSON-RPC channel and a
# single stray line on it corrupts the protocol stream.
set -euo pipefail
MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$MCP_ROOT/scripts/_mcp_env.sh"

if [[ "${1:-}" == "--check" ]]; then
  "$PYTHON_BIN" - <<'PY'
import asyncio
from prodtools_mcp_write.server import create_write_mcp_server, TOOL_NAMES
registered = sorted(t.name for t in asyncio.run(create_write_mcp_server().list_tools()))
if registered != sorted(TOOL_NAMES):
    raise SystemExit(f"tool registration mismatch: {registered} != {sorted(TOOL_NAMES)}")
print("OK: write tools", ", ".join(registered))
PY
  exit 0
fi

exec "$PYTHON_BIN" -m prodtools_mcp_write.server
```

Rewrite `start_mcp.sh` to source the same helper, keeping its existing two-part `--check` (the "MCP deps without the ops path" check and the full-environment check) exactly as it is. **Verify both still start**: `bash mcp/scripts/start_mcp.sh --check` must print the same four read-only tool names it printed before this task.

The preamble carries subtle discipline — CVMFS scripts are not `set -e` clean, and the `PYTHONPATH` order (venv, then repo root, then ops) is load-bearing. Move it; do not re-derive it.

Add `prodtools_mcp_write` to the packages list in `mcp/pyproject.toml`, and add a `prodtools-write` entry to `.mcp.json` pointing at `mcp/scripts/start_write_mcp.sh`.

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3` and `bash mcp/scripts/start_write_mcp.sh --check`
Expected: `OK`, and the check prints all three tool names.

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp_write mcp/scripts/start_write_mcp.sh mcp/pyproject.toml mcp/scripts/install.sh .mcp.json test/test_unit.py
git commit -m "feat(mcp): prodtools-write server scaffold

Separate from the read-only server so its 'performs no writes' claim
stays literally true, and so one PreToolUse matcher covers the whole
write namespace."
```

---

### Task 7: `runner.py` — identity dispatch and the confirm gate

**Files:**
- Create: `mcp/src/prodtools_mcp_write/runner.py`
- Test: `test/test_unit.py`

**Interfaces:**
- Produces: `RunAs` (`'self'` | `'mu2epro'`), `require_confirmed(run_as, confirm) -> None`, `run_cli(argv, run_as, cwd=None) -> dict` returning `{'rc': int, 'stdout': str, 'stderr': str}`, `ksu_wrapper(argv) -> list[str]`.

This is the security-critical unit. It is the only place that knows how to become `mu2epro` and the only place that enforces `confirm`.

**Deliberate deviation from the spec.** §2.4 says `run_as="self"` should call the CLI *in-process* through `adapters.py`. This plan uses a subprocess for both identities instead, because **the MCP server's stdout is the JSON-RPC channel** — `json2jobdef` and `submit_map` print freely and also `chdir`, so running them in-process risks corrupting the protocol stream with a stray line. `adapters.py`'s stdout guard exists for exactly that hazard, but a subprocess removes the hazard rather than containing it, and keeps the two identities on one code path. Update the spec's §2.4 to match.

- [ ] **Step 1: Write the failing test**

```python
class TestWriteRunnerGate(unittest.TestCase):
    def setUp(self):
        from prodtools_mcp_write import runner
        self.runner = runner

    def test_mu2epro_without_confirm_is_refused(self):
        with self.assertRaises(PermissionError) as ctx:
            self.runner.require_confirmed('mu2epro', False)
        self.assertIn('confirm', str(ctx.exception).lower())

    def test_mu2epro_with_confirm_is_allowed(self):
        self.runner.require_confirmed('mu2epro', True)   # must not raise

    def test_self_needs_no_confirm(self):
        self.runner.require_confirmed('self', False)     # must not raise

    def test_unknown_run_as_is_refused(self):
        with self.assertRaises(ValueError):
            self.runner.require_confirmed('root', True)

    def test_ksu_wrapper_has_every_required_env_export(self):
        # Each of these is a known failure, not a style choice:
        # a caller-owned workdir breaks condor_vault_storer, an
        # unreset USER picks the wrong submitter and tarball, and
        # without the CVMFS sourcing jobsub_submit is not on PATH.
        cmd = ' '.join(self.runner.ksu_wrapper(['bin/submit_map', '--map', '/tmp/m.json']))
        self.assertIn('ksu mu2epro', cmd)
        self.assertIn('unset MUSE_WORK_DIR', cmd)
        self.assertIn('USER=mu2epro', cmd)
        self.assertIn('LOGNAME=mu2epro', cmd)
        self.assertIn('HOME=/exp/mu2e/app/home/mu2epro', cmd)
        self.assertIn('XDG_RUNTIME_DIR', cmd)
        self.assertIn('mktemp -d', cmd)
        self.assertIn('setupmu2e-art.sh', cmd)
        self.assertIn('muse setup ops', cmd)

    def test_self_does_not_use_ksu(self):
        with patch('subprocess.run') as run:
            run.return_value = SimpleNamespace(returncode=0, stdout='', stderr='')
            self.runner.run_cli(['bin/submit_map', '--map', '/tmp/m.json'], 'self')
        argv = run.call_args[0][0]
        self.assertNotIn('ksu', ' '.join(argv))

    def test_missing_mu2epro_token_is_reported_never_remediated(self):
        with patch('subprocess.run') as run:
            run.return_value = SimpleNamespace(
                returncode=1, stdout='',
                stderr='kx509: no credentials cache found')
            out = self.runner.run_cli(['bin/submit_map'], 'mu2epro')
        self.assertEqual(out['rc'], 1)
        # Nothing in the runner may attempt a refresh.
        joined = ' '.join(' '.join(c[0][0]) for c in run.call_args_list)
        for forbidden in ('htgettoken', 'getToken', 'kinit', 'voms-proxy-init'):
            self.assertNotIn(forbidden, joined)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestWriteRunnerGate 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp_write.runner'`

- [ ] **Step 3: Implement**

```python
"""Identity dispatch for the write server.

The ONLY place that knows how to become mu2epro, and the ONLY place
that enforces confirm. Kept apart from tools.py so the security-
critical logic is testable without MCP plumbing.
"""
import os
import subprocess

RUN_AS_VALUES = ('self', 'mu2epro')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# Verbatim from .claude/commands/mu2epro-submit.md:121-133. Every line
# here is a known failure mode, not a style preference:
#   - mktemp INSIDE ksu: a caller-owned workdir makes
#     condor_vault_storer fail
#   - USER/LOGNAME/HOME: ksu does not reset them, so getpass.getuser()
#     would return the caller and pick the wrong tarball and role
#   - XDG_RUNTIME_DIR: the caller's /run/user/<uid> is not writable by
#     mu2epro
#   - unset MUSE_WORK_DIR only: unsetting MUSE_* breaks the muse shell
#     function itself
_KSU_TEMPLATE = """
unset MUSE_WORK_DIR
export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
WORKDIR=$(mktemp -d /tmp/mu2epro_mcp.XXXXXX)
export XDG_RUNTIME_DIR="$WORKDIR"
cd "$WORKDIR"
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1
muse setup ops > /dev/null 2>&1
setup OfflineOps > /dev/null 2>&1
{command}
"""


def require_confirmed(run_as, confirm):
    """Refuse a production write that was not explicitly confirmed.

    This gate lives in the call signature so it cannot be configured
    away. The PreToolUse hook is the second, independent gate; a hook
    can be un-armed by a settings reload, and an irreversible action
    must not depend on that.
    """
    if run_as not in RUN_AS_VALUES:
        raise ValueError(
            f"run_as must be one of {RUN_AS_VALUES}, got {run_as!r}")
    if run_as == 'mu2epro' and not confirm:
        raise PermissionError(
            "run_as='mu2epro' registers artifacts in production SAM and "
            "submits production grid jobs. This is not reversible. Pass "
            "confirm=true to proceed.")


# Entry points this server may ever run as mu2epro. Defence in depth:
# quoting alone makes injection impossible, but an allowlist also stops a
# caller invoking an arbitrary repo script as the production account.
ENTRY_POINTS = ('bin/json2jobdef', 'bin/submit_map', 'bin/submissions')


def ksu_wrapper(argv):
    """Wrap a repo-relative argv in the full working ksu block.

    EVERY interpolated word is quoted, argv[0] included. An earlier draft
    of this plan quoted only argv[1:] and interpolated the executable
    raw — `argv[0] = 'bin/x; id #'` then executed as mu2epro. The one
    argument that names what runs is the one that most needs quoting.
    """
    if argv[0] not in ENTRY_POINTS:
        raise ValueError(f"not a permitted entry point: {argv[0]!r}")
    command = ' '.join(
        ['bash', _quote(f'{REPO_ROOT}/{argv[0]}')] +
        [_quote(a) for a in argv[1:]])
    return ['ksu', 'mu2epro', '-e', '/bin/bash', '-c',
            _KSU_TEMPLATE.format(command=command)]


def _quote(arg):
    return "'" + str(arg).replace("'", "'\\''") + "'"


def run_cli(argv, run_as, cwd=None):
    """Run a prodtools command under the requested identity.

    Credentials are NEVER remediated. A missing mu2epro token comes back
    as a non-zero rc with its stderr intact; no refresh is attempted,
    ever.
    """
    if run_as == 'mu2epro':
        cmd = ksu_wrapper(argv)
    else:
        cmd = [f'bash', f'{REPO_ROOT}/{argv[0]}'] + [str(a) for a in argv[1:]]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd or REPO_ROOT)
    return {'rc': proc.returncode, 'stdout': proc.stdout,
            'stderr': proc.stderr}
```

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp_write/runner.py test/test_unit.py
git commit -m "feat(mcp): write-server identity dispatch and confirm gate

require_confirmed lives in the call signature so it cannot be
configured away; the PreToolUse hook is a second, independent gate,
because a hook can be un-armed by a settings reload and an
irreversible action must not depend on that.

The ksu block is verbatim from mu2epro-submit.md — each line is a
known failure mode."
```

---

### Task 8: `push_cnf`

**Files:**
- Create: `mcp/src/prodtools_mcp_write/tools.py` (this tool only)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `runner.require_confirmed`, `runner.run_cli`.
- Produces: `push_cnf(json: str, desc: str, dsconf: str, jobdefs_map: str, run_as: str, confirm: bool = False, simjob_version: str | None = None) -> dict` returning `{'tarball', 'datasets', 'map_path', 'entry_index'}`.

- [ ] **Step 1: Write the failing test**

```python
class TestPushCnfTool(unittest.TestCase):
    def setUp(self):
        from prodtools_mcp_write import tools
        self.tools = tools

    def test_mu2epro_without_confirm_refused_before_running_anything(self):
        with patch('prodtools_mcp_write.runner.run_cli') as run:
            with self.assertRaises(PermissionError):
                self.tools.push_cnf(json='data/Run1B/resampler_beam.json',
                                    desc='PhysicalPionStops', dsconf='Run1Bap',
                                    jobdefs_map='/tmp/m.json',
                                    run_as='mu2epro')
        run.assert_not_called()

    def test_builds_the_expected_argv(self):
        with patch('prodtools_mcp_write.runner.run_cli',
                   return_value={'rc': 0, 'stdout': '', 'stderr': ''}) as run:
            with patch('prodtools_mcp_write.tools._read_map_entry',
                       return_value=(3, {'tarball': 'cnf.mu2e.D.C.0.tar'})):
                self.tools.push_cnf(json='data/Run1B/resampler_beam.json',
                                    desc='PhysicalPionStops', dsconf='Run1Bap',
                                    jobdefs_map='/tmp/m.json',
                                    run_as='mu2epro', confirm=True)
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], 'bin/json2jobdef')
        self.assertIn('--prod', argv)
        self.assertIn('--jobdefs', argv)
        self.assertIn('/tmp/m.json', argv)
        self.assertIn('PhysicalPionStops', argv)

    def test_result_is_read_from_the_map_not_stdout(self):
        noisy = 'Added JSON entry for cnf.mu2e.WRONG.tar to jobdefs_list.json'
        with patch('prodtools_mcp_write.runner.run_cli',
                   return_value={'rc': 0, 'stdout': noisy, 'stderr': ''}):
            with patch('prodtools_mcp_write.tools._read_map_entry',
                       return_value=(0, {'tarball': 'cnf.mu2e.RIGHT.C.0.tar'})):
                out = self.tools.push_cnf(json='j.json', desc='D', dsconf='C',
                                          jobdefs_map='/tmp/m.json',
                                          run_as='self')
        self.assertEqual(out['tarball'], 'cnf.mu2e.RIGHT.C.0.tar')
        self.assertEqual(out['entry_index'], 0)

    def test_nonzero_rc_raises_with_stderr(self):
        with patch('prodtools_mcp_write.runner.run_cli',
                   return_value={'rc': 2, 'stdout': '', 'stderr': 'boom'}):
            with self.assertRaises(RuntimeError) as ctx:
                self.tools.push_cnf(json='j.json', desc='D', dsconf='C',
                                    jobdefs_map='/tmp/m.json', run_as='self')
        self.assertIn('boom', str(ctx.exception))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestPushCnfTool 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp_write.tools'`

- [ ] **Step 3: Implement**

```python
"""The three write tools. Thin: validate, delegate to runner, read the
result back from the artifact the CLI wrote — never from its stdout."""
import json as _json
from pathlib import Path

from prodtools_mcp_write import runner


def _read_map_entry(map_path):
    """Return (index, entry) for the LAST entry in a jobdefs map.

    json2jobdef appends the entry it just pushed, so the tarball name
    comes from the file it wrote rather than from parsing human output
    through ksu — which is exactly the parsing this project exists to
    eliminate.
    """
    entries = _json.loads(Path(map_path).read_text())
    if not entries:
        raise RuntimeError(f"{map_path} has no entries after the push")
    return len(entries) - 1, entries[-1]


def push_cnf(json, desc, dsconf, jobdefs_map, run_as, confirm=False,
             simjob_version=None):
    """Build a cnf tarball and register it.

    run_as="self" registers under your own dataset owner and scratch
    outstage. run_as="mu2epro" registers in PRODUCTION SAM and is not
    reversible; it requires confirm=true.

    jobdefs_map must be a path you can write: production_manager/
    direct_maps/ is mu2epro-owned, and this tool never invents a path.
    """
    runner.require_confirmed(run_as, confirm)
    argv = ['bin/json2jobdef', '--json', json, '--desc', desc,
            '--dsconf', dsconf, '--prod', '--jobdefs', jobdefs_map,
            '--verbose']
    result = runner.run_cli(argv, run_as)
    if result['rc'] != 0:
        raise RuntimeError(
            f"json2jobdef failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")
    index, entry = _read_map_entry(jobdefs_map)
    return {
        'tarball': entry.get('tarball'),
        'datasets': [o.get('dataset') for o in entry.get('outputs', [])],
        'map_path': jobdefs_map,
        'entry_index': index,
    }
```

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp_write/tools.py test/test_unit.py
git commit -m "feat(mcp): push_cnf tool

The result is read back from the map file json2jobdef wrote, not
scraped from its stdout through ksu."
```

---

### Task 9: `enqueue_campaign` and `run_submissions`

**Files:**
- Modify: `mcp/src/prodtools_mcp_write/tools.py`
- Test: `test/test_unit.py`

**Interfaces:**
- Produces: `enqueue_campaign(map_path: str, entry: int, slice_size: int, run_as: str, confirm: bool = False) -> dict` returning `{'campaign_id', 'njobs', 'tarball'}`; `run_submissions(campaign_id: int, run_as: str, confirm: bool = False) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
class TestEnqueueAndRunTools(unittest.TestCase):
    def setUp(self):
        from prodtools_mcp_write import tools
        from utils import submission_ledger as sl
        self.tools = tools
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')

    def test_entry_is_required_no_fan_out_default(self):
        import inspect
        sig = inspect.signature(self.tools.enqueue_campaign)
        self.assertIs(sig.parameters['entry'].default, inspect.Parameter.empty)

    def test_campaign_id_is_required(self):
        import inspect
        sig = inspect.signature(self.tools.run_submissions)
        self.assertIs(sig.parameters['campaign_id'].default,
                      inspect.Parameter.empty)

    def test_enqueue_refuses_mu2epro_without_confirm(self):
        with patch('prodtools_mcp_write.runner.run_cli') as run:
            with self.assertRaises(PermissionError):
                self.tools.enqueue_campaign(map_path='/tmp/m.json', entry=0,
                                            slice_size=500, run_as='mu2epro')
        run.assert_not_called()

    def test_enqueue_reads_campaign_id_from_the_ledger(self):
        camp = self.sl.create_campaign(
            self.db, tarball='cnf.mu2e.D.C.0.tar',
            entry={'tarball': 'cnf.mu2e.D.C.0.tar', 'njobs': 500},
            slice_size=500, map_path='/tmp/m.json')
        with patch('prodtools_mcp_write.runner.run_cli',
                   return_value={'rc': 0, 'stdout': 'noise', 'stderr': ''}):
            with patch('prodtools_mcp_write.tools._ledger_path_for',
                       return_value=self.db):
                with patch('prodtools_mcp_write.tools._read_map_entry',
                           return_value=(0, {'tarball': 'cnf.mu2e.D.C.0.tar'})):
                    out = self.tools.enqueue_campaign(
                        map_path='/tmp/m.json', entry=0, slice_size=500,
                        run_as='self')
        self.assertEqual(out['campaign_id'], camp)
        self.assertEqual(out['njobs'], 500)

    def test_run_submissions_reports_attention_keys(self):
        with patch('prodtools_mcp_write.runner.run_cli',
                   return_value={'rc': 2, 'stdout': '', 'stderr': ''}):
            out = self.tools.run_submissions(campaign_id=1, run_as='self')
        # rc=2 is the documented "needs attention" exit, not a crash.
        self.assertTrue(out['needs_attention'])
        self.assertEqual(out['rc'], 2)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestEnqueueAndRunTools 2>&1 | tail -5`
Expected: FAIL — `AttributeError: module 'prodtools_mcp_write.tools' has no attribute 'enqueue_campaign'`

- [ ] **Step 3: Implement**

Append to `tools.py`:

```python
def _ledger_path_for(run_as):
    """Which ledger this identity writes. For mu2epro this IS the
    production ledger (see submission_ledger.ledger_for)."""
    from utils import submission_ledger
    return submission_ledger.ledger_for(
        'mu2epro' if run_as == 'mu2epro' else None)


def enqueue_campaign(map_path, entry, slice_size, run_as, confirm=False):
    """Create a campaign row for ONE entry of a map.

    `entry` is required: a map can hold several entries and a tool whose
    default is "all of them" is the hazard this signature exists to
    remove.
    """
    runner.require_confirmed(run_as, confirm)
    argv = ['bin/submit_map', '--map', map_path, '--entry', str(entry),
            '--enqueue', '--slice-size', str(slice_size)]
    result = runner.run_cli(argv, run_as)
    if result['rc'] != 0:
        raise RuntimeError(
            f"submit_map --enqueue failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")

    from utils import submission_ledger
    _, map_entry = _read_map_entry(map_path)
    tarball = map_entry.get('tarball')
    db = _ledger_path_for(run_as)
    match = [c for c in submission_ledger.all_campaigns(db)
             if c['tarball'] == tarball]
    if not match:
        raise RuntimeError(
            f"enqueue reported success but no campaign for {tarball} is in "
            f"{db} — reconcile before retrying")
    campaign = match[-1]
    return {'campaign_id': campaign['id'],
            'njobs': (campaign.get('entry') or {}).get('njobs'),
            'tarball': tarball}


def run_submissions(campaign_id, run_as, confirm=False):
    """Tick ONE campaign: top-up plus drain.

    `campaign_id` is required. Ticking every active campaign is the
    cron's job, not an interactive call.
    """
    runner.require_confirmed(run_as, confirm)
    argv = ['bin/submissions', 'run', '--campaign', str(campaign_id)]
    result = runner.run_cli(argv, run_as)
    # rc=2 is the documented "something needs attention" exit — held
    # rows, exhausted recoveries, a paused campaign. It is a report, not
    # a crash, and must not be raised as an error.
    if result['rc'] not in (0, 2):
        raise RuntimeError(
            f"submissions run failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")
    return {'rc': result['rc'],
            'needs_attention': result['rc'] == 2,
            'campaign_id': campaign_id,
            'output': result['stdout']}
```

**`submissions run` has no `--campaign` flag today** — it has `--dry-run`, `--row`, `--max-attempts`, `--max-queued` (`submissions.py:1152-1169`). Add it in this task, since `run_submissions` requires it:

```python
    run.add_argument('--campaign', type=int, default=None,
                     help='Top up only this campaign id (the recovery '
                          'pass still runs). Without it every active '
                          'campaign is ticked, which is the cron '
                          'behaviour.')
```

and filter in `top_up`, immediately after the active-campaign list is fetched:

```python
    campaigns = submission_ledger.active_campaigns(db_path)
    if only_campaign is not None:
        campaigns = [c for c in campaigns if c['id'] == only_campaign]
```

threading `only_campaign=None` through `top_up`'s signature. Cover it with:

```python
    def test_campaign_filter_ticks_only_the_named_one(self):
        submitted = []
        with patch.object(self.submissions, 'submit_slice',
                          side_effect=lambda c, n, db: submitted.append(c['id']) or True):
            self.submissions.top_up(self.db, cap=10_000, only_campaign=self.camp_b)
        self.assertEqual(submitted, [self.camp_b])
```

- [ ] **Step 4: Run the tests**

Run: `python3 -u test/test_unit.py 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp_write/tools.py utils/submissions.py test/test_unit.py
git commit -m "feat(mcp): enqueue_campaign and run_submissions tools

entry and campaign_id are required — no argument defaults to 'all'.
rc=2 is surfaced as needs_attention rather than raised: it is the
documented attention exit, not a crash."
```

---

### Task 10: Arm the gate, and document it

**Files:**
- Modify: `.claude/settings.json` (PreToolUse), `.claude/hooks/mu2epro-guard.sh`, `CLAUDE.md`, `mcp/README.md`
- Test: manual verification, plus the suite

**Interfaces:**
- Consumes: everything above.

**Why this task exists:** the current guard is registered with `matcher: "Bash"` and greps the command string for `ksu mu2epro`. A subprocess spawned inside the MCP server is not a Bash tool call and never reaches it. Without this task the gate disappears by construction rather than by decision.

- [ ] **Step 1: Extend the hook registration**

In `.claude/settings.json`, add a second `PreToolUse` matcher beside the existing `Bash` one:

```json
{
  "matcher": "mcp__prodtools-write__.*",
  "hooks": [
    {
      "type": "command",
      "command": "bash /exp/mu2e/app/users/oksuzian/muse_050125/prodtools/.claude/hooks/mcp-write-guard.sh",
      "timeout": 5
    }
  ]
}
```

- [ ] **Step 2: Write the hook**

Create `.claude/hooks/mcp-write-guard.sh`:

```bash
#!/bin/bash
# PreToolUse guard for the prodtools-write MCP server.
#
# The Bash guard (mu2epro-guard.sh) greps the command string for
# `ksu mu2epro`. A subprocess spawned INSIDE the MCP server is not a
# Bash tool call, so that hook can never see it — this one covers the
# whole mcp__prodtools-write__* namespace instead of enumerating tools,
# so a future write tool cannot silently escape the gate.
#
# run_as="self" is allowed through: it writes only the caller's own
# scratch, datasets and ledger. Only run_as="mu2epro" prompts.
input=$(cat)
run_as=$(printf '%s' "$input" | jq -r '.tool_input.run_as // ""')
if [ "$run_as" = "mu2epro" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"This call runs as the mu2epro PRODUCTION account: it can register SAM datasets, write dCache, and submit production grid jobs. Not reversible. Confirm before executing."}}'
fi
exit 0
```

- [ ] **Step 3: Verify the gate both ways**

Run, and confirm the first prompts and the second does not:

```bash
echo '{"tool_input":{"run_as":"mu2epro"}}' | bash .claude/hooks/mcp-write-guard.sh
echo '{"tool_input":{"run_as":"self"}}'    | bash .claude/hooks/mcp-write-guard.sh
```

Expected: the first prints a `permissionDecision: ask` payload; the second prints nothing.

Then confirm the in-tool gate is independent of the hook:

```bash
python3 -u test/test_unit.py TestWriteRunnerGate 2>&1 | tail -3
```

Expected: `OK` — proving `confirm` is enforced even with no hook armed.

- [ ] **Step 4: Document**

In `CLAUDE.md`, under the MCP section, add:

```markdown
A second, write-capable server (`prodtools-write`) exposes submission:
`push_cnf`, `enqueue_campaign`, `run_submissions`. Every tool takes a
required `run_as`:

- `run_as="self"` needs no privilege and writes only your own scratch,
  datasets and ledger (`/exp/mu2e/data/users/$USER/prodtools/`). No
  prompt.
- `run_as="mu2epro"` registers artifacts in production SAM and submits
  production grid jobs. It is refused unless `confirm=true`, AND a
  PreToolUse hook prompts. Both gates are deliberate: a hook can be
  un-armed by a settings reload.

The read-only `prodtools` server still performs NO writes. Keep it that
way — that claim is why its tools are called without deliberation.
```

Note in `mcp/README.md` that `submissions status` reads the production ledger by default and `--mine` reads your own.

- [ ] **Step 5: Commit**

```bash
git add .claude/settings.json .claude/hooks/mcp-write-guard.sh CLAUDE.md mcp/README.md
git commit -m "feat(mcp): arm the write gate and document run_as

The Bash matcher cannot see a subprocess spawned inside an MCP server,
so the write namespace needs its own PreToolUse matcher. It matches the
whole namespace rather than enumerating tools, so a future write tool
cannot silently escape it. run_as=self is allowed through; only
mu2epro prompts."
```

---

## Acceptance test (run once, after Task 10)

Not automated — it needs the real grid. Run a tiny `run_as="self"` campaign end to end and confirm each stage:

1. `push_cnf(..., run_as="self")` → a cnf registered under your own dataset owner.
2. `enqueue_campaign(..., run_as="self")` → a campaign row in `/exp/mu2e/data/users/$USER/prodtools/submissions.db` (created by `ensure_ledger_dir`, not by hand).
3. `run_submissions(..., run_as="self")` → a real cluster, submitted with the Analysis role and a scratch outstage.
4. `submissions status --mine` shows it; plain `submissions status` still shows production.
5. Kill the client mid-submit and re-run: the reserved row must make the retry refuse rather than double-submit.

Step 5 is the one that proves Phase 1 worked. Everything else can pass with the old ordering.

## Deferred, deliberately

- **Retiring `/mu2epro-submit`.** It stays the supported path and the fallback while the MCP path earns trust.
- **A request/approval queue** letting non-privileged users initiate *production* work. That is a governance system, not plumbing.
- **The MCP output-count defect** (`prodtools_mcp/tools/status.py:161-168` feeds the bare glob `*.art` to SAM, so every glob-output campaign reads `state: "unknown"`). Real, unrelated, tracked separately.
