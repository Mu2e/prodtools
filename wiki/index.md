# Wiki Index — Mu2e prodtools operational knowledge

### Campaigns
- [[run1bak-campaign]] — Run1B-series campaign on SimJob `Run1Bak`; introduces geom `v40` (field-off variant first); run cadence 1470; current entries = 4 resampler_beam stages _(seeded 2026-05-19)_
- [[run1ban-campaign]] — Run1B-series campaign on SimJob `Run1Ban`; same `v40` + DS-off geometry as Run1Bak but rebuilds MuminusStopsCat self-contained via MuBeamResampler `TargetStops` side output → artcat → MuonStopSelector _(seeded 2026-06-07)_

### Incidents
- [[2026-07-05-run1ban-mix-recovery-data-loss]] — 54 NoPrimaryMix1BB outputs lost: POMS re-queued complete work, pushOutput recoverDelay=3600 clobber-rewrites, 53 rewrites vanished off-grid _(investigated 2026-07-09)_
<!-- entries added by wiki-ingest -->

### Decisions
- [[2026-08-12-run1b-consolidation]] — Run1B folded into Offline `main` (PRs #1923, #1927, Production #565); `main` keeps the Run1A default and Run1B is selected by naming `geom_run1_b_v40.txt`. Production-scoped: v40 + degrader fix only; v01–v06 and their un-ported C++ archived under tag `run1b-archive-2026-08-08`. Run1Baa/Bab/Baf, Run1Bag/Bah and Run1Bai are **frozen** — v01 resolves on main but `inDS2Vacuum` is silently ignored, so re-running exits 0 with the wrong world. Reproduce via each entry's pinned CVMFS Musing, not the tag _(2026-08-12)_
- [[2026-07-21-input-preflight-check]] — `check_inputs` gate: verify a campaign's inputs are readable before jobs launch (resilient pileup present+sized vs SAM by direct stat; tape inputs staged via mdh — mdh is blind to resilient). Read-only, block-only (exit 2), fails closed; wired into `json2jobdef --prod --enqueue` (the map-file-removal branch, 2026-08-11, retired `submit_map`). Motivated by the index-519 truncated-pileup incident _(2026-07-21)_
- [[2026-07-12-hygiene-tiers-and-kept-duplication]] — tiers 1+2 consolidations (drifted fcl writer, cnf-name contract) + the deliberate-duplication do-not-fix list _(2026-07-12)_
- [[2026-04-21-extend-jobdef-per-index-overrides]] — add `event_id_per_index` to tbs; per-index linear overrides `offset + index × step` for any fcl key _(ingested 2026-04-21)_
- [[2026-04-21-fold-pbi-into-json2jobdef]] — delete the `gen_pbi_sequence` utility; add a `split_lines` input_data shape to `json2jobdef` _(ingested 2026-04-21)_
- [[2026-04-29-remove-poms-from-submit-loop]] — phased plan to drop POMS: Phase 1 keeps `mu2ejobsub` and adds Python `submit_map` + local SQLite state; Phase 2 replaces `mu2ejobsub` with direct `jobsub_submit` _(proposed 2026-04-29)_
- [[2026-04-30-phase2-direct-jobsub-implementation]] — concrete Phase 2 plan: replace `mu2ejobsub.sh` worker with `utils/runjob.py` (uses `jobfcl`/`jobiodetail`/`push_data` already in prodtools); add `utils/jobsub_argv.py` argv builder; `--backend direct` flag in submit.py. Driving win: per-job pushOutput SAM registration on the worker _(proposed 2026-04-30)_
- [[2026-05-22-mdc2025ap-rpcexternal-chain]] — added non-Physical `RPCExternal` entry to `data/mdc2025/resampler_beam.json` at MDC2025ap, consuming `sim.mu2e.PiMinusFilter.MDC2025ac.art` (MDC2025's rename of `PiTargetFilt`); partial-PS-off geom; fills the MDC2025 gap vs MDC2020aw RPCExternal. Extended 2026-05-23 with `RPCInternal` companion; both cnfs declared in SAM, map MDC2025-026 _(ingested 2026-05-22)_
- [[2026-05-23-mdc2025ap-pbarstgun-chain]] — 2025-era remake of `dts.mu2e.PbarSTGun.MDC2020ar.art`; two staged entries (`PbarSTGunStops` in `stage1.json`, `PbarResampling` in `resampler_beam.json`); fcl renamed in MDC2025 (output is now `dts.*.PbarResampling.*` not `dts.*.PbarSTGun.*`). Stage 0 pushed to map MDC2025-026, stage 1 blocked on grid completion _(ingested 2026-05-23)_
- [[2026-05-26-mdc2025ap-cosmiccryall-chain]] — CosmicCRYAll MDC2025ap primary; first MDC2025ap entry rooted in `S2Resampler.fcl` (cosmic) instead of `StopParticle.fcl` (stop). Documents 3 cosmic-vs-stop chain divergences (`PrimaryOutput.fileName` required, `bFieldFile` not inherited, `inputFile` defaults). Pushed to new map MDC2025-027 (njobs=50000); also documents the outloc-fix-in-POMS-map recovery procedure (no SAM/dCache mutation needed) _(ingested 2026-05-26)_
- [[mu2ename-unified-grammar]] — one `Mu2eName` value object covers file/dataset/tarball forms with parse + build + ~10 derivations; replaced two half-classes and ~45 hand-rolled `.split('.')`/f-string sites across `utils/`; folded `_TIER_TO_OWNER_CLASS` onto `.tier_class` _(ingested 2026-05-29)_
- [[2026-06-07-run1ban-mustop-rebuild-chain]] — Run1Ban builds `MuminusStopsCat.Run1Ban` self-contained via existing MuBeamResampler@Run1Ban-001 `TargetStops` side output → artcat@Run1Ban-001 → MuonStopSelector@Run1Ban; no new producer needed because MuBeamResampler.fcl emits 5 outputs in one job _(ingested 2026-06-07)_
- [[2026-06-14-run1ban-primaries-added]] — 4 Run1Ban-001 primary entries (CeEndpoint/FlateMinus/FlatGamma/NoPrimary) appended to `data/Run1B/primary_muon.json`; mirror Run1Bai-001 shape but with geom `v40` / run `1470` / `MuminusStopsCat.Run1Ban` input. All locally smoke-verified, 26000 jobs into MDC2025-029 _(ingested 2026-06-14)_
- [[2026-06-15-run1ban-stm-resampler-port]] — 3 STM resampler entries (BeamToVDEle/Mu/Target) ported from MDC2025ai → `data/Run1B/resampler_stm.json` at Run1Ban-001; uniform 5000×200000; Run1Bai beam cats + Run1Ban-001 TargetStopsCat. 15000 jobs added to MDC2025-029 (now 61004) _(ingested 2026-06-15)_
- [[2026-07-02-jobdef-arithmetic-and-tbs-njobs]] — per-index job arithmetic (sequencer/job_outputs/njobs/…) consolidated once into `Mu2eJobBase`, `jobiodetail.py` deleted (its stale copies made mkrecovery/submit disagree with the worker — mkrecovery since retired, see [[2026-08-08-retire-poms-backend]]); tarballs now self-descriptive via `tbs.njobs` (declared-or-derived; absent = open-ended, POMS map authoritative), fixing direct-backend generator submission _(decided 2026-07-02)_
- [[2026-07-03-file-resolver-and-sam-query-plan]] — IMPLEMENTED 2026-07-06: `utils/file_resolver.py` owns all dCache/CVMFS path grammar; `samweb_wrapper` deepened into the only SAM access path (q_* builders + fail-loud named queries); verified byte-identical fcl on real cnfs. MetaCat seam now real _(proposed 2026-07-03, implemented 2026-07-06)_
- [[2026-07-10-firstjob-index-windows]] — POMS-map entries can window a cnf's index space (`firstjob`: entry runs cnf indices `[F, F+njobs)`); the supported statistics-expansion mechanism (baseSeed = 1+index, so only fresh indices give fresh physics — `version`/run bumps do NOT); same tarball reusable once per window, mkrecovery/db_builder/submit were window-aware (mkrecovery/db_builder since retired — see [[2026-08-08-retire-poms-backend]]) _(implemented 2026-07-10)_
- [[2026-08-08-retire-poms-backend]] — remove the POMS submission backend entirely: dispatch (`runmu2e --jobdesc`), recovery (`mkrecovery` + SAM index definitions), and monitoring (`poms_db`/`db_builder`/`db_analyzer`/`pomsMonitor` + `web/pomsMonitor`); the direct backend (`submit_map --enqueue` + `submissions run`) is now the only submission path; ~2,800 lines deleted, SQLAlchemy dependency gone; `pre-poms-removal` git tag is the escape hatch _(decided 2026-08-08)_

### Runs
<!-- entries added by wiki-ingest -->
- [[2026-07-11-noprimary-run1ban-001-remake]] — remake NoPrimary.Run1Ban at 100× stats (10B empty frames) as new-name `Run1Ban-001`; new POMS map MDC2025-033; mix.json merge 10→1 for the 200k-ev/file granularity _(2026-07-11)_

### Sources
- [[pbi-sequence-workflow]] — full PBI chain (stage 1 dts → stage 2 mix dig → stage 3 reco mcs) via `json2jobdef` + `runmu2e` _(2026-04-25)_
- [[input-data-dir-shape]] — use `inloc: "dir:<path>"` for cvmfs-resident inputs; basenames in input_data, runtime resolves via existing `dir:` prefix _(2026-04-21)_
- [[input-data-chunk-mode]] — `chunk_lines` input_data shape; on-the-fly chunking at grid time via `tbs.chunk_mode` + runmu2e sed slice. Best of split_lines and dir: without the trade-offs _(2026-04-22)_
- [[input-data-max-nfiles]] — `max_nfiles` cap inside nested-dict value form of input_data; deterministic sorted prefix slice (or bound on random sample); reuses the `{"count": N, "random": bool}` precedent. Rule of thumb: new input_data options go in the value dict, not as sibling keys _(2026-05-27)_
- [[metacat-reference]] — samweb→metacat CLI bridge, MQL patterns, Python API snippets, read-only MCP install (from `Mu2e/aitools`) _(2026-04-24)_
- [[justin-vs-prodtools]] — DUNE justIN workflow system mapped onto prodtools concepts: JIT file allocation vs frozen cnf lists, file-state auto-recovery vs the since-retired mkrecovery (see [[2026-08-08-retire-poms-backend]]), adoption shape if Mu2e follows the MetaCat/Rucio path _(2026-07-07)_
- [[poms-reference]] — POMS data model (Campaign/Stage/Submission), dispatch lifecycle, Mu2e conventions (`i<stem>` naming via mkidxdef, dropbox path, decoupling possibility), `poms_client` library, common pitfalls _(2026-04-28)_
- [[json2jobdef-staging-workflow]] — cross-stage map of `data/<campaign>/*.json`: entry shapes (scalar vs array), stage→fcl table, dsconf flow, DbService rule, standard add-entry → json2jobdef → push loop _(2026-05-05)_
- [[prodtools-prd]] — descriptive PRD: what prodtools is today, personas (P1 prod ops, P2 physicist, P3 dev), goals/non-goals, capabilities, integration surface, gotcha categories, in-flight roadmap _(2026-05-05)_
- [[g4bl-runner]] — g4bl runner architecture (native AL9 spack); current execution path + `401e3da` SL7-removal diff (binds, env hygiene, `_is_inside_sl7` retired, `poms/g4bl.cfg` deleted); minimized POMS map shape; naming/dsconf convention _(2026-05-05)_
- [[reference-rpc-primary-inherits-bfgeom]] — RPC*/Pbar*/TargetStop primary fcls inherit `bfgeom_no_tsu_ps_v01.txt` via `StopParticle.fcl:41`; don't restate it in `fcl_overrides` _(2026-05-23)_
- [[digi-output-stream-by-fcl]] — output stream names depend on digi fcl: OnSpill/OffSpill = Triggered+Triggerable, Extracted/NoField = single Output; wrong override keys are silent no-ops; MDC2025af CosmicCRYExtracted entry has stale wrong-shape overrides _(2026-05-29)_
- [[2026-05-19-run1bak-resampler-additions]] — appended 4 field-off Run1Bak entries (Neutrals/MuBeam/EleBeam/MuStop Flash + MuStopPileup) to `data/Run1B/resampler_beam.json`; originals preserved; pions excluded _(2026-05-19)_
- [[prodtools-mcp-server]] — six status/discovery tools; `unknown` ≠ zero; two-part venv check _(2026-07-26)_

### Analyses
<!-- entries added by wiki-query when answers are filed -->

### Maintenance
- [[lint-2026-04-21]] — initial lint; wiki freshly initialized, 0 errors, 0 warnings, 1 info (coverage gap: no sources ingested yet) _(2026-04-21)_
- [[lint-2026-04-22]] — post-PBI-sequence lint; 0 errors, 2 warnings (raw-slug ambiguity, stale overview questions), 3 info _(2026-04-22)_
- [[lint-2026-04-24]] — post-stage-2-mix lint; 0 errors, 4 warnings (raw-slug ambiguity recurring, 2 orphans, 1 stale overview claim), 4 info _(2026-04-24)_
- [[lint-2026-05-05]] — post `json2jobdef-staging-workflow` ingest; 3 errors (all pre-existing broken slugs), 0 warnings, 2 info _(2026-05-05)_
- [[lint-2026-05-23]] — post MDC2025ap RPC/Pbar chain ingests; 4 errors (3 pre-existing slugs + 1 new `reference-rpc-primary-inherits-bfgeom`), 1 warning (stale overview), 3 info _(2026-05-23)_
- [[lint-2026-05-26]] — post MDC2025-026/027 primary push; 2 errors (both pre-existing recurring slugs), 0 warnings, 3 info (CosmicCRYAll coverage gap, PBI/g4bl carried forward, cross-ref maintenance) _(2026-05-26)_
- [[lint-2026-05-29]] — post architecture-audit (PomsEntry shipped, #2/#3/#4 drilled and shelved); 3 errors (2 pre-existing recurring slugs + 1 new cross-store ref to memory in [[mu2ename-unified-grammar]]), 4 warnings (orphans incl. mu2ename synthesis page), 3 info _(2026-05-29)_
- [[lint-2026-07-06]] — post resolver-refactor lint; 6 errors ALL FIXED (the 3 recurring slugs finally resolved: raw-doc refs → plain paths, memory ref → plain text), 1 contradiction + 1 orphan fixed, overview refresh recommended; `[[]]`-is-pages-only convention adopted _(2026-07-06)_
