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

# Re-exported so callers need not import the writer module for a path.
from utils.submission_ledger import DEFAULT_DB  # noqa: F401


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


def campaigns(db_path=None, state=None):
    """Campaign rows, newest last. `entry` is parsed from entry_json."""
    db_path = db_path or DEFAULT_DB
    sql = 'SELECT * FROM campaigns'
    params = ()
    if state is not None:
        sql += ' WHERE state = ?'
        params = (state,)
    sql += ' ORDER BY id'
    out = []
    for row in _query(db_path, sql, params):
        row['entry'] = json.loads(row.pop('entry_json'))
        out.append(row)
    return out


def rows(db_path=None):
    """Submission rows, newest last. `entry` and `indices` are parsed."""
    db_path = db_path or DEFAULT_DB
    out = []
    for row in _query(db_path, 'SELECT * FROM submissions ORDER BY id'):
        row['entry'] = json.loads(row.pop('entry_json'))
        row['indices'] = json.loads(row.pop('indices_json'))
        out.append(row)
    return out
