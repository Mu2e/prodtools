---
status: accepted
---

# The sim-epochs catalog is recomputed from SAM, never stored as metadata

Three hand-kept "current datasets" lists already drift apart
(`docs/latest_datasets.md`, the wiki table, `bin/latestDatasets`), and
that drift is the problem the catalog exists to end. So the only curated
input is one small file per digitization campaign in prodtools git
(`data/epochs/<letters>.json`: purpose, standing, roots, pins with
reasons); every other fact — membership, current/stale/superseded,
generation, gaps, retire candidates — is derived from SAM parentage and
the cnf at query time by `bin/epochs` and the read-only `prodtools` MCP,
and published to Ray's `sim_catalog.json` path in his server's exact
shape with our fields alongside. We deliberately do not stamp `epoch`
or `status` onto datasets as metacat metadata: a stored derived fact
goes stale on the next remake, which recreates the drift.

## Considered Options

Let Ray's server own the derivation. Rejected: it leans on prodtools'
dsconf parser, `jobquery` and the samweb wrapper and would fork; the
file is the contract instead, hosting stays his.
