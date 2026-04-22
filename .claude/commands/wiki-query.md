---
description: Ask a question against the prodtools wiki; answer with citations; offer to file
argument-hint: <question>
---

# Wiki Query

Ask a question. Read the wiki. Synthesize with citations. Offer to
file the answer back as a new page so explorations compound.

## Pre-condition

Read `wiki/SCHEMA.md`. If missing, tell the user to run
`/wiki-init` first.

## Process

### 1. Read `wiki/index.md` first

Scan the full index to identify likely-relevant pages. **Do NOT
answer from general knowledge** — the wiki is the source of truth,
even if you think you know the answer. If the wiki is empty for this
topic, say so explicitly rather than back-filling from training.

### 2. Read relevant pages

Read the identified pages in full. Follow one level of `[[slug]]`
links if they point to pages that seem relevant.

### 3. Synthesize the answer

Ground the response in wiki pages:
- Cite inline using `[[slug]]` for every claim sourced from a page
- Note agreements and disagreements between pages
- Flag gaps: "The wiki has no page on X" or "[[page]] doesn't cover
  Y yet"
- Suggest follow-up sources to ingest or questions to investigate

Format by question type:
- Factual → prose with citations
- Comparison → table
- How-it-works → numbered steps
- What-do-we-know-about-X → structured summary with open questions

### 4. Always offer to save

After answering, ask:

> "Worth saving as `wiki/pages/<suggested-slug>.md` under Analyses?"

If yes:
- Write the page with frontmatter: `tags: [query, analysis]`,
  `sources: [all cited slugs]`
- Add entry to `wiki/index.md` under Analyses
- Append to `wiki/log.md`:
  ```
  ## [<today>] query | <question summary>
  Filed as: [[<slug>]]
  ```

If no:
```
## [<today>] query | <question summary>
Not filed.
```

## Common Mistakes

- **Answering from memory** — Always read the wiki pages. Wiki
  contradictions are valuable signal.
- **Skipping the save offer** — Good answers compound the wiki.
- **No citations** — Every factual claim should trace to a `[[slug]]`.
