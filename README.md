# prodtools

Python tooling for Mu2e Monte Carlo production: it builds job
definitions (cnf tarballs), submits them to the grid, tracks each
campaign in a submission ledger, verifies the outputs and recovers the
jobs that failed.

Two ways in — a command line for doing the work, and a read-only MCP
server for asking about it.

## Command line

On a Fermilab node:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
source bin/setup.sh    # optional: puts bin/ on PATH
```

Then, for example:

```bash
# build a cnf tarball (a job definition) from a campaign entry
json2jobdef --json data/Run1B/stage1.json --desc POT_Run1_a --dsconf MDC2025ac

# the fcl one job of that cnf would run
jobfcl --jobdef cnf.mu2e.POT_Run1_a.MDC2025ac.0.tar --index 0

# what is running
submissions status
```

`json2jobdef --prod --enqueue --slice-size N` is how a production
campaign is created: it builds the cnf, pushes it to SAM and registers
the campaign. Campaigns do not advance on their own — each slice,
verification and recovery is a manual `submissions run` as `mu2epro`.

**[EXAMPLES.md](EXAMPLES.md) is the reference for every command**: real
invocations, flags, and the JSON config shapes. Start there.

## Ask instead of run

`mcp/` holds a read-only MCP server that answers campaign and dataset
questions in plain language from any MCP client — "how is MDC2025au
doing?", "what came out of it?", "where are the files?". It performs no
writes. Connect to a shared instance with one line, or run your own:

**[mcp/README.md](mcp/README.md)** has the quick start.
**[mcp/SUBMIT.md](mcp/SUBMIT.md)** is how to submit your own grid jobs
through the second, write-capable server: a small CeEndpoint sample on
the newest MDC2025 release, fifteen minutes, nothing to edit.

## Where things are

| Path | What it holds |
| --- | --- |
| `bin/`, `utils/` | the commands and the code behind them |
| `data/<family>/` | campaign entries — the physics input (`mdc2025/`, `Run1B/`, `g4bl/`) |
| `templates/<family>/` | curated per-stage settings shared across campaigns (`MDC2025/`, `Run1B/`) |
| `mcp/` | the read-only and write MCP servers |
| `EXAMPLES.md` | CLI reference, regenerated from the code |
| `CONTEXT.md` | glossary: tier, hop, stage, campaign, entry |
| `docs/adr/` | decisions and why they were made |
| `wiki/` | operational knowledge: campaigns, incidents, runbooks |
| `test/` | unit suite: `cd test && python3 -m unittest test_unit` (two MCP cases skip without `mcp/.venv`) |

Repository: <https://github.com/Mu2e/prodtools>
