---
title: Retire the POMS backend
tags: [decision, submission, poms, direct-backend, retirement]
sources: [docs/superpowers/specs/2026-08-08-poms-removal-design.md]
updated: 2026-08-08
---

# Decision: Retire the POMS backend

**Date:** 2026-08-08
**Type:** ADR
**Status:** Implemented

## Decision

Remove the POMS submission backend from prodtools entirely: dispatch
(`runmu2e --jobdesc` / `_dispatch_and_execute`), recovery
(`mkrecovery` + SAM index definitions), and monitoring
(`poms_db`/`db_builder`/`db_analyzer`/`pomsMonitor` +
`web/pomsMonitor`). The direct backend (`submit_map --enqueue` +
`submissions run`, specs 2026-07-18/19) is the only submission path.

~2,800 lines deleted; the SQLAlchemy dependency (and the pyenv-ana
requirement for monitoring tools) is gone.

## Escape hatch

Git tag `pre-poms-removal` (immediately before the first deletion
commit) reaches every deleted file. A legacy POMS stage needing a
recovery: scratch-checkout the tag, run `mkrecovery` from there.
In-flight POMS jobs were never at risk — workers execute the tarball
shipped at submit time.

## Alternatives considered

- **Migrate remaining map-033 POMS stages to direct first**: cleanest
  end state, rejected for the extra migration/resubmission work before
  any deletion.
- **Wait for map-033 to drain**: zero risk, rejected because removal
  would block on campaign timelines.
- **Deprecate-then-delete**: rejected — the tag already provides
  rollback and prodtools has a single operator.

## What deliberately stays

- `utils/map_entry.py` (ex-`poms_entry.py`): the submission-map entry
  grammar, shared by the direct backend.
- `validate_jobdesc` / `process_jobdef` in runmu2e: called by the
  direct worker path.
- `process_g4bl_jobdef` and `process_template` in runmu2e: g4bl and
  template-mode machinery, not POMS. Both are now uncalled in-repo —
  their only caller was the POMS dispatch tail — so a follow-up
  decides whether the upstream mu2eg4bl path and the template workflow
  still want them.
- The `poms_map/` directory name and numbered-map convention
  (external, mu2epro area).
- pushOutput's `_POMS` suffix in `Dataset.Tag` (external tool).

## Operational decommission (separate from the repo commits)

1. Repoint mu2epro's datasetMon crontab entry at a slim script with
   only the original `inspect_datasets.py` loop
   (`/exp/mu2e/app/home/mu2epro/cron/datasetMon/inspect_datasets.py`
   is external and survives); the three dashboard-refresh steps
   (db_builder / build_lineage / render_static) die with the repo code.
2. The synced web checkout at
   `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools/` is never synced
   again; web admins may delete it later.
3. `/web/.../data/poms_data.db` and the published static dashboard
   stay frozen at their last render.
