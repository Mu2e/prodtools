#!/usr/bin/env bash
# Build the venv and verify it. Run once after checkout.
set -euo pipefail

MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$MCP_ROOT"

set +u
# CVMFS setup scripts are not set -e clean; guard around them the same
# way start_mcp.sh does. `|| true` here would silently swallow a real
# CVMFS outage and let the venv build against a half-set-up environment.
if [[ $- == *e* ]]; then _restore_e=1; set +e; else _restore_e=0; fi
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh 1>&2
_rc=$?
if [[ ${_restore_e} -eq 1 ]]; then set -e; fi
if [[ ${_rc} -ne 0 ]]; then exit ${_rc}; fi
muse setup ops 1>&2
set -u

# Record which spack env this venv's interpreter binds to. An ops-env
# retirement changes the failure mode from an import error to a failed
# exec, so the binding is worth having written down.
echo "binding to: $(command -v python3)" | tee "$MCP_ROOT/.venv-binding"

python3 -m venv .venv
# --upgrade so the venv carries its OWN transitive deps. metacat's venv
# does not, and survives only because the ops PYTHONPATH supplies idna.
#
# pip must NOT see the ops PYTHONPATH: with it exported, pip resolves
# idna/certifi/jsonschema against the spack env, marks them satisfied,
# and skips installing them into .venv — leaving exactly the
# non-self-contained venv that part 1 of the check then fails on.
env -u PYTHONPATH ./.venv/bin/pip install --upgrade pip 1>&2
env -u PYTHONPATH ./.venv/bin/pip install -e . 1>&2

echo "== verifying =="
exec "$MCP_ROOT/scripts/start_mcp.sh" --check
