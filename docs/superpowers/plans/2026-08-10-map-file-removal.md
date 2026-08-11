# Map-File Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the persistent intermediate submission-map file from the direct workflow, make a wrong campaign setting fixable after enqueue, delete the multi-entry `jobdesc` machinery, and retire the POMS-era operator surface.

**Architecture:** Four independent changes against `utils/` in a single Python package. Change 2 adds a whitelisted, validated key-setter to the sqlite ledger plus a CLI verb. Change 4 is delete-and-edit on docs. Change 1 extracts two pure seams (`build_jobdesc` in json2jobdef, `enqueue_entry` in submit) so `json2jobdef --enqueue` and `submit_map --enqueue` share one implementation with one set of preflight gates. Change 3 collapses the `jobdesc` list to a single object and renames its accessor module.

**Tech Stack:** Python 3.9 standard library only (`sqlite3`, `json`, `re`, `argparse`, `unittest`). No third-party packages, no wheel install.

**Spec:** `docs/superpowers/specs/2026-08-10-map-file-removal-design.md` (commits `996dc49`, `3019ba5`, `7a66ce7`).

## Global Constraints

- Test suite is `python3 -u test/test_unit.py`. Baseline **988 OK (skipped=1)**. Every task must end with the suite green and the count >= 988.
- The suite MUST keep running on plain `python3.9` with no wheel installed. No new third-party imports anywhere.
- **Do NOT `git push`.** The user pushes from their own shell.
- Work in a git worktree. There is no deploy step — `submissions run` and `submit_map` execute straight out of the working checkout, so a half-applied edit in `utils/` breaks the next hand-tick in the main checkout.
- `EXAMPLES.md` is a derived artifact. Never hand-edit it; edit `docs/EXAMPLES_schema.md` and regenerate with the `refresh-examples` skill.
- Task order is fixed: Tasks 1-2 (Change 2) first, they unblock campaign 54. Then Task 3 (Change 4). Then Tasks 4-6 (Change 1) and Tasks 7-8 (Change 3). Task 9 last.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
  ```

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `utils/submission_ledger.py` | sqlite ledger API. Gains `EDITABLE_ENTRY_KEYS`, `_validate_entry_value`, `set_campaign_entry_key`; `set_campaign_memory` becomes a delegating alias. | 1, 2 |
| `utils/submissions.py` | `submissions` verb CLI. Gains the `set-entry` subparser and handler. | 2 |
| `.claude/commands/poms-push.md` | Deleted. | 3 |
| `.claude/commands/mu2epro-submit.md`, `jit-cnf-build.md`, `mu2ejobsub-submit.md` | POMS-era text removed. | 3 |
| `templates/README.md` | POMS qualifier removed. | 3 |
| `utils/json2jobdef.py` | cnf build + config→entry projection. Gains `build_jobdesc`; `append_jobdef` delegates. Gains `--enqueue` / `--slice-size`. | 4, 6 |
| `utils/submit.py` | Submission. Gains public `enqueue_entry`; `_enqueue_entries` loops over it. Ships a single-object `jobdesc`. | 5, 7 |
| `utils/jobsub_argv.py` | ops JSON builder. `jobdesc` becomes a single object. | 7 |
| `utils/runmu2e.py` | Worker. Single-entry `validate_jobdesc` / `process_jobdef`. | 7 |
| `utils/prod_utils.py` | `resolve_map_index` takes one entry. | 7 |
| `utils/map_entry.py` → `utils/jobdesc.py` | Entry accessor layer. Renamed; dead POMS constants deleted. | 3, 8 |
| `docs/EXAMPLES_schema.md` | Generation spec for EXAMPLES.md. Edited twice (scrub, then additive). | 3, 9 |
| `test/test_unit.py` | The whole suite. | all |

---

## Task 1: `set_campaign_entry_key` — whitelisted, validated campaign edit

**Files:**
- Modify: `utils/submission_ledger.py:32` (regex), `:478-514` (`set_campaign_memory`)
- Test: `test/test_unit.py` — in the campaign test class whose `setUp` is at line 4860, beside the existing `test_set_memory_*` tests at 4951-4993

**Interfaces:**
- Produces:
  - `EDITABLE_ENTRY_KEYS = ('inloc', 'memory', 'disk', 'expected_lifetime')`
  - `set_campaign_entry_key(db_path, camp_id, key, value, include_open_rows=False) -> (previous, changed_row_ids)` — `previous` is the prior value or `None`; `changed_row_ids` is `[]` in this task (the cascade arrives in Task 2).
  - `set_campaign_memory(db_path, camp_id, memory) -> previous` — unchanged signature, now delegating.

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py` immediately after `test_set_memory_unknown_campaign_raises` (line 4993):

```python
    def test_set_entry_key_sets_inloc(self):
        cid = self._create()
        previous, rows = self.sl.set_campaign_entry_key(
            self.db, cid, 'inloc', 'resilient')
        self.assertEqual(previous, 'tape')
        self.assertEqual(rows, [])
        self.assertEqual(
            self.sl.active_campaigns(self.db)[0]['entry']['inloc'],
            'resilient')

    def test_set_entry_key_preserves_other_keys(self):
        cid = self._create()
        self.sl.set_campaign_entry_key(self.db, cid, 'inloc', 'resilient')
        entry = self.sl.active_campaigns(self.db)[0]['entry']
        self.assertEqual(entry['tarball'], self.entry['tarball'])
        self.assertEqual(entry['njobs'], self.entry['njobs'])
        self.assertEqual(entry['outputs'], self.entry['outputs'])

    def test_set_entry_key_refuses_non_whitelisted_key(self):
        """tarball/njobs/firstjob/input_pattern define the campaign's
        identity and index space — editing them in place corrupts a live
        campaign rather than fixing it."""
        cid = self._create()
        for bad in ('tarball', 'njobs', 'firstjob', 'input_pattern',
                    'outputs', 'nonsense'):
            with self.assertRaises(ValueError, msg=bad) as cm:
                self.sl.set_campaign_entry_key(self.db, cid, bad, 'x')
            self.assertIn('not editable', str(cm.exception))
        self.assertEqual(
            self.sl.active_campaigns(self.db)[0]['entry'], self.entry)

    def test_set_entry_key_validates_inloc(self):
        cid = self._create()
        for good in ('tape', 'disk', 'resilient', 'stash', 'none',
                     'dir:/pnfs/mu2e/persistent/x'):
            self.sl.set_campaign_entry_key(self.db, cid, 'inloc', good)
        for bad in ('Resilient', 'dir:relative/path', 'dir:', 'nfs', ''):
            with self.assertRaises(ValueError, msg=bad):
                self.sl.set_campaign_entry_key(self.db, cid, 'inloc', bad)
        # last good value survived every rejected write
        self.assertEqual(
            self.sl.active_campaigns(self.db)[0]['entry']['inloc'],
            'dir:/pnfs/mu2e/persistent/x')

    def test_set_entry_key_validates_lifetime(self):
        cid = self._create()
        for good in ('48h', '3600s', '30m', '2d'):
            self.sl.set_campaign_entry_key(
                self.db, cid, 'expected_lifetime', good)
        for bad in ('48', '48 h', '48hr', 'forever', ''):
            with self.assertRaises(ValueError, msg=bad):
                self.sl.set_campaign_entry_key(
                    self.db, cid, 'expected_lifetime', bad)

    def test_set_entry_key_validates_disk_like_memory(self):
        cid = self._create()
        self.sl.set_campaign_entry_key(self.db, cid, 'disk', '50GB')
        with self.assertRaises(ValueError):
            self.sl.set_campaign_entry_key(self.db, cid, 'disk', '50 GB')

    def test_set_entry_key_refused_on_closed_campaign(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'complete')
        with self.assertRaises(ValueError) as cm:
            self.sl.set_campaign_entry_key(
                self.db, cid, 'inloc', 'resilient')
        self.assertIn('complete', str(cm.exception))

    def test_set_entry_key_allowed_while_paused(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'paused')
        self.sl.set_campaign_entry_key(self.db, cid, 'inloc', 'resilient')
        self.assertEqual(
            self.sl.all_campaigns(self.db)[0]['entry']['inloc'], 'resilient')

    def test_set_entry_key_unknown_campaign_raises(self):
        with self.assertRaises(ValueError) as cm:
            self.sl.set_campaign_entry_key(self.db, 999, 'inloc', 'tape')
        self.assertIn('999', str(cm.exception))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: FAIL with `AttributeError: module 'utils.submission_ledger' has no attribute 'set_campaign_entry_key'`

- [ ] **Step 3: Rename the size regex and add the validators**

In `utils/submission_ledger.py`, replace line 32:

```python
_MEMORY_RE = re.compile(r'^\d+(MB|GB)$')
```

with:

```python
# Shared by the memory and disk keys — both take a jobsub size string.
_SIZE_RE = re.compile(r'^\d+(MB|GB)$')
_LIFETIME_RE = re.compile(r'^\d+[smhd]$')

# Entry keys `submissions set-entry` may edit on a live campaign.
# Deliberately excludes tarball/njobs/firstjob/input_pattern: those
# define the campaign's identity and index space, so changing one in
# place corrupts a live campaign instead of fixing it. The correct
# operation there is cancel + re-enqueue.
EDITABLE_ENTRY_KEYS = ('inloc', 'memory', 'disk', 'expected_lifetime')

# inloc forms utils/file_resolver.py actually accepts.
_INLOC_SIMPLE = ('tape', 'disk', 'resilient', 'stash', 'none')


def _validate_entry_value(key, value):
    """Reject a malformed value at the boundary. Written here rather
    than at submit time because an unparseable value would otherwise sit
    in the ledger looking applied and only surface a tick later — as a
    jobsub_submit rejection for the resource keys, or, worse, as a
    SILENT SAM fallback for a misspelled inloc."""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string, got {value!r}")
    if key in ('memory', 'disk'):
        if not _SIZE_RE.match(value):
            raise ValueError(
                f"{key} must look like '3000MB' or '4GB', got {value!r}")
    elif key == 'expected_lifetime':
        if not _LIFETIME_RE.match(value):
            raise ValueError(
                f"expected_lifetime must look like '48h' or '3600s', "
                f"got {value!r}")
    elif key == 'inloc':
        if value not in _INLOC_SIMPLE and not value.startswith('dir:/'):
            raise ValueError(
                f"inloc must be one of {', '.join(_INLOC_SIMPLE)} or "
                f"'dir:/<absolute path>', got {value!r}")
```

- [ ] **Step 4: Replace `set_campaign_memory` with the general setter plus an alias**

Replace `utils/submission_ledger.py:478-514` in full with:

```python
def set_campaign_entry_key(db_path, camp_id, key, value,
                           include_open_rows=False):
    """Set one whitelisted key on a live campaign's entry snapshot;
    return (previous_value, changed_row_ids).

    Same live-retune contract as set_campaign_slice: active/paused only,
    binds from the next tick.

    By default this edits the CAMPAIGN's snapshot only, so it reaches
    future slices and nothing else — rows already dispatched keep the
    entry they were submitted with. That default is deliberate, and it
    is what `memory` depends on: an UNSET memory is exactly what earns a
    recovery the 4000MB floor (submissions.recovery_resource_argv), so
    cascading a memory value would silently forfeit the better failure
    mode.
    """
    if key not in EDITABLE_ENTRY_KEYS:
        raise ValueError(
            f"{key!r} is not editable; choose one of "
            f"{', '.join(EDITABLE_ENTRY_KEYS)}. Identity and index-space "
            f"keys (tarball, njobs, firstjob, input_pattern) define the "
            f"campaign — cancel and re-enqueue instead")
    _validate_entry_value(key, value)
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT state, tarball, entry_json FROM campaigns WHERE id = ?',
            (camp_id,)).fetchone()
        if row is None:
            raise ValueError(f"no campaign {camp_id}")
        if row['state'] not in ('active', 'paused'):
            raise ValueError(
                f"campaign {camp_id} is {row['state']} — {key} only "
                f"applies to an active or paused campaign")
        entry = json.loads(row['entry_json'])
        previous = entry.get(key)
        entry[key] = value
        con.execute('UPDATE campaigns SET entry_json = ? WHERE id = ?',
                    (json.dumps(entry), camp_id))
        con.commit()
        return previous, []
    finally:
        con.close()


def set_campaign_memory(db_path, camp_id, memory):
    """Back-compat alias for the 'memory' key; returns the previous
    value. Never cascades to rows — see set_campaign_entry_key for why
    that default is what protects the recovery floor."""
    previous, _ = set_campaign_entry_key(db_path, camp_id, 'memory', memory)
    return previous
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count >= 997. The seven pre-existing `test_set_memory_*` tests must still pass unchanged — they exercise the alias.

- [ ] **Step 6: Commit**

```bash
git add utils/submission_ledger.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(ledger): whitelisted set_campaign_entry_key with boundary validation

set-memory was the only way to change a live campaign's entry, so the
PhysicalPionStops inloc fix (disk -> resilient) had no verb. Generalize
it to a keyed setter over EDITABLE_ENTRY_KEYS = inloc, memory, disk,
expected_lifetime.

The whitelist is the point: tarball/njobs/firstjob/input_pattern define
the campaign's identity and index space, so editing one in place
corrupts a live campaign rather than fixing it.

Values validate at the boundary for the reason the old _MEMORY_RE check
gave — a bad value would otherwise sit in the ledger looking applied.
For inloc the stakes are higher than a jobsub rejection: a misspelled
value falls back to SAM silently.

set_campaign_memory keeps its signature and delegates.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 2: `--include-open-rows` cascade and the `set-entry` verb

**Files:**
- Modify: `utils/submission_ledger.py` — `set_campaign_entry_key` body
- Modify: `utils/submissions.py:1373` (subparser, beside `set-memory`), `:1491` (handler, beside the `set-memory` handler)
- Test: `test/test_unit.py` — beside the Task 1 tests

**Interfaces:**
- Consumes: `set_campaign_entry_key(db_path, camp_id, key, value, include_open_rows=False) -> (previous, changed_row_ids)`, `EDITABLE_ENTRY_KEYS` (Task 1)
- Produces: CLI `submissions set-entry <CAMPAIGN_ID> <key> <value> [--include-open-rows]`

**Why the cascade exists:** `submissions.resubmit` (`utils/submissions.py:610`) rebuilds its map from `row['entry']` — the row's own frozen snapshot — so a campaign-only edit never reaches a recovery. Campaign 54 is fully dispatched and has no future slices, so without this flag the edit would be a no-op there.

**Why rows are matched by tarball:** the two ledger tables carry no `campaign_id` column. The partial unique index `campaigns_live_tarball` guarantees at most one live campaign per tarball, so the match is unambiguous for a live campaign — but a cancelled predecessor could have left an open row behind, which is why the changed ids are returned and printed rather than a bare count.

**Why `closed_utc IS NULL` and not `state = 'active'`:** `open_rows` is active-only and misses `submitting` rows (reserved-but-not-yet-attached). Those rows will still be recovered, so they must receive the new value too.

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py` after `test_set_entry_key_unknown_campaign_raises`:

```python
    def _row(self, state='active', entry=None):
        """One submissions row on this campaign's tarball."""
        rid = self.sl.record_submission(
            self.db, tarball=self.entry['tarball'],
            entry=entry or dict(self.entry), indices=[0, 1],
            jobsub_id='1.0@sched', cluster_id='1')
        if state != 'active':
            self.sl.close_row(self.db, rid, state)
        return rid

    def test_cascade_off_by_default_protects_recovery_floor(self):
        """An UNSET memory is what earns a recovery the 4000MB floor, so
        the default must not push a value into dispatched rows."""
        cid = self._create()
        rid = self._row()
        self.sl.set_campaign_entry_key(self.db, cid, 'memory', '3000MB')
        row = [r for r in self.sl.all_rows(self.db) if r['id'] == rid][0]
        self.assertNotIn('memory', row['entry'])

    def test_cascade_updates_open_rows_and_reports_ids(self):
        cid = self._create()
        rid = self._row()
        previous, changed = self.sl.set_campaign_entry_key(
            self.db, cid, 'inloc', 'resilient', include_open_rows=True)
        self.assertEqual(previous, 'tape')
        self.assertEqual(changed, [rid])
        row = [r for r in self.sl.all_rows(self.db) if r['id'] == rid][0]
        self.assertEqual(row['entry']['inloc'], 'resilient')

    def test_cascade_skips_closed_rows(self):
        cid = self._create()
        closed = self._row(state='complete')
        open_id = self._row()
        _, changed = self.sl.set_campaign_entry_key(
            self.db, cid, 'inloc', 'resilient', include_open_rows=True)
        self.assertEqual(changed, [open_id])
        by_id = {r['id']: r for r in self.sl.all_rows(self.db)}
        self.assertEqual(by_id[closed]['entry']['inloc'], 'tape')
        self.assertEqual(by_id[open_id]['entry']['inloc'], 'resilient')

    def test_cascade_preserves_row_specific_keys(self):
        """A recovery child's snapshot may differ from the campaign's
        (e.g. firstjob dropped); the cascade must touch only its key."""
        cid = self._create()
        child = dict(self.entry)
        child['memory'] = '4000MB'
        rid = self._row(entry=child)
        self.sl.set_campaign_entry_key(
            self.db, cid, 'inloc', 'resilient', include_open_rows=True)
        row = [r for r in self.sl.all_rows(self.db) if r['id'] == rid][0]
        self.assertEqual(row['entry']['memory'], '4000MB')
        self.assertEqual(row['entry']['inloc'], 'resilient')

    def test_cascade_leaves_other_tarballs_alone(self):
        cid = self._create()
        other = self.sl.record_submission(
            self.db, tarball='cnf.mu2e.Other.TestConf.0.tar',
            entry={'tarball': 'cnf.mu2e.Other.TestConf.0.tar',
                   'njobs': 3, 'inloc': 'tape', 'outputs': []},
            indices=[0], jobsub_id='2.0@sched', cluster_id='2')
        _, changed = self.sl.set_campaign_entry_key(
            self.db, cid, 'inloc', 'resilient', include_open_rows=True)
        self.assertNotIn(other, changed)
        row = [r for r in self.sl.all_rows(self.db) if r['id'] == other][0]
        self.assertEqual(row['entry']['inloc'], 'tape')
```

`close_row(db_path, row_id, state, note=None)` is at `utils/submission_ledger.py:349`. `record_submission` is keyword-only and is at `:170`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: FAIL — `test_cascade_updates_open_rows_and_reports_ids` gets `changed == []`.

- [ ] **Step 3: Add the cascade to the ledger function**

In `set_campaign_entry_key`, extend the docstring and replace the commit block. After the `UPDATE campaigns` statement and before `con.commit()`:

```python
        changed = []
        if include_open_rows:
            open_ = con.execute(
                'SELECT id, entry_json FROM submissions '
                'WHERE tarball = ? AND closed_utc IS NULL ORDER BY id',
                (row['tarball'],)).fetchall()
            for r in open_:
                r_entry = json.loads(r['entry_json'])
                r_entry[key] = value
                con.execute(
                    'UPDATE submissions SET entry_json = ? WHERE id = ?',
                    (json.dumps(r_entry), r['id']))
                changed.append(r['id'])
        con.commit()
        return previous, changed
```

Append to the docstring, after the `memory` paragraph:

```
    include_open_rows=True additionally rewrites the entry snapshot of
    every not-yet-closed row on this campaign's tarball, which is what
    makes RECOVERIES pick the change up (submissions.resubmit rebuilds
    its map from row['entry'], not from the campaign). Rows match by
    tarball because the two tables carry no campaign_id; the partial
    unique index campaigns_live_tarball keeps that unambiguous for a
    live campaign, but a cancelled predecessor could have left an open
    row behind — so the changed ids are RETURNED, not just counted.

    `closed_utc IS NULL` rather than state='active' on purpose: a
    'submitting' row (reserved, cluster not yet attached) is still going
    to be recovered, so it needs the new value too.
```

- [ ] **Step 4: Add the CLI subparser**

In `utils/submissions.py`, immediately after the `set-memory` subparser block (ends at `mem_p.add_argument('memory', help="e.g. 3000MB")`, line 1379):

```python
    entry_p = sub.add_parser(
        'set-entry',
        help='Set one key on a live campaign\'s entry (takes effect on '
             'the next tick)')
    entry_p.add_argument('camp_id', type=int)
    entry_p.add_argument('key',
                         choices=submission_ledger.EDITABLE_ENTRY_KEYS)
    entry_p.add_argument('value', help='e.g. resilient, 3000MB, 48h')
    entry_p.add_argument(
        '--include-open-rows', action='store_true',
        help='Also rewrite not-yet-closed rows on this campaign\'s '
             'tarball, so their RECOVERIES use the new value. Off by '
             'default because an unset memory is what earns a recovery '
             f'the {RECOVERY_MEMORY} floor.')
```

- [ ] **Step 5: Add the CLI handler**

In `utils/submissions.py`, immediately after the `if verb == 'set-memory':` block (which ends with `return`):

```python
    if verb == 'set-entry':
        _acquire_lock(db)
        try:
            old, rows = submission_ledger.set_campaign_entry_key(
                db, args.camp_id, args.key, args.value,
                include_open_rows=args.include_open_rows)
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        print(f"campaign {args.camp_id}: {args.key} {old or 'unset'} -> "
              f"{args.value} (applies from the next tick)")
        if args.include_open_rows:
            print(f"  rows updated: "
                  f"{', '.join(str(r) for r in rows) if rows else 'none'}")
        else:
            print("  rows already submitted keep their own entry; pass "
                  "--include-open-rows to reach their recoveries")
        return
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count >= 1002.

- [ ] **Step 7: Verify the CLI surface by hand**

Run: `python3 -u utils/submissions.py set-entry --help`
Expected: usage line showing `camp_id`, `{inloc,memory,disk,expected_lifetime}`, `value`, and `--include-open-rows`.

Run: `python3 -u utils/submissions.py set-entry 1 tarball x 2>&1 | tail -2`
Expected: argparse rejects `tarball` as an invalid choice (exit 2). This proves the whitelist is enforced at the CLI as well as in the ledger.

- [ ] **Step 8: Commit**

```bash
git add utils/submission_ledger.py utils/submissions.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(submissions): set-entry verb with opt-in row cascade

A campaign-only edit reaches future slices and nothing else, because
resubmit() rebuilds its map from the ROW's frozen snapshot. Campaign 54
is fully dispatched, so without a cascade the inloc fix would be a
silent no-op there.

--include-open-rows rewrites every not-yet-closed row on the campaign's
tarball. It is off by default: an unset memory is what earns a recovery
the 4000MB floor, so cascading memory would forfeit the better failure
mode. inloc has no such floor and normally wants the flag on.

Rows match by tarball (the tables carry no campaign_id) and by
closed_utc IS NULL rather than state='active', so reserved 'submitting'
rows are covered too. The changed ids are printed, not counted, because
a cancelled predecessor on the same tarball could have left a row open.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 3: Retire the POMS-era operator surface (Change 4)

**Files:**
- Delete: `.claude/commands/poms-push.md`
- Modify: `utils/map_entry.py:31-37`, `.claude/commands/mu2epro-submit.md:48-59`, `.claude/commands/jit-cnf-build.md:222-232`, `.claude/commands/mu2ejobsub-submit.md:2,17-18`, `templates/README.md:3-5`, `docs/EXAMPLES_schema.md`
- Regenerate: `EXAMPLES.md`

**Interfaces:** none — this task changes no code paths.

- [ ] **Step 1: Delete the dead skill and constants**

```bash
git rm .claude/commands/poms-push.md
```

`/poms-push` plans a production POMS push: pick the map number, extend `poms_map/MDC2025-NNN.json` in place versus allocating a new one, keep the running total under 100k jobs. Every step describes machinery that no longer exists, and it cites `feedback_extend_existing_poms_map.md` — a memory that does not exist. There is no direct-backend equivalent to port it to: Change 1 removes the map-number problem entirely.

In `utils/map_entry.py`, delete lines 31-37 in full:

```python
# Documents the external map convention; intentionally unreferenced.
# The production map files live at this path and match this basename
# pattern by convention (mu2epro area), but nothing in-repo reads these
# constants — their former consumers (DB builder, staleness check,
# dashboards) were removed with the POMS backend.
DEFAULT_POMS_DIR = "/exp/mu2e/app/users/mu2epro/production_manager/poms_map"
POMS_MAP_PATTERN = "MDC202*"
```

- [ ] **Step 2: Verify nothing imported them**

Run: `grep -rn "DEFAULT_POMS_DIR\|POMS_MAP_PATTERN" . --include=*.py`
Expected: no output.

- [ ] **Step 3: Fix `.claude/commands/mu2epro-submit.md`**

Replace lines 48-59 (the `<map.json>` bullet through the `poms_map/` caveat paragraph) with:

```markdown
- `<map.json>` — absolute path to the submission map: a throwaway
  `/tmp` map, one per campaign, e.g. `/tmp/map_noprimary_au.json`.
  Pass the SAME path that was given to `json2jobdef --prod --jobdefs`.

  Do not create map files under
  `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/` or
  `direct_maps/`. Both are historical; the direct workflow neither reads
  nor wants a persistent file there.
```

- [ ] **Step 4: Fix `.claude/commands/jit-cnf-build.md`**

Replace lines 227 and 229-232. Delete this bullet entirely:

```markdown
  - A POMS campaign if you've added a corresponding POMS-map entry.
```

and change the following bullet's tail from:

```markdown
    JIT-cnfs must go via the upstream `/mu2ejobsub-submit` skill or a
    POMS campaign until that scope cut is lifted.
```

to:

```markdown
    JIT-cnfs must go via the upstream `/mu2ejobsub-submit` skill until
    that scope cut is lifted.
```

- [ ] **Step 5: Fix `.claude/commands/mu2ejobsub-submit.md` (description only)**

This skill STAYS — it wraps the upstream mu2egrid `mu2ejobsub` CLI, which still exists and is still the right tool for smoke tests and one-offs. Only its prose mentions a dead workflow.

Line 2, change `outside the POMS-map / submit_map flow` to `outside the submit_map flow`.

Lines 17-18, change `where the full POMS-map → \`submit_map\` workflow is overkill` to `where the full \`submit_map\` workflow is overkill`.

- [ ] **Step 6: Fix `templates/README.md`**

Line 4: change `synthesize a \`json2jobdef\` config for one POMS-free chain hop` to `synthesize a \`json2jobdef\` config for one chain hop`. Every hop is POMS-free now; the qualifier no longer distinguishes anything.

- [ ] **Step 7: Add the schema scrub bullet**

In `docs/EXAMPLES_schema.md`, add to the tribal-knowledge bullet list (the one containing the `pre-poms-removal` notes around lines 178-191):

```markdown
- The `json2jobdef --prod --jobdefs` example must use a throwaway `/tmp`
  map path. Never show
  `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-NNN.json`
  — POMS is retired, and that path teaches a map-numbering convention
  that no longer has a consumer.
```

- [ ] **Step 8: Regenerate EXAMPLES.md**

Invoke the `refresh-examples` skill with the argument:
`focus on the POMS scrub: the json2jobdef --prod --jobdefs example must use a /tmp map path, not poms_map/MDC2025-NNN.json`

Then verify: `grep -n "poms_map" EXAMPLES.md`
Expected: no output.

- [ ] **Step 9: Verify the sweep and run the suite**

Run:
```bash
grep -rn -i "poms" utils/ bin/ mcp/ data/ templates/ .claude/ EXAMPLES.md
```
Expected: ONLY these, all of which are deliberately kept because each explains why current behaviour is what it is —
- `utils/runmu2e.py:39`, `:240-241`, `:751-755` (streaming is the default *because* the POMS launch template never passed `--copy-input`; delete these and the default loses its rationale)
- `utils/runmu2e.py:772-773` (the git-tag pointer, which fires only when someone runs `runmu2e` by hand)
- `utils/map_entry.py:3-5`, `:35` docstring text — retired in Task 8's rename
- `utils/submission_ledger.py:5`, `utils/file_resolver.py:132`, `utils/submissions.py:424`, `:487`, `utils/job_common.py:197`, `utils/submit.py:14`
- `EXAMPLES.md` line noting template/g4bl runner modes were deleted with the POMS backend

`wiki/` is a deliberate historical record and is NOT swept.

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count unchanged from Task 2. The four `test_unit.py` POMS mentions are docstrings and stay.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore(poms): retire the POMS-era operator surface

A sweep found live operator-facing surface, not just stale comments.
/poms-push is an invocable skill that walks through picking a POMS map
number and extending poms_map/MDC2025-NNN.json in place — all of it gone
— while citing feedback_extend_existing_poms_map.md, a memory that does
not exist. Deleted; Change 1 removes the map-number problem entirely.

Also: the two dead constants in map_entry.py (self-documented as
"intentionally unreferenced"), the "POMS-driven" map home in
/mu2epro-submit, the POMS-campaign outcome in /jit-cnf-build, the now
vacuous "POMS-free" qualifier in templates/README, and the poms_map/
path in the canonical --jobdefs example.

/mu2ejobsub-submit STAYS — it wraps the upstream mu2egrid CLI, which
still exists. Only its description is trimmed.

Kept deliberately: the past-tense why-comments. runmu2e's streaming
default exists BECAUSE the POMS launch template never passed
--copy-input; deleting that strands the default without a rationale.
Same for the wiki history and runmu2e.py:772's git-tag pointer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 4: Extract `build_jobdesc` from `append_jobdef`

**Files:**
- Modify: `utils/json2jobdef.py:423-508`
- Test: `test/test_unit.py:5083` and `:5113` (existing `test_append_jobdef_passes_*`)

**Interfaces:**
- Produces: `build_jobdesc(config) -> dict` — the pure config→entry projection. Raises `ValueError` when `config['outloc']` is not a dict.
- `append_jobdef(config, jobdefs_file=None)` keeps its signature and behaviour exactly.

**On the name:** `jobdesc` is what this dict is already called on the far side of the wire — `ops["jobdesc"]`, `validate_jobdesc`, and the `jobdesc` parameter in `runmu2e.py`. Today the same object carries two names depending on which side it sits on, inherited from POMS owning the submit side. The builder takes the consumer's name.

**On the `outloc` wart:** `append_jobdef` currently prints `Warning: outloc must be a dictionary...` and returns without writing anything. Extracting that into a pure function needs a return value, so `build_jobdesc` raises `ValueError` and `append_jobdef` catches it and reproduces the existing warning verbatim. Behaviour is preserved exactly in this task. Task 6's `--enqueue` path deliberately does NOT catch, because a silently-skipped entry under `--prod` means the cnf was pushed with no campaign created — a half-done production push that looks successful.

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py`, immediately before `test_append_jobdef_passes_resource_keys` (line 5083):

```python
    def test_build_jobdesc_projects_core_keys(self):
        from utils.json2jobdef import build_jobdesc
        config = {'desc': 'D', 'dsconf': 'C', 'owner': 'mu2e',
                  'inloc': 'tape', 'njobs': 7,
                  'outloc': {'*.art': 'tape'},
                  'simjob_setup': '/cvmfs/x/setup.sh'}
        with patch('utils.json2jobdef.get_parfile_name',
                   return_value='cnf.mu2e.D.C.0.tar'):
            entry = build_jobdesc(config)
        self.assertEqual(entry['tarball'], 'cnf.mu2e.D.C.0.tar')
        self.assertEqual(entry['inloc'], 'tape')
        self.assertEqual(entry['njobs'], 7)
        self.assertEqual(entry['outputs'],
                         [{'dataset': '*.art', 'location': 'tape'}])

    def test_build_jobdesc_omits_njobs_for_generic(self):
        """Absence of njobs is what makes runmu2e pick direct-input
        mode, so a generic tarball must not carry one."""
        from utils.json2jobdef import build_jobdesc
        config = {'desc': 'D', 'dsconf': 'C', 'owner': 'mu2e',
                  'inloc': 'tape', 'njobs': 7, 'generic_tarball': True,
                  'outloc': {'*.art': 'tape'},
                  'simjob_setup': '/cvmfs/x/setup.sh'}
        with patch('utils.json2jobdef.get_parfile_name',
                   return_value='cnf.mu2e.D.C.0.tar'):
            entry = build_jobdesc(config)
        self.assertNotIn('njobs', entry)

    def test_build_jobdesc_rejects_non_dict_outloc(self):
        from utils.json2jobdef import build_jobdesc
        config = {'desc': 'D', 'dsconf': 'C', 'owner': 'mu2e',
                  'inloc': 'tape', 'njobs': 7,
                  'outloc': [{'*.art': 'tape'}],
                  'simjob_setup': '/cvmfs/x/setup.sh'}
        with patch('utils.json2jobdef.get_parfile_name',
                   return_value='cnf.mu2e.D.C.0.tar'):
            with self.assertRaises(ValueError):
                build_jobdesc(config)
```

`patch` is already imported at `test/test_unit.py:28` (`from unittest.mock import MagicMock, patch`). There is no bare `mock` name in this suite — use `patch`, not `mock.patch`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: FAIL with `ImportError: cannot import name 'build_jobdesc'`.

- [ ] **Step 3: Extract the projection**

In `utils/json2jobdef.py`, replace the whole of `append_jobdef` (lines 423-508, ending with the `_write_jobdef_json_entry(jobdef_entry, jobdefs_file)` call) with:

```python
def build_jobdesc(config):
    """Project a build config onto the submission entry (the `jobdesc`).

    Pure: no filesystem writes. The one impure part is the `njobs: -1`
    branch, which asks the freshly-built cnf for its job count.

    Raises ValueError if `outloc` is not a dict — see append_jobdef for
    why that stays a warning on the file-writing path and is fatal on
    the enqueue path.
    """
    parfile_name = get_parfile_name(config)
    is_generic = config.get('generic_tarball', False)

    jobdef_entry = {
        "tarball": parfile_name,
        "inloc": config['inloc'],
        "outputs": []
    }

    # Optional per-entry resource requests pass through to the entry;
    # the submit path reads them via map_entry.resources_of
    # (CLI flag > entry key > built-in default).
    for key in ('memory', 'disk', 'expected_lifetime'):
        if key in config:
            jobdef_entry[key] = config[key]

    # Draining configuration passes through too, so a draining map comes
    # out of --jobdefs ready to enqueue instead of needing a hand-edit:
    # the submit path reads `input_pattern` (map_entry.is_draining, the
    # kind discriminator) and `prestage` (submit._validate_draining_entry,
    # and the tape-residency gate in submissions.drain_tick) off the
    # ENTRY, so a value left behind in the JSON config would silently do
    # nothing.
    for key in ('input_pattern', 'prestage'):
        if key in config:
            jobdef_entry[key] = config[key]

    # A draining entry is defined by having an input_pattern and NO index
    # space. Emitting both would leave the entry self-contradictory --
    # is_draining() would say draining while njobs claimed a fixed window
    # -- so refuse rather than write it.
    if 'input_pattern' in config and not is_generic:
        fail("Error: input_pattern requires generic_tarball: true "
             "(a draining entry has no fixed job count)")

    # Optional cnf-index window start (statistics expansion; semantics
    # in utils/map_entry.py). firstjob_of/validate_window are the single
    # validation authority — shared with the submit path.
    try:
        firstjob = firstjob_of(config)
    except ValueError as e:
        fail(f"Error: {e}")
    if firstjob and is_generic:
        fail("Error: firstjob requires a fixed job count (njobs); "
             "generic tarball entries have no index window")

    # Generic tarballs have no pre-determined job count — omit njobs so
    # runmu2e detects direct-input mode (absence of njobs is the trigger)
    if not is_generic:
        njobs = config['njobs']
        jp = None
        if njobs == -1:
            jp = Mu2eJobPars(parfile_name)
            njobs = jp.njobs()
            print(f"Queried job count: {njobs}")
        jobdef_entry["njobs"] = njobs
        if firstjob:
            capacity = (jp or Mu2eJobPars(parfile_name)).njobs()
            try:
                validate_window(firstjob, njobs, capacity)
            except ValueError as e:
                fail(f"Error: {e} for {parfile_name}")
            jobdef_entry["firstjob"] = firstjob
            print(f"Windowed entry: cnf indices {firstjob}..{firstjob + njobs - 1}")

    outloc = config['outloc']
    if not isinstance(outloc, dict):
        raise ValueError(
            f"outloc must be a dictionary with dataset-specific "
            f"locations for {config.get('desc', 'unknown')}")
    for dataset_name, location in outloc.items():
        jobdef_entry["outputs"].append({
            "dataset": dataset_name,
            "location": location
        })
    return jobdef_entry


def append_jobdef(config, jobdefs_file=None):
    """Append the config's entry to a jobdefs file in JSON format.

    A non-dict outloc warns and skips rather than failing, preserving
    long-standing behaviour on this path. The enqueue path (json2jobdef
    --enqueue) deliberately does NOT swallow it: skipping there would
    push a cnf to SAM and create no campaign, a half-done production
    push that reports success.
    """
    try:
        jobdef_entry = build_jobdesc(config)
    except ValueError as e:
        print(f"Warning: {e}")
        return
    _write_jobdef_json_entry(jobdef_entry, jobdefs_file)
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count >= 1005. The existing `test_append_jobdef_passes_resource_keys` and `test_append_jobdef_passes_draining_keys` must pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add utils/json2jobdef.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor(json2jobdef): extract build_jobdesc from append_jobdef

Splits the config-to-entry projection from the file write, so the
projection can be tested without touching the filesystem and reused by
the coming --enqueue path.

Named build_jobdesc, not build_map_entry: `jobdesc` is what this dict is
already called on the far side of the wire (ops["jobdesc"],
validate_jobdesc, runmu2e's jobdesc parameter). The two-names-for-one
-object split was inherited from POMS owning the submit side.

Behaviour is unchanged. build_jobdesc raises ValueError on a non-dict
outloc; append_jobdef catches it and reproduces the existing warning, so
that path still warns and skips.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 5: Extract `enqueue_entry` from `_enqueue_entries`

**Files:**
- Modify: `utils/submit.py:328-397`
- Test: `test/test_unit.py:5225` (existing `test_enqueue_*` block)

**Interfaces:**
- Produces: `enqueue_entry(entry, *, ledger_db, slice_size, dry_run=False, resources=None, provenance=None) -> int | None` — returns the new campaign id, or `None` under `dry_run`. Owns the draining-shape validation, `_ensure_local_tarball`, the `check_inputs` preflight with its exit-2 report, the njobs sanity checks, `_snapshot_entry`, and the one-line operator errors.
- `_enqueue_entries(entries_to_submit, map_path, opts)` keeps its signature and becomes a loop over `enqueue_entry`.

**Error protocol:** `enqueue_entry` RETAINS `sys.exit()`. Converting `submit.py`'s 19 exits to exceptions is a separate follow-on spec (it restructures the path that launches every production job). Because `json2jobdef` is also a CLI, inheriting those exit codes is correct: an entry whose inputs fail preflight must exit 2 from either entry point.

- [ ] **Step 1: Write the failing test**

Add to class `TestEnqueue` (`test/test_unit.py:5195`), after `test_enqueue_writes_campaign`. That class's `setUp` already patches `_ensure_local_tarball` and `check_inputs` for every test in it, and provides `self.db`, `self.sl` and `self.entry` — so these tests need no patching of their own:

```python
    def test_enqueue_entry_returns_campaign_id(self):
        from utils.submit import enqueue_entry
        camp_id = enqueue_entry(self.entry, ledger_db=self.db,
                                slice_size=2)
        camps = self.sl.active_campaigns(self.db)
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0]['id'], camp_id)
        self.assertEqual(camps[0]['slice_size'], 2)
        self.assertEqual(camps[0]['entry'], self.entry)

    def test_enqueue_entry_dry_run_returns_none(self):
        from utils.submit import enqueue_entry
        self.assertIsNone(enqueue_entry(
            self.entry, ledger_db=self.db, slice_size=2, dry_run=True))
        self.assertEqual(self.sl.all_campaigns(self.db), [])

    def test_enqueue_entry_records_provenance(self):
        from utils.submit import enqueue_entry
        enqueue_entry(self.entry, ledger_db=self.db, slice_size=2,
                      provenance='data/x.json#Desc@Conf')
        self.assertEqual(
            self.sl.active_campaigns(self.db)[0]['map_path'],
            'data/x.json#Desc@Conf')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: FAIL with `AttributeError: module 'utils.submit' has no attribute 'enqueue_entry'`.

- [ ] **Step 3: Write `enqueue_entry` and rewrite `_enqueue_entries` as a loop**

In `utils/submit.py`, replace lines 328-397 (all of `_enqueue_entries`) with:

```python
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


def _enqueue_entries(entries_to_submit, map_path, opts):
    """Register each entry as a campaign. Returns new campaign ids.
    Preflight and error handling live in enqueue_entry."""
    ids = []
    for idx, entry in entries_to_submit:
        camp_id = enqueue_entry(
            entry,
            ledger_db=opts.ledger_db,
            slice_size=opts.slice_size,
            dry_run=opts.dry_run,
            resources=_effective_resources(entry, opts),
            provenance=map_path)
        if camp_id is not None:
            ids.append(camp_id)
    return ids
```

Note: the per-entry error messages lose their `entry {idx}` prefix, because `enqueue_entry` does not know the index. Existing tests that assert on those strings must be updated to match (search for `entry 0` and `entry 1` in the enqueue test block).

- [ ] **Step 4: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count >= 1007. Fix any test asserting the old `entry {idx}` message text.

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor(submit): extract public enqueue_entry from _enqueue_entries

One entry, one campaign, one home for the preflight gates —
check_inputs with its exit-2 report, draining-shape validation, the
njobs sanity checks, and the one-line operator errors. _enqueue_entries
becomes a loop over it and keeps its signature.

This is what json2jobdef --enqueue will call, so the two entry points
share one implementation instead of forking the gates.

sys.exit is retained on purpose: converting submit.py's 19 exits to
exceptions restructures the path that launches every production job and
gets its own change. Both callers are CLIs, so the exit codes are
correct as-is.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 6: `json2jobdef --enqueue`

**Files:**
- Modify: `utils/json2jobdef.py:556-606` (argparse + main), `:688-740` (`process_single_entry`), and `process_all_for_dsconf`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `build_jobdesc(config)` (Task 4), `submit.enqueue_entry(entry, *, ledger_db, slice_size, dry_run, resources, provenance)` (Task 5)
- Produces: CLI flags `--enqueue`, `--slice-size N` (default 1000)

**Ordering is load-bearing:** enqueue runs AFTER `_pushout_to_sam`, because `enqueue_entry` resolves the tarball from SAM via `_ensure_local_tarball` and `check_inputs` reads it. A campaign whose cnf is not in SAM is broken from birth.

- [ ] **Step 1: Write the failing tests**

```python
    def test_enqueue_requires_prod(self):
        """A campaign whose cnf is not in SAM is broken from birth:
        enqueue_entry resolves the tarball from SAM."""
        proc = subprocess.run(
            [sys.executable, 'utils/json2jobdef.py',
             '--json', 'data/Run1B/resampler_beam.json',
             '--desc', 'PhysicalPionStops', '--dsconf', 'Run1Bap',
             '--enqueue'],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--enqueue requires --prod',
                      proc.stdout + proc.stderr)

    def test_slice_size_requires_enqueue(self):
        proc = subprocess.run(
            [sys.executable, 'utils/json2jobdef.py',
             '--json', 'data/Run1B/resampler_beam.json',
             '--desc', 'PhysicalPionStops', '--dsconf', 'Run1Bap',
             '--slice-size', '500'],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--slice-size requires --enqueue',
                      proc.stdout + proc.stderr)

    def test_prod_requires_jobdefs_or_enqueue(self):
        """A bare --prod would silently write jobdefs_list.json into the
        current directory."""
        proc = subprocess.run(
            [sys.executable, 'utils/json2jobdef.py',
             '--json', 'data/Run1B/resampler_beam.json',
             '--desc', 'PhysicalPionStops', '--dsconf', 'Run1Bap',
             '--prod'],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--prod requires --jobdefs or --enqueue',
                      proc.stdout + proc.stderr)

    def test_provenance_string_format(self):
        from utils.json2jobdef import _provenance
        self.assertEqual(
            _provenance('data/Run1B/resampler_beam.json',
                        {'desc': 'PhysicalPionStops', 'dsconf': 'Run1Bap'}),
            'data/Run1B/resampler_beam.json#PhysicalPionStops@Run1Bap')
```

`subprocess` and `sys` are already imported at `test/test_unit.py:20-21`. These four go in a new `class TestJson2JobdefEnqueueFlags(unittest.TestCase)` at the end of the file. The three subprocess tests only exercise argparse-level refusals, so they never reach cnf building and need no Mu2e environment — but they must run from the repo root, so pass `cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` to each `subprocess.run`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: FAIL — argparse reports `unrecognized arguments: --enqueue`.

- [ ] **Step 3: Add the flags and the validation**

In `utils/json2jobdef.py`, after the `--jobdefs` argument (line 560):

```python
    p.add_argument('--enqueue', action='store_true',
                   help='After pushing the cnf, register the entry as a '
                        'sliced campaign in the ledger (no map file '
                        'written). Requires --prod.')
    p.add_argument('--slice-size', type=int, default=1000,
                   help='Jobs per slice for --enqueue (default 1000; '
                        'frozen into the campaign).')
```

Replace the `if args.prod:` block at line 571-573 with:

```python
    if args.enqueue and not args.prod:
        sys.exit("json2jobdef: --enqueue requires --prod (a campaign "
                 "needs the cnf in SAM)")
    if args.slice_size != 1000 and not args.enqueue:
        sys.exit("json2jobdef: --slice-size requires --enqueue")
    if args.prod and not (args.jobdefs or args.enqueue):
        sys.exit("json2jobdef: --prod requires --jobdefs or --enqueue "
                 "(a bare --prod would write jobdefs_list.json into the "
                 "current directory)")

    # If --prod is specified, enable pushout
    if args.prod:
        args.pushout = True
```

- [ ] **Step 4: Add the provenance helper**

In `utils/json2jobdef.py`, above `process_single_entry`:

```python
def _provenance(json_path, config):
    """Free-text origin recorded as the campaign's map_path. The column
    is never dispatched from — only the MCP status tools echo it — so it
    records where the entry CAME FROM rather than a filename that no
    longer exists."""
    return (f"{json_path}#{config.get('desc', '?')}"
            f"@{config.get('dsconf', '?')}")
```

- [ ] **Step 5: Wire the enqueue call into `process_single_entry`**

Change the signature at line 688 to accept the new parameters:

```python
def process_single_entry(config, pushout=False, no_cleanup=True,
                         jobdefs_list=None, extend=False,
                         ignore_empty=False, enqueue=False,
                         slice_size=1000, json_path=None):
```

Then, after the existing `if pushout: _pushout_to_sam(parfile_name, config['owner'])` (line 734-735), append:

```python
    # AFTER pushout, always: enqueue_entry resolves the tarball from SAM
    # and check_inputs reads it, so a campaign created before the push
    # would be broken from birth.
    if enqueue:
        from types import SimpleNamespace
        from utils.submit import enqueue_entry, _resolve_ledger_db
        entry = build_jobdesc(config)
        enqueue_entry(
            entry,
            ledger_db=_resolve_ledger_db(SimpleNamespace(ledger_db=None)),
            slice_size=slice_size,
            provenance=_provenance(json_path, config))
```

`_resolve_ledger_db(opts)` (`utils/submit.py:276`) reads `opts.ledger_db` and, when it is falsy, returns `submission_ledger.ensure_ledger_dir(submission_ledger.ledger_for())` — the identity-derived default, with its directory created. Passing `SimpleNamespace(ledger_db=None)` is how json2jobdef asks for that default without inventing its own path.

Note the deliberate asymmetry: `append_jobdef` swallows a non-dict `outloc` with a warning, but this path calls `build_jobdesc` directly and lets the `ValueError` propagate. Skipping here would push a cnf to SAM and create no campaign — a half-done production push that reports success.

- [ ] **Step 6: Pass the new arguments through both call sites**

In `main()`, the scalar branch (line 592-598) becomes:

```python
        process_single_entry(
            config,
            pushout=args.pushout,
            no_cleanup=args.no_cleanup,
            jobdefs_list=args.jobdefs,
            extend=args.extend,
            ignore_empty=args.ignore_empty,
            enqueue=args.enqueue,
            slice_size=args.slice_size,
            json_path=args.json,
        )
```

Then update `process_all_for_dsconf` (bulk mode) to thread `enqueue`, `slice_size` and `json_path` through to its `process_single_entry` call at line 820. Read that function first (`sed -n '800,830p' utils/json2jobdef.py`) and add the parameters to its signature in the same style as the existing ones.

Finally, guard the `--prod` summary print at line 602-606 so it only runs when a file was actually written:

```python
    # If --prod mode, print the submission-map summary after generation
    if args.prod and args.jobdefs:
        jobdefs_file = args.jobdefs
        print(f"\n{'='*60}")
        print(f"Submission-map summary: {jobdefs_file}")
        print(f"{'='*60}")
        summarize_map(jobdefs_file)
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count >= 1011.

- [ ] **Step 8: Verify the CLI surface**

Run: `python3 -u utils/json2jobdef.py --help | grep -A2 "enqueue\|slice-size"`
Expected: both flags documented.

- [ ] **Step 9: Commit**

```bash
git add utils/json2jobdef.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(json2jobdef): --enqueue registers the campaign directly, no map file

The map file's only job was carrying one derived entry from
json2jobdef --prod to submit_map --enqueue. --enqueue hands it over
in-process instead, calling the same enqueue_entry() the map path uses,
so the preflight gates keep one home.

--jobdefs becomes optional. Under --prod at least one of --jobdefs or
--enqueue is now required, which moves the "no bare --prod" rule from
skill convention into argparse. Passing both is legal: the file remains
the handle for a manual submit_map --first/--num re-dispatch.

Enqueue runs after pushout, always — enqueue_entry resolves the tarball
from SAM, so a campaign created before the push is broken from birth.
Hence --enqueue requires --prod.

map_path records the config provenance (path#desc@dsconf) instead of a
filename that no longer exists. The column is never dispatched from.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 7: Single-entry `jobdesc`

**Files:**
- Modify: `utils/jobsub_argv.py:170-178`, `utils/prod_utils.py:268-290`, `utils/runmu2e.py:81-129`, `:185-198`, `:638-680`
- Test: `test/test_unit.py` — the 34 references to `resolve_map_index` / `validate_jobdesc`

**Interfaces:**
- Produces: `resolve_map_index(entry, job_index) -> (entry, local_index) | (None, None)`; `ops["jobdesc"]` is a single object, not a list.

**Why behaviour is preserved:** with one entry `cumulative` is always 0, so today's `local = global - cumulative + firstjob` already reduces to `local = global + firstjob`, gated on `global < njobs`. The `--indices` recovery path is unaffected: `submit.py:654-656` rewrites the shipped copy to `firstjob: 0, njobs: jobset[-1] + 1` so `local == global`, and that rewrite is independent of the list wrapper.

**Why no version skew:** `_bundle_prodtools` (`utils/submit.py:400`) ships this repo's `utils/` with every submission and rebuilds whenever a source file changes, so the worker always runs the code version that submitted it. Jobs already in flight keep their own older bundle and their own older `ops` — also self-consistent.

- [ ] **Step 1: Write the failing tests**

```python
    def test_resolve_map_index_single_entry(self):
        from utils.prod_utils import resolve_map_index
        entry = {'tarball': 'cnf.mu2e.D.C.0.tar', 'njobs': 10,
                 'inloc': 'tape', 'outputs': []}
        got_entry, local = resolve_map_index(entry, 3)
        self.assertIs(got_entry, entry)
        self.assertEqual(local, 3)

    def test_resolve_map_index_applies_firstjob(self):
        from utils.prod_utils import resolve_map_index
        entry = {'tarball': 'cnf.mu2e.D.C.0.tar', 'njobs': 10,
                 'firstjob': 100, 'inloc': 'tape', 'outputs': []}
        _, local = resolve_map_index(entry, 3)
        self.assertEqual(local, 103)

    def test_resolve_map_index_out_of_range(self):
        from utils.prod_utils import resolve_map_index
        entry = {'tarball': 'cnf.mu2e.D.C.0.tar', 'njobs': 10,
                 'inloc': 'tape', 'outputs': []}
        self.assertEqual(resolve_map_index(entry, 10), (None, None))

    def test_resolve_map_index_generic_entry_has_no_slots(self):
        from utils.prod_utils import resolve_map_index
        entry = {'tarball': 'cnf.mu2e.D.C.0.tar', 'inloc': 'tape',
                 'outputs': []}
        self.assertEqual(resolve_map_index(entry, 0), (None, None))

    def test_build_ops_json_ships_single_jobdesc(self):
        from utils.jobsub_argv import build_ops_json
        entry = {'tarball': 'cnf.mu2e.D.C.0.tar', 'njobs': 10,
                 'inloc': 'tape', 'outputs': []}
        ops = build_ops_json(entry=entry, jobset=[0, 1],
                             input_datasets=[], files=None)
        self.assertIsInstance(ops['jobdesc'], dict)
        self.assertEqual(ops['jobdesc']['tarball'], 'cnf.mu2e.D.C.0.tar')
```

`build_ops_json` is keyword-only: `build_ops_json(*, entry, jobset, input_datasets, files=None)` at `utils/jobsub_argv.py:165`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: FAIL — `resolve_map_index` returns a 3-tuple and `ops['jobdesc']` is a list.

- [ ] **Step 3: Rewrite `resolve_map_index`**

Replace `utils/prod_utils.py:268-290` with:

```python
def resolve_map_index(entry, job_index):
    """Map a global job index to the entry's cnf-local index.

    `local = job_index + firstjob`, so a windowed entry runs cnf indices
    [firstjob, firstjob+njobs). Window semantics (statistics expansion,
    seed safety): see utils/map_entry.py. A generic entry (no njobs)
    occupies no index space.

    Returns:
        tuple: (entry, local_job_index), or (None, None) if job_index is
               beyond the entry's njobs.
    """
    njobs = njobs_of(entry)
    if njobs is None or job_index >= njobs:
        return None, None
    return entry, job_index + firstjob_of(entry)
```

- [ ] **Step 4: Ship a single object in the ops JSON**

In `utils/jobsub_argv.py`, change line 178 from `"jobdesc": [dict(entry)],` to `"jobdesc": dict(entry),`, and update the docstring at line 170 from `single-element submission-map entry` to `the submission entry, consumed by`.

- [ ] **Step 5: Collapse the worker-side validation**

Replace `utils/runmu2e.py:81-129` (`validate_jobdesc`) with:

```python
def validate_jobdesc(jobdesc):
    """Validate the job description and pick the dispatch mode.

    Args:
        jobdesc: One job description dictionary

    Returns:
        str or False: 'direct_input' if direct-input mode, False if
                      normal mode

    Raises:
        SystemExit: If validation fails
    """
    if not jobdesc:
        fail("Error: No job description found in ops")

    # firstjob (cnf-index window) is only meaningful on an njobs-bearing
    # entry — anywhere else it would be silently ignored and the entry
    # would re-run cnf indices [0, N), duplicating physics.
    if 'firstjob' in jobdesc and 'njobs' not in jobdesc:
        fail("Error: jobdesc has 'firstjob' but no 'njobs' — "
             "index windows require a fixed job count")

    # Direct-input mode: tarball present but no njobs.
    if 'tarball' in jobdesc and 'njobs' not in jobdesc:
        _require_fields(jobdesc, ['tarball', 'inloc', 'outputs'],
                        'Direct-input mode')
        return 'direct_input'

    if 'njobs' not in jobdesc:
        fail("Error: Normal mode requires 'njobs' in the jobdesc")
    _require_fields(jobdesc, ['tarball', 'inloc', 'outputs'],
                    'Normal mode')
    return False
```

The deleted `len(jobdesc) > 1` refusal and the "generic tarball skipped in normal dispatch" branch are both unreachable with one entry: a single generic entry (tarball, no njobs) always classifies as direct-input first.

- [ ] **Step 6: Update the worker call sites**

In `utils/runmu2e.py`:

- `process_direct_input` (line 146): change `jobdesc_entry = jobdesc[0]` to `jobdesc_entry = jobdesc`, and its docstring from `List with exactly one job description dictionary` to `The job description dictionary`.
- `process_jobdef` (line 192-196): change to

```python
    jobdesc_entry, job_index_num = resolve_map_index(jobdesc, job_index)

    if jobdesc_entry is None:
        fail(f"Error: Job index {job_index} out of range. "
             f"Total jobs available: {jobdesc.get('njobs', 0)}")

    print(f"Global job index: {job_index}, "
          f"Local job index within definition: {job_index_num}")
```

  (the `Job {job_index} uses definition {jobdesc_index}` line goes — there is only one definition now)
- Line 655: change `inloc = jobdesc[0].get('inloc')` to `inloc = jobdesc.get('inloc')`.

- [ ] **Step 7: Run the full suite and fix the churn**

Run: `python3 -u test/test_unit.py 2>&1 | tail -30`

Expected: several failures among the 34 `resolve_map_index` / `validate_jobdesc` references. For each:
- Tests asserting multi-entry behaviour (`len(jobdesc) > 1` refusal, cumulative index walking across entries, "generic skipped in normal dispatch") are **deleted** — that behaviour no longer exists.
- Tests passing `[entry]` are changed to pass `entry`.
- Tests unpacking a 3-tuple from `resolve_map_index` are changed to unpack 2.

Re-run until `OK (skipped=1)`.

- [ ] **Step 8: Commit**

```bash
git add utils/prod_utils.py utils/jobsub_argv.py utils/runmu2e.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor(worker)!: ops["jobdesc"] is a single object, not a list

Every producer has shipped exactly one element since POMS was retired —
jobsub_argv.py:178 is the only one — while the worker kept live
multi-entry machinery: the len(jobdesc) > 1 refusal, the cumulative
index walk in resolve_map_index, and a "generic tarball skipped in
normal dispatch" branch that is unreachable with one entry (a single
generic entry always classifies as direct-input first).

Behaviour is preserved exactly: with one entry cumulative is always 0,
so local = global - cumulative + firstjob already reduced to
local = global + firstjob. The --indices recovery path is untouched --
submit.py rewrites the shipped copy to firstjob=0 so local == global,
independent of the list wrapper.

Safe with no version-skew window: _bundle_prodtools ships utils/ with
every submission, so the worker always runs the code that submitted it.
Jobs in flight keep their own older bundle and older ops.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 8: Rename `utils/map_entry.py` → `utils/jobdesc.py`

**Files:**
- Rename: `utils/map_entry.py` → `utils/jobdesc.py`
- Modify: the eight importers — `utils/json2jobdef.py`, `utils/jobsub_argv.py`, `utils/submit.py`, `utils/submissions.py`, `utils/prod_utils.py`, `mcp/src/prodtools_mcp/tools/status.py`, `mcp/src/prodtools_mcp_write/tools.py`, `test/test_unit.py`

**Interfaces:** every function name inside the module is unchanged. Only the module path moves.

- [ ] **Step 1: Rename the file**

```bash
git mv utils/map_entry.py utils/jobdesc.py
```

- [ ] **Step 2: Update the module docstring**

Replace `utils/jobdesc.py:1-25` (the docstring) with:

```python
"""Submission-entry (`jobdesc`) accessors.

A jobdesc describes one submission. It is stored in both ledger tables
(`campaigns.entry_json`, `submissions.entry_json`), shipped to the
worker as `ops["jobdesc"]`, and read there by `utils/runmu2e.py`:

    {
        "tarball":  "cnf.mu2e.<desc>.<dsconf>.<index>.tar",   # required
        "outputs":  [ {"dataset": "...", "location": "tape|disk|scratch"}, ... ],  # required
        "njobs":    <int>,                                    # optional
        "inloc":    "tape|disk|resilient|stash|dir:<path>|none",  # optional, defaults 'none'
        "firstjob": <int>,                                    # optional, defaults 0
    }

`firstjob` windows the entry into the cnf's index space: the entry's
njobs slots run cnf indices [firstjob, firstjob+njobs) instead of
[0, njobs). Since baseSeed = 1 + cnf index, this is the mechanism for
extending a dataset with fresh seeds while reusing the existing
tarball (statistics expansion of open-ended resampler/generator cnfs).

These helpers enforce fail-loud access on the required fields and the
documented sentinel defaults on the optional ones. Use them instead of
bare `entry[...]` or `entry.get(...)` so a malformed jobdesc is caught
at the boundary, not as a downstream crash.
"""
```

- [ ] **Step 3: Update every importer**

```bash
grep -rln "map_entry" utils/ mcp/ test/ bin/ | \
  xargs sed -i 's/utils\.map_entry/utils.jobdesc/g; s/from utils import map_entry/from utils import jobdesc/g'
```

Then find the stragglers — comments and docstrings referencing `utils/map_entry.py` as a cross-reference:

```bash
grep -rn "map_entry" utils/ mcp/ test/ bin/
```

Update each to `utils/jobdesc.py`. There are known cross-references in `utils/prod_utils.py` (`resolve_map_index`'s docstring) and in the Task 4 `build_jobdesc` comments.

- [ ] **Step 4: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count unchanged from Task 7.

- [ ] **Step 5: Verify no import survives and the MCP servers still load**

```bash
grep -rn "map_entry" . --include=*.py --include=*.md | grep -v docs/superpowers
```
Expected: no output.

```bash
python3 -c "import sys; sys.path.insert(0, '.'); from utils import jobdesc; print(jobdesc.tarball_of({'tarball': 'cnf.mu2e.D.C.0.tar'}))"
```
Expected: `cnf.mu2e.D.C.0.tar`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: rename utils/map_entry.py -> utils/jobdesc.py

The accessors operate on the dict the worker already calls `jobdesc`, so
leaving the module named after the file this branch deletes would
relocate the naming split rather than close it. Function names are
unchanged; only the module path moves.

This module was renamed once already (poms_entry -> map_entry). That
pass removed "poms" and landed on "map" — the name of the file we are
deleting. `jobdesc` is the name that stays true afterwards.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 9: Documentation for the new surface

**Files:**
- Modify: `docs/EXAMPLES_schema.md`, `CLAUDE.md`, `wiki/pages/2026-07-18-direct-recovery-loop.md`, `.claude/commands/mu2epro-run.md`, `.claude/commands/mu2epro-submit.md`
- Regenerate: `EXAMPLES.md`

**Interfaces:** consumes the CLI surface from Tasks 2 and 6.

- [ ] **Step 1: Update the EXAMPLES schema**

In `docs/EXAMPLES_schema.md`:

Extend the verb list at line 103 from `set-slice CAMP_ID N` and `set-memory CAMP_ID MEM` to also cover:

```markdown
      `set-entry CAMP_ID KEY VALUE [--include-open-rows]` — set one of
      inloc/memory/disk/expected_lifetime on a live campaign. Without
      the flag it reaches future slices only; with it, also the
      not-yet-closed rows, which is what makes RECOVERIES use the new
      value.
```

Add to the json2jobdef flag list: `--enqueue`, `--slice-size N`.

Add these tribal-knowledge bullets:

```markdown
- Under `--prod`, at least one of `--jobdefs` or `--enqueue` is
  required. `--enqueue` also requires `--prod`: the campaign's cnf must
  be in SAM, because enqueue resolves the tarball from there.
- `--enqueue` writes no map file. The campaign's `map_path` records the
  config provenance (`<config>.json#<desc>@<dsconf>`) instead.
- `set-entry --include-open-rows` is OFF by default because an UNSET
  `memory` is what earns a recovery the 4000MB floor; cascading a memory
  value forfeits it. An `inloc` fix normally wants the flag ON.
```

- [ ] **Step 2: Regenerate EXAMPLES.md**

Invoke the `refresh-examples` skill with the argument:
`focus on json2jobdef --enqueue / --slice-size, the widened --prod rule (at least one of --jobdefs or --enqueue), and the submissions set-entry verb with --include-open-rows`

Verify: `grep -n "enqueue\|set-entry" EXAMPLES.md | head`
Expected: both documented.

- [ ] **Step 3: Update `CLAUDE.md`**

In the "Running prodtools commands" section, add after the `/mu2epro-run` bullet:

```markdown
Production campaigns are created in one command:
`json2jobdef --prod --enqueue --slice-size N` builds the cnf, pushes it
to SAM, and registers the campaign. No map file is involved. A wrong
setting on a live campaign is fixed with
`submissions set-entry <ID> <key> <value> [--include-open-rows]` — the
flag is what reaches recoveries.
```

- [ ] **Step 4: Update the direct-recovery runbook**

In `wiki/pages/2026-07-18-direct-recovery-loop.md`, find the operator quickstart's two-step enqueue (`json2jobdef --prod --jobdefs <map>` followed by `submit_map --map <map> --enqueue`) and replace it with:

```
json2jobdef --json <config>.json --desc <D> --dsconf <C> \
    --prod --enqueue --slice-size 1000
```

Then add a `### Fixing a live campaign's settings` subsection stating: `set-entry` edits `inloc`, `memory`, `disk` or `expected_lifetime`; without `--include-open-rows` it reaches future slices only, because `resubmit()` rebuilds from the row's frozen snapshot; the flag defaults off because an unset `memory` is what earns a recovery the 4000 MB floor; and an `inloc` fix normally wants it on. Cite campaign 54 as the worked example.

- [ ] **Step 5: Update `/mu2epro-run`'s hard rule**

In `.claude/commands/mu2epro-run.md`, change the `json2jobdef --prod` HARD RULE from "`--jobdefs` is mandatory" to:

```markdown
   **HARD RULE for `json2jobdef --prod`:** at least one of `--jobdefs`
   or `--enqueue` is mandatory, and `argparse` now enforces it. Prefer
   `--enqueue`: it creates the campaign directly with no map file. Use
   `--jobdefs` only when you also want the file as a handle for a
   manual `submit_map --map <file> --first N --num M` re-dispatch.
```

Delete the backend-routing section's `direct_maps/`-versus-`/tmp` guidance — the normal path writes no file.

- [ ] **Step 6: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: `OK (skipped=1)`, count unchanged from Task 8.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
docs: one-command enqueue flow and the set-entry verb

EXAMPLES schema + regen, CLAUDE.md, the direct-recovery runbook, and
/mu2epro-run's hard rule, which widens from "--jobdefs is mandatory" to
"at least one of --jobdefs or --enqueue" now that argparse enforces it.

Records the two rules that are easy to get backwards: --enqueue requires
--prod (the cnf must be in SAM before a campaign points at it), and
set-entry --include-open-rows is off by default because an unset memory
is what earns a recovery the 4000MB floor.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Live acceptance (after all tasks, in the main checkout)

These are operator steps, not part of any task. Run them after merging.

1. **Fix campaign 54** — the reason Change 2 went first:
   ```
   submissions set-entry 54 inloc resilient --include-open-rows
   ```
   Expect the changed row ids printed. Confirm with `campaign_status`.
   The input dataset `sim.mu2e.PiTargetStops.Run1Bap.art` (500 files,
   14.95 GB) must be staged to resilient BEFORE this, or `inloc:
   resilient` falls back to SAM silently.

2. **One `json2jobdef --prod --enqueue` as mu2epro**, producing a
   campaign visible to `campaign_status`, with nothing written to
   `direct_maps/`.

3. **`submissions run --dry-run`** confirming that campaign's first
   slice would dispatch.
