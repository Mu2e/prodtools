---
title: Decision — grid workers run prodtools from a cvmfs release recorded on the campaign; the per-submission dev tarball is gone
tags: [decision, adr, prodtools, cvmfs, release, runjob, submit, reproducibility]
sources: []
updated: 2026-09-02
---

# Workers run prodtools from cvmfs releases

## Context
Since the direct backend was introduced, every submission bundled the
submitter's checkout (`utils/` + `bin/`) into `/tmp/prodtools-<user>.tar`,
shipped it per job via `-f dropbox://`, and `runjob.sh` untarred it on
the worker. The docstring said why: "avoids depending on a cvmfs-published
prodtools version that might not have our changes."

Meanwhile a real release channel existed and was ignored:
`/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/{v1.3.1 … v3.2.0}`,
`current → v3.2.0` (2026-08-20), published by `bin/install_prodtools.sh`
from GitHub tags of `Mu2e/prodtools`. By 2026-08-28 the working branch
was 39 commits past v3.2.0; every Run1Baw reco campaign, the MuCap1809
relaunch and the resolver fixes ran from the tarball, and nothing
recorded which code a given row ran. Row 362 and row 367 of campaign 94
ran different resolver code.

## Decision
- Every entry carries `prodtools_dir`, the **concrete** release dir
  (`current` resolved at enqueue, e.g. `…/bin/prodtools/v3.3.0`), set by
  `json2jobdef --enqueue` (default `current`, override
  `--prodtools-dir`). Slices and recoveries read it from the row, so a
  campaign runs one version for its whole life, and the ledger says which.
- The worker executable is that release's own `bin/runjob.sh`;
  `MU2EGRID_PRODTOOLS_DIR` tells it where the release is (jobsub copies
  the executable into the sandbox, so `$0` cannot). It sources the
  release's `bin/setup.sh` and execs its `utils/runmu2e.py`.
- **No second path.** No tarball, no checkout, no `else:` branch. A
  missing env var, a dir that is not a release, or a release whose
  `runjob.sh` predates the bootstrap is a loud error at the boundary
  where it is seen: `validate_entry_value('prodtools_dir')` at enqueue
  and `set-entry`, `prodtools_dir_of` at submit, `runjob.sh` on the
  worker. User: "I don't like fallbacks!"
- Testing unreleased worker code is a release-process question:
  `install_prodtools.sh --no-current vX.Y.Z-rc1` publishes a version
  without moving `current`; a test campaign pins it with
  `--prodtools-dir`.

## Consequences
- Worker-side fixes reach production only through a release:
  merge to `Mu2e/prodtools` main → tag → `install_prodtools.sh` on
  `cvmfsmu2e@oasiscfs.fnal.gov` → ~1 h propagation.
- Until a release containing the new `runjob.sh` is published, nothing
  can be enqueued: `current` (v3.2.0) is refused as "predates the cvmfs
  worker bootstrap". Deliberate — the alternative was silently sending
  jobs to a `runjob.sh` that untars a tarball nobody ships.
- Rows created before this change have no `prodtools_dir`; their
  recovery is refused until
  `submissions set-entry <campaign> prodtools_dir <dir> --include-open-rows`.
- Submissions no longer wait on RCDS publishing the tarball (30–60 s
  per submission in every tick log).
- A worker whose cvmfs catalog lags a fresh release fails in setup with
  a named error; recovery re-fires elsewhere.

## Amendment 2026-09-02 — dev checkout tarball restored as an explicit opt-in
The v3.3.1 grid smoke (g4bl entry mode) needed unreleased worker code
on the grid without a cvmfs publish. The mechanism from before this
decision was restored from `4314038^` with three changes that keep the
decision's substance:

- **Opt-in, not always-on.** `json2jobdef --enqueue --prodtools-dir
  <path>` with a path OUTSIDE `/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/`
  is a checkout. A cvmfs path behaves exactly as above. Refused for
  `mu2epro`: production still runs a published release only.
- **Snapshot with provenance.** `utils.submit.bundle_prodtools` tars
  the checkout's `bin/` + `utils/` ONCE at enqueue into
  `/exp/mu2e/data/users/<user>/prodtools/prodtools-tarballs/prodtools-<sha12>.tar`
  (not `/tmp`); the entry records `prodtools_tar` and
  `prodtools_ref={sha256,size,source_path}`. `jobdesc.prodtools_tar_of`
  re-hashes before every submit — slice, direct, recovery — and refuses
  a mismatch, the same gate `code_ref` applies to an Offline build.
  Answers "nothing recorded which code a given row ran".
- **Worker has exactly two paths, never both.** `jobsub_argv` emits
  `MU2EGRID_PRODTOOLS_TAR=<basename>` INSTEAD of `MU2EGRID_PRODTOOLS_DIR`
  and ships the tar via `-f dropbox://`; `runjob.sh` exits 1 when both
  or neither is set, extracts the tar under `$_CONDOR_SCRATCH_DIR`, then
  runs the same release check + setup + exec as the cvmfs path. The
  executable is still `<checkout>/bin/runjob.sh`, read live at submit —
  the one file not covered by the digest.

"Keep the tarball as a `run_as=self`-only dev path" below was rejected
in August because it "works when the env var is forgotten"; the
restored form cannot be reached by forgetting anything — only by naming
a non-cvmfs directory.

## Alternatives considered
- Keep the tarball as a `run_as=self`-only dev path: rejected in August —
  a second way of doing the same thing is a fallback, and it "works" when
  the env var is forgotten. Superseded by the 2026-09-02 amendment above
  (opt-in by explicit path, digest-pinned).
- Point at `current` rather than a version: rejected — `current` moved
  mid-campaign on 2026-08-20.

Related: [[2026-08-27-inloc-fallback-rendered-with-declared-proto]],
[[2026-08-28-corrupt-basket-dig-passes-integrity-gates]].
