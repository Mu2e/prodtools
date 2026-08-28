---
title: Campaign 94 died twice — resolver fallback found the file on stash but rendered it as a root:// URL
tags: [incident, file-resolver, inloc, stash, resilient, Run1Baw, MuCap1809keV, campaign-94]
sources: []
updated: 2026-08-27
---

# inloc fallback rendered with the declared protocol

## Symptom
Campaign 94 (`cnf.mu2e.MuCap1809keV.Run1Baw.0.tar`, 1000 jobs, `inloc: disk`)
lost every job twice (clusters 29687495 and 71737708): all exit 1, ~115 s
wallclock, no log dataset in SAM — died before `art` started. Input was
`sim.mu2e.MuminusStopsCat.Run1Baa.art` (2 files, 784 MB), present on stash
(CVMFS) and tape, absent from disk/scratch/resilient.

## Root cause
`FileResolver` in `utils/file_resolver.py` does two things with `inloc`:

1. **Lookup** — probes the declared area, then `_FALLBACK_ORDER`, and
   announces the substitution. This worked: `Warning: ... is not on 'disk';
   reading it from 'stash'`.
2. **URL rendering** — `url()` branched on the *declared* `self.inloc`,
   never on the resolved area. `proto_for_inloc('disk')` is `root`, the
   stash path is `/cvmfs/...`, and the `/pnfs/` check raised:

```
ValueError: Error: root protocol requested but a file pathname does not start with /pnfs:
  /cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash/datasets/sim/mu2e/MuminusStopsCat/Run1Baa/art/...
```

The `if self.inloc == 'stash': return path` escape existed but only fired
when stash was *declared*. Any `/pnfs`-family declaration that fell back to
stash hit this.

Reproduced locally with `runlocal --inloc disk --nevts 20` — the grid
signature (setup-phase exit 1, no log) does not say which step failed.

## Fix
- `be9c027` — `url()` keys on `_dataset_location()`'s result: stash → plain
  CVMFS path; resilient → always xrootd (no CVMFS mirror); other areas
  honour `proto`. `dir:` gets its own branch. Five regression tests in
  `TestStashMiss`.
- `2e4074f` — `_FALLBACK_ORDER` changed to
  `(disk, resilient, tape, stash, scratch)`: stash is rarely used, so it
  moves behind tape; resilient stays ahead of tape (staged pileup Cats);
  scratch last (evictable). A dataset that must be read from CVMFS declares
  `inloc: stash`, which is always probed first.
- Because that order would have routed campaign 94's input to tape, the
  three Run1Baa resampler inputs (`MuminusStopsCat`, `MuBeamCat`,
  `EleBeamCat`) were copied tape → resilient as mu2epro
  (`copy_to_stash --dest resilient --source tape`), size-verified, and all
  consuming entries in `primary_muon.json` / `resampler_beam.json` now
  declare `inloc: resilient`. Row 362 got
  `set-entry 94 inloc resilient --include-open-rows`.

## Outcome
Recovery pass re-fired all 1000 indices as row 367, cluster 29688921
(2026-08-27 22:38 UTC). 1000/1000 produced by 2026-08-28 00:15 UTC. 1000
resampler jobs on 2 resilient files raised no `FileOpenError` — a
`sequential_aux` resampler reads its slice once, unlike a mixer cycling a
Cat.

## Lessons
- **Two misdiagnoses before the real one.** `samweb locate-file` showing
  `(nearline)` is a location record, not residency (`mdh query-dcache -o`
  said `ONLINE_AND_NEARLINE`); and "inloc names the wrong place" was wrong
  because the fallback *does* find the file. Reproduce locally before
  naming a cause — the grid exit code says nothing.
- `--campaign ID` on `submissions run` narrows only the top-up phase; the
  recovery pass walks every open row and re-fired the broken entry once
  while the fix was still being discussed.
- `inloc` is a declaration. Make it name the copy you want read; whole-
  campaign inputs belong on resilient.
- `bin/copy_to_stash` and `bin/listNewDatasets` are Python (`python3`);
  `bin/json2jobdef`, `bin/runlocal` are bash wrappers.

Related: [[2026-08-24-cosmic-livetime-integer-truncation]] (same round).
