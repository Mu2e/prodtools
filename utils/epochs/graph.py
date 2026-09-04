"""Members and inputs of every epoch, from SAM parentage.

Members = closure of the roots walking children (decision 2, rule 1).
Inputs = everything reached walking parents from a dig root -- by
default all the way to the top of the real DAG, `input_depth=None`
(decision 2026-09-04). A dataset belongs to exactly one epoch: the one
whose root its dig matched.

`input_depth` survives as an explicit opt-in cap for a cheap partial
run. It is no longer the default because a capped walk yields a
knowingly incomplete input graph, and three rounds of trying to DETECT
which parts of an incomplete graph are safe to act on produced three
separate false-DELETE Criticals (C1, NEW-1, V1). Walking to closure
removes the cut, so there is nothing to detect; when a cap IS given,
`retire()` refuses the input section whole rather than judging it
per input.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from utils.epochs.dsconf import DsconfKey, DsconfParseError, parse_dsconf
from utils.epochs.epoch_files import EpochFile
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
    inputs: Set[str] = field(default_factory=set)
    # Declared cnf tarball parents (ADR 0003). Kept OUT of `inputs` and out
    # of `cat.inputs` on purpose: a cnf is provenance, not data, and must
    # never become a retire candidate. Read only by generation.cnf_for.
    cnf_parents: Set[str] = field(default_factory=set)
    status: str = ''
    hold: str = ''
    excluded: str = ''


@dataclass
class Catalog:
    epochs: Dict[str, EpochFile]
    # The `input_depth` this catalog was built with: None = the upward
    # walk ran to natural closure, so no input is truncated and the
    # input graph is complete. An int is an operator-set cap, and
    # `reports.retire()` then refuses the ENTIRE input section as soon
    # as anything was truncated.
    input_depth: Optional[int] = None
    members: Dict[str, Member] = field(default_factory=dict)
    inputs: Dict[str, Dict] = field(default_factory=dict)
    unparseable: List[Tuple[str, str]] = field(default_factory=list)
    foreign: Set[str] = field(default_factory=set)
    unclaimed_digs: Dict[str, List[str]] = field(default_factory=dict)
    # Families with at least one dig in SAM but no loaded EpochFile for
    # that family (decision 2026-09-03: retire() refuses to run while
    # this is non-empty — a partial catalog cannot tell a live input
    # from a retirable one).
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


class _Memo:
    """Per-build cache for the two lineage query methods only.

    A stops catalogue shared by hundreds of dts datasets, or a stage-1
    sim dataset shared by many descendants, would otherwise be asked
    via `children()`/`parents()` once per referencing walk — that
    multiplicity, not any single query's latency (every SAM query is
    sub-second on its own), is what kept a live `bin/epochs members`
    run from finishing in 10 minutes. Caching by name bounds each of
    the two methods to one real call per distinct dataset for the life
    of one `build_catalog` call, no matter how many members' or digs'
    walks reach it.

    `nfiles` is cached the same way for the same reason: `_walk_up`
    needs the file count of every input (task I3 — the parentage edge
    count under-reports the size of a proposed deletion), and a stops
    catalogue reached by hundreds of dig walks would otherwise cost one
    `count_files` per walk instead of one per build.

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
        self._nfiles_cache = {}  # type: Dict[str, int]

    def children(self, name):
        if name not in self._children_cache:
            self._children_cache[name] = self._source.children(name)
        return self._children_cache[name]

    def parents(self, name):
        if name not in self._parents_cache:
            self._parents_cache[name] = self._source.parents(name)
        return self._parents_cache[name]

    def nfiles(self, name):
        if name not in self._nfiles_cache:
            self._nfiles_cache[name] = self._source.nfiles(name)
        return self._nfiles_cache[name]

    def __getattr__(self, attr):
        return getattr(self._source, attr)


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
    """Depth-first (frontier.pop() is LIFO) over children. A child
    already claimed by another walk keeps its first epoch (it cannot
    happen for a well-formed SAM graph: a dig has one dsconf) but is
    still linked as a parent edge. A child already in `cat.members` is
    not re-walked — that, plus `source` being the per-build `_Memo`
    wrapper `build_catalog` passes in, is what keeps a downstream
    member shared by two different dig walks from being queried twice."""
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
                # Non-member parents of a downstream member. The cnf
                # tarball (ADR 0003's declared parent, kept by
                # SamSource.parents' PARENT_KEEP_TIERS) is recorded
                # separately and never becomes an input; anything else is
                # a physics parent this walk did not claim. One isparentof
                # query per member below dig.
                for p in source.parents(child):
                    if p.startswith('cnf.'):
                        m.cnf_parents.add(p)
                    elif p not in cat.members and p != parent.name:
                        m.inputs.add(p)
            m.parents.add(parent.name)


def _walk_up(dig: Member, source, cat: Catalog, depth: Optional[int]):
    """Depth-first (frontier.pop() is LIFO) over parents. `depth=None`
    (the default) walks to natural closure -- the top of the real DAG;
    an int caps the walk that many levels above the dig.

    A parent whose dsconf does not parse (controller ruling, task 4) is
    reported in `cat.unparseable` and dropped from this walk entirely: it
    is never recorded in `cat.inputs`, never linked as a member's `inputs`
    edge, and never walked further above. `Mu2eName.parse` itself is left
    to raise on a name that is not 5/6 dot-fields — that failure is not
    ours to swallow.

    `source` is the per-build `_Memo` wrapper `build_catalog` passes in
    (task 9b), so a parent shared by many digs' walks — a stops
    catalogue, a stage-1 sim dataset — is fetched from the real source
    at most once per build regardless of how many digs' walks reach it;
    every one of those walks still runs its own full pass over the
    (now cached) result, so `descendants` and `inputs`/`parents`
    bookkeeping is recorded for every dig exactly as before dedup.

    With no cap there is no cut and nothing is ever truncated, which is
    the point: a capped walk leaves a knowingly incomplete input graph,
    and every attempt to decide per input which parts of an incomplete
    graph are safe to delete has produced a false DELETE (C1, NEW-1,
    V1). When a cap IS given, truncation is a property of THIS WALK's
    cut, never of the node (NEW-1): `explored` used to live on the
    shared input record, so a node cut off for this dig but already
    expanded by an earlier dig's walk was left unmarked. Both sets below
    are local to one walk: a mark another walk set is never cleared
    here, `build_catalog` propagates the marks up the parent edges
    afterwards, and `retire()` refuses its whole input section while any
    mark stands.

    `explored` doubles as the per-walk visited set. Without a cap the
    old unconditional re-push would revisit a diamond's shared ancestors
    once per path (and never terminate on a parentage cycle); expanding
    each node at most once per walk costs no bookkeeping, since the
    `descendants` / `parents` edges a second visit would record were
    already recorded by the first.
    """
    explored = set()   # nodes THIS walk asked the parents of
    cut = set()        # nodes THIS walk stopped at
    frontier = [(dig.name, 0)]
    while frontier:
        child, level = frontier.pop()
        if depth is not None and level >= depth:
            # I6: the cap is per-walk. Stopping here means this dataset's
            # own parents were never asked for on this walk, so our picture
            # of the graph around it is partial -- and a partial picture is
            # exactly how an input recorded shallowly from a superseded dig
            # ends up on the retire list while a deeper live branch reaches
            # it unseen. Mark the frontier; retire() refuses to list a
            # truncated input, and raising --input-depth until the count is
            # zero is what makes the boundary a visible decision instead of
            # a silent one.
            cut.add(child)
            continue
        if child in explored:
            continue
        explored.add(child)
        for p, n in source.parents(child).items():
            if p.startswith('cnf.'):
                # provenance, not lineage: recorded on the member, never an
                # input, never walked above (a cnf has no physics parents)
                if child in cat.members:
                    cat.members[child].cnf_parents.add(p)
                continue
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
            rec = cat.inputs.get(p)
            if rec is None:
                # `n` here is the parentage EDGE count -- how many files of
                # p this child actually consumed (4907 of the 5000 in
                # dts.mu2e.MuBeamFlash.Run1Bai-003.art). The retire report's
                # nfiles column is read as the size of the deletion, so it
                # must be the DATASET's count; one memoized count_files per
                # distinct input buys that.
                rec = cat.inputs[p] = {'nfiles': source.nfiles(p), 'parents': set(),
                                       'descendants': set(), 'hold': '', 'excluded': '',
                                       'truncated': False}
            rec['descendants'].add(dig.name)
            if child in cat.inputs:
                cat.inputs[child]['parents'].add(p)
            else:
                cat.members[child].inputs.add(p)
            frontier.append((p, level + 1))
    # a node this walk stopped at AND never expanded by a shorter path of
    # its own is a real cut for this dig
    for name in cut - explored:
        rec = cat.inputs.get(name)
        if rec is not None:
            rec['truncated'] = True


def _propagate_truncation(cat: Catalog):
    """Push `truncated` UP the `cat.inputs[x]['parents']` edges to a
    fixpoint (NEW-1, second half).

    A walk cut off at `M` never asked what is above `M`, so every input
    the catalog knows above `M` -- recorded there by some OTHER dig's
    walk, which is why it is in `cat.inputs` at all -- may be reachable
    by the cut dig too. Its `descendants` set is therefore incomplete and
    its "no live descendant" reading unsafe. Over-marking is the correct
    direction: a truncated input is held back from the retire list, and
    the stderr count then genuinely means "raise --input-depth".

    Pure bookkeeping over data already in the Catalog; no SAM query."""
    frontier = [n for n, rec in cat.inputs.items() if rec['truncated']]
    while frontier:
        rec = cat.inputs.get(frontier.pop())
        if rec is None:
            continue
        for p in rec['parents']:
            up = cat.inputs.get(p)
            if up is not None and not up['truncated']:
                up['truncated'] = True
                frontier.append(p)


def build_catalog(epochs: Dict[str, EpochFile], source, families: Optional[List[str]] = None,
                  input_depth: Optional[int] = None) -> Catalog:
    """`input_depth=None` (the default since 2026-09-04) walks upward to
    natural closure: no cut, so no input is truncated and the input
    graph the retire report reasons over is the real one. Real Mu2e
    parentage chains are short (dig <- dts <- sim <- ..., about 5-6
    levels), every lineage query is memoized per build, and each node is
    expanded once per walk, so the extra cost is one `parents()` (plus
    one `nfiles()`) per distinct ancestor above the old depth-3
    frontier. That is the price of removing a class of false-deletion
    bug and is not to be traded back for speed.

    `families=None` discovers every family with a dig in SAM
    (`source.dig_families()`) instead of trusting the caller's list —
    the catalog must be complete before deletions are proposed. A
    family with digs but no loaded epoch still gets its digs walked
    into `cat.unclaimed_digs` (that is what makes the gap visible) and
    is recorded in `cat.missing_families`.

    `source` is wrapped once, here, in `_Memo`: every dig's
    `_walk_down`/`_walk_up` shares the same cache, so a dataset entered
    from many different digs (task 9b: a stops catalogue shared by
    hundreds of dts datasets) is asked at most once per build."""
    msource = _Memo(source)
    if families is None:
        families = sorted(msource.dig_families())
    cat = Catalog(epochs=dict(epochs), input_depth=input_depth)
    loaded_families = {e.family for e in epochs.values()}
    cat.missing_families = [f for f in families if f not in loaded_families]
    for family in families:
        for dig, n in sorted(msource.dig_datasets(family).items()):
            epoch = _claim(epochs, dig)
            if epoch is None:
                cat.unclaimed_digs.setdefault(family, []).append(dig)
                continue
            # M8: a dig can already be a member (reached as a child of
            # another dig, or matched by two family globs -- dig_datasets
            # globs `.{family}%`, so a family name that prefixes another
            # returns both). Re-creating it would discard the parents and
            # inputs the first object accumulated, exactly as `_walk_down`
            # avoids by looking the child up first.
            m = cat.members.get(dig)
            if m is None:
                m = _make_member(dig, n, epoch, cat)
            elif m.epoch != epoch:
                # NEW-3: M8's guard (keep the object, keep its accumulated
                # parents/inputs) also changed WHICH epoch wins — a dig
                # already reached as another dig's child kept the epoch
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
            _walk_up(m, msource, cat, input_depth)
    cat.foreign |= getattr(msource, 'foreign', set())
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
    _propagate_truncation(cat)
    _apply_pins(cat)
    return cat


def _input_of_epoch(cat: Catalog, rec: Dict, epoch: str) -> bool:
    """An input has no epoch of its own; it belongs to the epoch whose
    file pins it when at least one dig it feeds is a member of that
    epoch. Anything else is one epoch's file reaching into another
    epoch's lineage, which M7 says to report rather than apply."""
    return any(cat.members[d].epoch == epoch for d in rec['descendants'] if d in cat.members)


def _apply_pins(cat: Catalog):
    """Stamp `exclude`/`hold` on the member OR INPUT each pin names, and
    report every pin of any kind that lands nowhere.

    Before 2026-09-04 a pin was resolved with `cat.members.get()` alone
    and a miss did nothing at all, so (a) an input could not be held —
    there was no expressible way to keep a 20 000-file dts off the
    retire list — and (b) a typo'd dataset name in a `hold` read as "no
    protection requested" and was reported nowhere. Both now land in
    `cat.pin_problems`, which `retire()` refuses to run past.

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
                if m is not None:
                    if m.epoch != e.name:
                        cat.pin_problems.append(
                            f'{kind} pin in epoch {e.name} names {ds}, a member of epoch '
                            f'{m.epoch}: not applied')
                        continue
                    setattr(m, attr, pin['reason'])
                    continue
                rec = cat.inputs.get(ds)
                if rec is not None:
                    if not _input_of_epoch(cat, rec, e.name):
                        cat.pin_problems.append(
                            f'{kind} pin in epoch {e.name} names input {ds}, which feeds no '
                            f'dig of that epoch: not applied')
                        continue
                    rec[attr] = pin['reason']
                    continue
                cat.pin_problems.append(
                    f'{kind} pin in epoch {e.name} names {ds}, which is neither a member nor '
                    f'an input of the catalog')
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
            # NEW-7: freezing an epoch protects its INPUTS too. An operator
            # who freezes Run1Bah means "nothing here gets deleted", and a
            # frozen epoch's digs are usually superseded (a newer letter is
            # why it was frozen), so nothing live descends from their dts
            # and the dts was a retire candidate. The hold is stamped when
            # every dig the input feeds is a member of a frozen or retired
            # epoch and at least one of them is a member of THIS frozen
            # epoch: a descendant in a current epoch is a live line whose
            # own status decides, and an input feeding only retired epochs
            # stays retirable (decision 12).
            for rec in cat.inputs.values():
                if rec['hold']:
                    continue
                below = [cat.members[d].epoch for d in rec['descendants'] if d in cat.members]
                if len(below) != len(rec['descendants']) or e.name not in below:
                    continue
                if all(cat.epochs[x].status in ('frozen', 'retired') for x in below):
                    rec['hold'] = f'epoch {e.name} is frozen'
