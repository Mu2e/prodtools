# Tool Retirement Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire superseded prodtools CLI surface (evidence-first) so each job has exactly one documented tool: audit → user-approved verdict table → hard deletions in dependency order, with the pomsMonitor Flask app untangled from the static dashboard renderer last.

**Architecture:** Phase A produces a per-candidate verdict table from four evidence checks (callers, external consumers, git history, unique capability); a hard user gate approves it per-item. Phase B deletes approved items leaf-first, one commit each, unit suite green after every commit. The monitor chain is special: `render_static.py` currently *drives the Flask app with a test client* to produce the static page, so Flask retirement means extracting the `/api/jobs` payload builder into a plain module and freezing the rewritten HTML into a static-native template, verified byte-identical before any deletion.

**Tech Stack:** Python 3 (argparse CLIs, SQLAlchemy via `pyenv ana`), unittest (`test/test_unit.py`, 342 tests at baseline), bash bin-wrappers, SAM via `utils/samweb_wrapper`.

**Spec:** `docs/superpowers/specs/2026-07-18-tool-retirement-design.md`

## Global Constraints

- **Hard delete, one pass.** No deprecation stubs. Retired tools are removed outright — tool, orphaned `utils/` module, tests, EXAMPLES.md section.
- **Ambiguity always resolves to KEEP-REVISIT — nothing is deleted on doubt.**
- **The standing do-not-fix list is honored** (worker byte-identity, parity_test duplication, legacy SAM branches — `wiki/pages/2026-07-12-hygiene-tiers-and-kept-duplication.md`). Nothing on it is touched.
- **POMS vs direct submission backend consolidation is out of scope.**
- **Full unit suite green after every removal; a red suite reverts that item's deletion and re-classifies it KEEP-REVISIT.**
- **The static render is verified *before* the Flask deletion commit, not after.**
- **One commit per retired item.** Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov`
- **Environment preamble** for any command touching SQLAlchemy, Flask, or SAM (render, DB build, baseline capture):
  `source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh && muse setup ops && source /cvmfs/mu2e.opensciencegrid.org/bin/pyenv.sh ana`
  The plain unit suite (`python3 test/test_unit.py`) needs no preamble.
- **Scratch area:** use `$SCRATCH` = the session scratchpad directory (see system prompt), never `/tmp` directly.
- **Test-count expectations are indicative** (they assume every conditional task ran); the hard gate at each step is unittest printing `OK`.
- All paths below are relative to the repo root `/exp/mu2e/app/users/oksuzian/muse_050125/prodtools`.

---

### Task 1: Baseline — commit the simplify pass, branch for retirement

**Files:**
- Modify: none (commits existing working tree as-is)

**Interfaces:**
- Produces: branch `tool-retirement` containing the committed 2026-07-18 simplify pass; a clean working tree every later task builds on.

- [ ] **Step 1: Verify the suite is green before committing anything**

Run: `python3 test/test_unit.py 2>&1 | tail -3`
Expected: `OK` and `Ran 342 tests`

- [ ] **Step 2: Commit the simplify pass (everything currently in the working tree, including the untracked wiki page)**

```bash
git add -A
git commit -m "simplify: single homes, batch SAM surface, dead-parameter removal (2026-07-18 pass)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

- [ ] **Step 3: Confirm clean tree and create the retirement branch**

Run: `git status --porcelain | wc -l && git checkout -b tool-retirement`
Expected: `0`, then `Switched to a new branch 'tool-retirement'`

---

### Task 2: Phase A audit → verdict table → USER GATE

**Files:**
- Create: `docs/superpowers/plans/2026-07-18-tool-retirement-verdicts.md`

**Interfaces:**
- Produces: the verdict table every Phase B task's **Condition** line refers to. Verdict values: `RETIRE`, `KEEP`, `FOLD-THEN-RETIRE`, `KEEP-REVISIT`.

- [ ] **Step 1: Caller sweep — one grep per candidate name across all live surfaces**

```bash
for n in mkidxdef pomsMonitorWeb setup_run1b datasetFileList latestDatasets listNewDatasets json-editor json2jobdef.html; do
  echo "=== $n ==="
  grep -rn "$n" --include='*.py' --include='*.sh' --include='*.md' --include='*.html' \
    utils bin web test templates .claude EXAMPLES.md docs 2>/dev/null | grep -v __pycache__
done
```

Classify every hit as: code caller / docs-only / self-reference. Known starting facts (verify, don't re-derive): `utils/mkidxdef.py` is a 17-line argparse shim over `prod_utils.summarize_and_index`, and `json2jobdef --prod` calls that prod_utils function directly, not the mkidxdef module; `datasetFileList`'s utils module is imported by `logparser` and `jobdef_lookup` and its CLI is used by the `/mu2ejobsub-submit` skill.

- [ ] **Step 2: External-consumer sweep**

```bash
ls -la /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/ 2>&1
grep -n pomsMonitor /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/wsgi.py 2>&1
ls /exp/mu2e/app/users/mu2epro/production_manager/ 2>&1 | head
grep -rln 'mkidxdef\|pomsMonitorWeb\|setup_run1b\|latestDatasets\|listNewDatasets' /exp/mu2e/app/users/mu2epro/production_manager/ 2>/dev/null | head
crontab -l 2>/dev/null | grep -i 'poms\|prodtools'
grep -h 'pomsMonitor\|latestDatasets\|listNewDatasets\|mkidxdef\|datasetFileList' ~/.bash_history 2>/dev/null | sort | uniq -c | sort -rn | head -25
```

If a path is unreadable from this host, record "unverifiable from this host" in the verdict row — that is evidence toward KEEP-REVISIT for that item, not silence. The cgi-bin check decides whether a **deployed public WSGI instance** of the Flask app exists; if it does (or is unverifiable), the Flask verdict row must carry an explicit ops-decommission note for the user.

- [ ] **Step 3: pomsMonitor CLI per-flag usage sweep**

```bash
for f in --list --outputs --complete --incomplete --datasets-only --since --needs-processing --ignore --unignore --list-ignored --uniformity --target --round; do
  echo "=== pomsMonitor $f ==="
  grep -rn -- "pomsMonitor.*$f" wiki .claude EXAMPLES.md docs bin/update_pomsmonitor_web web/pomsMonitor/cron_run_inspect_datasets.sh 2>/dev/null | grep -v __pycache__
  grep -h -- "pomsMonitor.*$f" ~/.bash_history 2>/dev/null | head -3
done
```

`--build-db --pattern --db` are cron-load-bearing (`bin/update_pomsmonitor_web`) — automatic KEEP. Every other flag gets its own verdict sub-row. Same sweep for `latestDatasets --names-only` and `--show-count`, and for any other flag the auditor spots that is documented nowhere.

- [ ] **Step 4: History check per candidate**

```bash
for p in bin/mkidxdef utils/mkidxdef.py bin/pomsMonitorWeb web/pomsMonitor/__init__.py \
         web/static/monitor.html web/static/json2jobdef.html web/static/json-editor.html \
         bin/setup_run1b.sh utils/latestDatasets.py utils/listNewDatasets.py; do
  echo "=== $p ==="; git log --follow --oneline -5 -- "$p"
done
```

A file whose last substantive (non-hygiene) change is >6 months old with zero code callers strengthens RETIRE; recent functional commits strengthen KEEP.

- [ ] **Step 5: Write the verdict table**

Create `docs/superpowers/plans/2026-07-18-tool-retirement-verdicts.md` with exactly this structure — one row per item, evidence cited inline:

```markdown
# Tool retirement verdicts (2026-07-18) — Phase A output

| # | Item | Verdict | Evidence (callers / external / history / unique capability) | Notes for Phase B |
|---|------|---------|-------------------------------------------------------------|-------------------|
| 1 | `bin/mkidxdef` + `utils/mkidxdef.py` | | | |
| 2 | Flask app: `bin/pomsMonitorWeb` + `web/pomsMonitor/__init__.py` + `web/static/monitor.html` | | | ops-decommission note if cgi-bin deploy exists |
| 3 | JSON-editor feature: `web/static/json2jobdef.html` + `web/static/json-editor.html` + editor/API routes | | | reachable only via Flask; retiring it is a FEATURE decision |
| 4 | `pomsMonitor` CLI flags (one sub-row per flag from Step 3) | | | |
| 5 | `latestDatasets` vs `listNewDatasets` charter overlap | | | FOLD-THEN-RETIRE triggers Task 6 STOP |
| 6 | `bin/datasetFileList` CLI | | | module is load-bearing regardless |
| 7 | `bin/setup_run1b.sh` | | | |
| 8 | `latestDatasets --names-only` (+ any other vestigial flags found) | | | |

Decision rule: RETIRE requires all three of (no code callers) AND (no
external-consumer evidence, and the external checks were actually
readable) AND (unique capability covered elsewhere or explicitly
retired by the user). Anything else → KEEP or KEEP-REVISIT.
```

- [ ] **Step 6: Commit the audit artifact**

```bash
git add docs/superpowers/plans/2026-07-18-tool-retirement-verdicts.md
git commit -m "audit: tool-retirement verdict table (Phase A evidence)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

- [ ] **Step 7: STOP — USER GATE. Present the verdict table to the user and wait for per-item approval. Record approvals/rejections in the verdicts file (add an `Approved?` column). Do not begin any Phase B task until the user has ruled on every row. If the user rejects Flask retirement or keeps the JSON-editor feature, Tasks 7–9 are skipped entirely and the wiki page (Task 11) records why.**

---

### Task 3: Retire `bin/mkidxdef` + `utils/mkidxdef.py`

**Condition:** verdict row 1 = RETIRE, user-approved. Skip otherwise.

**Files:**
- Delete: `bin/mkidxdef`, `utils/mkidxdef.py`
- Modify: `utils/json2jobdef.py:531` (help text), `utils/prod_utils.py:196` (docstring), `.claude/commands/mu2epro-run.md:26,38`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `prod_utils.summarize_and_index(jobdefs, prod=...)` remains the only index-definition entry point, reached via `json2jobdef --prod`.

- [ ] **Step 1: Confirm no code caller imports the module**

Run: `grep -rn 'mkidxdef' --include='*.py' utils bin web test | grep -v __pycache__ | grep -v 'utils/mkidxdef.py'`
Expected: exactly two hits — the `json2jobdef.py:531` help string and the `prod_utils.py:196` docstring. Any other hit → STOP, re-classify KEEP-REVISIT.

- [ ] **Step 2: Delete the tool and module**

```bash
git rm bin/mkidxdef utils/mkidxdef.py
```

- [ ] **Step 3: Scrub the two prose references**

In `utils/json2jobdef.py` line 531, change the `--prod` help from
`'Production mode: enable pushout and run mkidxdef after generation'` to
`'Production mode: enable pushout and create SAM index definitions after generation'`.

In `utils/prod_utils.py` around line 196, reword the `summarize_and_index` docstring sentence that says it backs "``json2jobdef --prod`` and the standalone ``mkidxdef`` CLI" to say it backs "``json2jobdef --prod``" only.

In `.claude/commands/mu2epro-run.md`: remove `mkidxdef` from the example command list on line 26 and delete the `/mu2epro-run mkidxdef --jobdefs jobdefs_list.json --prod` example on line 38.

- [ ] **Step 4: Suite green, no stray references**

Run: `python3 test/test_unit.py 2>&1 | tail -3 && grep -rn mkidxdef --include='*.py' --include='*.md' utils bin web test .claude | grep -v __pycache__`
Expected: `OK`; the grep returns nothing.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "retire: mkidxdef standalone CLI (json2jobdef --prod is the entry point)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 4: Retire `bin/setup_run1b.sh`

**Condition:** verdict row 7 = RETIRE, user-approved. Skip otherwise.

**Files:**
- Delete: `bin/setup_run1b.sh`

**Interfaces:** none — the script only sources `bin/setup.sh` and prepends `.` to `MU2E_SEARCH_PATH`.

- [ ] **Step 1: Confirm docs-only references**

Run: `grep -rn setup_run1b --include='*.py' --include='*.sh' --include='*.md' utils bin web test .claude templates | grep -v __pycache__ | grep -v 'bin/setup_run1b.sh'`
Expected: nothing (EXAMPLES.md line 33 is regenerated in Task 10, so it does not count; if it appears here, note it and proceed). Any `.py`/`.sh`/skill hit → STOP, KEEP-REVISIT.

- [ ] **Step 2: Delete, verify, commit**

```bash
git rm bin/setup_run1b.sh
python3 test/test_unit.py 2>&1 | tail -3
git commit -m "retire: setup_run1b.sh helper (source bin/setup.sh + MU2E_SEARCH_PATH inline instead)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```
Expected: `OK`, `Ran 342 tests` (count unchanged — no tests touch this script).

- [ ] **Step 3 (only if verdict row 6 = RETIRE, user-approved): retire `bin/datasetFileList` by the same two-step procedure** — reference check (`grep -rn 'bin/datasetFileList\|datasetFileList ' --include='*.py' --include='*.sh' --include='*.md' utils bin web test .claude | grep -v __pycache__`, expect docs-only), then `git rm bin/datasetFileList` ONLY — **never `utils/datasetFileList.py`**, which is load-bearing for `logparser` and `jobdef_lookup` regardless of the CLI's verdict — suite, own commit. Also update the `/mu2ejobsub-submit` skill (`.claude/commands/mu2ejobsub-submit.md:44,106`), which invokes the CLI.

---

### Task 5: Vestigial-flag sweep (worked example: `latestDatasets --names-only`)

**Condition:** verdict row 8 sub-rows = RETIRE, user-approved, per flag. Skip flags not approved.

**Files:**
- Modify: `utils/latestDatasets.py:231-232,299-300`; plus one modify per additional approved flag, at the exact lines the verdict table cites.

**Interfaces:** none — flags being removed are, by verdict, called by nothing.

- [ ] **Step 1: Remove the `--names-only` argument definition**

In `utils/latestDatasets.py`, delete the `add_argument` call at lines 231–232:

```python
    ap.add_argument("--names-only", action="store_true",
                    help=...)   # ← delete this whole call, both lines
```

- [ ] **Step 2: Simplify its only use**

At lines 299–300, replace:

```python
    # (--names-only is accepted as an explicit alias of the default).
    show_count = args.show_count and not args.names_only
```

with:

```python
    show_count = args.show_count
```

- [ ] **Step 3: Verify flag is gone and suite is green**

Run: `python3 bin/latestDatasets --help 2>&1 | grep -c names-only; grep -cn names_only utils/latestDatasets.py; python3 test/test_unit.py 2>&1 | tail -3`
Expected: `0`, `0`, `OK`.

- [ ] **Step 4: Repeat Steps 1–3 for every other verdict-approved flag** — same pattern: delete its `add_argument` at the line the verdict table cites, simplify each `args.<flag>` use site the table cites (the table must cite them; if it doesn't, go back and add them before editing), re-run the tool's `--help` grep and the suite. If a flag's removal turns a test red, revert that flag's edit (`git checkout -- <file>`), mark it KEEP-REVISIT in the verdicts file, and continue with the next flag.

- [ ] **Step 5: Commit (one commit covering the approved flag group)**

```bash
git add -A
git commit -m "retire: vestigial CLI flags per 2026-07-18 verdict table

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 6: `latestDatasets` / `listNewDatasets` fold gate

**Condition:** only if verdict row 5 = FOLD-THEN-RETIRE, user-approved.

- [ ] **Step 1: STOP. Do not improvise a merge.** A fold changes user-facing behavior (flags move between tools, `/recent-datasets` and the chain-emit workflow both re-point). Write a separate mini-spec + plan for the fold (`docs/superpowers/specs/` / `docs/superpowers/plans/`, same process as this one) and get it approved before touching either tool. If the verdict is KEEP (expected — their flag surfaces barely overlap: latest-per-description + `--emit` templates vs recent-by-time + `--completeness`), this task is a no-op; record the clarified one-line charter for each tool in the verdicts file for Task 11's wiki page to quote.

---

### Task 7: Extract the jobs payload builder out of the Flask app

**Condition:** verdict rows 2 and 3 = RETIRE, user-approved (Flask AND the JSON-editor feature — if either is KEEP, skip Tasks 7–9 entirely).

**Files:**
- Create: `web/pomsMonitor/jobs_payload.py`
- Test: `test/test_unit.py` (append a new numbered section at the end)

**Interfaces:**
- Consumes: `utils.poms_db.get_db_session(db_path)`, `utils.poms_db.Job`, `utils.db_analyzer.build_dataset_info_map(session, jobs)`, `utils.samweb_wrapper.locate_files_strict(names)`, `utils.file_resolver.{sam_physical_path, path_from_sam_locations}`, `utils.jobquery.Mu2eJobPars(path).setup()` — all existing, unchanged.
- Produces: `build_jobs_payload(db_path) -> list[dict]` — the exact `/api/jobs` payload (same keys, same order), consumed by Task 8's rewritten `render_static.py`.

- [ ] **Step 1: Write the failing tests** — append to `test/test_unit.py` (next section number after the current last one; import the module by path so the `web/pomsMonitor/__init__.py` WSGI shim, still present until Task 9, is NOT executed):

```python
# NN. jobs_payload: static dashboard data builder (web/pomsMonitor/jobs_payload.py)
class TestJobsPayload(unittest.TestCase):
    """build_jobs_payload replaces the Flask /api/jobs route for render_static."""

    @classmethod
    def setUpClass(cls):
        d = os.path.join(os.path.dirname(__file__), '..', 'web', 'pomsMonitor')
        if d not in sys.path:
            sys.path.insert(0, d)
        import jobs_payload
        cls.jp = jobs_payload

    def test_empty_db_yields_empty_list_and_closes_session(self):
        mock_session = MagicMock()
        mock_session.query.return_value.all.return_value = []
        with patch.object(self.jp, 'get_db_session', return_value=mock_session), \
             patch.object(self.jp, 'build_dataset_info_map', return_value={}):
            self.assertEqual(self.jp.build_jobs_payload('/nonexistent.db'), [])
        mock_session.close.assert_called_once()

    def test_job_row_shape_matches_api_jobs(self):
        job = MagicMock(njobs=3, tarball='', source_file='x.json',
                        complete=True, avg_real_h=None, avg_vmhwm_gb=None,
                        outputs=[])
        mock_session = MagicMock()
        mock_session.query.return_value.all.return_value = [job]
        with patch.object(self.jp, 'get_db_session', return_value=mock_session), \
             patch.object(self.jp, 'build_dataset_info_map', return_value={}):
            payload = self.jp.build_jobs_payload('/nonexistent.db')
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['njobs'], 3)
        self.assertEqual(payload[0]['setup_script'], '')
        self.assertEqual(payload[0]['outputs'], [])
        self.assertEqual(
            sorted(payload[0].keys()),
            sorted(['njobs', 'tarball', 'source_file', 'setup_script',
                    'complete', 'avg_real_h', 'avg_vmhwm_gb', 'outputs']))
```

(If `test_unit.py` does not already import `os`/`sys` at top level, add them; `MagicMock` and `patch` are already imported.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 test/test_unit.py TestJobsPayload -v 2>&1 | tail -5`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'jobs_payload'`

- [ ] **Step 3: Create `web/pomsMonitor/jobs_payload.py`** — the `/api/jobs` route body verbatim minus Flask (no `jsonify`, explicit `db_path`, session closed):

```python
#!/usr/bin/env python3
"""Build the pomsMonitor jobs.json payload straight from the SQLite DB.

Extracted from the retired Flask app's ``/api/jobs`` route so
``render_static.py`` can produce the static dashboard without Flask.
The payload shape (keys and key order) is the route's, unchanged —
``jobs.json`` consumers depend on it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from utils.poms_db import get_db_session, Job
from utils.db_analyzer import build_dataset_info_map
from utils.samweb_wrapper import locate_files_strict
from utils.file_resolver import sam_physical_path, path_from_sam_locations
from utils.jobquery import Mu2eJobPars

_setup_cache = {}


def _setup_scripts(tarballs):
    """Resolve the embedded setup script for each cnf tarball. Cache
    misses are located in ONE batch SAM round-trip (vs one per job row),
    falling back to a per-tarball locate if the batch call fails."""
    todo = [t for t in tarballs if t not in _setup_cache]
    locations = {}
    if todo:
        try:
            locations = locate_files_strict(todo)
        except Exception:
            locations = {}
    for tarball in todo:
        setup = ''
        try:
            locs = locations.get(tarball)
            if locs:
                full_path = path_from_sam_locations(tarball, locs)
            else:
                full_path = sam_physical_path(tarball)
            if os.path.exists(full_path):
                setup = Mu2eJobPars(full_path).setup() or ''
        except Exception:
            # Leave setup empty when SAM or the tarball is unavailable.
            pass
        _setup_cache[tarball] = setup
    return _setup_cache


def build_jobs_payload(db_path):
    """Return the dashboard's jobs list (one dict per jobdef)."""
    session = get_db_session(db_path)
    try:
        all_jobs = session.query(Job).all()
        info_map = build_dataset_info_map(session, all_jobs)
        setup_map = _setup_scripts(
            [job.tarball for job in all_jobs if job.tarball])
        jobs = []
        for job in all_jobs:
            njobs = job.njobs or 0
            outputs = []
            for output in job.outputs:
                info = info_map.get(output.dataset)
                nfiles = int(info.nfiles or 0) if info else 0
                nevts = int(info.nevts or 0) if info else 0

                creation_date_str = None
                if info and info.creation_date:
                    if isinstance(info.creation_date, str):
                        creation_date_str = info.creation_date.split('T')[0]
                    else:
                        creation_date_str = info.creation_date.strftime('%Y-%m-%d')

                outputs.append({
                    'name': output.dataset,
                    'nfiles': nfiles,
                    'nevts': nevts,
                    'events_per_file': round(nevts / nfiles, 2) if nfiles > 0 else 0.0,
                    'avg_size_mb': round((info.total_size or 0) / nfiles / 1e6, 2) if nfiles else 0.0,
                    'status': 'OK' if nfiles >= njobs else 'MISSING',
                    'has_children': info.has_children if info else False,
                    'creation_date': creation_date_str,
                    'location': (info.location or 'N/A') if info else 'N/A'
                })

            setup_script = setup_map.get(job.tarball, '') if job.tarball else ''

            jobs.append({
                'njobs': njobs,
                'tarball': job.tarball or '',
                'source_file': job.source_file or '',
                'setup_script': setup_script,
                'complete': job.complete or False,
                'avg_real_h': float(job.avg_real_h) if getattr(job, 'avg_real_h', None) is not None else None,
                'avg_vmhwm_gb': float(job.avg_vmhwm_gb) if getattr(job, 'avg_vmhwm_gb', None) is not None else None,
                'outputs': outputs
            })
        return jobs
    finally:
        session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 test/test_unit.py 2>&1 | tail -3`
Expected: `OK`, `Ran 344 tests` (342 + the 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add web/pomsMonitor/jobs_payload.py test/test_unit.py
git commit -m "web: extract /api/jobs payload builder from Flask app (pre-retirement)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 8: Freeze the static template and rewrite `render_static.py` (byte-diff verified)

**Condition:** same as Task 7.

**Files:**
- Create: `web/pomsMonitor/monitor_static.html` (generated, then committed as source)
- Modify: `web/pomsMonitor/render_static.py` (full rewrite of the transformation half)

**Interfaces:**
- Consumes: `jobs_payload.build_jobs_payload(db_path)` from Task 7.
- Produces: `render(out_dir, prodtools_dir, db_path)` with the SAME CLI (`--out` required, `--prodtools-dir`, `--db`) so `bin/update_pomsmonitor_web` and `web/pomsMonitor/cron_run_inspect_datasets.sh` run unchanged.

- [ ] **Step 1: Capture the BASELINE render with the current (Flask-driven) code** — env preamble required:

```bash
python3 web/pomsMonitor/render_static.py --out "$SCRATCH/baseline" \
    --prodtools-dir "$PWD" --db "$PWD/poms_data.db"
```
Expected: `wrote .../jobs.json (... bytes, N jobs)` and `wrote .../index.html (... bytes)` with index.html ≥ 25000 bytes.

- [ ] **Step 2: Generate the static-native template** by running the CURRENT `_rewrite_html` once with a stamp placeholder:

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, 'web/pomsMonitor')
import render_static as rs
html = open('web/static/monitor.html', encoding='utf-8').read()
out = rs._rewrite_html(html, '@@REFRESHED_AT@@')
open('web/pomsMonitor/monitor_static.html', 'w', encoding='utf-8').write(out)
print('template:', len(out), 'bytes')
EOF
```
Expected: `template: <N> bytes` with N ≥ 25000. The template now embeds the lineage-walker and parallel-fetch JS that `_rewrite_html` used to inject on every render.

- [ ] **Step 3: Rewrite `web/pomsMonitor/render_static.py`.** Delete `_load_app`, `_LINEAGE_JS`, `_LOADJOBS_JS`, `_must_sub`, and `_rewrite_html` entirely (they are baked into the template now), and replace `render()` with:

```python
_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(_HERE, 'monitor_static.html')
_STAMP = '@@REFRESHED_AT@@'


def render(out_dir: str, prodtools_dir: str, db_path: str) -> None:
    # --prodtools-dir is kept for cron back-compat; utils are imported
    # from the checkout containing this script (jobs_payload pins it).
    sys.path.insert(0, _HERE)
    import jobs_payload

    jobs_data = jobs_payload.build_jobs_payload(db_path)
    if not jobs_data:
        print("WARNING: jobs payload is empty", file=sys.stderr)
    jobs_body = json.dumps(jobs_data, separators=(',', ':')).encode('utf-8')

    with open(_TEMPLATE, encoding='utf-8') as f:
        html = f.read()
    if _STAMP not in html:
        raise SystemExit(f"{_TEMPLATE} lacks the {_STAMP} placeholder")
    refreshed_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()
    html = html.replace(_STAMP, refreshed_at)

    os.makedirs(out_dir, exist_ok=True)
    jobs_path = os.path.join(out_dir, 'jobs.json')
    index_path = os.path.join(out_dir, 'index.html')
    with open(jobs_path, 'wb') as f:
        f.write(jobs_body)
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"wrote {jobs_path} ({len(jobs_body)} bytes, {len(jobs_data)} jobs)")
    print(f"wrote {index_path} ({len(html.encode('utf-8'))} bytes)")
```

Keep `main()` and its three arguments exactly as they are. Update the module docstring: `index.html` now comes from the `monitor_static.html` template next to the script (stamp substitution only); `jobs.json` from `jobs_payload.build_jobs_payload`. Remove the now-unused `re` import (keep `argparse`, `datetime`, `json`, `os`, `sys`).

- [ ] **Step 4: Render AFTER and byte-diff against baseline** — env preamble required; same DB, so outputs must match modulo the timestamp:

```bash
python3 web/pomsMonitor/render_static.py --out "$SCRATCH/after" \
    --prodtools-dir "$PWD" --db "$PWD/poms_data.db"
diff "$SCRATCH/baseline/jobs.json" "$SCRATCH/after/jobs.json" && echo JOBS-IDENTICAL
diff <(sed 's/Last refreshed: [^<]*/Last refreshed: X/' "$SCRATCH/baseline/index.html") \
     <(sed 's/Last refreshed: [^<]*/Last refreshed: X/' "$SCRATCH/after/index.html") \
     && echo HTML-IDENTICAL
```
Expected: `JOBS-IDENTICAL` and `HTML-IDENTICAL`, no diff output. **Any diff → STOP**: fix the discrepancy (do not proceed to Task 9 with a non-identical render); if it cannot be made identical, revert Task 8, mark the Flask row KEEP-REVISIT, and report at the wrap-up.

- [ ] **Step 5: Suite green, commit template + rewrite together**

```bash
python3 test/test_unit.py 2>&1 | tail -3
git add web/pomsMonitor/monitor_static.html web/pomsMonitor/render_static.py
git commit -m "web: render_static goes static-native (template + jobs_payload, no Flask test-client)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```
Expected: `OK`, `Ran 344 tests`.

---

### Task 9: Delete the Flask app, WSGI shim, and served HTML

**Condition:** same as Task 7, AND Task 8's byte-diff passed.

**Files:**
- Delete: `bin/pomsMonitorWeb`, `web/pomsMonitor/__init__.py`, `web/static/monitor.html`, `web/static/json2jobdef.html`, `web/static/json-editor.html`
- Modify: `web/pomsMonitor/README.md`

**Interfaces:**
- Consumes: Task 8's self-contained renderer (nothing imports the deleted files afterward).
- Produces: `web/pomsMonitor/` is a plain script directory (`build_lineage.py`, `render_static.py`, `jobs_payload.py`, `monitor_static.html`, cron script, README) — no package `__init__`, no Flask anywhere.

- [ ] **Step 1: Pre-deletion reference check**

Run: `grep -rn 'pomsMonitorWeb\|from pomsMonitor import\|import pomsMonitor\b' --include='*.py' --include='*.sh' utils bin web test | grep -v __pycache__ | grep -v 'bin/pomsMonitorWeb' | grep -v 'web/pomsMonitor/__init__.py'`
Expected: nothing (Task 8 removed `render_static`'s `from pomsMonitor import app`; `test_unit.py`'s `from utils import pomsMonitor` hits are the CLI module — the `\b` excludes them... verify by eye that every remaining hit is `utils.pomsMonitor`, not the Flask app). Unexpected hits → STOP.

- [ ] **Step 2: Delete**

```bash
git rm bin/pomsMonitorWeb web/pomsMonitor/__init__.py \
       web/static/monitor.html web/static/json2jobdef.html web/static/json-editor.html
```

If the verdict kept the JSON-editor feature, this whole task was skipped (see Task 7's condition) — there is no partial variant.

- [ ] **Step 3: Rewrite `web/pomsMonitor/README.md`** — remove the WSGI/cgi-bin install instructions; document the static-only architecture in their place: the two crons (`bin/update_pomsmonitor_web`, `cron_run_inspect_datasets.sh`), the three artifacts (`index.html` from `monitor_static.html`, `jobs.json` from `jobs_payload.py`, `lineage.json` from `build_lineage.py`), and the DB/htdocs paths already listed in the cron headers. If Phase A found a live cgi-bin deployment, add a "Decommission" section stating exactly what the user must remove on the web host (`cgi-bin/pomsMonitor/` directory and its `wsgi.py` registration line).

- [ ] **Step 4: Full check — env preamble required for the render**

```bash
python3 test/test_unit.py 2>&1 | tail -3
python3 web/pomsMonitor/render_static.py --out "$SCRATCH/postdelete" \
    --prodtools-dir "$PWD" --db "$PWD/poms_data.db"
```
Expected: `OK`, `Ran 344 tests`; render succeeds with index.html ≥ 25000 bytes (proves no lingering import of the deleted files).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "retire: pomsMonitor Flask app + WSGI shim + JSON-editor UI (static render is the product)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 10: EXAMPLES schema edit + regeneration

**Condition:** always runs (after all approved deletions).

**Files:**
- Modify: `docs/EXAMPLES_schema.md:71-73` (the explicit CLI tool list)
- Regenerate: `EXAMPLES.md` (never hand-edit)

**Interfaces:**
- Consumes: the final retired-item list from the verdicts file.

- [ ] **Step 1: Edit the schema's tool list** — in `docs/EXAMPLES_schema.md` lines 71–73, delete every retired name (e.g. `pomsMonitorWeb`, `mkidxdef`) from the user-facing CLI enumeration; leave kept tools untouched. Scan the rest of the schema for prose mentioning retired tools (`grep -n 'mkidxdef\|pomsMonitorWeb\|setup_run1b' docs/EXAMPLES_schema.md`) and remove those too.

- [ ] **Step 2: Regenerate EXAMPLES.md** — invoke the `/refresh-examples` skill from the orchestrating session (subagents must not hand-edit EXAMPLES.md; if executing inline, invoke the Skill tool with `refresh-examples`).

- [ ] **Step 3: Verify no retired name survives in EXAMPLES.md**

Run: `grep -n 'mkidxdef\|pomsMonitorWeb\|setup_run1b\|names-only' EXAMPLES.md` (extend the pattern with every retired name from the verdicts file)
Expected: nothing.

- [ ] **Step 4: Suite green, commit**

```bash
python3 test/test_unit.py 2>&1 | tail -3
git add docs/EXAMPLES_schema.md EXAMPLES.md
git commit -m "docs: EXAMPLES regenerated post-retirement (schema tool list pruned)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

---

### Task 11: Wiki retirement page, log entry, memory updates, final sweep

**Condition:** always runs, last.

**Files:**
- Create: `wiki/pages/2026-07-18-tool-retirement.md`
- Modify: `wiki/log.md`; memory files under the project memory dir that name retired tools

**Interfaces:**
- Consumes: the approved verdicts file (quote verdicts verbatim, including KEEPs and their reasons — a KEEP with evidence is knowledge too).

- [ ] **Step 1: Write `wiki/pages/2026-07-18-tool-retirement.md`** with frontmatter matching existing pages (`title`, `tags: [decision, hygiene, retirement]`, `sources: []`, `updated: 2026-07-18`) and these sections, each filled from the verdicts file: **Retired** (per item: what it was, the evidence, where its job lives now — e.g. "mkidxdef → `json2jobdef --prod`; the logic is `prod_utils.summarize_and_index`"); **Kept, with charters** (per KEEP item, its one-line charter — especially the `latestDatasets` vs `listNewDatasets` split); **KEEP-REVISIT** (what was ambiguous and what evidence would settle it); **Ops decommission** (the cgi-bin steps from Task 9's README section, if any); **Related** links to `[[2026-07-12-hygiene-tiers-and-kept-duplication]]` and `[[2026-07-18-simplify-pass-consolidations]]`.

- [ ] **Step 2: Append a dated entry to `wiki/log.md`** (match the existing entry format) summarizing the pass in 3–5 lines with a link to the new page.

- [ ] **Step 3: Update memory** — in `/nashome/o/oksuzian/.claude/projects/-exp-mu2e-app-users-oksuzian-muse-050125-prodtools/memory/`:
  - `feedback_json2jobdef_prod_is_entry_point.md`: reword its mkidxdef mention to past tense ("standalone CLI retired 2026-07-18").
  - `reference_pomsmonitor_static_lineage_cache.md`: note that render_static is now template-based (`monitor_static.html` + `jobs_payload.py`), Flask app retired.
  - Grep the memory dir for every other retired name (`grep -rln 'mkidxdef\|pomsMonitorWeb\|setup_run1b' <memory-dir>`) and fix each hit; update the corresponding `MEMORY.md` pointer lines only if a memory's one-line hook changed.

- [ ] **Step 4: Final sweep — retired names must survive only in wiki/specs/plans/git history**

```bash
for n in mkidxdef pomsMonitorWeb setup_run1b; do   # extend with every retired name
  echo "=== $n ==="
  grep -rn "$n" --include='*.py' --include='*.sh' --include='*.html' --include='*.md' \
    utils bin web test templates .claude EXAMPLES.md docs/EXAMPLES_schema.md 2>/dev/null | grep -v __pycache__
done
python3 test/test_unit.py 2>&1 | tail -3
```
Expected: every grep empty; `OK`.

- [ ] **Step 5: Commit docs + wiki**

```bash
git add wiki/
git commit -m "wiki: tool-retirement pass logged (verdicts, charters, ops decommission)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UqMQXZQw5zAUqadxsxqgov"
```

(Memory files live outside the repo — no commit needed for them.)
