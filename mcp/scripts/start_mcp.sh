#!/usr/bin/env bash
# Start the read-only prodtools MCP stdio server.
#
# All setup output goes to stderr: stdout is the JSON-RPC channel and a
# single stray line on it corrupts the protocol stream.
set -euo pipefail

MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$MCP_ROOT/scripts/_mcp_env.sh"

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
from prodtools_mcp.server import create_mcp_server, get_server_info, TOOL_NAMES
info = get_server_info()
# Build the server for real: this is the only automated check that the
# @mcp.tool decorators resolve without collision and that every
# advertised name is actually registered. Without it a registration
# regression passes install.sh and only breaks at first client use.
import asyncio
server = create_mcp_server()
registered = sorted(t.name for t in asyncio.run(server.list_tools()))
if registered != sorted(TOOL_NAMES):
    raise SystemExit(f"tool registration mismatch: registered={registered} advertised={sorted(TOOL_NAMES)}")
print("OK: interpreter", sys.executable)
print("OK: tools", ", ".join(registered))
PY
  echo "== part 3: HTCondor client matches this node ==" 1>&2
  "$PYTHON_BIN" - <<'PY'
from prodtools_mcp import condor
report = condor.version_report()
if report['series_match'] is not True:
    raise SystemExit(
        f"FATAL: {report['reason']}\n"
        f"  client={report['client']} node={report['node']}")
print(f"OK: htcondor client {report['client']} matches node "
      f"condor {report['node']}")
PY
  exit 0
fi

exec "$PYTHON_BIN" -m prodtools_mcp.server
