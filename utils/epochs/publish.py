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
"""
import json
from typing import Dict

from utils.epochs.graph import Catalog
from utils.epochs.reports import count_warnings, gaps, incomplete_reasons, lookup, retire


def catalog_document(cat: Catalog, gens: Dict, generated_at: str) -> dict:
    epochs = []
    for e in sorted(cat.epochs.values(), key=lambda e: e.name):
        members = [m for m in cat.members.values() if m.epoch == e.name]
        members.sort(key=lambda m: m.name)
        rows = []
        for m in members:
            g = gens.get(m.name)
            info = lookup(cat, m.name)
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
        'inputs': {k: {'nfiles': v['nfiles'], 'descendants': sorted(v['descendants'])}
                   for k, v in sorted(cat.inputs.items())},
        'unparseable': [list(x) for x in cat.unparseable],
        'foreign': sorted(cat.foreign),
        'unclaimed_digs': {k: sorted(v) for k, v in cat.unclaimed_digs.items()},
        'missing_families': list(cat.missing_families),
        'gaps': gaps(cat),
        'incomplete': reasons,
        'retire': retire(cat) if not reasons else None,
        'count_warnings': count_warnings(cat),
    }


def write_catalog(doc: dict, path: str) -> None:
    with open(path, 'w') as f:
        json.dump(doc, f, indent=1, sort_keys=False)
        f.write('\n')
