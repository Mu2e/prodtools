"""run_status: how a one-shot run (`json2jobdef --once`, grid or --local) went.

Such a run has no ledger row and nothing in SAM, so neither
campaign_status nor any dataset tool can see it. What exists is its
receipt (utils/run_receipt), the live queue, and -- once the cluster has
left the queue -- the jobs' exit codes. Those are the complete success
record, by the rule utils/jobwait already lives by: runjob.sh -> runmu2e
runs the output copy INSIDE the job, so a job can only exit 0 after its
copies landed. No filesystem is consulted.

Read-only, like everything in this server: the exit codes are fetched on
demand and never written down. Condor history fades after about two
weeks; from then on a run reads `unknown`, never `short` and never
`done`. `jobwait --json` is the durable record for whoever needs one.

A `--once --local` run (receipt `executor: "local"`) has no cluster: its
record is runlocal's own summary.json (written atomically, so a missing
file means runlocal never finished), and while there is none, whether
runlocal's process is still there -- asked of /proc, on its own host.

A `--once --local --site eaf` run (receipt `executor: "eaf"`) runs that
same runlocal on the user's Fermilab EAF server, writing into the same
run directory on /exp. Its liveness is read from files there
(utils/eaf: eaf.done, the eaf.alive heartbeat), so any gpvm can answer.
"""

import json
import os
import socket
from types import SimpleNamespace

from utils.queue_state import schedd_of

from ..adapters import ToolError
from . import status

# Indices listed per bucket. A 10000-job run that lost everything must
# not answer with 10000 numbers. A caller that collects the run's files
# asks for every ok job's outputs with all_outputs=True; the failure lists
# stay capped either way.
INDEX_CAP = 200


def _root_for(mine, user):
    """Whose runs. Unlike campaign_status there is no production default
    to fall back on: production never runs `--once`."""
    from utils import run_receipt
    if user is None and not mine:
        raise ToolError(
            'invalid_argument', 'whose run is this?',
            'Pass user="<login>" (either transport), or mine=true on your '
            'own stdio server. One-shot runs are personal: there is no '
            'production default.')
    _, login = status._resolve_identity(mine, user)
    return run_receipt.runs_root(login), login


def _default_codes_fn(jobid, njobs, log=None):
    from utils import jobwait
    return jobwait.collect_exit_codes(jobid, njobs,
                                      log=log or (lambda *_: None))


def _default_job_pars_fn(path):
    from utils.jobquery import Mu2eJobPars
    return Mu2eJobPars(path)


def run_status(name, mine=False, user=None, runs_root=None,
               clusters_fn=None, codes_fn=None, job_pars_fn=None,
               alive_fn=None, host_fn=None, now_fn=None, all_outputs=False):
    from utils import jobwait, run_receipt
    root, login = _root_for(mine, user)
    root = runs_root or root
    try:
        receipt = run_receipt.read(root, name)
    except ValueError as e:
        raise ToolError('invalid_argument', str(e),
                        'A run name is the cnf name without ".tar", e.g. '
                        'cnf.<login>.<desc>.<dsconf>.0') from e
    except run_receipt.RunNotFound as e:
        raise ToolError('not_found', str(e),
                        'Check the name and whose run it is (user=).') from e

    out = {k: receipt.get(k) for k in
           ('name', 'state', 'created_utc', 'submitted_utc', 'jobid',
            'cluster_id', 'njobs', 'outstage', 'prodtools_dir', 'error',
            'executor', 'host', 'pid', 'started_utc', 'summary', 'log',
            'parallel', 'server')
           if receipt.get(k) is not None}
    out['receipt'] = os.path.join(root, name, run_receipt.RECEIPT)
    executor = receipt.get('executor')
    if executor in ('local', 'eaf'):
        if receipt.get('state') != 'running':
            return out          # building / starting / failed
        if executor == 'eaf':
            import time
            probe = _eaf_probe(out, receipt, os.path.join(root, name),
                               now_fn or time.time)
        else:
            probe = _local_probe(receipt, alive_fn or _default_alive_fn,
                                 host_fn or socket.getfqdn)
        return _runlocal_status(out, receipt, probe, all_outputs)
    if receipt.get('state') != 'submitted':
        # building / submitting / failed: no cluster this receipt vouches
        # for, so nothing to look up. 'submitting' on a run that is not
        # being submitted right now means the submit died mid-way.
        return out

    cluster, njobs = str(receipt['cluster_id']), int(receipt['njobs'])
    clusters, reason = (clusters_fn or status._default_clusters_fn)(login)
    # The receipt's jobid names the schedd; a snapshot that never asked
    # it would read this cluster as gone, and the run as finished.
    out['queue'] = status.covered_queue_block(
        [cluster], [schedd_of(receipt.get('jobid'))], clusters, login,
        reason=reason)
    if out['queue']['state'] != 'known':
        out['state'] = 'unknown'
        return out
    if any(out['queue'].get(k) for k in ('running', 'idle', 'held')):
        out['state'] = 'running'
        return out

    codes = (codes_fn or _default_codes_fn)(receipt['jobid'], njobs)
    cnf = os.path.join(root, name, name + '.tar')
    summary = jobwait.summary(
        SimpleNamespace(jobdef=cnf, cluster=receipt['jobid'], njobs=njobs,
                        first=0, outstage=receipt.get('outstage')),
        codes, (job_pars_fn or _default_job_pars_fn)(cnf))
    failed, unknown = _fill_jobs(out, njobs, summary,
                                 all_outputs=all_outputs)
    if unknown:
        out['state'] = 'unknown'
        out['note'] = (f'condor history has no record of how {len(unknown)} '
                       f'of {njobs} jobs ended (no exit code, no signal, '
                       f'no removal). That is not a failure and not a '
                       f'success: history fades after about two weeks, and '
                       f'a schedd can be briefly unreachable.')
    else:
        out['state'] = 'short' if failed else 'done'
    return out


def _fill_jobs(out, njobs, summary, all_outputs=False):
    """The `jobs` and `outputs` blocks, from a summary in the shape
    jobwait and runlocal share (`jobs` of {index, rc, outputs}, `ok`,
    `failed`) plus `unknown`. `outputs` is capped at INDEX_CAP unless
    all_outputs. Returns (failed, unknown), uncapped."""
    failed, unknown = summary['failed'], summary['unknown']
    out['jobs'] = {'expected': njobs, 'ok': summary['ok'],
                   'failed': failed[:INDEX_CAP],
                   'unknown': unknown[:INDEX_CAP]}
    if failed:
        out['jobs']['exit_codes'] = {j['index']: j['rc']
                                     for j in summary['jobs']
                                     if j['index'] in failed[:INDEX_CAP]}
    if len(failed) > INDEX_CAP or len(unknown) > INDEX_CAP:
        out['jobs']['truncated'] = {'failed': len(failed),
                                    'unknown': len(unknown)}
    out['outputs'] = {j['index']: j['outputs'] for j in summary['jobs']
                      if j['rc'] == 0}
    if not all_outputs and len(out['outputs']) > INDEX_CAP:
        keep = sorted(out['outputs'])[:INDEX_CAP]
        out['outputs'] = {i: out['outputs'][i] for i in keep}
        out['outputs_truncated'] = summary['ok']
    return failed, unknown


def _runlocal_status(out, receipt, probe, all_outputs=False):
    """A `--once --local` run that was started, on either executor:
    runlocal's summary once it wrote one, else what `probe()` says of
    runlocal -- (state, note), state 'gone' when it exited. The state is
    recomputed on every call; the receipt is never rewritten."""
    njobs, path = int(receipt['njobs']), receipt['summary']
    summary, bad = _read_summary(path)
    if summary is None and bad is None:
        state, note = probe()
        if state != 'gone':
            out['state'] = state
            if note:
                out['note'] = note
            return out
        # Gone -- but runlocal may have written its summary and exited
        # between the read above and the probe. `failed` is final to a
        # caller, so read the summary again before saying it.
        summary, bad = _read_summary(path)
        if summary is None and bad is None:
            out['state'] = 'failed'
            out['note'] = note
            return out
    return _from_summary(out, njobs, summary, bad, all_outputs)


def _local_probe(receipt, alive_fn, host_fn):
    """runlocal on a gpvm: asked of /proc, on its own host."""
    def probe():
        pid, host = receipt['pid'], receipt.get('host')
        if host != host_fn():
            return 'unknown', (f'the run is on {host} and has written no '
                               f'summary yet; only that host can say whether '
                               f'runlocal (pid {pid}) is still going.')
        alive = alive_fn(pid, receipt['summary'])
        if alive is None:
            return 'unknown', (f'cannot read /proc/{pid} on this host, so '
                               f'whether runlocal is still going is unknown.')
        if alive:
            return 'running', None
        return 'gone', (f'runlocal (pid {pid}) is gone and wrote no '
                        f'summary; see {receipt.get("log")}')
    return probe


def _eaf_probe(out, receipt, run_dir, now_fn):
    """runlocal on EAF: read from the launch script's files on /exp
    (eaf.done, else the eaf.alive heartbeat), so any gpvm can answer. The
    hub is never asked: this server performs no network writes and holds
    no EAF token."""
    from utils import eaf

    def probe():
        try:
            code = eaf.read_done(run_dir)
        except ValueError as exc:
            return 'unknown', str(exc)
        if code is not None:
            return 'gone', (f'runlocal on EAF exited {code} and wrote no '
                            f'summary; see {receipt.get("log")}')
        age = eaf.heartbeat_age(run_dir, now_fn())
        if age is not None and age < eaf.STALE_S:
            out['heartbeat_age_s'] = int(age)
            return 'running', None
        return 'unknown', (
            f'no summary, no {eaf.DONE}, and the heartbeat is '
            + ('missing' if age is None else f'{int(age)} s old')
            + f': the EAF server may have been stopped under the run. '
              f'`python -m utils.eaf status` shows the server; see '
              f'{receipt.get("log")}')
    return probe


def _from_summary(out, njobs, summary, bad, all_outputs=False):
    """The verdict from runlocal's summary (or why it cannot be read)."""
    if bad is not None:
        out['state'] = 'unknown'
        out['note'] = bad
        return out
    seen = {j['index'] for j in summary['jobs']}
    outside = sorted(i for i in seen if not 0 <= i < njobs)
    summary = dict(summary,
                   unknown=[i for i in range(njobs) if i not in seen])
    failed, unknown = _fill_jobs(out, njobs, summary,
                                 all_outputs=all_outputs)
    if unknown or outside:
        out['state'] = 'unknown'
        notes = []
        if unknown:
            notes.append(f"runlocal's summary has no record for "
                         f"{len(unknown)} of {njobs} jobs.")
        if outside:
            notes.append(f"runlocal's summary lists indices outside "
                         f"0..{njobs - 1}: {outside[:INDEX_CAP]}")
        out['note'] = ' '.join(notes)
    else:
        out['state'] = 'short' if failed else 'done'
    return out


def _read_summary(path):
    """runlocal's summary at `path`: (summary, None); (None, None) while
    there is no file; (None, note) when there is one that cannot be used
    -- unreadable, not JSON, or not in the shape _fill_jobs reads. A bad
    summary is `unknown` to the caller, never an exception out of
    run_status and never `done`."""
    try:
        with open(path) as fh:
            summary = json.load(fh)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        return None, f"runlocal's summary {path} cannot be read: {exc}"
    why = _malformed(summary)
    if why:
        return None, f"runlocal's summary {path} is malformed: {why}"
    return summary, None


def _malformed(summary):
    """Why a parsed summary is not in runlocal's shape, or None."""
    if not isinstance(summary, dict):
        return f'its root is a {type(summary).__name__}, not an object'
    if not isinstance(summary.get('jobs'), list):
        return "it has no 'jobs' list"
    if 'ok' not in summary or not isinstance(summary.get('failed'), list):
        return "it has no 'ok' count or no 'failed' list"
    for job in summary['jobs']:
        if not (isinstance(job, dict)
                and all(k in job for k in ('index', 'rc', 'outputs'))):
            return (f'a job is not an object with index, rc and outputs: '
                    f'{job!r:.200}')
        index = job['index']
        if not isinstance(index, int) or isinstance(index, bool):
            return f'a job index is not an integer: {index!r:.200}'
    return None


def _default_alive_fn(pid, summary_path):
    """True when process `pid` is this run's runlocal, False when it is
    gone, None when this host will not say.

    Reads the command line rather than calling kill(pid, 0): pids are
    reused, and kill() needs the same user. runlocal's command line
    carries `--json <run dir>/summary.json`, which names exactly one run.
    """
    try:
        with open(f'/proc/{int(pid)}/cmdline', 'rb') as fh:
            argv = fh.read().split(b'\0')
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return os.fsencode(summary_path) in argv
