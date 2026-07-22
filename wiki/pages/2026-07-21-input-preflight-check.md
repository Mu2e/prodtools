---
title: Input pre-flight check — resilient size + tape prestage gate
tags: [decision, check_inputs, submit_map, enqueue, resilient, prestage, mdh, data-handling, sliced-campaigns]
sources: [docs/superpowers/specs/2026-07-21-input-preflight-check-design.md, docs/superpowers/plans/2026-07-21-input-preflight-check.md]
updated: 2026-07-21
---

# Input pre-flight check — resilient size + tape prestage gate

`check_inputs` (`utils/check_inputs.py`, CLI `bin/check_inputs`) verifies
a campaign's input files are *readable* before jobs launch. It exists
because two failure modes each kill a whole slice of jobs identically,
and nothing checked for them until the jobs were already on the grid:

1. **A truncated / missing file on resilient.** The trigger was the
   2026-07-21 index-519 incident — a pileup replica
   (`dts.mu2e.NeutralsFlashCat.MDC2025ad.001430_00000637.art`) was
   exactly 1 MiB instead of 113 MB (interrupted copy, or a monthly
   resilient purge catching a file mid-life). The file *existed*, so
   every existence check passed; only art discovered it at open time.
2. **An evicted (NEARLINE) tape input** — a slow per-job tape recall
   that can time out the lease.

See the incident detail in [[log]] (2026-07-21 entries).

## What it does

Reads the frozen input list straight from the cnf tarball
(`Mu2eJobPars`): `tbs.inputs` = primary, `tbs.auxin` = pileup — the whole
set, deduplicated, no per-index reconstruction. For each distinct file
it checks at the file's real read home:

| input class | check | tool |
|---|---|---|
| resilient pileup (`auxin`, `inloc=resilient`) | present AND size == SAM | direct `os.path.getsize` vs `list-files --fileinfo` |
| tape / persistent primary (`inputs`) | staged (not `NEARLINE`) | `mdh query-dcache -o` |

**mdh is blind to resilient** — it only knows tape/disk/scratch
(`/pnfs/mu2e/tape`, `/pnfs/mu2e/persistent`, `/pnfs/mu2e/scratch`), never
`/pnfs/mu2e/resilient`. That is *why* the two-mechanism split is
mandatory, not incidental: resilient must be POSIX-stat'd (it is a flat
path, statable on interactive nodes; stat triggers no recall), tape must
go through mdh (it uses hash subdirs mdh computes internally).

Problems are one of four kinds: `truncated`, `missing`, `nearline`,
`query_error`. Everything **fails closed** — a SAM/mdh failure or an
unparseable result is a `query_error`, never assumed OK.

## Design decisions (rationale)

- **Read-only, block-only.** The check never re-copies a file or
  prestages. It reports and exits 2 (the "needs a human" convention).
  Prestaging stays in the existing `/prestage` skill; `check_inputs`
  detects `NEARLINE` and prints the `/prestage <dataset>` command.
- **Enqueue gate, not per-tick.** `submit_map --enqueue` runs the check
  before writing a campaign row; a failing entry blocks with a grouped
  report and **no campaign is created**. It does NOT run before every
  slice — a file purged *mid-campaign* is not caught by the gate. The
  runtime tape-fallback is the accepted safety net for that window, and
  the standalone `bin/check_inputs` is available to re-check by hand when
  a monthly purge is suspected.
- **Missing-from-disk is a hard block, not a warn.** A pileup file
  absent from resilient would still let its job *complete* via the
  runtime resolver's tape fallback — so "tape fallback at run time" is
  acceptable. But launching a 500-job slice into mass tape recalls
  hammers tape and times out leases, so the gate blocks and asks the
  operator to re-stage first. The fallback then only ever covers files
  purged *after* the gate passed.
- **Check the disk home directly — do not reuse `FileResolver.locate()`.**
  `locate()` falls back to SAM (→ tape) when a resilient file is absent,
  which would let a *purged* pileup file masquerade as a normal tape
  input and pass. The check reuses the file-resolver *primitives*
  (`resilient_path`, `infer_dataset_location`) but verifies the resilient
  path directly, so a missing pileup file is reported `missing` rather
  than reclassified. This is the one place the implementation
  deliberately departs from the spec's "reuse `FileResolver`" wording —
  same intent (no independent path grammar), correct mechanism.

## Usage

```bash
# by hand, before launching or when a purge is suspected:
check_inputs cnf.mu2e.RMCPhaseSpace0NExternalMix1BB.MDC2025ar_best_v1_3.0.tar
```

Needs no mu2epro — a status check, safe to run as yourself. Exits 0 clean
/ 2 on any problem. Flags: `--inloc` (default `resilient`), one or more
cnf tarballs. Full usage in `EXAMPLES.md`.

Related: [[direct-recovery-loop]] (the campaign workflow this gates),
[[log]] (the truncated-file incident and the two systemic gaps it
exposed — staging accepts partial copies with no verify; a failed job's
log push silently no-ops).
