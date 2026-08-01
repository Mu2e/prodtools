# listNewDatasets — completeness from the submission ledger

**Date:** 2026-08-01
**Status:** approved for planning
**Scope:** `utils/listNewDatasets.py` and one new function in
`utils/submissions.py`. The POMS-backed completeness path is removed, not
kept as a fallback.

## Goal

Make `listNewDatasets --completeness` report `<landed>/<expected>` for
direct-submission campaigns, so a running round shows real job progress
instead of a dash.

## Motivation

`--completeness` looks up the expected count in `poms_data.db`, populated from
the POMS maps. Since direct submission became the single backend (2026-07-20),
new campaigns are recorded in `submissions.db` instead, and the column reports
`—` ("not produced via POMS") for all of them. Measured 2026-08-01 during the
MDC2025au digi round:

```
    1432 dig.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.art     —
    2239 dig.mu2e.CosmicCRYExtracted.MDC2025au_best_v1_5.art      —
```

Over a 14-day window, POMS answered for **0 of 75** listed datasets. The
column is blind to precisely the campaigns an operator most wants to watch:
you get files-landed with no denominator.

The two stores do not overlap. `poms_data.db` holds 775 historical datasets
(MDC2025ai / Run1Bah era); `submissions.db` holds 47 campaigns spanning
2026-07-21 onward. POMS holds the past, the ledger holds the present.

## Non-goals

- **No live queue state.** Idle/running/held per dataset was considered and
  rejected for now: it needs the HTCondor bindings and a cluster→dataset
  mapping, and answers a different question ("is it moving?") than this column
  ("how far along?"). It remains a plausible follow-up.
- **No per-index detail.** `verify_row` already reports exactly which indices
  are missing; duplicating that here would make a listing tool expensive.
- **No POMS fallback.** Keeping it would preserve a lookup that answered zero
  times in 14 days, at the cost of the entire SQLAlchemy apparatus. Historical
  numbers remain available by querying `poms_data.db` directly.

## Design

### Data flow

Note `submission_ledger.all_campaigns()` returns dicts whose `entry_json`
column is **already parsed** into an `entry` key (see `_campaign_to_dict`), so
consumers read `row['entry']['njobs']`, not `row['entry_json']`.

```
submissions.db --all_campaigns()--> campaign rows
                                      |
              entry["njobs"] ---------+     expected (the submitted WINDOW)
              entry["tarball"] -------+
                                      v
        locate_tarball -> Mu2eJobPars -> extract_datasets_from_tarball
                                      |
                                      v
              { dig.mu2e.CosmicCRYAllOnSpill.MDC2025au_best_v1_5.art : 2500,
                dig.mu2e.ensembleMDS3cOnSpill.MDC2025au_best_v1_5.art :  496, ... }
```

### Why the tarball, and not the entry

The ledger's `entry_json` is the **submission map entry**, whose `outputs` is a
glob:

```json
{ "tarball": "cnf.mu2e.CosmicCRYAll.MDC2025au_best_v1_5.0.tar",
  "inloc": "tape",
  "outputs": [{ "dataset": "*.art", "location": "tape" }],
  "njobs": 2500 }
```

`*.art` cannot be matched against a dataset name, so the entry alone gives the
expected count but not the dataset it applies to. Two rejected alternatives:

- **`chain_emit.output_datasets(entry)`** — reads `fcl_overrides`, which the
  map entry does not carry. Verified to return nothing for all 47 campaigns.
- **Convention** (`<cnf desc>` + optional `OnSpill`) — unsound. Measured on the
  real round: `CosmicCRYAll` produces `...CosmicCRYAllOnSpill...`,
  `CosmicCRYExtracted` produces `...CosmicCRYExtracted...` (no suffix), and
  `MuCap1809keVCalo` produces `...MuCap1809keVCaloOnSpill...`. Prefix matching
  is also ambiguous — `FlatGamma` is a prefix of `FlatGammaCalo`.

The cnf tarball is authoritative, and it is what `verify_row` already uses.

### Cost

Measured: `locate_tarball` + `extract_datasets_from_tarball` takes ~0.2 s per
campaign warm (0.5 s on the first, cold), so ~11 s for all 47.

Bounded further by resolving lazily: derive the set of dsconfs from the
datasets actually being listed, filter campaigns to those dsconfs, and resolve
only that subset. A `--days 1` run touches a handful of tarballs.

Note the measurement was taken with tarballs recently fetched by `submit_map`.
A genuinely cold cache pays a dCache fetch per tarball; the lazy filter keeps
that proportional to what is being listed.

### Function surface

New in `utils/submissions.py`, beside `verify_row` (it reuses the same three
helpers):

```
ledger_expected(db_path, dsconfs=None, *, locate=locate_tarball)
    -> (expected: dict[str, int], failures: dict[str, str])
```

- `dsconfs` — optional set; when given, only campaigns whose tarball carries
  one of those dsconfs are resolved.
- `locate` — injected for testing, so unit tests do no I/O.
- `expected` maps output dataset name to summed `njobs`.
- `failures` maps tarball name to the reason it could not be resolved.

Changed in `utils/listNewDatasets.py`:

- `_get_completeness(dataset)` becomes a dict lookup against `expected`.
- Removed: the `poms_db` session, the SQLAlchemy import guard,
  `_ensure_db_fresh`, `_db_is_stale`, and the flags `--no-rebuild`, `--db`,
  `--poms-dir`.
- Added: `--ledger-db`, defaulting to `submission_ledger.DEFAULT_DB` (env
  `MU2E_SUBMISSIONS_DB`, else
  `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`) — the same constant
  the `submissions` CLI uses for its `--db`.

  Deliberately **not** named `--db`, even though `submissions` uses that name
  and the POMS `--db` is being removed. Reusing it would silently repoint an
  existing `--db /path/to/poms_data.db` invocation at the wrong database; a
  distinct name makes the retired flag fail loudly with "unrecognized
  argument" instead.

The `WARNING: DB stale (... map(s) newer than DB)` message disappears, as does
the need to run `pyenv ana` before using `--completeness`.

### Expected count is the window, not cnf capacity

`entry_json["njobs"]` is what was actually submitted. For a windowed campaign
this differs from the cnf's baked capacity, and the ledger value is the correct
one:

```
CosmicCRYAll   cnf njobs 12500   (capacity)
               map njobs  2500   (submitted — 20% of dts)  <- what this reports
```

The cnf-based comparison used by `latestDatasets --complete-only` can never be
satisfied for such a campaign. This column does not inherit that bug.

### Multiple campaigns producing one dataset — take the MAX, not the sum

A tarball may be enqueued more than once. Campaign `njobs` is an **absolute
target index count**, not an increment: a later campaign resumes via its cursor
from where the earlier one stopped, so the two **overlap** rather than
partition. Expected is therefore `max(njobs)` across the campaigns producing a
dataset.

An earlier draft of this spec said "sum", and cited `RPCInternalPhysical` at
250 then 1667 as the justifying example. That example disproves it. Measured
2026-08-01 from the submission rows' index ranges:

```
RPCExternalPhysicalMix1BB            RPCInternalPhysicalMix1BB
  row 48:   0..49    (50)              row 49:    0..249   (250)
  row 61:  50..549  (500)              row 60:  250..749   (500)
  row 67: 550..833  (284)              row 66:  750..1249  (500)
                                       row 68: 1250..1666  (417)
  union: 0..833  = 834                 union: 0..1666 = 1667
  SAM holds:       834                 SAM holds:      1667
  max(njobs) = max(50,834)  = 834      max(250,1667) = 1667   <- correct
  sum(njobs) = 50+834       = 884      250+1667      = 1917   <- overstates
```

Summing double-counts the earlier campaign's window exactly, so both datasets —
which are complete — reported `834/884 INCOMPLETE` and `1667/1917 INCOMPLETE`.

`max` is a no-op for the 22 `MDC2025au_best_v1_5` digi campaigns, which are 1:1.

The exact ground truth, if ever needed, is the union of `indices_json` over the
`submissions` rows for that tarball — it handles overlaps *and* recovery
children (rows 76/78/79 resubmitted single indices already inside earlier
ranges). `max(njobs)` is the cheaper equivalent and also covers a campaign that
is enqueued but has not yet submitted anything.

## Error handling

A wrong denominator is worse than no denominator, so every failure degrades to
a visible marker rather than a number:

| condition | column | notes |
|---|---|---|
| dataset in `expected` | `1432/2500` | plus ` INCOMPLETE` when landed < expected (existing marker semantics, unchanged) |
| dataset absent from `expected` | `—` | no known campaign produced it |
| ledger file missing or unreadable | column disabled | one warning, listing still prints |

One unresolvable campaign never aborts the report.

**There is no per-dataset `?`.** An earlier draft of this spec claimed an
unresolvable tarball would mark its dataset `?`. That is not implementable: the
dataset name is *obtained from* the tarball, so when the tarball cannot be
resolved we do not know which dataset it would have named. Such a dataset is
therefore indistinguishable from one no campaign produced, and shows `—`.

The failure is still surfaced — `ledger_expected` returns a `failures` map of
tarball to reason, and the caller prints one stderr warning naming the
unresolved tarballs. That tells the operator the denominators are incomplete
without pretending to know which rows are affected.

## Numerator: total dataset size, not the listing window

The `COUNT` column counts files *created within the lookback window*. The
completeness numerator must instead be the dataset's **total** file count in
SAM, matching what the POMS column reported (`DatasetInfo.nfiles`). For a
campaign that started before the window, the two differ, and using the windowed
count would understate progress against a full-campaign denominator.

This costs one `dataset_file_count` call per listed dataset when
`--completeness` is on — the same per-dataset SAM cost `--size` already pays.

## Testing

Unit tests inject `locate`, so no tarball or network access occurs:

1. **Dataset present** — a campaign with `njobs=2500` whose tarball yields
   `dig...CosmicCRYAllOnSpill...` reports `1432/2500 INCOMPLETE` at 1432 landed,
   and `2500/2500` with no marker at 2500.
2. **Dataset absent** — a dataset from no campaign reports `—`.
3. **Unresolvable tarball** — `locate` returns `None`; the tarball appears in
   `failures`, contributes nothing to `expected`, and the other campaigns still
   resolve normally.
4. **Overlapping campaigns take the max** — two campaigns (250 and 1667)
   yielding the same output dataset report `.../1667`, not `.../1917`. This is
   the real `RPCInternalPhysical` case, where summing double-counted the first
   window and made a complete dataset read `1667/1917 INCOMPLETE`.
5. **Window, not capacity** — a campaign whose entry says 2500 while the cnf
   carries 12500 reports `/2500`. Guards the regression this design exists to
   avoid.
6. **dsconf filter** — with `dsconfs={'MDC2025au_best_v1_5'}`, `locate` is never
   called for a campaign of another dsconf.

## Documentation

`EXAMPLES.md` is derived — update `docs/EXAMPLES_schema.md` where it describes
the completeness column and the removed flags, then regenerate with
`/refresh-examples`. Never hand-edit `EXAMPLES.md`.

## Follow-up (not in scope)

Live queue state — idle/running/held per dataset from the HTCondor bindings —
would distinguish "still running" from "stalled with nothing queued". The
current column cannot: a campaign submitted a minute ago and one whose jobs all
died both read `INCOMPLETE`. Worth revisiting once this lands.
