# Context

Glossary for prodtools. Terms only — no implementation details, no specs.

## Data tier

The kind of artifact a file holds, and the first field of every Mu2e
file name: `sim`, `dts`, `dig`, `mcs`, `nts`/`ntd`, `cnf`, `log`.

## Hop

One tier-to-tier transformation in the production chain. The hops in
use are `sim -> dts`, `dts -> dig`, `dig -> mcs`, `mcs -> nts|ntd`.

A hop is not the same as a Stage: more than one Stage can produce the
same output tier (both a resampler and a primary generator produce
`dts`), so a tier pair does not uniquely name a Stage.

## Stage

A named kind of work that consumes one tier and produces another —
`mix`, `digi`, `reco`, `ntuple`. A Stage owns the curated physics for
its hop (geometry, DbService version, fcl, merge factors), held in
`templates/<family>/<stage>.json`.

Not every hop has a Stage. `sim -> dts` has none: its work is
hand-authored per campaign in `data/<campaign>/*.json` because its
sizing (`njobs`, `events`, `run`) and generator choice are physics
judgment, not derivable from the input.

## Description (`desc`)

The physics content of a dataset, and the third field of a Mu2e file
name — `NoPrimary`, `CeEndpoint`, `STMBeamToVDEle`. A `desc` is not a
Stage. One Stage processes many `desc`s: `NoPrimary` is one key in the
`mix` Stage's template.

## Campaign

A unit of submission tracked in the ledger: one cnf tarball, one index
space, a cursor advanced in slices. A Campaign always has a real
tarball; it comes into existence only when that tarball is built.

## Entry

A single configuration object that `json2jobdef` turns into a cnf —
either hand-authored in `data/<campaign>/*.json`, or synthesized from a
Stage template plus a discovered input dataset.

## Origin

Free-text provenance recorded on a Campaign, describing where its Entry
came from. Deliberately *not* an address: nothing parses it, and
nothing dispatches from it.

## Location

A named storage area a file can be read from or written to: `tape`,
`disk`, `scratch`, `resilient`, `stash`, `outstage`, `none`, or
`dir:<path>`. A Location answers, in one place, which dCache area it
is, how a job reads from it, what token scope writing to it needs,
whether writing there declares the file, and whether an Entry may name
it as an inloc or an outloc. The inloc/outloc split is policy held by
the Location, not a difference in kind — `copy_to_stash` writes to
`stash`, but no Entry may emit there.

## Declared / Undeclared output

A **declared** output is registered in SAM: it has metadata, parentage,
and can be found by dataset query. Declaring is what makes exact
recovery and provenance possible.

An **undeclared** output exists only as files on a filesystem. It has no
SAM record, so it has no parentage — a lineage query will report its
child as a primary, which is a wrong answer rather than a missing one.

Declaration is independent of *where* the files sit. `scratch` names a
storage area, not a declaration policy: the `scratch` output location
writes a **declared** dataset that happens to live under
`/pnfs/mu2e/scratch/datasets/`. Saying "on scratch" therefore does not
mean "not in SAM"; the two must be stated separately.

The only output location that declares nothing today is `outstage`.

## Reference node

A node a chain map names but does not submit. It exists so other nodes
can depend on something the chain did not produce — a pre-existing
dataset, or work submitted by hand.
