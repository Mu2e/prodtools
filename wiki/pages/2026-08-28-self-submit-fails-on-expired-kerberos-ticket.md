---
title: Self-account grid submit fails at condor_vault_storer when the kerberos ticket is expired — bearer token stays fresh
tags: [incident, credentials, kerberos, jobsub, self-submission, ledger-guards]
sources: []
updated: 2026-08-28
---

# 2026-08-28 self-submit: condor_vault_storer exit 256 with a fresh bearer token

A grid acceptance test as user `oksuzian` (`run_as="self"`, campaign 3 in
`/exp/mu2e/data/users/oksuzian/prodtools/submissions.db`, cnf
`cnf.oksuzian.MCPAcceptance.MCPTest003.0.tar`, 2 jobs × 50 POT events)
failed twice at `jobsub_submit` before succeeding. The failure was a
credential problem, not a prodtools defect; the ledger guards behaved
exactly as designed.

## Symptom

`jobsub_submit` got through RCDS publish and sandbox upload, then:

```
Failed to process job credential requests (1): 'process_job_credentials():
'/opt/jobsub_lite/bin/condor_vault_storer' failed: exit code 256'; BAILING OUT.
```

It exited **0** with no cluster id. prodtools treated that as failed
(row marked `failed`, campaign `paused`), which is correct — no orphan
cluster, nothing to clean up.

## Why the usual token checks passed

- `/run/user/$UID/bt_u$UID` was fresh (written minutes earlier) with full
  `compute.*` and `storage.modify` scopes.
- `htgettoken -a htvaultprod.fnal.gov -i mu2e` reported `succeeded` — it
  reused the still-valid **vault** token and never needed kerberos.
- `condor_vault_storer` takes a different path: it must obtain a new token
  *for the schedd's credd*, which authenticates to vault with **kerberos**.
  `klist` showed the ticket expired 08/26 with `renew until` also past —
  the auto-renew cron can only extend inside the 7-day window.

Diagnosis command (harmless — stores nothing on failure):

```
/opt/jobsub_lite/bin/condor_vault_storer -v mu2e
# → Kerberos init failed: GSSError ... Ticket expired.
```

## Fix and recovery

1. Interactive `kinit` (the only step needing a human).
2. `submissions reconcile <ROW>` for each failed reservation (7 and 8 here;
   `jobsub_q --user oksuzian` confirmed the window was empty first).
3. `submissions resume <ID>`, then one `run_submissions(run_as="self")`
   tick → cluster `71759961@jobsub03`, ledger row 9 attached.

Verification after drain: `sim.oksuzian.Beam.MCPTest003.art` and
`sim.oksuzian.Neutrals.MCPTest003.art` each 2 files at
`/pnfs/mu2e/scratch/datasets/usr-sim/sim/oksuzian/...`, 2 logs at
`log.oksuzian.MCPAcceptance.MCPTest003.log`; the tick reported
`row 9: complete (2 indices)`.

## Takeaways

- `klist` is the first check for a self-account submit failure, before
  any token inspection — a fresh bearer token proves nothing about vault
  storage.
- `jobsub_submit` exit 0 + no cluster id is the same signature for this
  as for other submit-side failures; the ledger's "no new active row"
  evidence rule is what catches it.
- The `era_mismatch_ok` entry key is required when a test dsconf like
  `MCPTest003` is paired with a real Musing — `validate_era_agreement`
  refuses otherwise, before any SAM push.
- Related: [[2026-08-02-draining-smoke-input-push-incident]] (the other
  credential-scope incident, on the worker side).
