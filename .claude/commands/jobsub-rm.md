---
description: Remove mu2epro grid jobs (whole clusters or single procs) with schedd resolution and mandatory re-query
argument-hint: <cluster|cluster.proc> [more...] [--dry-run]
---

# Remove mu2epro grid jobs

Removes HTCondor jobs owned by `mu2epro` via `ksu`, resolving each
cluster's schedd first and **verifying with `jobsub_q` afterwards**.

Use this for: clearing OOM/wall-clock-held clusters that will never
self-clear, killing a mis-submitted cluster, dropping a stuck recovery.

## Why a dedicated skill (the gotchas it encodes)

1. **`--jobid <cluster>` alone is not enough — it needs the schedd**,
   and the schedd differs per cluster. `jobsub_rm --jobid 71272458`
   without `@jobsubNN.fnal.gov` fails, and guessing the wrong schedd
   yields `Job NNN not found` — which reads like "already gone" but
   means "I asked the wrong machine". The pool has ~5 jobsub schedds
   and consecutive clusters routinely land on different ones.

2. **`<cluster>.0@schedd` removes ONE PROC, not the cluster.** The
   response `Job 29474667.0 marked for removal` (singular "Job") looks
   like success while 999 jobs keep running. The whole-cluster form is
   `<cluster>@schedd` with NO proc, and its response is plural:
   `All jobs in cluster 29474667 have been marked for removal`.
   **Read which of those two sentences came back** — that is the
   fastest tell that you removed a proc when you meant a cluster.
   (Incident 2026-08-05: the four held RMC clusters.)

3. **Never trust `jobsub_rm`'s exit code or its output.** It reports
   success for jobs it never touched. The re-query in step 5 is
   mandatory, not optional.

4. **Removal is asynchronous.** Jobs enter state `X` (removing) and
   linger seconds-to-minutes. `X` is success in progress, not failure —
   do not re-issue the removal, just re-query.

## Usage

```
/jobsub-rm 29474667 29474668            # whole clusters
/jobsub-rm 29474667.965                 # one proc
/jobsub-rm 29474667 --dry-run           # resolve + report, remove nothing
```

Bare integer = whole cluster. `N.M` = that proc only. The schedd is
always resolved for you — never pass `@schedd` yourself.

## Instructions

You are given a list of cluster (or cluster.proc) ids and optionally
`--dry-run`.

1. **Resolve each cluster to its schedd and count its jobs.** Read the
   DEFAULT `jobsub_q` table — `-af` is unreliable here
   (`reference_jobsub_q_af_unreliable`). Status checks need no `ksu`:

   ```bash
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \
     && muse setup ops > /dev/null 2>&1 \
     && jobsub_q --group mu2e --user mu2epro 2>/dev/null \
     | awk -v want="<space-separated clusters>" '
         BEGIN {split(want,w," "); for (i in w) keep[w[i]]=1}
         $1 ~ /@/ {split($1,a,"."); split($1,b,"@")
                   if (a[1] in keep) {n[a[1]" "b[2]]++; st[a[1]" "$6]++}}
         END {for (k in n) print k, n[k]
              for (k in st) print "  state", k, st[k]}' | sort
   ```

   A cluster absent from this listing is **already gone** — report it
   and drop it from the removal list. Do not "remove" it anyway.

2. **Show the user what will be removed** before touching anything:
   cluster, schedd, job count, and the state breakdown (how many
   `H`/`I`/`R`). Removing running jobs discards work in progress;
   removing held jobs discards nothing. Say which case this is.

   If `--dry-run`, stop here.

3. **Confirm.** Print `WARNING: this permanently removes <N> grid jobs
   as mu2epro; it cannot be undone.` and get an explicit "yes". Skip
   the confirmation only when the user's request already named these
   exact clusters AND said to remove them.

4. **Remove**, one `--jobid` per cluster, whole-cluster form:

   ```bash
   timeout 590 ksu mu2epro -e /bin/bash -c '
   unset MUSE_WORK_DIR
   export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
   WORKDIR=$(mktemp -d /tmp/mu2epro_rm.XXXXXX); export XDG_RUNTIME_DIR="$WORKDIR"
   cd "$WORKDIR"
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1
   muse setup ops > /dev/null 2>&1
   for J in <cluster1@schedd1> <cluster2@schedd2>; do
     echo "=== rm $J ==="
     jobsub_rm --group mu2e --jobid "$J" 2>&1 | tail -2
   done
   '
   ```

   The `USER`/`LOGNAME`/`HOME`/`XDG_RUNTIME_DIR` exports are the same
   fix `/mu2epro-submit` needs: `ksu` does not reset them, and the
   caller's `/run/user/<uid>` is not writable by mu2epro
   (`reference_ksu_jobsub_env`).

   Check each response line: **"All jobs in cluster N"** = correct.
   **"Job N.0"** = you removed one proc; re-issue without the `.0`.

5. **VERIFY (mandatory).** Re-query and report the true state:

   ```bash
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \
     && muse setup ops > /dev/null 2>&1 \
     && jobsub_q --group mu2e --user mu2epro 2>/dev/null \
     | awk '$1 ~ /@/ {split($1,a,"."); if (a[1]==<C1>||a[1]==<C2>) print}'
   ```

   - Nothing returned → removed.
   - Rows in state `X` → removal in flight; that is success. Re-query
     once more rather than re-issuing.
   - Rows in `H`/`I`/`R` → the removal did **not** take. Report it as
     FAILED and diagnose (wrong schedd? proc-form?) before retrying.

6. **Report:** per cluster, jobs removed and confirmed final state.

## Ledger interaction (direct-submission campaigns)

Removing a cluster does **not** update `submissions.db`. The row stays
`active` until the next `submissions run` sees the cluster gone,
SAM-verifies its outputs, and issues a recovery child for whatever is
missing.

That is usually what you want — but know the resource consequence:
`recovery_resource_argv` (`utils/submissions.py`) applies
`RECOVERY_MEMORY = '4000MB'` / `RECOVERY_LIFETIME = '48h'` as a FLOOR
to any row whose **own snapshot entry** names no memory. It reads the
row's entry, never the campaign's, so `submissions set-memory` does not
reach rows already dispatched. A 100%-failed row therefore recovers
its entire index list at the floor, not at the campaign's value.

If the campaign's own config was the cause of the failure (wrong
memory, wrong lifetime), fix it with `submissions set-memory` too, or
the next slice repeats it.

## Notes

- `jobsub_rm` needs mu2epro. **Never** fetch or refresh the mu2epro
  token — if it is missing, stop and report
  (`feedback_never_get_mu2epro_token`).
- Before removing, it is worth capturing *why* the jobs were stuck:
  `jobsub_q --group mu2e --user mu2epro --hold` gives `CODE/SUB` plus
  the reason text. Aggregate by code, never by the reason string (it
  embeds hostnames). Once removed, that evidence is gone.
- Held jobs often self-clear — check the hold AGE and cause first
  (`reference_held_jobs_block_recovery`). Resource-starvation holds
  drain on their own; OOM (`34/102`) and
  `MemoryUsage > RequestMemory` (`26/0`) never do.
