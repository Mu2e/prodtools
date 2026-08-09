# Consolidating the Run1B branch into main in Mu2e/Offline

**Date:** 2026-08-08
**Repo under change:** `Mu2e/Offline` (this spec lives in prodtools for convenience)
**Status:** design approved, implementation not started
**Scope:** production-scoped — see "Scoping decision" below

## Problem

`Mu2e/Offline` carries a long-lived `Run1B` branch, opened December 2025. As of
2026-08-08 it is 66 commits ahead of `main` and 0 commits behind — `main` was
merged into it the same day (PR #1922). The substantive delta is 18 files.

Because the branch is never behind, a merge is mechanically clean. The reason it
has not landed is not conflicts. A PR was already attempted:
[#1849 "Run1B->Main"](https://github.com/Mu2e/Offline/pull/1849), opened
2026-06-02 and closed unmerged on 2026-06-19. Offline CI was green on everything
except `trigger` (return code 2). The blocking feedback came from
@michaelmackenzie:

> By eye it's hard to really review this large of a diff and be confident that
> it doesn't change the nominal running. [...] We could try doing a gdml dump on
> main and here, and looking to see if any volumes change between the two
> versions?

with four inline comments:

| File | Comment |
|---|---|
| `Mu2eG4/geom/geom_common.txt:6` | We shouldn't change the default geometry |
| `Mu2eG4/geom/geom_run1_b_v01.txt:40` | Should these be in all Run 1B geometry files? |
| `GeometryService/src/VirtualDetectorMaker.cc:189` | I don't know if we should have default z values for these |
| `Mu2eG4/src/constructVirtualDetectors.cc:570` | It seems like this is a case I missed when fixing these parent volume changes? This should probably be doing the tracker in DS2 check |

The branch is also actively costing us. `Mu2e/Production` has **no** Run1B
branch — all Run1B configuration is on Production `main` — and it references a
geometry file that exists only on the Offline `Run1B` branch:

| Consumer | References | On Offline main? |
|---|---|---|
| `Production/Tests/Run1BReco.fcl` | `geom_run1_b_v40.txt` | **no** |
| `prodtools/templates/Run1B/digi.json` | `geom_run1_b_v40.txt` | **no** |
| `prodtools/templates/Run1B/reco.json` | `geom_run1_b_v40.txt` | **no** |
| `Production/JobConfig/recoMC/NoFieldRun1B.fcl` | `geom_run1_b_v01.txt` | yes |

Production main is presently broken against Offline main for the v40 path.

## Scoping decision

The Run1B branch contains two generations of stopping-target modelling, and only
the second is live.

Run1B physically has two stopping targets: **the thin 37-foil target, which is
common with Run1A**, and **a disk at the face of TS5**.

`geom_run1_b_v01.txt` and `v03`–`v06` do not model that. Each one overrides

```
vector<double> stoppingTarget.radii = {600.00};   // v06: {600.00, 600.00}
bool stoppingTarget.foilTarget_supportStructure = false;
```

which **replaces** the 37×75 mm foils inherited from `geom_run1_a.txt` with a
single 600 mm disk, collapsing both targets into one object and discarding the
foil target that should be shared with Run1A.

`geom_run1_b_v40.txt` models it correctly: it inherits `geom_run1_a.txt`
untouched — so the 37 foils survive with their support structure — and adds the
TS5 endcap plate through the TSdA (`tsda.z0 = 4195`, `r4 = 600`, `rin = 135`,
`halfLength4 = 8.75`, i.e. a 1.75 cm aluminium plate with a 135 mm hole), plus
the pion degrader repurposed as the 1.75 cm mobile target.

Consumers confirm v40 superseded the earlier line:

| geometry | referenced by |
|---|---|
| **v40** | `Production/Tests/Run1BReco.fcl`, prodtools `digi.json` + `reco.json`, Run1Bak and Run1Ban campaigns |
| **ds_on_v40** | ships alongside v40; v40 plus `degrader.rotation = 120.0` to move the mobile target out of the beam for DS-on running |
| **v01** | the default inside `NoFieldRun1B.fcl`, which prodtools overrides to v40; historical Run1Baa/Run1Baa1 |
| v02, v03, v04, v05, v06 | nothing |

The prodtools wiki records the migration directly: *"geometry file
`geom_run1_b_v40.txt` instead of `v06`"*.

**Therefore consolidation ships only what production runs: v40 and ds_on_v40.**
v02–v06 are not ported. The branch is archived under a tag before deletion so
they remain recoverable if a study needs them.

This removes most of the contested surface from #1849, because every contested
change existed to make the 600 mm-disk configurations build:

| Change | Why it drops out |
|---|---|
| Stopping-target mother radius | v40 keeps the 37 foils and their support structure, so main's existing formula already produces the right radius (support-derived ≈504 mm dominates foil-derived ≈76 mm). No overlap, no fix. |
| `STMUpstream` gating | v40 does not set `hasSTM = false`; it inherits `true` from `geom_run1_a.txt`, exactly as nominal. No gate needed. |
| Tracker/calorimeter in DS2Vacuum | v40 has `tracker.inDS2Vacuum`, `ds2.halfLength` and `ds.hasServicePipes` **all commented out**. Unused. |
| `constructVirtualDetectors.cc` reindentation | only needed if the parent-volume rework is ported. It is not. |
| `isDumbbell` parent-volume defect | in the parent-volume rework, which is not ported. |
| `constructTSdA.cc` cutout/extra/tubes | v40 uses only ordinary `TSdA_v01`/`v02` parameters that main already handles. |

Nothing live builds a G4 world with v01–v06 either: `NoFieldRun1B.fcl` includes
`recoMC/NoField.fcl` and has no `g4run`, so v01's undersized stopping-target
mother is never constructed.

## Goal and non-goal

**Goal.** Offline `main` gains the ability to run the Run1B production
geometry, and the `Run1B` branch is archived and retired.

**Non-goals.**
- Offline `main` does not change what it does by default. `geom_common.txt`
  keeps including `geom_run1_a_stickman.txt`.
- `geom_run1_b_v01.txt` is **not modified**. It already exists on main; the
  branch's +6 lines add `mu2e.detectorSystemZ0 = 7000`, which would change
  geometry for a file a live Production fcl names as its default.
- Redesigning v01–v06 to add the TS5 disk to the 37 foils rather than replacing
  them is a physics change, not consolidation. Out of scope.

## Governing principle

> Every Run1B behavior is selected by a geometry file, never by a code default.

The testable form: **a gdml dump of `geom_common.txt` and of `geom_run1_a.txt`
must be byte-identical before and after each PR.** This is the check Mackenzie
proposed.

Where the branch changes nominal behavior, prefer, in order:

1. **No gate at all** — a rule that is unconditionally correct and reproduces
   main's behavior in main's configuration.
2. **A gate keyed on configuration that already exists**, including the mere
   presence of a key.
3. **A new flag** — not needed anywhere in this consolidation.

## What lands

### PR1 — Run1B production geometry and VD identifiers

| File | Status | Nominal impact |
|---|---|---|
| `Mu2eG4/geom/geom_run1_b_v40.txt` | new | none — nothing includes it |
| `Mu2eG4/geom/geom_run1_b_ds_on_v40.txt` | new | none |
| `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl` | new (not on the branch) | none |
| `DataProducts/inc/VirtualDetectorId.hh` | 22 enums appended before `lastEnum` | none — existing ids keep their numbers |

Both geometry files' includes resolve on main today. Reconstruction builds
`GeometryService` but not the G4 world, so `Production/Tests/Run1BReco.fcl` and
the prodtools v40 digi/reco templates work as soon as this lands.

`gdmldump_run1_b_v40.fcl` is added because it does not exist on the branch and
v40 cannot otherwise be verified.

### PR2 — degrader placement and EMC source virtual detectors

**Degrader geometry** (`Mu2eG4/src/constructProtonAbsorber.cc`). The mother
volume half-width and its children's z offsets disagreed, leaving the filter
partly outside its mother. The rewrite places the filter at the mother's
upstream edge and the frame directly downstream, sizing the mother to contain
both. Unreachable in nominal running: `degrader.build` is `false` in
`degrader_v02.txt` and `true` only in v40, which uses the degrader as the Run1B
mobile target. Every `degrader.supportArm.*` key v40 overrides is already read
by `MECOStyleProtonAbsorberMaker.cc:444-456` with defaults, so no new
configuration plumbing is required.

**EMC source virtual detectors** (`GeometryService/src/VirtualDetectorMaker.cc`,
`Mu2eG4/src/constructVirtualDetectors.cc`). The branch adds `EMC_Source` (117),
`EMC_Source2` (118) and `EMC_0_Front` (119) with hardcoded defaults:

```cpp
const double zVDcenterInMu2e = c.getDouble("zEMCSourceInMu2e", 5300.);
```

so every geometry silently gains three virtual detectors, including MDC2020 and
MDC2025. Resolution: place each only when the geometry sets its z, using
`SimpleConfig::hasName()` (`ConfigTools/inc/SimpleConfig.hh:95`). Key presence
is the switch — no new knob. The placement code in `constructVirtualDetectors.cc`
is already guarded by `vdg->exist(vdId)`, so this single gate covers both files.

Only the EMC placement block is ported from `constructVirtualDetectors.cc`. The
parent-volume rework and its reindentation are not.

**v40 keeps the virtual detectors it has today.** v40 descends from
`geom_run1_a.txt` and never sets these keys, so on the branch it receives them
at the hardcoded defaults. Under key-presence gating it would lose them, which
would silently change production output. v40 therefore gets those same values
written explicitly:

```
double zEMCSourceInMu2e  = 5300.;
double zEMCSource2InMu2e = 4800.;
double zEMC0Front        = 5830.;
```

This preserves current behavior exactly. It is not an endorsement of the values:
`5300` and `4800` were chosen against v01, which sets
`mu2e.detectorSystemZ0 = 7000`, while v40 keeps `geom_run1_a.txt`'s `10171`, and
v01 itself overrides `zEMC0Front` to `8695`. Whether these are the right
positions for v40 is a separate question for @sdifalco, author of PR #1711 from
branch `sdifalco/newRUN1BVD`. Raising it must not block consolidation, because
the alternative — changing the values now — would alter production output under
cover of a refactor.

The 19 `Tracker_FEB_*` virtual detectors (120–138) are registered only under
`TrackerHasBrassRings`, which v40 does not set. They arrive as enum values in
PR1 and are never placed. That is correct and needs no further work.

## Testing

- **Normalized gdml diff is the primary gate on both PRs.** Geant4's writer
  appends pointer addresses to volume names (`Mu2eWorld.cc:326` calls
  `parser.Write` with the default `storeReferences=true`), so dumps must be
  normalized before comparison, and the normalizer must be proven deterministic
  before anything depends on it.
- Baselines: `geom_common.txt` via `gdmldump.fcl`, and `geom_run1_a.txt` via
  `gdmldump_run1_a.fcl`. Both already exist on main.
- **Fidelity check:** a normalized dump of v40 built from consolidated main must
  match one built from the `Run1B` branch. This is what proves consolidation
  reproduced production rather than approximating it.
- Offline CI: `build`, `g4surfaceCheck`, `rootOverlaps`, `ceSimReco`,
  `g4test_03MT`.
- Run1B smoke after PR1: `Production/Tests/Run1BReco.fcl`.

**On the `trigger` failure.** PR #1849's `trigger` test returned code 2. Check
whether main itself was red on the unchanged SHA at that time before attributing
it to these changes — a red Offline PR check has previously turned out to be a
broken main rather than a broken PR.

## Branch retirement

After PR2, main carries everything production runs, but **not** v02–v06 or the
un-ported code. So the branch cannot simply be deleted as redundant. Instead:

1. Tag it: `git tag run1b-archive-2026-08-08 Run1B` and push the tag. The
   superseded single-disk study geometries stay recoverable.
2. Delete the branch.
3. Announce that main's default geometry is deliberately still Run1A, that Run1B
   production is selected by naming `geom_run1_b_v40.txt`, and that v02–v06 live
   under the archive tag.

**Optional follow-up, separate repo, separate PR:** `NoFieldRun1B.fcl` on
Production main defaults to `geom_run1_b_v01.txt` and is overridden to v40 by
every prodtools caller. Changing that default to v40 would remove the last live
reference to the superseded line. Not part of this consolidation.

## Risks

- `lastEnum` grows by 22. Anything sized off it grows with it. Grep reco and
  EventNtuple for fixed VD-count assumptions before PR1.
- `EventNtuple/fcl/from_mcs-Run1B.fcl` may reference virtual detector ids
  numerically; confirm before PR1 changes the enum. Appending before `lastEnum`
  keeps existing numbers valid, so the risk is a hardcoded count, not a
  hardcoded id.
- v02–v06 disappear from main. Anyone reproducing a Run1Baa-era study needs the
  archive tag. This is why the tag is mandatory rather than optional.
