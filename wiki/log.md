# Wiki Log

Append-only. Format: `## [YYYY-MM-DD] <operation> | <title>`
Recent entries: `grep "^## \[" log.md | tail -10`

---

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
