---
title: beamkit — g4bl thin client over prodtools
tags: [reference, g4bl, beamkit, mcp, decision, smoke]
sources: [2026-09-03-beamkit-design]
updated: 2026-09-05
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

Implemented on branch `v1`, 53 commits, 222 tests. Seven MCP tools:
`run_beamline`, `make_recoveries`, `beamline_status`, `list_beamline_runs`,
`beamline_outputs`, `make_beamfile`, `get_server_info`. Module map and
diagrams live in the repo at `docs/architecture.md`; the domain vocabulary
in `CONTEXT.md`.

An architecture pass on 2026-09-04 (`1ff5d29..848206f`) fixed five bugs
and re-homed three rules:

- **A run past its last slice was unrecoverable through beamkit.** prodtools
  marks a campaign `complete` when its last slice is submitted, rows still
  verifying, and refuses a scoped tick on it. `make_recoveries` now reads
  the campaign state from the ledger and runs the bare tick once it is not
  `active`; the result names which form ran. The first smoke (campaign 6)
  was already in that state.
- **A failed `push_cnf` whose campaign exists is adopted**, not orphaned:
  the record takes the campaign id in state `created` and `make_recoveries`
  submits it. Before, the retry burned the next dsconf.
- prodtools rc=2 is now run state `needs_attention`; every caller input is
  refused before the deck fetch and SAM probe; the campaign's `njobs` from
  `push_cnf` is what `missing_indices` is measured against.
- `identity.py` is the one home for what `run_as` means and the only reader
  of `BEAMKIT_PRODTOOLS_DIR`; `publishing.py` holds the beam-file
  link/push/unwind; `naming.py` holds every Mu2e name. `make_beamfile` has a
  `label` (files and SAM artifact) distinct from `flavor` (cuts).
- `tests/test_bridge_contract.py` binds every bridge call to the real
  prodtools signatures when `BEAMKIT_PRODTOOLS_ROOT` names a checkout.

A simplify pass the same day (`dcca7da`, `2226e6b`) took src from 1314 to
1231 lines and tests from 1907 to 1806 without dropping behaviour: one
`BeamkitError` base replaces thirteen catch-and-rewrap blocks; `server.py`
registers the `tools.py` functions directly (FastMCP builds the schema from
the annotations, so every tool parameter is annotated and `run_as` is
required on the writing tools), which retired a 148-line AST test; the
record lost `sweep_id`, `beamfile_in` and `prodtools_datasets`; the contract
probe now pins the three facts beamkit copies from prodtools (worker g4bl
params, outloc vocabulary, dot-name grammar). Left in place on purpose: the
`publish=True` path (~100 lines) that waits on a prodtools `push_file`.

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

## Second grid smoke (2026-09-05): release path, green end to end

prodtools v3.3.2 was tagged at `49b3b57` and installed on cvmfs the same
day (`current -> v3.3.2`, worker files byte-identical to main). With
`BEAMKIT_PRODTOOLS_DIR` unset, `run_beamline(tag="G4blSmoke",
deck_ref=e470313, params={"epsMax": "0.01"}, njobs=3, events_per_job=10,
run_as="self")` produced run `G4blSmoke.e470313-001` (the dsconf collided
with the first smoke and took `-001`, as designed), campaign 7, row 13,
cluster 30101196 on jobsub05, entry pinned to
`/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/v3.3.2`. All three jobs
exited 0 in 200-483 s. `make_recoveries` ran the bare tick (campaign
already `complete`), verified row 13 against SAM; `beamline_outputs`
listed 3 nts files (237 kB) at scratch; `make_beamfile(flavor="bm")`
built `beamfiles/G4blSmoke.e470313-001.bm.txt`: 175 rows in, 26 out
(97 dropped by `drop_pdg`, 44 by `min_p_mev`, 6 backward, 2 duplicate),
pot 30, no missing indices. First beam file from grid output; every
beamkit tool has now run against the real prodtools release.

Before it, smoke campaign 6 (row 12) was closed with `submissions cancel
6 --close-rows`, which needed the fix on branch `close-rows-on-complete`:
the flag refused a `complete` campaign because `complete -> cancelled` is
not a ledger transition, and every one-slice campaign has that shape.

Related: [[g4bl-runner]], [[g4bl-epsmax-deck-gap]].
