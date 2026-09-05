"""Derived products: gaps, retire list (Ray's purge_proposal format), lookup.

Decisions 11, 12, 13. Nothing here reads a clock.

Input retirement (proposing deletion of upstream datasets once nothing
live descends from them) is NOT modeled here; see CONTEXT.md, "Input",
and `docs/adr/0005-input-retirement-proves-deadness-positively.md`.
`retire()` below reports member candidates only.
"""
from typing import Dict, List, Optional

from utils.epochs.epoch_files import GAP_TIERS
from utils.epochs.generation import Generation  # noqa: F401  (type only)
from utils.epochs.graph import Catalog, Member
from utils.epochs.status import GroupKey, group_keys_of, groups

Grouped = Dict[GroupKey, List[Member]]

EXPECTED_TIERS = GAP_TIERS
# The desc at mcs/nts is the dig desc (reco keeps the desc). A reco that
# changes desc (-KK, -CH) is a different physics line by convention.
# The tuple itself lives in epoch_files so a `not_expected` pin naming a
# tier gaps never asks about is refused when the file loads (NEW-4).


def _winner_of(cat: Catalog, m: Member, grouped: Grouped) -> Optional[Member]:
    """The current-or-stale sibling that beats `m`, else None. An
    own-series member competes in several groups at once, so ask about
    all of them (`group_keys_of`) rather than its own 4-tuple, which is
    not a key when a lettered sibling exists.

    `grouped` is passed in, never recomputed here: this is called once
    per member by `retire` and once per member by `publish`, and
    `groups(cat)` walks and sorts the entire catalog, so computing it
    here made a ~1000-member run regroup ~1000 times (1513 regroups in
    `catalog_document`, 756 in `retire`, measured 2026-09-04). It is a
    required argument rather than an optional one so a new call site
    cannot quietly reintroduce the loop.

    `groups(cat)` is a pure function of the member set and the
    `excluded` flags, both settled by the time `build_catalog` returns,
    so one value is good for every member of one catalog. It does NOT
    depend on `status`, which `assign_status` fills in afterwards — and
    the Member objects inside `grouped` are the live ones, so the
    `cand.status` read below always sees the current status."""
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
    cannot judge a superseded member against an epoch nothing confirmed);
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


def retire(cat: Catalog) -> List[Dict]:
    """Delete candidates: superseded-and-not-held members, plus every
    non-held member of a `retired` epoch.

    One fail-closed refusal. `retire()` raises on any
    `incomplete_reasons()` line — a family with no epoch file, an
    unclaimed dig, an unapplied pin, an epoch conflict — because a
    partial catalog cannot judge a superseded member against an epoch
    nothing confirmed.

    Input retirement (upstream datasets: stops catalogues, pileup, `dts`
    primaries) is not modeled in this version; see CONTEXT.md, "Input",
    and `docs/adr/0005-input-retirement-proves-deadness-positively.md`."""
    reasons = incomplete_reasons(cat)
    if reasons:
        raise ValueError('retire refused: catalog is incomplete — ' + '; '.join(reasons) +
                         "; run 'epochs propose --family <F>' and edit the file, then retry")
    grouped = groups(cat)
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
            by = _winner_of(cat, m, grouped)
            reason = f'superseded by {by.name}' if by else 'superseded'
        else:
            continue
        out.append({'dataset': m.name, 'nfiles': m.nfiles, 'kind': 'member',
                    'reason': reason, 'children': _member_children(cat, m.name)})
    return out


def purge_lines(entries: List[Dict]) -> List[str]:
    lines = []
    for e in entries:
        has = 'YES' if e['children'] else 'NO'
        kids = ','.join(e['children']) if e['children'] else 'NONE'
        lines.append(f"DELETE - - {has} {e['nfiles']} {e['dataset']} {kids} # {e['reason']}")
    return lines


def lookup(cat: Catalog, dataset: str, grouped: Optional[Grouped] = None) -> Dict:
    """`'unknown'` covers both a dataset the catalog never heard of and
    one it saw only as an upstream, non-member parent — input retirement
    is not modeled in this version (CONTEXT.md, "Input"), so no separate
    record exists for the latter.

    `grouped` is optional here because `cli.cmd_lookup` asks about ONE
    dataset and a single grouping is the whole cost; `catalog_document`,
    which calls this once per member, passes its own."""
    m = cat.members.get(dataset)
    if m is not None:
        if grouped is None:
            grouped = groups(cat)
        by = _winner_of(cat, m, grouped) if m.status == 'superseded' else None
        return {'kind': 'member', 'dataset': m.name, 'epoch': m.epoch, 'tier': m.tier,
                'desc': m.desc, 'status': m.status, 'hold': m.hold, 'excluded': m.excluded,
                'nfiles': m.nfiles, 'parents': sorted(m.parents),
                'children': _member_children(cat, m.name),
                'superseded_by': by.name if by else ''}
    return {'kind': 'unknown', 'dataset': dataset}


def count_warnings(cat: Catalog, ratio: float = 0.5,
                   grouped: Optional[Grouped] = None) -> List[Dict]:
    """Decision 4: file count is the one diagnostic. For every group, if the
    winner (the current or stale member) has far fewer files than its
    largest superseded sibling, warn — the signature of a top-up
    masquerading as a version. A warning is never a status: nothing here
    touches `m.status`, and this reads no clock."""
    out = []
    for members in (groups(cat) if grouped is None else grouped).values():
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
    which descs are on the minority ones.

    Two different blanks, kept apart on purpose: 'not evaluated' is a
    family outside `generation.GENERATION_FAMILIES`, where no generation
    was ever attempted and none is derivable; 'unknown' is an IN-SCOPE
    member whose cnf was looked for and not found, or found and not read.
    Collapsing them would hide a real fault inside a legacy era's rows —
    `cat.generation_*` carries the reason for each."""
    table: Dict[tuple, Dict[str, List[str]]] = {}
    for m in cat.members.values():
        if m.status != 'current':
            continue
        g = gens.get(m.name)
        if g is not None:
            label = g.label()
        elif m.key.family in cat.generation_out_of_scope:
            label = 'not evaluated'
        else:
            label = 'unknown'
        table.setdefault((m.key.family, m.tier), {}).setdefault(label, []).append(m.desc)
    out = []
    for (family, tier), by_gen in sorted(table.items()):
        biggest = max(len(v) for v in by_gen.values())
        for label, descs in sorted(by_gen.items()):
            out.append({'family': family, 'tier': tier, 'generation': label,
                        'count': len(descs), 'descs': sorted(descs),
                        'minority': len(descs) < biggest})
    return out
