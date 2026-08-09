#!/usr/bin/env bash
# Start the WRITE-capable prodtools MCP stdio server.
#
# All setup output goes to stderr: stdout is the JSON-RPC channel and a
# single stray line on it corrupts the protocol stream.
set -euo pipefail
MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$MCP_ROOT/scripts/_mcp_env.sh"

if [[ "${1:-}" == "--check" ]]; then
  "$PYTHON_BIN" - <<'PY'
import asyncio
from prodtools_mcp_write.server import create_write_mcp_server, TOOL_NAMES
registered = sorted(t.name for t in asyncio.run(create_write_mcp_server().list_tools()))
if registered != sorted(TOOL_NAMES):
    raise SystemExit(f"tool registration mismatch: {registered} != {sorted(TOOL_NAMES)}")
print("OK: write tools", ", ".join(registered))
PY
  exit 0
fi

exec "$PYTHON_BIN" -m prodtools_mcp_write.server
