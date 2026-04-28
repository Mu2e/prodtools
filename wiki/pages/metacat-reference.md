---
title: Metacat reference for prodtools
tags: [reference, metacat, data-handling, mcp, commissioned]
sources: [2026-04-24-mu2e-aitools-skills]
updated: 2026-04-24
---

# Metacat reference for prodtools

Pulled from [`Mu2e/aitools`](https://github.com/Mu2e/aitools) skills
`finding-data-metacat` + `coding-with-metacat` and the MCP-server
READMEs. Kept tight to prodtools use cases — catalog lookups we today
do via `samweb`, plus programmatic access for `bin/`-side scripts.

## When to use metacat

- SAM is winding down; metacat is the successor catalog. New workflows
  should prefer metacat.
- For existing prodtools paths that already call `samweb` (pushout,
  runmu2e, listNewDatasets, etc.), don't force-switch until there's a
  concrete benefit.
- `/mu2epro-run` + `pushOutput` still declares to SAM today. The
  transition plan isn't something prodtools controls.

## samweb → metacat CLI translation

Quick bridge for the commands we use most.

| samweb | metacat |
|---|---|
| `samweb list-definitions --defname '<pat>'` | `metacat dataset list '<pat>'` |
| `samweb count-files 'dh.dataset=<ds>'` | `metacat query "files from <ns>:<ds>" \| wc -l` |
| `samweb describe-definition <def>` | `metacat dataset show <ns>:<ds>` |
| `samweb list-files 'file_name in (...)'` | `metacat query "files from <ns>:<ds> where name in (...)"` |
| `samweb locate-file <f>` | `mdh print-url -l tape -s xrootd <did>` |

Datasets/files use **DIDs**: `<namespace>:<name>` (e.g.
`mu2e:dts.mu2e.PBINormal_33344.MDC2025aj.art`).

## Environment setup

```bash
mu2einit                              # or source setupmu2e-art.sh
muse setup ops
getToken                              # OAuth token (~2h), if needed
metacat auth login -m token $USER     # session auth
metacat auth whoami                   # verify
```

Most prodtools shells already have `metacat` on PATH after
`muse setup ops`.

## MQL query patterns

Metacat Query Language. **Not SQL** — the keyword is `files from`, not
`select`. `query()` in Python returns a lazy generator; wrap with
`list(...)` to force evaluation.

```bash
# Basic: all files in a dataset
metacat query "files from mu2e:dts.mu2e.PBINormal_33344.MDC2025aj.art"

# Subset: by subrun/run
metacat query "files from $DS where rs.first_subrun > 100"
metacat query "files from $DS where run = 1430"

# Filter by tier + size
metacat query "files where dh.tier=sim and file_size > 1000000000"

# Multi-dataset union
metacat query "files from mu2e:dataset1.art, mu2e:dataset2.art"

# From a file
metacat query -q my_query.mql
```

Clauses supported: `files from <did>`, `where <cond>`, `order by
<field>`, `limit <n>`, `offset <n>`.

## Python API (for prodtools bin scripts)

```python
from metacat.webapi import MetaCatClient
client = MetaCatClient()                        # env-driven server/token
```

**Core read-only methods** (no auth required for reads):

| Method | Use |
|---|---|
| `list_datasets(namespace_pattern=, with_counts=False)` | enumerate datasets |
| `get_dataset(did=, exact_file_count=False)` | dataset metadata + file_count |
| `get_dataset_files(did=, with_metadata=False)` | files in dataset |
| `get_file(did=, with_metadata=True)` | single file; returns `None` if missing (does not always raise) |
| `query(mql_string)` | MQL query; **lazy generator** — wrap with `list(...)` |

**Scalable pattern — filter first, then fetch counts:**

```python
# DON'T: with_counts=True across all datasets (slow)
# DO:
datasets = client.list_datasets(namespace_pattern="mu2e", with_counts=False)
selected = [d for d in datasets if d["name"].startswith("dts.mu2e.PBI")]
for d in selected:
    did = f"{d['namespace']}:{d['name']}"
    info = client.get_dataset(did=did, exact_file_count=False)
    print(did, info.get("file_count", 0))
```

**Pagination for large result sets:**

```python
limit, offset, out = 100, 0, []
while True:
    batch = list(client.query(f"files from {did} limit {limit} offset {offset}"))
    if not batch: break
    out.extend(batch); offset += limit
```

**Generate xroot URLs from a metacat query** (replaces samweb+locate
in `bin/` scripts):

```python
import subprocess
for f in client.query(f"files from {did} where run = {run}"):
    fdid = f"{f['namespace']}:{f['name']}"
    url = subprocess.run(["mdh", "print-url", "-l", "tape", "-s", "root", fdid],
                         capture_output=True, text=True).stdout.strip()
    print(url)
```

## Safety: read-only default

Upstream skill convention for AI-generated code: **default to read-only
methods**; require explicit user approval before calling any of
`declare_*`, `create_*`, `update_*`, `add_*`, `remove_*`, `delete_*`,
`retire_*`. prodtools inherits this — we don't write to the catalog
from bin/ today. The `pushOutput` path that registers outputs is
handled by `OfflineOps`, not by metacat Python API.

Upstream ships a `SafeMetaCat` wrapper class (in
`coding-with-metacat/SKILL.md`) that intercepts write methods and
raises unless `ALLOW_WRITES=True`. Worth adopting if we start
generating any declare/update code.

## Namespace & file naming (recap)

```
<namespace>:<tier>.<owner>.<description>.<config>.<sequencer>.<format>   # file DID
<namespace>:<tier>.<owner>.<description>.<config>.<format>               # dataset DID (no sequencer)
```

Matches the Mu2e 6-field convention we already use: `dts.mu2e.<desc>.<dsconf>.<seq>.art`.

## MCP servers available from Mu2e/aitools

`Mu2e/aitools/mcp/` ships three MCP servers. Relevant to prodtools:

### `metacat` (read-only) — commissioned 2026-04-24

Four tools: `discover_datasets`, `get_dataset_details`,
`query_dataset_files`, `get_server_info`.

**Status:** installed and running in the prodtools project. Venv at
`/exp/mu2e/app/users/oksuzian/muse_050125/aitools/mcp/metacat/.venv`
(Python 3.10.14 from Mu2e ops env — system Python 3.9 is too old for
the `mcp>=1.2.0` package, so build the venv after
`source setupmu2e-art.sh && muse setup ops`, not from bare system
python3). `scripts/start_mcp.sh` sources the Mu2e env internally, so
the MCP launcher needs no shell setup at call time.

Project-level registration (already in place):

- `.mcp.json` at repo root — defines `mcpServers.metacat-readonly` with
  absolute command path to `scripts/start_mcp.sh`.
- `.claude/settings.json` — adds `enabledMcpjsonServers:
  ["metacat-readonly"]` so the server auto-approves on session start.

**Install recipe** (for a fresh machine):

```bash
cd <muse-work-root>
git clone https://github.com/Mu2e/aitools.git
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
cd aitools/mcp/metacat
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -U pip -r requirements.txt -e .
bash scripts/start_mcp.sh --check   # smoke test
```

Auth: uses env-provided metacat config (`MetaCatClient()`), no
MCP-layer auth handling. Needs `getToken` / `metacat auth login` to
have been run.

**Verified in production (2026-04-24):** all 4 tools respond correctly
against live SAM/metacat data. Used to confirm the PBI mix pipeline
(see [[pbi-sequence-workflow]] → "POMS grid run completed").

### Schema quirks for the MCP tools

- `query_dataset_files` `sort_by` is limited to a fixed field set:
  `['created_timestamp', 'n_events', 'name', 'run', 'size',
  'subrun']`. Passing full metadata keys like `rs.first_subrun` is
  rejected with `sort_by must be one of: [...]`. The short names are
  conveniently mapped to the underlying metadata paths — no way to
  sort by arbitrary metadata keys in the current MCP API.
- `discover_datasets` `name_pattern` uses shell-style globs (`*`, `?`)
  — same as the samweb `--defname` flag, not MQL `like` syntax.
- `include_metadata=true` on `discover_datasets` returns empty
  `metadata: {}` for datasets; per-file metadata is accessed via
  `get_dataset_details` (sample file) or
  `query_dataset_files(include_metadata=true)`.

### `sim-epochs` (JSON catalog over MDC2025xx epochs)

Two tools: `get_simulation_epochs()`, `get_datasets_for_epoch(epoch)`.
File-backed (`data/sim_catalog.json`, overridable via
`SIM_EPOCHS_FILE`). Potentially useful for MDC2025aj/ai/ag epoch
queries without a round-trip to SAM/metacat. Install recipe: same
pattern as metacat, under `aitools/mcp/sim-epochs/`.

### `dqm` (not currently used by prodtools)

DQM Query Engine read-only MCP. Skipped.

## Gaps worth filling upstream

`Mu2e/aitools` currently has no prodtools/POMS/jobdef skills. The
content in `wiki/pages/pbi-sequence-workflow.md` (Stage 1+2),
`input-data-*` shapes, and our POMS push flow would fit naturally as
upstream skills. Low-effort PR; they have 4 contributors.

## Related

- [[pbi-sequence-workflow]] — production chain this reference supports
- [[input-data-chunk-mode]], [[input-data-dir-shape]] — input_data shapes
- Source: [[2026-04-24-mu2e-aitools-skills]]
