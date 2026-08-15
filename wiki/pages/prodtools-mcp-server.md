---
title: prodtools MCP server (read-only)
tags: [reference, mcp, tooling, commissioned]
sources: [2026-07-26-prodtools-mcp-design]
updated: 2026-08-09
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
- **`campaign_status` and `list_campaigns` read PRODUCTION by default,
  and take `mine: bool = False`.** Omitting it (or passing `false`)
  reads the production ledger and mu2epro's queue, exactly as before
  `mine` existed. `mine=true` switches BOTH the ledger path and the
  HTCondor queue owner to the caller's own account, from one resolution
  (`status._resolve_identity`) — the two axes coming apart is the exact
  bug this parameter exists to prevent; it already shipped once on the
  write side (`171517f`, `live_clusters()` defaulting to mu2epro). A
  personal (`run_as="self"`) campaign is invisible under the default
  call — an empty ledger reads exactly like "no campaigns" — so a
  self-submitted campaign needs `mine=true` to be found at all. Every
  reply names what it read: `db_path` at the top level, and `owner`
  inside each `queue` block. Another user's ledger is not reachable
  through MCP; use `submissions --db <path> status`.
- **The queue block comes from live HTCondor ClassAd queries**
  (`mcp/src/prodtools_mcp/condor.py`), not `jobsub_q` table parsing.
  This is an INDEPENDENT path from `utils/submissions.py`'s
  `live_clusters()`/`cluster_queue_state()`, which back the live
  production cron and stay untouched — the MCP server queries the pool
  itself, in-process, via the `htcondor` Python bindings (the v2 bindings
  from a PyPI cp310 wheel whose series is derived at install time from
  `/usr/bin/condor_version` — `mcp/pyproject.toml` carries only a
  floor, and `start_mcp.sh --check` fails when client and node
  disagree; the system RPM htcondor is py3.9-only, which is why this
  is a venv dep). Queries only the schedds whose
  `Name` starts with `jobsub` (8 daemons advertised, ~6 are jobsub
  schedds), filters server-side in the constraint
  (`Owner==<resolved account> && JobStatus==...` — `"mu2epro"` by
  default, or the caller's own account under `mine=true`), and projects
  only `ClusterId`/`JobStatus`/`HoldReasonCode`/`HoldReason` — never
  whole ClassAds. Measured live against the real pool 2026-07-26: ~0.5s
  for 190 clusters / ~500 jobs across 6 schedds queried in parallel.
  A literal `htcondor==23.0.*` pin lived here until 2026-08-09 and went
  stale against a 25.0.12 pool upgrade. The old client's SCITOKENS
  authentication was rejected by the collector, so schedd discovery
  raised before any schedd was contacted and every queue block read
  `unknown` — while `jobsub_q` on the same node worked, because it uses
  the node's own 25.x bindings. Token expiry was NOT the cause: the
  23.0.28 client fails with a freshly minted bearer token, and the
  25.0.12 client succeeds with an expired one.
- **`state: "unknown"` is not zero.** An unknown queue block omits its
  count keys entirely. Proc-form `jobsub_q` was verified on 2026-07-22
  reporting 0 total while 1976 jobs of one cluster ran, so a
  `{"running": 0}` from a failed query would read as "drained" and could
  trigger a recovery pass against live jobs. `condor.query_owner_jobs()`
  preserves the same fail-closed contract: a bounded ~60s wall clock
  (FastMCP runs sync tools inline on the event loop), and if EITHER the
  query times out OR any single schedd is unreachable, the whole result
  is `None` — never a partial undercount from the schedds that did
  answer (an undercount reads exactly like "drained").
- **`held > 0` adds `hold_reasons`**: `[{code, count, example}, ...]`
  sorted by count descending, aggregated by `HoldReasonCode`. Grouping
  by the `HoldReason` TEXT instead is the trap — the text embeds the
  slot and host (`Error from slot1_26@fnpc19131.fnal.gov: ...`), so
  every job's string is unique and a text-keyed count returns one entry
  per job. `example` is deliberately singular (one representative
  string, truncated) so it can't be mistaken for an aggregate. Verified
  live 2026-07-26 against MDC2025au: RPC campaigns showing `code: 34`,
  "Docker job has gone over memory limit of 2000 Mb", correctly
  collapsed to one entry each (counts 50 and 237) despite every job's
  `HoldReason` string differing by slot/host.
- `find_datasets` reports a **definition listing**, not existence:
  zero-file definitions appear and `-LH`/`-CH` variants do not. Its
  `basis` field says so on every response. Pass `require_files=True`
  when you need existence.
- **`find_datasets` `pattern` is a SQL `LIKE`, not a glob.** SAM's
  `defname` filter uses `%`; a `*` matches nothing and would come back
  as an empty list with no error. The tool translates a caller-typed
  `*`, so either works. Results cap at `limit` (default 500, hard
  ceiling `MAX_LIMIT = 5000`) with `truncated` reporting whether the cap
  bit, and `require_files=True` is refused above the cap rather than
  issuing one serial SAM query per record. The ceiling exists because
  the refusal's own remedy says "raise limit deliberately" — without one,
  that invites `require_files=True, limit=100000` and exactly the
  serial-query fan-out the refusal is there to prevent.
- **`trace_provenance` is bounded by `max_nodes` as well as `depth`.**
  `depth` alone does not bound the query cost: each level multiplies —
  a mixed `dig` file has ~33 parents, one `parents_of_file` call costs
  ~0.5s, and the default `depth=3` could fan out to thousands of serial
  SAM queries (roughly 47 minutes), wedging the whole server since
  FastMCP runs sync tools inline on the event loop. `max_nodes` (default
  500, hard ceiling `MAX_NODES = 2000`, mirroring `find_datasets`'
  `limit`) caps the walk on nodes discovered. The check runs **before**
  `edge_fn` is called for the next node, so a spent budget stops new
  queries rather than merely trimming the answer afterward — confirmed
  live against a real `MDC2025au` mixed `dig` file on 2026-07-26: the
  default call (`depth=3`, `max_nodes=500`) returned in well under a
  second with `truncated: true`, issuing only a couple of
  `parents_of_file` calls rather than the thousands an unbudgeted walk
  would have made. `truncated` covers both cutoffs; `max_nodes` is
  echoed in the response the way `limit` is.
- **`rows` is a count per submission state**, not open/closed.
  `exhausted` means the attempt cap was reached and a human must take
  over; bucketed with `complete`/`recovered` it was invisible.
- **`outputs[]` carries two denominators.** `submitted` is the campaign
  cursor — indices actually handed to the grid — and
  `expected_at_completion` is `njobs`. Every direct campaign is sliced,
  so judge what is in flight against `submitted`: a fully-landed cursor
  500 of njobs 4000 is 100% of what was submitted, not 12.5%.
- **Error `kind` distinguishes auth from outage.** `env_missing` means
  the ops environment is not on the path (`muse setup ops`);
  `auth_expired` means renew in your own shell — the server never
  refreshes credentials and you must not retry until you have.
  Classification keys on the exception's status **code**, not its text:
  `samweb_client.exceptions.SAMWebHTTPError.code` is a plain int, and a
  substring match on `'401'`/`'403'` used to misfire both ways — Mu2e
  filenames routinely contain sequences like `403` (a run/subrun
  sequencer, e.g.
  `dig.mu2e.FlatGamma.MDC2025au_best_v1_3.001430_00004031.art`), and
  `SAMWebHTTPError.__str__` embeds the URL — and therefore the filename
  — for every 5xx, so a plain SAM outage on such a file misclassified as
  `auth_expired`. The inverse also held: `__str__` returns only the
  message for 4xx, so real `401`/`403` never showed the code in text and
  fell through to `catalog_unavailable`. A word-only text fallback
  (`unauthorized`, `forbidden`, `credential`, `authentication failed`,
  `token` — never a bare digit) still covers exception types with no
  `.code`.

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

- **stdout is the JSON-RPC channel.** `trace_provenance` no longer
  touches `famtree` at all — `lineage.py` calls
  `samweb_wrapper.parents_of_file`/`children_of_file` directly — so
  `famtree.py:71`'s not-found print is not actually on this route. The
  guard stays in as defence in depth: `utils/samweb_wrapper.py` prints on
  error at several call sites of its own (e.g. `describe_definition:182`),
  and `definition_creation_date`'s text-fallback path
  (`samweb_wrapper.py:250`) reaches it, putting that print on
  `dataset_details`'s route. `adapters.safe_tool` redirects stdout to
  stderr around every call regardless of which util path triggers it.
- **`SystemExit` is trapped explicitly** — it derives from
  `BaseException`, so `except Exception` misses it and an uncaught one
  would kill the server rather than fail one call.
- **The ledger is opened `mode=ro` with no DDL.**
  `submission_ledger._connect` issues `CREATE` statements on every
  connect; a future schema addition shipped before mu2epro's writer runs
  it would otherwise break every status call.
- **Campaigns and rows come from one `ledger_ro.snapshot()`.** The cron
  commits `record_submission` and `advance_campaign` separately, so two
  independent reads can report a `cursor` that disagrees with the rows
  beside it. One connection, one deferred transaction.
- **Lineage edge functions must fail loudly.** `trace_provenance` uses
  `samweb_wrapper.parents_of_file`, not `famtree.get_parents` — the
  latter delegates to `file_lineage`, which swallows every exception and
  returns `[]`. For a lineage tool that reads as "this file is a
  primary", and an `lru_cache` would keep serving it after SAM
  recovered.
- **Every network call is bounded.** The HTCondor queue query
  (`condor.query_owner_jobs`) runs its per-schedd fan-out under a
  bounded executor with a ~60s total timeout, and a timeout renders as
  queue `state: "unknown"`. FastMCP runs sync tools inline on the event
  loop, so an unbounded wait wedges the whole server, not one call —
  the same class of bug the `trace_provenance` fan-out fix addressed.

## Not included

Job submission. It was designed alongside these tools and pulled out
after review found the direct path never calls `check_inputs`
(`utils/submit.py:241` runs only under `--enqueue`) and has no
idempotency guard under client timeouts. Until a follow-on spec lands,
`/mu2epro-submit` is the submission path.
