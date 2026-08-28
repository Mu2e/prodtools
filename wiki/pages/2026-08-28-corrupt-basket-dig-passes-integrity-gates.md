---
title: Two NoPrimaryMix1BB-002 digs with a corrupt ROOT basket passed both SAM integrity gates; reco caught them; producer node fnpc18003
tags: [incident, data-integrity, root, dcache, Run1Bav, Run1Baw, NoPrimaryMix1BB, campaign-73, campaign-83, fnpc18003]
sources: []
updated: 2026-08-28
---

# Corrupt basket in two dig files, invisible until reco

## Symptom
Campaign 83 (`NoPrimaryMix1BB-reco.Run1Baw_best_v1_5-002`) stalled at
1998/2000. Indices 1330 and 1473 failed three times each on different
nodes, ~16–23 min in, exit 1:

```
Fatal Root Error: TBasket::Streamer
The value of fKeylen is incorrect (-27091) ; trying to recover by setting it to zero
  ... while processing module CaloHitTruthMatch run: 1470 subRun: 1482 event: 4292
```

Inputs: `dig.mu2e.NoPrimaryMix1BB.Run1Bav_best_v1_5-002.001470_{00000445,00001443}.art`
(campaign 73's mix outputs, 2026-08-20).

## Diagnosis
- Sizes equal SAM; `ONLINE_AND_NEARLINE`; not truncation, not a cold read.
- Disk adler32 (converted to enstore seed-0) **equals SAM's CRC**, and the
  xrootd-served bytes equal the NFS-served bytes. The files are exactly
  what the producer declared: written corrupt, not damaged in transit.
  `pushOutput` computes the CRC on the worker's local copy, so both
  integrity gates (art rc=0, dCache CRC) pass a file that is bad on the
  worker before the copy. **Reco is the third gate.**
- PyROOT `GetEntry` over every entry of every TTree finds one short run
  of unreadable entries per file: Events 17524–17526 and
  EventMetaData/`EventBranchEntryInfo` 17523–17525 in `…00000445`;
  Events 19291–19294 in `…00001443`. Branches: `StrawGasSteps`,
  `CaloDigis`, `StrawDigis`, `EventBranchEntryInfo`. Everything else
  reads fine.
- The reco-fatal basket for `…00000445` is the EventMetaData one; the
  Events-tree ones are branches the KL reco never reads, which is why a
  local reco over 17500–17559 passed. Scan every tree, not just Events.
- Producer logs (found via SAM parentage, since a mix log is named by cnf
  **index** and the index ≠ output sequencer): both jobs exit 0,
  VmHWM 1.4 GB, nothing unusual — and both ran on **fnpc18003**, in the
  same concurrent window 2026-08-20 18:20–23:00 UTC, on slots 1_10 and
  1_47. That node produced 8 of the 2000 `-002` files; 2 are the corrupt
  ones, 6 reco'd clean. Chance of both bad files on a 0.4 % node ≈ 1e-5.
- The same node produced 21 of the `-003` files (2026-08-22); all 21 scan
  clean in every tree. Damage confined to the Aug-20 window.

## Mechanism (found by reading the bytes)
The ROOT basket seek tables of both files are intact; one contiguous
~264 KiB stretch of each file holds wrong bytes. Parsing the `TBasket`
key headers physically present inside `…00001443`'s bad stretch shows
self-recorded `seekKey` values 2 286 420 081 … 2 286 668 364 — the very
baskets that belong at those offsets in `…00000445`, displaced by a
constant, page-aligned Δ = +229 638 144 (219 MiB). So ~66 pages of job
903's output were written into job 1121's file; job 903's own region got
unrelated bytes (from none of the 8 sibling files). Two processes'
dirty pages crossed on the worker's local scratch: a page-cache /
block-layer / controller misdirected write on `fnpc18003`, not art,
ROOT, a DIMM error, or dCache. Diagnostic recipe: find `\x07TBasket`
markers in the bad region, decode the key header 34 bytes earlier, and
compare its `seekKey` with its position — constant non-zero Δ across
many keys = displaced block.

## Local reproduction (production fcl, verbatim error)
```bash
cp /pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/NoPrimaryMix1BB-reco/Run1Baw_best_v1_5-002/tar/bd/27/cnf.mu2e.NoPrimaryMix1BB-reco.Run1Baw_best_v1_5-002.0.tar .
jobfcl --jobdef cnf.mu2e.NoPrimaryMix1BB-reco.Run1Baw_best_v1_5-002.0.tar \
       --source dig.mu2e.NoPrimaryMix1BB.Run1Bav_best_v1_5-002.001470_00001443.art \
       --default-loc tape --default-proto file > job.fcl
source /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025aw/setup.sh
mu2e -c job.fcl --nskip 19270 -n 60        # 1443: fails in ~1 min
# 445: --nskip 30000 -n 2800, fails after 2523 events (entry ≈32523)
```
Scan scripts: `/exp/mu2e/data/users/oksuzian/claude-scratch/diag/scan_dig.py`
(Events) and `scan_all_trees.py` (every tree). A 6.5 GB file takes a few
minutes.

## Fix in prodtools (2026-08-28, branch, unreleased)
`utils/validate_root_outputs.py` + `runmu2e._validate_outputs`: after
`mu2e` exits 0 and before anything is pushed, every `.art`/`.root`
output is read back entry by entry under the job's own setup; a failure
marks the job failed (no data push, log pushed) and recovery re-runs the
index elsewhere. ~15 s per GB. Entry key `validate_outputs: false` opts
out; `--no-validate` on `runmu2e`/`runlocal`. Verified against the two
corrupt digs (BAD, rc=1) and a synthetic scrambled-block file. Reaches
production with the next cvmfs release.

## Resolution / options
Row 371 reached `DEFAULT_MAX_ATTEMPTS` = 3, so the next tick marks it
EXHAUSTED and recovery stops. Either accept 1998/2000, or retire the two
dig files from SAM/tape and resubmit campaign 73 indices 903 and 1121
(`submissions resubmit`). Report `fnpc18003` + window to FIFE.

## Lessons
- SAM presence proves the declared bytes arrived, not that the bytes are
  sound. A deterministic same-event failure across three nodes with a
  matching checksum means "written bad".
- Direct-backend log files are named by cnf index; log count minus art
  count = failed attempts.
- `condor_history` for clusters older than about a week returns nothing;
  the log dataset dir under `/pnfs/mu2e/persistent/datasets/phy-etc/log/`
  is greppable for `node_name` instead.

Related: [[2026-08-27-inloc-fallback-rendered-with-declared-proto]] (same round).
