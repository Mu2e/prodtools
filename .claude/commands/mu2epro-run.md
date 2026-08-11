---
description: Run a prodtools command as the mu2epro account (via ksu) in a /tmp workdir
argument-hint: [musing[/version]] <command> [args...]
allowed-tools: Bash
---

# Run a prodtools command as mu2epro

Switches to the `mu2epro` account via `ksu`, sources the Mu2e
environment, configures a Musing release (SimJob, AnalysisMDC2025,
etc.), and runs the command in a fresh `/tmp` workdir (because
`mu2epro` typically cannot write into the user's repo). Use this when
the command needs to run as the production account — e.g. `--pushout`
or `--prod` for SAM registration.

## Usage

```
/mu2epro-run [musing[/version]] <command> [args...]
```

- `musing[/version]` — optional Musing release.
  - Bare tag like `Run1Bag`, `MDC2025af`, `MDC2025an` → treated as `SimJob/<tag>`.
  - `<Musing>/<Version>` form like `AnalysisMDC2025/v02_00_00` → sources that musing's `setup.sh` directly.
  - Omitted → defaults to `SimJob/Run1Bag`.
- `command` — the prodtools command, e.g. `json2jobdef`, `jobfcl`.

Relative paths in arguments are resolved against the repo root, because
the command runs in `/tmp`. `/cvmfs/...` and other absolute paths pass
through unchanged.

## Examples

```
/mu2epro-run json2jobdef --json data/Run1B/stage1.json --index 0 --verbose
/mu2epro-run MDC2025af json2jobdef --json data/mdc2025/mix.json --dsconf MDC2025af_best_v1_1 --prod --enqueue --slice-size 1000
/mu2epro-run AnalysisMDC2025/v02_00_00 json2jobdef --json data/mdc2025/evntuple.json --desc evnt --dsconf MDC2025-003 --prod --enqueue
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps:

1. **Parse the version tag.** Examine the first whitespace-separated token:
   - If it contains `/` and matches `<Musing>/<Version>` (e.g. `AnalysisMDC2025/v02_00_00`), set `MUSING=<Musing>`, `MUSING_VERSION=<Version>`, drop it from the command.
   - Else if it looks like a bare SimJob tag (starts with uppercase letter, no `--`, no `.`, no slash — e.g. `Run1Bag`, `MDC2025af`, `MDC2025an`), set `MUSING=SimJob`, `MUSING_VERSION=<tag>`, drop it from the command.
   - Otherwise default `MUSING=SimJob`, `MUSING_VERSION=Run1Bag` and treat the full `$ARGUMENTS` as the command.

2. **Resolve the repo root** (the cwd at command invocation time) and
   store it in a shell variable — call it `REPO`. In every remaining
   argument, if it is a relative path (starts with `data/`, `fcl/`,
   `bin/`, `utils/`, or `test/`, or is a plain relative filename that
   exists under `REPO`), rewrite it as `$REPO/<path>`. Leave absolute
   paths (`/...`) and non-path arguments (`--flag`, values like
   `MDC2025af_best_v1_1`) untouched. The rewritten argv is
   `CMD_ARGS`.

3. **Check for production flags.** If `CMD_ARGS` contains `--pushout`
   or `--prod`, **warn the user** before running: these register
   artifacts in production SAM and are not reversible. Print:
   `WARNING: this invocation will register outputs in production SAM.`
   Then ask the user to confirm (reply "yes" to proceed). Do not run
   until they confirm. If they decline, stop.

   **HARD RULE for `json2jobdef --prod`:** `--prod` REQUIRES `--enqueue`
   (and `--enqueue` requires `--prod`), and `argparse` now enforces it.
   There is no `--jobdefs` flag — `json2jobdef` writes no file
   recording the campaign; `--enqueue` pushes the cnf to SAM and
   registers the sliced-submission campaign directly in the submission
   ledger. A bare `--prod` with no `--enqueue` would push the cnf and
   register nothing, so it is refused up front.

   **Known limitation, bulk `--dsconf X --prod --enqueue` (no `--desc`,
   line 36 above):** this processes every matching entry in one loop,
   and a failure partway through (e.g. entry 7 of 22) leaves campaigns
   registered for the entries before it and nothing for the rest — the
   bulk run as a whole is not resumable. Re-running the identical
   command then exits immediately on the FIRST entry's "active campaign
   already exists" refusal — that message is the double-submit guard
   doing its job, not ledger damage. Recovery is per-entry: re-run just
   the failed and remaining entries with `--desc <D> --dsconf <C>
   --prod --enqueue`.

4. **Run** the following as a single Bash command. Everything runs
   inside one `ksu` invocation so the sourced environment is live when
   the prodtools command executes:

   The `unset MUSE_WORK_DIR` line is **required**: `ksu` inherits the
   caller's environment, so if this session already has a `muse setup
   SimJob <other>` active (common), `muse setup SimJob <MUSING_VERSION>`
   aborts with `ERROR - Muse already setup for directory .../<other>`
   and the `&&` chain dies before the command runs. The guard in
   `museSetup.sh` keys on exactly one variable — a non-empty
   `MUSE_WORK_DIR` — so unsetting just that clears it. Unset **only**
   `MUSE_WORK_DIR`: do NOT `unset MUSE_*` (that wipes `MUSE_DIR`, and
   `muse` is a function `source ${MUSE_DIR}/bin/muse`, so it degrades to
   `source /bin/muse: No such file or directory`), and do NOT reset
   `PATH` (`muse` lives on the inherited PATH). Auth env
   (`KRB5CCNAME`, `XDG_RUNTIME_DIR`, `BEARER_TOKEN_FILE`, `HOME`) is
   untouched.

   For `MUSING=SimJob`:
   ```bash
   timeout 600 ksu mu2epro -e /bin/bash -c '
   WORKDIR=$(mktemp -d /tmp/mu2epro_run.XXXXXX)
   cd "$WORKDIR"
   echo "=== mu2epro workdir: $WORKDIR ==="
   unset MUSE_WORK_DIR
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh \
     && muse setup ops \
     && muse setup SimJob <MUSING_VERSION> \
     && setup OfflineOps \
     && bash <REPO>/bin/<command> <CMD_ARGS>
   RC=$?
   echo "=== artifacts ==="
   ls -la "$WORKDIR"
   exit $RC
   '
   ```

   For any other Musing (sourced directly):
   ```bash
   timeout 600 ksu mu2epro -e /bin/bash -c '
   WORKDIR=$(mktemp -d /tmp/mu2epro_run.XXXXXX)
   cd "$WORKDIR"
   echo "=== mu2epro workdir: $WORKDIR ==="
   unset MUSE_WORK_DIR
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh \
     && muse setup ops \
     && source /cvmfs/mu2e.opensciencegrid.org/Musings/<MUSING>/<MUSING_VERSION>/setup.sh \
     && setup OfflineOps \
     && bash <REPO>/bin/<command> <CMD_ARGS>
   RC=$?
   echo "=== artifacts ==="
   ls -la "$WORKDIR"
   exit $RC
   '
   ```

   `setup OfflineOps` puts `pushOutput` on `$PATH`. Required for
   `--pushout` / `--prod` flags; harmless otherwise.

5. **Report to the user:** the workdir path, the files produced, and
   the command's exit status. Note that the workdir persists in
   `/tmp/mu2epro_run.*` for inspection — offer to clean it up only if
   asked.

## Notes

- **For hand re-firing specific work (`submissions resubmit`) or a
  manual campaign tick (`submissions run`), use `/mu2epro-submit`
  instead** — this skill does NOT set
  `USER`/`LOGNAME`/`HOME`/`XDG_RUNTIME_DIR`, which the direct backend
  requires (else `condor_vault_storer` fails / wrong submitter). `/mu2epro-submit`
  bakes in that env fix plus dry-run + jobsub_q verification. New
  campaigns (including firstjob-window statistics expansions) go
  through `json2jobdef --prod --enqueue` here instead — there is no
  separate submit step.
- `ksu` requires that `oksuzian@FNAL.GOV` is listed in
  `~mu2epro/.k5users` for `/bin/bash`. If auth fails, report the error
  verbatim — do not retry automatically.
- Outputs written under `/tmp` are owned by `mu2epro` and not writable
  by the user's account. To read them afterwards, shell back through
  `ksu mu2epro -e ...`.
- Do **not** chain `ksu mu2epro` with commands that write into the
  user's repo (`prodtools/`) — mu2epro lacks write permission. If the
  command needs to consume repo files, read them by absolute path
  (step 2 above handles this).
