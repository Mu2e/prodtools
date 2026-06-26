---
title: Overview
tags: [overview, synthesis]
sources: [2026-04-21-pbi-sequence-implementation, 2026-05-19-run1bak-resampler-additions, 2026-05-22-mdc2025ap-rpcexternal-chain, 2026-05-23-mdc2025ap-pbarstgun-chain, 2026-06-07-run1ban-mustop-rebuild-chain]
updated: 2026-06-07
---

# Mu2e prodtools — Operational Overview

> Evolving synthesis of everything in the wiki. Updated by `/wiki-ingest` when sources shift the understanding.

## Current Understanding

The wiki now spans three layers of work:

**Core jobdef extension (2026-04 series).** The PBI sequence work
(Python port of `gen_NoPrimaryPBISequence.sh`) extended prodtools'
jobdef mechanism. The canonical shape is now `chunk_mode`:
`"input_data": {<cvmfs-path>: {"chunk_lines": N}}`, with each grid
worker `sed`-extracting its own slice at runtime — no chunk-file
staging, full N-way parallelism. Pattern: when a workflow doesn't
fit existing abstractions, extend them rather than bypass — keeps
`runmu2e` + `prod_utils.{run,push_data,push_logs}` as the single
point of maintenance.

**Submission-path refactor (2026-04-29 / 04-30).** Phased plan to
drop POMS from the production submit loop: Phase 1 keeps
`mu2ejobsub` and adds `submit_map` + local SQLite state; Phase 2
replaces `mu2ejobsub` with direct `jobsub_submit` via
`utils/runjob.py` + `utils/jobsub_argv.py`. Driving win: per-job
pushOutput SAM registration on the worker.

**Campaign chain ingests (2026-05 / 2026-06 series).** Four new
staging-config chains pushed to (or staged for) production:
- Run1Bak field-off resampler additions (NeutralsFlash, MuBeamFlash,
  EleBeamFlash, MuStopPileup; geom `v40` + `bfgeom_DSOff`, run 1470)
- MDC2025ap RPCExternal + RPCInternal (consume
  `sim.mu2e.PiMinusFilter.MDC2025ac.art`, fill the MDC2025 gap vs
  MDC2020aw)
- MDC2025ap PbarSTGun chain (stage 0 `PbarSTGunStops` pushed; stage 1
  `PbarResampling` drafted, blocked on grid completion — 2025 rename
  of the MDC2020ar chain: `stoppedSimpleAntiprotons` →
  `PbarSTGunStops`, `PbarSTGun` → `PbarResampling`)
- Run1Ban self-contained `MuminusStopsCat.Run1Ban` rebuild via
  `MuBeamResampler@Run1Ban-001` `TargetStops` side output → artcat →
  `MuonStopSelector`; opposite of Run1Bak (which reuses upstream
  Run1Baa stops). Key insight: `MuBeamResampler.fcl` emits five
  outputs in one job, so no separate TargetStops producer entry is
  needed.

A reference rule fell out of the RPC/Pbar work:
[[reference-rpc-primary-inherits-bfgeom]] — don't repeat
`bfgeom_no_tsu_ps_v01.txt` as `fcl_overrides` since the include
chain via `StopParticle.fcl:41` already sets it.

## Open Questions

- **Scale test of `chunk_mode` at N ≫ 52 jobs** (first production
  run used chunk_lines=1000 on a ~52,000-line source). Does sed on
  the cvmfs source behave well when many workers hit it
  concurrently? Open since 2026-04-22.
- **Pbar stage-1 readiness.** `PbarResampling.MDC2025ap` push is
  blocked on 200 stage-0 grid jobs landing
  `sim.mu2e.PbarSTGunStops.MDC2025ap.art` in SAM. Resume sequence
  documented in [[2026-05-23-mdc2025ap-pbarstgun-chain]].
- **Phase 2 cutover criteria.** No formal go/no-go for switching
  production from `mu2ejobsub` to direct `jobsub_submit`.

## Key Campaigns / Incidents / Decisions

- [[run1bak-campaign]] — first per-campaign page (Run1B family);
  records additive-bump pattern, +10 run-number cadence, and DS-off
  geometry pairing.
- [[run1ban-campaign]] — sister field-off Run1B campaign on
  `Run1Ban` musing; rebuilds MuminusStopsCat self-contained from its
  own resampler `TargetStops` side output. Demonstrates the
  "one MuBeamResampler job, five output streams" idiom.
- [[2026-05-22-mdc2025ap-rpcexternal-chain]] — RPCExternal +
  RPCInternal at MDC2025ap; both pushed to map MDC2025-026.
- [[2026-05-23-mdc2025ap-pbarstgun-chain]] — Pbar chain remake;
  stage 0 pushed, stage 1 blocked on grid.
- [[2026-04-21-extend-jobdef-per-index-overrides]] — first
  substantive extension to prodtools' core jobdef mechanism.
- [[2026-04-30-phase2-direct-jobsub-implementation]] — concrete
  POMS-decoupling implementation plan.
- [[pbi-sequence-workflow]] — PBI text-to-art pipeline, now
  jobdef-native, canonical shape is chunk_mode.
- [[input-data-chunk-mode]] — the canonical shape for parallel
  consumption of large cvmfs-resident text files.
