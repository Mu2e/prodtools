# One-shot submission to outstage, with nothing in SAM

Date: 2026-09-19
Status: approved 2026-09-20 in the SMALLER form: `list_runs` and the
`submissions runs` CLI verb are not built (the run name comes back from
`submit_once`, and `run_status` takes it). Everything else as below.
Origin: "Can the submission work with placing the files to outstage
without declaring?" Not today. The worker can; nothing can submit it.

## Context

An entry may name `outstage` as an output location. The worker
(`runmu2e._copy_to_outstage`) then copies the files to
`$MU2EGRID_WFOUTSTAGE/$CLUSTER/$PROCESS` with ifdh and declares nothing;
the log follows the data. `EXAMPLES.md` documents it for test and study
runs whose output should stay out of SAM.

What is missing is a way to submit one:

- `submit.enqueue_entry` refuses it (`_refuse_outstage_campaign`). A
  campaign is verified against SAM, fail-closed; with nothing declared
  every index reads as missing and each tick would recover the whole row
  forever. That refusal is correct and stays.
- The message says "submit it by hand", but the single-purpose submit
  CLI was retired when campaigns arrived. `jobwait`'s docstring still
  refers to it. By hand now means a hand-written `jobsub_submit`.
- ADR 0002 (exit codes harvested at tick time) would have let the ledger
  track undeclared outputs. It was superseded on 2026-08-28 and never
  built. This design does not revive it.

Checked in the code before writing this:

- **The worker never needs the cnf in SAM.** `submit_entry` ships it with
  `-f dropbox://<local path>`, and `runmu2e._direct_main` symlinks it from
  `$CONDOR_DIR_INPUT`. SAM is only a fallback when that file is absent.
- **`submit_entry` never needs it in SAM either.** `_ensure_local_tarball`
  fetches from SAM only when the tarball is not already on disk.
- **The outstage path and its token scope are already on every
  submission** (`jobsub_argv.compute_outstage`,
  `--need-storage-modify <outstage>`), declared outputs or not.
- `json2jobdef` without `--prod` already builds a cnf and pushes nothing.

So the whole chain exists except the entry point and a way to ask how it
went.

Decisions:

- **One shot.** All jobs go in one `jobsub_submit`. No slicing, no ticks,
  no recovery. A short run is remade under a new dsconf.
- **No ledger.** The ledger's job is to verify against SAM and resubmit;
  neither applies. The invariant becomes explicit and enforced both ways:
  **a submission is ledger-tracked if and only if its outputs are
  declared.**
- **Nothing in SAM**, the cnf included.
- **`run_as="self"` only.** Production outputs are declared, always.
- Completeness is read from job exit codes, as `jobwait` does: the output
  copy runs inside the job, so a job can only exit 0 after its copies
  landed.

## 1. Submitting

`json2jobdef --json J --desc D --dsconf C --once [--prodtools-dir DIR]`,
mutually exclusive with `--prod` and `--enqueue`:

1. Refuse unless **every** output location of the entry is `outstage`.
   The message names the key to change. `--once` never rewrites the
   entry's `outloc`: the entry is the record of what was run.
2. Refuse `owner: mu2e` and a run as mu2epro.
3. Refuse `njobs` above 10000 (one `jobsub_submit -N` cannot take more)
   and a generic tarball (no job count).
4. Make the run directory
   `/exp/mu2e/data/users/<user>/prodtools/runs/<cnf name without .tar>/`
   and refuse if it exists. A desc+dsconf pair is used once per user
   here too, for the same reason as in SAM: the output file names derive
   from it, and two runs writing the same names cannot be told apart
   afterwards.
5. Build the cnf there (the existing no-push path). Until now the cnf of
   an MCP-driven build landed in the server's cwd, the repo root.
6. Resolve the worker code exactly as `--enqueue` does (`current` on
   cvmfs, or the `--prodtools-dir` checkout tarred and digest-pinned).
7. Run the input pre-flight (`_preflight_inputs`), unchanged.
8. Write `receipt.json` with `state: "submitting"` and the full entry.
9. `jobsub_submit` the whole job set.
10. Rewrite the receipt: `state: "submitted"`, `jobid`
    (`NNNN@jobsub0X.fnal.gov`), `cluster_id`, `njobs`, `outstage`
    (the absolute `$MU2EGRID_WFOUTSTAGE`), `prodtools_dir`, UTC time. On a
    failed submit: `state: "failed"` with jobsub's last lines.

Steps 8 to 10 are the ledger's reserve / attach / fail pattern in a file.
A process killed between 9 and 10 leaves `submitting`; step 4 then
refuses a second attempt under that name, so an accepted-but-unrecorded
cluster is never duplicated. The message says to look in `jobsub_q` and
to pick a new dsconf.

In `submit_entry` the three ledger calls (`_reserve_in_ledger`,
`_attach_cluster` / `_fail_reservation`, `_log_submission`) are skipped
when `SubmitOptions.ledger_db is None`. That is guarded, because an
untracked production submission is the failure this repo fears most:

- `ledger_db is None` with any non-outstage output raises before
  anything is submitted;
- a ledger path with any outstage output raises too (today only
  `enqueue_entry` checks this; `submit_entry` will as well).

Every existing caller passes a ledger path, so their behaviour does not
change; the existing suite must pass untouched.

## 2. Asking how it went

Two read-only tools, and a CLI verb `submissions runs [NAME]` printing
the same thing.

`list_runs(user=None, mine=False)`: one line per receipt: name, state,
njobs, jobid, submitted time. Identity follows `campaign_status`: `user`
is validated as a login and selects
`/exp/mu2e/data/users/<user>/prodtools/runs/`; bare `mine` is refused on
a shared HTTP server.

`run_status(name, user=None, mine=False)`:

- reads the receipt;
- queue block from the existing `condor.query_owner_jobs` snapshot,
  restricted to the receipt's cluster: `running`, `idle`, `held` with
  hold reasons, or `state: "unknown"` with no counts when the query
  failed;
- once the cluster has left the queue, per-index outcomes from condor
  history. As built this calls `jobwait.collect_exit_codes` and
  `jobwait.summary` directly (the `condor_history` CLI), not the
  htcondor2 bindings first planned, so the exit-code rule has one home:
  `ok`, `failed` with the exit code, or `unknown` for an index history
  no longer has. Counts plus the failed and unknown indices, capped.
- for every `ok` index the output paths,
  `<outstage>/<cluster>/<proc>/<file name from the cnf>`, as
  `jobwait.summary` builds them.

Overall state: `submitting`, `failed` (the submit itself), `running`,
`done` (all ok), `short` (some failed), `unknown` (history gone or the
query failed). `unknown` is never read as `short`, and never as `done`.

Nothing is persisted: the read-only server performs no writes, here as
everywhere. Condor history fades after about two weeks, after which a
run reads `unknown`. Someone who wants a durable record runs
`jobwait --json`, which exists and is unchanged. The files on scratch
fade on a similar timescale, so this is a limit of the use case more
than of the tool.

## 3. MCP write tool

`submit_once(json, desc, dsconf, run_as, prodtools_dir=None)` runs the
CLI of section 1. `run_as="mu2epro"` is refused outright, before
`confirm` is even looked at. Returns the receipt.

## 4. Docs

- `mcp/README.md`: a third walkthrough, "Try something without touching
  SAM": copy `data/examples/ceendpoint.json`, change `outloc` to
  `{"*.art": "outstage"}`, `submit_once`, `run_status`. It states what is
  given up (no recovery, nothing findable through SAM, scratch lifetime),
  so nobody mistakes it for a lighter way to make a dataset.
- `EXAMPLES.md` (through its schema): "submit it by hand" becomes
  `--once`; the jobwait section stops citing the retired `submit` CLI.
- `_refuse_outstage_campaign`'s message points at `--once`.

## Testing

- Unit: the all-outstage refusal and its message; mu2epro and
  `owner: mu2e` refused; njobs cap; existing run directory refused;
  receipt written as `submitting` before the submit call and rewritten
  after, for success and for failure; **the two-way invariant in
  `submit_entry`** (no ledger + declared output raises; ledger +
  outstage raises; both before any submit call); `run_status` for each
  overall state including a failed condor query and an emptied history;
  identity rules of the two read tools under stdio and shared mode.
- The existing suite unchanged: every current caller passes a ledger.
- Live, self only: the CeEndpoint example with `outloc` outstage, 3 jobs.
  Pass means `run_status` reads `done` 3/3, the three files are where it
  says, `locate_file` finds neither the cnf nor any output in SAM, and
  the personal ledger has no new row. Then one deliberate failure (an
  fcl override that throws) to see `short` with the index named.
- To check during implementation rather than assume: that the worker's
  ifdh copy creates `<outstage>/<cluster>/<proc>/` when the workflow
  project directory does not exist yet.

## Out of scope

Recovery or resubmission of an outstage run; declaring outstage files to
SAM later (`push_file` already publishes a single built file, with
parents); slicing or throttling; production use; NERSC.
