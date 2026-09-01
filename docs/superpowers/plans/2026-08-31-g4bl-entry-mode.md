# g4bl Entry Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revive g4bl as a first-class entry type in the prodtools direct backend so the existing MCP write surface (`push_cnf` + `run_submissions`) carries g4bl campaigns with zero new tools.

**Architecture:** A `"runner": "g4bl"` entry builds a self-describing cnf tarball (`work/` = deck dir copy + `jobpars.json`) instead of a mu2ejobdef; the worker's direct mode grows a `g4bl` dispatch branch that runs the proven 401e3da native-AL9 spack recipe and reuses the existing push helpers (`push_data`, `push_logs`, `_push_with_retry`, `_push_all`, `log_storage_location`). Ledger, slicing, recovery, and MCP are untouched.

**Tech Stack:** Python 3 (no new deps), `tarfile`, `spack g4beamline` (worker runtime only), unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-g4bl-entry-mode-design.md`

## Global Constraints

- No fallbacks: validate at the boundary, fail loudly. No default values for `g4bl_dir`, `main_input`, `events_per_job`, `njobs`.
- g4bl CLI overrides use `key=value` form, never `param key=value` (g4bl 3.08b rejects the `param` form on the command line).
- Worker env recipe verbatim from 401e3da: `unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE`, then `source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh`, then `eval "$(spack load --sh g4beamline)"`.
- Output names: `nts.mu2e.<desc>.<dsconf>.<%08d index>.root`, `log.mu2e.<desc>.<dsconf>.<%08d index>.log`. `First_Event = index * events_per_job + 1`.
- No SAM parents for g4bl: `track_parents=False`, no `parents_list.txt`.
- Tests run with `python -m pytest test/test_unit.py -k <pattern> -v` and must not require the Mu2e environment (mock subprocess; build tarballs in tmpdirs).
- Full suite (`python -m pytest test/test_unit.py`) must stay green after every task.
- Commits: `feat:`/`test:`/`docs:` prefix, subject <=72 chars, trailer lines:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF`.
  Do NOT `git push` (ssh-agent unreachable from tool shells).
- Grid rollout is out of code scope: the worker half ships in the next cvmfs release (v3.3.1). Local worker harness validates it pre-release (Task 5).

---

### Task 1: Entry detection + validation (submit half)

**Files:**
- Modify: `utils/json2jobdef.py` (`determine_job_type` ~line 395, `validate_required_fields` ~line 317)
- Test: `test/test_unit.py` (new class `TestG4blEntryValidation`)

**Interfaces:**
- Consumes: existing `validate_entry_value`, `ENTRY_VALUE_KEYS`, `validate_outloc` (already imported in `utils/json2jobdef.py`).
- Produces: `determine_job_type(config) -> 'g4bl'` when `config.get('runner') == 'g4bl'`; `_validate_g4bl_entry(config) -> None` (calls `sys.exit(str)` on any violation). Task 2 relies on both names exactly.

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py`, following the file's existing unittest style (import `utils.json2jobdef` the way neighboring classes do):

```python
class TestG4blEntryValidation(unittest.TestCase):
    """g4bl entry detection and boundary validation (json2jobdef)."""

    def _entry(self, **over):
        d = {
            'runner': 'g4bl',
            'desc': 'G4blSmoke', 'dsconf': 'TestConf', 'owner': 'testuser',
            'g4bl_dir': self.g4bl_dir, 'main_input': 'deck.in',
            'events_per_job': 100, 'njobs': 2,
            'outloc': {'nts.*.root': 'scratch'},
        }
        d.update(over)
        return d

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='g4bl_test_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.g4bl_dir = os.path.join(self.tmp, 'scripts')
        os.makedirs(os.path.join(self.g4bl_dir, 'Geometry'))
        Path(self.g4bl_dir, 'deck.in').write_text('# deck\n')
        Path(self.g4bl_dir, 'Geometry', 'g.txt').write_text('geom\n')

    def test_determine_job_type_g4bl(self):
        from utils.json2jobdef import determine_job_type
        self.assertEqual(determine_job_type(self._entry()), 'g4bl')

    def test_valid_entry_passes(self):
        from utils.json2jobdef import validate_required_fields
        validate_required_fields(self._entry())  # must not raise

    def test_missing_required_key_fails(self):
        from utils.json2jobdef import validate_required_fields
        for key in ('desc', 'dsconf', 'outloc', 'g4bl_dir',
                    'main_input', 'events_per_job', 'njobs'):
            e = self._entry()
            del e[key]
            with self.assertRaises(SystemExit, msg=key):
                validate_required_fields(e)

    def test_forbidden_key_fails(self):
        from utils.json2jobdef import validate_required_fields
        for key, val in (('fcl', 'x.fcl'), ('simjob_setup', '/cvmfs/x'),
                         ('code', 'c.tar'), ('input_data', {'a': 'b'}),
                         ('resampler_name', 'r'), ('pbeam', '1BB'),
                         ('generic_tarball', True), ('input_pattern', 'p'),
                         ('firstjob', 5), ('inloc', 'tape')):
            with self.assertRaises(SystemExit, msg=key):
                validate_required_fields(self._entry(**{key: val}))

    def test_nonpositive_counts_fail(self):
        from utils.json2jobdef import validate_required_fields
        with self.assertRaises(SystemExit):
            validate_required_fields(self._entry(njobs=0))
        with self.assertRaises(SystemExit):
            validate_required_fields(self._entry(events_per_job=-5))

    def test_missing_dir_and_deck_fail(self):
        from utils.json2jobdef import validate_required_fields
        with self.assertRaises(SystemExit):
            validate_required_fields(self._entry(g4bl_dir='/nonexistent/x'))
        with self.assertRaises(SystemExit):
            validate_required_fields(self._entry(main_input='absent.in'))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -k TestG4blEntryValidation -v`
Expected: FAIL — `determine_job_type` returns `'stage1'` for the g4bl entry; `validate_required_fields` exits on missing `fcl`.

- [ ] **Step 3: Implement**

In `utils/json2jobdef.py`:

a) `determine_job_type` — first check, plus docstring line `'g4bl' - G4Beamline jobs (runner: g4bl)`:

```python
    if config.get('runner') == 'g4bl':
        return 'g4bl'
```

b) New `_validate_g4bl_entry` directly above `validate_required_fields`:

```python
G4BL_FORBIDDEN_KEYS = ('fcl', 'simjob_setup', 'code', 'input_data',
                       'resampler_name', 'pbeam', 'generic_tarball',
                       'input_pattern', 'firstjob', 'inloc')

def _validate_g4bl_entry(config):
    """Boundary validation for runner: g4bl entries. g4bl is decoupled
    from Offline: no fcl, no Musing, no SAM inputs — presence of any
    art-pipeline key is a config error, not something to ignore."""
    for req in ('desc', 'dsconf', 'outloc', 'g4bl_dir', 'main_input',
                'events_per_job', 'njobs'):
        if not config.get(req):
            sys.exit(f"json2jobdef: g4bl entry missing required field: {req}")
    for key in G4BL_FORBIDDEN_KEYS:
        if key in config:
            sys.exit(f"json2jobdef: g4bl entry must not carry '{key}'")
    for key in ('events_per_job', 'njobs'):
        v = config[key]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            sys.exit(f"json2jobdef: g4bl entry '{key}' must be a "
                     f"positive integer, got {v!r}")
    g4bl_dir = Path(config['g4bl_dir'])
    if not g4bl_dir.is_dir():
        sys.exit(f"json2jobdef: g4bl_dir not found: {g4bl_dir}")
    if not (g4bl_dir / config['main_input']).is_file():
        sys.exit(f"json2jobdef: main_input not found: "
                 f"{g4bl_dir / config['main_input']}")
    try:
        for key in ENTRY_VALUE_KEYS:
            if key in config:
                validate_entry_value(key, config[key])
        validate_outloc(config['outloc'])
    except ValueError as exc:
        sys.exit(f"json2jobdef: {exc}")
```

c) `validate_required_fields` — route g4bl entries before the fcl check:

```python
    if determine_job_type(config) == 'g4bl':
        return _validate_g4bl_entry(config)
```

(`validate_era_agreement` needs no change: it already returns early when `simjob_setup` is absent.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -k TestG4blEntryValidation -v` then the full suite `python -m pytest test/test_unit.py -q`.
Expected: new tests PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add utils/json2jobdef.py test/test_unit.py
git commit -m "feat(g4bl): detect and validate runner: g4bl entries"
```

---

### Task 2: g4bl cnf builder + jobdesc projection (submit half)

**Files:**
- Modify: `utils/json2jobdef.py` (`process_single_entry` ~line 750, `build_jobdesc` ~line 491)
- Test: `test/test_unit.py` (new class `TestG4blBuilder`)

**Interfaces:**
- Consumes: Task 1's `determine_job_type == 'g4bl'`; existing `get_parfile_name(config)` (returns `cnf.<owner>.<desc>.<dsconf>.0.tar`).
- Produces: `_build_g4bl_tarball(config) -> None` writing the cnf into cwd; ledger entries whose JSON carries `"runner": "g4bl"` (Task 4's worker reads exactly this key from the ops jobdesc).

- [ ] **Step 1: Write the failing tests**

```python
class TestG4blBuilder(unittest.TestCase):
    """g4bl cnf tarball contents and jobdesc projection."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='g4bl_build_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.g4bl_dir = os.path.join(self.tmp, 'scripts')
        os.makedirs(os.path.join(self.g4bl_dir, 'Geometry'))
        Path(self.g4bl_dir, 'deck.in').write_text('# deck\n')
        Path(self.g4bl_dir, 'Geometry', 'g.txt').write_text('geom\n')
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)
        self.config = {
            'runner': 'g4bl',
            'desc': 'G4blSmoke', 'dsconf': 'TestConf', 'owner': 'testuser',
            'g4bl_dir': self.g4bl_dir, 'main_input': 'deck.in',
            'events_per_job': 100, 'njobs': 2, 'inloc': 'none',
            'outloc': {'nts.*.root': 'scratch'},
        }

    def test_tarball_contents(self):
        from utils.json2jobdef import _build_g4bl_tarball, get_parfile_name
        _build_g4bl_tarball(self.config)
        name = get_parfile_name(self.config)
        self.assertEqual(name, 'cnf.testuser.G4blSmoke.TestConf.0.tar')
        with tarfile.open(name) as t:
            members = set(t.getnames())
            self.assertIn('jobpars.json', members)
            self.assertIn('work/deck.in', members)
            self.assertIn('work/Geometry/g.txt', members)
            jp = json.load(t.extractfile('jobpars.json'))
        self.assertEqual(jp, {'runner': 'g4bl', 'desc': 'G4blSmoke',
                              'dsconf': 'TestConf', 'main_input': 'deck.in',
                              'events_per_job': 100, 'njobs': 2})

    def test_build_jobdesc_carries_runner(self):
        from utils.json2jobdef import build_jobdesc
        entry = build_jobdesc(self.config)
        self.assertEqual(entry['runner'], 'g4bl')
        self.assertEqual(entry['njobs'], 2)
        self.assertEqual(entry['tarball'],
                         'cnf.testuser.G4blSmoke.TestConf.0.tar')
        self.assertEqual(entry['outputs'],
                         [{'dataset': 'nts.*.root', 'location': 'scratch'}])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -k TestG4blBuilder -v`
Expected: FAIL — `_build_g4bl_tarball` does not exist; `build_jobdesc` result has no `runner` key.

- [ ] **Step 3: Implement**

In `utils/json2jobdef.py` (ensure `tarfile` and `tempfile` are imported at the top; both are stdlib):

a) New builder near `build_jobdef`:

```python
def _build_g4bl_tarball(config):
    """Pack the self-describing g4bl cnf: work/ (a copy of g4bl_dir)
    plus jobpars.json. No fcl, no mu2ejobdef — the worker's g4bl
    branch consumes this shape directly (spec section 2)."""
    parfile_name = get_parfile_name(config)
    jobpars = {
        'runner': 'g4bl',
        'desc': config['desc'],
        'dsconf': config['dsconf'],
        'main_input': config['main_input'],
        'events_per_job': config['events_per_job'],
        'njobs': config['njobs'],
    }
    with tempfile.TemporaryDirectory(prefix='g4bl_jobdef_') as td:
        jp = Path(td) / 'jobpars.json'
        jp.write_text(json.dumps(jobpars, indent=2) + '\n')
        with tarfile.open(parfile_name, 'w') as tar:
            tar.add(config['g4bl_dir'], arcname='work')
            tar.add(jp, arcname='jobpars.json')
    print(f"Created {parfile_name}")
```

b) `process_single_entry` — branch after the `config['njobs'] = config.get('njobs', -1)` defaults block, before the generic-tarball/auto-desc/inputs flow:

```python
    if determine_job_type(config) == 'g4bl':
        if extend:
            sys.exit("json2jobdef: --extend is not supported for g4bl "
                     "entries (no SAM inputs to exclude)")
        _build_g4bl_tarball(config)
    else:
        ...  # existing generic/auto-desc/extend/inputs/_build_job_args/
             # build_jobdef flow, unchanged, indented into this else
```

The tail of `process_single_entry` (`_pushout_to_sam`, `enqueue`) is shared and stays outside the branch — that is the whole point: `--prod --enqueue` and the MCP `push_cnf` path work for g4bl with no further changes. `validate_output_filenames` needs no attention: it lives inside `build_jobdef` (utils/json2jobdef.py:473), which only the non-g4bl branch calls. Ordering note: `validate_required_fields` runs before the `config['inloc'] = config.get('inloc', 'none')` default, so forbidding `inloc` (Task 1) rejects only a USER-supplied value; the default applied afterwards is what `build_jobdesc` reads.

c) `build_jobdesc` — right after `jobdef_entry` is first constructed:

```python
    if config.get('runner') == 'g4bl':
        jobdef_entry['runner'] = 'g4bl'
```

No other `build_jobdesc` change: g4bl validation guarantees `njobs` is a positive int, so the `njobs == -1` Mu2eJobPars query is unreachable, and `firstjob`/`generic_tarball`/`input_pattern` are forbidden keys.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -k TestG4blBuilder -v`, then full suite.
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add utils/json2jobdef.py test/test_unit.py
git commit -m "feat(g4bl): build self-describing g4bl cnf; runner rides the jobdesc"
```

---

### Task 3: Enqueue preflight compatibility

**Files:**
- Test: `test/test_unit.py` (new class `TestG4blPreflight`)

**Interfaces:**
- Consumes: Task 2's `_build_g4bl_tarball`; existing `utils.check_inputs.check_inputs(tarball_path, inloc)`.
- Produces: proof (as a pinned test) that `enqueue_entry`'s gate passes a g4bl cnf — `check_inputs` returns `(True, [])` because the g4bl `jobpars.json` has no `tbs` block, so `_tbs_of` yields `{}` and both input sections are empty.

- [ ] **Step 1: Write the failing-or-passing test (pin the behavior)**

```python
class TestG4blPreflight(unittest.TestCase):
    """A g4bl cnf must sail through the enqueue input gate: no tbs
    block means no inputs to check. Pinned so a future check_inputs
    change that assumes a mu2ejobdef shape fails HERE, not at enqueue."""

    def test_check_inputs_passes_g4bl_cnf(self):
        from utils.json2jobdef import _build_g4bl_tarball
        from utils.check_inputs import check_inputs
        tmp = tempfile.mkdtemp(prefix='g4bl_pre_')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        g4bl_dir = os.path.join(tmp, 'scripts')
        os.makedirs(g4bl_dir)
        Path(g4bl_dir, 'deck.in').write_text('# deck\n')
        cwd = os.getcwd()
        os.chdir(tmp)
        self.addCleanup(os.chdir, cwd)
        _build_g4bl_tarball({
            'runner': 'g4bl', 'desc': 'G4blSmoke', 'dsconf': 'TestConf',
            'owner': 'testuser', 'g4bl_dir': g4bl_dir,
            'main_input': 'deck.in', 'events_per_job': 10, 'njobs': 1,
        })
        ok, problems = check_inputs(
            'cnf.testuser.G4blSmoke.TestConf.0.tar', 'none',
            sam_sizes=lambda ds: {})
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest test/test_unit.py -k TestG4blPreflight -v`
Expected: PASS if `Mu2eJobPars(...).json_data` tolerates the g4bl jobpars shape. If it FAILS (e.g. `Mu2eJobPars.__init__` in `utils/jobquery.py` demands mu2ejobdef keys), fix by making the failing accessor tolerate a jobpars.json without `tbs` — the fix belongs in `check_inputs._tbs_of` (fall back to `{}` is already its contract via `.get('tbs', {})`), NOT by adding a fake `tbs` to the g4bl jobpars.

- [ ] **Step 3: Full suite**

Run: `python -m pytest test/test_unit.py -q`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add test/test_unit.py
git commit -m "test(g4bl): pin check_inputs pass-through for g4bl cnfs"
```

---

### Task 4: Worker g4bl mode (runmu2e direct dispatch)

**Files:**
- Modify: `utils/runmu2e.py` (`validate_jobdesc` ~line 111, `_direct_dispatch` ~line 687; new helpers above `_direct_dispatch`)
- Test: `test/test_unit.py` (new class `TestG4blWorker`)

**Interfaces:**
- Consumes: ops jobdesc carrying `runner: "g4bl"`, `tarball`, `outputs`, `njobs` (Task 2); existing helpers `_require_fields`, `_emit_manifest`, `log_storage_location`, `push_data`, `push_logs`, `_push_with_retry`, `_push_all`, `Mu2eName`.
- Produces: `validate_jobdesc -> 'g4bl'`; `_g4bl_script(main_input, first_event, num_events, histo_path) -> str`; `_run_g4bl_job(jobdesc, index) -> (histo_file, log_file, job_failed)`; `_dispatch_g4bl(args, jobdesc, index) -> job_failed` wired into `_direct_dispatch`.

- [ ] **Step 1: Write the failing tests**

```python
class TestG4blWorker(unittest.TestCase):
    """Worker-side g4bl mode: jobdesc validation, command construction,
    run mechanics with a stubbed g4bl, dispatch tail routing."""

    def _jobdesc(self, **over):
        d = {'runner': 'g4bl',
             'tarball': 'cnf.testuser.G4blSmoke.TestConf.0.tar',
             'outputs': [{'dataset': 'nts.*.root', 'location': 'scratch'}],
             'njobs': 2}
        d.update(over)
        return d

    def test_validate_jobdesc_g4bl(self):
        from utils import runmu2e
        self.assertEqual(runmu2e.validate_jobdesc(self._jobdesc()), 'g4bl')

    def test_validate_jobdesc_g4bl_missing_field(self):
        from utils import runmu2e
        bad = self._jobdesc()
        del bad['njobs']
        with self.assertRaises(SystemExit):
            runmu2e.validate_jobdesc(bad)

    def test_g4bl_script_form(self):
        from utils.runmu2e import _g4bl_script
        s = _g4bl_script('deck.in', 101, 100, '/abs/nts.x.root')
        self.assertIn('unset SPACK_ENV PYTHONHOME PYTHONPATH '
                      'PYTHONNOUSERSITE', s)
        self.assertIn('eval "$(spack load --sh g4beamline)"', s)
        self.assertIn('viewer=none', s)
        self.assertIn('First_Event=101', s)
        self.assertIn('Num_Events=100', s)
        self.assertIn('histoFile=/abs/nts.x.root', s)
        self.assertNotIn('param ', s)

    def _make_cnf(self, tmp):
        g4bl_dir = os.path.join(tmp, 'scripts')
        os.makedirs(g4bl_dir)
        Path(g4bl_dir, 'deck.in').write_text('# deck\n')
        from utils.json2jobdef import _build_g4bl_tarball
        _build_g4bl_tarball({
            'runner': 'g4bl', 'desc': 'G4blSmoke', 'dsconf': 'TestConf',
            'owner': 'testuser', 'g4bl_dir': g4bl_dir,
            'main_input': 'deck.in', 'events_per_job': 100, 'njobs': 2})

    def test_run_g4bl_job_names_and_first_event(self):
        from utils import runmu2e
        tmp = tempfile.mkdtemp(prefix='g4bl_run_')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cwd = os.getcwd()
        os.chdir(tmp)
        self.addCleanup(os.chdir, cwd)
        self._make_cnf(tmp)
        captured = {}

        class FakeProc:
            stdout = io.StringIO("g4bl fake output\n")
            def wait(self):
                return 0

        def fake_popen(cmd, **kw):
            captured['script'] = cmd[2]
            return FakeProc()

        with patch.object(runmu2e.subprocess, 'Popen', fake_popen):
            histo, log, failed = runmu2e._run_g4bl_job(self._jobdesc(), 3)
        self.assertEqual(histo, 'nts.mu2e.G4blSmoke.TestConf.00000003.root')
        self.assertEqual(log, 'log.mu2e.G4blSmoke.TestConf.00000003.log')
        self.assertFalse(failed)
        self.assertIn('First_Event=301', captured['script'])
        self.assertIn('g4bl fake output', Path(log).read_text())

    def test_dispatch_g4bl_rejects_draining(self):
        from utils import runmu2e
        ops = {'jobdesc': self._jobdesc(), 'files': ['a.art']}
        args = types.SimpleNamespace(dry_run=True)
        with self.assertRaises(SystemExit):
            runmu2e._direct_dispatch(args, ops, 0)

    def test_dispatch_g4bl_dry_run_skips_pushes(self):
        from utils import runmu2e
        args = types.SimpleNamespace(dry_run=True)
        with patch.object(runmu2e, '_run_g4bl_job',
                          return_value=('nts.mu2e.G4blSmoke.TestConf.00000000.root',
                                        'log.mu2e.G4blSmoke.TestConf.00000000.log',
                                        False)), \
             patch.object(runmu2e, '_push_all') as push_all:
            failed = runmu2e._dispatch_g4bl(args, self._jobdesc(), 0)
        self.assertFalse(failed)
        push_all.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -k TestG4blWorker -v`
Expected: FAIL — `validate_jobdesc` falls through to normal mode and exits on missing `inloc`; `_g4bl_script`, `_run_g4bl_job`, `_dispatch_g4bl` undefined.

- [ ] **Step 3: Implement**

In `utils/runmu2e.py` (ensure `tarfile` and `shlex` are imported; both stdlib):

a) `validate_jobdesc` — insert after the firstjob/njobs consistency check, BEFORE the direct-input check:

```python
    # g4bl mode: runner marker from the entry. No inloc (no inputs at
    # all), no fcl — the tarball is self-describing (spec 2026-08-31).
    if jobdesc.get('runner') == 'g4bl':
        _require_fields(jobdesc, ['tarball', 'outputs', 'njobs'],
                        'g4bl mode')
        return 'g4bl'
```

Update the docstring's Returns line to `'g4bl', 'direct_input' or False`.

b) New helpers above `_direct_dispatch`:

```python
def _g4bl_script(main_input, first_event, num_events, histo_path):
    """Bash for one g4bl process — the proven 401e3da recipe: native
    AL9 spack, selective env unset (muse setup ops leaves SPACK_ENV
    pointing at ops-019, where g4beamline does not exist), and CLI
    `key=value` overrides (g4bl 3.08b rejects `param k=v` on the
    command line; that form is input-file syntax only)."""
    return (
        "unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE\n"
        "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh"
        " > /dev/null 2>&1\n"
        'eval "$(spack load --sh g4beamline)"\n'
        "cd work\n"
        f"g4bl {shlex.quote(main_input)} viewer=none "
        f"First_Event={first_event} Num_Events={num_events} "
        f"histoFile={shlex.quote(histo_path)}"
    )


def _run_g4bl_job(jobdesc, index):
    """Extract the g4bl cnf (already fetched into cwd by _direct_main),
    run one g4bl process, stream its output to both stdout and the
    SAM-named log. Returns (histo_file, log_file, job_failed).
    RuntimeError on prep failures — nothing ran, so no log to push."""
    tarball = Path(jobdesc['tarball']).name
    if not Path(tarball).is_file():
        raise RuntimeError(f"g4bl cnf not found in cwd: {tarball}")
    with tarfile.open(tarball) as t:
        t.extractall('.')
    if not Path('work').is_dir():
        raise RuntimeError(f"tarball missing 'work/' subdir: {tarball}")
    if not Path('jobpars.json').is_file():
        raise RuntimeError(f"tarball missing jobpars.json: {tarball}")
    jp = json.loads(Path('jobpars.json').read_text())
    main_input = jp['main_input']
    events_per_job = int(jp['events_per_job'])
    if not (Path('work') / main_input).is_file():
        raise RuntimeError(f"main_input not found: work/{main_input}")
    sequencer = f"{index:08d}"
    first_event = index * events_per_job + 1
    histo_file = f"nts.mu2e.{jp['desc']}.{jp['dsconf']}.{sequencer}.root"
    log_file = f"log.mu2e.{jp['desc']}.{jp['dsconf']}.{sequencer}.log"
    script = _g4bl_script(main_input, first_event, events_per_job,
                          os.path.abspath(histo_file))
    print(f"[g4bl] events_per_job={events_per_job} "
          f"first_event={first_event} histo={histo_file}")
    proc = subprocess.Popen(['bash', '-c', script],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    with open(log_file, 'w') as log_f:
        for line in proc.stdout:
            log_f.write(line)
            sys.stdout.write(line)
            sys.stdout.flush()
    proc.stdout.close()
    rc = proc.wait()
    return histo_file, log_file, rc != 0


def _dispatch_g4bl(args, jobdesc, index):
    """g4bl analog of the jobdef dispatch tail: run, manifest, push.
    No fcl, no mu2e -c, no SAM parents (g4bl jobs have no SAM inputs).
    pushOutput comes from the worker bootstrap's `setup OfflineOps`
    (bin/runjob.sh), so no simjob_setup is passed."""
    outputs = jobdesc['outputs']
    histo_file, log_file, job_failed = _run_g4bl_job(jobdesc, index)

    manifest_files = ([histo_file]
                      if not job_failed and Path(histo_file).exists()
                      else [])
    if Path(log_file).exists():
        _emit_manifest(log_file, manifest_files)

    log_location = log_storage_location(
        outputs, owner=Mu2eName(jobdesc['tarball']).owner)

    def data_push():
        if job_failed:
            return
        _push_with_retry(push_data, outputs, "", track_parents=False)

    def log_push():
        _push_with_retry(push_logs, log_file=log_file,
                         location=log_location)

    if args.dry_run:
        datasets = ('none (job failed)' if job_failed else histo_file)
        print(f"[g4bl] DRY RUN — would push data: {datasets}; "
              f"would push log to '{log_location}'. Skipping pushes.")
        return job_failed

    if job_failed:
        print("[g4bl] g4bl failed — skipping data push, still pushing log")
    _push_all(data_push, log_push)
    return job_failed
```

c) `_direct_dispatch` — right after `mode = validate_jobdesc(jobdesc)`:

```python
    if mode == 'g4bl':
        if files is not None:
            print("ERROR: ops carries a files list but the jobdesc is "
                  "g4bl mode — g4bl entries have no input files and "
                  "take no draining batches.")
            sys.exit(1)
        return _dispatch_g4bl(args, jobdesc, index)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -k TestG4blWorker -v`, then the full suite.
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add utils/runmu2e.py test/test_unit.py
git commit -m "feat(g4bl): worker g4bl dispatch — 401e3da recipe, shared push tail"
```

---

### Task 5: Wrapper + MCP push_cnf compatibility

**Files:**
- Modify: `bin/json2jobdef` (env guard, ~line 40)
- Modify: `utils/json2jobdef.py` (`process_single_entry`)
- Modify: `mcp/src/prodtools_mcp_write/tools.py` (`_select_push_params`, ~line 82)
- Test: `test/test_unit.py` (new class `TestG4blPushCnfParams`)

**Interfaces:**
- Consumes: Task 1's `determine_job_type == 'g4bl'`.
- Produces: `_select_push_params(json_path, desc, dsconf) -> (None, tarball_desc)` for g4bl entries (`runner.run_cli` / `_musing_clause` already treat a falsy `simjob_setup` as "no Musing step"); `bin/json2jobdef` runnable from a bare `muse setup ops` env for g4bl entries.

Two boundaries currently assume every entry has a Musing:

1. `bin/json2jobdef` exits unless `command -v mu2e` succeeds — but `mu2e` comes from a SimJob Musing, which a g4bl build neither needs nor should require.
2. MCP `_select_push_params` raises when the entry `has no simjob_setup field`, so `push_cnf` refuses g4bl entries before the build starts.

- [ ] **Step 1: Write the failing test**

```python
class TestG4blPushCnfParams(unittest.TestCase):
    """MCP push_cnf parameter selection for g4bl entries: no Musing
    to source, so simjob_setup comes back None (falsy -> _musing_clause
    emits no source step)."""

    def test_select_push_params_g4bl(self):
        import importlib
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'mcp', 'src'))
        self.addCleanup(sys.path.pop, 0)
        tools = importlib.import_module('prodtools_mcp_write.tools')
        tmp = tempfile.mkdtemp(prefix='g4bl_push_')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = os.path.join(tmp, 'g4bl.json')
        Path(cfg).write_text(json.dumps([{
            'runner': 'g4bl', 'desc': 'G4blSmoke', 'dsconf': 'TestConf',
            'owner': 'testuser', 'g4bl_dir': tmp, 'main_input': 'deck.in',
            'events_per_job': 10, 'njobs': 1,
            'outloc': {'nts.*.root': 'scratch'}}]))
        setup, tarball_desc = tools._select_push_params(
            cfg, 'G4blSmoke', 'TestConf')
        self.assertIsNone(setup)
        self.assertEqual(tarball_desc, 'G4blSmoke')
```

(Adapt the import mechanics to however existing tests load the MCP write modules — if `test/test_unit.py` already imports `prodtools_mcp_write`, reuse that pattern instead of the `sys.path` insert.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_unit.py -k TestG4blPushCnfParams -v`
Expected: FAIL with `ValueError: ... has no simjob_setup field`.

- [ ] **Step 3: Implement**

a) `mcp/src/prodtools_mcp_write/tools.py`, `_select_push_params` — replace the unconditional refusal:

```python
    simjob_setup = entry.get('simjob_setup')
    if not simjob_setup and entry.get('runner') != 'g4bl':
        raise ValueError(
            f"push_cnf: entry matching desc={desc!r} dsconf={dsconf!r} in "
            f"{json_path!r} has no simjob_setup field")
    if entry.get('runner') == 'g4bl':
        simjob_setup = None   # no Musing: _musing_clause('') is a no-op
```

(Note: a `--code` tarball entry also lacks `simjob_setup`; do not widen this beyond g4bl here — that is a separate, pre-existing limitation.)

b) `bin/json2jobdef` — require only the ops env at the wrapper; move the `mu2e` requirement into Python where the entry type is known:

```bash
if [[ -z "$MUSE_DIR" ]]; then
    echo "Error: Mu2e environment not set up. Run: muse setup ops (art entries additionally need a Musing: muse setup SimJob <tag> or source a Musing setup.sh)"
    exit 1
fi
```

c) `utils/json2jobdef.py`, `process_single_entry` — inside the non-g4bl branch added in Task 2, before `_build_job_args`:

```python
        if shutil.which('mu2e') is None:
            sys.exit("json2jobdef: 'mu2e' not on PATH — art entries "
                     "need a Musing (muse setup SimJob <tag> or source "
                     "a Musing setup.sh)")
```

(`import shutil` at the top if not already present.) This preserves the old wrapper error for art entries at the same effective boundary while letting g4bl builds run from a bare ops env.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -k TestG4blPushCnfParams -v`, then the full suite.
Expected: PASS, no regressions (existing tests that call `process_single_entry` may need `shutil.which` patched — mock it to return `'/usr/bin/mu2e'` in those fixtures if they run without the Mu2e env).

- [ ] **Step 5: Commit**

```bash
git add bin/json2jobdef utils/json2jobdef.py mcp/src/prodtools_mcp_write/tools.py test/test_unit.py
git commit -m "feat(g4bl): push_cnf and wrapper accept Musing-less g4bl entries"
```

---

### Task 6: Local end-to-end smoke (real g4bl, real pushes as self)

Manual verification — run in the main session (needs Mu2e env + user token), not a subagent. No code changes expected; any failure here reopens Tasks 2/4/5.

- [ ] **Step 1: Verify no cnf-name collision, then build a real cnf**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/g4blsmoke  # mkdir -p first
samweb locate-file cnf.oksuzian.G4blSmoke.MCPTest005.0.tar || echo FREE
```

Write `g4bl_smoke.json`:

```json
[{
  "runner": "g4bl",
  "desc": "G4blSmoke", "dsconf": "MCPTest005", "owner": "oksuzian",
  "g4bl_dir": "/exp/mu2e/app/users/oksuzian/G4BeamlineScripts",
  "main_input": "Mu2E.in",
  "events_per_job": 10, "njobs": 1,
  "outloc": {"nts.*.root": "scratch"}
}]
```

Build (local, no SAM): `/mu2e-run json2jobdef --json <abs>/g4bl_smoke.json --desc G4blSmoke --dsconf MCPTest005`. Verify tarball members (`tar tf`): `work/Mu2E.in`, `work/Geometry/...`, `jobpars.json`.

- [ ] **Step 2: Run the worker branch via the local harness**

Per memory `reference_local_worker_harness.md`: per-job dir, ops JSON `{"jobs":[0],"inspec":{},"jobdesc":{...entry...,"prodtools_dir":"<repo>"}}` with the entry from `build_jobdesc` (carries `runner: "g4bl"`), env `CONDOR_DIR_INPUT` (cnf+ops), `PROCESS=0`, `MU2EGRID_JOBDEF`, `MU2EGRID_OPSJSON`, `JSB_TMP` with stdout redirected to `$JSB_TMP/JOBSUB_LOG_FILE`. First with `--dry-run` (g4bl RUNS, pushes skipped): expect `nts.mu2e.G4blSmoke.MCPTest005.00000000.root` non-trivial size, `log.mu2e...log` containing g4bl output + `mu2egrid manifest`, dry-run line naming push targets with log location `scratch`.

- [ ] **Step 3: Real pushes as self**

Re-run without `--dry-run` (fresh job dir; user bearer token present). Verify: `samweb locate-file nts.mu2e.G4blSmoke.MCPTest005.00000000.root` and the log file both in SAM under scratch datasets (`/pnfs/mu2e/scratch/datasets/...`). Judge log routing by the `processing: <class> log...` line (writeLog artifact caveat).

- [ ] **Step 4: Record**

Append results (file sizes, SAM paths) to the wiki update in Task 6. No commit from this task.

---

### Task 7: Docs — wiki page, EXAMPLES schema, memory

**Files:**
- Modify: `wiki/pages/g4bl-runner.md` (currently documents the retired path as current)
- Modify: `docs/EXAMPLES_schema.md` (add g4bl entry shape so `/refresh-examples` picks it up)
- Memory: update `project_g4bl_entry_mode.md` status

- [ ] **Step 1: Update wiki/pages/g4bl-runner.md**

Rewrite the "Current execution path" section: g4bl is a direct-backend entry mode (`runner: "g4bl"`, spec + this plan); the mu2ejobsub-era `process_g4bl_jobdef` path is historical (keep the 401e3da recipe table — the recipe itself is what the new worker branch runs). Add the smoke results from Task 5. Update `updated:` frontmatter.

- [ ] **Step 2: Add the g4bl entry shape to docs/EXAMPLES_schema.md**

One subsection under the entry-shape documentation: the JSON keys (`runner`, `g4bl_dir`, `main_input`, `events_per_job`, `njobs`, `outloc`), the canonical invocation (`json2jobdef --json ... --desc ... --dsconf ...`; production: `--prod --enqueue --slice-size N`), and the note that grid execution needs prodtools >= v3.3.1 on cvmfs. Then run `/refresh-examples`.

- [ ] **Step 3: Update memory**

`project_g4bl_entry_mode.md`: status -> implemented (commit SHAs), smoke result, grid pending v3.3.1 release.

- [ ] **Step 4: Commit**

```bash
git add wiki/pages/g4bl-runner.md wiki/index.md wiki/log.md docs/EXAMPLES_schema.md EXAMPLES.md
git commit -m "docs(g4bl): entry-mode wiki + EXAMPLES schema for runner: g4bl"
```
