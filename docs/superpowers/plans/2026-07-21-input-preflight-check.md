# Input Pre-flight Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify a campaign's input files are readable before jobs launch — refuse the campaign (exit 2) when a resilient pileup file is missing/truncated or a tape input is not staged.

**Architecture:** A pure logic module `utils/check_inputs.py` reads the frozen input file list from the cnf tarball, splits it into primary (`tbs.inputs`) and pileup (`tbs.auxin`), and verifies each group at its real read location: resilient pileup by direct `os.path.getsize` vs the SAM-recorded size (catches missing + truncated), tape/persistent inputs by `mdh query-dcache` locality (catches NEARLINE eviction). All I/O goes through injected seams so the logic is unit-tested with fakes. A `bin/check_inputs` CLI and a gate in `submit_map --enqueue` are the two entry points.

**Tech Stack:** Python 3.9 stdlib only (dataclasses, subprocess, os, tarfile); `mdh` and `samweb` via existing `utils/` wrappers; `unittest` with in-memory tarballs.

## Global Constraints

- **Read-only, block-only.** The check NEVER re-copies files or triggers prestaging. It returns problems; callers exit 2. Prestaging lives only in the `/prestage` skill.
- **Exit 2 = needs-attention** (matches the existing queue-count guard). The enqueue gate additionally writes NO campaign row when the check fails.
- **Fail closed.** Any SAM/mdh/stat failure or unparseable result becomes a `query_error` problem — never assumed OK.
- **Four problem kinds only:** `truncated`, `missing`, `nearline`, `query_error`.
- **Reuse, don't reimplement path logic.** Use `utils.file_resolver.resilient_path` and `utils.file_resolver.infer_dataset_location`; do not hand-build /pnfs paths. (Independent path logic drifting from the runner's is the class of bug that caused the 2026-07-21 log-to-tape incident.)
- **mdh is blind to resilient.** `mdh query-dcache` only knows tape/disk/scratch (`/pnfs/mu2e/tape`, `/pnfs/mu2e/persistent`, `/pnfs/mu2e/scratch`), never `/pnfs/mu2e/resilient`. Resilient files MUST be checked by direct `os.path.getsize` (resilient is a flat path, POSIX-statable on interactive nodes); tape/persistent files MUST be checked by mdh (they use hash subdirs mdh computes internally).
- **Scope:** `inloc` in practice is `resilient` for mixing pileup and the primary reads from its SAM home (dcache/enstore). Pileup-on-stash is out of scope (the mixing rule mandates resilient). Persistent-primary truncation is out of scope (archive files are integrity-checked upstream; the observed failure and active-staging risk are on resilient).
- **Injected seams for I/O.** `check_inputs` and the two check functions take their SAM/mdh/stat callables as keyword args defaulting to the real implementations, so tests pass fakes (the `verify_row(sam_lister=...)` pattern already in the codebase).
- **Commit footers on every commit:**
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
  ```
- **Test suite baseline:** 474 tests pass today (`python3 -m unittest test.test_unit`). Every task adds tests and the suite must stay green.

## File Structure

- **`utils/check_inputs.py`** (new) — all check logic: `Problem`, `split_inputs`, `check_resilient`, `check_tape`, `check_inputs`, the default I/O helpers, and `main()` for the CLI.
- **`utils/samweb_wrapper.py`** (modify) — add `file_sizes_in_dataset(dataset) -> dict[str,int]` (batch size lookup, one query per dataset).
- **`bin/check_inputs`** (new) — bash wrapper sourcing the Mu2e env, exec `python3 utils/check_inputs.py` (mirrors `bin/submissions`).
- **`utils/submit.py`** (modify) — call the gate in `_enqueue_entries` before `create_campaign`.
- **`test/test_unit.py`** (modify) — tests for every unit.
- **`docs/EXAMPLES_schema.md`** (modify) + `EXAMPLES.md` (regen) + **`wiki/pages/`** — docs.

---

### Task 1: Tarball input extraction (`Problem`, `split_inputs`)

**Files:**
- Create: `utils/check_inputs.py`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `utils.jobquery.Mu2eJobPars` (`.json_data` dict), `utils.job_common.Mu2eName` (`.parse(fname).with_extension('art').dataset`).
- Produces:
  - `Problem` — frozen dataclass `(dataset: str, filename: str, kind: str, detail: str)`.
  - `split_inputs(tarball_path: str) -> tuple[dict[str,list[str]], dict[str,list[str]]]` returning `(primary_by_ds, auxin_by_ds)`: distinct input filenames grouped by dataset name, read from `tbs['inputs']` and `tbs['auxin']` respectively. Order-preserving, deduplicated.

- [ ] **Step 1: Write the failing test**

Add to `test/test_unit.py` (near the other Mu2eJobPars tests; uses the existing `_make_tarball` helper):

```python
class TestSplitInputs(unittest.TestCase):
    """split_inputs reads the frozen input file lists from a cnf tarball,
    splitting primary (tbs.inputs) from pileup (tbs.auxin), grouped by
    dataset and deduplicated — no per-index reconstruction."""

    def _tar(self, inputs=None, auxin=None):
        jp = {
            "code": "", "setup": "/cvmfs/x/setup.sh",
            "tbs": {"seed": "services.SeedService.baseSeed"},
            "jobname": "cnf.mu2e.TestDesc.TestConf.0.tar",
            "owner": "mu2e", "dsconf": "TestConf",
        }
        if inputs is not None:
            jp["tbs"]["inputs"] = inputs
        if auxin is not None:
            jp["tbs"]["auxin"] = auxin
        return _make_tarball(jp)

    def test_splits_primary_and_pileup_by_dataset(self):
        from utils.check_inputs import split_inputs
        tar = self._tar(
            inputs={"source.fileNames": [1, [
                "dts.mu2e.Prim.CampA.001430_00000000.art",
                "dts.mu2e.Prim.CampA.001430_00000001.art"]]},
            auxin={"physics.filters.M.fileNames": [1, [
                "dts.mu2e.Pile.CampB.001430_00000005.art"]]},
        )
        primary, auxin = split_inputs(tar)
        os.unlink(tar)
        self.assertEqual(set(primary), {"dts.mu2e.Prim.CampA.art"})
        self.assertEqual(len(primary["dts.mu2e.Prim.CampA.art"]), 2)
        self.assertEqual(set(auxin), {"dts.mu2e.Pile.CampB.art"})

    def test_dedups_repeated_files(self):
        from utils.check_inputs import split_inputs
        f = "dts.mu2e.Pile.CampB.001430_00000005.art"
        tar = self._tar(auxin={
            "physics.filters.A.fileNames": [1, [f]],
            "physics.filters.B.fileNames": [1, [f]],
        })
        _, auxin = split_inputs(tar)
        os.unlink(tar)
        self.assertEqual(auxin["dts.mu2e.Pile.CampB.art"], [f])

    def test_missing_sections_yield_empty(self):
        from utils.check_inputs import split_inputs
        tar = self._tar()
        primary, auxin = split_inputs(tar)
        os.unlink(tar)
        self.assertEqual(primary, {})
        self.assertEqual(auxin, {})

    def test_problem_is_frozen(self):
        from utils.check_inputs import Problem
        p = Problem("ds", "f.art", "truncated", "detail")
        self.assertEqual(p.kind, "truncated")
        with self.assertRaises(Exception):
            p.kind = "missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestSplitInputs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.check_inputs'`.

- [ ] **Step 3: Write minimal implementation**

Create `utils/check_inputs.py`:

```python
"""Pre-flight verification of a campaign's input files.

See docs/superpowers/specs/2026-07-21-input-preflight-check-design.md.
Read-only: reports problems, never remediates. Blocks (exit 2) when any
input is unreadable so a slice of jobs is not launched to die in bulk.
"""
import os
from dataclasses import dataclass

from utils.jobquery import Mu2eJobPars
from utils.job_common import Mu2eName


@dataclass(frozen=True)
class Problem:
    dataset: str
    filename: str
    kind: str      # 'truncated' | 'missing' | 'nearline' | 'query_error'
    detail: str


def _section_files(tbs, section):
    """Flatten the file lists of one tbs section (inputs/auxin).

    Each entry is `[merge_factor, [file, ...]]`; the file list is value[1].
    """
    files = []
    for value in tbs.get(section, {}).values():
        if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], list):
            files.extend(value[1])
    return files


def _group_by_dataset(files):
    """Group filenames by their dataset (tier.owner.desc.dsconf.art),
    order-preserving and deduplicated."""
    out = {}
    for f in dict.fromkeys(files):          # dedup, preserve order
        ds = str(Mu2eName.parse(f).with_extension('art').dataset)
        out.setdefault(ds, []).append(f)
    return out


def split_inputs(tarball_path):
    """(primary_by_ds, auxin_by_ds): distinct input files grouped by
    dataset, from the tarball's tbs.inputs (primary) and tbs.auxin
    (pileup). Frozen in the tarball — no per-index reconstruction."""
    jp = Mu2eJobPars(tarball_path)
    tbs = jp.json_data.get('tbs', {})
    return (_group_by_dataset(_section_files(tbs, 'inputs')),
            _group_by_dataset(_section_files(tbs, 'auxin')))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestSplitInputs -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/check_inputs.py test/test_unit.py
git commit -m "feat: input pre-flight — tarball input extraction (split_inputs)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 2: Resilient disk check + SAM size helper

**Files:**
- Modify: `utils/samweb_wrapper.py`
- Modify: `utils/check_inputs.py`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `Problem` (Task 1); `utils.file_resolver.resilient_path(filename) -> str`.
- Produces:
  - `utils.samweb_wrapper.file_sizes_in_dataset(dataset: str) -> dict[str,int]` — `{filename: file_size}` via one `list-files --fileinfo`.
  - `utils.check_inputs._default_disk_size(pnfs_path: str) -> int | None` — `os.path.getsize`, `None` if absent.
  - `utils.check_inputs.check_resilient(dataset, files, sam_sizes, disk_size) -> list[Problem]` where `sam_sizes: Callable[[str], dict[str,int]]` and `disk_size: Callable[[str], int | None]`. Emits `missing` (absent from resilient), `truncated` (size ≠ SAM), `query_error` (no SAM size for a file).

- [ ] **Step 1: Write the failing test**

Add to `test/test_unit.py`:

```python
class TestCheckResilient(unittest.TestCase):
    """Resilient pileup: present AND size matches SAM. Catches the
    2026-07-21 truncation (1 MiB stub) and a purge (missing entirely).
    mdh cannot see resilient, so this is a direct os.path.getsize vs the
    SAM-recorded size."""

    DS = "dts.mu2e.Pile.CampB.art"
    F1 = "dts.mu2e.Pile.CampB.001430_00000000.art"
    F2 = "dts.mu2e.Pile.CampB.001430_00000001.art"

    def test_all_present_and_sized_ok(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1, self.F2],
            sam_sizes=lambda ds: {self.F1: 100, self.F2: 200},
            disk_size=lambda p: 100 if self.F1 in p else 200)
        self.assertEqual(probs, [])

    def test_truncated_file_flagged(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1],
            sam_sizes=lambda ds: {self.F1: 113643009},
            disk_size=lambda p: 1048576)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "truncated")
        self.assertEqual(probs[0].filename, self.F1)

    def test_missing_file_flagged(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1],
            sam_sizes=lambda ds: {self.F1: 100},
            disk_size=lambda p: None)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "missing")

    def test_no_sam_size_is_query_error(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1],
            sam_sizes=lambda ds: {},
            disk_size=lambda p: 100)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "query_error")

    def test_default_disk_size_absent_is_none(self):
        from utils.check_inputs import _default_disk_size
        self.assertIsNone(_default_disk_size("/pnfs/mu2e/resilient/nope/x.art"))


class TestFileSizesInDataset(unittest.TestCase):
    """file_sizes_in_dataset returns {filename: size} from one
    list-files --fileinfo call."""

    def test_maps_name_to_size(self):
        import collections
        from utils import samweb_wrapper
        FI = collections.namedtuple("fileinfo",
                                    "file_name file_id file_size event_count")
        fake_client = MagicMock()
        fake_client.listFiles.return_value = [
            FI("dts.mu2e.Pile.CampB.001430_00000000.art", 1, 111, 9),
            FI("dts.mu2e.Pile.CampB.001430_00000001.art", 2, 222, 9),
        ]
        wrapper = MagicMock()
        wrapper.client = fake_client
        with patch.object(samweb_wrapper, "get_samweb_wrapper",
                          return_value=wrapper):
            out = samweb_wrapper.file_sizes_in_dataset("dts.mu2e.Pile.CampB.art")
        self.assertEqual(out, {
            "dts.mu2e.Pile.CampB.001430_00000000.art": 111,
            "dts.mu2e.Pile.CampB.001430_00000001.art": 222})
        # one query, fileinfo requested
        _, kwargs = fake_client.listFiles.call_args
        self.assertTrue(kwargs.get("fileinfo"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckResilient test.test_unit.TestFileSizesInDataset -v`
Expected: FAIL — `ImportError: cannot import name 'check_resilient'` / `file_sizes_in_dataset`.

- [ ] **Step 3a: Add the SAM helper**

In `utils/samweb_wrapper.py`, add a method to `SAMWebWrapper` (next to `get_metadata`):

```python
    def file_sizes_in_dataset(self, dataset: str) -> Dict[str, int]:
        """{filename: file_size} for a dataset via one list-files
        --fileinfo. Used by the input pre-flight check to get expected
        sizes without one get-metadata call per file."""
        q = q_dataset(dataset)
        return {fi.file_name: fi.file_size
                for fi in self.client.listFiles(dimensions=q, fileinfo=True)}
```

and a module-level passthrough (next to `get_metadata` at module scope):

```python
def file_sizes_in_dataset(dataset: str) -> Dict[str, int]:
    """{filename: file_size} for a dataset (one list-files --fileinfo)."""
    return get_samweb_wrapper().file_sizes_in_dataset(dataset)
```

(`q_dataset` and `Dict` are already imported/defined in this module.)

- [ ] **Step 3b: Add the resilient check**

Append to `utils/check_inputs.py` (add `from utils.file_resolver import resilient_path` to the imports):

```python
def _default_disk_size(pnfs_path):
    """Actual size of a resilient/disk file, or None if absent. Resilient
    is a flat /pnfs path, POSIX-statable on interactive nodes; stat does
    not trigger a tape recall."""
    try:
        return os.path.getsize(pnfs_path)
    except OSError:
        return None


def check_resilient(dataset, files, sam_sizes, disk_size):
    """Verify pileup files staged to resilient: each present AND its size
    equals the SAM-recorded size. Returns a list of Problems."""
    expected = sam_sizes(dataset)          # {filename: int}
    problems = []
    for f in files:
        path = resilient_path(f)
        actual = disk_size(path)
        if actual is None:
            problems.append(Problem(dataset, f, 'missing',
                                    f'absent from resilient: {path}'))
        elif f not in expected:
            problems.append(Problem(dataset, f, 'query_error',
                                    f'no SAM size for {f}'))
        elif actual != expected[f]:
            problems.append(Problem(dataset, f, 'truncated',
                                    f'{actual} bytes on disk, SAM expects '
                                    f'{expected[f]}'))
    return problems
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckResilient test.test_unit.TestFileSizesInDataset -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/check_inputs.py utils/samweb_wrapper.py test/test_unit.py
git commit -m "feat: input pre-flight — resilient size check + SAM size helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 3: Tape locality check

**Files:**
- Modify: `utils/check_inputs.py`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `Problem` (Task 1); `utils.file_resolver.infer_dataset_location(dataset) -> str` (returns `'enstore'` for tape, `'dcache'` for disk, `'N/A'` otherwise).
- Produces:
  - `utils.check_inputs._default_locality(mdh_loc: str, filenames: list[str]) -> dict[str,str]` — runs `mdh query-dcache -o -l <mdh_loc> <files...>`; values in `ONLINE` / `ONLINE_AND_NEARLINE` / `NEARLINE` / `MISSING` / `ERROR`. Fails closed (all `ERROR`) on any parse mismatch.
  - `utils.check_inputs.check_tape(dataset, files, locality, dataset_location) -> list[Problem]` where `locality: Callable[[str, list[str]], dict[str,str]]` and `dataset_location: Callable[[str], str]`. Emits `nearline` (NEARLINE), `missing` (MISSING), `query_error` (ERROR or unknown storage location).

- [ ] **Step 1: Write the failing test**

Add to `test/test_unit.py`:

```python
class TestCheckTape(unittest.TestCase):
    """Primary / tape inputs: NEARLINE (evicted) must block with a
    /prestage hint; ONLINE passes; unknown storage or query failure fails
    closed."""

    DS = "dts.mu2e.Prim.CampA.art"
    F1 = "dts.mu2e.Prim.CampA.001430_00000000.art"
    F2 = "dts.mu2e.Prim.CampA.001430_00000001.art"

    def test_online_passes(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1, self.F2],
            locality=lambda loc, fs: {self.F1: "ONLINE",
                                      self.F2: "ONLINE_AND_NEARLINE"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(probs, [])

    def test_nearline_blocks_with_prestage_hint(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "NEARLINE"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "nearline")
        self.assertIn("/prestage", probs[0].detail)

    def test_disk_dataset_queries_disk_location(self):
        from utils.check_inputs import check_tape
        seen = {}
        def loc(mdh_loc, fs):
            seen["loc"] = mdh_loc
            return {self.F1: "ONLINE"}
        probs = check_tape(self.DS, [self.F1], locality=loc,
                           dataset_location=lambda ds: "dcache")
        self.assertEqual(probs, [])
        self.assertEqual(seen["loc"], "disk")

    def test_enstore_dataset_queries_tape_location(self):
        from utils.check_inputs import check_tape
        seen = {}
        def loc(mdh_loc, fs):
            seen["loc"] = mdh_loc
            return {self.F1: "ONLINE"}
        check_tape(self.DS, [self.F1], locality=loc,
                   dataset_location=lambda ds: "enstore")
        self.assertEqual(seen["loc"], "tape")

    def test_missing_reported(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "MISSING"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(probs[0].kind, "missing")

    def test_unknown_storage_location_fails_closed(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "ONLINE"},
            dataset_location=lambda ds: "N/A")
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "query_error")

    def test_locality_error_fails_closed(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "ERROR"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(probs[0].kind, "query_error")


class TestDefaultLocalityParsing(unittest.TestCase):
    """_default_locality parses `mdh query-dcache -o`: stdout carries one
    locality token per FOUND file in input order; stderr carries an
    'Error: File not found in dCache: <path>' line per missing file. A
    count mismatch fails closed."""

    F1 = "dts.mu2e.Prim.CampA.001430_00000000.art"
    F2 = "dts.mu2e.Prim.CampA.001430_00000001.art"

    def _run(self, stdout, stderr, rc=0):
        from utils.check_inputs import _default_locality
        completed = MagicMock(stdout=stdout, stderr=stderr, returncode=rc)
        with patch("utils.check_inputs.subprocess.run", return_value=completed):
            return _default_locality("tape", [self.F1, self.F2])

    def test_all_found(self):
        out = self._run("ONLINE \nNEARLINE \n", "")
        self.assertEqual(out, {self.F1: "ONLINE", self.F2: "NEARLINE"})

    def test_one_missing_reconciled_by_path(self):
        out = self._run(
            "ONLINE \n",
            f"Error: File not found in dCache: /pnfs/mu2e/tape/x/{self.F2}\n")
        self.assertEqual(out, {self.F1: "ONLINE", self.F2: "MISSING"})

    def test_count_mismatch_fails_closed(self):
        # two found files claimed but only one status line, no missing
        out = self._run("ONLINE \n", "")
        self.assertEqual(out, {self.F1: "ERROR", self.F2: "ERROR"})

    def test_subprocess_failure_fails_closed(self):
        from utils.check_inputs import _default_locality
        with patch("utils.check_inputs.subprocess.run",
                   side_effect=OSError("mdh not found")):
            out = _default_locality("tape", [self.F1])
        self.assertEqual(out, {self.F1: "ERROR"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckTape test.test_unit.TestDefaultLocalityParsing -v`
Expected: FAIL — `ImportError: cannot import name 'check_tape'`.

- [ ] **Step 3: Write minimal implementation**

Append to `utils/check_inputs.py` (add `import subprocess` to the imports and `from utils.file_resolver import resilient_path, infer_dataset_location` — extend the existing file_resolver import):

```python
_LOC_TO_MDH = {'enstore': 'tape', 'dcache': 'disk'}
_LOCALITY_TOKENS = ('ONLINE', 'NEARLINE', 'ONLINE_AND_NEARLINE')


def _default_locality(mdh_loc, filenames):
    """{filename: state} via `mdh query-dcache -o -l <mdh_loc>`.

    stdout: one locality token per FOUND file, in input order.
    stderr: 'Error: File not found in dCache: <path>' per missing file.
    Reconcile missing files by basename, map the rest positionally, and
    fail closed (all ERROR) on any count mismatch or subprocess failure.
    """
    filenames = list(filenames)
    cmd = ['mdh', 'query-dcache', '-o', '-l', mdh_loc] + filenames
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (subprocess.SubprocessError, OSError):
        return {f: 'ERROR' for f in filenames}

    missing = set()
    for line in proc.stderr.splitlines():
        if 'File not found in dCache' in line:
            missing.add(os.path.basename(line.split('dCache:')[-1].strip()))

    states = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    found = [f for f in filenames if f not in missing]
    if len(states) != len(found):
        return {f: 'ERROR' for f in filenames}   # fail closed

    result = {f: 'MISSING' for f in missing}
    for f, s in zip(found, states):
        result[f] = s if s in _LOCALITY_TOKENS else 'ERROR'
    return result


def check_tape(dataset, files, locality, dataset_location):
    """Verify tape/persistent inputs are readable without a tape recall.
    NEARLINE (evicted) → block with a /prestage hint. Returns Problems."""
    loc = dataset_location(dataset)
    mdh_loc = _LOC_TO_MDH.get(loc)
    if mdh_loc is None:
        return [Problem(dataset, f, 'query_error',
                        f'unknown storage location {loc!r} for {dataset}')
                for f in files]
    states = locality(mdh_loc, files)
    problems = []
    for f in files:
        st = states.get(f, 'ERROR')
        if st in ('ONLINE', 'ONLINE_AND_NEARLINE'):
            continue
        if st == 'NEARLINE':
            problems.append(Problem(dataset, f, 'nearline',
                                    f'not staged (NEARLINE); run '
                                    f'/prestage {dataset}'))
        elif st == 'MISSING':
            problems.append(Problem(dataset, f, 'missing',
                                    f'absent from dCache {mdh_loc}'))
        else:
            problems.append(Problem(dataset, f, 'query_error',
                                    f'locality query failed for {f}'))
    return problems
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckTape test.test_unit.TestDefaultLocalityParsing -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/check_inputs.py test/test_unit.py
git commit -m "feat: input pre-flight — tape locality check (mdh)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 4: Assemble `check_inputs`

**Files:**
- Modify: `utils/check_inputs.py`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `split_inputs` (Task 1), `check_resilient` + `_default_disk_size` + `file_sizes_in_dataset` (Task 2), `check_tape` + `_default_locality` + `infer_dataset_location` (Task 3).
- Produces:
  - `check_inputs(tarball_path, inloc, *, sam_sizes=file_sizes_in_dataset, disk_size=_default_disk_size, locality=_default_locality, dataset_location=infer_dataset_location) -> tuple[bool, list[Problem]]`. Routing: pileup (`auxin`) with `inloc == 'resilient'` → `check_resilient`; pileup with any other inloc → `check_tape`; primary (`inputs`) → always `check_tape`. Returns `(ok, problems)` where `ok = not problems`.

- [ ] **Step 1: Write the failing test**

Add to `test/test_unit.py` (reuses the `TestSplitInputs._tar` shape inline):

```python
class TestCheckInputs(unittest.TestCase):
    """check_inputs assembles split_inputs + the two checks with the
    inloc routing, returning (ok, problems)."""

    def _tar(self, inputs=None, auxin=None):
        jp = {"code": "", "setup": "/cvmfs/x/setup.sh",
              "tbs": {"seed": "s"}, "jobname": "cnf.mu2e.T.C.0.tar",
              "owner": "mu2e", "dsconf": "C"}
        if inputs is not None:
            jp["tbs"]["inputs"] = inputs
        if auxin is not None:
            jp["tbs"]["auxin"] = auxin
        return _make_tarball(jp)

    PRIM = "dts.mu2e.Prim.CampA.001430_00000000.art"
    PILE = "dts.mu2e.Pile.CampB.001430_00000005.art"

    def _tar_both(self):
        return self._tar(
            inputs={"source.fileNames": [1, [self.PRIM]]},
            auxin={"physics.filters.M.fileNames": [1, [self.PILE]]})

    def test_all_clean(self):
        from utils.check_inputs import check_inputs
        tar = self._tar_both()
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: 100,
            locality=lambda loc, fs: {f: "ONLINE" for f in fs},
            dataset_location=lambda ds: "dcache")
        os.unlink(tar)
        self.assertTrue(ok)
        self.assertEqual(probs, [])

    def test_resilient_pileup_checked_by_size_not_mdh(self):
        from utils.check_inputs import check_inputs
        tar = self._tar_both()
        called = {"mdh": []}
        def loc(mdh_loc, fs):
            called["mdh"].extend(fs)
            return {f: "ONLINE" for f in fs}
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: 1048576,      # truncated pileup
            locality=loc, dataset_location=lambda ds: "dcache")
        os.unlink(tar)
        self.assertFalse(ok)
        self.assertEqual([p.kind for p in probs], ["truncated"])
        # pileup went through the resilient size path, never mdh
        self.assertNotIn(self.PILE, called["mdh"])

    def test_nearline_primary_blocks(self):
        from utils.check_inputs import check_inputs
        tar = self._tar_both()
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: 100,
            locality=lambda loc, fs: {f: "NEARLINE" for f in fs},
            dataset_location=lambda ds: "enstore")
        os.unlink(tar)
        self.assertFalse(ok)
        self.assertEqual([p.kind for p in probs], ["nearline"])

    def test_missing_resilient_not_reclassified_as_tape(self):
        # The flagged subtlety: a pileup file absent from resilient must
        # be reported 'missing', NOT quietly checked as a tape input.
        from utils.check_inputs import check_inputs
        tar = self._tar(auxin={"physics.filters.M.fileNames":
                               [1, [self.PILE]]})
        def loc(mdh_loc, fs):
            raise AssertionError("pileup must not reach the tape path")
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: None,          # purged from resilient
            locality=loc, dataset_location=lambda ds: "enstore")
        os.unlink(tar)
        self.assertFalse(ok)
        self.assertEqual([p.kind for p in probs], ["missing"])

    def test_non_resilient_inloc_routes_pileup_to_tape(self):
        from utils.check_inputs import check_inputs
        tar = self._tar(auxin={"physics.filters.M.fileNames":
                               [1, [self.PILE]]})
        ok, probs = check_inputs(
            tar, "tape",
            sam_sizes=lambda ds: {},
            disk_size=lambda p: None,
            locality=lambda loc, fs: {f: "ONLINE" for f in fs},
            dataset_location=lambda ds: "enstore")
        os.unlink(tar)
        self.assertTrue(ok)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckInputs -v`
Expected: FAIL — `ImportError: cannot import name 'check_inputs'`.

- [ ] **Step 3: Write minimal implementation**

Append to `utils/check_inputs.py` (extend the samweb import to include the size helper: `from utils.samweb_wrapper import file_sizes_in_dataset`):

```python
def check_inputs(tarball_path, inloc, *,
                 sam_sizes=file_sizes_in_dataset,
                 disk_size=_default_disk_size,
                 locality=_default_locality,
                 dataset_location=infer_dataset_location):
    """Verify a campaign's inputs are readable. Returns (ok, problems).

    Pileup (tbs.auxin) staged to resilient is checked by direct size vs
    SAM (mdh cannot see resilient); everything else — the primary, and
    pileup under a non-resilient inloc — is checked by tape/disk locality.
    Read-only: never remediates. Callers exit 2 when ok is False.
    """
    primary, auxin = split_inputs(tarball_path)
    problems = []
    for ds, files in auxin.items():
        if inloc == 'resilient':
            problems += check_resilient(ds, files, sam_sizes, disk_size)
        else:
            problems += check_tape(ds, files, locality, dataset_location)
    for ds, files in primary.items():
        problems += check_tape(ds, files, locality, dataset_location)
    return (not problems, problems)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckInputs -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/check_inputs.py test/test_unit.py
git commit -m "feat: input pre-flight — assemble check_inputs with inloc routing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 5: CLI (`bin/check_inputs` + `main`)

**Files:**
- Modify: `utils/check_inputs.py` (add `format_report`, `main`, `__main__` guard)
- Create: `bin/check_inputs`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `check_inputs` (Task 4), `Problem` (Task 1).
- Produces:
  - `format_report(tarball_path, problems) -> str` — human-readable report grouped by dataset (one header per tarball, `OK` when empty).
  - `main(argv=None) -> int` — parses `[--inloc INLOC] tarball [tarball...]` (default `--inloc resilient`), runs `check_inputs` per tarball, prints reports, returns 0 (all clean) or 2 (any problem).

- [ ] **Step 1: Write the failing test**

Add to `test/test_unit.py`:

```python
class TestCheckInputsCLI(unittest.TestCase):
    """format_report + main: grouped report, exit 0 clean / 2 on problems."""

    def test_format_report_ok(self):
        from utils.check_inputs import format_report
        text = format_report("cnf.mu2e.T.C.0.tar", [])
        self.assertIn("cnf.mu2e.T.C.0.tar", text)
        self.assertIn("OK", text)

    def test_format_report_groups_problems(self):
        from utils.check_inputs import format_report, Problem
        probs = [
            Problem("dts.mu2e.Pile.CampB.art", "f1.art", "truncated", "1 != 2"),
            Problem("dts.mu2e.Prim.CampA.art", "f2.art", "nearline",
                    "run /prestage dts.mu2e.Prim.CampA.art"),
        ]
        text = format_report("cnf.mu2e.T.C.0.tar", probs)
        self.assertIn("truncated", text)
        self.assertIn("/prestage", text)
        self.assertIn("dts.mu2e.Pile.CampB.art", text)

    def test_main_returns_2_on_problem(self):
        from utils import check_inputs as ci
        with patch.object(ci, "check_inputs",
                          return_value=(False, [ci.Problem(
                              "ds", "f.art", "truncated", "d")])):
            rc = ci.main(["--inloc", "resilient", "cnf.mu2e.T.C.0.tar"])
        self.assertEqual(rc, 2)

    def test_main_returns_0_when_clean(self):
        from utils import check_inputs as ci
        with patch.object(ci, "check_inputs", return_value=(True, [])):
            rc = ci.main(["cnf.mu2e.T.C.0.tar"])
        self.assertEqual(rc, 0)

    def test_main_default_inloc_is_resilient(self):
        from utils import check_inputs as ci
        seen = {}
        def fake(tar, inloc, **kw):
            seen["inloc"] = inloc
            return (True, [])
        with patch.object(ci, "check_inputs", side_effect=fake):
            ci.main(["cnf.mu2e.T.C.0.tar"])
        self.assertEqual(seen["inloc"], "resilient")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckInputsCLI -v`
Expected: FAIL — `ImportError: cannot import name 'format_report'` / `main`.

- [ ] **Step 3a: Add `format_report`, `main`, `__main__` to `utils/check_inputs.py`**

Add `import argparse` and `import sys` to the imports, then append:

```python
def format_report(tarball_path, problems):
    """Human-readable report for one tarball, grouped by dataset."""
    lines = [f"=== {os.path.basename(tarball_path)}"]
    if not problems:
        lines.append("  OK: all inputs present, sized, and staged")
        return "\n".join(lines)
    by_ds = {}
    for p in problems:
        by_ds.setdefault(p.dataset, []).append(p)
    for ds in sorted(by_ds):
        lines.append(f"  {ds}: {len(by_ds[ds])} problem(s)")
        for p in by_ds[ds]:
            lines.append(f"    [{p.kind}] {p.filename}: {p.detail}")
    return "\n".join(lines)


def main(argv=None):
    """CLI: check one or more cnf tarballs. Returns 0 (all clean) or 2."""
    ap = argparse.ArgumentParser(
        description="Pre-flight check that a campaign's inputs are readable "
                    "(resilient pileup present+sized, tape inputs staged). "
                    "Read-only; run /prestage to fix NEARLINE inputs.")
    ap.add_argument('--inloc', default='resilient',
                    help="input location the jobs read from (default: "
                         "resilient, the mixing default)")
    ap.add_argument('tarballs', nargs='+', help="cnf.*.tar file(s)")
    args = ap.parse_args(argv)

    worst = 0
    for tb in args.tarballs:
        ok, problems = check_inputs(tb, args.inloc)
        print(format_report(tb, problems))
        if not ok:
            worst = 2
    return worst


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 3b: Create `bin/check_inputs`**

```bash
#!/bin/bash

# check_inputs - pre-flight check that a campaign's input files are
# readable before jobs launch (resilient pileup present + correctly
# sized, tape inputs staged). Read-only. Wrapper for
# utils/check_inputs.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/../utils/check_inputs.py"

if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    exec python3 "$PYTHON_SCRIPT" "$@"
fi

# Set up Mu2e environment (needed for samweb + mdh)
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops

exec python3 "$PYTHON_SCRIPT" "$@"
```

Then make it executable:

```bash
chmod +x bin/check_inputs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestCheckInputsCLI -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/check_inputs.py bin/check_inputs test/test_unit.py
git commit -m "feat: input pre-flight — bin/check_inputs CLI and report

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 6: Enqueue gate in `submit_map --enqueue`

**Files:**
- Modify: `utils/submit.py:226-262` (`_enqueue_entries`)
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `check_inputs` (Task 4), `format_report` (Task 5); existing `tarball_of(entry)`, `entry['inloc']`, `submission_ledger.create_campaign`.
- Produces: no new public symbol — `_enqueue_entries` gains a pre-check that exits 2 (no campaign row) when any entry's inputs fail.

**Note for the implementer:** the enqueue path currently runs `create_campaign` inside a `for idx, entry in entries_to_submit` loop (submit.py:250) and is **file-free** — `njobs_of` reads the entry dict, nothing opens the tarball. The gate introduces a tarball *read* (`check_inputs` opens it via `Mu2eJobPars`), so you MUST first resolve the tarball to a local path using the existing helper `_ensure_local_tarball(tarball_of(entry))` (submit.py:44) — it fetches the cnf from dropbox/SAM into cwd if not already local and returns the resolved `Path`. Pass that path to `check_inputs`. Put the whole gate at the top of the loop body, before `njobs_of`/`_snapshot_entry`, so a failing entry aborts BEFORE any campaign is created.

- [ ] **Step 1: Write the failing test**

Add to `test/test_unit.py`:

```python
class TestEnqueueInputGate(unittest.TestCase):
    """submit_map --enqueue refuses to create a campaign when an entry's
    inputs fail the pre-flight check (exit 2, no ledger row)."""

    def test_failing_check_blocks_and_creates_no_campaign(self):
        from utils import submit
        entry = {"tarball": "cnf.mu2e.T.C.0.tar", "inloc": "resilient",
                 "njobs": 100, "outputs": [{"dataset": "dig.mu2e.*.art",
                                            "location": "tape"}]}
        opts = MagicMock(dry_run=False, slice_size=500,
                         ledger_db="/tmp/never.db")
        created = []
        with patch.object(submit, "_ensure_local_tarball",
                          return_value=Path("cnf.mu2e.T.C.0.tar")), \
             patch.object(submit, "check_inputs",
                          return_value=(False, [submit.Problem(
                              "dts.mu2e.Pile.CampB.art", "f.art",
                              "truncated", "1 != 2")])), \
             patch.object(submit.submission_ledger, "create_campaign",
                          side_effect=lambda *a, **k: created.append(1)):
            with self.assertRaises(SystemExit) as cm:
                submit._enqueue_entries([(0, entry)], "map.json", opts)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(created, [])   # no campaign row

    def test_passing_check_creates_campaign(self):
        from utils import submit
        entry = {"tarball": "cnf.mu2e.T.C.0.tar", "inloc": "resilient",
                 "njobs": 100, "outputs": [{"dataset": "dig.mu2e.*.art",
                                            "location": "tape"}]}
        opts = MagicMock(dry_run=False, slice_size=500,
                         ledger_db="/tmp/never.db")
        with patch.object(submit, "_ensure_local_tarball",
                          return_value=Path("cnf.mu2e.T.C.0.tar")), \
             patch.object(submit, "check_inputs", return_value=(True, [])), \
             patch.object(submit.submission_ledger, "create_campaign",
                          return_value=7):
            ids = submit._enqueue_entries([(0, entry)], "map.json", opts)
        self.assertEqual(ids, [7])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestEnqueueInputGate -v`
Expected: FAIL — `AttributeError: <module 'utils.submit'> does not have the attribute 'check_inputs'` (not yet imported).

- [ ] **Step 3: Wire the gate**

In `utils/submit.py`, add to the imports (near the other `from utils...` imports):

```python
from utils.check_inputs import check_inputs, format_report, Problem
```

Then in `_enqueue_entries`, at the very top of the `for idx, entry in entries_to_submit:` loop body (before `njobs = njobs_of(entry)`), add:

```python
        tarball_path = _ensure_local_tarball(tarball_of(entry))
        ok, problems = check_inputs(str(tarball_path), entry['inloc'])
        if not ok:
            print(format_report(str(tarball_path), problems))
            sys.exit(f"submit_map: entry {idx} inputs not ready "
                     f"({len(problems)} problem(s)) — fix and re-run; "
                     f"no campaign created")
```

(`sys`, `tarball_of`, and `_ensure_local_tarball` are already defined/imported in `submit.py`; `Problem` is imported for the test's use of `submit.Problem`.)

**Test note:** the two tests patch `submit.check_inputs`, so `_ensure_local_tarball` still runs for real. The fixture tarball name `cnf.mu2e.T.C.0.tar` is not on disk, so `_ensure_local_tarball` would try to fetch it. Patch it too: add `patch.object(submit, "_ensure_local_tarball", return_value=Path("cnf.mu2e.T.C.0.tar"))` to both tests' `with` blocks (import `Path` is already available in the test module).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit.TestEnqueueInputGate -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m unittest test.test_unit 2>&1 | grep -E "^Ran|^OK|^FAILED"`
Expected: `OK`, count ≈ 503 (474 baseline + 29 new).

- [ ] **Step 6: Commit**

```bash
git add utils/submit.py test/test_unit.py
git commit -m "feat: input pre-flight — gate submit_map --enqueue on input readiness

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/EXAMPLES_schema.md`
- Regenerate: `EXAMPLES.md` (via `/refresh-examples`)
- Create/modify: `wiki/pages/` + `wiki/log.md`

**Note:** `EXAMPLES.md` is a derived artifact — do NOT hand-edit it. Add the tool to the schema, then regenerate.

- [ ] **Step 1: Add `check_inputs` to the EXAMPLES schema**

In `docs/EXAMPLES_schema.md`, add `check_inputs` to the Additional Tools list (near `copy_to_stash`), with a one-line description and the canonical invocation:

```markdown
- `check_inputs` — pre-flight check that a campaign's input files are
  readable before jobs launch: resilient pileup present and correctly
  sized (vs SAM), tape inputs staged (not NEARLINE). Read-only; exits 2
  on any problem. Run before `submit_map --enqueue` (which also gates on
  it) or by hand when a resilient purge is suspected.

  ```bash
  check_inputs cnf.mu2e.RMCPhaseSpace0NExternalMix1BB.MDC2025ar_best_v1_3.0.tar
  check_inputs --inloc resilient cnf.mu2e.*.tar
  ```
```

- [ ] **Step 2: Regenerate EXAMPLES.md**

Run the `/refresh-examples` slash command (single pass; it overwrites `EXAMPLES.md` from source). Confirm the new `check_inputs` subsection appears and every flag shown (`--inloc`) exists in `utils/check_inputs.py`'s argparse.

- [ ] **Step 3: Add a wiki page**

Create `wiki/pages/2026-07-21-input-preflight-check.md` following `wiki/SCHEMA.md` conventions: what the check verifies (resilient size vs SAM; tape locality via mdh), why mdh can't see resilient, the enqueue gate + standalone command, the block-only/exit-2 contract, and the link to the index-519 incident (`wiki/log.md`) that motivated it. Cross-link `[[direct-recovery-loop]]` and the truncated-file log entry.

- [ ] **Step 4: Log it**

Append a dated entry to `wiki/log.md` summarizing the feature and its motivation (the index-519 truncated-pileup incident).

- [ ] **Step 5: Commit**

```bash
git add docs/EXAMPLES_schema.md EXAMPLES.md wiki/
git commit -m "docs: input pre-flight check (schema, EXAMPLES regen, wiki)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

## Notes for the executor

- **`mdh` and `samweb` are unavailable in the unit-test env** — that is by design. Every check function takes its I/O as injected callables; the tests pass fakes and never touch the network. Only the thin defaults (`_default_disk_size`, `_default_locality`, `file_sizes_in_dataset`) call the real tools, and their tests mock `subprocess.run` / the SAM client.
- **Do not add auto-remediation.** If a task tempts you to "just prestage it here," stop — that is an explicit non-goal. The check reports; the human runs `/prestage`.
- **Run `bin/check_inputs` for real against a live tarball only under the Mu2e env** (`bin/check_inputs` sources it). Expect it to hit SAM + mdh; it is read-only and safe to run as yourself (no mu2epro needed — it is a status check).
