# Consolidating the Run1B branch into main in Mu2e/Offline

**Date:** 2026-08-08
**Repo under change:** `Mu2e/Offline` (this spec lives in prodtools for convenience)
**Status:** design approved, implementation not started

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
> it doesn't change the nominal running. [...] As virtual detectors have been
> added, this will likely change the simulation results such that comparisons
> would probably need high statistics and distribution shape comparisons to
> ensure they're correct. We could try doing a gdml dump on main and here, and
> looking to see if any volumes change between the two versions?

with four inline comments:

| File | Comment |
|---|---|
| `Mu2eG4/geom/geom_common.txt:6` | We shouldn't change the default geometry |
| `Mu2eG4/geom/geom_run1_b_v01.txt:40` | Should these be in all Run 1B geometry files? |
| `GeometryService/src/VirtualDetectorMaker.cc:189` | I don't know if we should have default z values for these |
| `Mu2eG4/src/constructVirtualDetectors.cc:570` | It seems like this is a case I missed when fixing these parent volume changes? This should probably be doing the tracker in DS2 check |

The branch is also actively costing us. `Mu2e/Production` has **no** Run1B
branch — all Run1B configuration is on Production `main` — and it references
geometry files that exist only on the Offline `Run1B` branch:

| Consumer | References | On Offline main? |
|---|---|---|
| `Production/JobConfig/recoMC/NoFieldRun1B.fcl` | `geom_run1_b_v01.txt` | yes |
| `Production/Tests/Run1BReco.fcl` | `geom_run1_b_v40.txt` | **no** |
| `prodtools/templates/Run1B/digi.json` | `geom_run1_b_v40.txt` | **no** |
| `prodtools/templates/Run1B/reco.json` | `geom_run1_b_v40.txt` | **no** |

So Production main is presently broken against Offline main for the v40 path.
Consolidation repairs a real cross-repo dangling reference; it is not tidying.

## Goal and non-goal

**Goal.** Offline `main` gains the ability to *run* Run1B, and the `Run1B`
branch is retired.

**Non-goal.** Offline `main` does not change what it does by default.
`Mu2eG4/geom/geom_common.txt` keeps including `geom_run1_a_stickman.txt`. The
branch's flip to `geom_run1_b_v01.txt` is dropped.

Keeping the Run1A default costs Run1B production nothing: every Run1B consumer
already sets `services.GeometryService.inputFile` explicitly. Nothing in
Production or prodtools relies on `geom_common.txt` resolving to Run1B.

## Governing principle

> Every Run1B behavior is selected by a geometry file, never by a code default.

The testable form: **a gdml dump of `geom_common.txt` and of `geom_run1_a.txt`
must be byte-identical before and after each PR.** This is the check Mackenzie
proposed, and the branch already ships `Mu2eG4/fcl/gdmldump_run1_b_v01.fcl` to
build on.

A corollary that shaped the whole design, in order of preference:

1. **No gate at all** — find the rule that is unconditionally correct and
   happens to reproduce main's behavior in main's configuration. This is where
   the stopping-target radius lands.
2. **A gate keyed on configuration that already exists** — `hasSTM`, or the
   presence of a key. This is where `STMUpstream` and the EMC virtual detectors
   land.
3. **A new flag** — not needed anywhere in this consolidation.

None of the three contested changes requires a new knob, which converts them
from "behavior change under a switch" into "main-side bug fix that Run1B
exposed" — a materially easier review.

## Classification of the 18 files

### Group A — pure addition, zero nominal impact

Split by *kind*, because the delivery split follows it: A1 is data and can be
reviewed by reading configuration, A2 is code and belongs with the other code
review even though it is equally inert.

**A1 — configuration and identifiers (ships in PR1):**

| File | Why it is inert on main |
|---|---|
| `Mu2eG4/geom/geom_run1_b_{v02,v03,v04,v05,v06,v40,ds_on_v40}.txt` | new files, nothing includes them |
| `Mu2eG4/fcl/gdmldump_run1_b_v01.fcl` | new file |
| `Mu2eG4/geom/geom_run1_b_v01.txt` (+6) | affects v01 only |
| `DataProducts/inc/VirtualDetectorId.hh` | 22 enums appended before `lastEnum`; existing ids keep their numbers |

**A2 — code, nominal-safe but still code (ships in PR2):**

| File | Why it is inert on main |
|---|---|
| `Mu2eG4/src/Mu2eWorld.cc` | `tracker.inDS2Vacuum` / `calorimeter.inDS2Vacuum`, both `getBool(..., false)` |
| `Mu2eG4/src/constructTSdA.cc` | `tsda.cutout.build`, `tsda.extra.build`, `tsda.tubes.build` all default `false`; see sentinel note below |
| most of `Mu2eG4/src/constructVirtualDetectors.cc` | parent-volume ternaries on `tracker.inDS2Vacuum` (default `false`) |

**`tsda.rin` sentinel.** The branch changes
`getDouble("tsda.rin", 0.0)` with test `< 1.0e-06` to
`getDouble("tsda.rin", -1.0)` with test `< 0.`. This is behavior-neutral for
every existing geometry: `TSdA_v01.txt:14` sets `240.0` and `TSdA_v02.txt:8`
sets `235.0`. No geometry sets `tsda.rin = 0`. The change only matters for a
future geometry wanting a hole-less disk, which the old sentinel could not
express.

### Group B — changes nominal today; resolved without any new configuration

**B1. `STMUpstream` virtual detector.** The branch comments it out entirely.

On main it is placed at `VirtualDetectorMaker.cc:195`, *outside* the
`if (c.getBool("hasSTM",false))` block that begins at line 480 and wraps every
other STM virtual detector. It is the only STM VD not gated on `hasSTM`.

Resolution: move it inside that block. Run1A, MDC and v40 have `hasSTM = true`
and keep it bit-identically; Run1B v01–v06 set `hasSTM = false` and drop it
automatically. **No new knob.** Standalone justification: *STMUpstream is the
only STM VD not gated on `hasSTM`.*

**B2. Stopping-target mother radius.** The branch replaces main's
`radius = _foilTarget_supportStructure_rOut - 0.001` with the older
`max over foils(rOut + perp) + 1`.

The two configurations describe physically different objects:

| | Run1A / MDC / v40 | Run1B v01–v06 |
|---|---|---|
| foils | 37 | 1 |
| `stoppingTarget.radii` | 75.00 mm each | 600.00 mm |
| `stoppingTarget.halfThicknesses` | 0.0528 mm | 3.175 mm |
| `foilTarget_supportStructure` | `true` (`stoppingTarget_CD3C_34foils.txt:74`) | `false` |

Run1B's target is a single aluminium disk spanning essentially the whole DS
aperture, so there are no suspension wires to model. The flag difference is
deliberate, not an oversight.

Main derives the mother radius solely from the support structure. Both
configurations reach `StoppingTargetMaker.cc:123-136` with
`endAtOPA = true` — v01 inherits it via `geom_run1_a.txt` — so both take the
OPA-derived branch, not the 250 mm fallback at line 138:

| | support-derived | foil-derived | main's radius | needed |
|---|---|---|---|---|
| Run1A | ≈503.6 | ≈76 | 503.6 | 503.6 |
| Run1B v01 | ≈447.7 | 601 | **447.7** | 601 |

So Run1B gets a mother cylinder smaller than the single foil it contains. That
is the overlap the branch was fixing.

Resolution: **do not branch on configuration at all.** Take the maximum of both
constraints, unconditionally, in `StoppingTargetMaker.cc`:

```cpp
radius = std::max(_foilTarget_supportStructure_rOut - 0.001,  // reach the OPA
                  maxFoilOuterRadius + 1);                    // enclose the foils
```

Run1A yields `max(503.6, 76) = 503.6`, bit-identical to main. Run1B yields
`max(447.7, 601) = 601`, correct. One code path in both, no dependence on
`foilTarget_supportStructure`, and it stays correct if that flag is ever flipped
on a Run1B geometry. Standalone justification: *the mother volume must satisfy
both constraints it has always had; main only ever enforced one of them.*

`constructStoppingTarget.cc` keeps using `target->cylinderRadius()` as main does
— the fix above already makes that value correct in both regimes. The branch's
`maxMotherRadius` support-structure loop is **not** ported: it is empty exactly
when Run1B needs it (no support structures) and only grows the mother in
nominal, so it is dead code for its intended case and a behavior change for the
other. The one part worth keeping is guarding the OPA overlap check on
`hasProtonAbsorber`, since Run1B sets it false and `GeomHandle` would throw.

**B3. EMC source virtual detectors.** The branch adds `EMC_Source` (117),
`EMC_Source2` (118) and `EMC_0_Front` (119) with hardcoded defaults:

```cpp
const double zVDcenterInMu2e = c.getDouble("zEMCSourceInMu2e", 5300.);
```

so every geometry silently gains three VDs, including MDC2020 and MDC2025.

Resolution: place each VD only when the geometry file actually sets its z, using
`SimpleConfig::hasName()` (`ConfigTools/inc/SimpleConfig.hh:95`). Key presence
*is* the switch — **no new knob**. The placement code in
`constructVirtualDetectors.cc` is already guarded by `vdg->exist(vdId)`, so this
single gate covers both files.

This has a consequence that must be handled, and it is Mackenzie's second
comment. Geometry lineage:

| File | Includes | Sets `zEMC*`? |
|---|---|---|
| `geom_run1_b_v01.txt` | (base) | yes: `5300 / 4800 / 8695` |
| `geom_run1_b_v02.txt` | v01 | inherited |
| `geom_run1_b_v03.txt` | `geom_run1_a.txt` | no |
| `geom_run1_b_v04.txt` | `geom_run1_a.txt` | no |
| `geom_run1_b_v05.txt` | `geom_run1_a.txt` | no |
| `geom_run1_b_v06.txt` | `geom_run1_a.txt` | no |
| `geom_run1_b_v40.txt` | `geom_run1_a.txt` | no |
| `geom_run1_b_ds_on_v40.txt` | v40 | no |

Only v01 configures them deliberately, and it overrides `zEMC0Front` from the
code default `5830` to `8695` — so `5830` is a value no tuned configuration
uses. Everything else inherits all three by accident. Since prodtools digi and
reco run **v40**, and `EventNtuple/fcl/from_mcs-Run1B.fcl` exists specifically to
add the virtual-detector-step branch, v40 needs explicit values rather than
inherited defaults. See "Open item" below.

**B4. Tracker FEB virtual detectors** (`Tracker_FEB_0..18_SurfIn`, ids 120–138)
are already gated, but inconsistently: `VirtualDetectorMaker` gates on
`TrackerHasBrassRings` while `constructVirtualDetectors` gates on
`tracker.inDS2Vacuum`. Both default `false`, so nominal is safe either way, but
they must be reconciled to one key.

### Group C — unconditional but unreachable in nominal

`Mu2eG4/src/constructProtonAbsorber.cc` rewrites the degrader mother/filter/
frame z-layout. Nominal geometries have `degrader.build = false` (the pion
degrader is off by default), so the rewritten code never executes on main. It is
required by v40, which turns the degrader on and uses it as the Run1B stopping
target. Lands as-is with that justification.

### Group D — must not be merged

`Mu2eG4/geom/geom_common.txt`. Dropped.

### Group E — defects in the branch, fixed during consolidation

1. `constructVirtualDetectors.cc:~570` tests `isDumbbell` where it should test
   `tracker.inDS2Vacuum` (Mackenzie's fourth comment). Nominal-safe either way
   since both default `false`, but wrong for Run1B.
2. The B4 gate disagreement above.

### Group F — reviewability

Of the 491 changed lines in `constructVirtualDetectors.cc`, only 302 survive
`diff -w -B`. Roughly 189 lines — about 38% — are pure reindentation, from
dedenting the `hasDiskCalorimeter` block by one level. Isolating that into a
whitespace-only commit shrinks the substantive diff by more than a third and
directly answers "too large a diff to review by eye."

## Delivery: three sequential PRs, then retire the branch

### PR1 — Run1B geometry configs and VD identifiers

Contents: Group A1 — geometry configs, the gdml-dump fcl, and the enum. No code.

Every include resolves against main today (`geom_run1_a.txt` and
`geom_run1_b_v01.txt` both exist), so the files parse on arrival.

This PR alone repairs the cross-repo break. Reconstruction builds
`GeometryService` but not the G4 world, so the DS2Vacuum and degrader machinery
is not needed for that path: **`Production/Tests/Run1BReco.fcl` and the prodtools
v40 digi/reco templates work as soon as PR1 lands.**

What PR1 does not do: G4 simulation with the new geometries is not yet faithful
to the branch. That arrives in PR3.

Verification: build; `geom_common.txt` gdml unchanged (trivially — no code
changes); `mu2e -c Mu2eG4/fcl/gdmldump_run1_b_v01.fcl` runs.

### PR2 — Gate Run1B behavior on existing configuration

Five commits, ordered so the reviewer can skip the first:

1. Whitespace-only reindent of `constructVirtualDetectors.cc`. Verifiable by
   confirming `diff -w -B` between the two revisions is empty.
2. A2 — parent-volume ternaries on `tracker.inDS2Vacuum` and `Mu2eWorld.cc`;
   fix E1; reconcile E2.
3. B1 — `STMUpstream` inside the existing `hasSTM` block.
4. B2 — ST mother radius as the max of the support-derived and foil-derived
   constraints, unconditionally.
5. A2 — `constructTSdA.cc` gated additions and the `tsda.rin` sentinel.

Verification: gdml dumps of `geom_common.txt` and `geom_run1_a.txt` byte-identical
to main; `g4surfaceCheck` and `rootOverlaps` green.

### PR3 — EMC source VDs and v40 simulation support

Contents: B3 (`hasName()` gating), the `+233` placement block in
`constructVirtualDetectors.cc`, Group C, and explicit `zEMCSourceInMu2e` /
`zEMCSource2InMu2e` / `zEMC0Front` values for v03, v04, v05, v06 and v40.

Verification: `geom_common.txt` and `geom_run1_a.txt` gdml still identical (the
EMC VDs are absent because the keys are absent); **v01's gdml matches what the
`Run1B` branch produces today** — that is the proof consolidation was faithful;
v40's gdml gains the three VDs at the chosen z with no overlaps.

### Branch retirement

After PR3, `git diff main Run1B` should be empty except `geom_common.txt`.
Delete the branch and record why, so nobody re-forks it.

## Open item requiring a human decision

The z values for `EMC_Source` / `EMC_Source2` / `EMC_0_Front` on v03–v06 and v40.

v01 uses `5300 / 4800 / 8695` with `mu2e.detectorSystemZ0 = 7000`; v40 keeps
`geom_run1_a.txt`'s `10171`. The keys are absolute Mu2e z, so they do not move
with `detectorSystemZ0` — but the detectors do, which is why v01's numbers
cannot simply be copied.

Proposed resolution: derive `zEMC0Front` from disk 0's actual front face in each
geometry, and ask @sdifalco — author of PR #1711 from branch `sdifalco/newRUN1BVD`,
which introduced these VDs — for the physics intent behind `EMC_Source` and
`EMC_Source2` before choosing their values.

This blocks PR3 only. PR1 and PR2 proceed independently.

## Testing strategy

- **gdml diff is the primary regression gate on every PR.** It is the evidence
  Mackenzie asked for and it makes "nominal is unchanged" a mechanical check
  rather than a claim.
- Offline CI: `build`, `g4surfaceCheck`, `rootOverlaps`, `ceSimReco`,
  `g4test_03MT`.
- Run1B smoke after PR1: `Production/Tests/Run1BReco.fcl`.

**On the `trigger` failure.** PR #1849's `trigger` test returned code 2. Before
attributing that to these changes, check whether main itself was red on the
unchanged SHA at that time — a red Offline PR check has previously turned out to
be a broken main rather than a broken PR. Determine the cause before PR2, since
PR2 is where trigger-relevant geometry changes concentrate.

## Risks

- `lastEnum` grows by 22. Anything sized off it grows with it. Grep reco and
  EventNtuple for fixed VD-count assumptions before PR1.
- `EventNtuple/fcl/from_mcs-Run1B.fcl` may reference virtual detector ids
  numerically; confirm before PR1 changes the enum.
- PR3 changes simulation output for Run1B geometries by construction (VDs are
  added). Comparisons against branch-produced datasets need the gdml equivalence
  argument, not event-level agreement.
