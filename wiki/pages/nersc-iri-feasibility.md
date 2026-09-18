---
title: Running prodtools jobs at NERSC — HEPCloud today, IRI Facility API later
tags: [nersc, iri, hepcloud, backend, decision, analysis]
sources: [amsc-iri-docs, nersc-iri-openapi-v2, mu2ewiki-hpc, nersc-cvmfs-docs, doe-iri-github]
updated: 2026-09-10
---

# Running prodtools jobs at NERSC

Assessment written 2026-09-10 in answer to "can we use prodtools with
NERSC IRI (https://amsc-docs-d762d2.gitlab.io/IRI/)?".

## Verdict

- **HEPCloud route works today and needs a small prodtools change.**
  Mu2e already runs at Perlmutter through `jobsub_submit
  --site=NERSC-Perlmutter-CPU`. The direct backend hardcodes flags that
  NERSC rejects; a per-entry `site` knob would fix it.
- **IRI Facility API route is feasible on paper, blocked in practice
  by authentication.** NERSC's IRI endpoint issues bearer tokens only
  via a browser-interactive Globus login, which the IRI examples repo
  itself labels temporary. No unattended or collaboration-account path
  is documented. The API is self-described as "not final and may change
  at any time".

## What IRI is

DOE program (https://iri.science) standardising a Facility API across
ALCF, NERSC, OLCF and ESnet. NERSC's instance:
`https://api.iri.nersc.gov/api/v2/` (OpenAPI at `/api/v2/openapi.json`,
Swagger UI at the root). Reference implementation and clients live at
https://github.com/doe-iri (`iri-facility-api-python`,
`iri-facility-api-toolkit` = pip `amscrot-py`, `iri-facility-api-examples`
notebooks). AmSC (https://amsc.energy.gov) adds an identity layer and a
Python client (`amsc-client`, GitLab-hosted, not on PyPI).

## NERSC IRI API surface (verified from openapi.json, version 2.0.0)

| Area | Endpoints | Notes |
|---|---|---|
| Public | `/facility`, `/facility/sites`, `/status/resources`, `/status/incidents`, `/status/events` | reachable from mu2egpvm without a token |
| Account | `/account/whoami`, `/account/projects`, `.../project_allocations` | bearer |
| Compute | `POST /compute/job/{resource_id}`, `GET /compute/status/{resource_id}/{job_id}`, `POST /compute/status/{resource_id}` (bulk), `DELETE /compute/cancel/...`, `PUT` update | PSI/J JobSpec; optional `Idempotency-Key` header |
| Filesystem | `upload`, `download`, `ls`, `stat`, `mkdir`, `cp`, `mv`, `rm`, `checksum`, `compress`, `extract`, `view` (5 MB cap) ... | async task model (`TaskSubmitResponse`) |

JobSpec fields: `executable`, `arguments`, `directory`, `environment`,
`inherit_environment`, `stdin_path`/`stdout_path`/`stderr_path`,
`pre_launch`/`post_launch`, `launcher`, `container {image,
volume_mounts}`, `resources {node_count, process_count,
processes_per_node, cpu_cores_per_process, gpu_cores_per_process,
exclusive_node_use, memory (bytes)}`, `attributes {duration (s),
queue_name, account, reservation_id, custom_attributes}`.
Job states: `new, queued, held, active, completed, failed, canceled`.
No job-array concept: one JobSpec is one Slurm job.

Security scheme: `HTTPBearer` only. Token source today: Globus OAuth
authorization-code flow
(https://github.com/NERSC/iri-api-get-globus-token) — prints a URL,
user pastes the code back, refresh token cached at
`~/.globus/auth_tokens.json`. The examples repo says of this path:
"THIS IS TEMPORARY AND WILL NOT BE SUPPORTED IN THE FUTURE".
The Superfacility API client-credential flow (Iris-issued client,
JWT assertion, ~10 min access tokens) is documented for
`api.nersc.gov/api/v1.2`, not for the IRI endpoint.

## Perlmutter facts that matter for prodtools

- **cvmfs is live on compute nodes**, `mu2e.opensciencegrid.org` and
  `fermilab.opensciencegrid.org` are in the mounted list
  (https://docs.nersc.gov/services/cvmfs/). Batch jobs add
  `#SBATCH --module=cvmfs` and `-L cvmfs`; shifter needs
  `--module=cvmfs`, podman-hpc needs `-v /cvmfs:/cvmfs`. The Mu2eWiki
  2023 note "syncs cvmfs to static disk once a day" is stale.
- Perlmutter is SLES, so the EL9 Mu2e stack runs inside a container
  (`fnal-wn-el9` image via shifter/podman-hpc), exactly what the IRI
  `container` block expresses.
- Wall clock 24 h (48 h max, shorter starts sooner). FIFO queue, no
  fair share.
- Storage: project `m4599`, `/global/cfs/cdirs/m4599/mu2e` (old
  `m3249` still holds files). SAM station serves a `nersc` location
  only when the file has one; xrootd streaming to disk is not
  supported, ifdh copy from dCache is; OSDF/StashCache is available.
- Mixing/pileup jobs are a poor fit (12 GB input failures); resampling
  at 12k parallel worked with resilient inputs (2026 notes).

## Route A — HEPCloud via jobsub (exists, small change)

Mu2eWiki `[submit]` block that worked with prodtools under POMS:

```
global.mu2e_options = --no-timing
expected-lifetime = 23h
timeout = 22h
OS = EL9
disk = 10GB
site = NERSC-Perlmutter-CPU
```

Bare jobsub form: `jobsub_submit --OS=EL9 --group=mu2e --role=Production
--resource-provides=usage_model="OFFSITE" --site="NERSC-Perlmutter-CPU"`.
Notes from 2025 tests: only `--role=Production` works (mu2epro only),
do not include a singularity classad, `--no-timing` is mandatory
(TimeTracker temp files fill the 10 GB scratch).

What `utils/jobsub_argv.py` does today that NERSC rejects or lacks:

1. `--resource-provides usage_model=OPPORTUNISTIC,DEDICATED` is
   hardcoded; NERSC needs `usage_model=OFFSITE` plus `--site`.
2. `--singularity-image` is always emitted; NERSC says omit it.
3. `--OS=EL9` is never emitted.
4. `--mu2e-options --no-timing` exists on `runmu2e.py` but nothing on
   the submit side passes it.
5. `--tar_file_name dropbox://` (the `code` tarball path) relies on
   RCDS, which NERSC does not serve; `code` entries stay Fermigrid-only.

Worker side needs nothing: `bin/runjob.sh` sources
`/cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh`, gets `$PROCESS`,
`$CONDOR_DIR_INPUT`, the credmon bearer token and ifdh from the
HEPCloud glidein like any other offsite slot. Ledger, `queue_state`
and recovery are untouched because the cluster is still a jobsub
cluster.

Implementation shape: add `site` to `RESOURCE_KEYS`/entry values,
branch the argv builder on it, plumb `mu2e_options`. Test with a
10-index slice as mu2epro, 10 GB disk, 23 h lifetime.

## Route B — IRI Facility API backend (new backend, blocked on auth)

Mapping that does fit:

| prodtools today | IRI equivalent |
|---|---|
| `-f dropbox://cnf.tar`, ops JSON | `POST /filesystem/upload/{cfs}` to `/global/cfs/cdirs/m4599/mu2e/prodtools/<campaign>/` |
| `file://runjob.sh` executable | `executable` inside `container {image: fnal-wn-el9, volume_mounts: [/cvmfs, /global/cfs/...]}` |
| `-e MU2EGRID_*` | `environment` |
| `--memory/--disk/--expected-lifetime` | `resources.memory` (bytes), `attributes.duration` (s), `queue_name`, `account=m4599`, `custom_attributes` for `-L cvmfs`, `--module=cvmfs`, constraint |
| `-N njobs` (one proc per index) | one `POST /compute/job` per index, or one job with `process_count=N` plus a shim that sets `PROCESS` from `SLURM_PROCID` (mu2ejobsub already does `PROCESS=${PROCESS:-$ALPS_APP_PE}` in its HPC branch) |
| `jobsub_q` / htcondor2 ClassAds | `POST /compute/status/{resource_id}` bulk filter; states include `held` |
| cluster_id in ledger | IRI job id(s); ledger needs a `backend` column and a second poller |

Gaps, in order of severity:

1. **No unattended token.** Globus interactive login only, flagged
   temporary. Production ticks run as mu2epro from cron-free manual
   `submissions run`; a human-in-the-browser step per token refresh is
   workable for a personal test, not for the production account.
   Collaboration accounts at NERSC do not document API client
   ownership. Ask help.nersc.gov what the supported machine-to-machine
   path for `api.iri.nersc.gov` will be.
2. **Worker credentials.** No HTCondor credmon means no
   `BEARER_TOKEN` for ifdh stage-in, pushOutput dCache writes or SAM
   declares. Options: ship a vault token via `/filesystem/upload` and
   run `htgettoken` on the worker (present at
   `/cvmfs/fermilab.opensciencegrid.org/products/common/prd/htgettoken`,
   so visible on Perlmutter), or stage outputs to CFS and push from a
   Fermilab node afterwards. Either is a security-review item; the
   3 h token lifetime versus 24 h walltime is the same constraint as
   [[merge-factor-capped-by-token-lifetime]].
3. **Environment contract.** `runjob.sh` and `runmu2e.py` read
   `$PROCESS`, `$CONDOR_DIR_INPUT`, `$_CONDOR_SCRATCH_DIR`; a thin
   Slurm-side wrapper sets them from `SLURM_PROCID`, the upload
   directory and `$SCRATCH`.
4. **Ledger and recovery** are condor-shaped (`cluster_id`,
   `jobsub_id`, hold reasons from ClassAds, "row stays active while any
   job is queued"). An IRI backend needs its own liveness query and a
   `held` mapping.
5. **Accounts.** No NERSC, Globus or sfapi credentials exist on this
   node (`~/.globus`, `~/.sfapi`, `~/.amscrot` absent). Membership in
   `m4599` and IRI endpoint enablement must be confirmed in Iris first.
6. **API stability.** `info.description` says it may change at any
   time; PSI/J JobSpec is the only stable part.

## Recommendation

- Want NERSC cycles for resampling or primaries now: do Route A. One
  entry key, one argv branch, one mu2epro test slice.
- Want IRI: first a manual probe, not code. Get a Globus token with
  `get_globus_token.py --facilities nersc --validate-iri`, call
  `/account/projects` and `/compute/resources` to see whether `m4599`
  and a Perlmutter compute resource are visible, then submit an `echo`
  job in `fnal-wn-el9` that runs `ls /cvmfs/mu2e.opensciencegrid.org`.
  Start a backend only after NERSC names a non-interactive token path.

## Probe results (2026-09-10 evening, first real jobs)

**Verdict update: the auth blocker is gone.** `api.iri.nersc.gov/api/v2`
rejects Globus `iri_api` tokens (`401 Facility Specific authentication
failed: 403: Invalid token`; v1 additionally says `Globus authentication
failed`). What it accepts is a NERSC **Superfacility API client** token:
Iris profile → "Superfacility API Clients" → "+ New Client", level red
(only red may submit jobs; 48 h lifetime, 2 source-IP ranges, 30 d after
a NERSC security review), source IP `131.225.240.98/32` (mu2esrv01
egress). Client id is 13 chars; the key comes as PEM or JWK. Exchange at
`https://oidc.nersc.gov/c2id/token` with authlib `PrivateKeyJWT`
(client_credentials); access tokens live 600 s. Fully unattended.

Everything below ran from mu2egpvm with that client (user `oksuzian`,
uid 105241, groups m4599 + m5115):

| Step | Result |
|---|---|
| `/account/whoami`, `/account/projects` | m4599 "Intensity Frontier Computing via HEPCloud"; CPU allocation 97 150 node-h, 71 % used; GPU 11 463 node-h |
| `/compute/resources` | `compute` = `94351904-6dba-4c16-b5cd-fbd280d8615b` (Perlmutter), also `jobs` (Slurm commands), `login`, `cori` |
| storage resources (`/status/resources`) | `cfs` = `59e80c79-4dfd-4c53-9c07-7405685fcd37`, `scratch` = `43d8f6c0-…`, `homes` = `65b28619-…`; `POST /filesystem/resources` is the listing (GET is 405) |
| CFS layout | `/global/cfs/cdirs/m4599/mu2e` does NOT exist. `Experiments/mu2e` and `Experiments/mu2e_staging` are `rlc:m3249 770` — permission denied for this user. `Users/` is 2777: `Users/oksuzian/iri-probe` created via `mkdir parent=true` |
| upload / download | work; download returns text verbatim (not base64); 5 MiB cap both ways |
| submit (`qos=debug`, `constraint=cpu`, 600 s) | accepted; queued 3–8 min; status carries the full sacct record (`partition regular_milan_ss11`, `reqtres mem=487802M`, nodelist, elapsed) |
| native job (no container) | SLES 15-SP6; `/cvmfs/mu2e.opensciencegrid.org` and `fermilab.opensciencegrid.org` mounted (plus `mu2e.osgstorage.org`); `setupmu2e-art.sh` sources, muse v4_14_01 on PATH; `SLURM_PROCID` present; `/tmp` 353 GB tmpfs; no `ifdh`/`htgettoken` before `muse setup ops` |
| `custom_attributes` | `constraint` reaches Slurm; `licenses: cvmfs` and `module: cvmfs` do NOT (`SLURM_JOB_LICENSES=cfs:1`); cvmfs mounted regardless |
| container, short name `fermilab/fnal-wn-el9:latest` | podman-hpc resolves unqualified names to `registry.suse.com` → denied. Always give `docker.io/…` |
| container `docker.io/fermilab/fnal-wn-el9:latest`, no mounts | image pulled ON the compute node (~13 layers), AlmaLinux 9.8 inside, runs as uid 0; `/global/cfs` and `/cvmfs` invisible → exit 127 |
| container + ONE `volume_mounts` entry (`read_only` set) | works: script from CFS runs inside EL9; image ships `/usr/bin/htgettoken` |
| container + TWO `volume_mounts` entries (any order, `read_only` set or not) | always `Error: parsing reference " ": invalid reference format`, exit 125 — an adapter bug serialising >1 mount into the podman-hpc command line |
| `executable=/bin/bash, arguments=["-lc", text]` inline form | ran on the SLES host, not in the container, even though the image was pulled — do not use |

Job ids for reference: 58176064 (first, 2 mounts, fail), 58176602
(native, ok), 58176608/58176616 (image names), 58177067/58177071
(probe2 no-mounts / 2 mounts), 58177149/58177152/58177157 (1 mount ok,
1 mount ok, inline-on-host), 58177533/58177537/58177541 (2 mounts, all
fail).

Follow-ups 58177871 (single `/cvmfs` read-only mount: inside the
container, exit 127 only because CFS was not mounted — `read_only: true`
is fine) and 58177874 (inline `bash -lc` with a CFS mount: ran on SLES
again — the inline form is what escapes the container).

**Job 58177878 — the Mu2e stack runs at Perlmutter, end to end.**
Native SLES job, `probe3.sh`: apptainer 1.5.3 from
`/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer`,
image `/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest`
(that repo IS mounted at NERSC), `exec -B /cvmfs -B /global/cfs`, then
inside AlmaLinux 9.8: `muse setup ops` gives `ifdh`, `xrdcp`,
`htgettoken`, `gfal-copy`, python 3.12; `muse setup SimJob MDC2025aw`;
`mu2e -c Offline/Mu2eG4/fcl/g4test_03.fcl -n 2` → "Art has completed
and will exit with status 0", CPU 28.6 s, wall 264.7 s (cold cvmfs
cache), VmHWM 2232 MB, `data_03.root` + `g4test_03.root` written under
`/tmp`. Container runs as the user (uid 105241), not root, unlike
podman-hpc. This is the OSG-glidein pattern and needs nothing from the
adapter beyond one Slurm job, so it is the shape a prodtools IRI
backend should use: `runjob.sh` gains a Perlmutter branch that wraps
itself in apptainer.

Remaining for a real backend: worker credential for dCache writes and
SAM declares (no credmon — `htgettoken` is in the image, a vault token
would have to be placed on CFS), `SLURM_PROCID` → `PROCESS` shim, an
IRI status poller and ledger column, and the 48 h red-client renewal
until NERSC's security review. Report the multi-mount bug and the
inline-form escape to help.nersc.gov.

## g4bl probe (2026-09-11, job 58197742) — GREEN

Second runner proven. Same native-Slurm + apptainer-from-cvmfs shape as
the art probe, payload = the prodtools g4bl worker recipe
(`utils/runmu2e.py _g4bl_script`) copied line for line into
`g4bl_inner.sh`: unset `SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE`,
source `setupmu2e-art.sh`, `eval "$(spack load --sh g4beamline)"`,
`g4bl Mu2E.in viewer=none First_Event=1 Num_Events=100 histoFile=... epsMax=0.01`.
Deck = beamkit's pinned G4BeamlineScripts e470313, tarred without `.git`
(113 KB, under the 5 MB upload cap) and extracted on CFS by the job.

| item | Perlmutter (100 ev) | Fermilab grid, same deck (1000 ev, campaign 8) |
|---|---|---|
| g4bl exit | 0 | 0 |
| Z3712 rows | 419 | 4962 |
| warnings | 51 (GeomSolids1001 6, GeomVol1002 4, Geometry Error 39, Run0111 2) | identical |
| CPU | 1m22s | ~1.4 s/event |
| wall in container | 4m12s (cold cvmfs cache for the g4beamline spack tree) | — |
| queue wait (debug) | ~9 min | — |

Output `nts.oksuzian.G4blIri.e470313.00000000.root` (137 KB) landed on
CFS next to the scripts. Nothing was declared to SAM. Both prodtools
runners now run on Perlmutter through the IRI API; the remaining work is
the prodtools backend (Slurm submit path, ledger backend column, IRI
poller, outstage harvest), not the payload.

`probe_iri.py` gained `mkdir` and `ls` subcommands
(`POST /filesystem/mkdir/{id}` and `/filesystem/ls/{id}`, both async
tasks); upload into a missing directory fails with
`Error: 400: Error downloading: No such file`.

## beamkit NERSC backend (2026-09-12) — production path built and live-verified

beamkit gained a second backend (`site="nersc"`) that submits g4bl runs to
Perlmutter through this API from any host with a Superfacility API client:
cnf built locally, run laid out under `base_dir/runs/<run_id>/` on CFS,
one Slurm job per 128 indices (`index = BK_OFFSET + SLURM_PROCID`), no
recovery, beam file built by a second Slurm job on the node. Spec and plan
live in the beamkit repo (`docs/specs/2026-09-11-nersc-backend-design.md`,
`docs/plans/2026-09-11-nersc-backend.md`). Live smoke passed: Slurm job
58227570 ran two indices of ten events (1 min 22 s), job 58227941 built
the bm beam file (14 rows). Three API facts the live run corrected:

- `whoami` with a client-credential token returns the numeric account id
  (`{"username": "105241"}`), not the login.
- `mkdir` is not `mkdir -p`: a missing parent fails with
  `Error: 404: mkdir: cannot create directory '<path>': No such file or directory`.
- An `Idempotency-Key` header on job submission is refused:
  `501: Idempotency-Key provided but no idempotency store is configured`.

A final whole-branch review (2026-09-12) added four hardening fixes worth
knowing when configuring the backend: every transport fault (timeout,
non-JSON reply) is raised as `IriError` and a failed layout is retryable;
`submit_run` checks that `inner.sh` exists on CFS before posting a job;
`nersc.toml` refuses an `owner` that is not a Mu2e name token and a
`base_dir` outside `/global/cfs/` (the job binds only `/cvmfs` and
`/global/cfs` into the container); jobs are sliced by the record's
`slice_size`, so changing `procs_per_node` after a partial submit cannot
submit an index twice. Branch v1 HEAD cc4b964, 332 tests.

Distribution decision (2026-09-12): beamkit is published to cvmfs
(`bin/install_beamkit.sh`, a mirror of prodtools' installer, with a venv
baked at the release path) and started per user through
`scripts/beamkit-mcp-cvmfs`; laptops without cvmfs use `pip install
beamkit`. Not the central HTTP host on mu2eaigpvm01: that pattern fits
read-only servers, while beamkit submits jobs and writes records under the
caller's own identity. `mcp` is pinned below 2 (FastMCP rename).

**Using it (v0.3.0 live on cvmfs 2026-09-12):** on any gpvm add
`{"mcpServers": {"beamkit": {"command": "/cvmfs/mu2e.opensciencegrid.org/bin/beamkit/current/scripts/beamkit-mcp-cvmfs"}}}`
to `.mcp.json`; nothing to install. NERSC jobs need `~/.sfapi/{client_id,priv_key.pem}`
(chmod 400) and `/exp/mu2e/data/users/$USER/beamkit/nersc.toml`. Laptop:
`pip install git+https://github.com/oksuzian/beamkit.git@v0.3.0`. Then
`get_server_info`, `run_beamline(tag, deck_ref, run_as="self", site="nersc", njobs, events_per_job)`,
`beamline_status`, `beamline_outputs`, `make_beamfile(run_id, "bm", "self", site="nersc")`.
Full table in the beamkit README "Quick start".

## Probe recipe (2026-09-10, superseded by the sfapi path above for step 3)

Scripts staged at `/exp/mu2e/data/users/oksuzian/prodtools/nersc-iri-probe/`:
`get_globus_token.py` (copy of NERSC/iri-api-get-globus-token, native-app
client id `fae5c579-490a-4d76-b6eb-d78f65caeb63`, no secret needed),
`probe_iri.py` (requests-only CLI over the v2 API), `probe.sh` (payload).

1. Confirm NERSC account and `m4599` membership at https://iris.nersc.gov.
2. `python3 -m pip install --user globus-sdk` on mu2egpvm (python 3.10,
   `requests` present, `globus_sdk` absent).
3. `python3 get_globus_token.py --facilities nersc --validate-iri` —
   prints a Globus URL, open it on a laptop, paste the code back. Token
   lands in `~/.globus/auth_tokens.json`; refresh later with
   `--refresh-only`. Validation hits `/api/v1/account/projects`; an
   empty `session_info.authentications` means rerun with `--force-login`.
4. `probe_iri.py whoami` — projects list must show `m4599`.
5. `probe_iri.py resources` — note the Perlmutter compute id and a
   filesystem id (cfs or scratch); the public `/status/resources` list
   shows names only (`compute`, `cfs`, `scratch`, `shifter`, ...).
6. `probe_iri.py upload FS_ID probe.sh /global/cfs/cdirs/m4599/mu2e/iri-probe/probe.sh`.
7. `probe_iri.py submit COMPUTE_ID /global/cfs/cdirs/m4599/mu2e/iri-probe/probe.sh --dry-run`
   then without `--dry-run`. First try may 422 on
   `custom_attributes` keys (`slurm.constraint`, `slurm.licenses` are
   PSI/J names, unverified against NERSC's adapter) or on the container
   `volume_mounts` shape; the error text is the finding.
8. `probe_iri.py status COMPUTE_ID JOB_ID` until `completed`/`failed`,
   then `probe_iri.py download FS_ID .../probe.sh.out`.

What `probe.sh` answers: OS inside the job, `/cvmfs/mu2e...` visible,
`setupmu2e-art.sh` sources, which of `ifdh`/`htgettoken` exist, what
Slurm env replaces `$PROCESS`, scratch quota.

Filesystem `upload` and `download` are capped at 5 MiB (5242880 bytes)
per the OpenAPI description. Enough for a cnf tarball and ops JSON;
useless for code tarballs or output files. Bulk data must move by
Globus or ifdh/gfal from the worker.

## Sources

- https://amsc-docs-d762d2.gitlab.io/IRI/ (sub-pages 404 on 2026-09-10)
- https://api.iri.nersc.gov/api/v2/openapi.json (fetched 2026-09-10)
- https://github.com/doe-iri (api docs, python impl, toolkit, examples)
- https://github.com/NERSC/iri-api-get-globus-token
- https://github.com/amsc-interfaces/amsc-client-tutorial
- https://docs.nersc.gov/services/cvmfs/ , /services/sfapi/ , /accounts/collaboration_accounts/
- https://mu2ewiki.fnal.gov/wiki/HPC (NERSC 2023–2026 sections)
- https://htcondor.readthedocs.io/en/25.0/faq/users/perlmutter.html (annex alternative, ssh-based)
- prodtools `bin/runjob.sh`, `utils/jobsub_argv.py`, `utils/submit.py`, `utils/runmu2e.py`, `utils/submission_ledger.py`, `utils/queue_state.py`

## IRI v2 outage and the sfapi fallback (2026-09-12/13)

From 16:36 CDT on 2026-09-12 every facility endpoint of api.iri.nersc.gov
(`compute/resources`, `compute/status`, `filesystem/*`, `facility/sites`)
returned `500: An unexpected error occurred` while `account/whoami`,
`account/projects` and the legacy api.nersc.gov v1.2 kept working with the
same client. A fresh red client and a fresh amber client saw the same
500s, and the red client could run a command on Perlmutter through v1.2,
so the credential, IP allow-list and facility were all fine. Still down
after 15 h; ticket filed with NERSC. `scripts/iri-ping` in beamkit prints
the discriminating probes with a verdict. Real client expiry looks
different: `invalid_client` from oidc.nersc.gov.

beamkit's answer is a second transport: `transport = "sfapi"` in
`nersc.toml` drives the legacy Superfacility API v1.2 with the same seven
client methods (mkdir via `utilities/command`, ls/upload/download under
`utilities/`, sbatch rendered from the PSI/J spec and posted to
`compute/jobs/perlmutter`, state from `sacct` with `cached=false`).
Verified on 2026-09-13 with run G4blSfapi.e470313-001 (Slurm 58268029, 2
of 2 outputs, 1 min 24 s in the shared qos). Quirks worth remembering:
v1.2 puts errors in a 200 with `status: "ERROR"`; utilities paths keep a
double slash after the machine; `-L cvmfs` is rejected by sbatch; the
executable must be launched with `srun` or only one task runs.

## Getting outputs back (2026-09-13)

Outputs of a NERSC run stay on CFS under `base_dir/runs/<run_id>/out/`;
nothing is declared to SAM. `fetch_outputs(run_id, dest, kind)` (beamkit
22e6213) pulls the nts files, or the complete beam files, into a local
directory through the API download endpoint. Both APIs cap that at
5 242 880 bytes per file, so a run with one larger file is refused whole
and needs Globus (NERSC "Perlmutter" collection) or scp from a login
node. On the sfapi transport the file arrives base64 with
`binary=true`; IRI v2 has no binary flag and its download task fails on
a ROOT file with pydantic's `string_unicode` error (checked 2026-09-14
once the adapter was back, which it was by 21:36 CDT), so that
transport refuses and points at `transport = "sfapi"`. Verified live on G4blSfapi.e470313-001:
two ROOT files, sizes match, magic `root`. Harvest to dCache/SAM stays
a v2 item.

## beamkit over MCP, cvmfs retired (2026-09-15)

beamkit 0.4.0 (branch v1, df7ecf8..2314fe5) no longer imports prodtools:
`bridge.py` is an MCP client of the two prodtools servers, spawned as
stdio children from `$BEAMKIT_PRODTOOLS_ROOT/mcp/scripts/` (default the
cvmfs prodtools release) through `mcpclient.StdioServer` (private loop
thread, one serve task, respawn after death). prodtools gained
`locate_file` and `dataset_files` on the read-only server (branch
mcp-locate-and-dataset-files, 920d58d; PR to Mu2e/prodtools pending).
beamkit's interpreter needs none of the ops environment, so `uvx --from
git+https://github.com/oksuzian/beamkit@v0.4.0 beamkit-mcp` is the one
install line and the beamkit cvmfs installer and launchers were deleted
(the cvmfs tree stays frozen at v0.3.1). Verified: 393 tests, the schema
contract test against the real prodtools servers, `get_server_info` over
stdio. The live Fermilab submit could not be verified: see the next
section.

## prodtools push_cnf broken by ops-021 + OfflineOps six (2026-09-15)

Since the ops-021 spack environment (Python 3.12) became `muse setup
ops` on 2026-09-12, prodtools' write chain `setupmu2e-art.sh && muse
setup ops && setup OfflineOps && bin/json2jobdef` fails at import:
`setup OfflineOps` prepends sam_web_client v3_6's python dir, whose
bundled six 1.11.0 has no working `six.moves` on 3.12. Signature:
`ModuleNotFoundError: No module named 'six.moves'` from
`samweb_client/http_client.py`. Reproduced with
`prodtools_mcp_write.runner.run_cli(["bin/json2jobdef", ...],
run_as="self")` alone, so the user's own `prodtools-write` MCP server
is affected too. Fix candidates: prepend `$SPACK_ENV/.spack-env/view/python`
after `setup OfflineOps` in the runner chain; or pin ops-019; or a
sam_web_client with six >= 1.16.
