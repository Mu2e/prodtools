---
description: Run json2jobdef against a data/<campaign>/<stage>.json entry by stage name + selectors (wraps /mu2e-run with sensible defaults)
argument-hint: <stage> [--campaign mdc2025] [--simjob-version V] [--desc D --dsconf C | --index N | --dsconf C] [extra json2jobdef flags]
allowed-tools: Bash
---

# Run a json2jobdef stage entry

Resolve `data/<campaign>/<stage>.json`, validate the stage name, and
dispatch to `/mu2e-run json2jobdef --json <that-file> [selectors]`.
For the staging-config model and stage→fcl mapping, see
`wiki/pages/json2jobdef-staging-workflow.md`.

This skill is for **non-production** invocations. For `--prod` /
`--pushout` use `/mu2epro-run json2jobdef ...` directly so the run
happens under the `mu2epro` account.

## Usage

```
/stage-entry <stage> [--campaign <name>] [--simjob-version <V>] <selectors> [extra-flags]
```

- `<stage>` — one of: `g4bl`, `stage1`, `primary_muon`, `pbi_sequence`,
  `mds3a`, `digi`, `mix`, `merge_filter`, `reco`, `resampler_beam`,
  `resampler_stm`, `evntuple`. Validated against `data/<campaign>/`.
- `--campaign <name>` — directory under `data/`. Default: `mdc2025`.
- `--simjob-version <V>` — forwarded to `/mu2e-run` as the SimJob tag
  (e.g. `MDC2025af`, `Run1Bag`). Default: `Run1Bag` (the `/mu2e-run`
  default).
- **Selectors** (at least one required, forwarded to `json2jobdef`):
  - `--dsconf C` — all entries matching that dsconf, expanded across
    every `input_data` element (the natural bulk selector for
    array-shape stages mix/reco/evntuple).
  - `--desc D --dsconf C` — one scalar entry by descriptor + dsconf.
  - `--index N` — **N is the flat position in the expanded
    (entry × input_data) list**, not the JSON array index. Easy to
    miscount with array-shape entries; prefer the other two.
- All other flags pass through verbatim (`--verbose`, `--no-cleanup`,
  `--extend`, `--ignore-empty`, `--enqueue`, `--slice-size N`).

## Examples

```
# Bulk: every reco entry tagged with this dsconf, expanded across all inputs
/stage-entry reco --simjob-version MDC2025am --dsconf MDC2025am_best_v1_3

# Single scalar entry (digi has per-entry desc)
/stage-entry digi --simjob-version MDC2025af --desc CeEndpointDigis --dsconf MDC2025af_best_v1_3

# Different campaign directory
/stage-entry stage1 --campaign Run1B --desc POT_Run1_a --dsconf MDC2025ac

# Flat-position index (only if you already know the expansion order)
/stage-entry mix --simjob-version MDC2025af --index 0
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps:

1. **Parse args.** The first positional token is `<stage>`. Then scan
   the remaining tokens for `--campaign <X>` (default `mdc2025`) and
   `--simjob-version <V>` (default empty — let `/mu2e-run` pick its
   own default). Strip those two flags from the forwarded argv. Keep
   everything else (selectors + extra flags) intact as `EXTRA`.

2. **Refuse production flags.** If `EXTRA` contains `--prod` or
   `--pushout`, **stop** and tell the user to invoke `/mu2epro-run
   json2jobdef --json data/<campaign>/<stage>.json <selectors> --prod`
   instead. This skill does not switch accounts.

3. **Validate stage.** Check `data/<campaign>/<stage>.json` exists.
   If not, run `ls data/<campaign>/*.json` and list the available
   stages, then exit non-zero. Do not guess or auto-correct.

4. **Require a selector.** `EXTRA` must contain at least one of
   `--index`, `--desc`, or `--dsconf`. If none, print the Usage block
   and exit. (`--desc` alone without `--dsconf` is fine — `json2jobdef`
   will error helpfully if the combination is wrong.)

5. **Dispatch.** Build the forwarded command:

   ```
   /mu2e-run [<SIMJOB_VERSION>] json2jobdef --json data/<campaign>/<stage>.json <EXTRA>
   ```

   If `--simjob-version` was given, include it as the first argument
   to `/mu2e-run`; otherwise omit it. Run it via Bash.

6. **Report.** After the run, list any new `cnf.*.tar` files in cwd
   (`ls -t cnf.*.tar 2>/dev/null | head -5`) and remind the user of
   the next step: `jobfcl --jobdef <cnf.tar> --index 0 > test.fcl &&
   mu2e -c test.fcl` to smoke-test locally before production push.

## Notes

- The schema for each `data/<campaign>/<stage>.json` and the stage →
  fcl mapping live in `wiki/pages/json2jobdef-staging-workflow.md`.
  Read that page before adding a new entry.
- Per-stage gotchas (DbService overrides on digi/mix/reco; trig-config
  drift on aj; PBI event-offset PRs) are cross-linked from that page.
- For multi-entry array-shape stages (mix, reco, evntuple) `--dsconf`
  alone is the natural multi-select; for scalar stages prefer
  `--desc` + `--dsconf` to disambiguate.
