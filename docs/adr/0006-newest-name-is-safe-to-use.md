---
status: accepted
---

# The newest name is safe to use: a remade parent obligates the downstream remake

Decided 2026-09-05 (Yuri). For every description at every tier, the
dataset with the newest name is the one to run on, and production keeps
that promise: when a parent is remade, every downstream tier that
existed for that description is remade before the round is complete.
A **stale** member of a **current** epoch — newest among its siblings,
but built on a parent that has since moved on — is therefore a rule
violation, not a state to live with.

Today the catalog already ranks the newest name first: `stale` means
"still the dataset to run on; a remake is owed", never "do not use".
What the rule adds is the obligation. `bin/epochs gaps` reports the
violation as it did before, and now **exits 1** whenever any printed
row is `stale`. `missing` rows (a dig content with no mcs or nts yet)
are work not yet done, not a broken promise, and do not fail. The
verdict follows the rows the caller asked about, so `gaps --epoch X`
judges X alone, and a frozen epoch, which produces no gap rows at all,
is outside the rule.

## Considered Options

Also gate `json2jobdef --enqueue`, refusing an input dataset that is
not `current` in the catalog unless the entry says why. That prevents
building a dataset that is stale at birth. Deferred: it touches the
production path and needs a family-scoped catalog walk at enqueue
time; the report-and-fail rule was wanted first and costs nothing.

A naming rule instead: downstream dsconfs encode the parent's campaign
letters so that lexicographic order alone tracks currency. Rejected:
the ntuple series (`MDC2025-NNN`) would change convention, and a
name that sorts newest still does not remake anything — the
obligation stays whatever the name says.

## Consequences

`gaps` becomes usable as a completion check: a round is not done while
it exits 1. Nothing else changes exit code. `members --status stale`
still lists the datasets to run on meanwhile. `CONTEXT.md` ("Dataset
status", Stale), `docs/EXAMPLES_schema.md` (`gaps`) and the wiki
design page record the rule.
