# POMS architecture & data model — agent fetch from github.com/fermitools/poms

**Source:** https://github.com/fermitools/poms (research conducted 2026-04-28)
**Method:** WebFetch + GitHub API on the public repo
**Agent slice:** architecture / data model

---

POMS (Production Operations Management System) is a workflow orchestration platform that automates job submission, monitoring, and recovery for large-scale data processing campaigns at Fermilab. It abstracts the complexity of distributed job management and data handling, providing experiment teams with a campaign-centric interface to manage multi-stage processing pipelines.

## Core entities and relationships

**Campaign** — A named, top-level container representing a complete data processing workflow. Campaigns group related processing stages, store default parameters, and track overall progress across job submissions. (`webservice/CampaignsPOMS.py`, `webservice/poms_model.py`)

**CampaignStage** — An individual processing step within a campaign, defining how a specific dataset will be processed. Each stage specifies a JobType template, launch credentials (LoginSetup), dataset inputs, software versions, split strategy, and completion criteria. Stages form a directed acyclic graph via dependencies. (`webservice/StagesPOMS.py`, `webservice/poms_model.py`)

**Submission** — A single job launch request issued for a campaign stage. Tracks the entire lifecycle from submission creation through job completion. Each submission has a unique ID and maintains status history (New → Running → Located/Failed/Removed). (`webservice/SubmissionsPOMS.py`)

**JobType** — A reusable job template storing execution logic: launch scripts, parameter schemas, file I/O expectations, recovery configurations. Versioned via snapshots at submission time. (`webservice/poms_model.py`, `webservice/MiscPOMS.py`)

**LoginSetup** — Authentication and launch configuration entity storing credentials, launch host, and launch account.

**DataDispatcherSubmission** — Bridge entity connecting submissions to the Data Dispatcher metadata service when stages process data through DD projects rather than SAM datasets. (`webservice/DMRService.py`)

**Tag / CampaignsTag** — Flexible labeling mechanism allowing campaigns to be categorized via key-value pairs.

**Experimenter** — User entity storing credentials, role information, and session data.

## Dispatch lifecycle

1. **Submission Creation** — POMS creates a submission record tied to the campaign stage, inheriting dataset configuration, JobType snapshot, and parameter overrides. Marked "New."

2. **Job Submission** — `poms_jobsub_wrapper` submits jobs to HTCondor via jobsub_lite, creating HTCondor classads for each job in the dataset split.

3. **Job Execution & Monitoring** — `submission_agent` daemon polls LENS (Landscape query service) every ~120s, aggregating status. Jobs execute on compute nodes, producing outputs.

4. **Output Tracking** — POMS tracks output files through SAM (for SAM-based stages) or Data Dispatcher.

5. **Submission Completion** — `wrapup_tasks` identifies submissions meeting completion criteria. Submissions transition to "Located" (success) or "Failed".

6. **Recovery & Cascading** — If configured, recovery jobs reprocess failed/unprocessed files. Once Located, dependent downstream stages auto-launch.

## Key terminology

- **Dataset (POMS)** — A SAM dataset definition name referenced by a campaign stage. Queried via SAM's `defname:` syntax.
- **Recovery** — Additional submissions for a JobType to reprocess failed/skipped/unconsumed files.
- **Draining** — Ceasing new submissions to a stage while allowing in-flight to complete.
- **Status Dump** — Snapshot of submission state stored for historical reference.

## Database schema and code paths

- `ddl/poms_ddl.sql` — relational schema (primary)
- `webservice/poms_model.py` — Python ORM/model layer
- `webservice/poms_service.py` — CherryPy-based web service entry point
- `webservice/CampaignsPOMS.py`, `StagesPOMS.py`, `SubmissionsPOMS.py`, `JobsPOMS.py`, `DMRService.py` — business logic
- `webservice/SAMSpecifics.py` — SAM integration layer

Core tables: `campaigns`, `campaign_stages`, `campaign_stage_snapshots`, `submissions`, `submission_snapshots`, `jobs`, `job_histories`, `job_types`, `login_setups`, `experimenters`, `experiments`, `files`, `data_dispatcher_submissions`.

## What POMS is NOT

- **Not SAM** — POMS orchestrates workflows that consume SAM datasets; SAM catalogs files.
- **Not jobsub_lite** — POMS calls jobsub_lite to submit jobs; adds campaign management above it.
- **Not art** — POMS manages where/when art jobs run; art defines what they do.
- **Not Data Dispatcher** — POMS can delegate to DD; POMS is the orchestrator over these tools.
