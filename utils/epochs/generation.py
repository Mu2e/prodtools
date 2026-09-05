"""What code made a dataset (decisions 6 and 17).

Two routes to a member's cnf, tried in this order and REPORTED:
  parent — the cnf is a declared SAM parent (ADR 0003, new outputs)
  index  — data/epochs/cnf_index.json, built once from every cnf's
           tbs.outfiles templates: a template without `{desc}` claims its
           dataset; a template with `{desc}` (generic draining cnf) claims
           every dataset of that tier and dsconf that no explicit cnf
           claims.
No third route. A member with neither is reported as generation None.

Generation is only attempted for `GENERATION_FAMILIES`. Every failure
inside that scope is recorded on the Catalog and reported; nothing is
silently blank.
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

# Both routes to a generation read a jobdef TARBALL. MDC2020-era cnfs are
# per-job `.fcl` files that predate the jobdef tarball, so no generation is
# derivable for that family however the cnf is found -- `read_generation`
# would hand the .fcl straight to `tarfile.open`. Measured 2026-09-04 over
# the full catalog: 271 of 272 unresolved members were MDC2020, and all
# five unreadable cnfs were MDC2020. Members outside this set get a blank
# generation reported ONCE per family, not once per dataset, so that a
# blank inside the set stays a real signal. Adding a future era is one
# entry here.
GENERATION_FAMILIES = frozenset({'MDC2025', 'Run1B'})


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
    return {'__indexed__': [], '__generic__': {}, '__unlocatable__': [], '__unreadable__': {},
            '__conflicts__': {}}


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
    whole index build — nothing here is silently skipped; the CLI prints
    '__unreadable__' and '__unlocatable__'.

    Two cnfs declaring the same output is a real anomaly, not something
    to settle by sort order (M4): `cnf_names()` is sorted, so the plain
    assignment used to hand the dataset to the lexicographically LAST
    claimant and say nothing. The FIRST claim is kept and every later
    one is recorded under '__conflicts__' for the CLI to print."""
    from utils.jobquery import Mu2eJobPars
    idx = _empty_index()
    idx.update(existing or {})
    done = set(idx['__indexed__'])
    unloc = set(idx['__unlocatable__'])
    unread = dict(idx['__unreadable__'])
    conflicts = dict(idx.get('__conflicts__') or {})
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
            if ds in idx and idx[ds] != cnf:
                conflicts.setdefault(ds, [idx[ds]]).append(cnf)
                continue           # first claim stands; the collision is reported
            idx[ds] = cnf
        for key in generic:
            if key in idx['__generic__'] and idx['__generic__'][key] != cnf:
                conflicts.setdefault(f'__generic__:{key}', [idx['__generic__'][key]]).append(cnf)
                continue
            idx['__generic__'][key] = cnf
        done.add(cnf)
        unread.pop(cnf, None)
    idx['__indexed__'] = sorted(done)
    idx['__unlocatable__'] = sorted(unloc - done)
    idx['__unreadable__'] = unread
    idx['__conflicts__'] = conflicts
    return idx


def cnf_for(m: Member, cat: Catalog, index: Dict) -> Optional[Tuple[str, str]]:
    """The declared SAM parent wins over the index (ADR 0003). This route
    is sparse but NOT empty: measured 2026-09-04 over the full catalog,
    12 of 762 live members resolved through a declared cnf parent and 478
    through the index. `runmu2e` does not yet append the cnf to
    `parents_list.txt`, so it does not light up for every new output; it
    fires only where parentage was declared by some other path. A cnf
    never becomes a member, an input or a retire candidate either way.

    Two declared cnf parents — a campaign cnf plus the recovery cnf built
    on a newer Musing, the normal shape once ADR 0003 lands — is the
    mixed-generation condition `consistency` exists to detect, so it is
    NOT settled by sort order (NEW-2, the ruling M4 already made for
    `build_cnf_index.__conflicts__`). The first claim in sort order
    stands and the collision is recorded in `cat.generation_conflicts`,
    which the CLI prints."""
    if len(m.cnf_parents) > 1:
        kept = sorted(m.cnf_parents)
        line = (f'{m.name} declares {len(kept)} cnf parents: kept {kept[0]}, also claimed by '
                f'{", ".join(kept[1:])} (its generation is reported from {kept[0]} alone)')
        if line not in cat.generation_conflicts:
            cat.generation_conflicts.append(line)
    for p in sorted(m.cnf_parents):
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
    the same cnf by different routes.

    Members outside `GENERATION_FAMILIES` are skipped before any lookup:
    their family is recorded once and their generation is blank by
    design, not by failure.

    In scope, every way of arriving at no generation is recorded on the
    catalog under its own reason — unresolved (no cnf claims it),
    unlocatable (SAM gives no location) or unreadable (the cnf is there
    but will not open). The read is guarded per cnf exactly as
    `build_cnf_index` guards its own: a dCache denial mid-stream, a
    corrupt tarball or a vanished scratch file must degrade one member's
    generation, never abort `consistency` or `publish` for the whole
    catalog. Re-entrant: the record fields are sets/dicts, so calling
    this twice on one catalog reports each fault once."""
    out: Dict[str, Optional[Generation]] = {}
    cache: Dict[str, Generation] = {}
    for m in cat.members.values():
        if m.status not in ('current', 'stale'):
            continue
        if m.key.family not in GENERATION_FAMILIES:
            cat.generation_out_of_scope.add(m.key.family)
            out[m.name] = None
            continue
        found = cnf_for(m, cat, index)
        if found is None:
            cat.generation_unresolved.add(m.name)
            out[m.name] = None
            continue
        cnf, kind = found
        if cnf in cache:
            out[m.name] = cache[cnf]._replace(source=kind)
            continue
        if cnf in cat.generation_unreadable or cnf in cat.generation_unlocatable:
            out[m.name] = None          # already diagnosed; do not re-probe
            continue
        path = source.local_path(cnf)
        if not path:
            cat.generation_unlocatable.add(cnf)
            out[m.name] = None
            continue
        try:
            cache[cnf] = read_generation(path, cnf, kind)
        except (tarfile.ReadError, OSError, ValueError, json.JSONDecodeError) as exc:
            cat.generation_unreadable[cnf] = f'{type(exc).__name__}: {exc}'
            out[m.name] = None
            continue
        out[m.name] = cache[cnf]._replace(source=kind)
    return out
