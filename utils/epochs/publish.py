"""The file Ray's sim-epochs server reads: {"epochs":[{"name","datasets"}]}
with our fields alongside (ADR 0004, decision 10). Recomputed every time;
`generated_at` is informational and the only clock in the package.

`retire()` raises when the catalog is incomplete (a family with digs but
no loaded epoch file, or a dig matching no epoch root) rather than guess
at a deletion list. `catalog_document` must never crash on that: it calls
`incomplete_reasons(cat)` itself, publishes those lines under
`'incomplete'`, and — only when the catalog IS complete — calls
`retire(cat)` for the `'retire'` key. A non-empty `'incomplete'` next to
a `None` `'retire'` is the reported state; nothing here fills the gap
with a guess.

One caveat a consumer of the served JSON has to read (V5): when
`'incomplete'` is non-empty, `'retire'` is `null` AND the
`epochs[].datasets` / `epochs[].members` grouping may be WRONG for a
contested subtree: a dig whose own root claim disagreed with the epoch
it was walked in under has its own epoch corrected, but every member
below it still carries the epoch `_walk_down` inherited. That is the
reason `'retire'` is `null`, and `'incomplete'` names the dig. Do not
read the grouping as authoritative while `'incomplete'` is non-empty.

`'retire'` carries member candidates only: input retirement (proposing
deletion of upstream datasets) is not modeled in this version. See
CONTEXT.md, "Input", and
`docs/adr/0005-input-retirement-proves-deadness-positively.md`.
"""
import json
from typing import Dict

from utils.epochs.graph import Catalog
from utils.epochs.reports import count_warnings, gaps, incomplete_reasons, lookup, retire
from utils.epochs.status import groups


def catalog_document(cat: Catalog, gens: Dict, generated_at: str) -> dict:
    # ONE grouping for the whole document. `lookup` (per member, below)
    # and `count_warnings` each used to compute their own, so a
    # ~1000-member catalog walked and sorted itself 1513 times to write
    # one file. `groups(cat)` is a pure function of the member set and
    # the `excluded` flags, neither of which anything here changes.
    grouped = groups(cat)
    epochs = []
    for e in sorted(cat.epochs.values(), key=lambda e: e.name):
        members = [m for m in cat.members.values() if m.epoch == e.name]
        members.sort(key=lambda m: m.name)
        rows = []
        for m in members:
            g = gens.get(m.name)
            info = lookup(cat, m.name, grouped)
            rows.append({'name': m.name, 'tier': m.tier, 'desc': m.desc, 'status': m.status,
                         'hold': m.hold, 'excluded': m.excluded,
                         'generation': g.label() if g else '', 'cnf': g.cnf if g else '',
                         'superseded_by': info['superseded_by']})
        epochs.append({'name': e.name, 'purpose': e.purpose, 'status': e.status,
                       'roots': list(e.roots), 'datasets': [m.name for m in members],
                       'members': rows})
    reasons = incomplete_reasons(cat)
    return {
        'generated_at': generated_at,
        'epochs': epochs,
        'unparseable': [list(x) for x in cat.unparseable],
        # a member with two declared cnf parents: its generation above is
        # reported from the first in sort order alone (NEW-2). `publish`
        # prints no stderr noise, so this is the only place the served
        # document carries the anomaly.
        'generation_conflicts': list(cat.generation_conflicts),
        'foreign': sorted(cat.foreign),
        'unclaimed_digs': {k: sorted(v) for k, v in cat.unclaimed_digs.items()},
        'missing_families': list(cat.missing_families),
        'gaps': gaps(cat),
        'incomplete': reasons,
        'retire': retire(cat) if not reasons else None,
        'count_warnings': count_warnings(cat, grouped=grouped),
    }


def write_catalog(doc: dict, path: str) -> None:
    with open(path, 'w') as f:
        json.dump(doc, f, indent=1, sort_keys=False)
        f.write('\n')
