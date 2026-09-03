"""Members and inputs of every epoch, from SAM parentage.

Members = closure of the roots walking children (decision 2, rule 1).
Inputs = everything reached walking parents from a dig root, up to
`input_depth` levels (decision 13). A dataset belongs to exactly one
epoch: the one whose root its dig matched.
"""
import fnmatch
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from utils.epochs.dsconf import DsconfKey, DsconfParseError, parse_dsconf
from utils.epochs.epoch_files import EpochFile
from utils.job_common import Mu2eName


def root_matches(root: str, dataset: str) -> bool:
    """SAM '%' wildcard semantics on a 5-field dataset name."""
    return fnmatch.fnmatchcase(dataset, root.replace('%', '*'))


@dataclass
class Member:
    name: str
    tier: str
    desc: str
    dsconf: str
    key: DsconfKey
    epoch: str
    nfiles: int
    parents: Set[str] = field(default_factory=set)
    inputs: Set[str] = field(default_factory=set)
    status: str = ''
    hold: str = ''
    excluded: str = ''


@dataclass
class Catalog:
    epochs: Dict[str, EpochFile]
    members: Dict[str, Member] = field(default_factory=dict)
    inputs: Dict[str, Dict] = field(default_factory=dict)
    unparseable: List[Tuple[str, str]] = field(default_factory=list)
    foreign: Set[str] = field(default_factory=set)
    unclaimed_digs: Dict[str, List[str]] = field(default_factory=dict)


def _claim(epochs: Dict[str, EpochFile], dig: str):
    hits = [e.name for e in epochs.values() if any(root_matches(r, dig) for r in e.roots)]
    if len(hits) > 1:
        raise ValueError(f'{dig} matches roots of more than one epoch: {hits}')
    return hits[0] if hits else None


def _make_member(name: str, nfiles: int, epoch: str, cat: Catalog):
    n = Mu2eName.parse(name)
    try:
        key = parse_dsconf(n.dsconf)
    except DsconfParseError as exc:
        cat.unparseable.append((name, str(exc)))
        return None
    m = Member(name=name, tier=n.tier, desc=n.description, dsconf=n.dsconf,
               key=key, epoch=epoch, nfiles=nfiles)
    cat.members[name] = m
    return m


def _walk_down(root_member: Member, source, cat: Catalog):
    """Breadth-first over children. A child already claimed by another
    walk keeps its first epoch (it cannot happen for a well-formed SAM
    graph: a dig has one dsconf) but is still linked as a parent edge."""
    frontier = [root_member]
    while frontier:
        parent = frontier.pop()
        for child, n in source.children(parent.name).items():
            m = cat.members.get(child)
            if m is None:
                m = _make_member(child, n, parent.epoch, cat)
                if m is None:
                    continue
                frontier.append(m)
                # Non-member parents of a downstream member (today: none,
                # since log/cnf/etc are dropped; after ADR 0003's code plan:
                # the cnf). One isparentof query per member below dig.
                for p in source.parents(child):
                    if p not in cat.members and p != parent.name:
                        m.inputs.add(p)
            m.parents.add(parent.name)


def _walk_up(dig: Member, source, cat: Catalog, depth: int):
    """Breadth-first over parents, up to `depth` levels above the dig.

    A parent whose dsconf does not parse (controller ruling, task 4) is
    reported in `cat.unparseable` and dropped from this walk entirely: it
    is never recorded in `cat.inputs`, never linked as a member's `inputs`
    edge, and never walked further above. `Mu2eName.parse` itself is left
    to raise on a name that is not 5/6 dot-fields — that failure is not
    ours to swallow.
    """
    frontier = [(dig.name, 0)]
    while frontier:
        child, level = frontier.pop()
        if level >= depth:
            continue
        for p, n in source.parents(child).items():
            if p in cat.members:
                # a member above a dig (a dig-of-dig): a parent edge, never
                # an input
                if child in cat.members:
                    cat.members[child].parents.add(p)
                continue
            try:
                parse_dsconf(Mu2eName.parse(p).dsconf)
            except DsconfParseError as exc:
                cat.unparseable.append((p, str(exc)))
                continue
            rec = cat.inputs.setdefault(p, {'nfiles': n, 'parents': set(), 'descendants': set()})
            rec['descendants'].add(dig.name)
            if child in cat.inputs:
                cat.inputs[child]['parents'].add(p)
            else:
                cat.members[child].inputs.add(p)
            frontier.append((p, level + 1))


def build_catalog(epochs: Dict[str, EpochFile], source, families: List[str],
                  input_depth: int = 3) -> Catalog:
    cat = Catalog(epochs=dict(epochs))
    for family in families:
        for dig, n in sorted(source.dig_datasets(family).items()):
            epoch = _claim(epochs, dig)
            if epoch is None:
                cat.unclaimed_digs.setdefault(family, []).append(dig)
                continue
            m = _make_member(dig, n, epoch, cat)
            if m is None:
                continue
            _walk_down(m, source, cat)
            _walk_up(m, source, cat, input_depth)
    cat.foreign |= getattr(source, 'foreign', set())
    # a parent recorded as an input before its own walk made it a member
    for m in cat.members.values():
        m.inputs -= set(cat.members)
    # same invariant for the catalog-level inputs dict: a name recorded
    # in cat.inputs by one dig's upward walk can later be turned into a
    # member by a different dig's downward walk (dig processing order is
    # alphabetical, not dependency order); the retire report (a later
    # task) walks cat.inputs keys, so a stale one would misreport a live
    # member as retirable.
    for name in set(cat.inputs) & set(cat.members):
        del cat.inputs[name]
    _apply_pins(cat)
    return cat


def _apply_pins(cat: Catalog):
    for e in cat.epochs.values():
        for pin in e.pins['exclude']:
            m = cat.members.get(pin['dataset'])
            if m is not None:
                m.excluded = pin['reason']
        for pin in e.pins['hold']:
            m = cat.members.get(pin['dataset'])
            if m is not None:
                m.hold = pin['reason']
        if e.status == 'frozen':
            for m in cat.members.values():
                if m.epoch == e.name and not m.hold:
                    m.hold = f'epoch {e.name} is frozen'
