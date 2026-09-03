# Sim Epochs Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `bin/epochs` command that derives, from SAM alone, which production datasets are current, stale or superseded, which are missing, which may be deleted, and publishes the result in the file shape Ray's `sim-epochs` MCP server reads.

**Architecture:** One small curated JSON file per digitization campaign in `data/epochs/`; everything else computed at run time from SAM parentage (`ischildof:` / `isparentof:` dataset queries) and from the cnf tarballs. A pure Python core (parser, graph, status, reports) that takes an injected data source, so the whole suite runs without the Mu2e environment; one thin `SamSource` over `utils/samweb_wrapper`; one argparse CLI. Nothing is ever written to SAM or metacat.

**Tech Stack:** Python 3.9 (cvmfs floor), `typing.NamedTuple` / `dataclasses`, `unittest` classes under the `pytest` runner (`test/test_epochs.py`), `utils.samweb_wrapper`, `utils.job_common.Mu2eName`, `utils.jobquery.Mu2eJobPars`.

**Spec:** `wiki/pages/2026-09-02-sim-epochs-design.md` (sections marked "decided" and the section 17 table are authoritative), glossary `CONTEXT.md` (Epoch, Root, Input, Family, Sibling, status, Hold, Gap, Generation), `docs/adr/0003-cnf-tarball-declared-as-sam-parent.md`, `docs/adr/0004-epoch-catalog-derived-never-stored.md`.

**Out of scope for this plan** (separate plans): the `runmu2e.push_data` change that declares the cnf as a parent (ADR 0003 code side); the MCP tools on the `prodtools` server; the `input_epoch` production hook (deferred by decision 16).

## Global Constraints

- Python 3.9 only: no `match`, no `X | Y` type unions, no `list[str]` at runtime in signatures (use `typing.List`).
- **No timestamps anywhere** in ordering or status (decision 4). Creation dates are never read. `generated_at` in the published file is the only clock and is informational.
- **No fallbacks**: an unparseable dsconf, a dataset with no cnf, a SAM error, are reported by name, never guessed, never silently dropped, never defaulted (project rule "no fallbacks — data OR code paths").
- **Owner `mu2e` only** (decision 14): every discovery query is `*.mu2e.*`; a non-`mu2e` owner in a parentage walk is recorded under `foreign` and never becomes a member.
- **Nothing derived is stored** (ADR 0004). The only files written under `data/epochs/` are the curated epoch files (`propose` verb) and the cnf index (immutable facts, decision 6 backfill). Status, gaps, retire lists are always recomputed.
- Status vocabulary is exactly `current`, `stale`, `superseded`. A hold is not a status.
- Tiers `log`, `cnf`, `etc` are never members.
- Tests run standalone: `test/test_epochs.py` stubs `samweb_client` and `ifdh` exactly as `test/test_unit.py` does, and every SAM access goes through an injected source object.
- Commit after every task with the project trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC
  ```
- Real-SAM checks (Task 12) run as the current user via `/mu2e-run`; nothing here needs `mu2epro`.

---

## File Structure

| File | Responsibility |
|---|---|
| `utils/epochs/__init__.py` | Package marker, one docstring. |
| `utils/epochs/dsconf.py` | Parse a dsconf string into `DsconfKey`; ordering; family extraction. Pure. |
| `utils/epochs/epoch_files.py` | Load, validate, propose and write `data/epochs/<letters>.json`. Pure except file I/O. |
| `utils/epochs/source.py` | `SamSource`: the only module that imports `utils.samweb_wrapper`. Dataset-level children, parents, file counts, cnf listing, local path of a SAM file. |
| `utils/epochs/graph.py` | `Member`, `Catalog`; build members and inputs from roots by walking a source. Pure given a source. |
| `utils/epochs/status.py` | Assign `current`/`stale`/`superseded` and holds. Pure. |
| `utils/epochs/generation.py` | `Generation` from a cnf tarball; cnf lookup by parent or index; index build. |
| `utils/epochs/reports.py` | gaps, consistency, retire (with Ray's purge line format), lookup. Pure. |
| `utils/epochs/publish.py` | Build the catalog JSON in Ray's shape plus our fields. Pure. |
| `utils/epochs/cli.py` | argparse verbs and text rendering. |
| `bin/epochs` | Python wrapper, same shape as `bin/latestDatasets`. |
| `test/test_epochs.py` | All tests, with `FakeSource`. |
| `data/epochs/*.json` | Curated epoch files, created by `propose`, edited by people. |
| `data/epochs/cnf_index.json` | dataset → cnf name, built by `index-cnfs`. |

---

### Task 1: dsconf parser and ordering

**Files:**
- Create: `utils/epochs/__init__.py`
- Create: `utils/epochs/dsconf.py`
- Create: `test/test_epochs.py`

**Interfaces:**
- Produces: `DsconfKey(family, letters, rev, purpose, major, minor, suffix, extra)` NamedTuple; `parse_dsconf(s) -> DsconfKey` raising `DsconfParseError(ValueError)`; `DsconfKey.sort_key() -> tuple`; `family_of(s) -> str`.

- [ ] **Step 1: Create the test file with the standard preamble and the parser tests**

```python
"""Tests for utils/epochs: sim-epochs catalog. Runs standalone (samweb stubbed)."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _mod in ('samweb_client', 'ifdh'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from utils.epochs.dsconf import DsconfKey, DsconfParseError, parse_dsconf, family_of


class TestDsconfParse(unittest.TestCase):
    def test_full_grammar(self):
        k = parse_dsconf('MDC2025au_best_v1_5-001')
        self.assertEqual(k, DsconfKey('MDC2025', 'au', 0, 'best', 1, 5, 1, ''))

    def test_no_purpose_dts_style(self):
        k = parse_dsconf('MDC2025ap')
        self.assertEqual((k.family, k.letters, k.purpose), ('MDC2025', 'ap', None))

    def test_revision_digit(self):
        k = parse_dsconf('Run1Bab2_best_v1_2')
        self.assertEqual((k.family, k.letters, k.rev), ('Run1B', 'ab', 2))

    def test_single_letter_tag(self):
        k = parse_dsconf('MDC2020r')
        self.assertEqual((k.family, k.letters), ('MDC2020', 'r'))

    def test_own_series_no_letters(self):
        k = parse_dsconf('MDC2025-003')
        self.assertEqual((k.family, k.letters, k.suffix), ('MDC2025', '', 3))

    def test_legacy_offline_tail(self):
        k = parse_dsconf('MDC2020aw_best_v1_3_v06_06_00')
        self.assertEqual(k.extra, 'v06_06_00')

    def test_unparseable_raises(self):
        with self.assertRaises(DsconfParseError):
            parse_dsconf('best_v1_5')
        with self.assertRaises(DsconfParseError):
            parse_dsconf('')

    def test_family_of(self):
        self.assertEqual(family_of('Run1Ban_best_v1_4-000'), 'Run1B')


class TestDsconfOrder(unittest.TestCase):
    def key(self, s):
        return parse_dsconf(s).sort_key()

    def test_letters_then_version_then_suffix(self):
        self.assertLess(self.key('MDC2025au_best_v1_3'), self.key('MDC2025au_best_v1_5'))
        self.assertLess(self.key('MDC2025au_best_v1_5'), self.key('MDC2025au_best_v1_5-001'))
        self.assertLess(self.key('MDC2025ar_best_v1_1'), self.key('MDC2025au_best_v1_1'))

    def test_revision_outranks_base(self):
        self.assertLess(self.key('Run1Bab_best_v1_2'), self.key('Run1Bab2_best_v1_2'))

    def test_two_letters_outrank_one(self):
        self.assertLess(self.key('MDC2020r'), self.key('MDC2020aa'))

    def test_own_series_ranks_below_any_letters(self):
        self.assertLess(self.key('MDC2025-003'), self.key('MDC2025ad_best_v1_3'))

    def test_purpose_not_in_order(self):
        # best vs perfect are siblings by purpose; the order key ignores it
        self.assertEqual(self.key('MDC2020ar_best_v1_3'), self.key('MDC2020ar_perfect_v1_3'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.epochs'`

- [ ] **Step 3: Write the package marker and the parser**

`utils/epochs/__init__.py`:
```python
"""Sim-epochs catalog: derive dataset status from SAM, never store it.

See wiki/pages/2026-09-02-sim-epochs-design.md and CONTEXT.md.
"""
```

`utils/epochs/dsconf.py`:
```python
"""Parse and order Mu2e dsconf strings.

Grammar (decision 4 of the 2026-09-03 grill):
    <family><letters><rev?>[_<purpose>_v<major>_<minor>][_<extra>][-<suffix>]
    family  : starts with an uppercase letter, e.g. MDC2025, Run1B, MDC2020
    letters : one or two lowercase campaign letters; ABSENT for the dead
              own-series ntuple names (MDC2025-003), which rank lowest
    rev     : optional single digit revision (Run1Bab2)
    extra   : legacy Offline-version tail (v06_06_00), ordered last, lexically

Ordering never uses a clock. `sort_key()` is the only comparison the
catalog makes between siblings.
"""
import re
from typing import NamedTuple, Optional

_RE = re.compile(
    r'^(?P<family>[A-Z][A-Za-z0-9]*?)'
    r'(?P<letters>[a-z]{1,2})?(?P<rev>\d)?'
    r'(?:_(?P<purpose>[a-z]+)_v(?P<major>\d+)_(?P<minor>\d+))?'
    r'(?:_(?P<extra>v\d+_\d+_\d+))?'
    r'(?:-(?P<suffix>\d+))?$'
)


class DsconfParseError(ValueError):
    """The dsconf does not follow the Mu2e grammar. Report it; never guess."""


class DsconfKey(NamedTuple):
    family: str
    letters: str          # '' for own-series names
    rev: int              # 0 when absent
    purpose: Optional[str]
    major: int            # -1 when absent
    minor: int            # -1 when absent
    suffix: int           # -1 when absent
    extra: str            # '' when absent

    def sort_key(self):
        """Newest sorts highest. Purpose is a group key, not an order key."""
        return ((len(self.letters), self.letters), self.rev,
                self.major, self.minor, self.suffix, self.extra)


def parse_dsconf(s: str) -> DsconfKey:
    m = _RE.match(s or '')
    if not m:
        raise DsconfParseError(f'dsconf does not parse: {s!r}')
    g = m.groupdict()
    # A family with no letters is only legal in the own-series form
    # (MDC2025-003); a bare 'MDC2025' would otherwise parse as a family.
    if g['letters'] is None and g['suffix'] is None:
        raise DsconfParseError(f'dsconf has neither letters nor suffix: {s!r}')
    return DsconfKey(
        family=g['family'],
        letters=g['letters'] or '',
        rev=int(g['rev']) if g['rev'] else 0,
        purpose=g['purpose'],
        major=int(g['major']) if g['major'] else -1,
        minor=int(g['minor']) if g['minor'] else -1,
        suffix=int(g['suffix']) if g['suffix'] else -1,
        extra=g['extra'] or '',
    )


def family_of(s: str) -> str:
    return parse_dsconf(s).family
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add utils/epochs/__init__.py utils/epochs/dsconf.py test/test_epochs.py
git commit -m "feat(epochs): dsconf parser and clock-free ordering

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 2: epoch files — load, validate, propose

**Files:**
- Create: `utils/epochs/epoch_files.py`
- Modify: `test/test_epochs.py` (append)

**Interfaces:**
- Consumes: `parse_dsconf`, `family_of` from Task 1.
- Produces: `EpochFile(name, family, purpose, status, roots, pins)` NamedTuple where `pins` is a dict with keys `exclude` (list of `{dataset, reason}`), `hold` (list of `{dataset, reason}`), `not_expected` (list of `{desc, tiers, reason}`), `order` (list of `{tier, desc, purpose, winner, reason}`), `notes` (list of `{pattern, note}`); `load_epoch_files(dirpath) -> Dict[str, EpochFile]` raising `EpochFileError`; `propose_epoch(letters_dsconf_sample) -> dict`; `write_epoch_file(dirpath, data) -> str`; constant `EPOCH_STATUSES = ('current', 'frozen', 'retired')`.

- [ ] **Step 1: Append the tests**

```python
from utils.epochs.epoch_files import (EpochFile, EpochFileError, load_epoch_files,
                                      propose_epoch, write_epoch_file, EPOCH_STATUSES)


def _tmpdir():
    d = tempfile.mkdtemp()
    return d


class TestEpochFiles(unittest.TestCase):
    def _write(self, d, name, data):
        with open(os.path.join(d, name + '.json'), 'w') as f:
            json.dump(data, f)

    def test_load_minimal(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {
            'name': 'MDC2025au', 'purpose': 'Run-1 nominal', 'status': 'current',
            'roots': ['dig.mu2e.%.MDC2025au_%.art']})
        files = load_epoch_files(d)
        e = files['MDC2025au']
        self.assertEqual(e.family, 'MDC2025')
        self.assertEqual(e.pins['exclude'], [])
        self.assertEqual(e.pins['hold'], [])

    def test_name_must_match_filename_and_parse(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025an', 'purpose': '', 'status': 'current',
                                     'roots': ['dig.mu2e.%.MDC2025an_%.art']})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_bad_status_rejected(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025au', 'purpose': '', 'status': 'active',
                                     'roots': ['dig.mu2e.%.MDC2025au_%.art']})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_pin_without_reason_rejected(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025au', 'purpose': '', 'status': 'current',
                                     'roots': ['dig.mu2e.%.MDC2025au_%.art'],
                                     'pins': {'exclude': [{'dataset': 'mcs.mu2e.X.MDC2025au_best_v1_5.art'}]}})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_root_must_be_five_field_mu2e_pattern(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025au', 'purpose': '', 'status': 'current',
                                     'roots': ['dig.oksuzian.%.MDC2025au_%.art']})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_propose_from_sample_dsconf(self):
        data = propose_epoch('MDC2025au_best_v1_5')
        self.assertEqual(data['name'], 'MDC2025au')
        self.assertEqual(data['roots'], ['dig.mu2e.%.MDC2025au_%.art'])
        self.assertEqual(data['status'], 'current')
        self.assertEqual(data['purpose'], '')
        d = _tmpdir()
        path = write_epoch_file(d, data)
        self.assertTrue(path.endswith('MDC2025au.json'))
        self.assertIn('MDC2025au', load_epoch_files(d))

    def test_write_refuses_to_overwrite(self):
        d = _tmpdir()
        data = propose_epoch('MDC2025au_best_v1_5')
        write_epoch_file(d, data)
        with self.assertRaises(EpochFileError):
            write_epoch_file(d, data)

    def test_missing_dir_is_empty_not_error(self):
        self.assertEqual(load_epoch_files(os.path.join(_tmpdir(), 'nope')), {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k TestEpochFiles`
Expected: FAIL with `ImportError` on `utils.epochs.epoch_files`

- [ ] **Step 3: Write the module**

`utils/epochs/epoch_files.py`:
```python
"""Curated epoch files: data/epochs/<letters>.json.

The ONLY curated input of the catalog (ADR 0004). One file per
digitization campaign, named by its campaign letters (decision 9).
A person writes `purpose`, flips `status`, and adds pins with reasons.
Everything else is derived.
"""
import glob
import json
import os
from typing import Dict, List, NamedTuple

from utils.epochs.dsconf import DsconfParseError, parse_dsconf

EPOCH_STATUSES = ('current', 'frozen', 'retired')
PIN_KINDS = {
    'exclude': ('dataset', 'reason'),
    'hold': ('dataset', 'reason'),
    'not_expected': ('desc', 'tiers', 'reason'),
    'order': ('tier', 'desc', 'purpose', 'winner', 'reason'),
    'notes': ('pattern', 'note'),
}


class EpochFileError(ValueError):
    """A curated epoch file is malformed. Name the file and the field."""


class EpochFile(NamedTuple):
    name: str
    family: str
    purpose: str
    status: str
    roots: List[str]
    pins: Dict[str, list]


def _check_root(path, root):
    parts = root.split('.')
    if len(parts) != 5:
        raise EpochFileError(f'{path}: root must have 5 dot-fields: {root!r}')
    if parts[1] != 'mu2e':
        raise EpochFileError(f'{path}: root owner must be mu2e: {root!r}')


def _check_pins(path, pins):
    out = {k: [] for k in PIN_KINDS}
    for kind, entries in (pins or {}).items():
        if kind not in PIN_KINDS:
            raise EpochFileError(f'{path}: unknown pin kind {kind!r}')
        if not isinstance(entries, list):
            raise EpochFileError(f'{path}: pins.{kind} must be a list')
        for e in entries:
            missing = [f for f in PIN_KINDS[kind] if f not in e]
            if missing:
                raise EpochFileError(f'{path}: pins.{kind} entry missing {missing}: {e}')
            out[kind].append(e)
    return out


def _parse_one(path) -> EpochFile:
    with open(path) as f:
        data = json.load(f)
    stem = os.path.splitext(os.path.basename(path))[0]
    for field in ('name', 'purpose', 'status', 'roots'):
        if field not in data:
            raise EpochFileError(f'{path}: missing {field!r}')
    if data['name'] != stem:
        raise EpochFileError(f'{path}: name {data["name"]!r} != filename stem {stem!r}')
    try:
        key = parse_dsconf(data['name'])
    except DsconfParseError as exc:
        raise EpochFileError(f'{path}: {exc}') from exc
    if not key.letters:
        raise EpochFileError(f'{path}: epoch name needs campaign letters: {data["name"]!r}')
    if data['status'] not in EPOCH_STATUSES:
        raise EpochFileError(f'{path}: status must be one of {EPOCH_STATUSES}, got {data["status"]!r}')
    if not isinstance(data['roots'], list) or not data['roots']:
        raise EpochFileError(f'{path}: roots must be a non-empty list')
    for r in data['roots']:
        _check_root(path, r)
    return EpochFile(name=data['name'], family=key.family, purpose=data['purpose'],
                     status=data['status'], roots=list(data['roots']),
                     pins=_check_pins(path, data.get('pins')))


def load_epoch_files(dirpath: str) -> Dict[str, EpochFile]:
    """Every data/epochs/*.json, validated. A missing directory is an
    empty catalog, not an error (first run). A malformed file is."""
    if not os.path.isdir(dirpath):
        return {}
    out = {}
    for path in sorted(glob.glob(os.path.join(dirpath, '*.json'))):
        if os.path.basename(path) == 'cnf_index.json':
            continue
        e = _parse_one(path)
        out[e.name] = e
    return out


def propose_epoch(sample_dsconf: str) -> dict:
    """The file a new letter family gets before a person touches it."""
    key = parse_dsconf(sample_dsconf)
    if not key.letters:
        raise EpochFileError(f'cannot propose an epoch from {sample_dsconf!r}: no letters')
    name = f'{key.family}{key.letters}{key.rev or ""}'
    return {
        'name': name,
        'purpose': '',
        'status': 'current',
        'roots': [f'dig.mu2e.%.{name}_%.art'],
        'pins': {k: [] for k in PIN_KINDS},
    }


def write_epoch_file(dirpath: str, data: dict) -> str:
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, data['name'] + '.json')
    if os.path.exists(path):
        raise EpochFileError(f'{path} exists; edit it, do not re-propose')
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    return path
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `21 passed`

- [ ] **Step 5: Commit**

```bash
git add utils/epochs/epoch_files.py test/test_epochs.py
git commit -m "feat(epochs): curated epoch files — load, validate, propose

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 3: the SAM source and its fake

**Files:**
- Create: `utils/epochs/source.py`
- Modify: `test/test_epochs.py` (append `FakeSource` and source tests)

**Interfaces:**
- Consumes: `utils.samweb_wrapper.list_files`, `count_files`, `definitions_matching`, `locate_file`, `file_lineage`; `utils.job_common.Mu2eName`.
- Produces: class `SamSource` with methods
  - `dig_datasets(family: str) -> Dict[str, int]` — 5-field `dig.mu2e.*.<family>*.art` dataset names with a file count > 0.
  - `children(dataset: str) -> Dict[str, int]` — child DATASET names → file count, from `ischildof: (dh.dataset X)`, tiers log/cnf/etc dropped, non-mu2e owners dropped and reported via `self.foreign`.
  - `parents(dataset: str) -> Dict[str, int]` — same from `isparentof:`.
  - `nfiles(dataset: str) -> int`.
  - `cnf_names() -> List[str]` — every `cnf.mu2e.*.tar` dataset-ish name known to SAM (definitions matching `cnf.mu2e.%`, 6-field tarball names only).
  - `local_path(filename: str) -> str` — `/pnfs/...` path of a SAM file, `''` if unknown.
  - `first_file(dataset: str) -> str`.
  - Module function `group_files_to_datasets(filenames) -> Dict[str, int]` (pure; used by both real and fake).
- Test double `FakeSource(graph)` built from a dict `{dataset: {'n': int, 'children': [names]}}`, exposing the same methods.

- [ ] **Step 1: Append the tests and the fake**

```python
from utils.epochs.source import group_files_to_datasets, SamSource, DROP_TIERS


class FakeSource:
    """In-memory stand-in for SamSource. graph: {dataset: {'n': int, 'children': [...]}}.
    Parents are derived by inverting children. cnfs: {cnf_name: local_path}."""
    def __init__(self, graph, cnfs=None, files=None):
        self.graph = graph
        self.cnfs = cnfs or {}
        self.files = files or {}
        self.foreign = set()
        self._parents = {}
        for p, rec in graph.items():
            for c in rec.get('children', []):
                self._parents.setdefault(c, []).append(p)

    def dig_datasets(self, family):
        return {d: r['n'] for d, r in self.graph.items()
                if d.startswith('dig.mu2e.') and f'.{family}' in d}

    def children(self, dataset):
        return {c: self.graph[c]['n'] for c in self.graph.get(dataset, {}).get('children', [])
                if c.split('.')[0] not in DROP_TIERS}

    def parents(self, dataset):
        return {p: self.graph[p]['n'] for p in self._parents.get(dataset, [])}

    def nfiles(self, dataset):
        return self.graph[dataset]['n']

    def cnf_names(self):
        return sorted(self.cnfs)

    def local_path(self, filename):
        return self.cnfs.get(filename) or self.files.get(filename, '')

    def first_file(self, dataset):
        return self.graph[dataset].get('first', '')


class TestGroupFiles(unittest.TestCase):
    def test_groups_by_dataset_and_drops_log_cnf_etc(self):
        names = ['mcs.mu2e.A.MDC2025au_best_v1_5.001430_00000000.art',
                 'mcs.mu2e.A.MDC2025au_best_v1_5.001430_00000001.art',
                 'log.mu2e.A.MDC2025au_best_v1_5.001430_00000000-1.log',
                 'cnf.mu2e.A.MDC2025au_best_v1_5.0.tar',
                 'etc.mu2e.A.MDC2025au_best_v1_5.0.txt']
        groups, foreign = group_files_to_datasets(names)
        self.assertEqual(groups, {'mcs.mu2e.A.MDC2025au_best_v1_5.art': 2})
        self.assertEqual(foreign, set())

    def test_non_mu2e_owner_goes_to_foreign(self):
        groups, foreign = group_files_to_datasets(
            ['nts.oksuzian.A.MDC2025au_best_v1_5.001430_00000000.root'])
        self.assertEqual(groups, {})
        self.assertEqual(foreign, {'nts.oksuzian.A.MDC2025au_best_v1_5.root'})

    def test_unparseable_name_raises(self):
        with self.assertRaises(ValueError):
            group_files_to_datasets(['not-a-mu2e-name'])


class TestSamSourceQueries(unittest.TestCase):
    """SamSource only builds SAM query strings and groups; exercise it with
    injected wrapper functions, no network."""
    def test_children_query_and_grouping(self):
        calls = []
        def fake_list_files(q):
            calls.append(q)
            return ['nts.mu2e.A.MDC2025au_best_v1_5.001430_00000000.root',
                    'log.mu2e.A.MDC2025au_best_v1_5.001430_00000000-1.log']
        src = SamSource(list_files_fn=fake_list_files)
        out = src.children('mcs.mu2e.A.MDC2025au_best_v1_5.art')
        self.assertEqual(calls, ['ischildof: (dh.dataset mcs.mu2e.A.MDC2025au_best_v1_5.art)'])
        self.assertEqual(out, {'nts.mu2e.A.MDC2025au_best_v1_5.root': 1})

    def test_dig_datasets_filters_five_field_art_and_counts(self):
        def fake_defs(defname=None, user=None):
            return ['dig.mu2e.A.MDC2025au_best_v1_5.art',
                    'dig.mu2e.A.MDC2025au_best_v1_5.001430_00000007',   # per-index def
                    'dig.mu2e.B.MDC2025au_best_v1_3.art',
                    'dig.mu2e.C.MDC2025au_best_v1_3-recovery']
        counts = {'dh.dataset dig.mu2e.A.MDC2025au_best_v1_5.art': 10,
                  'dh.dataset dig.mu2e.B.MDC2025au_best_v1_3.art': 0}
        src = SamSource(definitions_fn=fake_defs, count_files_fn=lambda q: counts[q])
        self.assertEqual(src.dig_datasets('MDC2025'), {'dig.mu2e.A.MDC2025au_best_v1_5.art': 10})

    def test_local_path_strips_dcache_prefix_and_appends_name(self):
        src = SamSource(locate_fn=lambda f: 'dcache:/pnfs/mu2e/persistent/x/y(1@z)')
        self.assertEqual(src.local_path('cnf.mu2e.A.B.0.tar'),
                         '/pnfs/mu2e/persistent/x/y/cnf.mu2e.A.B.0.tar')
        src = SamSource(locate_fn=lambda f: '')
        self.assertEqual(src.local_path('cnf.mu2e.A.B.0.tar'), '')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k "TestGroupFiles or TestSamSourceQueries"`
Expected: FAIL with `ImportError` on `utils.epochs.source`

- [ ] **Step 3: Write the module**

`utils/epochs/source.py`:
```python
"""The only module in utils/epochs that talks to SAM.

Dataset-level lineage uses SAM's `ischildof: (dh.dataset X)` and
`isparentof: (dh.dataset X)` dimensions (verified 2026-09-03: one call
returns every child/parent FILE of the dataset; we group them back to
5-field dataset names). Everything is injectable so the catalog core
is testable without the Mu2e environment.
"""
import re
from typing import Callable, Dict, List, Optional, Set, Tuple

from utils.job_common import Mu2eName

DROP_TIERS = frozenset({'log', 'cnf', 'etc'})
OWNER = 'mu2e'


def group_files_to_datasets(filenames) -> Tuple[Dict[str, int], Set[str]]:
    """6-field file names -> {5-field dataset: count}. Tiers in DROP_TIERS
    are not members (they are not physics). Non-mu2e owners are returned
    separately so a caller can report them; they never become members.
    An unparseable name raises: SAM handed back something that is not a
    Mu2e name and we will not guess."""
    groups: Dict[str, int] = {}
    foreign: Set[str] = set()
    for fn in filenames:
        n = Mu2eName.parse(fn)          # raises ValueError on a bad name
        if n.tier in DROP_TIERS:
            continue
        ds = str(n.dataset)
        if n.owner != OWNER:
            foreign.add(ds)
            continue
        groups[ds] = groups.get(ds, 0) + 1
    return groups, foreign


def _default_list_files(q):
    from utils.samweb_wrapper import list_files
    return list_files(q)


def _default_count_files(q):
    from utils.samweb_wrapper import count_files
    return count_files(q)


def _default_definitions(defname=None, user=None):
    from utils.samweb_wrapper import definitions_matching
    return definitions_matching(defname=defname, user=user)


def _default_locate(fn):
    from utils.samweb_wrapper import locate_file
    return locate_file(fn)


_LOC_RE = re.compile(r'^(?:dcache:|enstore:)?(/pnfs/[^(\s]+)')


class SamSource:
    def __init__(self, list_files_fn: Optional[Callable] = None,
                 count_files_fn: Optional[Callable] = None,
                 definitions_fn: Optional[Callable] = None,
                 locate_fn: Optional[Callable] = None):
        self._list = list_files_fn or _default_list_files
        self._count = count_files_fn or _default_count_files
        self._defs = definitions_fn or _default_definitions
        self._locate = locate_fn or _default_locate
        self.foreign: Set[str] = set()

    # -- discovery -------------------------------------------------------
    def dig_datasets(self, family: str) -> Dict[str, int]:
        """5-field dig.mu2e.*.<family>*.art datasets with at least one file.
        Definitions are only a candidate list: existence is a file count."""
        out = {}
        for name in self._defs(defname=f'dig.{OWNER}.%.{family}%.art'):
            parts = name.split('.')
            if len(parts) != 5 or parts[4] != 'art' or parts[1] != OWNER:
                continue
            n = self._count(f'dh.dataset {name}')
            if n > 0:
                out[name] = n
        return out

    def cnf_names(self) -> List[str]:
        out = []
        for name in self._defs(defname=f'cnf.{OWNER}.%'):
            parts = name.split('.')
            if len(parts) == 6 and parts[5] == 'tar':
                out.append(name)
        return sorted(set(out))

    # -- lineage ---------------------------------------------------------
    def _grouped(self, query: str) -> Dict[str, int]:
        groups, foreign = group_files_to_datasets(self._list(query))
        self.foreign |= foreign
        return groups

    def children(self, dataset: str) -> Dict[str, int]:
        return self._grouped(f'ischildof: (dh.dataset {dataset})')

    def parents(self, dataset: str) -> Dict[str, int]:
        return self._grouped(f'isparentof: (dh.dataset {dataset})')

    # -- files -----------------------------------------------------------
    def nfiles(self, dataset: str) -> int:
        return self._count(f'dh.dataset {dataset}')

    def first_file(self, dataset: str) -> str:
        files = self._list(f'dh.dataset {dataset} with limit 1')
        return files[0] if files else ''

    def local_path(self, filename: str) -> str:
        """/pnfs path of a SAM file for direct reading on a gpvm; '' if
        SAM does not know the file. Only used for cnf tarballs."""
        loc = self._locate(filename)
        if not loc:
            return ''
        m = _LOC_RE.match(loc)
        if not m:
            raise ValueError(f'unexpected SAM location for {filename}: {loc!r}')
        return f'{m.group(1).rstrip("/")}/{filename}'
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `27 passed`

- [ ] **Step 5: Commit**

```bash
git add utils/epochs/source.py test/test_epochs.py
git commit -m "feat(epochs): SamSource — dataset-level lineage via ischildof/isparentof

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 4: build the graph — members and inputs from roots

**Files:**
- Create: `utils/epochs/graph.py`
- Modify: `test/test_epochs.py` (append)

**Interfaces:**
- Consumes: `EpochFile`, `parse_dsconf`, a source with `dig_datasets/children/parents`.
- Produces:
  ```python
  @dataclass
  class Member:
      name: str; tier: str; desc: str; dsconf: str; key: DsconfKey
      epoch: str; nfiles: int
      parents: Set[str]       # member dataset names only
      inputs: Set[str]        # non-member parent dataset names (dts, Cats)
      status: str = ''        # filled by status.py
      hold: str = ''          # reason text when held, else ''
      excluded: str = ''      # reason text when excluded, else ''
  @dataclass
  class Catalog:
      epochs: Dict[str, EpochFile]
      members: Dict[str, Member]
      inputs: Dict[str, Dict]   # name -> {'nfiles': int, 'parents': set, 'descendants': set}
      unparseable: List[Tuple[str, str]]   # (name, error)
      foreign: Set[str]
      unclaimed_digs: Dict[str, List[str]] # family -> dig datasets matching no epoch root
  build_catalog(epochs, source, families, input_depth=3) -> Catalog
  root_matches(root_pattern, dataset) -> bool   # SAM '%' glob semantics
  ```

- [ ] **Step 1: Append the tests**

```python
from utils.epochs.graph import build_catalog, root_matches, Member, Catalog

AU = 'MDC2025au_best_v1_5'
AU3 = 'MDC2025au_best_v1_3'
AR = 'MDC2025ar_best_v1_1'
AN = 'MDC2025an_best_v1_1'


def _epoch(name, roots=None, status='current', pins=None):
    from utils.epochs.dsconf import family_of
    p = {k: [] for k in ('exclude', 'hold', 'not_expected', 'order', 'notes')}
    p.update(pins or {})
    return EpochFile(name=name, family=family_of(name + '_best_v1_0'), purpose='',
                     status=status, roots=roots or [f'dig.mu2e.%.{name}_%.art'], pins=p)


def _small_graph():
    """dts@ap -> dig CE@au v1_5 -> mcs CE@au v1_5 -> nts (old) + nts -001
       Cat@ac -> dts@ap
       dig CE@an v1_1 -> mcs CE@ar v1_1 -> nts CE@ar (an dig re-reco'd under ar)"""
    return {
        'sim.mu2e.MuminusStopsCat.MDC2025ac.art': {'n': 1, 'children': ['dts.mu2e.CeEndpoint.MDC2025ap.art']},
        'dts.mu2e.CeEndpoint.MDC2025ap.art': {'n': 1000, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AU}.art']},
        f'dig.mu2e.CeEndpointOnSpill.{AU}.art': {'n': 100, 'children': [f'mcs.mu2e.CeEndpointOnSpill.{AU}.art',
                                                                        f'log.mu2e.CeEndpointOnSpill.{AU}.log']},
        f'log.mu2e.CeEndpointOnSpill.{AU}.log': {'n': 100, 'children': []},
        f'mcs.mu2e.CeEndpointOnSpill.{AU}.art': {'n': 100, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AU}.root',
                                                                        f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']},
        f'nts.mu2e.CeEndpointOnSpill.{AU}.root': {'n': 100, 'children': []},
        f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root': {'n': 100, 'children': []},
        f'dig.mu2e.CeEndpointOnSpill.{AN}.art': {'n': 50, 'children': [f'mcs.mu2e.CeEndpointOnSpill.{AR}.art']},
        f'mcs.mu2e.CeEndpointOnSpill.{AR}.art': {'n': 50, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AR}.root']},
        f'nts.mu2e.CeEndpointOnSpill.{AR}.root': {'n': 50, 'children': []},
    }


class TestRootMatches(unittest.TestCase):
    def test_percent_is_glob(self):
        self.assertTrue(root_matches('dig.mu2e.%.MDC2025au_%.art', f'dig.mu2e.CeEndpointOnSpill.{AU}.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2025au_%.art', f'dig.mu2e.CeEndpointOnSpill.{AN}.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2025au_%.art', f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'))


class TestBuildCatalog(unittest.TestCase):
    def setUp(self):
        self.src = FakeSource(_small_graph())
        self.epochs = {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}

    def test_members_are_closure_below_roots_without_log(self):
        cat = build_catalog(self.epochs, self.src, ['MDC2025'])
        names = set(cat.members)
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', names)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', names)
        self.assertIn(f'mcs.mu2e.CeEndpointOnSpill.{AR}.art', names)
        self.assertNotIn(f'log.mu2e.CeEndpointOnSpill.{AU}.log', names)
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'].epoch, 'MDC2025an')

    def test_parents_split_into_members_and_inputs(self):
        cat = build_catalog(self.epochs, self.src, ['MDC2025'])
        dig = cat.members[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertEqual(dig.parents, set())
        self.assertEqual(dig.inputs, {'dts.mu2e.CeEndpoint.MDC2025ap.art'})
        nts = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']
        self.assertEqual(nts.parents, {f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'})

    def test_inputs_walk_up_and_record_descendants(self):
        cat = build_catalog(self.epochs, self.src, ['MDC2025'])
        self.assertIn('sim.mu2e.MuminusStopsCat.MDC2025ac.art', cat.inputs)
        dts = cat.inputs['dts.mu2e.CeEndpoint.MDC2025ap.art']
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', dts['descendants'])
        cat_in = cat.inputs['sim.mu2e.MuminusStopsCat.MDC2025ac.art']
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', cat_in['descendants'])

    def test_dig_matching_no_root_is_unclaimed(self):
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au')}, self.src, ['MDC2025'])
        self.assertEqual(cat.unclaimed_digs, {'MDC2025': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']})

    def test_unparseable_dsconf_reported_not_dropped_silently(self):
        g = _small_graph()
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append('nts.mu2e.CeEndpointOnSpill.weird.root')
        g['nts.mu2e.CeEndpointOnSpill.weird.root'] = {'n': 3, 'children': []}
        cat = build_catalog(self.epochs, FakeSource(g), ['MDC2025'])
        self.assertEqual([n for n, _ in cat.unparseable], ['nts.mu2e.CeEndpointOnSpill.weird.root'])
        self.assertNotIn('nts.mu2e.CeEndpointOnSpill.weird.root', cat.members)

    def test_pins_mark_excluded_and_held(self):
        e = _epoch('MDC2025au', pins={
            'exclude': [{'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'calo bug'}],
            'hold': [{'dataset': f'mcs.mu2e.CeEndpointOnSpill.{AU}.art', 'reason': 'paper 2026'}]})
        cat = build_catalog({'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')}, self.src, ['MDC2025'])
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].excluded, 'calo bug')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].hold, 'paper 2026')

    def test_frozen_epoch_holds_every_member(self):
        e = _epoch('MDC2025an', status='frozen')
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e}, self.src, ['MDC2025'])
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AR}.root'].hold, 'epoch MDC2025an is frozen')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k "TestRootMatches or TestBuildCatalog"`
Expected: FAIL with `ImportError` on `utils.epochs.graph`

- [ ] **Step 3: Write the module**

`utils/epochs/graph.py`:
```python
"""Members and inputs of every epoch, from SAM parentage.

Members = closure of the roots walking children (decision 2, rule 1).
Inputs = everything reached walking parents from a dig root, up to
`input_depth` levels (decision 13). A dataset belongs to exactly one
epoch: the one whose root its dig matched.
"""
import fnmatch
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from utils.epochs.dsconf import DsconfKey, DsconfParseError, parse_dsconf
from utils.epochs.epoch_files import EpochFile
from utils.job_common import Mu2eName


def root_matches(root: str, dataset: str) -> bool:
    """SAM '%' wildcard semantics on a 5-field dataset name."""
    return fnmatch.fnmatchcase(dataset, root.replace('%', '*'))


@dataclass
class Member:
    name: str
    tier: str
    desc: str
    dsconf: str
    key: DsconfKey
    epoch: str
    nfiles: int
    parents: Set[str] = field(default_factory=set)
    inputs: Set[str] = field(default_factory=set)
    status: str = ''
    hold: str = ''
    excluded: str = ''


@dataclass
class Catalog:
    epochs: Dict[str, EpochFile]
    members: Dict[str, Member] = field(default_factory=dict)
    inputs: Dict[str, Dict] = field(default_factory=dict)
    unparseable: List[Tuple[str, str]] = field(default_factory=list)
    foreign: Set[str] = field(default_factory=set)
    unclaimed_digs: Dict[str, List[str]] = field(default_factory=dict)


def _claim(epochs: Dict[str, EpochFile], dig: str):
    hits = [e.name for e in epochs.values() if any(root_matches(r, dig) for r in e.roots)]
    if len(hits) > 1:
        raise ValueError(f'{dig} matches roots of more than one epoch: {hits}')
    return hits[0] if hits else None


def _make_member(name: str, nfiles: int, epoch: str, cat: Catalog):
    n = Mu2eName.parse(name)
    try:
        key = parse_dsconf(n.dsconf)
    except DsconfParseError as exc:
        cat.unparseable.append((name, str(exc)))
        return None
    m = Member(name=name, tier=n.tier, desc=n.description, dsconf=n.dsconf,
               key=key, epoch=epoch, nfiles=nfiles)
    cat.members[name] = m
    return m


def _walk_down(root_member: Member, source, cat: Catalog):
    """Breadth-first over children. A child already claimed by another
    walk keeps its first epoch (it cannot happen for a well-formed SAM
    graph: a dig has one dsconf) but is still linked as a parent edge."""
    frontier = [root_member]
    while frontier:
        parent = frontier.pop()
        for child, n in source.children(parent.name).items():
            m = cat.members.get(child)
            if m is None:
                m = _make_member(child, n, parent.epoch, cat)
                if m is None:
                    continue
                frontier.append(m)
                # Non-member parents of a downstream member (today: none,
                # since log/cnf/etc are dropped; after ADR 0003's code plan:
                # the cnf). One isparentof query per member below dig.
                for p in source.parents(child):
                    if p not in cat.members and p != parent.name:
                        m.inputs.add(p)
            m.parents.add(parent.name)


def _walk_up(dig: Member, source, cat: Catalog, depth: int):
    frontier = [(dig.name, 0)]
    while frontier:
        child, level = frontier.pop()
        if level >= depth:
            continue
        for p, n in source.parents(child).items():
            if p in cat.members:
                # a member above a dig (a dig-of-dig): a parent edge, never
                # an input
                if child in cat.members:
                    cat.members[child].parents.add(p)
                continue
            rec = cat.inputs.setdefault(p, {'nfiles': n, 'parents': set(), 'descendants': set()})
            rec['descendants'].add(dig.name)
            if child in cat.inputs:
                cat.inputs[child]['parents'].add(p)
            else:
                cat.members[child].inputs.add(p)
            frontier.append((p, level + 1))


def build_catalog(epochs: Dict[str, EpochFile], source, families: List[str],
                  input_depth: int = 3) -> Catalog:
    cat = Catalog(epochs=dict(epochs))
    for family in families:
        for dig, n in sorted(source.dig_datasets(family).items()):
            epoch = _claim(epochs, dig)
            if epoch is None:
                cat.unclaimed_digs.setdefault(family, []).append(dig)
                continue
            m = _make_member(dig, n, epoch, cat)
            if m is None:
                continue
            _walk_down(m, source, cat)
            _walk_up(m, source, cat, input_depth)
    cat.foreign |= getattr(source, 'foreign', set())
    # a parent recorded as an input before its own walk made it a member
    for m in cat.members.values():
        m.inputs -= set(cat.members)
    _apply_pins(cat)
    return cat


def _apply_pins(cat: Catalog):
    for e in cat.epochs.values():
        for pin in e.pins['exclude']:
            m = cat.members.get(pin['dataset'])
            if m is not None:
                m.excluded = pin['reason']
        for pin in e.pins['hold']:
            m = cat.members.get(pin['dataset'])
            if m is not None:
                m.hold = pin['reason']
        if e.status == 'frozen':
            for m in cat.members.values():
                if m.epoch == e.name and not m.hold:
                    m.hold = f'epoch {e.name} is frozen'
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `35 passed`

- [ ] **Step 5: Commit**

```bash
git add utils/epochs/graph.py test/test_epochs.py
git commit -m "feat(epochs): build members and inputs from roots by SAM parentage

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 5: status — current, stale, superseded

**Files:**
- Create: `utils/epochs/status.py`
- Modify: `test/test_epochs.py` (append)

**Interfaces:**
- Consumes: `Catalog`, `Member`.
- Produces: `assign_status(cat: Catalog) -> Catalog` (mutates `member.status`), `group_key(m) -> Tuple[str, str, str, Optional[str]]` = `(family, tier, desc, purpose)`, `groups(cat) -> Dict[key, List[Member]]` (excluded members omitted), `STATUSES = ('current', 'stale', 'superseded')`.

- [ ] **Step 1: Append the tests**

```python
from utils.epochs.status import assign_status, group_key, groups, STATUSES


def _cat(graph=None, epochs=None):
    src = FakeSource(graph or _small_graph())
    eps = epochs or {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}
    return assign_status(build_catalog(eps, src, ['MDC2025']))


class TestStatus(unittest.TestCase):
    def test_newest_sibling_current_older_superseded(self):
        cat = _cat()
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'current')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'superseded')

    def test_status_crosses_epochs_within_family(self):
        cat = _cat()
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'].status, 'superseded')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'current')
        # its dig at an is superseded by the au dig (same desc, purpose best)
        self.assertEqual(cat.members[f'dig.mu2e.CeEndpointOnSpill.{AN}.art'].status, 'superseded')

    def test_stale_when_parent_moved_on_and_no_replacement(self):
        g = _small_graph()
        # re-reco'd mcs at -001 with no nts yet: old nts become stale, not superseded
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'] = {'n': 100, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'].status, 'current')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'superseded')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'stale')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'superseded')

    def test_parent_not_in_group_key(self):
        # same desc/tier/purpose, different parents: newest still wins (decision 3)
        g = _small_graph()
        g[f'dig.mu2e.CeEndpointOnSpill.{AN}.art']['children'].append(f'mcs.mu2e.CeEndpointOnSpill.{AU3}.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU3}.art'] = {'n': 5, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU3}.art'].status, 'superseded')

    def test_purpose_makes_siblings_not_versions(self):
        g = _small_graph()
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append('mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art')
        g['mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art'] = {'n': 100, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members['mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art'].status, 'current')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'current')

    def test_excluded_member_never_competes_and_has_no_status(self):
        e = _epoch('MDC2025au', pins={'exclude': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', 'reason': 'bad build'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'excluded')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'current')

    def test_order_pin_overrides_winner(self):
        e = _epoch('MDC2025au', pins={'order': [
            {'tier': 'nts', 'desc': 'CeEndpointOnSpill', 'purpose': 'best',
             'winner': AU, 'reason': '-001 is a 40-file top-up'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'current')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'superseded')

    def test_hold_does_not_change_status(self):
        e = _epoch('MDC2025au', pins={'hold': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'paper'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        m = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root']
        self.assertEqual((m.status, m.hold), ('superseded', 'paper'))

    def test_group_key_shape(self):
        cat = _cat()
        m = cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertEqual(group_key(m), ('MDC2025', 'mcs', 'CeEndpointOnSpill', 'best'))
        self.assertIn(('MDC2025', 'nts', 'CeEndpointOnSpill', 'best'), groups(cat))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k TestStatus`
Expected: FAIL with `ImportError` on `utils.epochs.status`

- [ ] **Step 3: Write the module**

`utils/epochs/status.py`:
```python
"""Derive current / stale / superseded (decisions 3, 4, 5, 7).

- Group key = (family, tier, desc, purpose). Parent is NOT in the key.
- Within a group the highest DsconfKey.sort_key() is the winner, unless
  an `order` pin names one.
- Winner is `current` if every member parent is current, else `stale`.
- Non-winners are `superseded`.
- Excluded members get status `excluded` and never compete.
- Holds never touch status.
Members are visited in topological order over member-parent edges so a
parent's status is final before its children are judged.
"""
from typing import Dict, List, Optional, Tuple

from utils.epochs.graph import Catalog, Member

STATUSES = ('current', 'stale', 'superseded')
GroupKey = Tuple[str, str, str, Optional[str]]


def group_key(m: Member) -> GroupKey:
    return (m.key.family, m.tier, m.desc, m.key.purpose)


def groups(cat: Catalog) -> Dict[GroupKey, List[Member]]:
    out: Dict[GroupKey, List[Member]] = {}
    for m in cat.members.values():
        if m.excluded:
            continue
        out.setdefault(group_key(m), []).append(m)
    for members in out.values():
        members.sort(key=lambda m: m.key.sort_key(), reverse=True)
    return out


def _order_pins(cat: Catalog) -> Dict[GroupKey, str]:
    pins = {}
    for e in cat.epochs.values():
        for p in e.pins['order']:
            pins[(e.family, p['tier'], p['desc'], p['purpose'])] = p['winner']
    return pins


def _winners(cat: Catalog) -> Dict[str, bool]:
    pins = _order_pins(cat)
    win: Dict[str, bool] = {}
    for key, members in groups(cat).items():
        chosen = members[0]
        if key in pins:
            named = [m for m in members if m.dsconf == pins[key]]
            if not named:
                raise ValueError(f'order pin for {key} names {pins[key]!r}, '
                                 f'which is not among {[m.dsconf for m in members]}')
            chosen = named[0]
        for m in members:
            win[m.name] = (m is chosen)
    return win


def _topological(cat: Catalog) -> List[Member]:
    """Parents before children. Member-parent edges only."""
    done, order = set(), []
    def visit(m: Member, stack):
        if m.name in done:
            return
        if m.name in stack:
            raise ValueError(f'parentage cycle at {m.name}')
        stack.add(m.name)
        for p in sorted(m.parents):
            visit(cat.members[p], stack)
        stack.discard(m.name)
        done.add(m.name)
        order.append(m)
    for m in sorted(cat.members.values(), key=lambda m: m.name):
        visit(m, set())
    return order


def assign_status(cat: Catalog) -> Catalog:
    win = _winners(cat)
    for m in _topological(cat):
        if m.excluded:
            m.status = 'excluded'
            continue
        if not win[m.name]:
            m.status = 'superseded'
            continue
        parents = [cat.members[p] for p in m.parents if not cat.members[p].excluded]
        m.status = 'current' if all(p.status == 'current' for p in parents) else 'stale'
    return cat
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `44 passed`

- [ ] **Step 5: Commit**

```bash
git add utils/epochs/status.py test/test_epochs.py
git commit -m "feat(epochs): current/stale/superseded across epochs, holds separate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 6: reports — gaps and retire list in Ray's format

**Files:**
- Create: `utils/epochs/reports.py`
- Modify: `test/test_epochs.py` (append)

**Interfaces:**
- Consumes: `Catalog` after `assign_status`; `groups`.
- Produces:
  - `EXPECTED_TIERS = ('mcs', 'nts')`
  - `gaps(cat) -> List[Dict]` entries `{'kind': 'missing'|'stale', 'epoch', 'desc', 'tier', 'dataset' (stale only), 'note'}`; frozen and retired epochs produce no `missing` entries.
  - `retire(cat, reasons_fn=None) -> List[Dict]` entries `{'dataset', 'nfiles', 'kind': 'member'|'input', 'reason', 'children': List[str]}`; excludes held members; includes every non-held member of a `retired` epoch; inputs by decision 13 with the letters guard.
  - `purge_lines(entries) -> List[str]` in Ray's format: `DELETE <created> <accessed> <YES|NO> <nfiles> <dataset> <children|NONE> # <reason>` with `created` and `accessed` both `-`.
  - `lookup(cat, dataset) -> Dict` summary of one member or input.

- [ ] **Step 1: Append the tests**

```python
from utils.epochs.reports import gaps, retire, purge_lines, lookup, EXPECTED_TIERS


class TestGaps(unittest.TestCase):
    def test_missing_tier_and_stale_reported(self):
        g = _small_graph()
        g[f'dig.mu2e.DIOtail.{AU}.art'] = {'n': 10, 'children': []}      # dig with no mcs
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'] = {'n': 100, 'children': []}   # re-reco, no nts yet
        cat = _cat(g)
        out = gaps(cat)
        kinds = {(x['kind'], x['desc'], x['tier']) for x in out}
        self.assertIn(('missing', 'DIOtail', 'mcs'), kinds)
        self.assertIn(('missing', 'DIOtail', 'nts'), kinds)
        self.assertIn(('stale', 'CeEndpointOnSpill', 'nts'), kinds)

    def test_not_expected_pin_silences_missing(self):
        g = _small_graph()
        g[f'dig.mu2e.ensembleMDS3b.{AU}.art'] = {'n': 10, 'children': []}
        e = _epoch('MDC2025au', pins={'not_expected': [
            {'desc': 'ensembleMDS3b', 'tiers': ['mcs', 'nts'], 'reason': 'kept at dig'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertFalse([x for x in gaps(cat) if x['desc'] == 'ensembleMDS3b'])

    def test_frozen_epoch_reports_no_missing(self):
        g = _small_graph()
        g[f'dig.mu2e.DIOtail.{AN}.art'] = {'n': 10, 'children': []}
        e = _epoch('MDC2025an', status='frozen')
        cat = _cat(g, {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e})
        self.assertFalse([x for x in gaps(cat) if x['desc'] == 'DIOtail'])

    def test_nts_expected_only_where_mcs_exists_in_family(self):
        # a superseded mcs still counts as "reached mcs"; nts missing is judged
        # against the CURRENT mcs desc, so a stale-free family reports nothing
        cat = _cat()
        self.assertEqual([x for x in gaps(cat) if x['kind'] == 'missing'], [])


class TestRetire(unittest.TestCase):
    def test_superseded_not_held_listed_with_children(self):
        cat = _cat()
        names = {x['dataset']: x for x in retire(cat)}
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', names)
        mcs_ar = names[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art']
        self.assertEqual(mcs_ar['children'], [f'nts.mu2e.CeEndpointOnSpill.{AR}.root'])
        self.assertIn('superseded by', mcs_ar['reason'])
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', names)

    def test_held_member_kept_off_the_list(self):
        e = _epoch('MDC2025au', pins={'hold': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'paper'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', {x['dataset'] for x in retire(cat)})

    def test_retired_epoch_lists_every_member(self):
        e = _epoch('MDC2025an', status='retired')
        cat = _cat(epochs={'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e})
        names = {x['dataset']: x['reason'] for x in retire(cat)}
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AN}.art', names)
        self.assertIn('epoch MDC2025an is retired', names[f'dig.mu2e.CeEndpointOnSpill.{AN}.art'])

    def test_input_with_no_live_descendant_listed_with_letters_guard(self):
        g = _small_graph()
        # an old dts@af whose only dig is superseded
        g['dts.mu2e.CeEndpoint.MDC2025af.art'] = {'n': 500, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        # a fresh dts@av with no dig yet: newer letters than any epoch -> never listed
        g['dts.mu2e.Fresh.MDC2025av.art'] = {'n': 5, 'children': []}
        cat = _cat(g)
        out = {x['dataset']: x for x in retire(cat)}
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025af.art', out)
        self.assertEqual(out['dts.mu2e.CeEndpoint.MDC2025af.art']['kind'], 'input')
        self.assertNotIn('dts.mu2e.CeEndpoint.MDC2025ap.art', out)     # feeds a current dig
        self.assertNotIn('dts.mu2e.Fresh.MDC2025av.art', out)

    def test_purge_line_format(self):
        cat = _cat()
        entry = [x for x in retire(cat) if x['dataset'] == f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'][0]
        line = purge_lines([entry])[0]
        cols = line.split('#')[0].split()
        self.assertEqual(cols[:5], ['DELETE', '-', '-', 'YES', '50'])
        self.assertEqual(cols[5], f'mcs.mu2e.CeEndpointOnSpill.{AR}.art')
        self.assertEqual(cols[6], f'nts.mu2e.CeEndpointOnSpill.{AR}.root')
        self.assertIn('superseded by', line.split('#', 1)[1])
        leaf = [x for x in retire(cat) if x['dataset'] == f'nts.mu2e.CeEndpointOnSpill.{AR}.root'][0]
        self.assertIn(' NO ', purge_lines([leaf])[0])
        self.assertIn(' NONE ', purge_lines([leaf])[0])


class TestLookup(unittest.TestCase):
    def test_member_and_input_and_unknown(self):
        cat = _cat()
        m = lookup(cat, f'nts.mu2e.CeEndpointOnSpill.{AU}.root')
        self.assertEqual((m['kind'], m['status'], m['epoch']), ('member', 'superseded', 'MDC2025au'))
        self.assertEqual(m['superseded_by'], f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root')
        i = lookup(cat, 'dts.mu2e.CeEndpoint.MDC2025ap.art')
        self.assertEqual(i['kind'], 'input')
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', i['descendants'])
        self.assertEqual(lookup(cat, 'nope.mu2e.x.y.art')['kind'], 'unknown')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k "TestGaps or TestRetire or TestLookup"`
Expected: FAIL with `ImportError` on `utils.epochs.reports`

- [ ] **Step 3: Write the module**

`utils/epochs/reports.py`:
```python
"""Derived products: gaps, retire list (Ray's purge_proposal format), lookup.

Decisions 11, 12, 13. Nothing here reads a clock.
"""
from typing import Dict, List, Optional

from utils.epochs.dsconf import parse_dsconf
from utils.epochs.graph import Catalog, Member
from utils.epochs.status import group_key, groups

EXPECTED_TIERS = ('mcs', 'nts')
# The desc at mcs/nts is the dig desc (reco keeps the desc). A reco that
# changes desc (-KK, -CH) is a different physics line by convention.


def _winner_of(cat: Catalog, m: Member) -> Optional[Member]:
    """The current-or-stale sibling that beats `m`, else None."""
    for cand in groups(cat).get(group_key(m), []):
        if cand.status in ('current', 'stale'):
            return cand if cand.name != m.name else None
    return None


def _not_expected(cat: Catalog, epoch: str, desc: str, tier: str) -> bool:
    for p in cat.epochs[epoch].pins['not_expected']:
        if p['desc'] == desc and tier in p['tiers']:
            return True
    return False


def gaps(cat: Catalog) -> List[Dict]:
    out = []
    present = {}   # (family, desc, tier) -> True if any current/stale member
    for m in cat.members.values():
        if m.status in ('current', 'stale'):
            present[(m.key.family, m.desc, m.tier)] = True
    for m in sorted(cat.members.values(), key=lambda m: m.name):
        if m.tier == 'dig' and m.status in ('current', 'stale') \
                and cat.epochs[m.epoch].status == 'current':
            for tier in EXPECTED_TIERS:
                if present.get((m.key.family, m.desc, tier)):
                    continue
                if _not_expected(cat, m.epoch, m.desc, tier):
                    continue
                out.append({'kind': 'missing', 'epoch': m.epoch, 'desc': m.desc,
                            'tier': tier, 'dataset': '',
                            'note': f'{m.name} has no current {tier}'})
        if m.status == 'stale':
            parents = ', '.join(sorted(m.parents))
            out.append({'kind': 'stale', 'epoch': m.epoch, 'desc': m.desc, 'tier': m.tier,
                        'dataset': m.name, 'note': f'parent moved on: {parents}'})
    return out


def _member_children(cat: Catalog, name: str) -> List[str]:
    return sorted(c.name for c in cat.members.values() if name in c.parents)


def _newest_epoch_key(cat: Catalog, family: str):
    keys = [parse_dsconf(e.name + '_x_v0_0') for e in cat.epochs.values() if e.family == family]
    return max((k.sort_key() for k in keys), default=None)


def retire(cat: Catalog) -> List[Dict]:
    out = []
    for m in sorted(cat.members.values(), key=lambda m: m.name):
        if m.hold or m.status == 'excluded':
            continue
        epoch_status = cat.epochs[m.epoch].status
        if m.status == 'superseded':
            by = _winner_of(cat, m)
            reason = f'superseded by {by.name}' if by else 'superseded'
        elif epoch_status == 'retired':
            reason = f'epoch {m.epoch} is retired'
        else:
            continue
        out.append({'dataset': m.name, 'nfiles': m.nfiles, 'kind': 'member',
                    'reason': reason, 'children': _member_children(cat, m.name)})
    live = {m.name for m in cat.members.values() if m.status in ('current', 'stale')}
    for name, rec in sorted(cat.inputs.items()):
        if any(d in live for d in rec['descendants']):
            continue
        key = parse_dsconf(name.split('.')[3]).sort_key()
        newest = _newest_epoch_key(cat, parse_dsconf(name.split('.')[3]).family)
        if newest is not None and key[0] > newest[0]:
            continue          # newer letters than any epoch: awaiting its mix
        out.append({'dataset': name, 'nfiles': rec['nfiles'], 'kind': 'input',
                    'reason': 'no current or stale descendant',
                    'children': sorted(rec['descendants'])})
    return out


def purge_lines(entries: List[Dict]) -> List[str]:
    lines = []
    for e in entries:
        has = 'YES' if e['children'] else 'NO'
        kids = ','.join(e['children']) if e['children'] else 'NONE'
        lines.append(f"DELETE - - {has} {e['nfiles']} {e['dataset']} {kids} # {e['reason']}")
    return lines


def lookup(cat: Catalog, dataset: str) -> Dict:
    m = cat.members.get(dataset)
    if m is not None:
        by = _winner_of(cat, m) if m.status == 'superseded' else None
        return {'kind': 'member', 'dataset': m.name, 'epoch': m.epoch, 'tier': m.tier,
                'desc': m.desc, 'status': m.status, 'hold': m.hold, 'excluded': m.excluded,
                'nfiles': m.nfiles, 'parents': sorted(m.parents), 'inputs': sorted(m.inputs),
                'children': _member_children(cat, m.name),
                'superseded_by': by.name if by else ''}
    rec = cat.inputs.get(dataset)
    if rec is not None:
        return {'kind': 'input', 'dataset': dataset, 'nfiles': rec['nfiles'],
                'parents': sorted(rec['parents']), 'descendants': sorted(rec['descendants'])}
    return {'kind': 'unknown', 'dataset': dataset}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `54 passed`

- [ ] **Step 5: Commit**

```bash
git add utils/epochs/reports.py test/test_epochs.py
git commit -m "feat(epochs): gap report and retire list in purge_proposal format

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 7: generation from the cnf, cnf index, consistency report

**Files:**
- Create: `utils/epochs/generation.py`
- Modify: `utils/epochs/reports.py` (append `consistency`)
- Modify: `test/test_epochs.py` (append)

**Interfaces:**
- Consumes: `utils.jobquery.Mu2eJobPars` (`.setup()`, `.json_data` (raw `tbs.outfiles` templates), `._extract_member('mu2e.fcl')`), a source with `cnf_names()`, `local_path()`, `parents()`.
- Produces:
  - `Generation(musing, version, setup, fcl_sha256, dbservice, geometry, bfield, source, cnf)` NamedTuple; `source` ∈ `'parent'|'index'`.
  - `read_generation(cnf_path, cnf_name, source_kind) -> Generation`.
  - `build_cnf_index(source, existing: Dict) -> Dict` — dataset → cnf, read straight from each cnf's `tbs.outfiles` templates (never `job_outputs()`, which needs a sequencer): a template without `{desc}` is an explicit claim on its 5-field dataset; a template with `{desc}` (generic draining cnf, e.g. `nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root`) is a generic claim stored under `'__generic__'[f'{tier}.{dsconf}']`. Explicit beats generic. Skips cnfs already in `existing['__indexed__']`; a cnf SAM cannot locate is listed under `'__unlocatable__'`.
  - `cnf_for(member, cat, index) -> Optional[Tuple[str, str]]` = `(cnf_name, 'parent'|'index')`.
  - `generations(cat, source, index) -> Dict[str, Optional[Generation]]` for every current/stale member; `None` means "no cnf found", reported, never guessed.
  - `reports.consistency(cat, gens) -> List[Dict]` entries `{'family', 'tier', 'generation': str, 'count', 'descs': [...], 'minority': bool}`.

- [ ] **Step 1: Append the tests (a real cnf tarball is built in a temp dir)**

```python
import hashlib
import tarfile
from utils.epochs.generation import (Generation, read_generation, build_cnf_index, cnf_for,
                                     generations)
from utils.epochs.reports import consistency


def _make_cnf(d, name, setup, outputs, fcl='services.DbService.version: "v1_5"\n'
              'services.GeometryService.inputFile: "Offline/Mu2eG4/geom/geom_run1_a.txt"\n'
              'services.GeometryService.bfgeomFile: "Offline/Mu2eG4/geom/bfgeom_v01.txt"\n'):
    """A minimal cnf tarball: jobpars.json (setup + tbs.outfiles) and mu2e.fcl.
    `outputs` are tbs.outfiles TEMPLATES exactly as mu2ejobdef writes them:
    'mcs.mu2e.X.MDC2025au_best_v1_5.sequencer.art' or, generic,
    'nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root'."""
    path = os.path.join(d, name)
    jobpars = {'setup': setup, 'tbs': {'outfiles': {f'out{i}': t for i, t in enumerate(outputs)}},
               'jobname': name, 'code': ''}
    with tarfile.open(path, 'w') as tar:
        for member, payload in (('jobpars.json', json.dumps(jobpars)), ('mu2e.fcl', fcl)):
            p = os.path.join(d, member)
            with open(p, 'w') as f:
                f.write(payload)
            tar.add(p, arcname=member)
    return path


SETUP_A = '/cvmfs/mu2e.opensciencegrid.org/Musings/AnalysisMDC2025/v02_00_00/setup.sh'
SETUP_B = '/cvmfs/mu2e.opensciencegrid.org/Musings/AnalysisMDC2025/v02_01_00/setup.sh'


class TestGeneration(unittest.TestCase):
    def test_read_generation_fields(self):
        d = _tmpdir()
        p = _make_cnf(d, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', SETUP_B, [])
        g = read_generation(p, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', 'index')
        self.assertEqual((g.musing, g.version), ('AnalysisMDC2025', 'v02_01_00'))
        self.assertEqual(g.dbservice, 'v1_5')
        self.assertTrue(g.geometry.endswith('geom_run1_a.txt'))
        self.assertTrue(g.bfield.endswith('bfgeom_v01.txt'))
        self.assertEqual(len(g.fcl_sha256), 64)
        self.assertEqual((g.source, g.cnf), ('index', 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar'))

    def test_unrecognized_setup_path_raises(self):
        d = _tmpdir()
        p = _make_cnf(d, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', '/some/where/setup.sh', [])
        with self.assertRaises(ValueError):
            read_generation(p, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', 'index')

    def test_index_from_declared_outputs_and_generic_dsconf(self):
        d = _tmpdir()
        explicit = _make_cnf(d, 'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar', SETUP_A,
                             [f'mcs.mu2e.CeEndpointOnSpill.{AU}.sequencer.art',
                              f'log.mu2e.CeEndpointOnSpill-reco.{AU}.sequencer.log'])
        generic = _make_cnf(d, 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar', SETUP_B,
                            ['nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root'])
        src = FakeSource(_small_graph(), cnfs={
            'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar': explicit,
            'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar': generic,
            'cnf.mu2e.Lost.MDC2025au_best_v1_5.0.tar': ''})
        idx = build_cnf_index(src, existing={})
        self.assertEqual(idx[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'],
                         'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar')
        self.assertNotIn(f'log.mu2e.CeEndpointOnSpill-reco.{AU}.log', idx)   # log tier never indexed
        self.assertEqual(idx['__generic__']['nts.MDC2025au_best_v1_5-001'],
                         'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar')
        self.assertEqual(idx['__unlocatable__'], ['cnf.mu2e.Lost.MDC2025au_best_v1_5.0.tar'])
        self.assertEqual(sorted(idx['__indexed__']), [k for k, v in sorted(src.cnfs.items()) if v])

    def test_index_skips_already_indexed_cnfs(self):
        src = FakeSource(_small_graph(), cnfs={'cnf.mu2e.A.MDC2025au_best_v1_5.0.tar': ''})
        idx = build_cnf_index(src, existing={'__indexed__': ['cnf.mu2e.A.MDC2025au_best_v1_5.0.tar'],
                                             '__generic__': {}, '__unlocatable__': []})
        self.assertEqual(idx['__unlocatable__'], [])

    def test_cnf_for_prefers_parent_then_index(self):
        g = _small_graph()
        g['cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar'] = {'n': 1, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']}
        cat = _cat(g)
        idx = {'__generic__': {f'nts.{AU}': 'cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar'}, '__indexed__': [], '__unlocatable__': [],
               f'mcs.mu2e.CeEndpointOnSpill.{AU}.art': 'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar'}
        self.assertEqual(cnf_for(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'], cat, idx),
                         ('cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar', 'parent'))
        self.assertEqual(cnf_for(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'], cat, idx),
                         ('cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar', 'index'))
        self.assertEqual(cnf_for(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'], cat, idx),
                         ('cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar', 'index'))
        self.assertIsNone(cnf_for(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'], cat, idx))

    def test_consistency_names_minority_descs(self):
        d = _tmpdir()
        a = _make_cnf(d, 'cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar', SETUP_A,
                      ['nts.mu2e.{desc}.MDC2025au_best_v1_5.sequencer.root'])
        b = _make_cnf(d, 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar', SETUP_B,
                      ['nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root'])
        g = _small_graph()
        g[f'mcs.mu2e.DIO.{AU}.art'] = {'n': 10, 'children': [f'nts.mu2e.DIO.{AU}.root']}
        g[f'dig.mu2e.DIO.{AU}.art'] = {'n': 10, 'children': [f'mcs.mu2e.DIO.{AU}.art']}
        g[f'nts.mu2e.DIO.{AU}.root'] = {'n': 10, 'children': []}
        src = FakeSource(g, cnfs={'cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar': a,
                                  'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar': b})
        cat = assign_status(build_catalog({'MDC2025au': _epoch('MDC2025au'),
                                           'MDC2025an': _epoch('MDC2025an')}, src, ['MDC2025']))
        idx = build_cnf_index(src, existing={})
        gens = generations(cat, src, idx)
        rows = [r for r in consistency(cat, gens) if r['tier'] == 'nts']
        by_gen = {r['generation']: r for r in rows}
        self.assertEqual(by_gen['AnalysisMDC2025/v02_01_00']['descs'], ['CeEndpointOnSpill'])
        self.assertEqual(by_gen['AnalysisMDC2025/v02_00_00']['descs'], ['DIO'])
        self.assertIn('unknown', by_gen)   # the mcs/dig members have no cnf -> reported
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k TestGeneration`
Expected: FAIL with `ImportError` on `utils.epochs.generation`

- [ ] **Step 3: Write the generation module**

`utils/epochs/generation.py`:
```python
"""What code made a dataset (decisions 6 and 17).

Two routes to a member's cnf, tried in this order and REPORTED:
  parent — the cnf is a declared SAM parent (ADR 0003, new outputs)
  index  — data/epochs/cnf_index.json, built once from every cnf's
           tbs.outfiles templates: a template without `{desc}` claims its
           dataset; a template with `{desc}` (generic draining cnf) claims
           every dataset of that tier and dsconf that no explicit cnf
           claims.
No third route. A member with neither is reported as generation None.
"""
import hashlib
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from utils.epochs.graph import Catalog, Member
from utils.job_common import Mu2eName

_SETUP_RE = re.compile(r'/Musings/([^/]+)/([^/]+)/setup\.sh$')
_FCL_KEYS = {
    'dbservice': re.compile(r'DbService\.version\s*:\s*"([^"]*)"'),
    'geometry': re.compile(r'GeometryService\.inputFile\s*:\s*"([^"]*)"'),
    'bfield': re.compile(r'bfgeomFile\s*:\s*"([^"]*)"'),
}
DROP_TIERS = frozenset({'log', 'cnf', 'etc'})


class Generation(NamedTuple):
    musing: str
    version: str
    setup: str
    fcl_sha256: str
    dbservice: str
    geometry: str
    bfield: str
    source: str
    cnf: str

    def label(self) -> str:
        return f'{self.musing}/{self.version}'


def read_generation(cnf_path: str, cnf_name: str, source_kind: str) -> Generation:
    from utils.jobquery import Mu2eJobPars
    jp = Mu2eJobPars(cnf_path)
    setup = jp.setup()
    m = _SETUP_RE.search(setup or '')
    if not m:
        raise ValueError(f'{cnf_name}: setup path is not a Musings setup.sh: {setup!r}')
    fcl = jp._extract_member('mu2e.fcl').decode('utf-8', 'replace')
    vals = {k: (rx.search(fcl).group(1) if rx.search(fcl) else '') for k, rx in _FCL_KEYS.items()}
    return Generation(musing=m.group(1), version=m.group(2), setup=setup,
                      fcl_sha256=hashlib.sha256(fcl.encode()).hexdigest(),
                      dbservice=vals['dbservice'], geometry=vals['geometry'],
                      bfield=vals['bfield'], source=source_kind, cnf=cnf_name)


def _empty_index() -> Dict:
    return {'__indexed__': [], '__generic__': {}, '__unlocatable__': []}


def output_claims(jobname: str, outfiles: Dict[str, str]):
    """(explicit datasets, generic keys) from tbs.outfiles templates.
    Templates are read raw — `job_outputs()` needs a sequencer, which a
    generic cnf cannot compute. `.owner.` and `.version.` are substituted
    from the cnf name, as job_outputs does."""
    n = Mu2eName.parse(jobname)
    explicit, generic = [], []
    for template in outfiles.values():
        t = template.replace('.owner.', f'.{n.owner}.').replace('.version.', f'.{n.dsconf}.')
        parts = t.split('.')
        if len(parts) != 6 or parts[0] in DROP_TIERS:
            continue           # /dev/null, relative paths, logs
        tier, owner, desc, dsconf, _seq, ext = parts
        if '{desc}' in desc:
            generic.append(f'{tier}.{dsconf}')
        else:
            explicit.append(f'{tier}.{owner}.{desc}.{dsconf}.{ext}')
    return explicit, generic


def build_cnf_index(source, existing: Dict) -> Dict:
    """dataset -> cnf name, from every cnf SAM knows. Idempotent: cnfs in
    existing['__indexed__'] are not re-read. Explicit claims are top-level
    keys; generic claims live under '__generic__'[f'{tier}.{dsconf}']."""
    from utils.jobquery import Mu2eJobPars
    idx = _empty_index()
    idx.update(existing or {})
    done = set(idx['__indexed__'])
    unloc = set(idx['__unlocatable__'])
    for cnf in source.cnf_names():
        if cnf in done:
            continue
        path = source.local_path(cnf)
        if not path:
            unloc.add(cnf)
            continue
        jp = Mu2eJobPars(path)
        explicit, generic = output_claims(cnf, jp.json_data.get('tbs', {}).get('outfiles', {}) or {})
        for ds in explicit:
            idx[ds] = cnf
        for key in generic:
            idx['__generic__'][key] = cnf
        done.add(cnf)
    idx['__indexed__'] = sorted(done)
    idx['__unlocatable__'] = sorted(unloc - done)
    return idx


def cnf_for(m: Member, cat: Catalog, index: Dict) -> Optional[Tuple[str, str]]:
    for p in sorted(m.inputs):
        if p.startswith('cnf.'):
            return p, 'parent'
    if m.name in index:
        return index[m.name], 'index'
    generic = index.get('__generic__', {}).get(f'{m.tier}.{m.dsconf}')
    if generic:
        return generic, 'index'
    return None


def generations(cat: Catalog, source, index: Dict) -> Dict[str, Optional[Generation]]:
    out: Dict[str, Optional[Generation]] = {}
    cache: Dict[str, Generation] = {}
    for m in cat.members.values():
        if m.status not in ('current', 'stale'):
            continue
        found = cnf_for(m, cat, index)
        if found is None:
            out[m.name] = None
            continue
        cnf, kind = found
        if cnf not in cache:
            path = source.local_path(cnf)
            if not path:
                out[m.name] = None
                continue
            cache[cnf] = read_generation(path, cnf, kind)
        out[m.name] = cache[cnf]
    return out
```

Note for the implementer: `source.parents()` in Task 3 drops the `cnf` tier, so a real SAM cnf parent never reaches `Member.inputs` today; the FakeSource does not drop it, which is how `test_cnf_for_prefers_parent_then_index` exercises the parent route. `cnf_for` checks `m.inputs` for a `cnf.` name because ADR 0003's code plan will make `source.py` keep 6-field `cnf.*.tar` parents (as tarball names, not grouped to a dataset) while still dropping log/etc. Do not change `DROP_TIERS` in `source.py` in this plan.

- [ ] **Step 4: Append `consistency` to `utils/epochs/reports.py`**

```python
def consistency(cat: Catalog, gens: Dict[str, Optional['Generation']]) -> List[Dict]:
    """Per (family, tier): how many current members sit on each generation,
    which descs are on the minority ones. 'unknown' = no cnf found."""
    table: Dict[tuple, Dict[str, List[str]]] = {}
    for m in cat.members.values():
        if m.status != 'current':
            continue
        g = gens.get(m.name)
        label = g.label() if g is not None else 'unknown'
        table.setdefault((m.key.family, m.tier), {}).setdefault(label, []).append(m.desc)
    out = []
    for (family, tier), by_gen in sorted(table.items()):
        biggest = max(len(v) for v in by_gen.values())
        for label, descs in sorted(by_gen.items()):
            out.append({'family': family, 'tier': tier, 'generation': label,
                        'count': len(descs), 'descs': sorted(descs),
                        'minority': len(descs) < biggest})
    return out
```

and add `from typing import Dict, List, Optional` is already present; add at the top of `reports.py`:
```python
from utils.epochs.generation import Generation  # noqa: F401  (type only)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `60 passed`

- [ ] **Step 6: Commit**

```bash
git add utils/epochs/generation.py utils/epochs/reports.py test/test_epochs.py
git commit -m "feat(epochs): generation from the cnf, cnf index, consistency report

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 8: publish — Ray's catalog shape plus our fields

**Files:**
- Create: `utils/epochs/publish.py`
- Modify: `test/test_epochs.py` (append)

**Interfaces:**
- Consumes: `Catalog` with statuses, `gens`, `gaps`, `retire`.
- Produces: `catalog_document(cat, gens, generated_at: str) -> dict` with keys `generated_at`, `epochs` (list of `{name, purpose, status, datasets: [names], members: [{name, tier, desc, status, hold, excluded, generation, cnf, superseded_by}]}`), `inputs`, `unparseable`, `foreign`, `unclaimed_digs`, `gaps`, `retire`; `write_catalog(doc, path)`.
- `datasets` per epoch = every member name, so Ray's `get_datasets_for_epoch` reads unchanged.

- [ ] **Step 1: Append the tests**

```python
from utils.epochs.publish import catalog_document, write_catalog


class TestPublish(unittest.TestCase):
    def test_ray_shape_and_our_fields(self):
        cat = _cat()
        doc = catalog_document(cat, gens={}, generated_at='2026-09-03T00:00:00Z')
        self.assertEqual(doc['generated_at'], '2026-09-03T00:00:00Z')
        names = {e['name']: e for e in doc['epochs']}
        self.assertEqual(set(names), {'MDC2025au', 'MDC2025an'})
        au = names['MDC2025au']
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', au['datasets'])
        member = [m for m in au['members'] if m['name'] == f'nts.mu2e.CeEndpointOnSpill.{AU}.root'][0]
        self.assertEqual(member['status'], 'superseded')
        self.assertEqual(member['superseded_by'], f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root')
        self.assertEqual(member['generation'], '')
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025ap.art', doc['inputs'])
        self.assertIsInstance(doc['gaps'], list)
        self.assertIsInstance(doc['retire'], list)

    def test_write_is_valid_json_and_deterministic(self):
        cat = _cat()
        doc = catalog_document(cat, gens={}, generated_at='x')
        d = _tmpdir()
        p = os.path.join(d, 'sim_catalog.json')
        write_catalog(doc, p)
        with open(p) as f:
            back = json.load(f)
        self.assertEqual(back['epochs'][0]['name'], 'MDC2025an')   # sorted by name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k TestPublish`
Expected: FAIL with `ImportError` on `utils.epochs.publish`

- [ ] **Step 3: Write the module**

`utils/epochs/publish.py`:
```python
"""The file Ray's sim-epochs server reads: {"epochs":[{"name","datasets"}]}
with our fields alongside (ADR 0004, decision 10). Recomputed every time;
`generated_at` is informational and the only clock in the package."""
import json
from typing import Dict, Optional

from utils.epochs.graph import Catalog
from utils.epochs.reports import gaps, lookup, retire


def catalog_document(cat: Catalog, gens: Dict, generated_at: str) -> dict:
    epochs = []
    for e in sorted(cat.epochs.values(), key=lambda e: e.name):
        members = [m for m in cat.members.values() if m.epoch == e.name]
        members.sort(key=lambda m: m.name)
        rows = []
        for m in members:
            g = gens.get(m.name)
            info = lookup(cat, m.name)
            rows.append({'name': m.name, 'tier': m.tier, 'desc': m.desc, 'status': m.status,
                         'hold': m.hold, 'excluded': m.excluded,
                         'generation': g.label() if g else '', 'cnf': g.cnf if g else '',
                         'superseded_by': info['superseded_by']})
        epochs.append({'name': e.name, 'purpose': e.purpose, 'status': e.status,
                       'roots': list(e.roots), 'datasets': [m.name for m in members],
                       'members': rows})
    return {
        'generated_at': generated_at,
        'epochs': epochs,
        'inputs': {k: {'nfiles': v['nfiles'], 'descendants': sorted(v['descendants'])}
                   for k, v in sorted(cat.inputs.items())},
        'unparseable': [list(x) for x in cat.unparseable],
        'foreign': sorted(cat.foreign),
        'unclaimed_digs': {k: sorted(v) for k, v in cat.unclaimed_digs.items()},
        'gaps': gaps(cat),
        'retire': retire(cat),
    }


def write_catalog(doc: dict, path: str) -> None:
    with open(path, 'w') as f:
        json.dump(doc, f, indent=1, sort_keys=False)
        f.write('\n')
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `62 passed`

- [ ] **Step 5: Commit**

```bash
git add utils/epochs/publish.py test/test_epochs.py
git commit -m "feat(epochs): publish catalog in the sim-epochs server shape

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 9: CLI and `bin/epochs`

**Files:**
- Create: `utils/epochs/cli.py`
- Create: `bin/epochs`
- Modify: `test/test_epochs.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv=None, source=None, now_fn=None) -> int` with verbs:
  - `propose [--family F ...]` — write a file for every dig letter family with none; prints the paths; exit 0.
  - `members [--family F] [--epoch E] [--tier T] [--status S] [--json]`
  - `gaps [--family F] [--json]`
  - `consistency [--family F] [--json]`
  - `retire [--family F] [--json]` — default output is purge lines.
  - `lookup DATASET [--json]`
  - `index-cnfs` — rebuild/extend `data/epochs/cnf_index.json`.
  - `publish --out PATH [--family F]`
  - common: `--epochs-dir` (default `<repo>/data/epochs`), `--input-depth` (default 3).
  - Every verb except `propose` loads EVERY epoch file and lets `build_catalog` discover the dig families from SAM (`families=None`); `--family` and `--epoch` filter the PRINTED rows only, never what is loaded, so status and retire are always judged on the complete catalog (ruling of 2026-09-03: a partial catalog proposed deleting MDC2025 stop catalogues that only unloaded MDC2025 digs used). `propose` takes explicit `--family` and refuses to run with none.
  - Exit codes: 0 ok; 2 usage/curation error (`EpochFileError`); 3 SAM/parse error.

- [ ] **Step 1: Append the tests**

```python
import io
import contextlib
from utils.epochs import cli as epochs_cli


def _run(argv, source, epochs_dir):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = epochs_cli.main(['--epochs-dir', epochs_dir] + argv, source=source,
                             now_fn=lambda: '2026-09-03T00:00:00Z')
    return rc, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.d = _tmpdir()
        self.src = FakeSource(_small_graph())
        for name in ('MDC2025au', 'MDC2025an'):
            write_epoch_file(self.d, propose_epoch(name + '_best_v1_0'))

    def test_propose_writes_missing_family_files(self):
        d = _tmpdir()
        rc, out, _ = _run(['propose', '--family', 'MDC2025'], self.src, d)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(os.listdir(d)), ['MDC2025an.json', 'MDC2025au.json'])
        rc, out, _ = _run(['propose', '--family', 'MDC2025'], self.src, d)
        self.assertIn('nothing to propose', out)

    def test_propose_without_family_is_usage_error(self):
        rc, _, err = _run(['propose'], self.src, self.d)
        self.assertEqual(rc, 2)

    def test_members_text_and_json(self):
        rc, out, _ = _run(['members', '--tier', 'nts', '--status', 'current'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', out)
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root current', out)
        rc, out, _ = _run(['members', '--json'], self.src, self.d)
        rows = json.loads(out)
        self.assertEqual({r['status'] for r in rows} <= {'current', 'stale', 'superseded', 'excluded'}, True)

    def test_retire_prints_purge_lines(self):
        rc, out, _ = _run(['retire'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith('DELETE - - '))

    def test_lookup_unknown_is_nonzero(self):
        rc, out, _ = _run(['lookup', 'nope.mu2e.a.b.art'], self.src, self.d)
        self.assertEqual(rc, 1)

    def test_publish_writes_file(self):
        p = os.path.join(_tmpdir(), 'sim_catalog.json')
        rc, out, _ = _run(['publish', '--out', p], self.src, self.d)
        self.assertEqual(rc, 0)
        with open(p) as f:
            self.assertEqual(json.load(f)['generated_at'], '2026-09-03T00:00:00Z')

    def test_malformed_epoch_file_is_exit_2(self):
        with open(os.path.join(self.d, 'MDC2025au.json'), 'w') as f:
            f.write('{"name": "MDC2025au"}')
        rc, _, err = _run(['members'], self.src, self.d)
        self.assertEqual(rc, 2)
        self.assertIn('MDC2025au.json', err)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_epochs.py -q -k TestCli`
Expected: FAIL with `ImportError` on `utils.epochs.cli`

- [ ] **Step 3: Write the CLI**

`utils/epochs/cli.py`:
```python
"""bin/epochs — the sim-epochs catalog command. See EXAMPLES.md."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from utils.epochs.dsconf import DsconfParseError, parse_dsconf
from utils.epochs.epoch_files import (EpochFileError, load_epoch_files, propose_epoch,
                                      write_epoch_file)
from utils.epochs.generation import build_cnf_index, generations
from utils.epochs.graph import build_catalog
from utils.epochs.publish import catalog_document, write_catalog
from utils.epochs.reports import consistency, gaps, lookup, purge_lines, retire
from utils.epochs.status import assign_status

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_EPOCHS_DIR = os.path.join(REPO, 'data', 'epochs')
INDEX_NAME = 'cnf_index.json'


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _source():
    from utils.epochs.source import SamSource
    return SamSource()


def _load_index(epochs_dir) -> Dict:
    p = os.path.join(epochs_dir, INDEX_NAME)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def _families(args, epochs) -> List[str]:
    if args.family:
        return list(args.family)
    return sorted({e.family for e in epochs.values()})


def _catalog(args, source):
    epochs = load_epoch_files(args.epochs_dir)
    fams = _families(args, epochs)
    if not fams:
        raise EpochFileError(f'no epoch files in {args.epochs_dir}; run `epochs propose --family F` first')
    if args.epoch:
        epochs = {k: v for k, v in epochs.items() if k == args.epoch}
        if not epochs:
            raise EpochFileError(f'no epoch file named {args.epoch!r} in {args.epochs_dir}')
    cat = build_catalog(epochs, source, fams, input_depth=args.input_depth)
    return assign_status(cat)


def _emit(rows, as_json: bool, text_fn):
    if as_json:
        print(json.dumps(rows, indent=1))
    else:
        for line in text_fn(rows):
            print(line)


def cmd_propose(args, source):
    if not args.family:
        print('epochs propose: --family is required (nothing is guessed)', file=sys.stderr)
        return 2
    have = load_epoch_files(args.epochs_dir)
    written = []
    for fam in args.family:
        seen = set()
        for dig in sorted(source.dig_datasets(fam)):
            dsconf = dig.split('.')[3]
            try:
                key = parse_dsconf(dsconf)
            except DsconfParseError as exc:
                print(f'unparseable dig dsconf, skipped: {dig} ({exc})', file=sys.stderr)
                continue
            name = f'{key.family}{key.letters}{key.rev or ""}'
            if name in have or name in seen:
                continue
            seen.add(name)
            written.append(write_epoch_file(args.epochs_dir, propose_epoch(dsconf)))
    if not written:
        print('nothing to propose: every dig letter family has an epoch file')
    for p in written:
        print(f'proposed {p}  <- fill in "purpose"')
    return 0


def cmd_members(args, source):
    cat = _catalog(args, source)
    rows = []
    for m in sorted(cat.members.values(), key=lambda m: (m.epoch, m.tier, m.desc, m.name)):
        if args.tier and m.tier != args.tier:
            continue
        if args.status and m.status != args.status:
            continue
        rows.append({'dataset': m.name, 'epoch': m.epoch, 'tier': m.tier, 'desc': m.desc,
                     'status': m.status, 'hold': m.hold, 'nfiles': m.nfiles})
    _emit(rows, args.json, lambda rs: [f"{r['status']:<10} {r['nfiles']:>6} {r['dataset']}"
                                      + (f"  [hold: {r['hold']}]" if r['hold'] else '') for r in rs])
    _report_noise(cat)
    return 0


def _report_noise(cat):
    for name, err in cat.unparseable:
        print(f'unparseable: {name}: {err}', file=sys.stderr)
    for fam, digs in cat.unclaimed_digs.items():
        for d in digs:
            print(f'unclaimed dig (no epoch root matches): {d}', file=sys.stderr)


def cmd_gaps(args, source):
    cat = _catalog(args, source)
    rows = gaps(cat)
    _emit(rows, args.json, lambda rs: [f"{r['kind']:<8} {r['epoch']:<10} {r['tier']:<4} {r['desc']:<40} {r['note']}" for r in rs])
    return 0


def cmd_consistency(args, source):
    cat = _catalog(args, source)
    gens = generations(cat, source, _load_index(args.epochs_dir))
    rows = consistency(cat, gens)
    _emit(rows, args.json, lambda rs: [f"{r['family']:<8} {r['tier']:<4} {r['count']:>4}  {r['generation']:<32}"
                                      + ('  ' + ', '.join(r['descs']) if r['minority'] else '') for r in rs])
    return 0


def cmd_retire(args, source):
    cat = _catalog(args, source)
    rows = retire(cat)
    _emit(rows, args.json, purge_lines)
    return 0


def cmd_lookup(args, source):
    cat = _catalog(args, source)
    info = lookup(cat, args.dataset)
    print(json.dumps(info, indent=1))
    return 0 if info['kind'] != 'unknown' else 1


def cmd_index_cnfs(args, source):
    idx = build_cnf_index(source, _load_index(args.epochs_dir))
    os.makedirs(args.epochs_dir, exist_ok=True)
    p = os.path.join(args.epochs_dir, INDEX_NAME)
    with open(p, 'w') as f:
        json.dump(idx, f, indent=1, sort_keys=True)
        f.write('\n')
    print(f'{p}: {len(idx["__indexed__"])} cnfs indexed, '
          f'{len(idx["__unlocatable__"])} unlocatable, {len(idx["__generic__"])} generic')
    for c in idx['__unlocatable__']:
        print(f'unlocatable cnf: {c}', file=sys.stderr)
    return 0


def cmd_publish(args, source, now_fn):
    cat = _catalog(args, source)
    gens = generations(cat, source, _load_index(args.epochs_dir))
    doc = catalog_document(cat, gens, now_fn())
    write_catalog(doc, args.out)
    print(f'{args.out}: {len(doc["epochs"])} epochs, '
          f'{sum(len(e["datasets"]) for e in doc["epochs"])} members, '
          f'{len(doc["gaps"])} gaps, {len(doc["retire"])} retire candidates')
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog='epochs', description=__doc__)
    p.add_argument('--epochs-dir', default=DEFAULT_EPOCHS_DIR)
    p.add_argument('--input-depth', type=int, default=3)
    sub = p.add_subparsers(dest='verb', required=True)

    def common(sp, epoch=True):
        sp.add_argument('--family', action='append')
        if epoch:
            sp.add_argument('--epoch')
        sp.add_argument('--json', action='store_true')

    common(sub.add_parser('propose'), epoch=False)
    s = sub.add_parser('members'); common(s)
    s.add_argument('--tier'); s.add_argument('--status')
    common(sub.add_parser('gaps'))
    common(sub.add_parser('consistency'))
    common(sub.add_parser('retire'))
    s = sub.add_parser('lookup'); common(s); s.add_argument('dataset')
    common(sub.add_parser('index-cnfs'), epoch=False)
    s = sub.add_parser('publish'); common(s); s.add_argument('--out', required=True)
    return p


def main(argv: Optional[List[str]] = None, source=None, now_fn=None) -> int:
    args = build_parser().parse_args(argv)
    if not hasattr(args, 'epoch'):
        args.epoch = None
    source = source or _source()
    now_fn = now_fn or _now
    try:
        if args.verb == 'propose':
            return cmd_propose(args, source)
        if args.verb == 'members':
            return cmd_members(args, source)
        if args.verb == 'gaps':
            return cmd_gaps(args, source)
        if args.verb == 'consistency':
            return cmd_consistency(args, source)
        if args.verb == 'retire':
            return cmd_retire(args, source)
        if args.verb == 'lookup':
            return cmd_lookup(args, source)
        if args.verb == 'index-cnfs':
            return cmd_index_cnfs(args, source)
        if args.verb == 'publish':
            return cmd_publish(args, source, now_fn)
    except EpochFileError as exc:
        print(f'epochs: {exc}', file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f'epochs: {exc}', file=sys.stderr)
        return 3
    return 2
```

`bin/epochs`:
```python
#!/usr/bin/env python3
"""Sim-epochs catalog: derive dataset status from SAM, publish for the
sim-epochs MCP server. Wrapper for utils/epochs/cli.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.epochs.cli import main

if __name__ == '__main__':
    sys.exit(main())
```

Then: `chmod +x bin/epochs`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_epochs.py -q`
Expected: `70 passed`

- [ ] **Step 5: Run the whole suite once to check nothing else broke**

Run: `python3 -m pytest test/test_unit.py test/test_epochs.py -q`
Expected: all pass (the count of `test_unit.py` is whatever it was before; no failures).

- [ ] **Step 6: Commit**

```bash
git add utils/epochs/cli.py bin/epochs test/test_epochs.py
git commit -m "feat(epochs): bin/epochs CLI — propose, members, gaps, consistency, retire, lookup, index-cnfs, publish

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 10: seed the real epoch files for MDC2025 and Run1B

**Files:**
- Create: `data/epochs/MDC2025*.json`, `data/epochs/Run1B*.json` (by `propose`, then edited)

- [ ] **Step 1: Propose against real SAM, as the current user**

Run (via `/mu2e-run`, no musing needed; `bin/epochs` is plain Python):
```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh && muse setup ops \
  && python3 bin/epochs propose --family MDC2025 --family Run1B
```
Expected: one `proposed data/epochs/<name>.json` line per dig letter family: MDC2025 ad, ai, an, ap, au; Run1B ab, ab2, af, ah, ai, an, ap, aq, av, aw (per the 2026-09-03 survey). Any `unparseable dig dsconf` line on stderr is a finding to record in the wiki page, not to fix here.

- [ ] **Step 2: Fill in `purpose` and `status` by hand**

For each file, set `purpose` to one sentence from the wiki campaign pages (`wiki/pages/` has MDC2025 and Run1B campaign notes; `grep -l "<letters>" wiki/pages/*.md`), and set `status` to `frozen` for every Run1B file (decision 9: Run1B campaigns are frozen). Leave MDC2025 files `current`. Do not add pins yet.

- [ ] **Step 3: Verify the files load**

Run: `python3 bin/epochs members --family MDC2025 --tier dig --json | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"`
Expected: a number equal to the dig dataset count for MDC2025 (43 + 5 + 2 + 2 + 1 = 53 on 2026-09-03; a different number is fine if SAM changed, a Python traceback is not).

- [ ] **Step 4: Commit**

```bash
git add data/epochs/
git commit -m "data(epochs): seed epoch files for MDC2025 and Run1B letter families

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 11: build the cnf index and run the reports against real SAM

**Files:**
- Create: `data/epochs/cnf_index.json`
- Create: `/exp/mu2e/data/users/oksuzian/claude-scratch/probes/epochs_first_run/` (scratch, not committed)

- [ ] **Step 1: Build the index**

Run:
```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh && muse setup ops \
  && time python3 bin/epochs index-cnfs
```
Expected: `data/epochs/cnf_index.json: N cnfs indexed, U unlocatable, G generic` with N around 850 (the 2026-08-26 POMS sweep counted 849). Reading each tarball from `/pnfs` costs about 0.1-0.5 s; budget 10 minutes. Every `unlocatable cnf:` line is a cnf SAM has no location for; list them in the wiki page section 17 follow-ups, do not retry.

- [ ] **Step 2: Run every report and keep the output**

```bash
P=/exp/mu2e/data/users/oksuzian/claude-scratch/probes/epochs_first_run; mkdir -p $P
python3 bin/epochs members     --family MDC2025 > $P/members.txt  2> $P/members.err
python3 bin/epochs gaps        --family MDC2025 > $P/gaps.txt     2> $P/gaps.err
python3 bin/epochs consistency --family MDC2025 > $P/consistency.txt 2> $P/consistency.err
python3 bin/epochs retire      --family MDC2025 > $P/retire.txt   2> $P/retire.err
python3 bin/epochs publish     --out $P/sim_catalog.json
```
Expected checks, each one a line in the report to the user:
- `members.txt`: `nts.mu2e.MuCap1809keVCaloOnSpill.MDC2025au_best_v1_5-001.root` is `current` and `nts.mu2e.MuCap1809keVCaloOnSpill.MDC2025au_best_v1_5.root` is `superseded`.
- `members.txt`: `mcs.mu2e.CeEndpointOnSpill.MDC2025ar_best_v1_1.art` is `superseded`; `mcs.mu2e.CosmicCalibOnSpill.MDC2025ar_best_v1_1.art` is `current`.
- `gaps.txt` contains `missing ... mcs ensembleMDS3bOnSpill`.
- `consistency.txt` for tier `nts` shows `AnalysisMDC2025/v02_01_00` and `AnalysisMDC2025/v02_00_00` rows with the minority descs named; any `unknown` row lists members whose cnf could not be found — record them.
- `retire.txt` lines parse with `awk '{print $1,$4,$5,$6}'` and every `# reason` names a superseding dataset or an epoch.
- `members.err` / `gaps.err`: every `unparseable:` and `unclaimed dig` line goes verbatim into the wiki page.

- [ ] **Step 3: Sanity-check one lineage by hand**

Run: `python3 bin/epochs lookup nts.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5-001.root`
Expected: `kind member`, `status current`, parents = the mcs, `superseded_by` empty.

- [ ] **Step 4: Commit the index**

```bash
git add data/epochs/cnf_index.json
git commit -m "data(epochs): cnf index — dataset to cnf, built from declared outputs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

### Task 12: docs — EXAMPLES schema, wiki, ADR wording, CLAUDE.md pointer

**Files:**
- Modify: `docs/EXAMPLES_schema.md` (add an `epochs` tool subsection to the Additional Tools list)
- Modify: `wiki/pages/2026-09-02-sim-epochs-design.md` (section 13 phases: mark phase 1 done with the commit range; section 17 follow-ups: add unparseable/unclaimed/unlocatable findings from Task 11)
- Modify: `docs/adr/0003-cnf-tarball-declared-as-sam-parent.md` (backfill sentence)
- Modify: `CLAUDE.md` (one line under "Prodtools usage" listing `epochs` among the commands EXAMPLES.md covers)

- [ ] **Step 1: EXAMPLES schema entry**

Append to the Additional Tools list in `docs/EXAMPLES_schema.md`:
```markdown
- `epochs` — sim-epochs catalog (`utils/epochs/cli.py`). Verbs: `propose
  --family F` (write `data/epochs/<letters>.json` for every dig letter
  family lacking one), `members`, `gaps`, `consistency`, `retire`
  (purge_proposal lines), `lookup DATASET`, `index-cnfs`, `publish --out
  FILE`. Tribal knowledge to preserve: status is `current | stale |
  superseded` and is recomputed from SAM on every call, never stored;
  `frozen` is an epoch standing / a hold, never a dataset status; no
  timestamps are used anywhere; `-NNN` is a collision counter; renames
  across desc need an `exclude` pin with a reason; `propose` refuses to
  run without `--family`; exit 2 = a malformed epoch file, exit 3 = SAM
  or name-parse error. Design: `wiki/pages/2026-09-02-sim-epochs-design.md`,
  glossary `CONTEXT.md`, ADRs 0003 and 0004.
```

- [ ] **Step 2: ADR 0003 wording**

In `docs/adr/0003-cnf-tarball-declared-as-sam-parent.md` replace
```
Legacy datasets are backfilled once by reading
the cnf name from one log per dataset and feeding the same code path.
```
with
```
Legacy datasets are backfilled once from the cnf catalog itself: every
cnf in SAM declares its outputs in `tbs.outfiles`, and a generic `evnt`
cnf claims the datasets carrying its dsconf (`bin/epochs index-cnfs`,
`data/epochs/cnf_index.json`). Logs are not read.
```
and in Considered Options replace `Kept as the one-time backfill.` with `Not needed even for the backfill: the cnf's own output list is a better record than the log text.`

- [ ] **Step 3: Wiki and CLAUDE.md**

Wiki page section 13, change `1. Schema, ...  A few days.` to
`1. DONE 2026-09-0x (commits <first>..<last>): bin/epochs with parser, closure, status, gaps, consistency, retire, publish; data/epochs seeded for MDC2025 and Run1B; cnf index built.` with the real commit hashes from `git log --oneline`. Append the Task 11 findings under section 17 follow-ups.

CLAUDE.md, in the Prodtools usage list of commands, add `epochs` after `copy_to_stash`.

- [ ] **Step 4: Regenerate EXAMPLES.md**

Run `/refresh-examples epochs` (the skill regenerates the whole doc; review the diff of the `epochs` section; do not commit EXAMPLES.md through the skill — commit it here).

- [ ] **Step 5: Commit**

```bash
git add docs/EXAMPLES_schema.md EXAMPLES.md docs/adr/0003-cnf-tarball-declared-as-sam-parent.md \
        wiki/pages/2026-09-02-sim-epochs-design.md CLAUDE.md
git commit -m "docs(epochs): EXAMPLES entry, ADR 0003 backfill wording, wiki phase-1 record

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0115w5JympLoYF5uk2FkXiAC"
```

---

## Self-review notes (done while writing)

- Spec coverage: decisions 1–5, 7–15, 17–19 are implemented by Tasks 1–11; decision 6's code side (cnf as parent) and decision 16 (production hook) are explicitly out of scope; decision 10's MCP tools are a separate plan. `notes` pins are validated (Task 2) and stored, and surface only through the epoch file itself in this plan: printing them on retire lines is left to the MCP plan, where the diff query lives.
- Placeholder scan: none.
- Type consistency: `Member.inputs` holds non-member parents; `cnf_for` reads `m.inputs` for a future cnf parent; `DROP_TIERS` keeps `cnf` out until ADR 0003's code plan flips it. `retire()` reason strings are what `purge_lines` prints after `#`. `groups()` sorts descending so index 0 is the newest; `_winner_of` relies on that.
- Known approximation stated in code: `EXPECTED_TIERS` assumes the reco keeps the dig's desc. A reco that renames (`-KK`, `-CH`) shows as a gap until a `not_expected` pin says otherwise; that is the decision-11 behavior, not a bug.
