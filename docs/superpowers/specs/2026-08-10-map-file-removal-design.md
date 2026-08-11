# Removing the submission-map file from the direct workflow

**Date:** 2026-08-10
**Status:** design, approved for planning

## Goal

Remove the persistent intermediate map file (`direct_maps/*.json`) from the
normal direct-submission workflow, make a wrong submission setting fixable
after a campaign is enqueued, and delete the multi-entry `jobdesc` machinery
that nothing has produced since the POMS backend was retired.

Three independent changes. Each can land and be verified on its own.

## Background — what the map actually is

`direct_maps/map_physicalpionstops_run1bap.json` and its five siblings look
like configuration. They are not. The evidence:

1. **The format is the POMS artifact.** `utils/map_entry.py:3-5` says so:
   *"historically the POMS-map entry shape, now also the direct backend's map
   format."* The direct backend inherited the shape rather than designing one.

2. **The file is read exactly once**, at `submit_map --enqueue`. After that
   `campaigns.entry_json` is authoritative. The ledger's `map_path` column is
   written but never dispatched from — only the MCP status tools echo it back
   (`mcp/src/prodtools_mcp/tools/status.py:373,424`).

3. **Every field in it is derived.** `json2jobdef.append_jobdef`
   (`utils/json2jobdef.py:423`) builds the entry from the build config:
   `tarball` from `get_parfile_name`, `inloc`/`outloc`/resource keys/
   `input_pattern`/`prestage` copied through, and `njobs` from the config —
   where the default `-1` (`json2jobdef.py:694`) means *query the built cnf*,
   which is how campaign 52's `njobs: 2500` arrived without `reco.json` ever
   naming a job count. Nothing in a map file is hand-authored.

4. **The accumulator role is dead.** One file used to gather many entries for
   one POMS batch (`/tmp/map_digi_au.json` produced 22 campaigns). Today every
   file in `direct_maps/` holds one or two entries, and every entry is exactly
   one campaign. The largest is 471 bytes.

5. **The convention is already inconsistent.** The ledger shows three eras:
   campaign 1 used `poms_map/`, campaigns 2-49 used throwaway `/tmp` maps,
   campaigns 50-56 use persistent `direct_maps/` files. Within the same week,
   draining reco and ntuple (campaigns 48, 49) used `/tmp` while indexed reco
   (campaign 52) used `direct_maps/`.

So the file's only real job is transporting one derived entry from
`json2jobdef --prod` to `submit_map --enqueue`. Its persistence gives it an
authority it does not have: editing `direct_maps/map_physicalpionstops_run1bap.json`
today changes nothing about campaign 54, and neither does editing
`data/Run1B/resampler_beam.json`.

**What is not vestigial:** the entry *dict* itself. It is stored in both
ledger tables, and it crosses the grid boundary — `jobsub_argv.py:178` puts it
into `ops["jobdesc"]` and `runmu2e.py` consumes it on the worker node to
resolve indices and route outputs. `map_entry.py` is its typed accessor layer,
imported by eight modules including both MCP servers. The entry stays.

## Change 1 — `json2jobdef --enqueue`

### Interface

Two new flags on `json2jobdef`:

- `--enqueue` — after building and pushing the cnf, register the derived entry
  as a sliced campaign in the ledger. No file is written.
- `--slice-size N` (default 1000) — frozen into the campaign, same meaning as
  `submit_map --slice-size`.

Two new validation rules, both one-line refusals before any work starts:

- `--enqueue` requires `--prod`. A campaign whose cnf is not in SAM is broken
  from birth: `enqueue_entry` resolves the tarball from SAM via
  `_ensure_local_tarball` and `check_inputs` reads it.
  Message: `json2jobdef: --enqueue requires --prod (a campaign needs the cnf in SAM)`
- `--slice-size` without `--enqueue` is refused rather than silently ignored.

`--jobdefs` becomes optional. Under `--prod`, **at least one** of `--jobdefs`
or `--enqueue` is required, so a bare `--prod` can no longer silently write
`jobdefs_list.json` into the current directory. Passing both is legal and
writes the file as well — the file remains the handle for a manual
`submit_map --map <file> --first N --num M` re-dispatch.

The `--prod`-requires-`--jobdefs` rule is currently enforced only by
convention in the `/mu2epro-run` skill. It was originally a guard against a
SAM-polluting `ijobdefs_list` definition (incident 2026-05-19); that
capability went out with the POMS backend and no code outside
`json2jobdef.py`'s own default filename references it now. This change moves
the rule into `argparse`, where it belongs, in its widened form.

### Ordering

In `process_single_entry` (`utils/json2jobdef.py:688`) the sequence becomes:

1. build the cnf
2. `append_jobdef(config, jobdefs_list)` — only if `--jobdefs` was given
3. `_pushout_to_sam(parfile_name, config['owner'])`
4. **new:** `enqueue_entry(...)` — only if `--enqueue` was given

Enqueue must be last. Steps 2 and 3 keep their current order and behaviour.

### Seams

Two extractions, so each thing keeps exactly one implementation:

**`build_map_entry(config) -> dict`** in `utils/json2jobdef.py`. The pure
config-to-entry projection, extracted from `append_jobdef`. `append_jobdef`
becomes `_write_jobdef_json_entry(build_map_entry(config), jobdefs_file)`; the
`--enqueue` path calls `build_map_entry` and passes the result to
`enqueue_entry`. The projection — including the `njobs: -1` cnf query, the
windowed-entry validation, and the draining-key passthrough — becomes testable
without touching the filesystem.

**`enqueue_entry(entry, *, ledger_db, slice_size, dry_run=False, resources=None, provenance=None) -> int | None`**
in `utils/submit.py`. Returns the new campaign id, or `None` under `dry_run`.
It owns what `_enqueue_entries` (`utils/submit.py:328`) owns today: the
draining-shape validation, `_ensure_local_tarball`, the `check_inputs`
preflight with its exit-2 report, the njobs sanity checks, `_snapshot_entry`,
and the one-line operator errors for duplicate-live-campaign and DB failures.
`_enqueue_entries` becomes a loop over it and keeps its own signature.

`enqueue_entry` **retains `sys.exit()` as its error protocol** in this change.
Converting `submit.py` to exceptions is the follow-on's job (see below).
Because `json2jobdef` is also a CLI, inheriting those exit codes is correct
behaviour, not a workaround: an entry whose inputs fail preflight must exit 2
from either entry point.

### Bulk mode

`process_all_for_dsconf` gains the same passthrough, so
`json2jobdef --json data/mdc2025/digi.json --dsconf MDC2025au_best_v1_5 --prod --enqueue`
reproduces the whole `map_digi_au.json` batch — 22 campaigns — in one command
with no file at all.

### Provenance

`create_campaign(map_path=...)` is free-text and never dispatched from. The
`--enqueue` path passes the build config's identity instead of a filename:

    data/Run1B/resampler_beam.json#PhysicalPionStops@Run1Bap

This is strictly more useful than the map filename it replaces and than the
`NULL` it would otherwise be. Renaming the `map_path` column is out of scope;
the column's documented meaning becomes "where this entry came from".

## Change 2 — `submissions set-entry`

### Why

`set-memory` is the only way to change a live campaign's entry today. The
PhysicalPionStops incident (2026-08-10) needed `inloc` changed from `disk` to
`resilient`, and there was no verb for it.

### Interface

    submissions set-entry <CAMPAIGN_ID> <key> <value> [--include-open-rows]

**Editable keys are whitelisted:** `inloc`, `memory`, `disk`,
`expected_lifetime`. Anything else is refused with the allowed set named.
`tarball`, `njobs`, `firstjob` and `input_pattern` define the campaign's
identity and index space — editing them in place corrupts a live campaign
rather than fixing it, and the correct operation is `cancel` plus a fresh
enqueue.

**Values are validated at the boundary**, for the reason the existing
`_MEMORY_RE` check gives: an unparseable value would otherwise sit in the
ledger looking applied and surface a tick later as a `jobsub_submit`
rejection. `memory` and `disk` keep the jobsub size format;
`expected_lifetime` takes the jobsub duration format; `inloc` must be one of
`tape`, `disk`, `resilient`, `stash`, `none`, or `dir:` followed by an
absolute path — the forms `utils/file_resolver.py` actually accepts.

**Campaign state:** active or paused only, unchanged from `set-memory`.

### `--include-open-rows`

`set-entry` on a campaign alone changes only what future slices inherit.
Recoveries do not see it: `resubmit` (`utils/submissions.py:610`) rebuilds its
map from `row['entry']`, the row's own frozen snapshot. A fully-dispatched
campaign like 54 has no future slices, so a campaign-only edit would have
changed nothing there.

`--include-open-rows` additionally rewrites the entry snapshot of every
non-closed `submissions` row belonging to the campaign, which is what makes
recoveries pick the change up.

Rows carry no `campaign_id` — the two tables link by `tarball`, with the
partial unique index `campaigns_live_tarball` guaranteeing at most one live
campaign per tarball. Row selection is therefore
`tarball = <campaign tarball> AND closed_utc IS NULL`. That is unambiguous for
a live campaign, but a cancelled predecessor on the same tarball could in
principle have left an open row behind, so the command **prints the row ids it
changed** rather than only a count.

**The flag defaults off, and that default is deliberate.** `set-memory` does
not cascade on purpose: an *unset* `memory` is exactly what earns a recovery
the 4000 MB floor (`recovery_resource_argv`, `utils/submissions.py:592`),
so cascading a memory value would silently forfeit the better failure mode.
`inloc` has no equivalent floor, so an inloc fix normally wants the flag on.
Off-by-default preserves today's `set-memory` semantics exactly.

### Backward compatibility

`submission_ledger.set_campaign_memory` is reimplemented as a call to the new
`set_campaign_entry_key`, and the `set-memory` CLI verb stays as an alias.
Existing docs, EXAMPLES.md and operator muscle memory keep working.

## Change 3 — single-entry `jobdesc`

`ops["jobdesc"]` is a JSON array. Every producer ships exactly one element —
`jobsub_argv.py:178` is the only one, and it writes `[dict(entry)]`. The
worker still carries live multi-entry machinery that nothing has fed since
POMS was retired:

- `runmu2e.validate_jobdesc` (`utils/runmu2e.py:81`) — the `len(jobdesc) > 1`
  refusal, the per-entry enumeration loops, and the "generic tarball skipped
  in normal dispatch" branch. That last branch is unreachable with one entry:
  a single generic entry (tarball, no njobs) always classifies as
  direct-input mode first.
- `prod_utils.resolve_map_index` (`utils/prod_utils.py:268`) — the cumulative
  walk across entries.
- `runmu2e.py:195` — `total_jobs = sum(d.get('njobs', 0) for d in jobdesc)`.

### The change

`ops["jobdesc"]` becomes a single object. `resolve_map_index(entry, job_index)`
takes one entry and returns `(entry, job_index + firstjob_of(entry))` when
`job_index < njobs_of(entry)`, else `(None, None)`.

**Behaviour is preserved exactly.** With one entry `cumulative` is always 0,
so today's `local = global - cumulative + firstjob` already reduces to
`local = global + firstjob`, gated on `global < njobs`. The `--indices`
recovery path is unaffected: `submit.py:654-656` rewrites the shipped copy to
`firstjob: 0, njobs: jobset[-1] + 1` so that `local == global`, and that
rewrite is independent of the list wrapper.

### Why this is safe to do now

There is no version skew to manage. `_bundle_prodtools` (`utils/submit.py:400`)
ships this repo's `utils/` with every submission and rebuilds the bundle
whenever a source file changes, so the worker always runs the code version
that submitted it. Change both sides in one commit and every new job is
self-consistent; jobs already in flight keep their own older bundle and their
own older `ops`, also self-consistent. There is no window in which the two
disagree.

The real cost is test churn: 34 references to `resolve_map_index` /
`validate_jobdesc` in `test/test_unit.py` need updating, and the multi-entry
cases among them are deleted rather than ported.

## Non-goals

- **The map format is not removed.** `submit_map --map` stays as the manual
  and recovery dispatch path, `map_entry.py` stays as the entry's accessor
  layer, and `submissions.py` keeps synthesizing one-entry maps internally.
- **`direct_maps/` files are not deleted.** Writing there stops; the six
  existing files are mu2epro-owned and inert, since every campaign they
  describe is already enqueued. Removing another account's files is a separate
  operator decision.
- **The `map_path` column is not renamed.**
- **No schema migration.** No `campaign_id` column is added; Change 2 works
  within the existing tarball linkage.

## Follow-on (not in this spec) — in-process dispatch

`submissions.py` writes an entry it already holds in memory to a tmpdir at
four sites (lines 623, 677, 726, 942) purely to shell `bin/submit_map`, which
parses it straight back. That round-trip is POMS residue: `submit_map` was a
CLI-first tool because POMS drove it from outside.

It is not dead code — every production job goes through it — so removing it is
a rewrite, not a deletion. `submit.py` signals every problem with `sys.exit()`
(19 calls); calling it in-process means converting a module's error protocol
to exceptions, teaching every caller to catch them, and replacing the mu2e
environment setup that `bin/submit_map` currently guarantees.

That restructures the path which launches every production job, so it gets its
own spec, its own change, and its own test pass. Change 1's `enqueue_entry`
extraction is its first in-process call site and establishes the pattern.

## Testing

Suite is `python3 -u test/test_unit.py`, baseline 988 OK (skipped=1) as of
2026-08-10, and it must keep running on plain python3.9 with no wheel
installed.

**Change 1.** `build_map_entry` gets direct projection tests: resource-key
passthrough, draining-key passthrough, `njobs: -1` resolving from the cnf,
windowed entries, and the generic-tarball `njobs` omission. The existing
`test_append_jobdef_passes_resource_keys` / `..._draining_keys`
(`test/test_unit.py:5083,5113`) become tests of the pure function plus one
thin test that `append_jobdef` still writes the file. The `test_enqueue_*`
block (`test/test_unit.py:5225`) moves to `enqueue_entry` and is extended with
json2jobdef as a second caller. New flag tests: `--enqueue` without `--prod`
refused, `--slice-size` without `--enqueue` refused, `--prod` with neither
`--jobdefs` nor `--enqueue` refused, and `--prod --enqueue` creating a campaign
whose `map_path` carries the config provenance string.

**Change 2.** The seven `test_set_memory_*` tests (`test/test_unit.py:4951`)
are mirrored for `set-entry`, plus: rejection of a non-whitelisted key naming
the allowed set, `inloc` validation accepting `dir:/abs/path` and rejecting a
bare relative path, `set-memory` still working through the alias, and a
cascade test asserting `--include-open-rows` changes open rows, leaves closed
rows alone, and reports the changed row ids. One test must assert that the
cascade is off by default, since that default protects the recovery floor.

**Change 3.** Multi-entry `validate_jobdesc` and `resolve_map_index` tests are
deleted; single-entry tests assert `local == firstjob + global`, out-of-range
returning `None`, and the `--indices` rewrite still yielding `local == global`.

**Live acceptance.** One `json2jobdef --prod --enqueue` run as mu2epro
producing a campaign visible to `campaign_status`, with no file written to
`direct_maps/`, followed by one `submissions run --dry-run` confirming the
campaign's first slice would dispatch.

## Migration and docs

- `docs/EXAMPLES_schema.md` gains the `--enqueue` / `--slice-size` flags, the
  widened `--prod` rule, and the `set-entry` verb; `EXAMPLES.md` is then
  regenerated via `/refresh-examples` (never hand-edited).
- The `/mu2epro-run` skill's hard rule becomes "`--prod` requires `--jobdefs`
  or `--enqueue`", and its backend-routing section drops the
  `direct_maps/`-versus-`/tmp` guidance, since the normal path writes no file.
- `CLAUDE.md` and the direct-recovery runbook (`wiki/pages/2026-07-18-direct-recovery-loop.md`)
  get the one-command enqueue flow and the `set-entry` verb.
