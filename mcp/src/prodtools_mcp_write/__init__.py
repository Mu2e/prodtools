"""Write-capable MCP server for prodtools submission.

Deliberately SEPARATE from prodtools_mcp, which advertises that it
performs no writes. That claim is why its tools are called without
deliberation; mixing writes in would turn it into "read-only except
these three", a caveat that erodes. A separate tool namespace also
means one PreToolUse matcher covers every write tool that will ever
exist, instead of an enumeration a future tool can silently escape.
"""
