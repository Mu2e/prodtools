"""Campaign status tools.

Composed from the read-only ledger plus, optionally, a jobsub_q snapshot
and SAM output counts. The bare call is ledger-only: a 23-row ledger
fanned out to one SAM count per output dataset would exceed the client's
timeout.
"""
import subprocess

from prodtools_mcp import ledger_ro
from prodtools_mcp.adapters import ToolError

# jobsub table state letters. C and X are terminal.
_TERMINAL = ('C', 'X')

CAMPAIGN_STATES = ('active', 'complete', 'paused', 'cancelled')

# jobsub_q talks to the collector over the network and can hang. This
# runs inline on FastMCP's event loop, so an unbounded wait wedges the
# whole server, not just one call.
QUEUE_TIMEOUT_S = 60


def queue_block(cluster_ids, clusters):
    """Queue counts for a campaign's clusters from a live_clusters()
    snapshot.

    A None snapshot means the query could not be trusted. It returns
    state='unknown' and OMITS the count keys entirely — there must be no
    zero to misread. Proc-form jobsub_q was verified on 2026-07-22
    reporting 0 total while 1976 jobs of one cluster ran; a {"running": 0}
    from a failed query reads as 'drained' and could trigger a recovery
    pass against live jobs.
    """
    if clusters is None:
        return {'state': 'unknown',
                'reason': 'jobsub_q query failed or was unparseable'}
    running = idle = held = 0
    seen = []
    for cid in cluster_ids:
        states = clusters.get(str(cid))
        if not states:
            continue
        seen.append(str(cid))
        for st in states:
            if st in _TERMINAL:
                continue
            if st == 'H':
                held += 1
            elif st == 'I':
                idle += 1
            else:
                running += 1
    return {'state': 'known', 'running': running, 'idle': idle,
            'held': held, 'clusters': seen}


def _outputs_block(entry, njobs, submitted, count_fn):
    """Produced-vs-expected per output dataset.

    Two denominators, because one is misleading on its own:

    - `expected_at_completion` is njobs — one output file per job per
      stream. Derived this way deliberately, to avoid a /pnfs cnf read on
      every status call.
    - `submitted` is the campaign cursor: how many indices have actually
      been handed to the grid. EVERY direct campaign is sliced, so at
      cursor 500 of njobs 4000 with all 500 landed, comparing produced
      against njobs alone reports 12.5% when the truth is 100% of what is
      in flight.
    """
    from utils.poms_entry import outputs_of
    try:
        outputs = outputs_of(entry)
    except ValueError as exc:
        return {'state': 'unknown', 'reason': str(exc)}
    datasets = []
    for out in outputs:
        dataset = out.get('dataset')
        if not dataset:
            continue
        try:
            produced = count_fn(dataset)
        except Exception as exc:
            return {'state': 'unknown',
                    'reason': f'SAM count failed for {dataset}: {exc}'}
        datasets.append({'dataset': dataset,
                         'expected_at_completion': njobs,
                         'submitted': submitted,
                         'produced': produced})
    return {'state': 'known', 'datasets': datasets}


def _timeout_runner(cmd, **kwargs):
    kwargs.setdefault('timeout', QUEUE_TIMEOUT_S)
    return subprocess.run(cmd, **kwargs)


def _default_clusters_fn():
    """live_clusters with a bounded wait.

    live_clusters catches OSError (jobsub_q missing) but a hung collector
    raises TimeoutExpired, which is a SubprocessError, not an OSError.
    Catch it here and return None so queue_block renders state="unknown"
    — a timeout is exactly the case that must never serialize as zero.
    """
    from utils.submissions import live_clusters
    try:
        return live_clusters(runner=_timeout_runner)
    except subprocess.TimeoutExpired:
        return None


def _default_count_fn(dataset):
    from utils.samweb_wrapper import dataset_file_count
    return dataset_file_count(dataset)


def _row_counts(rows):
    """Submission rows counted PER STATE.

    An open/closed split hides `exhausted` — the state that means the
    attempt cap was reached and a human must take over
    (utils/submissions.py:14-17). Bucketed as "closed" alongside
    complete/recovered, a campaign with five exhausted rows looked
    identical to a clean one. Every state in ledger_ro.ROW_STATES is
    present with an explicit zero: these are counts of rows actually
    read, not an unknown, so zero is the honest value.
    """
    counts = {state: 0 for state in ledger_ro.ROW_STATES}
    for row in rows:
        counts[row['state']] = counts.get(row['state'], 0) + 1
    return counts


def _matches(camp, campaign, campaign_id):
    if campaign_id is not None:
        return camp['id'] == campaign_id
    if campaign is not None:
        return campaign in (camp['tarball'] or '')
    return True


def campaign_status(campaign=None, campaign_id=None, include_queue=True,
                    include_outputs=True, db_path=None,
                    clusters_fn=None, count_fn=None):
    """Status of one campaign, or a ledger-only summary of all of them.

    With neither `campaign` nor `campaign_id`, this is ledger-only: local
    sqlite, no network, and the queue/outputs blocks are omitted.
    """
    from utils.poms_entry import njobs_of

    # One snapshot, one connection: campaigns and rows must agree. See
    # ledger_ro.snapshot.
    all_camps, all_rows = ledger_ro.snapshot(db_path)
    selected = [c for c in all_camps if _matches(c, campaign, campaign_id)]

    named = campaign is not None or campaign_id is not None
    # An empty ledger is a finding, not an error — list_campaigns()
    # returns [] for it and these siblings must not disagree. Only a
    # selector that matched nothing is not_found.
    if not selected and named:
        raise ToolError(
            'not_found',
            f'no campaign matching '
            f'{campaign_id if campaign_id is not None else campaign!r}',
            'Call list_campaigns() to see what exists.')

    want_queue = named and include_queue
    want_outputs = named and include_outputs

    clusters = None
    if want_queue:
        clusters = (clusters_fn or _default_clusters_fn)()

    tarball_counts = {}
    for camp in all_camps:
        tarball_counts[camp['tarball']] = \
            tarball_counts.get(camp['tarball'], 0) + 1

    out = []
    for camp in selected:
        njobs = njobs_of(camp['entry'])
        rec = {
            'id': camp['id'],
            'state': camp['state'],
            'tarball': camp['tarball'],
            'map_path': camp['map_path'],
            'slice_size': camp['slice_size'],
            'cursor': camp['cursor'],
            'njobs': njobs,
            'created_utc': camp['created_utc'],
        }
        if tarball_counts.get(camp['tarball'], 0) > 1:
            rec['note'] = ('more than one campaign shares this tarball; '
                           'submission rows correlate by tarball only '
                           '(no foreign key) and may be conflated')
        if want_queue:
            mine = [r for r in all_rows if r['tarball'] == camp['tarball']]
            rec['rows'] = _row_counts(mine)
            cluster_ids = [r['cluster_id'] for r in mine if r['cluster_id']]
            rec['queue'] = queue_block(cluster_ids, clusters)
        if want_outputs:
            rec['outputs'] = _outputs_block(
                camp['entry'], njobs, camp['cursor'],
                count_fn or _default_count_fn)
        out.append(rec)

    return {'db_path': db_path or ledger_ro.DEFAULT_DB, 'campaigns': out}


def list_campaigns(state=None, db_path=None):
    """Ledger-only campaign listing. No network."""
    if state is not None and state not in CAMPAIGN_STATES:
        raise ToolError(
            'invalid_argument',
            f'unknown state {state!r}',
            f'Expected one of {CAMPAIGN_STATES}.')
    camps = ledger_ro.campaigns(db_path, state=state)
    from utils.poms_entry import njobs_of
    listing = [{
        'id': c['id'],
        'state': c['state'],
        'tarball': c['tarball'],
        'map_path': c['map_path'],
        'cursor': c['cursor'],
        'njobs': njobs_of(c['entry']),
        'slice_size': c['slice_size'],
        'created_utc': c['created_utc'],
    } for c in camps]
    return {'count': len(listing), 'campaigns': listing}
