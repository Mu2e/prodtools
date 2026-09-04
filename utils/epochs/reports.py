"""Derived products: gaps, retire list (Ray's purge_proposal format), lookup.

Decisions 11, 12, 13. Nothing here reads a clock.
"""
from typing import Dict, List, Optional

from collections import Counter

from utils.epochs.dsconf import parse_dsconf
from utils.epochs.epoch_files import GAP_TIERS
from utils.epochs.generation import Generation  # noqa: F401  (type only)
from utils.epochs.graph import (INCOMPLETE_CAP, INCOMPLETE_DROPPED_TIER, INCOMPLETE_FOREIGN,
                                INCOMPLETE_UNPARSEABLE, Catalog, Member, upward_edges)
from utils.epochs.status import group_keys_of, groups

EXPECTED_TIERS = GAP_TIERS
# The desc at mcs/nts is the dig desc (reco keeps the desc). A reco that
# changes desc (-KK, -CH) is a different physics line by convention.
# The tuple itself lives in epoch_files so a `not_expected` pin naming a
# tier gaps never asks about is refused when the file loads (NEW-4).


def _winner_of(cat: Catalog, m: Member) -> Optional[Member]:
    """The current-or-stale sibling that beats `m`, else None. An
    own-series member competes in several groups at once, so ask about
    all of them (`group_keys_of`) rather than its own 4-tuple, which is
    not a key when a lettered sibling exists."""
    grouped = groups(cat)
    for key in group_keys_of(grouped, m):
        for cand in grouped[key]:
            if cand.status in ('current', 'stale'):
                if cand.name != m.name:
                    return cand
                break
    return None


def _not_expected(cat: Catalog, epoch: str, desc: str, tier: str) -> bool:
    for p in cat.epochs[epoch].pins['not_expected']:
        if p['desc'] == desc and tier in p['tiers']:
            return True
    return False


def gaps(cat: Catalog) -> List[Dict]:
    out = []
    present = {}   # (family, desc, tier) -> True if any current/stale member
    for m in cat.members.values():
        # M1: a member of a RETIRED epoch is a delete candidate whatever its
        # status, so it must not count as "this tier is covered" -- otherwise
        # retiring an epoch one commit too early both proposes deleting the
        # current dataset and removes the row that would have caught it.
        if m.status in ('current', 'stale') and cat.epochs[m.epoch].status != 'retired':
            present[(m.key.family, m.desc, m.tier)] = True
    for m in sorted(cat.members.values(), key=lambda m: m.name):
        if m.tier == 'dig' and m.status in ('current', 'stale') \
                and cat.epochs[m.epoch].status == 'current':
            for tier in EXPECTED_TIERS:
                if present.get((m.key.family, m.desc, tier)):
                    continue
                if _not_expected(cat, m.epoch, m.desc, tier):
                    continue
                out.append({'kind': 'missing', 'epoch': m.epoch, 'desc': m.desc,
                            'tier': tier, 'dataset': '',
                            'note': f'{m.name} has no current {tier}'})
        # M2: wiki §4 -- a frozen epoch is "a hold on every member, nothing
        # expected to change, NO GAP REPORT". The missing branch above was
        # already gated on epoch status; the stale branch was not, and 14 of
        # the 28 gap rows in the 2026-09-03 run were stale rows inside
        # frozen epochs.
        if m.status == 'stale' and cat.epochs[m.epoch].status == 'current':
            parents = ', '.join(sorted(m.parents))
            out.append({'kind': 'stale', 'epoch': m.epoch, 'desc': m.desc, 'tier': m.tier,
                        'dataset': m.name, 'note': f'parent moved on: {parents}'})
    return out


def _member_children(cat: Catalog, name: str) -> List[str]:
    return sorted(c.name for c in cat.members.values() if name in c.parents)


def _newest_epoch_key(cat: Catalog, family: str):
    # A bare lettered epoch name (e.g. 'MDC2025au') parses directly; no
    # need to fabricate a purpose/version tail. None is legitimate here:
    # by the time retire() calls this, `family` is known to be loaded
    # (retire() refuses to run otherwise, see below), so None means the
    # family has an epoch file but that epoch happens to own no digs at
    # all — the input is then judged by its descendants alone.
    keys = [parse_dsconf(e.name) for e in cat.epochs.values() if e.family == family]
    return max((k.sort_key() for k in keys), default=None)


def incomplete_reasons(cat: Catalog) -> List[str]:
    """One human-readable line per way the catalog is known-unsafe to
    propose deletions from: a family with digs but no loaded epoch file,
    a dig matching no epoch root, a pin that could not be applied (a
    `hold` naming a dataset the catalog does not have is a protection
    the human believes they placed and does not have), or a dig whose
    root claim disagreed with the epoch it was walked in under (the
    members below it then carry an epoch nothing confirmed, and an epoch
    is what stamps a frozen hold). Empty when the catalog is complete,
    every pin landed and every dig's epoch agrees with its root.

    `retire()` refuses to run while this is non-empty (a partial catalog
    cannot tell a still-needed input from a retirable one);
    `catalog_document` publishes these lines instead of a retire list."""
    reasons = []
    for family in sorted(cat.missing_families):
        reasons.append(f"family {family} has digs but no epoch file; "
                       f"run 'epochs propose --family {family}'")
    for digs in cat.unclaimed_digs.values():
        for dig in sorted(digs):
            reasons.append(f'dig {dig} matches no epoch root')
    reasons.extend(cat.pin_problems)
    reasons.extend(cat.epoch_conflicts)
    return reasons


def truncated_inputs(cat: Catalog) -> List[str]:
    """Every input the upward walk was cut at, or that sits above such a
    node. A DIAGNOSTIC for `lookup` and `publish` only — it decides
    nothing, and it under-reports by construction (a node that was only
    ever cut, never expanded, has no record to carry the mark). What
    decides is `cat.input_graph_incomplete`; see
    `input_retirement_refusal`."""
    return sorted(n for n, rec in cat.inputs.items() if rec.get('truncated'))


_REMEDIES = {
    INCOMPLETE_CAP: 're-run without --input-depth (the default walks to closure)',
    INCOMPLETE_UNPARSEABLE: 'make the dsconf parseable, or extend the dsconf grammar '
                            '(tracked separately) so these names can be read',
    INCOMPLETE_FOREIGN: 'a lineage that runs through a dataset not owned by mu2e cannot be '
                        'walked; re-own it or accept that its region stays unread',
    INCOMPLETE_DROPPED_TIER: 'remove the tier from source.DROP_TIERS, or add it to '
                             'source.PROVENANCE_ONLY_TIERS if it really carries no lineage',
}


def input_retirement_refusal(cat: Catalog) -> str:
    """One line saying why NO input is proposed for deletion, or `''`.

    Keyed on `cat.input_graph_incomplete` and on nothing else. Five
    different things sever the upward or downward walk — a
    `--input-depth` cap, an unparseable dsconf either way, a foreign
    owner, a dropped tier that could carry physics — and until round 4
    only the cap had a flag, so the other four shortened the graph in
    silence while `retire()` judged the region beyond them on whatever
    some other dig's walk happened to record. That is the C1 / NEW-1 /
    V1 / new-C1 / new-C2 shape, five times. The judgement is therefore
    not made per input at all: a known-incomplete input graph yields no
    input retirement proposals whatsoever, exactly as an incomplete
    catalog, an unapplied pin or an epoch conflict refuses `retire()` as
    a whole.

    **This is expected to fire on today's production data** — the
    2026-09-03 pre-flight counted 30 unparseable member and input names —
    and that is the correct outcome, not a bug to design around. We
    cannot prove an input is unused while part of the graph is
    unreadable. There is deliberately no flag that bypasses it.

    The member section is unaffected: members come from the downward
    closure, and a member is judged by its own status, never by what
    reaches it."""
    reasons = cat.input_graph_incomplete
    if not reasons:
        return ''
    counts = Counter(line.split(':', 1)[0] for line in reasons)
    top = ', '.join(f'{kind} x{n}' for kind, n in counts.most_common(3))
    if len(counts) > 3:
        top += f', +{len(counts) - 3} more kinds'
    remedies = []
    for kind, _ in counts.most_common():
        remedy = _REMEDIES.get(kind)
        if kind == INCOMPLETE_CAP and cat.input_depth is not None:
            remedy = (f're-run without --input-depth (--input-depth {cat.input_depth} is in '
                      f'force; the default walks to closure)')
        if remedy and remedy not in remedies:
            remedies.append(remedy)
    return (f'input retirement refused: the input graph is known-incomplete in '
            f'{len(reasons)} place(s) [{top}], so what reaches those regions was never '
            f'asked. NO input is proposed for deletion. The MEMBER section is unaffected. '
            f'No flag bypasses this — an input cannot be shown unused while part of the '
            f'lineage is unreadable. Remedy: ' + '; '.join(remedies) + '.')


def live_datasets(cat: Catalog) -> set:
    """THE answer to "is this dataset live". Nothing else may decide it.

    A dataset is live when a `current`, `stale` or `excluded` member is
    reachable from it downward over ANY parentage edge the catalog holds.
    Computed once per catalog as reverse reachability from the live
    members over `graph.upward_edges` — the union of `cat.upward`,
    `Member.parents`, `Member.inputs`, `cat.inputs[x]['parents']` and the
    dig `descendants` sets.

    The union is the whole point (round 4, root cause A). `retire()` used
    to test `rec['descendants']`, which only ever holds the dig an upward
    walk started from; the catalog separately records `Member.inputs` —
    written by `_walk_down` precisely because a downstream member can have
    a parent no walk claimed — and that edge went nowhere. An input
    consumed by a `current` mcs below a superseded dig was therefore
    listed for DELETE while the catalog itself knew the consumer
    (verify-3 C2). That is the original C1 defect surviving in a second
    code path, and it is cap- and closure-independent, because nothing
    ever walks upward from a non-dig member. Fixing one edge set at a
    time is what produced five Criticals with one outcome; there is one
    relation now, over every edge.

    `excluded` is in the seed (NEW-6). `retire()` refuses to list an
    excluded member — we are keeping the dataset — so proposing the
    deletion of its sole upstream input in the same run is incoherent.
    Keeping the dataset means keeping what made it; that is also the
    fail-closed reading and the documented rule (CONTEXT.md, "Input").

    Pure bookkeeping, no SAM query. The `in live` guard terminates on a
    parentage cycle, which `assign_status` rejects for members but which
    an input-level cycle can still present."""
    up = upward_edges(cat)
    live: set = set()
    frontier = [m.name for m in cat.members.values()
                if m.status in ('current', 'stale', 'excluded')]
    while frontier:
        name = frontier.pop()
        if name in live:
            continue
        live.add(name)
        frontier.extend(up.get(name, ()))
    return live


def retire(cat: Catalog) -> List[Dict]:
    """Delete candidates, members first and then inputs.

    Two fail-closed refusals, neither of them per row. `retire()` raises
    on any `incomplete_reasons()` line — a family with no epoch file, an
    unclaimed dig, an unapplied pin, an epoch conflict — because a
    partial catalog cannot tell a still-needed input from a retirable
    one. And whenever `cat.input_graph_incomplete` is non-empty — a cap,
    an unparseable dsconf up or down, a foreign owner, a dropped tier
    that might carry physics — **a known-incomplete input graph yields
    no input retirement proposals at all**: the whole input section is
    dropped (see `input_retirement_refusal`), members are still
    reported.

    The per-input `truncated` marks are a diagnostic for `lookup` and
    `publish` only. They decide nothing: keying the refusal on them left
    four other ways to sever the graph unmarked, and a mark could be
    destroyed by the input->member reconciliation (I1). Liveness likewise
    has exactly one implementation, `live_datasets`, over every edge the
    catalog holds."""
    reasons = incomplete_reasons(cat)
    if reasons:
        raise ValueError('retire refused: catalog is incomplete — ' + '; '.join(reasons) +
                         "; run 'epochs propose --family <F>' and edit the file, then retry")
    out = []
    for m in sorted(cat.members.values(), key=lambda m: m.name):
        if m.hold or m.status == 'excluded':
            continue
        epoch_status = cat.epochs[m.epoch].status
        # A retired epoch's reason wins even when the member is ALSO
        # superseded by a live sibling in another epoch (decision 12: every
        # non-held member of a retired epoch is listed, unconditionally).
        if epoch_status == 'retired':
            reason = f'epoch {m.epoch} is retired'
            if m.status in ('current', 'stale'):
                # decision 12 still lists it, but the human reading the purge
                # proposal has to be told they are deleting a live answer
                reason += f' (but this is still the {m.status} member of its group)'
        elif m.status == 'superseded':
            by = _winner_of(cat, m)
            reason = f'superseded by {by.name}' if by else 'superseded'
        else:
            continue
        out.append({'dataset': m.name, 'nfiles': m.nfiles, 'kind': 'member',
                    'reason': reason, 'children': _member_children(cat, m.name)})
    if input_retirement_refusal(cat):
        return out
    live = live_datasets(cat)
    for name, rec in sorted(cat.inputs.items()):
        # ONE liveness relation, over every edge the catalog holds. Not
        # `rec['descendants']`: that set names only the dig each upward
        # walk started from, and reading it alone is what listed an input
        # of a current member for deletion (verify-3 C2).
        if name in live:
            continue
        if rec.get('hold') or rec.get('excluded'):
            continue          # held inputs are protected, exactly as held members are
        key = parse_dsconf(name.split('.')[3]).sort_key()
        newest = _newest_epoch_key(cat, parse_dsconf(name.split('.')[3]).family)
        if newest is not None and key[0] > newest[0]:
            continue          # newer letters than any epoch: awaiting its mix
        out.append({'dataset': name, 'nfiles': rec['nfiles'], 'kind': 'input',
                    'reason': 'no current, stale or excluded member reachable '
                              'over any recorded edge',
                    'children': sorted(rec['descendants'])})
    return out


def purge_lines(entries: List[Dict]) -> List[str]:
    lines = []
    for e in entries:
        has = 'YES' if e['children'] else 'NO'
        kids = ','.join(e['children']) if e['children'] else 'NONE'
        lines.append(f"DELETE - - {has} {e['nfiles']} {e['dataset']} {kids} # {e['reason']}")
    return lines


def lookup(cat: Catalog, dataset: str) -> Dict:
    m = cat.members.get(dataset)
    if m is not None:
        by = _winner_of(cat, m) if m.status == 'superseded' else None
        return {'kind': 'member', 'dataset': m.name, 'epoch': m.epoch, 'tier': m.tier,
                'desc': m.desc, 'status': m.status, 'hold': m.hold, 'excluded': m.excluded,
                'nfiles': m.nfiles, 'parents': sorted(m.parents), 'inputs': sorted(m.inputs),
                'children': _member_children(cat, m.name),
                'superseded_by': by.name if by else ''}
    rec = cat.inputs.get(dataset)
    if rec is not None:
        # M2: the per-node `truncated` flag is a diagnostic, not the rule.
        # An operator asking about an input that the WHOLE-section refusal
        # is withholding used to see `"truncated": false` and no other
        # sign; `retire`, `members`, `gaps`, `consistency` and `publish`
        # all report it and `lookup` was the one gap.
        return {'kind': 'input', 'dataset': dataset, 'nfiles': rec['nfiles'],
                'hold': rec.get('hold', ''), 'excluded': rec.get('excluded', ''),
                'truncated': bool(rec.get('truncated')),
                'input_retirement_refused': input_retirement_refusal(cat),
                'live': dataset in live_datasets(cat),
                'parents': sorted(rec['parents']), 'descendants': sorted(rec['descendants'])}
    return {'kind': 'unknown', 'dataset': dataset}


def count_warnings(cat: Catalog, ratio: float = 0.5) -> List[Dict]:
    """Decision 4: file count is the one diagnostic. For every group, if the
    winner (the current or stale member) has far fewer files than its
    largest superseded sibling, warn — the signature of a top-up
    masquerading as a version. A warning is never a status: nothing here
    touches `m.status`, and this reads no clock."""
    out = []
    for members in groups(cat).values():
        winner = next((m for m in members if m.status in ('current', 'stale')), None)
        if winner is None:
            continue
        superseded = [m for m in members if m.status == 'superseded']
        if not superseded:
            continue
        sibling = max(superseded, key=lambda m: m.nfiles)
        if winner.nfiles < ratio * sibling.nfiles:
            out.append({'dataset': winner.name, 'nfiles': winner.nfiles,
                        'sibling': sibling.name, 'sibling_nfiles': sibling.nfiles,
                        'note': f'winner has {winner.nfiles} files, superseded sibling has '
                                f'{sibling.nfiles}: top-up or in-flight remake? pin `order` '
                                f'if the older one is the real current'})
    out.sort(key=lambda x: x['dataset'])
    return out


def consistency(cat: Catalog, gens: Dict[str, Optional['Generation']]) -> List[Dict]:
    """Per (family, tier): how many current members sit on each generation,
    which descs are on the minority ones. 'unknown' = no cnf found."""
    table: Dict[tuple, Dict[str, List[str]]] = {}
    for m in cat.members.values():
        if m.status != 'current':
            continue
        g = gens.get(m.name)
        label = g.label() if g is not None else 'unknown'
        table.setdefault((m.key.family, m.tier), {}).setdefault(label, []).append(m.desc)
    out = []
    for (family, tier), by_gen in sorted(table.items()):
        biggest = max(len(v) for v in by_gen.values())
        for label, descs in sorted(by_gen.items()):
            out.append({'family': family, 'tier': tier, 'generation': label,
                        'count': len(descs), 'descs': sorted(descs),
                        'minority': len(descs) < biggest})
    return out
