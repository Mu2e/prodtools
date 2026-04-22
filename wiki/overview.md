---
title: Overview
tags: [overview, synthesis]
sources: [2026-04-21-pbi-sequence-implementation]
updated: 2026-04-22
---

# Mu2e prodtools — Operational Overview

> Evolving synthesis of everything in the wiki. Updated by `/wiki-ingest` when sources shift the understanding.

## Current Understanding

First substantial ingest is the 2026-04-21 PBI sequence work — a
Python port of `gen_NoPrimaryPBISequence.sh`. The initial approach
extended prodtools' jobdef mechanism with generic per-index linear
overrides (`event_id_per_index`). Subsequent iteration settled on
`chunk_mode`: the submit-side shape is `"input_data": {<cvmfs-path>:
{"chunk_lines": N}}`, and each grid worker `sed`-extracts its own
slice from the cvmfs source at runtime — no staging of chunk files,
full N-way parallelism. Pattern worth noting: when a workflow
doesn't fit prodtools' existing abstractions, the fix is to extend
the abstraction rather than bypass it — keeps `runmu2e` +
`prod_utils.{run,push_data,push_logs}` as the single point of
maintenance for execution + SAM push.

## Open Questions

- Scale test of `chunk_mode` at N ≫ 52 jobs (first production run
  used chunk_lines=1000 on a ~52,000-line source). Does sed on the
  cvmfs source behave well when many workers hit it concurrently?

## Key Campaigns / Incidents / Decisions

- [[2026-04-21-extend-jobdef-per-index-overrides]] — first
  substantive extension to prodtools' core jobdef mechanism.
- [[pbi-sequence-workflow]] — PBI text-to-art pipeline, now
  jobdef-native, canonical shape is chunk_mode.
- [[input-data-chunk-mode]] — the canonical shape for parallel
  consumption of large cvmfs-resident text files.
