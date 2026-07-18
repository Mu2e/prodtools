#!/bin/bash
# PreToolUse(Bash) guard for prodtools.
#
# Forces a permission prompt for any Bash command that runs as the mu2epro
# PRODUCTION account (i.e. contains `ksu mu2epro`, allowing arbitrary leading
# words like `timeout 590 ksu mu2epro -e ...`). Those commands can register or
# retire SAM datasets, write dCache, edit POMS maps, and submit grid jobs, so
# they should never run without an explicit human OK.
#
# Non-matching commands produce no output and exit 0 -> the normal permission
# flow proceeds unchanged (allow).
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')
if printf '%s' "$cmd" | grep -qE 'ksu[[:space:]]+mu2epro'; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"This command runs as the mu2epro PRODUCTION account (ksu mu2epro): it can register/retire SAM datasets, write dCache, edit POMS maps, and submit grid jobs. Confirm before executing."}}'
fi
exit 0
