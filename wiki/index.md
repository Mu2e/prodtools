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
- [[pbi-sequence-workflow]] — how to use `json2jobdef` + `runmu2e` to produce `dts.mu2e.PBI*.art` outputs _(2026-04-21)_
- [[input-data-dir-shape]] — use `inloc: "dir:<path>"` for cvmfs-resident inputs; basenames in input_data, runtime resolves via existing `dir:` prefix _(2026-04-21)_

### Analyses
<!-- entries added by wiki-query when answers are filed -->

### Maintenance
- [[lint-2026-04-21]] — initial lint; wiki freshly initialized, 0 errors, 0 warnings, 1 info (coverage gap: no sources ingested yet) _(2026-04-21)_
