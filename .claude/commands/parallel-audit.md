---
description: Spawn N parallel Explore agents on independent slices of the repo, then synthesize findings into a prioritized punch list
argument-hint: <topic> [--agents N]
allowed-tools: Agent
---

# Parallel codebase audit

Fan out `N` Explore subagents on **independent** slices of the repo,
each producing a tight punch list, then synthesize the returns into a
single prioritized list with `[critical|high|medium|low]` tags and
file:line citations.

This pattern is the right tool when:
- The question spans the codebase (>3 directories or surfaces).
- The agents' work is mostly independent — overlap wastes tokens.
- The user wants concrete, citable findings, not a tour.

It is **not** the right tool when the answer is in one file
(use Read), one symbol (use Bash grep), or already known
(answer directly).

## Usage

```
/parallel-audit <topic> [--agents N]
```

- `<topic>` — what the audit is about. Examples:
  `"simplification opportunities"`,
  `"unused code and dead imports"`,
  `"test coverage gaps in utils/"`,
  `"CLI ergonomic inconsistencies"`,
  `"performance hot paths"`,
  `"security review of subprocess calls"`.
- `--agents N` — optional, default `4`. Cap at `6` (more invites
  duplication and synthesis overhead).

## Examples

```
# What this repo's biggest simplification wins are
/parallel-audit "simplification opportunities"

# Test gaps before adding CI
/parallel-audit "test coverage gaps" --agents 3

# Pre-PR security pass
/parallel-audit "security review: subprocess, SQL strings, file paths"
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps.

### 1. Parse args

- First non-flag token sequence (until a `--` flag) is the `TOPIC`.
- `--agents N` sets the agent count; default `4`, clamp to `[2, 6]`.

### 2. Pick the slicing dimensions

Choose `N` **non-overlapping** slices that together cover the topic.
Slicing strategies (pick the one that fits the topic):

- **By directory**: `bin/`, `utils/`, `data/`, `web/`, `tests/`,
  `wiki/`. Best when the topic is breadth-first and mechanical.
- **By concern**: code quality / CLI ergonomics / data layer /
  hygiene+CI / docs. Best for "code review" type asks.
- **By layer**: entry points / library / persistence / external IO.
  Best for performance, security, error-handling.
- **By risk**: critical paths / hot paths / cold paths. Best for
  performance and reliability audits.

Reject overlap. If two slices would inspect the same files for the
same thing, merge them and pick a different fourth slice.

### 3. Brief each agent

Spawn all `N` agents in **a single message with N parallel tool
calls** (not sequentially). Each brief must include:

- The slice's scope (paths, file globs, or symbol patterns).
- The exact question for this slice — narrow, not the umbrella topic.
- The output contract:
  - Top ~8 findings, prioritized.
  - Each finding: `file:line` + one-sentence problem +
    one-sentence fix + severity tag `[critical|high|medium|low]`.
  - Word cap (typically 500–600 words per agent).
  - Final line: a one-sentence "biggest single win" recommendation.
- What to **skip** (the slices owned by sibling agents) — prevents
  duplication.

Do not delegate synthesis. Each agent reports raw findings; the
lead (you) ranks and dedupes.

### 4. Synthesize

Once all agents return:

1. Collect findings, **dedupe** items mentioned by multiple agents
   (these are higher-confidence — flag them).
2. Group by severity, then by theme.
3. Trim noise: cosmetic style, hypothetical "what-ifs", anything
   without a file:line citation.
4. Verify any surprising claim before including it. Agent reports
   can be wrong about file contents — when an agent's claim drives
   a destructive recommendation, run a quick `Read`/`grep` first.
5. End with a clear "if you do one thing, do this" recommendation
   and explicit ROI ordering.

### 5. Report

Produce the punch list in the conversation. Format:

```
## [Severity] — [Theme]
1. [file:line] [problem]. [Fix].
2. ...

## Biggest leverage if you do one thing
[One paragraph, named action, expected ROI.]

Want me to start on [#N]?
```

Keep the synthesis tight — usually under 800 words. Long synthesis
defeats the point of fanning out.

## Notes

- The audit is **read-only**. Do not let agents edit files. The
  Explore subagent type already enforces this; if you use a
  general-purpose agent, say so explicitly in the brief.
- This skill is for **breadth**, not depth. For deep dives on a
  specific bug, use a single agent with full context, not parallel
  ones with partial context.
- Agents do not see the conversation history. Briefs must be
  self-contained — no "based on what we discussed" references.
- The synthesis lives in the lead conversation, not in any agent.
  Don't ask an agent to "synthesize the others' findings" — they
  can't see them.
- If an agent returns "nothing notable", trust it. A clean slice
  is information; don't pad the punch list to look thorough.
- For Mu2e prodtools specifically, the canonical 4-slice cut is:
  `utils+bin code quality / CLI ergonomics+EXAMPLES.md drift /
  DB schema+JSON configs / tests+repo hygiene`. Use this as the
  default unless the topic argues otherwise.
