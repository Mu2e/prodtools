"""In-process HTCondor ClassAd queries for the MCP server's queue block.

This is an INDEPENDENT path from utils/submissions.py. live_clusters()
and cluster_queue_state() there back the live production cron (the
verify/resubmit/top-up tick) and are deliberately fail-closed after a
real incident; they are not touched by this module, and this module
does not touch them. The MCP server gets its own query path so it can
carry HoldReasonCode — jobsub_q's human-formatted table (what
live_clusters parses) has the one-letter state but never the hold
reason.

Why this is possible now: the system RPM htcondor is py3.9-only, but
PyPI ships a cp310 manylinux wheel, and htcondor==23.0.28 matches the
pool's running version (verified 2026-07-26). mcp/pyproject.toml pins
htcondor==23.0.* as a venv dependency for exactly this reason.

htcondor itself is imported lazily, inside the two functions that talk
to the real pool (_locate_jobsub_schedds, _query_schedd) — never at
module level. That keeps this module importable, and its query logic
fully testable via injected fakes, on interpreters that never see the
real htcondor package (e.g. the plain-python3.9 unit-test run).
"""
import concurrent.futures
import re
import subprocess

OWNER = 'mu2epro'

# FastMCP runs sync tools inline on the event loop (the same class of
# bug the trace_provenance fan-out hit): an unbounded multi-schedd
# query would freeze the whole server. Bound the whole query, not just
# one schedd's slice of it.
QUERY_TIMEOUT_S = 60

# HTCondor JobStatus ClassAd values. Only these three are queried —
# 3/4/6/7 (removed/completed/transferring/suspended) are not open
# queue states worth carrying back.
IDLE = 1
RUNNING = 2
HELD = 5

# Server-side projection: fetch only what queue_block needs. Pulling
# whole ClassAds (hundreds of attributes) across ~5 schedds for
# thousands of jobs is needless network and memory cost.
_PROJECTION = ['ClusterId', 'JobStatus', 'HoldReasonCode', 'HoldReason']

_CONSTRAINT_TEMPLATE = (
    'Owner=="{owner}" && '
    '(JobStatus=={idle} || JobStatus=={running} || JobStatus=={held})'
)


def _is_jobsub_schedd(ad):
    """True for a schedd ClassAd whose Name starts with 'jobsub' — the
    pool advertises 8 daemons total (collector, negotiator, other
    schedds, ...); only ~5 are the jobsub_lite schedds that actually
    carry mu2epro's jobs (verified 2026-07-26). A pure predicate, split
    out from _locate_jobsub_schedds so the filter is testable without a
    real htcondor.Collector."""
    return str(ad.get('Name', '')).startswith('jobsub')


def _locate_jobsub_schedds():
    """Schedd location ClassAds for the pool's jobsub_lite schedds
    (see _is_jobsub_schedd)."""
    import htcondor
    coll = htcondor.Collector()
    ads = coll.locateAll(htcondor.DaemonTypes.Schedd)
    return [ad for ad in ads if _is_jobsub_schedd(ad)]


def _query_schedd(schedd_ad, owner):
    """Idle/running/held ClassAds for `owner` on one schedd, as plain
    dicts (never htcondor ClassAd objects — those don't belong outside
    this module). Filtering happens SERVER-SIDE in the constraint, not
    by fetching everything and filtering in Python."""
    import htcondor
    schedd = htcondor.Schedd(schedd_ad)
    constraint = _CONSTRAINT_TEMPLATE.format(
        owner=owner, idle=IDLE, running=RUNNING, held=HELD)
    ads = schedd.query(constraint, projection=_PROJECTION)
    return [{'ClusterId': ad.get('ClusterId'),
             'JobStatus': ad.get('JobStatus'),
             'HoldReasonCode': ad.get('HoldReasonCode'),
             'HoldReason': ad.get('HoldReason')} for ad in ads]


def query_owner_jobs(owner=OWNER, timeout=QUERY_TIMEOUT_S,
                     schedds_fn=_locate_jobsub_schedds,
                     query_fn=_query_schedd):
    """{cluster_id: [{'JobStatus', 'HoldReasonCode', 'HoldReason'}, ...]}
    for every idle/running/held job `owner` has across the pool's
    jobsub* schedds, or None when the result cannot be trusted.

    Trust rules, matching the convention queue_block already
    understands (None -> 'unknown', never a zero):
    - schedd discovery failing -> None (can't even enumerate schedds).
    - any per-schedd query raising -> the WHOLE result is None. A
      partial result that silently drops one schedd's jobs is an
      undercount, and an undercount reads as "drained" — exactly the
      failure mode that could trigger a recovery pass against jobs
      that are still live on the schedd this call failed to reach.
    - the whole query not finishing inside `timeout` -> None. Slow is
      indistinguishable from wrong here: this call runs inline on
      FastMCP's event loop, so it must return within the bound one way
      or another, and a timeout is exactly the case that must never
      serialize as a count.

    Queries run in parallel (one thread per schedd) so the wall clock
    is close to the slowest single schedd, not the sum of all of them.
    """
    try:
        schedds = schedds_fn()
    except Exception:
        return None
    if not schedds:
        return None

    clusters = {}
    untrustworthy = False
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=len(schedds))
    try:
        futures = {executor.submit(query_fn, sd, owner): sd for sd in schedds}
        try:
            for fut in concurrent.futures.as_completed(futures,
                                                        timeout=timeout):
                try:
                    for ad in fut.result():
                        cid = str(ad.get('ClusterId'))
                        clusters.setdefault(cid, []).append(ad)
                except Exception:
                    untrustworthy = True
        except concurrent.futures.TimeoutError:
            untrustworthy = True
    finally:
        # wait=False: don't block server shutdown-of-this-call on a
        # straggler thread stuck inside a slow/hung schedd query — the
        # timeout above already decided the result is untrustworthy.
        executor.shutdown(wait=False)

    if untrustworthy:
        return None
    return clusters


def hold_reasons(jobs):
    """[{'code', 'count', 'example'}, ...] sorted by count descending,
    aggregated by HoldReasonCode — NEVER by the HoldReason text. The
    text embeds the slot and host ("Error from slot1_26@fnpc19131...:
    ..."), so every job's string is unique and grouping by it returns
    one entry per job instead of one per failure mode. `example` is one
    representative reason string, truncated, and deliberately singular
    (not a list) so it can't be mistaken for an aggregate."""
    by_code = {}
    for job in jobs:
        code = job.get('HoldReasonCode')
        entry = by_code.setdefault(code, {'code': code, 'count': 0,
                                          'example': None})
        entry['count'] += 1
        if entry['example'] is None:
            reason = job.get('HoldReason')
            if reason:
                entry['example'] = reason[:200]
    return sorted(by_code.values(), key=lambda e: e['count'], reverse=True)


CONDOR_VERSION_BIN = '/usr/bin/condor_version'

_VERSION_RE = re.compile(r'\$CondorVersion:\s*(\d+\.\d+\.\d+)')


def parse_version(text):
    """'25.0.12' out of a `$CondorVersion: ... $` banner, or None.

    None rather than a guess: an unreadable version must stay unknown.
    A plausible default is what let a stale pin sit undetected."""
    if not text:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def series(version):
    """'25.0' out of '25.0.12' — the major.minor the wheel pin uses.
    None in, None out."""
    if not version:
        return None
    return '.'.join(version.split('.')[:2])


def _client_version_banner():
    """The bindings' own version banner. Imported lazily like every
    other htcondor use in this module, so the unit suite still runs
    where no wheel is installed."""
    import htcondor2
    return htcondor2.version()


def _node_version_banner():
    """condor_version(1) from its ABSOLUTE path.

    Absolute because `muse setup ops` rewrites PATH, and the version
    that matters is the node's own client RPM — the one jobsub_lite
    uses and the best local proxy for what the pool will accept.

    stdin=DEVNULL is mandatory, not tidiness: this can run inside an
    MCP server whose stdin IS the JSON-RPC channel, and a child that
    inherits it can consume protocol bytes."""
    completed = subprocess.run(
        [CONDOR_VERSION_BIN], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10, check=True)
    return completed.stdout.decode('utf-8', 'replace')


def _read_version(fn, label):
    """(version or None, error text or None) for one side."""
    try:
        banner = fn()
    except Exception as exc:
        return None, f'{label}: {type(exc).__name__}: {exc}'
    version = parse_version(banner)
    if version is None:
        return None, f'{label}: unparseable version banner {banner!r}'
    return version, None


def version_report(client_fn=_client_version_banner,
                   node_fn=_node_version_banner):
    """{'client', 'node', 'series_match', 'reason'} comparing the
    installed bindings against this node's condor client.

    `series_match` is True/False only when BOTH versions are known;
    when either side is unreadable it is None — never True. Claiming an
    agreement that was not verified is the exact failure this reporting
    exists to prevent.

    `reason` is None when both are known and the series agree; otherwise
    human-readable text naming what is wrong. It is diagnostic output,
    never a control-flow signal."""
    client, client_err = _read_version(client_fn, 'client bindings')
    node, node_err = _read_version(node_fn, CONDOR_VERSION_BIN)

    if client is None or node is None:
        errs = [e for e in (client_err, node_err) if e]
        return {'client': client, 'node': node, 'series_match': None,
                'reason': 'cannot compare HTCondor versions — '
                          + '; '.join(errs)}

    if series(client) == series(node):
        return {'client': client, 'node': node, 'series_match': True,
                'reason': None}

    return {
        'client': client, 'node': node, 'series_match': False,
        'reason': (f'HTCondor client bindings {client} do not match this '
                   f'node\'s condor {node}; the pool rejects the older '
                   f'client\'s authentication. Rerun mcp/scripts/install.sh '
                   f'to reinstall the matching wheel series.'),
    }
