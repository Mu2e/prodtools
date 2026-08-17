# Consolidating the MDC2020 branch into main (Mu2e/Production)

**Date:** 2026-08-17
**Repo:** `Mu2e/Production`
**Status:** design, approved for planning

## Goal

Delete the `MDC2020` branch. Express everything it carries either as a fix
that belongs on `main` unconditionally, or as a per-job era selection —
`Production/JobConfig/common/MDC2020.fcl` — in the same shape as
`Production/JobConfig/common/Run1B.fcl` (PR #569, merged 2026-08-16).

## Background

`MDC2020` diverges from `main` by 51 files, 153 insertions, 152 deletions
(merge-base `12a0eab8`, 2026-07-10). The branch is still live: its most
recent commit (`dffc707f`, Andrew Edmonds, 2026-08-12) adds parameters so
EventNtuple `from_dig.fcl` can read MDC2020 files.

**MDC2020 is a live production line, not an archive.** Musings are cut on a
roughly six-week cadence — `MDC2020bg` 2026-03-02, `bh` 2026-05-06, `bi`
2026-06-01, `bj` 2026-07-12 — and `MDC2020bi` generated about 7,000 mixing
dig files between 2026-06-01 and 06-08 (`CeMLeadingLogMix{1,2}BB`,
`FlateMinusMix{1,2}BB`, `CosmicCRYSignalAllMix{1,2}BB`, all at
`MDC2020bi_best_v1_3`). `SimJob/MDC2020bj` carries this branch
(`geom_common_MDC2020.txt`, `prolog_v11.fcl`) and has produced nothing yet.

An earlier draft of this spec claimed MDC2020 was "consumed, not generated",
inferred from `prodtools/data/` holding no MDC2020 entries. That inference
was wrong: it shows MDC2020 is not driven from that checkout, not that
nobody runs it. The consolidation therefore has to work for generation, and
it lands under a line that is actively producing.

Every *value* MDC2020 needs already exists on Offline `main`:
`geom_common_MDC2020.txt`, `CRVResponse/fcl/prolog_v11.fcl`,
`CRVReco/fcl/prolog_v11.fcl`. The branch is pure selection among files that
all ship today. One small Offline PR is still needed to expose the CRV
values as epilogs — see the correction below.

All measurements here are tip-to-tip (`git diff main MDC2020`). A three-dot
diff is the wrong tool for "what does the branch still owe main": it diffs
from the merge-base and so hides everything main added independently. It
initially made `TimeClusterCollections` look outstanding when main had
already added it in `e907cbc4`.

### The inversion

28 of the 51 diverging files are not MDC2020 configuration. They are a bug
on `main` that the branch already fixed.

`JobConfig/digitize/prolog.fcl` on main defines exactly one output module:

```fcl
Digitize.Outputs : { Output : @local::Digitize.Output }
Digitize.EndPath : [ @sequence::Digitize.EndSequence, Output ]
```

and `JobConfig/mixing/Mix.fcl` consumes it wholesale
(`outputs : @local::Digitize.Outputs`). There is no `TriggeredOutput` and no
`TriggerableOutput`. Yet 31 files on main — 110 references — still assign to
those names.

art only constructs output modules that appear in a path, so the stray
`outputs.TriggeredOutput` table is never instantiated. The assignment does
not error; it silently does nothing. Evidence from the nightly of
2026-08-16, built from main:

```
/exp/mu2e/app/users/mu2epro/nightly2/current/
  dig.owner.desc.version.sequencer.art   Aug 16 00:28
```

That is the placeholder `fileName` from the prolog. Three validation jobs
name distinct outputs and all three land on it:

| file | intends | gets |
|---|---|---|
| `Validation/ceDigi.fcl` | `dts.owner.ceDigi.dsconf.seq.art` | placeholder |
| `Validation/ceMix.fcl` | `dig.owner.ceMix.dsconf.seq.art` | placeholder |
| `Validation/cosmicOffSpill.fcl` | `dig.owner.cosmicOffSpill.seq.art` | placeholder |

None of those three filenames exists in `nightly2/current/`; the single
placeholder file does, and the nightly reports `OK ceMix`. Exit 0, wrong
result.

The same mechanism makes `CaloOutput`, `TrkOutput`, `DiagOutput` and
`UntriggeredOutput` dead in the `Validation/nightly/ceMix_NN.fcl` files;
the branch renamed only `TriggeredOutput` and left those in place.

The single stream is deliberate, not an accident to be undone. The
Triggered and Triggerable streams were consolidated, and `Output` carries
both selections — `JobConfig/digitize/OnSpill.fcl`:

```fcl
outputs.Output.SelectEvents : [@sequence::Digitize.SignalTriggers, @sequence::physics.TriggerablePaths ]
```

So the stale assignments are simply un-finished work, and deleting them
completes the consolidation rather than dropping a stream.

### What is genuinely MDC2020-specific

The residual files, after dropping Extracted (see Non-goals):

| class | files | mechanism |
|---|---|---|
| CRV era values (v11's 25 sectors, vs run1a's 2) | 5 prologs | reassigned by epilog; prologs untouched |
| Geometry pin (`geom_common_MDC2020.txt`) | 1 | reassigned by epilog |
| `MakeSS` NullProducer + its sequence entry | 1 | MDC2020-input-specific; goes in the epilog |
| MDC2020-era `Validation/` (retired — see Non-goals) | 8 | dropped |
| Extracted (retired — see Non-goals) | 7 | dropped |

`TimeClusterCollections: []` and `surfaceStepTags` are **not** outstanding —
main already carries them (`e907cbc4`, 2026-07-20). Everything else in the
tip-to-tip diff is main moving forward while the branch stood still, and is
discarded with the branch.

### Correction (2026-08-17): the baseline is Run1A, not v12

Measured while planning, and it supersedes the v11-vs-v12 framing below.
`Offline/CRVResponse/fcl/epilog_run1a_v01.fcl` and
`Offline/CRVReco/fcl/epilog_run1a_v01.fcl` set **exactly the keys that
separate v11 from v12**, and reduce them to two sectors:

```fcl
physics.producers.CrvPhotons.CRVSectors             : ["T1", "T2"]
physics.producers.CrvPhotons.photonYieldScaleFactor : 0.90
physics.producers.CrvCoincidenceClusterFinder.sectorConfig : [ {T1…}, {T2…} ]
```

Main includes them unconditionally at the end of `digitize/epilog.fcl` and
`reco/epilog.fcl` — *after* the prolog. So for any job that uses those
epilogs, the CRV prolog version is already overwritten and the v11/v12
difference is invisible. The branch's prolog pin matters only because the
branch also deletes the run1a includes.

Three consequences:

- **The real delta is v11's 25 sectors versus run1a's 2**, not 25 versus 23.
- **`Run1A.fcl` is unnecessary.** `MDC2020.fcl` is emitted after the base
  FCL, hence after `digitize/epilog.fcl`, so it wins on ordering alone. The
  run1a includes stay exactly where they are and current-era production is
  untouched. This removes the spec's highest-risk item.
- **The `@sequence::` self-reference is unsafe here** — it would append to
  run1a's 2-entry `sectorConfig`, not to a 23-entry one. `sectorConfig` must
  be restated in full.
- **`photonYieldScaleFactor` is a sixth key.** run1a sets `0.90`; v11 and
  v12 both use `0.84`. Omit it and MDC2020 jobs silently inherit Run1A's
  aging factor.

Because the content is CRV configuration and `epilog_run1a_v01.fcl` is the
established precedent, the values live in Offline as
`CRVResponse/fcl/epilog_MDC2020_v01.fcl` and
`CRVReco/fcl/epilog_MDC2020_v01.fcl`, and `MDC2020.fcl` includes them. That
adds one Offline PR that must merge first — the same cross-repo ordering
Run1B had with PR #569.

### The era pin is one decision

`CRVResponse/fcl/prolog_v11.fcl` is headed `//Use with crv_counters_v09.txt`;
`prolog_v12.fcl` says `crv_counters_v10.txt`. MDC2020's
`geom_common_MDC2020.txt` redirects to `geom_2021_PhaseI_v03.txt`, the
v09-counter world. Geometry and CRV version cannot be chosen independently —
the same lesson `Run1B.fcl` encodes for geometry and field.

The v11→v12 delta is value-only, confined to five keys:

| key | v11 (MDC2020) | v12 (current) |
|---|---|---|
| `CrvPhotons.CRVSectors` | 25 sectors, `…D4,C1,C2,C3,C4` | 23, `…D4,C1,C2` |
| `CrvPhotons.reflectors` | 25 elements | 23 |
| `CrvPhotons.lookupTableFileNames` | 25 elements | 23 |
| `CrvPhotons.scintillationYields` | `…,0,0,28610,28610` | `…,28610,28610` |
| `CrvCoincidenceClusterFinder.sectorConfig` | 25 entries | 23 |

v12's `C1,C2` **are** v11's `C3,C4` — identical lookup tables
(`LookupTable_2100_1`, `LookupTable_1550_1`) and yields (`28610`). v11's own
`C1,C2` are the CRV-Cryo-Inner modules, carrying scintillation yield `0`;
per the v11 header they are kept in the geometry only so that older files
remain compatible. So the era difference is: **MDC2020 has two extra,
disabled CRV sectors.**

No prolog table is added, removed or renamed between v11 and v12. Every
differing key survives into the resolved config under a stable path
(`physics.producers.CrvPhotons.*`, `physics.producers.CrvCoincidenceClusterFinder.*`,
spread in from `CrvDAQPackage.producers` and `CrvRecoPackage.producers`).
That is what makes an epilog viable despite the pin being a prolog include:
FHiCL has no conditionals and a later line cannot un-choose an include, but
it can reassign the values that include produced.

## Architecture

Two PRs, in order. Each is independently valuable and independently
revertible. The branch dies at PR 2; stopping after PR 1 still leaves main
better than it is now, because PR 1 is a bug fix that has nothing to do with
MDC2020.

### PR 1 — repair the output-module rename on main

**Scope:** every file on main that names a module the prolog does not
define — 31 files, 110 references. (28 of them are the pure-rename files the
branch already fixed; the other three — `Validation/ceDigi.fcl`,
`Validation/ceMix.fcl`, `Validation/cosmicOffSpill.fcl` — also carry
MDC2020-era changes on the branch, which are not taken.) Derived from main's
own internal contradiction, not from the branch; the branch is the
cross-check.

- `TriggeredOutput` → `Output`
- `TriggerableOutput` assignments → deleted. Triggerable is the older
  scheme; the two streams are consolidated and `Output` carries both
  selections. `gen_Digitize.sh` and `gen_Mix.sh` currently claim to emit a
  `…Triggerable…` dataset that is never produced.
- In `Validation/nightly/ceMix_NN.fcl`, `mu2emetadata.fcl.outkeys` entries
  follow the rename.
- `CaloOutput` / `TrkOutput` / `DiagOutput` / `UntriggeredOutput`
  assignments in the `ceMix_NN.fcl` files are equally dead. Remove them in
  this pass so the files stop describing outputs that cannot exist.

Files: `CampaignConfig/mdc2020_main.cfg`, `Scripts/gen_Digitize.sh`,
`Scripts/gen_Mix.sh`, `data/merge_filter.json`, `Validation/ceDigi.fcl`,
`Validation/ceMix.fcl`, `Validation/cosmicOffSpill.fcl`,
`Validation/nightly/CeSimReco/digitize.fcl`,
`Validation/nightly/CosmicSimReco/digitize{OnSpill,OffSpill}.fcl`,
`Validation/nightly/MDS/digitize.fcl`,
`Validation/nightly/ceMix_{00..19}.fcl`.

**Acceptance:** after the change, a nightly run produces distinct
`dig.owner.ceDigi.*` and `dig.owner.ceMix.*` files, and
`dig.owner.desc.version.sequencer.art` — the prolog placeholder — no longer
appears in `nightly2/current/`. `git grep -c 'TriggeredOutput\|TriggerableOutput'`
returns zero across the repo.

### PR 2 — the era switch

**Old-input compat** (additive, inert on inputs that already carry the
products): `MakeSS : { module_type : NullProducer }` in
`Digitize.producers` and in `Digitize.DigitizeSequence`;
`TimeClusterCollections : [ ]` on the trigger MC-matching producers in
`digitize/prolog.fcl`, `reco/prolog.fcl`, `recoMC/prolog.fcl`. Main already
carries part of this from `e907cbc4` (2026-07-20); reconcile against
Andrew's `dffc707f` (2026-08-12) rather than applying both blindly.

**Offline, first** (one PR, must merge before the Production PR):
`CRVResponse/fcl/epilog_MDC2020_v01.fcl` and
`CRVReco/fcl/epilog_MDC2020_v01.fcl`, siblings of the existing
`epilog_run1a_v01.fcl`, holding the v11 25-sector values verbatim:
`CrvPhotons.{CRVSectors,reflectors,lookupTableFileNames,scintillationYields,photonYieldScaleFactor}`
and `CrvCoincidenceClusterFinder.sectorConfig`.

**`Production/JobConfig/common/MDC2020.fcl`** (new), structured like
`Run1B.fcl` — geometry and CRV set together, with a header stating that they
are one decision and why:

```fcl
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/geom_common_MDC2020.txt"
#include "Offline/CRVResponse/fcl/epilog_MDC2020_v01.fcl"
#include "Offline/CRVReco/fcl/epilog_MDC2020_v01.fcl"
physics.producers.MakeSS : { module_type : NullProducer }
physics.DigitizePath : [ MakeSS, @sequence::physics.DigitizePath ]
```

`MakeSS` is MDC2020-input-specific by its own comment on the branch
("temporary patch for older MDC2020 output"), so it goes here rather than
unconditionally into main's `digitize/prolog.fcl`. That way it cannot reach
a current-era job at all. The last two lines apply only to digitize and
mixing entry points; a reco-only variant omits them.

The five Production prolog files are **not modified**. The CRV era is
carried entirely by reassigned values, so `prolog.fcl` vs `prolog_v11.fcl`
never enters the picture.

**No `Run1A.fcl`.** See the correction above: `MDC2020.fcl` already wins on
ordering, so the run1a includes stay where they are.

**Ordering.** Both files are campaign defaults, not overrides: they are
included ahead of a job's own keys so a job that pins a value still wins.
In prodtools this is the `#include_first` slot
(`prod_utils.COMMON_INCLUDE_KEY`), which `write_fcl_template` emits directly
after the base include. See `reference-campaign-common-json-overlay`.

**Acceptance:** the `MDC2020` branch merges into `main` with no diff, and is
deleted. Every MDC2020 entry point resolves identically under
`main + MDC2020.fcl` and under the branch.

## Verification

The method that worked for Run1B, and the only one that counts here: build
each job both ways — the branch's `prolog_v11` chain versus `main` plus the
new epilog — `fhicl-dump` both, and require the resolved configs to be
identical. Any difference must be named and justified in the PR, not
explained away.

Reasoning from FCL source is not verification. On the Run1B work, static
reading misclassified an entry that the dump caught, and six entries turned
out to be inheriting `bFieldFile` from their base FCL's epilog rather than
from `standardServices.fcl` — invisible in the source, obvious in the dump.
`/exp/mu2e/data/users/oksuzian/claude-scratch/verify_overlay.py` is the
working model: it appends a shim directory holding only the new file to
`FHICL_FILE_PATH`, so a pinned release's own Production still wins for every
file it already carries.

Entry points to cover: `digitize/OnSpill.fcl`, `digitize/OffSpill.fcl`,
`mixing/Mix.fcl`, `reco/NoField.fcl`, `recoMC/` via its prolog, and one
`pileup/` and one `primary/` job — the five prologs that include a CRV
prolog are reached through these. No `Extracted` entry point is covered;
Extracted is out of scope.

## Non-goals

- **Extracted, in every form.** MDC2020 Extracted is not a concern, so the
  branch's `epilog_Extracted.fcl` consolidation and its
  `geom_common_extracted_MDC2020.txt` pin are both dropped, along with the
  changes to `cosmic/Extracted{CORSIKA,CRY}.fcl`,
  `{digitize,reco,recoMC}/Extracted.fcl` and
  `cosmic/geom_cosmic_extracted.txt` — seven files. Main keeps its
  current-era Extracted configuration untouched, and the nightly `extracted`
  stream is unaffected. The one exception is the Extracted entry in
  `data/merge_filter.json`, which is in PR 1 solely for the output rename.
  Folding the three `Extracted.fcl` files into a shared epilog remains a
  reasonable tidy-up on its own merits; it is simply not part of
  consolidating this branch.
- **MDC2020-era `Validation/`.** The branch carries its own validation set
  pinned to MDC2020 geometry, MDC2020 input datasets and `firstRun: 1200`.
  It is retired — decided by the user on 2026-08-17 and reaffirmed after the
  live-production evidence above was presented. Main's `Validation/` keeps
  validating the current era; only the output-rename fixes are taken from the
  branch's copies.

  Consequence, accepted knowingly: after consolidation MDC2020 runs off
  `main` rather than off a pinned branch, and `main` took 45 commits in the
  five weeks after `MDC2020bj` was cut. With no MDC2020 stream in the
  nightly, an incompatibility introduced on main surfaces when the next
  MDC2020 round runs, not before — found and fixed by hand, as `dffc707f`
  was. The branch is currently acting as insulation and that insulation is
  being removed deliberately.
- **A prodtools `data/MDC2020/` campaign.** No MDC2020 entries exist, so
  there is nothing for a `common.json` overlay to attach to. MDC2020 jobs
  pick up `MDC2020.fcl` through whatever runs them. Wiring a campaign
  directory is separate work.
- **Offline changes.** None are required. Every file the era pin selects
  already ships on Offline main.
- **`Scripts/gen_*.sh`.** Deleting `CampaignConfig/` (below) leaves this
  family with no in-repo consumer, but the scripts may still be run by hand
  or from `production_manager/`, and two of them are repaired by the rename
  PR. They stay; whether the whole POMS-era submission path should exist is a
  separate question.

## Added scope (2026-08-17): delete `CampaignConfig/`

26 files, ~143 KB of POMS `.cfg`/`.ini` campaign definitions. The POMS
backend was removed from prodtools on 2026-08-08; **nothing in Production
references the directory**, and it was last modified 2025-08-01. It goes as
its own PR.

This shrinks the rename repair from 31 files / 110 references to **30 / 108**
— `CampaignConfig/mdc2020_main.cfg` held one file and two references, and
there is no point repairing a file that is about to be deleted.

## Risks

- **`MDC2020.fcl` copies values out of `prolog_v11.fcl`.** Mitigated by
  convention (versioned prologs are immutable) and by the dump-based
  acceptance test, which would catch any drift the moment it appeared.
- **`MDC2020.fcl` must land after `digitize/epilog.fcl` / `reco/epilog.fcl`
  to beat the run1a values.** Inside prodtools that is guaranteed by the
  `#include_first` slot, which is emitted after the whole base FCL. Outside
  prodtools — the `CampaignConfig` / `Scripts/gen_*.sh` path — it must be
  included last by hand, exactly as `Run1B.fcl` documents. Included too
  early it is silently overwritten by run1a's two-sector config, and the job
  still exits 0.
- **Do not delete the branch mid-round.** `SimJob/MDC2020bj` was cut
  2026-07-12 and has produced nothing; on the observed cadence a round is
  about due. Deleting the branch while one is in flight changes what a live
  campaign resolves against. Land the consolidation either after the round
  completes, or cut the round's Musing from consolidated `main` so it never
  depended on the branch. This is independent of the validation decision.
- **Frozen MDC2020 campaigns are unaffected either way.** They pin Musings
  (`SimJob/MDC2020aa` … `MDC2020bd`), which pin Offline and Production
  commits. Reproducibility does not run through this branch.
