---
title: Run1B consolidated into Offline main; Run1B branch retired
tags: [decision, run1b, offline, geometry, campaigns]
sources: []
updated: 2026-08-12
---

# Run1B consolidation

## Outcome

The long-lived `Run1B` branch in Mu2e/Offline is retired. Everything Run1B
production actually runs now lives on `main`.

| PR | repo | content | merged |
|---|---|---|---|
| [#1923](https://github.com/Mu2e/Offline/pull/1923) | Offline | `geom_run1_b_v40.txt`, 19 `Tracker_FEB_*` virtual detector ids (117–135), the `VirtualDetectorMaker` / `constructVirtualDetectors` blocks behind `hasTrackerFEBVirtualDetectors` | 2026-08-12, main @ `38f6943d5` |
| [#1927](https://github.com/Mu2e/Offline/pull/1927) | Offline | degrader mother sized to its contents (`fd1a90b77`) | before #1923 |
| [#565](https://github.com/Mu2e/Production/pull/565) | Production | `JobConfig/mixing/OneBB1500W.fcl` + `1BB1500W` beam constants | 2026-08-12 |
| `1dacb738` | Production | `NoFieldRun1B.fcl` default v01 → v40 | on main |

## The decision, and what it deliberately does not do

**Land capability, keep Run1A default.** `geom_common.txt` on main still points
at `geom_run1_a_stickman.txt`. Run1B is selected by *naming*
`geom_run1_b_v40.txt` — which is what every prodtools Run1B template already
does. Nothing on main changes behaviour for a job that does not ask for Run1B.

**Production-scoped: v40 and the degrader fix only.** `v02`–`v06` and the code
they need (`Mu2eWorld.cc`, `constructStoppingTarget.cc`, `constructTSdA.cc` and
the EMC source virtual detectors) were **not** ported. They were superseded, and
porting them would have meant landing a stopping-target model known to be wrong.

**Why v40 is the only correct Run1B geometry.** Run1B is Run1A plus a two-piece
aluminium assembly at the TS5 face: a 600 mm TSdA plate with a 135 mm aperture
at z=4195, and the pion degrader repurposed as a 150 mm mobile target at z=4235.
`v40` inherits `geom_run1_a.txt` untouched and *adds* that. `v01`–`v06` instead
*replaced* the 37-foil stopping target with a single 600 mm disk and moved the
tracker and calorimeter to extracted positions — a different detector, not a
Run1B one.

## The freeze, and why it is dangerous

`Run1Baa/Baa1/Bab/Bab2/Baf` (v01), `Run1Bag/Bah` (v03) and
`Run1Bai/-001/-002/-003/-007` (v06) cannot be re-run against main.

v03 and v06 are simply absent from main — those entries abort on a missing file,
which is the safe failure.

**v01 is the trap.** It *is* on main (added by `2032d440a` for
`EventDisplayRun1b.fcl`), so a job starts and completes. But:

- main's `v01` sets `tracker.inDS2Vacuum = true` and
  `calorimeter.inDS2Vacuum = true`, and **no code on main reads
  `inDS2Vacuum`** — that lives in `Mu2eWorld.cc` and
  `constructVirtualDetectors.cc` on the branch only. SimpleConfig does not warn
  about keys nobody reads.
- main's `v01` is not even the file those campaigns ran: the branch copy also
  sets `mu2e.detectorSystemZ0 = 7000` and the EMC source VD positions
  (`zEMCSourceInMu2e`, `zEMCSource2InMu2e`, `zEMC0Front`), read by
  `VirtualDetectorMaker.cc` on the branch.

Result: tracker and calorimeter stay in DS3Vacuum, the stopping-target mother is
undersized, exit status 0. Recorded in `data/Run1B/README.md`, where an operator
editing a Run1B entry will hit it.

## Reproducibility does not depend on the branch

Every frozen entry pins `simjob_setup` to an immutable CVMFS Musing, and each
Musing's `backing/Offline/Mu2eG4/geom/` carries exactly the geometries its
campaign needs (verified 2026-08-10): Run1Bab ships v01; Run1Baf v01–v02;
Run1Bah v01–v03; Run1Bai v01–v06. Retiring the branch strands nothing.

The tag `run1b-archive-2026-08-08` is the **source-code** record — the only
remaining copy of v02–v06, of the fuller v01, and of the un-ported C++. Read it
to understand a frozen campaign; run the Musing to reproduce one.

## Beam-power side thread

Reverse-engineering the Run1B intensity (`extendedMean: 5.92e6`, which existed
only in prodtools `data/Run1B/mix.json` and was documented nowhere) established
the primitive as **1.5e12 POT/cycle** = 0.375 × a full 4e12 batch → 5.9184e6 per
microbunch → **1.446 kW**. The deliberate target was 1.5 kW, so
`OneBB1500W.fcl` now carries 6.14e6 with the derivation written down. The full
ladder is in the session memory `reference_pbi_intensity_ladder_and_beam_power`.

## Still open

- A Run1B-by-default Musing is a 2-line delta on an Offline tag:
  `geom_common.txt` → `geom_run1_b_v40.txt`, and `fcl/standardServices.fcl`
  `bFieldFile` → `bfgeom_DSOff.txt`. There is no `bfgeom_common.txt`
  indirection. A DS-on variant is a *second* Musing
  (`geom_run1_b_ds_on_v40.txt` + `bfgeom_v01.txt`), not the same one.
- The live `Run1Ban` and `Run1Bap` Musings patch only `geom_common.txt`, and to
  the superseded `v01`, with the DS field **on**. Never drop the explicit
  geometry/field overrides from a Run1B entry on the assumption the Musing
  defaults are right.
- `ensemble/python/constants.py` gained the `1BB1500W` numbers but they are not
  wired into `get_duty_factor()` / `get_pot()`, which still branch on `'1BB'` /
  `'2BB'` only. Normalising Run1B with `run_mode='1BB'` overestimates POT by
  2.67×; use the `custom` method. Separately, `get_duty_factor()` fails open for
  unknown modes with its warning commented out.

Related: [[2026-06-07-run1ban-mustop-rebuild-chain]],
[[2026-06-14-run1ban-primaries-added]], [[2026-07-11-noprimary-run1ban-001-remake]].
