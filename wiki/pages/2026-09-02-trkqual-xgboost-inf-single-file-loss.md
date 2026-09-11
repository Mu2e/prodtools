---
title: TrkQual XGBoost inf throw — deterministic single-file loss in v02_01_00 ntuple drains
tags: [incident, eventntuple, trkqual, xgboost, analysismdc2025, campaign-99, draining]
sources: [joblog-86501262-jobsub01, condor-history-71817349-jobsub03]
updated: 2026-09-02
---

# TrkQual XGBoost inf throw — deterministic single-file loss

**Campaign 99** (`cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar`, EventNtuple
under AnalysisMDC2025 v02_01_00, draining `mcs.mu2e.%OnSpill.MDC2025au_best_v1_5.art`)
lost one input file three attempts in a row:

| row | cluster | exit | wallclock | note |
|---|---|---|---|---|
| 418 proc 100 | 71817349@jobsub03 | 1 | 1460 s | slice 1 |
| 419 | 86501262@jobsub01 | 1 | 933 s | recovery 1 |
| 421 | 71819280@jobsub03 | (pending) | | recovery 2, will fail identically |

Input: `mcs.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5.001430_00000533.art`
(13.7 GB, 65237 events — normal for that dataset, all 50 files are 13.4–13.8 GB;
ONLINE_AND_NEARLINE, so not a residency problem).

## Signature

Job log (fetched with `jobsub_fetchlog --group mu2e --jobid 86501262.0@jobsub01.fnal.gov`,
saved under `claude-scratch/joblogs/c99_533/`):

```
---- TrackQuality BEGIN
  XGDMatrixCreateFromMat failed: [22:30:44] .../xgboost-2.1.1/.../src/data/data.cc:1120:
  Check failed: valid: Input data contains `inf` or a value too large, while `missing` is not set to `inf`
  The above exception was thrown while processing module TrackQuality/TrkQualAll run: 1430 subRun: 810 event: 1152
Exception going through path EventNtuplePath
Art has completed and will exit with status 1.
[direct] mu2e failed — skipping data push, still pushing log
```

Crashed at event 31398 of 65237. `TrigReport Events total = 31398 passed = 31397 failed = 1`.
No nts, no SAM record; the log push reported status 0.

## Root cause (confirmed by local reproduction)

`ArtAnalysis/TrkDiag/src/TrackQuality_module.cc` was rewritten between
v02_00_00 (TMVA-SOFIE ANN) and v02_01_00 (ONNXRuntime ANN + XGBoost BDT).
The BDT call is

```cpp
XGDMatrixCreateFromMat(features.data(), 1, _nFeatures, NAN, &dmat)   // line 245
```

XGBoost accepts NaN as "missing" but rejects `inf`.

Running the single event locally with `physics.producers.TrkQualAll.debugLevel: 2`
shows the offending KalSeed has **14 hits, all inactive**:

```
[TrackQuality::produce::TrkQualAll] Printing track hit information
 Hit  1: active = 0, null = 0
 ...
 Hit 14: active = 0, null = 0
XGDMatrixCreateFromMat failed: ... Input data contains `inf` ...
```

With `nactive == 0` the feature vector becomes

| feature | expression | value |
|---|---|---|
| `[1]` | `nactive / nhits` | 0 / 14 = 0 |
| `[3]` | `nnullambig / nactive` | 0 / 0 = NaN (accepted as missing) |
| `[6]` | `nmatactive / nactive` | n / 0 = **inf** (rejected) |

So the cause is a KalSeed with zero active straw hits (a fit that failed
but was still stored in the collection) driving a divide-by-zero in the
material-fraction feature. The ONNX path performs no validation, which is
why v02_00_00 produced
`nts.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5.001430_00000533.root`
from the same input without complaint (garbage TrkQual for that one
track, no crash).

## Five-second reproducer

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops; unset MUSE_WORK_DIR
source /cvmfs/mu2e.opensciencegrid.org/Musings/AnalysisMDC2025/v02_01_00/setup.sh
getToken
cp /pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/evnt/MDC2025au_best_v1_5-001/tar/3a/2b/cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar .
fcldump --local-jobdef cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar \
    --fname mcs.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5.001430_00000533.art
cat >> mcs.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5.001430_00000533.fcl <<'F'
physics.producers.TrkQualAll.debugLevel: 2
source.firstRun: 1430
source.firstSubRun: 810
source.firstEvent: 1152
F
mu2e -c mcs.mu2e.CePLeadingLogOnSpill.MDC2025au_best_v1_5.001430_00000533.fcl -n 1
```

Notes: `jobfcl --source` refuses a generic cnf ("does not use ... as a
primary input file"); `fcldump --local-jobdef --fname` is the generic form.
`fcldump` needs `muse setup ops` (samweb_client) AND the Analysis musing,
in that order with `MUSE_WORK_DIR` unset between. `source.firstRun/
firstSubRun/firstEvent` seek straight to the event over xrootd; the whole
run is under 5 s on a 13.7 GB file. Work dir:
`claude-scratch/probes/c99_533/`.

## Fix drafted and verified (2026-09-02)

Branch `trkqual-nactive-zero` in `claude-scratch/probes/c99_533/muse/ArtAnalysis`
(commit on top of Mu2e/ArtAnalysis main 957c656; the cvmfs v02_01_00 copy of
the module is byte-identical to main). Two guards in `produce()`:
`nactive == 0` → both MVAResults 0 + `LogWarning`, `continue`; and any
non-finite feature → same. Issue and PR bodies in
`claude-scratch/probes/c99_533/{issue,pr}.md`. Filed 2026-09-02: issue https://github.com/Mu2e/ArtAnalysis/issues/11, PR https://github.com/Mu2e/ArtAnalysis/pull/12 (fork oksuzian/ArtAnalysis).

Verified by building ArtAnalysis against the musing as a backing build:

```bash
mkdir muse && cd muse && git clone https://github.com/Mu2e/ArtAnalysis.git   # patched
unset MUSE_WORK_DIR; source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse backing /cvmfs/mu2e.opensciencegrid.org/Musings/AnalysisMDC2025/v02_01_00
muse setup            # NOT `muse setup | tail` — a pipe puts it in a subshell and MUSE_WORK_DIR is lost
muse build -j 8       # 69 s for all of ArtAnalysis
```

Then the reproducer fcl with the local build on `LD_LIBRARY_PATH`:
`TrkQualAll` logs `KalSeed with 14 hits and none active ... assigning
quality 0`, `EventNtuple` runs, art exits 0 in 7 s.

## Why recovery cannot fix it

The exception is a function of the event content, not the worker. Every
resubmission re-reads the same 31398 events and throws at the same one.
The recovery loop will burn `max-attempts` and park the file. Rate is about
one event in 3×10⁶ for this sample; expect a handful more across the 4986
files of campaign 99 and across the four ntuple groups still to be staged.

## Fix options

1. **ArtAnalysis** (correct fix): treat `nactive == 0` like the
   `!entrance_found` case — skip both MVAs and assign quality 0 — and, as a
   belt-and-braces guard, replace non-finite features with `NAN` before the
   XGBoost call. Separately worth asking why a KalSeed with zero active hits
   is in the output collection at all.
   Needs a new AnalysisMDC2025 musing (v02_01_01), then re-run only the
   parked files under it. Owner: TrkQual maintainers (ArtAnalysis).
2. **Accept the loss**: 4985/4986 files, one `CePLeadingLogOnSpill` file
   missing from the `-001` ntuple. Document it on the dataset.
3. Not an option: an fcl workaround that disables the BDT for one file
   would make that file's TrkQual inconsistent with the rest.

## Lessons

- `pushOutput status at exit: 0` with no output dataset is the normal
  shape of an art failure on the direct backend: the log is pushed, data
  is not. Absence of the log dataset would have meant death before art.
- A single file failing twice in a draining campaign is a read-the-log
  event, not a resubmit event. `jobsub_fetchlog` works from the user
  account for mu2epro Production jobs (Analysis-role token suffices).
- `condor_history` against jobsub03 with `-limit 1100` takes over two
  minutes; run it in the background to a scratch file.
