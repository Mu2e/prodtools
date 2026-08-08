# POMS Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the POMS submission backend (dispatch, recovery, monitoring, SQLAlchemy) from prodtools, leaving the direct backend as the only submission path.

**Architecture:** Five dependency-ordered deletion commits, leaf-to-root: monitoring toolchain first (kills SQLAlchemy), then mkrecovery (its three shared helpers move out first), then runmu2e's POMS dispatch tail, then the `poms_entry` → `map_entry` rename, then docs. The `pre-poms-removal` git tag, created before the first deletion, is the recovery escape hatch for legacy POMS stages.

**Tech Stack:** Python 3 (no new dependencies — this plan only removes one: SQLAlchemy), unittest test suite, git.

**Spec:** `docs/superpowers/specs/2026-08-08-poms-removal-design.md`

## Global Constraints

- Test suite must pass after EVERY task: `python3 test/test_unit.py 2>&1 | tail -3` → `OK` (skips are fine). Run from the repo root.
- MCP health check must pass after Tasks 2 and 4: `bash mcp/scripts/start_mcp.sh --check` → exit 0.
- `git tag pre-poms-removal` is created in Task 1 BEFORE any deletion and must reach every deleted file.
- NEVER touch: `validate_jobdesc`, `process_jobdef`, `process_g4bl_jobdef` (runmu2e.py — all shared or out of scope), the `poms_map/` path strings (external directory, name is historical), anything under the direct-submission subsystem (`submissions.py` logic, `submission_ledger.py`, `submit.py` beyond the two help-text rewords named in Task 2).
- `process_g4bl_jobdef` becomes uncalled in-repo after Task 3 (its only caller was `_dispatch_and_execute`). This is EXPECTED — leave it in place. Deleting it is out of scope (flagged as a follow-up).
- Do NOT hand-edit `EXAMPLES.md` except via the regeneration step in Task 5.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
  ```
- Do NOT `git push` at any point.
- Final acceptance (after Task 5): `grep -ri poms utils/ bin/ test/ mcp/src web/ | grep -v poms_map | grep -v __pycache__` returns only comments describing the external map convention or history; `grep -rn sqlalchemy utils/ bin/ test/` returns nothing.

---

### Task 1: Tag the base and delete the POMS monitoring toolchain

**Files:**
- Delete: `utils/poms_db.py`, `utils/db_builder.py`, `utils/db_analyzer.py`, `utils/pomsMonitor.py`, `utils/_guards.py`, `bin/pomsMonitor`, `bin/update_pomsmonitor_web`, `web/pomsMonitor/` (entire directory)
- Modify: `test/test_unit.py` (four regions, below)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: a repo with no SQLAlchemy consumer. Task 2 assumes `utils/db_builder.py` is gone (its `sam_physical_path` import no longer exists).

**Context for the implementer:** These modules are the POMS monitoring stack: `poms_db.py` declares SQLAlchemy ORM models; `db_builder.py` builds a SQLite DB from POMS maps; `db_analyzer.py`/`pomsMonitor.py` are the CLI over it; `web/pomsMonitor/` renders a static dashboard; `_guards.py` is an import-gate whose only consumer is `bin/pomsMonitor` (verify: `grep -rn "_guards" bin/ utils/ | grep -v _guards.py` → only `bin/pomsMonitor`). Nothing else in `utils/` imports any of them (verify: `grep -rn "poms_db\|db_builder\|db_analyzer\|pomsMonitor" utils/ bin/ mcp/src | grep -v __pycache__ | grep -vE "utils/(poms_db|db_builder|db_analyzer|pomsMonitor)\.py|bin/(pomsMonitor|update_pomsmonitor_web)"` → only the `job_common.py:437` comment, handled in Task 2).

- [ ] **Step 1: Create the escape-hatch tag**

```bash
git tag pre-poms-removal
git tag -l pre-poms-removal   # verify it printed
```

- [ ] **Step 2: Delete the modules and scripts**

```bash
git rm utils/poms_db.py utils/db_builder.py utils/db_analyzer.py \
       utils/pomsMonitor.py utils/_guards.py \
       bin/pomsMonitor bin/update_pomsmonitor_web
git rm -r web/pomsMonitor
```

(`web/pomsMonitor/__pycache__` is untracked; if `git rm -r` leaves it, `rm -rf web/pomsMonitor` afterwards.)

- [ ] **Step 3: Delete the SQLAlchemy test scaffolding in `test/test_unit.py`**

Around line 40-59. Delete the `'poms_client'` entry from `_STUB_MODULES` (nothing imports poms_client any more — verify with `grep -rn poms_client utils/ bin/`), and delete this whole block:

```python
# SQLAlchemy can't be MagicMock-stubbed (poms_db declares real ORM models),
# so DB-backed tests are skipped when it's absent (plain ops env; see
# reference_pyenv_ana_for_db).
try:
    import sqlalchemy  # noqa: F401
    _HAVE_SQLALCHEMY = True
except ImportError:
    _HAVE_SQLALCHEMY = False
requires_sqlalchemy = unittest.skipUnless(
    _HAVE_SQLALCHEMY,
    "requires SQLAlchemy (source pyenv.sh ana after muse setup ops)")
```

All three `@requires_sqlalchemy` usages (lines ~3608, ~3625, ~3688) sit on classes deleted in Step 4, so nothing dangles.

- [ ] **Step 4: Delete the gencount + uniformity test block**

The whole section starting at the marker near line 3604:

```python
# ---------------------------------------------------------------------------
# 35. gencount + uniformity (poms_db.DatasetInfo, db_builder, pomsMonitor)
# ---------------------------------------------------------------------------
```

through the end of `TestUniformityReport` (three classes: `TestDatasetInfoGencount`, `TestGetDatasetGencount`, `TestUniformityReport`; the block ends where the next `# ----` section header before `Mu2eJobPars` contract tests begins, near line 3745).

- [ ] **Step 5: Delete the jobs_payload test block**

The section near line 4860:

```python
# ---------------------------------------------------------------------------
# 35. jobs_payload: static dashboard data builder (web/pomsMonitor/jobs_payload.py)
# ---------------------------------------------------------------------------
class TestJobsPayload(unittest.TestCase):
```

through the end of `TestJobsPayload` (ends at the `Submission ledger` section header near line 4902).

- [ ] **Step 6: Reword the stale docstring in `test_log_dataset_matches_legacy_helper`** (near line 321)

Old:
```python
        """Pinned against db_builder._jobdef_to_log_dataset's published output.

        Imported indirectly (expected values listed inline) because db_builder
        uses `str | None` syntax that needs Python 3.10+.
        """
```
New:
```python
        """Pinned against the published output of the legacy
        db_builder._jobdef_to_log_dataset helper (deleted with the POMS
        monitoring toolchain; expected values listed inline).
        """
```
The test itself exercises `Mu2eName.log_dataset()` and stays.

- [ ] **Step 7: Run the suite**

Run: `python3 test/test_unit.py 2>&1 | tail -3`
Expected: `OK` (total test count drops by the deleted classes; no errors, no failures).

- [ ] **Step 8: Audit and commit**

```bash
grep -rn "poms_db\|db_builder\|db_analyzer\|pomsMonitor\|_guards\|requires_sqlalchemy" utils/ bin/ test/ mcp/src | grep -v __pycache__
```
Expected: only the `utils/job_common.py:437` comment (Task 2 rewords it) and historical mentions inside `utils/mkrecovery.py` if any (deleted in Task 2).

```bash
git add -A test/test_unit.py
git commit -m "refactor(poms)!: delete POMS monitoring toolchain

poms_db/db_builder/db_analyzer/pomsMonitor + web/pomsMonitor static
dashboard + _guards import gate (bin/pomsMonitor was its only
consumer). Kills the repo's last SQLAlchemy dependency — the pyenv-ana
requirement for monitoring tools is gone. Legacy code reachable at tag
pre-poms-removal."
```
(append the footer lines from Global Constraints)

---

### Task 2: Retire mkrecovery (move the three shared helpers first)

**Files:**
- Modify: `utils/jobdef_lookup.py` (add two functions), `utils/file_resolver.py` (add one function), `utils/submissions.py:39-43,188,266,396,625`, `mcp/src/prodtools_mcp/tools/status.py:190-200`, `utils/job_common.py:434-438`, `utils/submit.py:402-409,826-828`, `test/test_unit.py` (four regions)
- Delete: `utils/mkrecovery.py`, `bin/mkrecovery`

**Interfaces:**
- Consumes: Task 1 done (db_builder gone).
- Produces: `jobdef_lookup.build_file_maps(job_io, datasets, njobs, firstjob=0, indices=None) -> dict`, `jobdef_lookup.extract_datasets_from_tarball(job_pars, njobs) -> list`, `file_resolver.sam_physical_path_or_none(filename, prefer_location=None) -> str|None`. Task 4 renames their `poms_entry` sibling imports; this task does not touch `poms_entry`.

- [ ] **Step 1: Add the two moved functions to `utils/jobdef_lookup.py`**

Append at the end of the file (it already imports `Mu2eName` from `utils.job_common` — no new imports needed). Copy verbatim from `utils/mkrecovery.py`:

```python
def build_file_maps(job_io, datasets, njobs, firstjob=0, indices=None):
    """One pass over the cnf's index window building, for each dataset in
    `datasets`, its {filename: window-relative index} map. job_outputs
    returns every output stream per call, so a single scan serves all of
    an entry's datasets (previously one full njobs-scan per dataset —
    and one fresh tarball parse each, megabytes for mixing cnfs).

    With `indices` given, scan exactly those indices instead of
    range(njobs) — map values are the indices as passed (the recovery
    loop passes ABSOLUTE cnf indices with firstjob=0, so values come
    back in the caller's own index space). njobs is ignored in that
    case.

    Structured dataset compare — a substring test would false-match
    sibling dsconfs where one is a prefix of the other (e.g. ..._v1_4 vs
    ..._v1_4-000).
    """
    wanted = set(datasets)
    maps = {ds: {} for ds in datasets}
    scope = indices if indices is not None else range(njobs)
    for job_idx in scope:
        for filename in job_io.job_outputs(firstjob + job_idx).values():
            try:
                ds = str(Mu2eName.parse(filename).dataset)
            except ValueError:
                continue
            if ds in wanted:
                maps[ds][filename] = job_idx
    return maps


def extract_datasets_from_tarball(job_pars, njobs):
    """Extract output dataset names from an already-parsed job definition
    (a Mu2eJobPars instance — parsing is the expensive part, so the caller
    parses once and shares the instance with build_file_maps)."""
    output_datasets = job_pars.output_datasets()

    # If output_datasets is empty, extract from actual output files
    if not output_datasets:
        dataset_set = set()
        for idx in range(min(10, njobs)):
            for filename in job_pars.job_outputs(idx).values():
                # Extract dataset name from filename (force .art extension to
                # match historical behavior — outputs may have other exts).
                try:
                    n = Mu2eName.parse(filename)
                except ValueError:
                    continue
                dataset_set.add(str(n.with_extension('art').dataset))
        output_datasets = list(dataset_set)

    return output_datasets
```

- [ ] **Step 2: Add `sam_physical_path_or_none` to `utils/file_resolver.py`**

Directly after `sam_physical_path` (ends near line 237):

```python
def sam_physical_path_or_none(filename, prefer_location=None):
    """sam_physical_path, but returns None instead of raising when SAM
    has no usable location. For swallow-and-skip consumers (submissions
    verify loop, MCP status) — formerly utils.mkrecovery.locate_tarball.
    NOT the same as jobdef_lookup.locate_tarball, which takes a cnf
    DEFNAME and raises."""
    try:
        return sam_physical_path(filename, prefer_location)
    except Exception:
        return None
```

- [ ] **Step 3: Rewire `utils/submissions.py`**

Import block — old (lines 39-43):
```python
from utils.file_resolver import infer_dataset_location
from utils.job_common import Mu2eName, expected_outputs_for
from utils.jobquery import Mu2eJobPars
from utils.mkrecovery import (build_file_maps, extract_datasets_from_tarball,
                              locate_tarball)
```
New:
```python
from utils.file_resolver import infer_dataset_location, sam_physical_path_or_none
from utils.job_common import Mu2eName, expected_outputs_for
from utils.jobdef_lookup import build_file_maps, extract_datasets_from_tarball
from utils.jobquery import Mu2eJobPars
```

Then the four `locate_tarball` sites (two are default parameter values — do not miss them):
- line ~188: `tarball_path = locate_tarball(row['tarball'])` → `tarball_path = sam_physical_path_or_none(row['tarball'])`
- line ~266: `locate=locate_tarball, count_fn=dataset_file_count):` → `locate=sam_physical_path_or_none, count_fn=dataset_file_count):`
- line ~396: `locate=locate_tarball):` → `locate=sam_physical_path_or_none):`
- line ~625: `tarball_path = locate_tarball(row['tarball'])` → `tarball_path = sam_physical_path_or_none(row['tarball'])`

Verify: `grep -n locate_tarball utils/submissions.py` → no matches.

- [ ] **Step 4: Rewire `mcp/src/prodtools_mcp/tools/status.py`** (near line 196, inside `_default_job_pars_fn`)

Old:
```python
    from utils.mkrecovery import locate_tarball
    path = locate_tarball(tarball)
```
New:
```python
    from utils.file_resolver import sam_physical_path_or_none
    path = sam_physical_path_or_none(tarball)
```

- [ ] **Step 5: Update `test/test_unit.py`**

1. `TestBuildFileMapsScoped` (near line 6154): `from utils.mkrecovery import build_file_maps` → `from utils.jobdef_lookup import build_file_maps`.
2. Delete class `TestMkrecoveryPrintIndices` (lines ~4297-4317, ends before `class TestLogStorageLocation`).
3. Delete class `TestMkrecoveryWindow` (lines ~4794-4820, ends before `class TestValidateWindow`). `TestValidateWindow` and `TestValidateJobdescFirstjob` STAY (they test `poms_entry.validate_window` and `runmu2e.validate_jobdesc`, both shared).
4. Comment near line 4258 (`--indices-file` test docstring): reword `Consumes `mkrecovery --print-indices` output, whose `# <tarball>`` → `Accepts `#`-prefixed comment headers (the historical mkrecovery --print-indices format), whose `# <tarball>``.
5. Docstrings near lines 3748 and 3763 (Mu2eJobPars contract tests): drop `mkrecovery`/`db_builder` from the consumer lists — e.g. `(mkrecovery, submit, db_builder, jobdef_lookup)` → `(submit, submissions, jobdef_lookup)`; `so mkrecovery` → `so recovery`.

- [ ] **Step 6: Reword the consumer-contract comment in `utils/job_common.py`** (lines ~434-438)

Old:
```python
    # Per-index job arithmetic. These are THE single implementation —
    # the worker names its actual output files through them (via
    # Mu2eJobFCL.generate_fcl), so every other consumer (mkrecovery,
    # submit, db_builder, jobdef_lookup) must get identical answers.
```
New:
```python
    # Per-index job arithmetic. These are THE single implementation —
    # the worker names its actual output files through them (via
    # Mu2eJobFCL.generate_fcl), so every other consumer (submit,
    # submissions, jobdef_lookup) must get identical answers.
```

- [ ] **Step 7: Reword the two `mkrecovery` references in `utils/submit.py`**

Docstring near line 402-409 — old: `` `mkrecovery --print-indices` output, which headers each tarball with `# <tarball>`, pipes straight in`` → new: ``an index dump that headers each tarball with `# <tarball>` pipes straight in``.

Help text near line 826-828 — old: `'Consumes '` / `` '`mkrecovery --print-indices` output directly.' `` → new: `'`#` comment lines (e.g. per-tarball headers) are ignored.'` (keep the rest of the help string).

- [ ] **Step 8: Delete mkrecovery**

```bash
git rm utils/mkrecovery.py bin/mkrecovery
```

- [ ] **Step 9: Run the suite and the MCP check**

Run: `python3 test/test_unit.py 2>&1 | tail -3` → `OK`.
Run: `bash mcp/scripts/start_mcp.sh --check` → exit 0.
Run: `grep -rn mkrecovery utils/ bin/ test/ mcp/src | grep -v __pycache__` → no matches.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor(poms)!: retire mkrecovery

build_file_maps + extract_datasets_from_tarball move to jobdef_lookup
(cnf introspection); locate_tarball's swallow-None flavour becomes
file_resolver.sam_physical_path_or_none (distinct from
jobdef_lookup.locate_tarball, which takes a defname and raises).
find_missing_indices — the sixth parallel completeness implementation —
and the SAM index-recovery path die with the POMS backend."
```
(append the footer lines)

---

### Task 3: Delete the POMS dispatch path

**Files:**
- Modify: `utils/runmu2e.py` (three regions), `utils/prod_utils.py:14-21,208-249`, `utils/json2jobdef.py:607`

**Interfaces:**
- Consumes: Tasks 1-2 done.
- Produces: `prod_utils.summarize_map(jobdefs_file)` (replaces `summarize_and_index(jobdefs_file, prod=True)`); `runmu2e.main()` is direct-mode-only. `validate_jobdesc` and `process_jobdef` keep their exact signatures — `_direct_dispatch` calls both.

- [ ] **Step 1: Delete `_dispatch_and_execute` from `utils/runmu2e.py`**

The whole function `def _dispatch_and_execute(mode, jobdesc, fname, args):` (lines ~983-1053, ends right before `def main():`). `process_g4bl_jobdef` (its g4bl branch's callee, lines ~413-580) STAYS — see Global Constraints.

- [ ] **Step 2: Make `main()` direct-only**

Delete the `--jobdesc` argument (line ~1062):
```python
    parser.add_argument('--jobdesc', help='Path to the job descriptions JSON file (e.g., jobdefs_list.json). Required for POMS mode; ignored in direct mode (MU2EGRID_JOBDEF set).')
```

Replace the body after `args = parser.parse_args()` — old:
```python
    if _is_direct_mode():
        _direct_main(args)
        return

    if not args.jobdesc:
        print("Error: --jobdesc is required (or set MU2EGRID_JOBDEF for direct mode)")
        sys.exit(1)

    with open(args.jobdesc, 'r') as f:
        jobdesc = json.load(f)
    mode = validate_jobdesc(jobdesc)

    fname = os.getenv("fname")
    if not fname:
        print("Error: fname environment variable is not set.")
        sys.exit(1)

    if _dispatch_and_execute(mode, jobdesc, fname, args):
        sys.exit(1)
```
New:
```python
    if not _is_direct_mode():
        print("Error: MU2EGRID_JOBDEF is not set. runmu2e runs only as the "
              "direct-backend worker; the POMS --jobdesc mode was removed "
              "(recover it from the pre-poms-removal git tag).")
        sys.exit(1)
    _direct_main(args)
```

Also reword the comment near line 911-912 in `_direct_dispatch` — old: `# have no SAM parents — match the POMS-mode logic in _dispatch_and_execute.` → new: `# have no SAM parents.`

- [ ] **Step 3: Replace `summarize_and_index` with `summarize_map` in `utils/prod_utils.py`**

Delete `create_index_definition` entirely (lines ~232-249) and replace `summarize_and_index` (lines ~208-229) with:

```python
def summarize_map(jobdefs_file):
    """Print the per-entry summary of a jobdefs/submission-map JSON.
    Shared by `json2jobdef --prod`. Tolerates njobs-less (generic)
    entries — they contribute 0 to the total."""
    with open(jobdefs_file, 'r') as f:
        jobdefs = json.load(f)

    total_jobs = sum(j.get('njobs', 0) for j in jobdefs)

    for i, j in enumerate(jobdefs):
        outputs = ", ".join(f"{o['dataset']}→{o['location']}" for o in outputs_of(j))
        njobs = njobs_of(j, 0)
        firstjob = firstjob_of(j)
        window = f", cnf window={firstjob}..{firstjob + njobs - 1}" if firstjob else ""
        print(f"[{i}] {tarball_of(j)}: {njobs} jobs, input={inloc_of(j)}, outputs={outputs}{window}")

    print(f"\nTotal: {total_jobs} jobs")
```

Then remove the four now-unused names from the `samweb_wrapper` import block (lines ~14-21): `create_definition`, `delete_definition`, `describe_definition`, `q_dataset_below_sequencer`. Before removing each, verify it has no other use: `grep -n "<name>" utils/prod_utils.py` → only the import line.

- [ ] **Step 4: Update the caller in `utils/json2jobdef.py`** (line ~607)

Old: `summarize_and_index(jobdefs_file, prod=True)`
New: `summarize_map(jobdefs_file)`

(json2jobdef reaches it via `from utils.prod_utils import *`, so no import edit.)

- [ ] **Step 5: Run the suite**

Run: `python3 test/test_unit.py 2>&1 | tail -3` → `OK`.
Also verify no test referenced the deleted names: `grep -n "summarize_and_index\|create_index_definition\|_dispatch_and_execute" test/test_unit.py` → no matches. (`validate_jobdesc` references in tests are expected and stay.)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(poms)!: delete POMS dispatch path

runmu2e loses _dispatch_and_execute and --jobdesc; main() is
direct-mode only (workers arrive via MU2EGRID_JOBDEF + runjob.sh).
validate_jobdesc/process_jobdef stay — the direct path calls both.
create_index_definition goes (SAM index definitions were POMS-only);
summarize_and_index becomes summarize_map. process_g4bl_jobdef is now
uncalled in-repo (kept: g4bl machinery, not POMS — follow-up decides
its fate)."
```
(append the footer lines)

---

### Task 4: Rename `poms_entry` → `map_entry`

**Files:**
- Rename: `utils/poms_entry.py` → `utils/map_entry.py`
- Modify: `utils/prod_utils.py:13,305`, `utils/submissions.py:43`, `utils/json2jobdef.py:20,438,446,463`, `utils/submit.py:36,458`, `utils/jobsub_argv.py:22`, `mcp/src/prodtools_mcp/tools/status.py:151,236,310`, `test/test_unit.py` (all `utils.poms_entry` imports + comments at ~375, ~379, ~5158)

**Interfaces:**
- Consumes: Tasks 1-3 done (the deleted modules were the other importers).
- Produces: `utils.map_entry` with the identical public surface (`tarball_of`, `outputs_of`, `njobs_of`, `inloc_of`, `firstjob_of`, `validate_window`, `resources_of`, `is_draining`, …). No function is renamed — module name only.

- [ ] **Step 1: Rename the module**

```bash
git mv utils/poms_entry.py utils/map_entry.py
```

- [ ] **Step 2: Update every import and reference**

Mechanical, repo-wide:
```bash
grep -rln "poms_entry" utils/ bin/ test/ mcp/src | grep -v __pycache__
```
In each listed file replace every `utils.poms_entry` → `utils.map_entry` and `from .poms_entry` → `from .map_entry` and `utils/poms_entry.py` (comment paths) → `utils/map_entry.py`. Known sites: `utils/prod_utils.py` (import line 13 + comment 305), `utils/submissions.py:43`, `utils/json2jobdef.py` (import 20 + comments 438/446/463), `utils/submit.py` (import 36 + docstring 458), `utils/jobsub_argv.py:22`, `mcp/src/prodtools_mcp/tools/status.py` (three function-local imports: 151, 236, 310), `test/test_unit.py` (~15 function-local imports + section comments near 375/379/5158/4826-4837/5169/5180/9114/9119).

- [ ] **Step 3: Update the module docstring**

In `utils/map_entry.py`, update the module docstring so it describes "submission-map entries" (the JSON entry shape shared by the direct backend's maps and the historical POMS maps) rather than "POMS-map entries". Keep the phrase "historically the POMS-map entry shape" somewhere in the docstring — the on-disk `poms_map/` directory keeps its name and readers will meet both terms.

- [ ] **Step 4: Verify, run the suite and the MCP check**

Run: `grep -rn "poms_entry" utils/ bin/ test/ mcp/src | grep -v __pycache__` → no matches.
Run: `python3 test/test_unit.py 2>&1 | tail -3` → `OK`.
Run: `bash mcp/scripts/start_mcp.sh --check` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(utils): rename poms_entry to map_entry

The module is the shared submission-map-entry grammar (window
validation, firstjob_of, accessor contracts) used by the direct
backend; with the POMS backend gone the old name was misleading.
Public surface unchanged."
```
(append the footer lines)

---

### Task 5: Docs — EXAMPLES schema + regen, CLAUDE.md, wiki page, review-doc note

**Files:**
- Modify: `docs/EXAMPLES_schema.md`, `EXAMPLES.md` (regenerated only), `CLAUDE.md`, `docs/architecture-review-2026-08-08.md`
- Create: `wiki/pages/2026-08-08-retire-poms-backend.md`

**Interfaces:**
- Consumes: Tasks 1-4 done (the doc edits describe the post-removal reality).
- Produces: nothing consumed later.

- [ ] **Step 1: Edit `docs/EXAMPLES_schema.md`**

Search `grep -n "pomsMonitor\|mkrecovery\|update_pomsmonitor_web\|POMS" docs/EXAMPLES_schema.md` and apply this mapping:

1. Section 11 tool list: remove `` `pomsMonitor`, `` and `` `mkrecovery`, `` from the CLI enumeration; remove `` `update_pomsmonitor_web`, `` from the ops-scripts parenthetical (keep `install_prodtools.sh`, `submissions_cron`).
2. Tribal-knowledge bullet on index filenames (near line 136): keep the fname-encodes-index fact; delete the sentence `` `mkrecovery` writes these as `etc.mu2e.index.000.{idx:07d}.txt`. `` and keep the trailing sentence about the `000` field.
3. Delete the bullet `` - `pomsMonitor` database default path is `poms_data.db` at the repo root (`db_analyzer.get_default_db_path`). `` entirely.
4. Ledger bullet (near line 165): replace the tail `` ; POMS owns its own recovery (`mkrecovery`). `` with `` ; the POMS backend was removed 2026-08 (legacy stages recover from the `pre-poms-removal` git tag). ``
5. Entry-modes bullet (near line 172): replace `They run via POMS campaigns, or the upstream `mu2ejobsub`/`mu2eg4bl` CLIs directly.` with `They run via the upstream `mu2ejobsub`/`mu2eg4bl` CLIs directly (the POMS backend was removed 2026-08).`
6. Any remaining `POMS` mention that describes a live capability (not history): reword to past tense or delete. Mentions of the `poms_map/` directory name stay.

- [ ] **Step 2: Regenerate `EXAMPLES.md`**

If executing in the main session: invoke the `/refresh-examples` skill with the hint `POMS backend removed: drop the pomsMonitor and mkrecovery sections, drop --jobdesc from runmu2e coverage, renumber sections contiguously`. If executing as a subagent: follow `docs/EXAMPLES_schema.md` end-to-end — regenerate the whole file from current source per the schema's rules (every flag verified against current argparse; contiguous numbering; tools enumerated from the current `bin/`).

Verify afterwards: `grep -n "pomsMonitor\|mkrecovery\|--jobdesc" EXAMPLES.md` → no matches.

- [ ] **Step 3: Edit `CLAUDE.md`**

In the Prodtools usage paragraph, old command list:
```
(`json2jobdef`, `jobfcl`, `fcldump`, `runmu2e`, `jobdef`,
`jobquery`, `pomsMonitor`, `famtree`, `logparser`,
`genFilterEff`, `datasetFileList`, `listNewDatasets`, `mkrecovery`,
`copy_to_stash`)
```
New:
```
(`json2jobdef`, `jobfcl`, `fcldump`, `runmu2e`, `jobdef`,
`jobquery`, `famtree`, `logparser`,
`genFilterEff`, `datasetFileList`, `listNewDatasets`,
`copy_to_stash`)
```

- [ ] **Step 4: Create `wiki/pages/2026-08-08-retire-poms-backend.md`**

```markdown
---
title: Retire the POMS backend
tags: [decision, submission, poms, direct-backend, retirement]
sources: [docs/superpowers/specs/2026-08-08-poms-removal-design.md]
updated: 2026-08-08
---

# Decision: Retire the POMS backend

**Date:** 2026-08-08
**Type:** ADR
**Status:** Implemented

## Decision

Remove the POMS submission backend from prodtools entirely: dispatch
(`runmu2e --jobdesc` / `_dispatch_and_execute`), recovery
(`mkrecovery` + SAM index definitions), and monitoring
(`poms_db`/`db_builder`/`db_analyzer`/`pomsMonitor` +
`web/pomsMonitor`). The direct backend (`submit_map --enqueue` +
`submissions run`, specs 2026-07-18/19) is the only submission path.

~2,800 lines deleted; the SQLAlchemy dependency (and the pyenv-ana
requirement for monitoring tools) is gone.

## Escape hatch

Git tag `pre-poms-removal` (immediately before the first deletion
commit) reaches every deleted file. A legacy POMS stage needing a
recovery: scratch-checkout the tag, run `mkrecovery` from there.
In-flight POMS jobs were never at risk — workers execute the tarball
shipped at submit time.

## Alternatives considered

- **Migrate remaining map-033 POMS stages to direct first**: cleanest
  end state, rejected for the extra migration/resubmission work before
  any deletion.
- **Wait for map-033 to drain**: zero risk, rejected because removal
  would block on campaign timelines.
- **Deprecate-then-delete**: rejected — the tag already provides
  rollback and prodtools has a single operator.

## What deliberately stays

- `utils/map_entry.py` (ex-`poms_entry.py`): the submission-map entry
  grammar, shared by the direct backend.
- `validate_jobdesc` / `process_jobdef` in runmu2e: called by the
  direct worker path.
- `process_g4bl_jobdef`: g4bl machinery, not POMS. Now uncalled
  in-repo (its only caller was the POMS dispatch tail) — follow-up
  decides whether the upstream mu2eg4bl path still wants it.
- The `poms_map/` directory name and numbered-map convention
  (external, mu2epro area).
- pushOutput's `_POMS` suffix in `Dataset.Tag` (external tool).

## Operational decommission (separate from the repo commits)

1. Repoint mu2epro's datasetMon crontab entry at a slim script with
   only the original `inspect_datasets.py` loop
   (`/exp/mu2e/app/home/mu2epro/cron/datasetMon/inspect_datasets.py`
   is external and survives); the three dashboard-refresh steps
   (db_builder / build_lineage / render_static) die with the repo code.
2. The synced web checkout at
   `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools/` is never synced
   again; web admins may delete it later.
3. `/web/.../data/poms_data.db` and the published static dashboard
   stay frozen at their last render.
```

- [ ] **Step 5: Append the post-review note to `docs/architecture-review-2026-08-08.md`**

Append at the end of the file:

```markdown
## Post-review update (2026-08-08)

The POMS backend was removed (spec
`docs/superpowers/specs/2026-08-08-poms-removal-design.md`, tag
`pre-poms-removal`). Consequences for the candidates above:

- Candidate 1 (campaign completeness): the sixth implementation
  (`mkrecovery.find_missing_indices`) is deleted; five remain.
- Honourable mention (runmu2e's two dispatch tails): resolved —
  `_dispatch_and_execute` is deleted; only the direct tail remains.
- Candidate 9 note: `resolve_map_index`'s proposed home is now
  `utils/map_entry.py` (renamed from `poms_entry.py`).
```

- [ ] **Step 6: Run the suite one last time and commit**

Run: `python3 test/test_unit.py 2>&1 | tail -3` → `OK`.
Final acceptance greps (from Global Constraints):
```bash
grep -ri poms utils/ bin/ test/ mcp/src web/ | grep -v poms_map | grep -v __pycache__
grep -rn sqlalchemy utils/ bin/ test/
```
First: only historical/`poms_map`-convention comments. Second: empty.

```bash
git add docs/EXAMPLES_schema.md EXAMPLES.md CLAUDE.md \
        docs/architecture-review-2026-08-08.md \
        wiki/pages/2026-08-08-retire-poms-backend.md
git commit -m "docs: regenerate EXAMPLES and record the POMS retirement

Schema loses the pomsMonitor/mkrecovery sections; EXAMPLES.md
regenerated from source. CLAUDE.md tool list updated. Wiki ADR
records the decision, the pre-poms-removal tag, and the operational
decommission steps (crontab repoint is a separate mu2epro action)."
```
(append the footer lines)

---

## Not in this plan (deliberate)

- **Operational decommission** (mu2epro crontab repoint, web-host cleanup): a mu2epro write action performed with the user at the console, per the spec's "Operational decommission" section — not a git task.
- **`process_g4bl_jobdef` deletion**: out of scope; recorded as a follow-up in the wiki page.
- **Memory-file updates** (`~/.claude/projects/*/memory/` entries that reference pomsMonitor/mkrecovery): the controller updates these after the plan completes; not repo work.
