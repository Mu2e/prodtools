---
title: Digitizing a beam-flash dts file (muon decay-in-flight study, DS at 50%)
tags: [run, digitize, onspill, pileup, resampler, field-map, offline-bug, run1a]
sources: []
updated: 2026-10-21
---

# Digitizing a beam-flash dts file

## Context
A collaborator built a muon decay-in-flight (DIF) study from the Production
`pileup/MuBeamResampler.fcl` (SimJob MDC2025ay). The changes:

- only `flashPath` is kept;
- the geometry is `geom_run1_a.txt` with the stopping target set to `DSVacuum`;
- the field is `bfgeom_DS50_no_tsu_ps_v01.txt`, so the DS is at 50%;
- every muon is forced to decay within 0–150 ns of proper time, using
  `g4run.physics.muon{Min,Max}PreAssignedDecayProperTime`.

The input is one `sim.mu2e.MuBeamCat.Run1Bai` file: 127,782 events for 1e7 POT.
That file holds charged particles entering TS3Vacuum, made with
`geom_run1_b_v06` and DS off. Upstream of TS3, v06 and run1_a are the same
geometry. The goal is to take this through `digitize/OnSpill.fcl`, then reco,
then the grid.

Test files: `/exp/mu2e/data/users/oksuzian/claude-scratch/jmott_dif/`
(`resample_early.fcl`, `digi_onspill.fcl`, `digi_all_early.fcl`,
`dts_peek.py`, `dig_peek.py`).

## What breaks, in order
Each item was hit in a local run on 2026-10-21.

1. **Run number.** An `EmptyEvent` source with no `firstRun` writes run 1.
   Digitize with `Sim_best` v1_4 then fails with `DbEngine::update failed to
   find tid N for run:subrun 1:0`. Turning off three `useDb` flags is not
   enough, because more tables follow. Set `source.firstRun` (1460 works).
   Grid jobs get their run number from the jobdef.
2. **`bfgeom_DS50_reco_v01.txt` cannot load.** Line 18 is
   `#include "Mu2eG4/geom/bfgeom_reco_v01.txt"`, which lacks the `Offline/`
   prefix. That fails with `Can't find file "Mu2eG4/geom/bfgeom_reco_v01.txt"`.
   This is true in Offline v13_39_00 and on GitHub main as of 2026-10-21.
   The fix is a local copy that includes `Offline/Mu2eG4/geom/bfgeom_reco_v01.txt`
   and sets `bfield.innerMaps = { "BFieldMaps/Mau13/DSMap50.header" }`.
   The `_no_tsu_ps` variant is correct.
3. **No `PrimaryParticle`.** `compressDigiMCs` needs `FindMCPrimary`, and flash
   jobs never make it. Add it to the resampler's `flashPath`:
   `{ module_type: FindMCPrimary SimParticles: "compressDetStepMCs" PrimaryGenIds: ["primaryProtonGun"] }`,
   and keep `*_FindMCPrimary_*_*` in the output. Without a process or gen id
   it throws "Neither process or gen codes specified".
4. **No VD, surface-step or trajectory products.** Flash `compressDetStepMCs`
   has `stepPointMCTags: []`, and `DtsKeptProducts` drops StepPointMCs. Do not
   rebuild those products in the resampler. Adding `MakeSS`, the target and
   absorber SDs, and standard compression still ended in
   `_simPtrRemap key ... not found` in `CompressDetStepMCs`.
   What works is to blank the tags in digitize:
   `compressDigiMCs.extraStepPointMCTags: []`, `surfaceStepTags: []`,
   `mcTrajectoryTag: ""`. The VD steps themselves can be kept after all, from
   `FlashFilter:virtualdetector`; see "Reco and ntuple" below. The final
   workflow keeps them and blanks only the surface-step and trajectory tags.

## Physics findings (2000 resampled events, no 250 ns cut)
- 44 events have detector steps, which is 2.2%. That takes 113 s, or 0.057 s
  per event, and 2.1 GB RSS.
- Tracker steps from muon-decay electrons fall at 125–550 ns, with 10/50/90%
  points of 169/240/470 ns. Only 14% are at 450 ns or later. Electron start
  momentum, 10/50/90%: 24/43/55 MeV/c.
- The flash `DetStepFilter` keeps only events with hits after 250 ns
  (`TimeCutConfig.MinimumTime 250`). For DIF, swap it for
  `@local::Pileup.filters.EarlyDetStepFilter`.
- Stock `OnSpill.fcl` writes **0 of 44** events. No physics trigger fires, and
  `TriggerableLH` needs a particle of 69–300 MeV/c with 15 digis.
- With `outputs.Output.SelectEvents: []` and `digitizationStart: 100`
  (strawElectronics, CaloShowerROMaker, CaloDigiMaker), all 44 are written.
  Ten of them have 15 or more straw digis. The mean is 21 straw digis and
  4.9 calo digis per event.
- The forced decay stores no weight. Each muon's weight is
  1 − exp(−150/2197) = 0.066. Proper time restarts at TS3, which is valid
  because the exponential is memoryless.

## Reco and ntuple
- `recoMC/OnSpill.fcl` runs once `compressRecoMCs.mcTrajectoryTag` is set to
  `""`, but it finds 0 tracks. The cause is the straw-hit windows:
  `makeSH.MinimumTime` 400 and `makePH.MinimumTime` 410. Set to 100, they give
  5 downstream-electron fits in 44 events, at 51–56 MeV/c with t0 236–314 ns.
- Virtual-detector steps can be kept. Set
  `compressDetStepMCs.stepPointMCTags: ["FlashFilter:virtualdetector"]` and keep
  `StepPointMCs_compressDetStepMCs`. Using `g4run:virtualdetector` throws
  `_simPtrRemap key ... not found`, because the flash steps point at
  `FlashFilter`'s particles (`MuBeamResampler.fcl` sets
  `compressDetStepMCs.simParticleTags: ["FlashFilter"]`).
- EventNtuple runs with `from_mcs-primary_addVDSteps.fcl`.
- `AnalysisMDC2025/v02_02_04` is backed by SimJob MDC2025ay, so one code
  tarball backed on it covers all four stages.

## prodtools workflow (2026-10-21)
The work dir is `/exp/mu2e/app/users/oksuzian/dif_musing`. It holds the DIF
repo (four fcls, the geometry and the fixed field file), `dif.json` (entries
`DIFFlash`, `DIFDigi`, `DIFReco` and `DIFNtuple`, dsconf MDC2025ay, owner
jmott in the `input_data` names) and a README with the CLI submission steps.
The code tarball is `/exp/mu2e/data/users/oksuzian/code_tarballs/dif/v1/Code.tar.bz2`,
sha256 26004a69…db48b.

The smoke used prodtools v3.3.4 from cvmfs, running `json2jobdef` and then
`runlocal --code`. Stages 2–4 used `dir:` inputs. The chain went from 2000
draws to 39 dts events, then 39 dig, then 10 mcs, then an nts. Nothing was
pushed or submitted.

Stage 1 index 0 failed with `[3010] File not online. Staging not allowed.`:
the xrootd door does not stage from tape. `MuBeamCat.Run1Bai` exists on tape
only (its persistent-disk directories are empty), and 8 of its 200 files were
NEARLINE. Prestage before every grid run.

The grid needs the code-tarball pattern of
[[2026-10-21-fcl-only-code-tarball-ts30]]. A `.` in `MU2E_SEARCH_PATH` does
not reach the worker.

## EventNtuple fit selection (2026-10-25)
The default EventNtuple `trk` branch merges all four fitters (`MergeKKAll`).
`trk.pdg` is the fitted charge, so `trk.pdg == 11` is not "KKDe". On 544
events it gave 226 fits: the 221 negatively charged KKDe fits plus 5 from
other fitters, and it missed the 2 positively charged KKDe fits. Gallery
reading `KKDe` directly counts 223.

`NtupleDIF.fcl` now writes separate `de`/`ue`/`dm`/`um` branches
(`trk.fits: [De, Ue, Dmu, Umu]`, `EventNtuple.PathSeparate`). The code tarball
is `code_tarballs/dif/v2`, sha256 09575fc5…6cadfe. `demcsim` truth shows all 223
KKDe fits are electrons from a decay with a muon mother.

The analysis note is in `oksuzian/AnalysisNotes`, `dif-ds50-2026-10`. Gallery can
read KalSeeds from Python only through small C++ helpers, because PyROOT cannot
use the `auto`-typed `hits()` and `intersections()` accessors.
