"""WSGI entry: read-only POMS monitor for mu2e-exp.fnal.gov public web.

Deploy to ``/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/pomsMonitor/`` (a
sibling of ``cgi-bin/dqmTimeline``) and register in
``cgi-bin/wsgi.py``::

    from pomsMonitor import app as pomsMonitor

The Flask app object is the one defined in ``bin/pomsMonitorWeb`` —
this shim just loads it and disables the routes that would mutate state
(rebuild DB, run ``json2jobdef``, save JSON files).

Configuration via environment variables (set in the Apache vhost or
``.htaccess``):

- ``PRODTOOLS_DIR`` — prodtools checkout to import from.
  Default: ``/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/v2.0.1``.
- ``POMS_DB_PATH`` — SQLite DB the app reads.
  Default: ``<PRODTOOLS_DIR>/poms_data.db``.

See ``README.md`` next to this file for the full install / cron setup.
"""

import importlib.util
import os
import sys
import types

PRODTOOLS_DIR = os.environ.get(
    "PRODTOOLS_DIR",
    "/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/v2.0.1",
)
sys.path.insert(0, PRODTOOLS_DIR)

# samweb_client is not installed in the public-web conda env. Stub it so
# prodtools' eager `from utils import jobfcl` chain can complete; routes
# that actually call SAM at request time will fail at instantiation.
# `/api/jobs` already swallows that failure (try/except around
# locate_file). `/api/dataset/<name>` (famtree) is hard-disabled below.
if "samweb_client" not in sys.modules:
    _stub = types.ModuleType("samweb_client")

    class _SAMWebClientUnavailable:
        def __init__(self, *_a, **_kw):
            raise RuntimeError(
                "samweb_client is not installed in this WSGI env; "
                "SAM-backed routes are disabled."
            )

    _stub.SAMWebClient = _SAMWebClientUnavailable
    sys.modules["samweb_client"] = _stub

# ``bin/pomsMonitorWeb`` has no ``.py`` suffix. ``spec_from_file_location``
# infers the loader from the suffix; with no suffix it returns a spec
# without a loader. Use ``SourceFileLoader`` explicitly so the script
# can be loaded as a module regardless of its filename.
from importlib.machinery import SourceFileLoader

_script_path = os.path.join(PRODTOOLS_DIR, "bin", "pomsMonitorWeb")
_loader = SourceFileLoader("pomsMonitorWeb_app", _script_path)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(_mod)

app = _mod.app

# Override DB path if requested. The Flask app re-resolves the DB on
# every request via ``_get_session`` → ``get_default_db_path``. Note
# that ``bin/pomsMonitorWeb`` does ``from utils.db_analyzer import
# get_default_db_path`` at import time, binding the original function
# into its own module namespace — so patching only ``db_analyzer`` is
# not enough; we also patch the binding inside the loaded module.
_db_override = os.environ.get("POMS_DB_PATH")
if _db_override:
    from utils import db_analyzer
    _override_fn = lambda: _db_override
    db_analyzer.get_default_db_path = _override_fn
    _mod.get_default_db_path = _override_fn

# --- Read-only hardening -------------------------------------------------
#
# The CGI process runs as ``nobody`` on the public web host, so the
# write endpoints would fail with permission errors anyway. But two of
# them (``/api/reload`` and ``/api/json2jobdef``) shell out to long-
# running commands, and one (``/api/json2jobdef``) executes
# ``bash -c <user-supplied string>`` — strict-disable them at the
# Flask level so they cannot be abused from the public network.
_DISABLED_ENDPOINTS = {
    "api_reload",          # POST /api/reload — rebuilds DB
    "api_json2jobdef",     # POST /api/json2jobdef — runs json2jobdef
    "api_save_json_file",  # POST /api/json-file/<path> — writes JSON
    "api_dataset_info",    # GET /api/dataset/<name> — needs samweb_client
}


def _forbidden(*_args, **_kwargs):
    return ("Endpoint disabled in the public read-only deployment.\n", 403)


for endpoint in list(app.view_functions):
    if endpoint in _DISABLED_ENDPOINTS:
        app.view_functions[endpoint] = _forbidden
