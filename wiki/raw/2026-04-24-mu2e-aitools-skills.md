---
title: Mu2e/aitools skills and MCP READMEs (source snapshot)
source_type: github-repo
source_url: https://github.com/Mu2e/aitools
fetched: 2026-04-24
---

# Mu2e/aitools — source snapshot

Snapshot of the upstream AI-tools repo at fetch time. Not refetched
automatically; treat as a point-in-time record. Synthesized page:
[[metacat-reference]].

## Repo shape

```
aitools/
├── ai-instructions.md     # Offline-focused entry point (build system, muse, spack)
├── skills/
│   ├── building-offline-software/SKILL.md
│   ├── building-with-muse/SKILL.md
│   ├── building-with-smack/SKILL.md
│   ├── understanding-data-handling/SKILL.md
│   ├── finding-data-sam/SKILL.md
│   ├── finding-data-metacat/SKILL.md    ← pulled
│   ├── coding-with-metacat/SKILL.md     ← pulled
│   └── coding-with-query-engine/SKILL.md
└── mcp/
    ├── metacat/          ← pulled (README)
    ├── sim-epochs/       ← pulled (README)
    ├── dqm/
    └── code-index/
```

Status at fetch time: 22 commits, 0 stars, 4 forks, Apache 2.0,
Python 70% / Shell 30%.

## `finding-data-metacat/SKILL.md` — what we pulled

- Environment setup (`mu2einit`, `muse setup ops`, Kerberos for auth).
- Full `metacat` CLI command table: `auth`, `dataset`, `file`,
  `namespace`, `category`, `named_query`, `query`, `version`,
  `validate`.
- Full `mdh` command table: `compute-crc`, `print-url`, `query-dcache`,
  `create-metadata`, `declare-file`, `locate-dataset`, `copy-file`,
  `prestage-files`, `verify-dataset`, `delete-files`, `upload-grid`.
- MQL patterns: `files from`, `where`, `order by`, `limit`, `offset`.
- Namespace & file naming (six-field DID convention).
- dCache storage tiers (`tape` / `disk` / `scratch`) and path prefixes.

## `coding-with-metacat/SKILL.md` — what we pulled

- Python client init (`MetaCatClient()`, env-driven vs explicit URL+token).
- Method reference table: read-only (`list_datasets`, `get_dataset`,
  `get_dataset_files`, `get_file`, `query`) vs write
  (`declare_file`, `create_dataset`, `update_dataset`, `move_files`,
  `delete_file`, `retire_file`, etc.).
- MQL in Python: `client.query()` is a lazy generator; force with
  `list(...)`.
- Scalable patterns: filter-then-count (not `with_counts=True` globally),
  pagination via `limit/offset`.
- Concrete snippets: list datasets, dataset statistics, recent files
  by timestamp, metadata completeness validation, URL generation from
  metacat + `mdh print-url`, art-job input list generation.
- `SafeMetaCat` wrapper class (blocks write methods unless
  `ALLOW_WRITES=True`).
- Safety rules (read-only default, confirm before writes, don't hardcode
  server URLs, don't use SQL syntax, force query evaluation).

## `mcp/metacat/README.md` — what we pulled

- Install: `python3 -m venv .venv; pip install -U pip -r requirements.txt -e .`
- Tools: `discover_datasets`, `get_dataset_details`,
  `query_dataset_files`, `get_server_info` (all read-only).
- Claude Code registration via `mcpServers.metacat-readonly.command`
  pointing at `scripts/start_mcp.sh`.
- Auth: env-provided via `MetaCatClient()` — no MCP-layer auth.
- Runs `setupmu2e-art.sh` + `muse setup ops` at startup.
- Group deployment via install script with version tagging.

## `mcp/sim-epochs/README.md` — what we pulled

- Stdio MCP over `data/sim_catalog.json` (overridable via
  `SIM_EPOCHS_FILE`).
- Tools: `get_simulation_epochs()`, `get_datasets_for_epoch(epoch)`.
- Catalog format (preferred): `{"epochs": [{"name": "MDC2025ad",
  "datasets": [...]}]}`. Compact: `{"MDC2025ad": [...], ...}`.
- Install pattern same as metacat. Env controls: `MCP_PYTHON`,
  `MCP_BASH_SETUP`, `MCP_PYTHONPATH_MODE`.
- Shared deployment convention: `/shared/mcp/sim-epochs/releases/0.1.0/`
  + `current` symlink + centralized registry.

## Not pulled

- `finding-data-sam/SKILL.md` — prodtools already runs on SAM; we have
  internal knowledge.
- `coding-with-query-engine/SKILL.md` — DBReader/DbIdList not a
  prodtools concern.
- `building-*/SKILL.md` — not a prodtools concern.
- `mcp/dqm`, `mcp/code-index` — not currently used.
