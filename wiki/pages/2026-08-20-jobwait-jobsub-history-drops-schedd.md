---
title: jobwait 0/N ok on successful cluster — jobsub_history drops -name schedd
tags: [incident]
sources: [2026-08-20-prodtools-jobwait-empty-history-unknown-rc]
updated: 2026-08-20
---

# jobwait `0/N ok` on a fully successful cluster — jobsub_history drops `-name <schedd>`

**Source:** `autoresearch/docs/handoff/prodtools-jobwait-empty-history-unknown-rc.md` (copied to `wiki/raw/`)
**Date ingested:** 2026-08-20
**Type:** incident

## Summary

`jobwait` watched cluster `29868598@jobsub05.fnal.gov` (autoresearch
closed loop, gridsmoke04/foilspf/mubeam, 15 jobs) to `drained`, then
reported `0/15 ok, unknown: [0..14]` and exited nonzero. All 15 jobs
had exited 0 with complete outstage output; the caller correctly
refused to count `unknown` as ok and terminated the chain, wasting the
~1 h cluster. The history query `jobsub_history -G mu2e -J
29868598@jobsub05.fnal.gov -limit 15 -af ProcId ExitCode` returned
header and zero data rows — while a same-day cluster on jobsub01
answered normally. The handoff's working theory was a schedd-dependent
history outage.

Root cause is a **jobsub_lite wrapper bug**, not a schedd defect and
not fading history. The deployed `jobsub_history` (jobsub_lite 1.13,
`/opt/jobsub_lite/bin/jobsub_history`) regex-extracts the `@schedd`
from the jobid and appends `-name <schedd>` to its argument list — one
line before reassigning that list (`passthru = out`), discarding the
append. Every jobsub_history query on the node therefore goes to the
default `SCHEDD_HOST` (jobsub01.fnal.gov on the gpvms). jobsub01
clusters answer by coincidence; clusters on any other schedd return
header-only. Decisive proof: direct `condor_history -name
jobsub05.fnal.gov 29868598 -limit 15 -af ProcId ExitCode` returned all
15 rows, every ExitCode 0. Upstream jobsub_lite master has rewritten
the wrapper (`lib/mains/cmd.py`) and passes `-name` correctly.

Fix (prodtools, 2026-08-20, branch `code-tarball`):
`collect_exit_codes` in `utils/jobwait.py` shells `condor_history`
directly with `-name` split from the jobid, and an empty history now
logs "no usable records for cluster N on <schedd>" so it reads as
history-unavailable, not as N failed jobs. The jobwait spec's
deliberate non-features (no file checks, honest `unknown`) are
untouched — this was the "query can be made to work" path the handoff
hoped for. Verified live: fixed code reads the lost cluster as 15/15
rc=0. Note cvmfs prodtools v3.1.0 still ships the old code until the
next release.

## Key Takeaways

- An empty `jobsub_history` answer proves nothing about the schedd:
  the 1.13 wrapper never sends `-name`, so it only ever describes the
  default SCHEDD_HOST. Verify with direct `condor_history -name` before
  believing any empty result.
- The bug class is worse than empty answers: a cluster-id collision on
  the default schedd would return a *different* cluster's exit codes —
  silent wrong data. Direct `-name` closes that too.
- `condor_history` works as a plain user from the gpvms, no extra auth;
  `-limit N` keeps the newest-first scan fast (8.4 s vs 51 s measured
  on a 999-job cluster).
- The caller-side guard "`unknown` never counts as ok" converted a
  silent wrong verdict into a visible failure — that contract did its
  job and stays.
- Pattern echo: this is the second jobsub_lite wrapper defect that
  survives because the wrapper *looks* like the condor tool
  (`jobsub_q -af` blank-values was the first). Prodtools now trusts
  direct condor tools for reads.

## Entities Touched

- `utils/jobwait.py` / the jobwait design spec
  (`docs/superpowers/specs/2026-08-16-jobwait-design.md`, addendum
  appended 2026-08-20)
- jobsub_lite 1.13 (`/opt/jobsub_lite/bin/jobsub_history`) — report
  upstream / upgrade node install
- autoresearch closed-loop grid path (the reporter and the caller)

## Relation to Other Wiki Pages

- [[2026-04-30-phase2-direct-jobsub-implementation]] — same direction
  of travel: bypass wrappers, drive condor/jobsub primitives directly.
