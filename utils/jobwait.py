#!/usr/bin/env python3
"""Block until a submitted cluster finishes; record how each job ended.

The grid twin of runlocal: `submit` starts a cluster and returns; this
waits for it to leave the queue, collects each job's exit code from
condor history, and writes the same style of `--json` summary runlocal
writes. Spec: docs/superpowers/specs/2026-08-16-jobwait-design.md.

Exit codes are the complete success record in direct mode: runjob.sh ->
runmu2e runs the output copy INSIDE the job, so a job can only exit 0
after its copies landed. No filesystem is consulted, deliberately:
pre-drain file checks race condor's evict-and-rerun, post-drain
counting is guessing (empty history is reported as `unknown`, never
inferred complete), and staying off /pnfs means this runs on any node
with a bearer token.

Also deliberately absent: any timeout (a held cluster is waited on;
patience is the caller's policy — `timeout 24h jobwait ...`), and any
acceptance threshold (exit 0 means ALL jobs ok; a caller happy with
95% reads `ok`/`failed` from the JSON, same as runlocal).

Condor history is a fading record — jobs from ~2 weeks back are already
gone from the mu2e schedds — so the JSON written here at drain time is
the durable per-job outcome record, not a cache of one.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import OUTPUT_TIERS
from utils.jobquery import Mu2eJobPars
from utils.queue_state import cluster_queue_state, live_clusters

# One jobsub_q snapshot per this many seconds while the cluster runs.
# 300 s against 30-120 min stages notices completion "immediately" on
# that scale without drumming on the schedd.
DEFAULT_POLL_S = 300


def split_jobid(jobid):
    """`'12345678@jobsub01.fnal.gov'` -> `('12345678', full id)`.

    The bare numeric cluster is what `live_clusters` keys on; the
    schedd is what `collect_exit_codes` passes to condor_history via
    `-name` — without it the query goes only to the node's default
    SCHEDD_HOST, which answers for that one schedd's clusters and
    returns nothing (or a colliding cluster id) for the rest. A bare
    id is still accepted so a caller that lost the schedd is not
    stuck, but its history is trustworthy only when the cluster
    happens to live on the default schedd.
    """
    cluster = jobid.split('@', 1)[0].split('.', 1)[0]
    if not cluster.isdigit():
        raise ValueError(f"bad --cluster {jobid!r} (want NNNN or NNNN@schedd)")
    return cluster, jobid


def wait_for_drain(cluster, jobid, poll_s, runner=subprocess.run,
                   sleeper=time.sleep, log=print):
    """Return when the cluster has left the queue. Fail-closed.

    `error` (jobsub_q failed or unparseable) is waited out exactly like
    `running`: a failed query is never evidence the jobs are done — the
    same rule submissions' cron learned when a per-jobid probe returned
    a valid zero-row table for a fully running cluster. `held` is also
    waited out, visibly: the operator resolves holds; this tool does
    not decide for them.

    A cluster absent from the very first snapshot classifies `drained`
    and falls straight through to history: a mistyped cluster id then
    yields all-`unknown` and a nonzero exit — visibly wrong, never a
    hang.
    """
    while True:
        clusters = live_clusters(runner=runner)
        state = cluster_queue_state(cluster, clusters)
        log(f"[jobwait] cluster {jobid}: {state}")
        if state == 'drained':
            return
        sleeper(poll_s)


def collect_exit_codes(jobid, njobs, runner=subprocess.run, log=print):
    """{proc: rc} from one condor_history call. Missing procs -> absent.

    condor_history is called DIRECTLY, `-name <schedd>` from the jobid.
    Not jobsub_history: the deployed jobsub_lite (1.13) wrapper parses
    the `@schedd` out of `-J` and builds a `-name schedd` — then drops
    it on the floor (`passthru = out` after the append), so every query
    silently goes to the node's default SCHEDD_HOST. A cluster on any
    other schedd comes back as zero rows and a fully successful run is
    reported `0/N ok` (2026-08-20, cluster 29868598@jobsub05 vs
    SCHEDD_HOST=jobsub01; upstream master has rewritten the wrapper).
    Worse than zero rows: a colliding cluster id on the default schedd
    would return some OTHER cluster's exit codes.

    `-limit njobs` stops condor_history's newest-first scan early:
    measured 8.4 s vs 51 s unlimited on a real 999-job cluster — and a
    just-drained cluster sits at the head of the history file. When
    fewer records exist than njobs the scan simply completes and the
    absent procs are reported by the caller as `unknown`.

    A proc can appear more than once (condor re-ran it); records come
    newest-first, so the FIRST occurrence wins. A non-numeric ExitCode
    ("undefined": removed, or exited by signal) stays out of the map —
    that proc's outcome is unknown, not zero.
    """
    cluster, _, schedd = jobid.partition('@')
    cluster = cluster.split('.', 1)[0]
    where = schedd or 'the default schedd'
    cmd = (['condor_history'] + (['-name', schedd] if schedd else [])
           + [cluster, '-limit', str(njobs), '-af', 'ProcId', 'ExitCode'])
    res = runner(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # One call, no retries (spec): the caller reports unknowns.
        log(f"[jobwait] condor_history rc={res.returncode}; "
            f"stderr:\n{res.stderr}")
        return {}
    codes = {}
    for line in (res.stdout or '').splitlines():
        fields = line.split()
        # Real rows are "ProcId ExitCode". Anything else is not data.
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        proc = int(fields[0])
        if proc in codes:
            continue
        if fields[1].lstrip('-').isdigit():
            codes[proc] = int(fields[1])
    if not codes:
        # Name the condition: "history unavailable on <schedd>" reads
        # very differently from an unknown list that looks like failed
        # jobs.
        log(f"[jobwait] history on {where} returned no usable records "
            f"for cluster {cluster} — outcomes unknown, not failed")
    return codes


def job_output_names(job_pars, index):
    """This index's output filenames, from the cnf. Non-Mu2e-named
    streams (a /dev/null sink) are dropped, mirroring runlocal's
    output_globs."""
    out = job_pars.job_outputs(index) or {}
    return sorted(v for v in out.values() if v and v.startswith(OUTPUT_TIERS))


def summary(args, codes, job_pars):
    """The run's facts as plain data — runlocal's summary contract.

    Same core shape (`jobs` with per-index `rc` and absolute-ish
    `outputs`, `ok`, `failed`) so a caller reads one schema whether the
    stage ran locally or on the grid. Grid-only additions: `proc` (the
    condor proc id; `index` is `--first` + proc, the cnf index),
    `cluster`, and `unknown` — the indices condor history had no usable
    record for. Unknown is NEVER folded into ok: an unverifiable job is
    not a successful one.

    Output paths are `<outstage>/<cluster>/<proc>/<name>` when
    `--outstage` names the root (exit 0 is the receipt they exist —
    the copy ran inside the job), bare cnf filenames otherwise.
    """
    cluster_num, _ = split_jobid(args.cluster)
    jobs = []
    for proc in range(args.njobs):
        index = args.first + proc
        names = job_output_names(job_pars, index)
        if args.outstage:
            names = [f"{args.outstage.rstrip('/')}/{cluster_num}/{proc}/{n}"
                     for n in names]
        jobs.append({'index': index,
                     'proc': proc,
                     'rc': codes.get(proc),
                     'outputs': names})
    return {
        'jobdef': str(args.jobdef),
        'cluster': args.cluster,
        'jobs': jobs,
        'ok': sum(1 for j in jobs if j['rc'] == 0),
        'failed': [j['index'] for j in jobs
                   if j['rc'] is not None and j['rc'] != 0],
        'unknown': [j['index'] for j in jobs if j['rc'] is None],
    }


def write_summary(path, data, log=print):
    """Atomic temp-then-rename, same contract as runlocal's: a caller
    polling the path never reads a half-written file, and a MISSING
    file means the driver died before reporting — not that zero jobs
    ran."""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=1) + '\n')
    tmp.replace(path)
    log(f"[jobwait] summary: {path}")


def drive(args, runner=subprocess.run, sleeper=time.sleep, log=print):
    """Wait, collect, report. Returns the process exit code:
    0 iff every job exited 0 — a convenience summary only; the JSON is
    written regardless, and the partial run is exactly when a caller
    needs it (runlocal's rule)."""
    cluster, jobid = split_jobid(args.cluster)
    wait_for_drain(cluster, jobid, args.poll_s,
                   runner=runner, sleeper=sleeper, log=log)
    codes = collect_exit_codes(jobid, args.njobs, runner=runner, log=log)
    data = summary(args, codes, Mu2eJobPars(args.jobdef))
    log(f"[jobwait] {data['ok']}/{args.njobs} ok, "
        f"failed: {data['failed'] or '-'}, "
        f"unknown: {data['unknown'] or '-'}")
    if args.json:
        write_summary(args.json, data, log=log)
    return 0 if data['ok'] == args.njobs else 1


def build_parser():
    parser = argparse.ArgumentParser(
        description="Block until a submitted cluster finishes, then record "
                    "each job's exit code (the complete success record in "
                    "direct mode: the output copy runs inside the job). "
                    "No timeout of its own — wrap in `timeout` if you "
                    "want one.")
    parser.add_argument('--jobdef', required=True,
                        help='cnf tarball the cluster was submitted from')
    parser.add_argument('--cluster', required=True,
                        help='cluster id, ideally with schedd: '
                             'NNNN@jobsub0X.fnal.gov (submit prints it)')
    parser.add_argument('--njobs', type=int, default=None,
                        help='jobs in the cluster (default: the cnf\'s '
                             'njobs; required for an open-ended cnf)')
    parser.add_argument('--first', type=int, default=0,
                        help='cnf index of proc 0 (default 0), for windows '
                             'submitted with a firstjob offset')
    parser.add_argument('--poll-s', type=float, default=DEFAULT_POLL_S,
                        help=f'seconds between jobsub_q snapshots '
                             f'(default {DEFAULT_POLL_S})')
    parser.add_argument('--outstage', default=None,
                        help='outstage root the submission used '
                             '($MU2EGRID_WFOUTSTAGE); with it, summary '
                             'output paths are absolute')
    parser.add_argument('--json', default=None, metavar='PATH',
                        help='write the machine-readable summary to PATH '
                             '(atomic; written on failure too)')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        split_jobid(args.cluster)
    except ValueError as exc:
        sys.exit(f"jobwait: {exc}")
    if args.poll_s <= 0:
        sys.exit("jobwait: --poll-s must be positive")
    if args.json:
        # Checked before hours of waiting, for the same reason runlocal
        # does: losing the summary to a directory typo at the one moment
        # it cannot be recomputed.
        args.json = str(Path(args.json).resolve())
        parent = Path(args.json).parent
        if not parent.is_dir():
            sys.exit(f"jobwait: --json directory does not exist: {parent}")
    if not Path(args.jobdef).is_file():
        sys.exit(f"jobwait: no such jobdef: {args.jobdef}")
    if args.njobs is None:
        args.njobs = Mu2eJobPars(args.jobdef).njobs()
    if args.njobs < 1:
        sys.exit("jobwait: cnf is open-ended (njobs 0) — pass --njobs")
    return drive(args)


if __name__ == '__main__':
    sys.exit(main())
