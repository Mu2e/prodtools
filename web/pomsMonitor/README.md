# Deploying `pomsMonitorWeb` on mu2e-exp.fnal.gov

Read-only Flask deployment that runs alongside `dqmTimeline` under the
Mu2e public web's WSGI infrastructure. The same Flask `app` defined in
`bin/pomsMonitorWeb` serves both local dev (`pomsMonitorWeb` on
`http://localhost:5000`) and the public web — this shim just disables
the write routes and reroutes the DB path.

## Install

1. Copy this directory to:
   ```
   /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/
   ```

2. Edit `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/wsgi.py` to import the
   app object alongside the existing `dqmTimeline`:
   ```python
   from hello import app as hello
   from dqmTimeline import server as dqmTimeline
   from pomsMonitor import app as pomsMonitor
   ```

3. **Dependencies in the conda env.** The env at
   `/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/venv/current/` (a conda env
   built from `ana_v2.5.0.yml`, conda-forge + `Mu2e/pyutils` via pip)
   has Flask + SQLAlchemy via `dash`, but **not** `samweb_client`.
   The shim works around this by stubbing `samweb_client` at import
   time, so the WSGI process boots without it. Routes that don't need
   SAM (dashboard, JSON browser, `/api/jobs`) work; `/api/dataset/<name>`
   (famtree) is hard-disabled with HTTP 403 since it would otherwise
   raise on the first request.

   To re-enable famtree later, install `samweb_client` into the conda
   env and remove `api_dataset_info` from `_DISABLED_ENDPOINTS` in
   `__init__.py`:
   ```bash
   /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/venv/current/bin/pip install \
       --extra-index-url https://scisoft.fnal.gov/python samweb_client
   ```
   The conda env is owned by `nobody`; the maintainer (see
   `venv/notes.txt`) needs to run this or add `samweb_client` to the
   `pip:` block in `ana_v2.5.0.yml` for the next env rebuild.

4. Set environment in the Apache vhost (or a `.htaccess` for the
   directory). Recommended defaults:
   ```apache
   SetEnv PRODTOOLS_DIR /cvmfs/mu2e.opensciencegrid.org/bin/prodtools/v2.0.1
   SetEnv POMS_DB_PATH /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db
   ```
   The DB path must be readable by the web user (`nobody`).

## DB freshness

The Flask app reads the SQLite DB on every request — no live build.
Schedule a cron job on any host that can `ksu mu2epro` and write to
the public-web data directory:

```cron
# Hourly refresh, runs as mu2epro under pyenv ana for SQLAlchemy.
15 * * * *  /usr/bin/ksu mu2epro -e -c '\
  source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh && \
  muse setup ops && \
  pyenv ana && \
  pomsMonitor --build-db --pattern "MDC202*" \
              --db /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db'
```

`pomsMonitor --build-db` walks the production POMS-map directory
(`/exp/mu2e/app/users/mu2epro/production_manager/poms_map/`) and the
SAM API for current dataset stats, so it must run from an mu2epro-
adjacent account with both filesystem and SAM read access.

## Disabled endpoints

This shim returns **HTTP 403** for four routes:

| Route                              | Reason for disabling                        |
| ---------------------------------- | ------------------------------------------- |
| `POST /api/reload`                 | Would rebuild DB at request time (heavy + races) |
| `POST /api/json2jobdef`            | Executes `bash -c <user input>` — unsafe   |
| `POST /api/json-file/<path>`       | Would overwrite JSON configs in prodtools  |
| `GET  /api/dataset/<name>`         | Famtree — needs `samweb_client` (not in conda env) |

To edit JSON configs or generate jobdefs, use the prodtools CLI
locally. The web UI is read-only by design.

## Routes that work

- `GET /` → redirect to `/monitor`
- `GET /monitor` → main dashboard HTML
- `GET /api/jobs` → JSON catalog of every Job + JobOutput + DatasetInfo
- `GET /api/dataset/<name>` → Mermaid family tree (needs samweb_client)
- `GET /api/json-files` → read-only list of `data/*.json` paths
- `GET /api/json-file/<path>` → read-only fetch of a `data/*.json`

## URL

Routes mount at whatever path the Apache config gives this app. With
the default `wsgi.py` import, the URL is most likely
`https://mu2e.fnal.gov/pomsMonitor/`. Confirm with the existing
`dqmTimeline` URL layout when deploying.

## Reverting / debugging

The shim is two files (`__init__.py` + this `README.md`). To revert:

1. Remove the `from pomsMonitor import ...` line from `wsgi.py`.
2. `rm -r /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/`.

To test the import locally before deploying (any host with `muse
setup ops` + `pyenv ana` available — these supply samweb + sqlalchemy
+ flask):

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
pyenv ana
PRODTOOLS_DIR=/exp/mu2e/app/users/mu2epro/.../prodtools \
PYTHONPATH=/exp/mu2e/app/users/mu2epro/.../prodtools/web:$PYTHONPATH \
python3 -c "
from pomsMonitor import app
for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
    methods = ','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
    view = app.view_functions[rule.endpoint]
    flag = ' [DISABLED]' if view.__name__ == '_forbidden' else ''
    print(f'  {methods:8s} {rule.rule:40s} {rule.endpoint}{flag}')
"
```

A working install prints 12 routes, four of them flagged `[DISABLED]`
(`api_reload`, `api_json2jobdef`, `api_save_json_file`,
`api_dataset_info`).
