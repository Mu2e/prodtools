---
title: stageEff — chain efficiency from art files or SAM
tags: [reference, tooling, mcp, normalization]
sources: []
updated: 2026-09-20
---

# stageEff — chain efficiency from art files or SAM

`bin/stageEff` (`utils/stage_eff.py` + `utils/stage_eff_reader.py`) and the
read-only MCP tool `stage_efficiency` report, per simulation stage, events
generated, events kept and their ratio, and the product along the chain
(e.g. POT -> MuBeam -> stops = stops per POT). See [[prodtools-mcp-server]].

## Why it exists

Request (Michael MacKenzie, Slack, 2026-09-18): a muon stopping rate
evaluation needs the upstream MuBeam efficiency, and for local scans (e.g.
optimizing the TS1 collimator) registering every test dataset in SAM and the
conditions DB is not acceptable. The existing sources fall short:

- `genFilterEff` / `SimEfficiencies2` are per stage, keyed by bare
  description (no dsconf), need registered datasets, and include any
  prescale. Run1Ban `MuminusStopsCat` 3.96e-5 contains the 1/1000
  `TargetStopPrescaleFilter`; without it the stop fraction is 0.0396.
- Offline has `GenEventCount` (N generated, per subrun) and
  `PrescaleFilterFraction`, but nothing carries them through a resampling
  stage.

## Decision: read the files, do not propagate through the mixer

The alternative considered was forwarding the upstream counts through
`Mu2eProductMixer` with a SubRun mix op (the cosmic livetime path does this
for one subrun). A probe `MixFilter` against art v3_15_00 (SimJob MDC2025aw,
2026-09-18) measured why that is wrong:

1. A SubRun mix op fires once per PRIMARY EVENT, not once per upstream
   subrun (660/660 calls, one product each). Summing overcounts by the
   number of events drawn.
2. `GenEventCount` carries no subrun key to dedupe on.
3. With `readMode: sequential`, `wrapFiles: true` and more than one input
   file, the `EventIDSequence` given to `processEventIDs` is stale: a file
   with 21 events reported 14 IDs of the first file and then its own last 7.
   `ResamplingMixer` stores that sequence (`writeEventIDs: true`). Observed
   on the probe only; not reproduced with the real ResamplingMixer, not
   reported to art.
4. A subrun that kept zero events never reaches the mixer. With one such
   stage-1 file (50 generated, 0 kept) next to three others, the
   mixer-visible efficiency was 63/200 = 0.315 against a true 63/250 = 0.252,
   and on wrapping past the empty file art threw `NO_SUBRUN`. Production
   resamplers declare no SubRun mix op (cosmic livetime excepted), so they
   are not exposed today. Sparse stages (`MuplusStopsCat`: 75 of 2e9) are
   mostly empty subruns.

Reading the SubRuns tree has none of these problems: every subrun is there,
including the empty ones, and no event loop is needed.

## How it works

- File stage: `stage_eff_reader.py` (pyROOT, needs the Mu2e dictionaries)
  takes `Events.GetEntries()` as the kept count and sums, over the SubRuns
  tree, the single `mu2e::GenEventCount_*` product and every
  `mu2e::PrescaleFilterFraction_*`. Works on files not declared in SAM.
- Dataset stage: sums `dh.gencount` and `event_count` from SAM (the same
  fields as `genFilterEff`), but a file without `dh.gencount` is an error
  rather than a skipped file. SAM has no prescale data.
- The ops environment has no ROOT, so from there (and from the MCP server)
  the reader runs in a clean-environment `muse setup SimJob [musing]`
  subprocess; in a SimJob shell it runs directly.
- A subrun holds the prescales of EVERY path of the job and the file does
  not say which one fed this output, so a prescale is undone only when named
  (`--prescale STAGE:LABEL`, MCP `prescales={"2": "..."}`).
- The same subrun in two different files is an error: it means the same
  events twice (a Cat file listed next to its inputs). An early version
  deduped silently and returned 0.504 for a true 0.252.

## Verification (2026-09-20)

- Undeclared local files including one with zero kept events: 250
  generated, 63 kept — exact.
- `sim.mu2e.MuminusStopsCat.Run1Ban.art` read over xrootd (2 files, 5000
  subruns, 6.6 s): 2000000000 / 79203, identical to SAM and to
  `SimEfficiencies2_Run1Ban.txt`.
- Chain `sim.mu2e.MuBeamCat.Run1Bai.art` (SAM) x those files, from an ops
  shell through the muse subprocess (23 s): chain 5.066e-07, 5.069e-04 with
  `TargetStopPrescaleFilter` undone.

## Not in this version

Discovering the upstream files from the fcl stored in a stage-2 file;
stamping the result into new stage-2 files at `json2jobdef` time;
cross-checking against the `SimEfficiencies2` row.
