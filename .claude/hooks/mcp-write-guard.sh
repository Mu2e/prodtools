#!/bin/bash
# PreToolUse guard for the prodtools-write MCP server.
#
# The Bash guard (mu2epro-guard.sh) greps the command string for
# `ksu mu2epro`. A subprocess spawned INSIDE the MCP server is not a
# Bash tool call, so that hook can never see it — this one covers the
# whole mcp__prodtools-write__* namespace instead of enumerating tools,
# so a future write tool cannot silently escape the gate.
#
# runner.require_confirmed's confirm=true is a MODEL-facing gate (the
# model supplies it to itself), so this hook is the ONLY human-in-the-
# loop checkpoint on a call that can register production SAM artifacts
# and submit production grid jobs. A sole human checkpoint must FAIL
# CLOSED: only a positively parsed run_as=="self" passes silently.
# run_as=="mu2epro", a missing run_as, a malformed payload, an
# unrecognised run_as value, or jq itself failing/missing all prompt.
# The final payload is built with plain printf (not jq) specifically so
# a missing/broken jq binary cannot suppress the prompt it should cause.
set -o pipefail
input=$(cat)
run_as=$(printf '%s' "$input" | jq -r '.tool_input.run_as // empty' 2>/dev/null)
jq_ok=$?

if [ "$jq_ok" -eq 0 ] && [ "$run_as" = "self" ]; then
  exit 0
fi

if [ "$jq_ok" -eq 0 ] && [ "$run_as" = "mu2epro" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"This call runs as the mu2epro PRODUCTION account: it can register SAM datasets, write dCache, and submit production grid jobs. Not reversible. Confirm before executing."}}'
else
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Could not determine run_as for a prodtools-write call (missing run_as, malformed hook input, an unrecognised value, or jq failed). Confirming before executing: this may be a production mu2epro call."}}'
fi
exit 0
