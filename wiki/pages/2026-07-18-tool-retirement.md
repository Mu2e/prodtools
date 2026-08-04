---
title: Tool-retirement pass (2026-07-18) — verdicts, charters, ops decommission
tags: [decision, hygiene, retirement]
sources: []
updated: 2026-07-18
---

# Tool-retirement pass (2026-07-18)

An 11-task evidence-first pass over `prodtools` CLI surface and the
`web/pomsMonitor` Flask app. Phase A gathered evidence per item (code
callers, external-consumer reachability, git history, unique
capability) into `docs/superpowers/plans/2026-07-18-tool-retirement-verdicts.md`;
Phase B applied the user-approved verdicts. This page is the durable
record — quotes below are taken verbatim from that verdicts file.

Decision rule applied throughout: **RETIRE** requires all three of (no
code callers) AND (no external-consumer evidence, and the external
checks were actually readable) AND (unique capability covered
elsewhere or explicitly retired by the user). Anything that fails a
leg is **KEEP** or **KEEP-REVISIT**.

## Retired

### `bin/mkidxdef` + `utils/mkidxdef.py`

Standalone CLI wrapper around index-definition creation. **RETIRE
approved.** Evidence: "zero code callers of `utils/mkidxdef.py`
(`grep -rn "utils.mkidxdef\|import mkidxdef"` → no hits); `bin/mkidxdef`
is only ever invoked by itself/docs. Confirmed known fact:
`json2jobdef.py:582` calls `summarize_and_index(jobdefs_file,
prod=True)` directly ... bypassing `utils/mkidxdef.py` entirely."
History: "last two touches are hygiene-only ... no non-hygiene commit
in tracked history." Unique capability: "none — `summarize_and_index`
in `prod_utils.py` (kept) is the real logic; `json2jobdef --prod` is
the standing documented entry point."

**Where its job lives now:** `prod_utils.summarize_and_index`,
called internally by `json2jobdef --prod` (`utils/json2jobdef.py:582`).
There is no separate CLI step to run — there never was, in terms of
the actual code path; `bin/mkidxdef` was a redundant, never-used entry
point to the same function. Commit `950106c`.

### `bin/setup_run1b.sh`

**RETIRE approved.** Evidence: "only hit anywhere is docs-only ...
Content is 14 lines, trivial: sources `bin/setup.sh` then `export
MU2E_SEARCH_PATH=".:$MU2E_SEARCH_PATH"` plus an echo — fully inlineable
in one line at any call site." History: "single commit ... a large
batch checkpoint commit, not dedicated functional work on this file;
zero commits since."

**Where its job lives now:** inline at any call site —
`source bin/setup.sh` followed by
`export MU2E_SEARCH_PATH=".:$MU2E_SEARCH_PATH"`. Commit `f5a84ab`.

### `latestDatasets --names-only`

**RETIRE approved** (`--show-count` stays). Evidence: "`--names-only`
is accepted as an explicit alias of the default ... This is not my
inference — it's a same-day finding:
`wiki/pages/2026-07-18-simplify-pass-consolidations.md:86` already
lists 'latestDatasets --names-only duplicate branch (flag kept as
no-op alias)' under 'Removed dead surface' from the just-committed
simplify pass — the branch logic was already deleted; only the flag
itself (accepted, silently ignored) remains."

**Where its job lives now:** nowhere — it was already a no-op alias
of the tool's default bare-name output before this pass; the flag
itself is now gone. `--show-count` remains the real, used modifier
(`EXAMPLES.md`: `latestDatasets --defname '...' --show-count`).
Commit `a911300`.

### Flask app — `bin/pomsMonitorWeb` + `web/pomsMonitor/__init__.py` + `web/static/monitor.html`

Verdict table classified this **KEEP-REVISIT** (external-consumer leg
failed the mechanical rule): "A deployed public WSGI instance of the
Flask app exists and is live ... `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/pomsMonitor.wsgi`
exists, is readable, and imports `pomsMonitor.app` from a synced repo
copy at `cgi-bin/prodtools/` (git HEAD `3ad4069`, 2026-04-29) ... both
the dashboard and the JSON-editor pages are reachable read-only in
production today." History showed genuine functional commits, not
just hygiene. Framed explicitly as **an ops decision, not a code
decision**: "the user must explicitly choose to decommission the
cgi-bin deployment ... before or as part of any Flask-app deletion."

**Gate outcome (user ruling):** RETIRE approved — "user chose
'decommission + retire'; wiki page must record the cgi-bin removal
steps (ops action on the web host, done by the user)." See
[Ops decommission](#ops-decommission) below.

**Where its job lives now:** the static renderer that already existed
alongside it. `web/pomsMonitor/render_static.py` renders `index.html`
from the `web/pomsMonitor/monitor_static.html` template
(stamp-substituted "Last refreshed" timestamp) and `jobs.json` from
`jobs_payload.build_jobs_payload` — no Flask, no WSGI, no test client.
Commit `cd955de` deleted `bin/pomsMonitorWeb`,
`web/pomsMonitor/__init__.py`, and `web/static/monitor.html`; commit
`9520d23` (pre-retire) had already made `render_static.py`
static-native so the deletion was safe.

### JSON-editor feature — `web/static/json2jobdef.html` + `web/static/json-editor.html`

Same ruling as the Flask app (tied to the same deployment). Verdict
table: "browsing/generating jobdefs via a web form has no other
implementation; CLI (`json2jobdef`) fully covers the same ground for
anyone with shell access." **RETIRE approved.**

**Where its job lives now:** the CLI, `json2jobdef` — always was the
full-capability equivalent, per `/stage-entry` and
`wiki/pages/json2jobdef-staging-workflow.md`. Deleted in `cd955de`
along with the Flask app that served it (the routes were
`bin/pomsMonitorWeb:97-98`/`102-103`).

## Kept, with charters

- **`pomsMonitor` CLI flags — all sub-rows KEEP confirmed.**
  `--build-db`/`--pattern`/`--db` are cron-load-bearing
  (`bin/update_pomsmonitor_web`). `--list`, `--outputs`, `--complete`,
  `--incomplete`, `--since`, `--needs-processing` have recorded
  interactive/documented use (`.claude/settings.local.json` allowlist,
  `EXAMPLES.md` worked examples). `--datasets-only`, `--ignore` /
  `--unignore` / `--list-ignored`, and `--uniformity`/`--target`/`--round`
  lack a literal grep hit but are documented in `EXAMPLES.md`'s
  "Key flags" prose and back real, non-stub code paths with no
  equivalent elsewhere — retiring `--ignore` family would silently
  break `--needs-processing`'s exclusion mechanism; `--uniformity`
  implements a unique events-per-job heuristic
  (`utils/pomsMonitor.py:40,140-141`).

- **`latestDatasets` vs `listNewDatasets` — KEEP both, distinct
  charters, no fold.** `latestDatasets.py:2-13`: "For each unique
  description ... pick the dataset with the latest dsconf" (group-by
  + emit chain configs — `--emit {digi,reco,mix} --campaign ...
  --skip-produced`, 7+ recorded invocations). `listNewDatasets.py:2`:
  "List recently created datasets from SAM database" (time-window
  `--days` + POMS-DB `--completeness` join, 40+ recorded invocations).
  "Zero shared functions ... and zero cross-import between the two
  modules." Both call into `samweb_wrapper`, which is "incidental
  infrastructure reuse, not charter overlap." Task 6 (fold
  investigation) = no-op, confirmed.

- **`bin/datasetFileList` CLI — KEEP confirmed.** Fails the
  no-code-callers leg outright: `utils/logparser.py:12` and
  `utils/jobdef_lookup.py:23,228` both import
  `get_dataset_files`/`get_definition_files` from
  `utils/datasetFileList.py`. The CLI itself is documented as a real
  fallback path in `.claude/commands/mu2ejobsub-submit.md:44,106` and
  has active test coverage (5 hits in `test/test_unit.py`).

## KEEP-REVISIT (resolved this pass)

Two items (Flask app, JSON-editor) were flagged KEEP-REVISIT by the
Phase-A mechanical rule because live external-consumer evidence
disqualified an automatic RETIRE — not because the evidence was
ambiguous, but because the rule as written treats "external consumer
found and reachable" as an automatic block regardless of whether
retiring it is still the right call. What would have settled it
either way: an explicit ops decision from the user about whether to
decommission the cgi-bin deployment. That decision was made in the
gate outcome (see above) — **decommission + retire**, with the
cgi-bin removal recorded as an ops runbook (below) since it happens on
the web host, outside this repo's git history.

No item remains open after this pass; all eight verdict-table rows
have a recorded gate outcome.

## Ops decommission

The static renderer replaces the Flask app's cron/build path, but a
**live cgi-bin WSGI deployment predates it** and is not touched by
deleting files from this repo — it runs from a separately synced
checkout (`cgi-bin/prodtools/`, pinned at commit `3ad4069`). That same
synced checkout is also what `cron_run_inspect_datasets.sh` runs
`db_builder.py` / `build_lineage.py` / `render_static.py` from
(`PRODTOOLS_DIR` in the cron header) — so until it is synced past this
retirement branch, the nightly cron keeps executing the *old*
Flask-test-client render path (with the `setup_script`-stubbing bug
described below) and the deleted Flask app files stay live on disk
there, even after `wsgi.py` is deregistered. The decommission runbook
lives in `web/pomsMonitor/README.md` ("Decommission (perform on the
web host, not in this repo)") and is **not yet executed** — it is a
human, out-of-repo action:

1. Sync `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools/` to a
   commit at or past this retirement branch (or repoint the cron's
   `PRODTOOLS_DIR` at a maintained checkout instead), **before or
   together with** step 2 — deregistering `wsgi.py` alone does not fix
   the cron's render path. The new static-native render path needs a
   *working* `samweb_client` in the cron's environment: the old path
   only "worked" there because the WSGI shim stubbed `samweb_client`
   at import time, and that stub was the bug (see "Found along the
   way" below), not a dependency to preserve.
2. Remove the `from pomsMonitor import app as pomsMonitor` registration
   line from `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/wsgi.py`.
3. `rm -r /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/`.
4. Verify post-sync: after the next cron run, the published
   `jobs.json` under `htdocs/computing/ops/production/pomsMonitor/`
   shows populated `setup_script` values (non-empty strings) —
   confirms the cron is on the fixed render path, not the stubbed one.

The synced `cgi-bin/prodtools/` checkout is a code source for the
cron, not something that can be left unmaintained once `wsgi.py` is
deregistered — it must track the repo or the nightly render silently
regresses to the old, buggy path. From that point on, only the static
artifacts under `htdocs/computing/ops/production/pomsMonitor/`
(produced by `bin/update_pomsmonitor_web` and the
`cron_run_inspect_datasets.sh` nightly cron) serve the dashboard.

## Found along the way

Two things surfaced during Phase B that weren't in the original
retirement scope but are worth recording here because they change
observable behavior:

1. **The byte-diff gate (Task 8) surfaced a pre-existing production
   bug.** The old Flask/WSGI render path stubbed `samweb_client` at
   import time (the conda env serving the WSGI process lacked it).
   Any code path touching that stub — including the live-request
   render — silently emptied every `setup_script` value in the
   published `jobs.json`. This was invisible because the dashboard
   never showed an error; the column was just blank. Per the gate
   amendment: "User ruling: **accept the corrected behavior**. Gate
   criterion amended to: index.html timestamp-normalized identical;
   jobs.json identical except `setup_script` transitions '' → real
   value (rigorous field-level comparison, zero other diffs)."
   Operational consequence: the static `jobs_payload.py` path doesn't
   go through that stub, so `setup_script` now populates correctly,
   and cron renders now do real SAM+tarball resolution instead of
   hitting the stub — **~minutes slower** per render, traded for a
   working column that was silently broken before.

2. **The static renderer architecture, post-retirement:**
   `web/pomsMonitor/monitor_static.html` (frozen, static-native
   template — no write-mode UI, no JSON-editor nav, read-only by
   construction) + `web/pomsMonitor/jobs_payload.py` (plain function,
   `build_jobs_payload`, no Flask/HTTP) +
   `web/pomsMonitor/render_static.py` (stamp-only: substitutes the
   "Last refreshed" timestamp into the template and writes
   `index.html` + `jobs.json`; fails loudly — non-zero exit — on
   template drift or an empty jobs payload, cron-friendly).
   `web/pomsMonitor/build_lineage.py` remains a separate, idempotent
   owner of `lineage.json`, untouched by `render_static.py`. Full
   detail in `web/pomsMonitor/README.md`.

## Related

- [[2026-07-12-hygiene-tiers-and-kept-duplication]] — the standing
  do-not-fix list this pass respected (nothing on that list was
  touched).
- [[2026-07-18-simplify-pass-consolidations]] — the same-day
  consolidation pass that immediately preceded this one and already
  found the `--names-only` no-op branch this pass formally retired.
