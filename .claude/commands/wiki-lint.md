---
description: Audit the prodtools wiki for broken links, orphans, contradictions, stale claims
---

# Wiki Lint

Audit the wiki. Produce a tiered categorized report. Offer concrete
fixes. Log the operation. Run this every 5–10 ingests.

## Pre-condition

Read `wiki/SCHEMA.md`. If missing, tell the user to run
`/wiki-init` first.

## Process

### 1. Build the page inventory

Read `wiki/index.md`, `wiki/overview.md`, and all files in
`wiki/pages/`. Build maps of:
- Existing slugs (filenames without `.md`)
- `[[slug]]` references found in any page
- `sources` listed in frontmatter

### 2. Run checks

**🔴 Errors (must fix)**

- **Broken links** — `[[slug]]` references with no matching
  `wiki/pages/<slug>.md`
- **Missing frontmatter** — pages without required `title`, `tags`,
  `sources`, or `updated`

**🟡 Warnings (should fix)**

- **Orphan pages** — pages with zero inbound `[[slug]]` references
  from other pages (excluding `index.md` and `overview.md`)
- **Contradictions** — claims in one page that conflict with another
  (same campaign/incident with different dates, names, outcomes)
- **Stale claims** — pages not updated within 90 days that contain
  "current", "latest", "recent", or year literals two or more years
  old

**🔵 Info (consider)**

- **Missing concept pages** — `[[slug]]` references appearing 3+
  times across the wiki with no dedicated page
- **Coverage gaps** — open questions in `overview.md` that could be
  answered by an ingest or web search
- **Missing cross-references** — two pages discussing the same entity
  but not linking to each other

### 3. Write the lint report

Always write `wiki/pages/lint-<today>.md` — do not ask:

```markdown
---
title: Lint Report <today>
tags: [lint, maintenance]
sources: []
updated: <today>
---

# Lint Report — <today>

## Summary
- 🔴 Errors: N
- 🟡 Warnings: N
- 🔵 Info: N

## 🔴 Broken Links
- [[source-page]] references [[missing-slug]] — does not exist
  Fix: create the page or remove the reference

## 🔴 Missing Frontmatter
- [[page]] is missing: <fields>

## 🟡 Orphan Pages
- [[slug]] — no inbound links
  Fix: link from [[related-page]] or delete if obsolete

## 🟡 Contradictions
- [[page-a]] says: "<claim>"
- [[page-b]] says: "<conflicting claim>"
  Recommendation: <which to trust, or "investigate">

## 🟡 Stale Claims
- [[page]] last updated <date>, contains "latest" — may be outdated
  Fix: re-verify or add "as of <date>" qualifier

## 🔵 Missing Concept Pages
- [[slug]] referenced N times, no page exists
  Fix: run `/wiki-ingest` or create a stub

## 🔵 Coverage Gaps
- Open question from overview.md: "<question>"
  Suggestion: <source type to ingest>

## 🔵 Missing Cross-References
- [[page-a]] and [[page-b]] both discuss <entity> — link them
```

Add the lint report to `wiki/index.md` under **Maintenance**.

### 4. Offer concrete fixes

For each fixable category:
- **Broken links:** "Remove the broken `[[slug]]` references?"
- **Missing cross-references:** "Add the missing links?"
- **Orphan pages:** "Add `status: orphan` to frontmatter?"
- **Missing frontmatter:** "Add missing fields with placeholders?"

Show exact diffs before writing. Apply only after confirmation.

### 5. Append to `wiki/log.md`

```
## [<today>] lint | <N> errors, <N> warnings, <N> info
Report: [[lint-<today>]]
Fixed: <list, or "none">
```
