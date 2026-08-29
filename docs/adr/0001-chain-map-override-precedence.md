---
status: accepted
---

# Chain-map overrides apply before the campaign common.json overlay

A chain map may override any field of the Entry it points at, which puts
it in direct conflict with `json2jobdef.apply_common_overlay`: that
overlay supplies campaign-wide *defaults* and deliberately lets an
entry-level value win, because reversing it would move the 42 frozen
Run1B entries (v01/v03/v06) onto current geometry. We apply the map's
override to the Entry first and run the common overlay afterwards, so
`setdefault` sees the overridden value as already stated. A map override
therefore behaves exactly as if the entry file had been edited, and
prodtools keeps a single precedence rule — entry-level pins beat
campaign defaults — instead of two inverted ones whose outcome depends
on call order.

## Considered Options

Applying the map override *last* would have given the map authority over
both the entry and the campaign default. Rejected: it lets a map
silently undo a campaign default that exists to stop frozen entries
drifting, and it makes precedence depend on a call-order detail rather
than on a rule a reader can state.

## Consequences

The deep merge must normalise both shapes of `fcl_overrides` — a plain
dict and the list-wrapped mixing shape `[{...}]` (see
`json2jobdef._override_dicts`) — or an override aimed at a `mix` node
lands on the wrong object and vanishes silently.

Because the map can override anything, the entry file on disk stops
being the record of what ran. The ledger already stores the materialised
`entry_json` on both the campaign and every row, so that record is
preserved there and recoveries reuse it.
