"""Read-only access to the submission ledger — MCP error-translation
adapter.

The read half itself lives in utils.submission_ledger (readonly=True
opens sqlite mode=ro, no DDL; the map_path->origin shim sits inside its
row shapers; `snapshot` is its one-transaction two-table read). This
module only translates the failure modes into ToolError with remedy
text, which utils/ cannot do (it must not import prodtools_mcp).

History: until 2026-08-28 this file was a ~160-line fork of the
writer's read half, because submission_ledger._connect issues DDL on
every connect and so could not serve a non-owner reader. The fork had
already drifted once — the origin shim existed only here, leaving the
CLI reader unprotected on an un-migrated ledger.
"""
import sqlite3

from prodtools_mcp.adapters import ToolError

from utils import submission_ledger as _sl

# Re-exported so callers need not import the writer module for a path
# or the row-state vocabulary.
from utils.submission_ledger import DEFAULT_DB  # noqa: F401
from utils.submission_ledger import STATES as ROW_STATES  # noqa: F401


def _translated(db_path, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError as exc:
        raise ToolError(
            'catalog_unavailable', str(exc),
            'Check MU2E_SUBMISSION_DB, or that the direct-submission '
            'subsystem has been run at least once.') from exc
    except sqlite3.Error as exc:
        raise ToolError(
            'catalog_unavailable',
            f'ledger query failed on {db_path}: {exc}',
            'The ledger schema may be older than this server expects, or '
            'a writer crash left a hot journal a reader cannot roll back.'
        ) from exc


def campaigns(db_path=None, state=None):
    """Campaign rows, newest last. `entry` is parsed from entry_json."""
    db_path = db_path or DEFAULT_DB
    camps = _translated(db_path, _sl.all_campaigns, db_path, readonly=True)
    if state is not None:
        camps = [c for c in camps if c.get('state') == state]
    return camps


def rows(db_path=None):
    """Submission rows, newest last. `entry` and `indices` are parsed."""
    db_path = db_path or DEFAULT_DB
    return _translated(db_path, _sl.all_rows, db_path, readonly=True)


def snapshot(db_path=None):
    """(campaigns, rows) from ONE transaction — see
    submission_ledger.snapshot for why two separate reads can disagree."""
    db_path = db_path or DEFAULT_DB
    return _translated(db_path, _sl.snapshot, db_path, readonly=True)
