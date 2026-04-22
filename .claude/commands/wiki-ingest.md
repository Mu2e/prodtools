---
description: Ingest a source into the prodtools operational wiki
argument-hint: <file|URL|pasted text>
---

# Wiki Ingest

Add a source to the wiki. Read it, discuss with the user, write a
summary page, update entity/concept pages, maintain index, overview,
and log.

## Pre-condition

Read `wiki/SCHEMA.md`. If missing, tell the user to run
`/wiki-init` first. Get wiki paths, frontmatter format,
cross-reference convention, category taxonomy from the schema.

## Process

### 1. Accept the source

The source can be:
- **File path** — read it directly; copy to `wiki/raw/<filename>` if
  it's not already inside `wiki/raw/`.
- **URL** — fetch it via WebFetch; save the rendered text to
  `wiki/raw/<slug>.md`.
- **Pasted text** — use what was provided; save to
  `wiki/raw/<slug>.md` for provenance.

### 2. Read the source in full

Read every page/section. Do not skip. For long sources, chunk and
read sequentially.

### 3. Surface takeaways — BEFORE writing anything

Tell the user:
- 3–5 bullet takeaways
- What campaigns, incidents, decisions, or runs this touches or
  introduces
- Whether it contradicts anything already in the wiki (read
  `wiki/index.md` and relevant pages to check)

Ask: **"Anything specific you want me to emphasize or de-emphasize?"**

Wait for the user's response before proceeding.

### 4. Generate slug

Lowercase, hyphens, no special characters. Include a date or campaign
tag where it helps disambiguate:
- `2026-04-15-poms-stall-incident`
- `run1bai-decision-keep-n-generations`

### 5. Write the source summary page

`wiki/pages/<slug>.md`:

```markdown
---
title: <source title>
tags: [<category-tag>, ...]
sources: [<slug>]
updated: <today>
---

# <Source Title>

**Source:** <original path or URL>
**Date ingested:** <today>
**Type:** <meeting-notes | docdb | slack | email | incident | decision | run | other>

## Summary

<2–3 paragraph synthesis in your own words>

## Key Takeaways

- <bullet>

## Entities Touched

<list of campaigns/incidents/decisions as [[slug]] links>

## Relation to Other Wiki Pages

<how this connects to or updates existing knowledge>
```

### 6. Update entity / concept pages

For each campaign, incident, decision, or run touched:

- **Page exists:** read, update relevant section, add this source to
  frontmatter `sources`, update `updated` date.
- **Page doesn't exist:** create it.

```markdown
---
title: <Name>
tags: [campaign | incident | decision | run]
sources: [<source-slug>]
updated: <today>
---

# <Name>

## Description

<synthesis across all sources that discuss this>

## Appearances in Sources

- [[source-slug]] — <one-line note>

## Related

- [[related-slug]] — <relationship>
```

### 7. Backlink audit — do not skip

Grep ALL existing pages in `wiki/pages/` for mentions of the new
page's entities/concepts. Add `[[new-slug]]` links where relevant.

**This is the step most commonly skipped.** A compounding wiki's
value comes from bidirectional links. Do not skip.

### 8. Update `wiki/index.md`

Add entries under the correct categories:
```
- [[<slug>]] — <one-line summary> _(ingested <date>)_
```

### 9. Update `wiki/overview.md`

If this source:
- Introduces a significant entity → add to "Key Campaigns / Incidents
  / Decisions"
- Shifts the overall understanding → update "Current Understanding"
- Raises a new question → add to "Open Questions"

Update the frontmatter `updated` date.

### 10. Append to `wiki/log.md`

```
## [<today>] ingest | <source title>
Pages written: <slug>
Pages updated: <comma-separated>
```

### 11. Report

- Summary page: `wiki/pages/<slug>.md`
- Entity/concept pages created or updated
- Pages that received backlinks
- Index and overview updated
