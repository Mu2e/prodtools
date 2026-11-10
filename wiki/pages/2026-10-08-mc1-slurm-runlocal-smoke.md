---
title: MC-1 Slurm cluster — access and first prodtools runlocal smoke
tags: [run, slurm, mc1, daq, runlocal, cvmfs, cluster]
sources: []
updated: 2026-10-08
---

# MC-1 Slurm cluster: first prodtools job

## The cluster
Andrew Norman set up a Slurm 26.05 batch cluster on the MC-1 trigger
workers inside the DAQ enclave (announced 2026-10-08 in a group DM with
Bertrand Echenard, Michael MacKenzie and Simon Corrodi). The user guide
is https://mu2e-exp.fnal.gov/daq/daqbatch/ (Fermilab SSO); its source is
`Mu2e/mu2edaq-batch-queues`, `docs/GETTING-STARTED.md`.

- Submit node `mu2e-mc1-worker-01`; partition `mc1` = workers 02–18.
  Workers 02–16 have 16 CPUs, 17–18 have 32 (hyperthreaded); ~60 GB
  for jobs per node; memory enforced; default 2000 MB/CPU.
- It is a DAQ resource: only the shared accounts `mu2ebatch`,
  `mu2eshift` and `mu2edaq` can submit. Offline testing uses
  `mu2ebatch`; Andrew adds principals to the `.k5login` files.
- No shared filesystem. `/scratch`, `/data-local`, `/tmp` are per node;
  output stays where the job ran.
- `/cvmfs/mu2e.opensciencegrid.org` is mounted on every node (set up
  the same day). `/pnfs` and `/exp` are not.
- The dCache xrootd doors (`fndcadoor`, `fndca1`, port 1094) accept TCP
  connections from worker-01.

## Getting in
```
ssh -J mu2eshift@g2gateway01.fnal.gov mu2ebatch@mu2e-mc1-worker-01.fnal.gov
```
The gateway login is the shared `mu2eshift` account, not your own: the
gateway refused `oksuzian@` and `mu2ebatch@` even after the k5login was
set up. Kerberos only. For non-interactive use pass `BatchMode=yes` to
both hops (a `ProxyCommand` with `-W %h:%p` carries it to the jump).

## Smoke test, 2026-10-08
One job of `cnf.mu2e.ExtractedCRY.MDC2025as.0.tar` (generator only, no
inputs, so no token or dCache read), 10 events, run by `runlocal` from
the cvmfs release `v3.3.4`:

1. scp the cnf tarball (583 B, from persistent disk) and two scripts to
   `/data-local/mu2ebatch/oksuzian/` on worker-01.
2. `salloc -N1 -c2 --mem=4G --time=00:30:00`; inside, `sbcast` the
   tarball and the node script to the allocated node (sbcast reads the
   file on worker-01, where salloc runs its command).
3. `srun` on the node: `source $P/bin/setup.sh; bash $P/bin/runlocal
   --jobdef <tar> --inloc none --first 0 --num 1 -j 1 --nevts 10`.

Result (Slurm job 69, worker-02): `runlocal rc=0`, 1/1 succeeded in
143 s, output `dts.mu2e.CosmicCRYExtracted.MDC2025as.001400_00000000.art`
passed runlocal's read-back. Art exit 0; 10 events, 1 passed the filter;
CPU 72.7 s, real 93.7 s; VmPeak 2140 MB, VmHWM 1923 MB.

## Network and CPU, 2026-10-08
Checked on all 17 compute workers (salloc -N17, one task each):

- Reachable from every worker: dCache xrootd (`fndcadoor`, `fndca1`
  :1094), dCache WebDAV (`fndcadoor:2880`), conditions DB
  (`dbdata0vm:8444` cache, `:9443` nocache; HTTPS answers), SAM
  (`sammu2e.fnal.gov:8483` — the samweb client builds `sam<exp>.fnal.gov`;
  `samweb.fnal.gov` does not resolve anywhere), vault
  (`htvaultprod:8200`), and github.com. Nothing a digi/reco job needs is
  blocked by the enclave, at the TCP level.
- CPUs: workers 02–16 (and 01) are 2 × 8-core **AMD Opteron 6320**
  (2012, no AVX2, no SMT); 17–18 are 1 × 16-core **Xeon E5-2683 v4**
  with hyperthreading (32 threads, AVX2). An Offline build that ever
  requires x86-64-v3 (AVX2) would not run on 02–16.

Same cnf, index 0, 100 events, one job per machine, runlocal v3.3.4:

| machine | CPU | art "Full event" mean | job CPU | real |
|---|---|---|---|---|
| gpvm (mu2egpvm, reference) | AMD EPYC | 0.0236 s | 34.7 s | 53.6 s |
| worker-17 | Xeon E5-2683 v4 | 0.0348 s (1.5×) | 48.9 s | 64.8 s |
| worker-02 | Opteron 6320 | 0.0569 s (2.4×) | 77.3 s | 85.0 s |

Initialisation dominates a 100-event job (~70 s of the Opteron's 77 s
CPU) and scales by about the same factor. ExtractedCRY is a light event;
a G4-heavy stage should be measured before sizing a campaign here.
Memory is ~3.9 GB per core, enough for 2.5 GB jobs.

## Compared with FermiGrid, 2026-10-08
Reference: 300 randomly sampled production logs of the same dataset
(`log.mu2e.ExtractedCRY.MDC2025as.log`, persistent disk), each a
500,000-event FermiGrid job; art's "Full event" row per job. MC-1 and
gpvm: the same cnf, index 0, 5000 events, one job on an otherwise idle
node. Filter pass rate is 4.96% in both, so the event mix matches.

| where | mean s/event | median s/event | vs typical FermiGrid slot |
|---|---|---|---|
| FermiGrid, median job (p10–p90) | 0.0300 (0.0187–0.0590) | 0.0193 (0.0116–0.0369) | 1.0 |
| gpvm (AMD EPYC) | 0.0241 | 0.0154 | 0.8× (faster) |
| MC-1 worker-17 (Xeon E5-2683 v4) | 0.0348 | 0.0230 | ~1.2× slower |
| MC-1 worker-02 (Opteron 6320) | 0.0583 | 0.0362 | ~1.9× slower |

- The Opterons sit at FermiGrid's slowest decile, alongside the oldest
  `fnpc1` AMD nodes (bogomips 3992: 0.050–0.064 s mean).
- The Xeons match the `fnpc1` Intel nodes (bogomips 4600: 0.0326 s).
- Grid slots run with every core busy; these MC-1 numbers are one job
  per idle node, so a full MC-1 node will be somewhat slower.
- Capacity, an estimate: 240 Opteron cores / 1.9 ≈ 125 plus the two
  Xeon nodes ≈ 30 is roughly **150 FermiGrid-slot equivalents**, an
  upper bound until measured under full load.

## Reading input over xrootd, 2026-10-08
`htgettoken` is not installed on the MC-1 nodes, so `getToken`
(`/cvmfs/mu2e.opensciencegrid.org/bin/getToken`, a thin htgettoken
wrapper) cannot run there. Workaround used for the test: mint the token
on a gpvm and copy only the bearer token, with reduced scope:

```
htgettoken -i mu2e --vaultserver htvaultprod.fnal.gov \
  --scopes storage.read:/mu2e -o <file>     # scope = storage.read:/mu2e only, ~3 h
```

A personal token's default scopes include `storage.modify` on your user
areas and `compute.create`; under a SHARED account anyone logged in could
use it until it expires, so copy a read-only one. Never run `getToken`
itself as yourself under `mu2ebatch`: it leaves a days-long vault token
in `/tmp/vt_u<uid>` that anyone on the account could mint tokens from.

Job: `cnf.mu2e.MuCap1809keVCalo.MDC2025ar.0.tar` index 0, 20 events,
`--inloc tape`, `BEARER_TOKEN_FILE` pointing at the sbcast token. Its
resampler input (`sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000000.art`,
7 GB, ONLINE_AND_NEARLINE so no recall) streamed from
`xroot://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/mu2e/tape/...` on
worker-03 (Slurm job 80): art exit 0, 20 events (9 passed), CPU 76.5 s,
real 100.6 s, runlocal rc=0 in 150 s. Token deleted from the node,
worker-01 and the gpvm afterwards.

## Full-node load test, 2026-10-08
16 ExtractedCRY.MDC2025as jobs at once (indices 0–15, 5000 events each,
`runlocal -j 16`) on Opteron worker-04 (`salloc -c16 --mem=48G`, Slurm
job 81): 16/16 succeeded, 819 s wall for the batch.

| | mean s/event | median s/event | CPU/real |
|---|---|---|---|
| worker-02, 1 job, idle node | 0.0583 | 0.0362 | 0.98 |
| worker-04, 16 jobs, full node | 0.0900 (0.076–0.101) | 0.0535 | 0.86 |
| FermiGrid, median production job | 0.0300 | 0.0193 | 0.98 |

- Filling the node costs ~1.5× per event. Likely cause, not verified:
  the Opteron 6320 is a Piledriver part whose cores come in pairs
  sharing one FPU and L2, plus memory bandwidth; CPU/real 0.86 also
  shows some waiting.
- A loaded Opteron core is ~3× slower than a typical FermiGrid slot.
- Revised capacity estimate: 240 Opteron cores / 3 ≈ 80 FermiGrid-slot
  equivalents; the two Xeon nodes were not load-tested, perhaps ~20–30
  more. Roughly **100 FermiGrid slots** for the whole cluster, about
  two-thirds of the idle-node estimate above.

## Still open
- A managed token for `mu2ebatch` (like mu2epro's), so jobs do not
  depend on someone copying a personal token in every 3 hours.
- A load test on a Xeon node (32 hyperthreaded slots).
- prodtools has no Slurm backend. The cheap path is sbatch running
  `runlocal --indices <share>` per node (no SAM, no ledger) plus output
  collection, since nothing is shared between nodes. A production
  backend would need array task → `PROCESS`, `squeue` liveness instead
  of HTCondor in `queue_state`, and write tokens on the workers.
