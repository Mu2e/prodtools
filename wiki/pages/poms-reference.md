---
title: POMS reference (FNAL Production Operations Management System)
tags: [reference, infra, poms]
sources: [2026-04-28-poms-architecture, 2026-04-28-poms-sam-wiring, 2026-04-28-poms-user-ops]
updated: 2026-07-11
---

# POMS reference

Production Operations Management System — Fermilab's workflow orchestrator
above SAM, jobsub_lite, and art. Code: https://github.com/fermitools/poms.
Mu2e instance: https://poms.fnal.gov.

This page synthesizes a 2026-04-28 research pass into POMS internals.
Verified claims are stated plainly; **unverified** items are flagged.
For the original raw research see the three `raw/` sources in
frontmatter.

## What POMS is (and isn't)

- **Is:** an orchestrator that wraps `jobsub_lite` (HTCondor submission),
  `samweb` (data cataloging), and the `art` framework. Adds campaign
  management, multi-stage dependencies, and automated recovery.
- **Is not:** SAM (file metadata + queries), jobsub_lite (low-level
  submission), or art (event processing framework).

## Data model

```
Campaign  ──┬── CampaignStage ──┬── Submission ──┬── Job
            │                   │                ├── Job
            │                   │                └── Job
            │                   ├── Submission ──── ...
            │                   ├── JobType (template)
            │                   ├── LoginSetup (auth)
            │                   └── (SAM dataset name, dispatch params)
            │
            └── CampaignStage ──── ...
```

| Entity | What it carries | Where in repo |
|---|---|---|
| **Campaign** | Top-level workflow container; experiment, default params | `webservice/CampaignsPOMS.py` |
| **CampaignStage** | One processing step. JobType ref, LoginSetup ref, **SAM dataset name** (the def POMS iterates), split strategy, completion criteria | `webservice/StagesPOMS.py` |
| **Submission** | A single launch of a stage. Tracks N jobs from creation → Located/Failed | `webservice/SubmissionsPOMS.py` |
| **JobType** | Reusable execution template (script, params, recovery cfg). Snapshotted at submission time for reproducibility | `webservice/MiscPOMS.py` |
| **LoginSetup** | Auth + launch host config | `webservice/MiscPOMS.py` |
| **DataDispatcherSubmission** | Bridge to Data Dispatcher when a stage uses DD instead of SAM | `webservice/DMRService.py` |

Schema: `ddl/poms_ddl.sql`. ORM: `webservice/poms_model.py`.

## Dispatch lifecycle

1. **User triggers a launch** — Web UI button, `poms_client` call, or
   POMS-internal cron.
2. **Submission created** — POMS writes a row tied to the CampaignStage
   with snapshotted JobType + parameters; status `New`.
3. **Jobs submitted** — `poms_jobsub_wrapper` calls `jobsub_lite` for
   each file in the stage's SAM def. One Condor job per file.
4. **Worker runs** — Worker gets `fname` env var = the assigned SAM
   file. Runs the JobType's executable (e.g., `runmu2e --jobdesc <map>`).
5. **POMS polls** — `submission_agent` daemon hits LENS every ~120s,
   updates job/file status.
6. **Completion** — `wrapup_tasks` flips submission to `Located` (all
   files delivered) or `Failed`.
7. **Recovery / cascade** — Configured recoveries re-dispatch failures.
   Once Located, downstream stages auto-launch.

## Recovery machinery (verified from source, 2026-07-11 research pass)

Recoveries are attached to the **JobType** (`campaign_recoveries`:
job_type_id, recovery_type_id, `recovery_order`, param_overrides), not to
the stage. After `wrapup_tasks` marks a submission Completed/Located it
calls `launch_recovery_if_needed` (SubmissionsPOMS.py ~1765): walk the
JobType's ordered recovery list from `submission.recovery_position`,
build each type's recovery dataset, and launch the FIRST one with
nfiles > 0 (`dataset_override=`, chained via `recovery_tasks_parent`).
Dependents launch only when no recovery fires.

Recovery types and the SAM dimensions they build
(`SAMSpecifics.create_recovery_dataset` ~60-154):

| Type | Dimension logic | Checks outputs exist? |
|---|---|---|
| `consumed_status` | snapshot minus consumed (`co%`) | **NO** — consumption bookkeeping only |
| `proj_status` / `process_status` | SAM `recovery_dimensions` REST endpoint per project/process | **NO** |
| `delivered_not_consumed` | delivered/skipped/unknown/... states | **NO** |
| `added_files` | defname minus snapshot (new files since launch) | **NO** (different purpose) |
| `pending_files` | snapshot **minus `isparentof:`(outputs matching version + create_date > submission)**, nested `output_ancestor_depth` deep | **YES** — SAM child declaration (not physical location) |

**The Run1Ban incident, at config level**
([[2026-07-05-run1ban-mix-recovery-data-loss]] L1): every type except
`pending_files` re-dispatches parents whose outputs exist whenever SAM
consumption state is incomplete (process killed after copyback but
before `setStatus consumed`, project ended early, re-snapshot under a
fresh project). Mu2e's MDC2020-era JobTypes configure
`recoveries = [["process_status", mem=4GB], ["process_status", mem=8GB]]`
(`Production/CampaignConfig/mdc2020_jobtypes.ini`) — i.e. the
non-verifying kind. **POMS-native mitigation: configure the recovery
chain to use `pending_files`** (child-existence guard;
`output_ancestor_depth=1` is already the Mu2e default). The MDC2025
JobType's recovery config lives only in the POMS DB (no .ini) — check
it in the web UI before the next drain campaign.

## Split types and completion

`webservice/split_types/`: byexistingruns, byrun, draining, drainingn,
limitn, list, mod, multiparam, new, nfiles, stagedfiles (+ parallel
`dd_split_types/` for Data Dispatcher).

- **`draining`** is a NO-OP in POMS: peek/next/prev all return
  `campaign_stages.dataset` unchanged, forever, no state ("assumes you
  have a draining/recursive definition"). All exclusion logic lives in
  the SAM definition text — POMS contributes nothing. What counts as
  "already done" is whatever the def says (typically `minus
  consumed_status consumed`), i.e. consumption, not output existence.
- `drainingn(n)` is the snapshot-cursor variant (source-verified
  2026-08-01): `cs_last_split` holds a **cumulative snapshot id** of
  everything ever delivered. `peek()` creates a persistent SAM
  definition `<dataset>_slice_<ls>_stage_<n>` with dims
  `defname:<base> minus snapshot_id <ls> with limit <n>` (0 files →
  StopIteration); `next()` snapshots the slice, unions it with the old
  cumulative via a `<dataset>_full_<ls>_stage_<n>_<unixtime>`
  definition, and stores the union's snapshot id as the new cursor.
  Growth-safe and overflow-safe, but "delivered = was in a snapshot" —
  the cursor advances at LAUNCH, independent of consumption and of
  output existence; failed slices are never re-delivered by the split
  type (that's the separate recovery layer's job). Side effect: the
  `_slice_`/`_full_` definitions persist in SAM forever — Mu2e's 2022
  `drainingn(1000)` ntuple stage over
  `mcs.mu2e.CeEndpointMix1BBSignal.MDC2020r_perfect_v1_0.art` left
  them behind, still visible in `samweb list-definitions --defname
  '%_slice_%_stage_%'` in 2026. (The `develop`-branch file also has a
  stray-backtick syntax error and a broken `len()` — the deployed
  build must differ.)

Completion (`wrapup_tasks`): while Running, `pct_complete >=
completion_pct` promotes to Completed. `completion_type=complete`
finishes there; `located` additionally counts OUTPUT files (dims with
`availability physical`/`anylocation`, version match, `create_date >
submission time`) against `tot_consumed * completion_pct/100` — plus a
fallback that force-locates any submission older than **2 days**.
Statuses: New → Idle → Running → Completed → Located (terminal;
also Failed/Removed/Cancelled).

## Mu2e-specific conventions

The Mu2e instance runs the upstream POMS code but layers conventions:

### Dropbox path

POMS scans `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/`
for `*.json` map files. Each map describes the jobs to dispatch:
tarball name, `njobs`, `inloc`, `outputs`, plus runner-specific
fields like `runner: "g4bl"`. See [pbi-sequence-workflow](pbi-sequence-workflow)
for an art-side example.

### `i<map_stem>` SAM-def naming

The Mu2e tool **`mkidxdef --prod`** (in `prodtools/bin/mkidxdef`)
creates a SAM def named `i<stem>` from a map file's stem. e.g.,
`MDC2025-025.json` → `iMDC2025-025`. This is **a Mu2e tooling
convention, not a POMS hardcode.** POMS itself takes whatever SAM
def name is configured on the CampaignStage.

The `iMDC2025-NNN` SAM defs are **content-agnostic placeholders** —
they contain `etc.mu2e.index.000.<seq>.txt` files used solely for
job-count iteration (POMS dispatches one worker per file). They're
not tied to any campaign by content; **`iMDC2025-025` can be reused
across stages** that just need N parallel dispatches. Confirmed
by user 2026-04-28.

### Campaign config layers (actual wiring, verified 2026-07-11)

There is no `prodtools/poms/` — the real config stack is:

1. `production_manager/poms_includes/<campaign>.cfg` — thin
   `[global] includes=` shims (e.g. `mdc2025ab.cfg`) pointing at →
2. `Production/CampaignConfig/mdc2025_{prolog,main,fermigrid,nersc}.cfg`
   — fife_launch layers: prolog = jobsub globals (`need-storage-modify`,
   memory/disk, `[sam_consumer] appname=SimJob schema=xroot`); main =
   the stages (`stage_main_digi/reco/evntuple/runjobdef/validation`);
   fermigrid/nersc = site overrides.
3. The map dispatch line (`mdc2025_main.cfg` `[stage_main_runjobdef]`):
   `submit.f=dropbox://.../poms_map/%(map)s` +
   `executable_1 = runjobdef --jobdefs $CONDOR_DIR_INPUT/%(map)s` —
   one static stage serves every MDC2025-NNN.json via the `%(map)s`
   POMS parameter; maps do NOT get their own campaigns.
4. Worker side: `bin/runmu2e` POMS mode reads the map + per-job index
   file and `process_jobdef()` materializes the fcl.

MDC2020-era campaigns used full `.ini` files (`mdc2020_jobtypes.ini`
etc., uploaded via `poms_client --upload_wf`); for MDC2025 the
stage/recovery/completion settings live only in the POMS DB (edited via
web UI — nothing local records them). No POMS cron runs on Mu2e nodes;
scheduling is entirely server-side at FNAL (local cron only renews
kerberos/tokens/metacat credentials).

### Dispatch decoupling (now verified at API level)

A stage's SAM-def field is the **`dataset`** column on
`campaign_stages`, editable via
`poms_client.update_campaign_stage(campaign_stage, experiment=...,
dataset=...)` (kwargs ride the POST) or the full
`campaign_stage_edit(...)`. A stage can therefore dispatch any map
against any `i<stem>` def without renaming either. End-to-end on the
Mu2e instance: still untested.

## User operations

### Web UI

https://poms.fnal.gov · OIDC auth (no Kerberos required for UI). Top
navigation: Campaigns → click campaign → stages list → "Launch".

### `poms_client` Python library

Available somewhere under POMS distribution (UPS or `pip install`).
Key methods:

```python
from poms_client.poms_client import pomsclient

pc = pomsclient(experiment='mu2e')

# List
pc.show_campaigns(experiment='mu2e')

# Submit / launch
pc.get_submission_id_for(campaign_stage_id=18, input_dataset='...')
pc.launch_campaign_stage_jobs(campaign_name='X', stage_name='Y', limit=1000)

# Inspect
pc.submission_details(submission_id=123)

# Modify stage (changes campaign_stages row)
pc.update_campaign_stage(...)

# Upload a campaign config (.ini) or a map file
pc.upload_wf('mycampaign.ini')
pc.upload_file('mymap.json')
```

### Standard Mu2e workflow (for art jobs)

1. Build tarball + push to SAM: `json2jobdef --prod --jobdefs MDC2025-NNN.json`
2. Create SAM index: `mkidxdef --prod --jobdefs MDC2025-NNN.json` →
   creates `iMDC2025-NNN`
3. Drop the map at `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-NNN.json`
4. POMS auto-discovers (web UI shows it in the campaign's stages),
   user clicks Launch, or POMS-side cron dispatches

### Monitoring

- **Web UI** — campaign/stage/submission pages with completion %,
  file statuses, Metacat lineage links
- **FIFEmon** — jobsub-level logs (jobsub job id → live status)
- **Kibana** — detailed worker logs at FNAL ELK stack

## Common pitfalls

| Symptom | Cause |
|---|---|
| Map dropped, never dispatched | POMS stage not configured / not pointing at the right SAM def / dropbox file pattern not matching stage cfg |
| Workers all fail at SAM push | `mu2epro` token missing on worker; `pushOutput` can't auth — see [feedback_never_get_mu2epro_token](../../memory/feedback_never_get_mu2epro_token.md) |
| Stage dispatches N workers but 0 outputs | SAM dataset has 0 matching files; check `samweb count-files defname:i<stem>` |
| `samweb create-definition` redirect-loop | Token-auth path issue; **never** fall back to `voms-proxy-init` (Mu2e migrated to bearer tokens) — see `feedback_no_voms_proxy_init` |

## Open questions — status after the 2026-07-11 source pass

1. ~~Column on `campaign_stages`~~ **ANSWERED**: plain `dataset` (Text),
   alongside `cs_split_type`, `cs_last_split` (split cursor),
   `completion_type`, `completion_pct` (webservice/poms_model.py; the
   old `ddl/poms_ddl.sql` is the pre-rename tasks/jobs schema — ignore).
2. ~~Map filename → SAM def auto-derivation~~ **ANSWERED**: no POMS
   convention; pure Mu2e glue (`mkidxdef` names `i<stem>`, a human
   config binds the stage's `dataset` + `%(map)s` param).
3. ~~update_campaign_stage payload~~ **ANSWERED**: kwargs on
   `update_campaign_stage(campaign_stage, experiment, role, ...,
   dataset=...)`; full-edit alternative `campaign_stage_edit(action,
   campaign_id, ae_stage_name, ..., dataset, ae_split_type,
   ae_completion_type, ae_completion_pct, ...)`.
4. **STILL OPEN**: whether the running Mu2e instance permits shared
   SAM defs across stages without admin help (API says yes; untested).
5. **NEW**: what recovery chain the MDC2025 JobType has in the POMS DB
   (web UI check) — if it is `process_status`/`consumed_status`-based
   like the MDC2020 inis, the [[2026-07-05-run1ban-mix-recovery-data-loss]]
   L1 exposure is still armed; `pending_files` is the POMS-native guard.

## Pointers

- Repo: https://github.com/fermitools/poms
- Mu2e POMS instance: https://poms.fnal.gov
- Mu2e wiki: https://mu2ewiki.fnal.gov/wiki/POMS
- Email: poms_announce@fnal.gov

## Related local pages

- [[pbi-sequence-workflow]] — concrete example of `MDC2025-025.json` map + iMDC2025-025 + Stage 3 reco dispatch
- [[2026-07-05-run1ban-mix-recovery-data-loss]] — the recovery machinery's failure mode in production
- `Production/CampaignConfig/mdc2025_{prolog,main,fermigrid}.cfg` + `production_manager/poms_includes/*.cfg` — the actual Mu2e campaign config stack (NOTE: `prodtools/poms/` does not exist; an earlier version of this page pointed there)
- `bin/mkidxdef` + `utils/mkidxdef.py` — the Mu2e i<stem> tool
