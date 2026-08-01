# Draining Campaigns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Growing-dataset (POMS-draining analog) support on the direct/ledger backend: pattern-keyed campaigns whose tick dispatches 1:1 direct-input jobs for input files that don't yet have outputs in SAM.

**Architecture:** A draining campaign is a `campaigns` row whose `entry_json` carries `input_pattern` and no `njobs`. Each `submissions run` tick computes `pending = inputs − landed − in-flight − parked` from SAM + the ledger, gates the candidate batch (settling age, dCache residency), and submits it via `submit_map --files`. The worker runs the already-existing `process_direct_input` path; verification is file-keyed by the same `job_outputs` name mapping the worker uses.

**Tech Stack:** Python 3 stdlib (sqlite3, argparse, unittest), samweb via `utils/samweb_wrapper`, mdh via `utils/check_inputs` helpers.

**Spec:** `docs/superpowers/specs/2026-08-01-draining-campaigns-design.md` — read it once before Task 1 if anything below seems ambiguous; the spec governs.

## Global Constraints

- **No DDL.** `submissions.db` schema is untouched: draining campaigns reuse `campaigns` (cursor stays 0, unused) and `submissions` (`indices_json` holds a JSON list of filenames for draining rows).
- **Kind discrimination is `is_draining(entry)`** (presence of `input_pattern` in the entry dict) — never sniff `indices_json` content types.
- **Fail-closed everywhere:** unknown queue count / SAM error / mdh error / unknown file age → no dispatch, no state change, one report line. A row or campaign is never guessed complete; pending is never guessed.
- **One name-mapping home:** `job_common.expected_outputs_for(input_fname, job_pars)` delegating to `job_pars.job_outputs(0, override_desc=…, override_seq=…)` — the exact worker-side substitution. Verifier, dispatcher, and worker share it; never re-derive output names elsewhere.
- **No SAM definitions, snapshots, or consumption state** are ever created or read.
- **exclude_desc is exact-match** on `Mu2eName.description` — never substring/prefix (`FlatGamma` must not drop `FlatGammaCalo`).
- Draining defaults: `min_age_minutes` **60**, `prestage` **false**. `DEFAULT_MAX_ATTEMPTS` (3), `DEFAULT_MAX_QUEUED` (5000), `RECOVERY_MEMORY` ('4000MB'), `RECOVERY_LIFETIME` ('48h') unchanged.
- Tests live in `test/test_unit.py` (unittest style, `unittest.mock.patch`, injectable callables, zero network). Full suite must pass at every commit: `python -m pytest test/test_unit.py -q`.
- `EXAMPLES.md` is derived — never hand-edit; only `docs/EXAMPLES_schema.md` changes here (regen deferred, see Task 8).
- Commit messages: `feat:`/`fix:`/`docs:` prefix, body says WHY, footer:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF`.

## File Structure

| File | Change |
|---|---|
| `utils/poms_entry.py` | add `is_draining(entry)` (single-owner discriminator) |
| `utils/job_common.py` | add module function `expected_outputs_for(input_fname, job_pars)` after the `Mu2eJobBase` class |
| `utils/submit.py` | enqueue branch for draining entries; `--files` flag + `_parse_files`; files branch in `submit_entry_direct`; files-aware `_record_in_ledger`/`_log_submission`; draining guards in `submit_map` |
| `utils/jobsub_argv.py` | `build_ops_json(..., files=None)` |
| `utils/runmu2e.py` | `_direct_dispatch` files branch (worker) |
| `utils/submissions.py` | `_dataset_of`, `draining_state`, `_gate_batch`, `_request_prestage`, `submit_drain_batch`, `drain_tick`, `verify_files_row`, `resubmit_files`; kind dispatch in `process_row`; skip in `top_up` + `_slice_overlaps_ledger`; `drain_tick` wiring in `_run_pass`; draining lines in `print_status`; `complete` verb |
| `docs/EXAMPLES_schema.md`, `wiki/pages/2026-07-18-direct-recovery-loop.md` | docs (Task 8) |
| `test/test_unit.py` | new test classes per task, appended at the end of the file |

---

### Task 1: Foundations — `is_draining` + `expected_outputs_for`

**Files:**
- Modify: `utils/poms_entry.py` (after `firstjob_of`, ~line 90)
- Modify: `utils/job_common.py` (module level, after the class containing `job_outputs`, ~line 527)
- Test: `test/test_unit.py` (append)

**Interfaces:**
- Consumes: `Mu2eName.parse` (job_common), `Mu2eJobBase.job_outputs(index, override_desc=None, override_seq=None) -> Dict[str, str]` (job_common.py:487).
- Produces: `poms_entry.is_draining(entry: dict) -> bool`; `job_common.expected_outputs_for(input_fname: str, job_pars) -> List[str]` (sorted, Mu2e-named only; raises `ValueError` on malformed input name, `RuntimeError` when no Mu2e-named outputs). Every later task uses these two names verbatim.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# 43. Draining campaigns: foundations (is_draining, expected_outputs_for)
# ---------------------------------------------------------------------------

class TestIsDraining(unittest.TestCase):
    """Campaign/row kind is discriminated ONLY by input_pattern presence."""

    def test_pattern_entry_is_draining(self):
        from utils.poms_entry import is_draining
        self.assertTrue(is_draining(
            {'tarball': 't', 'input_pattern': 'dig.mu2e.%.X.art'}))

    def test_index_entry_is_not(self):
        from utils.poms_entry import is_draining
        self.assertFalse(is_draining({'tarball': 't', 'njobs': 100}))


class TestExpectedOutputsFor(unittest.TestCase):
    """The single input->output name mapping, delegating to job_outputs
    (the exact worker-side substitution) so verifier and worker cannot
    drift."""

    IN = 'dig.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.001202_00000042.art'

    class FakePars:
        def __init__(self, out):
            self.out = out
            self.calls = []

        def job_outputs(self, index, override_desc=None, override_seq=None):
            self.calls.append((index, override_desc, override_seq))
            return self.out

    def test_delegates_desc_and_sequencer_from_input_name(self):
        from utils.job_common import expected_outputs_for
        jp = self.FakePars({'Output':
            'mcs.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.001202_00000042.art'})
        outs = expected_outputs_for(self.IN, jp)
        self.assertEqual(jp.calls, [(0, 'CosmicCRYAllOnSpill',
                                     '001202_00000042')])
        self.assertEqual(outs, ['mcs.mu2e.CosmicCRYAllOnSpill.'
                                'MDC2025au_best_v1_5.001202_00000042.art'])

    def test_filters_non_mu2e_streams_and_sorts(self):
        from utils.job_common import expected_outputs_for
        jp = self.FakePars({'b': 'nts.mu2e.X.C.000_000.root',
                            'null': '/dev/null',
                            'a': 'mcs.mu2e.X.C.000_000.art'})
        self.assertEqual(expected_outputs_for(self.IN, jp),
                         ['mcs.mu2e.X.C.000_000.art',
                          'nts.mu2e.X.C.000_000.root'])

    def test_dataset_name_rejected(self):
        from utils.job_common import expected_outputs_for
        with self.assertRaises(ValueError):
            expected_outputs_for('dig.mu2e.X.C.art', self.FakePars({}))

    def test_junk_name_rejected(self):
        from utils.job_common import expected_outputs_for
        with self.assertRaises(ValueError):
            expected_outputs_for('not-a-mu2e-name', self.FakePars({}))

    def test_no_outputs_is_a_hard_error(self):
        from utils.job_common import expected_outputs_for
        with self.assertRaises(RuntimeError):
            expected_outputs_for(self.IN, self.FakePars({'n': '/dev/null'}))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest test/test_unit.py::TestIsDraining test/test_unit.py::TestExpectedOutputsFor -v`
Expected: FAIL — `ImportError: cannot import name 'is_draining'` / `'expected_outputs_for'`.

- [ ] **Step 3: Implement**

`utils/poms_entry.py`, after `firstjob_of`:

```python
def is_draining(entry: dict) -> bool:
    """True for a draining (input_pattern) entry/campaign/row snapshot.

    The single-owner kind discriminator for the direct backend: a
    draining entry has `input_pattern` and no index space (no njobs/
    firstjob). Callers must never sniff indices_json content instead.
    """
    return 'input_pattern' in entry
```

`utils/job_common.py`, module level, right after the class that defines `job_outputs` (`os` is already imported there; if not, use `Path` from the module's existing imports for the basename):

```python
def expected_outputs_for(input_fname, job_pars):
    """Expected output filenames for one direct-input (draining) job.

    THE single home for the input->output name mapping: delegates to
    job_outputs(0, override_desc=, override_seq=) — the exact
    substitution process_direct_input performs on the worker — so the
    dispatcher, the verifier, and the worker cannot drift. Non-Mu2e-
    named streams (paths like /dev/null) are dropped, mirroring
    submit._read_cnf_facts. Raises ValueError on a malformed input name
    and RuntimeError when the cnf yields no Mu2e-named outputs (fail
    loud, never guess).
    """
    n = Mu2eName.parse(os.path.basename(input_fname))
    if not n.is_file:
        raise ValueError(f"not a Mu2e file name: {input_fname}")
    out = job_pars.job_outputs(0, override_desc=n.description,
                               override_seq=n.sequencer) or {}
    names = sorted(v for v in out.values() if v and '/' not in v)
    if not names:
        raise RuntimeError(f"no Mu2e-named outputs in cnf for {input_fname}")
    return names
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestIsDraining test/test_unit.py::TestExpectedOutputsFor -v` → PASS, then `python -m pytest test/test_unit.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add utils/poms_entry.py utils/job_common.py test/test_unit.py
git commit -m "feat(draining): is_draining discriminator + expected_outputs_for name mapping"
```

---

### Task 2: Enqueue path for draining entries

**Files:**
- Modify: `utils/submit.py` — `_enqueue_entries` (line 227), the no-njobs filter in `submit_map` (lines 586-595), new `_validate_draining_entry` before `_enqueue_entries`
- Test: `test/test_unit.py` (append)

**Interfaces:**
- Consumes: `poms_entry.is_draining` (Task 1), `submission_ledger.create_campaign`, `_snapshot_entry`, `_effective_resources`, `_ensure_local_tarball`.
- Produces: `_validate_draining_entry(entry) -> Optional[str]` (error string or None). `submit_map --map drain.json --enqueue --slice-size N` creates a draining campaign; draining entries survive the multi-entry no-njobs filter; a draining entry without `--enqueue`/`--files` is refused (the `--files` half arrives in Task 3 — until then the refusal message already names it).

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------
# 44. Draining campaigns: enqueue validation
# ---------------------------------------------------------------------------

class TestValidateDrainingEntry(unittest.TestCase):
    BASE = {'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
            'inloc': 'tape',
            'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art',
            'outputs': [{'dataset': '*.art', 'location': 'tape'}]}

    def _err(self, **over):
        from utils.submit import _validate_draining_entry
        return _validate_draining_entry({**self.BASE, **over})

    def test_valid_entry_passes(self):
        self.assertIsNone(self._err())

    def test_njobs_and_pattern_conflict(self):
        self.assertIn('njobs', self._err(njobs=100))

    def test_firstjob_rejected(self):
        self.assertIn('firstjob', self._err(firstjob=500))

    def test_pattern_must_be_five_fields(self):
        self.assertIn('5-field', self._err(input_pattern='dig.mu2e.%.art'))

    def test_missing_required_key(self):
        entry = {k: v for k, v in self.BASE.items() if k != 'outputs'}
        from utils.submit import _validate_draining_entry
        self.assertIn('outputs', _validate_draining_entry(entry))

    def test_exclude_desc_must_be_string_list(self):
        self.assertIn('exclude_desc', self._err(exclude_desc='NoPrimary'))

    def test_min_age_must_be_nonnegative_int(self):
        self.assertIn('min_age', self._err(min_age_minutes=-5))

    def test_prestage_must_be_bool(self):
        self.assertIn('prestage', self._err(prestage='yes'))


class TestEnqueueDraining(unittest.TestCase):
    """--enqueue on a draining entry creates a campaign with the
    snapshotted entry; check_inputs is skipped (a generic cnf bakes no
    inputs — the tick gates each batch instead)."""

    ENTRY = {'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
             'inloc': 'tape',
             'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art',
             'outputs': [{'dataset': '*.art', 'location': 'tape'}]}

    def _opts(self, **over):
        from argparse import Namespace
        base = dict(dry_run=False, slice_size=500, ledger_db='/x.db',
                    memory=None, disk=None, expected_lifetime=None)
        base.update(over)
        return Namespace(**base)

    def test_creates_campaign_without_check_inputs(self):
        from utils import submit
        created = {}

        def fake_create(db, *, tarball, entry, slice_size, map_path):
            created.update(tarball=tarball, entry=entry,
                           slice_size=slice_size)
            return 48

        with patch.object(submit, '_ensure_local_tarball',
                          return_value='/tmp/t.tar'), \
             patch.object(submit, 'check_inputs') as ci, \
             patch.object(submit.submission_ledger, 'create_campaign',
                          fake_create):
            ids = submit._enqueue_entries([(0, dict(self.ENTRY))],
                                          '/m.json', self._opts())
        self.assertEqual(ids, [48])
        ci.assert_not_called()
        self.assertEqual(created['slice_size'], 500)
        self.assertEqual(created['entry']['input_pattern'],
                         self.ENTRY['input_pattern'])

    def test_invalid_draining_entry_exits(self):
        from utils import submit
        bad = dict(self.ENTRY, njobs=100)
        with patch.object(submit, '_ensure_local_tarball',
                          return_value='/tmp/t.tar'):
            with self.assertRaises(SystemExit):
                submit._enqueue_entries([(0, bad)], '/m.json', self._opts())

    def test_dry_run_creates_nothing(self):
        from utils import submit
        with patch.object(submit, '_ensure_local_tarball',
                          return_value='/tmp/t.tar'), \
             patch.object(submit.submission_ledger,
                          'create_campaign') as cc:
            ids = submit._enqueue_entries([(0, dict(self.ENTRY))],
                                          '/m.json',
                                          self._opts(dry_run=True))
        self.assertEqual(ids, [])
        cc.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/test_unit.py::TestValidateDrainingEntry test/test_unit.py::TestEnqueueDraining -v`
Expected: FAIL — `_validate_draining_entry` not defined; SystemExit "has no njobs (generic tarball)" from the existing enqueue path.

- [ ] **Step 3: Implement in `utils/submit.py`**

Extend the poms_entry import (line 34):

```python
from utils.poms_entry import (tarball_of, outputs_of, njobs_of, inloc_of,
                              firstjob_of, validate_window, resources_of,
                              is_draining)
```

Add before `_enqueue_entries`:

```python
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
```

In `_enqueue_entries`, at the top of the `for idx, entry in entries_to_submit:` loop body, insert the draining branch (the existing body becomes the `else` path unchanged — use `continue` to keep the diff flat):

```python
    for idx, entry in entries_to_submit:
        if is_draining(entry):
            err = _validate_draining_entry(entry)
            if err:
                sys.exit(f"submit_map: entry {idx} {err}")
            _ensure_local_tarball(tarball_of(entry))
            # No check_inputs: a generic cnf bakes no inputs — the tick
            # gates every batch (residency + settling age) at dispatch.
            snap = _snapshot_entry(entry, _effective_resources(entry, opts))
            if opts.dry_run:
                print(f"[DRY RUN] would enqueue draining campaign: "
                      f"{tarball_of(entry)} "
                      f"pattern={entry['input_pattern']} "
                      f"slice={opts.slice_size}")
                continue
            try:
                camp_id = submission_ledger.create_campaign(
                    opts.ledger_db, tarball=tarball_of(entry), entry=snap,
                    slice_size=opts.slice_size, map_path=map_path)
            except (ValueError, sqlite3.Error) as e:
                sys.exit(f"submit_map: {e}")
            print(f"Enqueued draining campaign {camp_id}: "
                  f"{tarball_of(entry)} pattern={entry['input_pattern']} "
                  f"slice={opts.slice_size} (db {opts.ledger_db})")
            ids.append(camp_id)
            continue
        tarball_path = _ensure_local_tarball(tarball_of(entry))
        ...existing body unchanged...
```

In `submit_map`, fix the multi-entry filter (line 591) so draining entries are not dropped, and refuse draining entries outside `--enqueue` (insert right before the `if getattr(opts, 'enqueue', False):` branch at line 601):

```python
    if len(entries_to_submit) > 1:
        filtered = []
        for idx, entry in entries_to_submit:
            if njobs_of(entry) is None and not is_draining(entry):
                print(f"[INFO] Skipping entry {idx} "
                      f"({entry.get('tarball', '?')}): no njobs "
                      f"(generic tarball)")
                continue
            filtered.append((idx, entry))
        entries_to_submit = filtered
```

```python
    if not getattr(opts, 'enqueue', False):
        for idx, entry in entries_to_submit:
            if is_draining(entry) and getattr(opts, 'files', None) is None:
                print(f"Error: entry {idx} is a draining entry "
                      f"(input_pattern) — use --enqueue (tick-fed) or "
                      f"--files <list>")
                sys.exit(1)
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestValidateDrainingEntry test/test_unit.py::TestEnqueueDraining -v` → PASS; `python -m pytest test/test_unit.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "feat(draining): enqueue input_pattern entries as draining campaigns"
```

---

### Task 3: `--files` dispatch path

**Files:**
- Modify: `utils/submit.py` — argparse in `main()` (after `--indices-file`, line 663), `_parse_files` (new, after `_parse_indices`), `submit_entry_direct` (lines 400-535), `_record_in_ledger` (134), `_log_submission` (173), the `--files` guard in `submit_map`
- Modify: `utils/jobsub_argv.py` — `build_ops_json` (line 157)
- Modify: `utils/submissions.py` — `_slice_overlaps_ledger` (line 386) skip for file-keyed rows; extend the poms_entry import (line 39) to `from utils.poms_entry import njobs_of, is_draining`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `is_draining`, `expected_outputs_for` (Task 1), `Mu2eName.dataset` property, `Mu2eJobPars` (utils/jobquery).
- Produces: CLI `submit_map --map M --files LIST.txt`; `_parse_files(path) -> Optional[List[str]]` (sorted unique, each a 6-field Mu2e file name, `#` comments; `ValueError` on junk/empty); `opts.files` holds the parsed list (None otherwise); `build_ops_json(entry=, jobset=, input_datasets=, files=None)` adds `"files": [...]` when given; ledger rows for files submissions store the filename list in `indices_json`. Task 6's `resubmit_files` and Task 7's `submit_drain_batch` shell out to exactly `submit_map --map <m> --files <f> [--ledger-parent N] --ledger-db <db>`.

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------
# 45. Draining campaigns: --files dispatch
# ---------------------------------------------------------------------------

class TestParseFiles(unittest.TestCase):
    F1 = 'dig.mu2e.A.MDC2025au_best_v1_5.001202_00000001.art'
    F2 = 'dig.mu2e.B.MDC2025au_best_v1_5.001202_00000002.art'

    def _parse(self, text):
        from utils.submit import _parse_files
        with tempfile.NamedTemporaryFile('w', suffix='.txt',
                                         delete=False) as fh:
            fh.write(text)
        try:
            return _parse_files(fh.name)
        finally:
            os.unlink(fh.name)

    def test_none_passthrough(self):
        from utils.submit import _parse_files
        self.assertIsNone(_parse_files(None))

    def test_sorted_unique_with_comments(self):
        got = self._parse(f"# header\n{self.F2}\n{self.F1}\n{self.F2}\n")
        self.assertEqual(got, [self.F1, self.F2])

    def test_junk_name_raises(self):
        with self.assertRaises(ValueError):
            self._parse("not-a-name\n")

    def test_dataset_name_raises(self):
        with self.assertRaises(ValueError):
            self._parse("dig.mu2e.A.MDC2025au_best_v1_5.art\n")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            self._parse("# only a comment\n")


class TestBuildOpsJsonFiles(unittest.TestCase):
    def test_files_key_present_only_when_given(self):
        from utils.jobsub_argv import build_ops_json
        entry = {'tarball': 't', 'inloc': 'tape',
                 'outputs': [{'dataset': '*.art', 'location': 'tape'}]}
        ops = build_ops_json(entry=entry, jobset=[0, 1],
                             input_datasets=['dig.mu2e.A.C.art'],
                             files=['f1.art', 'f2.art'])
        self.assertEqual(ops['files'], ['f1.art', 'f2.art'])
        ops2 = build_ops_json(entry=entry, jobset=[0, 1],
                              input_datasets=['dig.mu2e.A.C.art'])
        self.assertNotIn('files', ops2)


class TestSubmitEntryDirectFiles(unittest.TestCase):
    """Files mode: jobset = positions, ledger row stores filenames,
    scopes derive from the mapped outputs of the batch."""

    ENTRY = {'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
             'inloc': 'tape',
             'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art',
             'outputs': [{'dataset': '*.art', 'location': 'tape'}]}
    FILES = ['dig.mu2e.A.MDC2025au_best_v1_5.001202_00000001.art',
             'dig.mu2e.B.MDC2025au_best_v1_5.001202_00000002.art']

    class FakePars:
        def __init__(self, path):
            pass

        def job_outputs(self, index, override_desc=None, override_seq=None):
            return {'Output': f'mcs.mu2e.{override_desc}.'
                              f'MDC2025au_best_v1_5.{override_seq}.art'}

    def _opts(self, **over):
        from argparse import Namespace
        base = dict(dry_run=False, files=list(self.FILES), indices=None,
                    first=None, num=None, memory=None, disk=None,
                    expected_lifetime=None, role=None, wftop=None,
                    wfproject=None, prodtools_tar=None, no_ledger=False,
                    ledger_db='/x.db', ledger_parent=None, map='/m.json')
        base.update(over)
        return Namespace(**base)

    def test_files_submission_records_filenames_in_ledger(self):
        from utils import submit
        recorded = {}

        def fake_record(db, *, tarball, entry, indices, jobsub_id,
                        cluster_id, map_path=None, parent_id=None):
            recorded['indices'] = indices
            return 99

        with patch.object(submit, '_ensure_local_tarball',
                          return_value=Path('/tmp/t.tar')), \
             patch('utils.jobquery.Mu2eJobPars', self.FakePars), \
             patch.object(submit, '_bundle_prodtools',
                          return_value=Path('/tmp/pt.tar')), \
             patch.object(submit, '_run_submit',
                          return_value={'tarball': self.ENTRY['tarball'],
                                        'cluster_id': '123',
                                        'jobsub_id': '123.0@s',
                                        'njobs': 2, 'status': 'submitted',
                                        'raw_output': ''}) as rs, \
             patch.object(submit, '_log_submission'), \
             patch.object(submit.submission_ledger, 'record_submission',
                          fake_record):
            result = submit.submit_entry_direct(dict(self.ENTRY), 0,
                                                self._opts())
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(recorded['indices'], self.FILES)
        # the jobsub argv references an ops JSON (shipped via dropbox)
        cmd = rs.call_args[0][0]
        self.assertEqual(cmd[0], 'jobsub_submit')
        self.assertTrue(any('ops-' in c for c in cmd))

    def test_files_dry_run_submits_nothing(self):
        from utils import submit
        with patch.object(submit, '_ensure_local_tarball',
                          return_value=Path('/tmp/t.tar')), \
             patch('utils.jobquery.Mu2eJobPars', self.FakePars), \
             patch.object(submit, '_run_submit') as rs:
            result = submit.submit_entry_direct(dict(self.ENTRY), 0,
                                                self._opts(dry_run=True))
        self.assertEqual(result['status'], 'dry_run')
        self.assertEqual(result['njobs'], 2)
        rs.assert_not_called()


class TestSliceOverlapSkipsFileRows(unittest.TestCase):
    def test_file_keyed_row_never_matches_an_index_window(self):
        from utils import submissions
        row = {'tarball': 'cnf.mu2e.reco.X.0.tar',
               'entry': {'input_pattern': 'dig.mu2e.%.X.art'},
               'indices': ['dig.mu2e.A.X.001202_00000001.art']}
        with patch.object(submissions.submission_ledger, 'all_rows',
                          return_value=[row]):
            self.assertFalse(submissions._slice_overlaps_ledger(
                '/x.db', 'cnf.mu2e.reco.X.0.tar', 0, 0, 100))
```

Note for the implementer: `tempfile`, `os`, `Path`, `patch` are already imported at the top of `test/test_unit.py` — check and reuse; do not re-import mid-file.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/test_unit.py::TestParseFiles test/test_unit.py::TestBuildOpsJsonFiles test/test_unit.py::TestSubmitEntryDirectFiles test/test_unit.py::TestSliceOverlapSkipsFileRows -v`
Expected: FAIL — `_parse_files` missing, `build_ops_json` rejects `files` kwarg, `submit_entry_direct` TypeErrors on the entry (no njobs), file-row overlap raises `TypeError: '<=' not supported between 'int' and 'str'`.

- [ ] **Step 3: Implement**

`utils/jobsub_argv.py` — replace `build_ops_json`:

```python
def build_ops_json(*, entry, jobset, input_datasets, files=None):
    """Worker-side ops JSON. Top-level keys:

    - `jobs`: PROCESS → real-job-index lookup table (replaces `mu2ejobmap`)
    - `inspec`: per-input-dataset (protocol, location)
    - `jobdesc`: single-element POMS-map entry, consumed by
      `runmu2e._direct_dispatch` via `process_jobdef`
    - `files` (draining batches only): job index → input art filename;
      the worker runs process_direct_input on files[index]
    """
    ops = {
        "jobs": list(jobset),
        "inspec": build_inspec(input_datasets, inloc_of(entry)),
        "jobdesc": [dict(entry)],
    }
    if files is not None:
        ops["files"] = list(files)
    return ops
```

`utils/submit.py`:

1. Extend the job_common import (line 33):

```python
from utils.job_common import (Mu2eName, log_storage_location,
                              expected_outputs_for)
```

2. Add `_parse_files` after `_parse_indices` (line 349):

```python
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
```

3. In `main()`, add after `--indices-file` (line 666):

```python
    parser.add_argument('--files', default=None,
                        help='File of input art filenames (one per line, '
                             '`#` comments) for a draining '
                             '(input_pattern) entry: one 1:1 direct-'
                             'input job per file. Written by the '
                             'submissions drain tick; also the operator '
                             'path for re-dispatching parked files.')
```

and after the `args.indices` parse block (line 720):

```python
    try:
        args.files = _parse_files(args.files)
    except (ValueError, OSError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    if args.files is not None and (
            args.first is not None or args.num is not None
            or args.indices is not None or args.enqueue):
        print("Error: --files cannot be combined with "
              "--first/--num/--indices/--indices-file/--enqueue")
        sys.exit(1)
```

4. In `submit_map`, extend the Task-2 guard block to validate the `--files` target:

```python
    if getattr(opts, 'files', None) is not None:
        if len(entries_to_submit) != 1:
            print("Error: --files requires exactly one entry "
                  "(use --entry N on a multi-entry map)")
            sys.exit(1)
        if not is_draining(entries_to_submit[0][1]):
            print("Error: --files requires a draining (input_pattern) "
                  "entry")
            sys.exit(1)
```

5. In `submit_entry_direct`, replace the facts/jobset block (lines 416-435) with a three-way branch. `firstjob`/`jobset` assignments move inside each branch:

```python
    files = getattr(opts, 'files', None)

    # njobs from the cnf is authoritative; POMS-map's field is informational.
    # output_filenames feeds the per-(area, tier, owner) token scope derivation
    # so pushOutput can MAKE_PARENT in `/pnfs/mu2e/<area>/datasets/...`.
    if files is not None:
        # Draining batch: one direct-input job per file. A generic cnf
        # has no index capacity — the jobset is positions into the
        # batch. Scope granularity is (area, tier, owner); desc plays no
        # role, so the FIRST file's mapped outputs cover the whole
        # batch's scopes (expected_outputs_for is the worker's own
        # substitution, so the names are exact).
        from utils.jobquery import Mu2eJobPars
        jp = Mu2eJobPars(str(tarball_path))
        njobs_total = len(files)
        input_datasets = sorted({str(Mu2eName.parse(f).dataset)
                                 for f in files})
        output_filenames = expected_outputs_for(files[0], jp)
        firstjob = 0
        jobset = list(range(len(files)))
    elif opts.dry_run and not tarball_path.is_file():
        ...existing stand-in block unchanged...
        firstjob = firstjob_of(entry)
        jobset = _compute_jobset(opts, njobs_total, firstjob=firstjob,
                                 entry_njobs=njobs_of(entry))
    else:
        njobs_total, input_datasets, output_filenames = \
            _read_cnf_facts(tarball_path)
        firstjob = firstjob_of(entry)
        jobset = _compute_jobset(opts, njobs_total, firstjob=firstjob,
                                 entry_njobs=njobs_of(entry))
```

(The existing `firstjob = firstjob_of(entry)` / `jobset = _compute_jobset(...)` lines at 433-435 are absorbed into the branches above — delete the originals.)

6. Branch the banner print (lines 437-447):

```python
    print(f"\n{'='*60}")
    if files is not None:
        print(f"Entry {idx}: {desc} (draining batch of {len(files)})")
        print(f"  tarball: {tarball_name}")
        print(f"  inloc:   {inloc_of(entry)}")
        print(f"  files:   {files[0]} .. {files[-1]}")
    else:
        print(f"Entry {idx}: {desc} (cnf njobs={njobs_total}, "
              f"submitting {len(jobset)})")
        ...existing lines unchanged...
    print(f"{'='*60}")
```

7. The ops build (line 462) gains the kwarg:

```python
    ops = _jobsub_argv.build_ops_json(
        entry=ops_entry,
        jobset=jobset,
        input_datasets=input_datasets,
        files=files,
    )
```

8. The ledger/log calls (lines 530-534) pass files through:

```python
    if not opts.no_ledger:
        _log_submission(firstjob, jobset, result, opts, files=files)
    if result['status'] == 'submitted' and not opts.no_ledger:
        _record_in_ledger(_snapshot_entry(entry, resources), firstjob,
                          jobset, result, opts, files=files)
```

9. `_record_in_ledger` — payload branch:

```python
def _record_in_ledger(entry, firstjob, jobset, result, opts, files=None):
    """...existing docstring, plus: for a draining batch (files given)
    the ledger stores the FILENAME list in indices_json — file-keyed
    rows are discriminated by is_draining(entry), never by content."""
    payload = (list(files) if files is not None
               else [firstjob + i for i in jobset])
    try:
        row_id = submission_ledger.record_submission(
            opts.ledger_db,
            tarball=result['tarball'],
            entry=entry,
            indices=payload,
            ...rest unchanged, using `payload` in the failure message...
```

10. `_log_submission` — files-aware line:

```python
def _log_submission(firstjob, jobset, result, opts, files=None):
    try:
        if files is not None:
            idx_line = (f"files: {len(files)} "
                        f"[{files[0]} .. {files[-1]}]")
        else:
            absolute = [firstjob + i for i in jobset]
            idx_line = (f"indices: {len(absolute)} absolute "
                        f"[{absolute[0]}..{absolute[-1]}]"
                        if absolute else "indices: none")
        ...rest unchanged...
```

`utils/submissions.py` — extend the import (line 39) and skip file-keyed rows in `_slice_overlaps_ledger` (insert after the tarball check, line 407):

```python
from utils.poms_entry import njobs_of, is_draining
```

```python
    for row in submission_ledger.all_rows(db_path):
        if row['tarball'] != tarball:
            continue
        if is_draining(row['entry']):
            continue   # file-keyed row — no index space to overlap
        if any(lo <= idx < hi for idx in row['indices']):
            return True
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestParseFiles test/test_unit.py::TestBuildOpsJsonFiles test/test_unit.py::TestSubmitEntryDirectFiles test/test_unit.py::TestSliceOverlapSkipsFileRows -v` → PASS; `python -m pytest test/test_unit.py -q` → all pass (existing `submit_entry_direct` tests build Namespaces without `files` — `getattr(opts, 'files', None)` keeps them green).

- [ ] **Step 5: Commit**

```bash
git add utils/submit.py utils/jobsub_argv.py utils/submissions.py test/test_unit.py
git commit -m "feat(draining): submit_map --files batch dispatch, file-keyed ledger rows"
```

---

### Task 4: Worker branch — `_direct_dispatch` runs `process_direct_input`

**Files:**
- Modify: `utils/runmu2e.py` — `_direct_dispatch` (lines 842-864)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: ops JSON with `files` (Task 3), `process_direct_input(jobdesc, fname, args) -> (fcl, simjob_setup, fname, outputs)` (runmu2e.py:235), `validate_jobdesc` (returns `'direct_input'` for a tarball+no-njobs entry, runmu2e.py:139), `_fetch_file_local` (already imported), `_resolve_direct_index`.
- Produces: a worker that, given ops `files`, fetches `files[index]` locally and runs the direct-input path; everything after prep (execute/manifest/push) is shared and unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------
# 46. Draining campaigns: worker files branch
# ---------------------------------------------------------------------------

class TestDirectDispatchFiles(unittest.TestCase):
    DRAIN = {'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
             'inloc': 'tape',
             'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art',
             'outputs': [{'dataset': '*.art', 'location': 'tape'}]}
    FILES = ['dig.mu2e.A.MDC2025au_best_v1_5.001202_00000001.art',
             'dig.mu2e.B.MDC2025au_best_v1_5.001202_00000002.art']

    def _args(self):
        from argparse import Namespace
        return Namespace(dry_run=False, copy_input=True)

    def _dispatch(self, ops, index):
        from utils import runmu2e
        calls = {}

        def fake_pdi(jobdesc, fname, args):
            calls['fname'] = fname
            return ('job.fcl', '/cvmfs/setup.sh', fname,
                    ops['jobdesc'][0]['outputs'])

        with patch.object(runmu2e, 'process_direct_input', fake_pdi), \
             patch.object(runmu2e, '_fetch_file_local') as ffl, \
             patch.object(runmu2e, '_execute_mu2e',
                          return_value=False), \
             patch.object(runmu2e, '_push_all'):
            failed = runmu2e._direct_dispatch(self._args(), ops, index)
        return failed, calls, ffl

    def test_index_selects_the_file(self):
        ops = {'jobs': [0, 1], 'files': list(self.FILES),
               'jobdesc': [dict(self.DRAIN)]}
        failed, calls, ffl = self._dispatch(ops, 1)
        self.assertFalse(failed)
        self.assertEqual(calls['fname'], self.FILES[1])
        ffl.assert_any_call(self.FILES[1])

    def test_index_out_of_range_exits(self):
        from utils import runmu2e
        ops = {'jobs': [0, 1, 2], 'files': list(self.FILES),
               'jobdesc': [dict(self.DRAIN)]}
        with self.assertRaises(SystemExit):
            runmu2e._direct_dispatch(self._args(), ops, 2)

    def test_files_with_normal_jobdesc_exits(self):
        from utils import runmu2e
        normal = dict(self.DRAIN, njobs=10)
        normal.pop('input_pattern')
        ops = {'jobs': [0], 'files': list(self.FILES),
               'jobdesc': [normal]}
        with self.assertRaises(SystemExit):
            runmu2e._direct_dispatch(self._args(), ops, 0)

    def test_direct_input_jobdesc_without_files_still_exits(self):
        from utils import runmu2e
        ops = {'jobs': [0], 'jobdesc': [dict(self.DRAIN)]}
        with self.assertRaises(SystemExit):
            runmu2e._direct_dispatch(self._args(), ops, 0)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/test_unit.py::TestDirectDispatchFiles -v`
Expected: FAIL — current `_direct_dispatch` exits on every `direct_input` jobdesc, files or not.

- [ ] **Step 3: Implement in `utils/runmu2e.py`**

Replace the top of `_direct_dispatch` (lines 842-862, through the `track_parents` assignment) with:

```python
def _direct_dispatch(args, ops, index):
    """Direct-mode equivalent of _dispatch_and_execute: run the entry's
    prep — normal index mode via process_jobdef, or a draining batch
    (ops ships a `files` list) via process_direct_input — then the
    shared mu2e -c → manifest → push (with retries) tail."""
    jobdesc = ops['jobdesc']
    files = ops.get('files')

    mode = validate_jobdesc(jobdesc)
    if files is not None:
        # Draining batch: PROCESS → position in the batch → input file.
        if mode != 'direct_input':
            print(f"ERROR: ops carries a files list but the jobdesc is "
                  f"'{mode or 'normal'}' mode — draining batches ship "
                  f"direct-input entries only.")
            sys.exit(1)
        if not 0 <= index < len(files):
            print(f"ERROR: job index {index} out of range for files "
                  f"list of length {len(files)}")
            sys.exit(1)
        fname = files[index]
        print(f"[direct] files[{index}] = {fname}")
        # Stage the input locally (direct mode has no POMS pre-staging;
        # matches the copy_input=True convention of _direct_main).
        _fetch_file_local(fname)
        fcl, simjob_setup, infiles, outputs = process_direct_input(
            jobdesc, fname, args)
        inloc = jobdesc[0].get('inloc')
    else:
        if mode != False:  # noqa: E712 — validate_jobdesc returns False for normal
            print(f"ERROR: direct mode supports normal-mode jobdescs "
                  f"only, got '{mode}'. direct_input entries run as "
                  f"draining batches (submit_map --files); template/"
                  f"g4bl via the upstream mu2ejobsub/mu2eg4bl CLIs.")
            sys.exit(1)
        fname = _synthesize_direct_fname(index)
        fcl, simjob_setup, infiles, outputs, inloc = process_jobdef(
            jobdesc, fname, args)

    # `dir:<path>` inloc means inputs come from a locally-mounted FS and
    # have no SAM parents — match the POMS-mode logic in _dispatch_and_execute.
    track_parents = not (isinstance(inloc, str) and inloc.startswith('dir:'))
```

Everything from `job_failed = _execute_mu2e(...)` down is unchanged.

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestDirectDispatchFiles -v` → PASS; `python -m pytest test/test_unit.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add utils/runmu2e.py test/test_unit.py
git commit -m "feat(draining): worker files branch — direct mode runs process_direct_input"
```

---

### Task 5: Pending predicate and batch gates

**Files:**
- Modify: `utils/submissions.py` — imports, `DEFAULT_MIN_AGE_MINUTES`, `_dataset_of`, `draining_state`, `_gate_batch`, `_request_prestage` (place the group after `ledger_expected`, before `_scratch_map_dir`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `expected_outputs_for`, `is_draining` (Task 1), `metadata_for_files` / `definitions_matching` / `files_in_dataset` / `_parse_sam_datetime` (samweb_wrapper), `_default_locality` + `_LOC_TO_MDH` (check_inputs), `infer_dataset_location` (file_resolver), `locate_tarball`, `Mu2eJobPars`, `submission_ledger.all_rows`.
- Produces:
  - `draining_state(camp, db_path, *, defs_fn=definitions_matching, sam_lister=files_in_dataset, locate=locate_tarball) -> dict` with keys `inputs`/`landed`/`in_flight`/`parked` (sets) and `pending` (sorted list); raises on anything preventing a sound answer.
  - `_gate_batch(entry, candidates, *, locality=_default_locality, metadata_fn=metadata_for_files, dataset_location=infer_dataset_location, now=None) -> (dispatch, young, tape_only)`; raises `RuntimeError` on unknown age/residency (fail-closed).
  - `_request_prestage(files, runner=subprocess.run) -> None` (never raises).
  - `DEFAULT_MIN_AGE_MINUTES = 60`.

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------
# 47. Draining campaigns: pending predicate + batch gates
# ---------------------------------------------------------------------------

def _mk_file(desc, i):
    return f'dig.mu2e.{desc}.MDC2025au_best_v1_5.001202_{i:08d}.art'


def _mk_out(desc, i):
    return f'mcs.mu2e.{desc}.MDC2025au_best_v1_5.001202_{i:08d}.art'


class _DrainPars:
    """Fake Mu2eJobPars: identity dig→mcs mapping (desc preserved)."""

    def __init__(self, path):
        pass

    def job_outputs(self, index, override_desc=None, override_seq=None):
        return {'Output': f'mcs.mu2e.{override_desc}.'
                          f'MDC2025au_best_v1_5.{override_seq}.art'}


class TestDrainingState(unittest.TestCase):
    CAMP = {'id': 48, 'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
            'entry': {'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
                      'inloc': 'tape',
                      'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art',
                      'outputs': [{'dataset': '*.art', 'location': 'tape'}]},
            'cursor': 0, 'slice_size': 500}

    def _state(self, *, in_files, out_files, rows=(), exclude=None,
               defs=None):
        from utils import submissions
        camp = {**self.CAMP,
                'entry': {**self.CAMP['entry'],
                          **({'exclude_desc': exclude} if exclude else {})}}
        in_ds = 'dig.mu2e.A.MDC2025au_best_v1_5.art'
        out_ds = 'mcs.mu2e.A.MDC2025au_best_v1_5.art'
        listing = {in_ds: list(in_files), out_ds: list(out_files)}
        if defs is None:
            defs = [in_ds]

        def lister(ds):
            return listing.get(ds, [])

        with patch.object(submissions, 'Mu2eJobPars', _DrainPars), \
             patch.object(submissions.os.path, 'exists',
                          return_value=True), \
             patch.object(submissions.submission_ledger, 'all_rows',
                          return_value=list(rows)):
            return submissions.draining_state(
                camp, '/x.db', defs_fn=lambda p: list(defs),
                sam_lister=lister, locate=lambda t: '/tmp/' + t)

    def test_growth_pending_is_inputs_minus_landed(self):
        ins = [_mk_file('A', i) for i in range(4)]
        outs = [_mk_out('A', 0), _mk_out('A', 1)]
        st = self._state(in_files=ins, out_files=outs)
        self.assertEqual(st['pending'], sorted(ins[2:]))
        self.assertEqual(len(st['landed']), 2)

    def test_in_flight_and_parked_excluded(self):
        ins = [_mk_file('A', i) for i in range(4)]
        rows = [{'tarball': self.CAMP['tarball'], 'state': 'active',
                 'entry': self.CAMP['entry'], 'indices': [ins[0]]},
                {'tarball': self.CAMP['tarball'], 'state': 'exhausted',
                 'entry': self.CAMP['entry'], 'indices': [ins[1]]}]
        st = self._state(in_files=ins, out_files=[], rows=rows)
        self.assertEqual(st['pending'], sorted(ins[2:]))
        self.assertEqual(st['in_flight'], {ins[0]})
        self.assertEqual(st['parked'], {ins[1]})

    def test_landed_exhausted_file_is_not_parked(self):
        ins = [_mk_file('A', 0)]
        rows = [{'tarball': self.CAMP['tarball'], 'state': 'exhausted',
                 'entry': self.CAMP['entry'], 'indices': [ins[0]]}]
        st = self._state(in_files=ins, out_files=[_mk_out('A', 0)],
                         rows=rows)
        self.assertEqual(st['parked'], set())
        self.assertEqual(st['pending'], [])

    def test_exclude_desc_drops_whole_dataset_exact_match(self):
        from utils import submissions
        # FlatGamma excluded must NOT drop FlatGammaCalo
        defs = ['dig.mu2e.FlatGamma.MDC2025au_best_v1_5.art',
                'dig.mu2e.FlatGammaCalo.MDC2025au_best_v1_5.art']
        fg = [_mk_file('FlatGamma', 0)]
        fgc = [_mk_file('FlatGammaCalo', 0)]
        listing = {defs[0]: fg, defs[1]: fgc,
                   'mcs.mu2e.FlatGammaCalo.MDC2025au_best_v1_5.art': []}
        with patch.object(submissions, 'Mu2eJobPars', _DrainPars), \
             patch.object(submissions.os.path, 'exists',
                          return_value=True), \
             patch.object(submissions.submission_ledger, 'all_rows',
                          return_value=[]):
            st = submissions.draining_state(
                {**self.CAMP,
                 'entry': {**self.CAMP['entry'],
                           'exclude_desc': ['FlatGamma']}},
                '/x.db', defs_fn=lambda p: defs,
                sam_lister=lambda ds: listing.get(ds, []),
                locate=lambda t: '/tmp/' + t)
        self.assertEqual(st['pending'], fgc)

    def test_non_dataset_definition_names_are_ignored(self):
        # drainingn-era _slice_/_full_ junk can match a pattern; a name
        # that does not parse as a 5-field dataset is skipped.
        ins = [_mk_file('A', 0)]
        st = self._state(
            in_files=ins, out_files=[],
            defs=['dig.mu2e.A.MDC2025au_best_v1_5.art',
                  'dig.mu2e.A.MDC2025au_best_v1_5.art_slice_0_stage_2'])
        self.assertEqual(st['pending'], ins)

    def test_unlocatable_tarball_raises(self):
        from utils import submissions
        with self.assertRaises(RuntimeError):
            with patch.object(submissions.submission_ledger, 'all_rows',
                              return_value=[]):
                submissions.draining_state(
                    self.CAMP, '/x.db', defs_fn=lambda p: [],
                    sam_lister=lambda ds: [], locate=lambda t: None)


class TestGateBatch(unittest.TestCase):
    ENTRY = {'inloc': 'tape', 'min_age_minutes': 60,
             'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art'}
    OLD = '2026-08-01T00:00:00+00:00'
    NOW = None  # set in setUp

    def setUp(self):
        from datetime import datetime, timezone
        self.NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    def _md(self, files, stamp=OLD):
        return lambda fl: [{'file_name': f, 'create_datetime': stamp}
                           for f in fl]

    def _gate(self, files, *, states, md=None):
        from utils import submissions
        return submissions._gate_batch(
            dict(self.ENTRY), files,
            locality=lambda loc, fl: {f: states.get(f, 'ERROR')
                                      for f in fl},
            metadata_fn=md or self._md(files),
            dataset_location=lambda ds: 'enstore',
            now=self.NOW)

    def test_online_files_dispatch_nearline_withheld(self):
        f1, f2 = _mk_file('A', 1), _mk_file('A', 2)
        dispatch, young, tape = self._gate(
            [f1, f2], states={f1: 'ONLINE_AND_NEARLINE', f2: 'NEARLINE'})
        self.assertEqual(dispatch, [f1])
        self.assertEqual(tape, [f2])
        self.assertEqual(young, [])

    def test_too_young_withheld(self):
        f1 = _mk_file('A', 1)
        fresh = '2026-08-01T11:30:00+00:00'   # 30 min old, min_age 60
        dispatch, young, tape = self._gate(
            [f1], states={f1: 'ONLINE'}, md=self._md([f1], fresh))
        self.assertEqual(dispatch, [])
        self.assertEqual(young, [f1])

    def test_unknown_age_fails_closed(self):
        f1 = _mk_file('A', 1)
        with self.assertRaises(RuntimeError):
            self._gate([f1], states={f1: 'ONLINE'},
                       md=lambda fl: [])   # no metadata returned

    def test_locality_error_fails_closed(self):
        f1 = _mk_file('A', 1)
        with self.assertRaises(RuntimeError):
            self._gate([f1], states={f1: 'ERROR'})

    def test_missing_file_fails_closed(self):
        f1 = _mk_file('A', 1)
        with self.assertRaises(RuntimeError):
            self._gate([f1], states={f1: 'MISSING'})
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/test_unit.py::TestDrainingState test/test_unit.py::TestGateBatch -v`
Expected: FAIL — `draining_state` / `_gate_batch` not defined.

- [ ] **Step 3: Implement in `utils/submissions.py`**

Extend imports at the top:

```python
import tempfile
from datetime import datetime, timedelta, timezone

from utils.check_inputs import _default_locality, _LOC_TO_MDH
from utils.file_resolver import infer_dataset_location
from utils.job_common import Mu2eName, expected_outputs_for
from utils.poms_entry import njobs_of, is_draining
from utils.samweb_wrapper import (files_in_dataset, definitions_matching,
                                  metadata_for_files, _parse_sam_datetime)
```

(`tempfile` is already imported — check; merge, don't duplicate. `Mu2eName` import already exists — extend that line.)

Add after `ledger_expected`:

```python
DEFAULT_MIN_AGE_MINUTES = 60


def _dataset_of(fname):
    """Dataset name of a Mu2e file name (drop the sequencer)."""
    return str(Mu2eName.parse(fname).dataset)


def draining_state(camp, db_path, *,
                   defs_fn=definitions_matching,
                   sam_lister=files_in_dataset,
                   locate=locate_tarball):
    """One draining campaign's file sets, computed fresh from SAM + the
    ledger — draining has NO cursor; nothing counts as done until its
    output exists (the fix for POMS drainingn's launch-time cursor).

        inputs    pattern datasets' files (exclude_desc removed)
        landed    inputs whose expected outputs ALL exist in SAM
        in_flight files in this campaign's ACTIVE rows
        parked    files in exhausted rows whose outputs are still missing
        pending   inputs − landed − in_flight − parked   (sorted)

    Dataset enumeration is definition-based (production convention);
    matched names that don't parse as 5-field datasets (e.g. POMS-era
    `_slice_`/`_full_` junk) are skipped. Raises on an unlocatable
    tarball or a malformed input filename — never guesses.
    """
    entry = camp['entry']
    exclude = set(entry.get('exclude_desc', []))
    path = locate(camp['tarball'])
    if not path or not os.path.exists(path):
        raise RuntimeError(f"cannot locate tarball {camp['tarball']}")
    jp = Mu2eJobPars(path)
    datasets = []
    for d in defs_fn(entry['input_pattern']):
        try:
            n = Mu2eName.parse(d)
        except ValueError:
            continue
        if not n.is_dataset or n.description in exclude:
            continue
        datasets.append(d)
    inputs = set()
    for ds in datasets:
        inputs.update(sam_lister(ds))
    out_of = {f: expected_outputs_for(f, jp) for f in sorted(inputs)}
    out_datasets = {_dataset_of(o)
                    for outs in out_of.values() for o in outs}
    existing = {ds: set(sam_lister(ds)) for ds in sorted(out_datasets)}
    landed = {f for f, outs in out_of.items()
              if all(o in existing[_dataset_of(o)] for o in outs)}
    in_flight, exhausted = set(), set()
    for r in submission_ledger.all_rows(db_path):
        if r['tarball'] != camp['tarball'] or not is_draining(r['entry']):
            continue
        if r['state'] == 'active':
            in_flight.update(r['indices'])
        elif r['state'] == 'exhausted':
            exhausted.update(r['indices'])
    parked = exhausted - landed
    pending = sorted(inputs - landed - in_flight - parked)
    return {'inputs': inputs, 'landed': landed, 'in_flight': in_flight,
            'parked': parked, 'pending': pending}


def _gate_batch(entry, candidates, *,
                locality=_default_locality,
                metadata_fn=metadata_for_files,
                dataset_location=infer_dataset_location,
                now=None):
    """Gate a candidate batch: (dispatch, young, tape_only).

    Settling age first (the POMS fts= idea: pushOutput declares metadata
    before locations settle — never race a half-pushed upstream batch),
    then dCache residency (never a job that hangs on tape recall).
    Raises RuntimeError whenever age or residency cannot be established:
    fail closed, no dispatch on unknowns.
    """
    now = now or datetime.now(timezone.utc)
    min_age = entry.get('min_age_minutes', DEFAULT_MIN_AGE_MINUTES)
    cutoff = now - timedelta(minutes=min_age)
    md_by_name = {}
    for md in metadata_fn(list(candidates)):
        md_by_name[md.get('file_name')] = md
    old_enough, young = [], []
    for f in candidates:
        stamp = (md_by_name.get(f) or {}).get('create_datetime')
        dt = _parse_sam_datetime(stamp) if stamp else None
        if dt is None:
            raise RuntimeError(
                f"no SAM create time for {f} — age unknown (fail closed)")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        (old_enough if dt <= cutoff else young).append(f)
    by_ds = {}
    for f in old_enough:
        by_ds.setdefault(_dataset_of(f), []).append(f)
    dispatch, tape_only = [], []
    for ds, fl in sorted(by_ds.items()):
        mdh_loc = _LOC_TO_MDH.get(dataset_location(ds))
        if mdh_loc is None:
            raise RuntimeError(f"unknown storage location for {ds}")
        states = locality(mdh_loc, fl)
        for f in fl:
            st = states.get(f, 'ERROR')
            if st in ('ONLINE', 'ONLINE_AND_NEARLINE'):
                dispatch.append(f)
            elif st == 'NEARLINE':
                tape_only.append(f)
            else:
                raise RuntimeError(f"locality {st!r} for {f} — "
                                   f"residency unknown (fail closed)")
    return dispatch, young, tape_only


def _request_prestage(files, runner=subprocess.run):
    """One batched `mdh prestage-files` request for tape-only pending
    files (entry opts in with `prestage: true`). Never raises — the
    request is an optimization and idempotent server-side; the tick
    continues either way."""
    try:
        with tempfile.NamedTemporaryFile(
                'w', suffix='.txt', delete=False) as fh:
            fh.write('\n'.join(sorted(files)) + '\n')
        res = runner(['mdh', 'prestage-files', fh.name],
                     capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            print(f"  prestage request failed rc={res.returncode}: "
                  f"{(res.stderr or '').strip()[:200]}")
    except Exception as e:
        print(f"  prestage request failed: {e}")
```

Note: `_parse_sam_datetime` (samweb_wrapper.py:61) strips the timezone by design and returns **naive UTC** — the `dt.tzinfo is None → replace(tzinfo=timezone.utc)` branch is therefore the NORMAL path (SAM stores UTC), not dead safety. Do not remove it.

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestDrainingState test/test_unit.py::TestGateBatch -v` → PASS; `python -m pytest test/test_unit.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "feat(draining): pending predicate (draining_state) + fail-closed batch gates"
```

---

### Task 6: File-keyed verification and recovery

**Files:**
- Modify: `utils/submissions.py` — `verify_files_row` + `resubmit_files` (after `resubmit`, line 345); kind dispatch in `process_row` (line 552)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `expected_outputs_for`, `_dataset_of`, `is_draining`, `locate_tarball`, `Mu2eJobPars`, `_scratch_map_dir`, `recovery_resource_argv`, `SUBMIT_MAP`, `submit_map --files` (Task 3).
- Produces: `verify_files_row(row, sam_lister=files_in_dataset) -> (missing, partial)` as **filenames** (same contract as `verify_row`: raises when verification is impossible); `resubmit_files(row, missing, db_path, dry_run=False, runner=subprocess.run) -> bool`; `process_row(...)` signature changes to `verify_fn=None, resubmit_fn=None` with per-row-kind defaults (explicit injections still honored — existing tests stay green).

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------
# 48. Draining campaigns: file-keyed verify + recovery
# ---------------------------------------------------------------------------

class TestVerifyFilesRow(unittest.TestCase):
    ROW = {'id': 7, 'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
           'entry': {'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art',
                     'inloc': 'tape',
                     'outputs': [{'dataset': '*.art', 'location': 'tape'}]},
           'indices': [_mk_file('A', 1), _mk_file('B', 2)],
           'attempt': 1, 'cluster_id': '123', 'parent_id': None}

    def _verify(self, existing_outputs):
        from utils import submissions
        with patch.object(submissions, 'locate_tarball',
                          return_value='/tmp/t.tar'), \
             patch.object(submissions.os.path, 'exists',
                          return_value=True), \
             patch.object(submissions, 'Mu2eJobPars', _DrainPars):
            return submissions.verify_files_row(
                dict(self.ROW), sam_lister=lambda ds: existing_outputs)

    def test_all_outputs_present_is_complete(self):
        missing, partial = self._verify([_mk_out('A', 1), _mk_out('B', 2)])
        self.assertEqual(missing, [])
        self.assertEqual(partial, [])

    def test_missing_output_names_the_input_file(self):
        missing, partial = self._verify([_mk_out('A', 1)])
        self.assertEqual(missing, [_mk_file('B', 2)])
        self.assertEqual(partial, [])

    def test_unlocatable_tarball_raises(self):
        from utils import submissions
        with patch.object(submissions, 'locate_tarball',
                          return_value=None):
            with self.assertRaises(RuntimeError):
                submissions.verify_files_row(dict(self.ROW))


class TestResubmitFiles(unittest.TestCase):
    def test_child_submission_uses_files_flag_and_parent(self):
        from utils import submissions
        row = dict(TestVerifyFilesRow.ROW)
        missing = [_mk_file('B', 2)]
        seen = {}

        def fake_run(cmd, **kw):
            seen['cmd'] = list(cmd)
            seen['files_text'] = Path(
                cmd[cmd.index('--files') + 1]).read_text()
            return SimpleNamespace(returncode=0)

        ok = submissions.resubmit_files(row, missing, '/x.db',
                                        runner=fake_run)
        self.assertTrue(ok)
        cmd = seen['cmd']
        self.assertIn('--files', cmd)
        self.assertIn('--ledger-parent', cmd)
        self.assertEqual(cmd[cmd.index('--ledger-parent') + 1], '7')
        self.assertIn(missing[0], seen['files_text'])
        # recovery resource floor applies (entry names no resources)
        self.assertIn('--memory', cmd)


class TestProcessRowKindDispatch(unittest.TestCase):
    def test_draining_row_uses_file_verify_and_file_resubmit(self):
        from utils import submissions
        row = dict(TestVerifyFilesRow.ROW)
        called = {}

        def fake_verify(r):
            called['verify'] = True
            return [], []

        with patch.object(submissions, 'verify_files_row', fake_verify), \
             patch.object(submissions.submission_ledger, 'close_row'):
            action = submissions.process_row(
                row, '/x.db', 3,
                clusters={},   # cluster absent from snapshot -> drained
                dry_run=False)
        self.assertEqual(action, 'complete')
        self.assertTrue(called.get('verify'))
```

Note: `SimpleNamespace` — check the test file's imports; `from types import SimpleNamespace` if not present.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/test_unit.py::TestVerifyFilesRow test/test_unit.py::TestResubmitFiles test/test_unit.py::TestProcessRowKindDispatch -v`
Expected: FAIL — functions not defined; `process_row` verifies via index `verify_row`.

- [ ] **Step 3: Implement in `utils/submissions.py`**

After `resubmit`:

```python
def verify_files_row(row, sam_lister=files_in_dataset):
    """SAM-verify one file-keyed (draining) ledger row.

    The exact analog of verify_row, keyed by input FILENAMES: expected
    outputs come from expected_outputs_for — the worker's own name
    substitution — so verification can never drift from what the job
    actually produced. Returns (missing, partial) as input filenames.
    Raises on anything that prevents verification (unlocatable tarball,
    SAM failure): a row is never guessed complete.
    """
    tarball_path = locate_tarball(row['tarball'])
    if not tarball_path or not os.path.exists(tarball_path):
        raise RuntimeError(f"cannot locate tarball {row['tarball']}")
    jp = Mu2eJobPars(tarball_path)
    files = row['indices']            # filenames for a draining row
    out_of = {f: expected_outputs_for(f, jp) for f in files}
    out_datasets = {_dataset_of(o)
                    for outs in out_of.values() for o in outs}
    existing = {ds: set(sam_lister(ds)) for ds in sorted(out_datasets)}
    missing, partial = [], []
    for f in files:
        absent = [o for o in out_of[f]
                  if o not in existing[_dataset_of(o)]]
        if absent:
            missing.append(f)
            if len(absent) < len(out_of[f]):
                partial.append(f)
    return missing, partial


def resubmit_files(row, missing, db_path, dry_run=False,
                   runner=subprocess.run):
    """Draining analog of resubmit(): child submission of exactly the
    missing input files via `submit_map --files` (child ledger row via
    --ledger-parent, attempt+1; the recovery resource floor applies)."""
    entry = row['entry']
    with _scratch_map_dir('recover-') as tmpdir:
        map_path = tmpdir / 'recovery-map.json'
        map_path.write_text(json.dumps([entry], indent=2) + '\n')
        files_path = tmpdir / 'files.txt'
        files_path.write_text(f"# {row['tarball']}\n"
                              + '\n'.join(missing) + '\n')
        cmd = [str(SUBMIT_MAP), '--map', str(map_path),
               '--files', str(files_path),
               '--ledger-parent', str(row['id']),
               '--ledger-db', str(db_path)]
        cmd += recovery_resource_argv(entry)
        if dry_run:
            cmd.append('--dry-run')
        print(f"  resubmit: {' '.join(cmd)}")
        res = runner(cmd)
    return res.returncode == 0
```

`process_row` — change the signature and add the dispatch at the top (everything else unchanged; the prints keep saying "indices", which is tolerable for v1 — do not rewrite messages):

```python
def process_row(row, db_path, max_attempts, clusters=None, dry_run=False,
                queue_state_fn=cluster_queue_state, verify_fn=None,
                resubmit_fn=None):
    """...existing docstring + one line:
    verify_fn/resubmit_fn default per row kind: file-keyed (draining)
    rows verify via verify_files_row and recover via resubmit_files;
    index rows keep verify_row/resubmit. Explicit injections win.
    """
    if verify_fn is None:
        verify_fn = (verify_files_row if is_draining(row['entry'])
                     else verify_row)
    if resubmit_fn is None:
        resubmit_fn = (resubmit_files if is_draining(row['entry'])
                       else resubmit)
    rid = row['id']
    ...unchanged...
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestVerifyFilesRow test/test_unit.py::TestResubmitFiles test/test_unit.py::TestProcessRowKindDispatch -v` → PASS; `python -m pytest test/test_unit.py -q` → all pass (`TestRecoverLoop` passes explicit `verify_fn`/`resubmit_fn` — verify while implementing; explicit injections still win).

- [ ] **Step 5: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "feat(draining): file-keyed verify_files_row + resubmit_files, kind dispatch in process_row"
```

---

### Task 7: The drain tick

**Files:**
- Modify: `utils/submissions.py` — `submit_drain_batch` + `drain_tick` (after `top_up`); skip in `top_up`'s loop (line 448); wiring in `_run_pass` (line 771)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `draining_state`, `_gate_batch`, `_request_prestage` (Task 5), `submit_map --files` (Task 3), `total_queued`, `submission_ledger.active_campaigns` / `set_campaign_state`, `_scratch_map_dir`, `SUBMIT_MAP`.
- Produces: `submit_drain_batch(camp, files, db_path, runner=subprocess.run) -> bool`; `drain_tick(db_path, cap, dry_run=False, count_fn=total_queued, submit_fn=submit_drain_batch, state_fn=draining_state, gate_fn=_gate_batch, prestage_fn=_request_prestage) -> dict` (summary counters: `drain-batch`, `would-drain-batch`, `drain-idle`, `drain-gated`, `drain-cap-wait`, `drain-error`, `campaign-paused`, `count-error`); `_run_pass` runs it after `top_up`; `top_up` skips draining campaigns.

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------
# 49. Draining campaigns: the drain tick
# ---------------------------------------------------------------------------

class TestDrainTick(unittest.TestCase):
    CAMP = {'id': 48, 'state': 'active',
            'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
            'entry': {'tarball': 'cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar',
                      'inloc': 'tape',
                      'input_pattern': 'dig.mu2e.%.MDC2025au_best_v1_5.art',
                      'outputs': [{'dataset': '*.art', 'location': 'tape'}]},
            'cursor': 0, 'slice_size': 2}

    def _tick(self, *, pending, cap=100, count=10, dry_run=False,
              gate=None, submit_ok=True, camps=None, prestage_camp=False):
        from utils import submissions
        camp = dict(self.CAMP)
        if prestage_camp:
            camp = {**camp, 'entry': {**camp['entry'], 'prestage': True}}
        state = {'inputs': set(pending), 'landed': set(),
                 'in_flight': set(), 'parked': set(),
                 'pending': sorted(pending)}
        submitted = []

        def fake_submit(c, batch, db):
            submitted.append(list(batch))
            return submit_ok

        prestaged = []
        with patch.object(submissions.submission_ledger,
                          'active_campaigns',
                          return_value=camps if camps is not None
                          else [camp]), \
             patch.object(submissions.submission_ledger,
                          'set_campaign_state') as scs:
            summary = submissions.drain_tick(
                '/x.db', cap, dry_run=dry_run,
                count_fn=lambda: count,
                submit_fn=fake_submit,
                state_fn=lambda c, db: dict(state),
                gate_fn=gate or (lambda e, cand: (list(cand), [], [])),
                prestage_fn=lambda fl: prestaged.append(list(fl)))
        return summary, submitted, prestaged, scs

    def test_one_gated_batch_per_campaign(self):
        pend = [_mk_file('A', i) for i in range(5)]
        summary, submitted, _, _ = self._tick(pending=pend)
        self.assertEqual(summary.get('drain-batch'), 1)
        self.assertEqual(submitted, [sorted(pend)[:2]])   # slice_size=2

    def test_idle_campaign_reports_and_submits_nothing(self):
        summary, submitted, _, _ = self._tick(pending=[])
        self.assertEqual(summary.get('drain-idle'), 1)
        self.assertEqual(submitted, [])

    def test_cap_stops_the_phase(self):
        pend = [_mk_file('A', i) for i in range(5)]
        summary, submitted, _, _ = self._tick(pending=pend, cap=11,
                                              count=10)
        self.assertEqual(summary.get('drain-cap-wait'), 1)
        self.assertEqual(submitted, [])

    def test_gate_failure_is_fail_closed(self):
        def bad_gate(entry, cand):
            raise RuntimeError('mdh down')
        pend = [_mk_file('A', 1)]
        summary, submitted, _, _ = self._tick(pending=pend, gate=bad_gate)
        self.assertEqual(summary.get('drain-error'), 1)
        self.assertEqual(submitted, [])

    def test_submit_failure_pauses_campaign(self):
        pend = [_mk_file('A', 1)]
        summary, _, _, scs = self._tick(pending=pend, submit_ok=False)
        self.assertEqual(summary.get('campaign-paused'), 1)
        scs.assert_called_once()
        self.assertEqual(scs.call_args[0][2], 'paused')

    def test_dry_run_submits_nothing_but_counts(self):
        pend = [_mk_file('A', 1)]
        summary, submitted, _, _ = self._tick(pending=pend, dry_run=True)
        self.assertEqual(summary.get('would-drain-batch'), 1)
        self.assertEqual(submitted, [])

    def test_prestage_requested_for_tape_only(self):
        pend = [_mk_file('A', 1), _mk_file('A', 2)]

        def gate(entry, cand):
            return [cand[0]], [], [cand[1]]
        summary, submitted, prestaged, _ = self._tick(
            pending=pend, gate=gate, prestage_camp=True)
        self.assertEqual(prestaged, [[sorted(pend)[1]]])
        self.assertEqual(submitted, [[sorted(pend)[0]]])

    def test_index_campaigns_are_ignored(self):
        camps = [{'id': 1, 'state': 'active', 'tarball': 't',
                  'entry': {'njobs': 100}, 'cursor': 0, 'slice_size': 10}]
        summary, submitted, _, _ = self._tick(pending=[], camps=camps)
        self.assertEqual(summary, {})
        self.assertEqual(submitted, [])


class TestTopUpSkipsDraining(unittest.TestCase):
    def test_draining_campaign_never_reaches_index_arithmetic(self):
        from utils import submissions
        camp = dict(TestDrainTick.CAMP)   # no njobs -> would TypeError
        with patch.object(submissions.submission_ledger,
                          'active_campaigns', return_value=[camp]):
            summary = submissions.top_up('/x.db', 100,
                                         count_fn=lambda: 0,
                                         submit_fn=lambda *a: True)
        self.assertEqual(summary, {})
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/test_unit.py::TestDrainTick test/test_unit.py::TestTopUpSkipsDraining -v`
Expected: FAIL — `drain_tick` not defined; `top_up` TypeErrors on `njobs - cursor` with njobs None.

- [ ] **Step 3: Implement in `utils/submissions.py`**

In `top_up`'s campaign loop (right after `if camp['state'] != 'active': continue`, line 449):

```python
            if is_draining(camp['entry']):
                continue   # fed by drain_tick, not by index slices
```

After `top_up`:

```python
def submit_drain_batch(camp, files, db_path, runner=subprocess.run):
    """Submit one draining batch through the submit_map CLI — the same
    battle-tested path as index slices (token check, argv build, ledger
    row, submit log). The snapshot entry ships VERBATIM."""
    with _scratch_map_dir('drain-') as tmpdir:
        map_path = tmpdir / 'drain-map.json'
        map_path.write_text(json.dumps([camp['entry']], indent=2) + '\n')
        files_path = tmpdir / 'files.txt'
        files_path.write_text('\n'.join(files) + '\n')
        cmd = [str(SUBMIT_MAP), '--map', str(map_path),
               '--files', str(files_path), '--ledger-db', str(db_path)]
        print(f"  campaign {camp['id']}: batch of {len(files)}: "
              f"{' '.join(cmd)}")
        res = runner(cmd)
    return res.returncode == 0


def drain_tick(db_path, cap, dry_run=False, count_fn=total_queued,
               submit_fn=submit_drain_batch, state_fn=draining_state,
               gate_fn=_gate_batch, prestage_fn=_request_prestage):
    """Feed draining campaigns: ONE gated batch per campaign per tick,
    oldest-first, under the same queue cap as index top-up (fresh
    count — index slices submitted moments earlier are already in it).
    Draining state is recomputed from SAM each tick and every unknown
    fails closed; a batch-submit failure pauses the campaign (no blind
    retry — the Run1Ban rule)."""
    summary = {}

    def bump(key):
        summary[key] = summary.get(key, 0) + 1

    camps = [c for c in submission_ledger.active_campaigns(db_path)
             if is_draining(c['entry'])]
    if not camps:
        return summary
    count = count_fn()
    if count is None:
        print("drain: queue count failed — draining skipped this tick")
        bump('count-error')
        return summary
    print(f"drain: {count} idle+running (cap {cap}), "
          f"{len(camps)} draining campaign(s)")
    for camp in camps:
        cid = camp['id']
        try:
            st = state_fn(camp, db_path)
        except Exception as e:
            print(f"campaign {cid}: draining state failed: {e} — "
                  f"skipped this tick (fail-closed)")
            bump('drain-error')
            continue
        n_in = len(st['inputs'])
        pct = 100.0 * len(st['landed']) / n_in if n_in else 0.0
        print(f"campaign {cid}: landed {len(st['landed'])}/{n_in} "
              f"({pct:.1f}%) | in-flight {len(st['in_flight'])} | "
              f"parked {len(st['parked'])} | pending {len(st['pending'])}")
        if not st['pending']:
            bump('drain-idle')
            continue
        candidates = st['pending'][:camp['slice_size']]
        try:
            batch, young, tape_only = gate_fn(camp['entry'], candidates)
        except Exception as e:
            print(f"campaign {cid}: batch gate failed: {e} — no "
                  f"dispatch this tick (fail-closed)")
            bump('drain-error')
            continue
        if young or tape_only:
            print(f"campaign {cid}: withheld {len(young)} too-young, "
                  f"{len(tape_only)} tape-only")
        if tape_only and camp['entry'].get('prestage'):
            if dry_run:
                print(f"campaign {cid}: would request prestage of "
                      f"{len(tape_only)} file(s)")
            else:
                prestage_fn(tape_only)
                print(f"campaign {cid}: prestage requested for "
                      f"{len(tape_only)} file(s)")
        if not batch:
            bump('drain-gated')
            continue
        if count + len(batch) > cap:
            print(f"drain: campaign {cid}: {count}+{len(batch)} > {cap} "
                  f"— headroom < batch, waiting for next tick")
            bump('drain-cap-wait')
            break
        if dry_run:
            print(f"campaign {cid}: would submit batch of {len(batch)}")
            bump('would-drain-batch')
            count += len(batch)
            continue
        if not submit_fn(camp, batch, db_path):
            submission_ledger.set_campaign_state(
                db_path, cid, 'paused',
                note='batch submit failed — check the submit log and '
                     'jobsub_q, then `submissions resume <ID>`')
            print(f"campaign {cid}: batch submit FAILED — PAUSED "
                  f"(no blind retry)")
            bump('campaign-paused')
            continue
        count += len(batch)
        bump('drain-batch')
    return summary
```

In `_run_pass`, after the `top_up` merge (line 776) and before the paused-campaign check:

```python
        for k, v in drain_tick(args.db, resolve_cap(args.max_queued),
                               dry_run=args.dry_run).items():
            summary[k] = summary.get(k, 0) + v
```

(`campaign-paused` and `count-error` are already in the exit-2 trigger list at line 791 — no change needed there.)

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestDrainTick test/test_unit.py::TestTopUpSkipsDraining -v` → PASS; `python -m pytest test/test_unit.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "feat(draining): drain_tick — one gated batch per campaign per tick under the queue cap"
```

---

### Task 8: Operator surface — status, `complete` verb, docs

**Files:**
- Modify: `utils/submissions.py` — `print_status` (line 650), `manage_campaign` (539), `build_parser` (675), `main` (800)
- Modify: `docs/EXAMPLES_schema.md` — draining bullets (do NOT regenerate `EXAMPLES.md` in this task; see Step 5)
- Modify: `wiki/pages/2026-07-18-direct-recovery-loop.md` — draining section
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `is_draining`, `submission_ledger.set_campaign_state` (transition `active → complete` already exists; `paused → complete` does not — resume first).
- Produces: `submissions complete <id> [--note ...]`; ledger-only draining lines in `submissions status`.

- [ ] **Step 1: Write the failing tests**

```python
# ---------------------------------------------------------------------------
# 50. Draining campaigns: status + complete verb
# ---------------------------------------------------------------------------

class TestCompleteVerb(unittest.TestCase):
    def test_complete_closes_an_active_campaign(self):
        from utils import submissions
        with patch.object(submissions.submission_ledger,
                          'set_campaign_state') as scs:
            submissions.manage_campaign('/x.db', 48, 'complete')
        scs.assert_called_once()
        self.assertEqual(scs.call_args[0][2], 'complete')

    def test_parser_accepts_complete(self):
        from utils import submissions
        args = submissions.build_parser().parse_args(['complete', '48'])
        self.assertEqual(args.verb, 'complete')
        self.assertEqual(args.camp_id, 48)


class TestStatusDrainingLine(unittest.TestCase):
    def test_draining_campaign_prints_pattern_and_ledger_counts(self):
        from utils import submissions
        import io, contextlib as _ctx
        camp = dict(TestDrainTick.CAMP)
        row = {'id': 1, 'state': 'active', 'attempt': 1, 'parent_id': None,
               'tarball': camp['tarball'], 'entry': camp['entry'],
               'indices': [_mk_file('A', 1), _mk_file('A', 2)],
               'created_utc': '2026-08-01T00:00:00+00:00',
               'cluster_id': '123', 'jobsub_id': '1.0@s',
               'map_path': None, 'closed_utc': None, 'note': None}
        buf = io.StringIO()
        with patch.object(submissions.submission_ledger, 'all_rows',
                          return_value=[row]), \
             patch.object(submissions.submission_ledger, 'all_campaigns',
                          return_value=[camp]), \
             _ctx.redirect_stdout(buf):
            submissions.print_status('/x.db')
        out = buf.getvalue()
        self.assertIn('dig.mu2e.%.MDC2025au_best_v1_5.art', out)
        self.assertIn('in-flight 2', out)
        self.assertIn('draining', out)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/test_unit.py::TestCompleteVerb test/test_unit.py::TestStatusDrainingLine -v`
Expected: FAIL — `manage_campaign` KeyError on 'complete'; parser rejects the verb; status TypeErrors on `njobs_of` None formatting for a draining campaign.

- [ ] **Step 3: Implement in `utils/submissions.py`**

`manage_campaign` — extend the map and docstring:

```python
    target = {'pause': 'paused', 'resume': 'active',
              'cancel': 'cancelled', 'complete': 'complete'}[action]
```

(docstring adds: "complete is the operator close-out for draining campaigns — non-blocking: closing with parked files is a legitimate decision. A paused campaign must be resumed first; paused → complete is not a ledger transition.")

`build_parser` — after the `cancel` subparser:

```python
    comp = sub.add_parser('complete',
                          help='Close a campaign complete (operator '
                               'close-out for draining campaigns; '
                               'already-submitted rows still get '
                               'verified/recovered)')
    comp.add_argument('camp_id', type=int)
    comp.add_argument('--note', default=None,
                      help='Reason recorded on the campaign (default: '
                           '"operator complete")')
```

`main` — extend the verb tuple:

```python
    if verb in ('pause', 'resume', 'cancel', 'complete'):
```

`print_status` — inside the campaigns loop, branch before the index-campaign line (the `rows` list from the top of the function is in scope; note the pre-existing early-return when the rows table is empty also hides campaigns — that quirk predates this plan, leave it):

```python
        for c in camps:
            if is_draining(c['entry']):
                mine = [r for r in rows
                        if r['tarball'] == c['tarball']
                        and is_draining(r['entry'])]
                infl = sum(len(r['indices']) for r in mine
                           if r['state'] == 'active')
                exh = sum(len(r['indices']) for r in mine
                          if r['state'] == 'exhausted')
                print(f"{c['id']:>4} {c['state']:<10} "
                      f"{'draining':>12} {c['slice_size']:>6}  "
                      f"{c['created_utc']:<20} {c['tarball']}")
                print(f"{'':>4} pattern {c['entry']['input_pattern']}  "
                      f"in-flight {infl}  exhausted-files {exh}  "
                      f"(drained fraction: `submissions run --dry-run`)")
                continue
            njobs = njobs_of(c['entry'])
            ...existing line unchanged...
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest test/test_unit.py::TestCompleteVerb test/test_unit.py::TestStatusDrainingLine -v` → PASS; `python -m pytest test/test_unit.py -q` → all pass.

- [ ] **Step 5: Documentation (schema + wiki only — no EXAMPLES regen)**

`docs/EXAMPLES_schema.md`: in the section describing `submit_map` / `submissions`, add these tribal-knowledge bullets (adapt wording to the file's style):

- Draining campaigns: a map entry with `input_pattern` (5-field dataset pattern, `%` wildcards) and NO `njobs` drains a growing dataset 1:1 through a generic cnf; enqueue with `submit_map --map M --enqueue --slice-size N`. Optional keys: `exclude_desc` (exact desc matches), `min_age_minutes` (default 60), `prestage` (default false).
- `submit_map --files LIST.txt` submits one direct-input job per filename against a draining entry — written by the tick; the operator path for re-dispatching parked files. Mutually exclusive with `--first/--num/--indices/--indices-file/--enqueue`.
- `submissions complete <id>` is the operator close-out for a draining campaign (it never auto-completes: the input set grows until the upstream finishes, and only the operator knows when that is).
- Draining state lives in SAM, not in a cursor: pending = inputs whose expected outputs (the worker's own `job_outputs` mapping) don't exist yet, minus in-flight and parked files. Nothing counts as done until its output exists.

Do **not** run the EXAMPLES regeneration here: a pending listNewDatasets docs task also touches `EXAMPLES.md`, and the derived file must be regenerated once, from the schema, after both plans' schema edits land (`/refresh-examples`, run by the controller/user).

`wiki/pages/2026-07-18-direct-recovery-loop.md`: append a `## Draining campaigns (2026-08-01)` section, ~15 lines: entry shape, the pending predicate, the two gates, one-batch-per-tick under the cap, file-keyed verify/park semantics, operator `complete`, pointer to the spec `docs/superpowers/specs/2026-08-01-draining-campaigns-design.md` and to `poms-reference.md` for the `drainingn` mechanics this replaces.

- [ ] **Step 6: Commit**

```bash
git add utils/submissions.py test/test_unit.py docs/EXAMPLES_schema.md wiki/pages/2026-07-18-direct-recovery-loop.md
git commit -m "feat(draining): submissions complete verb, draining status lines, docs"
```

---

## Post-plan (not tasks — controller/operator actions)

1. `/refresh-examples` once, after this plan AND the pending listNewDatasets schema edit both land.
2. **Live smoke before first production use** (spec §Testing): build/reuse a generic reco cnf, enqueue a draining campaign against a small real pattern, `submissions run --dry-run` to inspect the pending computation, then a 2-file real batch on FermiGrid; verify: worker log shows `files[i] = ...`, outputs land in SAM, `verify_files_row` closes the row complete. Also confirms the SAM metadata `create_datetime` key spelling the age gate relies on.
3. Deferred minors to carry into the final review: `process_row` messages still say "indices" for file rows; `print_status` hides campaigns when the rows table is empty (pre-existing).
