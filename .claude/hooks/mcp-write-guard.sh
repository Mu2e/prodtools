#!/bin/bash
# PreToolUse guard for the prodtools-write MCP server.
#
# The Bash guard (mu2epro-guard.sh) greps the command string for
# `ksu mu2epro`. A subprocess spawned INSIDE the MCP server is not a
# Bash tool call, so that hook can never see it — this one covers the
# whole mcp__prodtools-write__* namespace instead of enumerating tools,
# so a future write tool cannot silently escape the gate.
#
# run_as="self" is allowed through: it writes only the caller's own
# scratch, datasets and ledger. Only run_as="mu2epro" prompts.
input=$(cat)
run_as=$(printf '%s' "$input" | jq -r '.tool_input.run_as // ""')
if [ "$run_as" = "mu2epro" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"This call runs as the mu2epro PRODUCTION account: it can register SAM datasets, write dCache, and submit production grid jobs. Not reversible. Confirm before executing."}}'
fi
exit 0
