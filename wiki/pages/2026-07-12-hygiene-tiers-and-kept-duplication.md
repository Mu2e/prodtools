---
title: Hygiene tiers 1+2 — what was consolidated, and duplication kept on purpose
tags: [decision, hygiene, refactor, samweb, fcl]
sources: []
updated: 2026-07-12
---

# Hygiene tiers 1+2 (2026-07-12) — and the do-not-"fix" list

Four-agent audit (duplication / dead code / structure / API surface) over
utils+bin (~9.7k lines). Verdict: codebase already tight; real value was
drift-bug prevention, not raw size. Applied tiers 1+2 (commits `3a6b961`,
`af681e1`, −150 net lines, 322/322 tests); tier 3 deferred.

## Now single-homed (the contracts that were held by copy-paste)

- **`prod_utils.write_direct_input_fcl`** — the direct-input FCL writer
  existed in both `process_direct_input` (worker) and
  `fcldump.write_fcl_direct_input`, and had drifted (URL-resolve + base
  filtering on the fcldump side only). The drift is now two explicit
  flags: `format_input`, `filter_base`.
- **`config_utils.cnf_name`** — the cnf-name contract:
  json2jobdef's parfile name and jobdef's written tarball must be
  byte-identical or a `--prod` push registers a map entry whose tarball
  was never written. Was 4 copy-paste sites; verified byte-identical
  members vs the pushed `cnf.mu2e.NoPrimary.Run1Ban-001.0.tar`.
- **`runmu2e._execute_mu2e`** — shared execute step for POMS + direct
  backends.
- samweb_wrapper surface: 5 locate variants → 3 (swallow variants gone,
  fail-loud); `list_definitions` folded into `definitions_matching`;
  4 internal-only `q_*` builders privatized. `prod_utils.fail()` = the
  print+sys.exit(1) idiom's one home.

## Duplication/style KEPT on purpose — do not "clean up"

Future audits will re-flag these; they are deliberate:

1. **`genFilterEff`/`famtree` samweb instance style** — `process_dataset`
   takes the wrapper as an injected parameter (famtree passes it
   through). Not gratuitous inconsistency; converting to module calls
   changes the API.
2. **`file_resolver` locate→strip→append sequence appears twice** —
   scripted-consumer path vs worker inner loop; the worker variant must
   stay byte-identical (docstring flags it). Load-bearing.
3. **`mu2ejobdef` command-string echo in both jobdef and json2jobdef** —
   differences are semantic (`--code` fallback, presence-vs-truthiness
   run/events, resolved-vs-literal template path) and
   `test/parity_test.py` consumes json2jobdef's exact string.
4. **Direct-vs-POMS push halves in runmu2e** — retry, SHA256 manifest,
   and `log_storage_location` are direct-mode design (no POMS recovery
   layer), not drift. NOTE the open question: POMS-mode `push_logs`
   still defaults to `disk` while direct mode uses the first output's
   location — harmonizing is a *behavior* decision, not a refactor.
5. **Legacy branches that look dead but are live**: pre-`tbs.njobs`
   tarball handling, `subrunkey is None` sequencer path, string-form
   `input_data` (29 entries in data/mdc2025+mdc2030) — old cnfs/configs
   still exist in SAM.
6. **`sys.path.insert` boilerplate (~18 files)** — must run before
   package imports; cannot be factored into an import.

## Deferred (tier 3 + decisions), in rough value order

- Relocate the ~450-line single-caller "runner family"
  (`process_template/process_direct_input/process_jobdef/build_mu2e_cmd/
  process_g4bl_jobdef/push_data/push_logs`) out of prod_utils next to
  runmu2e → prod_utils 900→450 lines of genuinely shared helpers.
  Mechanical but must repoint test patch targets.
- Table-drive `_validate_options_for_source_type` (90 lines, **no unit
  coverage** — parity_test only) and `validate_jobdesc` ladders.
- **Three non-equivalent campaign regexes** (`job_common._CAMPAIGN_RE`,
  `chain_emit._FAMILY_RE`, `jobsub_argv.campaign_from_tarball`) for the
  same concept — unify only after a parity check over real dsconfs.
- 6–9 identical bin/ stubs → symlinks (trade: grep-ability).
- `Mu2eJobBase.json_data` raw access at 6 sites vs typed accessors
  (fail-loud `tbs` would be a behavior change).

## Related

- [[2026-07-02-jobdef-arithmetic-and-tbs-njobs]] — why pre-tbs.njobs
  branches stay live.
- [[2026-07-11-noprimary-run1ban-001-remake]] — the production cnf used
  for the byte-identity verification.
