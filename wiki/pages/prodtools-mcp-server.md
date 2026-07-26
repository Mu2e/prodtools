---
title: prodtools MCP server (read-only)
tags: [reference, mcp, tooling, commissioned]
sources: [2026-07-26-prodtools-mcp-design]
updated: 2026-07-26
---

# prodtools MCP server (read-only)

Stdio MCP server exposing prodtools campaign status and dataset
discovery to any MCP client. Spec:
`docs/superpowers/specs/2026-07-26-prodtools-mcp-design.md`.

## What it is

Six tools: `campaign_status`, `list_campaigns`, `find_datasets`,
`dataset_details`, `trace_provenance`, `get_server_info`. It imports
`utils/*` in-process and composes existing functions; there is no LLM
in it and it makes no external API calls.

It performs **no writes** — no submission, no SAM definition create or
delete, no ledger mutation. Every tool is safe as the calling user;
none needs mu2epro.

## Reading the output

- `campaign_status()` with no argument is ledger-only and cheap. Naming
  a campaign adds queue and output counts, which hit the network.
- **`state: "unknown"` is not zero.** An unknown queue block omits its
  count keys entirely. Proc-form `jobsub_q` was verified on 2026-07-22
  reporting 0 total while 1976 jobs of one cluster ran, so a
  `{"running": 0}` from a failed query would read as "drained" and could
  trigger a recovery pass against live jobs.
- `find_datasets` reports a **definition listing**, not existence:
  zero-file definitions appear and `-LH`/`-CH` variants do not. Its
  `basis` field says so on every response. Pass `require_files=True`
  when you need existence.

## Operating it

```bash
bash mcp/scripts/install.sh            # once
bash mcp/scripts/start_mcp.sh --check  # health
mcp/.venv/bin/python mcp/scripts/smoke_test_stdio.py
```

`--check` is two-part on purpose. Part 1 imports the MCP dependencies
**without** the ops `PYTHONPATH`. The neighbouring metacat server fails
exactly this — `import mcp` raises `ModuleNotFoundError: No module
named 'idna'` — and works only because its start script layers the ops
path underneath, leaving it one ops-env bump from breaking. Part 2
verifies the full environment and builds the server via `create_mcp_server()`,
comparing the registered tool names against the advertised list, so a
registration regression fails the check.

The venv's interpreter still binds to the ops spack view (system
`/usr/bin/python3` is 3.9, too old for `mcp`). `install.sh` records the
binding in `mcp/.venv-binding`; an ops-env retirement will present as a
failed exec rather than an import error.

## Design notes worth keeping

- **stdout is the JSON-RPC channel.** `utils/famtree.py:71` prints to
  stdout on the not-found path, directly on `trace_provenance`'s route,
  so `adapters.safe_tool` redirects stdout to stderr around every call.
- **`SystemExit` is trapped explicitly** — it derives from
  `BaseException`, so `except Exception` misses it and an uncaught one
  would kill the server rather than fail one call.
- **The ledger is opened `mode=ro` with no DDL.**
  `submission_ledger._connect` issues `CREATE` statements on every
  connect; a future schema addition shipped before mu2epro's writer runs
  it would otherwise break every status call.

## Not included

Job submission. It was designed alongside these tools and pulled out
after review found the direct path never calls `check_inputs`
(`utils/submit.py:241` runs only under `--enqueue`) and has no
idempotency guard under client timeouts. Until a follow-on spec lands,
`/mu2epro-submit` is the submission path.
