# runmu2e runner consolidation — design

**Status:** approved in chat 2026-09-01 (after the g4bl entry mode branch,
`ea62b44..7abdea7`, was reviewed clean). Ships in prodtools **v3.3.2**,
after the v3.3.1 grid smoke has validated the current two-dispatcher
shape — the grid must first prove the reviewed code, so a g4bl grid
failure has one suspect, not two.

## 1. Problem

`utils/runmu2e.py` has two parallel direct-mode dispatchers,
`_direct_dispatch` (mu2e / art) and `_dispatch_g4bl`. They share ~40
lines of identical tail — manifest → `log_storage_location` →
`data_push`/`log_push` closures → dry-run print → `_push_all` — that
differ only in five values (`outputs`, `log_file`, `owner`, `infiles`,
`simjob_setup`, `track_parents`). Two copies must be kept in sync by
hand; the final review of the g4bl branch flagged the duplication.

Tracing the seam exposed a latent production defect in the mu2e path:
`_emit_manifest` runs only `if Path(log_file).exists()`, but nothing
creates `log.<owner>.<desc>.<dsconf>.<seq>.log` on a worker before that
point — `push_logs` creates it *afterwards* by copying
`$JSB_TMP/JOBSUB_LOG_FILE`. Real production logs therefore carry **no**
`mu2egrid manifest` block (verified 2026-09-01 on
`log.mu2e.CeEndpoint.Run1Ban-001.617-1781534797.log`: 0 matches, and
its line 891 shows the copy happening). Only g4bl logs have a manifest,
because `_run_g4bl_job` streams its own log file first. (That evidence
log is itself POMS-era — its tail runs `runmu2e --jobdesc ...` — but
the direct backend inherited the identical `_direct_dispatch` /
`_emit_manifest` code, so the missing-manifest mechanism was the same
under both.)

A second, deeper defect surfaced only after this spec's fix (92dc555)
landed: even a log that DOES exist when `_emit_manifest` appends to it
does not keep what was appended. OfflineOps `pushOutput`'s `writeLog`
(`/cvmfs/.../OfflineOps/v00_04_02/Util/pushOutput.py:801`) `os.remove()`s
every `log`-tier file it pushes and rewrites it from
`$JSB_TMP/JOBSUB_LOG_FILE` plus a `JOBSUB_ERR` banner before declaring
it — for every `disk`/`scratch`/`tape` destination. So the file
`_emit_manifest` appends to is discarded by pushOutput itself; only the
`outstage` path (`_copy_to_outstage`, ifdh, no pushOutput) ever shipped
runmu2e's own file. This spec's "mu2e SAM logs gain the manifest" claim
(section 3.1) was therefore still false after 92dc555 — closed by a
follow-up fix that also prints the manifest to stdout, which IS
`$JSB_TMP/JOBSUB_LOG_FILE` on a worker and so survives `writeLog`.

Also asymmetric: g4bl runs its process through a hand-rolled
`subprocess.Popen` tee loop, while mu2e goes through `prod_utils.run`;
`push_logs` carries a dual `fcl=`/`log_file=` interface to serve both.

## 2. Design: one dispatcher, two runners, one tail

```
_direct_dispatch(args, ops, index)
    mode = validate_jobdesc(jobdesc)
    run = _run_g4bl_job(jobdesc, index)            if mode == 'g4bl'
        = _run_mu2e_job(args, jobdesc, files, mode, index)   otherwise
    return _finish_job(args, run)
```

### 2.1 `JobRun` — what a runner hands the tail

```python
class JobRun(NamedTuple):
    outputs: list            # jobdesc['outputs']: [{'dataset': glob, 'location': ...}]
    log_file: str            # SAM-named log in cwd; may not exist yet
    job_failed: bool
    owner: str               # routes log location and dCache namespace
    infiles: str = ""        # space-separated SAM parents; "" = none
    simjob_setup: Optional[str] = None
    track_parents: bool = False
```

`typing.NamedTuple` + `Optional` — the worker runs under py3.9 on cvmfs
(PEP 604 `X | None` is forbidden in `utils/`).

### 2.2 Runners

- `_run_mu2e_job(args, jobdesc, files, mode, index) -> JobRun` — the
  current draining/normal prep branches of `_direct_dispatch` moved
  verbatim (files-list checks, `process_direct_input` /
  `process_jobdef`), then `_execute_mu2e`, then `_validate_outputs`
  when enabled. `log_file = replace_file_extensions(fcl, "log", "log")`,
  `owner = Mu2eName(Path(fcl).name).owner`,
  `track_parents = not is_dir_inloc(inloc)`.
- `_run_g4bl_job(jobdesc, index) -> JobRun` — prep as today (extract
  cnf, read jobpars, build names via `Mu2eName.build`,
  `owner = Mu2eName(jobdesc['tarball']).owner`), but execution goes
  through `prod_utils.run(['bash', '-c', script], shell=False)` inside
  `try/except CalledProcessError` exactly like `_execute_mu2e`. No
  Popen tee: the process output reaches `$JSB_TMP/JOBSUB_LOG_FILE`
  through the worker's stdout like every other job. `infiles=""`,
  `simjob_setup=None`, `track_parents=False`.

### 2.3 The tail — `_finish_job(args, run: JobRun) -> bool`

1. `_materialize_log(run.log_file)`: if `$JSB_TMP/JOBSUB_LOG_FILE`
   exists, copy it to `run.log_file` (creating or overwriting); else if
   `run.log_file` is missing, print a warning. This is the JSB_TMP copy
   that today lives inside `push_logs`, moved **before** the manifest.
2. Manifest: `manifest_files = []` if `run.job_failed`, else every
   file matching any `o['dataset']` glob in `run.outputs`;
   `_emit_manifest(run.log_file, manifest_files)` when the log exists —
   it appends the SHA256 block to the file (serving the `outstage`
   path) AND prints the identical block to stdout, which is what
   actually survives `writeLog` into a pushOutput-declared SAM log
   (section 1).
3. `log_location = log_storage_location(run.outputs, owner=run.owner)`.
4. `data_push` (skipped when failed): `_push_with_retry(push_data,
   run.outputs, run.infiles, simjob_setup=run.simjob_setup,
   track_parents=run.track_parents)`.
   `log_push`: `_push_with_retry(push_logs, run.log_file,
   simjob_setup=run.simjob_setup, location=log_location,
   track_parents=run.track_parents)`.
5. `args.dry_run` → print would-push summary, return.
6. `_push_all(data_push, log_push)`; return `run.job_failed`.

Print prefix is `[direct]` throughout the tail; runners keep their own
(`[direct]`, `[g4bl]`).

### 2.4 `push_logs` simplification

```python
def push_logs(log_file, simjob_setup=None, location="disk", track_parents=True):
```

No `fcl=` parameter, no JSB_TMP copy (that is `_materialize_log`'s job).
Parents column: `"parents_list.txt"` iff `track_parents` **and** the
file exists, else `"none"` — replaces today's `log_file is None` proxy
for "this is an art job". `push_logs` has no callers outside
`utils/runmu2e.py`.

## 3. Behavior changes (deliberate)

1. **mu2e SAM logs gain the SHA256 manifest** they were always meant
   to carry — not because the file `_emit_manifest` writes reaches SAM
   (pushOutput's `writeLog` rewrites that file away for every
   disk/scratch/tape destination, section 1), but because the same
   block is printed into the worker log, which IS
   `$JSB_TMP/JOBSUB_LOG_FILE`, which `writeLog` copies into the SAM
   log verbatim. Regression test: `_finish_job` on a `JobRun` with
   `$JSB_TMP/JOBSUB_LOG_FILE` present must produce both a log file and
   captured stdout containing `mu2egrid manifest`.
2. **g4bl SAM logs become the full worker log** (runmu2e chatter +
   g4bl output + pushOutput debug), identical in kind to mu2e logs
   today, instead of g4bl's bare process output. Consistency, not loss.
3. Everything else is behavior-preserving: same push ordering, same
   retry/terminal-error handling, same dry-run gate, same owner
   routing, same failure-path "push the log anyway".

## 4. Non-goals

- No change to prep (`process_jobdef`, `process_direct_input`, g4bl
  extraction) — nothing in common to share.
- `_validate_outputs` stays mu2e-only (it runs under the Musing's ROOT;
  g4bl has no Musing).
- No change to `bin/runjob.sh`, submission, ledger, or MCP.
- The manifest does not start excluding fetched input files (push_data
  does; the manifest never did — unchanged).

## 5. Testing

- `TestPushLogsParents` reshaped to the new signature (5 cases keep
  their parents-column expectations, driven by `track_parents`).
- New `TestMaterializeLog` / `TestFinishJob`: JSB_TMP copy precedes
  manifest (the production fix pin); dry-run skips `_push_all`; failed
  run skips data push but still pushes the log; `JobRun` fields reach
  `push_data`/`push_logs` unchanged.
- `TestG4blWorker`: `_run_g4bl_job` patched at `runmu2e.run`, returns a
  `JobRun` with `owner == 'testuser'` (the 2026-09-01 403 pin moves
  here); dispatch dry-run test goes through `_direct_dispatch`.
- `TestDirectDispatchFiles` must pass unchanged (it patches
  `_execute_mu2e` and `_push_all`, both still on the path).
- Suite baseline: 1244 passed / 54 pre-existing env failures / 2
  skipped; failure set must stay byte-identical.

## 6. Rollout

Land on `code-tarball` after the v3.3.1 grid smoke passes; tag
v3.3.2; publish with `install_prodtools.sh --no-current`, pin one
campaign, promote to `current` after a production log is seen to
carry the manifest — this gate stays valid under the new mechanism
(the block now reaches a SAM log via the stdout print, not the file
append; section 1). Verify with `grep -c 'mu2egrid manifest' <log>`
on a freshly declared log: expected **≥ 1**. Do not promise **2**
(header + selfcheck) for a SAM-declared log the way the pre-fix
incident page did — the selfcheck line's guaranteed presence is a
property of the local file copy (which serves `outstage`), not of
whatever `writeLog` ultimately assembles into the SAM copy.
