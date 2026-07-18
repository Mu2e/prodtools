# `web/pomsMonitor/` — static dashboard renderer

This directory is a plain script directory. There is no Flask app, no
WSGI shim, no package `__init__.py`, and no live JSON-editor UI. The
dashboard is produced entirely offline by two Python scripts and
published as static files (`index.html` + `jobs.json` + `lineage.json`)
that any web server can serve with zero server-side logic.

## Contents

- `render_static.py` — renders `index.html` (from the
  `monitor_static.html` template, stamp-substituted with a
  "Last refreshed" timestamp) and `jobs.json` (from
  `jobs_payload.build_jobs_payload`). Exits non-zero on failure,
  including an empty jobs payload — cron-friendly.
- `jobs_payload.py` — builds the `/api/jobs`-shaped JSON catalog
  (Job + JobOutput + DatasetInfo) straight from the SQLite DB. No
  Flask, no HTTP — a plain function call.
- `build_lineage.py` — walks SAM to build/update `lineage.json`
  (`{dataset: {parents, stats}}`), the family-tree cache the static
  page's famtree popup reads instead of calling a live
  `/api/dataset/<name>` endpoint. Idempotent/incremental; owned
  independently of `render_static.py`, which never touches it.
- `monitor_static.html` — the frozen, static-native dashboard
  template. `/api/jobs` is a sibling `jobs.json` fetch; the famtree
  popup reads the pre-rendered `lineage.json`; write-mode UI (Reload
  button, JSON Editor / JobDesc Generator nav) is stripped — the page
  is read-only by construction, not by a server-side guard.
- `cron_run_inspect_datasets.sh` — cron entry point (see below).
- (also referenced from `bin/`) `bin/update_pomsmonitor_web` — a
  standalone rebuild-and-render wrapper for ad hoc/manual refreshes.

## The two crons

1. **`bin/update_pomsmonitor_web`** (repo `bin/`, run as the invoking
   user). Two required steps — refreshing the DB alone leaves the
   static HTML stale:
   1. `pomsMonitor --build-db --pattern "$PATTERN" --db "$DB"` —
      rebuild the SQLite DB from the production POMS maps.
   2. `render_static.py --out "$OUT" --prodtools-dir "$REPO" --db "$DB"`
      — re-render `index.html` + `jobs.json` from that DB.
   Backs up the previous `index.html`/`jobs.json` before overwriting
   and warns if the rendered page looks anomalously small (regression
   guard against picking up a stale template).

2. **`cron_run_inspect_datasets.sh`** (this directory, mu2epro's
   datasetMon nightly cron). Extended (2026-05) beyond its original
   `inspect_datasets.py` payload to also refresh the dashboard:
   1. Rebuild SQLite from POMS map JSONs (`db_builder.py`).
   2. Refresh lineage topology + stats (`build_lineage.py`,
      incremental).
   3. Re-render `index.html` + `jobs.json` (`render_static.py`).

## Paths (from the cron headers)

```
prodtools deploy : /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools/
SQLite DB        : /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db
static dashboard : /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor/
```

Published artifacts must be group-writable by `mu2e` (mu2epro's
primary group):

```bash
chgrp -R mu2e /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db \
              /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor
chmod -R g+w /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db \
             /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor
```

## Decommission (perform on the web host, not in this repo)

A live cgi-bin Flask instance predates this static renderer:

- `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/pomsMonitor.wsgi`
  imported a synced repo copy at
  `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools/`, pinned at commit
  `3ad4069` (2026-04-29).

To retire it:

1. Remove the `from pomsMonitor import app as pomsMonitor` registration
   line from `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/wsgi.py`.
2. `rm -r /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/`.

The synced `cgi-bin/prodtools/` checkout can stay (other cgi-bin apps
may still reference it) — once deregistered from `wsgi.py` it simply
stops serving the `/pomsMonitor` dashboard/editor URLs. Only the
static artifacts under `htdocs/computing/ops/production/pomsMonitor/`
serve the dashboard from here on.

## Note: the old render path silently dropped `setup_script`

The retired WSGI shim stubbed `samweb_client` at import time (the
conda env lacked it), so any code path touching that stub — including
the old live-request render — silently emptied every `setup_script`
value in the published `jobs.json`. The static `jobs_payload.py` +
`render_static.py` path does not go through that stub, so the
`setup_script` column now populates correctly. This was caught by
Task 8's byte-diff verification between the old and new render paths.
