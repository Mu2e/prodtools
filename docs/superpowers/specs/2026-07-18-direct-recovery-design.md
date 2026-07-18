# Direct-Backend Recovery Loop — Design

**Date:** 2026-07-18
**Status:** Approved by user (brainstorming session)
**Scope decisions (user rulings):** design + build recovery automation;
direct backend only; cron loop until complete; tracking via
submit_map-written submission records; loop runs from mu2epro's crontab.

## Problem

The direct submission backend (`submit_map --backend direct`) has no
automated recovery. Today an operator manually runs
`mkrecovery --print-indices` and pipes the result to
`submit_map --indices-file` — and nothing watches a campaign to
completion. POMS-backend stages have POMS's recovery chains; JustIN
treats recovery as a property of its file state machine. The direct
backend needs its own verify-and-resubmit loop, built on the one
recovery style the Run1Ban incident proved safe: **verifying output
files exist in SAM**, never consumption bookkeeping.

## Architecture

Three new pieces and one hook. No worker-side changes; worker fcl
byte-identity is untouched.

```
submit_map --backend direct ──writes──▶ submission ledger (sqlite3)
                                              │
        mu2epro crontab ── bin/recover_cron ──▶ bin/recover
                                              │ per active row:
                                              │  1. drain gate (jobsub_q)
                                              │  2. verify vs SAM (mkrecovery logic)
                                              │  3. complete / resubmit / exhaust
                                              └──resubmits via──▶ submit_map (writes child row)
```

## Component 1 — Submission ledger (`utils/submission_ledger.py`)

Stdlib `sqlite3` only — **no SQLAlchemy**. The submit path runs as
mu2epro in the bare ops environment where `pyenv ana` is not loaded.

Default DB path: `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`
(flag-overridable; `MU2E_SUBMISSION_DB` env var respected). A stable
absolute path is mandatory: mu2epro submissions run from throwaway
`/tmp` workdirs, and repo-relative paths would scatter state. One-time
ops step: create the directory as mu2epro.

Schema:

```sql
CREATE TABLE IF NOT EXISTS submissions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_utc  TEXT NOT NULL,              -- ISO8601
  state        TEXT NOT NULL DEFAULT 'active',
               -- active | complete | recovered | exhausted
  attempt      INTEGER NOT NULL DEFAULT 1, -- 1 = original submission
  parent_id    INTEGER REFERENCES submissions(id),
  map_path     TEXT,                       -- provenance only, may be stale
  tarball      TEXT NOT NULL,              -- cnf tarball basename
  entry_json   TEXT NOT NULL,              -- FULL map-entry snapshot, verbatim
  indices_json TEXT NOT NULL,              -- JSON list of absolute cnf indices
  jobsub_id    TEXT,                       -- e.g. 12345678.0@jobsub03.fnal.gov
  cluster_id   TEXT,                       -- numeric cluster (back-compat info)
  closed_utc   TEXT,
  note         TEXT
);
```

Design points:

- **Entry snapshot, not map reference.** `entry_json` stores the map
  entry verbatim (POMS snapshots JobTypes for the same reason).
  Recovery survives map edits, map moves, and vanished `/tmp` workdirs.
  `map_path` is informational.
- **States.** `active` = jobs possibly in flight, loop owns it.
  `complete` = all row indices verified in SAM. `recovered` = closed,
  superseded by a child row that owns the missing indices. `exhausted` =
  attempt cap reached with holes remaining; human takes over.
- **Chains.** A recovery resubmission is a new row with
  `parent_id = <old row>`, `attempt = parent.attempt + 1`, and
  `indices_json` = exactly the missing indices. Campaign state = state
  of each chain tip.
- API (all take an explicit db path): `record_submission(...) -> id`,
  `open_rows() -> [Row]`, `close_row(id, state, note)`,
  `chain_attempt(id) -> int`.

## Component 2 — Submit hook (in `utils/submit.py`)

On every **successful** direct-backend submission, `submit_map` appends
a ledger row. Details:

- Fires only for `--backend direct` and only when a cluster id was
  parsed (the existing "exited 0 but no cluster ID" failure path stays a
  failure — no row).
- **jobsub id parsing.** `_parse_cluster_id` today captures only the
  numeric cluster. Extend `_run_submit` to also parse the full
  `Use job id 12345678.0@jobsub03.fnal.gov` line — `jobsub_q` needs the
  schedd. Numeric-only fallback is recorded with a note; the loop
  reports such rows instead of guessing a schedd.
- New flags: `--ledger-parent <id>` (used by `recover` when
  resubmitting: sets `parent_id`, computes `attempt`) and `--no-ledger`
  (escape hatch for tests/ad-hoc runs). `--dry-run` never writes.
- A ledger **write failure aborts nothing already submitted** — the
  submission happened; the hook prints a loud warning with the exact row
  data so the operator can insert it manually. It must never crash the
  submit after jobs are queued.
- POMS-backend submissions never touch the ledger. The loop therefore
  cannot race POMS **by construction** — no backend inference anywhere.

## Component 3 — Recovery loop (`utils/recover.py`, `bin/recover`)

Per `active` row, in order:

1. **Drain gate.** `jobsub_q --jobid <jobsub_id>` (as the invoking user,
   normally mu2epro). Any job still idle/running → skip row, log.
   **Held jobs → report loudly and skip.** The loop never runs
   `condor_rm`; releasing or removing held jobs is a human decision.
2. **Verify.** Locate the tarball via `sam_physical_path` (as
   `mkrecovery.locate_tarball` does), parse once with `Mu2eJobPars`,
   build the file→index map **scoped to the row's index set**, and diff
   against `files_in_dataset` for every output dataset of the entry.
   Missing = union across datasets. Implementation: extend
   `mkrecovery.build_file_maps` with an optional `indices` iterable
   (default keeps today's `range(njobs)` window behavior) so the logic
   keeps a single home; `recover` passes the row's scattered indices.
3. **Act.**
   - No missing files → `close_row(id, 'complete')`.
   - Missing and `chain_attempt(id) < max_attempts` (default 3) →
     write the missing indices to a scratch indices-file, reconstruct a
     single-entry map JSON from `entry_json` in scratch, and resubmit by
     invoking **`submit_map` as a subprocess**:
     `submit_map --map <tmp.json> --backend direct
     --indices-file <tmp.txt> --ledger-parent <id>`. Reusing the CLI
     keeps one battle-tested submit path (token check, argv build,
     ledger write of the child row all included). Parent row →
     `recovered`.
     **Windowed entries:** current `submit.py` rejects `--indices` when
     the entry has `firstjob > 0` (the values ARE absolute cnf indices,
     so a window offset would be ambiguous). The reconstructed tmp entry
     therefore **drops `firstjob`** — the absolute indices already carry
     the window, and the worker's `firstjob + index` resolution
     degenerates to the identity. The original windowed entry stays
     untouched in the parent row's snapshot.
   - Missing and cap reached → `close_row(id, 'exhausted')` + loud
     report. Deterministic payloads re-run identical events, so a
     systematic failure (wall-clock tail, bad input) fails identically
     every round — `exhausted` is where the human takes over,
     re-walling is never fixed by blind retry.

CLI:

```
recover                      # process every active row (cron entry point)
recover --status             # read-only chain table, no actions
recover --dry-run            # drain-check + verify + report; no submits
recover --row N              # process one row
recover --max-attempts N     # default 3
recover --db PATH            # default as above
```

`--status` and `--dry-run` are safe under any account (status checks
never need mu2epro).

## Component 4 — Cron wrapper (`bin/recover_cron`)

For mu2epro's crontab, hourly. Responsibilities, in order:

1. `flock` a lockfile beside the DB — overlapping cron runs must not
   double-submit.
2. Environment setup (setupmu2e-art.sh + `muse setup ops`; no pyenv).
3. **Token check first.** No valid bearer token → log + exit non-zero.
   Never fetch or refresh a token (standing rule: token problems are
   reported, not remediated).
4. `recover` with output appended to a dated log beside the DB.

## Edge cases and verification items

- **Partial outputs / duplicate declare.** An index with one output
  stream landed and another missing gets fully re-run; the re-push then
  meets an already-declared SAM file. POMS `pending_files` recovery has
  the same shape, so production precedent suggests pushOutput tolerates
  it — but the implementation plan must include a test that *verifies*
  duplicate-declare behavior before the loop goes live. Until verified,
  `recover --dry-run` reports partial indices distinctly.
- **Tarball unlocatable.** Verify step fails → row stays `active`, loud
  log line; never guessed complete. (Fail loudly, no fallbacks.)
- **Entry with no parseable output datasets.** Same: report, skip, stay
  `active`.
- **Numeric-only jobsub id** (schedd parse failed): drain gate cannot
  run → report, skip. Operator can update the row.

## Testing

Unit tests (`test/test_unit.py` conventions, no network, no live
submissions):

- Ledger: round-trip record/read/close; chain attempt counting; states.
- Submit hook: fake `subprocess.run` capturing jobsub stdout fixtures →
  row written with full jobsub id; `--no-ledger` and dry-run write
  nothing; ledger failure does not raise after submission.
- Loop: injected fake `jobsub_q` + fake SAM listing + fake submit
  callable; cases: still-running skip, held-report skip, complete,
  resubmit-with-cap, exhausted, scattered-index verify, partial-output
  reporting.
- `build_file_maps(indices=...)` scoped scan matches the windowed scan
  on a real fixture tarball.

## Out of scope (deliberate)

- Resource escalation on retry (POMS's 4GB→8GB trick) — v2 knob if
  needed.
- Dashboard/pomsMonitor integration — the monitor may later *read* the
  ledger; state never lives in `poms_data.db` (it is rebuilt from
  scratch by cron and would clobber it).
- POMS-backend entries — POMS owns their recovery; `mkrecovery`'s SAM
  index-definition path is unchanged.
- Auto-handling of held jobs.

## Documentation

- `EXAMPLES.md`: regen via `/refresh-examples` after adding `recover`
  to `docs/EXAMPLES_schema.md`'s Additional Tools list (and
  `recover_cron` to the ops-scripts one-liners).
- Wiki: ops page for the loop (install runbook: DB dir creation,
  mu2epro crontab line, log location) + `wiki/log.md` entry.
