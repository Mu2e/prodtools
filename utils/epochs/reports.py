"""Derived products: gaps, retire list (Ray's purge_proposal format), lookup.

Decisions 11, 12, 13. Nothing here reads a clock.
"""
from typing import Dict, List, Optional, Set

from utils.epochs.dsconf import parse_dsconf
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


def _loaded_families(cat: Catalog) -> Set[str]:
    """Families that have at least one loaded epoch file."""
    return {e.family for e in cat.epochs.values()}


def _newest_epoch_keys(cat: Catalog) -> Dict[str, tuple]:
    """Newest DsconfKey.sort_key(), one per family with a loaded epoch.
    Computed once so the retire() input loop is O(inputs + epochs), not
    O(inputs * epochs)."""
    out: Dict[str, tuple] = {}
    for e in cat.epochs.values():
        key = parse_dsconf(e.name + '_x_v0_0').sort_key()
        if e.family not in out or key > out[e.family]:
            out[e.family] = key
    return out


def foreign_family_inputs(cat: Catalog) -> List[str]:
    """Inputs whose dsconf family has no loaded epoch (cat.epochs).

    These cannot be judged live-vs-retirable until their own family's
    epoch file is loaded (no epoch means no notion of "current" for
    that family) — retire() skips them rather than guess, so the CLI
    should warn about them separately instead of silently omitting
    them.
    """
    loaded_families = _loaded_families(cat)
    return sorted(name for name in cat.inputs
                  if parse_dsconf(name.split('.')[3]).family not in loaded_families)


def retire(cat: Catalog) -> List[Dict]:
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

    loaded_families = _loaded_families(cat)
    newest = _newest_epoch_keys(cat)
    live = {m.name for m in cat.members.values() if m.status in ('current', 'stale')}
    for name, rec in sorted(cat.inputs.items()):
        if any(d in live for d in rec['descendants']):
            continue
        key = parse_dsconf(name.split('.')[3])
        if key.family not in loaded_families:
            continue          # its own family isn't loaded: cannot be judged
        if key.sort_key()[0] > newest[key.family][0]:
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
