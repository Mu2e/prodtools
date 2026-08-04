# Tool retirement pass — evidence-first (2026-07-18)

Status: approved design, pre-implementation.

## Motivation

The 2026-07-12 hygiene tiers and the 2026-07-18 `/simplify` pass harvested
the code-level wins (single homes, batch SAM calls, dead parameters). What
remains is the *feature* level: whole tools in `bin/`, CLI modes, and dual
mechanisms that have been superseded. Goal: **one way to do each thing** —
retire obsolete tools and modes so the toolset, EXAMPLES.md, and the
`.claude` skills describe exactly one path per job.

## Decisions already made (brainstorm outcomes)

- **POMS submission path stays.** Retiring POMS-side machinery (map
  workflows, `mkrecovery` POMS recovery types) is explicitly out of scope.
- **The static render is the monitor product.** The published GitHub-Pages
  style page produced by `render_static.py` via the `update_pomsmonitor_web`
  cron is what is actually used; the live Flask app is not.
- **Hard delete, one pass.** No deprecation stubs. Retired tools are removed
  outright — tool, orphaned `utils/` module, tests, EXAMPLES.md section.
  Git history is the archive; the wiki retirement page explains where each
  job moved.
- **The standing do-not-fix list is honored** (worker byte-identity,
  parity_test duplication, legacy SAM branches — see
  `wiki/pages/2026-07-12-hygiene-tiers-and-kept-duplication.md`). Nothing on
  it is touched.

## Baseline (step 0)

Commit the uncommitted 2026-07-18 simplify pass first (342/342 tests green,
wiki page already written), then branch for this pass so the deletions land
as their own reviewable diff.

## Scope — candidate roster

| Candidate | Initial evidence | Working hypothesis |
|---|---|---|
| `bin/mkidxdef` CLI | Logic lives in `utils/mkidxdef.py`, called internally by `json2jobdef --prod`; standing rule says never invoke standalone; only docs reference the CLI | Retire the CLI; keep the module |
| Flask serving path: `bin/pomsMonitorWeb`, `web/pomsMonitor/__init__.py` | Cron calls `pomsMonitor --build-db` + `render_static.py` directly; only the static page is used | Retire Flask; make `monitor.html` static-native; delete the `_must_sub` JS-swap machinery |
| `pomsMonitor` CLI modes beyond `--build-db` | `--build-db` is cron-load-bearing; other report modes of unknown use | Audit per-flag |
| `latestDatasets` vs `listNewDatasets` | Overlapping dataset listings; `--emit` (chain templates) vs `--completeness` (used by `/recent-datasets`) | Fold-or-keep, decided by evidence |
| `bin/datasetFileList` | utils module imported by `logparser` and `jobdef_lookup`; CLI used by the `/mu2ejobsub-submit` skill | Likely keep |
| `setup_run1b.sh` | Referenced only in EXAMPLES.md | Retire if the Run1B musing setup is covered elsewhere |
| Vestigial flags/aliases across all tools | e.g. `latestDatasets --names-only` no-op alias | Sweep every tool's argparse for no-ops and compatibility aliases |

## Out of scope

- POMS vs direct submission backend consolidation.
- Merging tools beyond what the verdict table proposes as FOLD-THEN-RETIRE.
- Anything on the do-not-fix list.
- Code-level refactors with no feature-surface change (that was the
  simplify pass).

## Phase A — audit

For each candidate, four evidence checks:

1. **Callers** — grep across `utils/ bin/ web/ .claude/ templates/ wiki/
   EXAMPLES.md` and the cron script `bin/update_pomsmonitor_web`.
2. **External consumers** — mu2epro's `production_manager` scripts (if
   readable) and crontab entries.
3. **History** — `git log --follow` for the last substantive
   (non-hygiene) change.
4. **Unique capability** — what becomes impossible if it is gone.

Output: a verdict table, one row per candidate, verdict ∈ {RETIRE, KEEP,
FOLD-THEN-RETIRE, KEEP-REVISIT}, each row citing its evidence. Ambiguity
always resolves to KEEP-REVISIT — nothing is deleted on doubt.

## Checkpoint (user gate)

The user approves the verdict table per-item. Only approved RETIRE and
FOLD-THEN-RETIRE items proceed to Phase B.

## Phase B — execution

Deletions land in dependency order: leaf tools first, the monitor
untangling last (it is the only candidate with a real dependency edge).

Per item: delete the `bin/` entry, the `utils/` module if orphaned, its
tests, and any `.claude/` skill references; run the unit suite; one commit
per item.

Monitor untangling specifics: relocate `monitor.html` to a static-native
template owned by `render_static.py`, delete the `_must_sub` swap list,
delete the Flask app (`bin/pomsMonitorWeb`, `web/pomsMonitor/__init__.py`),
then verify by rendering to a scratch directory — the existing ≥25 KB
rich-page guard must pass and the rendered HTML must be structurally
identical to the currently published page — differences confined to
job-data values, none in markup or JS. `update_pomsmonitor_web`
keeps its interface; only its step-2 input template moves.

## Verification and error handling

- Full unit suite (342 tests at baseline) green after every removal; a red
  suite reverts that item's deletion and re-classifies it KEEP-REVISIT.
- The static render is verified *before* the Flask deletion commit, not
  after.
- After all removals: `/refresh-examples` regenerates EXAMPLES.md (editing
  `docs/EXAMPLES_schema.md` first if it names retired tools), and a final
  grep for each retired name must hit only wiki/history references.

## Documentation deliverables

- One wiki retirement page: what went, why, where each job moved.
- `wiki/log.md` entry.
- Memory updates for any pointer that names a retired tool.
- `.claude` skill edits (e.g. `/mu2epro-run`'s `mkidxdef` example).

## Success criteria

- Every approved RETIRE item is gone from `bin/`, `utils/`, tests,
  EXAMPLES.md, and `.claude` skills; grep finds only wiki/history mentions.
- The unit suite is green and the static monitor page renders with the
  rich-page guard passing.
- Each remaining tool has exactly one documented invocation path in
  EXAMPLES.md.
