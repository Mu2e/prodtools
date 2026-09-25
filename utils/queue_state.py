#!/usr/bin/env python3
"""Fail-closed jobsub_q queue snapshots — the drain-check primitives.

Moved out of utils/submissions.py (2026-08-16) so jobwait.py can wait on a
cluster without importing the full submit stack (submissions -> submit ->
prod_utils -> samweb_client, absent outside the Mu2e environment).
submissions re-imports every name, so callers are unaffected.

`-af JobStatus` is UNRELIABLE on jobsub_lite (2026-07-21: blank values,
and some flag orders silently drop --user and dump every experiment's
queue). Both probes instead parse the DEFAULT table: field 1 = jobsub id,
field 6 = one-letter HTCondor state (I idle, R running, H held,
C completed, X removed, S suspended). Drained/unknown id -> header +
"0 total" summary, no rows; live -> header + summary + rows; DAG children
use the same shape with the node name in OWNER.
"""

import getpass
import os
import re
import subprocess

_JOBID_RE = re.compile(r'^\d+\.\d+@\S+$')
_KNOWN_STATES = frozenset('IRHCXS<>')
_SKIP_PREFIXES = ('JOBSUBJOBID', 'Attempting to ', 'Storing bearer token')

# How much of jobsub_q's own output to repeat when a query is not trusted:
# the last lines carry the error, and a tick log must stay readable.
_REPORT_TAIL_LINES = 8
_REPORT_LINE_CHARS = 300


def _output_tail(text):
    """Last _REPORT_TAIL_LINES non-blank lines of `text`, each cut to
    _REPORT_LINE_CHARS characters, indented for the tick log."""
    lines = [ln.rstrip() for ln in (text or '').splitlines() if ln.strip()]
    return '\n'.join('    ' + ln[:_REPORT_LINE_CHARS]
                     for ln in lines[-_REPORT_TAIL_LINES:])


def report_jobsub_q_failure(cmd, res=None, error=None):
    """Print why a `jobsub_q` query was not trusted.

    The callers stay fail-closed (they return None); this only says why.
    Until 2026-09-25 both probes captured stderr and dropped it, so a
    production tick logged "jobsub_q --user failed" with no evidence.
    `res` is the CompletedProcess, `error` an exception that stopped the
    command from running at all; stdout, where the tick log goes.
    """
    what = ' '.join(cmd)
    if error is not None:
        print(f"{what}: could not run: {error}", flush=True)
        return
    # getattr: an injected runner may hand back a bare namespace with
    # only returncode and stdout (the tests' fakes do).
    stdout = getattr(res, 'stdout', '')
    stderr = getattr(res, 'stderr', '')
    if res.returncode != 0:
        tail = _output_tail(stderr) or _output_tail(stdout)
        print(f"{what}: exit {res.returncode}"
              + (f"; its output ends:\n{tail}" if tail else
                 "; it printed nothing"), flush=True)
    else:
        tail = _output_tail(stdout) or _output_tail(stderr)
        print(f"{what}: exit 0 but not a jobsub_q table"
              + (f"; its output ends:\n{tail}" if tail else
                 "; it printed nothing"), flush=True)


def _jobsub_table_states(stdout):
    """One-letter condor states from jobsub_q's default table, or None if
    untrustworthy.

    A flat projection of _jobsub_table_cluster_states — same table, same
    trust rules, ONE parser (they were two near-identical copies until
    2026-08-28, each carrying the fail-the-whole-parse rule separately).
    Order is cluster-then-row rather than strict row order; the only
    consumer (submissions.total_queued) counts states, so order carries
    no meaning."""
    clusters = _jobsub_table_cluster_states(stdout)
    if clusters is None:
        return None
    return [s for states in clusters.values() for s in states]


def _jobsub_table_cluster_states(stdout):
    """{cluster_id: [states]} from jobsub_q's default table, or None if
    untrustworthy. Cluster id is the leading integer of JOBSUBJOBID,
    before the first '.'.

    THE parser — _jobsub_table_states is a flat projection of it.
    Requires the header line (proof we got the table, not an error
    page); skips token-refresh noise and summary lines; any other
    unrecognized line fails the whole parse — a miscount floods the
    farm or starves campaigns, so never guess."""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not any(ln.startswith('JOBSUBJOBID') for ln in lines):
        return None
    clusters = {}
    for ln in lines:
        if ln.startswith(_SKIP_PREFIXES) or ' total; ' in ln:
            continue
        fields = ln.split()
        if (len(fields) < 6 or not _JOBID_RE.match(fields[0])
                or fields[5] not in _KNOWN_STATES):
            return None
        cluster = fields[0].split('.', 1)[0]
        clusters.setdefault(cluster, []).append(fields[5])
    return clusters


def queue_owner():
    """Whose grid queue this process's rows live in.

    The submitting identity, not a fixed account: `submissions run` as
    yourself submits as you; under ksu, USER=mu2epro is exported before
    running, so production still reads mu2epro's queue. Same
    generalization as submission_ledger.ledger_for.

    Was hardcoded to 'mu2epro'. Bug (found 2026-08-09): a self-run tick
    then queried mu2epro's queue, missed its own live cluster, read that
    as 'drained', and RECOVERED a row whose jobs were still running —
    the fail-closed drain signal only works when asked about the right
    account.
    """
    return os.environ.get('USER') or getpass.getuser()


def live_clusters(user=None, runner=subprocess.run):
    """{cluster_id: [states]} for every cluster with jobs in `user`'s
    default `jobsub_q --user` table, or None when the query cannot be
    trusted (caller treats None as 'error' — never drained).

    Drain-check's source of truth, taken ONCE per tick. Replaces the old
    per-jobid probe `jobsub_q --jobid <cluster>.<proc>@<schedd>`, which
    matches NOTHING on jobsub_lite and returns a valid ZERO-row table for
    a fully running cluster (verified 2026-07-22: 0 total while 1976
    jobs ran) — the old code fail-OPENED that to 'drained'. The --user
    table is the complete collector view; cluster-id membership in it is
    the reliable, fail-closed drain signal.

    `user` defaults to the submitting identity (queue_owner), NOT a
    fixed 'mu2epro' — the wrong account turns fail-closed into
    unconditional 'drained'."""
    cmd = ['jobsub_q', '--user', user or queue_owner()]
    try:
        res = runner(cmd, capture_output=True, text=True)
    except OSError as exc:
        # jobsub_q missing/unlaunchable → fail-closed
        report_jobsub_q_failure(cmd, error=exc)
        return None
    if res.returncode != 0:
        report_jobsub_q_failure(cmd, res)
        return None
    clusters = _jobsub_table_cluster_states(res.stdout)
    if clusters is None:
        report_jobsub_q_failure(cmd, res)
    return clusters


def cluster_queue_state(cluster_id, clusters):
    """'drained' | 'held' | 'running' | 'error' for a cluster, read from a
    live_clusters() snapshot. Fail-closed: None snapshot -> 'error',
    never drained. Absent from snapshot, or only terminal (C/X) rows ->
    'drained'. Any idle/running/transfer/suspended job -> 'running'; all
    non-terminal jobs held -> 'held' (human decides)."""
    if clusters is None:
        return 'error'
    states = clusters.get(str(cluster_id))
    if not states:
        return 'drained'
    active = [s for s in states if s not in ('C', 'X')]
    if not active:
        return 'drained'
    if any(s != 'H' for s in active):
        return 'running'
    return 'held'
