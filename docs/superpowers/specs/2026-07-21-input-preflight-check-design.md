# Input Pre-flight Check — Design

**Date:** 2026-07-21
**Status:** approved (brainstorm complete)
**Author:** oksuzian + Claude

## Problem

A campaign's input files can be present-but-broken or absent from the
disk area a job expects, and today nothing checks before jobs launch.
Both failure modes produce identical mass job-death:

1. **Truncated / corrupt file** on resilient. Observed 2026-07-21:
   `dts.mu2e.NeutralsFlashCat.MDC2025ad.001430_00000637.art` was 1 MiB
   instead of 113 MB (an interrupted copy, or a monthly resilient
   purge catching a file mid-life). The file *existed*, so every
   existence check passed; only art discovered it at open time. Index
   519 failed deterministically, twice, and its blast radius was 9 of
   7000 jobs.
2. **Evicted tape input** (NEARLINE). A primary input that has fallen
   off tape-backed dCache triggers a slow tape recall per job that can
   time out the lease.

Neither is caught until jobs are already on the grid, where they die in
bulk and cost a recovery cycle each.

## Goal

Verify a campaign's inputs are readable **before** committing jobs.
Read-only, block-only: refuse the campaign and exit 2 (the existing
"needs a human" convention), never remediate automatically.

## Non-goals

- **No auto-remediation.** The check never re-copies files or triggers
  prestaging. It reports and blocks; a human runs `/prestage` or
  re-stages, then reruns.
- **No per-tick gate.** The check runs once at campaign enqueue, not
  before every slice. A file purged mid-campaign is NOT caught by the
  gate — the runtime tape fallback is the accepted safety net for that
  window, and the standalone command is available to re-check by hand.
- **No prestaging logic.** `mdh prestage-files` lives in the existing
  `/prestage` skill; this feature detects and points there.

## Decisions (resolved during brainstorm)

| Question | Decision |
|----------|----------|
| On failure | Block only, exit 2. Never remediate inside the loop. |
| Cadence | Gate at `submit_map --enqueue` + standalone `bin/check_inputs`. NOT per-tick. |
| Prestaging | Detect NEARLINE, defer to `/prestage`. No prestage logic here. |
| Missing from disk home | Check the intended disk location directly; do not let the resolver's SAM/tape fallback reclassify it. |
| Severity | Any problem hard-blocks (exit 2). Messages distinguish crash-class (truncated) from re-stage-class (off-disk / NEARLINE). |

### Rationale for "missing from disk" = hard block

The runtime resolver falls back to tape when a resilient file is
absent, so a job whose pileup was purged would still *complete*, just
by reading from tape. That fallback is acceptable at RUN time. It is
not acceptable to LAUNCH into: a mixing job reads ~90 pileup files, and
mass tape recalls across a 500-job slice can hammer the tape system and
time out leases (MuStopPileup p99 is already ~19 h). So the enqueue
gate blocks on off-disk inputs and asks the human to re-stage first.
The tape fallback then only ever catches files purged AFTER the gate
passed — the narrow mid-campaign window the non-goal accepts.

## Architecture

Three pieces, each with one responsibility:

- **`utils/check_inputs.py`** — the logic.
  `check_inputs(tarball_path, inloc) -> (ok: bool, problems: list[Problem])`.
  A `Problem` carries `(dataset, filename, kind, detail)` where `kind`
  is one of `truncated`, `missing` (absent from its intended disk
  home), `nearline` (tape input not staged), or `query_error`. Pure: it
  calls the shared resolver and the SAM/mdh helpers, holds no I/O of
  its own.
- **`bin/check_inputs`** — thin CLI over the logic. Takes one or more
  tarball paths, prints a report grouped by dataset, exits 0 (clean) or
  2 (any problem).
- **Gate wiring in `utils/submit.py`** — the `--enqueue` path calls
  `check_inputs` for each entry's tarball before `create_campaign`. Any
  problem prints the report and exits 2 with NO campaign row written —
  the same refusal shape as the existing `--enqueue --no-ledger` guard.

## What it checks

Read the tarball once via `Mu2eJobPars`. The input file set is frozen
in the tarball and deduplicated:

- `tbs['inputs']` — primary input files
- `tbs['auxin']` — pileup files

This is the whole set (8470 files for the reference campaign, read in
0.37 s) — NO per-index iteration. (An earlier prototype iterated 7000
job indices to reconstruct this same list; the tarball already holds
it.)

For each **distinct** file, resolve its true read path with the SAME
resolver jobfcl uses:

```python
FileResolver(inloc=entry['inloc'], proto='root').locate(filename)
```

This is the single source of truth for where a job reads each file —
reusing it means the check verifies exactly what the job will stream,
with no independent path logic to drift. (Drift between a job's path
logic and an independent copy is the exact class of bug that caused the
2026-07-21 log-to-tape incident.)

Classify by the intended disk home and verify:

| Intended location | Check | Fail kinds |
|-------------------|-------|-----------|
| `/pnfs/mu2e/resilient/…` | present AND size matches SAM | `missing`, `truncated` |
| `/pnfs/mu2e/persistent/…` (disk) | present AND size matches SAM | `missing`, `truncated` |
| `/pnfs/mu2e/tape/…` | `mdh query-dcache -o` locality | `nearline` |
| stash / cvmfs | present | `missing` |

Expected sizes come from `samweb list-files --fileinfo` — one query
per dataset, returning exact per-file sizes. Exact size (not a
heuristic threshold) is what distinguishes a truncated file from a
legitimately small one: the reference campaign's EleBeamFlashCat files
are ~3 MB while NeutralsFlashCat are ~112 MB, so an absolute threshold
gives false positives; SAM's recorded size is authoritative.

### The disk-home direct check (resolves the flagged subtlety)

`FileResolver.locate()` for a `resilient` inloc returns the resilient
path only when the file is present; when it is ABSENT it falls back to
SAM and returns wherever else the file lives (typically tape). That
fallback is correct for a running job but would let a
purged-from-resilient file masquerade as a normal tape input in the
check, hiding exactly the purge scenario this feature targets.

So the check does not rely on `locate()`'s fallback for the presence
decision. For a file whose intended home is a disk area (resilient or
persistent), it verifies presence AT THAT DISK PATH directly. A pileup
file absent from resilient is reported `missing` (absent from its disk
home), with the `/prestage <dataset>` command to re-stage it — it is
NOT silently reclassified as a tape input.

## Data flow

Enqueue gate:

1. `submit_map --enqueue <map>` builds entries as today.
2. For each entry, `check_inputs(tarball, entry['inloc'])`.
3. Any problem across any entry → print the grouped report, `exit 2`,
   create NO campaign rows.
4. All clean → `create_campaign` as today.

Standalone:

1. `bin/check_inputs <tarball> [<tarball> …]`.
2. Read each tarball's `inloc` from its own entry shape (or accept
   `--inloc` when a bare tarball has none).
3. Print the report; RC 0 (clean) or 2 (any problem).

## Error handling

Fail **closed**, matching `verify_row`: if a SAM size lookup or an mdh
locality query fails, that file becomes a `query_error` problem (block)
— never assumed OK. A file absent from SAM entirely is a `missing`
problem. If `FileResolver.locate()` raises, that is a problem too. The
check never guesses a file is fine.

## Testing

Unit tests on in-memory tarballs (the existing `test_unit.py` pattern),
mocking the SAM file-size lister and the mdh locality query:

- all inputs present and correctly sized → `ok=True`, no problems
- a resilient file truncated (size < SAM) → `truncated`
- a resilient file absent from disk → `missing`, with a `/prestage`
  hint, NOT reclassified as tape
- a tape input NEARLINE → `nearline`, with a `/prestage` hint
- a tape input ONLINE / ONLINE_AND_NEARLINE → ok
- SAM lookup raises → `query_error` (fail closed, blocks)
- mdh query raises → `query_error` (fail closed, blocks)

Gate test: an entry with a failing check leaves NO campaign row in the
ledger and the process exits 2.

## Files

- Create: `utils/check_inputs.py`, `bin/check_inputs`
- Modify: `utils/submit.py` (enqueue gate), `test/test_unit.py` (tests)
- Reuse (no change): `utils/file_resolver.py` (`FileResolver`),
  `utils/jobquery.py` (`Mu2eJobPars`), `utils/samweb_wrapper.py`
  (file-size lister), the `/prestage` skill (referenced in messages).
- Docs: `EXAMPLES.md` regen via `docs/EXAMPLES_schema.md` +
  `/refresh-examples`; wiki runbook note.
