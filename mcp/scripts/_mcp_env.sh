# Shared environment preamble for the prodtools MCP stdio launchers.
#
# Sourced, never executed: it must not `exec` or `exit` on the success
# path. All setup output goes to stderr: stdout is the JSON-RPC channel
# and a single stray line on it corrupts the protocol stream.
#
# Callers must `set -euo pipefail` and define MCP_ROOT before sourcing
# this file.

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
export PYTHON_BIN
