---
description: Hand re-fire specific work (`submissions resubmit`) or run a manual campaign tick (`submissions run`) as mu2epro (ksu env fix + dry-run + jobsub_q verify)
argument-hint: resubmit <row-id> (--indices SPEC | --indices-file F | --files F) [--dry-run]  |  run [--campaign ID] [--row ID] [--dry-run] [extra submissions flags]
allowed-tools: Bash
---

# Grid-submitting `submissions` verbs as mu2epro

Runs `submissions resubmit` or `submissions run` as the `mu2epro`
production account via `ksu`, with the environment fixes that
`jobsub_submit` needs but that plain `ksu` does not provide. Always
dry-runs first, pauses for confirmation (grid production is not easily
reversible), then submits and verifies the cluster with `jobsub_q`.

Both verbs act on the production submission ledger
(`/exp/mu2e/data/users/mu2epro/prodtools/submissions.db` — this is
mu2epro's own ledger, which the CLI's "your own ledger" default
resolves to when run as mu2epro, so no `--db` flag is needed here):

- `resubmit ROW_ID` — re-fire a named set of indices (`--indices`/
  `--indices-file`) or input files (`--files`, draining rows only) from
  an *existing* ledger row, as a child submission (attempt+1). The
  entry comes from the row itself, so there is nothing to hand-edit.
  Use this for manual recovery of specific work — e.g. re-dispatching
  files parked by an exhausted draining row, or re-firing indices a
  human has confirmed are genuinely missing outside the normal
  `submissions run` cadence.
- `run [--campaign ID]` — one tick of the recovery pass + campaign
  top-up (the same thing the hourly cron does). `--campaign ID`
  restricts top-up to one campaign (the recovery pass still runs over
  every active row); omit it to tick everything, matching cron
  behaviour. Use this to force a tick out-of-band, e.g. right after
  fixing an `inloc` that was pausing a campaign.

For a **new** production campaign (including a firstjob-window
statistics expansion — set `firstjob`/`njobs` in the JSON config),
use `/mu2epro-run json2jobdef --prod --enqueue --slice-size N` instead
of this skill: it builds the cnf, pushes it to SAM, and registers the
campaign in the ledger in one command. There is no map file anywhere
in this workflow, so there is nothing to hand off to a separate submit
step for that case. Reach for `/mu2epro-submit` only once a campaign
already exists and you need to touch its ledger rows by hand.

## Why a dedicated skill (the gotchas it encodes)

`ksu mu2epro` does NOT reset `USER`/`LOGNAME`/`HOME`, and it inherits the
caller's `XDG_RUNTIME_DIR`. `jobsub_submit` breaks on both:

- `getpass.getuser()` returns the CALLER (e.g. `oksuzian`), so the
  direct backend tries to write `/tmp/prodtools-<caller>.tar` (not
  writable by mu2epro) and picks the wrong submitter/role. Symptom:
  `PermissionError: /tmp/prodtools-oksuzian.tar` even on `--dry-run`.
- `condor_vault_storer` mktemp's under `XDG_RUNTIME_DIR`; the caller's
  `/run/user/<uid>` is not writable by mu2epro → the vault step fails, and
  `jobsub_submit` can **exit 0 while `condor_submit` failed, leaving NO
  cluster**.

The fix (baked into every ksu block below):
```
unset MUSE_WORK_DIR
export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
WORKDIR=$(mktemp -d /tmp/mu2epro_submit.XXXXXX); export XDG_RUNTIME_DIR="$WORKDIR"
```
See `reference_ksu_jobsub_env` for the incident history.

## Usage

```
/mu2epro-submit resubmit <row-id> (--indices SPEC | --indices-file F | --files F) [--dry-run]
/mu2epro-submit run [--campaign ID] [--row ID] [--dry-run] [--max-attempts N] [--max-queued N]
```

- `<row-id>` — a ledger row id from `submissions status` (or the MCP
  `campaign_status`/`mine=true` view). `--files` only works against a
  draining (file-keyed) row; `--indices`/`--indices-file` only against
  an index row — the CLI refuses the mismatch by name.
- `resubmit` REFUSES when any named index/file is still covered by an
  unsettled row for the same tarball (duplicate-physics guard for
  deterministic payloads). It names the blocking row; check `jobsub_q`
  and clear it with `submissions reconcile <blocking-row-id>` first if
  the window is genuinely free (that verb is ledger-only — no
  `jobsub_submit` call — so it does not need this skill; run it via
  `/mu2epro-run submissions reconcile <row-id>`).

## Examples

```
# Recovery: resubmit specific missing indices from a stuck row
/mu2epro-submit resubmit 4231 --indices 4000,4001,4055-4062

# Re-dispatch parked draining files from an exhausted row
/mu2epro-submit resubmit 4198 --files /tmp/parked_ceendpoint.txt

# Force a tick for one paused-then-resumed campaign
/mu2epro-submit run --campaign 17

# Dry-run a full tick (recovery pass + top-up over every active campaign)
/mu2epro-submit run --dry-run
```

## Instructions

You are given `$ARGUMENTS` (the verb and its args, e.g.
`resubmit 4231 --indices 4000-4010` or `run --campaign 17`). Follow
these steps:

1. Resolve the repo root (cwd at invocation) → `REPO`.

2. **HARD RULE — token:** never run, suggest, or mention any mu2epro
   token-refresh. If the submit fails for lack of a token, STOP and report
   the absence; do not remediate.

3. **DRY-RUN first** (no submission): run the ksu block below with
   `--dry-run` appended. Show the user the would-* output — for
   `resubmit`, the reconstructed jobset size and any refusal; for
   `run`, what each active row/campaign would do. If the dry-run errors
   (e.g. `no ledger row N`, a blocking-row refusal), STOP and report —
   do NOT submit.

4. **WARN + confirm:** print `WARNING: this submits grid jobs to the
   production pool as mu2epro; the cluster is not easily reversible.` and ask
   for an explicit "yes". Do not proceed until confirmed. (The `.claude/`
   mu2epro guard hook also prompts on `ksu mu2epro`, but confirm here
   regardless — the hook may not be armed this session.)

5. **REAL submit:** the same ksu block WITHOUT `--dry-run`.

6. **VERIFY (mandatory — a vault failure can exit 0 with NO cluster):** parse
   the cluster ID from the output (`Submitted cluster: NNN`). Then run a
   second ksu block (same env exports) that does
   `source setupmu2e-art.sh && jobsub_q --group mu2e | grep -c "^<cluster>\."`
   and confirm the count equals the submitted job count. If the submit
   printed no cluster ID, or jobsub_q shows 0, treat it as **FAILED** —
   report and do NOT resubmit until you have confirmed via jobsub_q that no
   cluster exists (avoid double-submission).

7. **Report:** cluster ID, job count, and — for `resubmit` — the new
   child row id (`submissions status` shows it parented on the row you
   resubmitted).

### ksu block (dry-run and real submit share this shape)

```bash
timeout 590 ksu mu2epro -e /bin/bash -c '
unset MUSE_WORK_DIR
export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
WORKDIR=$(mktemp -d /tmp/mu2epro_submit.XXXXXX)
export XDG_RUNTIME_DIR="$WORKDIR"
cd "$WORKDIR"
bash <REPO>/bin/submissions <VERB> <ARGS> 2>&1
RC=${PIPESTATUS[0]}
echo "=== submit RC=$RC ==="
exit $RC
'
```
(`bin/submissions` sources the Mu2e ops environment itself, so nothing
extra is needed before it in this block.) A big `run` tick (many
campaigns) may exceed the 590s timeout; raise it if needed, and if it
times out go to step 6 (jobsub_q) BEFORE any retry — **never wrap
`submissions run` in `timeout` and let it silently kill the process
mid-tick; if you raise the timeout, raise it generously instead of
retrying blind.**

## Notes

- The direct backend ships this repo's `utils/`+`bin/` as
  `/tmp/prodtools-mu2epro.tar` and runs `runjob.sh` on the worker, so the
  worker executes THIS checkout's `runmu2e.py` (firstjob-aware). Submit from a
  repo whose code you trust.
- `resubmit`'s reconstructed entry drops any `firstjob` window — the
  `--indices`/`--files` you pass are absolute (cnf index or input
  filename), not relative to the original entry's window.
- Outputs push per-job from the worker (pushOutput) to each entry's
  `outputs[].location`; the direct backend does not use SAM index
  definitions separately.
- Do NOT chain this with commands that write into the user's repo — mu2epro
  cannot write there; read repo files by absolute path.
