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
# CLOSED: only a positively parsed run_as=="self" — or the one tool
# named in the whitelist below — passes silently. run_as=="mu2epro", a
# missing run_as, a malformed payload, an unrecognised run_as value, or
# jq itself failing/missing all prompt. The final payload is built with
# plain printf (not jq) specifically so a missing/broken jq binary
# cannot suppress the prompt it should cause.
#
# WHITELIST (operator decision, 2026-08-15): run_submissions is exempt.
# It only feeds slices from a campaign whose creation was already
# gated at push_cnf, and its worst case is grid hours a `jobsub_rm`
# can reclaim. push_cnf stays gated: it registers SAM datasets and
# burns a cnf name that can never be reused. Deliberately keyed on the
# EXACT tool name, so a future write tool added to this server still
# inherits the prompt — the property this hook exists to preserve.
set -o pipefail
input=$(cat)
run_as=$(printf '%s' "$input" | jq -r '.tool_input.run_as // empty' 2>/dev/null)
jq_ok=$?
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null)

if [ "$jq_ok" -eq 0 ] && [ "$run_as" = "self" ]; then
  exit 0
fi

if [ "$jq_ok" -eq 0 ] && [ "$tool" = "mcp__prodtools-write__run_submissions" ]; then
  exit 0
fi

if [ "$jq_ok" -eq 0 ] && [ "$run_as" = "mu2epro" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"This call runs as the mu2epro PRODUCTION account: it can register SAM datasets, write dCache, and submit production grid jobs. Not reversible. Confirm before executing."}}'
else
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Could not determine run_as for a prodtools-write call (missing run_as, malformed hook input, an unrecognised value, or jq failed). Confirming before executing: this may be a production mu2epro call."}}'
fi
exit 0
