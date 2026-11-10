---
title: justIN vs prodtools — workflow-model comparison
tags: [reference, justin, workflow, architecture]
sources: [dunejustin-docs]
updated: 2026-11-08
---

# justIN vs prodtools — workflow-model comparison

**Source:** https://dunejustin.fnal.gov/docs/ (fetched 2026-07-07,
docs v01.06.01). Facts marked *(docs)* were verified against the live
pages that day; unmarked mechanics are from general knowledge of the
DUNE stack and should be re-verified before relying on them.
**Context:** SAM retirement trajectory + Mu2e's MetaCat/Rucio moves
(see [[metacat-reference]], the Dec-2025 computing-organization diagram
in `docs/`). If Mu2e adopts the DUNE stack, justIN is the natural
workflow layer — this page maps its concepts onto prodtools'.

## What justIN is

DUNE's workflow system — the workflow layer of the Rucio + MetaCat
stack, successor to SAM-project-style file delivery.

- A **workflow** (request) contains **stages**; each stage has a
  jobscript, resource requirements, and output patterns *(docs)*.
- Jobs are **generic**: they land on a worker first, then fetch work.
  The jobscript calls `$JUSTIN_PATH/justin-get-file`, which returns
  `DID PFN RSE` — one file per call, chosen from the optimal storage
  for that site; empty output = no work left *(docs)*.
- Inputs come from **MetaCat queries** (`--mql`); no-input MC stages
  are declared with `--monte-carlo N` *(docs, tutorial index)*.
- **File state machine**: unallocated → allocated → processed. The
  jobscript lists finished files in `justin-processed-dids.txt` /
  `justin-processed-pfns.txt`; allocated-but-unlisted files are
  automatically reset to unallocated for another job to pick up
  *(docs)*. Non-zero jobscript exit suppresses output upload *(docs)*.
- **Outputs** are matched by `--output-pattern` (and
  `--output-pattern-next-stage` for chaining into the next stage),
  uploaded via Rucio, with MetaCat metadata supplied as a `<file>.json`
  sidecar *(docs)*.
- Env contract: `$JUSTIN_WORKFLOW_ID`, `$JUSTIN_STAGE_ID`,
  `$JUSTIN_SECRET`, `$JUSTIN_PATH` *(docs)*. CLI: `justin` (+
  `justin-fetchlogs`); dashboard at dunejustin.fnal.gov *(docs)*.

## Concept map

| prodtools | justIN | note |
|---|---|---|
| POMS map / `jobdefs_list.json` | workflow (request) | both are the campaign-submission unit |
| map entry | stage | 1 entry ≈ 1 stage |
| cnf tarball (frozen fcl + input lists) | jobscript (+ cvmfs code) | justIN has no payload artifact that freezes inputs |
| `fname=etc.mu2e.index.NNN` / `$PROCESS` → index | `justin-get-file` at runtime | index→work vs drain-a-queue |
| `input_data` + SAM query, frozen at build | `--mql` MetaCat query, allocated just-in-time | the core inversion (below) |
| `inloc` + protocol table | Rucio replica affinity / RSE choice | justIN picks per-file, per-site; prodtools per-entry |
| `mkrecovery` + index definitions | file-state auto-reset | recovery is a property, not a tool |
| `pushOutput` + SAM declare | `--output-pattern` + Rucio upload + MetaCat sidecar | |
| `outloc` per dataset | destination RSE / Rucio rules | |
| Mu2eName grammar, predictive naming | jobscript's responsibility | justIN has no system-level name grammar |
| completeness = nfiles vs njobs | stage file counters on dashboard | |
| `--extend` + version suffix | just run the workflow again (unprocessed files drain) | |

## The core inversion

prodtools freezes per-job inputs at cnf build time: index → exact
inputs → exact outputs, byte-reproducible fcl, deterministic sequencers
(see [[predictive-naming-proposal]] — prediction and frozen-by-definition
lists only make sense in this model). justIN freezes nothing: files are
allocated to whichever generic job asks next, so a job's contents — and
therefore any per-job output identity — are emergent, not predictable.
"Input files define output filenames" survives per *file*, not per
*job*.

Consequences:

- **justIN has what the direct path lacks**: the submit→track→recover
  loop (see [[2026-04-29-remove-poms-from-submit-loop]] — the planned
  `utils/recover.py` + tracking DB were never built; the file-state
  machine makes them unnecessary). Per-file retry with no recovery
  tooling is the single biggest operational win.
- **prodtools has what justIN doesn't**: deterministic job identity and
  byte-reproducible payloads. Reproducing one specific output file, or
  predicting a campaign's exact output names before running, has no
  justIN equivalent.

## n→1 (merge) under justIN — exists, but non-deterministic

Multi-file jobs are a jobscript-side loop: *"the user's jobscript may
execute justin-get-file multiple times and be allocated multiple input
files"* (file_processing.md, fetched 2026-07-07). No documented per-job
file limit or grouping flag; group size is jobscript discipline.

The file→job **partition is not deterministic**, on three grounds:
1. allocation is a live queue — files go to whichever job asks next,
   with the *"suggested PFN and RSE"* chosen by replica locality (so
   grouping depends on where jobs landed and when they started);
2. concurrent jobs of a stage interleave their allocations;
3. the retry machinery reshuffles by design — on failure *"all of the
   input files allocated to this job are reset to unallocated"* (≤6
   attempts) and are picked up by *other* jobs. One transient failure
   re-partitions files across merge outputs.

Deterministic: the processed *set* (MQL result, if the dataset is
frozen) and per-output parentage (MetaCat sidecar). So justIN offers
**provenance after the fact** where prodtools offers **prediction
before the fact** (partition = sorted frozen list ÷ merge factor,
output sequencer = first input of slice — see
[[predictive-naming-proposal]] n→1 section). Physics-harmless for MC
concatenation; a different regime when the merged file is a citable
artifact (Cat datasets referenced by name in downstream configs,
recovery by expected-vs-produced name comparison).

## Poor fits

- **Mixing**: a Mu2e mix job consumes a structured set (~90 aux inputs
  across 4 pileup catalogs with deterministic MaxEventsToSkip slices
  per index). One-file-at-a-time draining doesn't express this — and
  per the n→1 section above, even plain merges get non-deterministic
  groupings. A mixing stage under justIN would still need a
  prodtools-like payload that carries the aux-input structure.
- **Byte-stability guarantees**: prodtools' worker fcl is verified
  byte-identical across refactors; justIN jobscripts have no analogous
  contract.
- **Generator accounting**: prodtools generator njobs is a declared
  campaign size (tbs.njobs); justIN MC stages use `--monte-carlo N` —
  similar, but sequencer/run-space management stays payload-side.

## Plausible Mu2e adoption shape

justIN takes submit/track/recover (replacing submit_map + mkrecovery +
index definitions + the never-built direct-mode tracking DB); prodtools'
center of gravity shifts to what justIN doesn't do: deterministic
payload building — cnf/fcl generation (`jobfcl` inside the jobscript),
the Mu2eName grammar, config validation, mixing structure. The
[[2026-07-03-file-resolver-and-sam-query-plan]] seam matters here:
`samweb_wrapper` is the only SAM path, so a MetaCat/Rucio port touches
one module, not forty call sites.

## Open questions

- Is justIN usable outside DUNE (Mu2e VO support, separate instance)?
- How would Mu2e's dCache tape/persistent layout map to RSEs and who
  operates the Rucio rules?
- Mixing under JIT allocation: per-job structured allocation would need
  justIN-side support or stay frozen in a payload — which?
- Does per-file allocation cost (one call per file × 90 inputs/job)
  matter at Mu2e mixing scale?

## Related

- [[predictive-naming-proposal]] — the determinism this model trades away
- [[2026-04-29-remove-poms-from-submit-loop]] /
  [[2026-04-30-phase2-direct-jobsub-implementation]] — the in-house
  submit path justIN would subsume
- [[metacat-reference]] — the metadata half of the DUNE stack
- [[poms-reference]] — the system justIN replaces in spirit
- [[2026-11-08-push-downstream-only-when-upstream-stopped]] — why prodtools waits for a stage to stop rather than push on a partial input and `--extend` later (the frozen-cnf-list trade-off above)
