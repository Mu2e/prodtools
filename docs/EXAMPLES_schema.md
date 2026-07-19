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
newcomers. Assume familiarity with `art`, `FCL`, `SAM`, `POMS`, and the
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
   `json2jobdef` copies them from the JSON config into the map entry
   verbatim, and cross-reference the `submit_map` subsection for the
   precedence rule.
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
7. **Production Execution (`runmu2e`)** — role of `fname`
   env var, `etc.mu2e.index.NNN.NNNNNNN.txt` format, dry-run flag.
8. **Sequential vs. pseudo-random auxiliary input selection** — the
   `tbs.sequential_aux` flag.
9. **FCL overrides** — `fcl_overrides` dict, how template + `--embed`
    works, that base FCL stays unexpanded.
10. **Parity Tests** — `test/parity_test.sh` usage.
11. **Additional Tools** — one subsection per script in `bin/` that has
    user-facing CLI: `pomsMonitor`, `famtree`,
    `logparser`, `genFilterEff`, `datasetFileList`, `listNewDatasets`,
    `latestDatasets`, `mkrecovery`, `jobquery`,
    `submit_map`, `recover`, `copy_to_stash`. Ops scripts
    (`install_prodtools.sh`, `update_pomsmonitor_web`, `recover_cron`)
    get a one-line mention. Each subsection: one-line purpose, 1–3 example invocations,
    key flags. Enumerate from the current `bin/` directory — add any new
    script found there, remove any that no longer exist. (`runjob.sh` is
    a worker bootstrap, not user-facing — omit.)

    - `submit_map` must cover `--enqueue` and `--slice-size` (campaign
      registration for the sliced-submission top-up phase; direct
      backend only; mutually exclusive with
      `--first`/`--num`/`--indices`/`--indices-file`; submits nothing).
    - `recover` must cover `--max-queued`, `--pause-campaign`,
      `--resume-campaign`, `--cancel-campaign`, and note that `--status`
      also prints the resolved queue cap in effect for the top-up phase.
12. **Troubleshooting** — only entries that correspond to real error
    messages produced by current code. Remove stale ones.

## Tribal knowledge to preserve (non-derivable from code)

Include these verbatim or equivalent — they are NOT derivable from
reading the code:

- `muse setup SimJob` is optional for most tools; only `muse setup ops`
  is required.
- The `etc.mu2e.index.000.NNNNNNN.txt` filename in `fname` encodes the
  job index — the seventh-field `NNNNNNN` (the **sequencer**) is the
  job index, zero-padded to 7 digits. `mkrecovery` writes these as
  `etc.mu2e.index.000.{idx:07d}.txt`. The `000` field is a fixed
  description placeholder, not the index.
- `inloc` accepts `disk`, `tape`, `scratch`, `resilient`, `stash`,
  `none`, or `dir:<path>` (locally-mounted FS, e.g. cvmfs). There is no
  `auto`. `resilient` reads via xrootd, `stash` reads via CVMFS, and
  `dir:` reads via direct POSIX (the `file:` protocol is forced).
- Random sampling seed is derived from `(owner, desc, dsconf, dataset,
  count, njobs)` — same inputs always produce the same file selection.
- The per-job seed is `baseSeed = 1 + cnf index` (flat — no version, run,
  or dsconf term). To extend a dataset's statistics, reuse the existing
  tarball at fresh indices via a `firstjob` window: a POMS-map entry with
  `"firstjob": F, "njobs": M` runs cnf indices `[F, F+M)` (fresh seeds
  `F+1..`, fresh sequencers). Do NOT bump `version`/`run` for a
  same-input expansion — that restarts the cnf index at 0 and duplicates
  physics. Only open-ended cnfs (no `tbs.njobs` cap) can be windowed past
  their original count; closed cnfs are capacity-checked.
- Parity tests validate byte-for-byte equivalence against the Perl
  `mu2ejobdef` reference implementation.
- `pomsMonitor` database default path is `poms_data.db` at the repo root
  (`db_analyzer.get_default_db_path`).
- `genFilterEff` output is Proditions-compatible (`TABLE
  SimEfficiencies2`).
- `famtree` auto-excludes `etc*.txt` files from diagrams.
- Every successful `submit_map --backend direct` submission is recorded
  in the submission ledger (default
  `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, env
  `MU2E_SUBMISSION_DB`); `recover` drain-checks via jobsub_q, verifies
  outputs against SAM, and resubmits only missing indices (attempt cap,
  then `exhausted` for a human). POMS-backend stages are never in the
  ledger — POMS owns their recovery (`mkrecovery`).
- Optional per-entry resource keys `"memory"` / `"disk"` /
  `"expected_lifetime"` (jobsub-format strings, e.g. `4000MB` / `50GB` /
  `48h`) live in the POMS-map entry itself, or in the jobdef JSON config
  that produces it — `json2jobdef` copies them into the entry verbatim.
  Precedence at submission is CLI flag > entry key > built-in default
  (`2000MB` / `30GB` / `24h`). The *effective* values are frozen into
  the ledger row / campaign row snapshot at submission time, so a later
  recovery or cron-fed slice reproduces exactly what the jobs originally
  ran with — a CLI `--memory` no longer silently downgrades to the
  built-in default on resubmit.
- Sliced campaigns: `submit_map --enqueue` snapshots map entries into
  the campaigns table and submits nothing; `recover`'s top-up phase
  (runs after its recovery pass, inside the same hourly cron tick) then
  feeds whole slices to active campaigns, round-robin oldest-first,
  while total mu2epro idle+running jobs stay under a cap resolved as
  `--max-queued` flag > `MU2E_MAX_QUEUED` env > `10000` built-in
  default. A submit failure during top-up pauses the campaign rather
  than blind-retrying; an operator investigates and issues
  `--resume-campaign`.
- Every direct-backend submission attempt — manual, cron-fed slice, or
  recovery resubmit — appends a block to `submit-YYYYMMDD.log` beside
  the ledger DB (one file per UTC day, plain appends, no rotation).

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
