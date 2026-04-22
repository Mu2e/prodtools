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
