# Run1B Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Run1B production geometry on `Mu2e/Offline` main without changing what main does by default, then archive and retire the branch.

**Architecture:** Two sequential pull requests. PR1 ships the v40 geometry files and enum identifiers only, which repairs a dangling cross-repo reference immediately. PR2 ships the degrader placement fix and the EMC source virtual detectors. A normalized GDML dump diff is the regression gate on both.

**Tech Stack:** C++17, Geant4, art/fhicl, SimpleConfig geometry text files, Muse build system, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md`

## Global Constraints

- `Mu2eG4/geom/geom_common.txt` must **never** be modified. It keeps including `geom_run1_a_stickman.txt`.
- `Mu2eG4/geom/geom_run1_b_v01.txt` must **never** be modified. It is already on main and is the declared default inside a live Production fcl.
- `geom_run1_b_v02.txt` through `v06.txt` are **not ported**. They are the superseded single-disk modelling line with no consumers.
- No new configuration keys may be introduced. Use `SimpleConfig::hasName()` key-presence for the EMC virtual detectors.
- After every PR, the normalized GDML dump of `geom_common.txt` and of `geom_run1_a.txt` must be byte-identical to the pre-change baseline.
- All work happens in `Mu2e/Offline`. Branch from `main`, never from `Run1B`.
- Build environment is whatever `.muse` declares (`ENVSET p103` as of 2026-08-08). Do not pin a different one.
- Do not run `voms-proxy-init`. GDML dumps need no grid credentials.
- `muse setup` clobbers `$REPO`; assign it after setup if needed.
- Commit messages end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Responsibility | PR |
|---|---|---|
| `Mu2eG4/geom/geom_run1_b_v40.txt` | Run1B production geometry: 37 foils + TS5 plate + mobile degrader target | 1 |
| `Mu2eG4/geom/geom_run1_b_ds_on_v40.txt` | v40 with the mobile target rotated out of the beam | 1 |
| `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl` | GDML dump driver for v40 (new — not on the branch) | 1 |
| `DataProducts/inc/VirtualDetectorId.hh` | VD identifiers 117–138 | 1 |
| `Mu2eG4/src/constructProtonAbsorber.cc` | degrader mother/filter/frame layout | 2 |
| `GeometryService/src/VirtualDetectorMaker.cc` | EMC source VD registration, key-presence gated | 2 |
| `Mu2eG4/src/constructVirtualDetectors.cc` | EMC source VD placement (that block only) | 2 |

`gdml_baseline.sh` is a throwaway harness kept in the scratch directory, never committed.

---

### Task 1: Build the workspace and prove the GDML harness is deterministic

The whole plan rests on "normalized GDML dumps are comparable." Geant4's writer appends pointer addresses to volume names, which differ run to run. Prove the normalization works before trusting it as a gate.

**Files:**
- Create: `$WS/gdml_baseline.sh` (harness, never committed)

**Interfaces:**
- Produces: `gdml_baseline.sh <fcl> <name>` writing `$WS/gdml/<name>.gdml.norm`

- [ ] **Step 1: Set up a clean Offline clone on main**

```bash
export WS=/exp/mu2e/data/users/oksuzian/claude-scratch/run1b
mkdir -p $WS && cd $WS
git clone https://github.com/Mu2e/Offline
cd Offline && git fetch origin Run1B:Run1B && git checkout main
git log --oneline -1
grep ENVSET .muse
```

Expected: HEAD is main's tip; `.muse` declares `ENVSET p103`.

- [ ] **Step 2: Build**

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
cd $WS && muse setup && muse build -j 20
```

Expected: clean build. Roughly 10 minutes.

- [ ] **Step 3: Write the normalization harness**

```bash
mkdir -p $WS/gdml
cat > $WS/gdml_baseline.sh <<'SH'
#!/bin/bash
# Usage: gdml_baseline.sh <fcl-path> <output-name>
# Runs a GDML dump in an empty directory, strips Geant4 pointer suffixes,
# and writes $WS/gdml/<output-name>.gdml.norm
set -euo pipefail
fcl="$1"; name="$2"
work=$(mktemp -d)
pushd "$work" >/dev/null
mu2e -c "$fcl" -n 1 >"$name.log" 2>&1 || { echo "RUN FAILED: see $work/$name.log"; exit 1; }
gdml=$(ls *.gdml 2>/dev/null | head -1)
[ -n "$gdml" ] || { echo "NO GDML PRODUCED: see $work/$name.log"; exit 1; }
# Geant4 appends 0x<address> to solid/volume/material names when
# storeReferences is true (Mu2eWorld.cc:326 calls parser.Write with the default).
sed -E 's/0x[0-9a-f]{6,}//g' "$gdml" > "$WS/gdml/$name.gdml.norm"
cp "$name.log" "$WS/gdml/$name.log"
popd >/dev/null
echo "wrote $WS/gdml/$name.gdml.norm ($(wc -l < "$WS/gdml/$name.gdml.norm") lines)"
SH
chmod +x $WS/gdml_baseline.sh
```

Note the harness copies the run log next to the dump — later tasks grep it for overlap warnings.

- [ ] **Step 4: Prove determinism — dump the same geometry twice, unchanged**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl determinism_a
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl determinism_b
diff gdml/determinism_a.gdml.norm gdml/determinism_b.gdml.norm && echo "HARNESS OK"
```

Expected: `HARNESS OK`. If the diff is non-empty the normalization is insufficient — inspect the differing lines and extend the `sed`. **Do not proceed with a non-deterministic harness.** Everything downstream is meaningless without it.

- [ ] **Step 5: Confirm the normalization is not over-aggressive**

```bash
grep -c "0x" $WS/gdml/determinism_a.gdml.norm || echo "no raw pointers remain (expected)"
grep -c "<volume" $WS/gdml/determinism_a.gdml.norm
```

Expected: zero remaining `0x` matches, and a volume count in the thousands. A count near zero means the sed ate real content.

---

### Task 2: Capture baselines and run the pre-flight risk checks

**Files:** none modified — produces artifacts and answers three questions that gate PR1.

**Interfaces:**
- Produces: `$WS/gdml/baseline_common.gdml.norm`, `$WS/gdml/baseline_run1_a.gdml.norm`

- [ ] **Step 1: Capture the two nominal baselines**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        baseline_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl baseline_run1_a
cp gdml/baseline_*.gdml.norm $WS/   # keep a copy outside the working dir
```

These two files are the contract for the rest of the plan.

- [ ] **Step 2: Risk check — does anything size itself off `lastEnum`?**

```bash
cd $WS/Offline
grep -rn "lastEnum" --include=*.cc --include=*.hh . | grep -v VirtualDetectorId.hh
```

Read each hit. A loop bounded by `lastEnum` grows harmlessly. A **fixed-size array or persisted numeric width** does not. Record the verdict for the PR1 description.

- [ ] **Step 3: Risk check — does anything reference VD ids numerically?**

```bash
cd $WS
git clone --depth 1 https://github.com/Mu2e/EventNtuple
grep -rn "VirtualDetectorId\|vdid\|vd_id" EventNtuple/src EventNtuple/inc 2>/dev/null | head -30
cat EventNtuple/fcl/from_mcs-Run1B.fcl
```

The 22 new enums are appended *before* `lastEnum`, so existing ids keep their numbers and even numeric references stay valid. What you are looking for is a hardcoded *count*.

- [ ] **Step 4: Determine whether #1849's `trigger` failure was ours**

```bash
export GH_CONFIG_DIR=${GH_CONFIG_DIR:-$HOME/.config/gh}
gh api repos/Mu2e/Offline/commits/83b5e2ff2d44af8790386b1abb702433a0b148ed/statuses \
  -q '.[] | "\(.context)  \(.state)  \(.created_at)"' | sort -u
```

`83b5e2f` is the main commit #1849 was tested against. If main was already failing `trigger` there, the failure was never ours.

- [ ] **Step 5: Record the findings in the spec**

Append a "Pre-flight findings" section to `docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md` with the three answers, then:

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
git add docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md
git commit -m "docs(spec): record Run1B pre-flight risk-check findings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: PR1 — Run1B production geometry and VD identifiers

Configuration and identifiers only. No code. This is the PR that repairs `Production/Tests/Run1BReco.fcl` and the prodtools v40 templates.

**Files:**
- Create: `Mu2eG4/geom/geom_run1_b_v40.txt`, `Mu2eG4/geom/geom_run1_b_ds_on_v40.txt`
- Create: `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl`
- Modify: `DataProducts/inc/VirtualDetectorId.hh`

**Interfaces:**
- Produces: `VirtualDetectorId::EMC_Source` (117), `EMC_Source2` (118), `EMC_0_Front` (119), `Tracker_FEB_0..18_SurfIn` (120–138), and `bool VirtualDetectorId::isFEBTracker() const`. Task 5 relies on the first three.
- Produces: `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl`, used as a gate in Tasks 4, 5 and 6.

- [ ] **Step 1: Branch and take the four files**

```bash
cd $WS/Offline && git checkout main && git pull && git checkout -b run1b-v40-geometry
git checkout Run1B -- Mu2eG4/geom/geom_run1_b_v40.txt
git checkout Run1B -- Mu2eG4/geom/geom_run1_b_ds_on_v40.txt
git checkout Run1B -- DataProducts/inc/VirtualDetectorId.hh
git status --short
```

Expected: exactly three paths staged. **`geom_run1_b_v01.txt` must not appear** — the branch modifies it, and modifying it is forbidden by the global constraints.

- [ ] **Step 2: Write the v40 dump driver**

This file does not exist on the branch. Create `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl`:

```
#include "Offline/Mu2eG4/fcl/gdmldump.fcl"
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/geom_run1_b_v40.txt"
physics.producers.g4run.debug.GDMLFileName: "mu2e_run1_b_v40.gdml"
```

- [ ] **Step 3: Verify every include resolves on main**

```bash
cd $WS/Offline
grep -h '^#include' Mu2eG4/geom/geom_run1_b_v40.txt Mu2eG4/geom/geom_run1_b_ds_on_v40.txt \
  | sed 's/.*"Offline\/\(.*\)".*/\1/' | sort -u | while read p; do
  [ -f "$p" ] && echo "OK   $p" || echo "MISS $p"
done
```

Expected: every line `OK`. v40 includes `geom_run1_a.txt`; ds_on_v40 includes v40.

- [ ] **Step 4: Verify v40 keeps the 37-foil target**

```bash
cd $WS/Offline
grep -n "stoppingTarget" Mu2eG4/geom/geom_run1_b_v40.txt || echo "no stoppingTarget overrides (expected)"
grep -n "tsda\.\|degrader\." Mu2eG4/geom/geom_run1_b_v40.txt
```

Expected: **no `stoppingTarget` overrides at all**, so the 37 foils and their support structure are inherited from `geom_run1_a.txt` untouched — this is what makes v40 correct where v01–v06 were not, and why none of the stopping-target code changes are needed. The TS5 plate arrives through `tsda.*` and the mobile target through `degrader.*`.

- [ ] **Step 5: Verify the enum is purely additive**

```bash
cd $WS/Offline
git diff main -- DataProducts/inc/VirtualDetectorId.hh | grep "^-" | grep -v "^---"
```

Expected: the only `-` lines are the `"STM_UpStrLarge"` string-list entry and `lastEnum`, both reappearing as `+` lines with a trailing comma or backslash. **No existing enumerator may change position.**

- [ ] **Step 6: Build**

```bash
cd $WS && muse build -j 20
```

- [ ] **Step 7: Confirm nominal is untouched**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        pr1_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl pr1_run1_a
diff gdml/baseline_common.gdml.norm gdml/pr1_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/pr1_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`. PR1 changes no code, so any difference means the enum addition leaked into geometry.

- [ ] **Step 8: Commit and open PR1**

```bash
cd $WS/Offline
git add -A && git commit -m "feat(geom): add Run1B production geometry and virtual detector ids

Adds geom_run1_b_v40.txt and its DS-on counterpart, a gdml dump driver
for v40, and virtual detector identifiers 117-138.

Configuration and identifiers only -- no code, and geom_common.txt is
untouched, so nominal running is unaffected. Verified by gdml dumps of
geom_common.txt and geom_run1_a.txt being byte-identical to main after
normalization.

Repairs a dangling cross-repo reference: Production main's
Tests/Run1BReco.fcl already points at geom_run1_b_v40.txt.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git push -u origin run1b-v40-geometry
gh pr create -R Mu2e/Offline --base main --title "Run1B production geometry and virtual detector ids" --body "$(cat <<'EOF'
First of two PRs consolidating the Run1B production geometry into main.
See docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md.

Configuration and identifiers only. No code. `geom_common.txt` is
deliberately **not** changed -- main keeps `geom_run1_a_stickman.txt` as
its default geometry, addressing the review comment on #1849.

**Why now:** Production main's `Tests/Run1BReco.fcl` and the prodtools
Run1B digi/reco templates already reference
`Offline/Mu2eG4/geom/geom_run1_b_v40.txt`, which exists only on the
`Run1B` branch. Reconstruction builds GeometryService but not the G4
world, so those paths work as soon as this lands.

**Only v40 is ported.** Run1B has two stopping targets: the thin 37-foil
target shared with Run1A, and a disk at the face of TS5. v40 models this
correctly -- it inherits `geom_run1_a.txt` untouched and adds the TS5
plate via `tsda.*` plus the degrader as the mobile target. The earlier
v01-v06 line instead overrode `stoppingTarget.radii` to a single 600 mm
disk, replacing the foil target rather than adding to it. v40 superseded
that line and is what the Run1Bak and Run1Ban campaigns run; v02-v06
have no consumers and are not ported.

**Nominal impact:** none. Normalized gdml dumps of `geom_common.txt` and
`geom_run1_a.txt` are byte-identical to main.

**Enum safety:** the 22 new identifiers are appended before `lastEnum`;
no existing enumerator changes position.
EOF
)"
```

---

### Task 4: PR2 commit 1 — degrader filter and frame placement

**Files:**
- Modify: `Mu2eG4/src/constructProtonAbsorber.cc`

**Interfaces:**
- Consumes: `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl` from Task 3.

- [ ] **Step 1: Branch from main after PR1 has merged**

```bash
cd $WS/Offline && git checkout main && git pull && git checkout -b run1b-degrader-and-emc-vds
```

- [ ] **Step 2: Confirm the code is unreachable in nominal**

```bash
cd $WS/Offline
grep -rn "degrader.build" Mu2eG4/geom/*.txt
grep -n "supportArm" GeometryService/src/MECOStyleProtonAbsorberMaker.cc | head
```

Expected: `degrader_v02.txt` sets `degrader.build = false` ("off by default") and only `geom_run1_b_v40.txt` sets it `true`. That is why this can land unconditionally. Also expected: every `degrader.supportArm.*` key is read with a default at `MECOStyleProtonAbsorberMaker.cc:444-456`, so v40's overrides need no new plumbing.

- [ ] **Step 3: Capture the pre-change v40 geometry**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v40.fcl v40_before_degrader
grep -ic "overlap" gdml/v40_before_degrader.log || echo "no overlaps"
```

Record the overlap count. This is the "before" that the fix has to improve on — if it is already zero, the fix is not doing what the commit message will claim, and you should find out why before proceeding.

- [ ] **Step 4: Port the change**

```bash
cd $WS/Offline && git checkout Run1B -- Mu2eG4/src/constructProtonAbsorber.cc
```

The rewrite replaces a layout where the degrader mother's half-width and its children's z offsets disagreed. It places the filter at the mother's upstream edge:

```cpp
        const double dgr_z0 = pabs->degraderZ0(); // front face of the degrader
        const double mother_half_width = filterDims.at(2) + frameDims.at(2) + 0.1;
        CLHEP::Hep3Vector locationInMu2e (xLocMoBox, yLocMoBox, dgr_z0 + mother_half_width);
```

and the frame directly downstream of it, with the rod sharing the frame's z.

- [ ] **Step 5: Build and confirm nominal unchanged**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        deg_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl deg_run1_a
diff gdml/baseline_common.gdml.norm gdml/deg_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/deg_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`. The degrader is not built in either, so the rewritten code never runs. A diff here means `degrader.build` is true somewhere unexpected — find it before proceeding.

- [ ] **Step 6: Confirm the fix improves v40**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v40.fcl v40_after_degrader
grep -ic "overlap" gdml/v40_after_degrader.log || echo "no overlaps"
diff gdml/v40_before_degrader.gdml.norm gdml/v40_after_degrader.gdml.norm | head -40
```

Expected: overlap count drops to zero, and the diff shows `degraderFilter`, `degraderFrame`, `degraderRod` and the mother box moving. Those are the only volumes that may change.

- [ ] **Step 7: Commit**

```bash
cd $WS/Offline
git add Mu2eG4/src/constructProtonAbsorber.cc
git commit -m "fix(Mu2eG4): correct degrader filter and frame placement

The degrader mother half-width and its children's z offsets disagreed,
leaving the filter partly outside its mother. Places the filter at the
mother's upstream edge and the frame directly downstream, sizing the
mother to contain both.

Unreachable in nominal running: degrader.build is false by default and
true only in geom_run1_b_v40.txt, which uses the degrader as the Run1B
mobile stopping target. Nominal verified unchanged by gdml dump.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: PR2 commit 2 — EMC source virtual detectors

**Files:**
- Modify: `GeometryService/src/VirtualDetectorMaker.cc`
- Modify: `Mu2eG4/src/constructVirtualDetectors.cc`
- Modify: `Mu2eG4/geom/geom_run1_b_v40.txt`

**Interfaces:**
- Consumes: `VirtualDetectorId::EMC_Source`, `EMC_Source2`, `EMC_0_Front` from Task 3.

- [ ] **Step 1: Register each VD only when its z is configured**

In `VirtualDetectorMaker.cc`, add — using key presence rather than the branch's hardcoded defaults:

```cpp
      // These virtual detectors exist only where a geometry positions them.
      // A hardcoded default would silently add them to every geometry,
      // including MDC configurations that never asked for them.
      if ( c.hasName("zEMCSourceInMu2e") ) {
        const double zVDcenterInMu2e = c.getDouble("zEMCSourceInMu2e");
        Hep3Vector posEMCSource(0., 0., zVDcenterInMu2e-ds2centerInMu2e.z());
        vd->addVirtualDetector( VirtualDetectorId::EMC_Source,
                                ds2centerInMu2e, 0, posEMCSource);
      }
      if ( c.hasName("zEMCSource2InMu2e") ) {
        const double zVD2centerInMu2e = c.getDouble("zEMCSource2InMu2e");
        Hep3Vector posEMCSource2(0., 0., zVD2centerInMu2e-ds2centerInMu2e.z());
        vd->addVirtualDetector( VirtualDetectorId::EMC_Source2,
                                ds2centerInMu2e, 0, posEMCSource2);
      }
      if ( c.hasName("zEMC0Front") ) {
        const double zVDEMC0Front = c.getDouble("zEMC0Front");
        Hep3Vector posEMC0Front(0., 0., zVDEMC0Front-ds2centerInMu2e.z());
        vd->addVirtualDetector( VirtualDetectorId::EMC_0_Front,
                                ds2centerInMu2e, 0, posEMC0Front);
      }
```

`SimpleConfig::hasName()` is declared at `ConfigTools/inc/SimpleConfig.hh:95`.

**Do not** port the branch's commenting-out of `STMUpstream` in this file, and do not port its parent-volume rework. Neither is needed: v40 inherits `hasSTM = true` and does not use `tracker.inDS2Vacuum`.

- [ ] **Step 2: Port the placement block only**

```bash
cd $WS/Offline
git diff main Run1B -- Mu2eG4/src/constructVirtualDetectors.cc | grep -n "EMC_Source\|EMC_0_Front"
```

Port the `EMC_Source` / `EMC_Source2` / `EMC_0_Front` placement code and nothing else from this file. It is already guarded by `vdg->exist(vdId)`, so Step 1's registration gate governs it automatically.

- [ ] **Step 3: Move the TSdA lookup inside the existence guard**

The ported block reads `_config.getDouble("TSdA.rFactorForVDs")` with no default whenever `hasTSdA` is true, *before* the `vdg->exist(vdId)` guard. v40 sets `hasTSdA = true`. Confirm the key is reachable, then make it unconditional-safe anyway:

```bash
cd $WS/Offline
grep -rn "rFactorForVDs" Mu2eG4/geom/
head -6 Mu2eG4/geom/TSdA_v02.txt
```

Expected: defined as `650.` in `TSdA_v01.txt`, and `TSdA_v02.txt` includes `TSdA_v01.txt`, so every geometry using either has it. Move the lookup inside `if ( vdg->exist(vdId) )` regardless — a geometry that sets `hasTSdA = true` without including a TSdA version file would otherwise throw during VD construction, which is a needlessly fragile coupling.

- [ ] **Step 4: Give v40 the VD positions it already has**

v40 descends from `geom_run1_a.txt` and never sets these keys, so on the branch it receives them at the hardcoded defaults. Under key-presence gating it would lose them, silently changing production output. Write those same values explicitly into `Mu2eG4/geom/geom_run1_b_v40.txt`:

```
// Positions of the EMC source virtual detectors. These reproduce the
// hardcoded defaults this geometry has been receiving; whether they are
// the right positions for v40 is a separate physics question -- v40 keeps
// geom_run1_a.txt's mu2e.detectorSystemZ0 = 10171, while these values were
// chosen against v01's 7000.
double zEMCSourceInMu2e  = 5300.;
double zEMCSource2InMu2e = 4800.;
double zEMC0Front        = 5830.;
```

Do **not** substitute v01's `zEMC0Front = 8695.` here. v01 overrides it; v40 has been running with `5830`, and this commit preserves behavior rather than changing it.

- [ ] **Step 5: Build and confirm nominal gained nothing**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        emc_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl emc_run1_a
diff gdml/baseline_common.gdml.norm gdml/emc_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/emc_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
grep -c "EMC_Source" gdml/emc_common.gdml.norm || echo "no EMC source VDs in nominal (expected)"
```

Expected: both `UNCHANGED`, and no `EMC_Source` volumes in the nominal dump. Neither nominal geometry sets the keys.

- [ ] **Step 6: Confirm v40 has them**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v40.fcl emc_run1_b_v40
grep -c "EMC_Source\|EMC_0_Front" gdml/emc_run1_b_v40.gdml.norm
grep -ic "overlap" gdml/emc_run1_b_v40.log || echo "no overlaps"
```

Expected: a non-zero count and no overlaps.

- [ ] **Step 7: Commit**

```bash
cd $WS/Offline
git add GeometryService/src/VirtualDetectorMaker.cc Mu2eG4/src/constructVirtualDetectors.cc Mu2eG4/geom/geom_run1_b_v40.txt
git commit -m "feat(GeometryService): add EMC source virtual detectors where configured

Adds EMC_Source, EMC_Source2 and EMC_0_Front, placed only when the
geometry sets zEMCSourceInMu2e, zEMCSource2InMu2e or zEMC0Front. Key
presence is the switch, so no geometry gains virtual detectors it did
not ask for -- addressing the review comment on #1849 about hardcoded
default z values.

geom_run1_b_v40.txt now sets those keys explicitly to the values it was
previously receiving from the hardcoded defaults, so its output is
unchanged. Whether those are the right positions for v40 is a separate
physics question and deliberately not settled here.

Nominal verified unchanged by gdml dump; neither geom_common.txt nor
geom_run1_a.txt sets these keys.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: PR2 — fidelity check and open the PR

The check that proves consolidation reproduced production rather than approximating it.

- [ ] **Step 1: Dump v40 from the Run1B branch**

The branch has no v40 dump driver, so supply one:

```bash
cd $WS/Offline && git checkout Run1B && cd $WS && muse build -j 20
cat > /tmp/gdmldump_run1_b_v40.fcl <<'EOF'
#include "Offline/Mu2eG4/fcl/gdmldump.fcl"
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/geom_run1_b_v40.txt"
physics.producers.g4run.debug.GDMLFileName: "mu2e_run1_b_v40.gdml"
EOF
./gdml_baseline.sh /tmp/gdmldump_run1_b_v40.fcl fidelity_branch_v40
```

- [ ] **Step 2: Dump v40 from the consolidated branch**

```bash
cd $WS/Offline && git checkout run1b-degrader-and-emc-vds && cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v40.fcl fidelity_consolidated_v40
```

- [ ] **Step 3: Compare**

```bash
cd $WS
diff gdml/fidelity_branch_v40.gdml.norm gdml/fidelity_consolidated_v40.gdml.norm && echo "FIDELITY CONFIRMED"
```

Expected: `FIDELITY CONFIRMED`. v40 uses none of the code paths that were deliberately left behind — it inherits `hasSTM = true`, keeps the 37-foil target, and has `tracker.inDS2Vacuum` commented out — so a difference here means something that v40 *does* use was missed. Investigate before opening the PR; this is the last gate.

- [ ] **Step 4: Check ds_on_v40 as well**

```bash
cd $WS
cat > /tmp/gdmldump_ds_on_v40.fcl <<'EOF'
#include "Offline/Mu2eG4/fcl/gdmldump.fcl"
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/geom_run1_b_ds_on_v40.txt"
physics.producers.g4run.debug.GDMLFileName: "mu2e_ds_on_v40.gdml"
EOF
./gdml_baseline.sh /tmp/gdmldump_ds_on_v40.fcl consolidated_ds_on_v40
grep -ic "overlap" gdml/consolidated_ds_on_v40.log || echo "no overlaps"
```

ds_on_v40 is v40 with `degrader.rotation = 120.0`, moving the mobile target out of the beam. Expected: builds, no overlaps. It exercises the degrader fix at a different rotation, which is the case most likely to expose a sign error in the new z arithmetic.

- [ ] **Step 5: Final nominal check**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        pr2_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl pr2_run1_a
diff gdml/baseline_common.gdml.norm gdml/pr2_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/pr2_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

- [ ] **Step 6: Push and open PR2**

```bash
cd $WS/Offline
git push -u origin run1b-degrader-and-emc-vds
gh pr create -R Mu2e/Offline --base main --title "Run1B: degrader placement and EMC source virtual detectors" --body "$(cat <<'EOF'
Second and final PR consolidating the Run1B production geometry into
main. See docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md.

**Nominal unchanged**, verified by normalized gdml dumps of
`geom_common.txt` and `geom_run1_a.txt` being byte-identical to main
after every commit. `geom_common.txt` is never touched.

**Fidelity confirmed:** a normalized gdml dump of `geom_run1_b_v40.txt`
built from this branch is identical to one built from the `Run1B`
branch. Consolidation reproduced the production geometry, it did not
approximate it.

Two commits:

1. **Degrader placement.** The mother half-width and its children's z
   offsets disagreed, leaving the filter partly outside its mother.
   Unreachable in nominal running -- `degrader.build` is false by
   default and true only in v40, which uses the degrader as the Run1B
   mobile target.

2. **EMC source virtual detectors,** placed only where a geometry sets
   their z, so no configuration gains detectors it did not ask for --
   the review comment on #1849 about hardcoded default z values. v40
   sets them explicitly to the values it was already receiving, so its
   output is unchanged. Whether those are the right positions for v40 is
   a separate physics question, deliberately not settled here.

**Not ported, and why:** the stopping-target radius change, the
`STMUpstream` gating, the DS2Vacuum parent-volume rework and the TSdA
cutout options all existed to support the v01-v06 single-disk
geometries, which v40 superseded and which have no consumers. v40 keeps
the 37-foil target, inherits `hasSTM = true`, and has
`tracker.inDS2Vacuum` commented out.

After this merges the `Run1B` branch is archived under a tag and
deleted.
EOF
)"
```

---

### Task 7: Archive and retire the Run1B branch

Only after PR2 has merged. Unlike a full consolidation, main will **not** contain v02–v06 or the un-ported code, so the branch cannot be deleted as redundant — it must be archived first.

- [ ] **Step 1: Confirm main has everything production runs**

```bash
cd $WS/Offline && git checkout main && git pull && git fetch origin Run1B:Run1B
for f in geom_run1_b_v01.txt geom_run1_b_v40.txt geom_run1_b_ds_on_v40.txt; do
  [ -f "Mu2eG4/geom/$f" ] && echo "OK $f" || echo "MISSING $f"
done
git diff main Run1B --stat
```

Expected: all three `OK`. The `git diff` will list `geom_common.txt`, v02–v06 and the un-ported source changes — that is intended, not a failure. What must **not** appear is any change to v40, ds_on_v40, `constructProtonAbsorber.cc`, or the EMC VD code.

- [ ] **Step 2: Run the Run1B reconstruction smoke**

```bash
cd $WS && mu2e -c Production/Tests/Run1BReco.fcl -n 1
```

Expected: exit status 0. Needs a Production checkout alongside Offline in the Muse workspace.

- [ ] **Step 3: Archive the branch under a tag**

```bash
cd $WS/Offline
git tag -a run1b-archive-2026-08-08 Run1B -m "Archive of the Run1B branch at retirement.

Production geometry (v40, ds_on_v40) and the code it needs were
consolidated into main. The v01-v06 single-disk stopping-target
geometries were superseded by v40 and are preserved here rather than on
main. See docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md."
git push origin run1b-archive-2026-08-08
```

**This tag is mandatory, not optional.** It is the only remaining copy of v02–v06.

- [ ] **Step 4: Delete the branch**

```bash
export GH_CONFIG_DIR=${GH_CONFIG_DIR:-$HOME/.config/gh}
gh api repos/Mu2e/Offline/git/refs/tags/run1b-archive-2026-08-08 -q '.object.sha'
gh api -X DELETE repos/Mu2e/Offline/git/refs/heads/Run1B
```

Confirm the tag resolves **before** deleting the branch.

- [ ] **Step 5: Announce**

State on the relevant Mu2e channel that: main's default geometry is deliberately still Run1A; Run1B production is selected by naming `geom_run1_b_v40.txt`; and the superseded v02–v06 study geometries live under `run1b-archive-2026-08-08`.

- [ ] **Step 6: Record the outcome in the prodtools wiki**

Write `wiki/pages/2026-XX-XX-run1b-consolidation.md` covering what landed in each PR, that `geom_common.txt` still points at Run1A, the gdml-diff method, the v40-versus-v01–v06 stopping-target reasoning, and the archive tag.

- [ ] **Step 7: Note the optional Production follow-up**

`Production/JobConfig/recoMC/NoFieldRun1B.fcl` defaults to `geom_run1_b_v01.txt` and is overridden to v40 by every prodtools caller. Changing that default to v40 would remove the last live reference to the superseded line. Separate repo, separate PR, not part of this consolidation — record it as a follow-up rather than doing it here.

---

## Self-Review

**Spec coverage.** PR1 contents → Task 3. Degrader → Task 4. EMC VD gating and v40 z values → Task 5. Fidelity check → Task 6. Branch archive and retirement → Task 7. Testing strategy → Task 1 (harness), Task 2 (baselines), gdml checks in every subsequent task. Risks: `lastEnum` → Task 2 Step 2; numeric VD ids → Task 2 Step 3; `trigger` RC 2 → Task 2 Step 4; v02–v06 recoverability → Task 7 Step 3.

**Scope revision folded in.** Earlier drafts of this plan covered three PRs and thirteen tasks, porting v01–v06 along with the stopping-target radius fix, `STMUpstream` gating, the DS2Vacuum parent-volume rework and its whitespace split. All of that existed to support the single-disk geometries. Since Run1B keeps the 37-foil target shared with Run1A and adds the TS5 disk separately — which only v40 models, and only v40 has consumers — that work has no live consumer and is not ported. The spec has been rewritten to match; there is no remaining disagreement between the two documents.

**Placeholder scan.** No deferred values. The EMC z positions are fixed at the values v40 already receives (`5300 / 4800 / 5830`), which makes the commit behavior-preserving; the open physics question about whether those are correct for v40 is recorded in the spec and the PR body as explicitly out of scope, not as a blocking TBD.

**Consistency.** Config key names (`zEMCSourceInMu2e`, `zEMCSource2InMu2e`, `zEMC0Front`, `degrader.build`, `degrader.rotation`, `hasTSdA`, `TSdA.rFactorForVDs`, `TrackerHasBrassRings`) are spelled identically across tasks and match the source. The harness contract `gdml_baseline.sh <fcl> <name>` → `$WS/gdml/<name>.gdml.norm` plus `$WS/gdml/<name>.log` is used consistently in all 18 invocations, and Task 4 Step 3 and Task 5 Step 6 rely on the `.log` copy that Task 1 Step 3 creates. Branch names `run1b-v40-geometry` and `run1b-degrader-and-emc-vds` are consistent between creation, push and checkout.
