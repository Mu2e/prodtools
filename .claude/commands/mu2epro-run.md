---
description: Run a prodtools command as the mu2epro account (via ksu) in a /tmp workdir
argument-hint: [simjob-version] <command> [args...]
allowed-tools: Bash
---

# Run a prodtools command as mu2epro

Switches to the `mu2epro` account via `ksu`, sources the Mu2e
environment, and runs the command in a fresh `/tmp` workdir (because
`mu2epro` typically cannot write into the user's repo). Use this when
the command needs to run as the production account — e.g. `--pushout`
or `--prod` for SAM registration.

## Usage

```
/mu2epro-run [simjob-version] <command> [args...]
```

- `simjob-version` — optional SimJob release tag (e.g. `Run1Bag`,
  `MDC2025af`). Default: `Run1Bag`.
- `command` — the prodtools command, e.g. `json2jobdef`, `mkidxdef`,
  `mkrecovery`.

Relative paths in arguments are resolved against the repo root, because
the command runs in `/tmp`. `/cvmfs/...` and other absolute paths pass
through unchanged.

## Examples

```
/mu2epro-run json2jobdef --json data/Run1B/stage1.json --index 0 --verbose
/mu2epro-run MDC2025af json2jobdef --json data/mdc2025/mix.json --dsconf MDC2025af_best_v1_1 --prod
/mu2epro-run mkidxdef --jobdefs jobdefs_list.json --prod
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps:

1. **Parse the version tag.** If the first whitespace-separated token
   matches a SimJob version pattern (starts with an uppercase letter,
   no `--` prefix, no slash, no dot), treat it as `SIMJOB_VERSION` and
   drop it from the command. Otherwise default `SIMJOB_VERSION=Run1Bag`.
   Typical version tokens: `Run1Bag`, `Run1Bai`, `MDC2025ac`, `MDC2020ba`.

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

4. **Run** the following as a single Bash command. Everything runs
   inside one `ksu` invocation so the sourced environment is live when
   the prodtools command executes:

   ```bash
   timeout 600 ksu mu2epro -e /bin/bash -c '
   WORKDIR=$(mktemp -d /tmp/mu2epro_run.XXXXXX)
   cd "$WORKDIR"
   echo "=== mu2epro workdir: $WORKDIR ==="
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh \
     && muse setup ops \
     && muse setup SimJob <SIMJOB_VERSION> \
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
