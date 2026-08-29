---
status: superseded
---

# Exit codes are harvested at tick time and persisted to the ledger

> **Superseded 2026-08-28 — never implemented.** SAM verification is the
> completeness mechanism. See "Superseded by SAM verification" at the
> end. The text below is unchanged, kept as the reasoning record.

A chained campaign may write undeclared outputs, which `verify_row`
cannot see: it is fail-closed against SAM, so every index would read as
missing and each tick would recover a row whose files already exist —
the reason `submit._refuse_outstage_campaign` exists. Exit codes answer
completeness without a filesystem or SAM, because `runjob.sh` ->
`runmu2e` performs the output copy inside the job, so a job can only
exit 0 after its copies landed (`utils/jobwait.py`). We therefore
harvest per-job exit codes during `submissions run`, at the moment it
already detects that a row's cluster has left the queue, and persist the
per-index outcomes to the ledger.

## Considered Options

Running `jobwait` per cluster would reuse the existing tool unchanged,
but it blocks until the cluster drains — up to 48h — which means a
waiter process per cluster and a daemon the tick-based design otherwise
avoids.

## Consequences

Condor history is perishable: jobs roughly two weeks old are already
gone from the mu2e schedds. Persisting at tick time converts that
perishable record into a durable one, and is the whole point of
harvesting at the moment the cluster drains rather than on demand.

A tick that misses the window yields `unknown`, not `failed` and not
`complete`. An `unknown` keeps the row active and reports; it must never
be read as complete, and must never start a recovery.

Declared campaigns benefit too. SAM presence alone cannot distinguish a
failed job from one that succeeded while `pushOutput` silently did
nothing — a failure this repo has hit. Exit codes recorded alongside SAM
presence separate the two.

## Superseded by SAM verification (2026-08-28)

This decision was accepted but no part of it was built. There is no
exit-code column in the ledger (`utils/submission_ledger.py` `_SCHEMA`)
and no exit-code read anywhere in `utils/submissions.py`. The only
implementation of the harvest, `collect_exit_codes`
(`utils/jobwait.py:84`), lives in the module this ADR rejected, and
nothing imports it.

On review the gap is narrower than this ADR claims, and the mechanism
it proposed is heavier than the two real problems need.

**The mechanism today.** `verify_row` (`utils/submissions.py:76`)
derives each index's expected output files from the cnf and asks SAM
which landed. `process_row` (`:1144`) either closes the row or hands
the missing indices to `resubmit` (`:497`), which re-fires exactly
those indices. It is per-index rather than whole-batch; it raises
rather than guessing when the tarball is unlocatable or SAM fails ("a
row is never guessed complete"); and it reports `partial` for
half-landed indices. The file is the deliverable, so its presence is
the better question — a job that exits 0 having written nothing is not
complete.

**Correction to this ADR.** "Declared campaigns benefit too" overstated
the case. For a declared campaign, SAM presence is the stronger signal,
not the weaker one.

**The two real gaps, and the lighter fixes already identified:**

- Undeclared (`outstage`) outputs are invisible to `verify_row`, so
  such entries cannot be campaigns
  (`utils/submit.py:383` `_refuse_outstage_campaign`). The fix named
  there is to teach `verify_row` to list the outstage directory — not
  to harvest exit codes.
- A job that failed and a job whose `pushOutput` silently did nothing
  look identical (outputs absent) and both get recovered. The known
  non-retryable instance is already caught worker-side by
  `_is_terminal_push_error` (`utils/runmu2e.py:549`).

Reopen this ADR only if a case appears that neither lighter fix covers.
