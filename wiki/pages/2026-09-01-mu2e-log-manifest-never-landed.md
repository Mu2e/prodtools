---
title: Direct-backend art logs never carried the SAM manifest (fixed v3.3.2)
tags: [incident, runmu2e, logs, manifest, mu2e, g4bl, direct-backend, pushoutput]
sources: []
updated: 2026-09-02
---

# Direct-backend art logs never carried the SAM manifest

## Summary

Direct-backend art logs never contained the `mu2egrid manifest` block;
g4bl logs did. Two independent, stacked defects — a missing file AND a
worker-side rewrite that would have discarded it anyway — both had to
be fixed before a production SAM log could carry the manifest.

## Evidence

`log.mu2e.CeEndpoint.Run1Ban-001.617-1781534797.log` under
`/pnfs/mu2e/persistent/datasets/phy-etc/log/mu2e/CeEndpoint/Run1Ban-001/log/0e/f5/`
— 0 matches for `mu2egrid manifest`; its line 891 reads
`Copying jobsub log from /srv/jsb_tmp/JOBSUB_LOG_FILE to
log.mu2e.CeEndpoint.Run1Ban-001.617.log`. Found 2026-09-01 while
tracing the runmu2e consolidation seam. This evidence log is itself
**POMS-era** — its tail runs `runmu2e --jobdesc ...`, not the direct
backend's `MU2EGRID_JOBDEF` invocation — but the POMS path shared the
identical `_direct_dispatch` / `_emit_manifest` code, so the
missing-manifest mechanism (layer (a) below) applied to it unchanged.

## Root cause

Two layers, found one after the other:

**(a) Nothing created the file before the manifest step.**
`_direct_dispatch` (now `_finish_job`) appended the manifest only `if
Path(log_file).exists()`; nothing created the SAM-named
`log.<owner>.<desc>.<dsconf>.<seq>.log` on a worker before that
point — `push_logs` created it *afterwards* by copying the jobsub log
(`$JSB_TMP/JOBSUB_LOG_FILE`). The manifest step therefore always found
the file missing and silently skipped it. g4bl was exempt only because
its runner streamed its own log file first, so the file already
existed by the time the manifest step ran.

**(b) Even a file that exists doesn't survive the push.** Fixing (a)
(commit `92dc555`) turned out not to be enough: OfflineOps
pushOutput's `writeLog` (`Util/pushOutput.py:801`) `os.remove()`s
every `log`-tier file it pushes and rewrites it from
`$JSB_TMP/JOBSUB_LOG_FILE` plus a `JOBSUB_ERR` banner before declaring
it — for every `disk`/`scratch`/`tape` destination. So the file
`_emit_manifest` appends the manifest block to is discarded by
pushOutput itself, regardless of whether the file existed going in.
Only the `outstage` destination (`_copy_to_outstage`, ifdh, no
pushOutput) ever shipped runmu2e's own file untouched. This is why
92dc555's commit message and this project's own design spec claimed
"mu2e SAM logs gain the manifest" while that claim was still false for
every pushOutput-declared log.

## Impact

The manifest leg of the output-integrity chain was absent for every
pushOutput-declared direct-backend art log — the SAM-declared log
lacked the `sha256sum` lines and the `diskUse` header. No
data-integrity consequence by itself (pushOutput's CRC and dCache's
check still ran), but manifest-based consumers had nothing to read.

## Fix

Two commits, both required:

- prodtools v3.3.2 — commit `92dc555` ("fix(runmu2e): materialize the
  SAM log before the manifest step"): `_materialize_log` copies the
  jobsub log first, `_finish_job` appends the manifest, for both
  runners. Closes layer (a) — necessary but not sufficient.
- commit `987f987` ("fix(runmu2e): print the SAM manifest to stdout,
  not just the log file", this fix wave): `_emit_manifest` builds the
  manifest block once and both appends it to the file (serving
  `outstage`) AND prints the identical block to stdout. On a worker,
  stdout IS `$JSB_TMP/JOBSUB_LOG_FILE`, so the block survives
  `writeLog`'s rewrite into the SAM-declared log. Closes layer (b) —
  this is what actually lands the manifest in a production SAM log.

## Detection going forward

`grep -c 'mu2egrid manifest' <log>` on a freshly declared **v3.3.2+**
log: expected **≥ 1**. Do not promise **2** (header + selfcheck) the
way this page originally did — that count was a property of the local
file copy, not of whatever `writeLog` ultimately assembles into the
SAM-declared log.

**Verified 2026-09-02:** the first SAM-declared log produced by the fixed
tail (`_finish_job`, branch HEAD `bdd677f` shipped as a dev tarball) —
`log.oksuzian.G4blSmoke.MCPTest006.00000000-1788407754.log`, g4bl grid
smoke, campaign 5 / cluster 29824782@jobsub04 — carries **2** manifest
lines (header at 10200, selfcheck at 10229, sha256 of the nts between).
So under the stdout-print mechanism the selfcheck line does survive
`writeLog` too; the gate stays "≥ 1" because that is what the mechanism
guarantees. This was a g4bl job; the same `_finish_job` tail serves mu2e
jobs, but no mu2e SAM log has been checked yet.

## Related

- [[g4bl-runner]] — the runner that was exempt from layer (a) (it
  streamed its own log first), and now shares the same `_finish_job`
  push tail as mu2e, including the stdout-print fix for layer (b).
