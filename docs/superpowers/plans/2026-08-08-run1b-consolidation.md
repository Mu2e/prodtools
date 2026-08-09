# Run1B Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Run1B production geometry on `Mu2e/Offline` main without changing what main does by default, then archive and retire the branch.

**Architecture:** Two sequential pull requests. PR1 ships the v40 geometry files and enum identifiers only, which repairs a dangling cross-repo reference immediately. PR2 ships the degrader placement fix and the EMC source virtual detectors. A normalized GDML dump diff is the regression gate on both.

**Tech Stack:** C++17, Geant4, art/fhicl, SimpleConfig geometry text files, Muse build system, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md`

## Global Constraints

- `Mu2eG4/geom/geom_common.txt` must **never** be modified. It keeps including `geom_run1_a_stickman.txt`.
- `Mu2eG4/geom/geom_run1_b_v01.txt` must **never** be modified. It is already on main and is the declared default inside a live Production fcl.
- `geom_run1_b_v02.txt` through `v06.txt` are **not ported**. They are the superseded single-disk modelling line. `prodtools/data/Run1B` does reference v03 (14×) and v06 (15×) for the Run1Bag/Bah/Bai campaigns; those campaigns are deliberately **frozen** as completed history, not treated as a re-runnable recovery path. Task 7 documents the freeze.
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
cd "$work"
rc=0
mu2e -c "$fcl" -n 1 >"$name.log" 2>&1 || rc=$?
# Save the log FIRST, before any early exit, so it is always retrievable.
cp "$name.log" "$WS/gdml/$name.log"
if [ "$rc" -ne 0 ]; then
  echo "RUN FAILED (exit $rc): log at $WS/gdml/$name.log" >&2; exit 1
fi
# Do NOT use `ls *.gdml | head -1`: with no match, ls exits 2, pipefail
# propagates it, and set -e kills the script before any error message.
shopt -s nullglob
gdmls=(*.gdml)
shopt -u nullglob
if [ "${#gdmls[@]}" -eq 0 ]; then
  echo "NO GDML PRODUCED: log at $WS/gdml/$name.log" >&2; exit 1
fi
gdml="${gdmls[0]}"
if [ ! -s "$gdml" ] || [ "$(stat -c%s "$gdml")" -lt 1000 ]; then
  echo "GDML TRUNCATED ($(stat -c%s "$gdml") bytes): log at $WS/gdml/$name.log" >&2; exit 1
fi
# Geant4 appends 0x<address> to solid/volume/material names when
# storeReferences is true (Mu2eWorld.cc:326 calls parser.Write with the default).
sed -E 's/0x[0-9a-f]{6,}//g' "$gdml" > "$WS/gdml/$name.gdml.norm"
cd / && rm -rf "$work"
echo "wrote $WS/gdml/$name.gdml.norm ($(wc -l < "$WS/gdml/$name.gdml.norm") lines)"
SH
chmod +x $WS/gdml_baseline.sh
```

Three things in that script are load-bearing and were bugs in an earlier draft:

- **The log is copied before any early exit.** Later tasks read these logs, and a failure whose log is stranded in an unnamed `mktemp` dir is unactionable.
- **`ls *.gdml | head -1` is forbidden here.** Under `set -euo pipefail`, a no-match glob makes `ls` exit 2, `pipefail` surfaces it despite `head` exiting 0, and `set -e` kills the script *before* the error message ever prints. The failure path becomes dead code and the run dies with a bare exit 2.
- **Size check.** A crash that still exits 0 can leave a truncated dump; an empty file would otherwise normalize to an empty baseline and silently "match" nothing.

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

**Clone EventNtuple OUTSIDE `$WS`.** `$WS` is the Muse workspace root: any repository sitting there is picked up by `muse setup` and compiled by `muse build`. Cloning EventNtuple into `$WS` silently adds it to the build — which lengthens every rebuild and, far worse, changes the build environment away from the one the Step 1 baselines were captured against, making every later "nominal unchanged" comparison suspect. This is read-only reference material; keep it out of the workspace.

```bash
REF=/exp/mu2e/data/users/oksuzian/claude-scratch/eventntuple-ref
git clone --depth 1 https://github.com/Mu2e/EventNtuple $REF
grep -rn "VirtualDetectorId\|vdid\|vd_id" $REF/src $REF/inc 2>/dev/null | head -30
cat $REF/fcl/from_mcs-Run1B.fcl
```

If an earlier run already cloned it into `$WS`, move it out and drop its build artifacts before the next rebuild:

```bash
mv $WS/EventNtuple /exp/mu2e/data/users/oksuzian/claude-scratch/eventntuple-ref
rm -rf $WS/build/*/EventNtuple
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
that line and is what the Run1Bak and Run1Ban campaigns run. v02-v06 are
not ported: the campaigns that used them (Run1Baa, Run1Bag, Run1Bah,
Run1Bai) are being frozen as completed history, and those geometries are
preserved under an archive tag rather than on main.

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

- [ ] **Step 2: Understand exactly what changes, in nominal as well as v40**

```bash
cd $WS/Offline
grep -n "degrader.build\|degrader.rotation" Mu2eG4/geom/degrader_v02.txt
grep -c "degrader" $WS/gdml/baseline_common.gdml.norm
grep -n "supportArm" GeometryService/src/MECOStyleProtonAbsorberMaker.cc | head
```

**The degrader IS built in nominal running.** `degrader_v02.txt:11` sets `degrader.build = true`; "off by default" means `degrader.rotation = 120.0`, swinging it out of the beam, not omitting it. The nominal baselines contain 57 degrader references and all six degrader volumes. Do not repeat the earlier mistake of treating this code as unreachable.

The change is narrower than "placement". With `old = 2·filter_hl + frame_hl + 1` and `new = filter_hl + frame_hl + 0.1`:

| | old mother half | new mother half | filter abs z | frame abs z |
|---|---|---|---|---|
| nominal (filter 1.00, frame 6.35) | 9.35 | 7.45 | unchanged | unchanged |
| v40 (filter 8.75) | 24.85 | 15.20 | unchanged | unchanged |

The filter and frame do **not** move in either case. Only the mother box shrinks and re-centres around them, removing a downstream overrun of 1.9 mm in nominal and 9.65 mm on v40 — which is why the overlap only bites on v40. Confirm this algebra against the code before you touch anything; if the filter or frame absolute positions do move, the analysis is wrong and you must stop.

Also expected: every `degrader.supportArm.*` key is read with a default at `MECOStyleProtonAbsorberMaker.cc:444-456`, so v40's overrides need no new plumbing.

- [ ] **Step 3: Capture the pre-change v40 geometry, with overlap checking actually enabled**

A GDML dump does **not** report overlaps. Geant4's surface check is gated on the SimpleConfig key `g4.doSurfaceCheck` (read at `GeometryService/src/G4GeometryOptions.cc:102`), which is set `true` only in `Mu2eG4/geom/geom_SurfaceCheck.txt`. `gdmldump.fcl` runs `geom_common.txt`, so grepping a dump log for "overlap" is **vacuously silent** — it reports nothing whether or not overlaps exist. Verified empirically in Task 1: neither determinism run log contained any overlap string.

So the overlap evidence needs its own geometry wrapper. Create it as an **untracked** file in the scratch clone — it is verification scaffolding and must never be committed:

```bash
cd $WS/Offline
cat > Mu2eG4/geom/_scratch_v40_surfcheck.txt <<'EOF'
#include "Offline/Mu2eG4/geom/geom_run1_b_v40.txt"
bool g4.doSurfaceCheck             = true;
int  g4.nSurfaceCheckPointsPercmsq = 1;
int  g4.minSurfaceCheckPoints      = 100;
int  g4.maxSurfaceCheckPoints      = 10000000;
EOF
cat > /tmp/surfcheck_v40.fcl <<'EOF'
#include "Offline/Mu2eG4/fcl/gdmldump.fcl"
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/_scratch_v40_surfcheck.txt"
physics.producers.g4run.debug.GDMLFileName: "mu2e_v40_surfcheck.gdml"
EOF
cd $WS && muse build -j 20
./gdml_baseline.sh /tmp/surfcheck_v40.fcl v40_before_degrader
grep -ic "overlap" gdml/v40_before_degrader.log
```

Surface checking samples points per volume across ~14k volumes, so this run takes substantially longer than a plain dump — do not mistake slowness for a hang, and run it backgrounded.

Record the overlap count. This is the "before" the fix must improve on. **If it is zero, stop and investigate**: either the surface check is still not enabled (check the log for surface-check output at all, not just the word "overlap") or the degrader fix addresses something other than an overlap, in which case the commit message must be rewritten to claim only what is true.

`.gitignore` note: confirm `git status --porcelain` shows `_scratch_v40_surfcheck.txt` as untracked and never stage it. Delete it before the final push.

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
diff gdml/baseline_common.gdml.norm gdml/deg_common.gdml.norm > /tmp/deg_common.diff || true
diff gdml/baseline_run1_a.gdml.norm gdml/deg_run1_a.gdml.norm > /tmp/deg_run1_a.diff || true
wc -l /tmp/deg_common.diff /tmp/deg_run1_a.diff
grep -oE 'name="[A-Za-z_0-9]+"' /tmp/deg_common.diff | sort -u
```

**Expect a bounded diff, not byte-identity** — the degrader is built in both nominal geometries. What must be proven is that the diff is confined to exactly two things: `degraderOutline`'s box dimensions and the `degraderMother` placement. **No other volume may appear.**

In particular the filter, frame, rod and counterweight absolute positions must be identical; extract and compare them explicitly rather than eyeballing the diff. Any volume outside the degrader assembly showing up is a stop-and-report condition.

Record the exact diff line count and the volume-name list in the report. The PR body must state this precisely, because the claim to reviewers is "the mother box tightens around contents that do not move" — not "nothing changed".

- [ ] **Step 6: Confirm the fix improves v40**

```bash
cd $WS
./gdml_baseline.sh /tmp/surfcheck_v40.fcl v40_after_degrader
grep -ic "overlap" gdml/v40_after_degrader.log
diff gdml/v40_before_degrader.gdml.norm gdml/v40_after_degrader.gdml.norm | head -40
```

Use the **same** surface-check wrapper as Step 3, or the comparison is meaningless.

Expected: the overlap count drops to zero from the non-zero baseline recorded in Step 3, and the geometry diff shows `degraderFilter`, `degraderFrame`, `degraderRod` and the degrader mother box moving. Those are the only volumes that may change.

- [ ] **Step 7: Commit**

```bash
cd $WS/Offline
git add Mu2eG4/src/constructProtonAbsorber.cc
git commit -m "fix(Mu2eG4): size the degrader mother to its contents

The degrader mother half-width came from 2*filter_hl + frame_hl + 1
while its children were placed from a different expression, leaving the
mother oversized and overrunning its contents downstream. Sizes it as
filter_hl + frame_hl + 0.1 and places the filter at its upstream edge
with the frame directly downstream.

The filter and frame do not move: their absolute z is identical before
and after. Only the mother box shrinks and re-centres -- by 1.9 mm in
nominal geometries and 9.65 mm with geom_run1_b_v40.txt's 1.75 cm
plate, which is where the overrun actually caused an overlap.

Note the degrader IS built in nominal running -- degrader_v02.txt sets
degrader.build = true, and 'off by default' means rotation = 120 deg,
out of the beam. The nominal gdml diff is therefore non-empty but
bounded: degraderOutline dimensions and degraderMother placement only,
with every other volume byte-identical.

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
./gdml_baseline.sh /tmp/surfcheck_v40.fcl emc_v40_surfcheck
grep -ic "overlap" gdml/emc_v40_surfcheck.log
```

Expected: a non-zero VD count, and an overlap count no worse than Task 4 Step 6 left it. The overlap check must use the surface-check wrapper from Task 4 Step 3 — a plain dump log never reports overlaps, so grepping one proves nothing.

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

# and the overlap check, which needs surface checking enabled
cat > $WS/Offline/Mu2eG4/geom/_scratch_ds_on_v40_surfcheck.txt <<'EOF'
#include "Offline/Mu2eG4/geom/geom_run1_b_ds_on_v40.txt"
bool g4.doSurfaceCheck             = true;
int  g4.nSurfaceCheckPointsPercmsq = 1;
int  g4.minSurfaceCheckPoints      = 100;
int  g4.maxSurfaceCheckPoints      = 10000000;
EOF
cat > /tmp/surfcheck_ds_on_v40.fcl <<'EOF'
#include "Offline/Mu2eG4/fcl/gdmldump.fcl"
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/_scratch_ds_on_v40_surfcheck.txt"
physics.producers.g4run.debug.GDMLFileName: "mu2e_ds_on_v40_surfcheck.gdml"
EOF
./gdml_baseline.sh /tmp/surfcheck_ds_on_v40.fcl ds_on_v40_surfcheck
grep -ic "overlap" gdml/ds_on_v40_surfcheck.log
```

ds_on_v40 is v40 with `degrader.rotation = 120.0`, moving the mobile target out of the beam. Expected: builds, zero overlaps. This is the most valuable overlap check in the plan — it exercises the rewritten degrader z arithmetic at a *different* rotation from the one Task 4 verified, which is exactly where a sign error would hide.

Both `_scratch_*_surfcheck.txt` files must be untracked and deleted before the final push:

```bash
rm -f $WS/Offline/Mu2eG4/geom/_scratch_v40_surfcheck.txt \
      $WS/Offline/Mu2eG4/geom/_scratch_ds_on_v40_surfcheck.txt
cd $WS/Offline && git status --porcelain   # must be clean
```

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

**Nominal impact is bounded and stated up front.** `geom_common.txt` is
never touched. Normalized gdml dumps of `geom_common.txt` and
`geom_run1_a.txt` are byte-identical to main **except** for the degrader
mother box, which the first commit deliberately resizes -- see below.
Every other volume in both dumps is unchanged, and the exact diff is
reproduced in the commit message.

**Fidelity confirmed:** a normalized gdml dump of `geom_run1_b_v40.txt`
built from this branch is identical to one built from the `Run1B`
branch. Consolidation reproduced the production geometry, it did not
approximate it.

Two commits:

1. **Degrader mother sizing.** The mother half-width came from
   `2*filter_hl + frame_hl + 1` while its children were placed from a
   different expression, so the mother was oversized and overran its
   contents downstream. Now `filter_hl + frame_hl + 0.1`.

   The filter and frame **do not move** -- their absolute z is identical
   before and after. Only the mother shrinks and re-centres: by 1.9 mm
   in nominal, 9.65 mm on v40's 1.75 cm plate, which is where the
   overrun actually produced an overlap.

   This *does* run in nominal: `degrader_v02.txt` sets
   `degrader.build = true`, and "off by default" means
   `rotation = 120` (out of the beam), not absent. So the nominal gdml
   diff is non-empty but confined to `degraderOutline` dimensions and
   `degraderMother` placement.

2. **EMC source virtual detectors,** placed only where a geometry sets
   their z, so no configuration gains detectors it did not ask for --
   the review comment on #1849 about hardcoded default z values. v40
   sets them explicitly to the values it was already receiving, so its
   output is unchanged. Whether those are the right positions for v40 is
   a separate physics question, deliberately not settled here.

**Not ported, and why:** the stopping-target radius change, the
`STMUpstream` gating, the DS2Vacuum parent-volume rework and the TSdA
cutout options all existed to support the v01-v06 single-disk
geometries, which v40 superseded. Those geometries model the Run1B
target incorrectly -- they replace the 37-foil target Run1B shares with
Run1A rather than adding the TS5 disk to it. The campaigns that used
them (Run1Baa, Run1Bag, Run1Bah, Run1Bai) are being frozen as completed
history rather than kept re-runnable. v40 needs none of this code: it
keeps the 37-foil target with its support structure, inherits
`hasSTM = true`, and has `tracker.inDS2Vacuum` commented out.

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

- [ ] **Step 2b: Document the freeze where an operator will hit it**

This is the highest-value step in the task, because the freeze fails *silently*: a Run1Baa entry re-run against main still finds `geom_run1_b_v01.txt`, then builds a world with `tracker.inDS2Vacuum` ignored and an undersized stopping-target mother. No error, wrong answer.

Create `prodtools/data/Run1B/README.md`:

```markdown
# Run1B campaign entries — geometry status

`geom_run1_b_v40.txt` and `geom_run1_b_ds_on_v40.txt` are on Offline main.
Entries using them (Run1Ban, Run1Ban-001, Run1Bap) are runnable.

## FROZEN — do not re-run against Offline main

| campaign | geometry | status |
|---|---|---|
| Run1Baa | v01 | file is on main, but the code it needs is not |
| Run1Bag, Run1Bah | v03 | geometry not on main |
| Run1Bai, Run1Bai-001, -003, -007 | v06 | geometry not on main |

v02-v06 exist only under the Offline tag `run1b-archive-2026-08-08`.

Re-running a v01 entry against main does **not** fail loudly: the geometry
file resolves, but `tracker.inDS2Vacuum` is silently ignored and the
stopping-target mother is undersized. The result is a wrong world, not an
error. Check out the archive tag to reproduce any of these campaigns.

These geometries also model the Run1B target incorrectly -- they override
`stoppingTarget.radii` to a single 600 mm disk, replacing the 37-foil target
that Run1B shares with Run1A rather than adding the TS5 disk to it. v40
supersedes them and models it correctly.
```

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
git add data/Run1B/README.md
git commit -m "docs(Run1B): mark pre-v40 campaign entries frozen

Run1Baa/Bag/Bah/Bai use geom_run1_b_v01/v03/v06, which are not
supported on Offline main after the Run1B consolidation. Records that
re-running them fails silently rather than loudly, and points at the
archive tag.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Archive the branch under a tag**

```bash
cd $WS/Offline
git tag -a run1b-archive-2026-08-08 Run1B -m "Archive of the Run1B branch at retirement.

Production geometry (v40, ds_on_v40) and the code it needs were
consolidated into Offline main. The v02-v06 single-disk stopping-target
geometries were superseded by v40 and are preserved here rather than on
main, along with the stopping-target radius, STMUpstream and DS2Vacuum
changes they require.

This tag is the only copy of v02-v06. The Run1Baa, Run1Bag, Run1Bah and
Run1Bai campaigns (prodtools data/Run1B) can only be reproduced from
here -- see data/Run1B/README.md.

See docs/superpowers/specs/2026-08-08-run1b-consolidation-design.md."
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

State on the relevant Mu2e channel that: main's default geometry is deliberately still Run1A; Run1B production is selected by naming `geom_run1_b_v40.txt`; the superseded v02–v06 geometries live under `run1b-archive-2026-08-08`; and **Run1Baa, Run1Bag, Run1Bah and Run1Bai are frozen** — not re-runnable against main, and re-running them will not error, it will quietly build the wrong world.

- [ ] **Step 6: Record the outcome in the prodtools wiki**

Write `wiki/pages/2026-XX-XX-run1b-consolidation.md` covering what landed in each PR, that `geom_common.txt` still points at Run1A, the gdml-diff method, the v40-versus-v01–v06 stopping-target reasoning, and the archive tag.

- [ ] **Step 7: Note the optional Production follow-up**

`Production/JobConfig/recoMC/NoFieldRun1B.fcl` defaults to `geom_run1_b_v01.txt` and is overridden to v40 by every prodtools caller. Changing that default to v40 would remove the last live reference to the superseded line. Separate repo, separate PR, not part of this consolidation — record it as a follow-up rather than doing it here.

---

## Self-Review

**Spec coverage.** PR1 contents → Task 3. Degrader → Task 4. EMC VD gating and v40 z values → Task 5. Fidelity check → Task 6. Branch archive and retirement → Task 7. Testing strategy → Task 1 (harness), Task 2 (baselines), gdml checks in every subsequent task. Risks: `lastEnum` → Task 2 Step 2; numeric VD ids → Task 2 Step 3; `trigger` RC 2 → Task 2 Step 4; v02–v06 recoverability → Task 7 Step 3.

**Scope revision folded in.** Earlier drafts covered three PRs and thirteen tasks, porting v01–v06 along with the stopping-target radius fix, `STMUpstream` gating, the DS2Vacuum parent-volume rework and its whitespace split. All of that existed to support the single-disk geometries, which model the Run1B target incorrectly: they override `stoppingTarget.radii` to one 600 mm disk, replacing the 37-foil target Run1B shares with Run1A instead of adding the TS5 disk to it. Only v40 models it correctly, and v40 is what current production runs.

**Correction folded in.** An intermediate draft justified this by claiming v02–v06 have no consumers. That was wrong — it came from searching the Mu2e GitHub repos but not `prodtools/data/Run1B`, which references v01 (13×), v03 (14×) and v06 (15×), including in G4 stages. The scope is unchanged, but the reason is not "nothing uses them" — it is the deliberate choice to freeze Run1Baa/Bag/Bah/Bai as completed history. Task 7 Step 2b exists because of this: the freeze fails silently, and silent wrong answers need documentation at the point of use. A second intermediate decision to delete `geom_run1_b_v01.txt` from main was withdrawn for the same reason — it is referenced by `Offline/EventDisplay/fcl/EventDisplayRun1b.fcl` inside Offline itself. The spec has been updated to match; there is no remaining disagreement between the two documents.

**Placeholder scan.** No deferred values. The EMC z positions are fixed at the values v40 already receives (`5300 / 4800 / 5830`), which makes the commit behavior-preserving; the open physics question about whether those are correct for v40 is recorded in the spec and the PR body as explicitly out of scope, not as a blocking TBD.

**Consistency.** Config key names (`zEMCSourceInMu2e`, `zEMCSource2InMu2e`, `zEMC0Front`, `degrader.build`, `degrader.rotation`, `hasTSdA`, `TSdA.rFactorForVDs`, `TrackerHasBrassRings`) are spelled identically across tasks and match the source. The harness contract `gdml_baseline.sh <fcl> <name>` → `$WS/gdml/<name>.gdml.norm` plus `$WS/gdml/<name>.log` is used consistently in all 18 invocations, and Task 4 Step 3 and Task 5 Step 6 rely on the `.log` copy that Task 1 Step 3 creates. Branch names `run1b-v40-geometry` and `run1b-degrader-and-emc-vds` are consistent between creation, push and checkout.
