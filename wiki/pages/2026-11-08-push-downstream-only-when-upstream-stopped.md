---
title: Push a downstream cnf only after its input campaign has stopped
tags: [decision, push_cnf, chains, extend, campaign-order]
sources: [2026-11-08-run1a-col5-production-week]
updated: 2026-11-08
---

# Push a downstream cnf only after its input campaign has stopped

## Description

**Decision (2026-11-08, Yuri Oksuzian):** "we should be pushing jobdefs only
on datasets that stopped running." A downstream stage (mix, reco, ntuples,
the next resampler) is pushed only when the campaign producing its input has
stopped: no running, idle or held jobs in the queue, and every ledger row
verified. A SAM definition existing is not enough.

**Context.** Campaign 193 (Run 1A `PolyFlatGammaCalo`) sat at 199/200 files
for a day while one index ran into the 24 h limit. The proposal was to push
the mix on the 199 landed files and catch up the last one later with
`push_cnf(extend=True)`.

**Alternative rejected: push on a partial input, then `--extend`.** A cnf
freezes its input file list when it is built. Pushing early splits one
sample over two cnfs and two campaigns (`.0.tar` plus an `--extend`
`.1.tar`), leaves completeness to later bookkeeping, and builds on an
upstream that is still moving — recoveries can still land or fail.

**Consequence.** Waiting costs wall-clock time when one straggler holds a
whole stage. The answer to that is to deal with the straggler (remove the
held job, let a tick recover it — see
[[2026-11-08-run1a-col5-production-week]]) or to close the index on purpose,
not to push around it. Draining campaigns (generic ntuple cnfs that pick up
files as they land) are the deliberate exception: they bake no input list.

**How to check:** `campaign_status` for the upstream campaign shows
`queue.running == idle == held == 0` and `rows.active == 0`.

## Appearances in Sources

- [[2026-11-08-run1a-col5-production-week]] — the 193 → 197 mix push that
  waited for index 83

## Related

- [[2026-11-05-cnf-registered-before-input-check]] — pushing on a
  not-yet-ready input is what most often tripped that bug
- [[2026-07-18-direct-recovery-loop]] — how stopped rows are verified
