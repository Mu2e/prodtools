---
title: Direct-backend recovery loop — ledger + recover + cron
tags: [decision, recovery, direct-backend, submit_map, operations, sliced-campaigns]
sources: [docs/superpowers/specs/2026-07-18-direct-recovery-design.md, docs/superpowers/specs/2026-07-18-sliced-submission-design.md]
updated: 2026-07-19
---

# Direct-backend recovery loop — ledger + recover + cron

## What and why

`submit_map --backend direct` (the Phase 2 in-house submission path —
see [[2026-04-30-phase2-direct-jobsub-implementation]]) had no automated
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
here structurally avoids both failure modes: `recover` only ever asks
"does SAM have this file," never "did a job report success," and it
never deletes anything — a re-run's `pushOutput` re-declares, it does
not clobber.

## Architecture

Three new pieces and one hook into the existing submit path. No
worker-side changes — the fcl a worker runs is byte-identical whether or
not the ledger is involved.

```
submit_map --backend direct ──writes──▶ submission ledger (sqlite3)
                                              │
        mu2epro crontab ── bin/recover_cron ──▶ bin/recover
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
successful `--backend direct` submission — "successful" meaning a
cluster ID was actually parsed from `jobsub_submit`'s output; the
existing "exited 0 but no cluster ID" failure path still counts as a
failure and writes nothing.

Each row snapshots the map entry **verbatim** (`entry_json`) rather than
storing a reference back to the map file — the same reason POMS
snapshots JobTypes rather than re-reading them live. Recovery then
survives map edits, map file moves, and the fact that mu2epro
submissions typically run out of a throwaway `/tmp` workdir that may not
even exist anymore by the time `recover` runs. `indices_json` holds the
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
plus an already-absolute index would double-count. So when `recover`
reconstructs a single-entry map to resubmit missing indices, it drops
`firstjob` from that reconstructed entry entirely. This is safe because
the indices being resubmitted are *already* absolute cnf indices (they
came out of the ledger that way); the worker-side `firstjob + index`
resolution then degenerates to the identity. The original windowed
entry is untouched in the parent row's own snapshot — only the
throwaway recovery-map entry drops the field.

**Recovery loop (`utils/recover.py`, `bin/recover`).** Per active row,
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

**Cron wrapper (`bin/recover_cron`).** Not installed anywhere by this
branch — it is only the entry point a human wires into mu2epro's
crontab once the checklist below is cleared. In order: `flock` a
lockfile beside the DB so overlapping cron runs can't double-submit;
quiet Mu2e environment setup; a token check via `httokendecode` that
**only reports** a missing/expired token and exits non-zero — it never
fetches or refreshes one (this project's standing "never remediate
token problems" rule, also encoded in memory as
`feedback_never_get_mu2epro_token`); then `recover`, with output
appended to a dated log (`recover-YYYYMMDD.log`) beside the DB.

## Sliced campaigns (top-up phase)

Since 2026-07-19 the loop does more than recover — it can also *drive* a
big submission forward on its own, the direct-backend analog of a POMS
`drainingn`/`nfiles` split-type stage that hands out the next slice each
cron tick. Design: `docs/superpowers/specs/2026-07-18-sliced-submission-design.md`,
plan: `docs/superpowers/plans/2026-07-18-sliced-submission.md`. One new
table (`campaigns`, same sqlite3 DB) and one new phase inside `recover`
— no new daemons, no new cron entries, no worker-side changes.

**Enqueue workflow.** An operator registers a campaign instead of
submitting directly:

```bash
submit_map --map MDC2025-032.json --backend direct --enqueue --slice-size 2000
```

This snapshots the selected entries (all, or `--entry N`) into the
`campaigns` table at `cursor=0` and **submits nothing** — same
"hard error, not a fallback" discipline as the ledger write in the
normal submit path, but inverted: here nothing has gone to the grid
yet, so a DB failure at enqueue time is a hard error rather than a
warn-and-continue. `--slice-size` (default 1000) is frozen into the
row. Direct backend only; mutually exclusive with
`--first`/`--num`/`--indices`/`--indices-file`. An entry with no fixed
`njobs` (`generic_tarball`) can't be enqueued — a campaign needs a job
count to slice against. A second `--enqueue` for a tarball that already
has an *active* campaign is refused outright — no silent double-feed.

**Top-up semantics.** Every `recover` invocation (the same hourly cron
tick that does recovery) runs the top-up phase *after* the recovery
pass, under the same per-DB lock, with one fast-path skip: no active
campaigns means zero extra queries, so the top-up phase costs nothing
when unused. When there is work: count total mu2epro idle+running jobs
(`jobsub_q --user mu2epro -af JobStatus`, states `1`+`2`) — this counts
**everything** mu2epro has queued, POMS-launched jobs included, so the
cap bounds the account's whole farm footprint, not just this tool's
slice of it. Running top-up after recovery means a tick's
resubmissions are already counted before top-up measures headroom.
Then round-robin over active campaigns, oldest-first, one slice per
campaign per cycle: `n = min(slice_size, njobs - cursor)`; if
`count + n` would exceed the cap, that campaign (and the whole tick)
stops there — **whole slices only**, never a slice clamped down to fit
remaining headroom, so there's no confetti of tiny ledger rows. A slice
short of the full `slice_size` happens only at the tail of an entry,
never because of the cap. Each slice submits via the same `submit_map`
CLI subprocess call recovery already uses (`--map <tmp single-entry
map> --backend direct --first <cursor> --num <n>`), so it gets a
regular ledger row like any other direct submission — the campaign row
tracks the *cursor*, the ledger rows track the actual submitted jobs.

Cap resolution mirrors the DB-path pattern: `--max-queued` flag >
`MU2E_MAX_QUEUED` env > `10000` built-in default (`DEFAULT_MAX_QUEUED`
in `utils/recover.py`). Resolved once per invocation, nothing persists
between runs — deliberately not stored in the DB, so the effective cap
is always readable straight off the crontab line, and `recover
--status` prints it every time.

**Pause / resume / cancel.**

```bash
recover --pause-campaign 7    # operator off switch
recover --resume-campaign 7   # paused -> active
recover --cancel-campaign 7   # close; already-submitted rows still recovered
```

These are mutating (same per-DB lock as a full pass) and exit
immediately after acting — not valid combined with `--dry-run`.
`paused` means one of two things: a submit failure during top-up (the
loop pauses the campaign automatically rather than blind-retrying —
deterministic payloads make an unverified resubmit the exact Run1Ban
failure shape: queued-but-unrecorded jobs plus a later duplicate
resubmit), or an operator hold via `--pause-campaign`. Either way the
loop skips it until a human clears it. Before `--resume-campaign` after
an automatic pause, check the submit log and `jobsub_q` for whether the
failed attempt actually queued jobs — the ledger hook only fires on
success, so a partial failure leaves indices that look unsubmitted but
might not be. `cancelled` (via `--cancel-campaign`) closes the campaign
only; ledger rows already written for it continue through the recovery
loop to verified completion exactly as if the campaign were still
active — cancelling stops new slices, it does not abandon jobs already
in flight.

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
  campaign cursor/state. Query it with `recover --status` or `sqlite3`
  directly. This is the only layer with machine-checkable state.
- **Submit log** (`submit-YYYYMMDD.log`) — human-readable per-attempt
  record, including the raw `jobsub_submit` output, for *every* origin
  (manual/slice/recovery) uniformly. Answers "what actually happened
  when this specific submission ran, and what did jobsub say."
- **Recover log** (`recover-YYYYMMDD.log`, written by `bin/recover_cron`)
  — the loop's own decisions: measured queue count, cap in effect, per
  slice campaign id / tarball / entry-relative range / resulting jobsub
  id, and explicit skip lines ("headroom < slice, waiting", "queue
  count failed, top-up skipped", "campaign N paused: submit failed").
  Answers "why did (or didn't) the loop act this tick."

A stuck campaign is usually: recover log says why top-up didn't feed it
(cap, count-query failure, or the campaign isn't `active`); if it did
feed and nothing shows up in `jobsub_q`, the submit log has the raw
`jobsub_submit` output for that attempt; the ledger tells you whether a
row actually got recorded for it.

**Resource-key inheritance fix.** Building the top-up phase surfaced
(and fixed) a bug that predates sliced campaigns: a plain recovery
resubmit used to silently drop CLI resource overrides. `submit_map
--memory 4000MB` was CLI-only — the ledger snapshot didn't record it —
so a `recover` resubmit of those same jobs would fall back to the
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
17 * * * * <prodtools>/bin/recover_cron
```

where `<prodtools>` is the checked-out repo path that also serves
`submit_map`. Logs land at `recover-YYYYMMDD.log` next to the ledger DB
(`/exp/mu2e/data/users/mu2epro/prodtools/recover-YYYYMMDD.log` with the
default DB path) — one file per day, appended to across all cron
invocations that day.

Anyone can check status without mu2epro privileges — status checks
never need production credentials (`feedback_status_checks_no_mu2epro`
in memory):

```bash
recover --status
```

## Pre-activation checklist

These four items must be checked off **before** the cron line above
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
  verified, treat any `recover --dry-run` output that reports partial
  indices with extra suspicion, and prefer a manual look at those
  indices over letting the loop resubmit them unattended.
- **One real `recover --dry-run` pass** over a genuine ledger row on a
  cluster that has actually drained from the queue. This exercises the
  full tarball-locate → parse → SAM-list → diff path against real SAM
  and real dCache state, which no unit test does.
- **`jobsub_q --jobid <id> -af JobStatus` passthrough confirmed** against
  a real jobsub_lite installation. `queue_state` in `utils/recover.py`
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
  queue-count function in `utils/recover.py`) counts `JobStatus` tokens
  `1`/`2` across mu2epro's *whole* queue, not one job's. If `-af` output
  shape differs by-user vs by-jobid on this jobsub_lite install, the
  top-up cap silently miscounts and either starves a campaign or
  overshoots the farm-footprint cap it's meant to enforce. Confirm with
  a real `--user mu2epro` query against a live queue before relying on
  the count for anything unattended.

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
to fix. POMS-backend entries (`--backend mu2ejobsub` or anything
dispatched through a POMS map) are out of scope by construction, not by
convention: the ledger hook only fires inside the direct-backend submit
path, so POMS-backend jobs simply never produce a row. That also means
the loop cannot race POMS's own recovery machinery — there is no shared
state for the two to disagree about.

This is a pre-existing `submit_map` caveat, inherited by the loop rather
than introduced by it: if a submission exits 0 with no parseable cluster
id, `submit_map` reports it as a failure and writes no ledger row — but
if that submission was genuinely partial (jobs were actually queued
before the parse failure), a later manual resubmission of those same
indices double-runs them. Verify with `jobsub_q` before resubmitting by
hand in that specific situation.

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
