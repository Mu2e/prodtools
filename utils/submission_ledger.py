"""Submission ledger for the direct backend (recovery loop state).

One row per direct-backend submission (`json2jobdef --enqueue`, a
cron-fed slice, or `submissions resubmit`), including recovery-loop
resubmissions (chained via parent_id, attempt+1). Only the direct
backend writes rows here (the retired POMS backend never did), so the
recovery loop races nothing.

Stdlib sqlite3 ONLY — imported by the submit path, which runs as
mu2epro in the bare ops environment (no pyenv ana, no SQLAlchemy).

DB lives at a stable absolute path: mu2epro runs from throwaway /tmp
workdirs, so a repo-relative path would scatter state. An
OPERATOR-SUPPLIED directory (MU2E_SUBMISSION_DB, --ledger-db) is
surfaced when missing, never silently mkdir'd — a typo must fail, not
create a stray DB. A DERIVED path from ledger_for() IS created by
ensure_ledger_dir(), since it cannot be a typo.
"""
import getpass
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from utils.jobdesc import ENTRY_VALUE_KEYS, validate_entry_value

# Entry keys `submissions set-entry` may edit on a live campaign.
# Excludes tarball/njobs/firstjob/input_pattern: those define the
# campaign's identity and index space, so editing in place corrupts it
# instead of fixing it — use cancel + re-enqueue there.
#
# WHICH keys are editable is ledger policy; what a valid VALUE looks
# like is owned by utils/jobdesc.validate_entry_value, shared with the
# json2jobdef boundary so the two cannot drift. Derived from
# ENTRY_VALUE_KEYS, not restated, so a key validate_entry_value doesn't
# know cannot slip into the editable set unvalidated.
EDITABLE_ENTRY_KEYS = ENTRY_VALUE_KEYS


PRODUCTION_DB = '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db'


def ledger_for(user=None):
    """Ledger path for a UNIX account.

    One production ledger everyone reads, N personal ledgers each
    person writes — readers and writers resolve differently (see
    DEFAULT_DB). For 'mu2epro' this returns PRODUCTION_DB exactly, so
    the split needed no migration and leaves the production cron
    untouched.
    """
    return (f'/exp/mu2e/data/users/{user or getpass.getuser()}'
            f'/prodtools/submissions.db')


def ensure_ledger_dir(db_path):
    """Create the parent directory of a DERIVED ledger path; return it.

    Called ONLY on a ledger_for() path. An operator-supplied path
    (MU2E_SUBMISSION_DB, --ledger-db) is never mkdir'd: a typo there
    would silently create a stray database instead of failing. A
    derived path cannot be a typo, so that reasoning doesn't apply here.

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

# 'submitting' = a RESERVED row: indices claimed, jobsub_submit not yet
# returned. Deliberately not 'active' — the recovery loop must not
# verify a window that may never have launched. 'failed' = a reservation
# whose submit definitively failed; kept in the DB because jobsub_submit
# can exit non-zero having already made a cluster, so the window isn't
# proven free. 'reconciled' is the ONLY way out of either: a human
# checked jobsub_q and asserted the jobs are genuinely absent (see
# reconcile_row). Nothing sets it automatically; the row stays for the
# audit trail.
STATES = ('submitting', 'active', 'complete', 'recovered', 'exhausted',
          'failed', 'reconciled')

# States reconcile_row may close: windows whose jobs may or may not
# exist, neither clearable by an automatic path.
RECONCILABLE_STATES = ('failed', 'submitting')

CAMPAIGN_STATES = ('active', 'complete', 'paused', 'cancelled')

# Sliced-submission campaign lifecycle. 'complete' = fully SUBMITTED
# (verification continues per ledger row); 'paused' = operator or
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
    # campaign still owns its index space (resume after enqueue-on-top
    # would double-submit the same indices), so the live set is
    # active+paused, not just active.
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS campaigns_live_tarball "
        "ON campaigns(tarball) WHERE state IN ('active','paused')")
    # map_path -> origin (2026-08-11): free-text provenance; the map file
    # it used to name no longer exists. RENAME COLUMN needs sqlite >=
    # 3.25 (deployed: 3.34.1). PRAGMA-guarded/idempotent — a fresh DB
    # from _SCHEMA is left alone.
    for table in ('submissions', 'campaigns'):
        cols = [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
        if 'map_path' in cols and 'origin' not in cols:
            try:
                con.execute(
                    f'ALTER TABLE {table} RENAME COLUMN map_path TO origin')
            except sqlite3.OperationalError as exc:
                # Two _connect calls can race check-then-act above: a
                # cron tick, a manual command, and the write MCP server
                # can all touch the same never-migrated ledger in one
                # minute. Both PRAGMA checks can see map_path before
                # either ALTER commits; the loser's ALTER then fails with
                # "no such column: map_path" — not SQLITE_BUSY, so
                # timeout=30 busy-retry doesn't cover it. Re-check: if
                # origin exists now, the other side already migrated it.
                cols_now = [r[1] for r in
                           con.execute(f'PRAGMA table_info({table})')]
                if 'origin' in cols_now:
                    pass
                elif 'readonly database' in str(exc):
                    # Production ledger is world-readable but
                    # mu2epro-owned (0444 to others); a non-mu2epro
                    # reader hits this on the ALTER even though every
                    # other statement is a no-op. Leave it un-migrated
                    # and let ledger_ro's map_path->origin shim carry
                    # readers across the gap; a writer migrates it on
                    # its next connect.
                    pass
                else:
                    raise  # genuinely broken schema / locked / corrupt DB
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
    sorted. attempt = parent's attempt + 1 (1 if no parent); unknown
    parent_id is a ValueError.
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

    Makes _slice_overlaps_ledger's "crash-window guard" true: written
    after the fact, a row can't cover the window between a successful
    jobsub_submit and the ledger write, so the next tick would re-submit
    the same deterministic payload as duplicate physics.

    Raising here is load-bearing: if the window can't be recorded, the
    submission must not happen.
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

    Kept, not deleted: jobsub_submit can exit non-zero having already
    created a cluster, so the window isn't proven free and must keep
    blocking until a human reconciles it.
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

    Only exit from either state, and the only way a blocked campaign can
    move again: a failed reservation leaves a row covering
    [cursor, cursor+n), `_slice_overlaps_ledger` keeps seeing it, and
    top_up re-pauses the campaign every tick — `submissions resume`
    alone can't clear that, since it's the ROW, not the cursor, that
    blocks. Before this existed the only escape was hand-editing sqlite.

    The safety property is the call itself: only a human who just
    checked jobsub_q can assert the window is free. Nothing in the tick
    calls this.
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

    Needs-reconciliation case: someone must check jobsub_q before the
    window can be reused.
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


# States close_row may move an active row INTO. NOT derived from
# STATES: 'submitting' is a pre-submit reservation and 'failed' is
# fail_reservation's alone — a future addition to STATES must not
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

    entry is snapshotted verbatim — the caller already merged CLI
    resource overrides, so slices reproduce what was asked for. A second
    ACTIVE or PAUSED campaign for the same tarball is refused (double-
    submit guard): a paused campaign still owns its index space, so
    enqueueing on top and later resuming would feed two campaigns into
    the same indices. campaigns_live_tarball (see _connect) backstops
    this against the SELECT-then-INSERT race.
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

    Only active/paused campaigns accept it — a closed campaign's slice is
    never read again, so accepting one would report success for a no-op.
    Binds from the next tick: already-submitted batches keep the size
    they were dispatched with, since a ledger row records the indices it
    actually sent."""
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

    By default only the CAMPAIGN snapshot is edited, reaching future
    slices and nothing else — dispatched rows keep the entry they were
    submitted with. Deliberate for `memory`: an UNSET memory earns a
    recovery the 4000MB floor (submissions.recovery_resource_kwargs), so
    cascading a memory value would forfeit that better failure mode.

    include_open_rows=True also rewrites every not-yet-closed row's
    entry on this tarball, which is how RECOVERIES pick up the change
    (submissions.resubmit builds SubmitOptions from row['entry'], not
    the campaign). Rows match by tarball since neither table carries
    campaign_id; campaigns_live_tarball keeps that unambiguous for a
    live campaign, but a cancelled predecessor could leave an open row
    behind — so changed ids are RETURNED, not just counted.

    `closed_utc IS NULL`, not state='active': a 'submitting' row
    (reserved, no cluster yet) still gets recovered and needs the value.

    SETTLED CAMPAIGNS (2026-08-12): 'complete' (cursor exhausted) and
    'cancelled' both keep recovering already-dispatched rows —
    'complete' means every slice dispatched, not every job landed. The
    gate used to refuse those outright, blocking the operator exactly
    when needed: campaign 54 (PhysicalPionStops.Run1Bap) sat 'complete'
    at 500/500 dispatched with 239 outputs missing and an open row about
    to re-run them from the WRONG inloc — fixable only by hand-editing
    entry_json in sqlite.

    So a settled campaign now accepts ONLY the row cascade
    (include_open_rows=True); the CAMPAIGN snapshot stays untouched
    since no future slice reads it. Without the flag it's still
    refused, and the error names the flag.
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
        live = row['state'] in ('active', 'paused')
        if not live and not include_open_rows:
            raise ValueError(
                f"campaign {camp_id} is {row['state']} — its cursor is "
                f"settled, so editing the campaign snapshot would change "
                f"nothing. Pass --include-open-rows to set {key} on its "
                f"still-open rows, which is what their recoveries read")
        entry = json.loads(row['entry_json'])
        previous = entry.get(key)
        if live:
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
            # Resume: KEEP the pause-reason note; the operator clearing
            # the pause needs it.
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
