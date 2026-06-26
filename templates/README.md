# Per-family chain templates

Templates consumed by `latestDatasets --emit <stage> --campaign <C>` to
synthesize a `json2jobdef` config for one POMS-free chain hop
(`dts → digi → reco → ntuple`).

Layout: `templates/<family>/<stage>.json`, where `<family>` is the campaign
**with the release letters stripped** (`MDC2025ap` → `MDC2025`, `Run1Ban` →
`Run1B`) and `<stage>` is one of `digi`, `reco`, `ntuple`. One template set per
family covers all of its releases.

**Fail-loud**: if the file for a family/stage is absent, `--emit` errors — a new
family must have its physics deliberately curated, never silently inherited.

## `--campaign`: family or release

- `--campaign MDC2025` (family) → loads `templates/MDC2025/`, discovers the
  **latest** release (`dts.mu2e.%.MDC2025%.art` → e.g. `MDC2025ap`).
- `--campaign MDC2025ap` (release) → same family template, pinned to that
  release.

Either way the template dir is resolved by family.

## What's curated vs substituted

Everything in a template is the curated recipe — `fcl`, `fcl_overrides` (geom,
`services.DbService.*`, `nearestMatch`, output-name pattern), `inloc`/`outloc`.
The script substitutes these placeholders per discovered dataset:

- `{campaign}` → the dataset's **release** (e.g. `MDC2025ap`). Use it in
  `dsconf`, `input_data`, and `simjob_setup` so the family template adapts to
  whichever release is current.
- `{desc}` → the input dataset's description (e.g. `CeEndpoint`, or
  `CeEndpointOnSpill` at the reco hop).
- `{input}` → the full input dataset name (rarely needed; `input_data` is
  pinned automatically).

**Staleness caveat:** a family template's physics (DB version, geom,
`nearestMatch`) applies to whatever release is discovered. That is correct while
you run the *latest* release (the tool's purpose); if you deliberately target an
older release, update or pin the physics accordingly.

## input_data contract

Each template declares **exactly one** `input_data` pattern, e.g.
`{"dts.mu2e.{desc}.{campaign}.art": 10}`. It serves two purposes:

1. **Discovery** — `--emit` derives the SAM defname by replacing `{desc}` → `%`
   and `{campaign}` → `<campaign>%` (the trailing wildcard lets a family tag
   match its releases): `dts.mu2e.%.MDC2025%.art`.
2. **Chaining** — a stage's input pattern must match the *previous* stage's
   output stream, so the hops connect (digi emits `dig.mu2e.{desc}OnSpill.…`;
   reco consumes `dig.mu2e.{desc}.…` and picks those up, with `{desc}` carrying
   the full upstream description).

The merge factor paired with the pattern is preserved on the synthesized entry.

## Special primaries (a template can be a list)

A template is either a single entry (dict) or a **list** of entries — exactly
like `data/<campaign>/*.json`. With a list, each entry carries a `desc`:

- the **wildcard** entry uses `"desc": "{desc}"` — it handles every primary and
  drives discovery;
- a **special** entry names an explicit `desc` (e.g. `"CosmicCRYExtracted"`) and
  supplies its own `fcl`/output.

```json
[
  {"desc": "{desc}", "fcl": ".../OnSpill.fcl", ...},
  {"desc": "CosmicCRYExtracted", "fcl": ".../Extracted.fcl",
   "fcl_overrides": {"outputs.Output.fileName": "dig.owner.{desc}.version.sequencer.art"}, ...}
]
```

For each discovered dataset an explicit-`desc` entry wins; otherwise the
`{desc}` wildcard applies. `--skip-produced` uses the matched entry's output
name, so a special's real product is detected. Add Triggered/Triggerable or
other variants as more list entries the same way — no bespoke keys, just `desc`.

## Notes

- `ntuple` templates use the `AnalysisMDC2025` musing (not `SimJob`) and the
  `nts` dsconf conventionally follows the `MDC2025-NNN` scheme — adjust as needed.
- The shipped `MDC2025/*` files are a worked example; verify the physics
  (DB version, geom, fcl variant) against the campaign before a production run.
