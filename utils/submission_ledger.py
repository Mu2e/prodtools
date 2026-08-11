"""Submission ledger for the direct backend (recovery loop state).

One row per direct-backend submission (`json2jobdef --enqueue`, a
cron-fed slice, or a `submissions resubmit`), including the recovery
loop's own resubmissions (chained via parent_id, attempt+1). Only the
direct backend writes rows here (the retired POMS backend never did),
so the recovery loop races nothing by construction.

Stdlib sqlite3 ONLY — this module is imported by the submit path, which
runs as mu2epro in the bare ops environment (no pyenv ana, no
SQLAlchemy; see reference_pyenv_ana_for_db).

The DB lives at a stable absolute path: mu2epro submissions run from
throwaway /tmp workdirs, so a repo-relative path would scatter state.
An OPERATOR-SUPPLIED directory (MU2E_SUBMISSION_DB, --ledger-db) is
surfaced when missing (sqlite3.OperationalError), never silently
mkdir'd over — a typo must fail, not create a stray DB. A DERIVED
path from ledger_for() is created by ensure_ledger_dir(), since it
cannot be a typo.
"""
import getpass
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from utils.jobdesc import RESOURCE_KEYS, validate_entry_value

# Entry keys `submissions set-entry` may edit on a live campaign.
# Deliberately excludes tarball/njobs/firstjob/input_pattern: those
# define the campaign's identity and index space, so changing one in
# place corrupts a live campaign instead of fixing it. The correct
# operation there is cancel + re-enqueue.
#
# This is ledger policy — WHICH keys are safe to edit mid-flight. What
# a valid VALUE looks like is entry-format knowledge, owned by
# utils/jobdesc.validate_entry_value and shared with the json2jobdef
# boundary so the two cannot drift.
# Derived, not restated: a key added to RESOURCE_KEYS without being
# added here would be silently unvalidatable, and the reverse would let
# set-entry accept a key validate_entry_value ignores entirely.
EDITABLE_ENTRY_KEYS = ('inloc',) + RESOURCE_KEYS


PRODUCTION_DB = '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db'


def ledger_for(user=None):
    """Ledger path for a UNIX account.

    There is ONE production ledger everyone reads and N personal
    ledgers each person writes, so readers and writers resolve
    differently (see DEFAULT_DB below). For 'mu2epro' this returns
    PRODUCTION_DB exactly, which is why the split needs no migration
    and leaves the production cron untouched.
    """
    return (f'/exp/mu2e/data/users/{user or getpass.getuser()}'
            f'/prodtools/submissions.db')


def ensure_ledger_dir(db_path):
    """Create the parent directory of a DERIVED ledger path; return it.

    Called ONLY on a ledger_for() path. An operator-supplied path
    (MU2E_SUBMISSION_DB, --ledger-db) is deliberately never mkdir'd:
    a typo there would silently create a stray database instead of
    failing, which is why this module has always surfaced a missing
    directory rather than creating one. A derived path cannot be a
    typo, so the reasoning does not apply to it.

    Raises rather than falling back: writing a personal campaign into
    the production ledger is the worst available outcome.
    """
    parent = Path(db_path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"cannot create ledger directory {parent}: {exc}") from exc
    return db_path


# Readers resolve here and keep seeing production.
DEFAULT_DB = os.environ.get('MU2E_SUBMISSION_DB', PRODUCTION_DB)

# 'submitting' is a RESERVED row: its indices are claimed but
# jobsub_submit has not returned yet. It is deliberately not 'active' —
# the recovery loop must not try to verify a window that may never have
# launched. 'failed' is a reservation whose submit definitively failed;
# it stays in the DB because jobsub_submit can exit non-zero having
# already made a cluster, so its window is not proven free.
# 'reconciled' is the ONLY way out of either: a human has checked
# jobsub_q and asserted the window's jobs are genuinely absent (see
# reconcile_row). Nothing sets it automatically, and the row is kept
# rather than deleted so the audit trail of the failed attempt survives.
STATES = ('submitting', 'active', 'complete', 'recovered', 'exhausted',
          'failed', 'reconciled')

# States reconcile_row may close. Both are windows whose jobs may or may
# not exist; neither can be cleared by any automatic path.
RECONCILABLE_STATES = ('failed', 'submitting')

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
  origin       TEXT,
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
  origin       TEXT,
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
    # map_path -> origin (2026-08-11). The column is free-text provenance;
    # the map file it used to name no longer exists. RENAME COLUMN needs
    # sqlite >= 3.25 (deployed: 3.34.1). Idempotent: PRAGMA-guarded, so a
    # DB created fresh from _SCHEMA is left alone.
    for table in ('submissions', 'campaigns'):
        cols = [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
        if 'map_path' in cols and 'origin' not in cols:
            try:
                con.execute(
                    f'ALTER TABLE {table} RENAME COLUMN map_path TO origin')
            except sqlite3.OperationalError as exc:
                # Two _connect calls can race the check-then-act above: a
                # cron tick, a manual `submissions` command, and the
                # write MCP server can all touch the same never-migrated
                # ledger in the same minute. Both PRAGMA checks can see
                # map_path before either ALTER commits; whichever commits
                # first wins, and the loser's own ALTER then fails with
                # "no such column: map_path" — this is not SQLITE_BUSY,
                # so the connection's timeout=30 busy-retry does not
                # cover it. Re-check: if origin exists now, the other
                # side already finished the migration for us and there
                # is nothing left to do here.
                cols_now = [r[1] for r in
                           con.execute(f'PRAGMA table_info({table})')]
                if 'origin' in cols_now:
                    pass
                elif 'readonly database' in str(exc):
                    # The production ledger is world-readable but
                    # mu2epro-owned (0444 to everyone else); a
                    # non-mu2epro reader opening it hits this on the
                    # ALTER even though every other statement above is a
                    # no-op against an already-current schema. Reading is
                    # still the whole point of a status/query command, so
                    # leave the DB un-migrated and let ledger_ro's
                    # map_path->origin shim carry read consumers across
                    # the gap; a writer (which always has write access)
                    # will migrate it on its own next connect.
                    pass
                else:
                    # A genuinely broken schema, a locked/corrupt DB, or
                    # any other unrelated failure must still surface.
                    raise
    con.commit()
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _next_attempt(con, parent_id):
    """attempt number for a new row: parent's + 1, or 1 for an original.
    An unknown parent_id is a ValueError."""
    if parent_id is None:
        return 1
    parent = con.execute(
        'SELECT attempt FROM submissions WHERE id = ?',
        (parent_id,)).fetchone()
    if parent is None:
        raise ValueError(f"ledger has no row {parent_id} (parent)")
    return parent['attempt'] + 1


def record_submission(db_path, *, tarball, entry, indices, jobsub_id,
                      cluster_id, origin=None, parent_id=None):
    """Append one submission row; return its id.

    entry is snapshotted verbatim (recovery must survive map edits and
    vanished /tmp workdirs); indices are ABSOLUTE cnf indices, stored
    sorted. attempt = parent's attempt + 1 (1 for an original
    submission); an unknown parent_id is a ValueError.
    """
    con = _connect(db_path)
    try:
        attempt = _next_attempt(con, parent_id)
        cur = con.execute(
            'INSERT INTO submissions '
            '(created_utc, state, attempt, parent_id, origin, tarball, '
            ' entry_json, indices_json, jobsub_id, cluster_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (_now(), 'active', attempt, parent_id, origin, tarball,
             json.dumps(entry), json.dumps(sorted(indices)),
             jobsub_id, cluster_id))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def reserve_submission(db_path, *, tarball, entry, indices, origin=None,
                       parent_id=None):
    """Claim an index window BEFORE jobsub_submit runs; return the row id.

    This is what makes _slice_overlaps_ledger's "crash-window guard"
    claim true. Written after the fact, a row cannot cover the window
    between a successful jobsub_submit and the ledger write — there is
    nothing in the DB to overlap against, and the next tick re-submits
    the same deterministic payload as duplicate physics.

    Raising here is correct and load-bearing: if the window cannot be
    recorded, the submission must not happen.
    """
    con = _connect(db_path)
    try:
        attempt = _next_attempt(con, parent_id)
        cur = con.execute(
            'INSERT INTO submissions '
            '(created_utc, state, attempt, parent_id, origin, tarball, '
            ' entry_json, indices_json, jobsub_id, cluster_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)',
            (_now(), 'submitting', attempt, parent_id, origin, tarball,
             json.dumps(entry), json.dumps(sorted(indices))))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def attach_cluster(db_path, row_id, *, jobsub_id, cluster_id):
    """Promote a reserved row to 'active' once its cluster is known."""
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE submissions SET state = 'active', jobsub_id = ?, "
            "cluster_id = ? WHERE id = ? AND state = 'submitting'",
            (jobsub_id, cluster_id, row_id))
        if cur.rowcount != 1:
            raise ValueError(f"no reserved row {row_id} to attach a cluster to")
        con.commit()
    finally:
        con.close()


def fail_reservation(db_path, row_id, note):
    """Close a reserved row whose submit definitively failed.

    The row is kept, not deleted: jobsub_submit can exit non-zero having
    already created a cluster, so the window is not proven free and must
    keep blocking until a human reconciles it.
    """
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE submissions SET state = 'failed', closed_utc = ?, "
            "note = ? WHERE id = ? AND state = 'submitting'",
            (_now(), note, row_id))
        if cur.rowcount != 1:
            raise ValueError(f"no reserved row {row_id} to fail")
        con.commit()
    finally:
        con.close()


def reconcile_row(db_path, row_id, note):
    """Close a 'failed' or 'submitting' row after a HUMAN has checked
    jobsub_q; return the state it was in.

    This is the only exit from either state, and the only way a campaign
    blocked by one can ever move again: a failed reservation leaves a row
    covering [cursor, cursor+n), `_slice_overlaps_ledger` keeps seeing
    it, and top_up re-pauses the campaign on every tick — `submissions
    resume` alone can never clear that, because it is the ROW, not the
    cursor, that blocks. Before this existed the only escape was editing
    sqlite by hand.

    The safety property is preserved by making the call itself the
    assertion: jobsub_submit can exit non-zero having already made a
    cluster, so nobody but a human who has just looked at jobsub_q can
    say the window is free. Nothing in the tick calls this.
    """
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT state FROM submissions WHERE id = ?',
            (row_id,)).fetchone()
        if row is None:
            raise ValueError(f"ledger has no row {row_id}")
        if row['state'] not in RECONCILABLE_STATES:
            raise ValueError(
                f"row {row_id} is {row['state']!r}; only "
                f"{list(RECONCILABLE_STATES)} rows can be reconciled")
        con.execute(
            "UPDATE submissions SET state = 'reconciled', closed_utc = ?, "
            'note = ? WHERE id = ?', (_now(), note, row_id))
        con.commit()
        return row['state']
    finally:
        con.close()


def reserved_rows(db_path):
    """Rows still in 'submitting' — claimed windows with no cluster.

    A row that stays here is the needs-reconciliation case: someone must
    check jobsub_q before its window can be reused.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM submissions WHERE state = 'submitting' "
            "ORDER BY id").fetchall()
        return [_to_dict(r) for r in rows]
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


def row_by_id(db_path, row_id):
    """One row by id, entry/indices JSON parsed, or None."""
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT * FROM submissions WHERE id = ?', (row_id,)).fetchone()
        return _to_dict(row) if row is not None else None
    finally:
        con.close()


# States close_row may move an active row INTO. Deliberately NOT derived
# from STATES: 'submitting' is a pre-submit reservation and 'failed' is
# fail_reservation's alone, so a future addition to STATES must not
# silently become closable here.
_CLOSABLE_STATES = ('complete', 'recovered', 'exhausted')


def close_row(db_path, row_id, state, note=None):
    """Move an active row to a terminal state. Closing a non-active row
    (or to a non-terminal state) is a ValueError — state transitions are
    active → {complete, recovered, exhausted}, nothing else."""
    if state not in _CLOSABLE_STATES:
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


def create_campaign(db_path, *, tarball, entry, slice_size, origin=None):
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
            '(created_utc, state, origin, tarball, entry_json, cursor, '
            ' slice_size) VALUES (?, ?, ?, ?, ?, 0, ?)',
            (_now(), 'active', origin, tarball, json.dumps(entry),
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


def set_campaign_entry_key(db_path, camp_id, key, value,
                           include_open_rows=False):
    """Set one whitelisted key on a live campaign's entry snapshot;
    return (previous_value, changed_row_ids).

    Same live-retune contract as set_campaign_slice: active/paused only,
    binds from the next tick.

    By default this edits the CAMPAIGN's snapshot only, so it reaches
    future slices and nothing else — rows already dispatched keep the
    entry they were submitted with. That default is deliberate, and it
    is what `memory` depends on: an UNSET memory is exactly what earns a
    recovery the 4000MB floor (submissions.recovery_resource_kwargs), so
    cascading a memory value would silently forfeit the better failure
    mode.

    include_open_rows=True additionally rewrites the entry snapshot of
    every not-yet-closed row on this campaign's tarball, which is what
    makes RECOVERIES pick the change up (submissions.resubmit builds its
    SubmitOptions from row['entry'], not from the campaign). Rows match by
    tarball because the two tables carry no campaign_id; the partial
    unique index campaigns_live_tarball keeps that unambiguous for a
    live campaign, but a cancelled predecessor could have left an open
    row behind — so the changed ids are RETURNED, not just counted.

    `closed_utc IS NULL` rather than state='active' on purpose: a
    'submitting' row (reserved, cluster not yet attached) is still going
    to be recovered, so it needs the new value too.
    """
    if key not in EDITABLE_ENTRY_KEYS:
        raise ValueError(
            f"{key!r} is not editable; choose one of "
            f"{', '.join(EDITABLE_ENTRY_KEYS)}. Identity and index-space "
            f"keys (tarball, njobs, firstjob, input_pattern) define the "
            f"campaign — cancel and re-enqueue instead")
    validate_entry_value(key, value)
    con = _connect(db_path)
    try:
        row = con.execute(
            'SELECT state, tarball, entry_json FROM campaigns WHERE id = ?',
            (camp_id,)).fetchone()
        if row is None:
            raise ValueError(f"no campaign {camp_id}")
        if row['state'] not in ('active', 'paused'):
            raise ValueError(
                f"campaign {camp_id} is {row['state']} — {key} only "
                f"applies to an active or paused campaign")
        entry = json.loads(row['entry_json'])
        previous = entry.get(key)
        entry[key] = value
        con.execute('UPDATE campaigns SET entry_json = ? WHERE id = ?',
                    (json.dumps(entry), camp_id))
        changed = []
        if include_open_rows:
            open_ = con.execute(
                'SELECT id, entry_json FROM submissions '
                'WHERE tarball = ? AND closed_utc IS NULL ORDER BY id',
                (row['tarball'],)).fetchall()
            for r in open_:
                r_entry = json.loads(r['entry_json'])
                r_entry[key] = value
                con.execute(
                    'UPDATE submissions SET entry_json = ? WHERE id = ?',
                    (json.dumps(r_entry), r['id']))
                changed.append(r['id'])
        con.commit()
        return previous, changed
    finally:
        con.close()


def set_campaign_memory(db_path, camp_id, memory):
    """Back-compat alias for the 'memory' key; returns the previous
    value. Never cascades to rows — see set_campaign_entry_key for why
    that default is what protects the recovery floor."""
    previous, _ = set_campaign_entry_key(db_path, camp_id, 'memory', memory)
    return previous


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
