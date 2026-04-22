---
description: Regenerate EXAMPLES.md from current code against the schema
---

Regenerate `EXAMPLES.md` from scratch, treating it as a derived artifact
of the current source tree. Do not diff-patch the existing file —
overwrite it.

## Inputs (read in this order)

1. `docs/EXAMPLES_schema.md` — the spec: required sections, tribal
   knowledge to preserve, tone, anti-patterns. Follow it strictly.
2. All Python files under `utils/` and all entry points under `bin/` —
   the authoritative source for CLI flags, JSON keys, behavior.
3. JSON config files under `data/**/*.json` — for realistic examples and
   current campaign names.
4. `test/` — for working end-to-end invocations.
5. `git log --oneline -20` — for recent features that may need coverage.

## Rules

- Every CLI flag you document must exist in the current `argparse` for
  that tool. If a flag is not in the code, do not show it. When in doubt,
  `Grep` for the flag name before including it.
- Every JSON key you document must be consumed somewhere in
  `utils/`. Do not invent keys.
- Use campaign names (`MDC2020*`, `MDC2025*`, `Run1B*`) that appear in
  the current `data/` tree. Verify with `Grep` before using one.
- Preserve the tribal knowledge bullets listed in the schema verbatim or
  semantically equivalent — those facts are not derivable from code.
- Contiguous section numbering. No gaps.
- If `bin/` contains a user-facing script not covered in the schema's
  Additional Tools list, add a subsection for it. If the schema lists a
  tool that no longer exists in `bin/`, omit it.

## Process

1. Read `docs/EXAMPLES_schema.md` end-to-end.
2. List `bin/` and `utils/` — note what's actually there now.
3. For each section in the schema, `Read` the relevant source file(s)
   and draft the section. Do this section-by-section, not all at once.
4. After drafting, **spot-check**: pick 5 random commands from your
   draft. For each, `Grep` the source to confirm every flag and arg
   exists. If any fail, fix the draft.
5. `Write` the result to `EXAMPLES.md` (overwrite).
6. Report to the user: which sections changed substantively, what was
   added, what was removed.

## What NOT to do

- Do not hand-edit the existing `EXAMPLES.md` incrementally — generate
  fresh from source.
- Do not commit the result. The user reviews the diff first.
- Do not add a "Last updated" or "Generated from commit X" footer — git
  tracks that.
- Do not add features or caveats that aren't in the schema or derivable
  from code.
- Do not spawn sub-agents for this — single pass is sufficient at
  current repo scale.

## Arguments

`$ARGUMENTS` — if non-empty, treat as a hint about what specifically
changed and needs extra attention (e.g., "focus on mixing config
changes"). If empty, regenerate the whole doc.
