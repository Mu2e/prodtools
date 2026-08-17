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
EventNtuple `from_dig.fcl` can read MDC2020 files. MDC2020 is being
consumed, not generated — `prodtools/data/` holds zero MDC2020 entries, and
MDC2020 runs through the older `CampaignConfig/mdc2020_main.cfg` +
`Scripts/gen_Digitize.sh` / `gen_Mix.sh` path.

Everything MDC2020 needs already exists on Offline `main`:
`geom_common_MDC2020.txt`, `geom_common_extracted_MDC2020.txt`,
`CRVResponse/fcl/prolog_v11.fcl`, `CRVReco/fcl/prolog_v11.fcl`. **No Offline
change is required.** The branch is pure selection among files that all ship
on main today.

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

### What is genuinely MDC2020-specific

The residual 23 files:

| class | files | mechanism |
|---|---|---|
| `epilog_Extracted.fcl` consolidation | 6 | unconditional refactor |
| Old-input compat (`MakeSS` NullProducer, `TimeClusterCollections: []`, `surfaceStepTags`) | 3 | additive, inert on new data |
| Era pin: geometry + CRV version | 9 | the only real switch |
| `epilog_run1a_v01.fcl` removed from shared `digitize/epilog.fcl`, `reco/epilog.fcl` | 2 | un-include; needs the Run1B treatment |
| MDC2020-era `Validation/` (retired — see Non-goals) | 3 | dropped |

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

Three PRs, in order. Each is independently valuable and independently
revertible. The branch dies at PR 3; stopping after PR 1 or PR 2 still
leaves main better than it is now.

### PR 1 — repair the output-module rename on main

**Scope:** every file on main that names a module the prolog does not
define — 31 files, 110 references. (28 of them are the pure-rename files the
branch already fixed; the other three — `Validation/ceDigi.fcl`,
`Validation/ceMix.fcl`, `Validation/cosmicOffSpill.fcl` — also carry
MDC2020-era changes on the branch, which are not taken.) Derived from main's
own internal contradiction, not from the branch; the branch is the
cross-check.

- `TriggeredOutput` → `Output`
- `TriggerableOutput` assignments → deleted. The second stream no longer
  exists in the prolog; `gen_Digitize.sh` and `gen_Mix.sh` currently claim
  to emit a `…Triggerable…` dataset that is never produced.
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

**Open item to resolve before merging:** confirm with the author of
`51d3a532` ("Add Trk and Calo digitization streams. Standardize cosmic
resampler name") that folding `TriggerableOutput` away was intended and no
consumer expects a `…Triggerable…` dataset. If a second stream is wanted,
this PR restores it in the prolog instead of deleting the assignments.

**Acceptance:** after the change, a nightly run produces distinct
`dig.owner.ceDigi.*` and `dig.owner.ceMix.*` files, and
`dig.owner.desc.version.sequencer.art` — the prolog placeholder — no longer
appears in `nightly2/current/`. `git grep -c 'TriggeredOutput\|TriggerableOutput'`
returns zero across the repo.

### PR 2 — unconditional refactors

**`JobConfig/common/epilog_Extracted.fcl`** (new, from the branch) as the
single home for extracted geometry and field:

```fcl
services.GeometryService.inputFile  : "Production/JobConfig/cosmic/geom_cosmic_extracted.txt"
services.GeometryService.bFieldFile : "Offline/Mu2eG4/geom/bfgeom_no_field.txt"
```

included by `cosmic/ExtractedCORSIKA.fcl`, `cosmic/ExtractedCRY.fcl`,
`digitize/Extracted.fcl`, `reco/Extracted.fcl`, `recoMC/Extracted.fcl`,
replacing their individual `inputFile` assignments.

**Behaviour change to verify, not assume:** `digitize/Extracted.fcl`,
`reco/Extracted.fcl` and `recoMC/Extracted.fcl` on main set only
`inputFile`, and route it to `Offline/Mu2eG4/geom/geom_common_extracted.txt`.
The new epilog sets `bFieldFile` as well, and points `inputFile` at
Production's `geom_cosmic_extracted.txt`. Extracted is field-off by
definition, so this is most likely a latent fix — but it must be
demonstrated by dumping the resolved config before and after, per
Verification below, and any real difference named in the PR description.

**Old-input compat** (additive, inert on inputs that already carry the
products): `MakeSS : { module_type : NullProducer }` in
`Digitize.producers` and in `Digitize.DigitizeSequence`;
`TimeClusterCollections : [ ]` on the trigger MC-matching producers in
`digitize/prolog.fcl`, `reco/prolog.fcl`, `recoMC/prolog.fcl`. Main already
carries part of this from `e907cbc4` (2026-07-20); reconcile against
Andrew's `dffc707f` (2026-08-12) rather than applying both blindly.

**Acceptance:** for one representative job per touched entry point, the
resolved config is unchanged except for the keys this PR intends to change,
each named in the PR description.

### PR 3 — the era switch

**`Production/JobConfig/common/MDC2020.fcl`** (new), structured like
`Run1B.fcl`: geometry and CRV set together, with a header stating that they
are one decision and why.

```fcl
services.GeometryService.inputFile : "Offline/Mu2eG4/geom/geom_common_MDC2020.txt"

physics.producers.CrvPhotons.CRVSectors           : [ ... 25 entries ... ]
physics.producers.CrvPhotons.reflectors           : [ ... 25 entries ... ]
physics.producers.CrvPhotons.lookupTableFileNames : [ ... 25 entries ... ]
physics.producers.CrvPhotons.scintillationYields  : [ ... 25 entries ... ]

physics.producers.CrvCoincidenceClusterFinder.sectorConfig : [
  @sequence::physics.producers.CrvCoincidenceClusterFinder.sectorConfig,
  { CRVSector : "C3"  ... },
  { CRVSector : "C4"  ... }
]
```

`sectorConfig` differs from v12 by exactly two appended entries, so it
self-references rather than restating 25 tables. That pattern is already in
use on main (`Validation/nightly/MDS/digitize.fcl`,
`reco/Extracted.fcl`, `recoMC/Extracted.fcl`). The four `CrvPhotons` arrays
are an insert-with-renumber, not an append, so they are restated in full.
Values are copied verbatim from `Offline/CRVResponse/fcl/prolog_v11.fcl` and
`Offline/CRVReco/fcl/prolog_v11.fcl`; versioned prolog files are immutable
by convention, so the copy is pinned, not drifting.

The extracted variant (`JobConfig/cosmic/geom_cosmic_extracted.txt` →
`geom_common_extracted_MDC2020.txt`) is selected the same way, through the
`epilog_Extracted.fcl` introduced in PR 2.

**`Production/JobConfig/common/Run1A.fcl`** (new). Main's
`digitize/epilog.fcl` and `reco/epilog.fcl` currently include
`epilog_run1a_v01.fcl` unconditionally, so "no era selected" silently means
Run1A. Move those two includes into `Run1A.fcl` and have Run1A jobs select
it explicitly — the mirror of what `Run1B.fcl` established. Removing an
include is not expressible as an override, which is why this has to move
rather than be countered.

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
`digitize/Extracted.fcl`, `mixing/Mix.fcl`, `reco/NoField.fcl`,
`reco/Extracted.fcl`, `recoMC/Extracted.fcl`, and one `pileup/` and one
`primary/` job — the five prologs that include a CRV prolog are reached
through these.

## Non-goals

- **MDC2020-era `Validation/`.** The branch carries its own validation set
  pinned to MDC2020 geometry, MDC2020 input datasets and `firstRun: 1200`.
  It is retired. Main's `Validation/` keeps validating the current era; only
  the output-rename fixes are taken from the branch's copies. Consequence,
  accepted: no automated signal if a future Offline change breaks MDC2020
  reprocessing — which is the class of breakage `dffc707f` fixed by hand.
- **A prodtools `data/MDC2020/` campaign.** No MDC2020 entries exist, so
  there is nothing for a `common.json` overlay to attach to. MDC2020 jobs
  pick up `MDC2020.fcl` through whatever runs them. Wiring a campaign
  directory is separate work.
- **Offline changes.** None are required. Every file the era pin selects
  already ships on Offline main.
- **Retiring `CampaignConfig/` and `Scripts/gen_*.sh`.** PR 1 repairs them;
  whether the POMS-era submission path should exist at all is a separate
  question.

## Risks

- **PR 1 changes production dataset names.** `gen_Digitize.sh` and
  `gen_Mix.sh` currently emit a `…Triggerable…` filename that goes nowhere.
  Deleting it is correct only if nothing downstream expects that dataset.
  Resolve the `51d3a532` open item before merging.
- **`MDC2020.fcl` copies values out of `prolog_v11.fcl`.** Mitigated by
  convention (versioned prologs are immutable) and by the dump-based
  acceptance test, which would catch any drift the moment it appeared.
- **`Run1A.fcl` changes the default.** After PR 3, a job that selects no era
  no longer silently gets the Run1A CRV epilogs. Every current Run1A
  consumer must be found and switched in the same PR. This is the failure
  mode recorded in `reference-run1b-frozen-campaigns-selfcontained`: on
  main, a stale pin fails silently.
- **Frozen MDC2020 campaigns are unaffected either way.** They pin Musings
  (`SimJob/MDC2020aa` … `MDC2020bd`), which pin Offline and Production
  commits. Reproducibility does not run through this branch.
