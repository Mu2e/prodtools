---
title: Empty calo branches in 1809 keV ntuples — AnalysisMDC2025 v02_00_00 fills calo hits only through clusters
tags: [incident, eventntuple, calorimeter, ntuple, analysis-musing, mdc2025au]
sources: []
updated: 2026-09-02
---

# 2026-09-02 empty calo branches: v02_00_00 gates calo hits behind clusters

Paolo Girotti reported that both official 1.8 MeV ntuples were empty of calo
data:

```
nts.mu2e.MuCap1809keVCaloOnSpill.MDC2025ar_best_v1_1.root
nts.mu2e.MuCap1809keVCaloOnSpill.MDC2025au_best_v1_5.root
```

and, having regenerated a good ntuple himself from the same `mcs` list with a
slightly modified fcl, suspected "some grid or configuration error" in the
production jobs.

It is neither. It is a defect in the EventNtuple code shipped by
`AnalysisMDC2025/v02_00_00`, the musing the ntuple campaign pinned.

## The data upstream is fine

Measured on `...MDC2025au_best_v1_5.001430_00000009`:

| stage | product | occupancy |
|---|---|---|
| dig | `CaloShowerSteps_compressDigiMCs` | 100 % (736 252 objects) |
| dig | `CaloDigis_CaloDigiMaker` | 53.2 % (211 356 objects) |
| mcs | `CaloHits_CaloHitMaker` | 16.5 % (35 875 objects) |
| mcs | `CaloHits_CaloHitFastMaker_calo` | 52.5 % (126 339 objects) |
| mcs | `CaloClusters_CaloClusterMaker` | **0 %** |
| nts | `calohits`, `caloclusters`, everything | **0 %** |

Zero clusters is correct physics: a 1.8 MeV deposit is far below the cluster
seed threshold. The calo *hits*, however, are present in the mcs and should
have reached the ntuple.

## Root cause

`AnalysisMDC2025/v02_00_00`, `EventNtuple/src/EventNtupleMaker_module.cc:888`:

```cpp
if (_fillcaloclusters) {          // line 888
    // get CaloClusters, then fill hits via cluster.caloHitsPtrVector()
} else {                          // "//No clusters"
    if (_fillcalohits) { /* loop the CaloHitCollection standalone */ }
}
```

Standalone calo-hit filling sits in the `else`, so it runs only when cluster
filling is switched **off**. The defaults set `FillCaloClusters: true` *and*
`FillCaloHits: true`, so the cluster branch is always taken, and every calo hit
that is not a member of a cluster is dropped. No exception, no warning, art
exits 0.

For a sub-threshold sample this zeroes the whole calo block, because there are
no clusters to hang the hits on.

## Reproduction and fix confirmation

Same input file, same `EventNtuple/fcl/from_mcs-mockdata.fcl`, only the musing
differs:

| musing | `calohits` occupancy |
|---|---|
| v02_00_00 | 0.000 % |
| v02_01_00 | **17.6 %** (matches the 16.5 % `CaloHits` in the mcs) |

`v02_01_00` restructures the block into independent guards
(`if(calo.fill() && calo.fillHits())`), which is the fix. Paolo's own working
run was on EventNtuple HEAD — his fcl used the *nested* key names
(`calo.fillDigis`, `trk.fill`), which only exist in the fixed version. The fcl
edits were incidental; the newer code was what made it work.

## Diagnostic fingerprint

In any ntuple made with v02_00_00, `calohits` and `caloclusters` occupancy come
out **exactly equal**, because hits exist only where a cluster does:

```
FlatGammaCaloOnSpill      calohits 94.440%   caloclusters 94.440%
CeMLeadingLogOnSpill      calohits 89.881%   caloclusters 89.881%
RPCInternalPhysicalOnSpill calohits 76.113%  caloclusters 76.113%
CosmicCRYAllOnSpill       calohits 82.073%   caloclusters 82.073%
```

That equality is the tell. Those four are usable for cluster-level work but
have silently lost every non-clustered calo hit.

## Why mixing masked it

`Mix1BB` variants read ~98 % populated, because 1BB pileup supplies plenty of
cluster-seeding energy; the cluster branch then works and drags the hits in
with it. Every **unmixed** 1809 sample reads 0 %:

```
nts.mu2e.MuCap1809keVCaloMix1BB.MDC2025ar_best_v1_1   calohits 97.865%
nts.mu2e.MuCap1809keVCaloMix1BB.MDC2025au_best_v1_1   calohits 97.918%
nts.mu2e.MuCap1809keVCaloOnSpill.MDC2025au_best_v1_5  calohits  0.000%
nts.mu2e.MuCap1809keVCalo-KL.Run1B-005                calohits  0.000%
```

A healthy Mix1BB ntuple is therefore no evidence that the unmixed one is sound.

## Blast radius

Campaign 49 (`cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar`, 21 datasets) pinned
v02_00_00 — all of its ntuples lost non-clustered calo hits, and the 1809 one
lost the calo block entirely. The `MDC2025ar_best_v1_1` round is affected the
same way.

The Run1Baw evnt campaigns 96/97/98 pin `AnalysisMDC2025/v02_01_00` and are
**not** affected.


## Sweep: how far it reaches (2026-09-02)

The defect is not specific to v02_00_00. Every AnalysisMDC2025 release before
`v02_01_00` (2026-08-19) contains the same cluster-gated `else`:

| musing | status |
|---|---|
| v01_01_03 | buggy |
| v01_01_04 | buggy |
| v01_02_00 | buggy |
| v02_00_00 | buggy |
| **v02_01_00** | **fixed** |

A sweep of all 62 current `nts.mu2e.%.MDC2025%` datasets (one file each,
excluding Trig/Calib) found **62/62 carrying the fingerprint** — `calohits`
occupancy exactly equal to `caloclusters` occupancy — and exactly one totally
empty (`MuCap1809keVCaloOnSpill.MDC2025au_best_v1_5`; the superseded
`...MDC2025ar_best_v1_1` is empty too).

Severity is bimodal, and for most samples it is modest. Running the same input
file through both musings:

| sample | branch | v02_00_00 | v02_01_00 | lost |
|---|---|---|---|---|
| CeMLeadingLogOnSpill | calohits | 2481 | 2615 | 5.1 % |
| CeMLeadingLogOnSpill | caloclusters | 452 | 452 | 0 % |
| ensembleMDS3cOnSpill | calohits | 2654 | 2928 | 9.4 % |
| ensembleMDS3cOnSpill | caloclusters | 465 | 465 | 0 % |

So: **cluster-level quantities are untouched** — the cluster collections are
bit-identical. What is lost is 5-10 % of individual calo hits, namely those not
attached to any cluster. Only when *no* cluster forms at all does the loss go to
100 %, which is why the 1.8 MeV sample is the single catastrophic case.

Remake priority therefore is: the 1809 samples (unusable), then any hit-level
calo analysis (5-10 % biased), and not the cluster-level work (correct as is).

## Response

Campaign 99 (`cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar`, created 2026-09-02)
re-ntuples the 21 `mcs.mu2e.%OnSpill.MDC2025au_best_v1_5` datasets (4986 files)
with v02_01_00, writing `nts.mu2e.<desc>.MDC2025au_best_v1_5-001.<seq>.root`.

## Remedy

Re-ntuple the affected `mcs` with v02_01_00. The reco/digi stages are sound, so
nothing below `mcs` needs remaking — this is a cheap mcs → nts pass, not a
chain rebuild.

## Confirmed fixed in production (2026-09-02)

Campaign 99 remade the au_v1_5 OnSpill ntuples under `v02_01_00` as
`MDC2025au_best_v1_5-001`. All ten
`nts.mu2e.MuCap1809keVCaloOnSpill.MDC2025au_best_v1_5-001.root` files read
`calohits` occupancy 16.3–16.6 % (about 35 500 of 216 000 events each), matching
the 16.5 % `CaloHits_CaloHitMaker` occupancy in the mcs; `caloclusters` stays 0
as expected for a sub-threshold sample. The same files at the unsuffixed dsconf
(`v02_00_00`) still read 0 %. Paolo's issue is closed by the `-001` datasets.
