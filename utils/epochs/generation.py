"""What code made a dataset (decisions 6 and 17).

Two routes to a member's cnf, tried in this order and REPORTED:
  parent — the cnf is a declared SAM parent (ADR 0003, new outputs)
  index  — data/epochs/cnf_index.json, built once from every cnf's
           tbs.outfiles templates: a template without `{desc}` claims its
           dataset; a template with `{desc}` (generic draining cnf) claims
           every dataset of that tier and dsconf that no explicit cnf
           claims.
No third route. A member with neither is reported as generation None.
"""
import hashlib
import json
import re
import tarfile
from typing import Dict, List, NamedTuple, Optional, Tuple

from utils.epochs.graph import Catalog, Member
from utils.job_common import Mu2eName

_SETUP_RE = re.compile(r'/Musings/([^/]+)/([^/]+)/setup\.sh$')
_FCL_KEYS = {
    'dbservice': re.compile(r'DbService\.version\s*:\s*"([^"]*)"'),
    'geometry': re.compile(r'GeometryService\.inputFile\s*:\s*"([^"]*)"'),
    'bfield': re.compile(r'bfgeomFile\s*:\s*"([^"]*)"'),
}
DROP_TIERS = frozenset({'log', 'cnf', 'etc'})


class Generation(NamedTuple):
    musing: str
    version: str
    setup: str
    fcl_sha256: str
    dbservice: str
    geometry: str
    bfield: str
    source: str
    cnf: str

    def label(self) -> str:
        return f'{self.musing}/{self.version}'


def read_generation(cnf_path: str, cnf_name: str, source_kind: str) -> Generation:
    """Build a Generation from one cnf tarball. `cnf_path` must already be
    known-readable (see build_cnf_index's per-cnf isolation) — a corrupt
    tarball or a missing jobpars.json is not handled here.

    A setup path that is not a Musings setup.sh always raises ValueError
    (no generation is guessable without it). A missing `mu2e.fcl` member
    is a DIFFERENT, documented cnf shape (g4bl and code-tarball cnfs ship
    no embedded fcl at all — see Mu2eJobPars.recipe()), not an error:
    fcl_sha256 and the three fcl-derived fields come back '' rather than
    raising."""
    from utils.jobquery import Mu2eJobPars
    jp = Mu2eJobPars(cnf_path)
    setup = jp.setup()
    m = _SETUP_RE.search(setup or '')
    if not m:
        raise ValueError(f'{cnf_name}: setup path is not a Musings setup.sh: {setup!r}')
    try:
        fcl = jp._extract_member('mu2e.fcl').decode('utf-8', 'replace')
    except ValueError:
        fcl = None
    if fcl is None:
        fcl_sha256 = ''
        vals = {k: '' for k in _FCL_KEYS}
    else:
        fcl_sha256 = hashlib.sha256(fcl.encode()).hexdigest()
        vals = {k: (rx.search(fcl).group(1) if rx.search(fcl) else '') for k, rx in _FCL_KEYS.items()}
    return Generation(musing=m.group(1), version=m.group(2), setup=setup,
                      fcl_sha256=fcl_sha256,
                      dbservice=vals['dbservice'], geometry=vals['geometry'],
                      bfield=vals['bfield'], source=source_kind, cnf=cnf_name)


def _empty_index() -> Dict:
    return {'__indexed__': [], '__generic__': {}, '__unlocatable__': [], '__unreadable__': {}}


def output_claims(jobname: str, outfiles: Dict[str, str]):
    """(explicit datasets, generic keys) from tbs.outfiles templates.
    Templates are read raw — `job_outputs()` needs a sequencer, which a
    generic cnf cannot compute. `.owner.` and `.version.` are substituted
    from the cnf name, as job_outputs does."""
    n = Mu2eName.parse(jobname)
    explicit, generic = [], []
    for template in outfiles.values():
        t = template.replace('.owner.', f'.{n.owner}.').replace('.version.', f'.{n.dsconf}.')
        parts = t.split('.')
        if len(parts) != 6 or parts[0] in DROP_TIERS:
            continue           # /dev/null, relative paths, logs
        tier, owner, desc, dsconf, _seq, ext = parts
        if '{desc}' in desc:
            generic.append(f'{tier}.{dsconf}')
        else:
            explicit.append(f'{tier}.{owner}.{desc}.{dsconf}.{ext}')
    return explicit, generic


def build_cnf_index(source, existing: Dict) -> Dict:
    """dataset -> cnf name, from every cnf SAM knows (~850 and counting).
    Idempotent: cnfs in existing['__indexed__'] are not re-read. Explicit
    claims are top-level keys; generic claims live under
    '__generic__'[f'{tier}.{dsconf}'].

    A cnf SAM cannot locate lands in '__unlocatable__'. A cnf that DOES
    locate but is corrupt, has no jobpars.json, or holds unparseable JSON
    is isolated per-cnf: it lands in '__unreadable__' as
    {cnf_name: error_text} and is NOT added to '__indexed__', so a later
    run retries it automatically. One bad tarball must never abort the
    whole index build — nothing here is silently skipped; a later task's
    CLI prints '__unreadable__' and '__unlocatable__'."""
    from utils.jobquery import Mu2eJobPars
    idx = _empty_index()
    idx.update(existing or {})
    done = set(idx['__indexed__'])
    unloc = set(idx['__unlocatable__'])
    unread = dict(idx['__unreadable__'])
    for cnf in source.cnf_names():
        if cnf in done:
            continue
        path = source.local_path(cnf)
        if not path:
            unloc.add(cnf)
            continue
        try:
            jp = Mu2eJobPars(path)
            explicit, generic = output_claims(cnf, jp.json_data.get('tbs', {}).get('outfiles', {}) or {})
        except (tarfile.ReadError, OSError, ValueError, json.JSONDecodeError) as exc:
            unread[cnf] = str(exc)
            continue
        for ds in explicit:
            idx[ds] = cnf
        for key in generic:
            idx['__generic__'][key] = cnf
        done.add(cnf)
        unread.pop(cnf, None)
    idx['__indexed__'] = sorted(done)
    idx['__unlocatable__'] = sorted(unloc - done)
    idx['__unreadable__'] = unread
    return idx


def cnf_for(m: Member, cat: Catalog, index: Dict) -> Optional[Tuple[str, str]]:
    for p in sorted(m.inputs):
        if p.startswith('cnf.'):
            return p, 'parent'
    if m.name in index:
        return index[m.name], 'index'
    generic = index.get('__generic__', {}).get(f'{m.tier}.{m.dsconf}')
    if generic:
        return generic, 'index'
    return None


def generations(cat: Catalog, source, index: Dict) -> Dict[str, Optional[Generation]]:
    """A cnf is read at most once (`cache`), but `.source` reflects the
    ROUTE the current member took (parent vs index), not whichever
    member happened to populate the cache first — two members can reach
    the same cnf by different routes."""
    out: Dict[str, Optional[Generation]] = {}
    cache: Dict[str, Generation] = {}
    for m in cat.members.values():
        if m.status not in ('current', 'stale'):
            continue
        found = cnf_for(m, cat, index)
        if found is None:
            out[m.name] = None
            continue
        cnf, kind = found
        if cnf not in cache:
            path = source.local_path(cnf)
            if not path:
                out[m.name] = None
                continue
            cache[cnf] = read_generation(path, cnf, kind)
        out[m.name] = cache[cnf]._replace(source=kind)
    return out
