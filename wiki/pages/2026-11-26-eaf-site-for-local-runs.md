---
title: EAF as a site for --once --local runs
tags: [eaf, runlocal, backend, decision]
sources: [hep-endpoints, mu2ewiki-eaf, eaf-probe-2026-11-26]
updated: 2026-11-27
---

# EAF as a site for `--once --local` runs

**Decision (2026-11-26):** `json2jobdef --once --local --site eaf` (MCP
`run_local(..., site="eaf")`) runs an entry's runlocal on the user's
Fermilab EAF CPU server instead of the gpvm. How it works is in
`utils/eaf.py`.

**Why it can work:** a gpvm takes one local mu2e job at a time; an EAF
CPU server (https://analytics-hub.fnal.gov) guarantees 8 cores (up to 32
when its node is idle) and 64 GiB. EAF mounts `/exp` read-write, the
same CephFS as the gpvms (the Mu2e wiki's "read-only" is wrong), and has
`/cvmfs`, so the run directory is shared and nothing is copied. It has no
`/pnfs` mount and no grid token, so inputs must be `inloc` `none` or
`dir:/exp/...`. The CPU profile can only be read from a hand-started
server's `user_options`; the spawn form refuses the API token.

**Verified live 2026-11-26:** `POT_Run1_a` (SimJob MDC2025bb), 8 jobs x
20 events: launch confirmed in about 40 s, 8/8 ok at 171 s each,
`run_status` done, server stopped itself. Run
`cnf.oksuzian.POT_Run1_a.MDC2025bb.0`.

## Alternatives considered

- **Depend on RobMina/hep-endpoints**, where the idea came from: one
  handle per job, state in `$HOME`, GPU profile hard-coded. Copied the
  idea, not the code.
- **ssh to EAF:** there is none; the hub API is the only way in.
- **Ask the hub for liveness in run_status:** would put a token and
  network calls into the read-only server. Files on /exp answer the
  same question from any gpvm.

## Open

- EAF's idle-cull timeout and maximum server age are not measured; a run
  that outlives either dies with the server (run_status then reads
  `unknown`).
- `/pnfs` inputs would need a token on EAF; that is a security decision
  for SCD, not ours.
- EAF policy on long unattended runs: ask eaf-users before heavy use.

Related: [[nersc-iri-feasibility]], [[2026-10-08-mc1-slurm-runlocal-smoke]].
