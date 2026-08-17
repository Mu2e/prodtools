# `jobwait` — block until a cluster finishes, record how each job ended

**Date:** 2026-08-16
**Status:** draft — pending operator review
**Requested by:** the autoresearch prodtools switch
(`autoresearch:docs/superpowers/specs/2026-08-16-prodtools-switch-design.md`),
but generic: any caller that submits a cluster and needs a synchronous
"done, and here is the per-job outcome" answer.

## Goal

The grid twin of `runlocal`: one command that waits for a submitted cluster
to leave the queue, collects each job's exit code from condor history, and
writes the same `--json` summary `runlocal` writes. Three commands composed,
~40–80 lines, no policy.

```
jobwait --jobdef cnf.tar --cluster <id>@<schedd> [--poll-s 300] [--json out.json]
```

Why it must exist: condor history is a fading record (measured 2026-08-16:
jobs from ~2 weeks prior already rotated out of the mu2e schedds), so per-job
outcomes must be captured into a durable file at drain time or they are lost.
Nothing in prodtools or jobsub blocks today: the `submissions` cron ticks and
moves on, and `jobsub_wait` is a `condor_wait` wrapper that needs a local
condor event log jobsub_lite submissions do not leave on the submit node
(verified 2026-08-16 — it prints condor_wait usage and exits).

## How it decides "done"

Exit codes are the complete success record in direct mode: `runjob.sh` →
`runmu2e.py` runs the output copy *inside* the job, so a job can only exit 0
after its copies landed. No filesystem check is needed, and none is done.

1. **Wait.** Every `--poll-s` (default 300 s): take a `jobsub_q --user`
   snapshot via `submissions.live_clusters()` and classify with
   `cluster_queue_state()`. `running`/`held` → keep waiting. `error` (query
   failed or unparseable) → keep waiting; a failed query is never "drained"
   (fail-closed, same rule the cron learned). Only `drained` proceeds.
2. **Collect.** One history call:
   `jobsub_history -G mu2e -J <cluster>@<schedd> -limit <njobs> -af ProcId ExitCode`
   with `<njobs>` read from the cnf (`jobquery`). `-limit` passes through to
   condor_history and stops its newest-first scan early: measured 8.4 s vs
   51 s unlimited on a real 999-job cluster, and a just-drained cluster sits
   at the head of the history file — expect seconds. Fewer records than
   njobs → the scan simply completes (~51 s) and the absent indices are
   reported `rc: null` (status `unknown`).
3. **Report.** Write `--json` atomically (reuse `runlocal.py`'s
   `write_summary` machinery / schema): per-index
   `{index, rc, outputs: [...]}` — output paths are deterministic from the
   cnf (`expected_outputs_for`); exit 0 is the receipt they exist — plus
   `ok`, `failed`, `unknown` counts and the jobdef/cluster identity.
   Exit 0 iff every job exited 0; the JSON is written regardless, and the
   partial run is exactly when the caller needs it (runlocal's rule).

## Deliberate non-features

All operator decisions from the 2026-08-16 design conversation; recorded so
they are not "improved" back in later:

- **No file checking, primary or fallback.** Pre-drain file checks race
  condor's evict-and-rerun (a job can re-run after its copy if evicted
  between copy and exit), and hammer the dCache namespace across parallel
  campaigns. Post-drain fallback counting was rejected as guessing: empty
  history → honest `unknown` + nonzero exit beats inferred success. It also
  keeps jobwait free of any /pnfs or xrootd dependency — it runs on any node
  with a bearer token.
- **No internal timeout.** Held/stuck jobs → jobwait waits. Patience is the
  caller's policy (`timeout 24h jobwait …`, or the caller's own barrier).
- **No acceptance threshold.** Exit 0 means "all jobs ok", nothing more.
  Whether 95% completion is acceptable is caller policy, read from the JSON
  (`ok`/`expected`), for the same reason the timeout stays out.
- **No SAM, no ledger reads.** Completion detection is queue-side only. The
  submissions ledger remains submit-side bookkeeping.

## Components

### `utils/jobwait.py` (new)

The loop + history call + report. Imports, not reimplements:
`submissions.live_clusters` / `cluster_queue_state` (fail-closed snapshot),
`jobquery`/`Mu2eJobPars` (njobs, expected output names),
`runlocal`'s summary/JSON writer (schema-identical output — the point of the
shared contract). Subprocess runners injectable for tests, matching
`live_clusters(runner=...)`.

### `bin/jobwait` (new)

Thin wrapper, same shape as `bin/runlocal`: `--help` short-circuit, source
`setupmu2e-art.sh` + `muse setup ops`, exec the python.

### Unchanged

`submit.py` (already returns cluster id + schedd via `_parse_jobsub_id`),
`runmu2e.py`, `runlocal.py` (its writer may need a small extract-to-function
if not already importable), the ledger, the cron.

### Documentation

One EXAMPLES.md section: submit → jobwait → read JSON, including the
partial-failure JSON shape.

## Failure modes

- `jobsub_q` unreachable for hours: waits forever by design; caller's
  timeout is the backstop. Every failed snapshot logs one line.
- History empty or short (retention, schedd trouble): affected indices
  `rc: null`, `unknown` counted, nonzero exit. Never guessed complete.
- Cluster id never seen in any snapshot (typo, already drained before first
  poll): first snapshot classifies it `drained` → proceeds to history —
  a mistyped cluster yields `unknown` for all indices, nonzero exit, visibly
  wrong rather than hanging.
- Held cluster: reported in the wait-loop log lines (`held`), keeps waiting —
  operator intervenes with jobsub_hold/release/rm; jobwait then resolves
  normally.

## Testing

### Unit (no grid contact)

Injected fake runners for `jobsub_q` / `jobsub_history`:

- running → drained transition ends the loop; `error` snapshots never do.
- held is waited on, logged.
- history rows → per-index rc mapping; short history → `unknown` + rc null;
  full success → exit 0; one failure → exit 1, JSON still written.
- JSON schema equality with a `runlocal --json` fixture (the shared-contract
  claim, asserted).
- atomic write: no partial file on simulated crash mid-write.

### Live gate

One small real cluster (operator-approved): submit N=2 via `submit`, run
`jobwait`, confirm ExitCode passthrough end-to-end for our own jobs (verified
so far only against mu2epro's clusters), confirm JSON matches reality.

## Out of scope

- Any autoresearch-side rewiring (its own spec, linked above).
- Notifications, progress bars, multi-cluster waits (one cluster per call;
  callers loop).
- Retry/resubmit of failed indices (the JSON names them; resubmission is a
  caller decision — `submit --indices` already exists for it).
