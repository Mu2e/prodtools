# latestDatasets Selectable Ordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `latestDatasets` pick the latest dataset per description by either lexicographic dsconf order (default, unchanged) or SAM definition creation time, selected with `--latest-by {dsconf,time}`.

**Architecture:** Introduce one concept — an *order key*, a callable `name -> sortable` threaded into `_group_by_description`. `None` means today's dsconf sort. Because `latest_per_description` and `superseded_per_description` both delegate their sorting to that one function, they order by the same key by construction. The time key is built once per run and queries SAM only for descriptions that actually have competing versions.

**Tech Stack:** Python 3, stdlib `unittest` (`python3 test/test_unit.py`), `unittest.mock.patch`. SAM access through the existing `utils/samweb_wrapper.py` wrapper.

**Spec:** `docs/superpowers/specs/2026-07-31-latestdatasets-ordering-design.md`

## Global Constraints

- The default is `--latest-by dsconf` and it must make **zero** SAM calls. The `--emit` chain path depends on this.
- Time source is the SAM **definition** creation date via the existing `samweb_wrapper.definition_creation_date(defname) -> Optional[datetime]`. Do not use file-level timestamps.
- Query dates **only** for contended descriptions (2+ versions). A single-version description must never cost a SAM call.
- A missing date on a contended dataset **fails loudly**: exit non-zero, listing every offender by name. Never fall back to dsconf order.
- Row/presentation ordering is unchanged. Only *within-group* ordering (which member wins) depends on the key.
- stdout stays machine-readable (it is JSON under `--emit`). All progress and diagnostics go to stderr.
- `utils/samweb_wrapper.py` is **not** modified by this plan.
- The flag applies identically in lister mode, under `--superseded`, and under `--emit`.
- `EXAMPLES.md` is a derived artifact. Regenerate it with the `/refresh-examples` slash command; never hand-edit it.
- Do **not** modify `_narrow_to_latest_release`. Its analogous lex bug is recorded in the spec as an out-of-scope follow-up.
- Do **not** `git push`. Commit only.

## Starting state

`utils/latestDatasets.py` already has an uncommitted partial edit from before this plan: the module docstring documents `--latest-by time` and `from datetime import datetime` is imported but unused. Both become correct once Task 1 and Task 2 land — do not revert them. The same file also carries unrelated uncommitted `--superseded` work; leave it alone.

Baseline: `python3 test/test_unit.py` reports `Ran 670 tests ... OK`.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `utils/latestDatasets.py` | grouping, selection, CLI | all production changes |
| `test/test_unit.py` | unit tests | 5 tests appended to section 32 (`TestLatestPerDescription`, ends at the `test_superseded_skips_unparseable` method) |
| `EXAMPLES.md` | derived CLI reference | regenerated in Task 3 |

---

### Task 1: Injected order key in grouping and selection

Pure functions only — no SAM, no CLI. Makes the ordering decision injectable and proves both consumers honor it.

**Files:**
- Modify: `utils/latestDatasets.py` — `_group_by_description`, `latest_per_description`, `superseded_per_description`
- Test: `test/test_unit.py` — append to `class TestLatestPerDescription`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_group_by_description(names, order_key=None) -> (groups, skipped)` where `groups` maps `description -> [(dsconf, name), ...]` sorted ascending
  - `latest_per_description(names, order_key=None) -> (rows, skipped)`, `rows` = `[(description, dsconf, name, count), ...]`
  - `superseded_per_description(names, order_key=None) -> (rows, skipped)`, same row shape
  - `order_key` is `Optional[Callable[[str], Any]]`; `None` means sort by dsconf

- [ ] **Step 1: Write the failing tests**

Append these two methods to `class TestLatestPerDescription` in `test/test_unit.py`, immediately after `test_superseded_skips_unparseable`:

```python
    def test_injected_order_key_overrides_dsconf(self):
        """Real MDC2020 case: the ntuple series sorts BELOW the release series
        lexicographically ('-' < 'a') but was created six months later. An
        injected date key must beat dsconf order."""
        import datetime as _dt
        from utils.latestDatasets import latest_per_description
        stale = "nts.mu2e.CeEndpointMix1BBTriggered.MDC2020aw_best_v1_3_v06_06_00.root"
        newest = "nts.mu2e.CeEndpointMix1BBTriggered.MDC2020-001.root"
        dates = {stale: _dt.datetime(2025, 9, 6), newest: _dt.datetime(2026, 3, 10)}
        # dsconf order picks the stale one -- this is the bug being fixed
        rows, _ = latest_per_description([stale, newest])
        self.assertEqual(rows[0][2], stale)
        # the injected key picks the actually-newest
        rows, _ = latest_per_description([stale, newest], order_key=dates.__getitem__)
        self.assertEqual(rows[0][2], newest)

    def test_superseded_honors_same_order_key(self):
        """--superseded means 'every version that is not the latest', so it must
        order by the SAME key -- otherwise a dataset lands in both listings or
        in neither."""
        import datetime as _dt
        from utils.latestDatasets import (latest_per_description,
                                          superseded_per_description)
        a_stale = "nts.mu2e.A.MDC2020aw_best_v1_3_v06_06_00.root"
        a_new = "nts.mu2e.A.MDC2020-001.root"
        b_only = "nts.mu2e.B.MDC2020-001.root"
        names = [a_stale, a_new, b_only]
        dates = {a_stale: _dt.datetime(2025, 9, 6),
                 a_new: _dt.datetime(2026, 3, 10),
                 b_only: _dt.datetime(2026, 3, 10)}
        key = dates.__getitem__
        latest = {n for _, _, n, _ in latest_per_description(names, key)[0]}
        sup = {n for _, _, n, _ in superseded_per_description(names, key)[0]}
        self.assertEqual(latest, {a_new, b_only})
        self.assertEqual(sup, {a_stale})
        self.assertEqual(sup, set(names) - latest)      # exact complement
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
python3 test/test_unit.py TestLatestPerDescription 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)|TypeError"
```
Expected: FAILED, 2 errors, both `TypeError` on the not-yet-existent parameter —
`latest_per_description() got an unexpected keyword argument 'order_key'` from
the first test, and `latest_per_description() takes 1 positional argument but 2
were given` from the second (it passes the key positionally).

- [ ] **Step 3: Add the order_key parameter**

In `utils/latestDatasets.py`, replace the body of `_group_by_description` and the signatures of the two consumers.

`_group_by_description` becomes:

```python
def _group_by_description(names, order_key=None):
    """Group dataset names by description (3rd field), each group's members
    sorted ascending. Returns (groups, skipped) where groups maps
    description -> [(dsconf, name), ...] and skipped holds unparseable names.

    order_key: optional callable name -> sortable, deciding which member of a
    group counts as latest. Default (None) orders by dsconf lexicographically,
    which tracks campaign letter then version WITHIN a single naming series.
    Pass a key when a description spans series, where lex order is meaningless
    -- see _creation_date_key."""
    groups = defaultdict(list)
    skipped = []
    for name in names:
        parsed = parse_name(name)
        if parsed is None:
            skipped.append(name)
            continue
        description, dsconf = parsed
        groups[description].append((dsconf, name))
    if order_key is None:
        rank = lambda item: item[0]             # item = (dsconf, name)
    else:
        rank = lambda item: order_key(item[1])
    for items in groups.values():
        items.sort(key=rank)
    return groups, skipped
```

`latest_per_description` becomes:

```python
def latest_per_description(names, order_key=None):
    """Return list of (description, latest_dsconf, latest_name, count).

    order_key decides which member of each group wins -- see
    _group_by_description. Row order is always by description, independent of
    the key: selection changes, presentation stays stable."""
    groups, skipped = _group_by_description(names, order_key)
    rows = []
    for description, items in groups.items():
        latest_dsconf, latest_name = items[-1]
        rows.append((description, latest_dsconf, latest_name, len(items)))
    rows.sort(key=lambda r: r[0])
    return rows, skipped
```

`superseded_per_description` becomes:

```python
def superseded_per_description(names, order_key=None):
    """Inverse of latest_per_description: every group member EXCEPT the latest,
    i.e. datasets replaced by a newer sibling of the same description.
    Returns (rows, skipped) with rows = (description, dsconf, name, count)
    sorted by (description, dsconf); count is the total number of versions in
    that description's group. Descriptions with a single version contribute
    nothing (they have no replacement).

    MUST be given the same order_key as latest_per_description -- the two are
    set complements, so differing keys would put a dataset in both listings or
    in neither."""
    groups, skipped = _group_by_description(names, order_key)
    rows = []
    for description, items in groups.items():
        for dsconf, name in items[:-1]:      # all but the latest = superseded
            rows.append((description, dsconf, name, len(items)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows, skipped
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
python3 test/test_unit.py TestLatestPerDescription 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)"
```
Expected: `OK` with 7 tests (5 pre-existing + 2 new).

- [ ] **Step 5: Run the full suite for regressions**

Run:
```bash
python3 test/test_unit.py 2>&1 | grep -E "^(Ran|OK|FAILED)"
```
Expected: `Ran 672 tests` and `OK`.

- [ ] **Step 6: Commit**

```bash
git add utils/latestDatasets.py test/test_unit.py
git commit -m "feat(latestDatasets): injectable order key for latest-per-desc selection

_group_by_description takes an optional order_key (name -> sortable);
latest_per_description and superseded_per_description forward it, so the two
stay exact set complements by construction rather than by discipline. Default
None preserves the existing dsconf lexicographic sort.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 2: The creation-date key builder

Builds the time key from SAM, querying only contended descriptions, failing loudly on a missing date.

**Files:**
- Modify: `utils/latestDatasets.py` — module-level import line, new `_creation_date_key`
- Test: `test/test_unit.py` — append to `class TestLatestPerDescription`

**Interfaces:**
- Consumes: `_group_by_description(names, order_key=None)` from Task 1 is not called here; this task only needs `parse_name(name) -> Optional[(description, dsconf)]`, which already exists.
- Produces: `_creation_date_key(names) -> Callable[[str], datetime]`, suitable as the `order_key` argument of `latest_per_description` / `superseded_per_description`.

**Why the import moves to module level:** the tests patch with `patch.object(latestDatasets, 'definition_creation_date', ...)`, matching the existing `_dataset_exists` patching convention in this file. A function-local import would not be patchable that way.

- [ ] **Step 1: Write the failing tests**

Append these two methods to `class TestLatestPerDescription` in `test/test_unit.py`, after the two added in Task 1:

```python
    def test_creation_date_key_queries_only_contended(self):
        """Single-version descriptions have nothing to compare against, so they
        must never cost a SAM call."""
        import datetime as _dt
        from utils import latestDatasets
        a_one = "nts.mu2e.A.MDC2020aw_best_v1_3_v06_06_00.root"   # A: contended
        a_two = "nts.mu2e.A.MDC2020-001.root"                     # A: contended
        b_only = "nts.mu2e.B.MDC2020-001.root"                    # B: singleton
        asked = []

        def fake(name):
            asked.append(name)
            return _dt.datetime(2026, 3, 10)

        with patch.object(latestDatasets, 'definition_creation_date',
                          side_effect=fake):
            key = latestDatasets._creation_date_key([a_one, a_two, b_only])
        self.assertEqual(set(asked), {a_one, a_two})
        self.assertNotIn(b_only, asked)
        # the unqueried singleton still gets a usable rank, not a KeyError
        self.assertEqual(key(b_only), _dt.datetime.min)

    def test_creation_date_key_fails_loud_on_missing_date(self):
        """No date for a contended dataset must abort, naming it -- never
        silently revert to lexicographic order."""
        import datetime as _dt
        from utils import latestDatasets
        dated = "nts.mu2e.A.MDC2020aw_best_v1_3_v06_06_00.root"
        undated = "nts.mu2e.A.MDC2020-001.root"
        with patch.object(latestDatasets, 'definition_creation_date',
                          side_effect=lambda n: None if n == undated
                          else _dt.datetime(2025, 9, 6)):
            with self.assertRaises(SystemExit) as cm:
                latestDatasets._creation_date_key([dated, undated])
        self.assertIn(undated, str(cm.exception))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
python3 test/test_unit.py TestLatestPerDescription 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)|AttributeError"
```
Expected: FAILED — `AttributeError: <module 'utils.latestDatasets'> does not have the attribute 'definition_creation_date'`.

- [ ] **Step 3: Add the import and the key builder**

In `utils/latestDatasets.py`, extend the existing module-level import (currently `from utils.samweb_wrapper import dataset_file_count, definitions_matching`) to:

```python
from utils.samweb_wrapper import (dataset_file_count, definition_creation_date,
                                  definitions_matching)
```

Then add `_creation_date_key` immediately after `superseded_per_description`:

```python
def _creation_date_key(names):
    """Build an order_key ranking datasets by SAM definition creation date.

    Only CONTENDED descriptions (2+ versions) are queried: a single-version
    group has nothing to compare against, so its date is never needed. On a
    ~20-desc --emit run where most descs have one version, that is ~2 SAM
    calls instead of ~20.

    Fails loudly if SAM has no date for a contended dataset. Quietly reverting
    to dsconf order would answer a --latest-by time question with a
    lexicographic result -- the exact bug this mode exists to fix, made
    invisible. definition_creation_date is fail-soft and returns None on a SAM
    error, so an outage lands here too and surfaces as a loud failure."""
    by_desc = defaultdict(list)
    for name in names:
        parsed = parse_name(name)
        if parsed is not None:
            by_desc[parsed[0]].append(name)
    contended = [n for group in by_desc.values() if len(group) > 1 for n in group]
    if contended:
        # Status to stderr (stdout stays machine-readable): one SAM round trip
        # per dataset is the slow part, so signal it even when muted.
        print(f"Querying creation dates for {len(contended)} dataset(s), "
              f"please wait...", file=sys.stderr)
    dates = {}
    for name in contended:
        dates[name] = definition_creation_date(name)
        _vlog(f"# created {dates[name]}: {name}")
    undated = sorted(n for n, d in dates.items() if d is None)
    if undated:
        sys.exit("latestDatasets: --latest-by time: SAM has no creation date "
                 "for:\n" + "\n".join(f"  {n}" for n in undated))
    # Uncontended names were never queried, so they are absent from `dates`.
    # Their rank is never consulted (they are alone in their group), but a bare
    # dates[name] would raise KeyError.
    return lambda name: dates.get(name, datetime.min)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
python3 test/test_unit.py TestLatestPerDescription 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)"
```
Expected: `OK` with 9 tests.

- [ ] **Step 5: Run the full suite for regressions**

Run:
```bash
python3 test/test_unit.py 2>&1 | grep -E "^(Ran|OK|FAILED)"
```
Expected: `Ran 674 tests` and `OK`.

- [ ] **Step 6: Commit**

```bash
git add utils/latestDatasets.py test/test_unit.py
git commit -m "feat(latestDatasets): creation-date order key, contended-only

_creation_date_key queries samweb definition creation dates for descriptions
with 2+ versions only -- a singleton has nothing to compare against. A missing
date aborts naming every offender rather than reverting to dsconf order, which
would answer a time question with a lexicographic result.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 3: CLI flag, wiring, and docs

Exposes the choice as `--latest-by`, threads it through all three call sites, and regenerates the derived CLI reference.

**Files:**
- Modify: `utils/latestDatasets.py` — new `_order_key_for`, new argparse flag, three call sites (`_emit`, and the `--superseded` and default branches of `main`)
- Modify: `EXAMPLES.md` — regenerated, not hand-edited
- Test: `test/test_unit.py` — append to `class TestLatestPerDescription`

**Interfaces:**
- Consumes: `_creation_date_key(names) -> Callable[[str], datetime]` (Task 2); `latest_per_description(names, order_key=None)` and `superseded_per_description(names, order_key=None)` (Task 1).
- Produces: `_order_key_for(latest_by, names) -> Optional[Callable[[str], Any]]`, and the `--latest-by` CLI flag with `args.latest_by` defaulting to `"dsconf"`.

- [ ] **Step 1: Write the failing test**

Append this method to `class TestLatestPerDescription` in `test/test_unit.py`, after the two added in Task 2:

```python
    def test_dsconf_mode_makes_no_sam_calls(self):
        """The default path must stay free of SAM round trips -- the --emit
        chain relies on it."""
        from utils import latestDatasets

        def boom(name):
            raise AssertionError(f"SAM queried in dsconf mode: {name}")

        with patch.object(latestDatasets, 'definition_creation_date',
                          side_effect=boom):
            key = latestDatasets._order_key_for(
                "dsconf", ["nts.mu2e.A.MDC2020-000.root",
                           "nts.mu2e.A.MDC2020-001.root"])
        self.assertIsNone(key)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
python3 test/test_unit.py TestLatestPerDescription.test_dsconf_mode_makes_no_sam_calls 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)|AttributeError"
```
Expected: FAILED — `AttributeError: ... does not have the attribute '_order_key_for'`.

- [ ] **Step 3: Add the dispatcher**

In `utils/latestDatasets.py`, add immediately after `_creation_date_key`:

```python
def _order_key_for(latest_by, names):
    """Resolve the --latest-by choice to an order_key for
    _group_by_description. 'dsconf' -> None: lexicographic, zero SAM calls."""
    return _creation_date_key(names) if latest_by == "time" else None
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
python3 test/test_unit.py TestLatestPerDescription.test_dsconf_mode_makes_no_sam_calls 2>&1 | grep -E "^(Ran|OK|FAILED|ERROR)"
```
Expected: `OK`.

- [ ] **Step 5: Add the CLI flag**

In `main()` of `utils/latestDatasets.py`, add this argument immediately after the existing `--superseded` argument:

```python
    ap.add_argument("--latest-by", choices=("dsconf", "time"), default="dsconf",
                    help="how to pick the latest dataset per description: "
                         "'dsconf' (default) sorts dsconf lexicographically -- "
                         "correct within one naming series, and free of SAM "
                         "queries; 'time' sorts by SAM definition creation "
                         "date -- use it when a description spans naming "
                         "series, where lex order is meaningless (the ntuple "
                         "series MDC2020-001 sorts BELOW "
                         "MDC2020aw_best_v1_3_v06_06_00 because '-' < 'a', yet "
                         "was created six months later)")
```

- [ ] **Step 6: Wire the three call sites**

In `_emit()`, replace:

```python
    rows, skipped = latest_per_description(names)
```

with:

```python
    rows, skipped = latest_per_description(names,
                                           _order_key_for(args.latest_by, names))
```

In `main()`, in the `--superseded` branch, replace:

```python
        srows, sk2 = superseded_per_description(names)
```

with:

```python
        srows, sk2 = superseded_per_description(
            names, _order_key_for(args.latest_by, names))
```

In `main()`, in the default branch, replace:

```python
    rows, skipped = latest_per_description(names)
```

with:

```python
    rows, skipped = latest_per_description(names,
                                           _order_key_for(args.latest_by, names))
```

- [ ] **Step 7: Verify the flag is wired end to end**

`utils/latestDatasets.py` imports `samweb_client` at module level, so it cannot
run bare — the Mu2e environment must be sourced first. (The unit tests sidestep
this by stubbing the module before any `utils` import.) Both commands below were
confirmed working against the pre-change file.

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \
  && muse setup ops > /dev/null 2>&1 \
  && python3 utils/latestDatasets.py --help 2>&1 | grep -A1 "latest-by"
```
Expected: the `--latest-by {dsconf,time}` option appears in the help output.

Then confirm the default path is unchanged and still issues no SAM query, using
stdin so no network lookup is possible:
```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \
  && muse setup ops > /dev/null 2>&1 \
  && printf '%s\n' \
    'nts.mu2e.CeEndpointMix1BBTriggered.MDC2020aw_best_v1_3_v06_06_00.root' \
    'nts.mu2e.CeEndpointMix1BBTriggered.MDC2020-001.root' \
    | python3 utils/latestDatasets.py --stdin
```
Expected, exactly one line — the lexicographic winner, byte-identical to what
the pre-change code prints today:
```
nts.mu2e.CeEndpointMix1BBTriggered.MDC2020aw_best_v1_3_v06_06_00.root
```

- [ ] **Step 8: Run the full suite**

Run:
```bash
python3 test/test_unit.py 2>&1 | grep -E "^(Ran|OK|FAILED)"
```
Expected: `Ran 675 tests` and `OK`.

- [ ] **Step 9: Regenerate the derived CLI reference**

`EXAMPLES.md` is generated from source — do not hand-edit it. Invoke the `/refresh-examples` slash command, then confirm the new flag was picked up:

```bash
grep -n "latest-by" EXAMPLES.md
```
Expected: at least one hit in the `latestDatasets` section.

- [ ] **Step 10: Commit**

```bash
git add utils/latestDatasets.py test/test_unit.py EXAMPLES.md
git commit -m "feat(latestDatasets): --latest-by {dsconf,time}

Exposes the ordering choice and threads it through lister mode, --superseded,
and --emit. Default dsconf is byte-identical to today and issues no SAM query.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

## Manual verification against real SAM (optional, after Task 3)

The unit tests never touch the network. To confirm the fix against the definitions that motivated it, run with the Mu2e environment sourced:

```bash
/mu2e-run latestDatasets --defname 'nts.mu2e.CeEndpointMix1BBTriggered.MDC2020%.root' --latest-by time
```

Expected: `nts.mu2e.CeEndpointMix1BBTriggered.MDC2020-001.root` (definition created 2026-03-10), where the default `--latest-by dsconf` returns `...MDC2020aw_best_v1_3_v06_06_00.root` (created 2025-09-06).

This is a read-only SAM query — no `mu2epro`, no writes.
