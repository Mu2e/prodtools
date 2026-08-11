---
title: Direct-backend recovery loop — ledger + submissions + cron
tags: [decision, recovery, direct-backend, submit_map, operations, sliced-campaigns, draining-campaigns]
sources: [docs/superpowers/specs/2026-07-18-direct-recovery-design.md, docs/superpowers/specs/2026-07-18-sliced-submission-design.md, docs/superpowers/specs/2026-07-19-workflow-hardening-design.md, docs/superpowers/specs/2026-08-01-draining-campaigns-design.md]
updated: 2026-08-01
---

# Direct-backend recovery loop — ledger + submissions + cron

*(2026-07-19: the recovery/top-up CLI was renamed `recover` → `submissions`,
a safe-by-default verb structure — `submissions` [bare = `status`],
`submissions run [--dry-run|--row|--max-attempts|--max-queued]`,
`submissions pause/resume/cancel CAMP_ID`. `submit_map` also lost its
`--backend` flag — it is single-backend direct now. This page uses the
current spellings throughout; see the "Operator quickstart" section
below and `docs/superpowers/specs/2026-07-19-workflow-hardening-design.md`
for the full rationale.)*

## What and why

`submit_map` (the Phase 2 in-house direct submission path — see
[[2026-04-30-phase2-direct-jobsub-implementation]]) had no automated
recovery. An operator would run `mkrecovery --print-indices` by hand,
pipe the result into `submit_map --indices-file`, and nothing watched a
campaign to completion. That gap sat in contrast to the two other
recovery models already in the ecosystem: POMS-backend stages get
`mkrecovery` + POMS's own recovery chains (`pending_files` and friends —
see [[poms-reference]] and `reference_poms_recovery_types` in memory),
and justIN, DUNE's workflow layer, treats recovery as an emergent
*property* of its file-state machine rather than a tool a human runs
([[justin-vs-prodtools]] called this out explicitly: "the planned
`utils/recover.py` + tracking DB were never built" — that page is now
stale on that specific point, since this branch built exactly that; the
broader model comparison in that page still holds). This work closes
the gap for the direct backend with the one recovery style the Run1Ban
incident proved safe: **verifying that output files exist in SAM**,
never inferring completion from job exit codes or consumption
bookkeeping. [[2026-07-05-run1ban-mix-recovery-data-loss]] is the
cautionary tale — a bookkeeping-driven recovery loophole (POMS
`sim_drain` re-queuing already-complete indices) combined with a
destructive `pushOutput` re-run path (`recoverDelay`-gated delete +
rewrite) to silently lose 54 already-declared output files. The design
here structurally avoids both failure modes: `submissions run` only ever
asks "does SAM have this file," never "did a job report success," and
it never deletes anything — a re-run's `pushOutput` re-declares, it does
not clobber.

## Architecture

Three new pieces and one hook into the existing submit path. No
worker-side changes — the fcl a worker runs is byte-identical whether or
not the ledger is involved.

```
submit_map ──writes──▶ submission ledger (sqlite3)
                                              │
    mu2epro crontab ── bin/submissions_cron ──▶ bin/submissions run
                                              │ per active row:
                                              │  1. drain gate (jobsub_q)
                                              │  2. verify vs SAM (mkrecovery logic)
                                              │  3. complete / resubmit / exhaust
                                              └──resubmits via──▶ submit_map (writes child row)
```

**Ledger (`utils/submission_ledger.py`).** Stdlib `sqlite3` only — no
SQLAlchemy, because the submit path runs as mu2epro in the bare ops
environment where `pyenv ana` is never loaded (see
`reference_pyenv_ana_for_db` in memory for why that distinction
matters elsewhere in this repo). Default DB path
`/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, overridable by
`--ledger-db` / `--db` or the `MU2E_SUBMISSION_DB` env var. One row per
successful `submit_map` submission — "successful" meaning a
cluster ID was actually parsed from `jobsub_submit`'s output; the
existing "exited 0 but no cluster ID" failure path still counts as a
failure and writes nothing.

Each row snapshots the map entry **verbatim** (`entry_json`) rather than
storing a reference back to the map file — the same reason POMS
snapshots JobTypes rather than re-reading them live. Recovery then
survives map edits, map file moves, and the fact that mu2epro
submissions typically run out of a throwaway `/tmp` workdir that may not
even exist anymore by the time `submissions run` runs. `indices_json` holds the
**absolute cnf indices** actually submitted (`firstjob + entry-relative
index`), stored sorted. `jobsub_id` is the full `cluster.proc@schedd`
form — the numeric cluster alone isn't enough, because `jobsub_q` needs
the schedd to drain-check.

States are exactly `active | complete | recovered | exhausted`, and
transitions only ever go one way: `active` → one of the other three,
never back. `active` means "jobs possibly still in flight, the loop
owns this row." `complete` means every index in the row was verified
present in SAM. `recovered` means the row was closed because a **child**
row now owns its missing indices — the child is a fresh row with
`parent_id` set to the closed row's id and `attempt = parent.attempt +
1`. `exhausted` means the attempt cap was hit with holes still open; a
human takes over from there. Following a chain from any row to its tip
(walking `parent_id` forward) gives the current state of that original
submission's recovery lineage.

**Firstjob-drop rule for resubmission.** `submit_map --indices` takes
absolute cnf indices and explicitly rejects being combined with a
windowed entry (`firstjob > 0`) — the ambiguity is that a window offset
plus an already-absolute index would double-count. So when `submissions
run` reconstructs a single-entry map to resubmit missing indices, it drops
`firstjob` from that reconstructed entry entirely. This is safe because
the indices being resubmitted are *already* absolute cnf indices (they
came out of the ledger that way); the worker-side `firstjob + index`
resolution then degenerates to the identity. The original windowed
entry is untouched in the parent row's own snapshot — only the
throwaway recovery-map entry drops the field.

**Recovery loop (`utils/submissions.py`, `bin/submissions run`).** Per active row,
in order: (1) drain gate via `jobsub_q --jobid <jobsub_id> -af
JobStatus` — any job still idle/running skips the row for this pass;
any held job is reported loudly and also skipped, never released or
removed (`condor_rm`/`condor_release` are a human's call, always); (2)
verify — locate the tarball, parse it once with `Mu2eJobPars`, build a
file→index map **scoped to the row's own index set** (an extension to
`mkrecovery.build_file_maps`, so the expected-files logic stays single-
homed rather than forked), and diff against `files_in_dataset` for
every output dataset the entry produces; (3) act — no missing files
closes the row `complete`; missing files under the attempt cap
resubmits exactly those indices through the `submit_map` CLI as a
subprocess (reusing the CLI, not reimplementing the submit path, keeps
one battle-tested entry point — token check, jobsub argv construction,
and the ledger write for the child row all come for free); missing
files at the cap closes the row `exhausted` and reports loudly. A
partial-output index (some but not all of that index's output streams
landed) is flagged distinctly in the verify step — see the
pre-activation checklist below on why that distinction matters before
this goes live.

**Cron wrapper (`bin/submissions_cron`).** Not installed anywhere by this
branch — it is only the entry point a human wires into mu2epro's
crontab once the checklist below is cleared. In order: quiet Mu2e
environment setup; a token check via `httokendecode` that **only
reports** a missing/expired token and exits non-zero — it never fetches
or refreshes one (this project's standing "never remediate token
problems" rule, also encoded in memory as
`feedback_never_get_mu2epro_token`); then `submissions run`, with
output appended to a dated log (`submissions-YYYYMMDD.log`) beside the
DB. The per-DB lock (guarding overlapping cron/manual runs from
double-submitting) is taken inside `submissions run` itself, not by the
bash wrapper.

## Sliced campaigns (top-up phase)

Since 2026-07-19 the loop does more than recover — it can also *drive* a
big submission forward on its own, the direct-backend analog of a POMS
`drainingn`/`nfiles` split-type stage that hands out the next slice each
cron tick. Design: `docs/superpowers/specs/2026-07-18-sliced-submission-design.md`,
plan: `docs/superpowers/plans/2026-07-18-sliced-submission.md`. One new
table (`campaigns`, same sqlite3 DB) and one new phase inside
`submissions run` — no new daemons, no new cron entries, no worker-side
changes.

**Enqueue workflow.** The one-command path — build the cnf, push it to
SAM, and register the campaign, all in one invocation, no map file
involved:

```bash
json2jobdef --json <config>.json --desc <D> --dsconf <C> \
    --prod --enqueue --slice-size 1000
```

`--enqueue` requires `--prod` (the cnf must land in SAM first —
enqueue resolves the tarball from there, not from a file on disk).
Under `--prod`, at least one of `--jobdefs` or `--enqueue` is now
required; `argparse` enforces it. The campaign's `map_path` records
provenance as `<config>.json#<desc>@<dsconf>` instead of a filename.

An operator can still register a campaign from an existing map file
built with `--jobdefs` (e.g. to enqueue a hand-edited entry, or one
entry out of a multi-entry map via `--entry N`):

```bash
submit_map --map MDC2025-032.json --enqueue --slice-size 2000
```

Both paths snapshot the selected entry/entries into the `campaigns`
table at `cursor=0` and **submit nothing** — same "hard error, not a
fallback" discipline as the ledger write in the normal submit path,
but inverted: here nothing has gone to the grid yet, so a DB failure
at enqueue time is a hard error rather than a warn-and-continue.
`--slice-size` (default 1000) is frozen into the row. `submit_map` is
single-backend (direct) — no `--backend` flag exists. Mutually
exclusive with
`--first`/`--num`/`--indices`/`--indices-file`. An entry with no fixed
`njobs`, or `njobs < 1`, (`generic_tarball`, or `njobs: 0`) can't be
enqueued — a campaign needs a positive job count to slice against. A
second `--enqueue` for a tarball that already has an *active OR
paused* campaign is refused outright — no silent double-feed. *(added
at final review: the guard originally checked `active` only; a paused
campaign still owns its index space, so "pause then enqueue" would have
been an undetected double-submit path — see the crash-window discussion
below for the closely related overlap guard.)*

**Top-up semantics.** Every `submissions run` invocation (the same
hourly cron tick that does recovery) runs the top-up phase *after* the
recovery pass, under the same per-DB lock, with one fast-path skip: no
active campaigns means zero extra queries, so the top-up phase costs
nothing when unused. When there is work: count total mu2epro
idle+running jobs (`jobsub_q --user mu2epro -af JobStatus`, states
`1`+`2`) — this counts **everything** mu2epro has queued, POMS-launched
jobs included, so the cap bounds the account's whole farm footprint,
not just this tool's slice of it. Running top-up after recovery means a
tick's resubmissions are already counted before top-up measures
headroom. Then round-robin over active campaigns, oldest-first, one
slice per campaign per cycle: `n = min(slice_size, njobs - cursor)`; if
`count + n` would exceed the cap, that campaign (and the whole tick)
stops there — **whole slices only**, never a slice clamped down to fit
remaining headroom, so there's no confetti of tiny ledger rows. A slice
short of the full `slice_size` happens only at the tail of an entry,
never because of the cap. Each slice submits via the same `submit_map`
CLI subprocess call recovery already uses (`--map <tmp single-entry
map> --first <cursor> --num <n>`), so it gets a regular ledger row like
any other submission — the campaign row tracks the *cursor*, the ledger
rows track the actual submitted jobs.

Cap resolution mirrors the DB-path pattern: `--max-queued` flag >
`MU2E_MAX_QUEUED` env > `10000` built-in default (`DEFAULT_MAX_QUEUED`
in `utils/submissions.py`). Resolved once per invocation, nothing
persists between runs — deliberately not stored in the DB, so the
effective cap is always readable straight off the crontab line, and
`submissions status` prints it every time.

**Crash-window semantics** *(added at final review)*. A campaign's
cursor and its jobs' ledger rows are written by two different
statements inside the same `submit_map` child process: `jobsub_submit`
runs, the ledger row gets written (`_record_in_ledger`), and only then
does the parent `top_up` loop call `advance_campaign` to move the
cursor forward. If the parent process dies between "submit_map wrote
the ledger row" and "top_up advanced the cursor" — or between
`advance_campaign` and the subsequent `set_campaign_state('complete')`
on a campaign's last slice — the DB is left in a state where jobs went
out but the campaign's own bookkeeping doesn't yet reflect it. The next
tick's naive behavior would be to resubmit that same slice: the
deterministic-payload worst case, duplicate physics events.

Two independent guards close this, both exercised by the crash-window
unit tests in `TestTopUp`:

- **Overlap guard.** Before submitting any slice, `top_up` checks the
  submission ledger (`_slice_overlaps_ledger`, ANY row state — a
  `complete`/`recovered` row still proves a submission happened) for
  indices already inside the slice's absolute window
  `[firstjob+cursor, firstjob+cursor+n)`. A hit pauses the campaign
  with a `'ledger already covers indices in this slice — crash-window
  suspected...'` note instead of submitting. This also catches a human
  manually running `--first/--num` on a tarball that has a live
  campaign. It is deliberately not a false-positive source for the
  recovery loop's own resubmits: a resubmitted index is always inside a
  window the cursor has already advanced *past*, so it can never
  intersect a *future* slice's window.
- **Self-heal.** A campaign whose `cursor` already equals its `njobs`
  but is still `active` (the crash landed between the cursor-advance
  write and the completion write) is closed `complete` on the next tick
  with a `'fully submitted (self-heal)'` note, instead of sitting
  `active` forever with nothing left to feed.

**Reconcile procedure** for an overlap-paused campaign: compare the
ledger rows for the campaign's tarball (`submissions status`, or
`sqlite3 <db> "select * from submissions where tarball=...;"`) against
the campaign's `cursor` to work out how far the cursor should actually
be. `advance_campaign` has no CLI today — the fix is a manual
`sqlite3` `UPDATE campaigns SET cursor=... WHERE id=...`, matching the
"reconcile is deliberately human" pattern already used for a
submit-failure pause (see below). Only then `submissions resume
<ID>`. Resuming without reconciling risks the very double-submit the
guard exists to prevent, since the guard only checks the *next* slice
window, not retroactively fixing the cursor.

**Residual window.** The overlap guard shrinks but does not eliminate
the crash window: a child `submit_map` process can still die after
`jobsub_submit` succeeds but *before its own ledger write* — the same
residual gap the recovery pass's resubmits already live with (see
"Firstjob-drop rule" above and the pre-existing `submit_map` caveat in
"Semantics and limits" below). In that specific sub-window there is no
ledger row yet to overlap against, so a subsequent tick could still
re-submit. This is a known, accepted residual, not a gap this design
closes — it is the reason the pre-activation checklist below gained an
item to exercise it on a real DB before the cron goes live.

**Pause / resume / cancel.**

```bash
submissions pause 7    # operator off switch
submissions resume 7   # paused -> active
submissions cancel 7   # close; already-submitted rows still recovered
```

These are mutating (same per-DB lock as `run`), separate verbs from
`run`, and exit immediately after acting. `paused` means one of three
things *(third added at final review)*: a
submit failure during top-up (the loop pauses the campaign
automatically rather than blind-retrying — deterministic payloads make
an unverified resubmit the exact Run1Ban failure shape:
queued-but-unrecorded jobs plus a later duplicate resubmit); the
crash-window overlap guard above firing (the ledger already covers part
of the next slice — reconcile the cursor before resuming, see the
crash-window section); or an operator hold via `submissions pause <ID>`.
Either way the loop skips it until a human clears it. Before
`submissions resume <ID>` after an automatic (submit-failure) pause,
check the submit log and `jobsub_q` for whether the failed attempt
actually queued jobs — the ledger hook only fires on success, so a
partial failure leaves indices that look unsubmitted but might not be.
`cancelled` (via `submissions cancel <ID>`) closes the campaign
only; ledger rows already written for it continue through the recovery
loop to verified completion exactly as if the campaign were still
active — cancelling stops new slices, it does not abandon jobs already
in flight. `cancelled` does **not** free the tarball's index history —
re-enqueueing the same tarball afterward starts a brand-new campaign
row at `cursor=0`, with no memory of what the cancelled campaign
already fed. In practice `_slice_overlaps_ledger` (crash-window guard
above) will catch a naive re-enqueue-and-run the moment its first slice
overlaps the cancelled campaign's already-submitted indices and pause
it — but check `submissions status` / the ledger for that tarball
before re-enqueueing rather than relying on the guard to catch it after
the fact.

`complete` means "fully submitted" — every index has gone out as a
ledger row — **not** "fully verified". Verification remains the
recovery loop's job, per ledger row, same as any other direct
submission; a `complete` campaign's rows keep cycling through the
recovery pass (drain-check, SAM-verify, resubmit-on-miss) until each
one individually reaches `complete` at the row level too.

**Submission log.** Every direct-backend submission *attempt* — manual,
cron-fed slice, or recovery resubmit, success or failure alike —
appends a block to `submit-YYYYMMDD.log` beside the ledger DB (one file
per UTC day, plain appends, no rotation, cleanup manual — same as the
nightly validation logs), including the raw `jobsub_submit` output.
Introduced alongside sliced campaigns (design ruling: "submission
logging owned by the submit path") so every origin gets the same
durable per-attempt record — the ledger alone captures state
(indices, chain, cursor) but not the human-readable *why*/*how* of one
specific attempt, which matters more now that cron-fed slices add a
second unattended submitter alongside cron-fed recovery resubmits.

**Three-log-layer debugging story.** When something about a campaign
looks wrong, the three logs answer different questions and none of
them substitutes for another:

- **Ledger** (`submissions`/`campaigns` tables, sqlite3) — structured
  truth: exact indices submitted, attempt chains via `parent_id`,
  campaign cursor/state. Query it with `submissions status` or `sqlite3`
  directly. This is the only layer with machine-checkable state.
- **Submit log** (`submit-YYYYMMDD.log`) — human-readable per-attempt
  record, including the raw `jobsub_submit` output, for *every* origin
  (manual/slice/recovery) uniformly. Answers "what actually happened
  when this specific submission ran, and what did jobsub say."
- **Submissions log** (`submissions-YYYYMMDD.log`, written by
  `bin/submissions_cron`) — the loop's own decisions: measured queue
  count, cap in effect, per slice campaign id / tarball / entry-relative
  range / resulting jobsub id, and explicit skip lines ("headroom <
  slice, waiting", "queue count failed, top-up skipped", "campaign N
  paused: submit failed"). Answers "why did (or didn't) the loop act
  this tick."

A stuck campaign is usually: the submissions log says why top-up didn't
feed it (cap, count-query failure, or the campaign isn't `active`); if
it did feed and nothing shows up in `jobsub_q`, the submit log has the
raw `jobsub_submit` output for that attempt; the ledger tells you
whether a row actually got recorded for it.

**Resource-key inheritance fix.** Building the top-up phase surfaced
(and fixed) a bug that predates sliced campaigns: a plain recovery
resubmit used to silently drop CLI resource overrides. `submit_map
--memory 4000MB` was CLI-only — the ledger snapshot didn't record it —
so a `submissions run` resubmit of those same jobs would fall back to the
2000MB built-in default and OOM identically every attempt (deterministic
payloads mean a resource-starved job fails the same way every retry).
Fixed by moving `memory`/`disk`/`expected_lifetime` into optional map-
entry keys (`utils/poms_entry.resources_of`), resolved with precedence
CLI flag > entry key > built-in default, and freezing the *effective*
value into both the ledger row and the campaign row snapshot at
submission time. Recoveries and cron-fed slices both now reconstruct
from that snapshot, so they inherit the original resource request with
no extra plumbing — a `--memory 4000MB` submission stays 4000MB through
every subsequent recovery, not just the first attempt.

## Install runbook

One-time setup, as mu2epro:

```bash
mkdir -p /exp/mu2e/data/users/mu2epro/prodtools
```

The ledger DB is stdlib sqlite3 and self-creates its schema on first
write, but the **directory** is a deliberate one-time ops step, not
auto-`mkdir`'d — a missing directory surfaces loudly
(`sqlite3.OperationalError`) rather than being silently papered over
(same "fail loudly, no fallbacks" convention as the rest of this repo;
see `feedback_no_fallbacks` in memory).

Crontab line, in mu2epro's crontab on a GPVM (hourly, at :17 past the
hour to avoid the top-of-hour pile-on other cron jobs tend to cause):

```
17 * * * * <prodtools>/bin/submissions_cron
```

where `<prodtools>` is the checked-out repo path that also serves
`submit_map`. Logs land at `submissions-YYYYMMDD.log` next to the
ledger DB (`/exp/mu2e/data/users/mu2epro/prodtools/submissions-YYYYMMDD.log`
with the default DB path) — one file per day, appended to across all
cron invocations that day.

Anyone can check status without mu2epro privileges — status checks
never need production credentials (`feedback_status_checks_no_mu2epro`
in memory):

```bash
submissions status
```

## Pre-activation checklist

These five items must be checked off **before** the cron line above
goes into mu2epro's crontab. They need live services (a real jobsub_lite
install, a real drained cluster, a real pushOutput run) and are
deliberately **not** covered by the unit test suite, which only injects
fakes for `jobsub_q`, SAM listing, and the submit subprocess.

- **Duplicate-declare behavior.** Re-run one index whose outputs
  *partially* exist (or trace the worker's `pushOutput`/declare code
  path directly) and record exactly what happens to the file that is
  already SAM-declared. `verify_row` already flags these indices as
  `partial` distinctly from fully-missing ones, specifically because
  the design doc could only note that "POMS `pending_files` recovery has
  the same shape, so production precedent suggests pushOutput tolerates
  it" — that is an inference, not a verified fact for this loop. Until
  verified, treat any `submissions run --dry-run` output that reports
  partial indices with extra suspicion, and prefer a manual look at
  those indices over letting the loop resubmit them unattended.
- **One real `submissions run --dry-run` pass** on a genuine drained
  row — a real ledger row on a cluster that has actually drained from
  the queue. This exercises the full tarball-locate → parse → SAM-list
  → diff path against real SAM and real dCache state, which no unit
  test does.
- **`jobsub_q --jobid <id> -af JobStatus` passthrough confirmed** against
  a real jobsub_lite installation. `queue_state` in `utils/submissions.py`
  assumes condor_q autoformat (`-af`) passes straight through
  jobsub_lite to the underlying condor_q, returning one numeric
  HTCondor `JobStatus` value per queued proc (1 idle, 2 running, 5
  held). If jobsub_lite's `-af` handling differs from that assumption on
  the GPVM's actual jobsub_lite version, the drain gate silently
  misreads queue state — worth a direct check with a live cluster rather
  than trusting the assumption.
- **`jobsub_q --user mu2epro -af JobStatus` passthrough confirmed**
  against the installed jobsub_lite — same class of assumption as the
  per-jobid check above, different call site: `total_queued` (top-up's
  queue-count function in `utils/submissions.py`) counts `JobStatus`
  tokens `1`/`2` across mu2epro's *whole* queue, not one job's. If `-af`
  output shape differs by-user vs by-jobid on this jobsub_lite install,
  the top-up cap silently miscounts and either starves a campaign or
  overshoots the farm-footprint cap it's meant to enforce. Confirm with
  a real `--user mu2epro` query against a live queue before relying on
  the count for anything unattended.
- **Crash-window behavior verified on a test DB** *(added at final
  review)*. On a scratch/test `MU2E_SUBMISSION_DB`, run a campaign
  through top-up and kill the parent `submissions run` process between a
  child `submit_map`'s ledger write (jobs queued, ledger row present)
  and the parent's own `advance_campaign` call — timing this by hand or
  with a short sleep/breakpoint in a copy of `top_up`. The next
  `submissions run` tick must **pause the campaign with the crash-window
  overlap note**, not silently resubmit the same slice. This is the
  live-system half of the guard the unit tests (`TestTopUp`
  `test_overlap_*`) only exercise with a fake queue-count function and
  fake submit subprocess — it has not been run against a real
  `jobsub_submit`/`submit_map` child process end to end.

## Operator quickstart

### 1. Which path owns this entry?

There is no ownership key in the map today (see "Decided against" in
`docs/superpowers/specs/2026-07-19-workflow-hardening-design.md` —
dropped deliberately, POMS is on its way out). Work it out from how the
entry actually gets to the grid:

- **POMS-owned entries** — launched by a POMS campaign stage against a
  POMS-map JSON. Recovery is POMS's own machinery: `mkrecovery` builds
  the recovery SAM definition, POMS's `pending_files` (and friends)
  drive the resubmit. `submissions` never sees these — there is no
  ledger row.
- **`submit_map`/direct entries** — submitted by a human or a cron-fed
  slice through `submit_map`. Every successful submission gets a ledger
  row; `submissions run` watches it to completion automatically
  (drain-check, SAM-verify, resubmit-on-miss, `exhausted` for a human at
  the attempt cap).
- **`template`/`direct_input`/`g4bl` entry modes, and HPC submission** —
  `submit_map` cannot submit these (the direct worker doesn't support
  them). They run via POMS campaigns, or via the upstream
  `mu2ejobsub`/`mu2eg4bl` CLIs directly (`/mu2ejobsub-submit`,
  `/mu2eg4bl-submit` skills). Neither path produces a ledger row either.

**Never submit the same entry through both paths.** These are
deterministic payloads — the same tarball/index submitted twice runs
the same physics events twice, a duplicate-declare problem at
`pushOutput`, not a harmless retry. Before resubmitting *anything*, ask
(or check `submissions status` / the POMS campaign / `jobsub_q`) how
the entry was originally submitted. A log filename alone does not tell
you the backend — see `reference_log_name_not_backend_tell` in memory.

### 2. Human ksu environment for `submit_map` as mu2epro

`ksu mu2epro` does not fully impersonate mu2epro for jobsub's own
tooling — re-export the identity before submitting, or
`condor_vault_storer` fails (or worse, quietly records the wrong
submitter):

```bash
ksu mu2epro
export USER=mu2epro LOGNAME=mu2epro HOME=$(getent passwd mu2epro | cut -d: -f6)
export XDG_RUNTIME_DIR=/run/user/$(id -u)

submit_map --map MDC2025-032.json ...

# Verify the submission landed under the right identity:
jobsub_q --user mu2epro
```

The `/mu2epro-submit` skill wraps this. See also
`reference_ksu_jobsub_env` and `reference_ksu_muse_work_dir_collision`
in memory for related ksu gotchas (env re-export after jobsub calls,
`MUSE_WORK_DIR` collisions).

### 3. Reading `submissions` output

```bash
submissions
```

prints, in order:

- `queue cap in effect: N` — the resolved top-up cap for *this*
  invocation (`--max-queued` flag > `MU2E_MAX_QUEUED` env > `10000`;
  nothing is stored, so this line is always the live answer).
- The **ledger table**: `id / state / att(empt) / parent / #idx /
  created / tarball` — one row per submission (or resubmit chain link).
  `state` is one of `active | complete | recovered | exhausted`.
  `parent` is the ledger row this one recovers, if any (follow the
  chain to see a lineage's current tip).
- The **campaigns table** (only if any exist): `id / state / cursor /
  njobs / slice / created / tarball` — `cursor/njobs` shows submission
  progress (not verification progress — a `complete` campaign can still
  have `active` ledger rows being verified).

### 4. Exit-2 playbook

`submissions run` exits 2 whenever this pass (or, under `--dry-run`,
would this pass) leave something needing a human. One line per cause:

- **held** — a row has held jobs in the queue. Inspect with `jobsub_q
  --jobid <id>`, release or `condor_rm` by hand (the loop never
  touches held jobs itself); the next tick picks it up once it drains.
- **exhausted** — a row hit `--max-attempts` with outputs still
  missing. Human root-cause required: deterministic payloads mean the
  same failure repeats every retry, so re-running `submissions run`
  again will not fix it.
- **child-missing** — a resubmit succeeded but no child ledger row was
  recorded (a ledger-write failure right after a real submission). The
  chain is now unwatched — verify the new submission's indices
  manually and, if needed, insert the missing ledger row by hand.
- **campaign paused (submit failure)** — top-up tried to submit a slice
  and `submit_map` failed. Check `submit-YYYYMMDD.log` and `jobsub_q`
  for whether the failed attempt actually queued anything, then
  `submissions resume <ID>`.
- **campaign paused (crash-window overlap)** — the ledger already
  covers indices inside the next slice window (a prior submit/cursor
  write was interrupted by a crash). Reconcile the cursor against the
  ledger for that tarball (`submissions status` / `sqlite3`) *before*
  `submissions resume <ID>` — resuming blind risks the very
  double-submit the guard exists to prevent.
- **count-error** — `jobsub_q --user mu2epro -af JobStatus` itself
  failed or returned unparseable output. Top-up is skipped entirely
  this tick (not just under-counted) — every active campaign is
  starving until `jobsub_q` is healthy again.
- **paused-campaign (lingering)** — any campaign still `paused` when
  this tick ran, not just the tick that paused it — the signal repeats
  every hour until a human runs `submissions resume <ID>` or
  `submissions cancel <ID>`.

### 5. Fixing a live campaign's settings

```bash
submissions set-entry <CAMP_ID> <key> <value> [--include-open-rows]
```

`set-entry` edits one of `inloc`, `memory`, `disk`, or
`expected_lifetime` on a live campaign's entry. Without
`--include-open-rows` the change reaches future slices only —
`resubmit()` rebuilds a recovery from the row's own frozen snapshot,
not the campaign's current entry, so a row already submitted keeps
whatever it was submitted with. With `--include-open-rows`, every
not-yet-closed row on that campaign's tarball is rewritten too, which
is what makes an in-flight RECOVERY actually pick up the new value.

The flag defaults off because an *unset* `memory` is what earns a
recovery the `4000MB` floor (see the resource-key caveat above); pushing
a memory value onto every open row would forfeit that floor for indices
that hadn't needed it yet. An `inloc` fix, by contrast, normally wants
the flag on — a bad `inloc` (e.g. a resilient copy that was never
staged) breaks every open row identically, and there is no floor to
lose by cascading it.

Worked example — campaign 54 (`sim.mu2e.PiTargetStops.Run1Bap.art`
input, 500 files, 14.95 GB, needed staging to resilient first):

```bash
submissions set-entry 54 inloc resilient --include-open-rows
```

prints the changed row ids; confirm the new value stuck with
`campaign_status` (MCP) or `submissions status`.

## Semantics and limits

Re-runs are **deterministic**, not probabilistic retries — the payload
(fcl, inputs, seed) is identical between attempts, so a systematic
failure (a bad input file, a wall-clock tail, an OOM at a fixed memory
request) fails the same way every attempt. `exhausted` is therefore not
a failure of the loop; it is the loop correctly recognizing that blind
retry will not fix this and handing off to a human. Held jobs are never
touched automatically — `condor_rm`/`condor_release`/`jobsub_rm` are
always a human decision, reported but not acted on. Submissions made
with `--no-ledger` (ad-hoc smoke tests, `/mu2ejobsub-submit`-style
one-offs, anything explicitly opted out) are structurally invisible to
this loop — there is no row to process, so no drain-check, no verify,
no resubmit will ever happen for them; that is intentional, not a gap
to fix. POMS-launched entries (anything dispatched through a POMS map,
or submitted directly via the upstream `mu2ejobsub`/`mu2eg4bl` CLIs) are
out of scope by construction, not by convention: `submit_map` is
single-backend direct — it has no `--backend` flag and never drives
`mu2ejobsub` — so the ledger hook only fires on submissions that go
through `submit_map` itself; POMS-launched jobs simply never produce a
row. That also means the loop cannot race POMS's own recovery
machinery — there is no shared state for the two to disagree about.

This is a pre-existing `submit_map` caveat, inherited by the loop rather
than introduced by it: if a submission exits 0 with no parseable cluster
id, `submit_map` reports it as a failure and writes no ledger row — but
if that submission was genuinely partial (jobs were actually queued
before the parse failure), a later manual resubmission of those same
indices double-runs them. Verify with `jobsub_q` before resubmitting by
hand in that specific situation.

## Draining campaigns (2026-08-01)

A third campaign shape, alongside recovery and sliced top-up: a map
entry with `input_pattern` (5-field dataset pattern, `%` wildcards) and
NO `njobs` drains a growing input dataset 1:1 through a generic cnf,
enqueued the same way (`submit_map --map M --enqueue --slice-size N`).
`is_draining(entry)` (`utils/poms_entry.py`) is the single-owner kind
discriminator everywhere in `submissions.py` — never sniff
`indices_json`/`entry` shape by hand.

**Pending predicate.** No cursor: `draining_state` recomputes fresh
from SAM every tick — `pending = inputs − landed − in_flight − parked`,
where `landed` means every one of a file's expected outputs (computed
per-file from the cnf's own `job_outputs`, via `expected_outputs_for`)
already exists in SAM. Nothing counts as done until its output exists —
the structural fix for `drainingn`'s launch-time snapshot cursor (see
[[poms-reference]] for the mechanics this replaces).

**Two gates** before a candidate batch dispatches: a settling-age gate
(`min_age_minutes`, default 60, against SAM `create_date` — the key
live SAM metadata actually carries; `create_datetime` is tolerated as a
legacy fallback) and a dCache-residency gate (tape-only candidates are
withheld unless the entry opts in with `prestage: true`). Both fail
closed on any unknown.

`drain_tick` feeds **one gated batch per campaign per tick**, oldest-
first, under the same queue cap as index top-up. File-keyed rows verify
and resubmit by filename (`verify_files_row`/`resubmit_files`, the
draining analogs of `verify_row`/`resubmit`); an exhausted row's still-
missing files become **parked** — held out of `pending` until a human
re-dispatches them via `submit_map --files LIST.txt`. `submissions
complete <id>` is the operator close-out — draining never auto-
completes, since the input set keeps growing until the upstream
finishes.

Design: `docs/superpowers/specs/2026-08-01-draining-campaigns-design.md`.

## Related

- [[poms-reference]] — the POMS recovery machinery this loop deliberately
  does not touch or replace
- [[justin-vs-prodtools]] — the "recovery as a property of file state"
  model this loop approximates for the direct backend without adopting
  justIN wholesale
- [[2026-07-05-run1ban-mix-recovery-data-loss]] — the incident that
  motivates SAM-output-only verification and non-destructive resubmission
- [[2026-04-30-phase2-direct-jobsub-implementation]] — the direct
  backend this loop adds recovery to
- `docs/superpowers/specs/2026-07-18-direct-recovery-design.md` and
  `docs/superpowers/plans/2026-07-18-direct-recovery.md` — design and
  implementation plan
- `docs/superpowers/specs/2026-07-18-sliced-submission-design.md` and
  `docs/superpowers/plans/2026-07-18-sliced-submission.md` — design and
  implementation plan for the "Sliced campaigns (top-up phase)" section
  above
- `docs/superpowers/specs/2026-07-19-workflow-hardening-design.md` —
  design for the `recover` → `submissions` rename, the extended exit-2
  set, the flag-hygiene fixes, and the `submit_map` single-backend
  retirement; also records the "Decided against" ruling on a cross-path
  ownership key (see the Operator quickstart decision tree above for
  the interim mitigation)
