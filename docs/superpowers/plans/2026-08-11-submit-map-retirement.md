# `submit_map` Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the submission map from prodtools entirely — as a file, as a command, and as a word — leaving `json2jobdef --prod --enqueue` as the only way to create a campaign and `submissions` as the only operator CLI.

**Architecture:** `utils/submit.py` already contains the real engine, `submit_entry_direct(entry, idx, opts)`, which accepts one entry as an ordinary Python value. `submit_map()` is a file reader and loop wrapped around it, and `bin/submit_map` is a subprocess boundary that four call sites in `utils/submissions.py` cross by serialising an in-memory entry to a temp file. We replace `opts` with a frozen `SubmitOptions`, call the engine in-process, move the one genuine operator use (hand re-firing failed work) to a `submissions resubmit` verb, and delete the command.

**Tech Stack:** Python 3.9 (no third-party wheels), sqlite3 3.34.1, `unittest` (stdlib), jobsub_lite via `jobsub_submit`.

## Global Constraints

- **Suite command:** `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`. Baseline **1025 OK (skipped=1)**.
- **No subprocess-spawning tests.** `test/test_unit.py:38-47` stubs `samweb_client` and `ifdh` into `sys.modules` **in-process only**; a test that spawns a Python subprocess dies with `ModuleNotFoundError`.
- **Single-class run:** `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestClassName` (verified working).
- **Python 3.9 floor.** No `match`, no `X | Y` type unions, no `tomllib`. `typing.NamedTuple` and `dataclasses` are available.
- **Do NOT `git push`.** The user pushes from their own shell.
- **Never fetch or refresh the mu2epro token.** Missing token → stop and report.
- **Work stays in this worktree** (`.worktrees/map-file-removal`, branch `map-file-removal`). There is no deploy step — `submissions run` executes straight out of the working checkout.
- **`enqueue_entry` (`utils/submit.py:357`) keeps its `sys.exit()` protocol and is not touched.** Its only caller is `json2jobdef`, a CLI.
- **State the true test count in every commit message.** Deleting map machinery removes tests; do not claim a number you did not read off the runner.

---

## THREE TRAPS — read before starting any task

These are places where the obvious reading of the spec produces a production bug. Every task that touches them repeats the warning, but read them once here.

### Trap 1: `--first`/`--num` are NOT dead

The spec records that **manual operator** `--first/--num` windowing is unused, which is why the CLI can be deleted. **The mechanism is load-bearing.** `submit_slice` (`utils/submissions.py:726`) feeds every campaign slice by passing `--first <cursor> --num <n>`, and `_compute_jobset` (`utils/submit.py:554`) reads `opts.first` / `opts.num` to build the job list.

`SubmitOptions` **must** carry `first` and `num`. Deleting them breaks campaign slicing completely.

### Trap 2: `SystemExit` is not an `Exception`

`submit_entry_direct` raises `SystemExit` on an input pre-flight failure (`utils/submit.py:787`). `SystemExit` derives from `BaseException`, **not** `Exception`:

```
SystemExit.__mro__ == (SystemExit, BaseException, object)
```

So `except Exception:` **will not catch it**. Today this is harmless — it kills a subprocess and the parent reads a nonzero return code. In-process it terminates the whole tick. Every containment site must catch `(Exception, SystemExit)` explicitly. This is the single most likely way to ship a silent regression from this plan.

### Trap 3: the word `direct` has two meanings

**Dead meaning — the backend qualifier.** `submit_entry_direct`, and "direct backend" / "direct-backend submission" phrasing. The `mu2ejobsub` backend was retired 2026-07-19; `utils/submit.py:3` says "single backend". This is what gets renamed.

**Live meaning — direct input.** A job taking one named input file instead of computing inputs from an index (draining mode). **Never touch these:**

- `direct_input`, `process_direct_input`, `write_direct_input_fcl`, `_direct_input_dir`
- every `_direct_*` in `utils/runmu2e.py`: `_is_direct_mode:407`, `_load_direct_ops:418`, `_resolve_direct_index:428`, `_synthesize_direct_fname:439`, `_direct_dispatch:618`, `_direct_main:718`

**Do not run a tree-wide find-and-replace on `direct`.** Rename only the identifiers named in Task 2.

---

## File Structure

| File | Responsibility after this plan |
|---|---|
| `utils/submit.py` | Library only, no CLI. Owns `SubmitOptions` and `submit_entry`, the single submission engine. Keeps `enqueue_entry` for `json2jobdef`. |
| `utils/submissions.py` | Campaign tick and operator verbs. Calls `submit_entry` in-process through one containment helper. Owns the new `resubmit` verb. |
| `utils/submission_ledger.py` | Ledger schema and accessors. Gains `row_by_id`; `map_path` column becomes `origin`. |
| `mcp/src/prodtools_mcp_write/tools.py` | `push_cnf` and `run_submissions` only; `enqueue_campaign` deleted. |
| `mcp/src/prodtools_mcp/tools/status.py` | Echoes `origin` instead of `map_path`. |
| `bin/submit_map` | **Deleted.** |
| `test/test_unit.py` | Single suite; map-file tests removed, containment and resubmit tests added. |

---

## Task 1: Close the enqueue door

**Files:**
- Modify: `utils/submit.py` (argparse block `:933-1010`, `_enqueue_entries:438`, `submit_map:820`, `_reserve_in_ledger:163`, `submit_entry_direct:794`, `main:1044-1060`)
- Modify: `mcp/src/prodtools_mcp_write/tools.py` (`enqueue_campaign:345`, `_read_entries_strict:109`, `_read_map_entry:142`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `submit_map(map_path, opts)` still exists but is single-entry only; no `--enqueue`, `--slice-size`, `--entry`, `--no-ledger` anywhere. `enqueue_entry(entry, *, ledger_db, slice_size, dry_run, resources, provenance)` unchanged and still exported.

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py`, immediately before the `if __name__ == '__main__':` block at the end of the file. **Append at the end — do not insert between existing classes.** Inserting mid-file has previously stolen methods from the class above, and because both classes still passed, the suite gave no signal; the only tell was a class running more tests than it declared.

```python
class TestEnqueueDoorClosed(unittest.TestCase):
    """The only campaign-creation path is json2jobdef --prod --enqueue.

    submit_map's --enqueue was a second door into campaign creation, and
    a rule enforced on one door only is how campaign 54 lost 239 of 500
    jobs to an unvalidated inloc.
    """

    def test_enqueue_flags_are_gone_from_argv(self):
        import utils.submit as submit
        src = Path(submit.__file__).read_text()
        for flag in ("'--enqueue'", "'--slice-size'", "'--entry'",
                     "'--no-ledger'"):
            self.assertNotIn(
                flag, src,
                f"{flag} still registered in submit.py argparse")

    def test_enqueue_entries_helper_is_gone(self):
        import utils.submit as submit
        self.assertFalse(hasattr(submit, '_enqueue_entries'))

    def test_enqueue_entry_survives_for_json2jobdef(self):
        import utils.submit as submit
        self.assertTrue(callable(submit.enqueue_entry))

    def test_no_ledger_attribute_is_not_consulted(self):
        import utils.submit as submit
        src = Path(submit.__file__).read_text()
        self.assertNotIn('no_ledger', src)

    def test_mcp_enqueue_campaign_tool_is_gone(self):
        import prodtools_mcp_write.tools as tools
        self.assertFalse(hasattr(tools, 'enqueue_campaign'))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestEnqueueDoorClosed
```

Expected: FAIL — 5 failures (the flags, helper, `no_ledger` and the MCP tool all still exist); `test_enqueue_entry_survives_for_json2jobdef` passes already.

- [ ] **Step 3: Delete the argparse arguments**

In `utils/submit.py` `main()`, delete these four `parser.add_argument` blocks entirely: `--entry`, `--enqueue`, `--slice-size`, `--no-ledger`.

- [ ] **Step 4: Delete `_enqueue_entries` and the multi-entry logic**

Delete `_enqueue_entries` (`:438-453`) whole.

In `submit_map()`, delete the `--entry` range check, the `enumerate` fan-out, the generic-tarball skip filter, and the `--enqueue` dispatch. The function reduces to: load the JSON, require exactly one entry, validate, submit it. Replace the body from the `# Filter by --entry` comment through the `if getattr(opts, 'enqueue', False):` block with:

```python
    if len(entries) != 1:
        print(f"Error: {map_path} must contain exactly one entry "
              f"(got {len(entries)}) — multi-entry maps were removed with "
              f"the map workflow; use json2jobdef --dsconf to enqueue a set")
        sys.exit(1)
    entries_to_submit = [(0, entries[0])]
```

- [ ] **Step 5: Remove the `no_ledger` branches**

In `_reserve_in_ledger` (`:163`), delete:

```python
    if opts.no_ledger:
        return None
```

and drop the "or `None` when `--no-ledger`" clause from its docstring.

In `submit_entry_direct` (`:794`), change:

```python
    if not opts.no_ledger:
        _log_submission(firstjob, jobset, result, opts, files=files)
```

to:

```python
    _log_submission(firstjob, jobset, result, opts, files=files)
```

In `main()`, delete the `if not args.no_ledger:` guard around `_resolve_ledger_db` (keep the call, now unconditional), the `--enqueue`/`--no-ledger` contradiction check, the `--enqueue` combination checks, and the `--slice-size` range check.

`_attach_cluster` and `_fail_reservation` keep their `if row_id is None: return` guards — those are cheap and still correct.

- [ ] **Step 6: Delete the MCP `enqueue_campaign` tool**

In `mcp/src/prodtools_mcp_write/tools.py` delete `enqueue_campaign` whole, then `_read_map_entry` and `_read_entries_strict` if nothing else calls them (grep first — `push_cnf` may still use `_read_map_entry`; if so, keep it). Also delete the `submit_map` references in the surviving docstrings at `:114`, `:145`, `:302` — they describe a workflow that no longer exists.

Remove the `'enqueue_campaign'` entry from `TOOL_FUNCTIONS` in `mcp/src/prodtools_mcp_write/server.py:9`. `TOOL_NAMES` is derived from that dict (`TOOL_NAMES = tuple(TOOL_FUNCTIONS)`), so the two cannot drift — edit the dict only. Drop any `Path`/`json` imports left unused.

Leave `'bin/submit_map'` in `runner.py`'s `ALLOWED_ENTRY_POINTS` for now — Task 6 removes it when the script is deleted. Removing it here would break `enqueue_campaign`'s siblings before their turn.

- [ ] **Step 7: Fix the tests that exercised the deleted surface**

Run the full suite and repair fallout. Expect failures in `TestEnqueue`, `TestEnqueueErrorStyle`, `TestEnqueueInputGate`, `TestEnqueueDraining`, `TestEnqueueAndRunTools`, `TestSubmitEnqueueCreatesFreshLedgerDir`. Tests that assert `enqueue_entry` behaviour **stay** — retarget them at `enqueue_entry` directly rather than through `submit_map --enqueue` argv. Tests that only existed to cover the `submit_map --enqueue` CLI or `enqueue_campaign` are deleted.

- [ ] **Step 8: Run the full suite**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`. Record the exact test count for the commit message.

- [ ] **Step 9: Commit**

```bash
git add utils/submit.py mcp/src/prodtools_mcp_write/tools.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor!: close the submit_map enqueue door

Campaign creation is now json2jobdef --prod --enqueue and nothing else.
Two doors meant every safety rule had to be written twice; the inloc
validator was on one door only and campaign 54 lost 239 of 500 jobs.

Deletes --enqueue/--slice-size/--entry/--no-ledger, _enqueue_entries,
the multi-entry fan-out, and the enqueue_campaign MCP tool. enqueue_entry
keeps its sys.exit protocol untouched — json2jobdef is a CLI and inheriting
exit codes is correct there.

Suite: <N> OK (skipped=1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 2: `SubmitOptions` and the `submit_entry` rename

**Files:**
- Modify: `utils/submit.py` (add `SubmitOptions`; `submit_entry_direct:617`; helpers `_reserve_in_ledger:146`, `_attach_cluster:174`, `_fail_reservation:194`, `_log_submission:218`, `_effective_resources:252`, `_compute_jobset:554`; `main()`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: Task 1's cleaned `utils/submit.py`.
- Produces:
  - `submit.SubmitOptions` — a `typing.NamedTuple` with fields `ledger_db: str`, `dry_run: bool = False`, `first: Optional[int] = None`, `num: Optional[int] = None`, `indices: Optional[list] = None`, `files: Optional[list] = None`, `origin: Optional[str] = None`, `ledger_parent: Optional[int] = None`, `prodtools_tar: Optional[str] = None`, `role: Optional[str] = None`, `wftop: Optional[str] = None`, `wfproject: Optional[str] = None`, `memory: Optional[str] = None`, `disk: Optional[str] = None`, `expected_lifetime: Optional[str] = None`.
  - `submit.submit_entry(entry, idx, options)` → `dict` with keys `tarball`, `cluster_id`, `njobs`, `status`. Raises `SystemExit` on input pre-flight failure, `ValueError` on a bad window.
  - `submit_entry_direct` no longer exists.

> **TRAP 1 applies.** `first` and `num` are required fields of `SubmitOptions`. `submit_slice` passes them for every campaign slice and `_compute_jobset` reads them. The spec's note that `--first/--num` is unused refers to the **operator CLI flag**, not the mechanism.

> **TRAP 3 applies.** Rename `submit_entry_direct` → `submit_entry` and the "direct backend" wording in `utils/submit.py` docstrings **only**. Do not touch `direct_input`, `process_direct_input`, `write_direct_input_fcl`, `_direct_input_dir`, or any `_direct_*` in `utils/runmu2e.py`.

- [ ] **Step 1: Write the failing tests**

Append before `if __name__ == '__main__':`:

```python
class TestSubmitOptions(unittest.TestCase):
    """SubmitOptions replaces the argparse namespace the engine used to
    reach into, so submissions.py can call submit_entry without building
    a fake CLI object."""

    def test_defaults_allow_a_minimal_construction(self):
        from utils.submit import SubmitOptions
        o = SubmitOptions(ledger_db='/tmp/x.db')
        self.assertEqual(o.ledger_db, '/tmp/x.db')
        self.assertFalse(o.dry_run)
        self.assertIsNone(o.indices)
        self.assertIsNone(o.files)
        self.assertIsNone(o.origin)

    def test_carries_first_and_num(self):
        """TRAP 1: submit_slice feeds EVERY campaign slice through these.
        They are not the retired operator flags."""
        from utils.submit import SubmitOptions
        o = SubmitOptions(ledger_db='/tmp/x.db', first=100, num=50)
        self.assertEqual((o.first, o.num), (100, 50))

    def test_is_immutable(self):
        from utils.submit import SubmitOptions
        o = SubmitOptions(ledger_db='/tmp/x.db')
        with self.assertRaises(AttributeError):
            o.dry_run = True


class TestSubmitEntryRenamed(unittest.TestCase):
    """The `direct` suffix named a backend distinction retired
    2026-07-19; utils/submit.py:3 already says 'single backend'."""

    def test_submit_entry_exists(self):
        import utils.submit as submit
        self.assertTrue(callable(submit.submit_entry))

    def test_old_name_is_gone(self):
        import utils.submit as submit
        self.assertFalse(hasattr(submit, 'submit_entry_direct'))

    def test_live_direct_input_sense_is_untouched(self):
        """TRAP 3: `direct input` is a DIFFERENT, living concept — one
        named input file per job. A blanket rename would break draining."""
        import utils.runmu2e as runmu2e
        for name in ('_is_direct_mode', '_load_direct_ops',
                     '_resolve_direct_index', '_synthesize_direct_fname',
                     '_direct_dispatch', '_direct_main'):
            self.assertTrue(hasattr(runmu2e, name),
                            f"runmu2e.{name} was renamed — TRAP 3 violated")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestSubmitOptions TestSubmitEntryRenamed
```

Expected: FAIL — `ImportError`/`AttributeError` for `SubmitOptions` and `submit_entry`. `test_live_direct_input_sense_is_untouched` passes already (it is a regression guard).

- [ ] **Step 3: Add `SubmitOptions`**

Near the top of `utils/submit.py`, after the imports:

```python
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
```

Add `from typing import NamedTuple, Optional` to the imports.

- [ ] **Step 4: Rename the function and its parameter**

Rename `submit_entry_direct` → `submit_entry` and its third parameter `opts` → `options` throughout the body. Update the docstring's first line to:

```python
    """Submit one entry: build jobsub_submit argv via utils.jobsub_argv,
    ship prodtools as a dropbox tarball, run `runjob.sh` on the worker.
```

Replace `files = getattr(opts, 'files', None)` with `files = options.files` — the `getattr` guard existed because an argparse namespace might lack the attribute; a `NamedTuple` always has it.

Rename `opts` → `options` in the five helpers that receive it (`_reserve_in_ledger`, `_attach_cluster`, `_fail_reservation`, `_log_submission`, `_effective_resources`) and in `_compute_jobset`.

- [ ] **Step 5: Replace `opts.map` with `options.origin`**

Two sites. In `_reserve_in_ledger`:

```python
        map_path=options.origin,
```

(The `map_path=` **keyword** stays until Task 7 renames the ledger API.)

In `_log_submission`:

```python
            f"origin={options.origin} tarball={result['tarball']}",
```

- [ ] **Step 6: Build `SubmitOptions` in `main()`**

Replace the `submit_map(args.map, args)` call site so the namespace is converted once:

```python
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
```

Change `submit_map(map_path, opts)` to `submit_map(map_path, options)` and pass `options` through to `submit_entry`.

- [ ] **Step 7: Update the affected tests**

`TestSubmitEntryDirectResourceWiring` (`:5268`), `TestSubmitEntryDirectFiles` (`:11737`), `TestSubmitLedgerHook` (`:6183`), `TestSubmitReservesBeforeSubmitting` (`:6281`) construct fake `opts` objects (`argparse.Namespace` or `MagicMock`). Rewrite them to build `SubmitOptions` and call `submit_entry`. Rename the two `...Direct...` classes to `TestSubmitEntryResourceWiring` and `TestSubmitEntryFiles`.

A `MagicMock` standing in for `opts` is exactly what `SubmitOptions` exists to prevent — a mock answers every attribute with a truthy `Mock`, so a typo in a field name silently passes. Use the real type.

- [ ] **Step 8: Run the full suite**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`.

- [ ] **Step 9: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor: SubmitOptions replaces the argparse namespace; submit_entry

The engine reached into a parsed command line for 15 values, which is
why calling it from submissions.py meant spawning a CLI. A frozen
NamedTuple makes it callable directly.

`first`/`num` are deliberately retained: submit_slice feeds every
campaign slice through them. Only the operator-facing FLAGS are retired.

submit_entry_direct -> submit_entry: the `direct` suffix distinguished
this from the mu2ejobsub backend, retired 2026-07-19. The unrelated
`direct input` sense in runmu2e.py is untouched and now has a regression
test pinning it.

Suite: <N> OK (skipped=1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 3: Containment helper and the two campaign call sites

**Files:**
- Modify: `utils/submissions.py` (`submit_slice:726`, `submit_drain_batch:942`; add `_guarded_submit`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `submit.SubmitOptions`, `submit.submit_entry` from Task 2.
- Produces:
  - `_guarded_submit(what, fn)` → `bool` — runs one in-process submission, catching `(Exception, SystemExit)`, printing a diagnostic, returning `False` on failure.
  - `submit_slice(camp, n, db_path, submit_fn=None)` → `bool` (was `runner=subprocess.run`).
  - `submit_drain_batch(camp, files, db_path, submit_fn=None)` → `bool` (was `runner=subprocess.run`).

> **TRAP 2 applies.** `submit_entry` raises `SystemExit` on input pre-flight failure, and `SystemExit` inherits from `BaseException`. `except Exception:` will not catch it and the tick will die. Catch `(Exception, SystemExit)`.

- [ ] **Step 1: Write the failing tests**

Append before `if __name__ == '__main__':`:

```python
class TestSubmitContainment(unittest.TestCase):
    """The process boundary bin/submit_map provided is what stopped one
    bad campaign from killing the whole tick. Calling in-process removes
    it; _guarded_submit puts it back."""

    def test_exception_is_contained_and_reported_false(self):
        from utils import submissions
        def boom():
            raise RuntimeError('jobsub exploded')
        self.assertFalse(submissions._guarded_submit('campaign 7', boom))

    def test_system_exit_is_contained(self):
        """TRAP 2: submit_entry RAISES SystemExit on a pre-flight failure,
        and SystemExit derives from BaseException — `except Exception`
        lets it through and ends the tick."""
        from utils import submissions
        def preflight_fail():
            raise SystemExit('input pre-flight FAILED')
        self.assertFalse(
            submissions._guarded_submit('campaign 7', preflight_fail))

    def test_keyboard_interrupt_is_NOT_contained(self):
        """Ctrl-C must still stop the tick — swallowing it would make the
        process unkillable from the terminal."""
        from utils import submissions
        def interrupted():
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            submissions._guarded_submit('campaign 7', interrupted)

    def test_success_returns_true(self):
        from utils import submissions
        self.assertTrue(submissions._guarded_submit('campaign 7',
                                                    lambda: None))


class TestCallSitesContainFailures(unittest.TestCase):
    """Containment must live INSIDE submit_slice/submit_drain_batch.

    top_up already handles a False return by pausing the campaign and
    continuing, so the only new failure mode is an exception escaping the
    call site. Test that boundary directly — wrapping _guarded_submit by
    hand in the test would prove nothing about the real code path.
    """

    def test_submit_slice_contains_a_raising_engine(self):
        from utils import submissions

        def preflight_fail(entry, idx, options):
            raise SystemExit('input pre-flight FAILED')

        camp = {'id': 1, 'cursor': 0,
                'entry': {'tarball': 'a.tar', 'njobs': 10}}
        self.assertFalse(
            submissions.submit_slice(camp, 5, '/tmp/x.db',
                                     submit_fn=preflight_fail))

    def test_submit_drain_batch_contains_a_raising_engine(self):
        from utils import submissions

        def boom(entry, idx, options):
            raise RuntimeError('jobsub exploded')

        camp = {'id': 2,
                'entry': {'tarball': 'b.tar', 'input_pattern': 'dts.*.art'}}
        self.assertFalse(
            submissions.submit_drain_batch(camp, ['dts.mu2e.a.v.art'],
                                           '/tmp/x.db', submit_fn=boom))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestSubmitContainment TestCallSitesContainFailures
```

Expected: FAIL — `AttributeError: module 'utils.submissions' has no attribute '_guarded_submit'`.

- [ ] **Step 3: Add the containment helper**

In `utils/submissions.py`, immediately above `submit_slice`:

```python
def _guarded_submit(what, fn):
    """Run one in-process submission; return True on success, False on
    any failure, never propagating.

    This replaces the process boundary bin/submit_map used to provide.
    A subprocess that died gave the tick a nonzero return code and the
    loop moved on to the next campaign; an in-process call that raises
    would end the tick for every campaign.

    SystemExit is caught EXPLICITLY. submit_entry raises it on an input
    pre-flight failure, and SystemExit derives from BaseException, so a
    bare `except Exception` would let it escape — the exact regression
    this helper exists to prevent. KeyboardInterrupt is deliberately NOT
    caught: Ctrl-C must still stop the tick.
    """
    try:
        fn()
        return True
    except (Exception, SystemExit) as e:
        print(f"  {what}: submit FAILED ({type(e).__name__}: {e})")
        return False
```

- [ ] **Step 4: Convert `submit_slice`**

Replace the whole function:

```python
def submit_slice(camp, n, db_path, submit_fn=None):
    """Submit the campaign's next slice in-process. The snapshot entry
    ships VERBATIM: firstjob is preserved because cursor and first/num
    are entry-relative, exactly like a manual windowed submission.
    Returns True on submit success."""
    submit_fn = submit_fn or submit.submit_entry
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        first=camp['cursor'],
        num=n,
        origin=f"campaign {camp['id']}",
    )
    print(f"  campaign {camp['id']}: slice first={camp['cursor']} num={n}")
    return _guarded_submit(
        f"campaign {camp['id']}",
        lambda: submit_fn(camp['entry'], 0, options))
```

- [ ] **Step 5: Convert `submit_drain_batch`**

```python
def submit_drain_batch(camp, files, db_path, submit_fn=None):
    """Submit one draining batch in-process. The snapshot entry ships
    VERBATIM. Returns True on submit success."""
    submit_fn = submit_fn or submit.submit_entry
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        files=list(files),
        origin=f"campaign {camp['id']} drain",
    )
    print(f"  campaign {camp['id']}: batch of {len(files)}")
    return _guarded_submit(
        f"campaign {camp['id']}",
        lambda: submit_fn(camp['entry'], 0, options))
```

Add `from utils import submit` to the imports if not present.

- [ ] **Step 6: Update the tests that injected `runner=`**

`TestSubmitSlice` (`:5893`), `TestDrainTick` (`:12452`), `TestTopUpSkipsDraining` (`:12653`) pass `runner=` and assert on argv lists. Retarget them at `submit_fn=` and assert on the `SubmitOptions` the fake receives — e.g. that `options.first == cursor` and `options.num == n`, which is stronger than matching `'--first'` in a string list.

- [ ] **Step 7: Run the full suite**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`.

- [ ] **Step 8: Mutation-check the containment**

Temporarily change `except (Exception, SystemExit)` to `except Exception`, then run:

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestSubmitContainment TestCallSitesContainFailures
```

Expected: FAIL. If it passes, the tests do not pin the trap — fix them before restoring. Restore the code and confirm green.

- [ ] **Step 9: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor: campaign slices submit in-process, with failure containment

submit_slice and submit_drain_batch no longer serialise the entry to a
temp file and spawn bin/submit_map — they call submit_entry directly.

_guarded_submit restores what the process boundary was providing: a
failing campaign must not stop the tick from servicing the others. It
catches SystemExit EXPLICITLY, because submit_entry raises it on a
pre-flight failure and SystemExit derives from BaseException — `except
Exception` would let it through. Mutation-checked: narrowing the except
clause fails the suite. KeyboardInterrupt stays uncaught.

Suite: <N> OK (skipped=1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 4: The two recovery call sites

**Files:**
- Modify: `utils/submissions.py` (`resubmit:620`, `resubmit_files:674`, `recovery_resource_argv:~600`, `_scratch_map_dir:565`, `SUBMIT_MAP:49`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `_guarded_submit`, `submit.SubmitOptions`, `submit.submit_entry`.
- Produces:
  - `recovery_resource_kwargs(entry)` → `dict` with keys `memory` and/or `expected_lifetime` (replaces `recovery_resource_argv`).
  - `resubmit(row, missing, db_path, dry_run=False, submit_fn=None)` → `bool`.
  - `resubmit_files(row, missing, db_path, dry_run=False, submit_fn=None)` → `bool`.
  - `_scratch_map_dir` and `SUBMIT_MAP` no longer exist.

> **TRAP 2 applies** — the recovery path is where a raising submission is most likely, since it runs against inputs that already failed once.

- [ ] **Step 1: Write the failing tests**

```python
class TestRecoveryResourceKwargs(unittest.TestCase):
    """Recoveries get a 4000MB/48h FLOOR when the row's own entry names
    no value — an unset memory is what earns the floor."""

    def test_absent_keys_get_the_floor(self):
        from utils.submissions import (recovery_resource_kwargs,
                                       RECOVERY_MEMORY, RECOVERY_LIFETIME)
        kw = recovery_resource_kwargs({'tarball': 'x.tar'})
        self.assertEqual(kw['memory'], RECOVERY_MEMORY)
        self.assertEqual(kw['expected_lifetime'], RECOVERY_LIFETIME)

    def test_present_keys_are_left_alone(self):
        from utils.submissions import recovery_resource_kwargs
        kw = recovery_resource_kwargs(
            {'tarball': 'x.tar', 'memory': '8000MB'})
        self.assertNotIn('memory', kw)


class TestResubmitDropsFirstjob(unittest.TestCase):
    """--indices values are ABSOLUTE cnf indices, so the shipped entry
    must sit at firstjob=0 for the worker's `local == global` to hold."""

    def test_firstjob_is_stripped_from_the_shipped_entry(self):
        from utils import submissions
        captured = {}

        def fake_submit(entry, idx, options):
            captured['entry'] = entry
            captured['options'] = options
            return {'status': 'submitted', 'cluster_id': '1', 'njobs': 3,
                    'tarball': 'x.tar'}

        row = {'id': 9, 'tarball': 'x.tar',
               'entry': {'tarball': 'x.tar', 'firstjob': 400, 'njobs': 100}}
        ok = submissions.resubmit(row, [401, 402], '/tmp/x.db',
                                  submit_fn=fake_submit)
        self.assertTrue(ok)
        self.assertNotIn('firstjob', captured['entry'])
        self.assertEqual(captured['options'].indices, [401, 402])
        self.assertEqual(captured['options'].ledger_parent, 9)

    def test_a_raising_submit_is_contained(self):
        """TRAP 2 on the recovery path."""
        from utils import submissions

        def preflight_fail(entry, idx, options):
            raise SystemExit('input pre-flight FAILED')

        row = {'id': 9, 'tarball': 'x.tar',
               'entry': {'tarball': 'x.tar', 'njobs': 100}}
        self.assertFalse(
            submissions.resubmit(row, [1], '/tmp/x.db',
                                 submit_fn=preflight_fail))


class TestMapScratchDirIsGone(unittest.TestCase):
    def test_no_scratch_map_dir(self):
        from utils import submissions
        self.assertFalse(hasattr(submissions, '_scratch_map_dir'))

    def test_no_submit_map_constant(self):
        from utils import submissions
        self.assertFalse(hasattr(submissions, 'SUBMIT_MAP'))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestRecoveryResourceKwargs TestResubmitDropsFirstjob TestMapScratchDirIsGone
```

Expected: FAIL — `recovery_resource_kwargs` missing; `resubmit` still takes `runner=`; `_scratch_map_dir` and `SUBMIT_MAP` still present.

- [ ] **Step 3: Replace `recovery_resource_argv` with `recovery_resource_kwargs`**

```python
def recovery_resource_kwargs(entry):
    """Recovery resource FLOOR as SubmitOptions kwargs.

    Applies RECOVERY_MEMORY / RECOVERY_LIFETIME only where the row's own
    snapshot entry names nothing — an unset value is what earns a
    recovery the floor, so a row that already carries a value keeps it.
    """
    kwargs = {}
    for key, floor in (('memory', RECOVERY_MEMORY),
                       ('expected_lifetime', RECOVERY_LIFETIME)):
        if not entry.get(key):
            kwargs[key] = floor
    return kwargs
```

Delete `recovery_resource_argv`.

- [ ] **Step 4: Convert `resubmit`**

```python
def resubmit(row, missing, db_path, dry_run=False, submit_fn=None):
    """Resubmit missing indices in-process. Returns True on success.

    The reconstructed entry DROPS firstjob: --indices values are absolute
    cnf indices, and the worker-side firstjob+index resolution must
    degenerate to the identity. The original windowed entry stays in the
    parent row's snapshot.
    """
    submit_fn = submit_fn or submit.submit_entry
    entry = {k: v for k, v in row['entry'].items() if k != 'firstjob'}
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        indices=list(missing),
        ledger_parent=row['id'],
        dry_run=dry_run,
        origin=f"recovery of row {row['id']}",
        **recovery_resource_kwargs(entry))
    print(f"  resubmit row {row['id']}: {len(missing)} indices")
    return _guarded_submit(f"row {row['id']}",
                           lambda: submit_fn(entry, 0, options))
```

- [ ] **Step 5: Convert `resubmit_files`**

```python
def resubmit_files(row, missing, db_path, dry_run=False, submit_fn=None):
    """Draining analog of resubmit(): child submission of exactly the
    missing input files. Returns True on success."""
    submit_fn = submit_fn or submit.submit_entry
    entry = row['entry']
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        files=list(missing),
        ledger_parent=row['id'],
        dry_run=dry_run,
        origin=f"recovery of row {row['id']}",
        **recovery_resource_kwargs(entry))
    print(f"  resubmit row {row['id']}: {len(missing)} files")
    return _guarded_submit(f"row {row['id']}",
                           lambda: submit_fn(entry, 0, options))
```

- [ ] **Step 6: Delete the dead scaffolding**

Delete `_scratch_map_dir` (`:565`) and the `SUBMIT_MAP` constant (`:49`). Remove now-unused imports (`subprocess` may still be needed for `total_queued`'s `jobsub_q` call — check before removing).

- [ ] **Step 7: Update the affected tests**

`TestRecoverLoop` (`:6480`), `TestResubmitFiles` (`:12359`), `TestRecoveryResourceArgv` (`:11167`), `TestRecoverCap` (`:5562`) inject `runner=` and assert on argv. Retarget at `submit_fn=` and assert on `SubmitOptions` fields. Rename `TestRecoveryResourceArgv` → `TestRecoveryResourceKwargsLegacy` or fold its cases into the new class and delete it.

- [ ] **Step 8: Run the full suite**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`.

- [ ] **Step 9: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor: recovery resubmits go in-process

resubmit and resubmit_files call submit_entry directly, under the same
_guarded_submit containment as the campaign sites. recovery_resource_argv
becomes recovery_resource_kwargs — same 4000MB/48h floor, same "only when
the row's own entry names nothing" rule.

Deletes _scratch_map_dir and SUBMIT_MAP: nothing writes a map file any
more. firstjob-stripping is now pinned by a test rather than only a
comment.

Suite: <N> OK (skipped=1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 5: `submissions resubmit` verb

**Files:**
- Modify: `utils/submission_ledger.py` (add `row_by_id`)
- Modify: `utils/submissions.py` (add `_rows_blocking_indices`, `cmd_resubmit`; parser `:1380`; dispatch `:1537`)
- Modify: `utils/submit.py` (promote `_parse_indices`/`_parse_files` to public)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: everything from Tasks 2–4.
- Produces:
  - `submission_ledger.row_by_id(db_path, row_id)` → row `dict` or `None`.
  - `submissions._rows_blocking_indices(db_path, tarball, indices)` → blocking row `dict` or `None`.
  - `submit.parse_indices(spec, path)` and `submit.parse_files(path)` (renamed from `_parse_indices`/`_parse_files`).
  - CLI: `submissions resubmit <row-id> (--files LIST | --indices SPEC) [--dry-run]`.

**This is the highest-risk task in the plan.** It is new code on the recovery path, where a duplicate submission means duplicate physics — the payloads are deterministic, so re-sending a window that is actually running produces the same events twice. The overlap guard is not optional.

- [ ] **Step 1: Write the failing tests**

```python
class TestRowById(unittest.TestCase):
    def test_returns_the_row(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            submission_ledger.ensure_ledger_dir(db)
            rid = submission_ledger.reserve_submission(
                db, tarball='x.tar', entry={'tarball': 'x.tar'},
                indices=[1, 2, 3])
            row = submission_ledger.row_by_id(db, rid)
        self.assertEqual(row['id'], rid)
        self.assertEqual(row['indices'], [1, 2, 3])

    def test_missing_row_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            submission_ledger.ensure_ledger_dir(db)
            self.assertIsNone(submission_ledger.row_by_id(db, 999))


class TestResubmitOverlapGuard(unittest.TestCase):
    """Deterministic payloads make an unverified resubmit the Run1Ban
    failure mode: re-sending indices that are still live duplicates
    physics. A live row blocks; a closed one does not."""

    def _db_with_row(self, td, state, indices):
        db = os.path.join(td, 'submissions.db')
        submission_ledger.ensure_ledger_dir(db)
        rid = submission_ledger.reserve_submission(
            db, tarball='x.tar', entry={'tarball': 'x.tar'},
            indices=indices)
        if state != 'submitting':
            submission_ledger.attach_cluster(db, rid, jobsub_id='j',
                                             cluster_id='1')
        if state in ('complete', 'recovered', 'exhausted'):
            submission_ledger.close_row(db, rid, state)
        return db, rid

    def test_active_row_blocks(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db, rid = self._db_with_row(td, 'active', [5, 6, 7])
            blocking = submissions._rows_blocking_indices(db, 'x.tar', [6])
        self.assertIsNotNone(blocking)
        self.assertEqual(blocking['id'], rid)

    def test_submitting_row_blocks(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._db_with_row(td, 'submitting', [5, 6, 7])
            self.assertIsNotNone(
                submissions._rows_blocking_indices(db, 'x.tar', [6]))

    def test_complete_row_does_not_block(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._db_with_row(td, 'complete', [5, 6, 7])
            self.assertIsNone(
                submissions._rows_blocking_indices(db, 'x.tar', [6]))

    def test_other_tarball_does_not_block(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._db_with_row(td, 'active', [5, 6, 7])
            self.assertIsNone(
                submissions._rows_blocking_indices(db, 'other.tar', [6]))

    def test_disjoint_indices_do_not_block(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._db_with_row(td, 'active', [5, 6, 7])
            self.assertIsNone(
                submissions._rows_blocking_indices(db, 'x.tar', [99]))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestRowById TestResubmitOverlapGuard
```

Expected: FAIL — `row_by_id` and `_rows_blocking_indices` do not exist.

- [ ] **Step 3: Add `row_by_id`**

In `utils/submission_ledger.py`, after `all_rows`:

```python
def row_by_id(db_path, row_id):
    """One row by id, entry/indices JSON parsed, or None."""
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT * FROM submissions WHERE id = ?', (row_id,)).fetchone()
        return _to_dict(row) if row is not None else None
    finally:
        con.close()
```

- [ ] **Step 4: Add the overlap guard**

In `utils/submissions.py`, next to `_slice_overlaps_ledger`:

```python
# Row states that cannot have live jobs. Everything else — 'active',
# 'submitting', 'failed' — blocks a resubmit. 'failed' blocks
# deliberately: a jobsub_submit that exits non-zero can still have
# created a cluster, so its window is NOT proven free. Clear it with
# `submissions reconcile <row-id>` after checking jobsub_q.
_SETTLED_STATES = ('complete', 'recovered', 'exhausted', 'reconciled')


def _rows_blocking_indices(db_path, tarball, indices):
    """The blocking ledger row (truthy) if any unsettled row for
    `tarball` already covers one of `indices`, else None.

    The scattered-set analog of _slice_overlaps_ledger. Returns the ROW
    so the caller can name its id: the operator's next move is
    `submissions reconcile <row-id>`, and "something overlaps" would
    leave them hunting for which.
    """
    want = set(indices)
    for row in submission_ledger.all_rows(db_path):
        if row['tarball'] != tarball:
            continue
        if row.get('state') in _SETTLED_STATES:
            continue
        if want & set(row['indices']):
            return row
    return None
```

This works for both index rows and draining (file-keyed) rows: `row['indices']` holds filenames for the latter, and set intersection is the same operation either way.

- [ ] **Step 5: Promote the parsers**

In `utils/submit.py`, rename `_parse_indices` → `parse_indices` and `_parse_files` → `parse_files`, updating their call sites in `main()`. They now have a second consumer.

- [ ] **Step 6: Add the verb to the parser**

In `utils/submissions.py`, after the `set-entry` parser block:

```python
    resub_p = sub.add_parser(
        'resubmit',
        help='Re-fire specific work from a ledger row by hand',
        description='Submit a named set of indices or input files from an '
                    'existing ledger row, as a child submission (attempt+1). '
                    'The entry comes from the row, so there is no file to '
                    'write. REFUSES when any named index or file is still '
                    'covered by an unsettled row for the same tarball: '
                    'payloads are deterministic, so re-sending live work '
                    'duplicates physics. Clear a stuck row with '
                    '`submissions reconcile <row-id>` first.')
    resub_p.add_argument('row_id', type=int)
    resub_group = resub_p.add_mutually_exclusive_group(required=True)
    resub_group.add_argument('--indices', default=None,
                             help='Comma/space-separated ABSOLUTE cnf '
                                  'indices')
    resub_group.add_argument('--indices-file', default=None,
                             help='File of absolute cnf indices; `#` '
                                  'comment lines ignored')
    resub_group.add_argument('--files', default=None,
                             help='File of input art filenames, one per '
                                  'line, for a draining row')
    resub_p.add_argument('--dry-run', action='store_true',
                         help='Print what would be submitted, submit '
                              'nothing')
```

- [ ] **Step 7: Add the dispatch**

In `main()`, before the `pause/resume/cancel/complete` block:

```python
    if verb == 'resubmit':
        row = submission_ledger.row_by_id(db, args.row_id)
        if row is None:
            sys.exit(f"submissions: no ledger row {args.row_id} in {db}")
        if args.files is not None:
            payload = submit.parse_files(args.files)
            if not is_draining(row['entry']):
                sys.exit(f"submissions: row {args.row_id} is an index row "
                         f"— use --indices, not --files")
        else:
            payload = submit.parse_indices(args.indices, args.indices_file)
            if is_draining(row['entry']):
                sys.exit(f"submissions: row {args.row_id} is a draining "
                         f"(file-keyed) row — use --files, not --indices")
        if not payload:
            sys.exit("submissions: nothing to resubmit (empty selection)")

        blocking = _rows_blocking_indices(db, row['tarball'], payload)
        if blocking:
            sys.exit(
                f"submissions: refusing — row {blocking['id']} "
                f"(state={blocking['state']}) already covers part of this "
                f"selection for {row['tarball']}. Deterministic payloads "
                f"mean re-sending live work duplicates physics. Check "
                f"jobsub_q, then `submissions reconcile {blocking['id']}` "
                f"if the window is genuinely free.")

        if not args.dry_run:
            _acquire_lock(db)
        fn = resubmit_files if args.files is not None else resubmit
        ok = fn(row, payload, db, dry_run=args.dry_run)
        if not ok:
            sys.exit(f"submissions: resubmit of row {args.row_id} FAILED")
        return
```

Add `from utils import submit` to the imports if Task 3 did not already.

- [ ] **Step 8: Write the CLI-level tests**

```python
class TestResubmitVerb(unittest.TestCase):
    def test_refuses_a_missing_row(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            submission_ledger.ensure_ledger_dir(db)
            with self.assertRaises(SystemExit) as cm:
                submissions.main(['--db', db, 'resubmit', '999',
                                  '--indices', '1'])
            self.assertIn('no ledger row 999', str(cm.exception))

    def test_refuses_when_a_live_row_overlaps(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            submission_ledger.ensure_ledger_dir(db)
            rid = submission_ledger.reserve_submission(
                db, tarball='x.tar', entry={'tarball': 'x.tar'},
                indices=[1, 2, 3])
            submission_ledger.attach_cluster(db, rid, jobsub_id='j',
                                             cluster_id='1')
            with self.assertRaises(SystemExit) as cm:
                submissions.main(['--db', db, 'resubmit', str(rid),
                                  '--indices', '2'])
            self.assertIn('refusing', str(cm.exception))
            self.assertIn('reconcile', str(cm.exception))

    def test_rejects_indices_on_a_draining_row(self):
        from utils import submissions
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            submission_ledger.ensure_ledger_dir(db)
            rid = submission_ledger.reserve_submission(
                db, tarball='x.tar',
                entry={'tarball': 'x.tar', 'input_pattern': 'dts.*.art'},
                indices=['dts.mu2e.a.v.art'])
            submission_ledger.close_row(db, rid, 'complete')
            with self.assertRaises(SystemExit) as cm:
                submissions.main(['--db', db, 'resubmit', str(rid),
                                  '--indices', '1'])
            self.assertIn('draining', str(cm.exception))
```

If `submissions.main()` does not accept an argv list, add `def main(argv=None)` and pass `argv` to `parse_args` — a subprocess-based test is not an option (see Global Constraints).

- [ ] **Step 9: Run the full suite**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`.

- [ ] **Step 10: Mutation-check the guard**

Temporarily make `_rows_blocking_indices` `return None` unconditionally, then run `TestResubmitOverlapGuard TestResubmitVerb`. Expected: FAIL. If it passes, the guard is unpinned — fix the tests. Restore and confirm green.

- [ ] **Step 11: Commit**

```bash
git add utils/submissions.py utils/submission_ledger.py utils/submit.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat: submissions resubmit — hand re-firing without a map file

The one genuine operator use of submit_map was re-firing specific failed
work. It now names a ledger row instead of a file; the entry comes from
the row's snapshot.

Guarded by _rows_blocking_indices, the scattered-set analog of
_slice_overlaps_ledger: any unsettled row covering part of the selection
refuses the resubmit and names itself, because deterministic payloads
make an unverified re-send duplicate physics. 'failed' blocks
deliberately — a non-zero jobsub_submit can still have created a cluster.
Mutation-checked: neutering the guard fails the suite.

Suite: <N> OK (skipped=1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 6: Delete the command

**Files:**
- Delete: `bin/submit_map`
- Modify: `utils/submit.py` (delete `submit_map:820`, `main:933`, `_check_token` if unused)
- Modify: `mcp/src/prodtools_mcp_write/runner.py:56` (drop `'bin/submit_map'` from `ALLOWED_ENTRY_POINTS`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: Task 5's `submissions resubmit` (this task must not run before it — deleting the command first leaves an operator capability gap).
- Produces: `utils/submit.py` is a library with no `main()`. Public surface: `SubmitOptions`, `submit_entry`, `enqueue_entry`, `parse_indices`, `parse_files`, `check_inputs` re-exports.

- [ ] **Step 1: Write the failing tests**

```python
class TestSubmitMapCommandRetired(unittest.TestCase):
    def test_bin_submit_map_is_gone(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        self.assertFalse((repo / 'bin' / 'submit_map').exists())

    def test_submit_map_function_is_gone(self):
        import utils.submit as submit
        self.assertFalse(hasattr(submit, 'submit_map'))

    def test_submit_py_has_no_cli(self):
        import utils.submit as submit
        self.assertFalse(hasattr(submit, 'main'))

    def test_engine_is_still_exported(self):
        import utils.submit as submit
        for name in ('SubmitOptions', 'submit_entry', 'enqueue_entry'):
            self.assertTrue(hasattr(submit, name), f"lost {name}")

    def test_runner_allowlist_drops_the_deleted_script(self):
        """ALLOWED_ENTRY_POINTS is a security allowlist; an entry naming a
        script that no longer exists is dead surface."""
        from prodtools_mcp_write.runner import ALLOWED_ENTRY_POINTS
        self.assertNotIn('bin/submit_map', ALLOWED_ENTRY_POINTS)
        self.assertIn('bin/submissions', ALLOWED_ENTRY_POINTS)
        self.assertIn('bin/json2jobdef', ALLOWED_ENTRY_POINTS)
```

There is **no** module-level `REPO_ROOT` in `test/test_unit.py` — the idiom is `pathlib.Path(__file__).resolve().parent.parent` (see `:9757`, `:9784`). `pathlib` is already imported.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestSubmitMapCommandRetired
```

Expected: FAIL — 3 failures; `test_engine_is_still_exported` passes.

- [ ] **Step 3: Delete the command and the CLI**

```bash
git rm bin/submit_map
```

In `utils/submit.py` delete `submit_map()` (`:820`), `main()` (`:933`), and the `if __name__ == '__main__':` block. Check whether `_check_token` has any remaining caller (it was called from `submit_map`); if not, delete it and its `httokendecode` import.

In `mcp/src/prodtools_mcp_write/runner.py:56`, drop `'bin/submit_map'` from `ALLOWED_ENTRY_POINTS`, leaving `'bin/json2jobdef'` and `'bin/submissions'`. This is a security allowlist guarding which repo scripts the write server may run as mu2epro; an entry naming a deleted script is dead surface. `test/test_unit.py:10079` asserts on this path — update it.

Grep for stragglers and fix each:

```bash
grep -rn "submit_map\|bin/submit_map" utils/ bin/ mcp/ test/ --include='*.py' --include='*.sh'
```

- [ ] **Step 4: Run the full suite**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`.

- [ ] **Step 5: Verify the tick still works end to end**

```bash
bash bin/submissions --db /exp/mu2e/data/users/oksuzian/prodtools/submissions.db status
bash bin/submissions --db /exp/mu2e/data/users/oksuzian/prodtools/submissions.db run --dry-run
```

Expected: both exit 0 and print campaign state. This runs against **your own** ledger, not production. Never wrap `submissions run` in `timeout` — it orphans a cluster.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor!: delete bin/submit_map

Every caller is gone: campaign creation moved to json2jobdef --prod
--enqueue, the four tick call sites call submit_entry in-process, and
hand re-firing moved to `submissions resubmit`. utils/submit.py is now a
library with no CLI.

Suite: <N> OK (skipped=1). `submissions run --dry-run` verified green
against a personal ledger.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 7: `map_path` → `origin`

**Files:**
- Modify: `utils/submission_ledger.py` (`_SCHEMA:114`, `_CAMPAIGN_SCHEMA:132`, `_connect`, `record_submission:181`, `reserve_submission:206`, `create_campaign:379`)
- Modify: `utils/submit.py` (`_reserve_in_ledger`, `enqueue_entry`'s `provenance` threading)
- Modify: `mcp/src/prodtools_mcp/tools/status.py:373,424`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: Task 6's library-only `utils/submit.py`.
- Produces: both ledger tables have an `origin` column; `reserve_submission(..., origin=...)` and `create_campaign(..., origin=...)`; MCP status replies carry `origin`.

- [ ] **Step 1: Write the failing tests**

```python
class TestOriginColumnMigration(unittest.TestCase):
    """map_path named a file that no longer exists. The column is free-text
    provenance; nothing dispatches from it."""

    def _columns(self, db, table):
        con = sqlite3.connect(db)
        try:
            return [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
        finally:
            con.close()

    def test_fresh_db_has_origin_not_map_path(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            submission_ledger.ensure_ledger_dir(db)
            submission_ledger.all_rows(db)
            for table in ('submissions', 'campaigns'):
                cols = self._columns(db, table)
                self.assertIn('origin', cols, table)
                self.assertNotIn('map_path', cols, table)

    def test_legacy_db_is_migrated_preserving_values(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE submissions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_utc TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'active',
                  attempt INTEGER NOT NULL DEFAULT 1,
                  parent_id INTEGER,
                  map_path TEXT, tarball TEXT NOT NULL,
                  entry_json TEXT NOT NULL, indices_json TEXT NOT NULL,
                  jobsub_id TEXT, cluster_id TEXT, closed_utc TEXT, note TEXT);
                INSERT INTO submissions
                  (created_utc, map_path, tarball, entry_json, indices_json)
                  VALUES ('2026-01-01T00:00:00Z', '/tmp/legacy.json',
                          'x.tar', '{}', '[]');
            """)
            con.commit()
            con.close()
            rows = submission_ledger.all_rows(db)
        self.assertIn('origin', self._columns(db, 'submissions'))
        self.assertEqual(rows[0]['origin'], '/tmp/legacy.json')

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'submissions.db')
            submission_ledger.ensure_ledger_dir(db)
            for _ in range(3):
                submission_ledger.all_rows(db)
            self.assertIn('origin', self._columns(db, 'submissions'))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestOriginColumnMigration
```

Expected: FAIL — the column is still `map_path`.

- [ ] **Step 3: Rename in both schemas**

In `utils/submission_ledger.py`, change `map_path     TEXT,` to `origin       TEXT,` in `_SCHEMA` and `_CAMPAIGN_SCHEMA`.

- [ ] **Step 4: Add the migration**

In `_connect`, after the schema `executescript` and before returning:

```python
    # map_path -> origin (2026-08-11). The column is free-text provenance;
    # the map file it used to name no longer exists. RENAME COLUMN needs
    # sqlite >= 3.25 (deployed: 3.34.1). Idempotent: PRAGMA-guarded, so a
    # DB created fresh from _SCHEMA is left alone.
    for table in ('submissions', 'campaigns'):
        cols = [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
        if 'map_path' in cols and 'origin' not in cols:
            con.execute(
                f'ALTER TABLE {table} RENAME COLUMN map_path TO origin')
    con.commit()
```

- [ ] **Step 5: Rename the API parameters**

In `record_submission`, `reserve_submission` and `create_campaign`, rename the `map_path=None` keyword to `origin=None` and update the INSERT column lists and value tuples.

In `utils/submit.py` `_reserve_in_ledger`, change `map_path=options.origin` to `origin=options.origin`. In `enqueue_entry`, change both `create_campaign(..., map_path=provenance)` calls to `origin=provenance`, and update the docstring line "recorded as the campaign's map_path" to "recorded as the campaign's origin".

- [ ] **Step 6: Update the MCP echoes**

In `mcp/src/prodtools_mcp/tools/status.py`, change `'map_path': camp['map_path'],` (`:373`) and `'map_path': c['map_path'],` (`:424`) to `'origin': ...['origin'],`.

- [ ] **Step 7: Run the full suite**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`. Fix `TestMcpCampaignStatus` (`:8482`) and `TestMcpListCampaigns` (`:9101`) if they assert on the `map_path` key.

- [ ] **Step 8: Verify the migration against a real ledger copy**

```bash
cp /exp/mu2e/data/users/oksuzian/prodtools/submissions.db /tmp/ledger-migration-check.db
python3 -c "
import sys; sys.path.insert(0, '.')
from utils import submission_ledger as L
rows = L.all_rows('/tmp/ledger-migration-check.db')
camps = L.active_campaigns('/tmp/ledger-migration-check.db')
print('rows', len(rows), 'campaigns', len(camps))
print('sample origin:', rows[-1]['origin'] if rows else '(none)')
"
```

Expected: counts match the pre-migration DB and `origin` carries the old `map_path` value. **Work on the copy** — never migrate the live ledger as a test.

- [ ] **Step 9: Commit**

```bash
git add utils/submission_ledger.py utils/submit.py mcp/src/prodtools_mcp/tools/status.py test/test_unit.py
git commit -m "$(cat <<'EOF'
refactor: ledger map_path column becomes origin

The column is free-text provenance and nothing dispatches from it, but
it named a file that no longer exists. Renamed in both tables via a
PRAGMA-guarded ALTER TABLE RENAME COLUMN (sqlite 3.34.1; needs 3.25+),
idempotent and value-preserving. Verified against a copy of a real
ledger.

Suite: <N> OK (skipped=1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Task 8: Documentation

**Files:**
- Modify: `docs/EXAMPLES_schema.md`
- Regenerate: `EXAMPLES.md`
- Modify: `CLAUDE.md`, `mcp/README.md`

**Interfaces:**
- Consumes: the finished code from Tasks 1–7.
- Produces: no map vocabulary anywhere in the docs.

- [ ] **Step 1: Update the schema**

In `docs/EXAMPLES_schema.md`: delete the `submit_map` section (section 11) and its entry from the tools list; add a `submissions resubmit` subsection under the submissions verbs; renumber so section numbering stays contiguous with no gaps.

Also fix `docs/EXAMPLES_schema.md:203`, which still says "The campaign's `map_path` records provenance as…" — the column is `origin` since Task 7. The derived `EXAMPLES.md:120` carries the same text and is fixed by the Step 2 regeneration. Task 7's own sweep missed both because it grepped `--include="*.py"`.

New error strings to document:
- `submissions: no ledger row <N> in <db>`
- `submissions: refusing — row <N> (state=...) already covers part of this selection`
- `submissions: row <N> is a draining (file-keyed) row — use --files, not --indices`
- `submissions: row <N> is an index row — use --indices, not --files`
- `Error: <path> must contain exactly one entry` (only reachable pre-Task-6; drop it if Task 6 has landed)

- [ ] **Step 2: Regenerate `EXAMPLES.md`**

Run `/refresh-examples`. **A full regeneration, overwriting the file — not incremental edits.** CLAUDE.md requires it, and the predecessor branch carries an outstanding deviation on exactly this point (commit `c859821` claimed a regeneration but made targeted edits); this run clears it. Say so plainly in the commit message.

- [ ] **Step 3: Spot-check the regenerated doc**

Pick five commands at random from the new `EXAMPLES.md`. For each flag in each one, confirm it exists in the current `argparse` — e.g. for a documented `submissions resubmit --indices`:

```bash
grep -n "add_argument('--indices'" utils/submissions.py
```

List every `submissions` and `json2jobdef` flag at once to check against:

```bash
grep -rhn "add_argument('--" utils/submissions.py utils/json2jobdef.py | sed "s/.*add_argument('\(--[a-z-]*\)'.*/\1/" | sort -u
```

Then confirm the retired surface is absent:

```bash
grep -n "submit_map\|--jobdefs\|map file\|map_path\|--no-ledger" EXAMPLES.md
```

Expected: no matches.

- [ ] **Step 3b: Sweep stale in-code references to deleted symbols**

Task 4's review found live comments naming a function that no longer exists. Fix each:

- `utils/submission_ledger.py:501` — says `(submissions.recovery_resource_argv)`; the function is now `recovery_resource_kwargs`. The same docstring also says `resubmit` "rebuilds its map from `row['entry']`" — there is no map; it builds `SubmitOptions`.
- `utils/jobsub_argv.py:35` — comment names `recovery_resource_argv`.

**Rename the `submit_map:` error prefixes.** `enqueue_entry` emits seven operator-facing messages prefixed with the name of the deleted command (`utils/submit.py:383, 413, 429, 439, 445, 448, 461`). Its only caller is now `json2jobdef`, so the prefix should be `json2jobdef:`. This is not cosmetic: `:439` is `"submit_map: inputs not ready"` — the message an operator sees when a campaign's inputs are bad, which is the exact failure class that cost campaign 54 half its jobs. Telling them to go look at a command that does not exist wastes the one moment the message matters.

`enqueue_entry`'s `sys.exit` *protocol* stays exactly as it is — only the prefix string changes.

EXAMPLES.md documents these strings, so the Step 2 regeneration must reflect the new prefix. Grep the schema for `submit_map:` before regenerating.

Then confirm nothing else dangles:

```bash
grep -rn "recovery_resource_argv\|_scratch_map_dir\|SUBMIT_MAP\|submit_entry_direct" utils/ bin/ mcp/src/ test/
grep -rn "submit_map" utils/ bin/ mcp/src/ test/
```

Expected: no matches from either. The second is broader than the first on purpose — Task 6 left narrative `submit_map` prose in `utils/submissions.py`, `utils/submission_ledger.py`, `utils/runmu2e.py`, `mcp/src/prodtools_mcp_write/tools.py`, and `utils/submit.py`'s own docstrings, all correctly out of its scope and all owned here. Rewrite each to describe what the code does now; do not simply delete the sentence if it was explaining something real.

- [ ] **Step 3c: Fix the live skill docs in `.claude/commands/`**

These are **instructions future sessions follow**, not prose. Leaving them stale means a later session is told to run a command that no longer exists. Four files reference `submit_map`:

- **`.claude/commands/mu2epro-submit.md` (10 references)** — this skill exists *solely* to run `submit_map` as mu2epro. Its entire premise is gone. Rewrite it around `submissions resubmit` (hand re-firing) and `submissions run` (feeding campaigns), or delete it and fold what survives into `/mu2epro-run`. Decide which, and say why in the commit message.
- **`.claude/commands/mu2epro-run.md` (2)** — also still carries a "HARD RULE: `--jobdefs` is mandatory for `json2jobdef --prod`". That flag was deleted in `c859821`; the rule is now actively wrong and must go. Replace with `--prod` requiring `--enqueue`.
- **`.claude/commands/mu2ejobsub-submit.md` (7)** — check whether this skill is already dead: the `mu2ejobsub` backend was retired 2026-07-19. If it is, delete it; if not, drop only the `submit_map` references.
- **`.claude/commands/jit-cnf-build.md` (3)** — drop or retarget the references.

**Do NOT rewrite `wiki/` or `docs/superpowers/plans/`.** Those are historical records — a wiki page describing what the workflow was in May 2026 is *correct* as written, and editing it to match today's code destroys the record. Only live instructions get updated.

- [ ] **Step 4: Update `CLAUDE.md` and `mcp/README.md`**

In `CLAUDE.md`: drop `enqueue_campaign` from the write-server tool list (leaving `push_cnf` and `run_submissions`), and remove the "No map file is involved" phrasing — with no map anywhere, the disclaimer is itself a reference to a dead concept.

In `mcp/README.md`: same tool-list correction, delete the "`enqueue_campaign` remains the tool for a map that already exists" paragraph, and reword the `push_cnf` description to drop "No map file is involved".

- [ ] **Step 5: Run the full suite once more**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | tail -5
```

Expected: `OK (skipped=1)`.

- [ ] **Step 6: Commit**

```bash
git add docs/EXAMPLES_schema.md EXAMPLES.md CLAUDE.md mcp/README.md
git commit -m "$(cat <<'EOF'
docs: drop the map vocabulary; regenerate EXAMPLES.md

Schema loses the submit_map section and gains `submissions resubmit`.
EXAMPLES.md is a FULL regeneration from source per /refresh-examples,
not incremental edits — this also clears the deviation left by c859821,
which claimed a regeneration it did not perform.

CLAUDE.md and mcp/README.md lose enqueue_campaign and the "no map file
is involved" disclaimers, which were themselves references to a concept
that no longer exists.

Suite: <N> OK (skipped=1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Final acceptance (after Task 8)

Not a task — the operator runs these, since two need mu2epro.

- [ ] `submissions status` and `submissions run --dry-run` against a personal ledger, exit 0.
- [ ] `submissions resubmit <row-id> --indices <n> --dry-run` against a **closed** row, exit 0 and prints the intended submission.
- [ ] `submissions resubmit <row-id> --indices <n>` against a row that overlaps a live one — must REFUSE and name the blocking row.
- [ ] One `json2jobdef --prod --enqueue` as mu2epro (via `/mu2epro-run`), campaign appears in `list_campaigns`.
- [ ] `grep -rn "submit_map\|map_path\|--jobdefs" utils/ bin/ mcp/ EXAMPLES.md CLAUDE.md` returns **only these four expected hits** — anything else is a miss:
  - `utils/submission_ledger.py` — the `map_path` → `origin` migration must name the old column.
  - `mcp/src/prodtools_mcp/ledger_ro.py` — the read-only compat shim, same reason.
  - `EXAMPLES.md` (two sites) — `--jobdefs` inside sentences saying it no longer exists.

  `test/` is excluded from this grep on purpose: the retirement tests and legacy-schema fixtures need the literal strings as test *data*, not as prose.

**Never wrap `submissions run` in `timeout`** — it orphans a cluster.
