# Sliced Campaign Submission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** POMS-style sliced ("draining") campaign submission for the direct backend: `submit_map --enqueue` registers campaigns; the existing `recover` cron feeds whole slices while total mu2epro idle+running stays under a cap; resources (memory/disk/lifetime) move into map-entry keys and inherit through snapshots.

**Architecture:** One new `campaigns` table in the existing submission-ledger sqlite DB, one new top-up phase inside the existing `recover` loop (same cron, same lock), a dated submission log written by the submit path itself, and a resource-precedence chain (CLI flag > entry key > built-in default) frozen into every ledger/campaign snapshot.

**Tech Stack:** Python 3 stdlib only (sqlite3, subprocess, fcntl, argparse) — no SQLAlchemy, no new dependencies. Tests: `unittest` in `test/test_unit.py`.

**Spec:** `docs/superpowers/specs/2026-07-18-sliced-submission-design.md`

## Global Constraints

- Stdlib `sqlite3` ONLY in `utils/submission_ledger.py` and everything the submit path imports (runs as mu2epro in the bare ops env — no pyenv ana).
- DB default `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, env `MU2E_SUBMISSION_DB`, flag `--ledger-db` / `--db`.
- Queue cap resolution: `--max-queued` flag > `MU2E_MAX_QUEUED` env > `DEFAULT_MAX_QUEUED = 10000` module constant in `utils/recover.py`. Resolved once per invocation, never stored in the DB.
- `--slice-size` default **1000**, frozen into the campaign row at enqueue.
- **Whole slices only**: never clamp a slice to headroom; if `count + n > cap`, stop feeding for the tick. `n = min(slice_size, njobs - cursor)` (short only at end of entry).
- Queue count = `jobsub_q --user mu2epro -af JobStatus`, counting states `1` (idle) + `2` (running) only. Count failure or non-numeric output → skip the whole top-up phase loudly. Never guess.
- Campaign states exactly `active | complete | paused | cancelled`; transitions `active → complete|paused|cancelled`, `paused → active|cancelled`; anything else raises.
- Cursor is **entry-relative**; slices go out as `--first/--num`; the snapshot keeps `firstjob` intact (unlike recovery's `--indices`, which drops it).
- Duplicate enqueue (an *active* campaign already exists for the tarball) → hard error.
- Submission failure during top-up → campaign `paused` with note, cursor NOT advanced, no blind retry. `campaign-paused` joins the exit-2 conditions.
- `recover --status` and `recover --dry-run` are strictly read-only (would-* labels; no DB writes, no submissions).
- Enqueue DB write failure = hard error (nothing submitted yet). Post-submission ledger/log writes must NEVER raise — warn loudly and continue.
- Resource precedence: CLI flag > entry key (`memory`/`disk`/`expected_lifetime`) > built-ins in `utils/jobsub_argv.py` (`DEFAULT_MEMORY = "2000MB"`, `DEFAULT_DISK = "30GB"`, `DEFAULT_LIFETIME = "24h"`). Effective values are merged into every ledger and campaign `entry_json` snapshot. No escalation on retry.
- The loop never runs `condor_rm`; token problems are reported, never remediated (no fetch/refresh).
- No worker-side changes; worker fcl byte-identity untouched.
- Test command: `python3 test/test_unit.py` from the repo root (also run single classes as `python3 -m unittest test.test_unit.TestClassName -v`). No network, no live submissions in tests.
- Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov`

---

### Task 1: Campaigns table in the submission ledger

**Files:**
- Modify: `utils/submission_ledger.py` (append after `close_row`, ~line 137; extend `_connect`)
- Test: `test/test_unit.py` (new class after `TestSubmissionLedger`, ~line 4046)

**Interfaces:**
- Consumes: existing `_connect(db_path)`, `_now()` in the same module.
- Produces (later tasks rely on these exact signatures):
  - `CAMPAIGN_STATES = ('active', 'complete', 'paused', 'cancelled')`
  - `create_campaign(db_path, *, tarball, entry, slice_size, map_path=None) -> int`
  - `active_campaigns(db_path) -> list[dict]` — oldest first; each dict has keys `id, created_utc, state, map_path, tarball, entry (parsed), cursor, slice_size, closed_utc, note`
  - `all_campaigns(db_path) -> list[dict]` — same shape, every state
  - `advance_campaign(db_path, camp_id, new_cursor) -> None` — active rows only, cursor never moves backward
  - `set_campaign_state(db_path, camp_id, state, note=None) -> None` — validated transitions

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py`, directly after the `TestSubmissionLedger` class:

```python
class TestCampaignLedger(unittest.TestCase):
    """campaigns table in utils/submission_ledger.py (sliced submission)."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 10, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}

    def _create(self, tarball=None, slice_size=4):
        return self.sl.create_campaign(
            self.db, tarball=tarball or self.entry['tarball'],
            entry=self.entry, slice_size=slice_size,
            map_path='/tmp/map.json')

    def test_create_and_read_roundtrip(self):
        cid = self._create()
        camps = self.sl.active_campaigns(self.db)
        self.assertEqual(len(camps), 1)
        c = camps[0]
        self.assertEqual(c['id'], cid)
        self.assertEqual(c['state'], 'active')
        self.assertEqual(c['cursor'], 0)
        self.assertEqual(c['slice_size'], 4)
        self.assertEqual(c['entry'], self.entry)
        self.assertEqual(c['map_path'], '/tmp/map.json')
        self.assertIsNone(c['closed_utc'])

    def test_duplicate_active_tarball_refused(self):
        self._create()
        with self.assertRaises(ValueError):
            self._create()

    def test_reenqueue_allowed_after_close(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'cancelled')
        self._create()  # must not raise
        self.assertEqual(len(self.sl.all_campaigns(self.db)), 2)

    def test_slice_size_validated(self):
        with self.assertRaises(ValueError):
            self._create(slice_size=0)

    def test_advance_cursor(self):
        cid = self._create()
        self.sl.advance_campaign(self.db, cid, 4)
        self.assertEqual(self.sl.active_campaigns(self.db)[0]['cursor'], 4)

    def test_advance_backward_refused(self):
        cid = self._create()
        self.sl.advance_campaign(self.db, cid, 4)
        with self.assertRaises(ValueError):
            self.sl.advance_campaign(self.db, cid, 2)

    def test_advance_nonactive_refused(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'paused')
        with self.assertRaises(ValueError):
            self.sl.advance_campaign(self.db, cid, 4)

    def test_state_transitions(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'paused', note='op pause')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'], 'paused')
        self.sl.set_campaign_state(self.db, cid, 'active')   # resume
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['state'], 'active')
        self.assertIsNone(c['closed_utc'])                   # reopened
        self.sl.set_campaign_state(self.db, cid, 'complete')
        self.assertIsNotNone(self.sl.all_campaigns(self.db)[0]['closed_utc'])

    def test_invalid_transitions_raise(self):
        cid = self._create()
        with self.assertRaises(ValueError):
            self.sl.set_campaign_state(self.db, cid, 'nonsense')
        self.sl.set_campaign_state(self.db, cid, 'complete')
        with self.assertRaises(ValueError):
            self.sl.set_campaign_state(self.db, cid, 'active')  # complete is terminal
        with self.assertRaises(ValueError):
            self.sl.set_campaign_state(self.db, 999, 'paused')  # no such id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestCampaignLedger -v`
Expected: every test ERRORs with `AttributeError: module 'utils.submission_ledger' has no attribute 'create_campaign'` (and friends).

- [ ] **Step 3: Implement the campaigns table**

In `utils/submission_ledger.py`:

(a) After the `STATES` line (~line 26), add:

```python
CAMPAIGN_STATES = ('active', 'complete', 'paused', 'cancelled')

# Sliced-submission campaign lifecycle. 'complete' means fully SUBMITTED
# (verification continues per ledger row); 'paused' is the operator /
# submit-failure hold; 'cancelled' closes the campaign but already-
# submitted rows still get recovered.
_CAMPAIGN_TRANSITIONS = {
    'active': ('complete', 'paused', 'cancelled'),
    'paused': ('active', 'cancelled'),
}
```

(b) After the `_SCHEMA` string (~line 44), add:

```python
_CAMPAIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_utc  TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'active',
  map_path     TEXT,
  tarball      TEXT NOT NULL,
  entry_json   TEXT NOT NULL,
  cursor       INTEGER NOT NULL DEFAULT 0,
  slice_size   INTEGER NOT NULL,
  closed_utc   TEXT,
  note         TEXT
)
"""
```

(c) In `_connect`, after `con.execute(_SCHEMA)` add `con.execute(_CAMPAIGN_SCHEMA)`.

(d) Append at the end of the file:

```python
def create_campaign(db_path, *, tarball, entry, slice_size, map_path=None):
    """Register a sliced-submission campaign (cursor 0); return its id.

    entry is snapshotted verbatim — the caller has already merged any
    CLI resource overrides, so slices reproduce what was asked for. A
    second ACTIVE campaign for the same tarball is refused: that is the
    double-submit guard.
    """
    if slice_size < 1:
        raise ValueError(f"slice_size must be >= 1, got {slice_size}")
    con = _connect(db_path)
    try:
        dup = con.execute(
            "SELECT id FROM campaigns WHERE tarball = ? AND state = 'active'",
            (tarball,)).fetchone()
        if dup:
            raise ValueError(
                f"active campaign {dup['id']} already exists for {tarball}")
        cur = con.execute(
            'INSERT INTO campaigns '
            '(created_utc, state, map_path, tarball, entry_json, cursor, '
            ' slice_size) VALUES (?, ?, ?, ?, ?, 0, ?)',
            (_now(), 'active', map_path, tarball, json.dumps(entry),
             slice_size))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _campaign_to_dict(row):
    d = dict(row)
    d['entry'] = json.loads(d.pop('entry_json'))
    return d


def active_campaigns(db_path):
    """Active campaigns, oldest first, entry JSON parsed."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM campaigns WHERE state = 'active' ORDER BY id"
        ).fetchall()
        return [_campaign_to_dict(r) for r in rows]
    finally:
        con.close()


def all_campaigns(db_path):
    """Every campaign regardless of state, oldest first."""
    con = _connect(db_path)
    try:
        rows = con.execute('SELECT * FROM campaigns ORDER BY id').fetchall()
        return [_campaign_to_dict(r) for r in rows]
    finally:
        con.close()


def advance_campaign(db_path, camp_id, new_cursor):
    """Move an active campaign's cursor forward. Backward moves and
    non-active campaigns raise — the cursor only ever advances."""
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE campaigns SET cursor = ? "
            "WHERE id = ? AND state = 'active' AND cursor <= ?",
            (new_cursor, camp_id, new_cursor))
        if cur.rowcount != 1:
            raise ValueError(
                f"no active campaign {camp_id} with cursor <= {new_cursor}")
        con.commit()
    finally:
        con.close()


def set_campaign_state(db_path, camp_id, state, note=None):
    """Validated campaign transition (see _CAMPAIGN_TRANSITIONS).
    Reactivating a paused campaign clears closed_utc."""
    if state not in CAMPAIGN_STATES:
        raise ValueError(f"invalid campaign state: {state}")
    allowed_from = tuple(f for f, targets in _CAMPAIGN_TRANSITIONS.items()
                         if state in targets)
    con = _connect(db_path)
    try:
        row = con.execute('SELECT state FROM campaigns WHERE id = ?',
                          (camp_id,)).fetchone()
        if row is None:
            raise ValueError(f"no campaign {camp_id}")
        if row['state'] not in allowed_from:
            raise ValueError(
                f"campaign {camp_id}: cannot go {row['state']} -> {state}")
        closed = None if state == 'active' else _now()
        con.execute(
            'UPDATE campaigns SET state = ?, closed_utc = ?, note = ? '
            'WHERE id = ?',
            (state, closed, note, camp_id))
        con.commit()
    finally:
        con.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestCampaignLedger -v`
Expected: 9 tests, OK.

Then the full suite: `python3 test/test_unit.py`
Expected: OK (no regressions; count grows by 9).

- [ ] **Step 5: Commit**

```bash
git add utils/submission_ledger.py test/test_unit.py
git commit -m "feat: campaigns table in the submission ledger

Sliced-submission state: entry snapshot, entry-relative cursor,
slice_size, validated active|complete|paused|cancelled transitions,
duplicate-active-tarball guard."
```
(with the standard footer from Global Constraints)

---

### Task 2: Entry resource keys (memory/disk/expected_lifetime)

**Files:**
- Modify: `utils/poms_entry.py` (append accessor after `validate_window`, end of file)
- Modify: `utils/submit.py` (new helpers after `_record_in_ledger` ~line 222; edit `submit_entry_direct` argv call ~lines 445-478)
- Modify: `utils/json2jobdef.py` (`append_jobdef`, after the base dict ~line 435)
- Test: `test/test_unit.py` (new class after `TestCampaignLedger`)

**Interfaces:**
- Consumes: `jobsub_argv.build_jobsub_argv(disk=..., memory=..., expected_lifetime=...)` — `None` values fall back to `DEFAULT_DISK/MEMORY/LIFETIME` (already true, no change there).
- Produces:
  - `poms_entry.RESOURCE_KEYS = ('memory', 'disk', 'expected_lifetime')`
  - `poms_entry.resources_of(entry: dict) -> dict` — subset of RESOURCE_KEYS present; non-string value raises ValueError
  - `submit._effective_resources(entry, opts) -> dict` — keys `memory, disk, expected_lifetime`; value = CLI flag or entry key or None
  - `submit._snapshot_entry(entry, resources) -> dict` — copy of entry with non-None resources merged (original never mutated). Tasks 3 and 5 rely on snapshots built this way.

- [ ] **Step 1: Write the failing tests**

```python
class TestEntryResources(unittest.TestCase):
    """memory/disk/expected_lifetime: entry keys, precedence, snapshot."""

    def _opts(self, memory=None, disk=None, expected_lifetime=None):
        import argparse
        return argparse.Namespace(memory=memory, disk=disk,
                                  expected_lifetime=expected_lifetime)

    def test_resources_of_subset(self):
        from utils.poms_entry import resources_of
        self.assertEqual(resources_of({'tarball': 't'}), {})
        self.assertEqual(
            resources_of({'memory': '4000MB', 'njobs': 5}),
            {'memory': '4000MB'})
        self.assertEqual(
            resources_of({'memory': '4000MB', 'disk': '50GB',
                          'expected_lifetime': '48h'}),
            {'memory': '4000MB', 'disk': '50GB', 'expected_lifetime': '48h'})

    def test_resources_of_nonstring_raises(self):
        from utils.poms_entry import resources_of
        with self.assertRaises(ValueError):
            resources_of({'memory': 4000})

    def test_effective_cli_beats_entry(self):
        from utils.submit import _effective_resources
        eff = _effective_resources({'memory': '4000MB'},
                                   self._opts(memory='8000MB'))
        self.assertEqual(eff['memory'], '8000MB')

    def test_effective_entry_beats_default(self):
        from utils.submit import _effective_resources
        eff = _effective_resources({'memory': '4000MB'}, self._opts())
        self.assertEqual(eff['memory'], '4000MB')
        self.assertIsNone(eff['disk'])            # None -> jobsub_argv builtin
        self.assertIsNone(eff['expected_lifetime'])

    def test_snapshot_merges_without_mutating(self):
        from utils.submit import _snapshot_entry
        entry = {'tarball': 't', 'njobs': 5}
        snap = _snapshot_entry(entry, {'memory': '8000MB', 'disk': None,
                                       'expected_lifetime': None})
        self.assertEqual(snap['memory'], '8000MB')
        self.assertNotIn('disk', snap)
        self.assertNotIn('memory', entry)         # original untouched

    def test_append_jobdef_passes_resource_keys(self):
        import tempfile
        from utils import json2jobdef
        out = os.path.join(tempfile.mkdtemp(), 'map.json')
        config = {'desc': 'TestDesc', 'dsconf': 'TestConf', 'owner': 'mu2e',
                  'inloc': 'tape', 'njobs': 5, 'memory': '4000MB',
                  'outloc': {'sim.mu2e.TestDesc.TestConf.art': 'tape'}}
        json2jobdef.append_jobdef(config, jobdefs_file=out)
        with open(out) as f:
            entry = json.load(f)[0]
        self.assertEqual(entry['memory'], '4000MB')
        self.assertNotIn('disk', entry)           # absent key stays absent
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestEntryResources -v`
Expected: ImportError/AttributeError on `resources_of`, `_effective_resources`, `_snapshot_entry`; the `append_jobdef` test fails with `KeyError: 'memory'`.

- [ ] **Step 3: Implement**

(a) `utils/poms_entry.py`, append at end of file:

```python
RESOURCE_KEYS = ('memory', 'disk', 'expected_lifetime')


def resources_of(entry: dict) -> dict:
    """Optional per-entry resource requests (subset of RESOURCE_KEYS
    actually present). Values are jobsub-format strings ('4000MB',
    '50GB', '48h'); anything else is a malformed map."""
    res = {}
    for key in RESOURCE_KEYS:
        if key in entry:
            if not isinstance(entry[key], str):
                raise ValueError(
                    f"POMS entry {key!r} must be a string "
                    f"(jobsub format), got {entry[key]!r}")
            res[key] = entry[key]
    return res
```

(b) `utils/submit.py`:

Extend the poms_entry import (~line 34) with `resources_of`:

```python
from utils.poms_entry import (tarball_of, outputs_of, njobs_of, inloc_of,
                              firstjob_of, validate_window, resources_of)
```

After `_record_in_ledger` (~line 222), add:

```python
def _effective_resources(entry, opts):
    """Resource precedence: CLI flag > entry key > None (None lets
    jobsub_argv apply its built-in defaults)."""
    res = resources_of(entry)
    return {
        'memory': opts.memory or res.get('memory'),
        'disk': opts.disk or res.get('disk'),
        'expected_lifetime': (opts.expected_lifetime
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
```

In `submit_entry_direct`, immediately before the `argv = _jobsub_argv.build_jobsub_argv(` call (~line 445), add:

```python
    resources = _effective_resources(entry, opts)
```

and in that call replace the three resource kwargs:

```python
        disk=resources['disk'],
        memory=resources['memory'],
        expected_lifetime=resources['expected_lifetime'],
```

At the ledger hook (~line 476-477), snapshot the entry:

```python
    result = _run_submit(cmd, tarball_name, len(jobset))
    if result['status'] == 'submitted' and not opts.no_ledger:
        _record_in_ledger(_snapshot_entry(entry, resources), firstjob,
                          jobset, result, opts)
    return result
```

(c) `utils/json2jobdef.py`, in `append_jobdef`, directly after the base `jobdef_entry = {...}` dict (~line 435), add:

```python
    # Optional per-entry resource requests pass through to the map entry;
    # the submit path reads them via poms_entry.resources_of
    # (CLI flag > entry key > built-in default).
    for key in ('memory', 'disk', 'expected_lifetime'):
        if key in config:
            jobdef_entry[key] = config[key]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestEntryResources -v`
Expected: 6 tests, OK.
Full suite: `python3 test/test_unit.py` → OK.

- [ ] **Step 5: Commit**

```bash
git add utils/poms_entry.py utils/submit.py utils/json2jobdef.py test/test_unit.py
git commit -m "feat: entry resource keys with snapshot inheritance

memory/disk/expected_lifetime as optional map-entry keys (CLI flag >
entry key > built-in default), merged into the ledger snapshot so
recoveries stop silently downgrading CLI resource overrides.
json2jobdef passes the keys through from the jobdef config."
```

---

### Task 3: `submit_map --enqueue` / `--slice-size`

**Files:**
- Modify: `utils/submit.py` (new `_enqueue_entries` after `_snapshot_entry`; wire into `submit_map` ~line 592; argparse + validation in `main` ~lines 696-711)
- Test: `test/test_unit.py` (new class after `TestEntryResources`)

**Interfaces:**
- Consumes: `submission_ledger.create_campaign` (Task 1), `_effective_resources`/`_snapshot_entry` (Task 2), existing `tarball_of`/`njobs_of`.
- Produces: `submit._enqueue_entries(entries_to_submit, map_path, opts) -> list[int]` where `entries_to_submit` is `[(idx, entry), ...]`; CLI flags `--enqueue`, `--slice-size` (default 1000).

- [ ] **Step 1: Write the failing tests**

```python
class TestEnqueue(unittest.TestCase):
    """submit_map --enqueue: campaign registration, no submission."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 10, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}

    def _opts(self, dry_run=False, slice_size=100, memory=None):
        import argparse
        return argparse.Namespace(
            ledger_db=self.db, slice_size=slice_size, dry_run=dry_run,
            memory=memory, disk=None, expected_lifetime=None)

    def test_enqueue_writes_campaign(self):
        from utils.submit import _enqueue_entries
        ids = _enqueue_entries([(0, self.entry)], '/tmp/m.json', self._opts())
        camps = self.sl.active_campaigns(self.db)
        self.assertEqual([c['id'] for c in camps], ids)
        c = camps[0]
        self.assertEqual(c['tarball'], self.entry['tarball'])
        self.assertEqual(c['slice_size'], 100)
        self.assertEqual(c['cursor'], 0)
        self.assertEqual(c['map_path'], '/tmp/m.json')
        self.assertEqual(c['entry'], self.entry)
        # nothing submitted: the submissions table stays empty
        self.assertEqual(self.sl.open_rows(self.db), [])

    def test_enqueue_merges_cli_resources_into_snapshot(self):
        from utils.submit import _enqueue_entries
        _enqueue_entries([(0, self.entry)], '/tmp/m.json',
                         self._opts(memory='4000MB'))
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['entry']['memory'], '4000MB')
        self.assertNotIn('memory', self.entry)     # original untouched

    def test_enqueue_dry_run_writes_nothing(self):
        from utils.submit import _enqueue_entries
        ids = _enqueue_entries([(0, self.entry)], '/tmp/m.json',
                               self._opts(dry_run=True))
        self.assertEqual(ids, [])
        self.assertEqual(self.sl.all_campaigns(self.db), [])

    def test_enqueue_duplicate_is_hard_error(self):
        from utils.submit import _enqueue_entries
        _enqueue_entries([(0, self.entry)], '/tmp/m.json', self._opts())
        with self.assertRaises(ValueError):
            _enqueue_entries([(0, self.entry)], '/tmp/m.json', self._opts())

    def test_enqueue_generic_entry_refused(self):
        from utils.submit import _enqueue_entries
        generic = {'tarball': 'cnf.mu2e.G.C.0.tar', 'inloc': 'tape',
                   'outputs': []}   # no njobs
        with self.assertRaises(SystemExit):
            _enqueue_entries([(0, generic)], '/tmp/m.json', self._opts())

    def test_enqueue_db_failure_is_hard_error(self):
        from utils.submit import _enqueue_entries
        import argparse, sqlite3
        opts = argparse.Namespace(
            ledger_db='/nonexistent-dir-enqueue-test/s.db', slice_size=10,
            dry_run=False, memory=None, disk=None, expected_lifetime=None)
        with self.assertRaises(sqlite3.OperationalError):
            _enqueue_entries([(0, self.entry)], '/tmp/m.json', opts)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestEnqueue -v`
Expected: `ImportError: cannot import name '_enqueue_entries'`.

- [ ] **Step 3: Implement**

(a) In `utils/submit.py`, after `_snapshot_entry`, add:

```python
def _enqueue_entries(entries_to_submit, map_path, opts):
    """Register entries as sliced-submission campaigns (cursor 0) —
    submits NOTHING; the recover cron feeds slices while the mu2epro
    queue is under its cap. A DB failure here is a hard error: nothing
    has been submitted yet, so fail loudly (unlike the post-submission
    ledger hook, which must never raise). Returns new campaign ids."""
    ids = []
    for idx, entry in entries_to_submit:
        if njobs_of(entry) is None:
            sys.exit(f"Error: entry {idx} has no njobs (generic tarball) — "
                     f"a campaign needs a job count to slice")
        snap = _snapshot_entry(entry, _effective_resources(entry, opts))
        if opts.dry_run:
            print(f"[DRY RUN] would enqueue entry {idx}: "
                  f"{tarball_of(entry)} njobs={njobs_of(entry)} "
                  f"slice={opts.slice_size}")
            continue
        camp_id = submission_ledger.create_campaign(
            opts.ledger_db, tarball=tarball_of(entry), entry=snap,
            slice_size=opts.slice_size, map_path=map_path)
        print(f"Enqueued campaign {camp_id}: {tarball_of(entry)} "
              f"njobs={njobs_of(entry)} slice={opts.slice_size} "
              f"(db {opts.ledger_db})")
        ids.append(camp_id)
    return ids
```

(b) In `submit_map()`, right after the `if not entries_to_submit:` block (~line 594), add:

```python
    if getattr(opts, 'enqueue', False):
        _enqueue_entries(entries_to_submit, map_path, opts)
        return []
```

(c) In `main()`, add to the argparser (after `--no-ledger`, ~line 677):

```python
    parser.add_argument('--enqueue', action='store_true',
                        help='[direct] Register entries as sliced-submission '
                             'campaigns in the ledger DB instead of '
                             'submitting; bin/recover then feeds slices '
                             'while total mu2epro idle+running is under '
                             'its cap.')
    parser.add_argument('--slice-size', type=int, default=1000,
                        help='[direct] Jobs per slice for --enqueue '
                             '(default 1000; frozen into the campaign).')
```

(d) In `main()`, after the `args.indices = _parse_indices(...)` try/except (~line 707), add:

```python
    if args.enqueue:
        if args.backend != 'direct':
            print("Error: --enqueue requires --backend direct")
            sys.exit(1)
        if (args.first is not None or args.num is not None
                or args.indices is not None):
            print("Error: --enqueue submits nothing — it cannot be "
                  "combined with --first/--num/--indices")
            sys.exit(1)
        if args.slice_size < 1:
            print(f"Error: --slice-size must be >= 1, got {args.slice_size}")
            sys.exit(1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestEnqueue -v`
Expected: 6 tests, OK.
Full suite: `python3 test/test_unit.py` → OK.

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "feat: submit_map --enqueue registers sliced campaigns

--enqueue snapshots entries (CLI resources merged) into the campaigns
table with --slice-size (default 1000) and submits nothing; direct
backend only, mutually exclusive with --first/--num/--indices; DB
failure at enqueue is a hard error."
```

---

### Task 4: Submission log (`submit-YYYYMMDD.log`)

**Files:**
- Modify: `utils/submit.py` (imports ~line 20; `_run_submit` result dicts ~lines 129-164; new `_submission_log_path`/`_log_submission` after `_record_in_ledger`; call site in `submit_entry_direct` ~line 475)
- Test: `test/test_unit.py` (new class after `TestEnqueue`)

**Interfaces:**
- Consumes: `_run_submit` result dict (gains key `raw_output`), `opts.ledger_db`, `opts.no_ledger`, `opts.map`.
- Produces: `submit._submission_log_path(ledger_db) -> str` (dated file beside the DB), `submit._log_submission(firstjob, jobset, result, opts) -> None` (never raises).

- [ ] **Step 1: Write the failing tests**

```python
class TestSubmissionLog(unittest.TestCase):
    """Dated per-submission log beside the ledger DB (all origins:
    manual runs, cron slices, recovery resubmits)."""

    def setUp(self):
        import tempfile
        self.dbdir = tempfile.mkdtemp()
        self.db = os.path.join(self.dbdir, 'submissions.db')

    def _opts(self, no_ledger=False):
        import argparse
        return argparse.Namespace(ledger_db=self.db, no_ledger=no_ledger,
                                  map='/tmp/m.json')

    def _result(self, status='submitted'):
        return {'tarball': 'cnf.mu2e.T.C.0.tar', 'cluster_id': '123',
                'jobsub_id': '123.0@js.fnal.gov', 'njobs': 3,
                'status': status,
                'raw_output': 'Use job id 123.0@js.fnal.gov ...\n'}

    def _read_log(self):
        from utils.submit import _submission_log_path
        with open(_submission_log_path(self.db)) as f:
            return f.read()

    def test_success_block_appended(self):
        from utils.submit import _log_submission
        _log_submission(100, [0, 1, 2], self._result(), self._opts())
        text = self._read_log()
        self.assertIn('status=submitted', text)
        self.assertIn('cnf.mu2e.T.C.0.tar', text)
        self.assertIn('[100..102]', text)          # absolute indices
        self.assertIn('Use job id 123.0@js.fnal.gov', text)

    def test_failure_block_appended(self):
        from utils.submit import _log_submission
        _log_submission(0, [0], self._result(status='failed'), self._opts())
        self.assertIn('status=failed', self._read_log())

    def test_appends_not_truncates(self):
        from utils.submit import _log_submission
        _log_submission(0, [0], self._result(), self._opts())
        _log_submission(0, [1], self._result(), self._opts())
        self.assertEqual(self._read_log().count('=== end'), 2)

    def test_write_failure_never_raises(self):
        from utils.submit import _log_submission
        import argparse
        opts = argparse.Namespace(
            ledger_db='/nonexistent-dir-submitlog-test/s.db',
            no_ledger=False, map='/tmp/m.json')
        _log_submission(0, [0], self._result(), opts)  # must not raise

    def test_run_submit_carries_raw_output(self):
        from utils import submit
        fake = MagicMock(
            returncode=0, stderr='warn\n',
            stdout='1 job(s) submitted to cluster 12345678.\n'
                   'Use job id 12345678.0@jobsub03.fnal.gov to retrieve output\n')
        with patch('utils.submit.subprocess.run', return_value=fake):
            r = submit._run_submit(['jobsub_submit'], 'cnf.tar', 3)
        self.assertIn('Use job id', r['raw_output'])
        self.assertIn('warn', r['raw_output'])

    def test_run_submit_failure_carries_raw_output(self):
        from utils import submit
        fake = MagicMock(returncode=1, stderr='boom\n', stdout='')
        with patch('utils.submit.subprocess.run', return_value=fake):
            r = submit._run_submit(['jobsub_submit'], 'cnf.tar', 3)
        self.assertEqual(r['status'], 'failed')
        self.assertIn('boom', r['raw_output'])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestSubmissionLog -v`
Expected: ImportError on `_log_submission` / `_submission_log_path`; the two `_run_submit` tests fail with `KeyError: 'raw_output'`.

- [ ] **Step 3: Implement**

(a) Add to the imports in `utils/submit.py` (~line 28):

```python
from datetime import datetime, timezone
```

(b) In `_run_submit`, capture the raw text once at the top (after the two echo prints):

```python
    raw_output = (result.stdout or '') + (result.stderr or '')
```

and add `'raw_output': raw_output,` to **all three** returned dicts (nonzero-exit failure, no-cluster-id failure, success).

(c) After `_record_in_ledger` (~line 222), add:

```python
def _submission_log_path(ledger_db):
    """Dated submission log beside the ledger DB (one file per UTC day,
    plain appends, no rotation — cleanup is manual)."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d')
    return os.path.join(os.path.dirname(ledger_db) or '.',
                        f'submit-{stamp}.log')


def _log_submission(firstjob, jobset, result, opts):
    """Append a human-readable record of a direct-backend submission
    attempt — success AND failure (failures are exactly what gets
    debugged). Covers every origin (manual, cron slice, recovery
    resubmit): they all pass through here. Never raises: the attempt
    already happened; a log problem must not crash the submit."""
    try:
        absolute = [firstjob + i for i in jobset]
        idx_line = (f"indices: {len(absolute)} absolute "
                    f"[{absolute[0]}..{absolute[-1]}]"
                    if absolute else "indices: none")
        block = '\n'.join([
            f"=== {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"user={getpass.getuser()} status={result['status']}",
            f"map={opts.map} tarball={result['tarball']}",
            idx_line,
            f"cluster={result['cluster_id']} "
            f"jobsub_id={result.get('jobsub_id')}",
            "--- jobsub output ---",
            result.get('raw_output', '').rstrip(),
            "=== end",
            "",
        ])
        with open(_submission_log_path(opts.ledger_db), 'a') as fh:
            fh.write(block + '\n')
    except Exception as e:
        print(f"WARNING: submit-log write failed ({e}) — submission "
              f"outcome unaffected (status={result['status']})")
```

(d) In `submit_entry_direct`, extend the post-submit hook (~line 475, as left by Task 2):

```python
    result = _run_submit(cmd, tarball_name, len(jobset))
    if not opts.no_ledger:
        _log_submission(firstjob, jobset, result, opts)
    if result['status'] == 'submitted' and not opts.no_ledger:
        _record_in_ledger(_snapshot_entry(entry, resources), firstjob,
                          jobset, result, opts)
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestSubmissionLog -v`
Expected: 6 tests, OK.
Full suite: `python3 test/test_unit.py` → OK.

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "feat: dated submission log beside the ledger DB

submit_map appends a per-attempt block (fields + raw jobsub output,
success AND failure) to submit-YYYYMMDD.log for every direct
submission — manual, cron slice, or recovery resubmit. Never raises
post-submission; --no-ledger skips it."
```

---

### Task 5: Top-up phase + cap + campaign management in `recover`

**Files:**
- Modify: `utils/recover.py` (imports ~line 29; constants ~line 37; new functions after `resubmit` ~line 128; `print_status` ~line 226; full `main()` rewrite ~lines 241-297)
- Test: `test/test_unit.py` (new classes after `TestSubmissionLog`)

**Interfaces:**
- Consumes: Task 1's campaign API (`active_campaigns`, `all_campaigns`, `advance_campaign`, `set_campaign_state`), `poms_entry.njobs_of`, existing `SUBMIT_MAP` path and lock pattern.
- Produces:
  - `DEFAULT_MAX_QUEUED = 10000`
  - `resolve_cap(flag_value) -> int` — flag > `MU2E_MAX_QUEUED` env > default
  - `total_queued(user='mu2epro', runner=subprocess.run) -> int | None`
  - `submit_slice(camp, n, db_path, runner=subprocess.run) -> bool`
  - `top_up(db_path, cap, dry_run=False, count_fn=total_queued, submit_fn=submit_slice) -> dict` (action-count summary)
  - `manage_campaign(db_path, camp_id, action) -> None` for `action in ('pause', 'resume', 'cancel')`
  - CLI: `--max-queued N`, `--pause-campaign ID`, `--resume-campaign ID`, `--cancel-campaign ID`

- [ ] **Step 1: Write the failing tests**

```python
class TestRecoverCap(unittest.TestCase):
    """Cap resolution + queue counting for the top-up phase."""

    def test_resolve_cap_flag_wins(self):
        from utils import recover
        with patch.dict(os.environ, {'MU2E_MAX_QUEUED': '5'}):
            self.assertEqual(recover.resolve_cap(42), 42)

    def test_resolve_cap_env_beats_default(self):
        from utils import recover
        with patch.dict(os.environ, {'MU2E_MAX_QUEUED': '5'}):
            self.assertEqual(recover.resolve_cap(None), 5)

    def test_resolve_cap_default(self):
        from utils import recover
        env = {k: v for k, v in os.environ.items() if k != 'MU2E_MAX_QUEUED'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(recover.resolve_cap(None),
                             recover.DEFAULT_MAX_QUEUED)

    def test_resolve_cap_bad_env_exits(self):
        from utils import recover
        with patch.dict(os.environ, {'MU2E_MAX_QUEUED': 'lots'}):
            with self.assertRaises(SystemExit):
                recover.resolve_cap(None)

    def _runner(self, stdout, rc=0):
        def run(cmd, capture_output=True, text=True):
            self.cmd = cmd
            return MagicMock(returncode=rc, stdout=stdout, stderr='')
        return run

    def test_total_queued_counts_idle_and_running_only(self):
        from utils.recover import total_queued
        n = total_queued(runner=self._runner('1\n2\n2\n5\n4\n'))
        self.assertEqual(n, 3)              # held (5) / removed (4) excluded
        self.assertEqual(self.cmd[:3], ['jobsub_q', '--user', 'mu2epro'])
        self.assertIn('JobStatus', self.cmd)

    def test_total_queued_empty_is_zero(self):
        from utils.recover import total_queued
        self.assertEqual(total_queued(runner=self._runner('')), 0)

    def test_total_queued_failure_is_none(self):
        from utils.recover import total_queued
        self.assertIsNone(total_queued(runner=self._runner('', rc=1)))

    def test_total_queued_garbage_is_none(self):
        from utils.recover import total_queued
        self.assertIsNone(total_queued(runner=self._runner('1\nERROR\n')))


class TestTopUp(unittest.TestCase):
    """Slice feeding: cap gate, whole slices, round-robin, pause."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.calls = []

    def _campaign(self, tarball='cnf.mu2e.A.C.0.tar', njobs=10, slice=4):
        entry = {'tarball': tarball, 'njobs': njobs, 'inloc': 'tape',
                 'outputs': []}
        return self.sl.create_campaign(self.db, tarball=tarball,
                                       entry=entry, slice_size=slice)

    def _submit(self, ok=True):
        def fn(camp, n, db_path):
            self.calls.append((camp['id'], camp['cursor'], n))
            return ok
        return fn

    def test_feeds_until_complete(self):
        from utils.recover import top_up
        cid = self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=100, count_fn=lambda: 0,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [(cid, 0, 4), (cid, 4, 4), (cid, 8, 2)])
        self.assertEqual(s['slice'], 3)
        self.assertEqual(s['campaign-complete'], 1)
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'],
                         'complete')

    def test_cap_stops_whole_slice(self):
        from utils.recover import top_up
        self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=100, count_fn=lambda: 97,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [])            # 97+4 > 100: wait
        self.assertEqual(s['cap-wait'], 1)
        self.assertEqual(self.sl.active_campaigns(self.db)[0]['cursor'], 0)

    def test_cap_exact_fit_submits(self):
        from utils.recover import top_up
        self._campaign(njobs=4, slice=4)
        top_up(self.db, cap=100, count_fn=lambda: 96,
               submit_fn=self._submit())
        self.assertEqual(len(self.calls), 1)        # 96+4 == 100 fits

    def test_submitted_slices_consume_headroom(self):
        from utils.recover import top_up
        self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=8, count_fn=lambda: 0,
                   submit_fn=self._submit())
        self.assertEqual(len(self.calls), 2)        # 0+4, 4+4; 8+2 > 8 waits
        self.assertEqual(s['cap-wait'], 1)

    def test_failure_pauses_without_advancing(self):
        from utils.recover import top_up
        cid = self._campaign()
        s = top_up(self.db, cap=100, count_fn=lambda: 0,
                   submit_fn=self._submit(ok=False))
        c = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(c['state'], 'paused')
        self.assertEqual(c['cursor'], 0)
        self.assertEqual(s['campaign-paused'], 1)

    def test_round_robin_two_campaigns(self):
        from utils.recover import top_up
        a = self._campaign(tarball='cnf.mu2e.A.C.0.tar', njobs=4, slice=2)
        b = self._campaign(tarball='cnf.mu2e.B.C.0.tar', njobs=2, slice=2)
        top_up(self.db, cap=100, count_fn=lambda: 0,
               submit_fn=self._submit())
        self.assertEqual(self.calls,
                         [(a, 0, 2), (b, 0, 2), (a, 2, 2)])

    def test_no_campaigns_skips_count(self):
        from utils.recover import top_up
        def boom():
            raise AssertionError("count_fn must not be called")
        self.assertEqual(top_up(self.db, cap=100, count_fn=boom), {})

    def test_count_failure_skips_topup(self):
        from utils.recover import top_up
        self._campaign()
        s = top_up(self.db, cap=100, count_fn=lambda: None,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [])
        self.assertEqual(s['count-error'], 1)

    def test_dry_run_reports_and_writes_nothing(self):
        from utils.recover import top_up
        def boom(camp, n, db_path):
            raise AssertionError("submit_fn must not be called in dry-run")
        self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=100, dry_run=True, count_fn=lambda: 0,
                   submit_fn=boom)
        self.assertEqual(s['would-slice'], 3)
        self.assertEqual(s['would-campaign-complete'], 1)
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['cursor'], 0)            # DB untouched
        self.assertEqual(c['state'], 'active')


class TestSubmitSlice(unittest.TestCase):
    """submit_slice shells out through the submit_map CLI."""

    def test_argv_and_map_content(self):
        import tempfile
        from utils import recover
        entry = {'tarball': 'cnf.mu2e.W.C.0.tar', 'njobs': 50,
                 'firstjob': 100, 'inloc': 'tape', 'outputs': [],
                 'memory': '4000MB'}
        camp = {'id': 7, 'cursor': 10, 'slice_size': 5, 'entry': entry,
                'tarball': entry['tarball']}
        captured = {}
        def runner(cmd, **kw):
            captured['cmd'] = cmd
            return MagicMock(returncode=0)
        ok = recover.submit_slice(camp, 5, '/tmp/led.db', runner=runner)
        self.assertTrue(ok)
        cmd = captured['cmd']
        self.assertIn('--backend', cmd)
        self.assertIn('direct', cmd)
        self.assertEqual(cmd[cmd.index('--first') + 1], '10')
        self.assertEqual(cmd[cmd.index('--num') + 1], '5')
        self.assertEqual(cmd[cmd.index('--ledger-db') + 1], '/tmp/led.db')
        with open(cmd[cmd.index('--map') + 1]) as f:
            written = json.load(f)
        self.assertEqual(written, [entry])          # firstjob PRESERVED

    def test_nonzero_exit_is_failure(self):
        from utils import recover
        camp = {'id': 1, 'cursor': 0, 'slice_size': 2, 'tarball': 't',
                'entry': {'tarball': 't', 'njobs': 2}}
        ok = recover.submit_slice(
            camp, 2, '/tmp/led.db',
            runner=lambda cmd, **kw: MagicMock(returncode=1))
        self.assertFalse(ok)


class TestManageCampaign(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.cid = sl.create_campaign(
            self.db, tarball='cnf.mu2e.M.C.0.tar',
            entry={'tarball': 'cnf.mu2e.M.C.0.tar', 'njobs': 5},
            slice_size=2)

    def test_pause_resume_cancel(self):
        from utils.recover import manage_campaign
        manage_campaign(self.db, self.cid, 'pause')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'], 'paused')
        manage_campaign(self.db, self.cid, 'resume')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'], 'active')
        manage_campaign(self.db, self.cid, 'cancel')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'],
                         'cancelled')

    def test_resume_active_raises(self):
        from utils.recover import manage_campaign
        with self.assertRaises(ValueError):
            manage_campaign(self.db, self.cid, 'resume')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest test.test_unit.TestRecoverCap test.test_unit.TestTopUp test.test_unit.TestSubmitSlice test.test_unit.TestManageCampaign -v`
Expected: AttributeError on `resolve_cap`, `total_queued`, `top_up`, `submit_slice`, `manage_campaign`.

- [ ] **Step 3: Implement**

(a) `utils/recover.py` imports: add `njobs_of` (~line 29):

```python
from utils.poms_entry import njobs_of
```

(b) After `DEFAULT_MAX_ATTEMPTS = 3` (~line 37), add:

```python
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
```

(c) After `resubmit` (~line 128), add:

```python
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
    tmpdir = tempfile.mkdtemp(prefix='campaign-')
    map_path = Path(tmpdir) / 'campaign-map.json'
    map_path.write_text(json.dumps([camp['entry']], indent=2) + '\n')
    cmd = [str(SUBMIT_MAP), '--map', str(map_path), '--backend', 'direct',
           '--first', str(camp['cursor']), '--num', str(n),
           '--ledger-db', str(db_path)]
    print(f"  campaign {camp['id']}: slice first={camp['cursor']} "
          f"num={n}: {' '.join(cmd)}")
    res = runner(cmd)
    return res.returncode == 0


def top_up(db_path, cap, dry_run=False, count_fn=total_queued,
           submit_fn=submit_slice):
    """Feed slices from active campaigns while total idle+running stays
    under the cap. Whole slices only (n = min(slice_size, remaining) is
    short only at end of entry — never clamped to headroom); cycles
    oldest-first, one slice per campaign per cycle; the first slice
    that would exceed the cap stops the tick. Submission failure
    pauses the campaign (no blind retry — deterministic payloads make
    an unverified resubmit the Run1Ban failure mode). Returns an
    action-count summary in the recovery pass's style."""
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
                continue
            n = min(camp['slice_size'], remaining)
            if count + n > cap:
                print(f"top-up: campaign {camp['id']}: {count}+{n} > {cap} "
                      f"— headroom < slice, waiting for next tick")
                bump('cap-wait')
                return summary
            if dry_run:
                print(f"campaign {camp['id']}: would submit slice "
                      f"first={camp['cursor']} num={n}")
                bump('would-slice')
            else:
                if not submit_fn(camp, n, db_path):
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'paused',
                        note='submit failed — check the submit log and '
                             'jobsub_q before --resume-campaign')
                    print(f"campaign {camp['id']}: submit FAILED — PAUSED "
                          f"(no blind retry; check the submit log and "
                          f"jobsub_q, then --resume-campaign)")
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


def manage_campaign(db_path, camp_id, action):
    """Operator switches. cancel closes the campaign only —
    already-submitted ledger rows still get recovered normally."""
    target = {'pause': 'paused', 'resume': 'active',
              'cancel': 'cancelled'}[action]
    submission_ledger.set_campaign_state(
        db_path, camp_id, target, note=f'operator {action}')
    print(f"campaign {camp_id}: {action} -> {target}")
```

(d) In `print_status`, append after the submissions table:

```python
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
```

(e) Replace `main()` entirely with:

```python
def main():
    p = argparse.ArgumentParser(
        description='Verify-and-resubmit recovery loop + sliced-campaign '
                    'top-up for direct-backend submissions (state written '
                    'by submit_map).')
    p.add_argument('--db', default=submission_ledger.DEFAULT_DB,
                   help=f'Submission-ledger sqlite DB (default: '
                        f'{submission_ledger.DEFAULT_DB}, env '
                        f'MU2E_SUBMISSION_DB)')
    p.add_argument('--status', action='store_true',
                   help='Print ledger + campaigns and exit (read-only)')
    p.add_argument('--dry-run', action='store_true',
                   help='Report would-* actions only; no submissions, no '
                        'state changes')
    p.add_argument('--row', type=int, default=None,
                   help='Process only this ledger row id (skips top-up)')
    p.add_argument('--max-attempts', type=int, default=DEFAULT_MAX_ATTEMPTS,
                   help=f'Attempt cap per chain (default '
                        f'{DEFAULT_MAX_ATTEMPTS}); at the cap the row is '
                        f'marked exhausted for a human')
    p.add_argument('--max-queued', type=int, default=None,
                   help=f'Total mu2epro idle+running cap for the top-up '
                        f'phase (default: MU2E_MAX_QUEUED env, then '
                        f'{DEFAULT_MAX_QUEUED})')
    p.add_argument('--pause-campaign', type=int, default=None, metavar='ID',
                   help='Pause an active campaign and exit')
    p.add_argument('--resume-campaign', type=int, default=None, metavar='ID',
                   help='Reactivate a paused campaign and exit')
    p.add_argument('--cancel-campaign', type=int, default=None, metavar='ID',
                   help='Cancel a campaign and exit (already-submitted '
                        'rows still get recovered)')
    args = p.parse_args()

    if args.status:
        print(f"queue cap in effect: {resolve_cap(args.max_queued)}")
        print_status(args.db)
        return

    mgmt = [(a, cid) for a, cid in
            (('pause', args.pause_campaign),
             ('resume', args.resume_campaign),
             ('cancel', args.cancel_campaign)) if cid is not None]
    if mgmt and args.dry_run:
        sys.exit("--pause/--resume/--cancel-campaign mutate the DB — "
                 "not valid with --dry-run")

    if not args.dry_run:
        # One mutating pass at a time per DB — guards manual runs racing
        # the cron (both passing the drain gate before either closes a
        # row = double submit). Read-only modes skip the lock. Held for
        # the process lifetime; released on exit.
        lock_path = os.path.join(os.path.dirname(args.db) or '.',
                                 'recover.lock')
        main._lock_fh = open(lock_path, 'w')
        try:
            fcntl.flock(main._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(f"another recover run holds {lock_path} — exiting")

    if mgmt:
        for action, cid in mgmt:
            manage_campaign(args.db, cid, action)
        return

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
        print("recover summary: "
              + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    if (summary.get('held') or summary.get('exhausted')
            or summary.get('would-exhaust') or summary.get('child-missing')
            or summary.get('campaign-paused')):
        sys.exit(2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest test.test_unit.TestRecoverCap test.test_unit.TestTopUp test.test_unit.TestSubmitSlice test.test_unit.TestManageCampaign -v`
Expected: 21 tests, OK.
Full suite: `python3 test/test_unit.py` → OK (`TestRecoverLoop`/`TestRecoverCLI` must still pass — the lock block and process_row loop semantics are unchanged; the "No active submissions" line still prints when there are no rows).

- [ ] **Step 5: Commit**

```bash
git add utils/recover.py test/test_unit.py
git commit -m "feat: sliced-campaign top-up phase in the recover loop

After the recovery pass, feed whole slices from active campaigns while
total mu2epro idle+running (jobsub_q --user -af JobStatus) stays under
--max-queued / MU2E_MAX_QUEUED / 10000. Round-robin oldest-first;
submit failure pauses the campaign (exit 2); --pause/--resume/
--cancel-campaign operator switches; --status shows campaigns + cap;
dry-run stays read-only."
```

---

### Task 6: Documentation — EXAMPLES regen, wiki, schema

**Files:**
- Modify: `docs/EXAMPLES_schema.md` (submit_map/recover flag coverage + tribal-knowledge bullets)
- Regenerate: `EXAMPLES.md` (full regen per the schema — never hand-patch)
- Modify: `wiki/pages/2026-07-18-direct-recovery-loop.md` (campaigns section + checklist item)
- Modify: `wiki/log.md` (one dated entry)

**Interfaces:**
- Consumes: the final CLIs from Tasks 3 and 5 — verify every documented flag against `argparse` in `utils/submit.py` and `utils/recover.py` before writing it.

- [ ] **Step 1: Update `docs/EXAMPLES_schema.md`**

In the section that covers `submit_map`, add coverage requirements for `--enqueue` and `--slice-size` (campaign registration, direct backend only). In the `recover` tool entry, add `--max-queued`, `--pause-campaign`, `--resume-campaign`, `--cancel-campaign`, and the cap line in `--status`. Add tribal-knowledge bullets (exact wording at the author's discretion, content fixed):

- Entry resource keys `memory` / `disk` / `expected_lifetime` (jobsub strings) — precedence CLI flag > entry key > built-in defaults (2000MB/30GB/24h); effective values freeze into ledger/campaign snapshots so recoveries and slices reproduce them; `json2jobdef` passes the keys through from the jobdef config.
- Sliced campaigns: `submit_map --enqueue` registers, the recover cron feeds whole slices while total mu2epro idle+running < cap (`--max-queued` > `MU2E_MAX_QUEUED` > 10000); submit failure pauses the campaign for a human.
- Every direct submission attempt (manual, slice, recovery) appends to `submit-YYYYMMDD.log` beside the ledger DB.

- [ ] **Step 2: Regenerate `EXAMPLES.md`**

Follow `docs/EXAMPLES_schema.md` exactly (the `/refresh-examples` contract): regenerate the affected tool sections from current `argparse` — do not diff-patch prose you did not regenerate. Spot-check: every flag you document must exist in `utils/submit.py` / `utils/recover.py` argparse (`grep` each one).

- [ ] **Step 3: Update the wiki**

`wiki/pages/2026-07-18-direct-recovery-loop.md`:
- Add a "Sliced campaigns (top-up phase)" section: enqueue workflow (`submit_map --map X --backend direct --enqueue [--slice-size N]`), top-up semantics (runs after the recovery pass, whole slices, round-robin, cap resolution flag > env > 10000), pause/resume/cancel, `paused` = submit failure or operator hold, `complete` = fully submitted (verification stays per ledger row), submission log location and the three-log-layer debugging story (ledger = structured truth, submit log = per-attempt record, recover log = loop decisions).
- Add to the pre-activation checklist: verify `jobsub_q --user mu2epro -af JobStatus` condor_q passthrough on the installed jobsub_lite (the queue count assumes it — same class as the existing per-jobid check).
- Note the resource-key inheritance fix (recoveries no longer downgrade CLI `--memory` to built-in defaults).

`wiki/log.md`: append one dated entry (2026-07-18) summarizing: sliced-campaign submission built on the recovery loop; spec + plan paths; not activated (same checklist gates the cron).

- [ ] **Step 4: Verify**

Run: `python3 test/test_unit.py`
Expected: OK.
Spot-check five documented commands against argparse with `grep` (per the schema's own rule).

- [ ] **Step 5: Commit**

```bash
git add docs/EXAMPLES_schema.md EXAMPLES.md wiki/pages/2026-07-18-direct-recovery-loop.md wiki/log.md
git commit -m "docs: sliced-campaign submission — EXAMPLES regen, wiki runbook"
```

---

## Execution notes

- Task order is strict: 1 → 2 → 3 → 4 → 5 → 6 (each consumes the previous task's interfaces).
- Tasks 3 and 5 both touch `utils/submit.py`/`utils/recover.py` state left by earlier tasks — the line numbers given are pre-task-1 anchors; locate by the quoted code, not the number.
- The `main()` rewrite in Task 5 must preserve: the lock semantics (mutating passes only, held for process lifetime, `main._lock_fh`), the existing exit-2 set plus `campaign-paused`, and the `--row` filter error. `TestRecoverCLI` guards most of this.
- No changes to `bin/recover_cron` — the top-up phase rides inside `recover`.
