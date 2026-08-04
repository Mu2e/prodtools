# listNewDatasets Ledger Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `listNewDatasets --completeness` report `<landed>/<expected>` for direct-submission campaigns by sourcing expected job counts from `submissions.db`, and remove the POMS-backed path entirely.

**Architecture:** One new pure-ish function, `ledger_expected`, builds a `{output dataset → expected njobs}` map by reading campaign rows from the submission ledger and resolving each campaign's output dataset names out of its cnf tarball. `listNewDatasets` builds that map once per run and turns `_get_completeness` into a dict lookup, dropping SQLAlchemy, the POMS DB staleness/rebuild machinery, and three flags.

**Tech Stack:** Python 3, stdlib `unittest` (`python3 test/test_unit.py`), `unittest.mock.patch`. SQLite via the existing `utils/submission_ledger.py`.

**Spec:** `docs/superpowers/specs/2026-08-01-listnewdatasets-progress-design.md`

## Global Constraints

- Expected counts come from `submissions.db` only. The POMS path is **removed, not kept as a fallback**.
- Expected is `entry['njobs']` — the **submitted window**, never the cnf's baked capacity.
- Output dataset names must come from the **cnf tarball**, never from convention. `CosmicCRYAll` → `...CosmicCRYAllOnSpill...`, `CosmicCRYExtracted` → no suffix, and `FlatGamma` is a prefix of `FlatGammaCalo`.
- A wrong denominator is worse than none: an unresolvable campaign contributes nothing and is reported on stderr.
- **There is no per-dataset `?`** — the dataset name comes from the tarball, so an unresolvable tarball yields an unknown dataset, which shows `—`.
- The completeness **numerator is the dataset's total SAM file count**, not the windowed `COUNT`.
- One unresolvable campaign never aborts the report.
- stdout stays a clean table; warnings go to stderr.
- `EXAMPLES.md` is derived — edit `docs/EXAMPLES_schema.md` and regenerate via `/refresh-examples`. Never hand-edit.
- Do **not** `git push`. Commit only.

## Environment notes the implementer needs

- `utils/listNewDatasets.py` uses **bare** imports (`from samweb_wrapper import ...`), because `bin/listNewDatasets` puts both the repo root and `utils/` on `sys.path`. Keep that style in this file. `utils/submissions.py` uses `utils.`-prefixed imports; both work because it inserts the repo root itself.
- The test suite does **not** put `utils/` on `sys.path`, so `import listNewDatasets` needs a `setUpClass` that adds it. There is precedent at `test/test_unit.py:4752` (`TestJobsPayload`).
- Baseline: `python3 test/test_unit.py` reports `Ran 679 tests ... OK`.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `utils/submissions.py` | ledger ops + verification | add `ledger_expected`, add one import |
| `utils/listNewDatasets.py` | the listing tool | rewire completeness, delete POMS path, swap flags |
| `test/test_unit.py` | tests | two new sections, 8 tests |
| `docs/EXAMPLES_schema.md` | doc source of truth | update the completeness description and flag list |
| `EXAMPLES.md` | derived | regenerated |

---

### Task 1: `ledger_expected` — dataset → expected njobs

**Files:**
- Modify: `utils/submissions.py` — new import, new function after `verify_row` (which ends at line 205)
- Test: `test/test_unit.py` — new class appended at the end, before the `# Entry point` banner

**Interfaces:**
- Consumes: `submission_ledger.all_campaigns(db_path)` (returns dicts whose `entry_json` column is already parsed into an `entry` key), `locate_tarball(name)`, `Mu2eJobPars(path)`, `extract_datasets_from_tarball(job_pars, njobs)` — all already imported in `utils/submissions.py` except `Mu2eName`.
- Produces: `ledger_expected(db_path, dsconfs=None, *, locate=locate_tarball) -> (expected: dict[str, int], failures: dict[str, str])`

- [ ] **Step 1: Write the failing tests**

Append this class to `test/test_unit.py`, immediately **before** the `# Entry point` banner comment at the end of the file:

```python
# ---------------------------------------------------------------------------
# 41. ledger_expected (utils/submissions.py)
# ---------------------------------------------------------------------------

class TestLedgerExpected(unittest.TestCase):
    """Expected job counts per output dataset, sourced from the submission
    ledger. The dataset NAME comes from the cnf tarball; the COUNT comes from
    the ledger entry's njobs (the submitted window)."""

    CRY = 'cnf.mu2e.CosmicCRYAll.MDC2025au_best_v1_5.0.tar'
    MDS = 'cnf.mu2e.ensembleMDS3c.MDC2025au_best_v1_5.0.tar'
    OTHER = 'cnf.mu2e.NoPrimary.MDC2025ar_best_v1_3.0.tar'
    OUT = {
        CRY: ['dig.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.art'],
        MDS: ['dig.mu2e.ensembleMDS3cOnSpill.MDC2025au_best_v1_5.art'],
        OTHER: ['dig.mu2e.NoPrimaryOnSpill.MDC2025ar_best_v1_3.art'],
    }
    CRY_DS = 'dig.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.art'
    MDS_DS = 'dig.mu2e.ensembleMDS3cOnSpill.MDC2025au_best_v1_5.art'

    def _call(self, camps, dsconfs=None, unlocatable=()):
        """Run ledger_expected with the tarball layer faked out.

        locate is injected; Mu2eJobPars is reduced to identity so the 'path'
        is just the tarball name, which extract_ then maps to datasets."""
        from utils import submissions
        asked = []

        def fake_locate(tarball):
            asked.append(tarball)
            return None if tarball in unlocatable else tarball

        with patch.object(submissions, 'Mu2eJobPars', lambda p: p), \
             patch.object(submissions, 'extract_datasets_from_tarball',
                          lambda job, njobs: self.OUT[job]), \
             patch.object(submissions.submission_ledger, 'all_campaigns',
                          return_value=camps):
            expected, failures = submissions.ledger_expected(
                '/nonexistent.db', dsconfs=dsconfs, locate=fake_locate)
        return expected, failures, asked

    def test_maps_output_dataset_to_njobs(self):
        camps = [{'tarball': self.CRY, 'entry': {'njobs': 2500}},
                 {'tarball': self.MDS, 'entry': {'njobs': 496}}]
        expected, failures, _ = self._call(camps)
        self.assertEqual(expected, {self.CRY_DS: 2500, self.MDS_DS: 496})
        self.assertEqual(failures, {})

    def test_uses_submitted_window_not_cnf_capacity(self):
        """CosmicCRYAll's cnf carries 12500 capacity; the ledger entry says the
        2500 that were actually submitted. The ledger value must win."""
        camps = [{'tarball': self.CRY, 'entry': {'njobs': 2500}}]
        expected, _, _ = self._call(camps)
        self.assertEqual(expected[self.CRY_DS], 2500)

    def test_sums_when_one_tarball_is_enqueued_twice(self):
        """A tarball can be enqueued as several index windows (RPCInternal-
        Physical went out at 250 then 1667). Expected is their sum, and the
        tarball is resolved only once."""
        camps = [{'tarball': self.CRY, 'entry': {'njobs': 250}},
                 {'tarball': self.CRY, 'entry': {'njobs': 1667}}]
        expected, _, asked = self._call(camps)
        self.assertEqual(expected[self.CRY_DS], 1917)
        self.assertEqual(asked, [self.CRY])

    def test_unresolvable_tarball_yields_failure_not_a_number(self):
        camps = [{'tarball': self.CRY, 'entry': {'njobs': 2500}},
                 {'tarball': self.MDS, 'entry': {'njobs': 496}}]
        expected, failures, _ = self._call(camps, unlocatable={self.CRY})
        self.assertNotIn(self.CRY_DS, expected)
        self.assertIn(self.CRY, failures)
        self.assertEqual(expected[self.MDS_DS], 496)   # others unaffected

    def test_dsconf_filter_skips_other_campaigns_without_resolving(self):
        camps = [{'tarball': self.CRY, 'entry': {'njobs': 2500}},
                 {'tarball': self.OTHER, 'entry': {'njobs': 100}}]
        expected, _, asked = self._call(
            camps, dsconfs={'MDC2025au_best_v1_5'})
        self.assertEqual(asked, [self.CRY])
        self.assertEqual(list(expected), [self.CRY_DS])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
python3 test/test_unit.py TestLedgerExpected 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)|AttributeError"
```
Expected: FAILED — `AttributeError: <module 'utils.submissions'> does not have the attribute 'ledger_expected'` (raised by `patch.object` before the call).

- [ ] **Step 3: Add the import**

In `utils/submissions.py`, add `Mu2eName` to the imports. The existing block reads:

```python
from utils import submission_ledger
from utils.jobquery import Mu2eJobPars
from utils.mkrecovery import (build_file_maps, extract_datasets_from_tarball,
                              locate_tarball)
from utils.poms_entry import njobs_of
from utils.samweb_wrapper import files_in_dataset
```

Add one line after the `jobquery` import:

```python
from utils.job_common import Mu2eName
```

- [ ] **Step 4: Implement `ledger_expected`**

Add immediately after `verify_row` (which ends with `return missing, partial`) and before the `@contextlib.contextmanager` decorated `_scratch_map_dir`:

```python
def ledger_expected(db_path, dsconfs=None, *, locate=locate_tarball):
    """Map output dataset name -> expected job count, from the submission ledger.

    The ledger entry carries njobs -- the SUBMITTED window, not the cnf's baked
    capacity -- but names its outputs with a glob ("*.art"), so the dataset NAME
    has to come from the cnf tarball. That is the same source verify_row uses,
    and the only sound one: CosmicCRYAll produces ...CosmicCRYAllOnSpill... while
    CosmicCRYExtracted takes no suffix, and FlatGamma is a prefix of
    FlatGammaCalo, so neither convention nor prefix matching can be trusted.

    dsconfs: optional set of dsconfs; when given, campaigns of other dsconfs are
    skipped without resolving their tarball, so a short listing does not pay for
    the whole ledger.

    locate: injected for testing.

    Returns (expected, failures). expected maps dataset -> summed njobs over
    every campaign producing it (one tarball may be enqueued as several index
    windows). failures maps tarball -> reason for campaigns that could not be
    resolved; those contribute nothing rather than a guessed denominator. Note
    a failed campaign's dataset is simply unknown -- it cannot be marked, since
    its name was what the tarball would have supplied.
    """
    expected = {}
    failures = {}
    resolved = {}          # tarball -> [datasets], or None when unresolvable
    for camp in submission_ledger.all_campaigns(db_path):
        tarball = camp['tarball']
        njobs = (camp.get('entry') or {}).get('njobs')
        if not njobs:
            continue
        if dsconfs is not None:
            try:
                if Mu2eName.parse(tarball).dsconf not in dsconfs:
                    continue
            except ValueError:
                continue
        if tarball not in resolved:
            try:
                path = locate(tarball)
                if not path:
                    raise RuntimeError("tarball not locatable")
                datasets = extract_datasets_from_tarball(Mu2eJobPars(path), njobs)
                if not datasets:
                    raise RuntimeError("no output datasets in tarball")
                resolved[tarball] = datasets
            except Exception as e:
                failures[tarball] = str(e)
                resolved[tarball] = None
        datasets = resolved[tarball]
        if datasets is None:
            continue
        for ds in datasets:
            expected[ds] = expected.get(ds, 0) + njobs
    return expected, failures
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
python3 test/test_unit.py TestLedgerExpected 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)"
```
Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 6: Run the full suite**

Run:
```bash
python3 test/test_unit.py 2>&1 | grep -E "^(Ran|OK|FAILED)"
```
Expected: `Ran 684 tests` and `OK`.

- [ ] **Step 7: Commit**

```bash
git add utils/submissions.py test/test_unit.py
git commit -m "feat(submissions): ledger_expected — output dataset to expected njobs

Builds {output dataset -> expected job count} from the submission ledger.
njobs is the submitted window, not the cnf's capacity, so windowed campaigns
get a denominator they can actually reach. Dataset names come from the cnf
tarball because the ledger entry names its outputs with a glob, and because
neither convention nor prefix matching is sound (CosmicCRYAll -> ...OnSpill,
CosmicCRYExtracted -> no suffix, FlatGamma prefixes FlatGammaCalo).

An unresolvable tarball contributes nothing and is returned in failures --
never a guessed denominator.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 2: Rewire `listNewDatasets`, remove the POMS path

**Files:**
- Modify: `utils/listNewDatasets.py` — imports, delete `_db_is_stale` and `_ensure_db_fresh` (lines 21-53), `DatasetLister.__init__`, `_get_completeness`, `run`, `main`
- Test: `test/test_unit.py` — new class appended after `TestLedgerExpected`

**Interfaces:**
- Consumes: `ledger_expected(db_path, dsconfs=None, *, locate=locate_tarball) -> (expected, failures)` from Task 1; `submission_ledger.DEFAULT_DB` (env `MU2E_SUBMISSIONS_DB`, else `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`).
- Produces: `DatasetLister(..., completeness: bool, ledger_db: Optional[str])`; CLI flag `--ledger-db`.

- [ ] **Step 1: Write the failing tests**

Append this class to `test/test_unit.py`, after `TestLedgerExpected` and still before the `# Entry point` banner:

```python
# ---------------------------------------------------------------------------
# 42. listNewDatasets completeness column (ledger-backed)
# ---------------------------------------------------------------------------

class TestListerCompleteness(unittest.TestCase):
    """The COMPLETENESS column formats <landed>/<expected> from the ledger map.
    listNewDatasets uses bare imports, so utils/ must be on sys.path."""

    @classmethod
    def setUpClass(cls):
        d = os.path.join(os.path.dirname(__file__), '..', 'utils')
        if d not in sys.path:
            sys.path.insert(0, d)
        import listNewDatasets
        cls.lnd = listNewDatasets

    DS = 'dig.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.art'

    def _lister(self, expected, counts):
        lister = self.lnd.DatasetLister(completeness=True)
        lister._expected = expected
        lister._total_files = lambda ds: counts.get(ds, 0)
        return lister

    def test_reports_landed_over_expected_with_incomplete_marker(self):
        lister = self._lister({self.DS: 2500}, {self.DS: 1432})
        self.assertEqual(lister._get_completeness(self.DS),
                         "1432/2500 INCOMPLETE")

    def test_no_marker_once_landed_reaches_expected(self):
        lister = self._lister({self.DS: 2500}, {self.DS: 2500})
        self.assertEqual(lister._get_completeness(self.DS), "2500/2500")

    def test_dataset_from_no_campaign_reports_dash(self):
        lister = self._lister({}, {self.DS: 17})
        self.assertEqual(lister._get_completeness(self.DS), "—")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
python3 test/test_unit.py TestListerCompleteness 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)|AssertionError"
```
Expected: FAILED, 3 assertion failures of the form
`AssertionError: '-' != '1432/2500 INCOMPLETE'`.

The construction itself succeeds — `completeness` is already a valid kwarg, and
setting `_expected` / `_total_files` on the instance is legal Python. The
failure is that today's `_get_completeness` checks `self._db_session`, which
the current `__init__` sets to `None`, and returns `"-"` before looking at
anything else.

- [ ] **Step 3: Replace the module header and delete the POMS helpers**

In `utils/listNewDatasets.py`, replace everything from line 1 through the end of `_ensure_db_fresh` (line 53) with:

```python
#!/usr/bin/env python3
"""List recently created datasets from SAM database."""

import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from samweb_wrapper import (list_files, dataset_summary, dataset_file_count,
                            q_recent_files)
from job_common import Mu2eName
from submissions import ledger_expected
from submission_ledger import DEFAULT_DB
```

This drops the `glob`, `time` and `Tuple` imports, the `poms_entry` import, and both POMS helper functions.

- [ ] **Step 4: Rewrite the constructor**

Replace `DatasetLister.__init__` with:

```python
    def __init__(self, filetype: str = "art", days: int = 7,
                 user: str = "mu2epro", show_size: bool = False,
                 custom_query: Optional[str] = None,
                 completeness: bool = False,
                 ledger_db: Optional[str] = None):
        self.filetype = filetype
        self.days = days
        self.user = user
        self.show_size = show_size
        self.custom_query = custom_query
        self.ext = f".{filetype}"
        self.completeness = completeness
        self.ledger_db = ledger_db or DEFAULT_DB
        self._expected = {}      # dataset -> expected njobs, built in run()
```

- [ ] **Step 5: Add the numerator helper and rewrite `_get_completeness`**

Replace `_get_completeness` with these two methods:

```python
    def _total_files(self, dataset: str) -> int:
        """Total files in the dataset. NOT the windowed COUNT column: a
        campaign that started before the lookback window would otherwise be
        scored against a full-campaign denominator with a partial numerator."""
        try:
            return dataset_file_count(dataset)
        except Exception:
            return 0

    def _get_completeness(self, dataset: str) -> str:
        """<landed>/<expected> for a dataset produced by a direct campaign.

        '—' when no known campaign produced it. There is deliberately no
        per-dataset '?': the dataset name comes FROM the cnf tarball, so an
        unresolvable tarball leaves its dataset unidentifiable. Those failures
        are reported once on stderr by run() instead."""
        expected = self._expected.get(dataset)
        if expected is None:
            return "—"
        landed = self._total_files(dataset)
        marker = "" if landed >= expected else " INCOMPLETE"
        return f"{landed}/{expected}{marker}"
```

- [ ] **Step 6: Build the map in `run()`**

In `run()`, replace the whole opening block — from `# Refresh the POMS DB before SAM queries...` down to and including the `self.completeness = False` inside the `except` — with nothing, so `run()` now begins at `query = self.build_query()`.

Then, immediately after `sorted_datasets = sorted(dataset_counts.items())`, insert:

```python
        if self.completeness:
            dsconfs = set()
            for ds, _ in sorted_datasets:
                try:
                    dsconfs.add(Mu2eName.parse(ds).dsconf)
                except ValueError:
                    continue
            try:
                self._expected, failures = ledger_expected(self.ledger_db,
                                                           dsconfs=dsconfs)
            except Exception as e:
                print(f"WARNING: could not read ledger {self.ledger_db} ({e}); "
                      "completeness column disabled.", file=sys.stderr)
                self.completeness = False
                failures = {}
            for tarball, reason in sorted(failures.items()):
                print(f"WARNING: no expected count for {tarball}: {reason}",
                      file=sys.stderr)
```

- [ ] **Step 7: Rewrite `main()`**

Replace `main()` with:

```python
def main():
    parser = argparse.ArgumentParser(description="List recently created datasets from SAM database")
    parser.add_argument('--filetype', default='art', help='File format (default: art)')
    parser.add_argument('--days', type=int, default=7, help='Days to look back (default: 7)')
    parser.add_argument('--user', default='mu2epro', help='Username filter (default: mu2epro)')
    parser.add_argument('--size', action='store_true', help='Show average file sizes')
    parser.add_argument('--query', help='Custom SAM query')
    parser.add_argument('--completeness', action='store_true',
                        help='Append a <landed>/<expected> column, with expected '
                             'read from the submission ledger; datasets from no '
                             'known campaign show an em dash')
    parser.add_argument('--ledger-db', default=DEFAULT_DB,
                        help=f'Submission ledger SQLite path (default: {DEFAULT_DB})')
    args = parser.parse_args()

    lister = DatasetLister(filetype=args.filetype, days=args.days, user=args.user,
                           show_size=args.size, custom_query=args.query,
                           completeness=args.completeness,
                           ledger_db=args.ledger_db)

    lister.run()
```

The removed `--no-rebuild`, `--db` and `--poms-dir` will now fail with argparse's "unrecognized arguments" — a loud failure, which is intended.

- [ ] **Step 8: Run the tests to verify they pass**

Run:
```bash
python3 test/test_unit.py TestListerCompleteness 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)"
```
Expected: `Ran 3 tests` and `OK`.

- [ ] **Step 9: Run the full suite**

Run:
```bash
python3 test/test_unit.py 2>&1 | grep -E "^(Ran|OK|FAILED)"
```
Expected: `Ran 687 tests` and `OK`.

- [ ] **Step 10: Verify end to end against the live ledger**

`listNewDatasets` imports `samweb_client`, so the Mu2e environment must be sourced. Note there is no `pyenv ana` step any more — that was only needed for SQLAlchemy.

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \
  && muse setup ops > /dev/null 2>&1 \
  && python3 bin/listNewDatasets --days 1 --completeness 2>/dev/null \
     | grep -E "MDC2025au_best_v1_5|COMPLETENESS" | head
```
Expected: rows now carry real denominators, e.g.
`dig.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.art   1432/2500 INCOMPLETE`,
and `dig.mu2e.ensembleMDS3cOnSpill.MDC2025au_best_v1_5.art   496/496`.
Before this change every one of those rows showed `—`.

Also confirm the retired flag fails loudly:
```bash
python3 bin/listNewDatasets --completeness --no-rebuild 2>&1 | tail -2
```
Expected: `error: unrecognized arguments: --no-rebuild`.

- [ ] **Step 11: Commit**

```bash
git add utils/listNewDatasets.py test/test_unit.py
git commit -m "feat(listNewDatasets): completeness from the submission ledger

--completeness read poms_data.db and reported an em dash for every direct
campaign; POMS answered for 0 of 75 datasets over 14 days. Expected counts now
come from submissions.db via ledger_expected.

Removes the POMS path outright: the SQLAlchemy import guard, the DB
staleness check, the incremental rebuild, and the flags --no-rebuild, --db and
--poms-dir. --completeness no longer needs 'pyenv ana', and the 'WARNING: DB
stale' line is gone. Adds --ledger-db, deliberately not named --db so a stale
--db invocation fails loudly instead of silently pointing at the wrong file.

The numerator is the dataset's total SAM file count, not the windowed COUNT,
matching what the POMS column reported.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 3: Documentation

**Files:**
- Modify: `docs/EXAMPLES_schema.md` — the `listNewDatasets` completeness description and flag list
- Modify: `EXAMPLES.md` — regenerated, never hand-edited

Context established while writing this plan: the schema does **not** describe
this column, and contains neither "SQLAlchemy" nor "pyenv". The flag list and
the prerequisite prose in `EXAMPLES.md` are generated by reading the code, so a
regeneration picks up the flag swap and drops the SQLAlchemy claim on its own.
`docs/EXAMPLES_schema.md:156` mentions `poms_data.db` but for `pomsMonitor`,
not `listNewDatasets` — leave it alone.

The one thing regeneration cannot infer is the *rationale*, so the schema gains
a tribal-knowledge bullet.

- [ ] **Step 1: Add the tribal-knowledge bullet to the schema**

`EXAMPLES.md:42` currently claims `listNewDatasets --completeness` needs
SQLAlchemy and `pyenv ana`, which this change makes false. Verify it is still
there before regenerating:

```bash
sed -n '41,46p' EXAMPLES.md
```

Then, in `docs/EXAMPLES_schema.md`, add this to the tribal-knowledge bullets
(the section that lists facts not derivable from code — near the existing
`pomsMonitor database default path` bullet at line 156):

```markdown
- `listNewDatasets --completeness` reads expected job counts from the
  submission ledger (`submissions.db`), NOT from `poms_data.db`. It needs no
  SQLAlchemy and no DB rebuild. The output dataset name is resolved from each
  campaign's cnf tarball, because the ledger entry names its outputs with a
  glob; a dataset produced by no known campaign shows an em dash, and there is
  deliberately no per-dataset unknown marker (an unresolvable tarball leaves
  its dataset unidentifiable — those are warned about on stderr instead).
```

- [ ] **Step 3: Regenerate `EXAMPLES.md`**

Invoke the `/refresh-examples` skill via the Skill tool. Never hand-edit `EXAMPLES.md`. If the skill is unavailable, commit the schema change alone and report the regeneration as incomplete with the error.

- [ ] **Step 4: Confirm the regenerated doc matches the code**

Run:
```bash
grep -n "ledger-db\|no-rebuild\|poms-dir" EXAMPLES.md
grep -n "listNewDatasets --completeness" EXAMPLES.md
```
Expected: `--ledger-db` present; `--no-rebuild` and `--poms-dir` absent. The
second grep must **not** hit the SQLAlchemy prerequisite block near line 42 —
that tool no longer needs SQLAlchemy, and leaving the claim would send an
operator to run `pyenv ana` for nothing.

A `poms_data` hit is expected and correct if it is the `pomsMonitor` bullet;
check which tool the line refers to before changing anything.

- [ ] **Step 5: Commit**

```bash
git add docs/EXAMPLES_schema.md EXAMPLES.md
git commit -m "docs: listNewDatasets completeness is ledger-backed

Schema updated as the source of truth, EXAMPLES regenerated. Records the flag
swap (--no-rebuild/--db/--poms-dir removed, --ledger-db added) and that the
column no longer requires SQLAlchemy or a DB rebuild.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```
