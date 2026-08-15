# Architecture review — deepening opportunities (2026-08-08)

Output of an `/improve-codebase-architecture` pass over the full core
(`submissions.py`, `runmu2e.py`, `submit.py`, `jobdef.py`, `json2jobdef.py`,
`job_common.py`, `poms_entry.py`, `submission_ledger.py`, `jobsub_argv.py`,
`samweb_wrapper.py`, `file_resolver.py`, `chain_emit.py`, the `mcp/` server,
`bin/`, `test/test_unit.py`, and the twelve specs in `docs/superpowers/specs/`).

Vocabulary: *module* = interface + implementation; *deep* = a lot of behaviour
behind a small interface; *shallow* = interface nearly as complex as the
implementation; *seam* = where an interface lives; *adapter* = a concrete thing
satisfying an interface at a seam; *leverage* = what callers get from depth;
*locality* = what maintainers get.

Settled decisions honoured (not re-litigated): samweb_wrapper as the only SAM
path (zero violations found); the kept worker byte-identity/parity duplication;
the simplify-pass single-homes (poms_entry constants, samweb batch helpers,
mixing_desc).

---

## Candidates, ranked by locality/leverage bought

### 1. "How complete is this campaign?" has three independent implementations

**Files:** `utils/submissions.py:215-360` (`_draining_expected`, `ledger_expected`),
`utils/submissions.py:393-452` (`draining_state`),
`mcp/src/prodtools_mcp/tools/status.py:61-173` (`_draining_outputs_block`, `_outputs_block`),
`utils/listNewDatasets.py:100-163`, `utils/mkrecovery.py:46-71` (`find_missing_indices`),
`utils/submissions.py:176-212,615-642` (`verify_row`, `verify_files_row`).

**Problem.** Six functions answer one question — *which expected outputs exist
yet* — in five return vocabularies. Each independently rediscovers the same
facts: (a) output dataset names must come from the cnf tarball, never from the
map glob or a naming convention — the CosmicCRYAll/FlatGamma lesson is written
out four separate times (`submissions.py:227-233`, `:268-274`,
`status.py:64-70`, `job_common.py:591-602`); (b) a draining campaign has no
`njobs`; (c) the denominator moves. `ledger_expected`'s interface needs a
40-line docstring to explain its keyword contract — a shallow module.
Deletion test: deleting all three and replacing with one *concentrates* the
cnf-is-the-only-source-of-names rule at a single seam.

**Solution.** One `campaign_progress` module returning a single `Progress`
value: `.landed`, `.expected` (`None` when the denominator genuinely does not
exist), `.in_flight`, `.parked`, `.pending`, `.by_dataset()`. It owns the
fixed-vs-draining branch, the cnf output-name resolution (one
`expected_outputs_for` call site), the overlapping-window `max` rule, and the
"unresolvable tarball contributes nothing" policy. `submissions status`,
`submissions run --dry-run`, `listNewDatasets --completeness`, and the MCP
`campaign_status` become presentation over one value.

**Benefits.** Table-test ~20 completeness scenarios (fixed complete, fixed
partial, draining moving denominator, parked files, unlocatable tarball) with
no sqlite, no SAM, no monkeypatching — and pin the cross-consumer invariant
that listNewDatasets and the MCP can never disagree.

### 2. Ledger rows and campaigns are untyped dicts; `indices` is dual-typed

**Files:** `utils/submission_ledger.py` (whole), ~60 raw key reads in
`utils/submissions.py` (`:188-209,235,311-359,415-448,596-606,625-666,697-704,728-734,772-860,1096-1133`),
`utils/submit.py:137-170`, `mcp/src/prodtools_mcp/tools/status.py:220-299`,
`utils/listNewDatasets.py:153`.

**Problem.** `submission_ledger` is deep at *persistence* (SQL, validated state
transitions), but there is no domain module for what it stores, so the schema
is the interface. Callers must know: `row['indices']` is absolute cnf integers
— *unless* `is_draining(row['entry'])`, in which case it is input filenames,
with the discriminator in a third module (`poms_entry.py:98-105`) whose
docstring can only plead "never sniff indices_json content"; `camp['cursor']`
is entry-relative and the `firstjob + cursor` arithmetic is re-derived at
`submissions.py:707-734`, `:803-809`, and `submit.py:137-151`; campaign
`njobs` lives in `njobs_of(camp['entry'])`, not on the row; rows correlate to
campaigns by tarball string only, with a runtime warning note
(`status.py:279-282`) instead of a type.

**Solution.** `Campaign` and `SubmissionRow` value objects returned by the
ledger. `row.payload` is one of two types (`Indices` vs `InputFiles`) so
verify-code dispatches on type, not a sniff; `camp.window(n)` returns the
absolute slice; `ledger.rows_for(camp)` owns the tarball-correlation caveat
once. Purely additive: no DDL, no state-machine change.

**ADR note.** Touches the subsystem designed by the 2026-07-18/19 specs
(direct-recovery, sliced-submission, workflow-hardening) — does not contradict
them; layers a value layer on top.

**Benefits.** `TestRecoverLoop`/`TestTopUp` currently build real sqlite DBs and
inject 3-5 fakes per test to reach one branch. With value objects, `top_up`
scheduling becomes a pure function over `[Campaign]` — testable without a DB.
"A file-keyed row is rejected by index arithmetic" becomes a one-line test
that today does not exist.

### 3. `jobsub_argv` is shallow: the caller assembles the submission, and nothing tests the argv

**Files:** `utils/jobsub_argv.py:199-280` (`build_jobsub_argv`, 17 kwargs),
`utils/jobsub_argv.py:74-102` (`output_storage_dirs`),
`utils/submit.py:499-672` (`submit_entry_direct`; ops-entry rewrite at
`:585-593`, scope derivation at `:617-651`),
`utils/file_resolver.py:132-178` (`storage_scope`),
`utils/job_common.py:191-216` (`log_storage_location`).

**Problem.** "Pure functions only" was achieved by leaving the hard parts with
the caller: `submit.py` derives the log scope by hand (13 lines at
`:622-634`) and rewrites the shipped entry for `--indices`
(`{**entry, 'firstjob': 0, 'njobs': jobset[-1]+1}`) with a six-line comment
explaining a *worker-side* invariant. The token-scope rule behind the
CeMLeadingLog permanent-403 incident (`file_resolver.py:139-158`) is split
across three modules. Exactly one test touches `build_jobsub_argv`
(`test/test_unit.py:5282-5312`) — and it patches the function. Nothing pins
the full argv or the derived scope set: the two artifacts that actually
launch production jobs.

**Solution.** One `build_submission(entry, jobset, files, paths, resources,
submitter) -> Submission` seam carrying `.argv`, `.ops_json`, `.scopes`,
`.cluster_name`. It absorbs scope derivation (data and log), the `--indices`
entry rewrite, and the defaults. `submit_entry_direct` shrinks to
resolve → build → write → run → record.

**Benefits.** Golden-argv tests with zero patching. A scope-coverage property:
for every output filename and location, the emitted `--need-storage-modify`
set covers `file_resolver.dataset_dir(...)` minus `/pnfs` — extending
`TestStorageScopeCoversPhysicalPath` (`test_unit.py:4674`) from the single
scope to the set that ships. A table test that a tape data campaign always
emits both a tape data scope and a disk log scope.

### 4. `opts` is submit.py's real interface — an argparse Namespace that changes type mid-flight

**Files:** `utils/submit.py:499` (`submit_entry_direct(entry, idx, opts)`: 17
attributes + 4 `getattr` escapes at `:508,742,751,757`),
`utils/submit.py:805-925` (`main`),
`utils/submissions.py:585-612,645-666,688-704,864-878` (four hand-built
`submit_map` argv strings).

**Problem.** (1) Undeclared shape — the test suite hand-builds 19
`Namespace`/`SimpleNamespace` objects to call in. (2) Two-phase typing —
`main()` rewrites `args.indices` str→List[int] (`:886`) and `args.files`
str→List[str] (`:892`), so the same attribute means different things before
and after. (3) The `--files`/`--first`/`--num`/`--indices`/`--enqueue`
exclusion matrix lives across three functions, reachable only through
`SystemExit`. (4) The seam between the repo's two largest modules is a CLI
string: `submissions` shells `bin/submit_map` with hand-built argv that
nothing verifies until cron time.

**Solution.** A frozen `SubmitRequest` with `from_argv(argv)` owning the whole
exclusion matrix and both parses, plus `SubmitRequest.recovery(row, missing,
db)` / `.slice(camp, n, db)` / `.drain_batch(camp, files, db)` constructors
used by `submissions`, rendered to argv in exactly one place.

**Benefits.** The exclusion matrix becomes a ~15-row table over `from_argv`.
A round-trip test (request → argv → parse → equal request) pins the
submissions→submit_map contract currently discovered at cron time.

### 5. The stage config is a 27-key dict with no type, mutated in place across four modules

**Files:** `utils/json2jobdef.py` (35 `config[...]` sites), `utils/jobdef.py`
(14), `utils/config_utils.py` (7), `utils/mixing_utils.py` (1); data at
`data/{mdc2025,mdc2030,Run1B}/*.json`.

**Problem.** No one file answers "what is a valid stage config?".
`validate_required_fields` (`json2jobdef.py:319`) checks 4 of 27 keys.
`determine_job_type` (`:325-350`) infers a 5-way mode from key presence in a
specific elif order ("Note: Order matters"). Three private keys are injected
mid-flight (`_event_count_positive`, `_defer_keys`, `_max_events_to_skip`).
Five keys mutated in place (`njobs`, `version`, `chunk_mode`, `fcl_overrides`,
`inloc`). Pre- and post-expansion configs are different shapes with the same
name — `is_already_expanded` (`:723`) exists to tell them apart at runtime.
`poms_entry` is the deep twin on the *output* side; the input side has none.

**Solution.** A `StageConfig` parsed once at load: `.job_type` (enum, computed
once), `.inputs`, `.njobs`/`.is_generic`/`.window`, `.cnf_name()`,
`.fcl_overrides`, `.deferred_keys`. Expansion returns `List[StageConfig]`, so
pre/post is a type distinction and `is_already_expanded` disappears.

**Benefits.** Table-test all five job-type modes plus the ambiguous key combos
(`chunk_lines` + `resampler_name`, `pbeam` + dict `input_data`) that resolve
by accident of elif order. Validate every JSON under `data/` in one test — a
malformed entry is currently only discovered by running against it.

### 6. The jobdef build is a protocol over the current working directory

**Files:** `utils/json2jobdef.py:131` (`inputs.txt`), `:111` (`chunks/`),
`:656-664` (`_cleanup_temp_files`), `:805-807` (per-iteration unlink);
`utils/prod_utils.py:186` (`template.fcl`); `utils/jobdef.py:684-732`
(`jobpars.json`, `mu2e.fcl`, tarball in cwd); `utils/mixing_utils.py:112-115`
(`<mixer>Cat.txt`).

**Problem.** Two functions in the same process communicate through the literal
string `'inputs.txt'` in cwd (`json2jobdef.py:622,626,635` →
`jobdef.py:381`). Bulk mode cleans only `template.fcl` between entries —
stale `inputs.txt`/`chunks/`/`*Cat.txt` surviving is luck, not design. Two
concurrent runs in one directory corrupt each other silently. The stray
`cnf.*.tar`/`.fcl` files in the repo root are this failure mode. The
`--no-cleanup` default inversion (True inside `process_single_entry`, False
from the CLI) only makes sense once you know the workdir is shared.

**Solution.** A `BuildWorkspace` context manager owning a tmpdir:
`.inputs_file(files)`, `.template(...)`, `.chunks(...)`, `.tarball_path` — all
absolute paths downstream; cleanup is `__exit__`; `--no-cleanup` becomes
"keep the workspace and print its path"; bulk mode gets isolation free.

**Benefits.** Build tests stop needing `chdir`; assert on workspace contents
("a mixing entry produced exactly four `*Cat.txt`, one `template.fcl`, one
`inputs.txt`, and the tarball").

### 7. Two near-identical `jobsub_q` table parsers, plus a third queue path in the MCP

**Files:** `utils/submissions.py:87-106` (`_jobsub_table_states`) and
`:109-128` (`_jobsub_table_cluster_states`) — differ by three lines;
`:131-152,669-685` (the two probes); `mcp/src/prodtools_mcp/condor.py`
(independent HTCondor ClassAd path).

**Problem.** Both parsers encode the 2026-07-21 trust rules (header required,
skip prefixes, `_JOBID_RE`, `_KNOWN_STATES`, any unrecognized line fails the
parse) — twice. One tick shells `jobsub_q --user` twice (`live_clusters` and
`total_queued`). The MCP's ClassAd path is deliberately independent
(mcp/condor.py:1-24, per the MCP spec) — but the consequence is hold-reason
enrichment can never reach the cron's "HELD — human decision needed" message
(`submissions.py:1017`). Deletion test: deriving the total from the
per-cluster parse concentrates — one parse, one snapshot per tick, two views.

**Solution.** A `queue` module: `snapshot(user) -> QueueSnapshot | None`
(fail-closed `None` preserved verbatim), `.active_count()`,
`.state_of(cluster)`, `.hold_reasons(cluster)`. Two **adapters** at one seam —
the `jobsub_q` table parser and the `htcondor` ClassAd query — selected by
availability. Two adapters already exist, so the seam is real, not
hypothetical.

**ADR note.** Adjusts the MCP spec's independence decision — worth reopening
because the cron would gain hold reasons for free without changing its
fail-closed contract.

**Benefits.** Trust rules tested once (today `test_unit.py:5591-5644` and
`:6347-6377` assert overlapping properties on the same grammar). `top_up` cap
arithmetic testable against a snapshot fixture instead of a fake subprocess
returning a hand-written table string.

### 8. The `tbs` schema leaks out of `Mu2eJobBase` through public `json_data`

**Files:** `utils/job_common.py:284` (public `json_data`; accessors
`:333-588`); escapes at `utils/runmu2e.py:319`, `utils/check_inputs.py:63`,
`utils/jobdef_lookup.py:74`, `utils/jobfcl.py:112`,
`utils/jobquery.py:35,60,108`.

**Problem.** The class is nearly deep (`job_inputs`, `job_outputs`,
`sequencer`, `njobs`, `job_seed`, `job_event_settings` are the right
interface, declared THE single implementation at `job_common.py:433-439`),
but `json_data` is public, so four modules reach past it for `tbs` keys with
no accessor — making the 12-key, tuple-shaped grammar (`(merge, filelist)`,
`(nreq, filelist)`) public API by accident. `check_inputs._section_files`
(`:34-43`) and `jobquery.input_files` (`:58-68`) are two implementations of
"flatten a tbs section's file lists".

**Solution.** Add three accessors — `chunk_mode()`, `files_by_section(section)`,
`outfile_templates()` — and rename `json_data` → `_json_data`.

**Benefits.** `check_inputs.split_inputs` currently needs a real tarball on
disk (`TestSplitInputs`, `test_unit.py:6676`); with `files_by_section` a fake
suffices, and the tuple convention gets one test instead of three incidental
ones.

### 9. `prod_utils` is a grab-bag — and `resolve_map_index`, a core domain rule, lives in it

**Files:** `utils/prod_utils.py` (358 lines, seven unrelated concerns:
logging, subprocess runner, mdh fetch, `fail`, FCL writing, SAM counting,
merge-factor arithmetic, POMS-map summarising + SAM definition creation, the
worker's index arithmetic `:297-319`, the worker's `pushOutput` `:322`);
`import *` at `utils/json2jobdef.py:17-18` and `utils/mixing_utils.py:8`.

**Problem.** `resolve_map_index` implements `local = global − cumulative +
firstjob` — the same window semantics `poms_entry.validate_window`
(`:108-123`) and `firstjob_of` (`:84-95`) own, consumed by the worker
(`runmu2e.py:296`); the submit path's `--indices` entry rewrite
(`submit.py:585-593`) exists solely to satisfy it — yet it sits next to SAM
definition creation. The wildcard imports mean a reader of `json2jobdef.py`
cannot tell where `fail()`, `push_output`, `write_fcl_template`,
`expand_configs`, or `PILEUP_MIXERS` come from. Three test classes
(`TestResolveMapIndex` `:4098`, `TestComputeJobsetWindow` `:4145`,
`TestValidateWindow` `:4821`) test three halves of one rule from three
modules.

**Solution.** Move `resolve_map_index` into `poms_entry` beside
`validate_window`/`firstjob_of` — one home covering writer
(`json2jobdef.append_jobdef`), submit (`_compute_jobset`), and worker
(`process_jobdef`). Split the rest into worker-io (`run`,
`_fetch_file_local`, `push_output`) and fcl-writer (`write_fcl`,
`write_fcl_template`, `write_direct_input_fcl`); delete the `import *`.

**Benefits.** Enables the round-trip property test that does not exist today:
for every entry and every global index in range, `resolve_map_index` returns
the cnf index `_compute_jobset` intended and `job_outputs` names — the
invariant whose violation duplicates physics.

### 10. `Mu2eName` has no pattern twin — "does this name match?" is re-implemented per caller

**Files:** `utils/job_common.py:29-188` (`Mu2eName`, genuinely deep) vs
`utils/submissions.py:371-390` (`_matches_pattern`),
`utils/submit.py:248-267` (`_validate_draining_entry`),
`utils/jobsub_argv.py:92-101` (fnmatch vs `outputs[].dataset` globs),
`utils/chain_emit.py:262-277` (`derive_input_defname`),
`mcp/src/prodtools_mcp/tools/discovery.py:42`.

**Problem.** Five call sites each hold a piece of one idea: a Mu2e name
pattern is five (or six) fields, each literal or wildcard, where SAM writes
`%` and fnmatch wants `*`. The hard-won lesson at `submissions.py:378-384` —
SAM's `list-definitions --defname` does *substring* matching, returning junk
names that still parse as legal 5-field datasets, so `Mu2eName.parse` +
`is_dataset` does not catch them — is pinned to a private helper in the
recovery engine, invisible to `discovery.py` and `chain_emit`, which also
consume `definitions_matching` results.

**Solution.** `Mu2eNamePattern` beside `Mu2eName`: `.parse(s)` (accepts `%` or
`*`), `.matches(name)` (field-by-field), `.as_sam_defname()`,
`.with_campaign(c)`/`.with_desc(d)`, and a `.filter(names)` applying the
substring-match correction once.

**Benefits.** The `art_slice_0_stage_2` false-positive table currently pinned
inside `_matches_pattern`'s tests covers all five consumers with one grammar
test.

### Honourable mention: `runmu2e`'s two dispatch tails

`utils/runmu2e.py:860-951` (`_direct_dispatch`) and `:983-1053`
(`_dispatch_and_execute`) both run validate → prep → `_execute_mu2e` →
derive `track_parents` from `inloc.startswith('dir:')` → push. The shared
rules ("push data only on success, always push the log, `dir:` inloc has no
SAM parents") are written twice. Not ranked: it sits close to the settled
worker-parity boundary, and the POMS-mode tail collapses on its own when POMS
dispatch retires. Noted so the duplication reads as scheduled-for-deletion,
not accidental.

---

## Single-home leaks (small, mechanical fixes; independent of the candidates)

| # | Site | Violation |
|---|---|---|
| 1 | `utils/submissions.py:37` | imports private `_default_locality`, `_LOC_TO_MDH` from `check_inputs` (public `check_tape()` returns `[Problem]`, wrong shape for a dispatch gate) |
| 2 | `utils/submissions.py:46` | imports private `_parse_sam_datetime` from `samweb_wrapper` |
| 3 | `utils/prod_utils.py:271,274` | calls private `Mu2eJobFCL._extract_fcl()` / `._format_filename()` cross-module |
| 4 | `utils/runmu2e.py:319`, `utils/check_inputs.py:63`, `utils/jobdef_lookup.py:74` | raw `jp.json_data['tbs'][...]` reads bypassing `Mu2eJobBase` accessors (see candidate 8) |
| 5 | `utils/submit.py:24,311,343` | `import sqlite3` purely for `except sqlite3.Error` — the ledger's storage technology leaks into its caller's interface |
| 6 | `mcp/src/prodtools_mcp/ledger_ro.py:15,36,60-68` | second sqlite reader of the ledger; `_shape_row`/`_shape_campaign` are verbatim copies of `submission_ledger._to_dict`/`_campaign_to_dict` (`utils/submission_ledger.py:132-136,217-220`) — the JSON-column decode rule has two homes (read-only rationale is documented and sound; the copy is the problem) |
| 7 | `web/pomsMonitor/build_lineage.py:26,39` | raw sqlite against `poms_data.db`, otherwise owned by `utils/poms_db.py` |
| 8 | `utils/submissions.py:803` | `camp['entry'].get('firstjob', 0)` bypasses the fail-loud `poms_entry.firstjob_of()` — exactly the caller that must not guess (silently-ignored firstjob duplicates physics) |
| 9 | `utils/submissions.py:246,249` | dataset/probe names built with f-strings instead of `Mu2eName.build()` |
| 10 | `utils/submit.py:251`, `utils/submissions.py:386-387`, `mcp/.../tools/discovery.py:42`, `web/pomsMonitor/monitor_static.html:340,377` | `.split('.')` field-position knowledge outside `Mu2eName` |
| 11 | `utils/json2jobdef.py:17-18`, `utils/mixing_utils.py:8` | `import *` (see candidate 9) |
| 12 | `utils/listNewDatasets.py:14-18` | bare `from submissions import ...` resolves only via `bin/listNewDatasets`'s dual `sys.path` insert; normal import fails |
| 13 | `mdh` has no single home | shelled as string at `utils/prod_utils.py:92`, `utils/submissions.py:521`; Python API at `utils/check_inputs.py:162` — three call styles, two error models |
| 14 | `jobsub` has no single home | `jobsub_q` shelled at `utils/submissions.py:146,678`; `jobsub_submit` at `utils/submit.py:653`; third path at `mcp/src/prodtools_mcp/condor.py` (see candidate 7) |

---

## Already deep — do not touch (models to imitate)

`job_common.Mu2eName` (a real grammar behind a small interface);
`poms_entry` (141 lines owning the map-entry contract with fail-loud
accessors and one shared `validate_window`); `file_resolver.storage_scope`
(one home for the layout asymmetry behind a real incident, pinned by an
invariant test); `samweb_wrapper` (the completed SAM ADR — dimension grammar
composed in one place, explicit fail-loud vs legacy-swallow policy);
`config_utils.normalize_input_data` (the one part of the stage config that
does have an owner).

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
