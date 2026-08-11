"""Read-only access to the submission ledger.

utils.submission_ledger._connect issues DDL on every connect
(utils/submission_ledger.py:73-84): _SCHEMA, _CAMPAIGN_SCHEMA, and a
CREATE UNIQUE INDEX. Reading works today as a non-mu2epro user only
because every object already exists — creating a missing one raises
`OperationalError: attempt to write a readonly database`. A future
schema addition shipped before mu2epro's writer has run it would
otherwise break every campaign_status call.

This module opens the DB with sqlite's mode=ro URI and issues no DDL.
"""
import json
import os
import sqlite3
from urllib.request import pathname2url

from prodtools_mcp.adapters import ToolError

# Re-exported so callers need not import the writer module for a path
# or the row-state vocabulary.
from utils.submission_ledger import DEFAULT_DB  # noqa: F401
from utils.submission_ledger import STATES as ROW_STATES  # noqa: F401


def _connect(db_path):
    """Open the ledger read-only. No DDL, ever."""
    if not os.path.exists(db_path):
        raise ToolError(
            'catalog_unavailable',
            f'submission ledger not found: {db_path}',
            'Check MU2E_SUBMISSION_DB, or that the direct-submission '
            'subsystem has been run at least once.')
    uri = f'file:{pathname2url(db_path)}?mode=ro'
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise ToolError('catalog_unavailable',
                        f'cannot open ledger {db_path}: {exc}',
                        'Check filesystem access to the ledger.') from exc
    con.row_factory = sqlite3.Row
    return con


def _query(db_path, sql, params=()):
    con = _connect(db_path)
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    except sqlite3.Error as exc:
        raise ToolError(
            'catalog_unavailable',
            f'ledger query failed on {db_path}: {exc}',
            'The ledger schema may be older than this server expects, or '
            'a writer crash left a hot journal a reader cannot roll back.'
        ) from exc
    finally:
        con.close()


def _normalize_origin(row):
    """TRANSITION SHIM (2026-08-11, delete once every ledger has been
    touched by a writer at least once post-rename): the map_path->origin
    column rename (utils/submission_ledger.py) migrates on a WRITE
    connection only (_connect's PRAGMA-guarded ALTER TABLE) — this module
    deliberately opens mode=ro and issues no DDL (see module docstring),
    so it can be handed a ledger no writer has reconnected to since the
    rename shipped. Without this, status.py's unconditional
    camp['origin'] raises KeyError on such a ledger until the next cron
    tick / CLI invocation / write-server call happens to touch it — which
    may be never, for a personal or idle ledger. Normalize here so every
    caller downstream of ledger_ro always sees 'origin', regardless of
    which side of the migration the ledger is on.
    """
    if 'origin' not in row and 'map_path' in row:
        row['origin'] = row.pop('map_path')
    return row


def _shape_campaign(row):
    row['entry'] = json.loads(row.pop('entry_json'))
    return _normalize_origin(row)


def _shape_row(row):
    row['entry'] = json.loads(row.pop('entry_json'))
    row['indices'] = json.loads(row.pop('indices_json'))
    return _normalize_origin(row)


def campaigns(db_path=None, state=None):
    """Campaign rows, newest last. `entry` is parsed from entry_json."""
    db_path = db_path or DEFAULT_DB
    sql = 'SELECT * FROM campaigns'
    params = ()
    if state is not None:
        sql += ' WHERE state = ?'
        params = (state,)
    sql += ' ORDER BY id'
    return [_shape_campaign(r) for r in _query(db_path, sql, params)]


def rows(db_path=None):
    """Submission rows, newest last. `entry` and `indices` are parsed."""
    db_path = db_path or DEFAULT_DB
    return [_shape_row(r)
            for r in _query(db_path, 'SELECT * FROM submissions ORDER BY id')]


def snapshot(db_path=None):
    """(campaigns, rows) read through ONE connection and ONE transaction.

    Calling campaigns() and rows() separately takes two snapshots on two
    connections. The cron commits record_submission and advance_campaign
    independently (utils/submissions.py), so a read landing between them
    sees a `cursor` that disagrees with the rows it is reported beside —
    a campaign that looks under-submitted, or rows with no cursor to
    account for them.

    An explicit BEGIN opens a deferred transaction: the shared read lock
    is taken at the first SELECT and held until COMMIT, so both tables
    come from one coherent view. This is still read-only — no DDL, no
    write lock, and it does not take submissions.lock (spec, "Lock
    posture").
    """
    db_path = db_path or DEFAULT_DB
    con = _connect(db_path)
    try:
        con.execute('BEGIN')
        camps = [dict(r) for r in
                 con.execute('SELECT * FROM campaigns ORDER BY id')]
        subs = [dict(r) for r in
                con.execute('SELECT * FROM submissions ORDER BY id')]
        con.commit()
    except sqlite3.Error as exc:
        raise ToolError(
            'catalog_unavailable',
            f'ledger snapshot failed on {db_path}: {exc}',
            'The ledger schema may be older than this server expects, or '
            'a writer crash left a hot journal a reader cannot roll back.'
        ) from exc
    finally:
        con.close()
    return ([_shape_campaign(c) for c in camps],
            [_shape_row(r) for r in subs])
