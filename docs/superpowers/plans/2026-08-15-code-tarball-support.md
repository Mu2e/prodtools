# Custom Code Tarball (`--code`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a prodtools job run against an Offline build that is not on /cvmfs — a local Muse build tarred with `muse tarball` — both locally via `bin/runlocal` and on the grid via the direct submission backend.

**Architecture:** Sidecar delivery, copied from `mu2eprodsys --code`. The cnf records a *relative* setup path (`Code/setup.sh`), which is the sole signal that this is a code-mode cnf; the entry records the *absolute* path to `Code.tar.bz2`. On the grid, jobsub ships the tarball via `--tar_file_name dropbox://` (RCDS/cvmfs, published once, no per-job copy) and the worker resolves against `$INPUT_TAR_DIR_LOCAL`. Locally, `bin/runlocal` unpacks the same tarball once and resolves against that directory. One function, `resolve_setup`, performs that relative→absolute step for both.

**Tech Stack:** Python 3 stdlib only (`tarfile`, `hashlib`, `argparse`, `unittest`). No new dependencies. jobsub_lite `--tar_file_name`. Muse `muse tarball`.

**Spec:** `docs/superpowers/specs/2026-08-15-code-tarball-support-design.md` (commit `5035e39`). Read it before Task 1 — it carries the upstream evidence and the reasoning behind each decision.

## Global Constraints

- Branch is `code-tarball`, based on `aded95b`. **Do NOT `git push`** — the user pushes from their own interactive shell.
- Test suite: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`. Baseline before this plan: **1133 tests, OK (skipped=1)**. Every task must leave the suite green.
- All new tests go in `test/test_unit.py`. That file stubs `samweb_client`/`ifdh` in-process (`test/test_unit.py:38-47`); no test may spawn a subprocess, touch /cvmfs, or hit the network.
- The relative setup member name is exactly `Code/setup.sh`. This string comes from upstream (`mu2ejobdef:45`, `mu2eprodsys:337`) and from `museTarball.sh`, which writes everything under `Code/`. Do not invent a different name.
- **No fallbacks for missing required data.** A relative setup with no code root must raise, never silently fall back to a /cvmfs path.
- `code` and `simjob_setup` are mutually exclusive: exactly one per entry.
- Nothing in this plan pushes to SAM, submits grid jobs, or runs as `mu2epro`. Task 8 is the only task with live commands, and it runs as the current user.

---

## File Structure

| File | Responsibility in this plan |
|---|---|
| `utils/runmu2e.py` | Owns `resolve_setup` — the one relative→absolute step — and applies it where the cnf's setup is read. |
| `utils/prod_utils.py` | Gains `sha256_file`, the single hashing helper shared by the cnf builder and the submit-time gate. |
| `utils/jobdef.py` | Builds the cnf: validates the code tarball, writes `setup: "Code/setup.sh"` and `code_ref`. |
| `utils/jobdesc.py` | Entry grammar: `code_of` accessor, `code` validation, editable-key whitelist. |
| `utils/json2jobdef.py` | Config→cnf and config→entry projection: exactly-one rule, `code` pass-through. |
| `utils/jobsub_argv.py` | Adds `--tar_file_name dropbox://<tarball>` to the submit argv. |
| `utils/submit.py` | Passes the entry's code tarball to the argv builder; runs the pre-flight gate. |
| `utils/check_inputs.py` | Gains `check_code_tarball` — binds the entry's tarball to the cnf's `code_ref`. |
| `utils/runlocal.py` | Local runner: `--code` on the driver, `--code-root` on children, one shared unpack. |
| `utils/jobquery.py` | Truthful `codesize`, removal of the wrong `--extract-code`, `recipe()` reports code mode. |
| `docs/EXAMPLES_schema.md` | Documents the feature so `/refresh-examples` regenerates `EXAMPLES.md` with it. |
| `test/test_unit.py` | All unit tests. |

---

### Task 1: `resolve_setup` and the setup read path

The one function everything else wires into. Nothing works before this exists.

**Files:**
- Modify: `utils/runmu2e.py:64-77` (`_extract_simjob_setup`), `utils/runmu2e.py:153`, `utils/runmu2e.py:273`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `utils.runmu2e.CODE_SETUP_REL: str` — the constant `'Code/setup.sh'`.
  - `utils.runmu2e.resolve_setup(jp_setup: str, code_root: str | None = None) -> str`
  - `utils.runmu2e._extract_simjob_setup(tarball, jp=None, code_root=None) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py`:

```python
class TestResolveSetup(unittest.TestCase):
    """resolve_setup is the single relative->absolute step for a
    code-mode cnf. Absolute setup = a /cvmfs Musing; relative setup =
    the Offline build travels as a separate tarball."""

    def test_absolute_setup_passes_through(self):
        from utils.runmu2e import resolve_setup
        path = '/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Baq/setup.sh'
        self.assertEqual(resolve_setup(path), path)

    def test_absolute_setup_ignores_code_root(self):
        from utils.runmu2e import resolve_setup
        path = '/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Baq/setup.sh'
        self.assertEqual(resolve_setup(path, code_root='/srv/rcds'), path)

    def test_relative_setup_joins_code_root(self):
        from utils.runmu2e import resolve_setup
        self.assertEqual(resolve_setup('Code/setup.sh', code_root='/srv/rcds'),
                         '/srv/rcds/Code/setup.sh')

    def test_relative_setup_without_code_root_raises(self):
        from utils.runmu2e import resolve_setup
        with self.assertRaises(ValueError) as ctx:
            resolve_setup('Code/setup.sh')
        # The message must name both recovery paths, because the two
        # callers fail for different reasons.
        self.assertIn('INPUT_TAR_DIR_LOCAL', str(ctx.exception))
        self.assertIn('--code', str(ctx.exception))

    def test_relative_setup_with_empty_code_root_raises(self):
        # os.environ.get returns '' for an exported-but-empty variable;
        # that must fail like a missing one, not join to 'Code/setup.sh'.
        from utils.runmu2e import resolve_setup
        with self.assertRaises(ValueError):
            resolve_setup('Code/setup.sh', code_root='')

    def test_empty_setup_raises(self):
        from utils.runmu2e import resolve_setup
        with self.assertRaises(ValueError):
            resolve_setup('')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestResolveSetup`
Expected: FAIL with `ImportError: cannot import name 'resolve_setup'`

- [ ] **Step 3: Implement `resolve_setup`**

In `utils/runmu2e.py`, above `_extract_simjob_setup`:

```python
# The relative setup path a code-mode cnf carries, and the layout
# `muse tarball` produces. Same string upstream uses: mu2ejobdef:45
# (filename_tarsetup) and mu2eprodsys:337 (MU2EGRID_USERSETUP).
CODE_SETUP_REL = 'Code/setup.sh'


def resolve_setup(jp_setup, code_root=None):
    """The absolute path of the script to source for this job.

    An ABSOLUTE `jp_setup` is a /cvmfs Musing: returned unchanged, and
    `code_root` is ignored. A RELATIVE one means the cnf was built with
    `--code` and its Offline build ships as a separate tarball; it needs
    `code_root` and is joined onto it.

    Raises rather than falling back. A code-mode job that quietly
    sourced some /cvmfs release would run the WRONG Offline and report
    success, which is the failure this whole feature exists to avoid.
    """
    if not jp_setup:
        raise ValueError(
            "cnf jobpars carries no 'setup' — the tarball is malformed")
    if os.path.isabs(jp_setup):
        return jp_setup
    if not code_root:
        raise ValueError(
            f"cnf setup {jp_setup!r} is relative, so this cnf was built "
            f"with --code and its Offline build travels as a separate "
            f"tarball, but no code root was given. On the grid that "
            f"means $INPUT_TAR_DIR_LOCAL is unset — was --tar_file_name "
            f"passed to jobsub_submit? Locally, pass --code to "
            f"bin/runlocal.")
    return os.path.join(code_root, jp_setup)
```

`os` is already imported in `runmu2e.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestResolveSetup`
Expected: PASS, 6 tests

- [ ] **Step 5: Wire it into the setup read path**

In `utils/runmu2e.py`, change `_extract_simjob_setup` (currently at line 64) to take and apply a code root:

```python
def _extract_simjob_setup(tarball, jp=None, code_root=None):
    """Read the setup-script path from a cnf.*.tar's jobpars.json via
    Mu2eJobPars (pass a pre-built instance to avoid re-parsing the
    tarball) and resolve it through `resolve_setup`.

    Re-raises with a clear context line on the realistic failure modes
    (bad tarball, missing key, missing file).
    """
    try:
        jp = jp if jp is not None else Mu2eJobPars(tarball)
        setup = resolve_setup(jp.setup(), code_root=code_root)
        print(f"Job setup script: {setup}")
        return setup
    except (tarfile.TarError, KeyError, FileNotFoundError, OSError) as e:
        print(f"ERROR: Failed to get job setup information from {tarball}: {e}")
        raise
```

Note `ValueError` from `resolve_setup` is deliberately NOT swallowed by that `except` — it propagates with its own explanatory message.

Add a helper directly above it, so both call sites agree on where a code root comes from:

```python
def _code_root_from(args):
    """Where an unpacked code tarball lives, for this caller.

    The grid worker learns it from jobsub, which exports
    $INPUT_TAR_DIR_LOCAL when --tar_file_name was passed. The local
    runner passes --code-root and it arrives on `args`. Checked in that
    order so a local run inside a grid-like environment still wins.
    """
    return (getattr(args, 'code_root', None)
            or os.environ.get('INPUT_TAR_DIR_LOCAL'))
```

Then update both callers to pass it. At `utils/runmu2e.py:153` (inside `process_direct_input`):

```python
    simjob_setup = _extract_simjob_setup(tarball, jp=job_fcl,
                                         code_root=_code_root_from(args))
```

At `utils/runmu2e.py:273` (inside `process_jobdef`):

```python
    simjob_setup = _extract_simjob_setup(tarball, jp=jp,
                                         code_root=_code_root_from(args))
```

Both functions already take `args`. Confirm with `grep -n "def process_direct_input\|def process_jobdef" utils/runmu2e.py` that `args` is in scope at both sites; if `process_direct_input` does not receive `args`, pass `code_root=os.environ.get('INPUT_TAR_DIR_LOCAL')` there instead and note it in the commit message.

- [ ] **Step 6: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`
Expected: 1139 tests, OK (skipped=1). No pre-existing test may break: every current cnf has an absolute setup, which `resolve_setup` returns unchanged.

- [ ] **Step 7: Commit**

```bash
git add utils/runmu2e.py test/test_unit.py
git commit -m "feat(runmu2e): resolve_setup for code-mode cnfs

A relative jobpars 'setup' means the Offline build travels as a
separate tarball; it is joined onto a code root (\$INPUT_TAR_DIR_LOCAL
on the grid, --code-root locally). Absolute setup is unchanged, so
every existing cnf is unaffected. Missing code root raises rather than
falling back to /cvmfs: a code-mode job that sourced the wrong Offline
would report success.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 2: `sha256_file` and code-mode cnf construction

Makes `jobdef.py`'s long-dead `--code` flag real.

**Files:**
- Modify: `utils/prod_utils.py` (append helper), `utils/jobdef.py:215-224` (`_build_jobpars_json`), `utils/jobdef.py:682` (call site), `utils/jobdef.py:643-644` (equivalent-command print)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `utils.runmu2e.CODE_SETUP_REL` (Task 1).
- Produces:
  - `utils.prod_utils.sha256_file(path: str) -> tuple[str, int]` — returns `(hexdigest, size_bytes)`.
  - `utils.jobdef.CODE_SETUP_MEMBER: str` — `'Code/setup.sh'`.
  - `utils.jobdef.validate_code_tarball(path: str) -> None` — raises `ValueError`.
  - `utils.jobdef.build_code_ref(path: str) -> dict` — `{'sha256': str, 'size': int, 'source_path': str}`.
  - `_build_jobpars_json(config, tbs) -> dict` — **the third `code=""` parameter is removed.**

- [ ] **Step 1: Write the failing tests**

Add to `test/test_unit.py`:

```python
def _make_code_tarball(path, with_setup=True, bzip2=True):
    """Build a tiny stand-in for `muse tarball` output. Few KB, no
    /cvmfs, no network — safe for the in-process suite."""
    import tarfile as _tf
    mode = 'w:bz2' if bzip2 else 'w'
    with _tf.open(path, mode) as tar:
        payload = io.BytesIO(b'# fake muse setup\n')
        if with_setup:
            info = _tf.TarInfo('Code/setup.sh')
            info.size = len(payload.getvalue())
            tar.addfile(info, io.BytesIO(payload.getvalue()))
        other = _tf.TarInfo('Code/lib/libFake.so')
        other.size = 3
        tar.addfile(other, io.BytesIO(b'abc'))
    return path


class TestCodeTarballValidation(unittest.TestCase):
    """jobdef refuses a code tarball that cannot work on a worker,
    mirroring the checks Perl mu2ejobdef does at lines 808-828."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_good_tarball_accepted(self):
        from utils.jobdef import validate_code_tarball
        path = _make_code_tarball(os.path.join(self.dir, 'Code.tar.bz2'))
        validate_code_tarball(path)  # must not raise

    def test_missing_file_rejected(self):
        from utils.jobdef import validate_code_tarball
        with self.assertRaises(ValueError) as ctx:
            validate_code_tarball(os.path.join(self.dir, 'nope.tar.bz2'))
        self.assertIn('readable', str(ctx.exception))

    def test_uncompressed_tar_rejected(self):
        from utils.jobdef import validate_code_tarball
        path = _make_code_tarball(os.path.join(self.dir, 'plain.tar'),
                                  bzip2=False)
        with self.assertRaises(ValueError) as ctx:
            validate_code_tarball(path)
        self.assertIn('bzip2', str(ctx.exception))

    def test_tarball_without_code_setup_rejected(self):
        from utils.jobdef import validate_code_tarball
        path = _make_code_tarball(os.path.join(self.dir, 'nosetup.tar.bz2'),
                                  with_setup=False)
        with self.assertRaises(ValueError) as ctx:
            validate_code_tarball(path)
        self.assertIn('Code/setup.sh', str(ctx.exception))
        self.assertIn('muse tarball', str(ctx.exception))

    def test_name_does_not_decide(self):
        # Content decides, not the filename: a correctly built tarball
        # under any name is accepted.
        from utils.jobdef import validate_code_tarball
        path = _make_code_tarball(os.path.join(self.dir, 'my-build.tbz'))
        validate_code_tarball(path)


class TestSha256File(unittest.TestCase):

    def test_digest_and_size(self):
        from utils.prod_utils import sha256_file
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b'hello')
            name = fh.name
        self.addCleanup(os.unlink, name)
        digest, size = sha256_file(name)
        self.assertEqual(digest, hashlib.sha256(b'hello').hexdigest())
        self.assertEqual(size, 5)


class TestCodeModeJobpars(unittest.TestCase):
    """A code-mode cnf says setup='Code/setup.sh' and carries code_ref;
    upstream's `code` key stays empty because nothing is embedded."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.tarball = _make_code_tarball(
            os.path.join(self.dir, 'Code.tar.bz2'))

    def test_code_mode_shape(self):
        from utils.jobdef import _build_jobpars_json
        config = {'code': self.tarball, 'desc': 'Demo',
                  'dsconf': 'Run1Baq', 'owner': 'mu2e'}
        pars = _build_jobpars_json(config, {'outfiles': {}})
        self.assertEqual(pars['setup'], 'Code/setup.sh')
        self.assertEqual(pars['code'], '')
        self.assertEqual(pars['code_ref']['size'],
                         os.path.getsize(self.tarball))
        self.assertEqual(pars['code_ref']['source_path'],
                         os.path.abspath(self.tarball))
        self.assertEqual(len(pars['code_ref']['sha256']), 64)

    def test_setup_mode_shape_unchanged(self):
        from utils.jobdef import _build_jobpars_json
        setup = '/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/Run1Baq/setup.sh'
        config = {'simjob_setup': setup, 'desc': 'Demo',
                  'dsconf': 'Run1Baq', 'owner': 'mu2e'}
        pars = _build_jobpars_json(config, {'outfiles': {}})
        self.assertEqual(pars['setup'], setup)
        self.assertEqual(pars['code'], '')
        self.assertNotIn('code_ref', pars)

    def test_both_setup_and_code_rejected(self):
        from utils.jobdef import _build_jobpars_json
        config = {'simjob_setup': '/cvmfs/x/setup.sh', 'code': self.tarball,
                  'desc': 'Demo', 'dsconf': 'Run1Baq', 'owner': 'mu2e'}
        with self.assertRaises(ValueError):
            _build_jobpars_json(config, {'outfiles': {}})

    def test_neither_setup_nor_code_rejected(self):
        from utils.jobdef import _build_jobpars_json
        config = {'desc': 'Demo', 'dsconf': 'Run1Baq', 'owner': 'mu2e'}
        with self.assertRaises(ValueError):
            _build_jobpars_json(config, {'outfiles': {}})
```

`io`, `hashlib`, `tempfile`, `shutil`, `os` are all already imported at the top of `test/test_unit.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestCodeTarballValidation TestSha256File TestCodeModeJobpars`
Expected: FAIL — `cannot import name 'validate_code_tarball'` / `'sha256_file'`

- [ ] **Step 3: Add `sha256_file` to `utils/prod_utils.py`**

Append (and add `import hashlib` to that module's imports if absent):

```python
def sha256_file(path, chunk_size=1 << 20):
    """(hex digest, size in bytes) of a file, read in chunks.

    Single home for content hashing: `jobdef` stamps a code tarball's
    digest into the cnf at build time and `check_inputs` re-derives it
    at submit time. Two implementations would eventually disagree on
    chunking or encoding and turn a match into a spurious refusal.
    """
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
```

- [ ] **Step 4: Add validation and `code_ref` to `utils/jobdef.py`**

Near the top of `utils/jobdef.py`, beside the other module constants:

```python
# The setup script a code tarball must contain, and the relative path a
# code-mode cnf records. `muse tarball` puts everything under Code/
# (museTarball.sh:151-153, :217-228); upstream spells it the same way
# (mu2ejobdef:45, mu2eprodsys:337).
CODE_SETUP_MEMBER = 'Code/setup.sh'
```

Then, above `_build_jobpars_json`:

```python
def validate_code_tarball(path):
    """Refuse a code tarball that could not work on a worker.

    Same three checks Perl mu2ejobdef makes (mu2ejobdef:808-828):
    readable, bzip2-compressed, and containing Code/setup.sh. Done at
    BUILD time so a broken tarball costs one command instead of a
    thousand grid jobs.

    Scanning stops at the first match. bzip2 is not seekable, so a full
    walk of a ~1 GB archive is slow; `museTarball.sh` writes setup.sh
    early, so the first-match exit is nearly free in practice.

    Content decides, never the filename — a correctly built tarball is
    accepted under any name.
    """
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        raise ValueError(f"code tarball is not readable: {path}")
    try:
        with tarfile.open(path, 'r:bz2') as tar:
            for member in tar:
                if member.name == CODE_SETUP_MEMBER:
                    return
    except tarfile.ReadError as exc:
        raise ValueError(
            f"code tarball is not a bzip2-compressed tar archive: "
            f"{path} ({exc})")
    raise ValueError(
        f"code tarball has no {CODE_SETUP_MEMBER}: {path} — "
        f"build it with `muse tarball`")


def build_code_ref(path):
    """Provenance for a code-mode cnf: what build it was made against.

    The bytes are NOT embedded (sidecar delivery), so this digest is the
    only thing binding the cnf to a particular Offline build.
    `check_inputs.check_code_tarball` re-derives it at submit time and
    refuses a mismatch.
    """
    digest, size = sha256_file(path)
    return {'sha256': digest, 'size': size,
            'source_path': os.path.abspath(path)}
```

Add the imports `utils/jobdef.py` needs: `os`, `tarfile`, and `from utils.prod_utils import sha256_file`. Check which are already present with `grep -n "^import\|^from" utils/jobdef.py` and add only what is missing.

- [ ] **Step 5: Rewrite `_build_jobpars_json` and its call site**

Replace `utils/jobdef.py:215-224` with:

```python
def _build_jobpars_json(config: Dict, tbs: Dict) -> Dict:
    """Construct complete jobpars.json structure matching Perl mu2ejobdef.

    Perl field ordering: code, setup, tbs, jobname. `code_ref` is ours
    and sits after `setup`, next to the field it explains.

    `code` is ALWAYS empty. Upstream uses it for the name of an embedded
    archive member; prodtools ships the code as a jobsub sidecar and
    embeds nothing, so an empty string is the truthful answer and keeps
    a cnf of ours readable by mu2ejobquery.
    """
    setup = config.get('simjob_setup')
    code_path = config.get('code')
    if bool(setup) == bool(code_path):
        raise ValueError(
            "exactly one of 'simjob_setup' and 'code' must be set "
            f"(simjob_setup={setup!r}, code={code_path!r})")
    pars = {
        "code": "",
        "setup": setup or CODE_SETUP_MEMBER,
    }
    if code_path:
        pars["code_ref"] = build_code_ref(code_path)
    pars["tbs"] = _reorder(tbs, ['seed', 'subrunkey', 'event_id', 'outfiles'])
    pars["jobname"] = cnf_name(config, 'tar')
    return pars
```

Change the call site at `utils/jobdef.py:682` from `_build_jobpars_json(config, tbs, code="")` to:

```python
    jobpars = _build_jobpars_json(config, tbs)
```

In `create_jobdef`, before any output is written, add the fail-fast validation. Put it immediately after the function's existing argument handling and before `tbs = _parse_job_args(...)`:

```python
    # Fail before building anything: a bad code tarball should cost one
    # command, not a cnf that only breaks on a worker.
    if config.get('code'):
        validate_code_tarball(config['code'])
```

Finally fix the equivalent-command print at `utils/jobdef.py:642-644`. It already reads:

```python
    setup_arg = '--setup' if config.get('simjob_setup') else '--code'
    setup_val = config.get('simjob_setup') or config.get('code')
```

That is already correct — leave it. Confirm it still is with `sed -n 640,646p utils/jobdef.py`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestCodeTarballValidation TestSha256File TestCodeModeJobpars`
Expected: PASS, 10 tests

- [ ] **Step 7: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`
Expected: 1149 tests, OK (skipped=1). If a parity test that compares jobpars against Perl output breaks, read it: it should be exercising `--setup` mode, whose shape is unchanged. A break there means `_reorder` or key order regressed — fix that, do not adjust the parity expectation.

- [ ] **Step 8: Commit**

```bash
git add utils/jobdef.py utils/prod_utils.py test/test_unit.py
git commit -m "feat(jobdef): build code-mode cnfs, validate the code tarball

--code has been parsed but dead since it was added: _build_jobpars_json
was always called with code=\"\" and setup came from simjob_setup, which
is None on that path, so a --code cnf carried {code: \"\", setup: null}
and could not run.

Now a code-mode cnf records setup='Code/setup.sh' and a code_ref
{sha256, size, source_path}. The bytes are not embedded, so code_ref is
the only thing binding a cnf to an Offline build. Tarball is validated
at build time the way mu2ejobdef:808-828 does it: readable, bzip2,
contains Code/setup.sh.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 3: `code` as an entry key

The entry, not the cnf, carries the tarball's path. This task teaches the entry grammar about it.

**Files:**
- Modify: `utils/jobdesc.py:64-` (near `inloc_of`), `utils/jobdesc.py:177-215` (`validate_entry_value`), `utils/submission_ledger.py:43` (`EDITABLE_ENTRY_KEYS`), `utils/json2jobdef.py:337` (`validate_required_fields`), `utils/json2jobdef.py:483-485` (`build_jobdesc` pass-through)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `utils.jobdesc.code_of(entry: dict) -> str | None`
  - `validate_entry_value('code', value)` raises `ValueError` on a non-string or a relative path.
  - `EDITABLE_ENTRY_KEYS` includes `'code'`.
  - `build_jobdesc(config)` copies `config['code']` to `entry['code']` when present.

- [ ] **Step 1: Write the failing tests**

```python
class TestEntryCodeKey(unittest.TestCase):
    """The entry carries the code tarball's absolute path; the cnf
    carries only the relative setup and the digest."""

    def test_code_of_returns_path(self):
        from utils.jobdesc import code_of
        self.assertEqual(code_of({'code': '/exp/build/Code.tar.bz2'}),
                         '/exp/build/Code.tar.bz2')

    def test_code_of_none_for_musing_entry(self):
        from utils.jobdesc import code_of
        self.assertIsNone(code_of({'tarball': 'cnf.mu2e.X.Run1Baq.0.tar'}))

    def test_absolute_path_accepted(self):
        from utils.jobdesc import validate_entry_value
        validate_entry_value('code', '/exp/build/Code.tar.bz2')

    def test_any_suffix_accepted(self):
        # Content decides whether a tarball is usable (jobdef checks the
        # bzip2 magic); the name must not.
        from utils.jobdesc import validate_entry_value
        validate_entry_value('code', '/exp/build/my-build.tbz')

    def test_relative_path_rejected(self):
        from utils.jobdesc import validate_entry_value
        with self.assertRaises(ValueError) as ctx:
            validate_entry_value('code', 'Code.tar.bz2')
        self.assertIn('absolute', str(ctx.exception))

    def test_non_string_rejected(self):
        from utils.jobdesc import validate_entry_value
        with self.assertRaises(ValueError):
            validate_entry_value('code', 17)

    def test_code_is_editable_on_a_live_campaign(self):
        # A rebuilt tarball must be reachable without a new cnf.
        from utils.submission_ledger import EDITABLE_ENTRY_KEYS
        self.assertIn('code', EDITABLE_ENTRY_KEYS)


class TestJson2JobdefCodeConfig(unittest.TestCase):

    def test_exactly_one_of_setup_and_code(self):
        from utils.json2jobdef import validate_required_fields
        base = {'fcl': 'x.fcl', 'dsconf': 'Run1Baq', 'outloc': {'dts.*': 'disk'}}
        with self.assertRaises(SystemExit):
            validate_required_fields(dict(base))                    # neither
        with self.assertRaises(SystemExit):
            validate_required_fields(dict(base, simjob_setup='/cvmfs/s.sh',
                                          code='/exp/Code.tar.bz2'))  # both
        validate_required_fields(dict(base, simjob_setup='/cvmfs/s.sh'))
        validate_required_fields(dict(base, code='/exp/Code.tar.bz2'))

    def test_code_reaches_the_entry(self):
        from utils.json2jobdef import build_jobdesc
        config = {'code': '/exp/build/Code.tar.bz2', 'inloc': 'tape',
                  'desc': 'Demo', 'dsconf': 'Run1Baq', 'owner': 'mu2e',
                  'fcl': 'x.fcl', 'outloc': {}, 'njobs': 5}
        entry = build_jobdesc(config)
        self.assertEqual(entry['code'], '/exp/build/Code.tar.bz2')

    def test_musing_entry_has_no_code_key(self):
        from utils.json2jobdef import build_jobdesc
        config = {'simjob_setup': '/cvmfs/s.sh', 'inloc': 'tape',
                  'desc': 'Demo', 'dsconf': 'Run1Baq', 'owner': 'mu2e',
                  'fcl': 'x.fcl', 'outloc': {}, 'njobs': 5}
        self.assertNotIn('code', build_jobdesc(config))
```

If `build_jobdesc` needs config keys these dicts lack, read `utils/json2jobdef.py:448-520` and add exactly those keys — do not weaken the assertions.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestEntryCodeKey TestJson2JobdefCodeConfig`
Expected: FAIL — `cannot import name 'code_of'`

- [ ] **Step 3: Add `code_of` to `utils/jobdesc.py`**

Directly after `inloc_of` (ends around line 75):

```python
def code_of(entry, default=None):
    """Absolute path to this entry's code tarball, or `default`.

    Present only on an entry built from a `--code` config. Its absence
    means the ordinary case: the cnf names a /cvmfs Musing setup and no
    tarball travels with the job.

    The path lives on the ENTRY rather than in the cnf because a tarball
    can be moved or rebuilt, and because the entry snapshot is what
    later slices and recoveries read. The cnf keeps the digest instead,
    which is what actually has to stay true.
    """
    return entry.get('code', default)
```

- [ ] **Step 4: Teach `validate_entry_value` about `code`**

In `utils/jobdesc.py:196`, change the early return guard:

```python
    if key not in ('inloc', 'code') + RESOURCE_KEYS:
        return
```

and add a branch after the existing `elif key == 'inloc':` block:

```python
    elif key == 'code':
        # Absolute only: the submit host and the local runner resolve
        # this path from different working directories, and a relative
        # one would silently mean different files to each.
        # No suffix rule — jobdef.validate_code_tarball checks the bzip2
        # magic, so a correctly built tarball is usable under any name.
        if not value.startswith('/'):
            raise ValueError(
                f"code must be an absolute path, got {value!r}")
```

- [ ] **Step 5: Make `code` editable on a live campaign**

`utils/submission_ledger.py:43`:

```python
EDITABLE_ENTRY_KEYS = ('inloc', 'code') + RESOURCE_KEYS
```

`code` belongs here and `tarball`/`njobs`/`firstjob` do not: repointing at a rebuilt tarball changes which Offline runs, not the campaign's identity or index space. Note that the submit-time digest gate (Task 5) will refuse the new tarball unless the cnf was rebuilt too — which is the intended interaction, not a conflict.

- [ ] **Step 6: Enforce exactly-one in `json2jobdef`**

`utils/json2jobdef.py:337`, replace the `for req in (...)` loop's `simjob_setup` membership:

```python
    for req in ('fcl', 'dsconf', 'outloc'):
        if not config.get(req):
            sys.exit(f"Missing required field: {req}")
    # Exactly one source of Offline, the same rule mu2ejobdef enforces:
    # a /cvmfs Musing setup script, or a code tarball that travels with
    # the job. Both would be ambiguous; neither cannot run.
    if bool(config.get('simjob_setup')) == bool(config.get('code')):
        sys.exit("Exactly one of 'simjob_setup' and 'code' is required")
```

- [ ] **Step 7: Pass `code` through to the entry**

In `build_jobdesc` (`utils/json2jobdef.py`), after the `input_pattern`/`prestage` pass-through block at lines 483-485:

```python
    # The code tarball's path travels on the entry, not in the cnf: the
    # submit path reads it via jobdesc.code_of to add jobsub's
    # --tar_file_name, and the snapshot is what later slices reuse.
    if config.get('code'):
        jobdef_entry['code'] = config['code']
```

- [ ] **Step 8: Fix the equivalent-command print in `build_jobdef`**

`utils/json2jobdef.py:396-402` builds a mu2ejobdef-equivalent command
string with `'--setup', config['simjob_setup']` hardcoded. In code mode
that emits the literal `None`. `test/parity_test.py` consumes this exact
string via `result['perl_commands']`, so it has to be right. Replace the
two `cmd_parts` lines:

```python
    cmd_parts = [
        'mu2ejobdef',
        '--setup' if config.get('simjob_setup') else '--code',
        config.get('simjob_setup') or config['code'],
        '--dsconf', config['dsconf'],
        '--desc', config['desc'],
        '--dsowner', config['owner']
    ]
```

This mirrors what `utils/jobdef.py:642-644` already does for its own
printed command. No unit test: the string is diagnostic output whose
only consumer is the Perl parity harness, and Task 8 Step 2 reads it on
a real build.

- [ ] **Step 9: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestEntryCodeKey TestJson2JobdefCodeConfig`
Expected: PASS, 10 tests

- [ ] **Step 10: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`
Expected: 1159 tests, OK (skipped=1). A test asserting the exact `EDITABLE_ENTRY_KEYS` tuple or the `set-entry --help` choices list may need its expected value extended to include `code`; that is a correct update, not a weakening.

- [ ] **Step 11: Commit**

```bash
git add utils/jobdesc.py utils/json2jobdef.py utils/submission_ledger.py test/test_unit.py
git commit -m "feat(jobdesc): code tarball path as an entry key

The entry carries the absolute path, the cnf carries only the relative
setup and the digest, because a tarball can move and the entry snapshot
is what later slices and recoveries read. Absolute paths only: submit
host and local runner resolve from different directories. No suffix
rule -- jobdef checks the bzip2 magic, so the name must not decide.

'code' joins EDITABLE_ENTRY_KEYS so a rebuilt tarball is reachable
without a new campaign. json2jobdef now requires exactly one of
simjob_setup and code, the same rule mu2ejobdef enforces.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 4: Ship the tarball with the job

**Files:**
- Modify: `utils/jobsub_argv.py:199-` (signature) and `:274-277` (argv assembly), `utils/submit.py:793-808` (call site)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `utils.jobdesc.code_of` (Task 3).
- Produces: `build_jobsub_argv(..., code_tarball=None)`; when set, argv contains `--tar_file_name` followed by `dropbox://<path>`.

- [ ] **Step 1: Write the failing tests**

```python
class TestJobsubArgvCodeTarball(unittest.TestCase):
    """The code tarball rides jobsub's --tar_file_name (RCDS/cvmfs,
    published once, no per-job copy) -- NOT -f dropbox://, which
    transfers per job. mu2eprodsys:474-475 does the same."""

    def _argv(self, **extra):
        from utils.jobsub_argv import build_jobsub_argv
        return build_jobsub_argv(
            entry={'tarball': 'cnf.mu2e.Demo.Run1Baq_best_v1_5.0.tar',
                   'outputs': []},
            jobset=[0, 1, 2],
            jobdef_path='/tmp/cnf.mu2e.Demo.Run1Baq_best_v1_5.0.tar',
            ops_json_path='/tmp/ops.json',
            prodtools_tar_path='/tmp/prodtools-me.tar',
            worker_script_path='/repo/bin/runjob.sh',
            submitter='me',
            **extra)

    def test_no_code_tarball_no_flag(self):
        argv = self._argv()
        self.assertNotIn('--tar_file_name', argv)

    def test_code_tarball_adds_flag(self):
        argv = self._argv(code_tarball='/exp/build/Code.tar.bz2')
        idx = argv.index('--tar_file_name')
        self.assertEqual(argv[idx + 1], 'dropbox:///exp/build/Code.tar.bz2')

    def test_code_tarball_does_not_displace_the_three_input_files(self):
        # Regression guard: --tar_file_name is a DIFFERENT mechanism from
        # -f dropbox://. The cnf, the ops JSON and the prodtools tarball
        # must all still ship.
        argv = self._argv(code_tarball='/exp/build/Code.tar.bz2')
        shipped = [argv[i + 1] for i, a in enumerate(argv) if a == '-f']
        self.assertEqual(len(shipped), 3)
        self.assertIn('dropbox:///tmp/ops.json', shipped)
        self.assertIn('dropbox:///tmp/cnf.mu2e.Demo.Run1Baq_best_v1_5.0.tar',
                      shipped)
        self.assertIn('dropbox:///tmp/prodtools-me.tar', shipped)

    def test_executable_stays_last(self):
        argv = self._argv(code_tarball='/exp/build/Code.tar.bz2')
        self.assertTrue(argv[-1].startswith('file://'))


class TestSubmitPassesCodeTarball(unittest.TestCase):
    """submit_entry must actually wire the entry's code tarball into
    build_jobsub_argv. Same shape as TestSubmitEntryResourceWiring
    (test_unit.py:5323), which closes the identical gap for memory."""

    def test_entry_code_reaches_build_jobsub_argv(self):
        from utils.submit import submit_entry, SubmitOptions

        entry = {'tarball': 'cnf.mu2e.NoSuchTarballXYZ.TestConf.0.tar',
                 'njobs': 5, 'inloc': 'tape',
                 'outputs': [{'location': 'tape'}],
                 'code': '/exp/build/Code.tar.bz2'}
        options = SubmitOptions(
            ledger_db='/tmp/unused-code-wiring.db',
            dry_run=True, origin='/tmp/m.json')

        captured = {}

        def fake_build_jobsub_argv(**kwargs):
            captured.update(kwargs)
            return ['--fake-argv']

        with patch('utils.submit._jobsub_argv.build_jobsub_argv',
                   side_effect=fake_build_jobsub_argv), \
             patch('utils.submit._bundle_prodtools',
                   return_value=Path('/tmp/fake-prodtools.tar')):
            result = submit_entry(entry, 0, options)

        self.assertEqual(result['status'], 'dry_run')
        self.assertEqual(captured['code_tarball'], '/exp/build/Code.tar.bz2')

    def test_musing_entry_passes_none(self):
        from utils.submit import submit_entry, SubmitOptions

        entry = {'tarball': 'cnf.mu2e.NoSuchTarballXYZ.TestConf.0.tar',
                 'njobs': 5, 'inloc': 'tape',
                 'outputs': [{'location': 'tape'}]}
        options = SubmitOptions(
            ledger_db='/tmp/unused-code-wiring.db',
            dry_run=True, origin='/tmp/m.json')

        captured = {}

        def fake_build_jobsub_argv(**kwargs):
            captured.update(kwargs)
            return ['--fake-argv']

        with patch('utils.submit._jobsub_argv.build_jobsub_argv',
                   side_effect=fake_build_jobsub_argv), \
             patch('utils.submit._bundle_prodtools',
                   return_value=Path('/tmp/fake-prodtools.tar')):
            submit_entry(entry, 0, options)

        self.assertIsNone(captured['code_tarball'])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestJobsubArgvCodeTarball TestSubmitPassesCodeTarball`
Expected: FAIL — `build_jobsub_argv() got an unexpected keyword argument 'code_tarball'`

- [ ] **Step 3: Add the parameter to `build_jobsub_argv`**

In `utils/jobsub_argv.py`, add to the keyword-only signature, next to `extra_jobsub_args`:

```python
    code_tarball=None,
```

and extend the docstring:

```
    `code_tarball`, when set, is an absolute path to a `muse tarball`
    Code.tar.bz2. It rides `--tar_file_name dropbox://`, NOT `-f
    dropbox://`: jobsub publishes it to RCDS/cvmfs once, deduplicated by
    content, and the worker sees the unpacked tree at
    $INPUT_TAR_DIR_LOCAL. `-f` transfers per job, which a ~1 GB build
    tree cannot afford. Same split mu2eprodsys uses (mu2eprodsys:474).
```

- [ ] **Step 4: Emit the flag**

In `utils/jobsub_argv.py`, immediately after the three existing `-f dropbox://` lines (currently `:274-276`) and before the `extra_jobsub_args` block:

```python
    if code_tarball:
        argv.extend(["--tar_file_name", f"dropbox://{code_tarball}"])
```

- [ ] **Step 5: Pass it from the submit path**

In `utils/submit.py`, add `code_of` to the existing `from utils.jobdesc import ...` line, then add one argument to the `build_jobsub_argv` call at `:793`:

```python
        code_tarball=code_of(entry),
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestJobsubArgvCodeTarball TestSubmitPassesCodeTarball`
Expected: PASS, 6 tests

- [ ] **Step 7: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`
Expected: 1165 tests, OK (skipped=1)

- [ ] **Step 8: Commit**

```bash
git add utils/jobsub_argv.py utils/submit.py test/test_unit.py
git commit -m "feat(submit): ship the code tarball via jobsub --tar_file_name

RCDS/cvmfs is jobsub's default dropbox, so the tarball is published
once, deduplicated by content, and mounted at \$INPUT_TAR_DIR_LOCAL --
no per-job copy. -f dropbox:// transfers per job and a ~1 GB build tree
cannot afford that, which is why the two mechanisms stay separate here
exactly as they do in mu2eprodsys:474.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 5: Bind the tarball to the cnf at submit time

The separable safety property: you cannot ship different code than the cnf was built against.

**Files:**
- Modify: `utils/check_inputs.py:26-31` (`Problem` kinds comment), append `check_code_tarball`; `utils/submit.py:441-470` (`enqueue_entry`, both branches)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `utils.prod_utils.sha256_file` (Task 2), `utils.jobdesc.code_of` (Task 3), `code_ref` in jobpars (Task 2).
- Produces: `utils.check_inputs.check_code_tarball(entry: dict, cnf_path: str) -> tuple[bool, list[Problem]]`.

- [ ] **Step 1: Write the failing tests**

```python
class TestCheckCodeTarball(unittest.TestCase):
    """The entry names a tarball; the cnf remembers the digest of the
    tarball it was built against. They must still agree at submit time."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.code = _make_code_tarball(os.path.join(self.dir, 'Code.tar.bz2'))

    def _cnf(self, jobpars):
        """A cnf tarball carrying just jobpars.json — enough for
        Mu2eJobPars to read code_ref."""
        path = os.path.join(self.dir, 'cnf.mu2e.Demo.Run1Baq.0.tar')
        blob = json.dumps(jobpars).encode()
        with tarfile.open(path, 'w') as tar:
            info = tarfile.TarInfo('jobpars.json')
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
        return path

    def test_matching_digest_passes(self):
        from utils.check_inputs import check_code_tarball
        from utils.jobdef import build_code_ref
        cnf = self._cnf({'code': '', 'setup': 'Code/setup.sh',
                         'code_ref': build_code_ref(self.code), 'tbs': {}})
        ok, problems = check_code_tarball({'code': self.code}, cnf)
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_musing_cnf_and_musing_entry_pass(self):
        from utils.check_inputs import check_code_tarball
        cnf = self._cnf({'code': '', 'setup': '/cvmfs/x/setup.sh', 'tbs': {}})
        ok, problems = check_code_tarball({}, cnf)
        self.assertTrue(ok)

    def test_changed_bytes_refused(self):
        from utils.check_inputs import check_code_tarball
        from utils.jobdef import build_code_ref
        cnf = self._cnf({'code': '', 'setup': 'Code/setup.sh',
                         'code_ref': build_code_ref(self.code), 'tbs': {}})
        # Rebuild in place with different content, as `muse tarball` would.
        _make_code_tarball(self.code)
        with open(self.code, 'ab') as fh:
            fh.write(b'\x00')
        ok, problems = check_code_tarball({'code': self.code}, cnf)
        self.assertFalse(ok)
        self.assertEqual(problems[0].kind, 'code_mismatch')

    def test_deleted_tarball_refused(self):
        from utils.check_inputs import check_code_tarball
        from utils.jobdef import build_code_ref
        cnf = self._cnf({'code': '', 'setup': 'Code/setup.sh',
                         'code_ref': build_code_ref(self.code), 'tbs': {}})
        os.unlink(self.code)
        ok, problems = check_code_tarball({'code': self.code}, cnf)
        self.assertFalse(ok)
        self.assertEqual(problems[0].kind, 'missing')

    def test_entry_has_code_but_cnf_does_not(self):
        from utils.check_inputs import check_code_tarball
        cnf = self._cnf({'code': '', 'setup': '/cvmfs/x/setup.sh', 'tbs': {}})
        ok, problems = check_code_tarball({'code': self.code}, cnf)
        self.assertFalse(ok)
        self.assertEqual(problems[0].kind, 'code_mismatch')

    def test_cnf_has_code_but_entry_does_not(self):
        from utils.check_inputs import check_code_tarball
        from utils.jobdef import build_code_ref
        cnf = self._cnf({'code': '', 'setup': 'Code/setup.sh',
                         'code_ref': build_code_ref(self.code), 'tbs': {}})
        ok, problems = check_code_tarball({}, cnf)
        self.assertFalse(ok)
        self.assertEqual(problems[0].kind, 'code_mismatch')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestCheckCodeTarball`
Expected: FAIL — `cannot import name 'check_code_tarball'`

- [ ] **Step 3: Implement `check_code_tarball`**

In `utils/check_inputs.py`, extend the `Problem.kind` comment at line 29 to `# 'truncated' | 'missing' | 'nearline' | 'query_error' | 'code_mismatch'`, then append:

```python
def check_code_tarball(entry, cnf_path):
    """Verify the entry's code tarball is still the one the cnf was
    built against. Returns (ok, problems), same shape as check_inputs.

    Deliberately NOT folded into check_inputs: that function means one
    thing — input-data residency — and this is a different question
    about a different artifact.

    Sidecar delivery means the build's bytes are not in the cnf, so
    without this gate a rebuilt or replaced tarball would ship silently
    and the campaign's outputs would carry provenance that is simply
    wrong. mu2eprodsys binds nothing here; we can, cheaply.

    Hashing ~1 GB costs a few seconds, negligible beside the RCDS
    publish that follows.
    """
    from utils.jobdesc import code_of
    from utils.prod_utils import sha256_file

    code = code_of(entry)
    ref = Mu2eJobPars(cnf_path).json_data.get('code_ref')

    if code is None and ref is None:
        return (True, [])
    if code is None or ref is None:
        return (False, [Problem(
            dataset='code', filename=str(code or cnf_path),
            kind='code_mismatch',
            detail=("entry and cnf disagree about code mode: "
                    f"entry code={code!r}, cnf code_ref="
                    f"{'present' if ref else 'absent'}. Rebuild the cnf "
                    f"from the config you are enqueueing."))])
    if not os.path.isfile(code):
        return (False, [Problem(
            dataset='code', filename=code, kind='missing',
            detail="code tarball named by the entry no longer exists")])

    digest, size = sha256_file(code)
    if digest != ref.get('sha256'):
        return (False, [Problem(
            dataset='code', filename=code, kind='code_mismatch',
            detail=(f"sha256 {digest[:12]} does not match the cnf's "
                    f"code_ref {str(ref.get('sha256'))[:12]} "
                    f"({size} bytes now, {ref.get('size')} at build). "
                    f"Rebuild the cnf, or point the entry at the "
                    f"original tarball."))])
    return (True, [])
```

- [ ] **Step 4: Run the gate from the enqueue path**

In `utils/submit.py`'s `enqueue_entry`, both branches need it — the draining branch skips `check_inputs` (a generic cnf bakes no inputs) but its code tarball still has to be right.

In the draining branch, change `_ensure_local_tarball(tarball_of(entry))` to capture the path and gate on it:

```python
        tarball_path = _ensure_local_tarball(tarball_of(entry))
        ok, problems = check_code_tarball(entry, str(tarball_path))
        if not ok:
            print(format_report(str(tarball_path), problems))
            sys.exit(2)
```

In the normal branch, immediately after the existing `check_inputs` block that ends with `sys.exit(2)`:

```python
    ok, problems = check_code_tarball(entry, str(tarball_path))
    if not ok:
        print(format_report(str(tarball_path), problems))
        print("json2jobdef: code tarball does not match the cnf — "
              "no campaign created")
        sys.exit(2)
```

Add `check_code_tarball` to the existing `from utils.check_inputs import ...` line at `utils/submit.py:42`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestCheckCodeTarball`
Expected: PASS, 6 tests

- [ ] **Step 6: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`
Expected: 1171 tests, OK (skipped=1). Existing `enqueue_entry` tests use Musing cnfs and Musing entries, which the `code is None and ref is None` branch passes through untouched.

- [ ] **Step 7: Commit**

```bash
git add utils/check_inputs.py utils/submit.py test/test_unit.py
git commit -m "feat(check_inputs): bind the code tarball to the cnf at submit

Sidecar delivery keeps the build's bytes out of the cnf, so nothing
otherwise stops a rebuilt or replaced tarball from shipping under a cnf
that was built against different code -- outputs would carry provenance
that is simply wrong. The cnf's code_ref digest is re-derived at enqueue
and a mismatch refuses with exit 2, on both the normal and the draining
branch. mu2eprodsys binds nothing here; hashing ~1 GB costs seconds
beside the RCDS publish.

Kept out of check_inputs itself: that function means input-data
residency, and this is a different artifact.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 6: `bin/runlocal --code`

**Files:**
- Modify: `utils/runlocal.py` — imports, `build_parser` (~line 330-360), `child_argv` (~line 183-202), `main` (~line 362-381); add `unpack_code`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `utils.runmu2e._code_root_from` reading `args.code_root` (Task 1).
- Produces: `utils.runlocal.unpack_code(tarball: str, workdir: str) -> str` returning the directory that contains `Code/`; `--code` on the driver; `--code-root` on children.

- [ ] **Step 1: Write the failing tests**

```python
class TestRunlocalCode(unittest.TestCase):
    """The driver unpacks the code tarball ONCE and hands children the
    directory. 3.6 GB per job times four parallel jobs is not viable."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.code = _make_code_tarball(os.path.join(self.dir, 'Code.tar.bz2'))

    def test_unpack_creates_code_setup(self):
        from utils.runlocal import unpack_code
        root = unpack_code(self.code, self.dir)
        self.assertTrue(os.path.isfile(os.path.join(root, 'Code', 'setup.sh')))

    def test_unpack_is_idempotent(self):
        from utils.runlocal import unpack_code
        root = unpack_code(self.code, self.dir)
        marker = os.path.join(root, 'Code', 'setup.sh')
        with open(marker, 'a') as fh:
            fh.write('# touched\n')
        before = os.path.getsize(marker)
        self.assertEqual(unpack_code(self.code, self.dir), root)
        # Second call must not re-extract over an existing tree.
        self.assertEqual(os.path.getsize(marker), before)

    def test_child_argv_carries_code_root(self):
        from utils.runlocal import child_argv
        args = SimpleNamespace(entry_point='/repo/utils/runlocal.py',
                               jobdef='/tmp/cnf.tar', inloc='tape',
                               indices=[0, 1], nevts=10, mu2e_options='',
                               copy_input=False, code_root='/w/code')
        argv = child_argv(0, args)
        self.assertIn('--code-root', argv)
        self.assertEqual(argv[argv.index('--code-root') + 1], '/w/code')

    def test_child_argv_omits_code_root_when_absent(self):
        from utils.runlocal import child_argv
        args = SimpleNamespace(entry_point='/repo/utils/runlocal.py',
                               jobdef='/tmp/cnf.tar', inloc='tape',
                               indices=[0, 1], nevts=10, mu2e_options='',
                               copy_input=False, code_root=None)
        self.assertNotIn('--code-root', child_argv(0, args))

    def test_parser_accepts_both_flags(self):
        from utils.runlocal import build_parser
        args = build_parser().parse_args(
            ['--jobdef', 'cnf.tar', '--code', '/exp/Code.tar.bz2'])
        self.assertEqual(args.code, '/exp/Code.tar.bz2')
        self.assertIsNone(args.code_root)
        child = build_parser().parse_args(
            ['--jobdef', 'cnf.tar', '--one', '3', '--code-root', '/w/code'])
        self.assertEqual(child.code_root, '/w/code')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestRunlocalCode`
Expected: FAIL — `cannot import name 'unpack_code'`

- [ ] **Step 3: Add `unpack_code`**

Add `import tarfile` to `utils/runlocal.py`'s imports, then add beside `resolve_jobdef`:

```python
def unpack_code(tarball, workdir):
    """Unpack a `muse tarball` Code.tar.bz2 once, for every child to share.

    Returns the directory holding `Code/` — what `resolve_setup` wants as
    its code root, and what the grid gets from $INPUT_TAR_DIR_LOCAL.

    ONE unpack, not one per job: the build tree runs to several GB and
    the driver launches four jobs at a time by default. Re-running is
    cheap because an already-unpacked tree is detected and left alone.
    """
    root = Path(workdir) / 'code'
    marker = root / 'Code' / 'setup.sh'
    if marker.is_file():
        print(f"[local] code already unpacked at {root}")
        return str(root)
    root.mkdir(parents=True, exist_ok=True)
    print(f"[local] unpacking {tarball} into {root} "
          f"(several GB — this takes a while)")
    with tarfile.open(tarball, 'r:bz2') as tar:
        tar.extractall(root)
    if not marker.is_file():
        sys.exit(f"runlocal: {tarball} has no Code/setup.sh — "
                 f"build it with `muse tarball`")
    return str(root)
```

- [ ] **Step 4: Add the flags**

In `build_parser`, after `--copy-input`:

```python
    parser.add_argument('--code', default=None,
                        help='muse tarball Code.tar.bz2 to run against '
                             'instead of the cnf\'s /cvmfs setup; unpacked '
                             'once into <workdir>/code')
    parser.add_argument('--code-root', default=None,
                        help=argparse.SUPPRESS)
```

`--code-root` is suppressed from help on purpose: the driver sets it for its children, and a user who passes it by hand has skipped the unpack the driver would have done.

- [ ] **Step 5: Emit it in `child_argv`**

In `child_argv`, before the `return argv`:

```python
    if getattr(args, 'code_root', None):
        # Children get the already-unpacked directory, never --code:
        # one unpack serves all of them, and the printed command must
        # reproduce the job without redoing several GB of extraction.
        argv.extend(['--code-root', args.code_root])
```

- [ ] **Step 6: Unpack in `main` before the children launch**

In `main`, after `args.jobdef = resolve_jobdef(args.jobdef, args.workdir)` and before `args.entry_point = ...`:

```python
    if args.code:
        args.code_root = unpack_code(args.code, args.workdir)
```

The child branch (`if args.one is not None:`) is above this and untouched: a child already receives `--code-root` and must not unpack anything.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestRunlocalCode`
Expected: PASS, 5 tests

- [ ] **Step 8: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`
Expected: 1176 tests, OK (skipped=1)

- [ ] **Step 9: Commit**

```bash
git add utils/runlocal.py test/test_unit.py
git commit -m "feat(runlocal): --code runs a job against a muse tarball

The driver unpacks Code.tar.bz2 once into <workdir>/code and hands
children --code-root; a build tree of several GB cannot be unpacked per
job with four running at a time. Children resolve through the same
runmu2e.resolve_setup the grid worker uses, so a local smoke tests the
exact bytes the grid will run.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 7: Worker diagnostics, `jobquery` honesty, and docs

**Files:**
- Modify: `bin/runjob.sh:16-23` (diagnostics block), `utils/jobquery.py:74-88` (`codesize`, `extract_code`), `:89-121` (`recipe`), `:181-182` and `:194` and `:257-258` (CLI), `docs/EXAMPLES_schema.md`
- Regenerate: `EXAMPLES.md`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `code_ref` in jobpars (Task 2).
- Produces: no new callable surface. `--extract-code` is **removed** from `bin/jobquery`.

- [ ] **Step 1: Write the failing tests**

```python
class TestJobqueryCodeMode(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _cnf(self, jobpars):
        path = os.path.join(self.dir, 'cnf.mu2e.Demo.Run1Baq.0.tar')
        blob = json.dumps(jobpars).encode()
        with tarfile.open(path, 'w') as tar:
            info = tarfile.TarInfo('jobpars.json')
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
        return path

    def test_recipe_reports_code_mode(self):
        from utils.jobquery import Mu2eJobPars
        cnf = self._cnf({'code': '', 'setup': 'Code/setup.sh',
                         'code_ref': {'sha256': 'a' * 64, 'size': 12,
                                      'source_path': '/exp/Code.tar.bz2'},
                         'tbs': {'outfiles': {}}, 'jobname': 'demo'})
        text = Mu2eJobPars(cnf).recipe()
        self.assertIn('/exp/Code.tar.bz2', text)
        self.assertIn('a' * 64, text)

    def test_recipe_omits_code_line_for_musing_cnf(self):
        from utils.jobquery import Mu2eJobPars
        cnf = self._cnf({'code': '', 'setup': '/cvmfs/x/setup.sh',
                         'tbs': {'outfiles': {}}, 'jobname': 'demo'})
        self.assertNotIn('code:', Mu2eJobPars(cnf).recipe())

    def test_extract_code_is_gone(self):
        # It extracted any member ending in .tar, which under sidecar
        # delivery would pull out something that is not code at all.
        from utils.jobquery import Mu2eJobPars
        self.assertFalse(hasattr(Mu2eJobPars, 'extract_code'))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestJobqueryCodeMode`
Expected: FAIL — `'/exp/Code.tar.bz2' not found in ...` and `hasattr(...extract_code) is True`

- [ ] **Step 3: Fix `jobquery`**

Replace `codesize` and delete `extract_code` entirely (`utils/jobquery.py:74-88`):

```python
    def codesize(self):
        """Bytes of code embedded in this cnf: always 0.

        prodtools ships an Offline build as a jobsub sidecar
        (--tar_file_name), never inside the cnf, so nothing is embedded
        and 0 is the honest answer rather than a placeholder. The build
        a code-mode cnf was made against is recorded in `code_ref`;
        `--recipe` prints it.
        """
        return 0
```

Remove `--extract-code` from the parser (`:182`), from the `queries` list (`:194`), and its dispatch branch (`:257-258`).

In `recipe()`, after the `setup:` line:

```python
        code_ref = self.json_data.get('code_ref')
        if code_ref:
            lines.append(f"code: {code_ref.get('source_path')}")
            lines.append(f"code sha256: {code_ref.get('sha256')}"
                         f"    # ships via jobsub --tar_file_name, "
                         f"not embedded")
```

- [ ] **Step 4: Add the worker diagnostic**

In `bin/runjob.sh`, in the echo block at lines 16-23, after the `CONDOR_DIR_INPUT` line:

```bash
echo "INPUT_TAR_DIR_LOCAL=${INPUT_TAR_DIR_LOCAL:-unset}"
```

No logic — jobsub exports the variable when `--tar_file_name` was passed, and an `unset` here is the first thing to look for when a code-mode job fails.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py TestJobqueryCodeMode`
Expected: PASS, 3 tests

- [ ] **Step 6: Document in the schema**

Add a subsection to `docs/EXAMPLES_schema.md` covering: `simjob_setup` vs `code` as mutually exclusive config keys; that `code` must be `muse tarball` output containing `Code/setup.sh`; `bin/runlocal --code <tarball>`; that the grid path needs nothing extra beyond the entry key; and the tribal facts that are not derivable from code —

- RCDS publication is not instant, and `--skip-check rcds` must not be used.
- The code tarball is not in SAM: delete it and the campaign is unreproducible even though the cnf survives, so a `--prod` tarball must live on a durable mu2epro-readable path.
- A Muse work directory has no `setup.sh`; only `muse tarball` generates one.

- [ ] **Step 7: Regenerate EXAMPLES.md**

`EXAMPLES.md` is a derived artifact — never hand-edit it. Run the `/refresh-examples` slash command (full regeneration) and review the diff.

- [ ] **Step 8: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`
Expected: 1179 tests, OK (skipped=1). A test asserting `jobquery`'s query list or `--help` text may need `--extract-code` removed from its expectation.

- [ ] **Step 9: Commit**

```bash
git add bin/runjob.sh utils/jobquery.py docs/EXAMPLES_schema.md EXAMPLES.md test/test_unit.py
git commit -m "feat(jobquery): report code mode; drop the wrong --extract-code

extract_code() pulled out any tar member ending in .tar, which under
sidecar delivery is not code at all. codesize() now returns 0 as the
honest answer rather than a placeholder, and --recipe prints the
code_ref so a cnf still reconstructs which build it was made against.

runjob.sh echoes INPUT_TAR_DIR_LOCAL: unset is the first thing to check
when a code-mode job fails.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 8: Live gates — local smoke, then grid smoke

Unit tests cannot reach the three things that actually decide whether this works: RCDS publication, `$INPUT_TAR_DIR_LOCAL`, and `muse setup` against a read-only cvmfs mount. **Do not report the feature working until both gates pass.**

**Files:** none modified unless a gate fails.

**Interfaces:** consumes everything from Tasks 1-7.

- [ ] **Step 1: Build a code tarball**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup
muse tarball
```

Note the emitted path (`/exp/mu2e/data/users/$USER/museTarball/tmp.dir/Code.tar.bz2` by default) and its size. Confirm the layout:

```bash
tar tjf <tarball> | head -20   # must list Code/setup.sh
```

- [ ] **Step 2: Build a code-mode cnf**

Pick an existing small entry from `data/`. Add `"code": "<absolute path to Code.tar.bz2>"` and remove `simjob_setup` from a **copy** of that entry, then:

```bash
/mu2e-run json2jobdef --json <config.json> --desc <Desc> --dsconf <dsconf> --verb
```

Expected: the printed equivalent command shows `--code <path>`, and the cnf builds. Verify:

```bash
/mu2e-run jobquery --setup cnf.mu2e.<Desc>.<dsconf>.0.tar     # -> Code/setup.sh
/mu2e-run jobquery --recipe cnf.mu2e.<Desc>.<dsconf>.0.tar    # -> code: + sha256
```

- [ ] **Step 3: Local smoke**

```bash
bin/runlocal --jobdef cnf.mu2e.<Desc>.<dsconf>.0.tar \
             --code <absolute path to Code.tar.bz2> \
             --inloc <the entry's inloc> --indices 0 --nevts 10 \
             --workdir /exp/mu2e/data/users/$USER/claude-scratch/probes/codetest
```

Expected: exit 0, art output in `job_000000/`, and the log line
`Job setup script: /exp/.../codetest/code/Code/setup.sh` — an absolute
path under the unpack directory, not `Code/setup.sh`.

If this fails with `ERROR - Muse already setup`, the driver's
`child_env()` already drops `MUSE_WORK_DIR`; a failure here means the
unpacked `Code/setup.sh` itself is calling `muse setup` in a way that
conflicts — read the child's `stdout.log` before changing anything.

- [ ] **Step 4: Grid smoke — enqueue**

Five jobs, own account, own datasets. **`run_as="self"` only** — nothing in this plan runs as `mu2epro`.

```bash
/mu2e-run json2jobdef --json <config.json> --desc <Desc> --dsconf <dsconf> \
          --enqueue --slice-size 5
```

Expected: the pre-flight gate passes (`check_code_tarball` matched the digest) and a campaign id is printed. To prove the gate is armed rather than merely silent, append a byte to the tarball and re-run the same command: it must exit 2 with a `code_mismatch` problem. Restore the tarball afterwards by rebuilding and rebuilding the cnf.

- [ ] **Step 5: Grid smoke — submit and watch**

Feed one slice with `submissions run` for that campaign (`run_as="self"`). Then confirm in the submitted command line that BOTH mechanisms are present:

```
--tar_file_name dropbox://<...>/Code.tar.bz2
-f dropbox://<...>/ops.json  -f dropbox://<...>cnf...tar  -f dropbox://<...>prodtools-....tar
```

The first submit blocks while jobsub publishes to RCDS. That wait is expected; do not add `--skip-check rcds`.

- [ ] **Step 6: Read a worker log**

In the job log, confirm in order:

1. `INPUT_TAR_DIR_LOCAL=` is a real path, not `unset`.
2. `Job setup script: <that path>/Code/setup.sh`.
3. No `Error sourcing setup script`, and `mu2e` starts.
4. The job exits 0 and its outputs land.

Point 3 is the assumption the whole design rests on: `muse setup` running against a **read-only** cvmfs mount. mu2eprodsys does this in production, so it is expected to work — this is the measurement that turns that into a fact.

- [ ] **Step 7: Record the result**

Write a wiki page under `wiki/pages/` recording: the tarball size, the RCDS publish wall time, whether `muse setup` worked read-only, and the cluster id. If any gate failed, record the failure and stop — do not paper over it.

```bash
git add wiki/pages/<slug>.md
git commit -m "docs(wiki): code tarball grid smoke result"
```

---

## Notes for the implementer

- **Test counts in each task are cumulative and approximate.** The baseline is 1133; each task states what it should reach. If your number differs by a few because you split a test differently, that is fine — a *decrease* is not.
- **Never weaken an assertion to make a test pass.** If an existing test breaks, read it first: most of this plan is additive and existing cnfs (absolute setup, no `code_ref`) take unchanged code paths.
- **Do not `git push`.** The user pushes from their own shell.
- Task 5 is the one cleanly separable piece. If it proves costly, it can be dropped without breaking Tasks 1-4 and 6-7 — but say so explicitly rather than silently skipping it.
