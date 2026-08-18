#!/usr/bin/env python3
"""Fail-closed jobsub_q queue snapshots — the drain-check primitives.

Moved verbatim out of utils/submissions.py (2026-08-16) so that
utils/jobwait.py can wait on a cluster without importing the whole
submit stack (submissions -> submit -> prod_utils -> samweb_client,
which is absent outside the Mu2e environment). submissions re-imports
every name, so its callers and tests are unaffected.

jobsub_q output parsing. The `-af JobStatus` condor passthrough is
UNRELIABLE on jobsub_lite (observed 2026-07-21 on the first live
top-up tick: blank attribute values, and some flag orders silently
drop the --user filter and dump every experiment's queue — the tick
correctly refused with count-error). Both queue probes therefore
parse the DEFAULT table: one row per job, first field the jobsub id,
sixth field the one-letter HTCondor state (I idle, R running, H held,
C completed, X removed, S suspended). Empirical shapes 2026-07-21:
drained/unknown id -> header + "0 total; ..." summary, no rows; live
-> header + summary + rows; DAG children keep the same geometry with
the node name in the OWNER column.
"""

import getpass
import os
import re
import subprocess

_JOBID_RE = re.compile(r'^\d+\.\d+@\S+$')
_KNOWN_STATES = frozenset('IRHCXS<>')
_SKIP_PREFIXES = ('JOBSUBJOBID', 'Attempting to ', 'Storing bearer token')


def _jobsub_table_states(stdout):
    """One-letter condor states from jobsub_q's default table, or None
    when the output cannot be trusted. The header line must be present
    (proof we got the table, not an error page); token-refresh noise
    and per-schedd summary lines are skipped; any other unrecognized
    line fails the whole parse — a miscount either floods the farm or
    starves campaigns, so never guess."""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not any(ln.startswith('JOBSUBJOBID') for ln in lines):
        return None
    states = []
    for ln in lines:
        if ln.startswith(_SKIP_PREFIXES) or ' total; ' in ln:
            continue
        fields = ln.split()
        if (len(fields) < 6 or not _JOBID_RE.match(fields[0])
                or fields[5] not in _KNOWN_STATES):
            return None
        states.append(fields[5])
    return states


def _jobsub_table_cluster_states(stdout):
    """{cluster_id: [states]} from jobsub_q's default table, or None when
    the output cannot be trusted (same trust rules as
    _jobsub_table_states — header required, any unrecognized job row
    fails the whole parse). The cluster id is the leading integer of the
    JOBSUBJOBID (before the first '.')."""
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
    yourself submits as you, and under ksu the block exports
    USER=mu2epro before running, so production keeps reading mu2epro's
    queue exactly as before. Same generalization as
    submission_ledger.ledger_for.

    This was hardcoded to 'mu2epro'. Measured 2026-08-09: a self-run
    tick queried mu2epro's queue, did not find the caller's own live
    cluster in it, and cluster_queue_state read absent-from-snapshot as
    'drained' — so a still-running row was verified, found 2/2 outputs
    missing (of course: its jobs had not finished) and RECOVERED while
    its jobs were running. Every self tick would duplicate the whole
    campaign. The fail-closed drain signal is only fail-closed when it
    is asked about the right account.
    """
    return os.environ.get('USER') or getpass.getuser()


def live_clusters(user=None, runner=subprocess.run):
    """{cluster_id: [states]} for every cluster with jobs in `user`'s
    default `jobsub_q --user` table, or None when the query cannot be
    trusted (caller treats None as 'error' — never drained).

    This is the drain-check's source of truth, taken ONCE per tick.
    It replaces the old per-jobid probe `jobsub_q --jobid
    <cluster>.<proc>@<schedd>`, which matches NOTHING on this jobsub_lite
    and returns a valid, ZERO-row table for a fully running cluster
    (verified 2026-07-22: 0 total while 1976 jobs of one cluster ran) —
    the old queue_state fail-OPENED that to 'drained', prematurely
    recovering still-running rows. The --user table is the complete
    collector view (the same source total_queued trusts); cluster-id
    membership in it is the reliable, fail-closed drain signal.

    `user` defaults to the submitting identity (see queue_owner), NOT to
    a fixed 'mu2epro' — asking the wrong account turns the fail-closed
    signal into an unconditional 'drained'."""
    try:
        res = runner(['jobsub_q', '--user', user or queue_owner()],
                     capture_output=True, text=True)
    except OSError:
        return None      # jobsub_q missing/unlaunchable → fail-closed
    if res.returncode != 0:
        return None
    return _jobsub_table_cluster_states(res.stdout)


def cluster_queue_state(cluster_id, clusters):
    """'drained' | 'held' | 'running' | 'error' for a cluster, read from a
    live_clusters() snapshot. Fail-closed: a None snapshot (query failed
    or unparseable) is 'error', never drained. A cluster absent from the
    snapshot, or present with only terminal (C/X) rows, is 'drained'. Any
    idle/running/transfer/suspended job → 'running' (still working); only
    when every non-terminal job is held → 'held' (all preempted, human
    decides)."""
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
