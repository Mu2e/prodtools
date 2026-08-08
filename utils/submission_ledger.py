"""Submission ledger for the direct backend (recovery loop state).

One row per `submit_map` submission, including the
recovery loop's own resubmissions (chained via parent_id, attempt+1).
Only submit_map writes rows here (the retired POMS backend never did),
so the recovery loop races nothing by construction.

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
import re
import sqlite3
from datetime import datetime, timezone

# jobsub_submit's --memory grammar, as the house format uses it
# ('2500MB' in jobsub_argv.DEFAULT_MEMORY, '4000MB' in
# submissions.RECOVERY_MEMORY). Anchored: 'lots' and '3000 MB' are
# rejected rather than passed through to fail at submit time.
_MEMORY_RE = re.compile(r'^\d+(MB|GB)$')

DEFAULT_DB = os.environ.get(
    'MU2E_SUBMISSION_DB',
    '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db')

STATES = ('active', 'complete', 'recovered', 'exhausted')

CAMPAIGN_STATES = ('active', 'complete', 'paused', 'cancelled')

# Sliced-submission campaign lifecycle. 'complete' means fully SUBMITTED
# (verification continues per ledger row); 'paused' is the operator /
# submit-failure hold; 'cancelled' closes the campaign but already-
# submitted rows still get recovered.
_CAMPAIGN_TRANSITIONS = {
    'active': ('complete', 'paused', 'cancelled'),
    'paused': ('active', 'cancelled'),
}

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

_CAMPAIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_utc  TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'active',
  map_path     TEXT,
  tarball      TEXT NOT NULL,
  entry_json   TEXT NOT NULL,
  cursor       INTEGER NOT NULL DEFAULT 0,
  slice_size   INTEGER NOT NULL,
  closed_utc   TEXT,
  note         TEXT
)
"""


def _connect(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    con.execute(_CAMPAIGN_SCHEMA)
    # Closes the SELECT-then-INSERT race in create_campaign: a paused
    # campaign still owns its index space (enqueue-after-pause then
    # resume would feed two campaigns into the same indices = double
    # submit), so the live set is active+paused, not just active.
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS campaigns_live_tarball "
        "ON campaigns(tarball) WHERE state IN ('active','paused')")
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


def create_campaign(db_path, *, tarball, entry, slice_size, map_path=None):
    """Register a sliced-submission campaign (cursor 0); return its id.

    entry is snapshotted verbatim — the caller has already merged any
    CLI resource overrides, so slices reproduce what was asked for. A
    second ACTIVE or PAUSED campaign for the same tarball is refused:
    that is the double-submit guard. A paused campaign still owns its
    index space — enqueueing on top of it and later resuming both would
    feed two campaigns into the same indices. The
    campaigns_live_tarball unique index (see _connect) backstops this
    check against the SELECT-then-INSERT race.
    """
    if slice_size < 1:
        raise ValueError(f"slice_size must be >= 1, got {slice_size}")
    con = _connect(db_path)
    try:
        dup = con.execute(
            "SELECT id, state FROM campaigns WHERE tarball = ? "
            "AND state IN ('active', 'paused')",
            (tarball,)).fetchone()
        if dup:
            raise ValueError(
                f"{dup['state']} campaign {dup['id']} already exists for "
                f"{tarball}")
        cur = con.execute(
            'INSERT INTO campaigns '
            '(created_utc, state, map_path, tarball, entry_json, cursor, '
            ' slice_size) VALUES (?, ?, ?, ?, ?, 0, ?)',
            (_now(), 'active', map_path, tarball, json.dumps(entry),
             slice_size))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _campaign_to_dict(row):
    d = dict(row)
    d['entry'] = json.loads(d.pop('entry_json'))
    return d


def active_campaigns(db_path):
    """Active campaigns, oldest first, entry JSON parsed."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM campaigns WHERE state = 'active' ORDER BY id"
        ).fetchall()
        return [_campaign_to_dict(r) for r in rows]
    finally:
        con.close()


def all_campaigns(db_path):
    """Every campaign regardless of state, oldest first."""
    con = _connect(db_path)
    try:
        rows = con.execute('SELECT * FROM campaigns ORDER BY id').fetchall()
        return [_campaign_to_dict(r) for r in rows]
    finally:
        con.close()


def advance_campaign(db_path, camp_id, new_cursor):
    """Move an active campaign's cursor forward. Backward moves and
    non-active campaigns raise — the cursor only ever advances."""
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE campaigns SET cursor = ? "
            "WHERE id = ? AND state = 'active' AND cursor <= ?",
            (new_cursor, camp_id, new_cursor))
        if cur.rowcount != 1:
            raise ValueError(
                f"no active campaign {camp_id} with cursor <= {new_cursor}")
        con.commit()
    finally:
        con.close()


def set_campaign_slice(db_path, camp_id, slice_size):
    """Retune a live campaign's batch size; return the previous value.

    Only active/paused campaigns accept it — a closed campaign's slice
    is never read again, so silently accepting one would report success
    for a no-op. The value binds from the next tick: batches already
    submitted keep the size they were dispatched with, since a ledger
    row records the indices it actually sent."""
    if slice_size < 1:
        raise ValueError(f"slice_size must be >= 1, got {slice_size}")
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT state, slice_size FROM campaigns WHERE id = ?',
            (camp_id,)).fetchone()
        if row is None:
            raise ValueError(f"no campaign {camp_id}")
        if row['state'] not in ('active', 'paused'):
            raise ValueError(
                f"campaign {camp_id} is {row['state']} — slice_size only "
                f"applies to an active or paused campaign")
        con.execute('UPDATE campaigns SET slice_size = ? WHERE id = ?',
                    (slice_size, camp_id))
        con.commit()
        return row['slice_size']
    finally:
        con.close()


def set_campaign_memory(db_path, camp_id, memory):
    """Set the memory request on a live campaign's entry; return the
    previous value (None if the entry never named one).

    Same live-retune contract as set_campaign_slice: active/paused only,
    binds from the next tick. It edits the CAMPAIGN's entry snapshot, so
    it reaches future slices only — ledger rows already dispatched keep
    the entry they were submitted with, and their recoveries therefore
    keep the recovery floor (see submissions.recovery_resource_argv).

    The format is validated here rather than at submit time: an
    unparseable value would otherwise sit in the ledger looking applied
    and only surface as a jobsub_submit rejection a tick later.
    """
    if not _MEMORY_RE.match(str(memory)):
        raise ValueError(
            f"memory must look like '3000MB' or '4GB', got {memory!r}")
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT state, entry_json FROM campaigns WHERE id = ?',
            (camp_id,)).fetchone()
        if row is None:
            raise ValueError(f"no campaign {camp_id}")
        if row['state'] not in ('active', 'paused'):
            raise ValueError(
                f"campaign {camp_id} is {row['state']} — memory only "
                f"applies to an active or paused campaign")
        entry = json.loads(row['entry_json'])
        previous = entry.get('memory')
        entry['memory'] = memory
        con.execute('UPDATE campaigns SET entry_json = ? WHERE id = ?',
                    (json.dumps(entry), camp_id))
        con.commit()
        return previous
    finally:
        con.close()


def set_campaign_state(db_path, camp_id, state, note=None):
    """Validated campaign transition (see _CAMPAIGN_TRANSITIONS).
    Reactivating a paused campaign clears closed_utc and PRESERVES the
    existing note (the pause reason); other transitions overwrite the
    note."""
    if state not in CAMPAIGN_STATES:
        raise ValueError(f"invalid campaign state: {state}")
    allowed_from = tuple(f for f, targets in _CAMPAIGN_TRANSITIONS.items()
                         if state in targets)
    con = _connect(db_path)
    try:
        row = con.execute('SELECT state FROM campaigns WHERE id = ?',
                          (camp_id,)).fetchone()
        if row is None:
            raise ValueError(f"no campaign {camp_id}")
        if row['state'] not in allowed_from:
            raise ValueError(
                f"campaign {camp_id}: cannot go {row['state']} -> {state}")
        if state == 'active':
            # Resume: KEEP the note that explains why it was paused —
            # the operator clearing the pause is exactly who needs it.
            con.execute(
                'UPDATE campaigns SET state = ?, closed_utc = NULL '
                'WHERE id = ?', (state, camp_id))
        else:
            con.execute(
                'UPDATE campaigns SET state = ?, closed_utc = ?, '
                'note = ? WHERE id = ?',
                (state, _now(), note, camp_id))
        con.commit()
    finally:
        con.close()
