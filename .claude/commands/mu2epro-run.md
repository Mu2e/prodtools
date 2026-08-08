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
/mu2epro-run MDC2025af json2jobdef --json data/mdc2025/mix.json --dsconf MDC2025af_best_v1_1 --prod
/mu2epro-run AnalysisMDC2025/v02_00_00 json2jobdef --json data/mdc2025/evntuple.json --dsconf MDC2025-003 --prod --jobdefs /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-025.json
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

   **HARD RULE for `json2jobdef --prod`:** `--jobdefs` is mandatory.
   If the user invokes `json2jobdef --prod` without it, **do not run** —
   refuse and explain. The unflagged default produces a SAM-polluting
   `ijobdefs_list` definition (incident 2026-05-19).

   **Where `--jobdefs` points depends on the submission backend. Decide
   this FIRST — the two answers are different files in different
   places, and picking the wrong one is not cosmetic.**

   *If the jobs will be submitted by POMS:* the absolute path to the
   latest plain `MDC2025-NNN.json` under
   `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/`.
   - `ls .../poms_map/MDC2025-*.json` and take the highest plain-`MDC2025-NNN.json` (ignore variants like `MDC2025ad-NNN.json`, `RecoMDC2025*`, `old_*`, `test*`).
   - Sum `njobs` across entries: `jq '[.[].njobs] | add' <map>`.
   - If `current_total + new_entry_njobs ≤ 100000`, extend it; else allocate `MDC2025-(NNN+1).json`.
   - Never invent a per-campaign name here (`Run1Bak-001.json`, `MDC2025ad-NNN.json`) — POMS reads the numbered maps.

   *If the jobs will be submitted directly* (`submit_map --enqueue` +
   `submissions run`): a **throwaway `/tmp` map, one per campaign** —
   e.g. `/tmp/map_noprimary_au.json`. Pass the same path to
   `submit_map --enqueue` afterwards.
   - **Do NOT create a persistent file under `poms_map/`.** The
     directory name is historical; it does not mean every map there is
     a POMS map, and a new file there is a file the direct workflow
     neither reads nor wants.
   - The map is consumed once at enqueue to create the campaign row;
     `campaigns.entry_json` snapshots the entry, so the file is
     disposable afterwards.
   - **Never append a direct campaign to a POMS-active `MDC2025-NNN.json`.**
     POMS would dispatch those entries while `submissions run` feeds
     slices from the same tarball → duplicate jobs and duplicate SAM
     registration.

   **If you do not know which backend, ASK.** Do not infer it from the
   dsconf, the desc, or the log name (see
   `reference_log_name_not_backend_tell`).

   **Check the precedent before inventing anything.** The ledger records
   what every past campaign actually used:
   ```bash
   python3 -c "import sqlite3;c=sqlite3.connect('file:/exp/mu2e/data/users/mu2epro/prodtools/submissions.db?mode=ro',uri=True);[print(r) for r in c.execute('SELECT id,tarball,map_path FROM campaigns ORDER BY id')]"
   ```
   As of 2026-07-25 every direct campaign but the first used a `/tmp`
   map. If a sibling campaign already exists, copy its shape rather than
   minting a new convention.

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

- **For grid submission (`submit_map`), use
  `/mu2epro-submit` instead** — this skill does NOT set
  `USER`/`LOGNAME`/`HOME`/`XDG_RUNTIME_DIR`, which the direct backend
  requires (else `condor_vault_storer` fails / wrong submitter). `/mu2epro-submit`
  bakes in that env fix plus dry-run + jobsub_q verification.
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
