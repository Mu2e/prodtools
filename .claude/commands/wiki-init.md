---
description: Bootstrap or reinitialize the prodtools operational wiki
---

# Wiki Init

Bootstrap the prodtools operational wiki. The wiki lives at
`<repo-root>/wiki/` and holds durable operational knowledge (campaigns,
incidents, decisions, runs).

## Pre-flight

Check if `wiki/SCHEMA.md` already exists. If yes, ask the user whether
to reinitialize (backing up the current wiki first) or stop.

## Process

### 1. Confirm scope

Default categories for prodtools:
`Campaigns | Incidents | Decisions | Runs | Sources | Analyses`

If the user wants different categories, ask them to name the set.

### 2. Create the directory layout

```
<repo-root>/wiki/
├── SCHEMA.md         ← conventions + wiki identity
├── raw/              ← immutable source documents (drop files here)
├── pages/            ← LLM-maintained wiki pages, flat, slug-named
├── index.md          ← content catalog (one-line per page, by category)
├── log.md            ← append-only operation log
└── overview.md       ← evolving synthesis
```

**Critical:** `wiki/pages/` is flat. All pages live there as
`<slug>.md`. No subdirectories. Slugs are lowercase, hyphen-separated.

### 3. Write `wiki/SCHEMA.md`

Include: absolute wiki path, domain description, source types,
frontmatter template, `[[slug]]` cross-reference convention, log entry
format, category taxonomy, and the relationship to other stores
(`memory/` for short facts and preferences; `EXAMPLES.md` for
command-line usage; wiki for durable operational/tribal knowledge).

### 4. Seed `index.md`, `log.md`, `overview.md`

- `index.md`: section headers for each category, empty bullet lists.
- `log.md`: header, format note, one entry
  `## [<today>] init | <domain>`.
- `overview.md`: frontmatter + a "no sources yet" placeholder.

### 5. Report

Tell the user:
- Wiki initialized at `wiki/`
- Drop sources into `wiki/raw/`, then run `/wiki-ingest <file>`
- Run `/wiki-lint` periodically
- `SCHEMA.md` is how the other wiki skills find this wiki — don't move
  or delete it
