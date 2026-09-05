---
title: g4bl runner architecture (native AL9 spack)
tags: [reference, runner, g4bl, spack, al9, entry-mode]
sources: [2026-04-27-g4bl-runner-integration]
updated: 2026-09-02
---

# g4bl runner architecture

`prodtools` schedules Geant4 Beamline (`g4bl`) jobs alongside its
Offline-stage runners but the execution path is independent: no
`mu2e -c`, no SAM-art FCL pipeline, no Musing. Output is `.root`
(`nts.<owner>.*` tier per metacat convention — `<owner>` comes from
the cnf tarball name, never a literal `mu2e`; see below).

Since 2026-08-31 g4bl is a first-class **entry type**
(`"runner": "g4bl"`) in the prodtools **direct backend**: it shares
ledger, slicing, recovery and the MCP write surface (`push_cnf` /
`run_submissions`) with every other entry type, at the cost of zero
new tools and zero new servers. Design: `docs/superpowers/specs/
2026-08-31-g4bl-entry-mode-design.md`; implementation plan:
`docs/superpowers/plans/2026-08-31-g4bl-entry-mode.md`.

g4bl's place in the prodtools chain is documented in
[[json2jobdef-staging-workflow]] §"g4bl is decoupled". This page
covers **how the runner actually works** today, its history, and the
2026-09-01 local smoke that validated it end to end.

## Current execution path: `runner: "g4bl"` direct-backend entry (2026-08-31)

A campaign JSON entry:

```json
{
  "runner": "g4bl",
  "desc": "G4blSmoke", "dsconf": "MCPTest005", "owner": "oksuzian",
  "g4bl_dir": "/exp/mu2e/app/users/oksuzian/G4BeamlineScripts",
  "main_input": "Mu2E.in",
  "events_per_job": 10, "njobs": 1,
  "outloc": {"nts.*.root": "scratch"}
}
```

`json2jobdef` (`utils/json2jobdef.py`) detects it — `determine_job_type`
returns `'g4bl'` before any other branch — and validates it with
`_validate_g4bl_entry`: required `desc`, `dsconf`, `outloc`,
`g4bl_dir`, `main_input`, `events_per_job`, `njobs` (positive ints for
the last two); optional `g4bl_params` (2026-09-03: dict of g4bl
parameter name → string/number, appended to the g4bl command line as
`key=value` after the worker's own overrides; `First_Event`,
`Num_Events`, `histoFile`, `viewer` refused — e.g. `{"READ_Beam_File":
1}` picks the deck's beam-file mode from the JSON); forbidden `fcl`, `simjob_setup`, `code`, `input_data`,
`resampler_name`, `pbeam`, `generic_tarball`, `input_pattern`,
`firstjob`, `inloc` — any art-pipeline key alongside `runner: "g4bl"`
is a config error, not something to silently ignore. It then packs a
self-describing cnf tarball via `_build_g4bl_tarball`:

```
cnf.<owner>.<desc>.<dsconf>.0.tar
├── work/            # copy of g4bl_dir: deck + Geometry/ + aux files
└── jobpars.json     # {runner, desc, dsconf, main_input,
                      #  events_per_job, njobs, owner, tbs,
                      #  g4bl_params (only when set)}
```

No `mu2ejobdef` call, no fcl, no Musing setup. `bin/json2jobdef`'s
wrapper now requires only `MUSE_DIR` (`muse setup ops`); the old
`command -v mu2e` / Musing requirement moved into Python
(`process_single_entry`) and fires only for non-g4bl (art) entries —
a g4bl build runs from a bare ops env with no SimJob sourced (verified
live in the smoke below).

The worker (`utils/runmu2e.py`, direct-dispatch mode) gained a third
`validate_jobdesc` mode, `'g4bl'`: required `tarball`, `outputs`,
`njobs`; a g4bl jobdesc paired with a draining `files` list is refused
(g4bl jobs take no SAM input files). `_direct_dispatch` recognises
`runner: g4bl` and calls `_run_g4bl_job(jobdesc, index)`, which
extracts the cnf, reads `jobpars.json`, derives the owner from the cnf
tarball name, computes `First_Event = index * events_per_job + 1`,
builds the output/log names via `Mu2eName.build` (see below), and
runs g4bl through `prod_utils.run` via the `401e3da` recipe
(unchanged — see "Execution recipe" below; only the caller moved,
from the retired `process_g4bl_jobdef` to this runner). g4bl's output
reaches the worker's stdout like every other runner (i.e.
`$JSB_TMP/JOBSUB_LOG_FILE`). `_run_g4bl_job` returns a `JobRun`
(`outputs`, `log_file`, `job_failed`, `owner`; `infiles=""`,
`simjob_setup=None`, `track_parents=False`).

Output/log names: `nts.<owner>.<desc>.<dsconf>.<%08d seq>.root` /
`log.<owner>.<desc>.<dsconf>.<%08d seq>.log`, where `<owner>` is
parsed from the cnf tarball name (`Mu2eName(jobdesc['tarball']).owner`)
— **never** a literal `mu2e`. The literal was safe under the retired
path (production-account only); a self-submitted cnf using it instead
gets `DESTINATION MAKE_PARENT HTTP 403` from a user bearer token,
because the owner field is what routes the dCache namespace (`phy-*`
for owner `mu2e`, `usr-*/<owner>` otherwise). Found live in the
2026-08-31/09-01 smoke below; fixed in commit `dd0d437` (spec and plan
amended to match — see "Local end-to-end smoke").

The shared tail `_finish_job` then materializes the SAM log from the
jobsub log (`_materialize_log`), appends the SHA256 manifest to that
file AND prints the identical block to stdout — stdout is what
survives OfflineOps pushOutput's `writeLog` rewrite into a
pushOutput-declared SAM log, where the file append alone does not —
and pushes data (success only) and the log (always) via
`push_data(outputs, "", track_parents=False)` and
`push_logs(log_file, location=log_storage_location(outputs,
owner=owner), track_parents=False)` — the same shared tail every
other runner mode uses; `--dry_run` and the entire
ledger/slicing/recovery/MCP surface (`push_cnf`, `run_submissions`,
`campaign_status`) are unmodified. Since v3.3.2 the g4bl SAM log is
the full worker log, identical in kind to mu2e logs — see
[[2026-09-01-mu2e-log-manifest-never-landed]] for the pre-fix history.

Canonical invocation:

The checked-in config is `data/g4bl/g4bl.json` (one `G4blSmoke` entry
pointing at the user's clone of `Mu2e/G4BeamlineScripts`; bump `dsconf`
per campaign). Smoke configs before it lived only in
`claude-scratch/g4blsmoke/`.

```bash
# local build, no SAM
json2jobdef --json data/g4bl/g4bl.json --desc G4blSmoke --dsconf MCPTest005

# production campaign
json2jobdef --json data/g4bl/g4bl.json --desc G4blSmoke --dsconf MCPTest005 \
            --prod --enqueue --slice-size 100
```

Grid execution needs the worker's g4bl branch, which ships in
prodtools `>= v3.3.1` on cvmfs (not yet released as of 2026-09-02;
workers run their ledger entry's pinned `prodtools_dir`). Before that
release lands, a self-owned grid smoke can pin the checkout instead:

```bash
json2jobdef --json data/g4bl/g4bl.json --desc G4blSmoke --dsconf MCPTest006 \
            --prod --enqueue --slice-size 100 --prodtools-dir $PWD
```

`--prodtools-dir` outside the cvmfs release root tars the checkout's
`bin/` + `utils/` once, pins the digest on the entry, and ships the tar
to every job (`MU2EGRID_PRODTOOLS_TAR`); refused for `mu2epro`. See
[[2026-08-28-workers-run-prodtools-from-cvmfs-releases]] (amendment).

## Grid end-to-end smoke (2026-09-02) — PASSED

First g4bl campaign on FermiGrid through the direct backend, run as
`oksuzian` from the `code-tarball` checkout via the dev-tarball opt-in
(no cvmfs release involved):

- Enqueue: `json2jobdef --json g4bl_smoke_006.json --desc G4blSmoke
  --dsconf MCPTest006 --prod --enqueue --slice-size 100 --prodtools-dir
  $PWD` in a `setupmu2e-art.sh && muse setup ops && setup OfflineOps &&
  getToken` shell (no Musing; `setup OfflineOps` supplies `pushOutput`).
  Self ledger campaign 5, `prodtools_tar =
  .../prodtools-tarballs/prodtools-37d4024e0bf9.tar` (706560 bytes).
- Tick: `submissions --db <self.db> run --campaign 5` → cluster
  `29824782@jobsub04.fnal.gov`, 3 jobs (10 events each), row 11. All
  three ran within a minute of submission, exit 0, wall 239–344 s.
- Worker log (`log.oksuzian.G4blSmoke.MCPTest006.00000000-1788407754.log`,
  SAM-declared, 12393 lines): line 17 `MU2EGRID_PRODTOOLS_TAR=
  prodtools-37d4024e0bf9.tar`, line 25 `=== extracting dev prodtools
  tarball ...`, line 30 `=== exec python3 runmu2e.py ===`; g4beamline
  3.08 (spack, native AL9) `simulation complete`; `Copied nts...root to
  /pnfs/mu2e/scratch/datasets/usr-nts/nts/oksuzian/G4blSmoke/MCPTest006`
  + `Declare SAM`. **`grep -c 'mu2egrid manifest'` = 2** in the
  SAM-declared log — the first SAM log ever seen carrying the manifest
  (see [[2026-09-01-mu2e-log-manifest-never-landed]]).
- SAM: 3 × `nts.oksuzian.G4blSmoke.MCPTest006.0000000N.root`, 3 logs.
  Verification tick closed row 11 `complete (3 indices)`; campaign 5
  `complete 3/3`.

What this proves beyond the local smoke: RCDS/dropbox delivery of the
prodtools tar, `runjob.sh` tar mode on a real worker, the user bearer
token's scopes for `usr-nts`/`usr-etc` scratch pushes, and the
`_finish_job` manifest reaching a SAM-declared log. Still unproven: the
same chain from a cvmfs release (v3.3.1 tag at `7abdea7` is the plan; the
smoke ran the branch HEAD, which also carries the runmu2e consolidation).

## Local end-to-end smoke (2026-09-01)

Ran the full submit -> build -> dispatch -> push chain locally via the
local worker harness (real spack `g4beamline`, self account
`oksuzian`, no grid; see memory `reference_local_worker_harness.md`).

- **Build.** `json2jobdef --json g4bl_smoke.json --desc G4blSmoke
  --dsconf MCPTest005` from a bare `muse setup ops` env (no Musing
  sourced) built `cnf.oksuzian.G4blSmoke.MCPTest005.0.tar` (3.8 MB):
  `work/Mu2E.in`, `work/Geometry/...`, `jobpars.json` all present as
  expected.
- **Dispatch, dry-run.** Real spack g4bl ran 10 events, wrote
  `nts.<owner>.G4blSmoke.MCPTest005.00000000.root` plus a log ending in
  g4bl's `Run complete` summary and a `mu2egrid manifest` trailer;
  the dry-run line named the push targets, log routed to `scratch`
  (owner-aware). rc=0.
- **Owner-naming defect found live.** A real (non-dry-run) push with
  the pre-fix code hardcoded owner `mu2e` in the output/log names
  (`nts.mu2e.*` / `log.mu2e.*`); `pushOutput` routed that to the
  production `phy-nts`/`phy-etc` namespace and a user bearer token got
  `DESTINATION MAKE_PARENT HTTP 403`. Fixed in `dd0d437` (owner now
  parsed from the cnf tarball name); see "Current execution path"
  above.
- **Real push, owner-fixed (final attempt).**
  `nts.oksuzian.G4blSmoke.MCPTest005.00000000.root` DECLARED in SAM,
  located at `/pnfs/mu2e/scratch/datasets/usr-nts/nts/oksuzian/
  G4blSmoke/MCPTest005/root/6e/0c` — the owner-routed `usr-nts`
  namespace, confirming the 403 fix live. Independently re-verified
  via the read-only MCP (`dataset_details`): 1 file, 86951 bytes,
  matching the local artifact byte-for-byte. The log push exercised
  the same owner-aware `processing: scratch log....log` routing
  correctly; the final `writeLog` step failed only on a missing
  `jsb_tmp`/`JOBSUB_ERR_FILE` — a **local-harness artifact** (real
  grid workers get this from jobsub), not a worker code defect. Two
  earlier attempts in the same session hit two other harness-only
  failures (missing `JSB_TMP` plumbing; `pushOutput`'s
  `cat $BEARER_TOKEN_FILE` reading inherited stdin forever when the
  var was unexpectedly unset) — see `reference_local_worker_harness.md`
  for both.
- **Net result:** the g4bl direct-dispatch worker branch is validated
  end to end for data (nts) as self; log-push validation is blocked
  only by local-harness limitations and is expected to be moot on a
  real grid worker. Artifacts under
  `/exp/mu2e/data/users/oksuzian/claude-scratch/g4blsmoke/job4/`.

## Execution recipe: unchanged since `401e3da` (2026-04-28), now called from the direct-dispatch worker

The env/spack recipe below predates the entry-mode rewrite above and
is reused **verbatim** by it — only the caller changed, from the
retired mu2ejobsub-era `utils/prod_utils.py:process_g4bl_jobdef` to
the direct-backend `utils/runmu2e.py:_run_g4bl_job` runner (which
calls `_g4bl_script`).

```
bash -c '
  unset SPACK_ENV PYTHONHOME PYTHONPATH
  source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1
  eval "$(spack load --sh g4beamline)"
  cd <work dir>
  g4bl <main_input> viewer=none histoFile=<seq>.root \
       Num_Events=<N> First_Event=<F>
'
```

- **No apptainer / no SL7 container.** Workers already run on AL9
  (`fnal-wn-el9` outer container set by `poms/fermigrid.cfg`); g4bl
  3.08b has a native AL9 build available via spack.
- **`unset SPACK_ENV`** is mandatory before sourcing. `muse setup ops`
  runs before the g4bl subprocess launches, which activates the
  `ops-019` spack env; without unsetting, `spack load g4beamline`
  searches only `ops-019` (no g4beamline there) and fails with
  "matches no installed packages". See memory
  `reference_spack_env_after_muse_setup.md`.
- **`unset PYTHON*`** before sourcing — g4bl spack-load itself runs
  Python; AL9-mu2e PYTHONHOME/PATH leaks confuse it.
- **`g4bl key=value` (CLI)**, NOT `param key=value`. 3.08b rejects the
  `param` form on the command line — see memory
  `reference_g4bl_param_unset_semantics.md`. The `param` form is
  input-file syntax only.

`401e3da` ("Switch g4bl runner to native AL9 spack; minimize POMS map
shape") removed ~50 LOC of container wrapping from the (now also
retired) mu2ejobsub-backend implementation:

| Concern | Before `401e3da` (SL7/apptainer) | After `401e3da` (native AL9 spack) |
|---|---|---|
| Container | `apptainer exec --cleanenv` against `/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-dev-sl7:latest` | None — runs in the `fnal-wn-el9` worker outer container |
| Binds | HOME, `/tmp` (whole), `embed_dir`, `/cvmfs` | None (worker FS already has `/cvmfs`, `/tmp`, etc.) |
| Env hygiene | `--cleanenv` (drops PYTHONHOME / UPS_DIR / PRODUCTS that leaked from AL9 parent into SL7) | `unset SPACK_ENV PYTHON*` before sourcing (selective; same intent, but no container barrier needed) |
| Container override | `DEFAULT_G4BL_CONTAINER` constant + opt-in `container` JSON field | Removed |
| SL7-detection | `_is_inside_sl7()` (read `/etc/redhat-release`, skip wrap when grid Condor scheduled job inside SL7 directly via `poms/g4bl.cfg`'s `+SingularityImage`) | Removed |
| POMS submit cfg | `poms/g4bl.cfg` (sets `+SingularityImage` outer container to `fnal-dev-sl7`) | Deleted; reuses `poms/fermigrid.cfg` |
| LOC in `process_g4bl_jobdef` | ~120 LOC | ~70 LOC |

The pre-`401e3da` path is captured in
`wiki/raw/2026-04-27-g4bl-runner-integration.md` for institutional
record. The mu2ejobsub-backend `process_g4bl_jobdef` implementation
itself was retired 2026-07-19 (`utils/submit.py` header) along with
the rest of the Phase-1 mu2ejobsub backend — it is no longer a live
code path. This table and the recipe above survive as the historical
anchor for, and the direct source of, the current direct-backend
worker's g4bl recipe. Don't cite `process_g4bl_jobdef` as current
state.

## Why two SL7 gotchas became unnecessary

The 2026-04-27 raw notes flagged two gotchas that were specific to
the apptainer/SL7 wrap and don't apply to the native AL9 recipe (true
of both the retired mu2ejobsub caller and the current direct-dispatch
worker):

- **A.** `--cleanenv` mandatory when launching apptainer from a
  Python subprocess whose parent had AL9 mu2e env sourced. Symptom:
  `bash: setup: command not found` at line 3 of the runner script
  because UPS init silently failed under leaked AL9 PYTHONHOME.
- **B.** `/tmp` had to be bound *wholesale*, not just the per-job
  output subdir, because UPS init in `setupmu2e-art.sh` uses `/tmp`
  for scratch.

Both are gone with the native AL9 path. The replacement gotchas
(SPACK_ENV unset, `param` CLI form) are documented in their own
memory entries.

## Historical: mu2ejobsub-backend POMS map shape (`401e3da`, superseded 2026-08-31)

Retired along with the rest of the mu2ejobsub backend
(2026-07-19). Kept for historical reference only — the current
JSON-entry schema is in "Current execution path" above; do not build
against this shape.

Production `data/<campaign>/g4bl.json` carried 5 fields per entry
(matching the `MDC2025-NNN.json` map convention):

```json
{
  "runner": "g4bl",
  "tarball": "...",      // built once by g4bl_jobdef
  "njobs": <N>,
  "inloc": "none",
  "outputs": [{"dataset": "nts.mu2e.G4blPOT.TESTaa.root", "location": "disk"}]
}
```

Runtime config (`desc`, `dsconf`, `main_input`, `events_per_job`)
lived in `jobpars.json` *inside* the tarball, same self-describing
idea the current entry mode reuses. `embed_dir` mode (local smoke)
kept the full schema since there was no tarball; that distinction
does not exist in the current `runner: "g4bl"` entry mode, which
always builds a tarball.

## Historical: naming & dsconf convention (`401e3da` demonstrator)

- Output tier: `nts.mu2e.*` unconditionally. **Superseded**: the
  current entry mode names outputs `nts.<owner>.*` — the owner comes
  from the cnf tarball, never a hardcoded literal (see "Current
  execution path" and the owner-naming defect in the smoke section
  above). Was `g4bl.mu2e.*` pre-`401e3da`.
- `desc`/`dsconf` in the `401e3da` demonstrator were fixed
  placeholders (`G4blPOT` / `TESTaa`) baked into the POMS map, not
  campaign-supplied. The current entry mode takes ordinary `desc` /
  `dsconf` values straight from the JSON entry like every other
  runner type (e.g. `G4blSmoke` / `MCPTest005` in the 2026-09-01
  smoke) — there is no more per-runner hardcoded naming rule.

## Demonstrator artifacts (from `401e3da`, historical)

- Tarball SAM-declared at
  `/pnfs/mu2e/tape/phy-etc/cnf/mu2e/G4blPOT/TESTaa/tar/c7/74/`
- POMS map: `G4BL-000.json` (separate map family from `MDC2025-NNN`)
- SAM index def: `iG4BL-000` (1 file)

## Related

- `docs/superpowers/specs/2026-08-31-g4bl-entry-mode-design.md`
  (entry-mode design spec)
- `docs/superpowers/plans/2026-08-31-g4bl-entry-mode.md`
  (7-task TDD implementation plan)
- `utils/json2jobdef.py:_validate_g4bl_entry`,
  `utils/json2jobdef.py:_build_g4bl_tarball` (submit-side, current)
- `utils/runmu2e.py:_run_g4bl_job`, `utils/runmu2e.py:_finish_job`,
  `utils/runmu2e.py:_g4bl_script` (worker-side, current)
- [[2026-09-01-mu2e-log-manifest-never-landed]] (why the SAM log is now
  the full worker stdout, and how that differs from mu2e logs before
  v3.3.2)
- Memory `reference_local_worker_harness.md` (how the 2026-09-01
  smoke was run without a grid submission)
- Memory `reference_g4bl_decoupled_from_offline.md` (place in chain)
- Memory `reference_spack_env_after_muse_setup.md` (SPACK_ENV gotcha)
- Memory `reference_g4bl_param_unset_semantics.md` (CLI param form)
- Memory `feedback_no_fallbacks.md` (validation discipline)
- Memory `project_g4bl_entry_mode.md` (design decisions, status)
- `wiki/raw/2026-04-27-g4bl-runner-integration.md` (pre-`401e3da`
  history)
