# Sliced Campaign Submission (Direct Backend) — Design

**Date:** 2026-07-18
**Status:** Approved by user (brainstorming session)
**Scope decisions (user rulings):** build it; direct backend only;
throttle gate = total mu2epro idle+running jobs; campaigns enter via
`submit_map --enqueue`; architecture = extend the recovery loop
(Approach 1); submission logging owned by the submit path; resource
parameters live in the map entry and are inherited by slices and
recoveries via the snapshot.

## Problem

POMS launches campaign stages on a server-side cron, and a split type
(`drainingn`, `nfiles`, ...) hands each launch the next slice of the
dataset until it drains. The prodtools direct backend has the slice
*mechanism* (`submit_map --first N --num M`) but no automation: a human
advances the cursor. Big campaigns therefore go out as one giant
submission or as hand-fed windows.

Since 2026-07-18 the direct backend has a submission ledger and an
hourly `recover` cron with locking, a token gate, and queue
interrogation (`docs/superpowers/specs/2026-07-18-direct-recovery-design.md`).
That is exactly the infrastructure a sliced submitter needs. This
design adds the cursor.

A second problem surfaced during design: **recovery resubmissions
currently drop resource overrides.** `submit_map --memory 4000MB` is
CLI-only; the ledger snapshot doesn't record it, so a recovery of those
jobs would fall back to `jobsub_argv.DEFAULT_MEMORY` (2000MB), OOM
identically every attempt (deterministic payloads), and march to
`exhausted`. This design fixes that independently of slicing.

## Architecture

One new table and one new phase in the existing loop. No new daemons,
no new cron entries, no worker-side changes.

```
submit_map --enqueue ──writes──▶ campaigns table (same submissions.db)
                                     │ entry snapshot, cursor, slice_size, state
recover_cron (hourly) ──▶ recover
    1. recovery pass (existing, unchanged)
    2. top-up phase (new):
         skip unless an active campaign exists
         count = mu2epro idle+running   (jobsub_q --user mu2epro -af JobStatus)
         round-robin over active campaigns, oldest first:
             while count + n <= cap and indices remain:
                 submit_map --map <tmp> --backend direct
                            --first <cursor> --num <n>     (writes ledger row)
                 cursor += n;  count += n
                 cursor == njobs  →  campaign 'complete'
```

Recovery runs first each tick, so its resubmissions are already queued
when the count is taken. The count covers **all** mu2epro jobs —
including POMS-launched ones — so the cap bounds the experiment
account's total farm footprint, not just this tool's.

## Component 1 — Campaigns table (`utils/submission_ledger.py`)

Beside the existing `submissions` table, same sqlite3 DB, same
stdlib-only constraint:

```sql
CREATE TABLE IF NOT EXISTS campaigns (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_utc  TEXT NOT NULL,              -- ISO8601
  state        TEXT NOT NULL DEFAULT 'active',
               -- active | complete | paused | cancelled
  map_path     TEXT,                       -- provenance only
  tarball      TEXT NOT NULL,
  entry_json   TEXT NOT NULL,              -- EFFECTIVE entry snapshot (see resources)
  cursor       INTEGER NOT NULL DEFAULT 0, -- next entry-relative index to submit
  slice_size   INTEGER NOT NULL,
  closed_utc   TEXT,
  note         TEXT
);
```

Design points:

- **Cursor is entry-relative** (0 .. entry njobs). Slices go out as
  plain `--first/--num`, which compose with windowed entries: the
  entry's `firstjob` stays intact in the snapshot and the worker
  applies it, exactly as a manual windowed submission would. No
  index-space rewriting (unlike recovery's `--indices`, which must drop
  `firstjob`).
- **Total njobs** comes from the snapshot (`njobs_of`), not a separate
  column. `cursor == njobs` ⇒ `complete`.
- **States.** `active` = the loop feeds it. `complete` = every index
  submitted (jobs may still run; their ledger rows continue under the
  recovery loop until verified). `paused` = submission failure or
  operator pause; the loop skips it, a human resumes. `cancelled` =
  operator close; already-submitted rows still get recovered normally.
- **Double-submit guard.** Enqueueing a tarball that already has an
  *active* campaign is refused with a hard error.
- API (explicit db path, mirroring the submissions API):
  `create_campaign(...) -> id`, `active_campaigns() -> [Row]`,
  `advance_campaign(id, new_cursor)`,
  `set_campaign_state(id, state, note)` with allowed transitions
  `active → complete|paused|cancelled` and `paused → active|cancelled`;
  anything else raises.

## Component 2 — `submit_map --enqueue` (in `utils/submit.py`)

- Direct backend only. Mutually exclusive with
  `--first/--num/--indices/--indices-file`.
- New flag `--slice-size N` (default **1000**), only meaningful with
  `--enqueue`, frozen into the campaign row.
- Snapshots the selected map entries (all, or `--entry N`) into
  campaign rows with `cursor=0`. **Submits nothing.**
- DB write failure at enqueue is a **hard error** — nothing was
  submitted yet, so fail loudly (unlike the post-submission ledger
  hook, which must never raise). Enqueue to the default DB requires
  mu2epro (it is a production act); tests use `--ledger-db`.
- `--dry-run` prints what would be enqueued, writes nothing.

## Component 3 — Top-up phase (in `utils/recover.py`)

Runs inside the existing `recover` invocation, after the recovery pass,
under the same per-DB lock. Steps:

1. **Fast path.** No active campaigns → skip entirely (zero extra
   queries; current recover behavior untouched when unused).
2. **Count.** `jobsub_q --user mu2epro -af JobStatus`, count tokens
   `1` (idle) + `2` (running). Query failure → skip the whole top-up
   phase with a loud log line. Never guess.
3. **Feed.** Cycle repeatedly over active campaigns oldest-first, one
   slice per campaign per cycle, until the cap is reached or every
   campaign is fully submitted. For each: `n = min(slice_size,
   njobs - cursor)`. If `count + n > cap` → stop feeding this tick
   (**whole slices only** — no clamping `n` to headroom, no confetti of
   tiny ledger rows; the final partial slice is short because the entry
   ends, not because the cap does).
4. **Submit** via subprocess, same battle-tested path recovery uses:
   write the snapshot as a tmp single-entry map, then
   `submit_map --map <tmp> --backend direct --first <cursor> --num <n>`.
   The submission writes its own ledger row via the existing hook.
5. **On success**: `cursor += n`, `count += n`; `cursor == njobs` →
   `close_campaign(id, 'complete')`.
   **On failure**: campaign → `paused` with a note, cursor NOT
   advanced, loud log. No blind retry — the "exited 0 but no cluster
   id" failure class may have queued jobs, and deterministic payloads
   make an unverified resubmit the Run1Ban failure mode. The operator
   checks the submit log / condor, then `--resume-campaign`.
6. **Decision logging.** One line per action into the recover log:
   measured count, cap in effect, and per slice: campaign id, tarball,
   entry-relative range, resulting jobsub id — plus explicit skip lines
   ("headroom < slice, waiting"; "queue count failed, top-up skipped";
   "campaign N paused: submit failed").

**Cap resolution** (mirrors the DB-path pattern): `--max-queued` flag
> `MU2E_MAX_QUEUED` env var > `DEFAULT_MAX_QUEUED = 10000` module
constant in `utils/recover.py`. Resolved once per invocation; applies
to the whole pass; nothing persists between runs. Deliberately NOT
stored in the DB — the effective cap must be readable off the crontab
line, and `recover --status` prints the cap it resolved.

**CLI additions to `recover`:**

```
recover --max-queued N          # cap for this pass (default: env, then 10000)
recover --pause-campaign N      # operator off switch
recover --resume-campaign N     # paused -> active
recover --cancel-campaign N     # close; existing rows still recovered
recover --status                # now also: campaigns table + resolved cap
recover --dry-run               # top-up reports would-submit lines, no writes
```

`--status` and `--dry-run` remain strictly read-only. The campaign
management flags are mutating and take the same per-DB lock as a full
pass.

## Component 4 — Submission log (in `utils/submit.py`)

Every direct-backend submission **attempt** appends a block to a dated
file beside the DB: `<dbdir>/submit-YYYYMMDD.log`. Contents: UTC
timestamp, invoking user, map path, entry index, tarball, requested
range (`--first/--num`) or absolute indices (recovery), and the outcome
— jobsub id + cluster on success, failure text on failure — plus the
captured raw `jobsub_submit` output that `_run_submit` already holds.

- Covers **all three origins uniformly** — manual runs, cron-fed
  slices, recovery resubmits — because they all go through
  `submit_map`. One log family = the complete submission history.
- Roles: **ledger** = structured truth (exact indices, chains);
  **submit log** = human-readable per-submission record incl. raw
  jobsub output; **recover log** = why the loop did or didn't act.
- Same discipline as the ledger hook: the append happens after jobs
  are queued, so a log-write failure must never crash the submission —
  warn loudly and continue. Non-mu2epro users can't write the mu2epro
  dir → they get the warning (known ledger gotcha, same shape).
- `--no-ledger` skips the log too (it is the tests/ad-hoc escape
  hatch). `--dry-run` and `--enqueue` write nothing (nothing was
  submitted).
- Plain daily appends, no rotation; cleanup is manual (same as the
  nightly validation logs).

## Component 5 — Resource parameters (memory / disk / lifetime)

Today: built-in defaults in `utils/jobsub_argv.py`
(`DEFAULT_MEMORY = "2000MB"`, `DEFAULT_DISK = "30GB"`,
`DEFAULT_LIFETIME = "24h"`) overridden only by CLI flags. Recovery and
(without this design) cron slices would silently lose CLI overrides.

1. **Authoritative home: the map entry.** Three new optional entry
   keys — `"memory"`, `"disk"`, `"expected_lifetime"` — beside
   `tarball`/`njobs`/`inloc`. Precedence at submission:
   CLI flag > entry key > built-in default.
2. **Snapshot freezes the effective values.** When `submit_map` writes
   a ledger row or an `--enqueue` campaign row, CLI resource overrides
   are merged into the stored `entry_json` first. The snapshot records
   what the jobs actually ran with.
3. **Automatic inheritance.** Recovery reconstructs from the ledger
   snapshot; the cron feeds slices from the campaign snapshot — both
   carry the original memory/disk/lifetime with no extra plumbing.
   This fixes the existing 4000MB→2000MB recovery downgrade.
4. **No escalation on retry** (out of scope, unchanged): a recovery
   uses exactly the original resources. Wrong resources are a human
   decision at `exhausted`: bump the entry key, resubmit deliberately.

## Edge cases

- **Queue-count failure** → top-up skipped for the tick, loud log; the
  recovery pass is unaffected.
- **Paused campaign after submit failure** → before `--resume-campaign`
  the operator checks the submit log and condor for whether the failed
  attempt actually queued jobs; if it did, the ledger row is missing
  (hook fires only on success) and the indices will look unsubmitted —
  resolving that is deliberately human.
- **Duplicate enqueue** (active campaign for the same tarball) → hard
  error at enqueue time.
- **Windowed entries** slice naturally via `--first/--num`; `firstjob`
  survives in the snapshot untouched.
- **Cancel** closes the campaign only; ledger rows already written
  continue through the recovery loop to verified completion.
- **Campaign 'complete' ≠ jobs done** — it means "fully submitted".
  Verification remains the recovery loop's job, per ledger row.

## Testing

Unit tests (`test/test_unit.py` conventions: no network, no live
submissions; fake queue-count function and fake subprocess runner):

- Campaigns table: create/read/advance/close round-trip; state
  transitions (active→paused→active; active→complete/cancelled);
  duplicate-active-tarball refusal.
- Enqueue: rows written with snapshot + slice_size; nothing submitted;
  mutual exclusion with `--first/--num/--indices`; dry-run writes
  nothing; DB failure is a hard error.
- Top-up: under/over/exactly-at cap; whole-slice gating (headroom
  smaller than slice → wait); end-of-entry clamp (`n = remaining`);
  cursor advances only on success; failure → paused + no advance;
  round-robin across two campaigns; complete transition at
  `cursor == njobs`; fast path (no campaigns → no count query);
  count-failure → top-up skipped; dry-run reports would-submit and
  writes nothing; windowed entry keeps `firstjob` in the tmp map.
- Submission log: block appended on success and on failure; skipped
  under `--no-ledger`/`--dry-run`; write failure warns, never raises.
- Resources: entry-key precedence (flag > key > default) in the built
  jobsub argv; CLI override merged into ledger and campaign snapshots;
  recovery resubmit argv carries snapshot resources.

## Out of scope (deliberate)

- Per-campaign caps or priorities; adaptive slice sizing.
- Resource escalation on retry.
- POMS-backend entries (POMS already slices server-side).
- Auto-resume of paused campaigns.
- Log rotation.

## Documentation

- `EXAMPLES.md`: regen via `/refresh-examples` after adding
  `--enqueue`/`--slice-size`, the new `recover` flags, and the entry
  resource keys to `docs/EXAMPLES_schema.md`.
- Wiki: `wiki/pages/2026-07-18-direct-recovery-loop.md` gains a
  campaigns/slicing section; the pre-activation checklist gains one
  item — verify `jobsub_q --user mu2epro -af JobStatus` passthrough on
  the installed jobsub_lite (the count assumes it, same class as the
  existing per-jobid check). `wiki/log.md` entry.
- Memory: update `project_direct_recovery_loop` /
  `reference_recovery_two_paths` after merge.
