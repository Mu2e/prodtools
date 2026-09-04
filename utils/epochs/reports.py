"""Derived products: gaps, retire list (Ray's purge_proposal format), lookup.

Decisions 11, 12, 13. Nothing here reads a clock.
"""
from typing import Dict, List, Optional

from utils.epochs.dsconf import parse_dsconf
from utils.epochs.generation import Generation  # noqa: F401  (type only)
from utils.epochs.graph import Catalog, Member
from utils.epochs.status import group_key, groups

EXPECTED_TIERS = ('mcs', 'nts')
# The desc at mcs/nts is the dig desc (reco keeps the desc). A reco that
# changes desc (-KK, -CH) is a different physics line by convention.


def _winner_of(cat: Catalog, m: Member) -> Optional[Member]:
    """The current-or-stale sibling that beats `m`, else None."""
    for cand in groups(cat).get(group_key(m), []):
        if cand.status in ('current', 'stale'):
            return cand if cand.name != m.name else None
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
        if m.status in ('current', 'stale'):
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
        if m.status == 'stale':
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
    """One human-readable line per way the catalog is known-incomplete: a
    family with digs but no loaded epoch file, or a dig matching no
    epoch root. Empty when the catalog is complete. `retire()` refuses
    to run while this is non-empty (a partial catalog cannot tell a
    still-needed input from a retirable one); `catalog_document`
    (task 8) publishes these lines instead of a retire list."""
    reasons = []
    for family in sorted(cat.missing_families):
        reasons.append(f"family {family} has digs but no epoch file; "
                       f"run 'epochs propose --family {family}'")
    for digs in cat.unclaimed_digs.values():
        for dig in sorted(digs):
            reasons.append(f'dig {dig} matches no epoch root')
    return reasons


def retire(cat: Catalog) -> List[Dict]:
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
        elif m.status == 'superseded':
            by = _winner_of(cat, m)
            reason = f'superseded by {by.name}' if by else 'superseded'
        else:
            continue
        out.append({'dataset': m.name, 'nfiles': m.nfiles, 'kind': 'member',
                    'reason': reason, 'children': _member_children(cat, m.name)})
    live = {m.name for m in cat.members.values() if m.status in ('current', 'stale')}
    for name, rec in sorted(cat.inputs.items()):
        if any(d in live for d in rec['descendants']):
            continue
        key = parse_dsconf(name.split('.')[3]).sort_key()
        newest = _newest_epoch_key(cat, parse_dsconf(name.split('.')[3]).family)
        if newest is not None and key[0] > newest[0]:
            continue          # newer letters than any epoch: awaiting its mix
        out.append({'dataset': name, 'nfiles': rec['nfiles'], 'kind': 'input',
                    'reason': 'no current or stale descendant',
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
        return {'kind': 'input', 'dataset': dataset, 'nfiles': rec['nfiles'],
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
