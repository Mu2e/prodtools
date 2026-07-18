---
title: Simplify pass 2026-07-18 — new single homes, batch SAM calls, and two deliberate skips
tags: [decision, hygiene, refactor, samweb, dashboard]
sources: []
updated: 2026-07-18
---

# Simplify pass (2026-07-18) — consolidations and batch-call adoption

Four-agent /simplify sweep (reuse / simplification / efficiency / altitude)
over utils + bin + web/pomsMonitor, deliberately aimed at ground the
2026-07-12 hygiene audit did not cover. ~26 findings applied; 342/342 unit
tests green throughout. Complements
[[2026-07-12-hygiene-tiers-and-kept-duplication]] (whose do-not-fix list was
honored — none of those items were touched).

## New single homes (use these; don't re-derive)

- **`poms_entry.DEFAULT_POMS_DIR` / `poms_entry.POMS_MAP_PATTERN`** — the
  POMS-map directory and the `MDC202*` basename pattern. Consumers:
  db_builder (build_db default + `__main__`), listNewDatasets (staleness
  glob + rebuild), pomsMonitor `--pattern` default, bin/pomsMonitorWeb
  reload. `bin/update_pomsmonitor_web` (shell) still spells the pattern —
  cross-language, can't import.
- **`poms_entry.default_db_path()`** — poms_data.db location, honoring the
  `POMS_DB_PATH` env override *inside the function*. This replaced the WSGI
  shim's double monkey-patch of `get_default_db_path` (web/pomsMonitor/
  `__init__.py`) and two private copies. `db_analyzer.get_default_db_path`
  survives as a delegate for existing importers.
- **`config_utils.mixing_desc(input_desc, pbeam)`** — the mixing desc rule.
  Both `prepare_fields_for_job` (generation) and `chain_emit._deferred_descs`
  (`--skip-produced` matching) call it; they previously agreed by copy-paste,
  and divergence fails open (re-proposes produced mixes).
- **`file_resolver.classify_sam_location` / `infer_dataset_location`** —
  the enstore/dcache classifier + first-file SAM walk, moved out of
  db_builder privates (db_analyzer imported underscore names cross-module).
- **`file_resolver.path_from_sam_locations(filename, locations, prefer_location)`**
  — record-selection half of `sam_physical_path`, for batch-locate consumers
  (stash copy, dashboard).
- **`famtree.output_stem(name)`** — the `tier.owner.description.dsconf` stem
  of famtree's .md output; bin/pomsMonitorWeb reconstructs the filename via
  this instead of hand-splitting.
- **`Mu2eJobBase.setup()`** — hoisted from Mu2eJobPars so a Mu2eJobFCL
  instance can serve `_extract_simjob_setup` without a second tarball parse.
- **json2jobdef cleanup catalogs** — derived from `mixing_utils.PILEUP_MIXERS`
  (`f"{m}Cat.txt"`), no longer a parallel literal list.

## New samweb_wrapper surface (prefer over per-file loops)

- `metadata_for_files(filenames)` — batch getMultipleMetadata, fail-loud;
  genFilterEff now fetches per chunk (its `chunk_size` help finally tells
  the truth), falling back per-file on a chunk error.
- `first_file_in_definition(defname)` — streamed listFiles closed after one
  name; replaces full-list transfers that kept only `files[0]`
  (db_builder probes, famtree).
- `definition_creation_date(defname)` — structured `descDefinitionDict`
  first (`create_time`/`creation_date` keys), text "Creation Date:" regex
  as fallback; replaces db_builder's prose scraping.

## Efficiency changes worth knowing operationally

- `copy_to_stash`/resilient copies batch-locate the whole file list in one
  SAM round-trip (was one HTTP call per file; 10k-file Cat staging).
- `/api/jobs` (pomsMonitorWeb) batch-locates cnf tarballs and memoizes
  setup-script extraction per process (was locate + tarball gunzip per job
  row per request); DatasetInfo now via one `.in_()` query.
- `mkrecovery --jobdesc` parses each entry's tarball once and scans the
  index window once for ALL its datasets (`build_file_maps`); was one parse
  + full scan per output dataset. `find_missing_indices` keeps its signature
  (optional `file_to_job`/`actual_files` reuse params).
- `topology_for_dataset(ds, known=...)` stops lineage walks at datasets the
  cache already holds; build_lineage passes the live cache view.
- `render_static._must_sub`: the monitor.html JS swaps now FAIL the cron
  render on template drift instead of silently shipping Flask-wired JS.
- famtree: `get_parents` lru_cached; `--stats` computed once per dataset
  label; total via `dataset_file_count` instead of a full list.
- logparser/datasetFileList: `get_dataset_files(..., max_files=N)` caps
  path construction at the source (log sampling no longer builds 100k
  sha256 relpaths to read 10 logs).

## Removed dead surface (was reachable-looking, provably unused)

`json2jobdef` `json_output=False` mode (all callers passed True — parameter
deleted); `poms_db.Job.campaign` property; `prod_utils.get_def_counts`
`include_empty` param + unreachable pushOutput-failure warning;
`latestDatasets --names-only` duplicate branch (flag kept as no-op alias);
listNewDatasets dead ImportError guard; ~10 unused imports (several were
runner-relocation leftovers, e.g. `prod_utils` `os`).

## Deliberately SKIPPED — behavior decisions, not refactors

1. **`job_common.job_outputs` tier-prefix whitelist** (`dts./dig./sim./
   rec./nts./cnf./mcs.`) omits `ntd` — an ntd output template would skip
   sequencer parse/re-emit normalization (pass-through, placeholder subst
   only). No ntd-producing config exists today; deriving the tuple from
   `_TIER_TO_OWNER_CLASS` changes output naming on the parity-guarded
   worker path. Decide deliberately when the first ntd ntuple entry lands.
2. **`Job.indef` two grammars** (SAM defname in template mode vs
   comma-joined input list written by the tarball scan) — splitting into an
   `input_datasets` column needs a schema migration plus dashboard JS
   changes; coordinate as its own change.
3. **runmu2e normal-mode double parse** (Mu2eJobPars for chunk/inputs +
   write_fcl's own Mu2eJobFCL) — deduping means threading an instance
   through `write_fcl`'s signature on the worker hot path; direct-input
   mode WAS deduped (shares the Mu2eJobFCL).

## Test-side notes

Tests patching the moved fetchers repointed: `db_builder.first_file_in_definition`
(was `list_definition_files`) and, for `infer_dataset_location`, patch
`utils.samweb_wrapper.{first_file_in_definition,locate_file_strict}` — the
file_resolver function imports them lazily at call time. Stash copy tests
mock `utils.stash_utils.locate_files_strict` (batch), not the per-file call.

## Related

- [[2026-07-12-hygiene-tiers-and-kept-duplication]] — the standing
  do-not-fix list this pass honored.
- [[2026-07-03-file-resolver-and-sam-query-plan]] — the module-charter
  boundaries these consolidations extend.
