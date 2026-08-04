---
title: NoPrimary.Run1Ban-001 remake — 100× statistics (10B empty frames)
tags: [run, decision, run1ban, noprimary, mixing, poms, primary]
sources: []
updated: 2026-07-11
---

# NoPrimary.Run1Ban-001 remake (pushed 2026-07-11)

**Request:** "remake `dts.mu2e.NoPrimary.Run1Ban.art` with 50× more
events." Executed as **200,000 ev/job × 50,000 jobs = 10B events**
(user's explicit numbers → actually **100×** the prior 100M, not 50× —
flagged and confirmed). `dts.mu2e.NoPrimary.Run1Ban-001.art`, ~1 TB on
disk, 50,000 files.

## Why a new name (`-001`), not an in-place remake

The old `dts.mu2e.NoPrimary.Run1Ban.art` is a **complete 100M-event
production dataset** (20,000 files) consumed live by `mix.json`. Two
constraints forced a new dataset rather than a same-name remake:

1. **Name collision.** New files would reuse sequencers → SAM conflict.
   Same-name remake needs a destructive retire (mu2epro) first.
2. **Granularity change.** 200k ev/file ≠ the old 5k ev/file, so the new
   build cannot append to the old dataset (mixed granularity is bad).

Chosen: **option B (new name)** — non-destructive, old 100M sample stays
available. `data/Run1B/primary_muon.json` NoPrimary entry `dsconf`
`Run1Ban` → `Run1Ban-001` (joins the CeEndpoint/FlateMinus/FlatGamma
Run1Ban-001 resampler primaries under that dsconf — so `json2jobdef`
**requires `--desc NoPrimary`** to isolate it; `--dsconf Run1Ban-001`
alone matches four entries).

`NoPrimary.fcl` is `source: EmptyEvent` + NullMCPrimary — pure empty
pileup frames, **no G4**, ~100 bytes/event, negligible CPU. Scaling is
free compute; the only costs are disk (~1 TB) and 50k SAM files.

## Mixing granularity consequence (mix.json)

`mix.json` consumed `NoPrimary.Run1Ban` at **merge 1** (v1_4) and
**merge 10** (v1_5). Mixing reads whole NoPrimary files, so a mixing job
= (ev/file) × merge. At the new 200k ev/file:
- merge 10 → **2M ev/mixing-job** (unrunnable) → dropped to merge 1.
- Even **merge 1 = 200k ev/mixing-job** = 4× the validated 50k-ev
  NoPrimaryMix job ([[reference_mixing_merge_factor_10_validated]] analog).
  Runnable but longer — **smoke a mixing job before any remix**. You
  cannot go below merge 1, so 200k ev/file structurally forces bigger
  mixing jobs; smaller NoPrimary files would be needed to restore 50k.

Both `mix.json` NoPrimary entries repointed to `Run1Ban-001`, both merge 1.

## Production artifacts (verified 2026-07-11, RC=0)

- **cnf** `cnf.mu2e.NoPrimary.Run1Ban-001.0.tar` → declared in SAM,
  `dcache:/pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/NoPrimary/Run1Ban-001/tar/32/1d`.
  Self-describing tarball (`tbs.njobs=0`; map authoritative).
  Verified with `maxEvents 200000`, `firstRun 1470`, `baseSeed 1+index`,
  1-event `mu2e -c` status 0.
- **POMS map** `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-033.json`
  — **new map** (032 was at 80,599 jobs; +50,000 > 100k/map cap →
  allocate 033 per the extend-until-100k rule). Single entry: njobs
  50000, inloc disk, `*.art → disk`.
- **idx def** `iMDC2025-033` (`dh.sequencer < 0050000`).

Pushed via `/mu2epro-run json2jobdef --prod`; pre-existing mu2epro token
used as-is (never refreshed).

## Remaining step

A **new** POMS map number needs a campaign stage pointed at
`MDC2025-033.json` / `iMDC2025-033` (production-manager / web-UI action)
to actually dispatch the 50,000 jobs → `dts.mu2e.NoPrimary.Run1Ban-001.art`.
Creating the map + idx does not auto-launch a new campaign number.

## Related

- [[2026-07-10-firstjob-index-windows]] — the *other* expansion lever
  (append jobs to the same dataset via cnf index window); not used here
  because the granularity change mandated a fresh dataset.
- [[poms-reference]] — map/idx/campaign_stage wiring.
