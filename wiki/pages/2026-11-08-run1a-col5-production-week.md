---
title: Run 1A COL5 production week (campaigns 193–197)
tags: [run, mdc2025bb, run1a, col5, stragglers, recovery, push-order]
sources: [2026-11-08-run1a-col5-production-week]
updated: 2026-11-08
---

# Run 1A COL5 production week (campaigns 193–197)

**Source:** `wiki/raw/2026-11-08-run1a-col5-production-week.md` (session notes)
**Date ingested:** 2026-11-08
**Type:** run

## Summary

Michael MacKenzie's Run 1A requests at MDC2025bb moved through production in
the week of 2026-11-02. The COL5 μ⁻ flat-gamma primaries ran as campaigns
193 (`PolyFlatGammaCalo`, 200 jobs) and 194 (`PolyFlatGamma`, 1000 jobs) from
`sim.mu2e.MuminusPolyStopsCat.MDC2025bb.art`. 193 finished at 200 files,
12,572,330 events, 76.7 GB. Its Run 1A mix (`PolyFlatGammaCaloMix1BB` at
`MDC2025bb_best_v1_5`, 200 jobs, about 2.4 TB projected) was pushed as
campaign 197 only after 193 had stopped. The COL5 pion chain's first stage
(`PiPolyStops`, 500 jobs per era from `sim.mu2e.PiBeamCat.Run1Bah.art`) went
out as campaigns 195 (Run 1A, `MDC2025bb`) and 196 (Run 1B, `Run1Bbb`) once
Michael confirmed the higher Run 1B stop rate is expected (field off) and the
Mg-fit spectrum is acceptable for carbon for now.

Both flat-gamma campaigns stalled on stragglers: one index in 193 and four
in 194 ran past the 24 h wall-clock limit and were held (HTCondor hold code
26, `SYSTEM_PERIODIC_HOLD Run Time/limit`), while their siblings took about
2.5–2.8 h. A held job blocks its ledger row, so the tick does nothing until
a human acts. Removing the held proc and letting the next tick recover it
gave a same-seed resubmit at the recovery floor (4000 MB, 48 h). 193's
index 83 rerun finished in under 6.3 h; the first of 194's four reruns
landed within about 3.7 h. So these tails were the node (index 83 first ran
on fnpc8002 at 97% CPU for 24 h), not the seed: a same-seed retry is worth
one attempt.

The week also closed two prodtools defects. The push-order bug, where
`json2jobdef --prod --enqueue` registered the cnf in SAM before checking its
inputs, is fixed in PR #88 — see
[[2026-11-05-cnf-registered-before-input-check]]. The drain-check fix
(PRs #83 and #84: confirm a "drained" cluster with its own schedd before
recovering it) merged on 2026-11-08, and the production checkout moved from
the interim branch `drain-fix-live` back to `main`.

## Key Takeaways

- **Straggler workflow:** a hold with code 26 at the 24 h limit → remove the
  held proc (`/jobsub-rm <cluster>.<proc>`) → next tick sees the index
  missing and resubmits it at 4000 MB / 48 h (the floor applies because the
  entry names no memory or lifetime). Same seed, so it reproduces a
  physics-driven slowness — but node-driven tails clear.
- **Push a stage only after the stage before it has stopped** — see
  [[2026-11-08-push-downstream-only-when-upstream-stopped]]. The
  PolyFlatGammaCalo mix waited a day for one straggler instead of pushing on
  199/200 files.
- **`push_cnf` needs an absolute `json` path.** It runs `json2jobdef` under
  `ksu mu2epro` in a fresh `/tmp` workdir, so a repo-relative path fails at
  `load_json` — before anything reaches SAM, so the failure is harmless.
- **Sizes from smokes held up:** PolyFlatGammaCalo mix keeps about 36% of
  events at 537 kB each; its reco is 48 kB/event and ntuples 11 kB/event
  (entries in `data/mdc2025/{reco,evntuple}.json`, PR #87).

## Entities Touched

- [[2026-11-05-cnf-registered-before-input-check]] — the push-order incident,
  fixed by PR #88
- [[2026-11-08-push-downstream-only-when-upstream-stopped]] — the push rule
- [[2026-07-18-direct-recovery-loop]] — recovery floor and the
  "re-runs are deterministic" claim this week qualifies
- [[2026-07-21-input-preflight-check]] — the gate whose position moved before
  the push
- [[2026-08-20-jobwait-jobsub-history-drops-schedd]] — the earlier
  wrong-schedd bug; #83/#84 fix the tick's and jobwait's drain check in the
  same family

## Relation to Other Wiki Pages

Qualifies [[2026-07-18-direct-recovery-loop]]: a wall-clock tail does not
always fail the same way on every attempt — when the node is the cause, the
same-seed rerun succeeds. Extends [[2026-07-21-input-preflight-check]]: the
gate always blocked campaign creation, but until PR #88 it ran after the cnf
was already registered.
