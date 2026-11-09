---
title: cnf registered in SAM before the input check (orphan cnfs)
tags: [incident, push_cnf, json2jobdef, enqueue, check_inputs, sam]
sources: [2026-11-08-run1a-col5-production-week]
updated: 2026-11-08
---

# cnf registered in SAM before the input check (orphan cnfs)

## Description

`json2jobdef --prod --enqueue`, and the `push_cnf` MCP tool that wraps it,
pushed the cnf tarball to SAM first and ran the enqueue checks
(`check_inputs`, code-tarball gate, njobs) second. The push cannot be undone
and a cnf name is never reused, so a failing check left a registered cnf
with no campaign. The check itself was correct — it printed "no campaign
created" — but the cnf was already in SAM.

It happened three times between 2026-10-31 and 2026-11-04:

| date | cnf | why the check failed |
|---|---|---|
| 2026-10-31 | `CeMLeadingLog` ntuple `.0.tar` cnfs (MDC2025ba) | inputs had files but no SAM definition yet (samDD cron made it ~50 min later): `unknown storage location 'N/A'` |
| 2026-11-03 | `PolyStops.MDC2025bb.0.tar` | `inloc: resilient` for an input with no resilient copy |
| 2026-11-04 | `CosmicSignalOffSpill-reco.MDC2025au_best_v1_1.0.tar` | the check crashed: `import mdh` under a Python 3.10 Musing panicked in the cryptography binding (fixed by PR #85, direct dCache REST) |

Each was recovered with `push_cnf(extend=True)`, which builds a `.1.tar`
under a fresh name; the orphan `.0.tar` stays in SAM unused.

**Fix: Mu2e/prodtools PR #88** (opened 2026-11-08). `preflight_entry` in
`utils/submit.py` holds every enqueue check and writes nothing;
`process_single_entry` runs it on the local cnf, then pushes, then creates
the campaign with the preflight's snapshot. `enqueue_entry` still runs the
preflight itself when no snapshot is passed. Test
`TestEnqueuePreflightBeforePush`: the old order records `['push', 'check']`,
the new one `['check']` on a failed check and `['check', 'push', 'create']`
on success.

**Still open:** when the cnf name already exists in SAM, the push is skipped
without comparing content, so a re-run after editing the entry checks the
local cnf but campaigns the stale SAM one. SAM holds size and an adler32
`enstore:` checksum, so the two could be compared.

## Appearances in Sources

- [[2026-11-08-run1a-col5-production-week]] — the week the fix was written
  and the PR opened

## Related

- [[2026-07-21-input-preflight-check]] — the gate that moved before the push
- [[2026-11-08-push-downstream-only-when-upstream-stopped]] — the operating
  rule that avoids the commonest trigger (pushing on a not-yet-ready input)
