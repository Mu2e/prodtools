---
title: Sim epochs — moved to its own repository
tags: [decision, sim-epochs, catalog, retirement, provenance]
sources: [slack-dm-ray-2026-07-30, slack-dm-ray-2026-09-02, slack-groupdm-sophie-2026-07-13, sim-epochs-demo-mcp-2026-02-19]
updated: 2026-09-11
---

# Sim epochs — moved to its own repository

**Status (2026-09-11): sim-epochs lives at
<https://github.com/oksuzian/sim-epochs>, to be transferred to
`Mu2e/sim-epochs`. Nothing of it remains in prodtools.** This page is
the pointer; the design, glossary, ADRs and the record of every
decision are in that repository (`docs/design.md`, sections 1–21;
`CONTEXT.md`; `docs/adr/0003`–`0006`). Keeping a second copy here is
the drift this tool was built to end, so the copy was removed.

## What it is, in one paragraph

A catalog that answers, from SAM parentage and on every run: which
simulation datasets exist, which one to use (`current` / `stale` /
`superseded`), what each was made with, and what can be retired
together. A person writes one three-line JSON per digitization
campaign (name, purpose, standing); everything else is derived. One
rule came out of it, ADR 0006: the newest name is safe to use, so a
remade parent obligates the downstream remake before a round is
complete; `epochs gaps` exits 1 while one is owed.

## How it got here

- 2026-09-02..04: designed and built on a prodtools branch as
  `utils/epochs`, 63 commits, 165 tests, PR #58.
- 2026-09-05: cut out with `git filter-repo` (history preserved;
  `sim_epochs/graph.py` carries 21 commits), a 126-line `mu2e.py`
  replacing the four prodtools imports (name grammar, cnf tarball
  reader, four SAM calls, location-to-path). Output byte-identical to
  the prodtools build on production SAM. Reasons: the users are Sophie
  and Ray, not operators; the tool never runs on a worker; the only
  runtime dependency is `samweb_client` from `muse setup ops`.
- 2026-09-11: PR #58 closed unmerged. Prodtools `main` never carried
  the code.

## What prodtools still owes it

- **ADR 0003 (in the new repo): declare the cnf as a SAM parent.**
  `runmu2e` writes `parents_list.txt` from the input files only, so a
  new output's cnf is found through `data/epochs/cnf_index.json`
  instead of parentage. Appending the cnf name to `parents_list.txt`
  makes the generation route direct for every new dataset. Not done.
- **The era guard already exists.** `json2jobdef` refuses a dsconf
  whose campaign letters disagree with `simjob_setup` unless the entry
  says `era_mismatch_ok`. The catalog's audit found 13 `-KL` recos
  named `Run1Baw_best_v1_5` whose cnf pins `SimJob/Run1Baq`, and 7
  cnfs (5 MDC2025af, 1 MDC2025ar) named `v1_3` built on DbService
  `v1_1`; all predate the guard.
- **`latestDatasets` stays.** Same first step (newest dsconf per
  description); epochs adds the parent check, families, holds, gaps
  and the delete list. `--emit` for chain entries has no counterpart
  in epochs.
