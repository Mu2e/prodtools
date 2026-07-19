---
title: Direct-backend recovery loop — ledger + recover + cron
tags: [decision, recovery, direct-backend, submit_map, operations]
sources: [docs/superpowers/specs/2026-07-18-direct-recovery-design.md]
updated: 2026-07-18
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

These three items must be checked off **before** the cron line above
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
