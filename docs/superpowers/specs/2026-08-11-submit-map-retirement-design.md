# Retiring `submit_map` — dissolving the last of the map concept

**Date:** 2026-08-11
**Status:** design, approved for planning
**Predecessor:** `2026-08-10-map-file-removal-design.md` (landed on branch
`map-file-removal`, 15 commits)

## Goal

Remove the submission map from the codebase entirely — as a file, as an
operator-facing command, and as a word.

The predecessor spec removed the map the operator *writes*. A production
campaign is now one command (`json2jobdef --prod --enqueue`). What survives is
the map as an internal wire format, plus the command that used to read the
hand-written kind. This spec finishes the job.

Five changes, ordered. Each lands and is verified on its own.

## Background — the three surviving identities

The word "map" currently names three unrelated things.

**1. A subprocess wire format.** `utils/submissions.py` writes `[entry]` to a
tmpdir at four sites (`:623` resubmit, `:677` resubmit_files, `:726`
submit_slice, `:942` submit_drain_batch) and shells `bin/submit_map --map`.
The entry is already in memory — it came from `campaigns.entry_json`. The file
lives milliseconds and no human ever sees it.

**2. An operator CLI.** `submit_map --map FILE [--entry N] [--enqueue]`, with
multi-entry filtering and the generic-tarball skip. Documented in EXAMPLES.md
section 11.

**3. An MCP tool.** `enqueue_campaign(map_path, entry, ...)`
(`mcp/src/prodtools_mcp_write/tools.py:345`), which now serves only
hand-written maps — and nothing writes one.

### Why the file exists

`utils/submit.py:617` defines `submit_entry_direct(entry, idx, opts)`. It takes
**one entry as an ordinary Python value** and submits it. That is the engine.

`submit_map()` (`utils/submit.py:820`) is a thin wrapper: open a file, parse
JSON, filter entries, loop, call the engine.

So a campaign slice reaches the grid by: serialise the entry → write a temp
file → spawn a process → parse argv → `json.load` → loop over one element →
call a function that was already accepting exactly that value.

**The map file exists only because of the process boundary.** There is no
other reason. The boundary is historical: `submit_map` was the operator's
POMS-map command and came first; the campaign machinery was built later and
reused the submit logic the cheapest way available.

### Why this is not merely cosmetic

Two doors into campaign creation means every safety rule must be written
twice. That is not hypothetical — the `inloc` validator was added to the
`json2jobdef` path only, the `submit_map --enqueue` path let a bad value
through, and campaign 54 (`PhysicalPionStops.Run1Bap`) lost 239 of 500 jobs.
The fix (commit `c859821`) was to copy the check onto the second path. Two
copies that must be kept in sync forever is the fragile end state; one door is
the durable one.

## Decisions taken during design (2026-08-11)

- **Batch enqueue is covered.** The map's only irreducible capability was
  submitting N entries in one command (campaigns 32–47 all came from one
  `/tmp/map_digi_au.json`). The operator confirmed the only batches ever
  wanted are "every desc for a dsconf" and "one named desc" — exactly what
  `json2jobdef --dsconf` (`utils/json2jobdef.py:800`) and `--desc` already do.
  No hand-picked subsets. The capability is redundant.
- **`--no-ledger` is unused.** Confirmed by the operator. It can be deleted
  outright, which removes the only case that would still need an entry from a
  file rather than from the ledger.
- **Manual `--first/--num` windowing is unused.** Confirmed by the operator
  (2026-08-11). `submit_map --map M --first 0 --num 10` submits an arbitrary
  contiguous window outside the campaign tick; nobody does this. It is
  dropped deliberately and `submissions resubmit` does not replace it —
  recovery is by indices or by files, and ordinary progress is the tick's job.
  `_slice_overlaps_ledger` keeps its guard against a human-submitted window
  regardless: the guard costs nothing, and it also catches a crashed
  reservation, which is its more important role.
- **The hourly cron is not in use.** `submissions run` is invoked by hand.
  This does not change the design — the four call sites behave identically
  either way — but it means the blast radius of a tick-level failure is a
  human watching output, not an unattended loop.
- **Read the entry from an argument, not from the ledger.** An earlier option
  had `submit_map` take a campaign id and look the entry up. Rejected: the
  retry path modifies the entry before submitting (`resubmit()` strips
  `firstjob`), so the stored entry is not always the submitted entry. Passing
  the entry as a parameter sidesteps this and keeps the engine a pure function
  of its arguments.

## Change 1 — close the enqueue door

Delete from `utils/submit.py`:

- `--enqueue`, `--slice-size`, `--entry` argparse arguments
- `_enqueue_entries()` (`:438`)
- the multi-entry branch of `submit_map()`: the `--entry` range check, the
  `enumerate` fan-out, and the generic-tarball skip filter (`:846-865`)
- `--no-ledger` and its three checks (`:163`, `:794`, `:1044-1050`)

Delete `enqueue_campaign` from `mcp/src/prodtools_mcp_write/tools.py`, along
with `_read_entries_strict` and `_read_map_entry` if they have no other
caller after removal.

**Keep `enqueue_entry()` (`utils/submit.py:357`) exactly as it is**, including
its `sys.exit()` error protocol. Its only remaining caller is `json2jobdef`,
which is a CLI, so inheriting exit codes stays correct. The docstring's parked
note about converting to exceptions is now moot — that conversion is never
needed.

After this change there is exactly one way to create a campaign.

## Change 2 — explicit parameters for the engine

`submit_entry_direct(entry, idx, opts)` reaches into an argparse namespace.
It reads only: `dry_run`, `indices`, `files`, `prodtools_tar`, `role`,
`wftop`, `wfproject`, and (via the ledger helpers it passes `opts` to)
`ledger_db`, `ledger_parent`, and the resource overrides.

Replace `opts` with a single frozen options object (a `NamedTuple` or
`@dataclass(frozen=True)`) carrying exactly those fields, with defaults for
everything optional. Not loose keyword arguments: the value is threaded on to
`_reserve_in_ledger`, `_attach_cluster`, `_fail_reservation` and
`_log_submission`, and re-expanding it at each hop would be worse than the
namespace it replaces. `main()` builds it from the parsed argv; in-process
callers build it directly.

**Rename `submit_entry_direct` → `submit_entry`** (4 occurrences).

The `direct` suffix distinguished the direct backend from upstream
`mu2ejobsub`. That backend was retired 2026-07-19 and `utils/submit.py:3`
already describes the module as "single backend" — so the suffix contrasts
with nothing. This is the same failure mode as `map`: the concept died, the
vocabulary did not.

**Do NOT sweep the word `direct` across the tree.** A second, live meaning
exists: *direct input* — a job taking one named input file rather than
computing inputs from an index (the draining mode). All of these are the live
meaning and must not be touched:

- `direct_input`, `process_direct_input`, `write_direct_input_fcl`,
  `_direct_input_dir`
- every `_direct_*` function in `utils/runmu2e.py` (`_is_direct_mode:407`,
  `_load_direct_ops:418`, `_resolve_direct_index:428`,
  `_synthesize_direct_fname:439`, `_direct_dispatch:618`, `_direct_main:718`)

Only the backend-qualifier sense is renamed: the function name, and the
"direct backend" / "direct-backend submission" phrasing in docstrings and
comments in `utils/submit.py`.

## Change 3 — the four call sites go in-process

Replace each temp-file-plus-subprocess block in `utils/submissions.py` with a
direct call to `submit_entry`:

| Site | Function | Currently passes |
|---|---|---|
| `:623` | `resubmit` | `--indices-file`, `--ledger-parent`, recovery resources |
| `:677` | `resubmit_files` | `--files`, `--ledger-parent`, recovery resources |
| `:726` | `submit_slice` | `--first`, `--num` |
| `:942` | `submit_drain_batch` | `--files` |

Delete `_scratch_map_dir` (`utils/submissions.py:565`) and the `SUBMIT_MAP`
constant (`:49`) once no site references them.

### Failure containment is load-bearing

Today a crash during submission is contained by the process boundary: the
child dies, the parent reads a nonzero return code, marks that campaign
failed, and **continues with every other campaign**. Called in-process, an
unhandled exception ends the entire tick — one broken campaign stops all of
them.

Each call site must wrap the call and convert any exception into the same
outcome the nonzero exit code produces today (submit failed → pause the
campaign → continue). This is a behaviour-preservation requirement, not an
implementation detail, and needs its own test: **an engine that raises must
not prevent the remaining campaigns in the tick from being serviced.**

The upside is that the handler now sees the exception itself, so a failure can
be recorded with its cause rather than a bare status code. Pause notes should
carry it.

## Change 4 — `submissions resubmit`

The one genuine operator use of `submit_map` is re-firing specific failed work
by hand. It needs a home before the command is deleted.

New verb, alongside `reconcile` in `utils/submissions.py` (verbs are defined
`:1296-1399`):

```
submissions resubmit <row-id> --files parked.txt
submissions resubmit <row-id> --indices 4000,4001,4002
```

The entry comes from the ledger row, transformed exactly as `resubmit()`
already does — including stripping `firstjob`, because `--indices` values are
absolute cnf indices and the worker-side resolution must degenerate to the
identity.

**This is new code on the recovery path**, where duplicate submission means
duplicate physics (payloads are deterministic). It must reuse the same
overlap guard `_slice_overlaps_ledger` provides, and refuse rather than guess
when the window is not provably free. Treat it as the highest-risk item in
this spec, not as a freebie falling out of the refactor.

## Change 5 — delete the command, rename the column, fix the docs

- Delete `bin/submit_map`.
- Delete `submit_map()` (`utils/submit.py:820`) and `main()` (`:933`) from
  `utils/submit.py`. The module becomes a library with no CLI.
- Rename the `map_path` column to `origin` in both tables
  (`utils/submission_ledger.py:121` submissions, `:137` campaigns) via
  `ALTER TABLE ... RENAME COLUMN`, guarded so it runs once. Available sqlite
  is 3.34.1; `RENAME COLUMN` needs 3.25+. Update the `provenance` parameter
  threading (`utils/submit.py:376,398,430`) and the two MCP status echoes
  (`mcp/src/prodtools_mcp/tools/status.py:373,424`).
- Update `docs/EXAMPLES_schema.md` to drop section 11 and the map vocabulary,
  then regenerate `EXAMPLES.md` via `/refresh-examples`. **A full
  regeneration, not incremental edits** — CLAUDE.md requires it, and the
  predecessor branch has an outstanding deviation on exactly this point that
  should be cleared by the same run.
- Update `CLAUDE.md` and `mcp/README.md` to drop `enqueue_campaign` and the
  remaining map references.

## Architecture after

Two commands are the whole operator surface:

```
json2jobdef --prod --enqueue --slice-size N --json CONFIG --desc D --dsconf C
json2jobdef --prod --enqueue --slice-size N --json CONFIG --dsconf C

submissions status | run | pause | resume | cancel | complete
submissions set-entry | set-memory | set-slice | reconcile | resubmit
```

One library (`utils/submit.py`) with no CLI, whose entry point is
`submit_entry(entry, ...)`. One ledger as the durable record. No map file, no
map command, no map column, no map in the docs.

Unchanged: how cnfs are built, how campaigns slice, how recovery decides what
to re-fire, what lands in SAM. This is a surface and plumbing change.

## Testing

- Suite is `python3 -u test/test_unit.py`, run bare:
  `env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`.
  Baseline at branch head: **1025 OK (skipped=1)**. `test/test_unit.py:38-47`
  stubs `samweb_client`/`ifdh` into `sys.modules` **in-process only** — a
  subprocess-spawning test dies with `ModuleNotFoundError`.
- Tests that exercise `submit_map` via argv must be rewritten against
  `submit_entry` directly. Expect a net test-count decrease as map-file
  machinery goes; state the true count in commit messages.
- New tests required:
  - failure containment: an engine raising on campaign A must not stop the
    tick from servicing campaign B
  - `submissions resubmit` refuses when the window overlaps a live row
  - `submissions resubmit` strips `firstjob` before dispatch
  - the ledger migration is idempotent and preserves existing values
- Mutation-check the containment and overlap tests: revert the guard, confirm
  a test dies. A guard no test pins is not a guard.

## Implementation constraints

- **Work in a git worktree.** There is no deploy step; `submissions run`
  executes straight out of the working checkout, so a half-applied edit in
  `utils/` breaks the next hand-tick. Jobs already in flight are unaffected —
  `_bundle_prodtools` ships `utils/` with each submission.
- **Do not `git push`.** The operator pushes from their own shell.
- **Never fetch or refresh the mu2epro token.** Missing token → stop and
  report.
- Live acceptance before merge: one `json2jobdef --prod --enqueue` as
  mu2epro, one `submissions run --dry-run`, one `submissions resubmit
  --dry-run` against a closed row.

## Out of scope

- Any change to how cnfs are built, sliced, verified, or recovered.
- `enqueue_entry`'s `sys.exit` protocol — deliberately retained (Change 1).
- The bulk `--dsconf` enqueue path's lack of resumability, carried over from
  the predecessor spec's follow-ups.
