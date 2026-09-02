---
title: Direct-backend art logs never carried the SAM manifest (fixed v3.3.2)
tags: [incident, runmu2e, logs, manifest, mu2e, g4bl, direct-backend, pushoutput]
sources: []
updated: 2026-09-01
---

# Incident: mu2e log manifest never landed

## Summary

Direct-backend art logs never contained the `mu2egrid manifest` block;
g4bl logs did.

## Evidence

`log.mu2e.CeEndpoint.Run1Ban-001.617-1781534797.log` under
`/pnfs/mu2e/persistent/datasets/phy-etc/log/mu2e/CeEndpoint/Run1Ban-001/log/0e/f5/`
— 0 matches for `mu2egrid manifest`; its line 891 reads
`Copying jobsub log from /srv/jsb_tmp/JOBSUB_LOG_FILE to
log.mu2e.CeEndpoint.Run1Ban-001.617.log`. Found 2026-09-01 while
tracing the runmu2e consolidation seam.

## Root cause

`_direct_dispatch` appended the manifest only `if
Path(log_file).exists()`; nothing created the SAM-named log before
that point on a worker — `push_logs` created it afterwards by copying
the jobsub log. g4bl was exempt only because its runner streamed its
own log file first.

## Impact

The manifest leg of the output-integrity chain was absent for every
direct-backend art job; the SAM-declared log lacked the `sha256sum`
lines and the `diskUse` header. No data-integrity consequence by
itself (pushOutput's CRC and dCache's check still ran), but
manifest-based consumers had nothing to read.

## Fix

prodtools v3.3.2 — commit `92dc555` ("fix(runmu2e): materialize the
SAM log before the manifest step"): `_materialize_log` copies the
jobsub log first, `_finish_job` appends the manifest, for both
runners.

## Detection going forward

`grep -c 'mu2egrid manifest' <log>` on a freshly declared log after
the release: expected 2 (header + selfcheck).

## Related

- [[g4bl-runner]] — the runner that was exempt from this bug (it
  streamed its own log first), and now shares the same `_finish_job`
  push tail as mu2e.
