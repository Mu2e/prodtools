# Draining campaigns — growing-dataset support on the direct/ledger backend

**Date:** 2026-08-01
**Status:** approved for planning
**Scope:** `utils/submissions.py`, `utils/submit.py`, `utils/jobsub_argv.py`,
`utils/runmu2e.py`, `utils/submission_ledger.py` (no DDL), one thin wrapper
beside `Mu2eJobPars.job_outputs`. Docs via `docs/EXAMPLES_schema.md` +
`/refresh-examples`, wiki page update.

## Goal

Let the direct/ledger backend process a **growing** input dataset the way
POMS's `drainingn` split type did for mcs and ntuple stages: as input files
land, tick-by-tick dispatch 1:1 jobs from the existing generic
(`generic_tarball`) reco/evnt cnfs, without waiting for the upstream
campaign to finish — and without giving up the ledger's deterministic,
SAM-existence-based verification.

## Motivation

The backend is index-only today. A cnf bakes a frozen input list at
`json2jobdef` time, so an art→art stage cannot start until its input
dataset is final. POMS covered this with SAM-project draining; POMS is
being retired. The MDC2025au round is the live case: digis are landing
now, and reco+ntuple could be consuming them already.

Two explicit gates block the existing worker capability:

- `utils/runmu2e.py:850-856` — `_direct_dispatch` errors on any
  non-normal jobdesc: "template/direct_input/g4bl entries run via POMS
  campaigns".
- `utils/submit.py:586-595` — `submit_map` skips no-`njobs` entries
  ("generic tarball") in multi-entry maps.

The worker-side machinery (`process_direct_input`,
`write_direct_input_fcl`, `job_outputs(override_desc=, override_seq=)`)
already exists and runs in POMS mode today. This design adds the
dispatch, bookkeeping, and verification around it.

## What POMS actually does (source-verified 2026-08-01)

`fermitools/poms` `webservice/split_types/`, plus SAM artifacts checked on
the Mu2e instance:

- **`draining`** is a no-op: returns the stage's dataset name forever;
  all exclusion lives in the SAM definition text (typically consumption
  state).
- **`drainingn(n)`** keeps a **cumulative snapshot id** in
  `cs_last_split`. Each slice = `defname:<base> minus snapshot_id <ls>
  with limit <n>`; `next()` snapshots the slice, unions it with the old
  cumulative, stores the union's snapshot id. Growth-safe and
  overflow-safe — but the cursor advances at **launch**, so a slice
  whose jobs all fail is "delivered" forever (re-dispatch is delegated
  to the separate recovery layer, configured on Mu2e as the
  non-verifying `process_status`). Every `peek()` also creates
  persistent `_slice_`/`_full_` SAM definitions — Mu2e's 2022
  `drainingn(1000)` ntuple stage over
  `mcs.mu2e.CeEndpointMix1BBSignal.MDC2020r_perfect_v1_0.art` left them
  in SAM to this day.
- **`nfiles(n)`** is stride paging over a static dataset — the analog of
  our index campaigns.

Imported ideas: growth/overflow-safe slicing under a cap (`drainingn`),
dispatch-only-what-is-on-disk (`stagedfiles`' intent), a registration
settling delay (`new()`'s `fts=` parameter), drained-fraction reporting
(`completion_pct`, metric only). Rejected: launch-time cursors,
consumption state in any role, persistent slice definitions, the 2-day
force-locate fallback, live per-file handout (SAM projects / Data
Dispatcher), any daemon.

## Decisions (from brainstorm)

- **Use case:** the generic reco/evnt 1:1 cnfs (MDC2025ar / Run1Ban
  precedent). Merged (N:1) consumption is out of scope.
- **Granularity:** one campaign per (generic cnf, dataset **pattern**) —
  e.g. reco drains `dig.mu2e.%.MDC2025au_best_v1_5.art`. Two campaigns
  cover a round; new descs are picked up automatically.
- **Completion:** the operator closes the campaign explicitly. No
  auto-completion, no quiescence heuristics.

## Non-goals

- No merged (N:1) draining — unstable merge groups over a growing set
  is new territory; 1:1 matches the POMS precedent exactly.
- No SAM projects, snapshots, or consumption state.
- No new daemons; the existing `submissions run` cron tick drives
  everything.
- No retry resource-escalation ladder (follow-up; the flat recovery
  floor applies).
- No pipeline-graph rendering in status; cascades work by dataset
  chaining alone.

## Design

### Campaign shape and enqueue

A draining campaign is a map entry with `input_pattern` and **no
`njobs`** — the same no-njobs signature that already means direct-input
mode in a POMS jobdesc (`validate_jobdesc`, `utils/runmu2e.py:139`):

```json
[{ "tarball": "cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar",
   "inloc": "tape",
   "input_pattern": "dig.mu2e.%.MDC2025au_best_v1_5.art",
   "exclude_desc": ["NoPrimary"],
   "min_age_minutes": 60,
   "prestage": false,
   "outputs": [{"dataset": "*.art", "location": "tape"}] }]
```

```bash
submit_map --map au-reco-drain.json --enqueue --slice-size 500
```

Enqueue validation (`_enqueue_entries`):

- `input_pattern` present + `njobs` present → error (pick one mode).
- `input_pattern` present + `firstjob` present → error (no index space).
- `input_pattern` must parse as a 5-field `tier.owner.desc.dsconf.ext`
  pattern (`%` allowed per field).
- Required alongside: `tarball`, `inloc`, `outputs` (same as
  direct-input jobdescs today).
- `exclude_desc` (optional): list of exact desc strings.
  `min_age_minutes` (optional, default 60). `prestage` (optional,
  default false).

The `campaigns` row stores the entry verbatim in `entry_json`; `cursor`
stays 0 and is never read — **draining state lives in SAM and the
submissions rows, not in a cursor**. That is the fix for `drainingn`'s
launch-time-cursor defect: nothing is ever "delivered" until its output
exists. The per-tarball live-campaign unique index (double-enqueue
guard) works unchanged. **No schema change.**

### The output-name mapping — one function, shared with the worker

New thin wrapper (beside the ledger helpers in `utils/submissions.py`):

```
expected_outputs_for(input_fname, job_pars) -> list[str]
```

Parses `input_fname` with `Mu2eName` (desc, sequencer), then calls
`job_pars.job_outputs(0, override_desc=desc, override_seq=seq)` — the
**same** `Mu2eJobPars` path `process_direct_input` exercises on the
worker (`utils/job_common.py:487`). Verifier and worker cannot drift.
Non-Mu2e-named outputs (e.g. `/dev/null` streams) are filtered the same
way `job_outputs` already marks them.

Used identically by the pending computation (landed), verification
(missing), and token-scope derivation (batch outputs).

### The tick: pending predicate

`submissions run` gains a draining phase per active draining campaign:

```
inputs    = files of datasets matching input_pattern
            − datasets whose desc ∈ exclude_desc (exact match on
              Mu2eName desc, not substring)
            − files declared to SAM less than min_age_minutes ago
landed    = inputs whose expected_outputs_for(...) ALL exist in SAM
in-flight = files in this campaign's ACTIVE ledger rows
parked    = files whose latest row reached max_attempts, output still
            missing
pending   = inputs − landed − in-flight − parked
```

Dataset enumeration: `list_definitions(input_pattern)` → candidate
datasets → `files_in_dataset()` each (the metadata-based ground truth).
Known limitation, stated here deliberately: a dataset with files but no
SAM definition is invisible to enumeration (`list-definitions ≠ file
metadata`). Production dig/mcs datasets — the inputs of both target
stages — carry definitions; the caveat lives with `-LH`/`-CH` ntuple
splits, which are never draining *inputs*.

The `min_age` guard (the POMS `fts=` idea) exists because pushOutput
declares metadata before locations settle, and a half-pushed upstream
batch should not be raced. Requirement: no file younger than
`min_age_minutes` dispatches. Implementation may use a `create_date`
dims clause or batch metadata lookup — plan's choice.

Fcl-compatibility routing is the operator's job at campaign-creation
time: the pattern plus `exclude_desc` must select only descs the cnf's
single fcl can process (Run1Ban precedent: the generic evnt cnf cannot
ntuple `NoPrimary` — `genCountLogger` needs GenEventCount).

SAM cost per tick, MDC2025au scale: 1 `list_definitions` + ~22 input
`files_in_dataset` + ~22 output `files_in_dataset` ≈ 45 queries — the
same order as one recovery-pass verification.

### Gates before dispatch

1. **Residency** — reuse `check_inputs._default_locality` (parallel mdh
   `query_dcache`): only ONLINE / ONLINE_AND_NEARLINE files dispatch.
   NEARLINE-only files are reported (`tape-only: N`) and, when the
   entry sets `"prestage": true`, batched into one
   `mdh prestage-files` request per tick (request is idempotent
   server-side; re-requesting pending files is harmless). mdh failure →
   residency unknown → **no dispatch** for that campaign this tick
   (fail-closed).
2. **Queue cap** — draining campaigns join `top_up`'s existing
   headroom loop (oldest-first, interleaved with index campaigns, one
   batch per campaign per cycle, first batch that would exceed the cap
   stops the tick). A batch counts by its file count.
3. **Batch** = first `slice_size` dispatchable files (stable order:
   sorted by filename, so re-ticks are deterministic).

### Dispatch

`submit_map` gains `--files <path>` (one filename per line — written by
the tick into the existing scratch-map dir, exactly like the recovery
path writes `--indices-file` today). Mutually exclusive with
`--first`/`--num`/`--indices`/`--indices-file`/`--enqueue`.

In `submit_entry_direct` for a files batch:

- `jobset = range(len(files))`; njobs-capacity validation is skipped
  (a generic cnf has no capacity).
- ops JSON gains a `files` array beside `jobs`
  (`jobsub_argv.build_ops_json` grows an optional parameter);
  `inspec` is built from the batch's input datasets.
- Token scopes derive from `expected_outputs_for` over the batch (the
  outputs are concrete at submit time), plus the log scope from the
  first output — the existing derivation applies unchanged.
- The ledger row stores the **file list** in `indices_json` (opaque
  JSON; no DDL). Row kind is discriminated by
  `entry['input_pattern']` presence — never by sniffing the JSON
  content type.

`_slice_overlaps_ledger` (index-space crash guard) explicitly skips
file-keyed rows — comparing filenames against an index window is a
type error. The draining crash-window (submit succeeded, ledger write
lost) has a different and acceptable failure shape: the re-dispatched
file produces the **same output name**, so the duplicate push fails
loudly in SAM, the file parks, and the operator sees it — wasted CPU,
never silent duplicate physics (unlike index mode, where the guard is
mandatory).

### Worker

`_direct_dispatch` (`utils/runmu2e.py:842`) dispatches instead of
rejecting: when the ops JSON carries `files`, mode must be
`direct_input` (which `validate_jobdesc` already returns for a
tarball+no-njobs entry); resolve `index` via the existing
`_resolve_direct_index`, then
`fname = ops['files'][index]` →
`process_direct_input(jobdesc, fname, args)` — the exact function POMS
mode runs — then the shared execute/manifest/push path
(`_execute_mu2e`, `_emit_manifest`, `_push_all`). A `files` array with
a normal-mode jobdesc, or a direct_input jobdesc without `files`,
remains a hard error. template/g4bl remain rejected.

### Verification and recovery

`verify_files_row(row, sam_lister)` — sibling of `verify_row`, same
contract: expected = `expected_outputs_for` over the row's files;
returns `(missing, partial)` as **filenames**; raises when anything
prevents verification (a row is never guessed complete).

`process_row` branches on row kind. Recovery = `submit_map --files
<missing subset>` with `parent_id` chained and `attempt+1`; the
recovery resource floor (4000MB/48h) applies as today. At
`max_attempts` the row closes `exhausted` and its still-missing files
are thereby **parked**: the pending predicate excludes them, the status
line counts them, and re-dispatch requires explicit operator action
(new rows via `--files` reset the attempt chain). Nothing silently
cycles — the fix for both `drainingn`'s never-redeliver and
`draining`'s retry-forever.

### Status and completion

`submissions status` per draining campaign:

```
campaign 48 [draining] cnf.mu2e.reco.MDC2025au_best_v1_5.0.tar
  dig.mu2e.%.MDC2025au_best_v1_5.art
  landed 3120/3170 (98.4%) | in-flight 40 | tape-only 6 | parked 4
```

The percentage informs; it never triggers anything. Completion is the
operator's call: `submissions complete <id>` — one new
`manage_campaign` action targeting the existing `active → complete`
transition — printing the final drained fraction and parked count as a
confirmation line (non-blocking: closing at 98% with parked files is a
legitimate operator decision).

Cascades need no machinery: enqueue reco-drain
(`dig→mcs`) and evnt-drain (`mcs→nts`) together and the pipeline runs
end-to-end as digis land — the datasets are the DAG.

## Error handling

Fail-closed throughout; a draining campaign never guesses.

| condition | behavior |
|---|---|
| SAM query fails during pending computation | campaign skips the tick, one report line; never guesses pending |
| mdh residency lookup fails | no dispatch for that campaign this tick |
| input filename in a matched dataset fails `Mu2eName.parse` | hard error naming the file (no-fallbacks rule) |
| submit failure | campaign pauses with note (existing top_up behavior) |
| tarball unlocatable at verify | row stays active, reported (existing `verify_row` contract) |
| file at `max_attempts`, output still missing | parked: excluded from pending, counted in status, operator action required |
| queue count fails | draining phase skipped this tick (existing fail-closed cap check) |

## Testing

All logic injectable, no network in unit tests (~25 cases):

1. `expected_outputs_for` golden pairs — including the ar convention
   (mcs desc == dig desc, no suffix), the Run1Ban `-KL` suffix, and
   sequencer preservation.
2. Pending algebra: growth between ticks, overflow past `slice_size`,
   `exclude_desc` exact-match (a `FlatGamma` exclusion must not drop
   `FlatGammaCalo`), `min_age` boundary, in-flight and parked
   exclusion.
3. Enqueue validation: `input_pattern`+`njobs` rejected,
   `input_pattern`+`firstjob` rejected, malformed pattern rejected,
   double-enqueue guard fires.
4. Residency gate: NEARLINE files withheld; mdh failure → zero
   dispatch; `prestage: true` issues one batched request.
5. Dispatch: `--files` mutual exclusions; ops JSON carries `files`;
   ledger row stores filenames; scopes derived from mapped outputs.
6. Worker branch: ops fixture with `files` → correct fname reaches
   `process_direct_input`; `files` + normal jobdesc rejected;
   direct_input jobdesc without `files` rejected.
7. `verify_files_row`: missing/partial semantics; unverifiable raises.
8. `process_row` file branch: resubmit subset, attempt chain, park at
   `max_attempts`.
9. `_slice_overlaps_ledger` skips file-keyed rows.
10. `submissions complete` transition + confirmation output.

Plus one live smoke before first production use: a 2-file batch through
a real generic cnf on FermiGrid, verified end-to-end.

## Documentation

- `docs/EXAMPLES_schema.md`: draining-campaign entry shape, `--files`,
  `submissions complete`, the pending predicate in one paragraph; then
  `/refresh-examples` (never hand-edit `EXAMPLES.md`).
- Wiki: extend `2026-07-18-direct-recovery-loop.md` with the draining
  phase; `poms-reference.md` already carries the source-verified
  `drainingn` mechanics.

## Scope estimate

~430 production lines (`submissions.py` ≈ 275 across pending/tick/
verify/status; `submit.py` + `jobsub_argv.py` ≈ 105; `runmu2e.py` ≈ 30;
wrapper ≈ 20), ~650 test lines, ~7 plan tasks. Comparable to the
sliced-campaign feature (2026-07-19).

## Follow-ups (explicitly out of scope)

- Retry resource-escalation ladder (attempt-indexed memory bumps —
  the POMS ordered-recovery idea).
- Server-side pending via `isparentof:` dims (parentage-trusting
  alternative to the client diff).
- N:1 merged draining.
- Pipeline-graph rendering in `submissions status`.
- Auto-registration sugar for cascade pairs.
