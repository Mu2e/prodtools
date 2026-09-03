"""Derive current / stale / superseded (decisions 3, 4, 5, 7).

- Group key = (family, tier, desc, purpose). Parent is NOT in the key.
- Within a group the highest DsconfKey.sort_key() is the winner, unless
  an `order` pin names one.
- Winner is `current` if every member parent is current, else `stale`.
- Non-winners are `superseded`.
- Excluded members get status `excluded` and never compete.
- Holds never touch status.
Members are visited in topological order over member-parent edges so a
parent's status is final before its children are judged.
"""
from typing import Dict, List, Optional, Tuple

from utils.epochs.graph import Catalog, Member

STATUSES = ('current', 'stale', 'superseded')
GroupKey = Tuple[str, str, str, Optional[str]]


def group_key(m: Member) -> GroupKey:
    return (m.key.family, m.tier, m.desc, m.key.purpose)


def groups(cat: Catalog) -> Dict[GroupKey, List[Member]]:
    out: Dict[GroupKey, List[Member]] = {}
    for m in cat.members.values():
        if m.excluded:
            continue
        out.setdefault(group_key(m), []).append(m)
    for members in out.values():
        members.sort(key=lambda m: m.key.sort_key(), reverse=True)
    return out


def _order_pins(cat: Catalog) -> Dict[GroupKey, str]:
    pins = {}
    for e in cat.epochs.values():
        for p in e.pins['order']:
            pins[(e.family, p['tier'], p['desc'], p['purpose'])] = p['winner']
    return pins


def _winners(cat: Catalog) -> Dict[str, bool]:
    pins = _order_pins(cat)
    win: Dict[str, bool] = {}
    for key, members in groups(cat).items():
        chosen = members[0]
        if key in pins:
            named = [m for m in members if m.dsconf == pins[key]]
            if not named:
                raise ValueError(f'order pin for {key} names {pins[key]!r}, '
                                 f'which is not among {[m.dsconf for m in members]}')
            chosen = named[0]
        for m in members:
            win[m.name] = (m is chosen)
    return win


def _topological(cat: Catalog) -> List[Member]:
    """Parents before children. Member-parent edges only."""
    done, order = set(), []
    def visit(m: Member, stack):
        if m.name in done:
            return
        if m.name in stack:
            raise ValueError(f'parentage cycle at {m.name}')
        stack.add(m.name)
        for p in sorted(m.parents):
            visit(cat.members[p], stack)
        stack.discard(m.name)
        done.add(m.name)
        order.append(m)
    for m in sorted(cat.members.values(), key=lambda m: m.name):
        visit(m, set())
    return order


def assign_status(cat: Catalog) -> Catalog:
    win = _winners(cat)
    for m in _topological(cat):
        if m.excluded:
            m.status = 'excluded'
            continue
        if not win[m.name]:
            m.status = 'superseded'
            continue
        parents = [cat.members[p] for p in m.parents if not cat.members[p].excluded]
        m.status = 'current' if all(p.status == 'current' for p in parents) else 'stale'
    return cat
