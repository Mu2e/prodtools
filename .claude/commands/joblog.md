---
description: Fetch a grid job's log via jobsub_fetchlog and triage the failure (works after the cluster drains)
argument-hint: <cluster>[.<proc>] [--proc N] [--raw] [--row N]
---

# Fetch and triage a grid job log

Retrieves a job's stdout/stderr with `jobsub_fetchlog` and greps it for
the failure signatures we actually hit. **This is the only evidence path
for a job that failed after `mu2e` succeeded** — a job whose data push
fails leaves no log in SAM at all, so SAM tells you nothing.

Works after the cluster has drained, which is the normal case by the
time anyone looks.

**Hard limit — know this before you promise a log.** The sandbox exists
only for a job that **completed and transferred output back** (exit 0 or
nonzero, either is fine). A job killed at the wall-clock limit
(`SYSTEM_PERIODIC_HOLD Run Time/limit`) never transfers, so it has NO
retrievable log — not while running, not while held, not after
`condor_rm`. Verified 2026-07-28 against a control; three such logs were
lost before the pattern was understood.

To diagnose a job that is merely slow you must therefore catch it while
it still exists. `condor_ssh_to_job` is on PATH (`jobsub_ssh` is not) and
takes `-name <schedd> <cluster>.<proc> [command]` — but **this has NOT
been verified to work for jobsub_lite grid jobs**, which run on remote
OSG glideins and belong to mu2epro. Treat it as a lead, not a procedure,
until someone confirms it end to end. Prefer the measured route first:
`logparser <log-dataset> -n N` gives per-desc `Real`/`CPU` hours and
`VmHWM`/`VmPeak` from logs already in SAM, with no live attach at all,
and answers "is this desc slow or memory-hungry" directly.

## Usage

```
/joblog 85634648                 # proc 0 of that cluster
/joblog 85634648.1               # a specific proc
/joblog 28992641 --proc 1
/joblog 85634648 --raw           # also print the last 60 lines
/joblog --row 58                 # resolve the cluster from a ledger row id
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps.

### 1. Resolve the cluster and proc

- `<cluster>.<proc>` → split on the dot.
- `<cluster>` alone → proc `0` unless `--proc N` is given.
- `--row N` → read the cluster from the submission ledger, no ksu needed:
  ```bash
  python3 -c "
  import sqlite3
  con=sqlite3.connect('file:/exp/mu2e/data/users/mu2epro/prodtools/submissions.db?mode=ro',uri=True)
  r=con.execute('SELECT cluster_id,tarball,indices_json,attempt,state FROM submissions WHERE id=?',(N,)).fetchone()
  print(r)"
  ```

### 2. Fetch — as the CURRENT user, never ksu

`jobsub_fetchlog` retrieves **mu2epro's** job logs perfectly well as an
ordinary mu2e-group member. This is a read; it does not need `ksu`
(see `feedback_status_checks_no_mu2epro`). Do not wrap it.

The schedd is part of the job id and is NOT discoverable once the job
has left the queue, so **iterate over the schedds** — guessing one wrong
is the usual reason this "doesn't work":

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1
muse setup ops > /dev/null 2>&1
DEST=/exp/mu2e/data/users/oksuzian/claude-scratch/joblogs/<cluster>.<proc>
mkdir -p "$DEST"
for SD in jobsub01 jobsub02 jobsub03 jobsub04 jobsub05; do
  if timeout 45 jobsub_fetchlog --group mu2e \
        --jobid <cluster>.<proc>@${SD}.fnal.gov --destdir "$DEST" >/dev/null 2>&1; then
    echo "fetched from $SD"; break
  fi
done
ls -la "$DEST"
```

Write to `claude-scratch/joblogs/`, never `/tmp`
(see `feedback_scratch_dir_not_tmp`). The `.out` file is the useful one
(400+ KB typically); `.err` is usually setup noise.

If no schedd yields a log, say so plainly — the sandbox may have aged
out. Do not fall back to guessing or to `ksu`.

### 3. Triage — count signatures, don't dump the log

The `.out` is hundreds of KB; never cat it. Grep for the known failure
modes and report counts:

```bash
F=$(ls "$DEST"/*.out | head -1)
grep -oE "Art has completed and will exit with status [0-9]+|\
Mu2e execution failed|output file exists|running recover|rm failed|\
HTTP 403|Permission refused|status at exit: [0-9]+|\
Traceback|CalledProcessError|No such file or directory|\
Auth failed|Killed|Out of memory" "$F" | sort | uniq -c
tail -6 "$F"
```

### 4. Interpret

Map the signature to a diagnosis and say which it is:

- **`Art has completed ... status 0` + `output file exists` + `running
  recover` + `rm failed` + `HTTP 403`** → tape-orphan poison pill. The
  physics succeeded; the push could not replace a pre-existing
  undeclared file. Caused by a wrong/narrow token scope — fixed
  2026-07-28, so seeing this on a job submitted after that date is a
  regression worth reporting. See `reference_tape_orphan_poison_pill`.
- **`Art has completed ... status 0`, no push errors** → the job worked;
  look at the ledger/SAM instead, not the log.
- **`Mu2e execution failed`** → real physics/config failure; read the
  art traceback above the failure line.
- **`Auth failed: No protocols left to try`** → expired bearer token on
  the worker; input reads over xrootd failed.
- **`Killed` / `Out of memory`** → the container memory cap. Check
  events/job against the merge factor before raising memory
  (`reference_merge_factor_sizing`).
- **nothing matched** → print `tail -40` and read it.

### 5. Report

State: the cluster/proc, which schedd served it, the diagnosis, the
evidence lines that support it, and the log path so the user can look
themselves. If the diagnosis implies an action (raise `--max-attempts`,
clear an orphan, fix a scope), name it — but do not perform it.

## Notes

- **Do not use `ksu`.** Both the ledger read and the fetch work as the
  ordinary user. `ksu` is for writes.
- The proc number is the index *within the cluster*, not the cnf index.
  A 2-job recovery cluster has procs 0 and 1 regardless of which cnf
  indices they carry; the ledger row's `indices_json` maps them.
- `jobsub_q` cannot tell you the schedd once the job is gone, which is
  why step 2 iterates. Note the successful schedd in your report.
- Logs are retrievable for a while after the cluster drains but not
  forever — fetch early when investigating.
