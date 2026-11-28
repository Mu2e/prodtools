#!/usr/bin/env bash
# Start the WRITE-capable prodtools MCP stdio server.
#
# All setup output goes to stderr: stdout is the JSON-RPC channel and a
# single stray line on it corrupts the protocol stream.
#
#   --self-only   refuse run_as="mu2epro" in every tool, confirm or not
#                 (sets PRODTOOLS_WRITE_SELF_ONLY=1). For a client that asks
#                 no human before a tool call, e.g. an agent's automations.
#   --check       build the server, check its tool list, and exit.
#
# Any other argument is refused, so a mistyped --self-only can never start
# a server that takes mu2epro.
set -euo pipefail
MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

check=0
for arg in "$@"; do
  case "$arg" in
    --self-only) export PRODTOOLS_WRITE_SELF_ONLY=1 ;;
    --check) check=1 ;;
    *) echo "start_write_mcp.sh: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

. "$MCP_ROOT/scripts/_mcp_env.sh"

if [[ "$check" == 1 ]]; then
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
