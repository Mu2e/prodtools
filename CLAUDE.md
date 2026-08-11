# CLAUDE.md

Instructions for Claude Code when working in this repo.

## Prodtools usage

Before answering any question about running the prodtools commands
(`json2jobdef`, `jobfcl`, `fcldump`, `runmu2e`, `jobdef`,
`jobquery`, `famtree`, `logparser`,
`genFilterEff`, `datasetFileList`, `listNewDatasets`,
`copy_to_stash`), read `EXAMPLES.md` at the repo
root. It is the authoritative reference for CLI flags, JSON config
shapes, and canonical invocations. Do not guess flags or copy patterns
from memory — consult the current doc.

`EXAMPLES.md` is a derived artifact, regenerated from source by the
`/refresh-examples` slash command. The source of truth for its shape
and tribal knowledge is `docs/EXAMPLES_schema.md`. If `EXAMPLES.md`
needs a structural change, edit the schema and run `/refresh-examples`
— do not hand-edit `EXAMPLES.md`.

If `EXAMPLES.md` looks out of date relative to the code (new flag in
`argparse`, new tool in `bin/` not covered), run `/refresh-examples`
before proceeding.

## Running prodtools commands

- `/mu2e-run` — run as the current user. Use for local testing,
  debugging, dry runs, and any command that does not register outputs
  in production SAM.
- `/mu2epro-run` — run as the `mu2epro` account (via `ksu`) in a
  `/tmp` workdir. Required for production runs — anything with
  `--pushout` or `--prod`, or that registers artifacts in SAM as the
  production account. The skill warns before executing such flags and
  asks for explicit confirmation.

Production campaigns are created in one command:
`json2jobdef --prod --enqueue --slice-size N` builds the cnf, pushes it
to SAM, and registers the campaign. No map file is involved. A wrong
setting on a live campaign is fixed with
`submissions set-entry <ID> <key> <value> [--include-open-rows]` — the
flag is what reaches recoveries.

## MCP server

A read-only MCP server at `mcp/` exposes campaign status and dataset
discovery as typed tools (`campaign_status`, `list_campaigns`,
`find_datasets`, `dataset_details`, `trace_provenance`,
`get_server_info`). Prefer it over shelling the CLI for status questions —
it returns structured JSON and costs less context.

It performs **no writes**. Submission remains `/mu2epro-submit`.

A queue or outputs block with `state: "unknown"` has **no count keys**.
Never read a missing count as zero: the query failed and the campaign
may still be running. Do not start a recovery pass on an `unknown`.

`campaign_status` and `list_campaigns` read PRODUCTION by default. For a
campaign you submitted yourself (`run_as="self"`), pass `mine=true` — it
switches both the ledger and the grid queue to your account. Omitting it
against a personal campaign returns an empty result that looks exactly
like "no campaigns". Every reply names the ledger (`db_path`) and the
queue account (`queue.owner`); check them when a count surprises you.
Another user's ledger is not reachable here — use
`submissions --db <path> status`.

Setup: `bash mcp/scripts/install.sh`. Health check:
`bash mcp/scripts/start_mcp.sh --check`.

A second, write-capable server (`prodtools-write`) exposes submission:
`push_cnf`, `enqueue_campaign`, `run_submissions`. A campaign takes two
calls — `push_cnf(..., slice_size=N)` (builds the cnf, registers it,
creates the campaign, returns `campaign_id`; no map file) then
`run_submissions`. `push_cnf`'s `jobdefs_map` mode writes a map file
and creates no campaign; it is the legacy path. Every tool takes a
required `run_as`:

- `run_as="self"` needs no privilege and writes only your own scratch,
  datasets and ledger (`/exp/mu2e/data/users/$USER/prodtools/`). No
  prompt.
- `run_as="mu2epro"` registers artifacts in production SAM and submits
  production grid jobs. It is refused unless `confirm=true`, AND a
  PreToolUse hook prompts. Both gates are deliberate: a hook can be
  un-armed by a settings reload.

The read-only `prodtools` server still performs NO writes. Keep it that
way — that claim is why its tools are called without deliberation.

## Memory discipline

Save a memory immediately when you learn something non-obvious about
this project (investigation techniques, dataset-naming conventions,
SAM query patterns, campaign facts with dates, workflow gotchas). Use
the per-project memory at `~/.claude/projects/*/memory/` with its four
types — `reference`, `project`, `feedback`, `user` — and update
`MEMORY.md` with a one-line pointer. Do not save things derivable from
the current code.

## Operational wiki

Durable operational/tribal knowledge lives in `wiki/` following
Karpathy's LLM Wiki pattern (local adaptation of `kfchou/wiki-skills`).
Use it for: campaigns (MDC2020xx, Run1Bxx specifics), incidents
(production issues + root cause), decisions (ADR-style with rationale
and alternatives considered), notable runs, and ingested external
sources (meeting notes, docdb PDFs, Slack exports).

Skills: `/wiki-init`, `/wiki-ingest <source>`, `/wiki-query <question>`,
`/wiki-update <page>`, `/wiki-lint`. `wiki/SCHEMA.md` holds the
conventions and category taxonomy; `wiki/raw/` holds immutable source
documents; `wiki/pages/` holds the LLM-maintained pages (flat,
slug-named).

Scope separation:
- **Short facts and behavioral preferences** → `memory/`
  (auto-loaded every session)
- **Command-line usage** → `EXAMPLES.md` (regenerated from code)
- **Durable operational knowledge** → `wiki/` (ingested sources,
  synthesized pages, cross-references)
