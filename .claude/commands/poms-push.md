---
description: Plan a production POMS push — pick the right map number (extend existing in place vs allocate new) before delegating to /mu2epro-run
argument-hint: <json-config> --dsconf <dsconf> [--workflow <pattern>] [SIMJOB_VERSION]
allowed-tools: Bash, Read
---

# Plan a production POMS push

Decides whether a `--prod --jobdefs <map>` push should **extend an
existing POMS map in place** or **allocate a new map number**, based
on whether the workflow's earlier stages already live in a map.
Prints the recommended `/mu2epro-run` invocation; does **not** push.

The PBI chain is the canonical example: stages 1+2 already in
`MDC2025-025.json` → stage 3 reco must extend `025`, not allocate
`026`. This skill exists because that mistake was made on
2026-04-25 and remediation required deleting an orphan SAM index;
the convention is now codified here.

## Usage

```
/poms-push <json-config> --dsconf <dsconf> [--workflow <pattern>] [SIMJOB_VERSION]
```

- `<json-config>` — relative path to a prodtools JSON config (e.g.
  `data/mdc2025/reco.json`); resolved against the repo root.
- `--dsconf <dsconf>` — required; the dsconf `json2jobdef` will use
  (e.g. `MDC2025ai_best_v1_3`). Used to (a) filter the JSON config
  entries that will be pushed, and (b) forward to `/mu2epro-run`.
- `--workflow <pattern>` — optional substring or glob to match
  against existing tarball descs (e.g. `PBI`, `PBI*_33344`,
  `CeEndpoint`). If omitted, derived from the config's `desc` /
  `tarball_append` fields.
- `SIMJOB_VERSION` — optional SimJob version tag (e.g. `MDC2025ai`,
  `Run1Bag`); default `Run1Bag`. Forwarded to `/mu2epro-run`.

## Examples

```
# Auto-derive workflow from data/mdc2025/reco.json desc fields
/poms-push data/mdc2025/reco.json --dsconf MDC2025ai_best_v1_3 MDC2025ai

# Explicit workflow pattern (skip auto-detection)
/poms-push data/mdc2025/digi.json --dsconf MDC2025ai --workflow PBI MDC2025ai

# Brand-new workflow (no prior stages in any map)
/poms-push data/mdc2025/stage1.json --dsconf MDC2025aj --workflow NewThing MDC2025aj
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps.

### 1. Parse args

- Locate `--dsconf <value>`. **Error and stop** if missing.
- Locate `--workflow <value>` (optional).
- Locate a SimJob version tag — first positional token matching
  `^[A-Z][A-Za-z0-9_]+$` and not a `--flag`; e.g. `MDC2025ai`,
  `Run1Bag`. Default `Run1Bag`.
- The first remaining positional arg = `<json-config>` (relative
  path). Resolve it against the repo root (`cwd` at invocation
  time) into an absolute path `JSON_ABS`.
- Validate `JSON_ABS` exists; error if not.

### 2. Determine the workflow pattern

If `--workflow <pattern>` was given, use it verbatim. Otherwise:

a. Read `JSON_ABS`. The config is either a list of entries or a
   single entry. Filter to only entries whose `dsconf` matches
   `<dsconf>` (entries can hold `dsconf` as a string or a one-element
   array). If no entries match, error and stop.

b. From each filtered entry, extract a "workflow root" token:
   - Prefer `desc` field if present.
   - Else infer from `input_data` keys: parse `<tier>.<owner>.<desc>.<dsconf>.<ext>`,
     take the `desc` portion.
   - Strip any of these stage-suffix patterns from the right end
     (longest first):
     `Mix1BBTriggered`, `Mix1BBTriggerable`, `Mix2BBTriggered`,
     `Mix2BBTriggerable`, `Mix1BB-reco`, `Mix2BB-reco`,
     `Mix1BB`, `Mix2BB`, `OnSpillTriggered`, `OffSpillTriggered`,
     `OnSpill`, `OffSpill`, `Triggered`, `Triggerable`, `-reco`,
     `Cat`. Iterate until none match.
   - Also account for the entry's `tarball_append` (e.g. `-reco`)
     by ensuring it's stripped.

c. Take the **longest common prefix** of the resulting roots
   (case-sensitive). Examples:
   - `PBINormal_33344`, `PBIPathological_33344` → `PBI`
   - `CeMLeadingLog`, `CePLeadingLog` → `Ce`
   - `FlatGamma`, `FlatGammaCalo` → `FlatGamma`

d. If the common prefix is shorter than 3 chars or empty, **stop
   and ask** the user for `--workflow` explicitly. Don't guess.

Print the derived pattern with one-line rationale.

### 3. Scan existing maps

- Run: `ls /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-*.json`.
- Keep only files whose basename matches `^MDC2025-\d{3}\.json$`
  (exactly 3 digits). This excludes test variants (`-test`,
  `-test2`, `-tes`), special names (`-MDS3c`), and any other
  naming oddity in one stroke.
- For each remaining map, read it and extract every `tarball` value
  (each entry has one). For each tarball name, parse out the desc
  portion: `cnf.<owner>.<desc>.<dsconf>.<v>.tar` → `<desc>`.
- Count how many tarballs in each map have a `<desc>` that
  **contains the workflow pattern as a substring** (case-sensitive).

### 4. Decide the map

- **Exactly one non-test map has ≥ 1 matching tarball** →
  *extend in place*. Target = that map's path.
- **Multiple maps match** → stop and ask which one to extend.
  Show each candidate with its match count and a sample tarball.
- **Zero maps match** → *allocate next free number*:
  - Across all `MDC2025-NNN.json` matching `^MDC2025-\d{3}\.json$`,
    take `max(NNN) + 1`, format as 3-digit zero-padded.
  - Target = `/exp/mu2e/.../poms_map/MDC2025-<NNN>.json` (does
    not exist yet; `json2jobdef --prod --jobdefs <path>` will
    create it).

### 5. Report and stop

Print exactly this shape:

```
Workflow pattern: <pattern>  (source: <--workflow flag | derived from desc>)
Existing maps:
  MDC2025-NNN.json — <K> matching tarballs  (sample: <one-tarball>)
  ...
Decision: <extend MDC2025-NNN.json in place | allocate new MDC2025-NNN.json>
Reason: <one-liner>

Recommended command:

  /mu2epro-run <SIMJOB_VERSION> json2jobdef \
      --json <JSON_ABS> \
      --dsconf <dsconf> \
      --prod \
      --jobdefs /exp/mu2e/app/users/mu2epro/production_manager/poms_map/MDC2025-NNN.json
```

**Do not invoke `/mu2epro-run` yourself.** Print and stop. The user
inspects the decision and runs the command next. This keeps the
existing production-push confirmation gate in `/mu2epro-run` exactly
where it is.

## Notes

- Read-only by design — does not modify maps, push tarballs, call
  samweb, or change SAM definitions.
- `-test.json` maps are skipped intentionally. If a test-mode push
  is needed, pass `--workflow <test-pattern>` explicitly and target
  a `-test` map by hand.
- The longest-common-prefix heuristic can over-match (e.g. `Ce`
  catches both `CeEndpoint` and `CeMLeadingLog` workflows). If the
  derived pattern matches multiple unrelated maps, the skill stops
  per step 4 and asks. Pass `--workflow` to disambiguate.
- If the JSON config has multiple `dsconf` values, only the entries
  matching the supplied `--dsconf` are used to derive the pattern
  (mirrors what `json2jobdef --dsconf` will do at push time).
- This skill encodes the convention in
  `feedback_extend_existing_poms_map.md` (memory) and the Stage 3
  "Process note" subsection of
  `wiki/pages/pbi-sequence-workflow.md`. If the convention changes,
  update both alongside this skill.
