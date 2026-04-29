# POMS user operational workflow — agent fetch from github.com/fermitools/poms

**Source:** https://github.com/fermitools/poms (research 2026-04-28)
**Method:** WebFetch + GitHub API + Fermilab POMS docs
**Agent slice:** user-facing operations & workflows

---

## POMS web service

- **URL:** https://poms.fnal.gov
- **Auth:** OIDC (OpenID Connect) — no Kerberos for UI access
- **Support:** poms_announce@fnal.gov

## Conceptual model (operational view)

- **Campaign** — a complete production workflow ("Mu2e_Cosmic_MC_2025"). Contains one or more stages.
- **Stage** — a single processing step ("generation", "simulation", "reconstruction"). Stages connect via file-pattern dependencies.
- **Submission** — a single launch of a stage, creating N jobs across the grid.

## Step-by-step dispatch

### (a) Prepare map

Place POMS map JSON at:
```
dropbox:///exp/mu2e/app/users/mu2epro/production_manager/poms_map/<name>.json
```

### (b) Verify campaign & stages exist

Stages must have:
- A **JobType** (executable, command-line template, memory/disk)
- A **Launch Template / LoginSetup** (batch system, resources, software version)
- **Stage parameters** (dataset reference, map file path)

### (c) Launch

**Web UI:** poms.fnal.gov → Campaigns → click campaign → click "Launch" on the stage.

**CLI (`poms_client`):**
```python
from poms_client.poms_client import pomsclient
pc = pomsclient(experiment='mu2e')
pc.launch_campaign_stage_jobs(campaign_name='X', stage_name='Y', limit=1000)
```

POMS auto-identifies which stages are ready (no upstream dependencies).

## poms_client Python library

Methods (from `poms_client/python/poms_client.py`):

| Task | Method |
|------|--------|
| List campaigns | `show_campaigns(experiment='mu2e')` |
| Create submission | `get_submission_id_for(campaign_stage_id, input_dataset=...)` |
| Launch jobs | `launch_campaign_jobs()` / `launch_campaign_stage_jobs()` |
| Check status | `submission_details(submission_id)` |
| Modify stage params | `update_campaign_stage()` |
| Upload campaign cfg | `upload_wf(file_name)` |
| Upload map file | `upload_file(map_json_file)` |

Sample dispatch:
```python
pc = pomsclient(experiment='mu2e')
sid = pc.get_submission_id_for(campaign_stage_id=18, input_dataset='mu2e_cosmic_v2')
pc.launch_campaign_stage_jobs(campaign_name='Cosmic_MC_2025', stage_name='simulation', limit=1000)
```

## Monitoring

- Campaign → Stage → Submission detail page in web UI
- Completion %, file statuses ("not submitted", "reserved", "done", "failed"), Metacat links for lineage
- Real-time: Data Dispatcher UI components, FIFEmon for jobsub logs, Kibana for detailed logs
- CLI: `pc.submission_details(submission_id=N)` returns dict with completion percentage, file statuses

## Common pitfalls

1. **Wrong map path** — verify `dropbox:///` prefix; typos surface only when jobs fail.
2. **Dataset not found** — `input_dataset` must match Metacat/SAM. Verify via `samweb list-files`.
3. **JobType mismatch** — stage JobType must match tarball executable.
4. **Stage dependency unmet** — child stages only launch after parent completion.
5. **Auth/proxy** — CLI needs Kerberos / OIDC. Web UI uses OIDC directly.
6. **Resource quota** — campaigns stall if grid resources over-allocated.

## Recovery & draining

- Retry failed jobs: mark in submission details for re-queue
- Drain campaign: mark "inactive" to stop new launches
- Adjust recovery: `modify_job_type_recoveries()` in poms_client

## Repo paths (github.com/fermitools/poms)

- `webservice/CampaignsPOMS.py` — campaign API
- `webservice/SubmissionsPOMS.py` — submission logic
- `poms_client/python/poms_client.py` — Python CLI library
- `test/test_CampaignPOMS.py`, `test/mock_job.py` — example patterns
