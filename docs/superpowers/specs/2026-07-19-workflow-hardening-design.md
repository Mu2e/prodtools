# Direct-submission workflow hardening — design

**Date:** 2026-07-19
**Status:** approved design, pre-implementation
**Prereqs:** merged direct-recovery loop (spec `2026-07-18-direct-recovery-design.md`)
and sliced campaigns (spec `2026-07-18-sliced-submission-design.md`), both on
`field-off-option`. Subsystem NOT activated (no crontab installed anywhere).

## Goal

Close the multi-operator footguns found in the workflow review before
activation. Operators will include production people without this repo's
tribal context, so the CLI must be safe by default and self-describing,
failures must be visible to cron monitoring, and cross-path double
submission must be structurally impossible, not just documented.

No semantic redesign: the tick structure (recovery pass then top-up),
campaign states and transitions, crash-window guards, snapshot
inheritance, and the ledger schema all stay exactly as reviewed.

## Review findings this design answers

1. Status lives on a tool named after one of its three jobs
   (`recover --status`); bare `recover` is a mutating command.
2. The POMS map has no notion of which path owns an entry — nothing
   stops direct-submitting (or enqueueing) an entry POMS already runs.
3. Queue-count failure during top-up skips the phase loudly in the log
   but exits 0 — cron monitoring sees success.
4. A campaign that *stays* paused only signals exit 2 on the tick that
   paused it, not on later ticks.
5. Enqueue failures surface as raw tracebacks.
6. `--enqueue --no-ledger` is accepted though contradictory;
   `--status` + a management flag silently ignores the management flag.
7. `--resume-campaign` clobbers the note that says why the campaign was
   paused.
8. `submit_slice`/resubmit `mkdtemp` scratch dirs are never removed —
   hourly cron accumulates them in `/tmp` indefinitely.
9. The ksu environment requirements for direct submission by a human
   exist only in a Claude skill and a memory file, not in any
   operator-readable doc.
10. `submit_map`'s default backend is still `mu2ejobsub` (Phase 1), so
    an operator who forgets `--backend direct` silently gets the legacy
    path — unledgered, unwatched, invisible to `submissions status`.
    The Phase-1 backend has no operational use since the direct backend
    landed (wiki survey 2026-07-19: design-doc mentions only), and the
    entry modes it nominally covers (template/direct_input/g4bl, HPC)
    are launched via POMS or the upstream CLIs in practice.

## Change 1 — rename `recover` → `submissions`, verb structure

Rationale: the tool is the maintenance CLI for the whole
direct-submission subsystem (verify + resubmit + top-up + status +
campaign management). The rename is done NOW because nothing references
the old name yet (no crontab line, no trained operators, no published
release); after activation the same change churns a live cron.

The bare command becomes **read-only**. Mutating actions require an
explicit verb — the safe-by-default property is the point of the
restructure, not the name itself.

```
submissions                          # no verb → status (read-only)
submissions status                   # explicit form, same output
submissions run                      # the tick: recovery pass + top-up
submissions run --dry-run            # preview, read-only, would-* labels
submissions run --row N              # single ledger row, skips top-up
submissions run --max-attempts N     # default 3
submissions run --max-queued N       # cap override for this pass
submissions pause CAMP_ID [--note TEXT]
submissions resume CAMP_ID
submissions cancel CAMP_ID
```

- Global flag: `--db PATH` (default and `MU2E_SUBMISSION_DB` env
  unchanged), valid before the verb.
- Exit codes: `run` keeps the exit-2 needs-attention contract
  (extended by Change 3); `status` exits 0 unless the DB is unreadable;
  management verbs exit 0 on success, 1 with a one-line error on
  invalid transitions.
- Locking: `run` and `pause`/`resume`/`cancel` take the per-DB lock;
  `status` and `run --dry-run` never do. The old "management flags
  refused under --dry-run" rule disappears structurally — management
  is a separate verb.
- Files: `bin/recover` is **deleted** (clean cut, no alias — no muscle
  memory exists yet). `bin/submissions` is the entry point.
  `bin/recover_cron` → `bin/submissions_cron`, invoking
  `submissions run`; flock/token-gate/append behavior unchanged; the
  cron log file is renamed `submissions-YYYYMMDD.log` (nothing has
  written the old name in production; there is no live log history to
  preserve).
- Module: `utils/recover.py` → `utils/submissions.py`; imports in
  `bin/` and `test/test_unit.py` updated. Public function names inside
  the module are unchanged.
- CLI parsing: argparse subparsers. A bare invocation (no verb)
  dispatches to `status` explicitly — argparse `set_defaults` on the
  top-level parser, not a hidden fallthrough.

## Change 2 — path-ownership guard (`"backend": "direct"` entry key)

New optional POMS-map entry key: `"backend"`. The only recognized value
is `"direct"`.

- `submit_map` — one-shot, `--first/--num`, `--indices`,
  `--indices-file`, and `--enqueue` alike — **hard-errors** on any
  selected entry that does not carry `"backend": "direct"`:

  ```
  submit_map: entry 'cnf...tar' is not marked for the direct backend.
  Add "backend": "direct" to the entry in <map> if this entry is NOT
  run by a POMS campaign. Direct-submitting a POMS-owned entry runs
  identical payloads twice.
  ```

  No `--force` escape (house no-fallbacks rule). The fix is a one-line
  map edit by an operator who has verified ownership.
- With Change 7, every `submit_map` submission is direct, so the guard
  applies to all of them unconditionally. The key's meaning is
  ownership: "this entry is submitted through `submit_map`, not by a
  POMS campaign." POMS maps and `runmu2e` are untouched; POMS-owned
  entries simply never carry it.
- Ledger/campaign snapshots already copy the entry verbatim, so the key
  flows into snapshots; internal resubmit and slice tmp-maps therefore
  pass the guard without special-casing. There are no pre-guard ledger
  rows in any production DB (the production DB does not exist until the
  activation mkdir), so no migration is needed.
- `json2jobdef` passthrough: NOT added. The key marks a *submission
  path* decision made at map level, not a jobdef property; `--prod`
  pushes continue to write entries without it, and the operator marks
  entries direct when (and only when) they choose that path.

## Change 3 — exit-code honesty in `run`

Two additions to the needs-attention (exit 2) set:

- **Queue-count failure**: `total_queued()` returning `None` already
  skips top-up with a log line; it now also sets needs-attention. A
  wedged `jobsub_q` otherwise starves every campaign silently-in-cron
  forever.
- **Lingering paused campaign**: any campaign in state `paused`
  observed during a `run` pass (not just newly paused this tick) sets
  needs-attention. Paused means "a human must act"; the signal must
  repeat until they do. Both conditions set needs-attention under
  `run --dry-run` too (reported with the would-* labels), extending
  the existing dry-run exit-2 set the same way the live pass extends
  its own.

`status` never exits 2 — it is a display, not a monitor.

## Change 4 — clean errors and flag hygiene

- Enqueue failures (duplicate live campaign, njobs invalid, generic
  tarball, DB errors) print `submit_map: <one line>` and exit 1 — no
  tracebacks. Internal invariant violations may still traceback;
  operator-reachable errors must not.
- `--enqueue --no-ledger` → refused at argument validation
  (`submit_map: --enqueue registers a campaign in the ledger DB;
  --no-ledger contradicts it`).
- The `--status`-plus-management-flag silent ignore is resolved
  structurally by Change 1 (separate verbs).

## Change 5 — pause-note preservation

`set_campaign_state(..., 'active')` (resume) keeps the existing note
instead of clearing it. `pause` gains an optional `--note TEXT`
(default note unchanged: who/what paused it). Cancel behavior
unchanged. The note column remains a single value — no history table
(YAGNI); the submission log already timestamps every state-changing
invocation.

## Change 6 — tmp hygiene

One shared helper (in `utils/submissions.py`, used by both
`submit_slice` and the recovery resubmit path) creates the scratch
map dir and removes it in `finally` after the child `submit_map`
completes, success or failure. On cleanup failure: warn, never raise
(post-submission never-raise rule).

## Change 7 — retire `submit_map`'s mu2ejobsub backend

`submit_map` becomes single-backend (direct). Answers finding 10 by
deletion rather than by a required flag.

- **Deleted:** the `--backend` flag, `_submit_entry_mu2ejobsub`,
  `build_mu2ejobsub_argv`, and the backend dispatch in `submit_entry`.
  Passing `--backend` anything is an argparse error — loud, not silent.
- **Call sites:** `submit_slice` and the recovery resubmit path drop
  `--backend direct` from the child `submit_map` argv (`recover.py`,
  two sites — renamed module per Change 1).
- **Worker message:** `runmu2e` `_direct_dispatch` still refuses
  non-normal jobdesc modes, but the message stops recommending
  `--backend mu2ejobsub`; it now points at the POMS launch path and
  the upstream CLIs.
- **Boundary — what this does NOT touch:** the upstream `mu2ejobsub`
  tool, the POMS launch path that drives it, `runmu2e`'s worker-side
  shim compatibility (POMS-launched workers still come through the
  Perl `mu2ejobsub.sh` shim), and the Perl parity tests. Those are the
  POMS path and stay fully supported. We are deleting our Phase-1 CLI
  *driver* of mu2ejobsub, nothing upstream.
- **Policy (documented in the operator decision tree):** entry modes
  the direct worker does not support — template, direct_input, g4bl —
  and HPC submission are not submittable via `submit_map`. They run
  via POMS campaigns or the upstream `mu2ejobsub`/`mu2eg4bl` CLIs
  directly (both have local skills). `MU2EGRID_HPC` never had prodtools
  support; nothing is lost.
- **Tests:** the 7 mu2ejobsub-backend references in `test_unit.py` are
  removed or rewritten against the single-backend CLI; one new test
  asserts `--backend` is rejected as an unknown argument.

## Change 8 — docs

- **Wiki runbook** (`wiki/pages/2026-07-18-direct-recovery-loop.md`):
  - all `recover` invocations → `submissions` verbs; crontab line →
    `submissions_cron`;
  - the 5-item pre-activation checklist rewritten with the new
    spellings (items themselves unchanged);
  - new **operator quickstart** section: POMS-vs-direct decision tree
    (including where template/direct_input/g4bl/HPC submissions live),
    the ksu environment requirements for a human running `submit_map`
    (today only in the `/mu2epro-submit` skill), how to read
    `submissions` output, and the response playbook for each exit-2
    cause.
- **EXAMPLES.md** via `docs/EXAMPLES_schema.md` + `/refresh-examples`:
  `submissions` verb table, `backend` entry key, the two refusals,
  single-backend `submit_map` (no `--backend` flag anywhere; the
  resource-key note loses its "direct only" qualifier), tribal bullets
  updated (status command, safe-by-default note, where template/
  direct_input/g4bl/HPC submissions live now).
- Memory files and `MEMORY.md` pointers updated post-merge (they
  reference `recover`/`recover_cron` heavily).

## Testing

Extend `test/test_unit.py` (fake runners/subprocess, no network — house
style). New/updated coverage:

- verb dispatch: bare → status; `run` mutates only under the verb;
  `status` takes no lock; management verbs validate transitions.
- ownership guard: refusal (message includes the map path) for
  one-shot, windowed, indices, and enqueue selection; acceptance with
  the key; snapshot preserves the key; child-resubmit tmp map passes.
- exit 2 on count failure; exit 2 on lingering paused campaign
  (two consecutive fake ticks).
- resume preserves note; pause `--note` recorded.
- `--enqueue --no-ledger` refused; duplicate-enqueue prints one line,
  no traceback (assert on stderr shape).
- tmp helper: scratch dir gone after success and after a failing child.

Full suite green before merge (442 tests today; expect ~460+).

## Out of scope (deliberate)

- extending the direct worker to template/direct_input/g4bl modes
  ("Route B" — build only if a real need appears)
- HPC submission support in `submit_map`
- changes to the upstream `mu2ejobsub`/`mu2eg4bl` tools, the POMS
  launch path, or `runmu2e`'s worker-side shim compatibility
- POMS-side or map-generation changes beyond the optional key
- further verbs (`submissions watch`, log tailing)
- any alias or back-compat shim for the old `recover` name

## Sequencing

1. This hardening pass (spec → plan → SDD → merge).
2. Resume the parked sandbox-test brainstorm; run the test with the
   NEW spellings.
3. 5-item pre-activation checklist (updated wording).
4. Operator step: mkdir + install `submissions_cron` in mu2epro's
   crontab.
