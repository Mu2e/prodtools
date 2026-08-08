# POMS Removal Design

Date: 2026-08-08
Status: approved (brainstorming session)

## Goal

Remove the POMS submission backend from prodtools entirely. End state:
one submission backend — `submit_map --enqueue` + `submissions run`
(the direct backend per the 2026-07-18/19 hardening specs) — and no
POMS dispatch, no POMS recovery, no POMS monitoring, and no SQLAlchemy
dependency anywhere in the repo.

Estimated size: ~2,800 lines deleted, ~50 added (helper moves).

## Context and rationale

- Every campaign since the direct-submission subsystem landed has used
  the direct backend; POMS-driven stages remain only as legacy entries
  in `poms_map/MDC2025-033.json`.
- The POMS monitoring toolchain (`poms_db`/`db_builder`/`db_analyzer`/
  `pomsMonitor` + `web/pomsMonitor`) is the last SQLAlchemy consumer,
  which is why some tools require the `pyenv ana` environment. Deleting
  it removes that constraint.
- The architecture review (`docs/architecture-review-2026-08-08.md`)
  identified `find_missing_indices` as the sixth parallel
  implementation of campaign completeness, and runmu2e's duplicated
  dispatch tails as an honourable-mention candidate. Both resolve for
  free with this removal.

## Decisions (from the brainstorming session)

1. **Cutover: remove now.** In-flight POMS stages execute the tarball
   shipped at submit time, so deleting repo code does not affect
   running jobs. If a legacy POMS stage later needs a recovery or
   resubmission, check out the `pre-poms-removal` git tag in a scratch
   clone and run `mkrecovery` from there. No migration of remaining
   map-033 stages, no waiting for them to drain.
2. **Web monitor: delete code and cron; leave the last published
   render in place.** No tombstone page.
3. **Rename `utils/poms_entry.py` → `utils/map_entry.py`** as the final
   code commit. The module holds the shared submission-map-entry
   grammar (window validation, `firstjob_of`, resolve constants) used
   by the direct backend; after removal the old name is misleading.
4. **Execution: staged branch, dependency-ordered commits**, test suite
   green after every commit, tag first. Not a big-bang single commit,
   not a deprecation cycle.

## What is deleted, what moves, what stays

### Deleted outright

| Target | Notes |
|---|---|
| `utils/poms_db.py` | ORM models; last SQLAlchemy consumer |
| `utils/db_builder.py` | POMS-map → SQLite builder |
| `utils/db_analyzer.py` | reader/report layer over poms_data.db |
| `utils/pomsMonitor.py` | CLI over the above |
| `utils/_guards.py` | `require_packages`; sole consumer is `bin/pomsMonitor` |
| `bin/pomsMonitor`, `bin/update_pomsmonitor_web`, `bin/mkrecovery` | entry points |
| `web/pomsMonitor/` (entire directory) | `build_lineage.py`, `jobs_payload.py`, `render_static.py`, HTML templates, `cron_run_inspect_datasets.sh`, README |
| `utils/mkrecovery.py` (all but the three moved helpers) | `main`, `find_missing_indices`, `print_indices`, `create_recovery_definition` |
| `runmu2e.py`: `_dispatch_and_execute` + `--jobdesc` flag + main's POMS branch | the POMS dispatch tail |
| `utils/prod_utils.py`: `create_index_definition` + its call in `summarize_and_index` | SAM index definitions are POMS-only |
| Tests: `TestMkrecoveryPrintIndices`, `TestMkrecoveryWindow`, `TestJobsPayload`, the gencount/db_builder block (test_unit.py section 35), SQLAlchemy skip scaffolding (test_unit.py top), `from utils import mkrecovery` usage (~test_unit.py:4800) | `TestValidateJobdesc` and `TestBuildFileMapsScoped` stay (shared code) |

### Moves (before the deletion that would orphan them)

| Function | From | To | Consumers |
|---|---|---|---|
| `build_file_maps` | `utils/mkrecovery.py` | `utils/jobdef_lookup.py` | `utils/submissions.py` |
| `extract_datasets_from_tarball` | `utils/mkrecovery.py` | `utils/jobdef_lookup.py` | `utils/submissions.py` |
| `locate_tarball` (tarball filename → physical path, `None` on failure) | `utils/mkrecovery.py` | replaced by new 4-line `sam_physical_path_or_none(filename)` in `utils/file_resolver.py`, beside `sam_physical_path` | `utils/submissions.py:41`, `mcp/src/prodtools_mcp/tools/status.py:196` |

Note: `utils/jobdef_lookup.py` already has a *different*
`locate_tarball` (cnf defname → path, raises on failure). The
mkrecovery flavour is not merged into it — the semantics differ
(filename vs defname input, swallow vs raise). It becomes the
`file_resolver` helper instead, and the mkrecovery name disappears.

### Stays (shared with the direct backend, or external)

- `runmu2e.py`: `validate_jobdesc` and `process_jobdef` — the direct
  path calls both (`_direct_dispatch`, runmu2e.py:868).
- `utils/poms_entry.py` content — renamed to `utils/map_entry.py`;
  surviving importers to update: `submissions.py`, `submit.py`,
  `jobsub_argv.py`, `prod_utils.py`, `json2jobdef.py`,
  `mcp/src/prodtools_mcp/tools/status.py`, `test/test_unit.py`.
- `submit_map --indices` flag and its file format — direct-backend
  recovery still consumes index files; only the help-text references
  to `mkrecovery --print-indices` (submit.py:407, :828) are reworded.
- `poms_map/` directory and the numbered-map convention — external
  (mu2epro area); the directory name is historical and stays.
- pushOutput's hard-coded `_POMS` suffix in `Dataset.Tag` — external
  tool, not prodtools.
- `poms_data.db` and the last-published dashboard render on the web
  host — left frozen in place.

## Commit sequence

Branch: continue on a feature branch off `main`. Before commit 1, tag
the base: `git tag pre-poms-removal`.

1. `refactor(poms)!: delete POMS monitoring toolchain` — the four
   `utils/` monitor modules, `_guards.py`, the two `bin/` entry points,
   `web/pomsMonitor/`, their tests, and the SQLAlchemy test
   scaffolding. Reword the `job_common.py:437` comment that names
   `db_builder` in the Mu2eJobPars-consumer contract.
2. `refactor(poms)!: retire mkrecovery` — move the three helpers per
   the table above, update the two consumers (and
   `TestBuildFileMapsScoped`'s import, which follows `build_file_maps`
   to `jobdef_lookup`), delete the rest of `mkrecovery.py` +
   `bin/mkrecovery` + its two test classes, reword the `--indices`
   help in `submit.py`.
3. `refactor(poms)!: delete POMS dispatch path` — runmu2e
   `_dispatch_and_execute`/`--jobdesc`/main branch,
   `prod_utils.create_index_definition`.
4. `refactor(utils): rename poms_entry to map_entry` — `git mv` +
   import updates in the seven surviving importers.
5. `docs: regenerate EXAMPLES and update docs for POMS removal` —
   remove pomsMonitor/mkrecovery from `docs/EXAMPLES_schema.md`, run
   `/refresh-examples`, update CLAUDE.md's prodtools command list,
   add a wiki decision page (`wiki/pages/`) recording the retirement,
   rationale, and the `pre-poms-removal` tag, and mark the resolved
   items in `docs/architecture-review-2026-08-08.md`.

## Acceptance criteria

- Full test suite passes after every commit (805 tests minus the
  deleted classes).
- `bash mcp/scripts/start_mcp.sh --check` passes after commits 2
  and 4 (the MCP server imports both moved/renamed modules).
- After commit 5: `grep -ri poms utils/ bin/ test/ mcp/src web/`
  matches only `poms_map/` path strings and comments describing the
  external directory convention. No `sqlalchemy` reference remains in
  `utils/`, `bin/`, or `test/`.
- `git tag pre-poms-removal` exists and reaches every deleted file.

## Operational decommission (outside git, separate from the commits)

Performed as mu2epro (write actions — each needs explicit user
confirmation at execution time):

1. Replace the datasetMon crontab entry's target with a slim script
   containing only the original `inspect_datasets.py` loop
   (`/exp/mu2e/app/home/mu2epro/cron/datasetMon/inspect_datasets.py`
   is external to prodtools and survives). The three dashboard-refresh
   steps (db_builder / build_lineage / render_static) die with the
   repo code.
2. The synced web checkout at
   `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools/` is then never
   synced again; it can be deleted by web admins later (out of scope).
3. Leave `/web/.../data/poms_data.db` and the published static
   dashboard as-is (frozen last render).

## Risks

- **Legacy POMS stage needs a recovery** (e.g. a map-033 stage
  resubmission): mitigated by the `pre-poms-removal` tag; run
  `mkrecovery` from a scratch checkout of the tag.
- **Hidden external consumer of deleted modules**: the only known
  external execution path is the datasetMon cron via the synced web
  checkout, handled in the decommission steps. The cron keeps working
  (against its old frozen checkout) until the crontab edit lands, so
  ordering is not critical.
- **MCP server breakage via its `utils` imports**: covered by the
  `--check` acceptance criterion after commits 2 and 4.
