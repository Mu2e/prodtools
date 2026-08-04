---
title: Draining smoke — worker declared its input for push; pushOutput tried to delete production data
tags: [incident, draining, worker, pushoutput, token-scopes]
sources: []
updated: 2026-08-02
---

# 2026-08-02 draining smoke: input-as-output push, tape delete attempted

The mandatory 2-file draining smoke (cluster 29444911, campaign 1 in
`submissions-smoke.db`, generic reco cnf `cnf.mu2e.reco.MDC2025au_best_v1_1`
over `dig.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5.art`) FAILED at the
push stage — and in doing so caught a defect that would have attempted to
delete production inputs from tape at scale.

## What happened (per-job, both jobs identical)

1. Dispatch and reco were correct: `[direct] files[i] = dig...art`, input
   fetched (4.2 GB local copy), art exit 0, 13.6 GB mcs output produced
   with the right name (`mcs.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_1.<seq>.art`).
2. `push_data` built `output.txt` by globbing cwd against the map entry's
   `outputs[].dataset` = `*.art` — which matched BOTH the mcs output and
   the fetched input copy. The input was declared for push to tape.
3. pushOutput found the "output" already at its dataset path
   (`/pnfs/mu2e/tape/phy-sim/dig/...`), aged it past recoverTime (file and
   SAM record ~117000 s old), classified it a stale orphan, and ran its
   recover step: **three delete attempts on the production dig input**.
4. Every `gfal-rm` failed `HTTP 403 Permission refused` — the job token's
   per-desc `storage.modify` scope covered `/mu2e/tape/phy-sim/mcs/mu2e`
   only, not `dig`. The narrow scope (final-review fix I2) was the only
   line of defense.
5. Worker classified rc=2 as non-retryable, pushed the log, exited 1.
   **No data landed**; both dig inputs verified intact afterwards
   (enstore locations live, SAM records live).

## Root cause

Submission-side token-scope derivation uses `expected_outputs_for` (exact
mcs names); the worker's push half globs local files. The two halves
disagreed about what the job's outputs were — exactly the drift
`expected_outputs_for` was created to prevent; the push path had never
adopted it. Index-mode direct campaigns (MDC2025ar generic reco) never hit
this because their map entries carried explicit per-desc dataset names, so
the glob could only match the real output. The draining smoke map used
`*.art`, arming the hazard.

## Fixes (commit `0a405ee`, 771 tests green)

- `runmu2e.push_data` now excludes `infiles` basenames from every glob
  match — an output is never its own parent. Applies to all modes;
  behavior-neutral when inputs can't match a glob (template mode keeps
  inputs in `indir/`; resampler `infiles=''` unchanged).
- `submit._validate_draining_entry` refuses outputs globs that fnmatch the
  `input_pattern` (heuristic gate; the worker exclusion is authoritative).
  Draining maps should use tier-specific globs: `mcs.*.art`.

## Resolution — re-smoke PASSED (same day)

The re-smoke ran through the file-keyed recovery path itself: campaign
paused, real tick → `verify_files_row` found 2/2 missing → row 1 closed
`recovered` → `resubmit_files` child row 2, cluster 29446165 (4000 MB /
48 h recovery floor, fixed worker bundle). Drained in 135 min. Worker
log shows `Pattern '*.art' matched 1 files: ['mcs...']` — the input was
excluded live even under the hazardous glob still snapshotted in the
campaign entry. Both mcs outputs on tape WITH SAM parentage to their
dig inputs; logs pushed; closing tick closed row 2 `complete`. The
draining smoke gate is PASSED; this was also the first live exercise of
file-keyed recovery.

## Residuals

- **Two junk log files registered in SAM** under the *digi* dsconf
  namespace: `log.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5.001430_00000000-1785680818.log`
  and `...001430_00000001-1785683502.log`. Cleanup via `/retire-file`
  (operator runs it).
- **Log naming in direct-input mode is input-derived**
  (`replace_file_extensions(fcl, ...)`, fcl named after the input), so
  reco logs land in `log.<input desc>.<INPUT dsconf>.log`. MDC2025ar hid
  this because input and cnf dsconfs coincided (`ar_best_v1_1` both); the
  au smoke (dig v1_5 vs cnf v1_1) exposed it. Open decision: keep and
  document, or derive the log name from the expected output name.
- Campaign 1 row 1 still `active` in the smoke DB; the re-smoke plan is
  pause campaign → real tick → `verify_files_row` finds both missing →
  `resubmit_files` recovery ships the fixed worker (first live exercise of
  file-keyed recovery).

## Lessons

- The pre-production live smoke earned its "MANDATORY" status twice over:
  unit fakes could not see this (they restate glob behavior), and the
  final review's live SAM verification (create_date) had already shown
  the pattern.
- Narrow per-desc token scopes are not bureaucracy — they were the only
  thing between a smoke test and deleting production tape files. Never
  broaden scopes "for convenience".
- pushOutput's orphan recovery will delete an existing same-name file at
  the destination dataset path if it is older than recoverTime. Anything
  that mis-declares an existing production filename as an output arms it.

## Related

- [[2026-07-18-direct-recovery-loop]] — draining section.
- [[2026-07-12-hygiene-tiers-and-kept-duplication]] — the byte-identity /
  single-home discipline this incident vindicates.
