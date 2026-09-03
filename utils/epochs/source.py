"""The only module in utils/epochs that talks to SAM.

Dataset-level lineage uses SAM's `ischildof: (dh.dataset X)` and
`isparentof: (dh.dataset X)` dimensions (verified 2026-09-03: one call
returns every child/parent FILE of the dataset; we group them back to
5-field dataset names). Everything is injectable so the catalog core
is testable without the Mu2e environment.
"""
from typing import Callable, Dict, List, Optional, Set, Tuple

from utils.job_common import Mu2eName

DROP_TIERS = frozenset({'log', 'cnf', 'etc'})
OWNER = 'mu2e'


def group_files_to_datasets(filenames) -> Tuple[Dict[str, int], Set[str]]:
    """6-field file names -> {5-field dataset: count}. Tiers in DROP_TIERS
    are not members (they are not physics). Non-mu2e owners are returned
    separately so a caller can report them; they never become members.
    An unparseable name raises: SAM handed back something that is not a
    Mu2e name and we will not guess."""
    groups: Dict[str, int] = {}
    foreign: Set[str] = set()
    for fn in filenames:
        n = Mu2eName.parse(fn)          # raises ValueError on a bad name
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

    # -- discovery -------------------------------------------------------
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
        out = []
        for name in self._defs(defname=f'cnf.{OWNER}.%'):
            parts = name.split('.')
            if len(parts) == 6 and parts[5] == 'tar':
                out.append(name)
        return sorted(set(out))

    # -- lineage ---------------------------------------------------------
    def _grouped(self, query: str) -> Dict[str, int]:
        groups, foreign = group_files_to_datasets(self._list(query))
        self.foreign |= foreign
        return groups

    def children(self, dataset: str) -> Dict[str, int]:
        return self._grouped(f'ischildof: (dh.dataset {dataset})')

    def parents(self, dataset: str) -> Dict[str, int]:
        return self._grouped(f'isparentof: (dh.dataset {dataset})')

    # -- files -----------------------------------------------------------
    def nfiles(self, dataset: str) -> int:
        return self._count(f'dh.dataset {dataset}')

    def first_file(self, dataset: str) -> str:
        files = self._list(f'dh.dataset {dataset} with limit 1')
        return files[0] if files else ''

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
