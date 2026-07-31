# latestDatasets — selectable ordering: dsconf vs creation time

**Date:** 2026-07-31
**Status:** approved for planning
**Scope:** `utils/latestDatasets.py` only. `samweb_wrapper` is unchanged —
the date helper it needs already exists.

## Goal

Let `latestDatasets` pick the latest dataset per description by **either**
lexicographic dsconf order (today's behavior, the default) **or** SAM
definition creation time, selected per run with `--latest-by {dsconf,time}`.

## Motivation

Selection is lexicographic on the dsconf field. That works inside a single
naming series, where dsconf sorts by campaign letter then version
(`MDC2025ao_best_v1_1` < `MDC2025ap_best_v1_1`). It silently produces the
wrong answer when one description spans **two** naming series.

Observed 2026-07-31 on the real SAM definitions for
`nts.mu2e.CeEndpointMix1BBTriggered.MDC2020%.root`:

| dsconf | definition created | files |
|---|---|---|
| `MDC2020an_v06_01_00_perfect_v1_3` | 2024-12-26 | 1000 |
| `MDC2020an_v06_01_01_best_v1_3` | 2025-01-09 | 1000 |
| `MDC2020an_v06_01_01_perfect_v1_3` | 2025-01-09 | 1000 |
| `MDC2020aw_best_v1_3_v06_05_00` | 2025-06-18 | 333 |
| `MDC2020aw_best_v1_3_v06_06_00` | 2025-09-06 | 333 |
| `MDC2020-000` | 2025-12-12 | 334 |
| `MDC2020-001` | **2026-03-10** | 334 |

`'-'` (0x2D) sorts below `'a'` (0x61), so
`max()` returns `MDC2020aw_best_v1_3_v06_06_00` — a dataset created six
months *before* the actual latest, `MDC2020-001`. The ntuple dsconf series
(`MDC20XX-NNN`) is a counter that carries no release information, so no
string comparison can rank it against the release series. Only a timestamp
can.

## Non-goals

- **No change to the default.** `--latest-by dsconf` remains the default and
  makes zero SAM calls, so the chain-emit path (`--emit`) is unaffected
  unless the operator opts in.
- **No side-by-side output.** One ordering per run. A mode that prints both
  winners, or that audits where they disagree, was considered and rejected
  as unneeded (YAGNI).
- **No composite key.** Time-primary-with-dsconf-tiebreak was considered and
  rejected: it changes selection semantics for every existing caller with no
  way to opt out.
- **No file-level timestamps.** See "Time source" below.

## Design

### The order key

One new concept: an **order key**, a callable `name -> sortable` that decides
which member of a description group wins. `None` means today's behavior
(sort by dsconf). Every other change is threading that one value.

This is deliberately an injected callable rather than a mode string handled
inside the grouping function. Two reasons:

1. **Grouping stays pure.** No network call inside `_group_by_description`,
   so existing grouping tests need no SAM mock, and ordering can be tested
   with a fake key.
2. **Contention detection is separable.** Deciding which datasets need a date
   is a distinct responsibility from grouping, and lives in the key builder.

### Consistency invariant

`latest_per_description` and `superseded_per_description` **must** receive the
same key. `--superseded` is defined as "every version that is not the latest";
if the two functions ordered by different keys, a dataset could appear in both
listings or in neither. Sharing one sort inside `_group_by_description` makes
this hold by construction rather than by discipline.

### Function surface (`utils/latestDatasets.py`)

| Function | Change |
|---|---|
| `_group_by_description(names, order_key=None)` | sort each group by `order_key(name)` when given, else by dsconf |
| `latest_per_description(names, order_key=None)` | accept and forward |
| `superseded_per_description(names, order_key=None)` | accept and forward |
| `_creation_date_key(names)` | **new** — build the time key |
| `_order_key_for(latest_by, names)` | **new** — `None` for `dsconf`, `_creation_date_key(names)` for `time` |
| `main()`, `_emit()` | build the key once from the name list, pass it down |

`utils/samweb_wrapper.py` is unchanged:
`definition_creation_date(defname) -> Optional[datetime]` already exists
(structured `descDefinitionDict` first, text `Creation Date:` parse as
fallback).

### Time source

**SAM definition creation date**, one `describe-definition` call per dataset.

Rejected alternative: the first file's `create_date` (two calls per dataset).
It measures when the *data* was produced rather than when the *definition* was
made, which is marginally more faithful, but costs double and needs a choice of
aggregate. The two agree closely in practice — for `MDC2020-001` the definition
was created 2026-03-10T23:51 and its first file 2026-03-10T18:57, five hours
apart, against inter-version gaps of months.

Known limitation, accepted: a definition created well ahead of production, or
not recreated after a re-run, carries a date that does not match its data. If
that bites in practice, the file-date variant is the fix.

### Cost control

Only **contended** descriptions are queried. A description with a single
version has nothing to compare against, so its date is never fetched. In a
typical `--emit` run over ~20 descriptions where most have one version, this is
~2 SAM calls rather than ~20.

Consequence for the returned key: uncontended names are absent from the date
map, so the key must default them (`dates.get(name, datetime.min)`) rather than
index directly. Their rank is never consulted — they are alone in their group —
but a bare `dates[name]` would raise `KeyError`.

### Data flow

```
names (samweb / stdin)
  |
  +- 'dsconf' -> order_key = None                          ... no SAM
  |
  +- 'time'   -> _creation_date_key(names):
                   parse + group by desc                   ... no SAM
                   contended = groups with >= 2 members    ... no SAM
                   definition_creation_date(n) per member  ... 1 SAM call each
                   any None -> sys.exit(1)
                   -> lambda name: dates.get(name, datetime.min)
  |
  v
_group_by_description(names, order_key)
  +-> latest_per_description      -> last of each group
  +-> superseded_per_description  -> all but last
```

### Output ordering is unchanged

Only *within-group* ordering — which member wins — depends on the key. The
order of the printed rows is untouched: `latest_per_description` sorts rows by
description, `superseded_per_description` by `(description, dsconf)`. Selection
changes; presentation stays stable and diffable.

### CLI

```
--latest-by {dsconf,time}    (default: dsconf)
```

Help text must name the failure it prevents, not merely describe the sort:

> how to pick the latest dataset per description: 'dsconf' (default) sorts
> dsconf lexicographically, which is correct within one naming series; 'time'
> sorts by SAM definition creation date — use it when a description spans
> naming series, where lex order is meaningless (the ntuple series
> `MDC2020-001` sorts BELOW `MDC2020aw_best_v1_3_v06_06_00` because '-' < 'a',
> yet was created six months later)

The flag applies identically in lister mode, under `--superseded`, and under
`--emit`.

## Error handling

- **Missing date on a contended dataset** — exit 1, listing every offender by
  name:

  ```
  latestDatasets: --latest-by time: SAM has no creation date for:
    nts.mu2e.Foo.MDC2020-002.root
    nts.mu2e.Bar.MDC2020ax_best_v1_3.root
  ```

  Falling back to dsconf order here would answer a `--latest-by time` question
  with a lexicographic result — precisely the bug this mode exists to fix, now
  invisible. Consistent with the repo's no-fallbacks rule: validate at the
  boundary, fail loudly.

- **SAM error during the query** — `definition_creation_date` is fail-soft and
  returns `None`, which lands in the same path. A SAM outage therefore surfaces
  as a loud failure, never as a silently lexicographic answer.

- **Unparseable names** — unchanged. They are collected into `skipped` before
  the key builder sees them, so they never trigger a SAM call or a date error.

- **Progress and diagnostics to stderr only** — `Querying creation dates for N
  dataset(s), please wait...` plus a `_vlog` line per dataset, matching the
  existing `_filter_complete` pattern. stdout stays machine-readable; it is
  JSON under `--emit`.

## Testing

Five tests in `test/test_unit.py` section 32. The ordering tests inject a fake
key and need no SAM mock; only the key *builder* is mocked.

1. **Injected key overrides dsconf** — the real MDC2020 dsconf set with a fake
   date key selects `MDC2020-001`, not `MDC2020aw_best_v1_3_v06_06_00`.
2. **`superseded` honors the same key** — under one fake key, the superseded
   set is the exact complement of the latest set (the consistency invariant).
3. **Only contended datasets are queried** — mock `definition_creation_date`;
   assert singleton descriptions are never passed to it.
4. **Missing date fails loudly** — mock returns `None` for a contended dataset;
   assert `SystemExit` and that the message names that dataset.
5. **`dsconf` mode makes no SAM calls** — mock raises if called; the default
   path must not touch it.

## Documentation

`EXAMPLES.md` is a derived artifact — regenerate with `/refresh-examples` after
implementation rather than hand-editing. The `latestDatasets` section picks the
new flag up from `argparse`.

## Known follow-up (out of scope)

`_narrow_to_latest_release` has the same lex bug on a different axis.
`Mu2eName.campaign` parses `MDC2020-001` to `MDC2020` and
`MDC2020aw_best_v1_3_v06_06_00` to `MDC2020aw`; `max()` picks `MDC2020aw`, and
the function then **drops** every name whose campaign differs — so the newer
`MDC2020-NNN` definitions vanish from the result entirely rather than merely
ranking last. That is release narrowing, not version selection, and it needs
its own decision about what "latest release" means when two series coexist. Not
addressed here.

**Implemented mitigation.** Rather than change that function, its two call
sites (`_emit()` for the `ntuple` stage, and the lister's `--campaign` branch)
now skip narrowing entirely when `--latest-by time` is in effect: release
narrowing is campaign-order logic and would delete newer-by-date names before
the key ever saw them. Ordering by campaign tag is still wrong on its own
terms; it is simply no longer able to defeat time mode.

## Measured: neither key is universally correct

Checked against live SAM on 2026-07-31. Lexicographic and time ordering
disagree **in both directions** on real production definitions, which is why
`--latest-by` is a flag rather than a replacement:

- **Time right, lex wrong** — 8 of 17 contended descriptions in
  `dig.mu2e.%.Run1B%.art`. `Run1Bab2_best_v1_2` (created 2026-02-10) loses
  lexicographically to `Run1Bab_best_v1_2` (2026-02-01) because `'_'` (0x5F)
  sorts above `'2'` (0x32) — a revision losing to the thing it revises.
- **Lex right, time wrong** — `NeutralsFlashCat` in `dts.mu2e.%.MDC2025%.art`.
  `MDC2025ac` was re-created five hours *after* `MDC2025ad` on the same day, so
  pure-time ordering would hand a chain the older campaign's data. Remakes and
  backfills are routine in this project, so this is a live hazard.

Reproduce with
`/exp/mu2e/data/users/oksuzian/claude-scratch/probes/inversion_check.py`.

## Residual follow-ups (recorded, not fixed)

Surfaced by the final whole-branch review and deliberately left for a later
pass. None is load-bearing for this feature.

1. **The MCP server does not get the fix.** `mcp/src/prodtools_mcp/tools/
   discovery.py:96` calls `latest_per_description(names)` with the default key,
   so `find_datasets(latest_only=True)` still returns the lexicographic winner.
   Since `CLAUDE.md` names MCP the preferred interface for status questions,
   "end to end" currently means CLI-only. Deciding whether MCP should expose an
   ordering choice, or always order by time, needs its own design pass.
2. **A parseable non-definition name prints a SAM error to stdout.**
   `utils/samweb_wrapper.py:182` uses a bare `print`, reached from
   `_creation_date_key`. stdout is meant to stay machine-readable; the run does
   exit non-zero, so the damage is contained. Fix belongs in `latestDatasets`
   (redirect or pre-check), since `samweb_wrapper` is out of scope. The
   fail-loud message ("SAM has no creation date for") also mis-describes both
   real causes: a name that is not a definition, and a SAM outage.
3. **The module docstring omits the narrowing caveat** that the `argparse` help
   and `EXAMPLES.md` now carry.
4. **An `EXAMPLES.md` troubleshooting entry is not a literal quote.** It gives
   `latestDatasets: --superseded cannot be combined with ...`; `argparse`
   actually emits `latestDatasets: error: --superseded cannot be combined
   with ...`. `docs/EXAMPLES_schema.md` requires literal messages, so correct
   it in the schema and regenerate — never by hand-editing `EXAMPLES.md`.
