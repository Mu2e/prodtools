# prodtools MCP server — read-only core

**Date:** 2026-07-26
**Status:** approved for planning
**Scope:** read-only tools only. Job submission is deferred to a
follow-on spec — see "Deferred: submission".

## Goal

Expose prodtools campaign status and dataset discovery/provenance as an
MCP stdio server, so any MCP client — not just this repo's Claude Code
session with its `.claude/` skills — can inspect production state
through typed tools returning structured JSON.

## Motivation

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

- **No HTTP transport.** stdio only. An HTTP service would run as
  whoever launched it, giving every client one shared identity and
  requiring an authn/authz layer that does not exist today. stdio keeps
  authorization exactly where it already is.
- **No writes of any kind in this phase.** No submission, no SAM
  definition creation, no ledger mutation. Every tool here is safe to
  run as the calling user; none needs mu2epro.
- **Not a replacement for the skills.** `/mu2epro-run` and
  `/mu2epro-submit` remain the path when a Musing release must be
  selected per invocation, and remain the only submission path until the
  follow-on spec lands.

## Prior art and environment constraints

`metacat-readonly` (in the sibling `aitools/` repo, wired into this
repo's `.mcp.json`) proves the pattern in this exact environment:
FastMCP stdio server, 481 LOC, 4 tools, read-only, started by a script
that sources the Mu2e environment first.

Constraints discovered while surveying it:

**Musing releases cannot be held in-process.** `muse setup` aborts if
`MUSE_WORK_DIR` is already set (`museSetup.sh:163-168`), and the
required release varies per call (`SimJob/MDC2025au` for mixing,
`AnalysisMDC2025/v02_00_00` for ntuples). Anything needing a Musing must
fork a subprocess, which is why `json2jobdef`, `jobfcl`, `fcldump`, and
`runmu2e` are out of scope for in-process tools.

**The metacat venv is not self-contained.** `.venv/bin/python -c
"import mcp"` fails with `ModuleNotFoundError: No module named 'idna'`.
It works only because `start_mcp.sh` composes
`PYTHONPATH=$VENV_SITE:$MU2E_OPS_PYTHONPATH`, letting `muse setup ops`
silently supply the missing transitive dependency. The server is one
ops-env bump from breaking, and the failure would present as a traceback
inside `httpx`.

**The venv's interpreter is also pinned to the ops spack view.**
metacat's `.venv/bin/python3` symlinks into
`/cvmfs/.../ops-019/.spack-env/view/bin/python3`, and system
`/usr/bin/python3` is 3.9 — too old for `mcp`. Installing dependencies
completely fixes the site-packages half of this; the interpreter binding
remains. An ops-env retirement therefore changes the failure mode from
an `httpx` traceback to a failed exec. `install.sh` records which spack
env it bound to, and `--check` verifies the interpreter still resolves.

Command tiers by environment need:

| Tier | Requires | Commands |
|---|---|---|
| Pure Python | nothing | `jobquery` |
| `muse setup ops` | samweb/mdh/metacat | `latestDatasets`, `listNewDatasets`, `famtree`, `logparser`, `datasetFileList`, `check_inputs`, `submissions` |
| `pyenv ana` | SQLAlchemy | `pomsMonitor`, `listNewDatasets --completeness` |
| A Musing release | `muse setup SimJob/<tag>` | `json2jobdef`, `jobfcl`, `fcldump`, `runmu2e` |

Tools here draw only from the first two tiers.

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
    adapters.py               # error envelope, SystemExit trap, stdout guard
    ledger_ro.py              # read-only ledger access (no DDL)
    tools/status.py           # campaign_status, list_campaigns
    tools/discovery.py        # find_datasets, dataset_details, trace_provenance
    tools/lineage.py          # depth-bounded traversal for trace_provenance
```

**The load-bearing boundary:** tool functions are plain Python taking
plain arguments and returning plain dicts. FastMCP decorators appear
only in `server.py`. Every tool is therefore testable in the existing
`test/test_unit.py` suite with no MCP machinery and no stdio transport —
matching how `submissions.py` already accepts `runner=subprocess.run`
and `sam_lister=files_in_dataset`.

**Execution strategy:** in-process imports throughout. No subprocess in
this phase. The `utils/` modules are already factored for it —
`submission_ledger.all_campaigns()`/`all_rows()` return dicts,
`submissions.live_clusters()`/`cluster_queue_state()` take injectable
runners, `famtree.get_parents()` and
`latestDatasets.latest_per_description()` are plain functions.

**Imports are lazy, inside tool functions** — the pattern
`utils/check_inputs.py:21` already establishes, so `server.py` imports
cleanly without the Mu2e environment and the tools stay unit-testable.

**`start_mcp.sh` must add the repo root to `PYTHONPATH`** alongside
venv-site and the ops path, since `prodtools_mcp` imports `utils.*`.
metacat's script has no equivalent line to copy. Note also that
venv-first ordering means any venv-installed package shadows the ops
copy for in-process imports such as `samweb_client`; the venv must not
install anything the ops env also provides.

### `adapters.py` — three jobs, not one

**1. Trap `SystemExit`.** It derives from `BaseException`, so
`except Exception` does not catch it. Uncaught, it terminates the server
mid-session rather than failing one call. Reachable examples on these
tools' paths: `submissions.resolve_cap` (`submissions.py:59`) and
`_acquire_lock` (`submissions.py:648`). (`jobquery.py`'s six
`sys.exit(1)` calls are all inside `main()` argument validation and are
*not* reachable — the tools use `Mu2eJobPars` directly — and `runmu2e`
is excluded from in-process use entirely. The trap is still required;
those two are simply the honest examples.)

**2. Guard stdout.** In a stdio server, **stdout is the JSON-RPC
channel.** Any `print()` inside a util injects plain text mid-protocol
and corrupts the stream. This is not hypothetical:
`famtree.get_first_file_from_dataset` prints `"No files found for
dataset: …"` to stdout on the not-found path (`utils/famtree.py:71`),
directly on `trace_provenance`'s composition path. Every tool call is
therefore wrapped in `contextlib.redirect_stdout(sys.stderr)`. metacat
survives without this only because its own server code never prints and
its start script sends setup output to stderr
(`start_mcp.sh:16,26`) — it is luck, not design.

**3. Build the error envelope.** See "Error contract".

### `ledger_ro.py` — read-only ledger access

`submission_ledger._connect` executes DDL on **every** connect
(`submission_ledger.py:73-84`): `_SCHEMA`, `_CAMPAIGN_SCHEMA`, and a
`CREATE UNIQUE INDEX IF NOT EXISTS`. Reading works today as a
non-mu2epro user only because every object already exists; creating a
missing one raises `OperationalError: attempt to write a readonly
database`. Any future schema addition shipped in code before mu2epro's
writer has run it would break every `campaign_status` call, and a hot
journal from a crashed writer cannot be rolled back by a reader.

`ledger_ro.py` therefore opens the DB via the sqlite URI form with
`mode=ro` and issues no DDL, reusing `submission_ledger`'s row-to-dict
shaping. An `OperationalError` surfaces as `catalog_unavailable` with
the DB path in the message, not as a traceback.

**Lock posture:** `submissions.lock` (`submissions.py:637-648`)
serializes mutating cron passes. Read-only tools do not take it —
consistent with current manual status-check practice, and stated here so
it is a decision rather than an oversight. Readers may therefore observe
a campaign mid-advance; `cursor` is a snapshot, not a transaction
boundary.

## Tool surface

Six tools.

### `campaign_status(campaign=None, campaign_id=None, include_queue=True, include_outputs=True)`

Composes `ledger_ro` campaign/row reads,
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
         "expected": 4000, "produced": 412}
      ]}
    }
  ]
}
```

Field derivations, all of which need stating because none is a stored
column:

- **No integer `entry` field.** The ledger stores the whole entry dict
  as `entry_json` (`submission_ledger.py:57-69`); an index into the map
  is not recoverable from it. Callers get `tarball` and `njobs` instead.
- **`njobs`** is `poms_entry.njobs_of(entry)` (`poms_entry.py:69`).
- **`outputs[].expected`** is `njobs` — one output file per job per
  output stream. Derived this way deliberately, to avoid a `/pnfs` cnf
  read on every status call.
- **`rows` correlates to a campaign by `tarball` only** — there is no
  foreign key. A tarball reused across an older completed campaign will
  conflate rows; the count carries a `note` when more than one campaign
  shares a tarball.

When a sub-query fails, its block becomes
`{"state": "unknown", "reason": "..."}` **with the count keys absent
entirely** — see "Error contract".

### `list_campaigns(state=None)`

Thin ledger listing, no network. Orientation call: "what is running at
all?" `state` filters over `CAMPAIGN_STATES`
(`active`/`complete`/`paused`/`cancelled`,
`submission_ledger.py:28`).

### `find_datasets(campaign=None, tier=None, desc=None, pattern=None, latest_only=False, require_files=False)`

Wraps `latestDatasets.fetch_definitions` and `latest_per_description`.

```json
{"count": 19, "truncated": false, "datasets": [
  {"name": "dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art",
   "tier": "dig", "owner": "mu2e", "desc": "FlatGamma",
   "dsconf": "MDC2025au_best_v1_3", "file_format": "art"}
]}
```

**This is a definition listing, not an existence check.**
`fetch_definitions` is `samweb list-definitions`
(`latestDatasets.py:47-48`): zero-file definitions appear, and
`-LH`/`-CH` variants do not. `require_files=True` filters to
definitions with at least one file, the way `_filter_complete`
(`latestDatasets.py:88`) already does; the response always carries the
caveat in a `basis` field so a caller cannot mistake the listing for
existence.

### `dataset_details(dataset)`

```json
{"dataset": "...", "exists": true, "file_count": 800,
 "event_count": 4000000, "total_size_bytes": 4294967296,
 "created_utc": null}
```

`file_count`, `event_count`, and `total_size_bytes` come from
`samweb_wrapper.dataset_summary` (`samweb_wrapper.py:405`). `exists` is
determined by file count, not definition listing.

**`created_utc` is nullable and comes from a different call** —
`definition_creation_date` (`samweb_wrapper.py:386`), which returns
`None` for exactly the metadata-only `-LH`/`-CH` datasets this section
warns about. `dataset_summary` does not carry a creation time.

### `trace_provenance(name, direction="up", depth=3)`

```json
{"root": "...", "direction": "up", "depth": 3, "truncated": false,
 "nodes": ["..."], "edges": [{"child": "...", "parent": "..."}]}
```

**This is new traversal code, not a wrapper.**
`famtree.topology_for_dataset` (`famtree.py:118`) has no depth limit, no
truncation signal, and walks parents only — its recursion is a closure
that cannot be parameterized. Nothing in `famtree` walks children;
`samweb_wrapper.children_of_file` (`samweb_wrapper.py:417`) is per-file.
`lineage.py` implements a depth-bounded breadth-first walk reusing
`famtree.get_parents` and `samweb_wrapper.children_of_file` as the edge
functions, sets `truncated` when `depth` cuts the walk short, and
returns edges and nodes as data — **not** the mermaid string, which is a
presentation format the caller can build itself.

`famtree.get_parents` is decorated `lru_cache(maxsize=None)`
(`famtree.py:46`). Lineage is immutable so the cached values stay
correct, but an unbounded cache in a long-lived server grows without
limit; `lineage.py` uses its own bounded cache and does not rely on it.

### `get_server_info()`

Static capabilities and safe-usage guidance, as metacat's does.
Explicitly advertises that this server performs no writes.

### Explicitly not exposed

`samweb_wrapper` exports `create_definition` (`:357`) and
`delete_definition` (`:361`). In-process these are one import away.
**The tool layer never calls them.** Stated here so the constraint
survives future edits.

## Error contract

Every tool returns its success dict or:

```json
{"error": {"kind": "...", "message": "...", "remedy": "..."}}
```

`kind` from a closed set: `env_missing`, `auth_expired`,
`catalog_unavailable`, `not_found`, `invalid_argument`, `internal`.

**1. `SystemExit` is caught explicitly** — see `adapters.py` above.

**2. "Unknown" must never render as "zero".** `queue_state` has a known
fail-open bug where proc-form `jobsub_q` reports 0 total while jobs run,
and `jobsub_q -af` is unreliable enough that `submissions.py` parses the
default table fail-closed. A failed queue query serialized as
`{"running": 0}` would lead a caller to conclude the campaign drained
and start a recovery pass against live jobs. An unknown block therefore
carries `state: "unknown"` and **omits the count keys entirely**, so
there is no zero to misread. Tools reuse `submissions.live_clusters()`
and `cluster_queue_state()` rather than reimplementing the parse.

**3. Fail loudly; never substitute empty.** A SAM outage returns
`catalog_unavailable`, not an empty dataset list. An empty list is a
finding; manufacturing one from an error is how a campaign gets declared
complete that is not.

**4. Never remediate credentials, and never touch mu2epro's.** The
server does not invoke `htgettoken`, `kinit`, or any refresh; auth
failures return `auth_expired` with "renew in your own shell" as the
remedy. Note this is a rule about *remediation*, not about all token
activity: `jobsub_q` performs implicit token acquisition of its own,
whose "Attempting to …"/"Storing bearer token" noise `submissions.py:77`
already skips as a matter of course.

## Testing

Tool functions are plain Python, so tests live in the existing
`test/test_unit.py` (540 tests) with no MCP machinery. Dependencies
inject as they already do elsewhere: `runner=` for subprocess,
`sam_lister=` for catalog, `db_path=` pointed at a tmp sqlite carrying
the real schema.

Five cases carry the design's weight:

1. **Queue query fails → `state: "unknown"` and count keys absent.**
   Regression test for the fail-open bug.
2. **A util raises `SystemExit` → error envelope returned, process
   survives.**
3. **SAM raises → `catalog_unavailable`, not an empty list.**
4. **A tool whose composition path prints to stdout leaves stdout
   clean.** Drive `trace_provenance` through the `famtree.py:71`
   not-found path with stdout captured; assert nothing but JSON-RPC
   reached stdout and the text landed on stderr.
5. **Ledger opens read-only.** Assert no DDL is issued, and that a DB
   missing an expected index surfaces `catalog_unavailable` rather than
   an `OperationalError` traceback.

Plus `scripts/smoke_test_stdio.py`: spawn the server over stdio, list
tools, call `get_server_info`.

## Deployment

`install.sh` builds the venv, then runs a **two-part check**:

1. MCP dependencies import with the ops `PYTHONPATH` **removed** —
   proving self-containment. This is precisely what the metacat venv
   would fail today, catching the `idna` class of breakage at install
   time rather than at first use.
2. A full check with ops present, proving samweb/mdh/metacat are
   reachable and the pinned interpreter still resolves.

`start_mcp.sh --check` runs the same verification on demand.

Registration adds a `prodtools` entry to `.mcp.json` beside
`metacat-readonly`, and `prodtools` to `enabledMcpjsonServers` in
`.claude/settings.json`.

## Deferred: submission

`submit_campaign` was designed alongside these tools and is **removed
from this spec**, to be designed properly in a follow-on. Review found
five issues that each change its shape, and shipping it here would
couple a low-risk read-only server to the one tool that can cause
irreversible harm. The follow-on must resolve:

- **No input pre-flight on the direct path.** `check_inputs` runs only
  in `_enqueue_entries` (`submit.py:241`), reached only via `--enqueue`
  (`submit.py:601`). A direct windowed submit never calls it, so
  submissions would launch with unverified inputs — the exact bulk-death
  failure `check_inputs` exists to gate. Options: call
  `utils.check_inputs.check_inputs()` in-process before submitting, or
  add an explicit enqueue mode.
- **No idempotency under client timeouts.** Large submissions can exceed
  `timeout 590` during RCDS publish; MCP clients time out sooner. The
  ksu child survives, the result is lost, and a retry re-submits the
  same window. Because payloads are deterministic that is duplicate
  physics, and the only overlap guard,
  `_slice_overlaps_ledger` (`submissions.py:291`), runs solely in cron's
  `top_up` (`submissions.py:384`) — never on direct `--first/--num`.
- **`entry=None` fans out over every entry** (`submit.py:583`), so the
  scalar return shape is wrong and the default is hazardous for a typed
  write tool with no confirm step.
- **The ksu block must be the full working one** from
  `.claude/commands/mu2epro-submit.md:121-133`: mktemp *inside* ksu (or
  the workdir is caller-owned and `condor_vault_storer` fails), `cd
  "$WORKDIR"`, and `setupmu2e-art.sh` + `muse setup ops` sourced (or
  `jobsub_submit` is not on PATH).
- **Cluster ID should come from the ledger, not stdout.** `submit_map`
  already records it (`submit.py:134-162`); scraping human output
  through ksu reintroduces exactly the parsing this project is meant to
  eliminate. The follow-on must also define the partial failure the code
  already warns about — submission succeeded, ledger write failed — so a
  live cluster is never reported as a failure.

Until that spec lands, `/mu2epro-submit` remains the submission path.

## Open questions

None blocking. Two to settle during implementation:

- Whether `campaign_status` resolves `campaign` by dsconf substring
  against `tarball`, or requires an explicit `campaign_id`. Substring
  matching is friendlier but ambiguous across waves of one round.
- Whether the server gets its own `wiki/` page or folds into
  `prodtools-prd.md`.
