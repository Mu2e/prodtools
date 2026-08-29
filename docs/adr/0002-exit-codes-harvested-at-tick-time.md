---
status: accepted
---

# Exit codes are harvested at tick time and persisted to the ledger

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
