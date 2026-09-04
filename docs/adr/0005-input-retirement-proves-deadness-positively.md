---
status: accepted
---

# Input retirement is removed from this branch; when rebuilt, it must prove deadness positively

`retire()` had two halves: the member half (superseded-and-not-held
members, plus every member of a retired epoch) and the input half
(proposing deletion of upstream datasets — stops catalogues, pileup,
`dts` primaries — once nothing live descends from them). The input half
is removed 2026-09-04, code and tests both, leaving `retire()` reporting
member candidates only. Nothing here is disabled behind a flag: the
functions, fields and CLI flag it depended on
(`graph._walk_up`, `Catalog.inputs`/`Member.inputs`, `input_depth`, the
truncation machinery, `input_graph_incomplete`, `reports.live_datasets`,
`reports.input_retirement_refusal`, `--input-depth`) are deleted.

The input half's design was a negative search: walk upward from every
dig root to build the input graph, then propose deleting whatever no
live member's downward reachability was found to reach. Across five
review rounds that design produced eight Critical false-DELETE bugs and
three separate "this is now structural" claims, each broken by the next
reviewer. The pattern repeated because the design has an unstated
invariant nobody enforced until it was too late to matter: every node
the input graph touches must be either *expanded* (its own parents
asked for) or *registered* as a reason the graph is incomplete. Four
rounds of hardening closed one way a node could be neither — a depth
cap, an unparseable dsconf, a foreign owner, a dropped tier — and the
fifth found a node that was neither expanded nor registered through a
mechanism none of the earlier rounds had touched: a `Member.inputs`
edge recorded by the *downward* walk but never itself expanded upward,
and a dig definition dropped by *discovery*, before any walk ran. A
negative search over a graph built by best-effort traversal always has
an unwalked frontier somewhere; the next reviewer's job was reliably to
find it.

When input retirement is rebuilt, in its own branch, it must prove a
dataset dead by **enumerating its full downward closure** and requiring
every node in that closure to be a known member that is superseded or
in a retired epoch. Any node in the closure that is unknown to the
catalog, unparseable, foreign-owned, held, or itself live blocks the
proposal. This is positive proof rather than negative search: instead
of walking away from the dataset and asking "did I find a live
consumer", it walks toward every consumer the dataset could have and
requires each one to be accounted for and dead. A frontier that cannot
be fully classified refuses that one input, not the input section as a
whole — the failure mode changes from "an unwalked region silently
reads as safe" to "an unclassified region is visibly still open."

## Considered Options

Keep hardening the negative search (add a sixth register entry for the
`Member.inputs`/discovery gap, as findings-4 suggested). Rejected: eight
Criticals and five rounds is the track record of this design, not of
any one implementation of it; the sixth hardening pass would fix the
two shapes verify-4 found and leave the same unstated invariant for a
seventh reviewer to violate a new way. The register-every-early-exit
approach is sound bookkeeping for a design whose real defect is that
"walk away and see what you find" cannot be exhaustively enumerated by
listing failure modes after the fact.

Gate the input section behind a flag (e.g. keep the code, default it
off) so a future round can re-enable it once the dsconf grammar or the
frontier-invariant assertion lands. Rejected: a re-enable path is a
re-enable path with no memory of why it was disabled. The brief for
this removal is explicit — remove, do not disable — because leaving the
old code reachable is how someone flips it back on without reading five
rounds of review history first.

## Consequences

No upstream deletions are proposed by this version of `epochs retire`;
`retire()` returns member candidates only, and the published document
(`publish.catalog_document`) carries no `inputs`, `input_retirement_refused`,
`input_graph_incomplete` or `input_depth` keys. `CONTEXT.md`'s "Input"
entry, `docs/EXAMPLES_schema.md`'s `epochs` entry, and
`wiki/pages/2026-09-02-sim-epochs-design.md` are updated to say so.
Observable change on today's production data: none — the removed
incompleteness guard already suppressed the input section on every real
build (30 legacy dsconf names did not parse), so `retire()` was already
members-only in practice.

`utils/epochs/source.py`'s `nfiles()` is also removed: its only caller
was the input-record bookkeeping in the walk this ADR retires.
`source.parents()` stays — `graph._walk_down` still calls it, both to
find a member's declared cnf parent (ADR 0003's generation route) and
to link one member to another for status propagation — and a non-cnf,
non-member parent it returns is simply no longer recorded anywhere.
