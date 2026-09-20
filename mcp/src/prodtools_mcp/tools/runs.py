"""run_status: how a one-shot outstage run (`json2jobdef --once`) went.

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
"""

import os
from types import SimpleNamespace

from ..adapters import ToolError
from . import status

# Indices listed per bucket. A 10000-job run that lost everything must
# not answer with 10000 numbers.
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
               clusters_fn=None, codes_fn=None, job_pars_fn=None):
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
            'cluster_id', 'njobs', 'outstage', 'prodtools_dir', 'error')
           if receipt.get(k) is not None}
    out['receipt'] = os.path.join(root, name, run_receipt.RECEIPT)
    if receipt.get('state') != 'submitted':
        # building / submitting / failed: no cluster this receipt vouches
        # for, so nothing to look up. 'submitting' on a run that is not
        # being submitted right now means the submit died mid-way.
        return out

    cluster, njobs = str(receipt['cluster_id']), int(receipt['njobs'])
    clusters, reason = (clusters_fn or status._default_clusters_fn)(login)
    out['queue'] = status.queue_block([cluster], clusters, owner=login,
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
    if len(out['outputs']) > INDEX_CAP:
        keep = sorted(out['outputs'])[:INDEX_CAP]
        out['outputs'] = {i: out['outputs'][i] for i in keep}
        out['outputs_truncated'] = summary['ok']
    if unknown:
        out['state'] = 'unknown'
        out['note'] = (f'condor history has no record for {len(unknown)} '
                       f'of {njobs} jobs. That is not a failure and not a '
                       f'success: history fades after about two weeks, and '
                       f'a schedd can be briefly unreachable.')
    else:
        out['state'] = 'short' if failed else 'done'
    return out
