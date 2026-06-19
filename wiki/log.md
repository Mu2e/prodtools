# Wiki Log

Append-only. Format: `## [YYYY-MM-DD] <operation> | <title>`
Recent entries: `grep "^## \[" log.md | tail -10`

---

## [2026-05-22] ingest | MDC2025ap RPCExternal chain (non-Physical RPC at MDC2025)
Pages written: 2026-05-22-mdc2025ap-rpcexternal-chain
Pages updated: index.md
Raw: raw/2026-05-22-mdc2025ap-rpcexternal-chain.md
Reason: added single `RPCExternal` entry to `data/mdc2025/resampler_beam.json` at MDC2025ap to produce `dts.mu2e.RPCExternal.MDC2025ap.art` — the MDC2025 analog of `dts.mu2e.RPCExternal.MDC2020aw.art`. MDC2025 previously shipped only Physical RPC variants. Records the `PiTargetFilt → PiMinusFilter` rename insight and the partial-PS-off vs full DS-off geom distinction.

## [2026-05-23] update | 2026-05-22-mdc2025ap-rpcexternal-chain (local smoke passed)
Reason: `mu2e -c cnf.mu2e.RPCExternal.MDC2025ap.0.fcl -n 5` exited status 0 against real xroot `sim.mu2e.PiMinusFilter.MDC2025ac.001430_00000005.art` input; CPU 33 s, VmPeak 2.57 GB. PrimaryFilter rejected all 5 events (expected low-stats prescale, matches Run1Bak `-n ≥ 5` convention). Entry confirmed wireable end-to-end; ready for production push.


## [2026-04-21] init | Mu2e prodtools operational knowledge

## [2026-04-21] lint | 0 errors, 0 warnings, 1 info
Report: [[lint-2026-04-21]]
Fixed: none (nothing to fix; wiki is freshly initialized)

## [2026-04-21] ingest | PBI sequence implementation (conversational)
Pages written: 2026-04-21-extend-jobdef-per-index-overrides, pbi-sequence-workflow
Pages updated: index.md, overview.md
Raw: raw/2026-04-21-pbi-sequence-implementation.md

## [2026-04-21] update | pbi-sequence-workflow (post-test findings)
Reason: end-to-end test surfaced six gotchas (path doubling, SAM vs local, PBISequence pset validator rejects 3 params, -n injects maxEvents, NoPrimary.fcl missing surfaceStepTag, output filename hardcoded in NoPrimary.fcl) plus one Offline-side blocker (CompressDetStepMCs ProductNotFound on SurfaceStep — not fixable from prodtools).
Source: in-session test runs on 2026-04-21 in /tmp/pbi_test.*

## [2026-04-21] update | pbi-sequence-workflow (MDC2025ai resolves blocker)
Reason: switching simjob_setup from MDC2025ac to MDC2025ai resolved both Offline-side issues (newer NoPrimary.fcl adds surfaceStepTag + genCounter to PrimaryPath). End-to-end test now passes: 1000 events processed, 202KB dts.mu2e.PBINormal_33344.MDC2025ai.00.art written, art exit status 0. pbi_sequence.json default dsconf updated to MDC2025ai.
Source: in-session test run 2026-04-21 in /tmp/pbi_test.VCZk

## [2026-04-21] ingest | Fold PBI sequence generation into json2jobdef
Pages written: 2026-04-21-fold-pbi-into-json2jobdef
Pages updated: pbi-sequence-workflow, index.md
Reason: refactor removed utils/pbi_sequence.py + bin/gen_pbi_sequence; all PBI work now flows through json2jobdef via new split_lines input_data shape. Verified end-to-end; unit tests 160/160 pass.

## [2026-04-21] update | pbi-sequence-workflow + fold-pbi-into-json2jobdef (Mu2e-standard chunk sequencers)
Reason: chunk filenames were `.NN.txt` producing non-standard outputs like `...00.art`. Updated to `<RRRRRR>_<SSSSSSSS>` sequencer (e.g. `001430_00000000.txt`) plus auto-injected `sequencer_from_index: True`, so outputs now follow Mu2e convention (`dts.mu2e.PBINormal_33344.MDC2025ai.001430_00000000.art`). Verified: index 0 → `001430_00000000.art`, index 25 → `001430_00000025.art`, mu2e exit 0 on both.
Source: in-session test run 2026-04-21 in /tmp/pbi_seq.*

## [2026-04-21] ingest | Literal input_data shape + inloc: literal + PBISequence runNumber sequencer
Pages written: input-data-literal-shape
Pages updated: pbi-sequence-workflow (literal is now the primary documented path), index.md
Reason: three-part extension to support cvmfs-resident inputs without SAM or staging: (1) new input_data shape `{"literal": true}` in json2jobdef, (2) new `literal` inloc mode in jobfcl that passes paths through verbatim, (3) jobfcl.sequencer() now recognizes `source.runNumber` (PBISequence's run key) as a short-circuit to avoid parsing non-Mu2e-named inputs. Verified end-to-end: 25,438 events, 2.5 MB art file, exit 0. Unit tests 160/160 pass. Also pushed to production SAM successfully via /mu2epro-run with --pushout.

## [2026-04-21] update | input-data-literal-shape (short form is canonical)
Reason: added auto-detection by absolute-path key in json2jobdef — `"input_data": {"/cvmfs/.../f.txt": 1}` is now the canonical literal shape. The explicit `{"literal": true}` form remains accepted for backward compat / clarity. Rule: a key starting with "/" triggers literal mode; SAM dataset names never start with "/" so the disambiguation is unambiguous. Mixing literal and non-literal keys in one input_data raises an explicit error.
Source: in-session refactor 2026-04-21, verified with unit tests (160/160) and end-to-end run in /tmp/pbi_short.*

## [2026-04-22] update | chunk_mode hardening (post-review)
Reason: code review of the chunk_mode abstraction surfaced four issues. All addressed: (1) `sed` extraction in prod_utils.process_jobdef now uses shlex.quote for paths (cvmfs-safe today, but future configs could contain shell-unsafe chars); (2) json2jobdef._configure_chunk_mode rejects chunk_lines < 1 with a clear error; (3) PBISequence branch in jobdef.py now requires inputs OR chunk_mode — prevents submit-time misconfig from surfacing as fileNames:@nil at mu2e time; (4) new TestConfigureChunkMode class in test/test_unit.py, 9 tests. Also fixed defensive isinstance(chunk_mode, dict) check so pre-existing stash test's MagicMock mocking pattern doesn't trip the new code path. Unit suite now 181/181.
Updated: input-data-chunk-mode.

## [2026-04-22] ingest | chunk_mode (on-the-fly chunking at grid)
Pages written: input-data-chunk-mode
Pages updated: pbi-sequence-workflow (chunk_mode is now canonical; dir: + split_lines relegated to alternatives), index.md
Reason: new input_data shape `{<cvmfs-path>: {"chunk_lines": N}}`. At submit, json2jobdef counts lines, sets njobs=ceil(lines/N), stores `tbs.chunk_mode={source,lines,local_filename}` in jobpars. At grid runtime, runmu2e reads chunk_mode, runs sed to extract the per-job slice to `chunk.txt`, mu2e reads it. No chunk staging, full N-way parallelism. Verified end-to-end locally: job index 5 extracted lines 5001-6000, produced dts.mu2e.PBINormal_33344.MDC2025ai.001430_00000005.art with 1000 events, art exit 0. Unit tests 172/172 pass. Implementation: json2jobdef._configure_chunk_mode helper + new 'chunk' job_type in determine_job_type (skip --inputs / --merge-factor), jobdef.py tbs pass-through, prod_utils.process_jobdef runtime sed extraction. PBISequence validation_rules relaxed: inputs+merge_factor now allowed not required. fname index gotcha documented: field [4] is job index (e.g. `etc.mu2e.index.000.0000005.txt` → index 5).
Source: in-session implementation + test 2026-04-22 in /tmp/pbi_chunk_run.*

## [2026-04-22] update | push_data API: `track_parents` bool instead of `inloc` string
Reason: initial fix passed `inloc` kwarg through push_data and checked `inloc.startswith('dir:')` inside. Cleaner: push_data takes `track_parents: bool`; runmu2e computes the policy from inloc at the call site. Keeps push_data reusable and free of inloc-specific knowledge. 172/172 unit tests still pass. input-data-dir-shape updated to reflect the new API.
Source: post-review refactor 2026-04-22

## [2026-04-22] update | push_data handles dir: inloc parent tracking
Reason: first real POMS grid run against iMDC2025-025 (v1.8.0 on cvmfs) succeeded in mu2e execution (25,438 events, art file produced) but pushOutput failed with `printJson --parents parents_list.txt <art> returned non-zero exit status 25` → `KeyError: 'checksum'` in pushOutput.copyFile. Root cause: for `inloc: dir:<path>` jobs, infiles are cvmfs paths that aren't SAM-registered; `printJson --parents` can't resolve them; metadata dict never gets 'checksum' populated. Fix: prod_utils.process_jobdef now returns inloc as 5th tuple element; push_data accepts inloc kwarg and writes `none` in output.txt third column (instead of `parents_list.txt`) when inloc starts with `dir:`. runmu2e updated to unpack and pass inloc through. Verified: 172/172 unit tests still pass. Wiki page input-data-dir-shape updated with a new "Output parent tracking" section.
Source: in-session fix 2026-04-22 diagnosing grid job `27819857.0@jobsub05.fnal.gov` stderr

## [2026-04-21] update | pbi-sequence-workflow (production push via POMS map 025)
Reason: first real --prod invocation landing in the production manager's poms_map/ directory. Pushed cnf.mu2e.PBINormal_33344.MDC2025ai.0.tar + cnf.mu2e.PBIPathological_33344.MDC2025ai.0.tar to /pnfs/mu2e/persistent/..., wrote /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-025.json (2-entry map), mkidxdef --prod created SAM index definition iMDC2025-025. POMS will discover iMDC2025-025 on its next scan and dispatch both PBI Normal + Pathological jobs using the dir:/cvmfs/.../DataFiles/PBI/ inloc. Also confirmed: --prod handles "already-exists" tarball gracefully (pushes without error when SAM already has that filename).
Source: in-session --prod run 2026-04-21 in /tmp/mu2epro_run.*

## [2026-04-21] update | pbi-sequence-workflow (end-to-end via runmu2e + SAM pull)
Reason: proved full production chain — mu2epro pushes tarball via json2jobdef --pushout, then (in a clean shell, no pre-SimJob setup) runmu2e pulls it from SAM via mdh copy-file, generates FCL, runs mu2e, produces 25,438-event art file. Captured the hand-written jobdefs_list.json pattern for SAM-pull, the --nevts -1 requirement (mu2e -n <N> injects source.maxEvents which PBISequence rejects), and the /mu2epro-run harness gotcha (pre-sourced SimJob collides with runmu2e's internal source). Also removed v.0 was retired manually by production team and re-pushed with the `dir:` form.
Source: in-session test runs 2026-04-21 in /tmp/mu2epro_run.zK13mB

## [2026-04-21] update | prod_utils uses `file` protocol for dir: inloc
Reason: runmu2e's runtime path hardcoded protocol=`root` (xroot). xroot rewriting in jobfcl only handles /pnfs paths; for cvmfs paths delivered via `dir:<path>` inloc, this raised "root protocol requested but a file pathname does not start with /pnfs". Fix: pick `proto = 'file' if inloc.startswith('dir:') else 'root'` in prod_utils.process_jobdef. Verified end-to-end — runmu2e --dry-run now generates correct FCL with direct cvmfs path. Unit tests 172/172 pass.
Updated: input-data-dir-shape.

## [2026-04-21] update | removed `literal` inloc in favor of existing `dir:` mode
Pages deleted: input-data-literal-shape
Pages written: input-data-dir-shape
Pages updated: pbi-sequence-workflow, index.md
Reason: in review it became clear that jobfcl's pre-existing `dir:<path>` inloc already handles our case (cvmfs-resident inputs). The `literal` mode I added duplicated functionality without earning its keep — the only case it won (inputs from multiple distinct directories in one input_data) has no real use today. Removed: `inloc: "literal"` branch in jobfcl._locate_file, absolute-path detection in json2jobdef._create_inputs_file, `{"literal": true}` handling in calculate_merge_factor. Replaced: a small dispatch in json2jobdef that writes input_data keys verbatim when `inloc.startswith('dir:')`. Kept: the `source.runNumber` sequencer short-circuit in jobfcl (orthogonal fix, still needed for PBISequence). PBI config now uses `"inloc": "dir:/cvmfs/.../PBI/"` + basename keys. End-to-end verified: 25,438 events, exit 0, correct output filename. Unit tests 160/160 pass.
Source: in-session revert 2026-04-21 in /tmp/pbi_dir.*

## [2026-04-22] lint | 0 errors, 2 warnings, 3 info
Report: [[lint-2026-04-22]]
Fixed: (A) dropped two stale Open Questions from overview.md + refreshed Current Understanding to reflect chunk_mode as canonical; (C) added [[input-data-chunk-mode]] cross-link in input-data-dir-shape.md Related section. Deferred: (B) raw-slug ambiguity — left as convention debate; (D) index annotation — ADR entry still accurate for the decision recorded.

## [2026-04-24] ingest | Mu2e/aitools skills + MCP READMEs
Source: https://github.com/Mu2e/aitools (fetched 2026-04-24, 22 commits upstream). Pulled: skills/finding-data-metacat, skills/coding-with-metacat, mcp/metacat/README, mcp/sim-epochs/README. Synthesized as [[metacat-reference]] — prodtools-focused cheatsheet covering samweb→metacat CLI translation, MQL patterns, Python API snippets, read-only safety default, and install steps for metacat + sim-epochs MCP servers. Raw snapshot at [[2026-04-24-mu2e-aitools-skills]]. Skipped: SAM skill (internal knowledge), query-engine skill (not prodtools concern), building-* skills, dqm/code-index MCPs.

## [2026-04-24] update | mix stage-2 push + POMS completion + event_id_per_index verified
Production push of `cnf.mu2e.PBI{Normal,Pathological}_33344Mix1BB.MDC2025ai_best_v1_3.0.tar` completed via `/mu2epro-run` with `--prod`; POMS map `MDC2025-025.json` extended in place (4 jobdef tarballs, 104 jobs total); SAM index `iMDC2025-025` recreated. Grid turnaround ~1 hour (cnf declared 05:00 UTC → dig datasets 06:00 UTC). All 52 mix jobs succeeded on first dispatch. Sample file metadata confirms `event_id_per_index` produced globally unique `(run, subrun, event)` tuples as designed — index 21 → (1430, 21, 21001..22000), matching the offset+step*index formula. Updated [[pbi-sequence-workflow]] Stage 2 → "Production push / POMS run" subsection.

## [2026-04-24] commission | metacat-readonly MCP server
Installed `Mu2e/aitools/mcp/metacat` under `muse_050125/aitools/`. Venv requires Python 3.10+ (Mu2e ops env; system py3.9 fails `mcp>=1.2.0`). Registered project-level via new `.mcp.json` + `.claude/settings.json enabledMcpjsonServers: ["metacat-readonly"]`. All 4 tools (`discover_datasets`, `get_dataset_details`, `query_dataset_files`, `get_server_info`) verified against live metacat on 2026-04-24. Schema quirk noted: `sort_by` limited to fixed short-name set (not arbitrary metadata keys). Updated [[metacat-reference]] with commissioned status + install recipe + schema quirks.

## [2026-04-24] lint | 0 errors, 4 warnings, 4 info
Report: [[lint-2026-04-24]]
Inventory: 8 pages, 2 raw sources, 11 distinct wikilinks. Warnings: (1) raw-slug ambiguity recurring — `[[2026-04-21-pbi-sequence-implementation]]` + `[[2026-04-24-mu2e-aitools-skills]]` resolve only in `raw/`, deferred per [[lint-2026-04-22]]; (2) [[lint-2026-04-22]] orphan — closed by today's lint referencing it; (3) [[metacat-reference]] orphan — only inbound from index.md and raw, no cross-link from [[pbi-sequence-workflow]] Stage 2 despite shared subject; (4) stale claim in `overview.md` Open Questions about chunk_mode scale — N=52 successful run on 2026-04-22 contradicts the framing. Info: raw-frontmatter convention not formalized; `Campaigns`/`Incidents` index sections still empty; `pbi-sequence-workflow ↔ metacat-reference` cross-link gap; lint-chain hygiene noted. Fixed: none yet — awaiting user confirmation on which warnings to apply.

## [2026-04-25] update | pbi-sequence-workflow Stage 3 reco verified end-to-end
Reason: added Stage 3 (`dig → mcs`) reco entry to `data/mdc2025/reco.json` matching the ag pattern (`tarball_append: "-reco"`, array cross-product). 1-event smoke test on PBINormal index 0 passed (exit 0, all reco modules KKDe/Dmu/Ue/Umu + helix + calo + crv ran with Visited=1 Passed=1; 568 KB mcs output preserving the per-index sequencer `001430_00000021`). Per-index event chain `dig→mcs` confirmed: index 21 → events 21001..22000 carry through. Non-obvious gotcha surfaced: reco.json entries need explicit `services.DbService.{purpose,version}` overrides or jobs fail at `ProtonBunchTimeFromStrawDigis` with `EMPTY -1/-1/-1` calibration set; existing af/ag entries lack these, possibly never smoke-tested locally — flagged in workflow page as open question. PBIPathological smoke + production push pending.
Source: in-session test 2026-04-25 under `oksuzian` user; tarballs in repo root.

## [2026-04-25] update | pbi-sequence-workflow Stage 3 PBIPathological smoke verified
Reason: second of two PBI flavors confirmed working — `dig.mu2e.PBIPathological_33344Mix1BB.MDC2025ai_best_v1_3.art` index 0 reco passes (exit 0, all modules Visited=1 Passed=1, CPU 1.55s, VmPeak 1.98 GB). Both PBI flavors validated locally under MDC2025ai env. Stage 3 section in [[pbi-sequence-workflow]] updated with Pathological smoke result. Production push (`/mu2epro-run --prod`) is the only remaining step.
Source: in-session test 2026-04-25.

## [2026-04-25] update | pbi-sequence-workflow Stage 3 reco production push complete
Reason: `/mu2epro-run MDC2025ai json2jobdef --json data/mdc2025/reco.json --dsconf MDC2025ai_best_v1_3 --prod --jobdefs MDC2025-026.json` completed at 11:24 UTC. Both reco tarballs (`cnf.mu2e.PBI{Normal,Pathological}_33344Mix1BB-reco.MDC2025ai_best_v1_3.0.tar`) SAM-declared and copied to `/pnfs/.../phy-etc/cnf/mu2e/PBI*Mix1BB-reco/MDC2025ai_best_v1_3/tar/...`. New POMS map `MDC2025-026.json` (52 jobs total, 26 per flavor); SAM index `iMDC2025-026` def_id 218067 declared. POMS scan will pick up the 52 reco jobs next pass. Expected mcs outputs: `mcs.mu2e.PBI{Normal,Pathological}_33344Mix1BB.MDC2025ai_best_v1_3.art` (26 files each) + sibling logs. Full PBI chain (dts → dig → mcs) now in production.
Source: /mu2epro-run skill output 2026-04-25 06:24 CDT; verified via `samweb list-files` + `samweb describe-definition iMDC2025-026`.

## [2026-04-25] update | pbi-sequence-workflow Stage 3 push remediation (POMS map convention)
Reason: initial Stage 3 push targeted a fresh `MDC2025-026.json` — wrong; PBI chain stages 1+2 already in `MDC2025-025`, the convention is extend-in-place. Remediated by re-running `json2jobdef --prod --jobdefs MDC2025-025.json` (tarballs already in SAM, pushOutput no-op; entries appended). `MDC2025-025.json` now 6 entries / 156 jobs total (Stage 1 × 2 + Stage 2 × 2 + Stage 3 × 2). `iMDC2025-025` regenerated by `mkidxdef --prod`, def_id 218087, dimension `dh.sequencer < 0000156`. Orphan `MDC2025-026.json` map file and `iMDC2025-026` SAM index deleted. Saved feedback memory `feedback_extend_existing_poms_map.md` so the convention is auto-loaded next session. samweb-CLI quirk noted: `delete-definition iMDC2025-026` hit `RecursionError` under ksu mu2epro from one shell, succeeded from another — root cause not investigated.
Source: in-session 2026-04-25 ~11:53 UTC.

## [2026-04-25] commission | poms-push skill
Drafted `.claude/commands/poms-push.md` to codify the extend-vs-allocate POMS map decision (the convention I broke earlier today by allocating MDC2025-026 instead of extending 025). Behavior: read JSON config + dsconf, derive workflow pattern by stripping known stage suffixes (Mix1BB, -reco, Triggered, etc.) and taking longest common prefix across entries; scan `^MDC2025-\d{3}\.json$` (filter excludes -test/-tes/-MDS3c variants in one regex), count matching tarballs in each; print recommended `/mu2epro-run` invocation and stop (does NOT push — production gate stays in /mu2epro-run). Validated on canonical case `data/mdc2025/reco.json --dsconf MDC2025ai_best_v1_3 MDC2025ai`: derived pattern PBI, found 6 matching tarballs in MDC2025-025, decided "extend in place" — exactly the post-remediation correct answer. Skill is registered and visible in the skill list. Convention is now triple-anchored: memory `feedback_extend_existing_poms_map.md` (auto-loaded), wiki Stage 3 "Process note" (queryable rationale), `/poms-push` skill (executable enforcement at decision time).
Source: in-session 2026-04-25 ~12:10 UTC.

## [2026-04-25] update | listNewDatasets gains --completeness flag with auto-rebuild
Reason: completeness questions previously required pomsMonitor (campaign-scoped) or manual `jobquery --njobs`/`samweb count-files` per dataset. Added `--completeness` to `listNewDatasets` that joins against the existing pomsMonitor SQLite DB (`<repo>/poms_data.db`) and prints `<actual>/<expected>` per row. Includes auto-rebuild: cheap mtime check against POMS map files in lookback window; if any map newer than DB, run `build_db(since=now-days)` to refresh only changed entries. `--no-rebuild` opts out. Verified end-to-end on the freshly-completed PBI Stage 3 mcs datasets — both 26/26 complete; fast path (DB fresh) sub-second; rebuild path ~190s for the one stale map. Quirk: requires `pyenv ana` post `muse setup ops` for SQLAlchemy import — saved as `reference_pyenv_ana_for_db.md` memory.
Source: in-session 2026-04-25 ~12:30 UTC.

## [2026-04-25] update | pbi-sequence-workflow Stage 3 reco completed in production
Reason: POMS dispatched and completed all 52 PBI reco jobs (Normal + Pathological, 26 each) within ~30 minutes of the SAM index recreation. Verified via `listNewDatasets --completeness`: both mcs datasets show 26/26. Sibling log datasets also landed. Full PBI chain (dts → dig → mcs) end-to-end in production with globally-unique (run, subrun, event) tuples preserved.
Source: listNewDatasets query 2026-04-25 ~12:30 UTC.

## [2026-04-25] update | bin wrappers guard SQLAlchemy import
Reason: cryptic ModuleNotFoundError tracebacks turned into clear "Run pyenv ana" message at startup. Added to `bin/pomsMonitor`, `bin/list_no_child_datasets`, `bin/pomsMonitorWeb` (also guards Flask) — exit 2 on missing module. `bin/listNewDatasets` (via `utils/listNewDatasets.py`) checks only when `--completeness` is requested and degrades softly: prints warning, disables completeness column, runs the rest. Verified both modes by running each wrapper in `env -i` shell with only `muse setup ops` (no `pyenv ana`). Memory `reference_pyenv_ana_for_db.md` updated to describe the new clear symptom.
Source: in-session 2026-04-25 ~12:50 UTC.

## [2026-04-25] commission | recent-datasets skill
Drafted `.claude/commands/recent-datasets.md` to wrap `bin/listNewDatasets --completeness` with the right env (Mu2e setup + `pyenv ana` for SQLAlchemy + `python3` not `bash`) and sensible defaults (`--days 1`, completeness on). Also filters the noisy db_builder rebuild trace lines (`Skipping logparser ...`, `Loaded N job definitions`, `Discovered and cached ...`, etc.) so output is just the dataset table — keeps real signals (DB stale messages, warnings, custom-query echo) intact. Encodes three frictions hit during this session: wrong invocation (bash vs python3), forgot pyenv ana, forgot --completeness. Verified pipeline produces clean table on PBI mcs query (both 26/26).
Source: in-session 2026-04-25 ~13:00 UTC.

## [2026-04-27] commission | parallel-audit skill
Drafted `.claude/commands/parallel-audit.md` to encode the "fan out N Explore agents on non-overlapping slices, then synthesize" pattern. Inspired by Hermes Agent's `software-development/subagent-driven-development` skill (NousResearch/hermes-agent). Behavior: parse `<topic> [--agents N]` (default 4, clamp [2,6]); pick non-overlapping slicing dimension (by directory / concern / layer / risk); spawn all agents in a single tool-call message; synthesize returns into prioritized punch list with [critical|high|medium|low] tags and file:line citations. Dedupes findings (multi-agent mentions = higher confidence). Default Mu2e 4-cut documented in skill: `utils+bin code quality / CLI ergonomics+EXAMPLES drift / DB+JSON / tests+repo hygiene` — the cut already validated by today's deep-review run. Read-only by design; agents do not edit. Skill registered and visible in skill list.
Source: in-session 2026-04-27 ~14:00 UTC, after Hermes Agent comparison + earlier 4-agent prodtools audit.

## [2026-04-27] update | repo hygiene + Mu2eFilename consolidation
Reason: multiple noise files at repo root (`os`, `sys` PostScript blobs ~8MB each, `test2/`, `test_runmu2e/`, `test_reco/`, `prompts*.txt`, `momentum_resolution_*.png`, `MDC*-test.json`, `mu2e_common.gdml`) cluttering `git status`; duplicate `Mu2eFilename` class in `utils/job_common.py:15` and `utils/datasetFileList.py:21`. Added 11 root-anchored entries to `.gitignore` (untracked count dropped ~50→~30). Removed local `Mu2eFilename` from `datasetFileList.py`, added `relpathname()` (SHA256 hash subdir, byte-identical to old) to canonical class in `job_common.py`. Existing 13 unit tests across `TestMu2eFilename` (8) and `TestDatasetFileListFilename` (5) became regression tests for the merge — full suite 181/181 passing. Caveat: canonical class enforces 6+ dot-separated fields and raises `ValueError`; old local class was lenient. Safe in current call site (`f` from samweb is always well-formed) but flagged for downstream callers.
Source: in-session 2026-04-27 ~13:30 UTC.

## [2026-04-27] update | ~/.claude moved to /exp/mu2e/app
Reason: `/nashome` is at 93% capacity; `~/.claude` was 16M and growing. `~/.claude` is now a symlink to `/exp/mu2e/app/users/oksuzian/.claude` (cephfs, 334G free). Live session writes to `history.jsonl` continued through the symlink without disruption. Memory paths in MEMORY.md still resolve (the project memory key `-exp-mu2e-app-users-oksuzian-muse-050125-prodtools` is unchanged). Watch for any I/O lag on cephfs — small JSONL appends are the worst case but no symptoms so far.
Source: in-session 2026-04-27 ~13:45 UTC.

## [2026-04-28] update | g4bl tarball pushed to production SAM
First g4bl tarball declared in production SAM via `pushOutput`. Hand-built `cnf.mu2e.G4blPOT.TESTaa.0.tar` (625KB, contains `jobpars.json` + `work/Mu2E.in` + `work/Geometry/*.txt`). Resolves Unknown #1 from the demonstrator plan: `pushOutput` accepts our minimal `jobpars.json` (runner/main_input/events_per_job/desc/dsconf — no FCL-derived metadata) and produces valid SAM declaration. Tarball lives at `/pnfs/mu2e/tape/phy-etc/cnf/mu2e/G4blPOT/TESTaa/tar/c7/74/cnf.mu2e.G4blPOT.TESTaa.0.tar` (hash subdirs from `Mu2eFilename.relpathname()`). Pushed via `ksu mu2epro` direct (not `/mu2epro-run` because that skill expects prodtools `bin/` scripts; `pushOutput` is a UPS binary on PATH after `setup OfflineOps`). Next: Step 2 (`mkidxdef` for the dummy index dataset), Step 3 (POMS map JSON to dropbox).
Source: in-session 2026-04-28 ~14:30 UTC.

## [2026-04-28] ingest | poms-reference (FNAL POMS architecture + Mu2e conventions)
Spawned 3 parallel Explore agents to research github.com/fermitools/poms on architecture/data model, SAM-dataset/map wiring, and user operations. Synthesized into wiki/pages/poms-reference.md with raw sources at wiki/raw/2026-04-28-poms-{architecture,sam-wiring,user-ops}.md. Key findings: (1) POMS data model is Campaign → CampaignStage → Submission → Jobs, with the SAM-dataset name configured per-stage in POMS DB. (2) `i<map_stem>` is a Mu2e mkidxdef convention (in our `prod_utils.py:create_index_definition`), NOT a POMS hardcode — Agent #2 initially confused this; corrected in synthesis. (3) `poms_client` Python library exists with `update_campaign_stage()` and `launch_campaign_stage_jobs()` — stage config IS scriptable, not web-UI-only. (4) `iMDC2025-NNN` SAM defs are content-agnostic placeholders (just `etc.mu2e.index.000.<seq>.txt` files for job-count iteration); reusable across stages. Confirmed by user 2026-04-28 in the context of testing g4bl runner. Open questions: exact column name for stage's SAM dataset in `campaign_stages`, exact `update_campaign_stage()` payload, whether running Mu2e POMS allows admin-free SAM-def reuse — flagged in page for verification on the running instance. Pages written: poms-reference. Pages updated: index.md.
Source: in-session 2026-04-28, three Explore agents on github.com/fermitools/poms.

## [2026-04-28] update | iG4BL-000 SAM definition created
After samweb-write auth resolved upstream, retried `mkidxdef --prod` against the dropbox map `G4BL-000.json`. SAM def `iG4BL-000` (id 218203) created with one file `etc.mu2e.index.000.0000000.txt` (query `dh.dataset etc.mu2e.index.000.txt and dh.sequencer < 0000001`). Quirk: despite "Exceeded 30 redirects" error message during the create call, the operation actually succeeded — the def is registered under Username=`oksuzian` (not mu2epro, despite running through `ksu mu2epro`). Demonstrator state now: tarball at /pnfs/mu2e/tape/phy-etc/cnf/mu2e/G4blPOT/TESTaa/tar/c7/74/, map at /exp/mu2e/app/users/mu2epro/production_manager/poms_map/G4BL-000.json (5-field minimal shape), SAM def iG4BL-000 with 1 file. Ready for POMS-side stage configuration.
Source: in-session 2026-04-28 ~22:27 UTC.

## [2026-04-28] update | g4bl runner switched from SL7 container to native AL9 spack
User noted that `source mu2e-art.sh && spack load g4beamline` works natively on AL9 — no SL7 container needed. Refactored `process_g4bl_jobdef`: removed apptainer wrap path entirely (-50 LOC), removed `_is_inside_sl7()` helper, removed `DEFAULT_G4BL_CONTAINER` constant. New runner just does `unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE; source mu2e-art.sh; eval "$(spack load --sh g4beamline)"; cd embed_dir; g4bl <main_input> viewer=none First_Event=N Num_Events=M epsMax=0.01 histoFile=...`. Three discoveries: (1) g4bl 3.08b on AL9 (built against gcc-13.3.0 + Geant4 11.3.2) requires plain `key=value` CLI syntax, NOT `param key=value` — older 3.08 SL7 build was lenient. (2) `unset SPACK_ENV` is critical: `bin/runmu2e` does `muse setup ops` before invoking Python, which activates ops-019; subprocess inherits SPACK_ENV; `spack load g4beamline` then fails because g4beamline isn't in ops-019. Documented in reference_spack_env_after_muse_setup memory + Mu2e wiki. (3) Native AL9 path eliminates the entire SL7 nesting consideration; workers run in fnal-wn-el9 (standard fermigrid.cfg outer container) with no inner wrap. `poms/g4bl.cfg` could now be retired in favor of fermigrid.cfg. Both smoke modes pass: embed_dir produces 82KB ROOT + 570KB log; tarball mode produces 74KB ROOT + 570KB log; 0 fatal exceptions, 51 geometry warnings (normal for Mu2e Mau9 geometry). 181/181 unit tests still pass.
Source: in-session 2026-04-28 ~21:12 UTC, after user revealed `spack load g4beamline` works on AL9.

## [2026-04-28] update | retired poms/g4bl.cfg
Deleted `poms/g4bl.cfg` (SL7 outer-container submit cfg). No longer needed after the runner switched to native AL9 spack g4bl — workers can now run under the standard `poms/fermigrid.cfg` (fnal-wn-el9). Updated `wiki/pages/poms-reference.md` to note the retirement.
Source: in-session 2026-04-28.

## [2026-04-29] update | proposed: remove POMS from submit/recover loop
Filed [[2026-04-29-remove-poms-from-submit-loop]] — phased plan. Phase 1: add `utils/submit.py` + `bin/submit_map` driving existing `mu2ejobsub` from a local SQLite submissions table; repoint `mkrecovery` to emit `--jobs=...` argv (drop the SAM `etc.mu2e.index` dataset round-trip); switch `pomsMonitor`/`db_builder`/`listNewDatasets --completeness` off `poms_map/*.json`. Phase 2: replace `mu2ejobsub` with Python `jobsub_argv.py` driving `jobsub_submit` directly + Python/bash `run_job.sh` worker shim. Status: proposed, not started.
Source: in-session 2026-04-29.

## [2026-04-30] update | Phase 1 first-step shipped + Phase 2 plan filed
Phase 1: `utils/submit.py` + `bin/submit_map` written. Validated end-to-end: cluster 91222054 (2 BeamSplitter jobs, scratch outstage as oksuzian) submitted via prodtools→mu2ejobsub→jobsub_submit, no POMS in the loop. Outputs land in outstage but NOT in SAM (mu2ejobsub.sh does no pushOutput).

Phase 2 plan filed at [[2026-04-30-phase2-direct-jobsub-implementation]]. Driver: per-job pushOutput + Perl-free pipeline. Three review agents found 21 fidelity gaps; folded into the plan as 10 correctness blockers (CB1–CB10). Scope tightened to jobdef-mode only for v1 (template/direct-input/g4bl modes stay on `--backend mu2ejobsub`). Ownership cost (taking on `mu2egrid::impl` semantics) explicitly accepted in plan.
Source: in-session 2026-04-30.

## [2026-05-05] ingest | json2jobdef-staging-workflow page added
Pages written: json2jobdef-staging-workflow
Skill added: /stage-entry (.claude/commands/stage-entry.md)
Reason: cross-stage staging-config model (data/<campaign>/*.json entry shape, stage chain, dsconf flow, DbService rule) was undocumented; gap surfaced when planning a multi-dataset reco lift.

## [2026-05-05] lint | 3 errors, 0 warnings, 2 info
Report: [[lint-2026-05-05]]
Fixed: 4 broken `[[reference_*]]` cross-refs introduced in first draft of `json2jobdef-staging-workflow` (memory files don't resolve via `[[slug]]`; rewritten as prose pointers). 3 remaining errors are pre-existing broken slugs (`2026-04-21-pbi-sequence-implementation`, `2026-04-24-mu2e-aitools-skills`, `run1bai-campaign`) flagged in earlier lints; not addressed this pass.

## [2026-05-05] update | json2jobdef-staging-workflow + SimJob-version-selection rule
Pages updated: json2jobdef-staging-workflow (added "SimJob version selection" section)
Memory added: feedback_latest_simjob_in_new_entries
Reason: user codified a forward-only rule that new `data/<campaign>/*.json` entries default `simjob_setup` and dsconf prefix to the lexicographically-greatest available release under `/cvmfs/.../Musings/SimJob/`, with cross-checks against known-broken releases (aj trig-config drift, pre-aj PBI offset gap).

## [2026-05-05] update | json2jobdef-staging-workflow + stage-entry: --index semantics fix
Pages updated: json2jobdef-staging-workflow (added "Selector semantics" subsection)
Skill updated: /stage-entry (selectors block + examples now lead with --dsconf)
Memory added: reference_json2jobdef_index_is_flat
Reason: smoke test of am-bumped reco entries revealed --index N actually indexes the flattened (entry × input_data) expansion, not the JSON entry position — `--index 7` picked the 8th input of entry 0, not entry 7. argparse help is misleading. `--dsconf MDC2025am_best_v1_3` produced all 19 expected cnfs in one call (the natural bulk selector).

## [2026-05-05] update | MDC2025am reco entries event-validated end-to-end
Memory added: reference_mdc2025am_reco_verified
Reason: 3 new reco.json entries (OnSpill batch, Extracted_am, OffSpill) ran `mu2e -c` to status 0 on real dig tape inputs under MDC2025am simjob with `services.DbService.{purpose=Sim_best,version=v1_3}`. First production use of `recoMC/OffSpill.fcl` as a reco.json entry. Confirms the forward-only "latest SimJob" rule is safe to apply for reco against am. Token gotcha noted: stale `/tmp/bt_token_mu2e_Analysis_<uid>` insufficient; fresh `htgettoken -a htvaultprod.fnal.gov -i mu2e` after `setup OfflineOps` writes `/run/user/<uid>/bt_u<uid>` which xrootd accepts.

## [2026-05-05] update | MDC2025-025 extended with 19 MDC2025am reco entries
Pushed: 19 cnf tarballs SAM-declared as mu2e (CeEndpointTriggered, CeMLeadingLog{,Mix1BB}Triggered, CePLeadingLogTriggered, CePlusEndpointTriggered, CosmicCRYAllMix1BBTriggered, CosmicSignalTriggered, DIOtail95OnSpillTriggered, FlatGammaCalo/FlatGamma/FlateMinus/FlatePlus/NoPrimaryMix1BBTriggered, RMC/RPC{External,Internal}OnSpillTriggered, CosmicCRYExtractedTriggeredReco_am, CosmicSignalOffSpillTriggered) at MDC2025am_best_v1_3, three fcls (recoMC/{OnSpill,Extracted,OffSpill}.fcl).
Map: /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-025.json extended in place. iMDC2025-025 SAM index deleted+recreated. Map now holds 25 tarballs / 22402 jobs (was ~6/156 PBI-only before).
Reason: user-directed cross-purpose extension — MDC2025-025 originally housed the PBI chain; user chose to add the unrelated MDC2025am reco campaign to the same map (option A from session prompt) rather than allocate a new map number. Diverges from `feedback_extend_existing_poms_map.md` ("later stage of same workflow") — this is a same-map override for a different workflow.

## [2026-05-05] ingest | prodtools-prd page added (descriptive PRD via parallel agentic synthesis)
Pages written: prodtools-prd
Method: 3 parallel Explore agents (commands+workflows; integration+auth; users+history+gaps) → main-thread synthesis into a single ~330-line scannable PRD covering personas, goals/non-goals, capabilities (commands + skills), data flow diagram, integration surface, accounts/auth/env, in-flight roadmap signals, operational gotcha categories, repo layout, open questions.
Reason: user requested an "agentic teams" PRD; descriptive shape (what prodtools IS today) chosen as the safe default — no forward strategy assumed. Cross-links wiki + memory rather than restating; pruned a few unverifiable specifics from agent outputs (test counts, exact cluster IDs without source check). Open questions section captures Phase 2 cutover, cross-purpose POMS map precedent, MetaCat migration depth, and the lingering broken wiki slugs.

## [2026-05-05] update | corrected g4bl-vs-Offline chain conflation in PRD + staging-workflow
Pages updated: prodtools-prd (§1 problem statement + §7 data-flow diagram), json2jobdef-staging-workflow (added clarifying paragraph after the stage-chain table)
Memory added: reference_g4bl_decoupled_from_offline
Reason: user flagged that the chain `g4bl → primary → digi → mix → reco → evntuple` is wrong — g4bl is Geant4 Beamline (its own simulation tool, runner-driven, .root output, no mu2e -c) and is decoupled from the Offline chain. Its outputs reach Offline downstream consumers only via separate Offline-side conversion that lives outside prodtools. Corrected both pages to show Offline chain as `primary → digi → mix → reco → evntuple` with g4bl noted as a parallel prodtools-managed pipeline.

## [2026-05-05] update | pruned speculative "g4bl feeds Offline downstream" claim
Pages updated: prodtools-prd (§1 + §7 data-flow note), json2jobdef-staging-workflow (clarifying paragraph)
Memory updated: reference_g4bl_decoupled_from_offline (replaced "out-of-band conversion → downstream pileup/resampler inputs" diagram with terminal-within-prodtools)
Reason: user flagged that my earlier correction still over-claimed — I'd written that g4bl outputs feed Offline stages "only after Offline-side conversion". I had no evidence for that. Verified: no other `data/<campaign>/<stage>.json` references g4bl outputs as input; raw source `2026-04-27-g4bl-runner-integration.md` describes only the runner setup, no downstream path. Within prodtools, g4bl outputs are terminal; whether any external tool consumes them is not documented in this repo.

## [2026-05-05] update | g4bl runner is native AL9 spack, not SL7/apptainer
Pages updated: prodtools-prd (§1 + §7), json2jobdef-staging-workflow (clarifying paragraph)
Memory updated: reference_g4bl_decoupled_from_offline (history note + distinguishing markers)
Reason: I cited the 2026-04-27 raw notes (which captured the original SL7/apptainer integration) as if they described current state. Commit `401e3da` switched to native AL9 spack and retired `poms/g4bl.cfg`. Current code in `utils/prod_utils.py:679-710` uses `eval "$(spack load --sh g4beamline)"` directly on AL9 workers — no container wrap. The SPACK_ENV gotcha (memory `reference_spack_env_after_muse_setup.md`) was discovered during this migration.

## [2026-05-05] ingest | g4bl-runner page added (architecture detail per user request)
Pages written: g4bl-runner
Pages updated: prodtools-prd (§7 cross-link), json2jobdef-staging-workflow (clarifying paragraph cross-link), index.md
Reason: user asked for detail on the "no SL7/apptainer wrap since commit 401e3da" claim. Synthesized from `git show 401e3da` (commit message + diff): current execution path (bash → unset SPACK_ENV/PYTHON* → source mu2e-art.sh → eval spack load g4beamline → g4bl on AL9 worker, no nested wrap), before/after diff table (apptainer fnal-dev-sl7 → nothing; whole-/tmp + HOME + embed_dir binds → nothing; --cleanenv → selective unset; _is_inside_sl7 retired; DEFAULT_G4BL_CONTAINER constant retired; poms/g4bl.cfg deleted), why two prior gotchas became unnecessary, minimized POMS map shape (5 fields, runtime config in tarball jobpars.json), naming/dsconf renames (g4bl.mu2e → nts.mu2e, Mu2EBeamline → G4blPOT, MDC2025ai_g4bl_v1_0 → TESTaa), demonstrator artifacts. PRD stays scannable; deep history lives in the new page.

## [2026-05-08] update | MDC2025-025 extended with 20 MDC2025an ntuple entries (MDC2025-003)
Added 3 new entries to `data/mdc2025/evntuple.json` against the just-completed MDC2025an reco mcs outputs:
- mockdata batch (18 inputs: 17 OnSpill/Mix1BB triggered + 1 OffSpill-LH) → `from_mcs-mockdata.fcl`
- OffSpill-CH (1 input) → `from_mcs-OffSpill.fcl` (new in AnalysisMDC2025 v02_00_00, hardcodes FitType: CentralHelix)
- Extracted (1 input, merge factor 10) → `from_mcs-extracted.fcl`

simjob_setup: `AnalysisMDC2025/v02_00_00`. dsconf: `MDC2025-003` (next sequential after existing MDC2025-000/-002 ntuple campaigns).

Local smoke: OffSpill-CH cnf → `fcldump --local-jobdef` → `mu2e -c -n 1` ran clean (status 0, MergeKKOff + EventNtuple modules visited, ~0.4s CPU, 1.25 GB peak).

Production push via `/mu2epro-run AnalysisMDC2025/v02_00_00 json2jobdef --json data/mdc2025/evntuple.json --dsconf MDC2025-003 --prod --jobdefs /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-025.json`. 20 cnf tarballs SAM-declared and copied to `/pnfs/.../phy-etc/cnf/.../MDC2025-003/tar/...`. Map: `MDC2025-025.json` extended in place to **64 tarballs / 65594 jobs** (was 44 / 44648). `iMDC2025-025` deleted+recreated to span all 64 entries.

Findings worth noting:
- **OffSpill SIGSEGV is fixed in MDC2025an** (Offline v13_11_00). All 20 reco mcs outputs (incl. OffSpill -LH/-CH at 500/500 each) are complete.
- **fcl routing for OffSpill ntuples**: -LH → mockdata.fcl, -CH → OffSpill.fcl. The earlier evntuple.json precedent (v01_02_00) routed BOTH through mockdata, which was a latent bug (mockdata's default LoopHelix config doesn't match CH input). v02_00_00's dedicated OffSpill.fcl fixes it.
- **`fcldump --local-jobdef` is the cleaner smoke entry point** vs `jobfcl` directly — defaults to `--proto root, --loc tape, --index 0` and writes the fcl to a file. Saved as `reference_jobfcl_proto_root_for_tape_smoke.md`.
- **`/mu2e-run` and `/mu2epro-run` skills extended** to accept `<Musing>/<Version>` form (e.g. `AnalysisMDC2025/v02_00_00`) so they work for non-SimJob musings. Bare tags still treated as `SimJob/<tag>` for backwards compat.
- **`samweb list-definitions` ≠ file metadata** — initial completeness check missed OffSpill -LH/-CH because SAM definitions weren't auto-created for suffixed-output names; `samweb count-files 'dh.dataset X'` showed 500/500 each. Saved as `reference_samweb_definitions_vs_metadata.md`.

Source: in-session 2026-05-08 ~10:02 UTC; workdir `/tmp/mu2epro_run.PeRZO1`. Smoke artifacts in repo root: `cnf.oksuzian.CosmicSignalOffSpillTriggered-CH.MDC2025-003.0.tar`, `cnf.oksuzian....fcl`, `smoke_offspill_ch.fcl`.

## [2026-05-07] update | MDC2025-025 extended with 19 MDC2025an reco entries
Added 3 new entries to `data/mdc2025/reco.json` mirroring the am triplet (OnSpill batch of 17 inputs, Extracted, OffSpill) at `MDC2025an_best_v1_3` / `simjob_setup=MDC2025an`. Per the forward-only rule, am entries left untouched. Pushed via `/mu2epro-run`: 19 cnf tarballs SAM-declared + copied to `/pnfs/.../phy-etc/cnf/.../MDC2025an_best_v1_3/tar/...`.
Map: `MDC2025-025.json` extended in place to **44 tarballs / 44648 jobs** (was 25 / 22402); `iMDC2025-025` deleted+recreated to span all 44 entries.
Motivation: retry against the Offline `v13_11_00` build (am had `v13_09_00` and SIGSEGV'd OffSpill on grid 2026-05-06). Many `.so` libraries differ between am and an SimJob trees — realistic chance the SIGSEGV is fixed. Awaiting POMS dispatch + completeness check.
Gotcha encountered: first push attempt used relative `--jobdefs MDC2025-025.json` from /tmp workdir; json2jobdef created a fresh 19-entry map there, and `mkidxdef --prod` rebuilt `iMDC2025-025` to point only at those 19 entries (orphaning prior 25 PBI + am from the SAM index, dropbox file untouched). Recovered by re-running with absolute `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-025.json`; pushOutput no-ops on already-declared tarballs ("File ... already exists on SAM, skipping push") so cost was zero. Saved as `reference_jobdefs_use_absolute_path.md`.
Source: in-session 2026-05-07 ~23:13 UTC (workdir `/tmp/mu2epro_run.mFz3O2`).

## [2026-05-06] update | reco multi-output suffix-substitution rule + OffSpill entry fix
Memory added: reference_reco_output_suffix_overrides
File updated: data/mdc2025/reco.json (entry 9 OffSpill — added `outputs.CentralHelixOutput.fileName` override + restored `-LH` suffix on `outputs.LoopHelixOutput.fileName`)
Reason: discovered while debugging the SIGSEGV on OffSpill grid job that the in-flight cnf had a malformed `mcs.mu2e.description-CH.…` name in the appended fcl. Root cause: `recoMC/OffSpill.fcl` declares two outputs (`LoopHelixOutput` with `-LH`, `CentralHelixOutput` with `-CH` suffixes glued onto the desc token); mu2ejobfcl's auto-sub matches `\.<keyword>\.` patterns and so leaves `description-CH` literal. Bug independent of the SIGSEGV (would have surfaced at pushOutput SAM-declare for all 500 OffSpill jobs in MDC2025-025 even if reco had succeeded). Rule: for any reco entry, audit the upstream fcl's output declarations and override every output explicitly with `{desc}-<suffix>` form.

## [2026-05-11] update | fcldump --dataset two-pass cnf search

Bug: `fcldump --dataset <output>` assumes 1:1 desc→cnf mapping. Breaks
on suffixed-output cnfs (e.g. `cnf.mu2e.CeEndpointMix1BB` produces
`dig.mu2e.CeEndpointMix1BBTriggered.*` + `Triggerable.*`).

Fix: extracted `_search_jobdefs(jobdefs, desc, input_type, name_filter)`
helper. `find_matching_jobdef` now runs two passes — first with the
original name pre-filter (fast, ~1s for 1:1 case), then on miss without
the filter (full scan, ~2.5s for ~24 cnfs at MDC2025af_best_v1_1; cheaper
than expected because samweb caches dataset-file lookups).

Decisions captured:
- Two-pass over single-pass: preserves the fast path for the 80% case
  where 1:1 holds; fallback only fires on miss.
- No multi-match defense: SAM enforces uniqueness on (desc, dsconf,
  sequencer); two cnfs claiming the same output would collide at
  pushOutput, so first-match-wins is safe.
- No caching for v1: fallback wall-clock ~2.5s is acceptable for an
  interactive tool.

See memory `reference_cnf_to_output_desc_mismatch.md` for the bug class
(same trap as reco LH/CH suffixes, one stage upstream).

## [2026-05-12] update | /mu2eg4bl-submit skill added

`.claude/commands/mu2eg4bl-submit.md` — wraps upstream `mu2eg4bl` with
the three flags it actually needs in 2026 (`--g4bl-version=v3_08`,
`--predefined-args=sl7`,
`--jobsub-arg=--need-storage-modify=/mu2e/scratch/users/$USER/outstage`).
Bare invocation submits and runs but silently loses output to dCache 403;
verified 2026-05-11 against clusters 28125879 (no scope, 0 files) and
28127301 (with scope, 12 files). The skill also auto-builds
`Geometry.tar` from `Geometry/` and verifies a bearer token before
submitting. Cross-references memory
`reference_mu2eg4bl_needs_storage_modify.md`.

Production-style g4bl still goes through `/stage-entry g4bl` →
`json2jobdef` → native-AL9 prodtools runner (per
`wiki/pages/g4bl-runner.md`). This skill is for one-off beamline
studies that need the upstream mu2egrid path.

## [2026-05-12] update | /mu2ejobsub-submit skill added

`.claude/commands/mu2ejobsub-submit.md` — wraps upstream `mu2ejobsub`
for direct, one-off invocations (smoke tests, recoveries, ad-hoc
JIT-cnf submissions) outside the POMS-map / `submit_map` flow. Unlike
`/mu2eg4bl-submit`, no `--need-storage-modify` workaround is needed
because mu2ejobsub's Perl already requests WFOUTSTAGE scope internally
(per ADR `2026-04-30` §CB1).

Surfaces the critical caveat that `mu2ejobsub`'s worker shim does NOT
run pushOutput — outputs land in outstage but are not SAM-registered.
This is the gap Phase 2 (`submit_map --backend direct` →
`runmu2e.py` direct mode) was built to close. Skill body steers
users who need SAM-registered outputs to `/stage-entry` or
`submit_map --backend direct` instead.

Wraps the four `JOB_SET_SPECIFICATION` forms (`--all`,
`--firstjob/--njobs`, `--jobs`, `--jobset`); enforces exactly one;
defaults `--default-location=tape --default-protocol=root
--predefined-args=al9`; passes all other flags through verbatim.

## [2026-05-12] update | /jit-cnf-build skill added

`.claude/commands/jit-cnf-build.md` — wraps the front half of the
[JustInTimeFcl](https://mu2ewiki.fnal.gov/wiki/JustInTimeFcl) workflow:
build a one-off `cnf.<owner>.<desc>.<dsconf>.0.tar` from a hand-written
template.fcl via prodtools `jobdef` (thin wrapper around upstream
`mu2ejobdef`), then auto-smoke via `fcldump --local-jobdef --index 0`.

Composes with the recently-added `/mu2ejobsub-submit` skill: the
build-and-smoke output of this skill feeds directly into a
`/mu2ejobsub-submit <cnf>.tar --firstjob 0 --njobs N` invocation to
ship the JIT cluster (per JustInTimeFcl steps 1-4).

Refuses to author the template, set DbService overrides, or inject
output filename patterns — those are user responsibility per the
"no fallbacks for missing required data" feedback memory. Resolves
short `--setup MDC2025af` tags to full
`/cvmfs/.../Musings/SimJob/<tag>/setup.sh` paths. Surfaces ADR
`2026-04-30` §CB10 reminder that direct-input/template cnfs must use
the `mu2ejobsub` backend for submission (direct backend rejects
them).

For the declared-entries flow (`data/<campaign>/*.json`), use
`/stage-entry` instead — covered by `wiki/pages/json2jobdef-staging-workflow.md`.

## [2026-05-12] update | /mu2ejobdef-fcl skill added (upstream-Perl twin of /jit-cnf-build)

`.claude/commands/mu2ejobdef-fcl.md` — wraps the upstream Perl
binaries `mu2ejobdef` and `mu2ejobfcl` from `mu2egrid v8_03_02`
directly, bypassing the prodtools Python parity reimplementations
(`utils/jobdef.py`, `utils/jobfcl.py`).

Two subcommands:
- `build` — `mu2ejobdef --embed template.fcl ...` then optional
  `mu2ejobfcl --index 0` smoke
- `inspect` — `mu2ejobfcl --jobdef cnf.tar (--index N | --target T)`
  for the wiki's "Inspect fcl by index" / "by output filename"
  modes; supports `--default-proto file --default-loc dir:...` for
  the local-undeclared-input case

Intentional twin to `/jit-cnf-build`: same conceptual workflow,
different runtime. Useful for following the JustInTimeFcl wiki
verbatim, parity-testing prodtools against upstream, or as a fallback
when a prodtools wrapper misbehaves. Both end at the same downstream
(`/mu2ejobsub-submit cnf.X.tar --firstjob 0 --njobs N`), so cnfs
produced by either are interchangeable for submission.

Forwards all unknown flags verbatim — no rewriting `--option=value`
to `--option value`. Resolves bare SimJob tags (`MDC2025af`) to full
`/cvmfs/.../Musings/SimJob/<tag>/setup.sh` paths.

## [2026-05-19] ingest | Run1Bak resampler_beam additions (DS-off, v40 geom)
Pages written: 2026-05-19-run1bak-resampler-additions, run1bak-campaign
Pages updated: index.md (Campaigns + Sources), json2jobdef-staging-workflow (Related)
Reason: appended 4 new entries (NeutralsFlash, MuBeamFlash, EleBeamFlash, MuStopPileup) to `data/Run1B/resampler_beam.json` under dsconf `Run1Bak`, geom `geom_run1_b_v40.txt` + `bfgeom_DSOff.txt`, run 1470. Additive (originals at Run1Baa/Run1Baa1 v01 run 1440 preserved). Pion entries excluded. `input_data` refs unchanged (still consume upstream Run1B* outputs). Seeded the first Campaigns-section page in the wiki. Branch: field-off-option.

## [2026-05-19] update | Run1Bak NeutralsFlash xroot validation
Pages updated: 2026-05-19-run1bak-resampler-additions (added Validation section)
Reason: `mu2e -c cnf.mu2e.NeutralsFlash.Run1Bak.0.fcl -n 5` against real xroot input `sim.mu2e.Neutrals.MDC2025ae3.001430_00000001.art` completed status 0 (29 s CPU, 2.5 GB VmPeak). Confirms v40 geom + DS-off field loads cleanly under Run1Bak musing and resampler reads xrootd upstream. All 5 events filtered out by EarlyPrescaleFilter/DetStepFilter (normal prescale). Also documented 1-event smoke artifact (`inconsistent simStage: 1 vs 0`) as a pre-existing local-smoke limitation reproducing on Run1Baa originals too — not a Run1Bak regression. Other 3 entries (MuBeam/EleBeam/MuStopPileup) not yet runtime-validated.

## [2026-05-23] lint | 4 errors, 1 warning, 3 info
Report: [[lint-2026-05-23]]
Fixed: 3 issues — created [[reference-rpc-primary-inherits-bfgeom]] (closes 3 dead links, retires duplicated rule from 2 chain pages), substituted `[[run1bai-campaign]]` → [[run1bak-campaign]] in [[prodtools-prd]], refreshed `overview.md` with RPC/Pbar/Run1Bak ingest summary and new open questions. Remaining: 2 dead links ([[2026-04-21-pbi-sequence-implementation]], [[2026-04-24-mu2e-aitools-skills]]) and 3 info items.

## [2026-05-26] lint | 2 errors, 0 warnings, 3 info
Report: [[lint-2026-05-26]]
Fixed: none (audit-only; same 2 recurring broken slugs as last cycle: [[2026-04-21-pbi-sequence-implementation]] and [[2026-04-24-mu2e-aitools-skills]]). Improvement vs [[lint-2026-05-23]]: [[run1bai-campaign]] no longer counted as active broken link — confirmed [[prodtools-prd]] substitution from last cycle stuck. New coverage gap flagged: CosmicCRYAll MDC2025ap chain (3 non-obvious facts currently memory-only — cosmic chain `PrimaryOutput.fileName` requirement, bfgeom non-inheritance, outloc-lives-in-POMS-map recovery procedure) warrants an ingest page to complete the MDC2025ap trilogy alongside [[2026-05-22-mdc2025ap-rpcexternal-chain]] / [[2026-05-23-mdc2025ap-pbarstgun-chain]].

## [2026-05-26] ingest | MDC2025ap CosmicCRYAll chain + cosmic-vs-stop divergence
Page added: [[2026-05-26-mdc2025ap-cosmiccryall-chain]]
Reason: closes the coverage gap flagged in [[lint-2026-05-26]]. Captures three cosmic-vs-stop divergences (PrimaryOutput.fileName required, bFieldFile not inherited, inputFile defaults) that bit the push, plus the outloc-fix-in-POMS-map recovery procedure (also documented in `reference_outloc_lives_in_poms_map_not_cnf.md` memory). Sister ingest to [[2026-05-22-mdc2025ap-rpcexternal-chain]] and [[2026-05-23-mdc2025ap-pbarstgun-chain]]; explicitly cites [[reference-rpc-primary-inherits-bfgeom]] as a counterexample (the bfgeom inheritance rule does NOT apply to cosmic chains). Also flags input-residency caveat: `sim.mu2e.CosmicDSStopsCRYAll.MDC2025ab.art` is 44% tape-only at submission time despite entry tagging `inloc: disk`.

## [2026-05-27] ingest | input_data max_nfiles cap
Page added: [[input-data-max-nfiles]]
Reason: extended `_write_sam_inputs` in `utils/json2jobdef.py` with an optional `max_nfiles` key inside the nested-dict value form of `input_data` (caps per-dataset file count written to inputs.txt). Documents both the shape (positive-int validation, sorted prefix slice for non-random, min-bound for random branch) and the design rationale (why nested-dict not a sibling key — the parser's `for dataset, merge_factor in input_data.items()` loop would treat sibling keys as dataset names). Establishes the rule of thumb for future input_data options: extend the value dict, don't add sibling keys to the `{dataset: value}` map. njobs not auto-recomputed; entry author keeps `merge_factor × njobs ≤ max_nfiles`.

## [2026-05-29] ingest | Mu2eName unified dot-name grammar
Page added: [[mu2ename-unified-grammar]]
Reason: consolidated the Mu2e dot-name grammar into one value object (`utils/job_common.Mu2eName`). Covers file (6 fields), dataset (5 fields), and tarball (6 fields, slot 4 = integer index). Parse + build entry points, ~10 derivations (`.dataset`, `.with_sequencer`, `.as_tier`, `.log_dataset`, `.relpathname`), plus sub-field accessors (`.index`, `.campaign`, `.dsconf_base`, `.dsconf_version`, `.tier_class`) that absorbed the 7 "carve-out" sites the architecture audit flagged. Replaced two half-classes (`Mu2eFilename`, `Mu2eDSName`) and ~45 hand-rolled `.split('.')` / f-string call sites across 13 files in `utils/`. `Mu2eFilename` kept as alias of `Mu2eName` to preserve the Perl-parity contract on `relpathname()`. `_TIER_TO_OWNER_CLASS` deleted from `jobsub_argv.py` (now on `Mu2eName.tier_class`). Fail-loud type; two deliberately-lenient sites (`latestDatasets.parse_name`, `jobsub_argv.{description,campaign}_from_tarball`) keep their own `try/except → None` boundary. 203/203 unit tests green.

## [2026-05-29] lint | 3 errors, 4 warnings, 3 info
Report: [[lint-2026-05-29]]
Fixed: none (offered)

## [2026-05-29] ingest | digi output stream schema by fcl (bug found in MDC2025af CosmicCRYExtracted)
Pages written: digi-output-stream-by-fcl
Pages updated: index.md
Trigger: json2jobdef validator caught literal `desc` placeholder in MDC2025ap CosmicCRYExtracted cnf output; root cause = override keys (TriggeredOutput/TriggerableOutput) don't match Extracted.fcl's single Output stream.

## [2026-06-07] ingest | Run1Ban self-contained MuminusStopsCat rebuild chain
Pages written: 2026-06-07-run1ban-mustop-rebuild-chain, run1ban-campaign
Pages updated: run1bak-campaign, index, overview

## [2026-06-07] update | 2026-06-07-run1ban-mustop-rebuild-chain (stage-A push complete + TS3 collection clarification)
Pages updated: 2026-06-07-run1ban-mustop-rebuild-chain
Reason: pushed EleBeamFlash@Run1Ban-001 and NeutralsFlash@Run1Ban-001 cnfs to production SAM via MDC2025-029.json (alongside MuBeamFlash already there; 15000 jobs total). Each smoke-tested locally with `mu2e -c ... --nevts 5` to status 0. Documented the non-obvious cut-tree override in `epilog_1b.fcl` that moves the `Beam` write point from DS2Vacuum to TS3Vacuum — explaining why EleBeamCat.Run1Bai / MuBeamCat.Run1Bai are safe to reuse as seeds for Run1Ban resamplers (TS region is identical between v06 and v40 musings) and why only MuminusStopsCat needs a Run1Ban rebuild.

## [2026-06-14] ingest | Run1Ban-001 primaries (CeEndpoint, FlateMinus, FlatGamma, NoPrimary) added
Pages written: 2026-06-14-run1ban-primaries-added
Pages updated: run1ban-campaign, 2026-06-07-run1ban-mustop-rebuild-chain, index
Reason: 4 entries appended to data/Run1B/primary_muon.json (after Run1Bai-001 block), mirroring Run1Bai shape with three deliberate divergences: geom v40 (not v06), run 1470 (not 1460), input MuminusStopsCat.Run1Ban (not Run1Bai). All 4 smoke-tested locally (mu2e -c --nevts 1 → Art status 0) on 2026-06-14 against /pnfs/.../MuminusStopsCat.Run1Ban tape file via xrootd. 26000 new jobs to extend MDC2025-029.json (20004 → 46004, under 100k cap). Not yet pushed to production.

## [2026-06-15] ingest | Run1Ban STM resampler entries added
Pages written: 2026-06-15-run1ban-stm-resampler-port
Pages updated: run1ban-campaign, index
