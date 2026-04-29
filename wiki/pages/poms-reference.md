---
title: POMS reference (FNAL Production Operations Management System)
tags: [reference, infra, poms]
sources: [2026-04-28-poms-architecture, 2026-04-28-poms-sam-wiring, 2026-04-28-poms-user-ops]
updated: 2026-04-28
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

### Dispatch decoupling possibility (unverified)

A POMS stage's SAM-def field is configurable per-stage (via web UI
or `poms_client.update_campaign_stage()`). In principle, a stage
can dispatch `G4BL-000.json` against `iMDC2025-025` without renaming
either. **Not yet verified end-to-end** — the exact column name on
`campaign_stages` and the precise `update_campaign_stage()` payload
weren't pinned down by the 2026-04-28 research.

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

## Open questions (need verification on the running instance)

1. **Exact column on `campaign_stages`** holding the SAM-def name
   (likely `dataset` or `cs_dataset` — check via `\d campaign_stages`
   on the POMS DB).
2. **Whether map filename auto-derives the stage's SAM def** by some
   POMS convention, or only via Mu2e tooling glue.
3. **`poms_client.update_campaign_stage()` exact payload** for
   changing the SAM def of an existing stage.
4. **Whether the running Mu2e POMS instance permits stages to use
   shared/reused SAM defs** without admin intervention.

## Pointers

- Repo: https://github.com/fermitools/poms
- Mu2e POMS instance: https://poms.fnal.gov
- Mu2e wiki: https://mu2ewiki.fnal.gov/wiki/POMS
- Email: poms_announce@fnal.gov

## Related local pages

- [[pbi-sequence-workflow]] — concrete example of `MDC2025-025.json` map + iMDC2025-025 + Stage 3 reco dispatch
- `poms/main.cfg`, `poms/prolog.cfg`, `poms/fermigrid.cfg` in this repo — Mu2e-specific POMS submit configs (a `poms/g4bl.cfg` existed briefly to set an SL7 outer container; retired 2026-04-28 once g4bl gained a native AL9 spack build)
- `bin/mkidxdef` + `utils/mkidxdef.py` — the Mu2e i<stem> tool
