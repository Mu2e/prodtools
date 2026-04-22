---
description: Revise existing wiki pages when knowledge changes; diff-previewed, source-cited, logged
argument-hint: <page|new-fact|lint-report>
---

# Wiki Update

Revise existing wiki pages. Always show diffs before writing. Always
log. Always cite the source of new information.

## Pre-condition

Read `wiki/SCHEMA.md`. If missing, tell the user to run
`/wiki-init` first.

## Process

### 1. Identify what to update

Input can be:
- **Specific page slugs** — update those
- **A new fact** — read `wiki/index.md` to find affected pages, then
  read them
- **A lint report** — work through its recommendations item by item

### 2. For each page, propose before writing

```
Current:  "<existing text>"
Proposed: "<replacement>"
Reason:   <why>
Source:   <URL, file path, or description>
```

**Always include Source.** An edit without a source creates
untraceability — future you won't know why the change was made.

Ask for confirmation per page. Do not batch-apply without per-page
confirmation.

### 3. Downstream effect check

Grep `wiki/pages/` for `[[slug]]` references to each updated page.
For each linking page, ask: does the update change something it
asserts?

- If yes: flag it — "[[other-page]] may need updating"
- Offer to update it with the same confirm-before-write flow

### 4. Contradiction sweep

If the new information contradicts something in the wiki, search all
pages for the contradicted claim before updating. It may appear in
more than one place. Update all occurrences.

### 5. Update `wiki/index.md`

If the one-line summary changed, update it in `index.md`. Update the
`updated` date in the page's frontmatter.

### 6. Update `wiki/overview.md`

If the updates shift the overall synthesis (new understanding,
resolved open question, changed key claim), propose edits to
`overview.md` with the same confirm-before-write flow.

### 7. Append to `wiki/log.md`

```
## [<today>] update | <list of updated slugs>
Reason: <brief>
Source: <URL or description>
```

## Common Mistakes

- **Updating without citing the source** — always include where the
  new information came from. Makes the wiki auditable.
- **Skipping the downstream check** — an update that contradicts a
  linking page while leaving it unchanged creates silent inconsistency.
- **Skipping the log** — every change must be logged.
- **Batch-writing without confirmation** — show each diff individually.
