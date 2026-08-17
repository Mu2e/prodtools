# MDC2020 Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the `MDC2020` branch in `Mu2e/Production` by expressing the
MDC2020 detector era as a single selectable epilog, and remove the dead POMS
campaign config the branch was also carrying.

**Architecture:** Three PRs across two repos. An Offline PR adds two CRV
epilogs alongside the existing `epilog_run1a_v01.fcl`. A Production PR deletes
the dead POMS `CampaignConfig/`. A second Production PR adds
`JobConfig/common/MDC2020.fcl`, which sets geometry and CRV together and wins
on FHiCL ordering over the Run1A defaults already in `digitize/epilog.fcl` and
`reco/epilog.fcl`.

**Tech Stack:** FHiCL, art, Mu2e Offline/Production, `fhicl-dump`, `muse`,
Musings on `/cvmfs`, bash.

**Spec:** `docs/superpowers/specs/2026-08-17-mdc2020-consolidation-design.md`

## Global Constraints

- **Never `git push`, never `gh pr create`/`gh pr edit`, never push tags,
  never delete branches.** Commit locally only. The user runs every
  outward-facing command, including the final branch deletion.
- **Never run `voms-proxy-init`.** Auth is bearer tokens; use `getToken`.
- **Never refresh the `mu2epro` token.** If it is missing, stop and report.
- **Never search `/cvmfs` with `find` or `grep -r`.** Read specific paths.
- Scratch files go under
  `/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/`, never `/tmp`.
- **Measure, do not reason.** Every claim about a resolved FCL value comes
  from `fhicl-dump`, never from reading FCL source. This is not optional
  rigor: an `outputs.X.fileName` naming a module absent from a path is
  silently ignored, and a job with the wrong CRV config exits 0.
- **Tip-to-tip diffs only** (`git diff main MDC2020`). A three-dot diff
  answers a different question and hides what main added independently.
- Repos are at
  `/exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production` (remote
  `origin` = `Mu2e/Production`) and
  `/exp/mu2e/app/users/oksuzian/muse_050125/Offline` (remote `mu2e` =
  `Mu2e/Offline`). Work on a fresh branch in each; never commit to `main`.
- Values copied out of `prolog_v11.fcl` must be **byte-identical**. Extract
  them with the `sed` commands given, never by retyping.

---

## File Structure

**Offline** (new, PR A):
- `CRVResponse/fcl/epilog_MDC2020_v01.fcl` — the five `CrvPhotons` keys at
  v11's 25 sectors. Sibling of `epilog_run1a_v01.fcl`; same shape, same role.
- `CRVReco/fcl/epilog_MDC2020_v01.fcl` — `CrvCoincidenceClusterFinder.sectorConfig`
  at v11's 25 entries.

**Production** (PR B — delete dead POMS config, 26 files):
- `CampaignConfig/` — the whole directory, removed.

**Production** (PR C — the era, 1 file):
- `JobConfig/common/MDC2020.fcl` — new. Nothing else is modified; the five
  prolog files stay untouched because the CRV era is carried by values.

**Scratch** (not committed):
- `/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/dump_era.sh` — the
  acceptance harness.

---

## Task 1: Offline CRV MDC2020 epilogs

**Files:**
- Create: `CRVResponse/fcl/epilog_MDC2020_v01.fcl`
- Create: `CRVReco/fcl/epilog_MDC2020_v01.fcl`
- Repo: `/exp/mu2e/app/users/oksuzian/muse_050125/Offline`

**Interfaces:**
- Consumes: nothing.
- Produces: two include paths used by Task 4 —
  `Offline/CRVResponse/fcl/epilog_MDC2020_v01.fcl` and
  `Offline/CRVReco/fcl/epilog_MDC2020_v01.fcl`. Both are config-level
  epilogs (no `BEGIN_PROLOG`), assigning only
  `physics.producers.CrvPhotons.*` and
  `physics.producers.CrvCoincidenceClusterFinder.sectorConfig`.

- [ ] **Step 1: Branch off current main**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
git fetch mu2e --quiet
git checkout -b crv-mdc2020-epilog mu2e/main
```

- [ ] **Step 2: Generate the CRVResponse epilog by extraction, not retyping**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
OUT=CRVResponse/fcl/epilog_MDC2020_v01.fcl
cat > $OUT <<'HDR'
# CRV configuration for MDC2020: 25 sectors, matching crv_counters_v09.
#
# Sibling of epilog_run1a_v01.fcl and used the same way -- included AFTER the
# job's prolog so that it wins. Production selects it through
# Production/JobConfig/common/MDC2020.fcl, together with the MDC2020 geometry,
# because the CRV sector list and the geometry are one decision: the counter
# set is defined by the geometry.
#
# Values are copied verbatim from CRVResponse/fcl/prolog_v11.fcl. Sectors C1
# and C2 are the CRV-Cryo-Inner modules and carry scintillation yield 0; they
# exist in the geometry only so that older files stay readable.
#
HDR
git show mu2e/main:CRVResponse/fcl/prolog_v11.fcl \
  | sed -n '/^      CRVSectors /,/^      photonYieldScaleFactor /p' \
  | sed -E 's/^      ([A-Za-z_][A-Za-z0-9_]*)( *):/physics.producers.CrvPhotons.\1\2:/' >> $OUT
```

- [ ] **Step 3: Verify the extraction produced the five expected keys and nothing else**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
grep -c '^physics\.producers\.CrvPhotons\.' CRVResponse/fcl/epilog_MDC2020_v01.fcl
grep -o '^physics\.producers\.CrvPhotons\.[A-Za-z]*' CRVResponse/fcl/epilog_MDC2020_v01.fcl
```

Expected: `5`, and exactly these names in order — `CRVSectors`,
`reflectors`, `lookupTableFileNames`, `scintillationYields`,
`photonYieldScaleFactor`.

If the count or names differ, the `sed` did not match; fix the expression
rather than hand-editing the values. The `-E` pattern deliberately anchors on
"six spaces then an identifier then a colon", so it rewrites key lines and
leaves continuation lines and comments alone — a plain `s/^      /` prefixes
all 50 lines and corrupts the arrays. Both commands in this task were run
against `mu2e/main` before this plan was written and produce exactly the
counts stated.

- [ ] **Step 4: Verify the values are byte-identical to v11**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
diff <(git show mu2e/main:CRVResponse/fcl/prolog_v11.fcl \
        | sed -n '/^      CRVSectors /,/^      photonYieldScaleFactor /p' \
        | tr -d ' \t') \
     <(grep -v '^#' CRVResponse/fcl/epilog_MDC2020_v01.fcl \
        | sed 's/^physics\.producers\.CrvPhotons\./      /' | tr -d ' \t') \
  && echo "IDENTICAL"
```

Expected: `IDENTICAL`. Whitespace is stripped on both sides because only the
values matter; any other difference is a real one.

- [ ] **Step 5: Generate the CRVReco epilog by extraction**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
OUT=CRVReco/fcl/epilog_MDC2020_v01.fcl
cat > $OUT <<'HDR'
# CRV coincidence configuration for MDC2020: 25 sectors, matching
# crv_counters_v09. Sibling of epilog_run1a_v01.fcl; see
# CRVResponse/fcl/epilog_MDC2020_v01.fcl for the rationale.
#
# Copied verbatim from CRVReco/fcl/prolog_v11.fcl. The list is restated in
# full rather than appended to, because what it overrides depends on whether
# epilog_run1a_v01.fcl ran first -- and that leaves a 2-entry list, not a
# 23-entry one.
#
HDR
{ echo 'physics.producers.CrvCoincidenceClusterFinder.sectorConfig :'
  git show mu2e/main:CRVReco/fcl/prolog_v11.fcl \
    | sed -n '/^      sectorConfig :/,/^      \]/p' | sed '1d'
} >> $OUT
```

- [ ] **Step 6: Verify 25 sector entries and balanced brackets**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
grep -c 'CRVSector *:' CRVReco/fcl/epilog_MDC2020_v01.fcl
python3 -c "
s=open('CRVReco/fcl/epilog_MDC2020_v01.fcl').read()
print('braces', s.count('{')==s.count('}'), 'brackets', s.count('[')==s.count(']'))"
```

Expected: `25`, then `braces True brackets True`.

The list closes with `]`, not `}` — an end pattern of `/^      }/` runs past
the list and captures a stray brace, which is why the range above anchors on
`/^      \]/`. Verified before this plan was written: 25 entries, balanced
brackets, first value line `[`, last `]`, and the final four sectors
`C1 C2 C3 C4`.

- [ ] **Step 7: Prove both files parse, standalone**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
muse setup SimJob MDC2025au
cd /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
printf '#include "Production/JobConfig/reco/NoField.fcl"\n#include "Offline/CRVReco/fcl/epilog_MDC2020_v01.fcl"\n' > parse_reco.fcl
FHICL_FILE_PATH="/exp/mu2e/app/users/oksuzian/muse_050125:$FHICL_FILE_PATH" \
  fhicl-dump parse_reco.fcl > /dev/null && echo "CRVReco epilog parses"
```

Expected: `CRVReco epilog parses`. A syntax error prints a line number —
fix the extraction range, not the values.

Note: `FHICL_FILE_PATH` is **prepended** here so the working copy's Offline
wins over the Musing's. Every dump in this plan prepends, for the same
reason: the point is to test the working copy, not the pinned release.

Append instead when you must NOT disturb a pinned release — overlaying a
single new file onto a Musing that has to keep winning everywhere else. That
is what `verify_overlay.py` does for Run1B. Choosing the wrong direction
silently dumps the wrong file: no error, just an answer about someone else's
code.

- [ ] **Step 8: Commit**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/Offline
git add CRVResponse/fcl/epilog_MDC2020_v01.fcl CRVReco/fcl/epilog_MDC2020_v01.fcl
git commit -m "feat(CRV): add MDC2020 CRV epilogs, siblings of epilog_run1a_v01

MDC2020 uses crv_counters_v09 with 25 CRV sectors; the current era uses
crv_counters_v10 with 23, and epilog_run1a_v01.fcl reduces both to T1/T2.
Production needs to select the MDC2020 sector set per job, and a prolog
include cannot be un-chosen by a later line -- so the values are exposed as
an epilog, exactly as run1a already is.

Values copied verbatim from prolog_v11.fcl. No existing file changes, so no
current job is affected."
```

---

## Task 2: Delete the dead POMS campaign config

**Files:**
- Delete: `CampaignConfig/` (26 files, ~143 KB)
- Repo: `/exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production`

**Interfaces:**
- Consumes: nothing.
- Produces: a repo with no `CampaignConfig/`. No other task depends on it.

`CampaignConfig/` holds POMS campaign definitions — `.cfg` and `.ini` files
of `job_setup.prescript_N` lines. The POMS backend was removed from prodtools
on 2026-08-08. Nothing in Production references the directory, and it was
last modified 2025-08-01.

- [ ] **Step 1: Prove nothing references it, before deleting**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git checkout -b drop-campaignconfig origin/main
git grep -n 'CampaignConfig' -- . ':!CampaignConfig' || echo "NO EXTERNAL REFERENCES"
git ls-tree -r --name-only HEAD CampaignConfig/ | wc -l
```

Expected: `NO EXTERNAL REFERENCES`, then `26`.

If anything outside the directory references it, **stop and report** — the
deletion is not safe and this task is BLOCKED.

- [ ] **Step 2: Delete the directory**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git rm -r --quiet CampaignConfig/
git status --short | head -5
git ls-files CampaignConfig/ | wc -l
```

Expected: the last command prints `0`.

- [ ] **Step 3: Record what this orphans, without acting on it**

`CampaignConfig/` was the only in-repo consumer of the `Scripts/gen_*.sh`
family. Those 30-odd scripts are now referenced by nothing inside Production.

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git grep -l 'gen_[A-Z]' -- . ':!Scripts' || echo "gen_ scripts now unreferenced in-repo"
```

Do **not** delete them and do **not** modify them. `Scripts/` is out of scope
for this whole plan by decision — it stays exactly as it is on `main`. The
scripts may still be invoked by hand or from `production_manager/` outside
this repo. Note the finding in the task report so the user can decide
separately.

- [ ] **Step 4: Commit**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git commit -q -m "chore: delete CampaignConfig, the dead POMS campaign definitions

26 files of POMS .cfg/.ini job descriptions. The POMS backend was removed
from prodtools on 2026-08-08, nothing in this repo references the directory,
and it was last modified 2025-08-01.

This leaves the Scripts/gen_*.sh family with no in-repo consumer. They are
kept: they may still be run by hand or from production_manager, and
gen_Digitize.sh and gen_Mix.sh are being repaired separately."
git show --stat --oneline HEAD | tail -3
```

---

## Task 3: Build the era acceptance harness

**Files:**
- Create: `/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/dump_era.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `dump_era.sh <entry.fcl> <out.json> [extra_include.fcl]` — dumps
  an entry point's resolved config and extracts the eight keys that define
  the era, as sorted JSON suitable for `diff`. Used by Tasks 5 and 5.

This exists before the era file so that the era file's effect is measured
from the first moment, not asserted and checked later.

- [ ] **Step 1: Write the harness**

```bash
mkdir -p /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
cat > /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/dump_era.sh <<'EOF'
#!/bin/bash
# dump_era.sh <entry.fcl> <out.json> [extra_include.fcl]
#
# Resolve an entry point and extract the keys that define the detector era.
# Reading FCL source cannot answer what a job actually runs: includes land in
# an order no single file shows, and a later assignment silently wins.
set -euo pipefail
ENTRY="$1"; OUT="$2"; EXTRA="${3:-}"
WORK=$(mktemp -d /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/dump.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

{ echo "#include \"$ENTRY\""
  [ -n "$EXTRA" ] && echo "#include \"$EXTRA\""
} > "$WORK/t.fcl"

fhicl-dump "$WORK/t.fcl" > "$WORK/dump.txt"

python3 - "$WORK/dump.txt" "$OUT" <<'PY'
import json,re,sys
txt=open(sys.argv[1]).read()
KEYS=["services.GeometryService.inputFile",
      "services.GeometryService.bFieldFile",
      "physics.producers.CrvPhotons.CRVSectors",
      "physics.producers.CrvPhotons.reflectors",
      "physics.producers.CrvPhotons.lookupTableFileNames",
      "physics.producers.CrvPhotons.scintillationYields",
      "physics.producers.CrvPhotons.photonYieldScaleFactor",
      "physics.producers.CrvCoincidenceClusterFinder.sectorConfig"]
# fhicl-dump emits an indented tree; walk it and record the full dotted path.
out={}; stack=[]
for line in txt.splitlines():
    if not line.strip() or line.lstrip().startswith('#'): continue
    ind=len(line)-len(line.lstrip())
    while stack and stack[-1][0]>=ind: stack.pop()
    m=re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$', line)
    if not m: continue
    name,val=m.group(1),m.group(2).strip()
    path='.'.join([s[1] for s in stack]+[name])
    if val in ('{','['):
        stack.append((ind,name))
        if path in KEYS: out[path]='<<OPEN>>'
    elif path in KEYS:
        out[path]=val
# Structured values need the raw block, so capture them verbatim by brace match.
for k in KEYS:
    if out.get(k)=='<<OPEN>>':
        leaf=k.split('.')[-1]
        i=txt.find(leaf+' ')
        j=txt.find(leaf+':') if i<0 else i
        seg=txt[j:]
        depth=0; end=0
        for n,ch in enumerate(seg):
            if ch in '[{': depth+=1
            elif ch in ']}':
                depth-=1
                if depth==0: end=n+1; break
        out[k]=re.sub(r'\s+',' ',seg[:end]).strip()
json.dump(out, open(sys.argv[2],'w'), indent=2, sort_keys=True)
print(f"{sys.argv[2]}: {len(out)}/{len(KEYS)} keys")
PY
EOF
chmod +x /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/dump_era.sh
```

- [ ] **Step 2: Prove the harness reports the Run1A baseline main actually runs**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops && muse setup SimJob MDC2025au
S=/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
FHICL_FILE_PATH="/exp/mu2e/data/users/oksuzian/claude-scratch/run1b:$FHICL_FILE_PATH" \
  $S/dump_era.sh Production/JobConfig/digitize/OnSpill.fcl $S/base_onspill.json
python3 -c "
import json; d=json.load(open('$S/base_onspill.json'))
print('CRVSectors        :', d['physics.producers.CrvPhotons.CRVSectors'])
print('photonYieldScale  :', d['physics.producers.CrvPhotons.photonYieldScaleFactor'])
print('geometry          :', d['services.GeometryService.inputFile'])"
```

Expected — and this is the measurement the whole design rests on:
`CRVSectors` is `["T1","T2"]`, `photonYieldScaleFactor` is `0.90`, geometry
is `geom_common.txt`. That proves `epilog_run1a_v01.fcl` overwrites the CRV
prolog, and therefore that a later epilog can overwrite run1a.

If `CRVSectors` instead shows 23 or 25 entries, the run1a epilog did not run
for this entry point, the premise is wrong, and the task is **BLOCKED** —
report it rather than proceeding.

- [ ] **Step 3: Confirm all eight keys resolve**

```bash
S=/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
python3 -c "
import json; d=json.load(open('$S/base_onspill.json'))
missing=[k for k in d if d[k] in (None,'','<<OPEN>>')]
print('unresolved:', missing or 'none')
print('sectorConfig entries:', d['physics.producers.CrvCoincidenceClusterFinder.sectorConfig'].count('CRVSector'))"
```

Expected: `unresolved: none`, and `sectorConfig entries: 2` (run1a's T1/T2).

- [ ] **Step 4: Commit the harness path into the plan record only**

The harness is scratch and is not committed to either repo. Record its path
and the baseline JSON in the task report so later tasks reuse it.

---

## Task 4: Production MDC2020.fcl

**Files:**
- Create: `JobConfig/common/MDC2020.fcl`
- Repo: `/exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production`

**Interfaces:**
- Consumes: the two Offline epilogs from Task 1, by path. Requires Task 1's
  branch to be present in the Offline working copy used for dumping.
- Produces: `Production/JobConfig/common/MDC2020.fcl`, the single selector
  for the MDC2020 era. In prodtools it belongs in the `#include_first` slot
  (`prod_utils.COMMON_INCLUDE_KEY`); outside prodtools it must be included
  last, by hand.

- [ ] **Step 1: Branch**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git checkout -b mdc2020-era-epilog origin/main
```

- [ ] **Step 2: Write the file**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
cat > JobConfig/common/MDC2020.fcl <<'EOF'
#
# Detector configuration for MDC2020: geometry and CRV, set together.
#
# Include this LAST, after the job's own epilogs, so that it wins:
#
#   #include "Production/JobConfig/common/MDC2020.fcl"
#
# In prodtools that is the '#include_first' slot, which write_fcl_template
# emits after the whole base FCL -- so it lands after digitize/epilog.fcl and
# reco/epilog.fcl, and beats the Run1A CRV values those pull in. Included any
# earlier it is silently overwritten by Run1A's two-sector config and the job
# still exits 0.
#
# Geometry and CRV are ONE decision. geom_common_MDC2020 resolves to
# geom_2021_PhaseI_v03, which carries crv_counters_v09 and its 25 CRV
# sectors; the current geometry carries crv_counters_v10 and 23. Changing one
# without the other gives a job that runs and is wrong.
#
# Unlike Run1B.fcl this sets NO bFieldFile. No MDC2020 stage overrides the
# field, so each takes it from its own base FCL's epilog. Adding a field key
# here would silently move all of them. Do not add one for symmetry.
#
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/geom_common_MDC2020.txt"

#include "Offline/CRVResponse/fcl/epilog_MDC2020_v01.fcl"
#include "Offline/CRVReco/fcl/epilog_MDC2020_v01.fcl"
EOF
```

- [ ] **Step 3: Dump a digitize entry point with the era applied**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops && muse setup SimJob MDC2025au
S=/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
P="/exp/mu2e/data/users/oksuzian/claude-scratch/run1b:/exp/mu2e/app/users/oksuzian/muse_050125"
FHICL_FILE_PATH="$P:$FHICL_FILE_PATH" \
  $S/dump_era.sh Production/JobConfig/digitize/OnSpill.fcl $S/era_onspill.json \
                 Production/JobConfig/common/MDC2020.fcl
python3 -c "
import json; d=json.load(open('$S/era_onspill.json'))
print('geometry         :', d['services.GeometryService.inputFile'])
print('CRVSectors count :', d['physics.producers.CrvPhotons.CRVSectors'].count(','))
print('photonYieldScale :', d['physics.producers.CrvPhotons.photonYieldScaleFactor'])
print('sectorConfig     :', d['physics.producers.CrvCoincidenceClusterFinder.sectorConfig'].count('CRVSector'))"
```

Expected: geometry `geom_common_MDC2020.txt`; `CRVSectors count` 24 (25
entries, 24 commas); `photonYieldScale` `0.84`; `sectorConfig` `25`.

`photonYieldScale` still reading `0.90` is the specific failure this step
exists to catch — it means Run1A's aging factor survived.

- [ ] **Step 4: Prove the era matches the branch exactly**

The branch is the reference implementation. Its resolved config for the same
entry point must match ours key for key.

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
git -C /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production \
    worktree add -f /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/branchref origin/MDC2020
S=/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
FHICL_FILE_PATH="$S/branchref/..:$FHICL_FILE_PATH" \
  $S/dump_era.sh Production/JobConfig/digitize/OnSpill.fcl $S/branch_onspill.json
python3 - <<'PY'
import json
S='/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020'
a=json.load(open(f'{S}/branch_onspill.json')); b=json.load(open(f'{S}/era_onspill.json'))
bad=[k for k in a if k!='services.GeometryService.bFieldFile' and a[k]!=b.get(k)]
print("MATCH" if not bad else "DIFFER: "+", ".join(bad))
for k in bad:
    print(f"  branch: {a[k][:120]}")
    print(f"  ours  : {str(b.get(k))[:120]}")
PY
```

Expected: `MATCH`.

`bFieldFile` is excluded deliberately: the branch and main may differ there
for reasons unrelated to the era, and `MDC2020.fcl` sets no field key by
design. If it is the *only* difference, that is expected; if it differs,
record the two values in the task report so the reviewer can judge.

The worktree must be created with the branch checked out at
`$S/branchref`; note that its `Production` directory is
`$S/branchref` itself, so `FHICL_FILE_PATH` points at its parent.

- [ ] **Step 5: Repeat for the remaining entry points**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
S=/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
P="/exp/mu2e/data/users/oksuzian/claude-scratch/run1b:/exp/mu2e/app/users/oksuzian/muse_050125"
for e in digitize/OffSpill mixing/Mix reco/NoField recoMC/NoField; do
  n=$(echo $e | tr / _)
  FHICL_FILE_PATH="$P:$FHICL_FILE_PATH" \
    $S/dump_era.sh Production/JobConfig/$e.fcl $S/era_$n.json \
                   Production/JobConfig/common/MDC2020.fcl
  FHICL_FILE_PATH="$S/branchref/..:$FHICL_FILE_PATH" \
    $S/dump_era.sh Production/JobConfig/$e.fcl $S/branch_$n.json
  python3 -c "
import json
a=json.load(open('$S/branch_$n.json')); b=json.load(open('$S/era_$n.json'))
bad=[k for k in a if k!='services.GeometryService.bFieldFile' and a[k]!=b.get(k)]
print('$e:', 'MATCH' if not bad else 'DIFFER '+str(bad))"
done
```

Expected: `MATCH` for every entry point.

If an entry point does not exist under that exact path, list
`JobConfig/<dir>/` and substitute the real name rather than skipping it —
a skipped entry point is an unverified one, and must be named in the report.

- [ ] **Step 6: Prove current-era production is untouched**

`MDC2020.fcl` adds a file and changes nothing else, so a job that does not
include it must be bit-identical. Demonstrate rather than assume.

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
S=/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
P="/exp/mu2e/data/users/oksuzian/claude-scratch/run1b:/exp/mu2e/app/users/oksuzian/muse_050125"
FHICL_FILE_PATH="$P:$FHICL_FILE_PATH" \
  $S/dump_era.sh Production/JobConfig/digitize/OnSpill.fcl $S/after_onspill.json
diff $S/base_onspill.json $S/after_onspill.json && echo "CURRENT ERA UNCHANGED"
```

Expected: `CURRENT ERA UNCHANGED`. `base_onspill.json` is from Task 3 Step 2.

- [ ] **Step 7: Commit**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git add JobConfig/common/MDC2020.fcl
git commit -m "feat(config): add MDC2020.fcl, a single detector-configuration epilog

Geometry and CRV set together, in the shape Run1B.fcl established. The MDC2020
geometry carries crv_counters_v09 and its 25 CRV sectors; selecting one
without the other produces a job that runs and is wrong.

It works by winning on order rather than by changing anything: emitted after
the base FCL, it lands after digitize/epilog.fcl and reco/epilog.fcl and
overwrites the Run1A CRV values those pull in. No existing file is modified
and no current-era job is affected -- verified by dumping OnSpill with and
without the new file and diffing the resolved era keys.

Requires the Offline CRV MDC2020 epilogs to merge first.

Verified against the MDC2020 branch: identical resolved config for
digitize/OnSpill, digitize/OffSpill, mixing/Mix, reco/NoField and
recoMC/NoField."
```

---

## Task 5: Run a real MDC2020 job, and settle MakeSS from the result

**Files:**
- Modify (conditionally): `JobConfig/common/MDC2020.fcl`
- Repo: `/exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production`

**Interfaces:**
- Consumes: `MDC2020.fcl` from Task 4.
- Produces: either two extra lines in `MDC2020.fcl`, or a documented finding
  that they are unnecessary.

This task carries two jobs at once, because one run answers both.

**It is the end-to-end acceptance for the whole era switch.** MDC2020 is a
live production line — `SimJob/MDC2020bi` generated about 7,000 mixing dig
files in June 2026, and `MDC2020bj` (cut 2026-07-12) is loaded and unused.
Task 4 proves the resolved config matches the branch, which is necessary but
not sufficient: a config can resolve identically and still fail to run. With
MDC2020 validation retired by decision, this run is the *only* execution
evidence in the plan. It is not optional and it is not satisfied by a dump.

**It also settles `MakeSS`.** The branch adds
`MakeSS : { module_type : NullProducer }` to `Digitize.producers` and inserts
`MakeSS` into `Digitize.DigitizeSequence`, commented "temporary patch for
older MDC2020 output". Nothing on main consumes a product labelled `MakeSS`
in the digitize path — `compressDigiMCs.surfaceStepTags` is
`["compressDetStepMCs"]` — so its purpose cannot be determined by reading.

Prefer an `MDC2020bi`-era input over an older one: it is what production
actually last ran.

- [ ] **Step 1: Resolve a real MDC2020 dts input**

Use `dts.mu2e.CeMLeadingLog.MDC2020at.art` (2000 files). CeMLeadingLog is
what the June 2026 `MDC2020bi` round actually mixed, so it is representative
of live production rather than an arbitrary old dataset.

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
getToken > /dev/null
samweb count-files 'defname: dts.mu2e.CeMLeadingLog.MDC2020at.art'   # expect 2000
F=$(samweb list-files 'defname: dts.mu2e.CeMLeadingLog.MDC2020at.art with limit 1')
echo "FILE=$F"
samweb locate-file "$F"
URL=$(samweb get-file-access-url --schema=root "$F" | head -1)
echo "URL=$URL"
```

The correct listing syntax is `--defname` with SQL `%` wildcards, e.g.
`samweb list-definitions --defname 'dts.mu2e.%.%MDC2020%art'` (150 results).
There is **no** `--defname-contains` flag; passing it prints a usage error,
and piping that to `wc -l` silently reports `0`, which reads exactly like
"nothing exists".

**Check residency before running.** These files are `enstore:...(nearline)`
— on tape. An evicted file will stall rather than fail cleanly. If `mdh`
reports it is not on disk, pick another file from the definition or prestage
it; do **not** copy tape→disk.

If no MDC2020 dts file can be read, this task is **BLOCKED** — report it and
do not guess whether `MakeSS` is needed.

- [ ] **Step 2: Run a digitize job on that input without MakeSS**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
getToken > /dev/null
muse setup ops && muse setup SimJob MDC2025au
printf '#include "Production/JobConfig/digitize/OnSpill.fcl"\n#include "Production/JobConfig/common/MDC2020.fcl"\nsource.maxEvents: 5\n' > makess_off.fcl
P="/exp/mu2e/data/users/oksuzian/claude-scratch/run1b:/exp/mu2e/app/users/oksuzian/muse_050125"
FHICL_FILE_PATH="$P:$FHICL_FILE_PATH" \
  mu2e -c makess_off.fcl -s "$URL" -n 5 > makess_off.log 2>&1
echo "exit=$?"; tail -20 makess_off.log
```

`$URL` is from Step 1 and must still be set in this shell. An
`Auth failed: No protocols left to try` from `TNetXNGFile::Open` means the
bearer token expired — re-run `getToken`, do not switch to a copied file.

- [ ] **Step 3: Decide from the result**

- **Exit 0** → `MakeSS` is not needed. Record the evidence and skip Step 4.
  Do not add it "for safety": an unnecessary NullProducer in the path is a
  future puzzle for whoever reads it.
- **Non-zero, complaining about a missing SurfaceStep product** → it is
  needed. Proceed to Step 4.
- **Non-zero for any other reason** → that is a different problem, and with
  MDC2020 validation retired there is no other net to catch it. Report it and
  stop. Do not add `MakeSS` to make an unrelated error go away, and do not
  proceed to Task 6 with a failing job: an unexplained failure here means the
  era switch is not proven and the branch is not safe to delete.

- [ ] **Step 4: If needed, add MakeSS to MDC2020.fcl**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
cat >> JobConfig/common/MDC2020.fcl <<'EOF'

# MDC2020 dts files predate SurfaceSteps. A NullProducer supplies the empty
# collection the digitize path expects. This lives here, not in
# digitize/prolog.fcl, so it cannot reach a current-era job.
physics.producers.MakeSS : { module_type : NullProducer }
physics.DigitizePath : [ MakeSS, @sequence::physics.DigitizePath ]
EOF
```

`physics.DigitizePath` is defined as
`DigitizePath : @local::Digitize.DigitizeSequence` in
`JobConfig/digitize/Digitize.fcl`, so it is a config-level path and the
self-reference resolves. `MakeSS` is prepended rather than placed after
`PBISim` as on the branch; it has no inputs, so position within the path does
not matter as long as it precedes `compressDigiMCs`.

This applies only to digitize and mixing entry points. If a reco-only job
fails because `physics.DigitizePath` does not exist there, split the two
lines into `JobConfig/common/MDC2020_digi.fcl` and have digitize/mixing jobs
include both — report that outcome rather than deciding silently.

- [ ] **Step 5: Re-run the job and confirm it now succeeds**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
P="/exp/mu2e/data/users/oksuzian/claude-scratch/run1b:/exp/mu2e/app/users/oksuzian/muse_050125"
FHICL_FILE_PATH="$P:$FHICL_FILE_PATH" \
  mu2e -c makess_off.fcl -s "$URL" -n 5 > makess_on.log 2>&1
echo "exit=$?"; grep -E 'TrigReport|Art has completed' makess_on.log | tail -5
```

Expected: exit 0 and `Art has completed and will exit with status 0`.

- [ ] **Step 6: Re-run Task 4 Step 6 to confirm current-era jobs are still untouched**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
S=/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020
P="/exp/mu2e/data/users/oksuzian/claude-scratch/run1b:/exp/mu2e/app/users/oksuzian/muse_050125"
FHICL_FILE_PATH="$P:$FHICL_FILE_PATH" \
  $S/dump_era.sh Production/JobConfig/digitize/OnSpill.fcl $S/after2_onspill.json
diff $S/base_onspill.json $S/after2_onspill.json && echo "CURRENT ERA STILL UNCHANGED"
```

Expected: `CURRENT ERA STILL UNCHANGED`.

- [ ] **Step 7: Commit (only if Step 4 ran)**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git add JobConfig/common/MDC2020.fcl
git commit -m "feat(config): supply the SurfaceStep collection MDC2020 inputs lack

MDC2020 dts files predate SurfaceSteps, so the digitize path needs a
NullProducer to stand in. Kept in MDC2020.fcl rather than digitize/prolog.fcl
so it cannot reach a current-era job.

Measured, not assumed: a digitize job on a real MDC2020 dts input fails
without it and completes with status 0 with it, and the resolved config of a
current-era OnSpill job is unchanged."
```

---

## Task 6: Confirm the branch is fully absorbed

**Files:**
- Create: `/exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/residual.txt`

**Interfaces:**
- Consumes: the branches from Tasks 1, 2, 4, 5.
- Produces: an explicit account of every remaining tip-to-tip difference,
  each classified. This is what justifies deleting the branch.

- [ ] **Step 1: Compute the residual against a merged view**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git checkout -b absorbed-check origin/main
git merge --no-commit --no-ff drop-campaignconfig || true
git merge --no-commit --no-ff mdc2020-era-epilog || true
git commit -m "wip: combined view for residual check" || true
git diff --name-only HEAD origin/MDC2020 > /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/residual.txt
wc -l < /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/residual.txt
```

- [ ] **Step 2: Classify every residual file**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
while read -r f; do
  m=$(git cat-file -e HEAD:"$f" 2>/dev/null && echo y || echo n)
  b=$(git cat-file -e origin/MDC2020:"$f" 2>/dev/null && echo y || echo n)
  if   [ "$m$b" = yn ]; then echo "MAIN-ONLY      $f"
  elif [ "$m$b" = ny ]; then echo "BRANCH-ONLY    $f"
  else echo "BOTH-DIFFER    $f"; fi
done < /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/residual.txt | sort | uniq -c | sort -rn
```

Every residual file must fall into one of these, and each `BOTH-DIFFER` and
`BRANCH-ONLY` file must be named in the report with its reason:

- **MAIN-ONLY** — main moved forward; discarded with the branch. No action.
- **BOTH-DIFFER, MDC2020-era `Validation/`** — retired by design
  (`Validation/{ceDigi,ceMix,ceSimReco,ceSteps,cosmicOffSpill,cosmicSimReco,muDauSteps,potSim}.fcl`).
- **BOTH-DIFFER, Extracted** — out of scope by design
  (`JobConfig/cosmic/Extracted{CORSIKA,CRY}.fcl`,
  `JobConfig/{digitize,reco,recoMC}/Extracted.fcl`,
  `JobConfig/cosmic/geom_cosmic_extracted.txt`).
- **BRANCH-ONLY** — expected to be exactly
  `JobConfig/common/epilog_Extracted.fcl`, out of scope.
- **`CampaignConfig/*`** — deleted on main by Task 2; the branch's copies go
  with the branch. Expect 26 files in this category and no action.
- **Output-module rename (`Validation/*`, `Scripts/gen_*.sh`,
  `data/merge_filter.json`)** — the branch renames `TriggeredOutput` /
  `TriggerableOutput` to the single `Output` module that actually exists.
  Correct, but out of scope by decision: nothing consumes those Validation
  art outputs and `valJobCheck.sh` only checks return codes, so the rename
  buys nothing today. Expect ~30 files here and take none of them. Main keeps
  the dead names.
- **BOTH-DIFFER, the five prologs and two epilogs** — expected: the branch
  pins `prolog_v11` and deletes the run1a includes, which our design replaces
  with value reassignment. Confirm the *only* difference in each is the CRV
  include line (and, for `digitize/prolog.fcl`, `MakeSS`):

```bash
for f in JobConfig/{digitize,reco,recoMC,pileup,primary}/prolog.fcl \
         JobConfig/{digitize,reco}/epilog.fcl JobConfig/common/epilog.fcl; do
  echo "--- $f"; git diff HEAD origin/MDC2020 -- $f | grep -E '^[+-][^+-]'
done
```

**Anything that does not fit a category above is an unabsorbed change and
must be reported, not waved through.** The branch is only safe to delete
once every residual line is accounted for.

- [ ] **Step 3: Write the residual report**

Record in the task report: the count per category, the full `BOTH-DIFFER`
and `BRANCH-ONLY` lists with reasons, and an explicit statement that no
unclassified file remains.

- [ ] **Step 4: Clean up the scratch worktree**

```bash
cd /exp/mu2e/data/users/oksuzian/claude-scratch/run1b/Production
git worktree remove --force /exp/mu2e/data/users/oksuzian/claude-scratch/mdc2020/branchref
git branch -D absorbed-check
```

`absorbed-check` is a local scratch branch created by this task; deleting it
is not the branch deletion the Global Constraints reserve for the user. The
`MDC2020` branch itself is **never** deleted here.

- [ ] **Step 5: Hand off**

Report to the user, in this order:

1. The three PRs to push, with their branches:
   `crv-mdc2020-epilog` (Offline), `drop-campaignconfig` and
   `mdc2020-era-epilog` (Production). They are independent of each other
   except that the Offline one must merge first.
2. That the **Offline PR must merge first** — `MDC2020.fcl` does not resolve
   without it.
3. **Sequencing.** `SimJob/MDC2020bj` was cut 2026-07-12 and has produced
   nothing; on the observed six-week cadence a round is about due. Ask
   whether one is planned before the branch is deleted, and say plainly that
   deleting it mid-round changes what a live campaign resolves against.
   MDC2020 validation is retired by decision, so there is no automated signal
   that would catch this.
4. The residual report from Step 3, as the evidence for deleting the
   `MDC2020` branch — which remains the user's action.

---

## Notes for the implementer

**On `fhicl-dump` and `FHICL_FILE_PATH`.** Prepend the working copy when
testing a file you are editing; append it when overlaying onto a pinned
Musing that must otherwise win. Getting this backwards produces a dump of the
wrong file with no error.

**On the acceptance test.** `MATCH` against the branch is the bar. If a
comparison differs, do not adjust the harness to make it pass — the harness
extracts what the job actually runs, and a difference is a real one until
explained.

**On silent success.** Every failure mode in this work exits 0: a rename that
addresses nothing, an epilog included too early, a geometry pinned without
its CRV counterpart. No step here is satisfied by "the job ran". Each names
the specific value to read back.
