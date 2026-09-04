"""The only module in utils/epochs that talks to SAM.

Dataset-level lineage uses SAM's `ischildof: (dh.dataset X)` and
`isparentof: (dh.dataset X)` dimensions (verified 2026-09-03: one call
returns every child/parent FILE of the dataset; we group them back to
5-field dataset names). Everything is injectable so the catalog core
is testable without the Mu2e environment.
"""
from typing import Callable, Dict, List, Optional, Set, Tuple

from utils.epochs.dsconf import DsconfParseError, parse_dsconf
from utils.job_common import Mu2eName

DROP_TIERS = frozenset({'log', 'cnf', 'etc'})
# The one tier the PARENTS path keeps despite DROP_TIERS: ADR 0003 makes
# the cnf a declared SAM parent of every output, and that declaration is
# the authoritative answer to "what code made this dataset". It is never
# a member, never an input and never a retire candidate — it is recorded
# on `Member.cnf_parents` and read only by `generation.cnf_for`.
PARENT_KEEP_TIERS = frozenset({'cnf'})
OWNER = 'mu2e'


def group_files_to_datasets(filenames, keep_tiers=frozenset()) -> Tuple[Dict[str, int], Set[str]]:
    """6-field file names -> {5-field dataset: count}. Tiers in DROP_TIERS
    are not members (they are not physics). Non-mu2e owners are returned
    separately so a caller can report them; they never become members.
    An unparseable name raises: SAM handed back something that is not a
    Mu2e name and we will not guess.

    `keep_tiers` names DROP_TIERS entries to retain anyway, keyed by the
    6-FIELD FILE name rather than the 5-field dataset. That is deliberate
    for the only current user, `cnf`: a cnf is identified by its tarball
    (`cnf.mu2e.<desc>.<dsconf>.<index>.tar`), which is what `local_path`
    and `jobquery` read; the 5-field dataset name would be useless to
    both."""
    groups: Dict[str, int] = {}
    foreign: Set[str] = set()
    for fn in filenames:
        n = Mu2eName.parse(fn)          # raises ValueError on a bad name
        if n.tier in keep_tiers:
            if n.owner != OWNER:
                foreign.add(str(n.dataset))
                continue
            groups[fn] = groups.get(fn, 0) + 1
            continue
        if n.tier in DROP_TIERS:
            continue
        ds = str(n.dataset)
        if n.owner != OWNER:
            foreign.add(ds)
            continue
        groups[ds] = groups.get(ds, 0) + 1
    return groups, foreign


def _default_list_files(q):
    from utils.samweb_wrapper import list_files
    return list_files(q)


def _default_count_files(q):
    from utils.samweb_wrapper import count_files
    return count_files(q)


def _default_definitions(defname=None, user=None):
    from utils.samweb_wrapper import definitions_matching
    return definitions_matching(defname=defname, user=user)


def _default_locate(fn):
    from utils.samweb_wrapper import locate_file
    return locate_file(fn)


class SamSource:
    def __init__(self, list_files_fn: Optional[Callable] = None,
                 count_files_fn: Optional[Callable] = None,
                 definitions_fn: Optional[Callable] = None,
                 locate_fn: Optional[Callable] = None):
        self._list = list_files_fn or _default_list_files
        self._count = count_files_fn or _default_count_files
        self._defs = definitions_fn or _default_definitions
        self._locate = locate_fn or _default_locate
        self.foreign: Set[str] = set()
        self.unparseable_defs: List[str] = []

    # -- discovery -------------------------------------------------------
    def dig_families(self) -> Set[str]:
        """Every family with at least one dig.mu2e.*.art definition in SAM
        — discovered, not assumed from a caller's --family list, so the
        catalog can tell when a family with real digs has no epoch file
        loaded (decision: retire() refuses on an incomplete catalog
        rather than silently judging inputs of a family it never
        loaded). A definition whose dsconf does not parse is recorded in
        `self.unparseable_defs`, never silently dropped and never
        guessed into a family."""
        out: Set[str] = set()
        for name in self._defs(defname=f'dig.{OWNER}.%.art'):
            parts = name.split('.')
            if len(parts) != 5 or parts[4] != 'art' or parts[1] != OWNER:
                continue
            try:
                out.add(parse_dsconf(parts[3]).family)
            except DsconfParseError:
                self.unparseable_defs.append(name)
        return out

    def dig_datasets(self, family: str) -> Dict[str, int]:
        """5-field dig.mu2e.*.<family>*.art datasets with at least one file.
        Definitions are only a candidate list: existence is a file count."""
        out = {}
        for name in self._defs(defname=f'dig.{OWNER}.%.{family}%.art'):
            parts = name.split('.')
            if len(parts) != 5 or parts[4] != 'art' or parts[1] != OWNER:
                continue
            n = self._count(f'dh.dataset {name}')
            if n > 0:
                out[name] = n
        return out

    def cnf_names(self) -> List[str]:
        """6-field cnf tarball FILE names (`cnf.mu2e.<desc>.<dsconf>.<index>.tar`).

        SAM cnf DEFINITIONS are 5-field dataset names
        (`cnf.mu2e.<desc>.<dsconf>.tar`) and would never match a
        6-field filter -- `definitions_matching` is the wrong query
        here. The tarball files are what the index reads, so we list
        them directly; the `%.tar` tail excludes the per-job `.fcl`
        files that also live under the cnf tier."""
        out = []
        for name in self._list(f"dh.dataset like 'cnf.{OWNER}.%.tar'"):
            parts = name.split('.')
            if len(parts) == 6 and parts[5] == 'tar':
                out.append(name)
        return sorted(set(out))

    # -- lineage ---------------------------------------------------------
    def _grouped(self, query: str, keep_tiers=frozenset()) -> Dict[str, int]:
        groups, foreign = group_files_to_datasets(self._list(query), keep_tiers=keep_tiers)
        self.foreign |= foreign
        return groups

    def children(self, dataset: str) -> Dict[str, int]:
        return self._grouped(f'ischildof: (dh.dataset {dataset})')

    def parents(self, dataset: str) -> Dict[str, int]:
        """Parents keep the cnf tarball (PARENT_KEEP_TIERS) so ADR 0003's
        declared-parent route to a dataset's generation is reachable; the
        cnf comes back under its 6-field FILE name, everything else under
        its 5-field dataset name."""
        return self._grouped(f'isparentof: (dh.dataset {dataset})',
                             keep_tiers=PARENT_KEEP_TIERS)

    # -- files -----------------------------------------------------------
    def local_path(self, filename: str) -> str:
        """/pnfs path of a SAM file for direct reading on a gpvm; '' if
        SAM does not know the file. Only used for cnf tarballs.

        `locate_file` returns a location record dict (e.g.
        {'full_path': 'dcache:/pnfs/...', 'location_type': 'disk', ...})
        or '' when SAM has no location for the file. We hand the record
        to `path_from_sam_location`, the single home of the
        locate -> full_path -> cleanup grammar (storage-prefix strip,
        trailing '(pool@node)' strip, filename append), so a malformed
        record raises ValueError there rather than being parsed twice."""
        loc = self._locate(filename)
        if not loc:
            return ''
        from utils.file_resolver import path_from_sam_location
        return path_from_sam_location(filename, loc)
