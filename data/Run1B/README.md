# Run1B campaign entries — geometry status

Run1B is Run1A plus a two-piece aluminium assembly at the TS5 face. Only
`geom_run1_b_v40.txt` models it correctly: it inherits `geom_run1_a.txt`
untouched and adds a TSdA plate plus the repurposed pion degrader as a mobile
target. The earlier `v01`–`v06` geometries instead *replaced* the 37-foil
stopping target with a single 600 mm disk and moved the tracker and calorimeter
to extracted positions.

As of Offline `main` @ `38f6943d5` (PRs #1923 and #1927, merged 2026-08-12),
`geom_run1_b_v40.txt` and `geom_run1_b_ds_on_v40.txt` are on main. Entries using
them are runnable against main:

| campaign | stages |
|---|---|
| Run1Bak | `resampler_beam` (NeutralsFlash, MuBeamFlash, EleBeamFlash, MuStopPileup) |
| Run1Ban, Run1Ban-001 | `digi`, `mix`, `primary_muon`, `resampler_beam_mixing`, `resampler_stm` |
| Run1Bap | `primary_muon` (PolyFlatGamma), `resampler_beam` (pion/RPC chain) |

## FROZEN — do not re-run against Offline main

| campaign | geometry | stages |
|---|---|---|
| Run1Baa, Run1Baa1, Run1Bab, Run1Bab2, Run1Baf | v01 | `primary_muon`, `resampler_beam`, `digi`, `mix`, `reco` |
| Run1Bag, Run1Bah | v03 | `primary_muon`, `stage1`, `resampler_beam`, `digi`, `mix`, `reco` |
| Run1Bai, Run1Bai-001, -002, -003, -007 | v06 | `primary_muon`, `stage1`, `resampler_beam`, `resampler_beam_mixing`, `mix`, `reco` |

**These do not fail loudly against main — they fail silently.**

- `v03` and `v06` are not on main at all. Those entries abort with a missing
  geometry file, which is the *safe* case.
- `v01` **is** on main, added by `2032d440a` for `EventDisplayRun1b.fcl`. It
  resolves, so the job starts. But main carries neither the code the file needs
  nor the file's own full contents:
  - main's `v01` sets `tracker.inDS2Vacuum = true` and
    `calorimeter.inDS2Vacuum = true`. **No code on main reads `inDS2Vacuum`** —
    it lives in `Mu2eWorld.cc` and `constructVirtualDetectors.cc` on the Run1B
    branch only. SimpleConfig does not warn about keys nobody reads, so the
    tracker and calorimeter stay in DS3Vacuum and the stopping-target mother is
    undersized.
  - main's `v01` is also not the file these campaigns ran. The branch version
    additionally sets `mu2e.detectorSystemZ0 = 7000` and the EMC source virtual
    detector positions (`zEMCSourceInMu2e`, `zEMCSource2InMu2e`, `zEMC0Front`),
    read by `VirtualDetectorMaker.cc` on the branch.

  The result is a job that runs to completion and produces a wrong world.

## How to reproduce a frozen campaign

**Use the campaign's own `simjob_setup`, not Offline main and not the archive
tag.** Every frozen entry pins an immutable CVMFS Musing, and each Musing's
`backing/Offline/Mu2eG4/geom/` carries exactly the geometries that campaign
needs (verified 2026-08-10: Run1Bab ships v01; Run1Baf v01–v02; Run1Bah v01–v03;
Run1Bai v01–v06). Reproducibility never depended on the `Run1B` branch.

The Offline tag `run1b-archive-2026-08-08` is the source-code record of the
retired branch — the only remaining copy of `v02`–`v06` and of the un-ported
`Mu2eWorld.cc` / `constructVirtualDetectors.cc` changes. Read it to understand
what a frozen campaign did; run the Musing to reproduce it.

See `docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md` for the
consolidation rationale.
