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

## Dataset status

The derived standing of one dataset relative to its siblings:
**current**, **stale** or **superseded**. Status is a property of a
dataset, never of a group of datasets; it is computed from what exists,
not curated, and it answers exactly one question: "should new work use
this dataset". It is the primary answer the catalog gives, and every
retirement list is derived from it, not the other way round.

**Current**: newest among its siblings, and its parent is current.

**Stale**: newest among its siblings, but its parent has moved on. Still
the dataset to run on; a remake is owed. Never a retirement candidate.
_Avoid_: outdated, pending.

**Superseded**: a newer sibling exists. The only retirement candidate.
_Avoid_: old, obsolete, "not the latest".

## Hold

A human-placed protection against deletion, on one dataset or on a whole
Epoch, independent of status. A held dataset can be superseded for new
work and still undeletable (a published paper's reference reco). Holds
answer "may this be deleted"; status answers "should I use this". The
two are never folded into one label.
_Avoid_: frozen (as a dataset status), pinned, locked.

## Epoch

A named set of simulation datasets that share one truth content and are
retired together. Epochs never overlap in membership; an Epoch is
retirable when none of its members is current or stale. An Epoch is one digitization campaign, named by its
campaign letters (`MDC2025au`), identified by its **Roots** and carrying
a human-assigned standing: `current` (being worked on), `frozen` (a Hold
on every member, nothing expected to change), or `retired` (every
member is a deletion candidate); everything else about it is derived. Re-reconstructing or re-ntupling never starts
a new Epoch; re-digitizing on new geometry, conditions or code does.
_Avoid_: generation, version, campaign, "the latest datasets".

## Root

A dataset pattern, by convention at the `dig` tier with the conditions
version wildcarded, that a human writes to define an Epoch. A dataset
matching a Root belongs to that Epoch; so does everything downstream of
it by parentage. Roots are the only curated part of an Epoch.

## Input (of an Epoch)

A dataset upstream of a Root — `dts`, stop and pileup catalogues. Inputs
are recorded for provenance and shared across Epochs; they are not
members and do not take an Epoch's standing. An Input is retirable when
nothing current or stale descends from it and it is not held.

Three rules make that "nothing descends from it" fail closed rather than
open:

- An **excluded** member confers liveness exactly as a current or stale
  one does. `retire` refuses to list an excluded member, so proposing to
  delete its sole upstream Input in the same run would be incoherent: we
  are keeping the dataset, so we keep what made it.
- A **frozen** Epoch holds its Inputs as well as its members. An Input
  is held when every dig it feeds is a member of a frozen or retired
  Epoch and at least one of those Epochs is frozen. An Input that also
  feeds a current Epoch's dig is judged by that line's status as before,
  and one feeding only retired Epochs stays retirable.
- **Liveness is one relation over every edge the catalog holds**
  (2026-09-04). The catalog records the same DAG several ways —
  `Member.parents`, `Member.inputs` (a downstream member's parents that
  no walk claimed), `cat.inputs[x]['parents']`, and the dig
  `descendants` sets — and "nothing descends from it" used to be read
  off the `descendants` set alone, which names only the dig each upward
  walk started from. An Input feeding a *current* member below a
  superseded dig was therefore proposed for deletion while the catalog
  itself recorded the consumer. There is now exactly one function that
  answers "is this dataset live", over the union of every edge, and
  `retire` has no other way to decide.
- The upward walk runs to **natural closure** by default (2026-09-04):
  it stops at the top of the real DAG, not at a fixed depth, so no *cap*
  cuts it. That is not the same as a complete Input graph.
- **The Input graph is known-incomplete** whenever a walk stopped early
  or dropped an edge: the `--input-depth` cap, a dsconf that does not
  parse (above a dig or below one), a dataset in the lineage not owned
  by `mu2e` (the source never returns it), a dropped tier that might
  carry lineage. Each severs the graph identically. All of them are
  recorded in one register, and while it is non-empty `retire` proposes
  **no Input at all**, not "every Input except the marked ones". The
  member section is unaffected — a member is judged by its own status,
  never by what reaches it. This is the same fail-closed stance `retire`
  takes on an incomplete catalog, an unapplied pin and an Epoch
  conflict, and it replaces four rounds of per-Input detectors: each was
  correct for the shape it was given, and the next review found another
  shape of incompleteness reaching the same false DELETE. Deciding per
  Input on a graph known to be incomplete is the reasoning that produced
  them.
- **Expect the Input section to be refused on production data**, and do
  not treat that as a fault to design around: 30 legacy dsconfs did not
  parse in the 2026-09-03 pre-flight, and an Input cannot be shown
  unused while part of the lineage is unreadable. The remedy is to make
  the names parseable, not to pass a flag — there is deliberately no
  flag that bypasses the refusal.

## Family

A simulation line whose datasets compete for "current": the leading
token of the dsconf, `MDC2025`, `MDC2020`, `Run1B`. Status is decided
within a Family and across its Epochs; datasets in different Families
never supersede each other.

## Sibling

Two datasets in the same Family with the same tier, physics content and
purpose, differing only in dsconf. Siblings may sit in different
Epochs. Among Siblings exactly one is **current** or **stale**; the
rest are **superseded**. Datasets with different parents can still be
Siblings: a remade parent is the usual reason a Sibling exists.

One deliberate exception, and it is the consequence of a hand-placed
pin (V4, 2026-09-04). A dead own-series name (`MDC2025-003`, no
purpose) competes in EVERY purpose group of its `(family, tier, desc)`
and ranks lowest in each. An `order` pin naming it in the `best` group
makes it current there — and it stays current in the `perfect` group
too, alongside that group's lettered winner, so the unpinned purpose
groups of that base triple then show two current members. That is
intended: winning any group it competes in is what an `order` pin is
placed to do, and keeping a dataset a person pinned is the fail-closed
direction for a tool whose output is a delete list. The alternative —
letting the pin's group be overridden by the insertion order of the
groups dict — was the NEW-5 bug.

## Gap

An expected dataset that does not exist: a physics content that reached
`dig` in an Epoch but has no `mcs` or no `nts`, or a **stale** member
whose remake has not landed. Every dig content is expected at every
downstream tier unless a person has said otherwise, with a reason.

## Generation

The code and conditions that produced a dataset: musing and version,
Offline version, fcl and its named settings (DbService version,
geometry, field). Read from the dataset's cnf, never assigned. Two
Siblings differ by Generation; two Epochs differ by truth content.
_Avoid_: version (of a dataset), build, release (alone).

## Relationships (sim epochs)

- A **Family** contains many **Epochs**; an **Epoch** is one digitization campaign.
- An **Epoch** has one or more **Roots**; every dataset downstream of a Root by parentage is a member of that Epoch and of no other.
- **Inputs** sit above the Roots and are shared across Epochs.
- **Siblings** are compared within a Family, across its Epochs; exactly one is current or stale.
- **Status** is derived per dataset; a **Hold** is placed by a person and never changes status.
- A **Gap** is an expected member that does not exist, or a stale member.
- Every member has a **Generation**, read from its cnf.

## Example dialogue (sim epochs)

> **Dev:** "We re-ntupled twenty-one `MDC2025au` datasets. Is that a new Epoch?"
> **Domain expert:** "No. Same digs, same truth. The new ntuples are Siblings of the old ones, so they become current and the old ones superseded. The Epoch is untouched."
> **Dev:** "And the ntuples we have not remade yet, whose mcs was re-reco'd last month?"
> **Domain expert:** "Those are stale: still the file to run on, a remake owed, and never on the delete list. They show up as Gaps."
> **Dev:** "The reco in the 2025 paper is superseded now. Delete it?"
> **Domain expert:** "It is superseded for new work, and it carries a Hold. Both are true; the Hold keeps it off the list."
