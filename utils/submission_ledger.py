"""Submission ledger for the direct backend (recovery loop state).

One row per `submit_map --backend direct` submission, including the
recovery loop's own resubmissions (chained via parent_id, attempt+1).
POMS-backend submissions never touch this ledger, so the recovery loop
cannot race POMS by construction.

Stdlib sqlite3 ONLY — this module is imported by the submit path, which
runs as mu2epro in the bare ops environment (no pyenv ana, no
SQLAlchemy; see reference_pyenv_ana_for_db).

The DB lives at a stable absolute path: mu2epro submissions run from
throwaway /tmp workdirs, so a repo-relative path would scatter state.
The parent directory is a one-time ops creation — a missing directory
is surfaced (sqlite3.OperationalError), never silently mkdir'd over.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get(
    'MU2E_SUBMISSION_DB',
    '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db')

STATES = ('active', 'complete', 'recovered', 'exhausted')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_utc  TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'active',
  attempt      INTEGER NOT NULL DEFAULT 1,
  parent_id    INTEGER REFERENCES submissions(id),
  map_path     TEXT,
  tarball      TEXT NOT NULL,
  entry_json   TEXT NOT NULL,
  indices_json TEXT NOT NULL,
  jobsub_id    TEXT,
  cluster_id   TEXT,
  closed_utc   TEXT,
  note         TEXT
)
"""


def _connect(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def record_submission(db_path, *, tarball, entry, indices, jobsub_id,
                      cluster_id, map_path=None, parent_id=None):
    """Append one submission row; return its id.

    entry is snapshotted verbatim (recovery must survive map edits and
    vanished /tmp workdirs); indices are ABSOLUTE cnf indices, stored
    sorted. attempt = parent's attempt + 1 (1 for an original
    submission); an unknown parent_id is a ValueError.
    """
    con = _connect(db_path)
    try:
        attempt = 1
        if parent_id is not None:
            parent = con.execute(
                'SELECT attempt FROM submissions WHERE id = ?',
                (parent_id,)).fetchone()
            if parent is None:
                raise ValueError(f"ledger has no row {parent_id} (parent)")
            attempt = parent['attempt'] + 1
        cur = con.execute(
            'INSERT INTO submissions '
            '(created_utc, state, attempt, parent_id, map_path, tarball, '
            ' entry_json, indices_json, jobsub_id, cluster_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (_now(), 'active', attempt, parent_id, map_path, tarball,
             json.dumps(entry), json.dumps(sorted(indices)),
             jobsub_id, cluster_id))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _to_dict(row):
    d = dict(row)
    d['entry'] = json.loads(d.pop('entry_json'))
    d['indices'] = json.loads(d.pop('indices_json'))
    return d


def open_rows(db_path):
    """Active rows, oldest first, entry/indices JSON parsed."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM submissions WHERE state = 'active' ORDER BY id"
        ).fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        con.close()


def all_rows(db_path):
    """Every row regardless of state, oldest first."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            'SELECT * FROM submissions ORDER BY id').fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        con.close()


def close_row(db_path, row_id, state, note=None):
    """Move an active row to a terminal state. Closing a non-active row
    (or to a non-terminal state) is a ValueError — state transitions are
    active → {complete, recovered, exhausted}, nothing else."""
    if state not in STATES or state == 'active':
        raise ValueError(f"invalid closing state: {state}")
    con = _connect(db_path)
    try:
        cur = con.execute(
            'UPDATE submissions SET state = ?, closed_utc = ?, note = ? '
            "WHERE id = ? AND state = 'active'",
            (state, _now(), note, row_id))
        if cur.rowcount != 1:
            raise ValueError(f"no active row {row_id} to close")
        con.commit()
    finally:
        con.close()
