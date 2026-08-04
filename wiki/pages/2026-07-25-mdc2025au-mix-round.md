---
title: "MDC2025au mix round — wave 1 submitted, wave 2 pending"
tags: [campaign, mdc2025au, mixing, direct-submission]
sources: []
updated: 2026-07-25
---

# MDC2025au mix round

**Status:** wave 1 (500 jobs) submitted and running 2026-07-25; wave 2
(6,821 jobs / 36.4 TB) not started.

Reprocessing the full mixing roster at the MDC2025au release. Merge
factors come from [[mix-merge-factor-sizing]] — all 19 descs divide their
input exactly, 18 land on round job counts.

## Scope

19 descs in `templates/MDC2025/mix.json`. `NoPrimary` was already
produced at au (2000 files, 1.69 TB, ledger campaign 5) **at merge 1** —
it predates the merge-factor work and is the one au output at ~0.85
GB/file instead of ~5 GB. `latestDatasets --emit mix --campaign
MDC2025au --skip-produced` correctly drops it, leaving 18.

| | descs | jobs | TB |
|---|---|---|---|
| wave 1 (submitted) | 4 | 500 | 2.8 |
| wave 2 (pending) | 14 | 6,821 | 36.4 |
| **total remaining** | **18** | **7,321** | **39.2** |
| already done (NoPrimary) | 1 | 2,000 | 1.7 |

Waves were split deliberately: validate au mixing end-to-end on a small,
all-persistent-input set before committing the remaining ~36 TB.

## Wave 1 — submitted 2026-07-25

| desc | merge | jobs | cluster | ledger row | input | TB |
|---|---|---|---|---|---|---|
| FlatGamma | 4 | 125 | 29308498 | 22 | ap 500 files | 0.68 |
| RMCPhaseSpace0NInternal | 4 | 125 | 29308499 | 23 | at 500 files | 0.70 |
| RMCPhaseSpace1NInternal | 10 | 50 | 92753604 | 24 | at 500 files | 0.29 |
| MuCap1809keVCalo | 5 | 200 | 29308501 | 25 | ar 1000 files | 1.14 |

Campaigns 6–9, all `complete` on the submission cursor (fully
dispatched); verification continues on rows 22–25. Queue after
submission: 479 idle, 199 running, **0 held**.

`MuCap1809keVCalo`'s cnf already existed in SAM from earlier in the
round, so its push was skipped (see idempotency note below) and only the
map entry + campaign were added.

### Pre-flight, verified before submitting

- **Pileup staged on resilient** (the hard rule — see
  [[mix-merge-factor-sizing]] and the resilient rule in memory):
  MuBeamFlashCat/ac 20, EleBeamFlashCat/ac 400, NeutralsFlashCat/ad 1000,
  MuStopPileupCat/ac 50 files.
- **Primary inputs need NOT be on resilient.** `inloc: resilient` falls
  back to SAM for the primary input (`file_resolver.py:342-344`); only
  the pileup must genuinely live there. All 18 input dts datasets show 0
  files under `/pnfs/mu2e/resilient` and that is fine.
- **Input residency:** 14 of 18 inputs are on `persistent` dCache. All
  four wave-1 inputs are persistent — no tape recall.
- **Smoke:** `mu2e -c … -n 2` exit 0 for `RMCPhaseSpace1NInternal` (at
  input) and `FlatGamma` (ap input). CPU ≈ 4.1–4.2 s for 2 events,
  VmPeak ≈ 2.07 GB, VmHWM ≈ 1.01 GB.

### Memory headroom to watch

Default grid request is **2000 MB** (`submit.py:702`). The smoke's
VmPeak was 2074 MB — over the request — but VmHWM (resident, which is
what condor enforces) was only 1010 MB. The ar round ran these descs
fine at the default. Worth rechecking for wave 2's high-merge descs,
especially `RPCExternalPhysical` at merge 100.

## Operational sequence (what was actually run)

```bash
# 1. emit the roster, resolve input campaigns
./bin/latestDatasets --emit mix --campaign MDC2025au --skip-produced

# 2. build + smoke locally as yourself (non-destructive)
json2jobdef --verb --json <wave1>.json --dsconf MDC2025au_best_v1_3
fcldump --local-jobdef <cnf>.tar --index 0     # writes <stem>.fcl
mu2e -c <stem>.fcl -n 2

# 3. push as mu2epro; --jobdefs is a THROWAWAY /tmp map
json2jobdef --prod --jobdefs /tmp/map_wave1_au_mix.json \
            --json /tmp/au_wave1_mix.json --dsconf MDC2025au_best_v1_3

# 4. enqueue campaigns (submits NOTHING)
submit_map --map /tmp/map_wave1_au_mix.json --enqueue --slice-size 500

# 5. supervised tick — this is what actually submits
./bin/submissions run
```

Steps 3–5 run as `mu2epro`; step 5 needs the `USER`/`LOGNAME`/`HOME`/
`XDG_RUNTIME_DIR` re-export (see `/mu2epro-submit`).

## Decisions and corrections

### Direct campaigns use a throwaway /tmp map

`--jobdefs` for a **direct** campaign points at a per-campaign `/tmp`
map, not at anything under `poms_map/`. The ledger is the evidence:
campaign 1 used `poms_map/MDC2025ar-rmcextmix.json`; campaigns 2–5
(including au NoPrimary, `/tmp/map_noprimary_au.json`) all used `/tmp`.

`poms_map/` is a historical directory name — it does not mean everything
in it is a POMS map. Creating a new persistent file there for a direct
campaign produces a file the direct workflow neither reads nor wants.
Worse, appending a direct campaign to a POMS-active `MDC2025-NNN.json`
would have POMS dispatch those entries while `submissions run` feeds
slices from the same tarball → duplicate jobs and duplicate SAM
registration.

This contradicted the old `/mu2epro-run` HARD RULE ("always the latest
plain `MDC2025-NNN.json`, never per-campaign names"), which was
POMS-era. Both `/mu2epro-run` and `/mu2epro-submit` were amended
2026-07-25 to route on backend and to tell the operator to check the
ledger's `map_path` for precedent before inventing a filename:

```bash
python3 -c "import sqlite3;c=sqlite3.connect('file:/exp/mu2e/data/users/mu2epro/prodtools/submissions.db?mode=ro',uri=True);[print(r) for r in c.execute('SELECT id,tarball,map_path FROM campaigns ORDER BY id')]"
```

### json2jobdef --prod is idempotent — re-run it to add a map entry

`_pushout_to_sam` checks SAM first and no-ops if the file is already
there (`json2jobdef.py:625`, "Idempotent — repeat calls are no-ops once
SAM has the file"). `create_index_definition` deletes and recreates the
`i<mapstem>` definition from the map's current total.

So the way to add an already-pushed cnf to a map is simply to re-run
`json2jobdef --prod --jobdefs <same map>` with a config containing that
entry. It prints `already exists on SAM, skipping push`, appends the map
entry, and rebuilds the definition. **Do not hand-edit the map** — that
skips the definition rebuild and leaves it stale.

Observed for `MuCap1809keVCalo`: push skipped, entry appended,
`imap_wave1_au_mix` deleted and recreated at 500 jobs.

### Ledger rows can lag behind reality

Submission row 21 (au NoPrimary) read `active` for a day after its jobs
finished and all 2000 outputs were in SAM. It was bookkeeping lag, not
work in flight — the next `submissions run` closed it (`row 21:
complete (2000 indices)`). Check SAM output counts before treating a
stale `active` row as a stuck campaign.

## Wave 2 — pending

14 descs, 6,821 jobs, 36.4 TB. Blocking check before enqueueing:

**Four inputs are tape-backed** and need an `mdh` residency check (and
possibly a prestage) or their slices will trigger per-file tape recalls:
`IPAMuminusMichel` (ap), `PbarResampling` (ar), `RPCInternalPhysical`
(ap), `ensembleMDS3c` (af).

Also carried forward: `RPCExternalPhysical` at merge 100 runs only 50
jobs, sampling ~50 of the 1000 `NeutralsFlashCat` pileup files — flagged
for the sample owners, unresolved.

Hold wave 2 until wave 1 drains and passes completeness — that is the
point of the split.

## Related

- [[mix-merge-factor-sizing]] — where the merge factors come from
- [[2026-07-18-direct-recovery-loop]] — the direct submission subsystem
