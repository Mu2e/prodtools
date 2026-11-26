---
title: EAF as a site for --once --local runs
tags: [eaf, runlocal, backend, decision]
sources: [hep-endpoints, mu2ewiki-eaf, eaf-probe-2026-11-26]
updated: 2026-11-26
---

# EAF as a site for `--once --local` runs

**Decision (2026-11-26):** `json2jobdef --once --local --site eaf` (MCP
`run_local(..., site="eaf")`) runs an entry's runlocal on the user's
Fermilab EAF CPU server instead of the gpvm. Branch `eaf-site`, off
Mu2e/prodtools main 62d0b5e.

## Context

A gpvm takes one local mu2e job at a time without crowding other users.
EAF (https://analytics-hub.fnal.gov, JupyterHub 5.5.4 on Kubernetes)
gives each user CPU servers with 8 guaranteed cores (cgroup quota 32,
shared 78-CPU node) and 64 GiB. The idea came from reading
RobMina/hep-endpoints v0.1.0, whose EAF adapter drives the hub from a
remote host with an API token (GPU profile only).

Probe of 2026-11-26, from mu2egpvm with the user's hub token:

- `/exp` is CephFS mounted read-write on EAF; a file written there is on
  the gpvm at once. The Mu2e wiki's "read-only /exp/mu2e/data" is wrong.
- `/cvmfs/mu2e` is there; `setupmu2e-art.sh` + `muse setup SimJob` works
  (Offline v13_48_00, al9-prof-e29-p111). No `/pnfs` mount.
- No Kerberos ticket and no bearer token on EAF: unattended `/pnfs`
  reads are not possible.
- CPU profile, read from a hand-started server's `user_options`:
  `{"profile": "fife-sl7-unpriv-interactive-htcondor", "imagecpu":
  "mu2e-AL9-pixi"}`. The spawn form (`/hub/spawn`) does not accept the
  token, so this is the only way to learn it.

## How it works

Everything up to the launch is the `--local` path: refusals, cnf build,
receipt in `/exp/mu2e/data/users/<u>/prodtools/runs/<name>/`. Then
`_start_eaf` (utils/json2jobdef.py) instead of `_start_local`:

1. writes `eaf_launch.sh` into the run directory;
2. `eaf.Hub.ensure_server()` starts the named server `prodtools` with
   the CPU profile through the hub API (POST
   `/hub/api/users/<u>/servers/prodtools`) and waits up to 7 min;
3. types `setsid nohup bash eaf_launch.sh > runlocal.log 2>&1 < /dev/null &`
   into a Jupyter terminal (websocket, standard library client in
   utils/eaf.py);
4. waits for `eaf.pid` to appear on /exp; the receipt goes `running`
   with `executor: "eaf"`.

On EAF the script sets up the grid worker's environment (setupmu2e-art,
OfflineOps, `muse setup ops`, the checkout's `bin/setup.sh`), unsets
MUSE_WORK_DIR, runs runlocal in the background, writes `eaf.pid`, and
waits. A heartbeat loop rewrites `eaf.alive` every 60 s and posts hub
activity so the idle culler leaves the server alone. On exit the trap
writes `eaf.done` (`exit=<rc>`) and stops the server.

`run_status` reads only the files: summary.json, else `eaf.done`
(failed), else a heartbeat under 300 s (running), else `unknown`. No hub
call, so the read-only server stays network-write-free and token-free.
`python -m utils.eaf {status,stop <run>,stop-server}` for operators.

## Refusals (before the run name is spent)

- inloc other than `none` or `dir:/exp/...` (no grid token on EAF);
- prodtools checkout, code tarball or token file not under /exp or
  /cvmfs (EAF mounts nothing else);
- token missing, empty or not mode 600
  (`/exp/mu2e/app/users/<u>/.eaf_token`, or `$PRODTOOLS_EAF_TOKEN_FILE`);
- another EAF run still going (receipt running, no `eaf.done`, heartbeat
  fresh): one run per server.

`--parallel` defaults to 8 (the guaranteed cores), capped at 16 like
every `--local` run.

## Verified live 2026-11-26

`POT_Run1_a` (POT.fcl, SimJob MDC2025bb), 8 jobs x 20 events, inloc
none: server started through the API, launch confirmed in ~40 s, 8/8
jobs rc 0 at 171 s each, run_status `done` with output paths, server
stopped itself. Run `cnf.oksuzian.POT_Run1_a.MDC2025bb.0`.

Two bugs found only by the live run, both fixed with tests: the typed
line `cmd &; echo marker` is a bash syntax error (now two lines), and a
wait bounded between text messages never ends because the hub pings an
idle websocket (now a deadline inside recv).

## Alternatives considered

- **Depend on hep-endpoints.** One handle = one job, state in `$HOME`,
  GPU profile hard-coded, prodtools not pip-packaged. Copied the idea,
  not the code.
- **ssh to EAF.** There is none; the hub API is the only door.
- **Ask the hub for liveness in run_status.** Would put a token and
  network calls into the read-only server. Files on /exp answer the
  same question from any gpvm.

## Open

- Idle-cull timeout and maximum server age are not measured; a run
  longer than either dies with the server (run_status then reads
  `unknown`, heartbeat stale).
- `/pnfs` inputs need a token on EAF; putting a vault token there is a
  security decision for SCD, not ours.
- EAF policy on long unattended runs: ask eaf-users before heavy use.
- The token was made with a 1-week expiry; HTTP 401/403 says to renew.

Related: [[nersc-iri-feasibility]], [[2026-10-08-mc1-slurm-runlocal-smoke]].
