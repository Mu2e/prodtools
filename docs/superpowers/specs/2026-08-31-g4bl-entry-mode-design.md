# g4bl entry mode in the direct backend

Date: 2026-08-31
Status: approved (design), implementation pending
Origin: request for a "g4bl MCP server". Brainstorming resolved it to:
revive g4bl as a first-class entry mode in prodtools' direct backend,
so the existing MCP write surface (`push_cnf` + `run_submissions`)
carries g4bl campaigns with no new server.

## Context

The Phase-1 `mu2ejobsub` backend and its `template`/`direct_input`/`g4bl`
entry modes were retired 2026-07-19 (`utils/submit.py` header). Since
then the only g4bl grid path is the upstream `mu2eg4bl` CLI, which the
project does not want to depend on. The retired native-AL9 execution
recipe survives at commit `401e3da` (`utils/prod_utils.py:
process_g4bl_jobdef`) and in `wiki/pages/g4bl-runner.md`; it is proven
and is reused here nearly verbatim.

Decisions made during brainstorming:

- Backend: prodtools direct backend only. No wrapping of `mu2eg4bl` or
  `mu2ejobsub` ("We should not be using mu2eg4bl and mu2ejobsub in
  prodtools").
- Home: inside this repo. No standalone package, no third MCP server.
- Approach A approved: g4bl becomes an entry type; ledger, slicing,
  recovery, and the MCP surface are inherited unchanged.

## 1. Entry schema (submit half)

New job type `g4bl`, detected by `"runner": "g4bl"` in the entry JSON
(checked before the other `determine_job_type` branches; a g4bl entry
carries none of their marker keys).

```json
{
  "runner": "g4bl",
  "desc": "Mu2EBeamline",
  "dsconf": "Run1B-test",
  "owner": "oksuzian",
  "g4bl_dir": "/exp/mu2e/app/users/oksuzian/G4BeamlineScripts",
  "main_input": "Mu2E.in",
  "events_per_job": 1000,
  "njobs": 10,
  "outloc": {"nts.*.root": "scratch"}
}
```

- `g4bl_dir` — local directory holding the deck and its support files
  (`Geometry/`, auxiliary `.in` files). Copied wholesale into the
  tarball's `work/`. Required; must exist (fail loudly, no fallback).
- `main_input` — deck filename relative to `g4bl_dir`. Required; must
  exist inside `g4bl_dir`.
- `events_per_job`, `njobs` — required integers.
- `outloc` — the standard prodtools entry key (dataset glob ->
  location); `build_jobdesc` converts it to the ledger entry's
  `outputs` list as for every other entry type. The histogram glob is
  `nts.*.root`; location follows the usual rules (scratch for
  self-owned, tape/disk for mu2epro).
- Optional standard keys work unchanged: `memory`,
  `expected_lifetime`, `disk` (resource defaults otherwise), and the
  list-valued-key expansion axis (a list value expands into N entry
  variants — beamline knob sweeps come free).
- Not allowed / not present: `fcl`, `simjob_setup`, `inloc` (forced to
  `none`), `input_data`, `resampler_name`, `pbeam`. Presence of any of
  these alongside `runner: "g4bl"` is a validation error.

The builder packs `cnf.<owner>.<desc>.<dsconf>.0.tar`:

```
work/            # copy of g4bl_dir (deck + Geometry/ + aux files)
jobpars.json     # {"runner": "g4bl", "desc", "dsconf", "main_input",
                 #  "events_per_job", "njobs"}
```

Self-describing, same shape the retired runner consumed. No
`mu2e.fcl`, no `template.fcl`.

`validate_era_agreement` skips entries without `simjob_setup`: g4bl is
decoupled from Offline and has no musing era to agree with. (This is a
general rule, not a g4bl special case — the check is anchored on
`simjob_setup` presence.)

`--prod --enqueue`, ledger registration, `slice_size`, and
`submissions run` are untouched. `runner: "g4bl"` rides along in the
ledger entry JSON and therefore in the ops JSON handed to workers.

## 2. Worker mode (runmu2e direct mode)

`validate_jobdesc` gains a third mode: `'g4bl'` when
`jobdesc.get('runner') == 'g4bl'`. Required fields: `tarball`,
`outputs`, `njobs`. A g4bl jobdesc combined with a draining `files`
list is an error (g4bl jobs have no input files).

`_direct_dispatch` gets a g4bl branch replacing the fcl -> `mu2e -c`
path:

1. Extract the cnf tarball to a scratch dir; require `work/` and
   `jobpars.json` (fail loudly otherwise).
2. `sequencer` from the synthesized fname; `job_index` parsed from it;
   `First_Event = job_index * events_per_job + 1`.
3. Run g4bl via the proven 401e3da recipe:

   ```bash
   unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
   eval "$(spack load --sh g4beamline)"
   cd work/
   g4bl <main_input> viewer=none First_Event=<F> Num_Events=<N> \
        histoFile=<abs path>/nts.<owner>.<desc>.<dsconf>.<seq>.root
   ```

   Native AL9, no container. CLI overrides use `key=value` form (g4bl
   3.08b rejects `param key=value` on the command line). Stdout/stderr
   streamed to both the runner stdout and
   `log.<owner>.<desc>.<dsconf>.<seq>.log`.

   `<owner>` comes from the cnf tarball name
   (`Mu2eName(jobdesc['tarball']).owner`), NOT a literal `mu2e`: the
   owner field of the output name is what routes the dCache path
   (`phy-*` for owner mu2e, `usr-*/<owner>` otherwise). The retired
   401e3da recipe hardcoded `mu2e` because it only ever ran as
   production; a self-owned run named that way dies with
   `DESTINATION MAKE_PARENT HTTP 403` (found live, 2026-08-31 smoke).
4. Shared tail, reused as-is: SHA256 manifest appended to the log;
   `push_data(outputs, infiles="", track_parents=False)` on success;
   `push_logs(log_file=..., location=log_storage_location(outputs,
   owner))` always (the no-FCL `log_file` path in `push_logs` already
   exists for exactly this). `--dry_run` honored.

No SAM parents (g4bl jobs have no SAM inputs), so `parents_list.txt`
is never written and never named.

## 3. MCP surface

Zero new tools and zero new servers. `push_cnf(json, desc, dsconf,
slice_size, run_as)` builds and registers the g4bl cnf;
`run_submissions` ticks it; `campaign_status` / `find_datasets` /
`dataset_details` read it. The only change is subtractive: any
submit-side validation that assumes `fcl`/`simjob_setup` must accept
`runner: "g4bl"` entries.

The read-only server stays read-only.

## 4. Error handling

- Missing `g4bl_dir`, `main_input`, `events_per_job`, or `njobs`:
  build-time ValueError naming the key. No fallback values.
- Worker-side missing `work/` or `jobpars.json` in the tarball:
  RuntimeError before any g4bl exec; no log push (nothing ran).
- g4bl nonzero exit: log still pushed (`push_logs(log_file=...)`),
  data push skipped, job marked failed — standard recovery applies.
- `spack load g4beamline` failure surfaces in the streamed log; the
  job fails loudly (no container fallback, no version fallback).

## 5. Testing and rollout

- Unit tests (`test/test_unit.py`): builder output (tarball members,
  jobpars keys, refusal of fcl/simjob_setup/inloc alongside runner),
  `determine_job_type` ordering, `validate_jobdesc` g4bl mode +
  draining refusal, era-check skip, dispatch branch with a stubbed
  `g4bl` executable on PATH.
- Local smoke: run the worker branch on a gpvm via the local worker
  harness (env: `CONDOR_DIR_INPUT`, `PROCESS`, `MU2EGRID_JOBDEF`,
  `MU2EGRID_OPSJSON`, `JSB_TMP`) with real spack g4beamline, a few
  events, pushes to scratch as self. This validates the worker half
  without a cvmfs release.
- Grid rollout: the worker half runs only from the entry's
  `prodtools_dir` cvmfs release, so grid g4bl needs the next release
  (v3.3.1), bundled with the pending e136c61 owner-aware log-location
  fix. Grid smoke as self (scratch outputs) after the release lands.

## Out of scope

- surrokit integration (GP-driven beamline optimization). The sweep
  expansion axis plus SAM-registered nts outputs make a later
  surrokit adapter straightforward, but nothing here depends on it.
- Reviving `template`/`direct_input`-via-mu2ejobsub or HPC submission.
- Any change to the upstream mu2egrid tools or the
  `/mu2eg4bl-submit` skill (it remains as a legacy escape hatch).
