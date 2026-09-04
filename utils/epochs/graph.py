"""Members of every epoch, from SAM parentage.

Members = closure of the roots walking children (decision 2, rule 1).
A dataset belongs to exactly one epoch: the one whose root its dig
matched.

Input retirement -- proposing deletion of upstream datasets (stops
catalogues, pileup, `dts` primaries) once nothing live descends from
them -- is NOT modeled in this version. It was removed 2026-09-04 after
eight false-DELETE Criticals across five review rounds; see
CONTEXT.md, "Input", and
`docs/adr/0005-input-retirement-proves-deadness-positively.md` for the
rebuild's design (positive proof over the full downward closure,
rather than the negative search this branch tried and retired). Only
the downward walk (`_walk_down`) remains, so `source.parents()` is
still consulted -- but only to find a member's declared cnf parent
(ADR 0003's generation route) and to link one member to another; a
non-member, non-cnf parent is simply never recorded.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from utils.epochs.dsconf import DsconfKey, DsconfParseError, parse_dsconf
from utils.epochs.epoch_files import EpochFile
from utils.epochs.progress import Progress
from utils.job_common import Mu2eName


def root_matches(root: str, dataset: str) -> bool:
    """SAM '%' wildcard semantics on a 5-field dataset name.

    `%` is the ONLY metacharacter: everything between the `%` is escaped,
    so a hand-edited root carrying `?`, `[` or `]` matches those
    characters literally instead of behaving like a shell glob (M10).
    `fnmatch` gave them glob meaning, and a mis-parsed root silently
    claims — or fails to claim — digs, which feeds `unclaimed_digs` and
    therefore `retire()`'s refusal."""
    rx = '^' + '.*'.join(re.escape(part) for part in root.split('%')) + '$'
    return re.match(rx, dataset) is not None


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
    # Declared cnf tarball parents (ADR 0003). Kept OUT of `parents` on
    # purpose: a cnf is provenance, not data, and must never become a
    # retire candidate. Read only by generation.cnf_for.
    cnf_parents: Set[str] = field(default_factory=set)
    status: str = ''
    hold: str = ''
    excluded: str = ''


@dataclass
class Catalog:
    epochs: Dict[str, EpochFile]
    members: Dict[str, Member] = field(default_factory=dict)
    unparseable: List[Tuple[str, str]] = field(default_factory=list)
    # Datasets the downward walk reached whose owner is not `mu2e`
    # (`SamSource` never returns them as members or cnf parents, but does
    # report the name). Reporting only; nothing here decides a status.
    foreign: Set[str] = field(default_factory=set)
    unclaimed_digs: Dict[str, List[str]] = field(default_factory=dict)
    # Families with at least one dig in SAM but no loaded EpochFile for
    # that family (decision 2026-09-03: retire() refuses to run while
    # this is non-empty — a partial catalog cannot judge a superseded
    # member against an epoch nothing confirmed).
    missing_families: List[str] = field(default_factory=list)
    # One line per pin that could not be applied: it names nothing in the
    # catalog, or it names something owned by a different epoch. retire()
    # refuses to run while this is non-empty — a dropped `hold` is a
    # protection the human believes they placed and does not have.
    pin_problems: List[str] = field(default_factory=list)
    # One line per dig whose own root claim disagreed with the epoch it
    # inherited by being walked into as another dig's child (NEW-3). The
    # root claim wins on the dig itself, but the members BELOW it keep the
    # inherited epoch, and an epoch is what stamps a frozen hold — so
    # retire() refuses here too rather than delete on a guessed standing.
    epoch_conflicts: List[str] = field(default_factory=list)
    # One line per member with more than one declared cnf parent (NEW-2).
    # Reported, never resolved by sort order: two cnfs producing one
    # dataset is the mixed-generation condition `consistency` exists to
    # surface. Does not block retire() — a generation is provenance, not
    # a deletion input.
    generation_conflicts: List[str] = field(default_factory=list)


def _claim(epochs: Dict[str, EpochFile], dig: str):
    hits = [e.name for e in epochs.values() if any(root_matches(r, dig) for r in e.roots)]
    if len(hits) > 1:
        raise ValueError(f'{dig} matches roots of more than one epoch: {hits}')
    return hits[0] if hits else None


def _make_member(name: str, nfiles: int, epoch: str, cat: Catalog):
    """None when the dsconf does not parse. That drops the dataset AND
    everything below it from the member closure; `cat.unparseable`
    records the name and reason so the drop is visible rather than
    silent."""
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


class _Memo:
    """Per-build cache for `children`/`parents` only.

    A stops catalogue shared by hundreds of dts datasets, or a stage-1
    sim dataset shared by many descendants, would otherwise be asked
    via `children()`/`parents()` once per referencing walk — that
    multiplicity, not any single query's latency (every SAM query is
    sub-second on its own), is what kept a live `bin/epochs members`
    run from finishing quickly. Caching by name bounds each of the two
    methods to one real call per distinct dataset for the life of one
    `build_catalog` call, no matter how many digs' walks reach it (a
    dig reached both as another dig's child and as its own family entry
    is the concrete case, see `test_lineage_queries_are_memoized_per_build`).

    Every other source method (`dig_datasets`, `dig_families`,
    `foreign`, `unparseable_defs`, `cnf_names`, `local_path`, ...) is
    forwarded untouched via `__getattr__` — those are already called at
    most once (or a small, fixed number of times) per build and do not
    need caching.
    """
    def __init__(self, source):
        self._source = source
        self._children_cache = {}  # type: Dict[str, Dict[str, int]]
        self._parents_cache = {}  # type: Dict[str, Dict[str, int]]

    def children(self, name):
        if name not in self._children_cache:
            self._children_cache[name] = self._source.children(name)
        return self._children_cache[name]

    def parents(self, name):
        if name not in self._parents_cache:
            self._parents_cache[name] = self._source.parents(name)
        return self._parents_cache[name]

    def __getattr__(self, attr):
        return getattr(self._source, attr)


def _record_cnf_parents(member: Member, source) -> None:
    """The declared cnf tarball among `member`'s parents, if any (ADR
    0003). Everything else `source.parents()` returns for `member` is
    upstream data -- a dts, a stops catalogue, another member -- and is
    no longer recorded: input retirement, the only consumer of that
    edge, was removed 2026-09-04."""
    for p in source.parents(member.name):
        if p.startswith('cnf.'):
            member.cnf_parents.add(p)


def _walk_down(root_member: Member, source, cat: Catalog):
    """Depth-first (frontier.pop() is LIFO) over children. A child
    already claimed by another walk keeps its first epoch (it cannot
    happen for a well-formed SAM graph: a dig has one dsconf) but is
    still linked as a parent edge. A child already in `cat.members` is
    not re-walked — that, plus `source` being the per-build `_Memo`
    wrapper `build_catalog` passes in, is what keeps a downstream
    member shared by two different dig walks from being queried twice.

    `_record_cnf_parents` is called once for the root (a dig can declare
    a cnf parent directly, exactly like any other member) and once for
    every newly discovered child; a dig already visited as another dig's
    child still gets it called again here when its own family-loop entry
    triggers `_walk_down`, but the `_Memo` cache makes the repeat free."""
    _record_cnf_parents(root_member, source)
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
                _record_cnf_parents(m, source)
            m.parents.add(parent.name)


def build_catalog(epochs: Dict[str, EpochFile], source,
                  families: Optional[List[str]] = None, progress=None) -> Catalog:
    """`families=None` discovers every family with a dig in SAM
    (`source.dig_families()`) instead of trusting the caller's list —
    the catalog must be complete before a member is judged. A family
    with digs but no loaded epoch still gets its digs walked into
    `cat.unclaimed_digs` (that is what makes the gap visible) and is
    recorded in `cat.missing_families`.

    A caller MAY pass an explicit `families` list, and `cli` does for the
    verbs where one family's answer cannot depend on another's: a member
    is judged only against the siblings sharing its `group_key`, whose
    first field is the family. What a scoped catalog loses is
    `missing_families` — and therefore part of `incomplete_reasons()` —
    for every family outside the list, which is exactly why `retire` and
    `publish` never scope (`cli.SCOPABLE_VERBS`).

    `source` is wrapped once, here, in `_Memo`: every dig's
    `_walk_down` shares the same cache, so a dataset entered from many
    different digs (a stops catalogue shared by hundreds of dts
    datasets) is asked at most once per build.

    `progress` is a `progress.Progress` (or anything carrying the same
    three events). The default is a disabled one that prints nothing, so
    no caller has to branch on whether progress was wanted."""
    progress = progress or Progress(enabled=False)
    msource = _Memo(source)
    if families is None:
        families = sorted(msource.dig_families())
    cat = Catalog(epochs=dict(epochs))
    loaded_families = {e.family for e in epochs.values()}
    cat.missing_families = [f for f in families if f not in loaded_families]
    for family in families:
        digs = sorted(msource.dig_datasets(family).items())
        progress.family(family, len(digs))
        for dig, n in digs:
            # `finally`, not a call after the body: two `continue` paths
            # below (an unclaimed dig, an unparseable dsconf) are still a
            # dig gone past, and a progress counter that skips them would
            # never reach the family's total.
            try:
                epoch = _claim(epochs, dig)
                if epoch is None:
                    cat.unclaimed_digs.setdefault(family, []).append(dig)
                    continue
                # M8: a dig can already be a member (reached as a child of
                # another dig, or matched by two family globs -- dig_datasets
                # globs `.{family}%`, so a family name that prefixes another
                # returns both). Re-creating it would discard the parents the
                # first object accumulated, exactly as `_walk_down` avoids by
                # looking the child up first.
                m = cat.members.get(dig)
                if m is None:
                    m = _make_member(dig, n, epoch, cat)
                elif m.epoch != epoch:
                    # NEW-3: M8's guard (keep the object, keep its accumulated
                    # parents) also changed WHICH epoch wins — a dig already
                    # reached as another dig's child kept the epoch
                    # _walk_down inherited from its parent. For a dig the root
                    # claim is the authoritative answer; that is what roots are
                    # for. Fix it and report: members below it still carry the
                    # inherited epoch, and losing a frozen epoch's hold is a
                    # false DELETE.
                    cat.epoch_conflicts.append(
                        f'dig {dig} was walked in as a member of epoch {m.epoch} but its own root '
                        f'claim is epoch {epoch}; the root claim wins, and datasets below it may '
                        f'still carry {m.epoch}')
                    m.epoch = epoch
                if m is None:
                    continue
                _walk_down(m, msource, cat)
            finally:
                progress.dig(len(cat.members))
    progress.finish(len(cat.members))
    cat.foreign |= getattr(msource, 'foreign', set())
    _apply_pins(cat)
    return cat


def _apply_pins(cat: Catalog):
    """Stamp `exclude`/`hold` on the member each pin names, and report
    every pin of any kind that lands nowhere.

    A pin resolved with `cat.members.get()` alone: a miss goes to
    `cat.pin_problems`, which `retire()` refuses to run past. A typo'd
    dataset name in a `hold` reading as "no protection requested" is
    exactly the failure mode that refusal exists to catch.

    A `not_expected` pin naming a desc no dig of the epoch has is the
    same failure with a quieter symptom (NEW-4): it suppresses no gap
    row, and the gap it was written to explain comes back unexplained.
    It is reported here; the `order` pins are checked in
    `status._winners`, which is where the groups exist. A pin that
    matches nothing is never "no pin"; it is a broken instruction.
    """
    for e in sorted(cat.epochs.values(), key=lambda e: e.name):
        for kind, attr in (('exclude', 'excluded'), ('hold', 'hold')):
            for pin in e.pins[kind]:
                ds = pin['dataset']
                m = cat.members.get(ds)
                if m is None:
                    cat.pin_problems.append(
                        f'{kind} pin in epoch {e.name} names {ds}, which is not a member of '
                        f'the catalog')
                    continue
                if m.epoch != e.name:
                    cat.pin_problems.append(
                        f'{kind} pin in epoch {e.name} names {ds}, a member of epoch '
                        f'{m.epoch}: not applied')
                    continue
                setattr(m, attr, pin['reason'])
        for pin in e.pins['not_expected']:
            if not any(m.epoch == e.name and m.tier == 'dig' and m.desc == pin['desc']
                       for m in cat.members.values()):
                cat.pin_problems.append(
                    f"not_expected pin in epoch {e.name} names desc {pin['desc']!r}, which no "
                    f'dig of that epoch has: not applied')
        if e.status == 'frozen':
            for m in cat.members.values():
                if m.epoch == e.name and not m.hold:
                    m.hold = f'epoch {e.name} is frozen'
