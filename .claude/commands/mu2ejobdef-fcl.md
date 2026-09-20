---
description: Build and inspect a cnf tarball using the upstream Perl mu2ejobdef + mu2ejobfcl directly (bypasses the prodtools Python parity wrappers). Mirrors the JustInTimeFcl wiki workflow verbatim.
argument-hint: build --template T.fcl --desc D --dsconf C --setup S (--run-number R --events-per-job N | --inputs L --merge-factor M) [args] | inspect --jobdef <cnf>.tar (--index N | --target T) [--default-proto P] [--default-loc L]
allowed-tools: Bash
---

# Build / inspect cnf via upstream Perl `mu2ejobdef` + `mu2ejobfcl`

Wraps the upstream Perl binaries from `mu2egrid` (currently
`v8_03_02`):

- `mu2ejobdef` — packs a template.fcl + setup + dataset metadata into
  a `cnf.<owner>.<desc>.<dsconf>.0.tar`. This is what the
  [JustInTimeFcl](https://mu2ewiki.fnal.gov/wiki/JustInTimeFcl) wiki
  calls in its "Prepare the job definition file" step.
- `mu2ejobfcl` — materializes a per-index fcl from that cnf, or by
  target output filename. This is the wiki's "Inspect fcl file
  content" step.

**This is intentionally a separate skill from
`/jit-cnf-build`.** That skill calls prodtools' Python parity
reimplementations (`bin/jobdef` → `utils/jobdef.py`, `bin/fcldump` →
`utils/jobfcl.py:Mu2eJobFCL`). This skill calls the upstream Perl
binaries directly — same conceptual workflow, different runtime. Use
when:

- You want to follow the wiki commands verbatim
- You're parity-testing prodtools against the canonical upstream
- A prodtools wrapper bug makes you want a fallback
- You're investigating differences between the two implementations

For day-to-day work, `/jit-cnf-build` is the recommended path because
it composes cleanly with the rest of the prodtools chain
(`fcldump --local-jobdef`, `validate_output_filenames`,
`/mu2ejobsub-submit`).

## Usage

Two subcommands. Pick one explicitly:

```
/mu2ejobdef-fcl build  --template T.fcl --desc D --dsconf C \
                       (--setup MUSING | --code TAR) \
                       (--run-number R --events-per-job N | --inputs L --merge-factor M) \
                       [--dsowner U] [--auxinput=N:KEY:LIST] [--no-smoke] [extra args]

/mu2ejobdef-fcl inspect --jobdef <cnf>.tar (--index N | --target T) \
                        [--default-proto P] [--default-loc L] [extra args]
```

### `build` subcommand

Runs `mu2ejobdef`, then (unless `--no-smoke`) `mu2ejobfcl --index 0` to
sanity-check the result. Args are forwarded to `mu2ejobdef` **verbatim**
— this skill does not rewrite them. Spell flags exactly as the upstream
expects (`--option=value` or `--option value` both fine).

Required:

- `--template T.fcl` — passed as `--embed T.fcl`. Must exist.
- `--desc D` — `--description D`. (mu2ejobdef accepts `--desc` as a
  prefix abbreviation.)
- `--dsconf C` — `--dsconf C`.
- One of `--setup PATH-OR-TAG` or `--code TAR`. Bare SimJob tags
  (`MDC2025af`, etc.) resolve to
  `/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/<tag>/setup.sh`.
- One of: generator pair `--run-number R --events-per-job N`, or
  input-driven pair `--inputs LISTFILE --merge-factor M`.

Optional:

- `--dsowner U` (default `$USER`)
- `--auxinput=N:KEY:LISTFILE` (repeatable, exact upstream spelling)
- `--no-smoke` — skip the post-build `mu2ejobfcl --index 0` check
- Any other upstream flag (e.g. `--outdir`, `--override-output-description`,
  `--samplinginput=`, `--auto-description`, `--verbose`) passes through

### `inspect` subcommand

Runs `mu2ejobfcl` against an existing cnf. Equivalent to the wiki's
"Inspect fcl file content based on index" and "based on output
filename" examples.

Required:

- `--jobdef CNF.tar` — local path (absolute or cwd-relative) to an
  existing cnf tarball. The skill does NOT resolve dataset names to
  PNFS paths — use `samweb` / `fcldump --dataset` if you only have a
  dataset name.
- Exactly one of `--index N` or `--target FILENAME`.

Optional:

- `--default-proto P` (default `root`)
- `--default-loc L` (default `tape`)
- Any other upstream flag passes through verbatim

## Examples

```
# 1. Wiki "Examples" §1 — resampling
samweb list-definition-files sim.mu2e.CosmicDSStopsCORSIKALow.MDC2020ab.art \
       | head -n 10 > CosmicDSStop.txt
cat > template.fcl <<EOF
#include "Production/JobConfig/cosmic/S2Resampler.fcl"
#include "Production/JobConfig/cosmic/S2ResamplerLow.fcl"
outputs.PrimaryOutput.fileName: "dts.owner.CosmicCORSIKALow.version.sequencer.art"
EOF
/mu2ejobdef-fcl build --template template.fcl \
                      --desc CosmicCORSIKALow --dsconf MDC2020ab \
                      --setup MDC2020aa --dsowner mu2e \
                      --run-number 1203 --events-per-job 500000 \
                      --auxinput=1:physics.filters.CosmicResampler.fileNames:CosmicDSStop.txt

# 2. Wiki "Examples" §2 — generator job (no inputs)
echo '#include "Offline/CRVResponse/test/wideband/wideband4modules.fcl"' > template.fcl
echo 'source.module_type: EmptyEvent' >> template.fcl
/mu2ejobdef-fcl build --template template.fcl \
                      --desc CosmicWidebandCRY --dsconf MDC2020ae_best_v1_3 \
                      --setup MDC2020ae --dsowner mu2e \
                      --run-number 103001 --events-per-job 10000

# 3. Inspect by index (wiki "Inspect fcl file content based on index")
/mu2ejobdef-fcl inspect --jobdef cnf.mu2e.CosmicCORSIKALow.MDC2020ab.0.tar --index 1

# 4. Inspect by target output filename (wiki "based on output filename")
/mu2ejobdef-fcl inspect \
   --jobdef /pnfs/mu2e/persistent/datasets/phy-etc/cnf/mu2e/CeMLeadingLogMix1BBTriggered/MDC2020am_best_v1_3/tar/ed/d3/cnf.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020am_best_v1_3.0.tar \
   --target mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020am_best_v1_3.001210_00000570.art

# 5. Local-dir inputs (wiki "Using a local undeclared input")
/mu2ejobdef-fcl inspect --jobdef cnf.oksuzian.CosmicCRYAllOffSpillTriggered.MDC2020ai_perfect_v1_3.0.tar \
                        --index 0 \
                        --default-proto file --default-loc dir:/exp/mu2e/data/users/$USER/test3
```

## Instructions

You are given `$ARGUMENTS`. First positional token must be `build` or
`inspect`.

### Common to both

1. **Source the Mu2e environment + mu2egrid.** In a single Bash:

   ```bash
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
   source /cvmfs/mu2e.opensciencegrid.org/artexternals/setups
   setup mu2egrid
   ```

   After this, `which mu2ejobdef` and `which mu2ejobfcl` should
   resolve under `/cvmfs/.../mu2egrid/v8_03_02/bin/`.

2. **Forward all unknown flags verbatim.** The user is on the upstream
   path because they want it raw. Don't rewrite `--option=value` to
   `--option value` or vice versa — pass through exactly as typed.

### `build` subcommand

3. **Parse build args.** Extract `--template`, `--setup`/`--code`,
   `--no-smoke`, and `--dsowner` (default `$USER`). Everything else is
   `EXTRA`.

4. **Validate template.** `--template T.fcl` must exist and be
   non-empty. Exit otherwise.

5. **Resolve `--setup`.** If the value contains `/cvmfs/` and ends in
   `.sh`, pass as-is. If it's a bare tag (no `/`), expand to
   `/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/<tag>/setup.sh` and
   verify the file exists. Error out if not. If `--code` was used
   instead, validate the tar exists.

6. **Build.** Run:

   ```bash
   mu2ejobdef \
     [--setup <RESOLVED_SETUP> | --code <TAR>] \
     --dsconf <DSCONF> --dsowner <OWNER> --desc <DESC> \
     --embed <TEMPLATE> \
     <EXTRA>
   ```

   `mu2ejobdef` enforces the run-number-vs-inputs exclusivity itself;
   let it surface its own error if violated. On success the tar lands
   in cwd (or wherever `--outdir` points) named
   `cnf.<owner>.<desc>.<dsconf>.0.tar`.

7. **Smoke** (unless `--no-smoke`). Run:

   ```bash
   mu2ejobfcl --jobdef cnf.<owner>.<desc>.<dsconf>.0.tar \
              --index 0 \
              --default-proto root --default-loc tape
   ```

   Print the resulting fcl to stdout. Flag any line containing
   `mu2ejobfcl: error`, an unsubstituted `{desc}` / `{sequencer}` /
   `description` placeholder in an output filename, or an empty
   `source.fileNames: []` for non-generator jobs. Do not delete the
   cnf even on smoke failure — the user may want to inspect it.

8. **Report.** Print: cnf absolute path, smoke pass/fail/skipped,
   suggested next step:

   ```
   /mu2ejobsub-submit cnf.<owner>.<desc>.<dsconf>.0.tar --firstjob 0 --njobs 3
   ```

   (Same composition as `/jit-cnf-build` — both end in a
   `/mu2ejobsub-submit` cluster.)

### `inspect` subcommand

3. **Parse inspect args.** Require `--jobdef CNF`; require exactly one
   of `--index N` or `--target T`. Defaults: `--default-proto root`,
   `--default-loc tape` (unless user overrode).

4. **Validate the cnf path.** Must exist on the local filesystem. If
   the user gave a dataset name (`cnf.mu2e.X.Y.tar` without a
   directory), refuse with a hint: "this skill takes a local path;
   use `fcldump --dataset <name>` to resolve a dataset → cnf via
   prodtools' search infra."

5. **Run mu2ejobfcl:**

   ```bash
   mu2ejobfcl --jobdef <CNF> \
              [--index N | --target T] \
              --default-proto <PROTO> --default-loc <LOC> \
              <EXTRA>
   ```

   Print stdout to the user. If `mu2ejobfcl` exits non-zero, surface
   stderr.

## Notes

- **No prodtools wrappers involved.** Unlike `/jit-cnf-build`, this
  skill never calls `bin/jobdef`, `bin/jobfcl`, or `bin/fcldump`.
  Useful for isolating upstream-vs-prodtools differences.
- **`mu2ejobdef`'s `--embed` vs `--include`.** `--embed` packs the
  template's contents into the tarball (resolves the wiki's typical
  workflow). `--include` keeps a reference instead. The wiki always
  uses `--embed`; this skill matches that. If you need `--include`,
  pass it as `EXTRA` and the skill won't override.
- **`mu2ejobfcl --target` is the wiki's "based on output filename"
  mode.** mu2ejobfcl parses the target filename to find which job
  index produces that output. Useful when you have a desired output
  artifact and want to know what fcl built it. Mirrors the prodtools
  `fcldump --target` behavior.
- **DbService overrides remain user responsibility.** Reco/digi/mix
  templates must set `services.DbService.{purpose,version}` per memory
  `reference_reco_dbservice_overrides.md`. The upstream `mu2ejobdef`
  doesn't inject these and neither does this skill.
- **For dataset → cnf resolution**, use `fcldump --dataset <name>`
  (prodtools) which now handles the suffixed-output case via two-pass
  search (see memory `reference_cnf_to_output_desc_mismatch.md`).
  Then pass the resolved cnf path here via `inspect --jobdef`.
- **Composition with `/mu2ejobsub-submit`.** Once you have a cnf from
  this skill's `build`, ship a cluster with
  `/mu2ejobsub-submit cnf.X.tar --firstjob 0 --njobs N`. Same
  downstream as `/jit-cnf-build`. Both skills produce the same cnf
  shape (modulo any parity gap), so they're interchangeable for the
  submission step.
