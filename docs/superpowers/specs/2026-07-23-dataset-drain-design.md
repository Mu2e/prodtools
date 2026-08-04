# Dataset-Drain Campaigns (Data Dispatcher) — Design

**Date:** 2026-07-23
**Status:** Design approved, pending implementation plan

## Goal

Give the direct-submission subsystem a POMS-like capability: **bind a campaign
to an input dataset and keep draining it as that dataset grows**, instead of
requiring a fixed `njobs` known upfront.

The motivating shape is the mcs/nts pattern — a downstream stage (reco,
evntuple) consuming a continuously-produced upstream dataset, one input file
per output file.

## Background: what already exists

Most of the machinery is already built and in production; the gap is narrow.

**Already working (via POMS):**

- **Generic cnfs** — `cnf.mu2e.reco.MDC2025ar_best_v1_1.0.tar`,
  `cnf.mu2e.evnt.MDC2025ar_best_v1_1.0.tar` (and the Run1Ban pair). These carry
  **no `njobs` and no frozen input filelist**; `{desc}` is deferred.
- **The direct-input runner** — `process_direct_input(jobdesc, fname, args)`
  (`utils/runmu2e.py:235`). Hand it any art file; it processes that file and
  derives the output `desc` + `sequencer` from the input filename, with `dsconf`
  baked in from the cnf. Its docstring already anticipates this design:
  *"fname is an actual art file (e.g. assigned by Data Dispatcher)."*
- **Mode detection** — "tarball present but no njobs" selects `direct_input`
  (`utils/runmu2e.py:138-146`).
- **The tick** — `submissions run` already provides the periodic loop, the
  queue-depth throttle (`total_queued`, cap), the sqlite ledger, and
  fail-closed drain checking.

**The two blockers:**

1. `_direct_main` (`utils/runmu2e.py:823`) *resolves an index* — the direct
   backend's work unit is a condor `PROCESS` → cnf index. The fname path is
   reachable only through the POMS entry point.
2. `submit_map` **refuses** entries without `njobs`
   (`utils/submit.py:250`: `"entry N has no njobs (generic ...)"`); generic
   tarballs are skipped in normal dispatch.

So this design adds an orchestration layer and one worker mode. It introduces
**no new physics path** — the art-running code is the existing, proven runner.

## Non-goals

- Replacing the POMS-driven reco/ntuple draining that runs today. This is an
  additional option, not a migration.
- Changing the fixed-`njobs` campaign type (generation/mixing). That model and
  its deterministic index→input mapping are untouched.
- Building our own file-state/claim/lease store. That is Data Dispatcher's job.

## Key decisions

### D1 — Data Dispatcher is the file-allocation queue

DD (`ddisp`, server `https://metacat.fnal.gov:9443/mu2e_dd_prod/data`, v2.0.2)
owns per-file state: `available / reserved / done / failed`, plus worker leases.

*Rationale:* it is the Fermilab standard and the direction Mu2e is heading
(SAM→metacat), the codebase already names it, and the alternative means
building a claim/lease table we would later discard. Its Python client lives in
the ops env's py3.10 — the same interpreter the tick runs under — so this is an
in-process `import data_dispatcher`, **not** a subprocess.

### D2 — The growth loop is ours to build

DD projects are **snapshots**: `ddisp project` offers
`create/copy/show/list/restart/activate/cancel/delete` and has **no
"add files to a live project"** command. `project create` resolves its file list
at creation (inline MQL, `-q` query file, or `-l`/`-j` explicit lists).

Therefore *noticing new files and turning them into work* is our layer,
regardless of queue choice. Concretely: **one DD project per tick-delta.** A
long-lived project cannot see files that appear later, so "longer-lived
project" is not an available option.

### D3 — Output existence is authoritative for completeness; DD state is not

A worker marking a file `done` in DD means *the worker believed it succeeded*.
That is not proof the output landed — this codebase has already been bitten by
`pushOutput` exiting 0 while silently pushing nothing.

So the growth loop subtracts inputs whose **expected output already exists**,
not merely those DD marks `done`. This preserves the subsystem's existing
principle: verify against artifacts, never guess complete.

The expected output name is computable by the orchestrator using the same
derivation the worker uses — output `desc` + `sequencer` come from the input
filename, `dsconf` and tier from the cnf. DD `done` state remains useful as a
fast pre-filter and for operator visibility, but it is never the completeness
authority.

### D4 — Input discovery via metacat, output verification via SAM

DD projects are defined by metacat queries, so input discovery is metacat-side.
Outputs are declared in SAM by `pushOutput` today, so verification stays on the
existing, proven `samweb_wrapper` path.

This hybrid is deliberate and transitional. When output cataloging moves to
metacat, the verification side swaps behind the existing wrapper seam — a
contained change, not a redesign.

### D5 — Multi-file drain workers, not one job per file

A worker claims files in a loop until the project is empty or its time budget
is exhausted. This is what DD is designed for: submit K workers and let them
self-balance; a dead worker's lease expires and its file returns to
`available` for someone else.

*Rationale:* it removes one-submission-per-file churn, self-balances against a
backlog of unknown size, and gets retry-on-worker-death for free. The cap then
throttles **K (concurrent workers)** rather than jobs-per-file.

## Architecture

### New campaign type: `drain`

A ledger campaign that binds a **generic cnf** to an **input selector** instead
of a fixed `njobs`:

- `tarball` — a generic cnf (no njobs)
- `input_query` — the MQL selector describing the growing input dataset
- `outputs` — output dataset/location, as today
- `dd_worker_timeout` — DD lease per file (see R2)
- `max_workers` — ceiling on concurrent workers for this campaign

Fixed-`njobs` campaigns are unchanged; `drain` is a distinct state machine
sharing the same ledger and throttle.

### Tick lifecycle (extends `submissions run`)

1. **Verify** — unchanged for existing rows.
2. **Grow** — for each active `drain` campaign:
   a. Query metacat for files matching `input_query`.
   b. Subtract files whose expected output already exists (D3), and those
      currently `reserved`/`done` in the campaign's live DD projects.
   c. If the remainder is non-empty, `project create` a DD project over exactly
      those files; record the project id against the campaign.
3. **Top up** — submit workers for campaigns with a non-empty backlog, sized by
   backlog and bounded by the existing queue cap and `max_workers`. Each worker
   carries its DD project id.

### Worker lifecycle (new mode in `runmu2e`)

Direct mode gains a branch: when the ops JSON carries a DD project id, instead
of `_resolve_direct_index(ops)`:

```
loop:
    fname = dd.next_file(project)      # claim; None => project drained
    if fname is None: exit 0
    if time_budget_exhausted(): exit 0 # release claim, do not start new work
    run process_direct_input(jobdesc, fname, args)   # EXISTING runner
    pushOutput
    dd.mark(fname, done | failed)
```

The claim/lease is DD's; the art execution and output push are the existing
code paths.

### Duty split

| Concern | Owner |
|---|---|
| Which input files exist / are claimed / failed | Data Dispatcher |
| Which *jobs* were submitted, cluster ids, throttle | sqlite ledger |
| Whether an output actually landed | SAM (via `samweb_wrapper`) |

No component duplicates another's state.

## Error handling and recovery

- **Worker dies mid-file** — DD's lease (`-w`) expires and the file returns to
  `available`; a later worker picks it up. No operator action.
- **File fails repeatedly** — the worker marks it `failed`; it parks in DD for
  human review rather than looping forever. The tick reports non-zero `failed`
  counts as an attention condition (consistent with existing exit-2 honesty).
- **`pushOutput` silently no-ops** — caught by D3: the input is not subtracted
  because its output does not exist, so it is re-offered on a later tick.
- **DD unreachable / token expired** — the grow and top-up phases for `drain`
  campaigns skip with a reported error and **submit nothing** (fail-closed,
  matching the existing queue-count and drain-check behaviour). Fixed-`njobs`
  campaigns in the same tick are unaffected.
- **Duplicate processing** — prevented by DD reservation; if it happens anyway
  (e.g. a lease expires while the original worker is still alive), outputs are
  deterministic for a given input file, so the second push is a duplicate
  declare, not corruption.

## Prerequisites and risks

**R1 — Worker-side DD authentication (highest risk, blocking).**
Grid workers must authenticate to DD/metacat to claim files. `ddisp` currently
returns `WebAPIError: Token expired` locally, and the production account's
metacat scope is governed by the existing token cron — **we do not refresh
mu2epro tokens**. Before any implementation work, a phase-0 smoke must prove a
single grid job can authenticate to DD and claim one file. If it cannot, the
design does not proceed as written.

**R2 — Lease duration must be tuned per stage.**
DD's default worker timeout is 12h. With 24h job lifetimes, a dead worker would
strand its file for 12h. `dd_worker_timeout` should be set to roughly the
stage's worst-case per-file processing time plus margin (hours for reco, far
less for evntuple), not left at the default.

**R3 — Metacat/SAM split (D4).** Input discovery and output verification use
different catalogs during the transition. Accepted deliberately; revisit when
outputs move to metacat.

**R4 — Generic-cnf coverage gaps.** A single generic cnf cannot do per-input
overrides. The known example: the generic evnt cnf cannot ntuple `NoPrimary`
(its `genCountLogger` needs `GenEventCount` that pure-pileup input lacks).
Drain campaigns inherit this limitation; such descs keep dedicated entries.

## Testing strategy

- **Unit** — growth-loop set arithmetic (metacat listing minus existing outputs
  minus reserved) with injected fakes; expected-output-name derivation from an
  input filename; worker-loop control flow (drained, budget exhausted, failure
  marking) against a stubbed DD client. No network in unit tests, consistent
  with the existing `runner=`/injection style.
- **Fail-closed tests** — DD unreachable, token expired, and metacat query
  failure each submit nothing and report; a `drain` failure must not disturb
  fixed-`njobs` campaigns in the same tick.
- **Phase-0 integration smoke (gates everything)** — R1: one grid job
  authenticates to DD, claims one file, processes it via the existing generic
  reco cnf, pushes output, marks `done`.
- **End-to-end** — a small drain campaign over a static input dataset, then the
  same dataset grown by a few files, asserting the second tick picks up exactly
  the new files and no others.

## Implementation surface

- `utils/submission_ledger.py` — `drain` campaign fields, DD project ids.
- `utils/submissions.py` — grow phase, drain-aware top-up, reporting.
- New module (`utils/dd_client.py`) — thin wrapper over `import data_dispatcher`
  so tests inject a fake and the rest of the code stays DD-agnostic.
- `utils/runmu2e.py` — the worker pull branch in direct mode.
- `utils/submit.py` — allow generic (no-njobs) entries on the drain path only;
  the existing refusal stays for normal dispatch.
- `bin/submissions` / EXAMPLES / wiki — operator surface for creating and
  monitoring drain campaigns.
