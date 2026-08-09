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

`geom_run1_b_v40.txt` models it correctly. It inherits `geom_run1_a.txt`
untouched — so the 37 foils survive with their support structure — and adds
about fifteen lines that build a two-piece aluminium assembly at the TS5 face:

| piece | mechanism | position |
|---|---|---|
| TS5 endcap plate | TSdA repurposed: `r4 = 600`, `halfLength4 = 8.75`, `rin = 135` — a 1.75 cm plate with a 135 mm aperture | `tsda.z0 = 4195` |
| mobile target | pion degrader repurposed: `filter.rOut = 150`, `filter.halfLength = 8.75` | `degrader.upstreamEdge.z = 4235` |

Both are `StoppingTarget_Al` so the stops finder captures in them. The plate
blocks the TS5 face except its 135 mm aperture; the 150 mm disk rotates into
that aperture at `degrader.rotation = 60.0` and out of the beam at `120.0`,
which is the whole of `geom_run1_b_ds_on_v40.txt`. v40's own header states it:
*"1.75 cm thick mobile target, 1.75 cm thick Al TS5 endcap plate with 135 mm
hole added, otherwise a normal Run 1A config."*

v40 superseded the earlier line. The prodtools wiki records the migration
directly: *"geometry file `geom_run1_b_v40.txt` instead of `v06`"*. But the
earlier geometries are not unreferenced — `prodtools/data/Run1B/*.json`, the
campaign definition store, names all of them:

| geometry | refs in `data/Run1B` | campaigns in G4 stages | other consumers |
|---|---|---|---|
| **v40** | 28 | Run1Ban, Run1Ban-001, Run1Bap | `Production/Tests/Run1BReco.fcl`, prodtools `templates/Run1B/{digi,reco}.json` |
| **ds_on_v40** | 0 | — | ships alongside v40: v40 plus `degrader.rotation = 120.0`, moving the mobile target out of the beam for DS-on running |
| **v01** | 13 | Run1Baa | `Production/JobConfig/recoMC/NoFieldRun1B.fcl` (default, overridden to v40 by every prodtools caller), `Offline/EventDisplay/fcl/EventDisplayRun1b.fcl` |
| **v03** | 14 | Run1Bag, Run1Bah | — |
| **v06** | 15 | Run1Bai, Run1Bai-001, -003, -007 | — |
| v02, v04, v05 | 0 | — | — |

The G4-stage references (`primary_muon.json`, `stage1.json`,
`resampler_beam_mixing.json`) matter because those stages construct a world, and
v01 and v03–v06 each set the three things the un-ported code supports:

```
bool hasSTM                                     = false;
bool stoppingTarget.foilTarget_supportStructure = false;
bool tracker.inDS2Vacuum                        = true;
```

**Decision: ship v40 and ds_on_v40; freeze the Run1Baa–Run1Bai campaigns.**
v02–v06 are not ported and survive only under the archive tag. The campaigns
that used them are treated as completed history, not as a re-runnable recovery
path.

`geom_run1_b_v01.txt` is a special case: it is **already on main** and is
referenced from inside Offline itself by `EventDisplay/fcl/EventDisplayRun1b.fcl`.
It stays exactly as it is. Note that main's copy is internally inconsistent — it
relocates the tracker to `z = 7000` ("dpalo position") while leaving
`mu2e.detectorSystemZ0` at `geom_run1_a.txt`'s `10171`, and it sets
`tracker.inDS2Vacuum` / `calorimeter.inDS2Vacuum`, which no code on main reads.
The branch's `mu2e.detectorSystemZ0 = 7000` line (commit `c1fd4c345`, "Fix
detector system origin") addresses the first of those. Porting it is out of
scope: it would change geometry for a file this consolidation otherwise leaves
alone, and it would not fix the second problem without also porting the
DS2Vacuum code.

**What freezing costs.** Run1Baa, Run1Bag, Run1Bah and Run1Bai cannot be
re-run or recovered against main. Their `data/Run1B` entries will still resolve
`geom_run1_b_v01.txt` (it stays on main) but not v03 or v06, and even the v01
entries would build a G4 world with `tracker.inDS2Vacuum` silently ignored and
an undersized stopping-target mother. This is a **silent** wrong answer, not a
loud failure, which is why the freeze must be documented where an operator will
see it before attempting a recovery.

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

This is a deliberate trade, not an absence of consumers. The `data/Run1B`
entries for Run1Baa–Run1Bai do build G4 worlds; freezing them is the choice to
stop supporting that, in exchange for not carrying the stopping-target,
`STMUpstream` and DS2Vacuum changes on main for geometries that model the target
incorrectly in the first place.

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
volume's half-width and its children's z offsets were derived from
inconsistent formulas, leaving the mother oversized and extending downstream
past its contents.

**This code runs in nominal geometries.** `degrader_v02.txt:11` sets
`bool degrader.build = true`; "off by default" refers to
`degrader.rotation = 120.0`, which swings the degrader *out of the beam*, not
to its absence. Verified empirically: normalized dumps of both `geom_common.txt`
and `geom_run1_a.txt` contain all six degrader volumes, 57 references each.
An earlier draft of this spec claimed the opposite and treated the change as
unreachable in nominal — that was wrong.

What the rewrite actually changes is narrower than "placement", and the
distinction is what makes it reviewable. With nominal dimensions
(`filter.halfLength = 1.00`, `frame.halfLength = 6.35`) and with v40's
(`filter.halfLength = 8.75`):

| | old mother half-length | new mother half-length | filter absolute z | frame absolute z |
|---|---|---|---|---|
| nominal | 9.35 | 7.45 | unchanged | unchanged |
| v40 | 24.85 | 15.20 | unchanged | unchanged |

`old = 2·filter_hl + frame_hl + 1`, `new = filter_hl + frame_hl + 0.1`. The
filter and frame land at identical absolute z before and after in both cases —
only the mother box shrinks and re-centres around them. The oversize is 1.9 mm
in nominal and 9.65 mm with v40's 1.75 cm plate, which is why the overlap only
bites on v40.

So PR2 **cannot** claim nominal is byte-identical. It must claim, and
demonstrate, a *bounded* diff: `degraderOutline`'s box dimensions and the
`degraderMother` placement change; every other volume in the nominal dump is
untouched, and the filter, frame and rod do not move. That is a stronger claim
than "nothing changed" because it is checkable line by line.

Every `degrader.supportArm.*` key v40 overrides is already read by
`MECOStyleProtonAbsorberMaker.cc:444-456` with defaults, so no new
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
- **v02–v06 disappear from main, and Run1Baa–Run1Bai become non-reproducible
  against it.** The archive tag is the only remaining path, which is why it is
  mandatory rather than optional.
- **The freeze fails silently, and that is the sharpest risk here.** A v01 entry
  re-run against main still finds its geometry file, then builds a world with
  `tracker.inDS2Vacuum` ignored and an undersized stopping-target mother. No
  error, wrong answer. Mitigation is documentation at the point of use — a
  `data/Run1B/README.md` naming the frozen campaigns and the archive tag — plus
  the wiki page. A machine-readable gate in prodtools that refuses to submit a
  frozen entry would be better and is recorded as a follow-up; it is not part of
  this consolidation.

## Pre-flight findings (2026-08-09)

Workspace: `$WS/Offline` at HEAD `f14370362` on `main` (built, ENVSET
`al9-prof-e29-p103`). All checks below are read-only against that clone plus a
shallow clone of `Mu2e/EventNtuple`; nothing was modified in either.

**Baselines captured.** Both match Task 1's dumps and each other's determinism
proof — the normalized-gdml gate is sound before PR1 lands anything.

| File | `<volume` entries | residual `0x` |
|---|---|---|
| `baseline_common.gdml.norm` (`geom_common.txt` via `gdmldump.fcl`) | 13767 | 0 |
| `baseline_run1_a.gdml.norm` (`geom_run1_a.txt` via `gdmldump_run1_a.fcl`) | 14355 | 0 |

`baseline_common` reproduces Task 1's 13767/0 exactly — the geometry is
reproducible run to run, so the plan's gate stands.

**Step 2 — does anything size itself off `lastEnum`?** `grep -rn "lastEnum"
--include=*.cc --include=*.hh . | grep -v VirtualDetectorId.hh` in
`$WS/Offline` returns ~45 hits, but most belong to unrelated enum classes that
share the same "unknown/lastEnum" codegen idiom — `GenId`, `ProcessCode`,
`StepInstanceName`, `StepFilterMode`, `STMChannel`, `BFMapType`,
`CompressionLevel`. None of those resolve to `VirtualDetectorId::lastEnum`;
appending 22 VD enumerators does not touch them.

The hits that do resolve to `VirtualDetectorId::lastEnum`:

- `DataProducts/src/VirtualDetectorId.cc:49` — `printAll()`'s
  `for (int i=0;i<lastEnum;++i)`. Bounded by `lastEnum`, grows harmlessly.
- `DataProducts/src/VirtualDetectorId.cc:65` —
  `BOOST_STATIC_ASSERT(sizeof(tmp)/sizeof(char*) == lastEnum)`, checking the
  `VIRTUALDETECTORID_NAMES` macro (defined `DataProducts/inc/VirtualDetectorId.hh:95`)
  against `lastEnum`. Not a silent hazard — a compile-time consistency check.
  It does mean Task 3 must add 22 name strings to `VIRTUALDETECTORID_NAMES`
  alongside the 22 new enumerators, or the build fails loudly at this assert.
  Recorded as a required companion edit, not a defect.
- `DataProducts/src/VirtualDetectorId.cc:66` —
  `nam.insert(nam.begin(), tmp, tmp+lastEnum)`. Grows in lockstep with the
  assert above; harmless once the names macro is extended.
- `Analyses/src/ReadVirtualDetector_module.cc:41` —
  `const unsigned int nvdet = VirtualDetectorId::lastEnum;`, then used at
  lines 98–110 to dimension fixed-size C arrays inside a ROOT TTree struct
  (`Bool_t isvd[nvdet]`, `Float_t tvd[nvdet]`, etc.) and at line 946 to
  bounds-check `id > nvdet`. Dimensioned off `lastEnum` itself, not a literal
  — grows harmlessly and recompiles correctly at the wider size.

**Verdict:** no fixed-size array dimensioned on a literal, no persisted
format bakes in a width, no switch/lookup assumes a maximum id. One required
companion edit for Task 3 (extend `VIRTUALDETECTORID_NAMES`), enforced
fail-loud by a `BOOST_STATIC_ASSERT` rather than posing a silent risk. Clear
to proceed with PR1.

**Step 3 — does anything reference VD ids numerically?** Shallow-cloned
`Mu2e/EventNtuple` into `$WS/EventNtuple`.

`grep -rn "VirtualDetectorId\|vdid\|vd_id" EventNtuple/src EventNtuple/inc`:

- `EventNtuple/src/InfoMCStructHelper.cc:13,36-41` — builds
  `_vdmap` (`std::map<VirtualDetectorId,SurfaceId>`) keyed by symbolic names
  (`TT_FrontHollow`, `TT_Mid`, `TT_MidInner`, `TT_Back`, `TT_OutSurf`,
  `TT_InSurf`). Symbolic, not numeric — unaffected by appending enumerators
  before `lastEnum`.
- `EventNtuple/src/InfoMCStructHelper.cc:315,323` — range-for over
  `kseedmc._vdsteps` (a container, not a fixed array), storing
  `vdstep._vdid.id()` into a struct field. No hardcoded count or range.
- `EventNtuple/inc/InfoMCStructHelper.hh:50-51` — declares `_vdmap`; no count
  assumption.

`EventNtuple/fcl/from_mcs-Run1B.fcl` and its ancestor
`from_mcs-primary_addVDSteps.fcl` only set
`stepPointMCTags: ["compressRecoMCs:virtualdetector"]` — a string instance-name
tag, no numeric VD id anywhere.

**Verdict:** no hardcoded count, no numeric id literal, no fixed-range
iteration found in EventNtuple. Clear to proceed with PR1.

**Step 4 — was #1849's `trigger` failure ours?** Answered without
re-investigation: PRs #1844 and #1846, tested in the same June 2026 window as
#1849, both show `trigger` failing (`:x:`) in their FNALbuild comments against
main. The #1849 `trigger` RC 2 was pre-existing breakage on main, not caused
by the Run1B changes.

**Net:** all three pre-flight questions are answered clear. Nothing here
blocks PR1; the one actionable item (extend `VIRTUALDETECTORID_NAMES`) is a
required step of Task 3's own enum edit, not a new risk it introduces.
