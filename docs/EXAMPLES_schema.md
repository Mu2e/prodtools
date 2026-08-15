# EXAMPLES.md schema

This file is the source of truth for `EXAMPLES.md`. The `/refresh-examples`
slash command reads this schema + the current code and regenerates
`EXAMPLES.md` from scratch. Edit this file to change the shape, tone, or
preserved caveats of the generated doc. Do not hand-edit `EXAMPLES.md` —
changes will be overwritten on the next refresh.

## Purpose

`EXAMPLES.md` is a usage reference for the Python-based Mu2e production
tools. A reader with an active Mu2e environment should be able to copy any
command from it and have it run.

## Audience

Mu2e collaborators running production workflows — mix of experts and
newcomers. Assume familiarity with `art`, `FCL`, `SAM`, and the
`mu2e` executable. Do not assume familiarity with this repo's internals.

## Tone

Terse and example-first. Every command block is a real invocation the
reader can paste. Explanations follow the commands, not the other way
around. No marketing language, no "powerful", no emojis.

## Sources of truth

When regenerating, read in this order:

1. **This schema** (sections, caveats, anti-patterns below).
2. **CLI surface** — `argparse` definitions in `utils/*.py` and
   `bin/*` entry points. Every documented flag must exist in the current
   code. Every command must be runnable as written.
3. **Module docstrings** in `utils/*.py` and `bin/*`. Treat docstrings as
   canonical for tool purpose.
4. **JSON config schemas** under `data/` — enumerate the keys actually
   consumed by `utils/config_utils.py`, `utils/mixing_utils.py`,
   `utils/prod_utils.py`. Do not invent keys.
5. **Existing tests** under `test/` for working examples.
6. **Recent git log** — `git log --oneline -20` for context on new
   features that may not yet have examples.

## Required sections (in order)

1. **Environment Setup** — cvmfs source line, `muse setup ops`, optional
   `bin/setup.sh`, what becomes available.
2. **Overview** — two bullet lists: core production tools, analysis /
   diagnostic tools. One line each.
3. **Creating Job Definitions (`json2jobdef`, `jobdef`)** — JSON-based
   (recommended) and direct `jobdef` invocations. Cover stage-1, resampler,
   mixing shapes, and the optional per-entry resource keys `"memory"` /
   `"disk"` / `"expected_lifetime"` (jobsub-format strings) — mention that
   `json2jobdef` copies them from the JSON config into the submission
   entry verbatim, and cross-reference the `submissions` subsection for
   how a live campaign's resource keys are retuned after creation
   (`set-memory`, `set-entry`) and how an unset key earns a recovery a
   floor. There is no operator-facing CLI flag for these keys — the
   entry (JSON config, or a live-campaign edit) is the only way to set
   them. Cover `--enqueue` and `--slice-size N` (default 1000):
   `--enqueue` pushes the cnf to SAM and registers a sliced-submission
   campaign directly in the submission ledger. `--prod` REQUIRES
   `--enqueue`, and `--enqueue` requires `--prod`. There is no
   `--jobdefs` flag: `json2jobdef` writes no file recording the
   campaign at all.
4. **Random sampling in input data** — the `{"count": N, "random": true}`
   form and its deterministic-seed guarantee. Mention the optional
   `"max_nfiles": M` cap inside the same nested-dict value (positive int;
   non-random branch slices `sorted(files)[:M]`; random branch bounds
   `total_needed`; `njobs` is NOT auto-recomputed).
5. **FCL Generation (`jobfcl`, `fcldump`)** — from jobdef tarball, from
   dataset name, from target output filename. Include `--local-jobdef`.
6. **Mixing Jobs** — JSON schema with `pileup_datasets` list-of-dict form,
   automatic mixer mapping. Do not use the legacy `*_dataset` / `*_count`
   split form.
7. **Production Execution (`runmu2e`)** — direct-worker-only entry
   point: it refuses to run without `MU2EGRID_JOBDEF`, resolves its job
   index from `$PROCESS` via the ops JSON's `jobs` table, and carries
   that index internally in a synthesized `fname` (sequencer field).
   Cover the dry-run flag. Do not document a `fname=...` invocation —
   the POMS `--jobdesc` mode was removed 2026-08.
8. **Sequential vs. pseudo-random auxiliary input selection** — the
   `tbs.sequential_aux` flag.
9. **FCL overrides** — `fcl_overrides` dict, how template + `--embed`
    works, that base FCL stays unexpanded.
10. **Parity Tests** — `test/parity_test.sh` usage.
11. **Additional Tools** — one subsection per script in `bin/` that has
    user-facing CLI: `famtree`,
    `logparser`, `genFilterEff`, `datasetFileList`, `listNewDatasets`,
    `latestDatasets`, `jobquery`,
    `submissions`, `check_inputs`, `copy_to_stash`, `runlocal`. Ops scripts
    (`install_prodtools.sh`, `submissions_cron`)
    get a one-line mention. Each subsection: one-line purpose, 1–3 example invocations,
    key flags. Enumerate from the current `bin/` directory — add any new
    script found there, remove any that no longer exist. (`runjob.sh` is
    a worker bootstrap, not user-facing — omit.)

    - `submissions` — name, one-line role ("direct-submission subsystem
      CLI — status/run/pause/resume/cancel/complete/reconcile/resubmit"),
      the verb table
      (`status` is the default/read-only verb; `run` with `--dry-run`/
      `--row`/`--max-attempts`/`--max-queued`; `pause CAMP_ID [--note
      TEXT]`; `resume CAMP_ID`; `cancel CAMP_ID`; `complete CAMP_ID
      [--note TEXT]` — the operator close-out for a draining campaign;
      `set-slice CAMP_ID N` and `set-memory CAMP_ID MEM` — retune a live
      campaign's slice size / memory request for its remaining slices;
      `set-entry CAMP_ID KEY VALUE [--include-open-rows]` — set one of
      inloc/memory/disk/expected_lifetime on a live campaign. Without
      the flag it reaches future slices only; with it, also the
      not-yet-closed rows, which is what makes RECOVERIES use the new
      value; `reconcile ROW_ID [--note TEXT]` — close a ledger row stuck
      in `failed`/`submitting` after the operator has checked jobsub_q
      by hand and confirmed the window is genuinely free, so it stops
      blocking a campaign slice; `resubmit ROW_ID (--indices SPEC |
      --indices-file F | --files F) [--dry-run]` — hand re-fire a named
      set of indices or input files from an existing ledger row as a
      child submission (attempt+1); the entry comes from the row, so
      there is nothing to hand-edit. REFUSES when an unsettled row for
      the same tarball already covers part of the selection, naming the
      blocking row and pointing at `reconcile`; `--files` is only valid
      against a draining (file-keyed) row and `--indices`/
      `--indices-file` only against an index row — using the wrong one
      is refused by name), the global `--db` flag, the read-only guarantees (`status` and
      `run --dry-run` take no lock and submit nothing), and the
      extended exit-2 list for `run`: held,
      exhausted, child-missing, campaign paused (submit failure),
      campaign paused (crash-window overlap), queue-count failure,
      lingering paused campaign (repeats every tick until a human
      resumes or cancels it).
    - `check_inputs` — one-line role ("pre-flight check that a
      campaign's input files are readable before jobs launch: resilient
      pileup present and correctly sized vs SAM, tape inputs staged /
      not NEARLINE"). Cover: it is read-only and exits 2 on any problem
      (never remediates — points at `/prestage` for NEARLINE inputs);
      the `--inloc` flag (default `resilient`); accepts one or more cnf
      tarballs; and that `json2jobdef --enqueue` runs it automatically
      as a gate (a failing entry blocks with no campaign created). Note
      it needs no mu2epro — it is a status check, safe to run as
      yourself.
12. **Troubleshooting** — only entries that correspond to real error
    messages produced by current code. Remove stale ones.

    - The duplicate-campaign entry (`active`/`paused` campaign already
      exists for `<tarball>`) must recommend `submissions cancel <ID>`
      ONLY — never "pause then re-enqueue" (pausing does not free the
      tarball; a paused campaign is refused exactly like an active one,
      and even if it weren't, two campaigns feeding the same index space
      is the double-submit bug this guard exists to prevent). It must
      also warn that re-enqueueing the same tarball after `submissions
      cancel <ID>` starts the new campaign's cursor at 0 — it has no
      memory of what the cancelled campaign already fed — so the
      operator should check `submissions status` / the ledger for that
      tarball first, to avoid re-submitting already-covered indices.

    - `submissions resubmit` error catalog: `no ledger row <N> in
      <db>` (bad row id); `refusing — row <N> (state=...) already
      covers part of this selection` (an unsettled row for the same
      tarball overlaps the requested indices/files — point at
      `submissions reconcile <N>` after the operator confirms via
      jobsub_q); `row <N> is a draining (file-keyed) row — use
      --files, not --indices` and the inverse, `row <N> is an index
      row — use --indices, not --files` (selector must match the
      row's kind).

## Tribal knowledge to preserve (non-derivable from code)

Include these verbatim or equivalent — they are NOT derivable from
reading the code:

- `muse setup SimJob` is optional for most tools; only `muse setup ops`
  is required.
- The `etc.mu2e.index.000.NNNNNNN.txt` filename in `fname` encodes the
  job index — the seventh-field `NNNNNNN` (the **sequencer**) is the
  job index, zero-padded to 7 digits. The `000` field is a fixed
  description placeholder, not the index. Since the POMS backend was
  removed (2026-08) `fname` is no longer an operator-set env var: the
  worker synthesizes it from the `$PROCESS`-resolved index, and the
  same sequencer-carries-the-index rule still applies internally.
- `inloc` accepts `disk`, `tape`, `scratch`, `resilient`, `stash`,
  `none`, or `dir:<path>` (locally-mounted FS, e.g. cvmfs). There is no
  `auto`. `resilient` reads via xrootd, `stash` reads via CVMFS, and
  `dir:` reads via direct POSIX (the `file:` protocol is forced).
- `outloc` values accept `tape`, `disk`, `scratch`, `outstage`. The
  first three are pushOutput actions — each copies the file to its
  dataset path AND declares it to SAM. pushOutput has no
  copy-without-declare mode (`dosam` is set unconditionally), and its
  `scratch` action is a fully declared dataset that merely lives on
  scratch. `outstage` is prodtools' own: the worker copies the file to
  `$MU2EGRID_WFOUTSTAGE/$CLUSTER/$PROCESS` via ifdh and declares
  nothing — for test and study runs whose output should stay out of
  SAM. The log follows the data there (a declared log would name
  undeclared parents). An outstage entry CANNOT be enqueued as a
  campaign: verify_row is fail-closed against SAM, so with nothing
  declared every index reads as missing and each tick would recover the
  whole row forever. Build it and submit it by hand.
- `runlocal` runs cnf jobs on the current node, several at a time, and
  pushes NOTHING: no pushOutput, no SAM declare, no manifest. It shares
  the worker's own prep (`runmu2e.process_jobdef`), so a local run
  exercises the same tarball fetch, inloc handling and `--copy-input`
  staging the grid will; only the push tail is absent. Each job runs as
  a child process in its own `job_<index>/` directory — `process_jobdef`
  works in cwd and its copy-input branch runs `mkdir indir; mv *.art
  indir/`, so jobs sharing a directory would move each other's files.
  Its `--first`/`--num` are cnf indices directly (`baseSeed = 1 + index`,
  `firstSubRun = index`); there is no `firstjob` second index space.
  `--indices 0,3,7-9` names those indices one by one instead of a window
  — for rerunning exactly the jobs a grid pass lost, which are rarely
  contiguous. Ranges are inclusive at both ends, and the two forms are
  alternatives: passing `--indices` together with `--first`/`--num` is
  refused rather than silently clipped.
- Random sampling seed is derived from `(owner, desc, dsconf, dataset,
  count, njobs)` — same inputs always produce the same file selection.
- The per-job seed is `baseSeed = 1 + cnf index` (flat — no version, run,
  or dsconf term). To extend a dataset's statistics, reuse the existing
  tarball at fresh indices via a `firstjob` window: a JSON config entry
  with `"firstjob": F, "njobs": M` runs cnf indices `[F, F+M)` (fresh seeds
  `F+1..`, fresh sequencers). Do NOT bump `version`/`run` for a
  same-input expansion — that restarts the cnf index at 0 and duplicates
  physics. Only open-ended cnfs (no `tbs.njobs` cap) can be windowed past
  their original count; closed cnfs are capacity-checked.
- Parity tests validate byte-for-byte equivalence against the Perl
  `mu2ejobdef` reference implementation.
- `genFilterEff` output is Proditions-compatible (`TABLE
  SimEfficiencies2`).
- `famtree` auto-excludes `etc*.txt` files from diagrams.
- Every successful direct-backend submission (`json2jobdef --enqueue`,
  a cron-fed slice, or a `submissions resubmit`) is recorded in the
  submission ledger (default
  `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`,
  env `MU2E_SUBMISSION_DB`); `submissions run` drain-checks via jobsub_q,
  verifies outputs against SAM, and resubmits only missing indices
  (attempt cap, then `exhausted` for a human). Legacy POMS-launched
  entries are never in the ledger — a POMS-launched job (or one
  submitted via the upstream `mu2ejobsub`/`mu2eg4bl` CLIs) simply never
  passed through the direct backend; the POMS backend was removed
  2026-08 (legacy stages recover from the `pre-poms-removal` git tag).
- `direct_input` entries are not index-submittable — they run as
  draining batches (`submissions resubmit ROW_ID --files LIST.txt`). The
  `template` and `g4bl` runner modes were deleted with the POMS backend
  (2026-08, tag `pre-poms-removal`); g4bl and HPC submission go through
  the upstream `mu2ejobsub`/`mu2eg4bl` CLIs, which never touch the
  submission ledger.
- `json2jobdef` writes NO file recording the campaign and has no
  `--jobdefs` flag. Never show one, and never show
  `/exp/mu2e/app/users/mu2epro/production_manager/{poms_map,direct_maps}/`
  — those directories have no consumer. A production campaign is one
  command: `json2jobdef --prod --enqueue [--slice-size N]`.
- `--prod` requires `--enqueue`, and `--enqueue` requires `--prod`: the
  campaign's cnf must be in SAM, because enqueue resolves the tarball
  from there. A bare `--prod` would push the cnf and register nothing.
- The campaign's `origin` column (ledger `campaigns` table) records
  free-text provenance — for a `json2jobdef --enqueue` campaign, the
  config path plus desc/dsconf (`<config>.json#<desc>@<dsconf>`); for a
  `submissions resubmit`, `recovery of row <N>`. Nothing dispatches
  from it; the MCP status tools and `submissions status` just echo it
  back.
- Bulk `json2jobdef --dsconf X --prod --enqueue` (no `--desc`) processes
  every matching entry in one loop, and every refusal inside
  `enqueue_entry` is a `sys.exit`, so a failure partway through leaves
  campaigns registered for the entries before it and nothing for the
  entries after — bulk mode is not resumable as a whole. Re-running the
  identical bulk command then dies immediately on the FIRST entry's
  duplicate-live-campaign guard ("active campaign already exists" —
  this is the double-submit guard working correctly, not ledger
  corruption). Recovery is per-entry: re-run the failed and remaining
  entries individually with `--desc <D> --dsconf <C> --prod --enqueue`.
- `set-entry --include-open-rows` is OFF by default because an UNSET
  `memory` is what earns a recovery the 4000MB floor; cascading a memory
  value forfeits it. An `inloc` fix normally wants the flag ON.
- Optional per-entry resource keys `"memory"` / `"disk"` /
  `"expected_lifetime"` (jobsub-format strings, e.g. `4000MB` / `50GB` /
  `48h`) live in the jobdef JSON config entry — `json2jobdef` copies
  them into the ledger entry verbatim on `--enqueue`. There is no
  operator-facing CLI flag for these; the entry key (set in the JSON
  config, or retuned on a live campaign via `submissions set-memory` /
  `set-entry`) is the only way to set them, and it always wins over the
  `2500MB` / `30GB` / `24h` built-in default. The *effective*
  values are frozen into the ledger row / campaign row snapshot at
  submission time, so a later recovery or cron-fed slice reproduces
  exactly what the jobs originally ran with.
  The memory default sits above mu2egrid's `2000MB` on purpose: Mu2e
  primaries measure just over that line (VmHWM 2266 MB for
  `PiTargetStops`, 2377 MB for the RPC primaries), so entries that named
  no memory key were OOMing. Note the trade: naming the key at all
  forfeits the `4000MB` recovery floor, which applies only when the key
  is absent — so prefer leaving it unset unless the entry genuinely
  needs more than the default.
- `inloc` and the resource keys are validated at EVERY door into the
  ledger — `json2jobdef --enqueue` (where a campaign is born) and
  `submissions set-entry` (editing a live campaign) — by one shared
  validator, `jobdesc.validate_entry_value`.
  Document the refusals in the troubleshooting catalog: a misspelled
  `inloc` does NOT fail at runtime, it silently falls through to SAM,
  which is why it is refused at the boundary instead.
- A bulk `json2jobdef --dsconf X --prod --enqueue` that SKIPS any entry
  (invalid value, missing required field) exits 2 and lists what was
  skipped. Entries that already processed are left alone — they are in
  SAM and in the ledger.
- Sliced campaigns: `json2jobdef --enqueue` snapshots the entry into
  the campaigns table and submits nothing; `submissions run`'s top-up
  phase (runs after its recovery pass, inside the same hourly cron tick)
  then feeds whole slices to active campaigns, round-robin oldest-first,
  while total mu2epro idle+running jobs stay under a cap resolved as
  `--max-queued` flag > `MU2E_MAX_QUEUED` env > `5000` built-in
  default. A submit failure during top-up pauses the campaign rather
  than blind-retrying; an operator investigates and issues `submissions
  resume <ID>`. `--enqueue` refuses a second campaign for the same
  tarball while an `active` OR `paused` one exists — a paused campaign
  still owns its index space, so "pause then enqueue" is not a
  workaround (only `submissions cancel <ID>` frees the tarball, and
  re-enqueueing after cancel restarts the cursor at 0 — see the
  troubleshooting entry). Before every slice, top-up also checks the
  ledger for indices already covering the slice's window (any state —
  proves a submission happened even if the campaign row's cursor advance
  was lost to a crash); an overlap pauses the campaign with a
  crash-window note instead of resubmitting.
- Every submission attempt — manual, cron-fed slice, or recovery
  resubmit — appends a block to `submit-YYYYMMDD.log` beside the ledger
  DB (one file per UTC day, plain appends, no rotation).
- Draining campaigns: a JSON config entry with `input_pattern` (a
  5-field dataset pattern, `%` wildcards) and NO `njobs` drains a
  growing dataset 1:1 through a generic cnf, rather than a fixed index
  range; enqueue with the same `json2jobdef --desc D --dsconf C --prod
  --enqueue --slice-size N` path as an indexed campaign —
  `enqueue_entry` detects the draining shape from `input_pattern` and
  registers a file-keyed campaign instead of an index-keyed one.
  Optional entry keys: `exclude_desc` (exact desc matches to skip),
  `min_age_minutes` (default 60, SAM `create_date` age gate before
  a file is eligible), `prestage` (default false, opt-in tape recall
  for tape-only candidates).
- `submissions resubmit ROW_ID --files LIST.txt` submits one
  direct-input job per filename listed against a draining ROW (pick a
  row id for the campaign's tarball from `submissions status`, e.g. an
  `exhausted` one) — the file is written by the `submissions run` tick
  (parked files) and this is the operator path for re-dispatching them.
  Mutually exclusive with `--indices`/`--indices-file`, and refused
  against an index-keyed row (row kind — index vs draining — is fixed
  by whether the row's entry carries `input_pattern`).
- `submissions complete <id>` is the operator close-out for a draining
  campaign — it never auto-completes, because the input set keeps
  growing until the upstream production finishes, and only the
  operator knows when that point has been reached.
- Draining campaigns track pending work in SAM, not a cursor: pending
  = inputs whose expected outputs (computed per-file from the cnf's
  own `job_outputs` mapping) don't exist yet, minus files already
  in-flight or parked. Nothing counts as done until its output exists.
- A draining entry's `outputs[].dataset` globs must be tier-specific
  (`mcs.*.art`, never `*.art`): a glob that matches the input pattern
  is refused at enqueue, because the worker's push manifest would
  otherwise have declared the fetched input copy as an output —
  pushOutput then tries to delete the production input at its own
  dataset path (2026-08-02 smoke incident; the worker also excludes
  its inputs as the authoritative defense).
- Workers stream inputs via xroot by default (POMS-era parity). A JSON
  config entry sets `"copy_input": true` to stage inputs locally via mdh
  instead — worth it only for descs with fat runtime tails, where a
  mid-job xroot drop wastes the most CPU. The entry key wins over the
  worker's `--copy-input` CLI flag; `stash`/`resilient`/`dir:` inlocs
  always stream regardless.

If any of the above stops being true, update this list — do not leave a
stale caveat in the regenerated doc.

## Rules for examples

- Every JSON config snippet must round-trip through the current
  `json2jobdef` loader without error. If unsure, shell out and verify.
- Every CLI flag shown must appear in the current `argparse` for that
  tool. When in doubt, read the source — do not guess.
- Prefer campaign names that appear in current files under `data/` (as
  of the regen). Do not use a campaign name if no JSON under `data/`
  references it.
- File paths in examples must follow the current Mu2e naming convention:
  `tier.owner.description.dsconf.sequencer.extension`.
- Keep one canonical example per feature. Do not show five variants of
  the same invocation.

## Anti-patterns (do not include)

- Speculative future features ("coming soon", "planned").
- Commands that were true for a past release but not the current code.
- Internal implementation details unless they affect the user (e.g.,
  "uses ThreadPoolExecutor" — only mention if the user sees the effect).
- Benchmarks or performance numbers (these rot fastest).
- References to `mu2e_poms_util` (old package name). The current package
  is `utils/` under `prodtools/`.

## Output constraints

- File goes to `EXAMPLES.md` at repo root. Overwrite entirely.
- Use GitHub-flavored markdown. Code blocks must carry a language tag
  (`bash`, `json`, `python`, `fcl`).
- Section numbering must be contiguous — no gaps (the current
  `EXAMPLES.md` jumps from 5 to 7; the regen must fix this).
- No footer claiming what commit produced this file — git already tracks
  that.
