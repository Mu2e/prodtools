---
title: "Mix merge factors: sizing to ~5 GB with exact, round job counts"
tags: [reference, prodtools, mixing, templates, sizing]
sources: []
updated: 2026-07-25
---

# Mix merge factors: sizing to ~5 GB with exact, round job counts

**Date:** 2026-07-25
**Applies to:** `templates/MDC2025/mix.json` (per-desc `merge`, consumed by
`utils/chain_emit.py`)

The MDC2025 mix template carries one `merge` factor per description. This
page records how those numbers were derived, the three constraints they
satisfy, and the traps that produce wrong answers.

## The three constraints

1. **~5 GB output files** — the tape-friendly target.
2. **`merge` exactly divides the input file count** — otherwise the last
   job is a runt.
3. **The resulting job count is a round number** — operational
   legibility (500, 400, 250, 1000 … not 417 or 539).

All three are satisfiable for 18 of 19 descs. See "Where it is impossible"
below for the exception.

## Deriving GB-per-input-file — measure, never infer

The correct denominator is **bytes of output per input file**, and the only
trustworthy way to get it is from a completed round:

```
GB/in = (output dataset total size) / (input files consumed)
```

**The whole MDC2025ar mix round ran at merge 1.** Every
`dig.mu2e.<desc>Mix1BB.MDC2025ar_best_v1_3.art` has a file count equal to
its `dts` input file count, so for that round `total size / file count` is
already bytes-per-input-file with no merge arithmetic in the way. Verify
this before reusing the shortcut on a later round:

```bash
samweb list-files --summary "dh.dataset dig.mu2e.<desc>Mix1BB.<conf>.art"
samweb count-files "dh.dataset dts.mu2e.<desc>.<campaign>.art"
```

Two known deviations from a clean 1:1 in the ar round:
- `PbarResampling` — 4974 output vs 5000 input (26 files lost; divide by
  4974, not 5000).
- `ensembleMDS3c` — input itself is 4960, not a round 5000.

Then `merge = round(5.0 / GB_per_in)`, snapped to a divisor of the input
file count (see below). GB here is **10^9 bytes**, matching SAM's
`Total size`.

## Trap: a merge factor belongs to a (desc, input campaign) PAIR

Not to a desc alone. dts granularity varies between campaigns, and the
error is large enough to invert a recommendation:

> `PbarResampling` digi measured against MDC2025ap gave 14.8 GB/file;
> against MDC2025ar it is 0.89 GB/file — a **16×** difference, because ar
> dts files hold 1,962 events vs ap's 32,703. The au round consumes **ar**.
> Measuring ap flipped the recommendation from merge 56 to merge 2.

Always resolve the input campaign from the emit before measuring:

```bash
./bin/latestDatasets --emit mix --campaign MDC2025au    # NOT `bash bin/...`
```

then read `input_data` out of each emitted entry. Note `latestDatasets` is
a `#!/usr/bin/env python3` script — invoke it directly; the `bash bin/<cmd>`
pattern in `/mu2e-run` only works for the bash entry points.

## Trap: a remainder is harmless, but it is still a runt job

The arithmetic is ceil-div with a clamped final slice — no input file is
ever dropped:

- `utils/job_common.py:256` — `(len(filelist) + merge - 1) // merge`
- `utils/job_common.py:350` — `last = min(first + merge - 1, nf - 1)`

So a non-dividing merge factor is a cosmetic/efficiency problem (one short
job producing an undersized file), never a correctness or data-loss
problem. Worth fixing when the nearest exact divisor costs little; not
worth a big size compromise.

## Trap: display rounding leaks into the arithmetic

Deriving merge from a `%.2f`-formatted GB/file value gives wrong integers.
`RPCExternalPhysical` at 0.0536 GB/file is merge **93**, not 100 — but two
decimals hides the difference. Compute from raw byte totals.

(In that specific case the *divisibility* constraint independently selects
100 anyway — 5000/100 = 50 jobs exactly, at 5.36 GB. Right answer, but for
a reason the rounding shortcut did not supply.)

## The measured table (2026-07-25, au round inputs)

| desc | input | GB/in | merge | jobs | GB/out |
|---|---|---|---|---|---|
| CeMLeadingLog | ap 2000 | 1.249 | 4 | 500 | 5.00 |
| CePLeadingLog | ap 2000 | 0.945 | 5 | 400 | 4.73 |
| CosmicSignal | ap 5000 | 0.414 | 10 | 500 | 4.14 |
| DIOtail95 | ap 5000 | 1.109 | 5 | 1000 | 5.54 |
| FlatGamma | ap 500 | 1.363 | 4 | 125 | 5.45 |
| FlatGammaCalo | ap 2000 | 0.771 | 5 | 400 | 3.86 |
| FlateMinus | ap 1500 | 0.903 | 6 | 250 | 5.42 |
| FlatePlus | ap 1500 | 0.906 | 6 | 250 | 5.44 |
| IPAMuminusMichel | ap 5000 | 1.622 | 4 | 1250 | 6.49 |
| MuCap1809keVCalo | ar 1000 | 1.141 | 5 | 200 | 5.70 |
| NoPrimary | af 2000 | 0.844 | 5 | 400 | 4.22 |
| PbarResampling | ar 5000 | 0.762 | 8 | 625 | 6.09 |
| RMCPhaseSpace0NExternal | at 7000 | 0.385 | 14 | 500 | 5.40 |
| RMCPhaseSpace0NInternal | at 500 | 1.402 | 4 | 125 | 5.61 |
| RMCPhaseSpace1NExternal | at 7000 | 0.229 | 20 | 350 | 4.59 |
| RMCPhaseSpace1NInternal | at 500 | 0.583 | 10 | 50 | 5.83 |
| RPCExternalPhysical | ap 5000 | 0.054 | 100 | 50 | 5.36 |
| RPCInternalPhysical | ap 5000 | 0.223 | 20 | 250 | 4.45 |
| ensembleMDS3c | af 4960 | 0.509 | 10 | 496 | 5.09 |

Total 7,721 jobs. Runtime at these factors (merge-1 means scaled by the
factor) spans 0.2–3.0 h, longest being `RPCExternalPhysical` ~3.0 h and
`FlatGammaCalo` ~2.2 h — all far inside the lease.

## Where it is impossible

**`ensembleMDS3c` cannot have a round job count.** Its input is 4960 files
= 2^5 · 5 · 31, so `njobs = 4960/merge` always retains the factor 31 unless
`merge` is a multiple of 31 — and merge 31 gives 160 jobs at 15.8 GB. 496
jobs at 5.09 GB is the best available, and it is at least exact.

**`IPAMuminusMichel` cannot have both round and ~5 GB.** The ideal merge is
3.08 and 3 does not divide 5000. The choice is 1250 jobs @ 6.49 GB (merge
4, current) or 2500 @ 3.24 GB (merge 2). Merge 3 gives the best size
(4.87 GB) but is neither exact nor round.

**`PbarResampling` is a near-tie.** merge 8 → 625 jobs @ 6.09 GB vs merge 5
→ 1000 jobs @ 3.81 GB; |dev| from 5 GB is 1.09 vs 1.19. Currently 8.

## Open concern — pileup diversity at very high merge

`RPCExternalPhysical` at merge 100 runs only **50 jobs**, so it draws from
roughly 50 of the 1000 `NeutralsFlashCat` pileup files. This is inherent to
a 0.054 GB/file input under a 5 GB target — the alternative is many more,
much smaller output files. Flagged for the sample owners; not resolved
here.

## Related

- [[2026-07-02-jobdef-arithmetic-and-tbs-njobs]] — where the ceil-div lives
- [[input-data-max-nfiles]]
