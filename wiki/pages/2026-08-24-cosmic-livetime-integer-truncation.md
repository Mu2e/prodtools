---
title: Cosmic livetime zero/truncated in resampled dts (Mu2eProductMixer integer division)
tags: [incident, cosmics, livetime, Run1Ban, Run1Bah, MDC2020, EventMixing]
sources: []
updated: 2026-08-24
---

# Cosmic livetime zero/truncated in resampled dts

## Symptom
`mu2e -c Offline/Print/fcl/printCosmicLivetime.fcl -s dts.mu2e.CosmicCRYAll.Run1Ban.*.art`
prints, for every subrun:

```
N Primaries:0 ... Livetime:0.000000
Processed 1 Subruns for a total of 0 primaries and 0 seconds total livetime
```

## Root cause
`Offline/EventMixing/src/Mu2eProductMixer.cc:239` (`endSubRun`):

```cpp
float scaling = resampledEvents_ / generatedEvents_;
```

`resampledEvents_` and `generatedEvents_` are both `unsigned int`
(`Mu2eProductMixer.hh:258-259`), so the division is integer division and
truncates before the float assignment. The stored mixed product is
`CosmicLivetime(totalPrimaries_*scaling, ..., livetime_*scaling)` with
instance `mixed` — the ONLY livetime product S2Resampler keeps
(`Cosmic.S2KeptProducts` keeps `mu2e::CosmicLivetime_*_mixed_*`).

## Arithmetic (measured 2026-08-24)
S1 input `sim.mu2e.CosmicDSStopsCRYAll.MDC2025ab`: GenEventCount =
128,783 events/subrun; S1 `CosmicLivetime_generate__Primary` = 29.586 s
per subrun (primaries=1 by design: CRY computes livetime internally,
primaries field is a dummy — `CRYEventGenerator_module.cc endSubRun`).

| dataset | events/job | scaling true | scaling stored | livetime/subrun stored (true) |
|---|---|---|---|---|
| dts.mu2e.CosmicCRYAll.Run1Ban | 100,000 | 0.776 | **0** | **0 s** (22.97 s) |
| dts.mu2e.CosmicCRYAll.Run1Bah | 500,000 | 3.882 | 3 | 88.76 s (114.9 s, -23%) |
| dts.mu2e.CosmicCRYSignalAll.MDC2020ar | — | — | 11 | 130.2 s (floor-biased) |
| `dts.mu2e.CosmicCRYAll.MDC2020ar` | 500,000 | 11.82 | 11 | 130.3 s (140 s, -6.9%) |

Rule: any S2 cosmic resampling with events/job < S1 generated-per-subrun
gives exactly zero; otherwise livetime is understated by up to
1/scaling (floor bias).

## Scope
- Bug introduced 2021-10-27 (Offline commit `c30db7ecd`, "fixing
  generated events count"); still present on Mu2e/Offline `main`
  (checked 2026-08-24). Affects ALL cosmic resampled dts made since,
  and everything downstream (digi/reco livetime normalization sums
  dts/mcs subrun livetimes).
- nts never carried livetime anyway (see memory
  `reference_eventntuple_no_cosmic_livetime`), so analyses normalize
  from dts/mcs subrun sums — those sums are zero for Run1Ban cosmics
  and ~23% low for Run1Bah.

## Fix
```cpp
float scaling = static_cast<float>(resampledEvents_) / static_cast<float>(generatedEvents_);
```
Draft PR: [Mu2e/Offline #1940](https://github.com/Mu2e/Offline/pull/1940) (opened 2026-08-24, branch `oksuzian:fix-cosmic-livetime-scaling`). existing datasets can be corrected offline since
true scaling is recomputable: `events_per_job / genCount(S1 subrun)` ×
S1 livetime (all recoverable from S1 files + cnf).

## Repro notes
- Env from a non-login shell: `unset MU2E` first (setupmu2e-art.sh
  early-returns if $MU2E set, leaving `muse` undefined), then
  `source setupmu2e-art.sh; source Musings/SimJob/Run1Ban/setup.sh; getToken`.
- Probe logs: /exp/mu2e/data/users/oksuzian/claude-scratch/probes/livetime0/
