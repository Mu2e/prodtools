---
title: beamkit — g4bl thin client over prodtools
tags: [reference, g4bl, beamkit, mcp, decision, smoke]
sources: [2026-09-03-beamkit-design]
updated: 2026-09-04
---

# beamkit

`beamkit` is a separate repo (`oksuzian/beamkit`, local checkout
`/exp/mu2e/app/users/oksuzian/beamkit`) that drives G4beamline production
through prodtools. It is a **thin client**: it owns no queue, no catalog and
no submission state. prodtools owns all three, and the g4bl runner itself
(`_run_g4bl_job`, `_build_g4bl_tarball`) stays in prodtools because worker
code reaches grid nodes only through the prodtools cvmfs release. See
[[g4bl-runner]] for that runner.

What beamkit adds is the layer prodtools has no opinion about: a reproducible
pin of the deck, a name derived from that pin, the entry JSON composed from
it, and a beam-file builder for the ntuples a run produces.

## Status (2026-09-04)

Implemented on branch `v1`, 33 commits, 181 tests. Seven MCP tools:
`run_beamline`, `make_recoveries`, `beamline_status`, `list_beamline_runs`,
`beamline_outputs`, `make_beamfile`, `get_server_info`. Module map and
diagrams live in the repo at `docs/architecture.md`.

One prerequisite is still unbuilt: **prodtools has no `push_file`**, so
`make_beamfile(publish=True)` is refused up front by
`bridge.push_file_available()`. Everything else runs.

## What prodtools had to grow for it

`push_cnf` gained an optional `prodtools_dir` parameter
(`mcp/src/prodtools_mcp_write/tools.py`, commit 1df204e on `code-tarball`).
It is appended to the `json2jobdef` argv as `--prodtools-dir`, which tars the
dev checkout and ships it to every worker. It is **refused outright for
`run_as="mu2epro"`** — deliberately stricter than `json2jobdef` itself, which
only refuses a *non-cvmfs* directory for mu2epro. beamkit sets it from the
environment variable `BEAMKIT_PRODTOOLS_DIR`.

This exists because cvmfs `current` (v3.3.0) has no g4bl runner. Once v3.3.1
is installed, the variable should be unset and runs should take the release.

## Conventions worth knowing

- **dsconf is the deck sha7.** `run_id = <tag>.<dsconf>`. A `-NNN` suffix is
  allocated only when the unsuffixed cnf name is already in SAM — never
  speculatively.
- **Output is `nts.<owner>.<tag>.<dsconf>.root`.** Note that prodtools'
  `push_cnf` returns the *glob* `nts.*.root`, because `json2jobdef` copies the
  `outloc` key verbatim into `outputs[].dataset`; beamkit resolves the real
  name itself and keeps the glob under a separate key. Anything reading
  `outputs[].dataset` from a g4bl entry gets a glob, not a name.
- **Beam files never wait for completeness.** `make_beamfile` builds from
  whatever nts files exist; `pot = n_files × events_per_job` and the missing
  indices go in the sidecar.
- **Nothing advances on its own.** `make_recoveries` runs one prodtools tick,
  and that recovery pass is ledger-wide — under `run_as="mu2epro"` it advances
  every other active production campaign too.

## First grid smoke (2026-09-04)

`G4blSmoke.e470313`, cnf `cnf.oksuzian.G4blSmoke.e470313.0.tar`, campaign 6,
ledger row 12, cluster 93589068, 3 jobs at 10 events, 184-259 s each. Run as
self with the dev prodtools tarball.

**Plumbing passed, physics failed for a deck reason.** Deck pinning, cnf build
and registration, campaign creation, submission, the log push and status
read-back all worked. All three jobs then exited 1 on
`G4Exception Geometry001 / SetMaximumEpsilonStep`, because upstream
G4BeamlineScripts at `e470313` carries no `epsMax` param and Geant4 rejects
g4bl's 0.05 default — see [[g4bl-epsmax-deck-gap]]. The runner handled it
correctly: skipped the data push, still pushed the logs to
`/pnfs/mu2e/scratch/datasets/usr-etc/log/oksuzian/G4blSmoke/e470313/`.

A green run needs either `params={"epsMax": "0.01"}` or the deck fix committed
upstream and re-pinned.

Related: [[g4bl-runner]], [[g4bl-epsmax-deck-gap]].
