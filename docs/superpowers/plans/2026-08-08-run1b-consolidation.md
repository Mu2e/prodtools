# Run1B Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Run1B branch's 18-file delta on `Mu2e/Offline` main without changing what main does by default, then retire the branch.

**Architecture:** Three sequential pull requests. PR1 ships configuration and enum identifiers only (repairs a dangling cross-repo reference immediately). PR2 ships the code that makes Run1B geometries buildable, with every Run1B behavior gated on configuration that already exists. PR3 ships the EMC source virtual detectors and v40 simulation support. A normalized GDML dump diff is the regression gate on every PR.

**Tech Stack:** C++17, Geant4, art/fhicl, SimpleConfig geometry text files, Muse build system, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md`

## Global Constraints

- `Mu2eG4/geom/geom_common.txt` must **never** be modified. It keeps including `geom_run1_a_stickman.txt`.
- No new configuration keys may be introduced to gate Run1B behavior. Use `hasSTM`, `stoppingTarget.foilTarget_supportStructure`, and `SimpleConfig::hasName()` key-presence.
- After every PR, the normalized GDML dump of `geom_common.txt` and of `geom_run1_a.txt` must be byte-identical to the pre-change baseline.
- All work happens in `Mu2e/Offline`. Branch from `main`, never from `Run1B`.
- Build environment is whatever `.muse` declares (`ENVSET p103` as of 2026-08-08). Do not pin a different one.
- Do not run `voms-proxy-init`. GDML dumps need no grid credentials.
- `muse setup` clobbers `$REPO`; if you need that variable, assign it after setup.
- Commit messages end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Responsibility | PR |
|---|---|---|
| `Mu2eG4/geom/geom_run1_b_{v02..v06,v40,ds_on_v40}.txt` | Run1B geometry variants | 1 |
| `Mu2eG4/geom/geom_run1_b_v01.txt` | Run1B baseline geometry (+6 lines) | 1 |
| `Mu2eG4/fcl/gdmldump_run1_b_v01.fcl` | GDML dump driver for v01 | 1 |
| `DataProducts/inc/VirtualDetectorId.hh` | VD identifiers 117–138 | 1 |
| `Mu2eG4/src/Mu2eWorld.cc` | tracker/calo parent volume selection | 2 |
| `Mu2eG4/src/constructVirtualDetectors.cc` | VD placement, parent volume selection | 2, 3 |
| `GeometryService/src/VirtualDetectorMaker.cc` | VD registration and gating | 2, 3 |
| `GeometryService/src/StoppingTargetMaker.cc` | ST mother cylinder radius | 2 |
| `Mu2eG4/src/constructStoppingTarget.cc` | ST mother volume, OPA overlap check | 2 |
| `Mu2eG4/src/constructTSdA.cc` | TSdA cutout/extra/tubes, rin sentinel | 2 |
| `Mu2eG4/src/constructProtonAbsorber.cc` | degrader mother/filter/frame layout | 3 |
| `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl` | GDML dump driver for v40 (new, not on branch) | 3 |

`tools/gdml_baseline.sh` is a throwaway harness kept in the scratch directory, never committed.

---

### Task 1: Build the workspace and prove the GDML harness is deterministic

The entire plan rests on "normalized GDML dumps are comparable." Geant4's writer appends pointer addresses to volume names, which differ run to run. Prove the normalization works before trusting it as a gate.

**Files:**
- Create: `$SCRATCH/gdml_baseline.sh` (harness, never committed)

**Interfaces:**
- Produces: `gdml_baseline.sh <fcl> <output-name>` writing `$SCRATCH/gdml/<output-name>.gdml.norm`

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

Expected: build completes with no errors. This takes roughly 10 minutes.

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
# storeReferences is true (Mu2eWorld.cc calls parser.Write with the default).
sed -E 's/0x[0-9a-f]{6,}//g' "$gdml" > "$WS/gdml/$name.gdml.norm"
popd >/dev/null
echo "wrote $WS/gdml/$name.gdml.norm ($(wc -l < "$WS/gdml/$name.gdml.norm") lines)"
SH
chmod +x $WS/gdml_baseline.sh
```

- [ ] **Step 4: Prove determinism — dump the same geometry twice, unchanged**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl determinism_a
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl determinism_b
diff gdml/determinism_a.gdml.norm gdml/determinism_b.gdml.norm && echo "HARNESS OK"
```

Expected: `HARNESS OK`. If the diff is non-empty, the normalization is insufficient — inspect the differing lines and extend the `sed` before going further. **Do not proceed past this step with a non-deterministic harness.** Everything downstream is meaningless without it.

- [ ] **Step 5: Confirm the normalization is not over-aggressive**

```bash
grep -c "0x" $WS/gdml/determinism_a.gdml.norm || echo "no raw pointers remain (expected)"
grep -c "<volume" $WS/gdml/determinism_a.gdml.norm
```

Expected: zero remaining `0x` matches, and a volume count in the thousands. A volume count near zero means the sed ate real content.

---

### Task 2: Capture baselines and run the pre-flight risk checks

**Files:** none modified — this task produces artifacts and answers two questions that gate PR1.

**Interfaces:**
- Produces: `$WS/gdml/baseline_common.gdml.norm`, `$WS/gdml/baseline_run1_a.gdml.norm`

- [ ] **Step 1: Capture the two nominal baselines**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        baseline_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl baseline_run1_a
```

Expected: both succeed. These two files are the contract for the rest of the plan. Copy them somewhere safe.

- [ ] **Step 2: Risk check — does anything size itself off `lastEnum`?**

```bash
cd $WS/Offline
grep -rn "lastEnum" --include=*.cc --include=*.hh . | grep -v VirtualDetectorId.hh
```

Read each hit. A loop bounded by `lastEnum` is fine — it grows harmlessly. A **fixed-size array or a persisted numeric width** is not. Record the verdict in the PR1 description.

- [ ] **Step 3: Risk check — does anything reference VD ids numerically?**

```bash
cd $WS
git clone --depth 1 https://github.com/Mu2e/EventNtuple
grep -rn "VirtualDetectorId\|vdid\|vd_id" EventNtuple/src EventNtuple/fcl EventNtuple/inc 2>/dev/null | head -30
grep -rn "11[0-9]\|12[0-9]" EventNtuple/fcl/from_mcs-Run1B.fcl 2>/dev/null
```

Expected: references are symbolic. Since the 22 new enums are appended *before* `lastEnum`, existing ids keep their numbers and even numeric references stay valid — but confirm nothing hardcodes the *count*.

- [ ] **Step 4: Determine whether #1849's `trigger` failure was ours**

```bash
export GH_CONFIG_DIR=${GH_CONFIG_DIR:-$HOME/.config/gh}
gh api repos/Mu2e/Offline/commits/83b5e2ff2d44af8790386b1abb702433a0b148ed/statuses \
  -q '.[] | "\(.context)  \(.state)  \(.created_at)"' | sort -u
```

`83b5e2f` is the main commit #1849 was merged onto for testing. If main was already failing `trigger` there, the failure was never ours. Record the answer — PR2 is where trigger-relevant geometry changes concentrate, and you want to know the baseline before you get there.

- [ ] **Step 5: Commit the findings to the spec as an addendum**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
# append a "Pre-flight findings" section to the spec recording the three answers
git add docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md
git commit -m "docs(spec): record Run1B pre-flight risk-check findings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: PR1 — Run1B geometry configs and VD identifiers

Configuration and identifiers only. No code. This is the PR that repairs `Production/Tests/Run1BReco.fcl` and the prodtools v40 templates.

**Files:**
- Create: `Mu2eG4/geom/geom_run1_b_{v02,v03,v04,v05,v06,v40,ds_on_v40}.txt`
- Create: `Mu2eG4/fcl/gdmldump_run1_b_v01.fcl`
- Modify: `Mu2eG4/geom/geom_run1_b_v01.txt` (+6 lines)
- Modify: `DataProducts/inc/VirtualDetectorId.hh`

**Interfaces:**
- Produces: VD identifiers `EMC_Source` (117), `EMC_Source2` (118), `EMC_0_Front` (119), `Tracker_FEB_0..18_SurfIn` (120–138), and `bool VirtualDetectorId::isFEBTracker() const`. Tasks 5, 9 and 10 rely on these names.
- Produces: geometry files whose `#include` targets all resolve on main.

- [ ] **Step 1: Branch and take the files verbatim from Run1B**

```bash
cd $WS/Offline && git checkout main && git pull && git checkout -b run1b-consolidation-configs
for f in v02 v03 v04 v05 v06 v40 ds_on_v40; do
  git checkout Run1B -- Mu2eG4/geom/geom_run1_b_$f.txt
done
git checkout Run1B -- Mu2eG4/geom/geom_run1_b_v01.txt
git checkout Run1B -- Mu2eG4/fcl/gdmldump_run1_b_v01.fcl
git checkout Run1B -- DataProducts/inc/VirtualDetectorId.hh
git status --short
```

Expected: 10 files staged, nothing else.

- [ ] **Step 2: Verify every include resolves on main**

```bash
cd $WS/Offline
grep -h '^#include' Mu2eG4/geom/geom_run1_b_*.txt | sed 's/.*"Offline\/\(.*\)".*/\1/' | sort -u | while read p; do
  [ -f "$p" ] && echo "OK   $p" || echo "MISS $p"
done
```

Expected: every line `OK`. A `MISS` means a dependency also has to come across in PR1.

- [ ] **Step 3: Verify the enum is purely additive**

```bash
cd $WS/Offline
git diff main -- DataProducts/inc/VirtualDetectorId.hh | grep "^-" | grep -v "^---"
```

Expected: the only `-` lines are the `"STM_UpStrLarge"` string-list entry and `lastEnum`, both of which reappear as `+` lines with a trailing comma or backslash. **No existing enumerator may change position.** If any pre-existing identifier moved, stop — that silently renumbers persisted data.

- [ ] **Step 4: Build**

```bash
cd $WS && muse build -j 20
```

Expected: clean build.

- [ ] **Step 5: Confirm nominal is untouched**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        pr1_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl pr1_run1_a
diff gdml/baseline_common.gdml.norm gdml/pr1_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/pr1_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`. PR1 changes no code, so any difference here means the enum addition leaked into geometry — investigate before proceeding.

- [ ] **Step 6: Confirm the v40 reco path now resolves**

```bash
cd $WS/Offline && ls -l Mu2eG4/geom/geom_run1_b_v40.txt
```

Expected: the file exists. This is what `Production/Tests/Run1BReco.fcl` and `prodtools/templates/Run1B/{digi,reco}.json` have been pointing at.

- [ ] **Step 7: Commit and open PR1**

```bash
cd $WS/Offline
git add -A && git commit -m "feat(geom): add Run1B geometry configs and virtual detector ids

Adds geom_run1_b_v02..v06, v40 and ds_on_v40, the v01 EMC source
positions, a v01 gdml dump driver, and virtual detector identifiers
117-138 (EMC source VDs and tracker FEB VDs).

Configuration and identifiers only -- no code changes, and
geom_common.txt is untouched, so nominal running is unaffected.
Verified by gdml dumps of geom_common.txt and geom_run1_a.txt being
byte-identical to main after normalization.

Repairs a dangling cross-repo reference: Production main's
Tests/Run1BReco.fcl already points at geom_run1_b_v40.txt.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git push -u origin run1b-consolidation-configs
gh pr create -R Mu2e/Offline --base main --title "Run1B geometry configs and virtual detector ids" --body "$(cat <<'EOF'
First of three PRs consolidating the Run1B branch into main. See
docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md.

Configuration and identifiers only. No code. `geom_common.txt` is
deliberately **not** changed -- main keeps `geom_run1_a_stickman.txt` as
its default geometry, addressing the review comment on #1849.

**Why now:** Production main's `Tests/Run1BReco.fcl` and the prodtools
Run1B digi/reco templates already reference
`Offline/Mu2eG4/geom/geom_run1_b_v40.txt`, which exists only on the
`Run1B` branch. This PR repairs that dangling reference. Reconstruction
builds GeometryService but not the G4 world, so those paths work as soon
as this lands.

**Nominal impact:** none. Normalized gdml dumps of `geom_common.txt` and
`geom_run1_a.txt` are byte-identical to main.

**Enum safety:** the 22 new identifiers are appended before `lastEnum`;
no existing enumerator changes position.
EOF
)"
```

---

### Task 4: PR2 commit 1 — isolate the whitespace churn

About 189 of the 491 changed lines in `constructVirtualDetectors.cc` are pure reindentation. Landing that separately shrinks the reviewable diff by more than a third.

**Files:**
- Modify: `Mu2eG4/src/constructVirtualDetectors.cc`

- [ ] **Step 1: Branch from main**

```bash
cd $WS/Offline && git checkout main && git pull && git checkout -b run1b-consolidation-gating
```

- [ ] **Step 2: Extract only the reindentation**

Take the Run1B version, then revert every substantive change back to main's content, keeping only the changed indentation. The target is a commit where the following prints nothing:

```bash
cd $WS/Offline
git show main:Mu2eG4/src/constructVirtualDetectors.cc > /tmp/cvd_main.cc
diff -w -B /tmp/cvd_main.cc Mu2eG4/src/constructVirtualDetectors.cc
```

Expected: **empty output.** That is the definition of "whitespace only" and the reviewer's entire job on this commit.

- [ ] **Step 3: Verify the substantive diff shrank**

```bash
cd $WS/Offline
git show Run1B:Mu2eG4/src/constructVirtualDetectors.cc > /tmp/cvd_run1b.cc
echo "before: $(diff /tmp/cvd_main.cc /tmp/cvd_run1b.cc | grep -c '^[<>]') changed lines"
echo "after:  $(diff Mu2eG4/src/constructVirtualDetectors.cc /tmp/cvd_run1b.cc | grep -c '^[<>]') changed lines"
```

Expected: roughly 491 before, roughly 302 after.

- [ ] **Step 4: Build and confirm nominal unchanged**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        ws_common
diff gdml/baseline_common.gdml.norm gdml/ws_common.gdml.norm && echo "UNCHANGED"
```

Expected: `UNCHANGED`. Reindentation cannot change geometry; if it does, the commit is not whitespace-only.

- [ ] **Step 5: Commit**

```bash
cd $WS/Offline
git add Mu2eG4/src/constructVirtualDetectors.cc
git commit -m "style(Mu2eG4): reindent constructVirtualDetectors hasDiskCalorimeter block

Whitespace only. Verify with:
  git show HEAD~1:Mu2eG4/src/constructVirtualDetectors.cc > /tmp/before.cc
  diff -w -B /tmp/before.cc Mu2eG4/src/constructVirtualDetectors.cc

Split out ahead of the Run1B consolidation so the substantive diff is
reviewable: this removes ~189 of 491 changed lines.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: PR2 commit 2 — parent volume selection

**Files:**
- Modify: `Mu2eG4/src/Mu2eWorld.cc`
- Modify: `Mu2eG4/src/constructVirtualDetectors.cc`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: configuration keys `tracker.inDS2Vacuum` and `calorimeter.inDS2Vacuum`, both defaulting `false`. Task 9's FEB block reads `tracker.inDS2Vacuum`.

- [ ] **Step 1: Apply the `Mu2eWorld.cc` change**

In `Mu2eWorld::constructTracker()`, replace:

```cpp
    std::string theDS3("DS3Vacuum");
    if ( _config.getBool("inGaragePosition",false) ) theDS3 = "garageFakeDS3Vacuum";
    VolumeInfo const & detSolDownstreamVacInfo = _helper->locateVolInfo(theDS3);
```

with:

```cpp
    std::string theDS2("DS2Vacuum");
    std::string theDS3("DS3Vacuum");
    if ( _config.getBool("inGaragePosition",false) ) {
      theDS2 = "garageFakeDS2Vacuum";
      theDS3 = "garageFakeDS3Vacuum";
    }
    // Run1B places the tracker in DS2Vacuum, which is extended towards the MBS.
    bool trackerInDS2 = _config.getBool("tracker.inDS2Vacuum", false);
    VolumeInfo const & detSolDownstreamVacInfo = _helper->locateVolInfo(trackerInDS2 ? theDS2 : theDS3);
```

Apply the identical pattern in `Mu2eWorld::constructCal()`, reading `calorimeter.inDS2Vacuum` into `calorimeterInDS2`.

- [ ] **Step 2: Apply the parent-volume ternaries in `constructVirtualDetectors.cc`**

There are six sites where a hardcoded `DS3Vacuum` parent becomes conditional. Each takes the form:

```cpp
          VolumeInfo const & parent = ( !_config.getBool("tracker.inDS2Vacuum",false) ) ?
            _helper->locateVolInfo(theDS3) :
            _helper->locateVolInfo("DS2Vacuum");
```

Take them from the branch:

```bash
cd $WS/Offline
git diff main Run1B -- Mu2eG4/src/constructVirtualDetectors.cc | grep -n "inDS2Vacuum\|isDumbbell"
```

- [ ] **Step 3: Fix the branch's `isDumbbell` defect**

One site tests `isDumbbell` where it should test `tracker.inDS2Vacuum`. This is Mackenzie's review comment on #1849 (`constructVirtualDetectors.cc:570`): *"It seems like this is a case I missed when fixing these parent volume changes? This should probably be doing the tracker in DS2 check"*. Change:

```cpp
          VolumeInfo const & parent = ( _config.getBool("isDumbbell",false) ) ?
```

to:

```cpp
          VolumeInfo const & parent = ( !_config.getBool("tracker.inDS2Vacuum",false) ) ?
```

Note the added negation — the ternary's branches stay in place, so the sense must invert. Verify by reading the two branches: the `true` arm must select `theDS3`.

- [ ] **Step 4: Reconcile the FEB gate**

`VirtualDetectorMaker` registers the FEB VDs under `TrackerHasBrassRings`; `constructVirtualDetectors` places them under `tracker.inDS2Vacuum`. Pick `TrackerHasBrassRings` in both — it names the physical thing the VDs measure, and registration is the upstream gate. Both default `false`, so nominal is unaffected either way.

- [ ] **Step 5: Build and confirm nominal unchanged**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        pv_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl pv_run1_a
diff gdml/baseline_common.gdml.norm gdml/pv_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/pv_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`. Every new key defaults `false`, so both nominal geometries take the pre-existing branch.

- [ ] **Step 6: Commit**

```bash
cd $WS/Offline
git add Mu2eG4/src/Mu2eWorld.cc Mu2eG4/src/constructVirtualDetectors.cc
git commit -m "feat(Mu2eG4): allow tracker and calorimeter in DS2Vacuum

Run1B extends DS2Vacuum towards the MBS and places the tracker and
calorimeter inside it. Adds tracker.inDS2Vacuum and
calorimeter.inDS2Vacuum, both defaulting false, so nominal geometries
keep DS3Vacuum unchanged.

Also fixes one virtual detector parent that tested isDumbbell where it
should test tracker.inDS2Vacuum, and reconciles the tracker FEB VD gate
so registration and placement both use TrackerHasBrassRings.

Nominal verified unchanged by gdml dump of geom_common.txt and
geom_run1_a.txt.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: PR2 commit 3 — gate STMUpstream on `hasSTM`

**Files:**
- Modify: `GeometryService/src/VirtualDetectorMaker.cc`

- [ ] **Step 1: Confirm the premise before changing anything**

```bash
cd $WS/Offline
grep -n "STMUpstream" GeometryService/src/VirtualDetectorMaker.cc
grep -n 'getBool("hasSTM"' GeometryService/src/VirtualDetectorMaker.cc
```

Expected: `STMUpstream` appears around line 195; the `hasSTM` block opens around line 480. The point of the change is that `STMUpstream` sits **outside** it while every other STM virtual detector sits inside. If that is not what you see, stop and re-derive the fix.

- [ ] **Step 2: Wrap the placement**

Replace:

```cpp
      const Hep3Vector STMOffset(targetOffset.x()-shift.x(),targetOffset.y()-shift.y(), targetOffset.z()-shift.z() - 0.5*( (coll5pos.z()+deltaZ5.z()) - (targetOffset.z()-shift.z()) ));
      vd->addVirtualDetector( VirtualDetectorId::STMUpstream,
                              ds2centerInMu2e,0,STMOffset);
```

with:

```cpp
      // STMUpstream is an STM virtual detector and belongs under the same
      // guard as every other one. Geometries without an STM (Run1B v01-v06)
      // have no meaningful place for it.
      if ( c.getBool("hasSTM",false) ) {
        const Hep3Vector STMOffset(targetOffset.x()-shift.x(),targetOffset.y()-shift.y(), targetOffset.z()-shift.z() - 0.5*( (coll5pos.z()+deltaZ5.z()) - (targetOffset.z()-shift.z()) ));
        vd->addVirtualDetector( VirtualDetectorId::STMUpstream,
                                ds2centerInMu2e,0,STMOffset);
      }
```

- [ ] **Step 3: Verify the nominal geometries have `hasSTM = true`**

```bash
cd $WS/Offline
grep -n "hasSTM" Mu2eG4/geom/geom_run1_a_stickman.txt Mu2eG4/geom/geom_run1_a.txt
grep -n "hasSTM" Mu2eG4/geom/geom_run1_b_v01.txt Mu2eG4/geom/geom_run1_b_v40.txt
```

Expected: `true` for both nominal geometries and for v40 (which inherits from `geom_run1_a.txt`), `false` for v01. That asymmetry is exactly what makes the gate correct.

- [ ] **Step 4: Build and confirm nominal unchanged**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        stm_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl stm_run1_a
diff gdml/baseline_common.gdml.norm gdml/stm_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/stm_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`, because both have `hasSTM = true`.

- [ ] **Step 5: Commit**

```bash
cd $WS/Offline
git add GeometryService/src/VirtualDetectorMaker.cc
git commit -m "fix(GeometryService): gate STMUpstream VD on hasSTM

STMUpstream was the only STM virtual detector placed outside the
hasSTM guard, so geometries with no STM still got it. Run1B (hasSTM
false) exposed this; nominal geometries have hasSTM true and are
unaffected, verified by gdml dump.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: PR2 commit 4 — stopping target mother radius

**Files:**
- Modify: `GeometryService/src/StoppingTargetMaker.cc`
- Modify: `Mu2eG4/src/constructStoppingTarget.cc`

- [ ] **Step 1: Confirm the premise, and print the actual numbers**

```bash
cd $WS/Offline
sed -n '116,140p' GeometryService/src/StoppingTargetMaker.cc
grep -n "stoppingTarget.radii" Mu2eG4/geom/stoppingTargetHoles_DOE_review_2017.txt
grep -n "foilTarget_supportStructure\b" Mu2eG4/geom/stoppingTarget_CD3C_34foils.txt Mu2eG4/geom/geom_run1_b_v01.txt
grep -n "^#include" Mu2eG4/geom/geom_run1_b_v01.txt
```

Expected: main derives the radius **only** from the support structure. Both configurations reach the `endAtOPA` branch at line 123 — v01 inherits `endAtOPA = true` through `geom_run1_a.txt`, so neither takes the `250.` fallback at line 138. The resulting numbers:

| | support-derived | foil-derived | main's radius | needed |
|---|---|---|---|---|
| Run1A (37 foils, rOut 75) | ≈503.6 | ≈76 | 503.6 | 503.6 |
| Run1B v01 (1 foil, rOut 600) | ≈447.7 | 601 | **447.7** | 601 |

Run1B's mother cylinder is smaller than the single foil it contains. That is the bug. Note that the flag difference is legitimate — Run1B's target is one 600 mm aluminium disk spanning the DS aperture, with no suspension wires to model, not a 37-foil array.

To see the real values rather than trusting the arithmetic above, raise the verbosity:

```bash
cd $WS
mu2e -c Offline/Mu2eG4/fcl/gdmldump.fcl -n 1 \
  --config-out /dev/null 2>&1 | grep -i "stopping target" | head
```

- [ ] **Step 2: Enforce both constraints, unconditionally**

Do **not** branch on `_foilTarget_supportStructure`. The mother has always had two constraints — reach the OPA so the support wires fit, and enclose the foils — and main only ever enforced the first. Taking the maximum satisfies both and reproduces main exactly wherever main was already correct.

Replace:

```cpp
    double radius=-1;
    /*
    for (unsigned int ifoil=0; ifoil<_targ->_foils.size(); ifoil++)
      {
        double rtest=_targ->_foils[ifoil].rOut() + _targ->_foils[ifoil].centerInDetectorSystem().perp();
        radius=max(radius,rtest);
      }
    // beef it up by a mm
    radius+=1;
    */
    // fix it to the diameter of the outer proton absorber
    radius=_foilTarget_supportStructure_rOut - 0.001;
```

with:

```cpp
    // The mother cylinder has two constraints: it must reach the outer proton
    // absorber so the support wires fit inside it, and it must enclose the
    // foils themselves. Enforce both. For the 37-foil target the first
    // dominates (~504 mm vs ~76 mm); for a single wide-disk target such as
    // Run1B the second does (~448 mm vs 601 mm).
    double foilRadius = -1;
    for (unsigned int ifoil=0; ifoil<_targ->_foils.size(); ifoil++) {
      double rtest = _targ->_foils[ifoil].rOut() + _targ->_foils[ifoil].centerInDetectorSystem().perp();
      foilRadius = std::max(foilRadius, rtest);
    }
    foilRadius += 1; // beef it up by a mm
    double radius = std::max(_foilTarget_supportStructure_rOut - 0.001, foilRadius);
```

Add `#include <algorithm>` to the include block if not present.

This is deliberately **not** gated on `foilTarget_supportStructure`. Keying on that flag would make the fix depend on a configuration choice that is orthogonal to it, and would silently reintroduce the overlap if anyone ever set the flag true on a wide-disk target.

- [ ] **Step 3: Gate the OPA lookup in `constructStoppingTarget.cc`**

Run1B sets `hasProtonAbsorber = false`, so `GeomHandle<MECOStyleProtonAbsorber>` would throw. Wrap the existing OPA overlap check:

```cpp
    if ( config.getBool("hasProtonAbsorber", true) ) {
      GeomHandle<MECOStyleProtonAbsorber> pabs;
      const double cylinderRadius = target->cylinderRadius();
      // ... existing OPA overlap logic unchanged ...
    }
```

**Do not** port the branch's `maxMotherRadius` support-structure loop. It is empty whenever `foilTarget_supportStructure` is false (Run1B never has support structures), so it does nothing for Run1B — and when support structures *do* exist it grows the nominal mother volume. It is dead code for the case it was written for and a behavior change for the case it was not. Keep `TubsParams targetMotherParams(0., target->cylinderRadius(), target->cylinderLength()/2.);` as main has it; Step 2 already makes `cylinderRadius` correct in both regimes.

- [ ] **Step 4: Build and confirm nominal unchanged**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        st_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl st_run1_a
diff gdml/baseline_common.gdml.norm gdml/st_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/st_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`. This is the highest-risk commit in PR2. The nominal path must have the support-derived term win the `max`; a diff here means the foil-derived term overtook it, so the arithmetic in Step 1 was wrong for this geometry. Investigate rather than adjusting the formula to make the diff go away.

- [ ] **Step 4b: Confirm the fix actually fixes Run1B**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v01.fcl st_run1_b_v01
grep -i "overlap" $(ls -t /tmp/tmp.*/st_run1_b_v01.log 2>/dev/null | head -1) || echo "no overlaps reported"
grep -B2 -A2 "StoppingTargetMother" gdml/st_run1_b_v01.gdml.norm | head -20
```

Expected: no overlap warnings, and `StoppingTargetMother`'s radius is ≈601, not ≈448. A negative result here means the `max` is not reaching the foil-derived term — check that `_targ->_foils` is populated before the radius is computed.

- [ ] **Step 5: Commit**

```bash
cd $WS/Offline
git add GeometryService/src/StoppingTargetMaker.cc Mu2eG4/src/constructStoppingTarget.cc
git commit -m "fix(GeometryService): ST mother must enclose its foils, not just reach the OPA

The stopping target mother radius was derived solely from the support
structure outer radius. That is one of two constraints the mother has
always had -- it must also enclose the foils themselves.

For the 37-foil target the support term dominates (~504 mm vs ~76 mm),
so nominal running is unchanged, verified by gdml dump. For a single
wide-disk target such as Run1B v01 the foil term dominates (601 mm vs
~448 mm), and the mother was previously smaller than its own contents.

Takes the maximum of both terms unconditionally rather than branching on
foilTarget_supportStructure, which is orthogonal to the constraint and
would reintroduce the overlap if ever set true on a wide-disk target.

Also guards the outer-proton-absorber overlap check on
hasProtonAbsorber, since Run1B has none and the GeomHandle would throw.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: PR2 commit 5 — TSdA options, then open PR2

**Files:**
- Modify: `Mu2eG4/src/constructTSdA.cc`

- [ ] **Step 1: Port the gated additions from the branch**

```bash
cd $WS/Offline && git checkout Run1B -- Mu2eG4/src/constructTSdA.cc
```

- [ ] **Step 2: Verify every addition is gated and defaults off**

```bash
cd $WS/Offline
grep -n 'getBool("tsda\.' Mu2eG4/src/constructTSdA.cc
```

Expected: `tsda.cutout.build`, `tsda.extra.build` and `tsda.tubes.build`, each `getBool(..., false)`. Any ungated addition must be gated before committing.

- [ ] **Step 3: Verify the `tsda.rin` sentinel change is inert**

```bash
cd $WS/Offline
grep -n 'tsda.rin' Mu2eG4/src/constructTSdA.cc Mu2eG4/geom/TSdA_v01.txt Mu2eG4/geom/TSdA_v02.txt
grep -rn 'tsda.rin' Mu2eG4/geom/*.txt
```

The sentinel moves from `getDouble("tsda.rin", 0.0)` with test `< 1.0e-06` to `getDouble("tsda.rin", -1.0)` with test `< 0.`. Expected: `TSdA_v01.txt` sets `240.0`, `TSdA_v02.txt` sets `235.0`, and **no geometry sets `0`** — so every existing configuration takes the same branch as before. If any geometry sets `tsda.rin = 0`, the change is not inert and that geometry flips from "use TS5 outer radius" to "literal zero".

- [ ] **Step 4: Build and confirm nominal unchanged**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        tsda_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl tsda_run1_a
diff gdml/baseline_common.gdml.norm gdml/tsda_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/tsda_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`. Both nominal geometries use `TSdA_v02.txt` with `hasTSdA = true`, so this exercises the real path.

- [ ] **Step 5: Commit**

```bash
cd $WS/Offline
git add Mu2eG4/src/constructTSdA.cc
git commit -m "feat(Mu2eG4): optional TSdA cutout, extra disk and tubes

Run1B v40 uses the TSdA as an aluminium collimator. Adds
tsda.cutout.build, tsda.extra.build and tsda.tubes.build, all
defaulting false.

Also changes the tsda.rin sentinel from 0.0 to -1.0 so a hole-less disk
becomes expressible. Inert for existing geometries: TSdA_v01 sets 240
and TSdA_v02 sets 235, and no geometry sets zero.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Build a Run1B geometry end to end**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v01.fcl pr2_run1_b_v01
```

Expected: succeeds. Before PR2 this would fail or produce a mother-volume overlap — this is the first point where a Run1B geometry actually builds on main.

- [ ] **Step 7: Push and open PR2**

```bash
cd $WS/Offline
git push -u origin run1b-consolidation-gating
gh pr create -R Mu2e/Offline --base main --title "Run1B: gate geometry changes on existing configuration" --body "$(cat <<'EOF'
Second of three PRs consolidating the Run1B branch into main. See
docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md.

**Nominal running is unchanged, and that is mechanically verified.**
After each commit, a normalized gdml dump of `geom_common.txt` and
`geom_run1_a.txt` is byte-identical to main. `geom_common.txt` itself is
never touched.

**No new gating knobs.** The two contested behavior changes from #1849
are keyed on configuration that already exists:

- `STMUpstream` was the only STM virtual detector outside the `hasSTM`
  guard. Now inside it. Nominal has `hasSTM = true`.
- The stopping target mother radius was derived solely from the support
  structure, ignoring the foils. For the 37-foil target the support term
  dominates (~504 mm vs ~76 mm) so nominal is unchanged; for Run1B's
  single 600 mm disk it does not (~448 mm vs 601 mm), leaving the mother
  smaller than its contents. Now takes the maximum of both terms,
  unconditionally.

Both stand on their own as fixes; Run1B is what exposed them.

**Read the first commit by checking it is whitespace-only:**
```
git show <sha>~1:Mu2eG4/src/constructVirtualDetectors.cc > /tmp/before.cc
git show <sha>:Mu2eG4/src/constructVirtualDetectors.cc  > /tmp/after.cc
diff -w -B /tmp/before.cc /tmp/after.cc   # empty
```
That removes ~189 of 491 changed lines from review.

Also fixes the `isDumbbell` parent-volume case flagged in review on
#1849.
EOF
)"
```

---

### Task 9: PR3 — gate the EMC source virtual detectors on key presence

**Files:**
- Modify: `GeometryService/src/VirtualDetectorMaker.cc`
- Modify: `Mu2eG4/src/constructVirtualDetectors.cc`

**Interfaces:**
- Consumes: `VirtualDetectorId::EMC_Source`, `EMC_Source2`, `EMC_0_Front` from Task 3.
- Produces: the contract that these VDs exist only when the geometry sets `zEMCSourceInMu2e`, `zEMCSource2InMu2e`, `zEMC0Front`. Task 11 relies on it.

- [ ] **Step 1: Branch from main (after PR1 and PR2 have merged)**

```bash
cd $WS/Offline && git checkout main && git pull && git checkout -b run1b-consolidation-emc
```

- [ ] **Step 2: Register each VD only when its z is configured**

Instead of the branch's hardcoded defaults, write:

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

- [ ] **Step 3: Port the placement block**

```bash
cd $WS/Offline
git diff main Run1B -- Mu2eG4/src/constructVirtualDetectors.cc | grep -n "EMC_Source\|EMC_0_Front"
```

Port the `EMC_Source` / `EMC_Source2` / `EMC_0_Front` placement code. It is already guarded by `vdg->exist(vdId)`, so Step 2's registration gate governs it automatically — no second gate is needed.

- [ ] **Step 4: Check the `TSdA.rFactorForVDs` lookup is reachable-safe**

The ported block reads `_config.getDouble("TSdA.rFactorForVDs")` with no default when `hasTSdA` is true, and does so *before* the `vdg->exist(vdId)` guard. Confirm the key is always available:

```bash
cd $WS/Offline
grep -rn "rFactorForVDs" Mu2eG4/geom/
head -6 Mu2eG4/geom/TSdA_v02.txt
```

Expected: defined in `TSdA_v01.txt` as `650.`, and `TSdA_v02.txt` includes `TSdA_v01.txt` — so every geometry using either version has it. If a geometry ever uses `hasTSdA = true` without including a TSdA version file, this throws; move the lookup inside the `vdg->exist` guard if so.

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

- [ ] **Step 6: Confirm v01 still gets its VDs**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v01.fcl emc_run1_b_v01
grep -c "EMC_Source" gdml/emc_run1_b_v01.gdml.norm
```

Expected: a non-zero count. v01 sets all three keys, so key-presence gating must not have cost it anything.

- [ ] **Step 7: Commit**

```bash
cd $WS/Offline
git add GeometryService/src/VirtualDetectorMaker.cc Mu2eG4/src/constructVirtualDetectors.cc
git commit -m "feat(GeometryService): add EMC source virtual detectors where configured

Adds EMC_Source, EMC_Source2 and EMC_0_Front, placed only when the
geometry sets zEMCSourceInMu2e, zEMCSource2InMu2e or zEMC0Front. Key
presence is the switch, so no geometry gains virtual detectors it did
not ask for -- addressing the review comment on #1849 about hardcoded
default z values.

Nominal verified unchanged by gdml dump; neither geom_common.txt nor
geom_run1_a.txt sets these keys.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: PR3 — degrader geometry for v40

**Files:**
- Modify: `Mu2eG4/src/constructProtonAbsorber.cc`

- [ ] **Step 1: Confirm the code is unreachable in nominal**

```bash
cd $WS/Offline
grep -rn "degrader.build" Mu2eG4/geom/*.txt
```

Expected: `degrader_v02.txt` sets it `false` ("off by default"), and only `geom_run1_b_v40.txt` sets it `true`. That is why this can land unconditionally.

- [ ] **Step 2: Port the change**

```bash
cd $WS/Offline && git checkout Run1B -- Mu2eG4/src/constructProtonAbsorber.cc
```

The rewrite places the filter at the upstream edge of the degrader mother and the frame immediately downstream of it, replacing a layout where the mother half-width and the child offsets disagreed.

- [ ] **Step 3: Build and confirm nominal unchanged**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        deg_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl deg_run1_a
diff gdml/baseline_common.gdml.norm gdml/deg_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/deg_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

Expected: both `UNCHANGED`. The degrader is not built in either, so the rewritten code never runs. A diff here would mean `degrader.build` is true somewhere you did not expect — find it before proceeding.

- [ ] **Step 4: Commit**

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
stopping target. Nominal verified unchanged by gdml dump.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: PR3 — EMC source z values for the run1_a-descended geometries

**This task is blocked** on a physics decision. Tasks 9 and 10 do not depend on it; land them first if the answer is slow.

**Files:**
- Modify: `Mu2eG4/geom/geom_run1_b_{v03,v04,v05,v06,v40}.txt`
- Create: `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl`

**Interfaces:**
- Consumes: the key-presence contract from Task 9.

- [ ] **Step 1: Establish what the branch currently produces for v40**

```bash
cd $WS/Offline && git checkout Run1B && cd $WS && muse build -j 20
cat > /tmp/gdmldump_run1_b_v40.fcl <<'EOF'
#include "Offline/Mu2eG4/fcl/gdmldump.fcl"
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/geom_run1_b_v40.txt"
physics.producers.g4run.debug.GDMLFileName: "mu2e_run1_b_v40.gdml"
EOF
./gdml_baseline.sh /tmp/gdmldump_run1_b_v40.fcl branch_run1_b_v40
grep -A2 "EMC_Source" gdml/branch_run1_b_v40.gdml.norm | head -20
```

This shows where the hardcoded `5300 / 4800 / 5830` defaults actually put the VDs in v40's geometry. It is the "what we have today" reference for the conversation in Step 2 — note that these values were tuned against v01, which sets `mu2e.detectorSystemZ0 = 7000`, while v40 keeps `geom_run1_a.txt`'s `10171`.

- [ ] **Step 2: Get the physics intent**

Ask @sdifalco — author of PR #1711 from branch `sdifalco/newRUN1BVD`, which introduced these VDs — what `EMC_Source` and `EMC_Source2` are meant to measure, and whether v03–v06 and v40 need them at all. Record the answer in the spec.

For `EMC_0_Front`, derive the value from disk 0's actual front face in each geometry rather than hand-tuning:

```bash
cd $WS/Offline
grep -rn "diskZMotherShift\|calorimeter.caloMotherZ0\|diskInnerRadius" Mu2eG4/geom/calorimeter_CsI_v2.txt | head
```

- [ ] **Step 3: Add the values**

For each of v03, v04, v05, v06 and v40, append a block of the shape v01 uses:

```
// Set position of EMC Source Virtual Detectors
double zEMCSourceInMu2e  = <value>;
double zEMCSource2InMu2e = <value>;
double zEMC0Front        = <value>;
```

Omit a key entirely if Step 2 concludes that geometry should not have that VD — omission is now a meaningful statement, which is the point of the key-presence gate.

- [ ] **Step 4: Commit the v40 dump driver**

```bash
cd $WS/Offline && git checkout run1b-consolidation-emc
cp /tmp/gdmldump_run1_b_v40.fcl Mu2eG4/fcl/gdmldump_run1_b_v40.fcl
```

- [ ] **Step 5: Verify each geometry gets what was intended**

```bash
cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v40.fcl pr3_run1_b_v40
grep -c "EMC_Source\|EMC_0_Front" gdml/pr3_run1_b_v40.gdml.norm
```

Expected: matches the decision from Step 2. Also confirm no overlaps were introduced:

```bash
grep -i "overlap" $(ls -t /tmp/tmp.*/pr3_run1_b_v40.log 2>/dev/null | head -1) || echo "no overlaps reported"
```

- [ ] **Step 6: Commit**

```bash
cd $WS/Offline
git add Mu2eG4/geom/geom_run1_b_v0*.txt Mu2eG4/geom/geom_run1_b_v40.txt Mu2eG4/fcl/gdmldump_run1_b_v40.fcl
git commit -m "feat(geom): position EMC source VDs in the run1_a-descended Run1B geometries

v03-v06 and v40 descend from geom_run1_a.txt rather than
geom_run1_b_v01.txt, so they never set the EMC source VD positions and
previously relied on hardcoded code defaults. Sets them explicitly, and
adds a v40 gdml dump driver.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: PR3 — fidelity check and open the PR

The one check that proves consolidation was faithful rather than merely safe.

- [ ] **Step 1: Dump v01 from the Run1B branch**

```bash
cd $WS/Offline && git checkout Run1B && cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v01.fcl fidelity_branch_v01
```

- [ ] **Step 2: Dump v01 from the consolidated branch**

```bash
cd $WS/Offline && git checkout run1b-consolidation-emc && cd $WS && muse build -j 20
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_b_v01.fcl fidelity_consolidated_v01
```

- [ ] **Step 3: Compare**

```bash
cd $WS
diff gdml/fidelity_branch_v01.gdml.norm gdml/fidelity_consolidated_v01.gdml.norm && echo "FIDELITY CONFIRMED"
```

Expected: `FIDELITY CONFIRMED`. Two differences are legitimate and, if present, must be explained rather than dismissed:
- `STMUpstream` — absent from both (v01 has `hasSTM = false`, and the branch commented it out unconditionally). If it appears in one, the Task 6 gate is wrong.
- Support-structure-derived volumes — v01 has none.

Any other difference means something did not come across. Investigate before opening the PR; this is the last gate.

- [ ] **Step 4: Final nominal check on the full PR3 branch**

```bash
cd $WS
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump.fcl        pr3_common
./gdml_baseline.sh Offline/Mu2eG4/fcl/gdmldump_run1_a.fcl pr3_run1_a
diff gdml/baseline_common.gdml.norm gdml/pr3_common.gdml.norm && echo "COMMON UNCHANGED"
diff gdml/baseline_run1_a.gdml.norm gdml/pr3_run1_a.gdml.norm && echo "RUN1A UNCHANGED"
```

- [ ] **Step 5: Push and open PR3**

```bash
cd $WS/Offline
git push -u origin run1b-consolidation-emc
gh pr create -R Mu2e/Offline --base main --title "Run1B: EMC source virtual detectors and v40 support" --body "$(cat <<'EOF'
Third and final PR consolidating the Run1B branch into main. See
docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md.

**Nominal unchanged**, verified as in PR2 by normalized gdml dumps of
`geom_common.txt` and `geom_run1_a.txt`.

**Fidelity confirmed:** a normalized gdml dump of `geom_run1_b_v01.txt`
built from this branch is identical to one built from the `Run1B`
branch. Consolidation reproduced the branch, it did not approximate it.

EMC source virtual detectors are placed only where a geometry sets their
z, so no configuration gains detectors it did not ask for. The
run1_a-descended Run1B geometries now set those positions explicitly
instead of inheriting code defaults tuned for v01.

The degrader placement fix is unreachable in nominal running --
`degrader.build` is false by default and true only in v40.

After this merges the `Run1B` branch can be deleted; see the follow-up
issue.
EOF
)"
```

---

### Task 13: Retire the Run1B branch

Only after PR3 has merged.

- [ ] **Step 1: Prove main now contains everything**

```bash
cd $WS/Offline && git checkout main && git pull && git fetch origin Run1B:Run1B
git diff main Run1B --stat
```

Expected: `Mu2eG4/geom/geom_common.txt` and nothing else. Any other file is something consolidation missed — do not delete the branch until this is clean.

- [ ] **Step 2: Verify the cross-repo references resolve**

```bash
cd $WS/Offline
for f in geom_run1_b_v01.txt geom_run1_b_v40.txt; do
  [ -f "Mu2eG4/geom/$f" ] && echo "OK $f" || echo "MISSING $f"
done
```

Expected: both `OK`. These back `Production/JobConfig/recoMC/NoFieldRun1B.fcl`, `Production/Tests/Run1BReco.fcl` and the prodtools Run1B templates.

- [ ] **Step 3: Run the Run1B reconstruction smoke**

```bash
cd $WS && mu2e -c Production/Tests/Run1BReco.fcl -n 1
```

Expected: exit status 0. Needs a Production checkout alongside Offline in the Muse workspace.

- [ ] **Step 4: Delete the branch and record why**

```bash
export GH_CONFIG_DIR=${GH_CONFIG_DIR:-$HOME/.config/gh}
gh api -X DELETE repos/Mu2e/Offline/git/refs/heads/Run1B
```

Announce on the relevant Mu2e channel that Run1B is consolidated, that main's default geometry is deliberately still Run1A, and that Run1B configurations are selected by naming a `geom_run1_b_*.txt` file.

- [ ] **Step 5: Record the outcome in the prodtools wiki**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
# write wiki/pages/2026-XX-XX-run1b-consolidation.md covering: what landed in
# each PR, that geom_common.txt still points at Run1A, the gdml-diff method,
# and the EMC source z values chosen in Task 11
```

---

## Self-Review

**Spec coverage.** Group A1 → Task 3. Group A2 → Tasks 5 and 8. Group B1 → Task 6. Group B2 → Task 7. Group B3 → Tasks 9 and 11. Group B4 → Task 5 Step 4. Group C → Task 10. Group D (never touch `geom_common.txt`) → global constraint, re-verified in every gdml step. Group E1 → Task 5 Step 3. Group E2 → Task 5 Step 4. Group F → Task 4. Open item → Task 11. Testing strategy → Task 1 (harness), Task 2 (baselines), every subsequent build step. Risks: `lastEnum` → Task 2 Step 2; numeric VD ids → Task 2 Step 3; `trigger` RC 2 → Task 2 Step 4.

**Deviations from the spec, both deliberate:**
1. The spec said to port `constructStoppingTarget.cc`'s `maxMotherRadius` under the same gate. Task 7 Step 3 **drops** it instead: the support-structure loop is empty exactly when Run1B needs it and only changes behavior for nominal, so gating it would preserve dead code while risking the thing the gate exists to protect.
2. Task 11 adds `Mu2eG4/fcl/gdmldump_run1_b_v40.fcl`, which the spec did not list. Without it v40 cannot be verified, and v40 is the configuration prodtools actually runs.

**Correction folded in during review.** An earlier draft gated B2 on
`foilTarget_supportStructure` and justified it with a 250 mm fallback radius.
Both were wrong: v01 inherits `endAtOPA = true` from `geom_run1_a.txt`, so it
takes the OPA-derived branch (≈448 mm), not the fallback — and keying the fix
to that flag ties it to a configuration choice orthogonal to the constraint.
Task 7 now enforces both constraints unconditionally via `max`. The spec has
been updated to match; there is no remaining disagreement between the two
documents.

**Placeholder scan.** The only deferred value is the EMC z numbers in Task 11, which the spec already identifies as requiring a physics decision. Task 11 is explicitly marked blocked, does not block Tasks 9, 10 or 12, and Step 1 produces the concrete reference data the decision needs rather than leaving it abstract.

**Consistency.** Config key names (`tracker.inDS2Vacuum`, `calorimeter.inDS2Vacuum`, `TrackerHasBrassRings`, `hasSTM`, `stoppingTarget.foilTarget_supportStructure`, `hasProtonAbsorber`, `zEMCSourceInMu2e`, `zEMCSource2InMu2e`, `zEMC0Front`, `tsda.cutout.build`, `tsda.extra.build`, `tsda.tubes.build`) are spelled identically across tasks and match the values verified in the source. The harness contract `gdml_baseline.sh <fcl> <name>` → `$WS/gdml/<name>.gdml.norm` is used consistently in all 24 invocations. Branch names `run1b-consolidation-configs`, `run1b-consolidation-gating`, `run1b-consolidation-emc` are consistent between creation, push and checkout.
