#!/usr/bin/env bash
# Build the venv and verify it. Run once after checkout.
set -euo pipefail

MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$MCP_ROOT"

set +u
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh 1>&2 || true
muse setup ops 1>&2
set -u

# Record which spack env this venv's interpreter binds to. An ops-env
# retirement changes the failure mode from an import error to a failed
# exec, so the binding is worth having written down.
echo "binding to: $(command -v python3)" | tee "$MCP_ROOT/.venv-binding"

python3 -m venv .venv
# --upgrade so the venv carries its OWN transitive deps. metacat's venv
# does not, and survives only because the ops PYTHONPATH supplies idna.
./.venv/bin/pip install --upgrade pip 1>&2
./.venv/bin/pip install -e . 1>&2

echo "== verifying =="
exec "$MCP_ROOT/scripts/start_mcp.sh" --check
