---
description: List recently created datasets with completeness — sources Mu2e env, defaults to last 1 day with --completeness column
argument-hint: [days] [--query <pattern>] [extra listNewDatasets flags]
allowed-tools: Bash
---

# List recent datasets with completeness

Thin wrapper over `bin/listNewDatasets --completeness` that does all
the env setup so you can ask "what landed recently and is it
complete?" with one command. Encodes:

- `source setupmu2e-art.sh && muse setup ops`
- `python3 bin/listNewDatasets` (not `bash` — the wrapper has a
  Python shebang)
- `--completeness` flag on by default (expected counts come from
  the submission ledger, `submissions.db`, via `--ledger-db`; no
  rebuild step and no extra env setup beyond the Mu2e env sourced
  above)
- `--days 1` by default (more useful than the 7-day default for
  "what changed today")
- Output filtered to drop blank lines; kept as a heuristic hook for
  future noisy trace lines, though the ledger-backed path doesn't
  currently produce any

## Usage

```
/recent-datasets [days] [--query <pattern>] [extra-args]
```

- `[days]` — optional first positional integer, sets `--days N`.
  Default `1`.
- `--query <pattern>` — pass-through to `listNewDatasets --query`,
  for SAM where-clauses (e.g. `"dh.dataset like 'mcs.mu2e.PBI%'"`).
  When given, `--days` has no effect — the custom query overrides
  the date filter entirely.
- Anything else — passed through verbatim
  (`--user oksuzian`, `--filetype log`, `--ledger-db <path>`,
  `--size`, etc.).

## Examples

```
# What landed in the last day, with completeness
/recent-datasets

# Last 7 days
/recent-datasets 7

# All recent PBI mcs files (regardless of date — query overrides date)
/recent-datasets --query "dh.dataset like 'mcs.mu2e.PBI%Mix1BB.MDC2025ai_best_v1_3.art'"

# Last day, your own datasets, with file sizes
/recent-datasets 1 --user oksuzian --size

# Point at a non-default submission ledger
/recent-datasets 7 --ledger-db /path/to/other/submissions.db
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps.

### 1. Parse args

- If the first whitespace-separated token is a positive integer,
  treat it as `DAYS` and drop it from the argv. Otherwise
  `DAYS=1`.
- Everything else is `EXTRA_ARGS` (passed through).

### 2. Resolve repo root

Set `REPO=$PWD` at invocation time. The wrapper at
`$REPO/bin/listNewDatasets` is the entry point.

### 3. Run

Execute as a single Bash command so the sourced env is live for
the listNewDatasets call:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \
  && muse setup ops > /dev/null 2>&1 \
  && python3 <REPO>/bin/listNewDatasets --completeness --days <DAYS> <EXTRA_ARGS> 2>&1 \
     | grep -v -E '^$'
```

### 4. Report

Print the filtered output to the user. The table that survives the
filter — header, dividers, dataset rows, completeness column — is
what they actually want. Any `WARNING:` lines (an unresolvable
ledger entry, or the ledger itself being unreadable) survive the
filter on purpose: the user should know if the completeness column
is degraded.

## Notes

- The filter is heuristic; if a future `listNewDatasets` change
  introduces new noise lines, add their prefixes to the grep. If a
  real warning gets accidentally filtered, drop the matching
  pattern.
- Read-only by design — no SAM writes, no ledger writes.
  `--completeness` reads `submissions.db` directly; there is no DB
  rebuild step of any kind.
- For "what's *not yet* in production", use the prodtools MCP
  `campaign_status(campaign="<name>")` (or `submissions status` for
  the raw ledger) instead; this skill is for the "what landed in
  SAM" angle.
- For per-dataset family trees use `famtree`; for per-dataset
  log metrics use `logparser`. This skill is intentionally narrow.
