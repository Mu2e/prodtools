---
description: Build a one-off cnf tarball from a hand-written template.fcl (the JustInTimeFcl workflow) by wrapping prodtools jobdef + fcldump for the build-and-smoke cycle
argument-hint: --template T.fcl --desc D --dsconf C (--setup MUSING | --code TAR) (--run-number R --events-per-job N | --inputs LISTFILE --merge-factor M) [--owner U] [--aux N:KEY:LIST] [--no-smoke]
allowed-tools: Bash
---

# Build a JIT cnf tarball from a template.fcl

Wraps the front half of the
[JustInTimeFcl](https://mu2ewiki.fnal.gov/wiki/JustInTimeFcl) workflow:
take a hand-written `template.fcl`, run `jobdef` (prodtools wrapper
around upstream `mu2ejobdef`), and smoke-test with `fcldump
--local-jobdef --index 0`. The output is a
`cnf.<owner>.<desc>.<dsconf>.0.tar` ready for submission via
`/mu2ejobsub-submit`.

This skill is for **one-off / ad-hoc cnf construction** from a
hand-rolled template. For the declared-entries flow
(`data/<campaign>/*.json`), use `/stage-entry` instead — that goes
through `json2jobdef`, which builds the cnf AND handles the per-stage
gotchas (DbService overrides, dsconf rules, multi-output suffixes)
documented in `wiki/pages/json2jobdef-staging-workflow.md`.

## ⚠️ Things this skill will not do for you

- **Author the template.fcl.** You write it. It must contain at least
  the `#include "Production/JobConfig/.../*.fcl"` line and the
  `outputs.<Module>.fileName: "<tier>.owner.<desc>.version.sequencer.art"`
  override(s) per the file-naming convention. The `.owner.`,
  `version`, `sequencer` literal tokens are placeholders that
  `mu2ejobfcl` substitutes per-job. Don't use `{desc}` placeholders
  here unless your cnf is intentionally generic (direct-input mode).
- **Set DbService overrides.** Reco/digi/mix templates need
  `services.DbService.purpose: "Sim_best"` and
  `services.DbService.version: "v1_N"` in the template (per memory
  `reference_reco_dbservice_overrides.md`). The skill does NOT inject
  these — `mu2e -c` will crash at PBTFSD if they're missing for a
  reco/digi/mix job.
- **Submit anything.** Build + smoke only. Submission is a separate
  step via `/mu2ejobsub-submit` (one-off) — see the notes below on why
  a JIT-built cnf cannot go through the production campaign flow.

## Usage

```
/jit-cnf-build --template T.fcl --desc D --dsconf C \
               (--setup MUSING | --code TAR) \
               (--run-number R --events-per-job N | --inputs LIST --merge-factor M) \
               [--owner U] [--aux N:KEY:LISTFILE] [--no-smoke]
```

Required:

- `--template T.fcl` — path to your template.fcl. Must exist and be
  non-empty.
- `--desc D` — physics descriptor (becomes the 3rd field in
  `cnf.<owner>.<desc>.<dsconf>.0.tar` and in every output filename
  via `mu2ejobfcl` substitution).
- `--dsconf C` — build/calib version (e.g. `MDC2025am_best_v1_3`,
  `MDC2020ai_perfect_v1_3`, `TESTaa`). Per memory
  `feedback_desc_no_simjob_suffix.md`, do NOT glue the SimJob letter
  onto `--desc` — the dsconf already disambiguates.
- Either `--setup PATH` (full path to a SimJob setup.sh), or a bare
  SimJob tag (e.g. `MDC2025af`, `MDC2020ai_perfect_v1_3`) which the
  skill resolves to
  `/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/<tag>/setup.sh`; or
  `--code TAR` if you have a custom Offline-code tarball.
- Either generator mode (`--run-number R --events-per-job N`) or
  input-driven mode (`--inputs LIST --merge-factor M`). `mu2ejobdef`
  enforces this exclusivity itself; let it error out if you mix them.

Optional:

- `--owner U` — `cnf.<owner>...` field. Default: `$USER`.
- `--aux N:KEY:LISTFILE` — forward to `mu2ejobdef
  --auxinput=N:KEY:LISTFILE`. Repeatable for multiple aux inputs.
  Example: `--aux 1:physics.filters.CosmicResampler.fileNames:CosmicDSStop.txt`.
- `--no-smoke` — skip the post-build `fcldump --index 0` check.
  Default is to smoke — it catches malformed templates, missing
  DbService overrides, unsubstituted placeholders, and other build
  errors before you submit a cluster.

## Examples

```
# 1. Generator job (cosmic CRY with no source inputs)
echo '#include "Offline/CRVResponse/test/wideband/wideband4modules.fcl"' > template.fcl
echo 'source.module_type: EmptyEvent' >> template.fcl
/jit-cnf-build --template template.fcl --desc CosmicWidebandCRY \
               --dsconf MDC2020ae_best_v1_3 --setup MDC2020ae \
               --run-number 103001 --events-per-job 10000

# 2. Resampling stage from the wiki example
cat > template.fcl <<EOF
#include "Production/JobConfig/cosmic/S2Resampler.fcl"
#include "Production/JobConfig/cosmic/S2ResamplerLow.fcl"
outputs.PrimaryOutput.fileName: "dts.owner.CosmicCORSIKALow.version.sequencer.art"
EOF
samweb list-definition-files sim.mu2e.CosmicDSStopsCORSIKALow.MDC2020ab.art \
       | head -n 10 > CosmicDSStop.txt
/jit-cnf-build --template template.fcl --desc CosmicCORSIKALow \
               --dsconf MDC2020ab --setup MDC2020aa \
               --run-number 1203 --events-per-job 500000 \
               --aux 1:physics.filters.CosmicResampler.fileNames:CosmicDSStop.txt

# 3. Reco template (note the explicit DbService overrides — required!)
cat > template.fcl <<EOF
#include "Production/JobConfig/reco/Reco.fcl"
services.DbService.purpose: "Sim_best"
services.DbService.version: "v1_3"
outputs.Output.fileName: "mcs.owner.CosmicCRYAllOffSpillTriggeredReco.version.sequencer.art"
EOF
ls dig.oksuzian.CosmicCRYAllOffSpillTriggered.MDC2020ai_perfect_v1_3.*.art > inputs.txt
/jit-cnf-build --template template.fcl --desc CosmicCRYAllOffSpillTriggeredReco \
               --dsconf MDC2020ai_perfect_v1_3 --setup MDC2020ai_perfect_v1_3 \
               --inputs inputs.txt --merge-factor 10

# 4. Build but skip the smoke (e.g. when local mu2e -c isn't set up)
/jit-cnf-build --template template.fcl --desc X --dsconf C \
               --setup MDC2025af --run-number 1 --events-per-job 100 --no-smoke
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps:

1. **Parse args.** Required: `--template`, `--desc`, `--dsconf`, and
   one of `--setup`/`--code`, and either
   `--run-number`+`--events-per-job` or `--inputs`+`--merge-factor`.
   Optional: `--owner` (default `$USER`), `--aux` (repeatable),
   `--no-smoke`.

2. **Validate the template.** If `--template T.fcl` doesn't exist or
   is empty, exit with a clear error. Don't try to create it.

3. **Resolve `--setup`.** If the value starts with `/cvmfs/` and ends
   in `.sh`, use as-is. If it's a bare tag (no `/`, no `.sh`), expand
   to `/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/<tag>/setup.sh`
   and verify the file exists. Exit with a clear error if the
   resolved path doesn't exist (typo in tag, retired musing, etc.).
   If `--code TAR` was given instead, validate the tar exists and
   skip the setup-path expansion.

4. **Refuse silent overrides.** If the user passed both `--setup` and
   `--code`, error out — `mu2ejobdef` accepts only one. Same if they
   passed `--run-number` along with `--inputs` (mutually exclusive
   per upstream).

5. **Build.** Source the Mu2e environment, set up mu2egrid, and run:

   ```bash
   jobdef \
     --setup <RESOLVED_SETUP> \
     --dsconf <DSCONF> --dsowner <OWNER> --desc <DESC> \
     [--run-number <R> --events-per-job <N>] \
     [--inputs <LIST> --merge-factor <M>] \
     [--auxinput=<N1>:<K1>:<L1> --auxinput=<N2>:<K2>:<L2> ...] \
     --embed <TEMPLATE>
   ```

   (Note: prodtools `bin/jobdef` accepts `--code` too if `--setup` is
   omitted — pass whichever the user supplied.)

   The resulting tarball will be named
   `cnf.<owner>.<desc>.<dsconf>.0.tar` and written to cwd. If `jobdef`
   exits non-zero, surface the stderr and stop — don't smoke a broken
   cnf.

6. **Smoke** (unless `--no-smoke`). Run:

   ```bash
   fcldump --local-jobdef cnf.<owner>.<desc>.<dsconf>.0.tar --index 0
   ```

   `fcldump` defaults to `--proto root --loc tape`, which is the right
   smoke target for most cnfs (per memory
   `reference_jobfcl_proto_root_for_tape_smoke.md`). If the user's
   inputs are local (`dir:/exp/...`), they'll need to re-run fcldump
   manually with `--proto file --loc dir:...`. Don't try to guess that
   from the template.

   Inspect the smoke output: if it contains an unsubstituted
   `description` token, an empty `source.fileNames: []`, or an error
   line from `mu2ejobfcl`, flag it loudly. Do not delete the cnf —
   the user may want to inspect it.

7. **Report.** Print:
   - cnf path: absolute path to the produced tarball
   - cnf basename: `cnf.<owner>.<desc>.<dsconf>.0.tar`
   - smoke result: pass/fail/skipped
   - next-step suggestion:
     ```
     # Submit a smoke cluster (3 jobs, defaults):
     /mu2ejobsub-submit cnf.<owner>.<desc>.<dsconf>.0.tar --firstjob 0 --njobs 3
     # Or just dump the fcl for index N:
     fcldump --local-jobdef cnf.<owner>.<desc>.<dsconf>.0.tar --index N
     ```

## Notes

- `jobdef` (prodtools) is a thin Python wrapper around the upstream
  Perl `mu2ejobdef`. It forwards `--setup`, `--code`, `--dsconf`,
  `--desc`, `--dsowner`, `--run-number`, `--events-per-job`,
  `--embed`, `--inputs`, `--merge-factor`, `--auxinput=` verbatim.
  This skill calls `jobdef` (not `mu2ejobdef` directly) per the
  CLAUDE.md "prefer prodtools wrappers" rule, but the underlying tool
  is the same.
- The `cnf.<owner>.<desc>.<dsconf>.0.tar` naming convention is
  enforced by upstream `mu2ejobdef` — the trailing `.0` is the
  literal Mu2e file-naming sequencer field and is always `0` for cnf
  tarballs. See `https://mu2ewiki.fnal.gov/wiki/FileNames`.
- For reco/digi/mix templates, you MUST set
  `services.DbService.purpose` and `.version` in the template — see
  memory `reference_reco_dbservice_overrides.md`. There's no good
  reason for the skill to inject these silently, because the correct
  values depend on the dsconf calib intent (`Sim_best` vs
  `Sim_perfect` vs other), which the user knows and the skill
  doesn't.
- Output filename suffixes (`-LH`, `-CH`, `Triggered`, `Triggerable`)
  if present in your template's `outputs.X.fileName` literal will
  pass through unchanged. The `validate_output_filenames` check in
  `utils/jobfcl.py` runs at smoke time and will reject any
  unsubstituted `description`/`{desc}`/etc. placeholder. See memory
  `reference_reco_output_suffix_overrides.md`.
- Once the cnf is built and smoke-tested, the natural next steps are:
  - `/mu2ejobsub-submit <cnf>.tar --firstjob 0 --njobs N` for a
    one-off / smoke cluster.
  - The prodtools direct backend (`json2jobdef --enqueue` +
    `submissions run`) does not handle template/g4bl modes — those run
    via the upstream `mu2ejobsub`/`mu2eg4bl` CLIs per ADR
    `2026-04-30-phase2-direct-jobsub-implementation.md` §CB10 —
    JIT-cnfs must go via the upstream `/mu2ejobsub-submit` skill until
    that scope cut is lifted.
