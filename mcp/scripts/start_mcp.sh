#!/usr/bin/env bash
# Start the read-only prodtools MCP stdio server.
#
# All setup output goes to stderr: stdout is the JSON-RPC channel and a
# single stray line on it corrupts the protocol stream.
set -euo pipefail

MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$MCP_ROOT/.." && pwd)"

# CVMFS setup scripts are not set -e clean; guard around them.
set +u
if [[ $- == *e* ]]; then _restore_e=1; set +e; else _restore_e=0; fi
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh 1>&2
_rc=$?
if [[ ${_restore_e} -eq 1 ]]; then set -e; fi
if [[ ${_rc} -ne 0 ]]; then exit ${_rc}; fi
muse setup ops 1>&2
set -u

MU2E_OPS_PYTHONPATH="${PYTHONPATH:-}"

if [[ -n "${MCP_PYTHON:-}" ]]; then
  PYTHON_BIN="$MCP_PYTHON"
elif [[ -x "$MCP_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$MCP_ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

VENV_SITE="$("$PYTHON_BIN" - <<'PY'
import site
paths = [p for p in site.getsitepackages() if 'site-packages' in p]
print(paths[0] if paths else '')
PY
)"

# Order matters: venv first, then the repo root (prodtools_mcp imports
# utils.*), then the ops env. metacat's script has no repo-root entry to
# copy — this server needs one.
PP="$REPO_ROOT"
[[ -n "$VENV_SITE" ]] && PP="$VENV_SITE:$PP"
[[ -n "$MU2E_OPS_PYTHONPATH" ]] && PP="$PP:$MU2E_OPS_PYTHONPATH"
export PYTHONPATH="$PP"

if [[ "${1:-}" == "--check" ]]; then
  echo "== part 1: MCP deps WITHOUT the ops path ==" 1>&2
  env -u PYTHONPATH PYTHONPATH="${VENV_SITE:-}:$REPO_ROOT" \
    "$PYTHON_BIN" - <<'PY'
import importlib
importlib.import_module("mcp.server.fastmcp")
print("OK: mcp imports without the ops PYTHONPATH (self-contained)")
PY
  echo "== part 2: full environment ==" 1>&2
  "$PYTHON_BIN" - <<'PY'
import importlib, sys
importlib.import_module("mcp.server.fastmcp")
importlib.import_module("samweb_client")
from prodtools_mcp.server import get_server_info
info = get_server_info()
print("OK: interpreter", sys.executable)
print("OK: tools", ", ".join(info["tools"]))
PY
  exit 0
fi

exec "$PYTHON_BIN" -m prodtools_mcp.server
