"""Campaign status tools.

Composed from the read-only ledger plus, optionally, a live HTCondor
queue snapshot and SAM output counts. The bare call is ledger-only: a
23-row ledger fanned out to one SAM count per output dataset would
exceed the client's timeout.
"""
from prodtools_mcp import condor, ledger_ro
from prodtools_mcp.adapters import ToolError

CAMPAIGN_STATES = ('active', 'complete', 'paused', 'cancelled')


def queue_block(cluster_ids, clusters):
    """Queue counts for a campaign's clusters from a
    condor.query_owner_jobs() snapshot: {cluster_id: [{'JobStatus',
    'HoldReasonCode', 'HoldReason'}, ...]}.

    A None snapshot means the query could not be trusted. It returns
    state='unknown' and OMITS the count keys entirely — there must be no
    zero to misread. Proc-form jobsub_q was verified on 2026-07-22
    reporting 0 total while 1976 jobs of one cluster ran; a {"running": 0}
    from a failed query reads as 'drained' and could trigger a recovery
    pass against live jobs. condor.query_owner_jobs() preserves this same
    fail-closed contract (timeout or any unreachable schedd -> None).

    When held > 0, adds `hold_reasons`: the held jobs' HoldReasonCode
    breakdown (see condor.hold_reasons — grouped by CODE, never by the
    HoldReason text, which embeds a unique slot/host per job).
    """
    if clusters is None:
        return {'state': 'unknown',
                'reason': 'HTCondor queue query failed, timed out, or '
                          'could not reach every schedd'}
    running = idle = held = 0
    seen = []
    held_jobs = []
    for cid in cluster_ids:
        jobs = clusters.get(str(cid))
        if not jobs:
            continue
        seen.append(str(cid))
        for job in jobs:
            st = job.get('JobStatus')
            if st == condor.RUNNING:
                running += 1
            elif st == condor.IDLE:
                idle += 1
            elif st == condor.HELD:
                held += 1
                held_jobs.append(job)
            # Anything else (removed/completed/transferring/suspended)
            # is neither a live nor a held job — not counted.
    block = {'state': 'known', 'running': running, 'idle': idle,
            'held': held, 'clusters': seen}
    if held:
        block['hold_reasons'] = condor.hold_reasons(held_jobs)
    return block


def _draining_outputs_block(rows, tarball, count_fn, job_pars_fn):
    """Produced-vs-dispatched per output dataset, for a DRAINING campaign.

    A draining entry's `outputs[].dataset` is the worker's cwd filename
    GLOB (`mcs.*.art`), not a dataset name — feeding it to a SAM
    dimension raises a parse error, which is why every draining campaign
    read `state: unknown` before this branch existed. The real output
    dataset names come from `expected_outputs_for`, the same worker-owned
    substitution `verify_files_row` verifies against, so status and
    verification cannot drift.

    There is no `expected_at_completion`: the input dataset is still
    growing, so the denominator does not exist yet. `dispatched` is the
    honest one — input files handed to the grid that map to this output
    dataset. `produced` is the whole output dataset's SAM count, which
    can exceed `dispatched` when an earlier round or a smoke wrote into
    the same dataset.

    Cost: one SAM count per output dataset DISPATCHED SO FAR (3 early in
    a campaign, one per desc by the end). include_outputs=False skips it.
    """
    # Mu2eName (the path-grammar owner), NOT utils.submissions._dataset_of:
    # importing the submission engine into a read-only server would drag in
    # the whole submit/recovery stack for a one-line name parse.
    from utils.job_common import Mu2eName, expected_outputs_for

    def dataset_of(fname):
        return str(Mu2eName.parse(fname).dataset)

    dispatched = [f for row in (rows or []) for f in (row['indices'] or [])]
    if not dispatched:
        return {'state': 'known', 'datasets': [],
                'note': 'no inputs dispatched yet'}

    by_input_ds = {}
    for fname in dispatched:
        try:
            by_input_ds.setdefault(dataset_of(fname), []).append(fname)
        except Exception as exc:
            return {'state': 'unknown',
                    'reason': f'unparsable input file name {fname!r}: {exc}'}

    try:
        job_pars = job_pars_fn(tarball)
    except Exception as exc:
        return {'state': 'unknown',
                'reason': f'cannot read cnf {tarball}: {exc}'}

    counts = {}
    for input_ds, files in by_input_ds.items():
        try:
            outs = expected_outputs_for(files[0], job_pars)
        except Exception as exc:
            return {'state': 'unknown',
                    'reason': f'cannot map outputs for {input_ds}: {exc}'}
        for out in outs:
            out_ds = dataset_of(out)
            counts[out_ds] = counts.get(out_ds, 0) + len(files)

    datasets = []
    for dataset in sorted(counts):
        try:
            produced = count_fn(dataset)
        except Exception as exc:
            return {'state': 'unknown',
                    'reason': f'SAM count failed for {dataset}: {exc}'}
        datasets.append({'dataset': dataset,
                         'dispatched': counts[dataset],
                         'produced': produced})
    return {'state': 'known', 'datasets': datasets}


def _outputs_block(entry, njobs, submitted, count_fn, rows=None,
                   tarball=None, job_pars_fn=None):
    """Produced-vs-expected per output dataset.

    Draining campaigns take a different path entirely — see
    _draining_outputs_block.

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
    from utils.poms_entry import is_draining, outputs_of
    if is_draining(entry):
        return _draining_outputs_block(
            rows, tarball, count_fn, job_pars_fn or _default_job_pars_fn)
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


def _default_clusters_fn():
    """condor.query_owner_jobs(), the MCP server's own path — direct
    ClassAd queries, independent of utils.submissions.live_clusters()
    (which backs the live production cron and stays untouched). Already
    bounded and already fail-closed to None on any timeout or
    unreachable schedd; nothing to add here."""
    return condor.query_owner_jobs()


def _default_count_fn(dataset):
    from utils.samweb_wrapper import dataset_file_count
    return dataset_file_count(dataset)


def _default_job_pars_fn(tarball):
    """Load a campaign's cnf so draining outputs can be named. Raises on
    an unlocatable/unreadable tarball — the caller turns that into
    state='unknown', never a zero count."""
    import os
    from utils.jobquery import Mu2eJobPars
    from utils.file_resolver import sam_physical_path_or_none
    path = sam_physical_path_or_none(tarball)
    if not path or not os.path.exists(path):
        raise RuntimeError('tarball not locatable in SAM')
    return Mu2eJobPars(path)


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
                    clusters_fn=None, count_fn=None, job_pars_fn=None):
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
        # Rows back both blocks: the queue reads their cluster ids, and a
        # draining campaign's output DATASETS are only discoverable from
        # the input filenames they dispatched.
        mine = ([r for r in all_rows if r['tarball'] == camp['tarball']]
                if (want_queue or want_outputs) else [])
        if want_queue:
            rec['rows'] = _row_counts(mine)
            cluster_ids = [r['cluster_id'] for r in mine if r['cluster_id']]
            rec['queue'] = queue_block(cluster_ids, clusters)
        if want_outputs:
            rec['outputs'] = _outputs_block(
                camp['entry'], njobs, camp['cursor'],
                count_fn or _default_count_fn, rows=mine,
                tarball=camp['tarball'], job_pars_fn=job_pars_fn)
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
