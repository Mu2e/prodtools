---
description: Submit art jobs via upstream mu2ejobsub (mu2egrid v8) with sensible 2026 defaults — useful for smoke tests, recoveries, and ad-hoc JIT-cnf submissions outside the direct-backend campaign flow
argument-hint: <cnf-tarball> [--all | --firstjob N --njobs M | --jobs i,j,...] [--inloc tape|disk|...] [--proto root|ifdh] [extra mu2ejobsub flags]
allowed-tools: Bash
---

# Submit art jobs via `mu2ejobsub` (one-off / smoke / recovery)

`mu2ejobsub` (`/cvmfs/.../mu2egrid/v8_03_02/bin/mu2ejobsub`) is the
upstream Perl wrapper around `jobsub_submit` for `mu2e -c` jobs. It
consumes a cnf tarball (`cnf.mu2e.*.tar`) built by `mu2ejobdef` /
`json2jobdef` / `jobdef`, and ships `njobs` worker processes that
materialize per-index fcl via `mu2ejobfcl` and run `mu2e -c`.

This skill is for **direct, one-off invocations** — smoke tests, single
recoveries, ad-hoc JIT-fcl submissions — where the full production
campaign machinery is overkill. For production-style runs of many
cnfs at once, use `/mu2epro-run json2jobdef --prod --enqueue
--slice-size N` to register a campaign (`submissions run` then feeds
it, on cron or via `/mu2epro-submit run`).

## ⚠️ Important caveat: no SAM registration

`mu2ejobsub`'s worker shim (`mu2egrid::impl/mu2ejobsub.sh`) writes
outputs to outstage but does **not** run `pushOutput` — the resulting
art files are NOT SAM-registered. They sit in
`$WFTOP/<user>/workflow/<wfproject>/outstage/<cluster>/…/<index>/` and
no `samweb get-metadata` query will find them. This is exactly the gap
the prodtools Phase 2 direct backend (`utils/submit.py` →
`runmu2e.py` direct mode, driven by `json2jobdef --enqueue` +
`submissions run`) was built to close. See ADR
`2026-04-30-phase2-direct-jobsub-implementation.md` for the full
"what mu2ejobsub does vs what we need" comparison.

If you need SAM-registered outputs, use `/stage-entry <stage> …` (via
the prodtools chain) or the direct-backend campaign flow, not this
skill.

## Usage

```
/mu2ejobsub-submit <cnf-tarball> <JOB_SET> [--inloc L] [--proto P] [extra-flags]
```

- `<cnf-tarball>` — path to a `cnf.mu2e.*.tar`. Either an absolute PNFS
  path (`/pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/<desc>/<dsconf>/tar/…`),
  a local file, or a bare basename which the skill will resolve via
  `samweb`/`datasetFileList` (same logic as `fcldump --dataset`).
- **`<JOB_SET>`** — exactly one of (matches `mu2ejobsub` `JOB_SET_SPECIFICATION`):
  - `--all` (every job index defined in the jobdef; valid only for
    finite jobdefs)
  - `--firstjob N --njobs M` (sequential range)
  - `--jobs i1,i2,…` (specific indices, e.g. for recovery)
  - `--jobset file.json` (JSON with a `"jobset": [i,j,…]` key)
- `--inloc L` — `tape` (default), `disk`, `scratch`, or `dir:/abs/path`.
  Forwarded as `--default-location`.
- `--proto P` — `root` (default, recommended for tape inputs) or `ifdh`
  (pre-stage to worker; needed when `inloc=dir:` per the
  `fcldump --default-protocol` memory `reference_jobfcl_proto_root_for_tape_smoke`).
- Any other `mu2ejobsub` flag passes through: `--memory`,
  `--expected-lifetime`, `--site`, `--priority`, `--predefined-args`,
  `--dry-run`, `--verbose`, `--jobsub-arg=...`, etc.

The flag the skill **always appends** unless the user overrides:

- `--predefined-args=al9` — the modern container. Override with
  `--predefined-args=sl7` ONLY if your cnf points at an Offline build
  predating the AL9 migration (rare for new work; check the
  `setup` line inside the cnf's `jobpars.json` if unsure).

## Examples

```
# 3-job smoke from a freshly-built cnf in cwd
/mu2ejobsub-submit cnf.oksuzian.CosmicCRYAll.test01.0.tar --firstjob 0 --njobs 3

# Full set of an Extracted-reco cnf, tape inputs (default --inloc tape --proto root)
/mu2ejobsub-submit cnf.mu2e.CosmicCRYExtractedTriggeredReco.MDC2025af_best_v1_3.0.tar --all

# Recovery: submit just the failed indices
/mu2ejobsub-submit cnf.mu2e.CeEndpointMix1BB.MDC2025af_best_v1_1.0.tar --jobs 17,42,103

# JIT-cnf with local-dir inputs (per the JustInTimeFcl wiki workflow)
/mu2ejobsub-submit cnf.oksuzian.CosmicCRYAllOffSpillTriggered.MDC2020ai_perfect_v1_3.0.tar \
                   --firstjob 0 --njobs 1 \
                   --inloc dir:/exp/mu2e/data/users/oksuzian/test3 --proto ifdh

# Dry-run to inspect the jobsub argv without actually submitting
/mu2ejobsub-submit cnf.mu2e.Reco.MDC2025af_best_v1_3.0.tar --firstjob 0 --njobs 1 --dry-run --verbose
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps:

1. **Parse args.** First positional token is `<cnf-tarball>`. Then scan
   for at least one of `--all`, `--firstjob/--njobs`, `--jobs`,
   `--jobset` (the JOB_SET). Also extract `--inloc <L>` (default `tape`)
   and `--proto <P>` (default `root`). Strip those from the forwarded
   argv. The remainder is `EXTRA`.

2. **Validate the job-set.** Exactly one of `--all`, `--firstjob`+`--njobs`,
   `--jobs`, `--jobset` must be present. If zero, print the Usage and
   exit. If multiple, refuse and exit. Don't try to guess.

3. **Locate the cnf.** Resolve `<cnf-tarball>`:
   - If it's an absolute path that exists: use as-is.
   - If it's a local file in cwd: use the absolute path.
   - Else: try `samweb locate-file <basename>` or
     `datasetFileList --defname <basename>`. If neither finds it, exit
     with a clear error. (The `find_matching_jobdef` machinery in
     `fcldump.py` is the reference implementation — but for this skill
     we only need direct path resolution, not the desc-suffix
     search fallback.)

4. **Verify bearer token.** Same as `/mu2eg4bl-submit`: check
   `/run/user/$(id -u)/bt_u$(id -u)` and `/tmp/bt_u$(id -u)`. If both
   absent, stop and tell the user:

   ```
   No bearer token found. Run this in the prompt:
     ! htgettoken -i mu2e -a htvaultprod.fnal.gov
   Then re-invoke /mu2ejobsub-submit.
   ```

5. **Refuse silent backend mismatches.** If `EXTRA` contains
   `--predefined-args=sl7` AND the cnf's embedded `jobpars.json` `setup`
   field points at a modern AL9 SimJob release (path contains a known
   AL9 build, e.g. anything `MDC2025*` post-`af`), warn the user that
   they're forcing an SL7 container against an AL9 build. Don't block —
   just warn. (Inverse case: AL9 container against an old SL7-built
   Offline. Rare; same warning pattern.)

6. **Submit.** Source the Mu2e environment, set up `mu2egrid`, and run:

   ```bash
   mu2ejobsub \
     --jobdef <CNF_PATH> \
     <JOB_SET> \
     --default-location <INLOC> \
     --default-protocol <PROTO> \
     --predefined-args=al9 \
     <EXTRA>
   ```

   Capture the cluster id from the line
   `<N> job(s) submitted to cluster <CLUSTER>.` On failure, surface the
   stderr — don't paper over it.

7. **Report.** Print:
   - Cluster id (`<CLUSTER>.0@jobsubNN.fnal.gov`)
   - Expected outstage:
     `/pnfs/mu2e/{persistent|scratch}/users/$USER/workflow/<wfproject>/outstage/<CLUSTER>/`
     where `<wfproject>` is derived from the cnf basename (typically
     the dsconf campaign code like `MDC2025af`). If the user passed
     `--wfproject` or `--wftop`, reflect that in the path. For
     non-Production submissions (oksuzian, etc.) the default `wftop`
     is `/pnfs/mu2e/scratch/users/`; for Production it's
     `/pnfs/mu2e/persistent/users/`.
   - Monitor command:
     `condor_q -name <schedd> -constraint 'ClusterId==<CLUSTER>' -af ProcId JobStatus`
     (use `condor_q`, **not** `jobsub_q --jobid`, which can transiently
     return misleading "0 total").
   - **Reminder that outputs will NOT be SAM-registered.** Suggest
     `samweb declare-file` / `pushOutput` post-hoc if the user actually
     needs SAM presence, or point them at the direct-backend campaign
     flow (`json2jobdef --prod --enqueue`) for the right path.

   Don't auto-monitor or auto-fetch logs.

## Notes

- `mu2ejobsub` requests `storage.modify` scope for WFOUTSTAGE internally
  (per ADR `2026-04-30-phase2-direct-jobsub-implementation.md` §CB1).
  Unlike `mu2eg4bl`, you do NOT need a `--jobsub-arg=--need-storage-modify=…`
  workaround. So the failure-mode is different from
  `/mu2eg4bl-submit`: if a job fails here, look at `condor_history`
  ExitCode / HoldReason and the `jobsub_fetchlog` archive, not at
  outstage absence.
- For the JustInTimeFcl wiki workflow's "local undeclared input"
  variant, use `--inloc dir:/exp/.../testdir --proto ifdh` (per the
  wiki). The cnf must have been built with the right `--auxinput=…`
  list referencing basenames present in that dir.
- This skill submits as the **current user**. For production runs as
  `mu2epro`, do not use this skill — use `/mu2epro-run json2jobdef
  --prod --enqueue` to register a campaign, and `/mu2epro-submit` for
  hand re-firing or a manual tick.
- If the cnf was built with the prodtools chain, the
  `services.DbService.{purpose,version}` overrides are already in
  jobpars.json (per memory `reference_reco_dbservice_overrides.md`).
  If the cnf came from a hand-rolled `mu2ejobdef --embed template.fcl`
  per JustInTimeFcl, you have to set those in the template yourself or
  `mu2e -c` will crash at PBTFSD.
- The fastest way to smoke a cnf locally before submitting via this
  skill: `fcldump --local-jobdef <cnf>.tar --index 0 > test.fcl &&
  mu2e -c test.fcl` (per memory
  `reference_jobfcl_proto_root_for_tape_smoke`).
