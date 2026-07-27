"""Dataset discovery tools.

find_datasets reports a samweb DEFINITION listing, which is not the same
as existence: zero-file definitions appear and -LH/-CH variants do not.
Every response carries that caveat in `basis` so a caller cannot mistake
one for the other.
"""
from prodtools_mcp.adapters import ToolError, classify_catalog_error

_BASIS = ('samweb list-definitions: a definition listing, not an '
          'existence check — zero-file definitions appear and -LH/-CH '
          'variants do not. Pass require_files=True to filter to '
          'definitions with at least one file.')

# There are ~20,000 SAM definitions. FastMCP runs sync tools inline on
# the event loop, so an unbounded result — and especially the one serial
# HTTP round-trip per record that require_files costs — freezes the
# whole server, not just this call.
DEFAULT_LIMIT = 500

# `limit` itself was unbounded: a caller following the require_files
# refusal's own remedy ("raise limit deliberately") could set
# limit=100000 and get exactly the thousand-serial-query fan-out the
# refusal exists to prevent. This is a hard ceiling on the input, not a
# second truncation point — DEFAULT_LIMIT stays the default.
MAX_LIMIT = 5000


def _default_fetch_fn(pattern, user):
    from utils.latestDatasets import fetch_definitions
    return fetch_definitions(pattern, user)


def _default_count_fn(dataset):
    from utils.samweb_wrapper import dataset_file_count
    return dataset_file_count(dataset)


def _parse(name):
    """Split tier.owner.desc.dsconf.format. Returns None if it does not
    have the five prodtools fields."""
    parts = name.split('.')
    if len(parts) != 5:
        return None
    return {'name': name, 'tier': parts[0], 'owner': parts[1],
            'desc': parts[2], 'dsconf': parts[3], 'file_format': parts[4]}


def defname_query(campaign=None, tier=None, desc=None, pattern=None):
    """The `defname` filter handed to samweb.

    SAM's defname filter is a SQL LIKE: the wildcard is `%`, NOT `*`.
    Verified against the live catalog on 2026-07-26 —
    `--defname "cnf.mu2e.%.MDC2025au_best_v1_3.tar"` returns rows and the
    `*` form returns none. The repo already knows this
    (utils/jobdef_lookup.py:41, utils/chain_emit.py:264, EXAMPLES.md:495).

    Callers will type `*` anyway, so translate it. The filters are pushed
    into the defname rather than applied only client-side, so the server
    does not pull ~20,000 definitions to keep three.
    """
    if pattern:
        return pattern.replace('*', '%')
    return '.'.join([tier or '%', '%', desc or '%',
                     f'{campaign}%' if campaign else '%', '%'])


def find_datasets(campaign=None, tier=None, desc=None, pattern=None,
                  latest_only=False, require_files=False, user=None,
                  limit=DEFAULT_LIMIT, fetch_fn=None, count_fn=None):
    """Datasets matching the given filters, from the SAM definition list."""
    if (not isinstance(limit, int) or isinstance(limit, bool)
            or limit < 1 or limit > MAX_LIMIT):
        raise ToolError('invalid_argument',
                        f'limit must be a positive integer in '
                        f'1..{MAX_LIMIT}, got {limit!r}',
                        'Omit it for the default of '
                        f'{DEFAULT_LIMIT}; narrow the query with '
                        'campaign/tier/desc instead of raising limit '
                        'past the ceiling.')
    fetch = fetch_fn or _default_fetch_fn
    query = defname_query(campaign, tier, desc, pattern)
    try:
        names = fetch(query, user)
    except Exception as exc:
        raise classify_catalog_error(
            exc, f'samweb list-definitions failed: {exc}') from exc

    if latest_only:
        try:
            from utils.latestDatasets import latest_per_description
        except ImportError as exc:
            raise classify_catalog_error(
                exc, f'latest_only needs the ops environment: {exc}'
            ) from exc
        rows, _skipped = latest_per_description(names)
        names = [row[2] for row in rows]

    # Client-side filters are a correctness backstop, not the primary
    # mechanism: `_` is also a LIKE wildcard, and a caller-supplied
    # `pattern` is not narrowed by campaign/tier/desc at all.
    records = []
    for name in names:
        rec = _parse(name)
        if rec is None:
            continue
        if campaign and campaign not in rec['dsconf']:
            continue
        if tier and rec['tier'] != tier:
            continue
        if desc and rec['desc'] != desc:
            continue
        records.append(rec)

    records.sort(key=lambda r: r['name'])

    if require_files:
        # Refuse rather than issue thousands of serial round-trips. The
        # caller must narrow the query; silently counting the first
        # `limit` would answer a different question than the one asked.
        if len(records) > limit:
            raise ToolError(
                'invalid_argument',
                f'require_files=True would issue {len(records)} serial '
                f'SAM queries (limit {limit})',
                'Narrow the query with campaign/tier/desc, or raise '
                'limit deliberately.')
        count = count_fn or _default_count_fn
        kept = []
        for rec in records:
            try:
                if count(rec['name']) > 0:
                    kept.append(rec)
            except Exception as exc:
                raise classify_catalog_error(
                    exc, f'file count failed for {rec["name"]}: {exc}'
                ) from exc
        records = kept

    truncated = len(records) > limit
    records = records[:limit]
    return {'count': len(records), 'truncated': truncated,
            'limit': limit, 'basis': _BASIS, 'datasets': records}


def _default_summary_fn(dataset):
    from utils.samweb_wrapper import dataset_summary
    return dataset_summary(dataset)


def _default_created_fn(dataset):
    from utils.samweb_wrapper import definition_creation_date
    return definition_creation_date(dataset)


def dataset_details(dataset, summary_fn=None, created_fn=None):
    """File/event/size counts for one dataset, plus its creation date.

    `exists` is decided by file count, not by definition listing.
    `created_utc` is nullable: definition_creation_date returns None for
    exactly the metadata-only -LH/-CH datasets, and dataset_summary
    carries no creation time of its own.
    """
    try:
        summary = (summary_fn or _default_summary_fn)(dataset) or {}
    except Exception as exc:
        raise classify_catalog_error(
            exc, f'dataset summary failed for {dataset}: {exc}') from exc

    try:
        created = (created_fn or _default_created_fn)(dataset)
    except Exception:
        created = None      # creation date is decoration, not the answer

    file_count = summary.get('file_count', 0) or 0
    return {
        'dataset': dataset,
        'exists': file_count > 0,
        'file_count': file_count,
        'event_count': summary.get('total_event_count', 0) or 0,
        'total_size_bytes': summary.get('total_file_size', 0) or 0,
        'created_utc': created.isoformat() if created is not None else None,
    }
