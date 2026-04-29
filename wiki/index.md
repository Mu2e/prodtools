# Wiki Index — Mu2e prodtools operational knowledge

### Campaigns
<!-- entries added by wiki-ingest -->

### Incidents
<!-- entries added by wiki-ingest -->

### Decisions
- [[2026-04-21-extend-jobdef-per-index-overrides]] — add `event_id_per_index` to tbs; per-index linear overrides `offset + index × step` for any fcl key _(ingested 2026-04-21)_
- [[2026-04-21-fold-pbi-into-json2jobdef]] — delete the `gen_pbi_sequence` utility; add a `split_lines` input_data shape to `json2jobdef` _(ingested 2026-04-21)_

### Runs
<!-- entries added by wiki-ingest -->

### Sources
- [[pbi-sequence-workflow]] — full PBI chain (stage 1 dts → stage 2 mix dig → stage 3 reco mcs) via `json2jobdef` + `runmu2e` _(2026-04-25)_
- [[input-data-dir-shape]] — use `inloc: "dir:<path>"` for cvmfs-resident inputs; basenames in input_data, runtime resolves via existing `dir:` prefix _(2026-04-21)_
- [[input-data-chunk-mode]] — `chunk_lines` input_data shape; on-the-fly chunking at grid time via `tbs.chunk_mode` + runmu2e sed slice. Best of split_lines and dir: without the trade-offs _(2026-04-22)_
- [[metacat-reference]] — samweb→metacat CLI bridge, MQL patterns, Python API snippets, read-only MCP install (from `Mu2e/aitools`) _(2026-04-24)_
- [[poms-reference]] — POMS data model (Campaign/Stage/Submission), dispatch lifecycle, Mu2e conventions (`i<stem>` naming via mkidxdef, dropbox path, decoupling possibility), `poms_client` library, common pitfalls _(2026-04-28)_

### Analyses
<!-- entries added by wiki-query when answers are filed -->

### Maintenance
- [[lint-2026-04-21]] — initial lint; wiki freshly initialized, 0 errors, 0 warnings, 1 info (coverage gap: no sources ingested yet) _(2026-04-21)_
- [[lint-2026-04-22]] — post-PBI-sequence lint; 0 errors, 2 warnings (raw-slug ambiguity, stale overview questions), 3 info _(2026-04-22)_
- [[lint-2026-04-24]] — post-stage-2-mix lint; 0 errors, 4 warnings (raw-slug ambiguity recurring, 2 orphans, 1 stale overview claim), 4 info _(2026-04-24)_
