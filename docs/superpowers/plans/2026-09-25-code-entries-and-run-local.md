# Code-tarball entries in submit_once, and run_local — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the prodtools MCP write server submit code-tarball entries once to outstage (P1), and run any entry on the local node, detached, with a receipt that `run_status` reads (P2).

**Architecture:** A new stdlib-only `utils/code_cache.py` unpacks each code tarball once per content hash. The write server then passes the unpacked `Code/setup.sh` to `run_cli` where it would pass a Musing's `setup.sh`; the two scripts are identical in form. `json2jobdef --once --local` reuses `submit_once`'s refusals, build and receipt, then starts `utils/runlocal.py` in its own session instead of submitting. `run_status` gains a branch that reads runlocal's `summary.json`, or checks `/proc/<pid>/cmdline` while the run is going.

**Tech Stack:** Python 3.9, the system `/usr/bin/python3` that runs the unit suite, with stdlib `unittest`. The Mu2e runtime for the live gate: `setupmu2e-art.sh`, `muse`, and jobsub is not used.

**Spec:** `docs/superpowers/specs/2026-09-25-code-entries-and-run-local-design.md`

## Global Constraints

- **Worktree:** `WT=/exp/mu2e/data/users/oksuzian/claude-scratch/worktrees/prodtools-code-entries`. Every command runs from `$WT`. Never touch `/exp/mu2e/app/users/oksuzian/muse_050125/prodtools`.
- **Branches:** Tasks 1–2 go on `code-entries-run-local` (PR 1). Task 3 starts by running `git switch -c run-local`, and Tasks 3–6 go on `run-local` (PR 2).
- **Unit suite command:** `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit`. For one class, use `... -m unittest test.test_unit.<Class> -v`. The baseline on `mu2e/main` 8c592cb is `Ran 1477 tests ... OK (skipped=4)`.
- **Python 3.9:**
  - no `match`;
  - no `X | None` in annotations evaluated at runtime;
  - no `tarfile` `filter=` argument.
- **Where new test classes go:** directly above the final `if __name__ == '__main__':` block of `test/test_unit.py`. Task 1 moves that block to the end of the file.
- **Tests write only to temporary directories.** Get them from `_mkdtemp()`, pass `root=`, or patch `code_cache.cache_root`. No test writes under `/exp/mu2e/data`.
- **Stdlib only:** `utils/code_cache.py` and `utils/run_receipt.py` import nothing from the Mu2e stack. The read-only server imports `run_receipt`.
- **No stdout from the write server:** nothing it calls in-process may print. Its stdout carries the MCP protocol.
- **No silent fallbacks.** Every refusal raises, or calls `sys.exit` with a message that names the cause and the fix.
- **`submit_once` and `run_local` are `run_as="self"` only.**
- **Commit messages** end with these two lines:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM
  ```
- **Never push, open a PR, or post to GitHub.** Task 8 is the controller's, and it needs the user's go-ahead.
- **Live-gate scratch** goes under `/exp/mu2e/data/users/oksuzian/`, never `/tmp`.

## Review Focus

1. **A relative `--code-root` given to runlocal.** Jobs run in their own `job_NNNNNN/` directories, so the path must be made absolute at the driver. Task 3: `test_a_relative_code_root_is_made_absolute`.
2. **`kill <pid>` on a running local run.** No `mu2e` may be left running. Each job has its own session, so this needs the new SIGTERM handler. Task 3: `test_sigterm_ends_a_real_job_in_its_own_session_and_exits_143`. The live gate is Task 7, step 5.
3. **The same desc+dsconf twice, or a leftover `starting` receipt.**
   - The refusal must point at a possibly running runlocal. Task 4: `test_a_used_name_points_at_a_possibly_running_local_run`.
   - `run_status` must return `starting` as it is. Task 6: `test_a_starting_receipt_is_returned_as_it_is`.
4. **A stale `<sha>.part.<pid>` directory from a killed unpack.** It must not block, or be mistaken for, a finished tree. Task 1: `test_a_stale_part_dir_from_a_killed_unpack_is_left_alone`.
5. **A job runlocal killed on timeout (rc 124).** It reads `short` with its exit code, never `done`. Task 6: `test_a_failed_or_timed_out_job_is_short_with_its_exit_code`.

---

### Task 1: Run the whole unit suite, and add `utils/code_cache.py`

**Files:**
- Create: `utils/code_cache.py`
- Modify: `test/test_unit.py`: move the `if __name__ == '__main__':` block at lines 18787-18788 to the end of the file, and add `class TestCodeCache` above it.

**Interfaces:**
- Produces:
  - `code_cache.cache_root(user=None) -> str` returns `'/exp/mu2e/data/users/<user>/prodtools/code'`.
  - `code_cache.unpacked(tarball: str, root: str = None) -> str` returns the directory holding `Code/`, which is `<root>/<sha256 of the bytes>`. It raises `ValueError` or `OSError` and never prints.

- [ ] **Step 1: Make the direct run execute every class**

`test/test_unit.py` calls `unittest.main()` at line 18787, and 15 classes (87 tests) are defined below it. Running the file directly (`python3 test/test_unit.py`) never runs them. Delete these two lines at 18787-18788:

```python
if __name__ == '__main__':
    unittest.main(verbosity=2)
```

Then append them as the last lines of the file, after one blank line:

```python


if __name__ == '__main__':
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Verify that both ways of running agree**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py 2>&1 | grep -E '^Ran|^OK|^FAILED'`
Expected: `Ran 1477 tests` and `OK (skipped=4)`. Before this step it said `Ran 1390`.

- [ ] **Step 3: Commit**

```bash
git add test/test_unit.py
git commit -m "test: run every class in test_unit.py, not only those above unittest.main()

15 classes (87 tests, including every submit_once and run_status test)
were defined below the __main__ block, so running the file directly
never ran them; python -m unittest did.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM"
```

- [ ] **Step 4: Write the failing tests**

Add `import signal` to the imports at the top of `test/test_unit.py`, after `import shutil`; Task 3 uses it. Then add this class directly above the final `if __name__ == '__main__':` block:

```python
class TestCodeCache(unittest.TestCase):
    """utils/code_cache: one unpack per tarball CONTENT, shared by every
    cnf build and local run of it. Silent, because the write MCP server
    calls it in-process and its stdout carries the protocol."""

    def setUp(self):
        from utils import code_cache
        self.cc = code_cache
        self.dir = _mkdtemp()
        self.root = os.path.join(self.dir, 'cache')
        self.code = _make_code_tarball(os.path.join(self.dir, 'Code.tar.bz2'))

    @staticmethod
    def _sha(path):
        with open(path, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def test_a_miss_unpacks_under_the_content_hash(self):
        got = self.cc.unpacked(self.code, self.root)
        self.assertEqual(got, os.path.join(self.root, self._sha(self.code)))
        self.assertTrue(os.path.isfile(os.path.join(got, 'Code', 'setup.sh')))
        self.assertTrue(os.path.isfile(
            os.path.join(got, 'Code', 'lib', 'libFake.so')))

    def test_a_hit_does_not_open_the_tarball_again(self):
        first = self.cc.unpacked(self.code, self.root)
        with patch.object(self.cc.tarfile, 'open',
                          side_effect=AssertionError('re-extracted')):
            self.assertEqual(self.cc.unpacked(self.code, self.root), first)

    def test_the_key_is_the_content_not_the_file_name(self):
        other = os.path.join(self.dir, 'Renamed.tar.bz2')
        shutil.copy(self.code, other)
        self.assertEqual(self.cc.unpacked(self.code, self.root),
                         self.cc.unpacked(other, self.root))
        self.assertEqual(len(os.listdir(self.root)), 1)

    def test_no_code_setup_is_refused_and_leaves_nothing(self):
        bad = _make_code_tarball(os.path.join(self.dir, 'NoSetup.tar.bz2'),
                                 with_setup=False)
        with self.assertRaises(ValueError) as ctx:
            self.cc.unpacked(bad, self.root)
        self.assertIn('Code/setup.sh', str(ctx.exception))
        self.assertIn('muse tarball', str(ctx.exception))
        self.assertEqual(os.listdir(self.root), [])

    def test_a_tarball_that_is_not_bzip2_is_refused_and_leaves_nothing(self):
        bad = _make_code_tarball(os.path.join(self.dir, 'Plain.tar'),
                                 bzip2=False)
        with self.assertRaises(ValueError) as ctx:
            self.cc.unpacked(bad, self.root)
        self.assertIn('bzip2', str(ctx.exception))
        self.assertEqual(os.listdir(self.root), [])

    def test_relative_and_missing_paths_are_refused(self):
        for bad in ('Code.tar.bz2',
                    os.path.join(self.dir, 'nope.tar.bz2'),
                    None):
            with self.assertRaises(ValueError, msg=bad):
                self.cc.unpacked(bad, self.root)
        self.assertFalse(os.path.exists(self.root))

    def test_a_lost_rename_race_returns_the_winners_tree(self):
        """Two builds of one new tarball at once (autoresearch starts
        several stages together): the loser uses the winner's tree and
        leaves no part directory behind."""
        def racing(src, dst):
            shutil.copytree(src, dst)          # the other process won
            raise OSError(39, 'Directory not empty', dst)
        with patch.object(self.cc.os, 'rename', side_effect=racing):
            got = self.cc.unpacked(self.code, self.root)
        self.assertTrue(os.path.isfile(os.path.join(got, 'Code', 'setup.sh')))
        self.assertEqual(os.listdir(self.root), [os.path.basename(got)])

    def test_a_stale_part_dir_from_a_killed_unpack_is_left_alone(self):
        stale = os.path.join(self.root, self._sha(self.code) + '.part.99999')
        os.makedirs(os.path.join(stale, 'Code'))
        got = self.cc.unpacked(self.code, self.root)
        self.assertTrue(os.path.isfile(os.path.join(got, 'Code', 'setup.sh')))
        self.assertTrue(os.path.isdir(stale))

    def test_it_never_writes_to_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cc.unpacked(self.code, self.root)
            self.cc.unpacked(self.code, self.root)
        self.assertEqual(out.getvalue(), '')

    def test_the_default_root_sits_next_to_runs(self):
        from utils import run_receipt
        self.assertEqual(self.cc.cache_root('alice'),
                         '/exp/mu2e/data/users/alice/prodtools/code')
        self.assertEqual(os.path.dirname(self.cc.cache_root('alice')),
                         os.path.dirname(run_receipt.runs_root('alice')))
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestCodeCache -v 2>&1 | tail -5`
Expected: 10 errors, `ModuleNotFoundError: No module named 'utils.code_cache'` (or `ImportError: cannot import name 'code_cache'`).

- [ ] **Step 6: Write the implementation**

Create `utils/code_cache.py`:

```python
#!/usr/bin/env python3
"""A per-user cache of unpacked `muse tarball` code tarballs.

A code-tarball entry (`code` set, no `simjob_setup`) has no Musing to
source: its environment is the tarball's own `Code/setup.sh`, the same
script the grid worker sources from $INPUT_TAR_DIR_LOCAL. Building its
cnf, or running it locally, needs the tarball unpacked somewhere. This
unpacks each tarball ONCE, under a directory named for the sha256 of its
bytes, so every build and local run of the same content shares one
tree, and a tarball rebuilt in place gets a new one.

Pure stdlib and SILENT: the write MCP server calls it in-process, and
that server's stdout carries the MCP protocol. runlocal.unpack_code
prints progress and sys.exit()s, which is right for a CLI and fatal
there.

Nothing evicts entries. Delete a <sha256> directory to free it; that is
safe while no local run is using it (run_status says which are running).
"""

import getpass
import hashlib
import os
import shutil
import tarfile

SETUP = os.path.join('Code', 'setup.sh')


def cache_root(user=None):
    """Next to runs/ (run_receipt.runs_root)."""
    return f'/exp/mu2e/data/users/{user or getpass.getuser()}/prodtools/code'


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def unpacked(tarball, root=None):
    """The directory holding Code/ for this tarball's content, unpacking
    it on first use. Raises ValueError or OSError; never prints.

    The rename is the commit: the tree is extracted into
    `<key>.part.<pid>` and renamed into place only once it is complete
    and has a Code/setup.sh, so a killed unpack leaves a part directory,
    never half a tree under the real name. A concurrent unpack of the
    same bytes that renamed first wins; this one then uses its tree.
    """
    if not isinstance(tarball, str) or not os.path.isabs(tarball):
        raise ValueError(
            f'a code tarball is named by an absolute path, got {tarball!r}')
    if not os.path.isfile(tarball):
        raise ValueError(f'code tarball {tarball} does not exist')
    root = root or cache_root()
    final = os.path.join(root, _sha256(tarball))
    if os.path.isfile(os.path.join(final, SETUP)):
        return final
    os.makedirs(root, exist_ok=True)
    part = f'{final}.part.{os.getpid()}'
    shutil.rmtree(part, ignore_errors=True)     # this pid's own leftover
    try:
        try:
            with tarfile.open(tarball, 'r:bz2') as tar:
                tar.extractall(part)
        except tarfile.ReadError as exc:
            raise ValueError(
                f'code tarball {tarball} is not a bzip2 tar file '
                f'({exc}): build it with `muse tarball`') from exc
        if not os.path.isfile(os.path.join(part, SETUP)):
            raise ValueError(
                f'code tarball {tarball} has no Code/setup.sh: build it '
                f'with `muse tarball`')
        try:
            os.rename(part, final)
        except OSError:
            if not os.path.isfile(os.path.join(final, SETUP)):
                raise
    finally:
        shutil.rmtree(part, ignore_errors=True)
    return final
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestCodeCache -v 2>&1 | tail -3`
Expected: `Ran 10 tests`, `OK`.

Run the full suite: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit 2>&1 | grep -E '^Ran|^OK|^FAILED'`
Expected: `Ran 1487 tests`, `OK (skipped=4)`.

- [ ] **Step 8: Commit**

```bash
git add utils/code_cache.py test/test_unit.py
git commit -m "feat(code_cache): unpack each code tarball once per content hash

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM"
```

---

### Task 2: `submit_once` builds code-tarball entries in the tarball's environment

**Files:**
- Modify: `mcp/src/prodtools_mcp_write/tools.py` (imports at lines 33-44; `_select_push_params` at :47-97; `submit_once` at :339-391)
- Modify: `mcp/README.md` (the `prodtools-write` section, after the `push_file(...)` paragraph)
- Modify: `CLAUDE.md` (the `submit_once` paragraph that starts "A third write tool")
- Test: `test/test_unit.py`, new `class TestCodeEntryPushParams`

**Interfaces:**
- Consumes: `code_cache.unpacked(tarball) -> str` (Task 1).
- Produces:
  - `tools._select_push_params(json_path, desc, dsconf, allow_code=False) -> (setup_script, tarball_desc)`. For a code entry with `allow_code=True`, `setup_script` is `<cache>/<sha256>/Code/setup.sh`.
  - `tools._code_setup(entry, desc, dsconf, json_path, allow_code) -> str`.

- [ ] **Step 1: Write the failing tests**

Add above the final `__main__` block:

```python
class TestCodeEntryPushParams(unittest.TestCase):
    """A code-tarball entry (`code`, no simjob_setup) builds in the
    tarball's own environment: submit_once sources the unpacked
    Code/setup.sh where it would source a Musing's setup.sh (the two are
    the same script). push_cnf refuses: a production cnf needs its code
    tarball on a durable path mu2epro can read first."""

    def setUp(self):
        from prodtools_mcp_write import tools
        from utils import code_cache
        self.tools = tools
        self.tmp = _mkdtemp()
        self.root = os.path.join(self.tmp, 'cache')
        patcher = patch.object(code_cache, 'cache_root',
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.code = _make_code_tarball(os.path.join(self.tmp, 'Code.tar.bz2'))
        with open(self.code, 'rb') as fh:
            self.setup = os.path.join(
                self.root, hashlib.sha256(fh.read()).hexdigest(),
                'Code', 'setup.sh')
        self.json_path = self._write({'code': self.code})

    def _write(self, keys, name='entries.json'):
        path = os.path.join(self.tmp, name)
        entry = {'desc': 'D', 'dsconf': 'C', 'fcl': 'x.fcl',
                 'outloc': {'*.art': 'outstage'}}
        entry.update(keys)
        with open(path, 'w') as fh:
            json.dump([entry], fh)
        return path

    def test_push_cnf_params_refuse_a_code_entry_and_name_the_way(self):
        with self.assertRaises(ValueError) as ctx:
            self.tools._select_push_params(self.json_path, 'D', 'C')
        self.assertIn('submit_once', str(ctx.exception))
        self.assertIn('run_local', str(ctx.exception))
        self.assertFalse(os.path.exists(self.root))     # nothing unpacked

    def test_allow_code_returns_the_unpacked_setup_script(self):
        setup, desc = self.tools._select_push_params(
            self.json_path, 'D', 'C', allow_code=True)
        self.assertEqual(setup, self.setup)
        self.assertTrue(os.path.isfile(setup))
        self.assertEqual(desc, 'D')

    def test_an_unusable_tarball_is_refused_naming_the_entry(self):
        path = self._write(
            {'code': os.path.join(self.tmp, 'gone.tar.bz2')}, 'gone.json')
        with self.assertRaises(ValueError) as ctx:
            self.tools._select_push_params(path, 'D', 'C', allow_code=True)
        self.assertIn("desc='D'", str(ctx.exception))
        self.assertIn('does not exist', str(ctx.exception))

    def test_a_musing_entry_is_unchanged(self):
        musing = '/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/C/setup.sh'
        path = self._write({'simjob_setup': musing}, 'musing.json')
        for allow in (False, True):
            self.assertEqual(self.tools._select_push_params(
                path, 'D', 'C', allow_code=allow)[0], musing)
        self.assertFalse(os.path.exists(self.root))

    def test_submit_once_sources_the_unpacked_setup(self):
        receipt = os.path.join(self.tmp, 'receipt.json')
        with open(receipt, 'w') as fh:
            json.dump({'name': 'cnf.alice.D.C.0', 'state': 'submitted'}, fh)
        with patch('prodtools_mcp_write.runner.run_cli',
                   return_value={'rc': 0, 'stderr': '',
                                 'stdout': f'RECEIPT {receipt}\n'}) as run:
            self.tools.submit_once(self.json_path, 'D', 'C', 'self')
        self.assertEqual(run.call_args[1]['simjob_setup'], self.setup)

    def test_push_cnf_refuses_before_running_anything(self):
        with patch('prodtools_mcp_write.runner.run_cli') as run:
            with self.assertRaises(ValueError) as ctx:
                self.tools.push_cnf(self.json_path, 'D', 'C', 1000, 'self')
        self.assertIn('submit_once', str(ctx.exception))
        run.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestCodeEntryPushParams -v 2>&1 | tail -8`
Expected:
- `TypeError: _select_push_params() got an unexpected keyword argument 'allow_code'` for the `allow_code` tests;
- the refusal tests fail, because the message says "has no simjob_setup field" and names neither `submit_once` nor `run_local`;
- `test_a_musing_entry_is_unchanged` errors, for the same `allow_code` reason.

- [ ] **Step 3: Write the implementation**

In `mcp/src/prodtools_mcp_write/tools.py`, add `import os` after `import json as _json` in the stdlib imports, and add this line after `from utils import push_file as _push_file`:

```python
from utils import code_cache
```

Change the signature of `_select_push_params` to:

```python
def _select_push_params(json_path, desc, dsconf, allow_code=False):
```

Append this paragraph to its docstring, before the closing `"""`:

```python
    A code-tarball entry (`code`, no `simjob_setup`) has its Musing
    INSIDE the tarball: with `allow_code` (submit_once, run_local) the
    returned setup script is that tarball's unpacked Code/setup.sh (see
    _code_setup); without it (push_cnf) the entry is refused.
```

Replace these lines:

```python
    is_g4bl = determine_job_type(entry) == 'g4bl'
    simjob_setup = None if is_g4bl else entry.get('simjob_setup')
    if not is_g4bl and not simjob_setup:
```

with:

```python
    is_g4bl = determine_job_type(entry) == 'g4bl'
    simjob_setup = None if is_g4bl else entry.get('simjob_setup')
    if not is_g4bl and not simjob_setup and entry.get('code'):
        simjob_setup = _code_setup(entry, desc, dsconf, json_path, allow_code)
    if not is_g4bl and not simjob_setup:
```

Add this function directly after `_select_push_params`:

```python
def _code_setup(entry, desc, dsconf, json_path, allow_code):
    """`Code/setup.sh` of the entry's code tarball, unpacked once into the
    per-user cache (utils/code_cache).

    A cvmfs Musing's setup.sh and a `muse tarball` Code/setup.sh are the
    same script (`muse setup $CODE_DIR`, then setup_post.sh), so run_cli
    sources this exactly as it sources a Musing, and the build sees what
    the grid worker will: the tarball's own FHiCL and search paths.

    push_cnf refuses (allow_code=False): a production cnf built against a
    code tarball needs that tarball on a durable path mu2epro can read
    first, which is a separate decision.
    """
    if not allow_code:
        raise ValueError(
            f"push_cnf: entry matching desc={desc!r} dsconf={dsconf!r} in "
            f"{json_path!r} is a code-tarball entry (`code`, no "
            f"simjob_setup). Those go through submit_once or run_local "
            f"only: a production cnf needs its code tarball on a durable "
            f"path mu2epro can read first.")
    try:
        root = code_cache.unpacked(entry['code'])
    except (ValueError, OSError) as e:
        raise ValueError(
            f"entry matching desc={desc!r} dsconf={dsconf!r} in "
            f"{json_path!r}: cannot use its code tarball: {e}") from e
    return os.path.join(root, 'Code', 'setup.sh')
```

In `submit_once`, change

```python
    simjob_setup, _ = _select_push_params(json, desc, dsconf)
```

to

```python
    simjob_setup, _ = _select_push_params(json, desc, dsconf,
                                          allow_code=True)
```

and add this paragraph to its docstring, after the `prodtools_dir` paragraph:

```python
    A code-tarball entry (`code`, no `simjob_setup`) builds in the
    tarball's own environment: it is unpacked once into
    /exp/mu2e/data/users/<you>/prodtools/code/<sha256>/ and its
    Code/setup.sh is sourced where a Musing's setup.sh would be.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestCodeEntryPushParams test.test_unit.TestSubmitOnceTool -v 2>&1 | tail -3`
Expected: `Ran 12 tests`, `OK`.

- [ ] **Step 5: Document it**

In `mcp/README.md`, add this paragraph after the paragraph that starts "`push_file(path, location, parents, run_as, confirm=False)`":

```markdown
`submit_once(json, desc, dsconf, run_as)` sends one entry to the grid
once, every output to outstage and nothing to SAM (`json2jobdef
--once`); the read-only server's `run_status(name, user=)` reports on
it. It also takes a code-tarball entry (`code`, no `simjob_setup`): the
tarball is unpacked once into
`/exp/mu2e/data/users/$USER/prodtools/code/<sha256>/`, and its
`Code/setup.sh` is sourced where a Musing's `setup.sh` would be. Nothing
clears that cache: delete a `<sha256>` directory to free it. `push_cnf`
refuses code-tarball entries.
```

In `CLAUDE.md`, append this sentence to the paragraph that starts "A third write tool, `submit_once`", after "declared.":

```markdown
It also takes code-tarball entries (`code`, no `simjob_setup`), built in
the tarball's own environment (`utils/code_cache.py`); `push_cnf`
refuses them.
```

- [ ] **Step 6: Run the full suite**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit 2>&1 | grep -E '^Ran|^OK|^FAILED'`
Expected: `Ran 1493 tests`, `OK (skipped=4)`.

- [ ] **Step 7: Commit**

```bash
git add mcp/src/prodtools_mcp_write/tools.py mcp/README.md CLAUDE.md test/test_unit.py
git commit -m "feat(mcp-write): submit_once takes code-tarball entries

The entry's tarball is unpacked once into the per-user code cache and
its Code/setup.sh is sourced where a Musing's setup.sh would be; the two
are the same script, so the runner is unchanged. push_cnf refuses code
entries: a production cnf needs the tarball on a durable path first.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM"
```

---

### Task 3: runlocal takes `--code-root`, and SIGTERM ends its jobs

**Files:**
- Modify: `utils/runlocal.py`:
  - imports (add `import threading`);
  - the new `_RUNNING`, `_RUNNING_LOCK` and `_terminate` next to `kill_job` (:246);
  - `_run_child` (:276-310);
  - `build_parser` (`--code-root` at :541-542);
  - `main` (:556-580).
- Test: `test/test_unit.py`, new `class TestRunLocalCodeRoot` and `class TestRunLocalStop`

**Interfaces:**
- Produces:
  - runlocal driver flag `--code-root DIR`, which is absolute after `main`, holds a `Code/setup.sh`, and cannot be given together with `--code`;
  - `runlocal._RUNNING: dict[int, Popen]` keyed by `id(proc)`;
  - `runlocal._RUNNING_LOCK: threading.Lock`;
  - `runlocal._terminate(signum, frame)`, which never returns and calls `os._exit(128 + signum)`.

- [ ] **Step 1: Create the PR 2 branch**

```bash
git switch -c run-local
```

- [ ] **Step 2: Write the failing tests**

Add above the final `__main__` block:

```python
class TestRunLocalCodeRoot(unittest.TestCase):
    """--code-root is public: prodtools' code cache hands runlocal an
    already-unpacked tree, so a local run does not unpack it again. Its
    jobs run in their own directories, so the path is made absolute."""

    def setUp(self):
        from utils import runlocal
        self.rl = runlocal
        self.dir = _mkdtemp()
        self.code = _make_code_tarball(os.path.join(self.dir, 'Code.tar.bz2'))
        self.tree = os.path.join(self.dir, 'tree')
        with tarfile.open(self.code, 'r:bz2') as tar:
            tar.extractall(self.tree)

    def _main(self, argv):
        seen = {}

        def drive(args):
            seen['args'] = args
            return 0
        with patch.object(self.rl, 'drive', side_effect=drive), \
             patch.object(self.rl, 'resolve_jobdef',
                          side_effect=lambda jobdef, workdir: jobdef), \
             patch.object(self.rl, 'unpack_code',
                          side_effect=AssertionError('unpacked again')):
            rc = self.rl.main(argv)
        return rc, seen

    def test_a_code_root_reaches_the_jobs_without_an_unpack(self):
        rc, seen = self._main(['--jobdef', '/x/cnf.tar', '--workdir',
                               self.dir, '--code-root', self.tree])
        self.assertEqual(rc, 0)
        want = os.path.realpath(self.tree)
        self.assertEqual(seen['args'].code_root, want)
        argv = self.rl.child_argv(0, seen['args'])
        self.assertEqual(argv[argv.index('--code-root') + 1], want)

    def test_a_relative_code_root_is_made_absolute(self):
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.dir)
        _, seen = self._main(['--jobdef', '/x/cnf.tar', '--workdir',
                              self.dir, '--code-root', 'tree'])
        self.assertTrue(os.path.isabs(seen['args'].code_root))
        self.assertEqual(seen['args'].code_root, os.path.realpath(self.tree))

    def test_code_and_code_root_together_are_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(['--jobdef', '/x/cnf.tar', '--code', self.code,
                        '--code-root', self.tree])
        self.assertIn('not both', str(ctx.exception))

    def test_a_code_root_without_code_setup_is_refused(self):
        empty = os.path.join(self.dir, 'empty')
        os.makedirs(empty)
        with self.assertRaises(SystemExit) as ctx:
            self._main(['--jobdef', '/x/cnf.tar', '--code-root', empty])
        self.assertIn('Code/setup.sh', str(ctx.exception))


class TestRunLocalStop(unittest.TestCase):
    """SIGTERM to the driver ends its jobs too. Each job runs in its own
    session (so a timeout can kill its whole group), which also means a
    signal to the driver alone never reached them: `kill <driver>` left
    every mu2e orphaned and still writing."""

    def setUp(self):
        from utils import runlocal
        self.rl = runlocal
        self.addCleanup(self._reset)

    def _reset(self):
        if self.rl._RUNNING_LOCK.locked():
            self.rl._RUNNING_LOCK.release()
        self.rl._RUNNING.clear()

    def test_sigterm_ends_a_real_job_in_its_own_session_and_exits_143(self):
        job = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            start_new_session=True)
        self.addCleanup(job.wait)
        self.addCleanup(lambda: job.poll() is None and job.kill())
        self.rl._RUNNING[id(job)] = job
        with patch.object(self.rl.os, '_exit',
                          side_effect=SystemExit) as exit_:
            with self.assertRaises(SystemExit):
                self.rl._terminate(signal.SIGTERM, None)
        exit_.assert_called_once_with(143)
        self.assertIsNotNone(job.poll())

    def test_every_running_job_is_ended_before_the_exit(self):
        events = []
        fakes = [SimpleNamespace(pid=2 ** 30 + i) for i in range(3)]
        for fake in fakes:
            self.rl._RUNNING[id(fake)] = fake

        def exit_(code):
            events.append(('exit', code))
            raise SystemExit(code)
        with patch.object(self.rl, 'kill_job',
                          side_effect=lambda proc: events.append(proc.pid)), \
             patch.object(self.rl.os, '_exit', side_effect=exit_):
            with self.assertRaises(SystemExit):
                self.rl._terminate(signal.SIGTERM, None)
        self.assertEqual(sorted(events[:3]), [f.pid for f in fakes])
        self.assertEqual(events[3:], [('exit', 143)])

    def test_a_job_is_registered_while_it_runs_and_removed_after(self):
        seen = []

        def popen(argv, **kwargs):
            proc = SimpleNamespace(pid=2 ** 30, returncode=0)
            proc.wait = lambda timeout=None: (
                seen.append(id(proc) in self.rl._RUNNING) or 0)
            return proc
        tar = _make_tarball(
            {'owner': 'mu2e', 'dsconf': 'TestConf',
             'tbs': {'outfiles': {'o': 'dts.owner.X.version.sequencer.art'}}})
        args = _runlocal_args(jobdef=tar, workdir=_mkdtemp())
        with patch.object(self.rl.subprocess, 'Popen', side_effect=popen):
            with contextlib.redirect_stdout(io.StringIO()):
                self.rl.drive(args)
        self.assertEqual(seen, [True])
        self.assertEqual(self.rl._RUNNING, {})

    def test_the_driver_installs_the_handler_and_restores_the_old_one(self):
        before = signal.getsignal(signal.SIGTERM)
        seen = {}

        def drive(args):
            seen['handler'] = signal.getsignal(signal.SIGTERM)
            return 0
        with patch.object(self.rl, 'drive', side_effect=drive), \
             patch.object(self.rl, 'resolve_jobdef',
                          side_effect=lambda jobdef, workdir: jobdef):
            self.rl.main(['--jobdef', '/x/cnf.tar', '--workdir', _mkdtemp()])
        self.assertIs(seen['handler'], self.rl._terminate)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestRunLocalCodeRoot test.test_unit.TestRunLocalStop -v 2>&1 | tail -12`
Expected:
- `AttributeError: ... has no attribute '_RUNNING_LOCK'` in every `TestRunLocalStop` test;
- `test_code_and_code_root_together_are_refused` and `test_a_code_root_without_code_setup_is_refused` fail, because no `SystemExit` is raised;
- `test_a_relative_code_root_is_made_absolute` fails, because the path stays `'tree'`.

- [ ] **Step 4: Write the implementation**

In `utils/runlocal.py`, add `import threading` after `import tarfile` in the imports. Add this block directly above `def kill_job`:

```python
# The jobs running now, so SIGTERM to the driver can end them too. Each
# job has its own session (kill_job signals its GROUP), so a signal to
# the driver never reaches them: without this, `kill <driver pid>` left
# every running mu2e orphaned and still writing. Keyed by id(): a Popen
# is hashable but the tests' stand-ins are not.
_RUNNING = {}
# Held across each Popen AND its registration, and by the SIGTERM
# handler until the process exits, so no job can start unseen by it.
_RUNNING_LOCK = threading.Lock()


def _terminate(signum, frame):
    """SIGTERM to the driver: end every running job's group, then exit
    128+signum WITHOUT a summary -- a missing summary is how a reader
    tells a stopped run from a finished one (see write_summary). The lock
    is never released: the process ends here."""
    _RUNNING_LOCK.acquire()
    for proc in list(_RUNNING.values()):
        kill_job(proc)
    os._exit(128 + signum)
```

In `_run_child`, replace this part:

```python
        proc = subprocess.Popen(argv, cwd=str(directory), env=child_env(),
                                stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            # 0 means no limit; None is how Popen.wait spells that.
            rc = proc.wait(timeout=args.timeout or None)
        except subprocess.TimeoutExpired:
            timed_out = True
            log.write(f"\n[local] killed after {args.timeout:g}s "
                      f"(--timeout), reported as rc={TIMEOUT_RC}\n")
            log.flush()
            kill_job(proc, log)
            rc = TIMEOUT_RC
```

with:

```python
        with _RUNNING_LOCK:
            proc = subprocess.Popen(argv, cwd=str(directory),
                                    env=child_env(), stdout=log,
                                    stderr=subprocess.STDOUT,
                                    start_new_session=True)
            _RUNNING[id(proc)] = proc
        try:
            try:
                # 0 means no limit; None is how Popen.wait spells that.
                rc = proc.wait(timeout=args.timeout or None)
            except subprocess.TimeoutExpired:
                timed_out = True
                log.write(f"\n[local] killed after {args.timeout:g}s "
                          f"(--timeout), reported as rc={TIMEOUT_RC}\n")
                log.flush()
                kill_job(proc, log)
                rc = TIMEOUT_RC
        finally:
            with _RUNNING_LOCK:
                _RUNNING.pop(id(proc), None)
```

In `build_parser`, replace:

```python
    parser.add_argument('--code-root', default=None,
                        help=argparse.SUPPRESS)
```

with:

```python
    parser.add_argument('--code-root', default=None,
                        help='an already-unpacked code tarball: the '
                             'directory holding Code/ (prodtools\' code '
                             'cache hands this over), instead of --code')
```

In `main`, directly after the `if args.one is not None:` block (the child's early return), insert:

```python
    if args.code and args.code_root:
        sys.exit("runlocal: give --code or --code-root, not both")
    if args.code_root:
        # Absolute: every job runs in its own job_NNNNNN/ directory.
        args.code_root = str(Path(args.code_root).resolve())
        if not (Path(args.code_root) / 'Code' / 'setup.sh').is_file():
            sys.exit(f"runlocal: --code-root {args.code_root} has no "
                     f"Code/setup.sh")
```

Replace the last line of `main`:

```python
    return drive(args)
```

with:

```python
    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        return drive(args)
    finally:
        signal.signal(signal.SIGTERM, previous)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestRunLocalCodeRoot test.test_unit.TestRunLocalStop test.test_unit.TestRunlocalCode test.test_unit.TestRunLocalDrive test.test_unit.TestRunLocalTimeout test.test_unit.TestRunLocalJsonSummary -v 2>&1 | tail -3`
Expected: `OK`.

Run the full suite: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit 2>&1 | grep -E '^Ran|^OK|^FAILED'`
Expected: `Ran 1501 tests`, `OK (skipped=4)`.

- [ ] **Step 6: Commit**

```bash
git add utils/runlocal.py test/test_unit.py
git commit -m "feat(runlocal): public --code-root; SIGTERM ends the running jobs

Each job runs in its own session so a timeout can kill its group, which
meant killing the driver left every mu2e orphaned and still writing.
The driver now ends each running job's group on SIGTERM and exits 143
without a summary.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM"
```

---

### Task 4: `json2jobdef --once --local`

**Files:**
- Modify: `utils/json2jobdef.py`:
  - `main` argparse (after `--once` at :771-776);
  - the flag checks (after the `if args.once:` block, :788-799);
  - the `--once` dispatch (:836-846);
  - `submit_once` (:861-942);
  - new `_start_local` directly after `submit_once`.
- Modify: `utils/run_receipt.py`: the `reserve` message (:77-84)
- Modify: `docs/EXAMPLES_schema.md:397` and `EXAMPLES.md:312-313`
- Test: `test/test_unit.py`, new `class TestJson2jobdefOnceLocal`

**Interfaces:**
- Consumes:
  - `code_cache.unpacked(tarball) -> str` (Task 1);
  - runlocal's `--code-root` (Task 3);
  - `runlocal.DEFAULT_PARALLEL == 4`.
- Produces:
  - CLI `json2jobdef --json J --desc D --dsconf C --once --local [--parallel N]`, which prints the receipt JSON and a `RECEIPT <path>` line, and exits 0 when the state is `running`;
  - `submit_once(config, *, ..., local=False, parallel=None, launch=None)`;
  - `_start_local(run_dir, entry, njobs, parallel=None, launch=None) -> receipt dict`;
  - receipt keys for a local run: `executor: "local"`, `state` (`starting`, then `running`), `host`, `pid`, `started_utc`, `summary` (`<run_dir>/summary.json`), `log` (`<run_dir>/runlocal.log`), `njobs`, `parallel`.

- [ ] **Step 1: Write the failing tests**

Add above the final `__main__` block:

```python
class TestJson2jobdefOnceLocal(unittest.TestCase):
    """`json2jobdef --once --local`: the same refusals, build and receipt
    as --once, then runlocal started detached on this node instead of a
    jobsub_submit. Returns with the receipt `running`."""

    NAME = 'cnf.alice.CeEndpoint.T1.0'

    def setUp(self):
        from utils import json2jobdef, run_receipt
        self.j, self.rr = json2jobdef, run_receipt
        self.root = _mkdtemp()
        self.run_dir = os.path.join(self.root, self.NAME)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        self.calls = {}

    def _config(self, **over):
        c = {'desc': 'CeEndpoint', 'dsconf': 'T1', 'owner': 'alice',
             'fcl': 'x.fcl', 'njobs': 3, 'events': 10, 'run': 1,
             'inloc': 'tape', 'outloc': {'*.art': 'outstage'},
             'simjob_setup': '/cvmfs/x/setup.sh'}
        c.update(over)
        return c

    def _entry(self, config):
        entry = {'tarball': self.NAME + '.tar', 'njobs': config['njobs'],
                 'inloc': 'tape',
                 'outputs': [{'dataset': '*.art', 'location': 'outstage'}]}
        if config.get('code'):
            entry['code'] = config['code']
        return entry

    def _launch(self, argv, **kwargs):
        self.calls['argv'] = argv
        self.calls['kwargs'] = kwargs
        self.calls['state_at_launch'] = self.rr.read(
            self.root, self.NAME)['state']
        return SimpleNamespace(pid=4242)

    def _run(self, config, launch=None, parallel=None):
        def build(cfg, **kwargs):
            self.calls['build_kwargs'] = kwargs

        def submit(*args, **kwargs):
            raise AssertionError('--local must never submit')
        with patch.object(self.j, 'build_jobdesc', side_effect=self._entry), \
             patch.object(self.j, 'prodtools_entry_keys',
                          side_effect=AssertionError('no worker bundle')), \
             patch('utils.code_cache.unpacked', return_value='/cache/abc'), \
             patch('socket.getfqdn', return_value='node.fnal.gov'), \
             patch.object(self.j.getpass, 'getuser', return_value='alice'):
            return self.j.submit_once(
                config, json_path='/j.json', root=self.root, build=build,
                submit=submit, local=True, parallel=parallel,
                launch=launch or self._launch)

    def test_local_starts_runlocal_detached_and_records_it(self):
        receipt = self._run(self._config())
        summary = os.path.join(self.run_dir, 'summary.json')
        log = os.path.join(self.run_dir, 'runlocal.log')
        self.assertEqual(self.calls['state_at_launch'], 'starting')
        self.assertFalse(self.calls['build_kwargs'].get('pushout'))
        argv = self.calls['argv']
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith(os.path.join('utils', 'runlocal.py')))
        self.assertEqual(argv[2:], [
            '--jobdef', os.path.join(self.run_dir, self.NAME + '.tar'),
            '--inloc', 'tape', '--first', '0', '--num', '3',
            '--parallel', '4', '--workdir', self.run_dir, '--json', summary])
        kwargs = self.calls['kwargs']
        self.assertEqual(kwargs['cwd'], self.run_dir)
        self.assertTrue(kwargs['start_new_session'])
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stdout'].name, log)
        self.assertIs(kwargs['stderr'], subprocess.STDOUT)
        self.assertEqual(receipt['state'], 'running')
        for key, want in (('executor', 'local'), ('host', 'node.fnal.gov'),
                          ('pid', 4242), ('summary', summary), ('log', log),
                          ('njobs', 3), ('parallel', 4)):
            self.assertEqual(receipt[key], want, key)
        self.assertIn('started_utc', receipt)
        self.assertEqual(receipt, self.rr.read(self.root, self.NAME))

    def test_a_code_entry_hands_runlocal_the_cached_code_root(self):
        config = self._config(code='/x/Code.tar.bz2')
        del config['simjob_setup']
        self._run(config)
        self.assertEqual(self.calls['argv'][-2:], ['--code-root', '/cache/abc'])

    def test_parallel_is_forwarded(self):
        receipt = self._run(self._config(), parallel=2)
        argv = self.calls['argv']
        self.assertEqual(argv[argv.index('--parallel') + 1], '2')
        self.assertEqual(receipt['parallel'], 2)

    def test_a_launch_failure_is_recorded_as_failed(self):
        def broken(argv, **kwargs):
            raise OSError('no such interpreter')
        with self.assertRaises(SystemExit):
            self._run(self._config(), launch=broken)
        got = self.rr.read(self.root, self.NAME)
        self.assertEqual(got['state'], 'failed')
        self.assertIn('no such interpreter', got['error'])

    def test_the_once_refusals_still_apply(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(self._config(outloc={'*.art': 'scratch'}))
        self.assertIn('outstage', str(ctx.exception))
        self.assertEqual(os.listdir(self.root), [])

    def test_a_g4bl_entry_is_refused_before_anything_exists(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(self._config(runner='g4bl'))
        self.assertIn('g4bl', str(ctx.exception))
        self.assertEqual(os.listdir(self.root), [])

    def test_a_used_name_points_at_a_possibly_running_local_run(self):
        self._run(self._config())
        with self.assertRaises(SystemExit) as ctx:
            self._run(self._config())
        self.assertIn('runlocal', str(ctx.exception))

    def test_a_real_child_owns_its_session_and_writes_to_the_log(self):
        """The MCP runner captures stdout/stderr and waits for EOF, so a
        child holding either would block the tool for the whole run."""
        def real(argv, **kwargs):
            self.calls['proc'] = subprocess.Popen(
                [sys.executable, '-c',
                 'import os, sys; print("from the child", os.getsid(0)); '
                 'sys.stdout.flush()'], **kwargs)
            return self.calls['proc']
        receipt = self._run(self._config(), launch=real)
        proc = self.calls['proc']
        proc.wait(timeout=30)
        with open(receipt['log']) as fh:
            text = fh.read()
        self.assertIn('from the child', text)
        self.assertEqual(int(text.split()[-1]), proc.pid)   # its own session

    def test_the_command_line_rules(self):
        base = ['--json', '/nope.json', '--desc', 'a', '--dsconf', 'b']
        for extra, want in (
                (['--local'], '--local requires --once'),
                (['--once', '--parallel', '2'], '--parallel requires --local'),
                (['--once', '--local', '--parallel', '0'], 'at least 1'),
                (['--once', '--local', '--prodtools-dir', '/x'],
                 '--prodtools-dir')):
            with self.assertRaises(SystemExit, msg=extra) as ctx:
                self.j.main(base + extra)
            self.assertIn(want, str(ctx.exception))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestJson2jobdefOnceLocal -v 2>&1 | tail -12`
Expected:
- `TypeError: submit_once() got an unexpected keyword argument 'local'` in every `submit_once` test;
- `test_the_command_line_rules` fails with argparse's `unrecognized arguments: --local`.

- [ ] **Step 3: Write the implementation**

**(a) The receipt message.** In `utils/run_receipt.py` `reserve`, replace the `RunExists` message with:

```python
        raise RunExists(
            f'{run_dir} exists: a desc+dsconf pair is used once, because '
            f'the output file names derive from it and two runs writing '
            f'the same names cannot be told apart. If its receipt says '
            f'"submitting", the previous attempt died mid-submit: look '
            f'for its cluster in jobsub_q before doing anything else. If '
            f'it says "starting" or "running", a local run may still be '
            f'going: look for a runlocal process whose command line names '
            f'{run_dir}. Pick a new dsconf.') from None
```

**(b) The flags.** In `utils/json2jobdef.py` `main`, add these after the `--once` argument:

```python
    p.add_argument('--local', action='store_true',
                   help='With --once: run the jobs on THIS node with '
                        'runlocal instead of submitting them. Returns once '
                        'runlocal has started, detached; the receipt says '
                        'where, and run_status reports. Excludes '
                        '--prodtools-dir.')
    p.add_argument('--parallel', type=int, default=None,
                   help='With --local: jobs at once (default 4, as '
                        'runlocal -j).')
```

**(c) The flag checks.** Directly after the `if args.once:` block, before `if args.enqueue and not args.prod:`, insert:

```python
    if args.local and not args.once:
        sys.exit("json2jobdef: --local requires --once")
    if args.parallel is not None and not args.local:
        sys.exit("json2jobdef: --parallel requires --local")
    if args.parallel is not None and args.parallel < 1:
        sys.exit("json2jobdef: --parallel must be at least 1")
    if args.local and args.prodtools_dir is not None:
        sys.exit("json2jobdef: --local runs this checkout's runlocal on "
                 "this node; --prodtools-dir names worker code for grid "
                 "jobs, so it has no meaning here")
```

**(d) The dispatch.** In the `--once` dispatch, replace:

```python
            receipt = submit_once(config, json_path=args.json,
                                  prodtools_dir=args.prodtools_dir)
```

with:

```python
            receipt = submit_once(config, json_path=args.json,
                                  prodtools_dir=args.prodtools_dir,
                                  local=args.local, parallel=args.parallel)
```

and replace:

```python
            sys.exit(0 if receipt['state'] == 'submitted' else 1)
```

with:

```python
            sys.exit(0 if receipt['state'] in ('submitted', 'running')
                     else 1)
```

**(e) `submit_once`.** Change its signature to:

```python
def submit_once(config, *, json_path=None, prodtools_dir=None, root=None,
                build=None, submit=None, local=False, parallel=None,
                launch=None):
```

Replace the last docstring paragraph ("Every refusal that can be made ...") with:

```python
    `local` (`--once --local`): the same refusals, build and receipt, but
    the jobs run on THIS node with runlocal, started detached
    (_start_local), instead of one jobsub_submit. One entry shape serves
    both executors, and a desc+dsconf pair is used once across them.

    Every refusal that can be made from the entry alone is made before
    anything exists on disk. `build`, `submit` and `launch` (runlocal's
    Popen) are seams for the tests.
```

Directly before `name = run_receipt.run_name(get_parfile_name(config))`, insert:

```python
    if local and determine_job_type(config) == 'g4bl':
        sys.exit("json2jobdef: --local runs art jobs with runlocal; a g4bl "
                 "entry cannot run locally.")
```

Replace:

```python
        entry.update(prodtools_entry_keys(
            resolve_prodtools_dir(prodtools_dir or PRODTOOLS_CVMFS_CURRENT),
            user=user))
```

with:

```python
        if not local:
            # The worker code travels with grid jobs only.
            entry.update(prodtools_entry_keys(
                resolve_prodtools_dir(
                    prodtools_dir or PRODTOOLS_CVMFS_CURRENT),
                user=user))
```

Directly after the `njobs` range check (the `raise ValueError(f"--once needs between 1 and ...")` block), still inside the `try`, insert:

```python
        if local:
            return _start_local(run_dir, entry, njobs, parallel=parallel,
                                launch=launch)
```

**(f) `_start_local`.** Add it directly after `submit_once`:

```python
LOCAL_SUMMARY = 'summary.json'
LOCAL_LOG = 'runlocal.log'


def _start_local(run_dir, entry, njobs, parallel=None, launch=None):
    """`--once --local`: start runlocal on the cnf built in `run_dir`,
    detached, and return the receipt `running` without waiting.

    Its own session (start_new_session): the run outlives this process,
    the MCP runner's shell and the MCP call; `kill <pid>` stops it, jobs
    included (runlocal's SIGTERM handler). No inherited pipes: the MCP
    runner captures stdout/stderr and waits for EOF, so a child holding
    either would block the tool for the whole run -- stdin is /dev/null
    and both outputs go to the log. The environment is inherited (ops +
    the entry's Musing or tarball); each job re-sources the setup itself
    with MUSE_WORK_DIR removed (runlocal.child_env).
    """
    import socket
    import subprocess
    from utils import code_cache, run_receipt
    from utils.jobdesc import code_of, inloc_of
    from utils.runlocal import DEFAULT_PARALLEL

    parallel = parallel or DEFAULT_PARALLEL
    launch = launch or subprocess.Popen
    run_receipt.update(run_dir, state='starting', executor='local',
                       entry=entry)
    summary = os.path.join(run_dir, LOCAL_SUMMARY)
    log = os.path.join(run_dir, LOCAL_LOG)
    argv = [sys.executable,
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'runlocal.py'),
            '--jobdef', os.path.join(run_dir, entry['tarball']),
            '--inloc', inloc_of(entry),
            '--first', '0', '--num', str(njobs),
            '--parallel', str(parallel),
            '--workdir', run_dir,
            '--json', summary]
    if code_of(entry):
        argv += ['--code-root', code_cache.unpacked(code_of(entry))]
    with open(log, 'w') as fh:
        proc = launch(argv, cwd=run_dir, stdin=subprocess.DEVNULL,
                      stdout=fh, stderr=subprocess.STDOUT,
                      start_new_session=True)
    return run_receipt.update(
        run_dir, state='running', host=socket.getfqdn(), pid=proc.pid,
        started_utc=run_receipt._now(), summary=summary, log=log,
        njobs=njobs, parallel=parallel)
```

**(g) Docs.** In `docs/EXAMPLES_schema.md:397`, replace the sentence `Build it and submit it by hand.` with:

```markdown
Submit it with `json2jobdef --once` (every job in one jobsub_submit, a
  receipt, nothing in SAM), or run it on this node with `json2jobdef
  --once --local` (runlocal started detached, the same receipt);
  `run_status` on the read-only MCP server reports on either.
```

In `EXAMPLES.md`, replace `Build it and submit it by hand — or run it on this node with` and the line after it, ``` `runlocal` (section 11), which pushes nothing at all.```, with:

```markdown
Submit it with `json2jobdef --once` (every job in one jobsub_submit, a
receipt, nothing in SAM), or run it on this node with `json2jobdef
--once --local`, which starts `runlocal` (section 11) detached under the
same receipt. Neither pushes anything to SAM.
```

(Ruling: EXAMPLES.md is hand-edited in step with its schema rather than regenerated with `/refresh-examples`. A full regeneration rewrites the whole file, which is out of proportion for one flag in this PR.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestJson2jobdefOnceLocal test.test_unit.TestJson2jobdefOnce test.test_unit.TestRunReceipt -v 2>&1 | tail -3`
Expected: `OK`.

Run the full suite: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit 2>&1 | grep -E '^Ran|^OK|^FAILED'`
Expected: `Ran 1510 tests`, `OK (skipped=4)`.

- [ ] **Step 5: Commit**

```bash
git add utils/json2jobdef.py utils/run_receipt.py docs/EXAMPLES_schema.md EXAMPLES.md test/test_unit.py
git commit -m "feat(json2jobdef): --once --local runs the entry on this node, detached

Same refusals, build and receipt as --once; then runlocal starts in its
own session with no inherited pipes, and the receipt records host, pid,
summary and log.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM"
```

---

### Task 5: The `run_local` MCP write tool

**Files:**
- Modify: `mcp/src/prodtools_mcp_write/tools.py` (`submit_once` at :339-391; new `_receipt_from` and `run_local`)
- Modify: `mcp/src/prodtools_mcp_write/server.py:11-16` (`TOOL_FUNCTIONS`)
- Modify: `mcp/README.md` (the `prodtools-write` section), `CLAUDE.md` (the "A third write tool" paragraph)
- Test: `test/test_unit.py`, new `class TestRunLocalTool`

**Interfaces:**
- Consumes:
  - `_select_push_params(..., allow_code=True)` (Task 2);
  - CLI `json2jobdef ... --once --local --parallel N` (Task 4).
- Produces:
  - `tools.run_local(json: str, desc: str, dsconf: str, run_as: str, parallel: int = 4) -> dict`, the receipt without `entry` plus a `receipt` key holding its path;
  - `tools._receipt_from(result, what) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add above the final `__main__` block:

```python
class TestRunLocalTool(unittest.TestCase):
    """run_local: `json2jobdef --once --local` through the write server.
    Self only; returns as soon as runlocal has started."""

    def setUp(self):
        from prodtools_mcp_write import tools
        self.tools = tools
        self.tmp = _mkdtemp()
        self.simjob_setup = (
            '/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/C/setup.sh')
        self.json_path = os.path.join(self.tmp, 'entries.json')
        with open(self.json_path, 'w') as f:
            json.dump([{'desc': 'D', 'dsconf': 'C',
                        'simjob_setup': self.simjob_setup, 'fcl': 'x.fcl',
                        'outloc': {'*.art': 'outstage'}}], f)
        self.receipt = os.path.join(self.tmp, 'receipt.json')
        with open(self.receipt, 'w') as f:
            json.dump({'name': 'cnf.alice.D.C.0', 'state': 'running',
                       'executor': 'local', 'host': 'node.fnal.gov',
                       'pid': 4242, 'entry': {'big': 'thing'}}, f)

    def _call(self, cli, **kwargs):
        with patch('prodtools_mcp_write.runner.run_cli',
                   return_value=cli) as run:
            out = self.tools.run_local(self.json_path, 'D', 'C',
                                       kwargs.pop('run_as', 'self'), **kwargs)
        return out, run

    def _ok(self):
        return {'rc': 0, 'stderr': '',
                'stdout': f'noise\nRECEIPT {self.receipt}\n'}

    def test_runs_json2jobdef_once_local_and_returns_the_receipt(self):
        out, run = self._call(self._ok())
        self.assertEqual(run.call_args[0][0], [
            'bin/json2jobdef', '--json', self.json_path, '--desc', 'D',
            '--dsconf', 'C', '--once', '--local', '--parallel', '4'])
        self.assertEqual(run.call_args[0][1], 'self')
        self.assertEqual(run.call_args[1]['simjob_setup'], self.simjob_setup)
        self.assertEqual(out['state'], 'running')
        self.assertEqual(out['pid'], 4242)
        self.assertEqual(out['receipt'], self.receipt)
        self.assertNotIn('entry', out)

    def test_parallel_is_forwarded(self):
        _, run = self._call(self._ok(), parallel=2)
        self.assertEqual(run.call_args[0][0][-2:], ['--parallel', '2'])

    def test_mu2epro_is_refused(self):
        with patch('prodtools_mcp_write.runner.run_cli') as run:
            with self.assertRaises(ValueError) as ctx:
                self.tools.run_local(self.json_path, 'D', 'C', 'mu2epro')
        self.assertIn('self', str(ctx.exception))
        run.assert_not_called()

    def test_a_bad_parallel_is_refused(self):
        for bad in (0, -1, True, '2'):
            with patch('prodtools_mcp_write.runner.run_cli') as run:
                with self.assertRaises(ValueError, msg=repr(bad)):
                    self.tools.run_local(self.json_path, 'D', 'C', 'self',
                                         parallel=bad)
            run.assert_not_called()

    def test_a_code_entry_sources_the_unpacked_setup(self):
        from utils import code_cache
        root = os.path.join(self.tmp, 'cache')
        code = _make_code_tarball(os.path.join(self.tmp, 'Code.tar.bz2'))
        with open(self.json_path, 'w') as f:
            json.dump([{'desc': 'D', 'dsconf': 'C', 'code': code,
                        'fcl': 'x.fcl', 'outloc': {'*.art': 'outstage'}}], f)
        with patch.object(code_cache, 'cache_root', return_value=root):
            _, run = self._call(self._ok())
        with open(code, 'rb') as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(run.call_args[1]['simjob_setup'],
                         os.path.join(root, sha, 'Code', 'setup.sh'))

    def test_a_failure_reports_both_streams(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({'rc': 1, 'stdout': 'built nothing',
                        'stderr': 'a local run may still be going: look '
                                  'for a runlocal process'})
        self.assertIn('runlocal process', str(ctx.exception))
        self.assertIn('built nothing', str(ctx.exception))

    def test_success_without_a_receipt_line_is_an_error_not_a_guess(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({'rc': 0, 'stdout': 'all good', 'stderr': ''})
        self.assertIn('RECEIPT', str(ctx.exception))

    def test_it_is_registered(self):
        from prodtools_mcp_write import server
        self.assertIn('run_local', server.TOOL_NAMES)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestRunLocalTool -v 2>&1 | tail -10`
Expected: `AttributeError: module 'prodtools_mcp_write.tools' has no attribute 'run_local'` in every test, and `test_it_is_registered` fails.

- [ ] **Step 3: Write the implementation**

In `tools.py`, replace the tail of `submit_once`, which starts at `paths = [line.split(' ', 1)[1].strip()` and runs through `return receipt`, with:

```python
    return _receipt_from(result, 'json2jobdef --once')
```

Add these two functions directly after `submit_once`:

```python
def _receipt_from(result, what):
    """The receipt a `json2jobdef --once` run printed the path of, minus
    its entry (the caller has it), plus that path."""
    paths = [line.split(' ', 1)[1].strip()
             for line in (result.get('stdout') or '').splitlines()
             if line.startswith('RECEIPT ')]
    if len(paths) != 1:
        raise RuntimeError(
            f"{what} exited 0 but printed {len(paths)} RECEIPT lines, so "
            f"which run this was cannot be told: {_both_streams(result)}")
    with open(paths[0]) as fh:
        receipt = _json.load(fh)
    receipt.pop('entry', None)
    receipt['receipt'] = paths[0]
    return receipt


def run_local(json: str, desc: str, dsconf: str, run_as: str,
              parallel: int = 4):
    """Run one entry's jobs on THIS node with runlocal -- `json2jobdef
    --once --local`. Returns as soon as runlocal has started; the jobs
    keep running after the call.

    The same rules as submit_once: every `outloc` value must be
    "outstage", a desc+dsconf pair is used once per user (a local run and
    a grid run share the name), run_as="self" only. A code-tarball entry
    (`code`, no `simjob_setup`) runs against its tarball, unpacked once
    into /exp/mu2e/data/users/<you>/prodtools/code/<sha256>/.

    Outputs stay in the run directory,
    /exp/mu2e/data/users/<you>/prodtools/runs/<name>/job_NNNNNN/. Ask the
    read-only server's run_status(name=..., user=...) how it went.
    `kill <pid>` (the receipt's pid, on its host) stops the run, jobs
    included.

    Returns the receipt: `name`, `state` ("running"), `host`, `pid`,
    `njobs`, `parallel`, `summary`, `log`.
    """
    if run_as != 'self':
        raise ValueError(
            f"run_local is run_as=\"self\" only, got {run_as!r}: it runs "
            f"the jobs on this node, as you.")
    if (not isinstance(parallel, int) or isinstance(parallel, bool)
            or parallel < 1):
        raise ValueError(f"parallel must be an int >= 1, got {parallel!r}")
    simjob_setup, _ = _select_push_params(json, desc, dsconf,
                                          allow_code=True)
    argv = ['bin/json2jobdef', '--json', json, '--desc', desc,
            '--dsconf', dsconf, '--once', '--local',
            '--parallel', str(parallel)]
    result = runner.run_cli(argv, run_as, simjob_setup=simjob_setup)
    if result['rc'] != 0:
        raise RuntimeError(
            f"json2jobdef --once --local failed (rc={result['rc']}): "
            f"{_both_streams(result)}")
    return _receipt_from(result, 'json2jobdef --once --local')
```

In `server.py`, add this line to `TOOL_FUNCTIONS` after `'submit_once': tools.submit_once,`:

```python
    'run_local': tools.run_local,
```

- [ ] **Step 4: Document it**

In `mcp/README.md`:
- change the line `Exposes submission: \`push_cnf\`, \`run_submissions\`, \`submit_once\`, and` to `Exposes submission: \`push_cnf\`, \`run_submissions\`, \`submit_once\`, \`run_local\`, and`;
- append this to the paragraph Task 2 added, the one that starts "`submit_once(json, desc, dsconf, run_as)`":

```markdown

`run_local(json, desc, dsconf, run_as, parallel=4)` runs the same kind
of entry on this node instead (`json2jobdef --once --local`): it starts
`runlocal` detached and returns at once, with a receipt `run_status`
reads the same way. Outputs stay in
`/exp/mu2e/data/users/$USER/prodtools/runs/<name>/job_NNNNNN/`, and
`kill <pid>` (the receipt's `pid`, on its `host`) stops the run, jobs
included. Delete a code-cache directory only when no local run is still
using it.
```

In `CLAUDE.md`, append this to the same paragraph Task 2 extended:

```markdown
A fourth, `run_local`, runs such an entry on this node instead:
`runlocal` started detached under the same receipt; `kill <pid>` stops
it, jobs included.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestRunLocalTool test.test_unit.TestSubmitOnceTool test.test_unit.TestCodeEntryPushParams -v 2>&1 | tail -3`
Expected: `OK`.

Run the full suite: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit 2>&1 | grep -E '^Ran|^OK|^FAILED'`
Expected: `Ran 1518 tests`, `OK (skipped=4)`.

- [ ] **Step 6: Commit**

```bash
git add mcp/src/prodtools_mcp_write/tools.py mcp/src/prodtools_mcp_write/server.py mcp/README.md CLAUDE.md test/test_unit.py
git commit -m "feat(mcp-write): run_local runs an entry on this node, detached

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM"
```

---

### Task 6: `run_status` reports local runs

**Files:**
- Modify: `mcp/src/prodtools_mcp/tools/runs.py`:
  - module docstring and imports;
  - `run_status` at :53-121;
  - new `_fill_jobs`, `_local_status` and `_default_alive_fn`.
- Modify: `mcp/src/prodtools_mcp/server.py`: the instructions line `"How did my --once run go?"` (:38), and the `run_status` tool description (:250-256)
- Test: `test/test_unit.py`, new `class TestMcpRunStatusLocal`

**Interfaces:**
- Consumes: the local receipt keys from Task 4 (`executor`, `state`, `host`, `pid`, `summary`, `log`, `njobs`, `parallel`) and runlocal's `summary.json`, which has `jobs` (each with `index`, `rc` and `outputs`), `ok` and `failed`.
- Produces:
  - `run_status(name, mine=False, user=None, runs_root=None, clusters_fn=None, codes_fn=None, job_pars_fn=None, alive_fn=None, host_fn=None)`;
  - `_default_alive_fn(pid, summary_path) -> True | False | None`.

- [ ] **Step 1: Write the failing tests**

Add above the final `__main__` block:

```python
class TestMcpRunStatusLocal(unittest.TestCase):
    """run_status on a `--once --local` run: runlocal's own summary once
    it wrote one, else whether its process is still there. Never asks
    condor, never rewrites the receipt."""

    NAME = 'cnf.alice.CeEndpoint.T1.0'

    def setUp(self):
        from prodtools_mcp import runtime
        from prodtools_mcp.tools import runs
        from utils import run_receipt
        self.runs, self.rr = runs, run_receipt
        self.addCleanup(runtime.set_shared, False)
        self.root = _mkdtemp()
        self.dir = run_receipt.reserve(self.root, self.NAME,
                                       {'tarball': self.NAME + '.tar'})
        self.summary = os.path.join(self.dir, 'summary.json')
        self.log = os.path.join(self.dir, 'runlocal.log')

    def _running(self):
        self.rr.update(self.dir, state='running', executor='local',
                       host='node.fnal.gov', pid=4242, njobs=3, parallel=2,
                       summary=self.summary, log=self.log)

    def _output(self, index):
        return os.path.join(self.dir, f'job_{index:06d}',
                            f'dts.alice.CeEndpoint.T1.001430_{index:08d}.art')

    def _write_summary(self, rcs):
        jobs = [{'index': i, 'rc': rc, 'timed_out': rc == 124,
                 'seconds': 1.0,
                 'dir': os.path.join(self.dir, f'job_{i:06d}'),
                 'log': os.path.join(self.dir, f'job_{i:06d}', 'stdout.log'),
                 'outputs': [self._output(i)]}
                for i, rc in sorted(rcs.items())]
        with open(self.summary, 'w') as fh:
            json.dump({'jobdef': 'x', 'workdir': self.dir, 'jobs': jobs,
                       'ok': sum(1 for j in jobs if j['rc'] == 0),
                       'failed': [j['index'] for j in jobs if j['rc'] != 0]},
                      fh)

    def _status(self, alive=True, host='node.fnal.gov'):
        seen = {}

        def alive_fn(pid, path):
            seen['alive'] = (pid, path)
            return alive

        def condor(*args, **kwargs):
            raise AssertionError('a local run never asks condor')
        out = self.runs.run_status(
            self.NAME, user='alice', runs_root=self.root,
            clusters_fn=condor, codes_fn=condor,
            alive_fn=alive_fn, host_fn=lambda: host)
        return out, seen

    def test_a_summary_with_every_job_ok_is_done_with_the_output_paths(self):
        self._running()
        self._write_summary({0: 0, 1: 0, 2: 0})
        out, seen = self._status()
        self.assertEqual(out['state'], 'done')
        self.assertEqual(out['executor'], 'local')
        self.assertEqual(out['jobs'], {'expected': 3, 'ok': 3,
                                       'failed': [], 'unknown': []})
        self.assertEqual(out['outputs'][2], [self._output(2)])
        self.assertNotIn('queue', out)
        self.assertEqual(seen, {})

    def test_a_failed_or_timed_out_job_is_short_with_its_exit_code(self):
        self._running()
        self._write_summary({0: 0, 1: 124, 2: 3})
        out, _ = self._status()
        self.assertEqual(out['state'], 'short')
        self.assertEqual(out['jobs']['failed'], [1, 2])
        self.assertEqual(out['jobs']['exit_codes'], {1: 124, 2: 3})
        self.assertEqual(sorted(out['outputs']), [0])

    def test_an_index_missing_from_the_summary_is_unknown(self):
        self._running()
        self._write_summary({0: 0, 2: 0})
        out, _ = self._status()
        self.assertEqual(out['state'], 'unknown')
        self.assertEqual(out['jobs']['unknown'], [1])
        self.assertIn('summary', out['note'])

    def test_no_summary_and_a_live_process_is_running(self):
        self._running()
        out, seen = self._status(alive=True)
        self.assertEqual(out['state'], 'running')
        self.assertEqual(seen['alive'], (4242, self.summary))

    def test_no_summary_and_no_process_is_failed_pointing_at_the_log(self):
        self._running()
        out, _ = self._status(alive=False)
        self.assertEqual(out['state'], 'failed')
        self.assertIn('pid 4242', out['note'])
        self.assertIn(self.log, out['note'])

    def test_no_summary_from_another_host_is_unknown_naming_the_host(self):
        self._running()
        out, seen = self._status(host='other.fnal.gov')
        self.assertEqual(out['state'], 'unknown')
        self.assertIn('node.fnal.gov', out['note'])
        self.assertNotIn('alive', seen)

    def test_a_process_table_that_will_not_say_is_unknown(self):
        self._running()
        out, _ = self._status(alive=None)
        self.assertEqual(out['state'], 'unknown')
        self.assertIn('/proc/4242', out['note'])

    def test_a_starting_receipt_is_returned_as_it_is(self):
        self.rr.update(self.dir, state='starting', executor='local')
        out, seen = self._status()
        self.assertEqual(out['state'], 'starting')
        self.assertEqual(seen, {})

    def test_the_default_liveness_check_reads_the_command_line(self):
        """kill(pid, 0) would answer for any process that reused the pid;
        runlocal's command line names this run's summary path."""
        proc = subprocess.Popen(
            [sys.executable, '-c',
             'import time; print("ready", flush=True); time.sleep(60)',
             '--json', self.summary],
            stdout=subprocess.PIPE)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        proc.stdout.readline()                 # exec'd: cmdline is final
        self.assertTrue(self.runs._default_alive_fn(proc.pid, self.summary))
        self.assertFalse(self.runs._default_alive_fn(
            proc.pid, self.summary + '.other'))
        proc.kill()
        proc.wait()
        self.assertFalse(self.runs._default_alive_fn(proc.pid, self.summary))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestMcpRunStatusLocal -v 2>&1 | tail -12`
Expected:
- `TypeError: run_status() got an unexpected keyword argument 'alive_fn'`;
- `AttributeError: ... has no attribute '_default_alive_fn'`.

- [ ] **Step 3: Write the implementation**

In `mcp/src/prodtools_mcp/tools/runs.py`, replace the module docstring's first line, `"""run_status: how a one-shot outstage run (\`json2jobdef --once\`) went.`, with:

```python
"""run_status: how a one-shot run (`json2jobdef --once`, grid or --local) went.
```

Then append this paragraph to the end of the module docstring:

```python

A `--once --local` run (receipt `executor: "local"`) has no cluster: its
record is runlocal's own summary.json (written atomically, so a missing
file means runlocal never finished), and while there is none, whether
runlocal's process is still there -- asked of /proc, on its own host.
```

Add `import json` and `import socket` to the imports, next to `import os`.

In `run_status`, change the signature to:

```python
def run_status(name, mine=False, user=None, runs_root=None,
               clusters_fn=None, codes_fn=None, job_pars_fn=None,
               alive_fn=None, host_fn=None):
```

Replace the key tuple:

```python
           ('name', 'state', 'created_utc', 'submitted_utc', 'jobid',
            'cluster_id', 'njobs', 'outstage', 'prodtools_dir', 'error')
```

with:

```python
           ('name', 'state', 'created_utc', 'submitted_utc', 'jobid',
            'cluster_id', 'njobs', 'outstage', 'prodtools_dir', 'error',
            'executor', 'host', 'pid', 'started_utc', 'summary', 'log',
            'parallel')
```

Directly after `out['receipt'] = os.path.join(root, name, run_receipt.RECEIPT)`, insert:

```python
    if receipt.get('executor') == 'local':
        if receipt.get('state') != 'running':
            return out          # building / starting / failed
        return _local_status(out, receipt, alive_fn or _default_alive_fn,
                             host_fn or socket.getfqdn)
```

Replace everything in `run_status` from `failed, unknown = summary['failed'], summary['unknown']` through the line before `if unknown:` with:

```python
    failed, unknown = _fill_jobs(out, njobs, summary)
```

Then add these functions after `run_status`:

```python
def _fill_jobs(out, njobs, summary):
    """The `jobs` and `outputs` blocks, from a summary in the shape
    jobwait and runlocal share (`jobs` of {index, rc, outputs}, `ok`,
    `failed`) plus `unknown`. Returns (failed, unknown), uncapped."""
    failed, unknown = summary['failed'], summary['unknown']
    out['jobs'] = {'expected': njobs, 'ok': summary['ok'],
                   'failed': failed[:INDEX_CAP],
                   'unknown': unknown[:INDEX_CAP]}
    if failed:
        out['jobs']['exit_codes'] = {j['index']: j['rc']
                                     for j in summary['jobs']
                                     if j['index'] in failed[:INDEX_CAP]}
    if len(failed) > INDEX_CAP or len(unknown) > INDEX_CAP:
        out['jobs']['truncated'] = {'failed': len(failed),
                                    'unknown': len(unknown)}
    out['outputs'] = {j['index']: j['outputs'] for j in summary['jobs']
                      if j['rc'] == 0}
    if len(out['outputs']) > INDEX_CAP:
        keep = sorted(out['outputs'])[:INDEX_CAP]
        out['outputs'] = {i: out['outputs'][i] for i in keep}
        out['outputs_truncated'] = summary['ok']
    return failed, unknown


def _local_status(out, receipt, alive_fn, host_fn):
    """A `--once --local` run that was started: runlocal's summary once
    it wrote one, else whether its process is still there. The state is
    recomputed on every call; the receipt is never rewritten."""
    njobs, path = int(receipt['njobs']), receipt['summary']
    try:
        with open(path) as fh:
            summary = json.load(fh)
    except FileNotFoundError:
        summary = None
    if summary is None:
        pid, host = receipt['pid'], receipt.get('host')
        if host != host_fn():
            out['state'] = 'unknown'
            out['note'] = (f'the run is on {host} and has written no '
                           f'summary yet; only that host can say whether '
                           f'runlocal (pid {pid}) is still going.')
            return out
        alive = alive_fn(pid, path)
        if alive is None:
            out['state'] = 'unknown'
            out['note'] = (f'cannot read /proc/{pid} on this host, so '
                           f'whether runlocal is still going is unknown.')
        elif alive:
            out['state'] = 'running'
        else:
            out['state'] = 'failed'
            out['note'] = (f'runlocal (pid {pid}) is gone and wrote no '
                           f'summary; see {receipt.get("log")}')
        return out
    seen = {j['index'] for j in summary['jobs']}
    summary = dict(summary,
                   unknown=[i for i in range(njobs) if i not in seen])
    failed, unknown = _fill_jobs(out, njobs, summary)
    if unknown:
        out['state'] = 'unknown'
        out['note'] = (f"runlocal's summary has no record for "
                       f"{len(unknown)} of {njobs} jobs.")
    else:
        out['state'] = 'short' if failed else 'done'
    return out


def _default_alive_fn(pid, summary_path):
    """True when process `pid` is this run's runlocal, False when it is
    gone, None when this host will not say.

    Reads the command line rather than calling kill(pid, 0): pids are
    reused, and kill() needs the same user. runlocal's command line
    carries `--json <run dir>/summary.json`, which names exactly one run.
    """
    try:
        with open(f'/proc/{int(pid)}/cmdline', 'rb') as fh:
            argv = fh.read().split(b'\0')
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return os.fsencode(summary_path) in argv
```

In `mcp/src/prodtools_mcp/server.py`:
- change the instructions line `- "How did my --once run go?" -> run_status(...)` to read `- "How did my --once / run_local run go?" -> run_status(name="cnf.<login>....0", user="<login>")`;
- replace the whole `description=` string of the `run_status` tool with:

```python
    @mcp.tool(description='How a one-shot run went: json2jobdef --once / '
                          'submit_once on the grid, or --once --local / '
                          'run_local on one node. Its receipt; for a grid '
                          'run the live queue, and once the cluster left '
                          'the queue the per-job exit codes and the output '
                          'paths on outstage; for a local run runlocal\'s '
                          'own summary, or whether it is still running. '
                          'Such a run has no ledger row and nothing in '
                          'SAM, so no other tool sees it. name is the cnf '
                          'name without ".tar". Say whose run: '
                          'user="<login>", or mine=true on your own stdio '
                          'server. state="unknown" is never a failure and '
                          'never a success.')
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit.TestMcpRunStatusLocal test.test_unit.TestMcpRunStatus -v 2>&1 | tail -3`
Expected: `OK`. The existing grid tests in `TestMcpRunStatus` pass unchanged.

Run the full suite: `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit 2>&1 | grep -E '^Ran|^OK|^FAILED'`
Expected: `Ran 1527 tests`, `OK (skipped=4)`.

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp/tools/runs.py mcp/src/prodtools_mcp/server.py test/test_unit.py
git commit -m "feat(mcp): run_status reports --once --local runs

From runlocal's summary.json once it exists; before that, from
/proc/<pid>/cmdline on the run's own host. Never asks condor, never
rewrites the receipt.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MyWw6RZmPD7Ap3TtFRCdWM"
```

---

### Task 7: Live gate on mu2esrv01 (controller runs it, no subagent)

Nothing here is submitted to the grid. It needs a valid Kerberos ticket, which the `run_cli` chain turns into a token with `getToken`. The steps run as self from `$WT`, on branch `run-local`.

**Files:** none in the repo. Scratch goes in `LIVE=/exp/mu2e/data/users/oksuzian/prodtools_live_0925`.

- [ ] **Step 1: Make the two live entries**

These are copies of autoresearch's gridphaseA01 mubeam entry: a code entry with a 15 MB tarball, outloc outstage, and 1 job each.

```bash
mkdir -p /exp/mu2e/data/users/oksuzian/prodtools_live_0925/build
/usr/bin/python3 - <<'EOF'
import json
src = ('/exp/mu2e/data/users/oksuzian/gridtest/autoresearch_grid/'
       'gridphaseA01/state/mubeam_entry.json')
[e] = json.load(open(src))
out = [dict(e, dsconf='Run1Bak_live01', njobs=1, events=10),
       dict(e, dsconf='Run1Bak_live02', njobs=1, events=20000)]
json.dump(out, open('/exp/mu2e/data/users/oksuzian/prodtools_live_0925/'
                    'mubeam_live.json', 'w'), indent=1)
EOF
```

- [ ] **Step 2: Check the P1 build path (cnf only, nothing submitted)**

```bash
cd $WT && /usr/bin/python3 - <<'EOF'
import sys; sys.path[:0] = ['.', 'mcp/src']
from prodtools_mcp_write import tools, runner
J = '/exp/mu2e/data/users/oksuzian/prodtools_live_0925/mubeam_live.json'
D, C = 'Run1A_MuBeam_gridphaseA01', 'Run1Bak_live01'
setup, _ = tools._select_push_params(J, D, C, allow_code=True)
print('setup', setup)
r = runner.run_cli(['bin/json2jobdef', '--json', J, '--desc', D, '--dsconf', C],
                   'self', cwd='/exp/mu2e/data/users/oksuzian/prodtools_live_0925/build',
                   simjob_setup=setup)
print('rc', r['rc']); print(r['stdout'][-1500:]); print(r['stderr'][-1500:])
EOF
/usr/bin/python3 -c "import sys; sys.path.insert(0, '$WT'); from utils.jobquery import Mu2eJobPars; print(Mu2eJobPars('/exp/mu2e/data/users/oksuzian/prodtools_live_0925/build/cnf.oksuzian.Run1A_MuBeam_gridphaseA01.Run1Bak_live01.0.tar').setup())"
```

Pass:
- `setup` is `/exp/mu2e/data/users/oksuzian/prodtools/code/<sha256>/Code/setup.sh`;
- `rc 0`;
- the cnf exists;
- the last command prints `Code/setup.sh`.

- [ ] **Step 3: Start the 10-event run**

```bash
cd $WT && /usr/bin/python3 - <<'EOF'
import sys, json; sys.path[:0] = ['.', 'mcp/src']
from prodtools_mcp_write import tools
r = tools.run_local('/exp/mu2e/data/users/oksuzian/prodtools_live_0925/mubeam_live.json',
                    'Run1A_MuBeam_gridphaseA01', 'Run1Bak_live01', 'self', parallel=1)
print(json.dumps(r, indent=1))
EOF
```

Pass: the call returns in about a minute, with `state` `running`, `executor` `local`, `host` `mu2esrv01.fnal.gov`, and a `pid`.

- [ ] **Step 4: Follow it to `done`**

Poll every 30 s with a Monitor until-loop running this snippet, until it exits 0:

```bash
cd $WT && /usr/bin/python3 - <<'EOF'
import sys; sys.path[:0] = ['.', 'mcp/src']
from prodtools_mcp.tools import runs
out = runs.run_status('cnf.oksuzian.Run1A_MuBeam_gridphaseA01.Run1Bak_live01.0', user='oksuzian')
print(out['state'], out.get('jobs'), out.get('outputs'), out.get('note'))
sys.exit(0 if out['state'] != 'running' else 1)
EOF
```

Pass:
- the state is `done`, with `jobs` equal to `{'expected': 1, 'ok': 1, 'failed': [], 'unknown': []}`;
- every path in `outputs[0]` exists (`ls -la` them).

If the state is `short` or `failed`, read `runlocal.log` and `job_000000/stdout.log` in the run directory, and stop. Do not retry blind.

- [ ] **Step 5: Stop a longer run and check nothing is orphaned**

Start `Run1Bak_live02` the same way as step 3. Wait until `run_status` reads `running` and the job's `mu2e` has started. Check that with this listing, which must show at least one process with `mu2e` in its command line:

```bash
for p in $(pgrep -u $USER); do d=$(readlink /proc/$p/cwd 2>/dev/null); case $d in *Run1Bak_live02*) echo "$p $(tr '\0' ' ' </proc/$p/cmdline | cut -c1-120)";; esac; done
```

Then run `kill <pid>`, with the pid from the receipt, and wait 60 s.

Pass:
- the same listing prints nothing;
- `run_status` reads `failed`, and its note names `pid <pid>` and `runlocal.log`;
- `runlocal.log` holds no summary line.

- [ ] **Step 6: Record the result**

Write the outcomes of steps 2–5, with any deviation, into the SDD ledger. For the PR bodies, record the run names and state lines. In autoresearch, update `wiki/drivers/contract-engine.md` Open questions (the P1 part-1 bullet) with "P1 + P2 implemented on prodtools branches `code-entries-run-local` / `run-local`; live gate passed <date>", and add a `wiki/log.md` bullet.

---

### Task 8: Open the PRs (controller only, after the user's go-ahead)

Stop and ask the user before any push. Only after a yes:

- [ ] **PR 1:**
  - push `code-entries-run-local` to `origin` (oksuzian/prodtools). SSH from the Bash tool may fail (autoresearch wiki `incidents/claude-bash-no-ssh-agent.md`); if so, ask the user to run the push with `! git -C $WT push -u origin code-entries-run-local`.
  - `gh pr create --repo Mu2e/prodtools --base main --head oksuzian:code-entries-run-local`. The body summarises Tasks 1–2, the test-harness fix, and the Task 7 step 2 result.
- [ ] **PR 2:** after PR 1 merges, rebase `run-local` onto `mu2e/main`, rerun the full suite, push, and open it the same way. The body summarises Tasks 3–6 and the Task 7 steps 3–5 results.
