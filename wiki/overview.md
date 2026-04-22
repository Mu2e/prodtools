---
title: Overview
tags: [overview, synthesis]
sources: []
updated: 2026-04-21
sources: [2026-04-21-pbi-sequence-implementation]
---

# Mu2e prodtools — Operational Overview

> Evolving synthesis of everything in the wiki. Updated by `/wiki-ingest` when sources shift the understanding.

## Current Understanding

First substantial ingest is the 2026-04-21 PBI sequence work — a
Python port of `gen_NoPrimaryPBISequence.sh` that required extending
prodtools' jobdef mechanism with generic per-index linear overrides
(`event_id_per_index`). Pattern worth noting: when a workflow
doesn't fit prodtools' existing abstractions, the fix is to extend
the abstraction rather than bypass it — keeps `runmu2e` +
`prod_utils.{run,push_data,push_logs}` as the single point of
maintenance for execution + SAM push.

## Open Questions

- Does `jobfcl --index N` correctly render
  `source.firstEventNumber = N × events_per_job` in the emitted FCL?
  Code path is in place; verification deferred to first real chain
  run.
- Where should PBI chunk text files land for grid runs (stash vs
  resilient)? Currently local-only.

## Key Campaigns / Incidents / Decisions

- [[2026-04-21-extend-jobdef-per-index-overrides]] — first
  substantive extension to prodtools' core jobdef mechanism.
- [[pbi-sequence-workflow]] — PBI text-to-art pipeline, now
  jobdef-native.
