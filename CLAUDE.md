# CLAUDE.md

Instructions for Claude Code when working in this repo.

## Prodtools usage

Before answering any question about running the prodtools commands
(`json2jobdef`, `jobfcl`, `fcldump`, `runmu2e`, `jobdef`, `jsonexpander`,
`jobquery`, `mkidxdef`, `pomsMonitor`, `famtree`, `logparser`,
`genFilterEff`, `datasetFileList`, `listNewDatasets`, `mkrecovery`,
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
