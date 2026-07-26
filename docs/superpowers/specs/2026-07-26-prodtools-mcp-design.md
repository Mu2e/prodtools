# prodtools MCP server — design

**Date:** 2026-07-26
**Status:** approved for planning

## Goal

Expose prodtools campaign status, dataset discovery/provenance, and job
submission as an MCP stdio server, so any MCP client — not just this
repo's Claude Code session with its `.claude/` skills — can drive
production work through typed tools returning structured JSON.

## Motivation

Four drivers, all selected:

1. **Reach other clients.** Collaborators using Claude Desktop, ChatGPT,
   Cursor, or their own agents get prodtools without needing this repo's
   skills directory.
2. **Structured JSON instead of stdout.** Callers receive typed objects
   rather than parsing command text. Fewer parse mistakes, far less
   context spent on log dumps.
3. **Safety enforced in code.** Rules that today live in skill markdown
   (which a model can rationalize past) become the tool surface itself.
4. **Less setup overhead.** Each skill invocation re-sources
   `setupmu2e-art.sh` + `muse setup`. A long-lived server pays that once.

## Non-goals

- **No HTTP transport.** stdio only. An HTTP service would submit as
  whoever runs the server, giving every client one shared identity and
  requiring an authn/authz layer that does not exist today. stdio keeps
  authorization exactly where it already is: Kerberos plus
  `~mu2epro/.k5users`.
- **No server-side submission gate.** Deliberate, decided by the
  production manager. Submission safety rests on the MCP client's own
  tool-approval prompt. See "Accepted risks".
- **No `check_inputs` tool.** It is already the `--enqueue` gate inside
  `submit_map`; its report surfaces through `submit_campaign`'s dry run.
- **Not a replacement for the skills.** `/mu2epro-run` and
  `/mu2epro-submit` remain the path when a Musing release must be
  selected per invocation. The two coexist.

## Prior art and environment constraints

`metacat-readonly` (in the sibling `aitools/` repo, wired into this
repo's `.mcp.json`) proves the pattern in this exact environment:
FastMCP stdio server, 481 LOC, 4 tools, read-only, started by a script
that sources the Mu2e environment first.

Two constraints discovered while surveying it:

**Musing releases cannot be held in-process.** `muse setup` aborts if
`MUSE_WORK_DIR` is already set, and the required release varies per call
(`SimJob/MDC2025au` for mixing, `AnalysisMDC2025/v02_00_00` for
ntuples). Anything needing a Musing must fork a subprocess. This is why
`json2jobdef`, `jobfcl`, `fcldump`, and `runmu2e` are out of scope for
in-process tools.

**The metacat venv is not self-contained.** `.venv/bin/python -c
"import mcp"` fails with `ModuleNotFoundError: No module named 'idna'`.
It works only because `start_mcp.sh` composes
`PYTHONPATH=$VENV_SITE:$MU2E_OPS_PYTHONPATH`, letting `muse setup ops`
silently supply the missing transitive dependency. The server is one
ops-env bump from breaking, and the failure would present as a traceback
inside `httpx`. This design installs deps completely and verifies it.

Command tiers by environment need:

| Tier | Requires | Commands |
|---|---|---|
| Pure Python | nothing | `jobquery` |
| `muse setup ops` | samweb/mdh/metacat | `latestDatasets`, `listNewDatasets`, `famtree`, `logparser`, `datasetFileList`, `check_inputs`, `submissions` |
| `pyenv ana` | SQLAlchemy | `pomsMonitor`, `listNewDatasets --completeness` |
| A Musing release | `muse setup SimJob/<tag>` | `json2jobdef`, `jobfcl`, `fcldump`, `runmu2e` |

Tools in this design draw only from the first two tiers in-process.

## Architecture

In-tree, versioned alongside the `utils/` modules it imports:

```
prodtools/mcp/
  pyproject.toml              # mcp>=1.2.0, deps resolved COMPLETELY
  scripts/start_mcp.sh        # source env, compose PYTHONPATH, exec server
  scripts/install.sh          # build venv, run two-part check
  scripts/smoke_test_stdio.py # spawn server, list tools, call get_server_info
  src/prodtools_mcp/
    __init__.py
    server.py                 # FastMCP wiring ONLY — no logic
    adapters.py               # error envelope, SystemExit trap
    tools/status.py           # campaign_status, list_campaigns
    tools/discovery.py        # find_datasets, dataset_details, trace_provenance
    tools/submit.py           # submit_campaign
```

**The load-bearing boundary:** tool functions are plain Python taking
plain arguments and returning plain dicts. FastMCP decorators appear
only in `server.py`. Every tool is therefore testable in the existing
`test/test_unit.py` suite with no MCP machinery and no stdio transport —
matching how `submissions.py` already accepts `runner=subprocess.run`
and `sam_lister=files_in_dataset`.

**Execution strategy:** in-process imports, subprocess only where
forced. The `utils/` modules are already factored for this —
`submission_ledger.all_campaigns()`/`all_rows()` return dicts,
`submissions.live_clusters()`/`cluster_queue_state()`/`verify_row()`
take injectable runners, `famtree.topology_for_dataset()` and
`latestDatasets.latest_per_description()` are plain functions.
Subprocess is reserved for `submit_campaign`, which needs
`ksu mu2epro`.

**Imports are lazy, inside tool functions** — the pattern
`utils/check_inputs.py:21` already establishes, so `server.py` imports
cleanly without the Mu2e environment and the tools stay unit-testable.

## Tool surface

Seven tools.

### `campaign_status(campaign=None, campaign_id=None, include_queue=True, include_outputs=True)`

Composes `submission_ledger.all_campaigns`/`all_rows`,
`submissions.live_clusters`/`cluster_queue_state`, and SAM output
counts.

Called with no argument it is **ledger-only** — local sqlite, no
network. Naming a campaign opts into the expensive parts;
`include_queue` and `include_outputs` gate them separately. Without
this, a bare status call against a 23-row ledger fans out to one SAM
count per output dataset and exceeds the client's timeout.

```json
{
  "db_path": "/exp/mu2e/data/users/mu2epro/prodtools/submissions.db",
  "campaigns": [
    {
      "id": 10,
      "state": "active",
      "tarball": "cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar",
      "entry": 0,
      "map_path": "/tmp/map_wave2_au_mix.json",
      "slice_size": 500,
      "cursor": 4000,
      "njobs": 4000,
      "created_utc": "2026-07-25T18:02:11+00:00",
      "rows": {"open": 2, "closed": 6},
      "queue": {"state": "known", "running": 12, "idle": 0,
                "held": 1, "clusters": [29308498]},
      "outputs": {"state": "known", "datasets": [
        {"dataset": "dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art",
         "expected": 800, "produced": 412}
      ]}
    }
  ]
}
```

When a sub-query fails, its block becomes
`{"state": "unknown", "reason": "..."}` **with the count keys absent
entirely** — see "Error contract".

### `list_campaigns(state=None)`

Thin ledger listing, no network. Orientation call: "what is running at
all?" `state` filters over `CAMPAIGN_STATES`
(`active`/`complete`/`paused`/`cancelled`).

### `find_datasets(campaign=None, tier=None, desc=None, pattern=None, latest_only=False)`

Wraps `latestDatasets.fetch_definitions` and `latest_per_description`.

```json
{"count": 19, "truncated": false, "datasets": [
  {"name": "dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art",
   "tier": "dig", "owner": "mu2e", "desc": "FlatGamma",
   "dsconf": "MDC2025au_best_v1_3", "file_format": "art"}
]}
```

### `dataset_details(dataset)`

Wraps `samweb_wrapper.dataset_summary` and `q_dataset`.

```json
{"dataset": "...", "exists": true, "file_count": 800,
 "event_count": 4000000, "total_size_bytes": 4294967296,
 "created_utc": "2026-07-25T02:11:00+00:00"}
```

Existence is determined by file count, not by definition listing —
`samweb list-definitions` does not imply file metadata, and a
definition-only check misses `-LH`/`-CH` variants.

### `trace_provenance(name, direction="up", depth=3)`

Wraps `famtree.get_parents` / `topology_for_dataset`. Returns edges and
nodes as data — **not** the mermaid diagram string, which is a
presentation format the caller can build itself.

```json
{"root": "...", "direction": "up", "depth": 3, "truncated": false,
 "nodes": ["..."], "edges": [{"child": "...", "parent": "..."}]}
```

### `submit_campaign(map_path, entry=None, first=None, num=None, dry_run=True)`

The one write tool. Subprocesses `ksu mu2epro` running `submit_map`,
with the environment fixes baked in as code rather than markdown:

```
unset MUSE_WORK_DIR
export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
WORKDIR=$(mktemp -d /tmp/mu2epro_submit.XXXXXX)
export XDG_RUNTIME_DIR="$WORKDIR"
```

`unset MUSE_WORK_DIR` alone — not `MUSE_*`, which wipes `MUSE_DIR` and
breaks the `muse` shell function; and not `PATH`. The private
`XDG_RUNTIME_DIR` matters because `condor_vault_storer` mktemps there,
and the caller's `/run/user/<uid>` is not writable by mu2epro.

#### Why ksu is per-call, not at startup

Submission identity is not a flag. `utils/submit.py:479` and `:613`
take the submitter from `getpass.getuser()`, and
`jobsub_argv.role_for_user()` maps `mu2epro` to the `Production` grid
role and everyone else to jobsub's default. The **OS user of the
process** is what decides whether a submission is a production
submission.

Production submission therefore requires the process to *be* mu2epro —
but not for `ksu` to live inside the tool. Two alternatives were
considered:

- **Server-wide mu2epro:** `start_mcp.sh` ksu's once and the whole
  server runs as mu2epro. Simplest — `submit_map` becomes an ordinary
  call. Rejected: every read-only tool would then run as mu2epro too,
  against the standing practice that status checks never need it, and a
  long-lived process would sit at production privilege all day.
- **Two servers:** a read-only `prodtools` as the calling user, plus a
  `prodtools-submit` whose start script ksu's once. Preserves the
  separation but doubles the registration and still holds privilege for
  that server's whole life.

Per-call ksu was chosen for least privilege: mu2epro is held for the
seconds a submission takes. The cost is the ticket dependency in
"Error contract" rule 4 — the server's inherited Kerberos cache must
still be valid at call time, and an expired one returns `auth_expired`.

Note that ksu cannot be eliminated under any of the three: the MCP
client spawns the server as the calling user, so becoming mu2epro is
always an explicit step somewhere.

`dry_run` **defaults to `True`**. This is not a gate — a caller may pass
`dry_run=False` in a single call with no token and no second step. It
means the unparameterized call is the safe one, matching
`/mu2epro-submit`'s dry-run-first flow.

**Verification is mandatory.** `jobsub_submit` can exit 0 while
`condor_submit` failed, leaving no cluster. The tool parses the cluster
ID, re-queries `jobsub_q`, and returns `verified`. A result with
`cluster_id: null` is a failure and is reported as one.

```json
{"dry_run": false, "map_path": "...", "entry": 0,
 "window": {"first": 0, "num": 500}, "total_jobs": 500,
 "check_inputs": {"ok": true, "problems": []},
 "cluster_id": 29308498, "verified": true, "queued": 500}
```

### `get_server_info()`

Static capabilities and safe-usage guidance, as metacat's does.

### Explicitly not exposed

`samweb_wrapper` exports `create_definition` and `delete_definition`.
In-process these are one import away. **The tool layer never calls
them.** Stated here so the constraint survives future edits.

## Error contract

Every tool returns its success dict or:

```json
{"error": {"kind": "...", "message": "...", "remedy": "..."}}
```

`kind` comes from a closed set: `env_missing`, `auth_expired`,
`catalog_unavailable`, `not_found`, `invalid_argument`, `internal`.

Four rules:

**1. `SystemExit` must be caught explicitly.** It derives from
`BaseException`, so `except Exception` does not catch it. `jobquery.py`
has six `sys.exit(1)` call sites, `runmu2e.py` five, and
`_guards.require_packages` exits 2 when SQLAlchemy is absent. Uncaught,
any of these terminates the server mid-session rather than failing one
call. Containing this is `adapters.py`'s reason to exist and is the one
hazard the in-process approach buys.

**2. "Unknown" must never render as "zero".** `queue_state` has a known
fail-open bug where proc-form `jobsub_q` reports 0 total while jobs run,
and `jobsub_q -af` is unreliable enough that `submissions.py` parses the
default table fail-closed. A failed queue query serialized as
`{"running": 0}` would lead a caller to conclude the campaign drained
and start a recovery pass against live jobs. Therefore an unknown block
carries `state: "unknown"` and **omits the count keys entirely**, so
there is no zero to misread. Tools reuse `submissions.live_clusters()`
and `cluster_queue_state()` rather than reimplementing the parse.

**3. Fail loudly; never substitute empty.** A SAM outage returns
`catalog_unavailable`, not an empty dataset list. An empty list is a
finding; manufacturing one from an error is how a campaign gets declared
complete that is not.

**4. Auth failures report, never remediate.** The server inherits its
Kerberos cache from whatever launched it, so `ksu mu2epro` works only
while that ticket is valid; a long-running server will start failing
submissions after it lapses. That returns `auth_expired` with "renew in
your own shell" as the remedy. The server never invokes `htgettoken`,
`kinit`, or any token refresh — the same standing rule the skills carry.

## Testing

Tool functions are plain Python, so tests live in the existing
`test/test_unit.py` (540 tests) with no MCP machinery. Dependencies
inject as they already do elsewhere: `runner=` for subprocess,
`sam_lister=` for catalog, `db_path=` pointed at a tmp sqlite carrying
the real schema.

Five cases carry the design's weight:

1. Queue query fails → `state: "unknown"` and count keys **absent**.
   The regression test for the fail-open bug.
2. A util raises `SystemExit` → error envelope returned, process
   survives.
3. SAM raises → `catalog_unavailable`, not an empty list.
4. `dry_run=True` never reaches a real `submit_map` — asserted on the
   fake runner's argv.
5. The ksu env fixes are actually emitted — asserted against the
   generated script text, pinning the `condor_vault_storer` knowledge to
   a test rather than a paragraph.

Plus `scripts/smoke_test_stdio.py`: spawn the server over stdio, list
tools, call `get_server_info`.

## Deployment

`install.sh` builds the venv, then runs a **two-part check**:

1. MCP dependencies import with the ops `PYTHONPATH` **removed** —
   proving self-containment. This is precisely what the metacat venv
   would fail today, catching the `idna` class of breakage at install
   time rather than at first use.
2. A full check with ops present, proving samweb/mdh/metacat are
   reachable.

`start_mcp.sh --check` runs the same verification on demand.

Registration adds a `prodtools` entry to `.mcp.json` beside
`metacat-readonly`, and `prodtools` to `enabledMcpjsonServers` in
`.claude/settings.json`.

## Accepted risks

**Submission has no server-side gate.** Decided deliberately. Any MCP
client that auto-approves tool calls can submit grid jobs, and the
`.claude/hooks/mu2epro-guard.sh` PreToolUse hook does not help: it
matches `Bash`, so MCP tool calls bypass it entirely. Mitigations that
remain: `dry_run=True` default, stdio-only transport (so the caller is
always a local process running as a user already in `~mu2epro/.k5users`),
and mandatory post-submit verification.

**In-process imports share fate with the server.** Contained by the
`SystemExit` trap, but a future util that segfaults a C extension or
leaks a file descriptor affects the whole process. Accepted in exchange
for the speed and testability the approach buys.

## Open questions

None blocking. Two to settle during implementation:

- Whether `campaign_status` should resolve `campaign` by dsconf
  substring against `tarball`, or require an explicit `campaign_id`.
  Substring matching is friendlier but ambiguous across waves of one
  round.
- Whether to add a `wiki/` page on the server, or fold it into
  `prodtools-prd.md`.
