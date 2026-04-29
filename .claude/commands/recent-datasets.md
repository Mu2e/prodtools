---
description: List recently created datasets with completeness — sources Mu2e env + pyenv ana, defaults to last 1 day with --completeness column
argument-hint: [days] [--query <pattern>] [extra listNewDatasets flags]
allowed-tools: Bash
---

# List recent datasets with completeness

Thin wrapper over `bin/listNewDatasets --completeness` that does all
the env setup so you can ask "what landed recently and is it
complete?" with one command. Encodes:

- `source setupmu2e-art.sh && muse setup ops && pyenv ana`
  (the last is required for SQLAlchemy; without it
  `--completeness` degrades to a warning)
- `python3 bin/listNewDatasets` (not `bash` — the wrapper has a
  Python shebang)
- `--completeness` flag on by default (auto-rebuilds the POMS DB
  if any map is newer than the DB, within the lookback window)
- `--days 1` by default (more useful than the 7-day default for
  "what changed today")
- Output filtered to drop the noisy `Skipping logparser ...`
  rebuild trace lines

## Usage

```
/recent-datasets [days] [--query <pattern>] [extra-args]
```

- `[days]` — optional first positional integer, sets `--days N`.
  Default `1`. Also controls the DB-staleness lookback window.
- `--query <pattern>` — pass-through to `listNewDatasets --query`,
  for SAM where-clauses (e.g. `"dh.dataset like 'mcs.mu2e.PBI%'"`).
  When given, `--days` only governs DB staleness, not the SAM
  filter (the custom query overrides the date filter).
- Anything else — passed through verbatim
  (`--user oksuzian`, `--filetype log`, `--no-rebuild`,
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

# Skip the auto-rebuild even if DB is stale
/recent-datasets 7 --no-rebuild
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
  && pyenv ana > /dev/null 2>&1 \
  && python3 <REPO>/bin/listNewDatasets --completeness --days <DAYS> <EXTRA_ARGS> 2>&1 \
     | grep -v -E '^(Skipping logparser|Loading [0-9]+ JSON files|Loaded [0-9]+ job definitions|Removed [0-9]+ jobs|Computing completion status|Marked [0-9]+ jobs as complete|Discovered and cached [0-9]+ derived datasets|Error listing definition files|Error describing definition|Warning: Could not count files|  Template mode:|^$)'
```

### 4. Report

Print the filtered output to the user. The table that survives the
filter — header, dividers, dataset rows, completeness column — is
what they actually want. The DB staleness/rebuild messages survive
the filter on purpose: the user should know if a slow rebuild
happened.

## Notes

- The filter is heuristic; if a future `listNewDatasets` /
  `db_builder` change introduces new noise lines, add their
  prefixes to the grep. If a real warning gets accidentally
  filtered, drop the matching pattern.
- Read-only by design — no SAM writes, no DB rebuild beyond what
  `listNewDatasets --completeness` already does internally.
- For "what's *not yet* in production", use `pomsMonitor
  --campaign <name> --outputs --incomplete` directly instead;
  this skill is for the "what landed in SAM" angle.
- For per-dataset family trees use `famtree`; for per-dataset
  log metrics use `logparser`. This skill is intentionally narrow.
