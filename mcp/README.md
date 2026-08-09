# prodtools MCP servers

Two servers live under `mcp/`, registered in `.mcp.json` at the repo
root and enabled in `.claude/settings.json`.

## `prodtools` (read-only)

Exposes campaign status and dataset discovery as typed tools:
`campaign_status`, `list_campaigns`, `find_datasets`, `dataset_details`,
`trace_provenance`, `get_server_info`. It performs **NO writes** — it
cannot submit jobs, create or delete SAM definitions, or modify the
submission ledger. That guarantee is why its tools can be called
without deliberation; do not weaken it.

Setup: `bash mcp/scripts/install.sh`.
Health check: `bash mcp/scripts/start_mcp.sh --check`.

## `prodtools-write`

Exposes submission: `push_cnf`, `enqueue_campaign`, `run_submissions`.
Every tool takes a required `run_as`:

- `run_as="self"` needs no privilege and writes only your own scratch,
  datasets and ledger (`/exp/mu2e/data/users/$USER/prodtools/`). No
  confirmation and no prompt.
- `run_as="mu2epro"` registers artifacts in production SAM and submits
  production grid jobs. It is refused in-tool unless `confirm=true`
  (`runner.require_confirmed`), and a `PreToolUse` hook
  (`.claude/hooks/mcp-write-guard.sh`, matcher
  `mcp__prodtools-write__.*`) additionally prompts for confirmation.
  Both gates are independent and deliberate: the hook covers the whole
  tool namespace so a future write tool cannot silently escape it, and
  the in-tool refusal survives a hook left un-armed by a settings
  reload.

`confirm=true` is a **model-facing** gate — the model supplies it to
itself — so the hook is the only *human*-in-the-loop checkpoint on a
`run_as="mu2epro"` call. It is written to fail CLOSED: only a
positively parsed `run_as=="self"` passes silently; `run_as=="mu2epro"`,
a missing `run_as`, malformed hook input, an unrecognised value, or a
failing/missing `jq` binary all produce a prompt.

**A settings-hooks edit is not live in an already-running session.**
Registering a new `PreToolUse` matcher in `.claude/settings.json` (as
this one is) requires a `/hooks` reload — a session started before the
edit will call `prodtools-write` tools with the hook un-armed even
though `CLAUDE.md` documents the gate as present. Run `/hooks` (or
start a fresh session) after any change here before relying on the
prompt.

Health check: `bash mcp/scripts/start_write_mcp.sh --check`.

Both launchers share environment setup via `mcp/scripts/_mcp_env.sh`.

## `submissions status` and `--mine`

The `submissions status` verb (see `utils/submissions.py`) reads the
**production ledger by default** — the same ledger the direct-submission
cron uses — *only when the `MU2E_SUBMISSION_DB` env var is unset*; if
it is set, that path wins over the production default (see
`resolve_db`/`build_parser` in `utils/submissions.py`). Pass `--mine` to
read your own ledger
(`/exp/mu2e/data/users/$USER/prodtools/submissions.db`) instead, e.g.
after a `run_as="self"` campaign run through `prodtools-write`. Plain
`submissions status` will not show a self-run campaign; `submissions
status --mine` will.
