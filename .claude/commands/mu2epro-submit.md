---
description: Submit a prodtools POMS map to the grid via the direct backend as mu2epro (ksu env fix + dry-run + jobsub_q verify)
argument-hint: <map.json> [--entry N] [--first N --num M] [extra submit_map flags]
allowed-tools: Bash
---

# Submit a POMS map as mu2epro (direct backend)

Runs `submit_map` as the `mu2epro` production account via
`ksu`, with the environment fixes the direct backend needs but that plain
`ksu` does not provide. Always dry-runs first, pauses for confirmation (grid
production is not easily reversible), then submits and verifies the cluster
with `jobsub_q`.

Use this for prodtools direct-backend submissions: firstjob statistics
expansions and per-job-pushOutput map submissions. For upstream `mu2ejobsub`
smoke tests use `/mu2ejobsub-submit`; for building/pushing a cnf use
`/mu2epro-run json2jobdef --prod`.

## Why a dedicated skill (the gotchas it encodes)

`ksu mu2epro` does NOT reset `USER`/`LOGNAME`/`HOME`, and it inherits the
caller's `XDG_RUNTIME_DIR`. The direct backend breaks on both:

- `getpass.getuser()` returns the CALLER (e.g. `oksuzian`), so `submit_map`
  tries to write `/tmp/prodtools-<caller>.tar` (not writable by mu2epro) and
  picks the wrong submitter/role. Symptom: `PermissionError:
  /tmp/prodtools-oksuzian.tar` even on `--dry-run`.
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
/mu2epro-submit <map.json> [--entry N] [--first N --num M] [extra submit_map flags]
```

- `<map.json>` — absolute path to the POMS map under
  `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/`.
- `--entry N` — submit only entry index N (default: ALL entries in the map).
  Use this when the map has entries that must NOT be resubmitted.
- `--first N --num M` — submit only the jobset slice `[N, N+M)` (recovery /
  partial). Default: the whole window.
- Any other flags pass through to `submit_map` (`--memory`,
  `--expected-lifetime`, `--disk`, …).

## Examples

```
# Firstjob expansion (map already windowed to firstjob/njobs)
/mu2epro-submit /exp/mu2e/app/users/mu2epro/production_manager/poms_map/Run1Ban-pileupext.json

# One entry of a multi-entry map (do not touch the others)
/mu2epro-submit /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-033.json --entry 1

# Recovery: resubmit indices 4000..4099 only
/mu2epro-submit /exp/mu2e/app/users/mu2epro/production_manager/poms_map/Run1Ban-pileupext.json --first 4000 --num 100
```

## Instructions

You are given `<map.json> [args...]`. Follow these steps:

1. Resolve the repo root (cwd at invocation) → `REPO`. The map path is
   absolute; treat the map path as `MAP` and everything else as `EXTRA`
   (passed through to `submit_map`).

2. **HARD RULE — token:** never run, suggest, or mention any mu2epro
   token-refresh. If the submit fails for lack of a token, STOP and report
   the absence; do not remediate.

3. **DRY-RUN first** (no submission): run the ksu block below with
   `--dry-run` appended to the `submit_map` command. Show the user: `Total
   jobs`, the `Entry N: ... window: cnf indices A..B` line, the jobset size,
   and the `jobsub_submit` argv. If the dry-run errors (e.g. window
   validation, missing tarball), STOP and report — do NOT submit.

4. **WARN + confirm:** print `WARNING: this submits <N> grid jobs to the
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

7. **Report:** cluster ID, job count, the window (cnf indices A..B), and next
   steps (drain → completeness → downstream Cat/stage).

### ksu block (dry-run and real submit share this shape)

```bash
timeout 590 ksu mu2epro -e /bin/bash -c '
unset MUSE_WORK_DIR
export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
WORKDIR=$(mktemp -d /tmp/mu2epro_submit.XXXXXX)
export XDG_RUNTIME_DIR="$WORKDIR"
cd "$WORKDIR"
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \
  && muse setup ops > /dev/null 2>&1 \
  && bash <REPO>/bin/submit_map --map <MAP> <EXTRA> 2>&1
RC=${PIPESTATUS[0]}
echo "=== submit RC=$RC ==="
exit $RC
'
```
(Append `--dry-run` to the `submit_map` line for step 3.) A big submission
(tens of thousands of jobs) may exceed the 590s timeout during RCDS publish;
raise it if needed, and if it times out go to step 6 (jobsub_q) BEFORE any
retry.

## Window prep (pre-step for firstjob expansions)

To advance a firstjob window before submitting, edit the map entry FIRST (as
mu2epro, with a backup). The submitted map must contain ONLY the window you
intend — a completed window left in the map would re-run it (or use
`--entry` to isolate one). For a single-entry expansion map:

```bash
ksu mu2epro -e /bin/bash -c '
MAP=/exp/mu2e/app/users/mu2epro/production_manager/poms_map/Run1Ban-pileupext.json
cp "$MAP" "$MAP.bak-$(date +%Y%m%d_%H%M%S)"
jq "[.[0] | .firstjob=<F> | .njobs=<N>]" "$MAP" > "$MAP.tmp" && mv "$MAP.tmp" "$MAP"
cat "$MAP"
'
```
`baseSeed = 1 + cnf index` and `firstSubRun = cnf index`, so a fresh window
gives fresh seeds/sequencers on the SAME tarball — no rebuild/retire (see
`reference_resampler_expansion_seed_mechanics` and
`reference_firstjob` / the firstjob wiki page).

## Notes

- The direct backend ships this repo's `utils/`+`bin/` as
  `/tmp/prodtools-mu2epro.tar` and runs `runjob.sh` on the worker, so the
  worker executes THIS checkout's `runmu2e.py` (firstjob-aware). Submit from a
  repo whose code you trust.
- Capacity vs window: the cnf's `tbs.njobs` is authoritative for capacity
  (0 = open-ended); the map entry's `njobs` (+ optional `firstjob`) is the
  window. `submit_map` validates the window against cnf capacity.
- Outputs push per-job from the worker (pushOutput) to each entry's
  `outputs[].location`; the direct backend does not use SAM index
  definitions separately.
- Do NOT chain this with commands that write into the user's repo — mu2epro
  cannot write there; read repo files by absolute path.
