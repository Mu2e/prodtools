"""bin/epochs — the sim-epochs catalog command. See EXAMPLES.md.

Every verb except `propose` loads EVERY epoch file and lets `build_catalog`
discover dig families from SAM (`families=None`), so status and retire are
always judged on the complete catalog. `--family` (repeatable) and
`--epoch` are OUTPUT filters applied to already-computed rows only — they
never change what is loaded or built (ruling of 2026-09-03: a partial
catalog proposed deleting MDC2025 stop catalogues that only unloaded
MDC2025 digs used).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from utils.epochs.dsconf import DsconfParseError, parse_dsconf
from utils.epochs.epoch_files import (EpochFileError, load_epoch_files, propose_epoch,
                                      write_epoch_file)
from utils.epochs.generation import build_cnf_index, generations
from utils.epochs.graph import build_catalog
from utils.epochs.publish import catalog_document, write_catalog
from utils.epochs.reports import (consistency, count_warnings, gaps, input_retirement_refusal,
                                  lookup, purge_lines, retire)
from utils.epochs.status import assign_status

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_EPOCHS_DIR = os.path.join(REPO, 'data', 'epochs')
INDEX_NAME = 'cnf_index.json'


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _source():
    from utils.epochs.source import SamSource
    return SamSource()


def _sam_error_class():
    """`samweb_client.Error`, the base every `utils.samweb_wrapper` method
    raises on an outage, an expired token or a malformed query (see its
    error-mode policy). It derives from neither ValueError nor OSError,
    so `except (ValueError, OSError)` let a SAM outage out as a traceback
    where `docs/EXAMPLES_schema.md` promises exit 3 (M11).

    Resolved lazily and narrowly: the import is optional so the CLI still
    runs without the Mu2e environment, and anything that is not an
    exception class degrades to an empty tuple, which catches NOTHING.
    A programming error keeps its traceback either way — this never
    blanket-catches Exception."""
    try:
        from samweb_client import Error
    except Exception:
        return ()
    if isinstance(Error, type) and issubclass(Error, BaseException):
        return Error
    return ()


def _load_index(epochs_dir) -> Dict:
    p = os.path.join(epochs_dir, INDEX_NAME)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def _catalog(args, source):
    """Load every epoch file in `args.epochs_dir` and build the COMPLETE
    catalog — families are discovered from SAM (`families=None`), never
    limited to `args.family`. Callers filter the rows they print, not
    what gets built (see module docstring)."""
    epochs = load_epoch_files(args.epochs_dir)
    if not epochs:
        raise EpochFileError(f"no epoch files in {args.epochs_dir}; "
                             f"run 'epochs propose --family F' first")
    cat = build_catalog(epochs, source, families=None, input_depth=args.input_depth)
    return assign_status(cat)


def _keep(args, family, epoch):
    """True when a printed row's family/epoch survive the --family
    (repeatable, OR-matched) and --epoch output filters. No filter given
    on either axis means everything on that axis passes."""
    if args.family and family not in args.family:
        return False
    if args.epoch is not None and epoch != args.epoch:
        return False
    return True


def _retire_keep(args, cat, entry):
    """Retire entries: a member entry is filtered by the member's own
    family/epoch (looked up in cat.members); an input entry has no epoch
    of its own, so under --epoch it is kept only when its family matches
    the NAMED epoch's family."""
    if entry['kind'] == 'member':
        m = cat.members[entry['dataset']]
        return _keep(args, m.key.family, m.epoch)
    fam = parse_dsconf(entry['dataset'].split('.')[3]).family
    if args.family and fam not in args.family:
        return False
    if args.epoch is not None:
        ep = cat.epochs.get(args.epoch)
        if ep is None or fam != ep.family:
            return False
    return True


def _emit(rows, as_json: bool, text_fn):
    if as_json:
        print(json.dumps(rows, indent=1))
    else:
        for line in text_fn(rows):
            print(line)


def _report_noise(cat, source):
    for name, err in cat.unparseable:
        print(f'unparseable: {name}: {err}', file=sys.stderr)
    for fam, digs in cat.unclaimed_digs.items():
        for d in digs:
            print(f'unclaimed dig (no epoch root matches): {d}', file=sys.stderr)
    refusal = input_retirement_refusal(cat)
    if refusal:
        # NOT "these N were withheld": a non-zero frontier count means the
        # upward picture is incomplete, and which OTHER inputs that makes
        # unsafe cannot be read off the count. The whole input section is
        # refused, and the message has to say so.
        print(refusal, file=sys.stderr)
    for line in cat.pin_problems:
        print(f'pin problem: {line}', file=sys.stderr)
    for line in cat.epoch_conflicts:
        print(f'epoch conflict: {line}', file=sys.stderr)
    for line in cat.generation_conflicts:
        print(f'generation conflict: {line}', file=sys.stderr)
    for fam in sorted(cat.missing_families):
        print(f"missing epoch file for family {fam} (digs exist); "
              f"run 'epochs propose --family {fam}'", file=sys.stderr)
    for name in getattr(source, 'unparseable_defs', None) or []:
        print(f'unparseable dig definition: {name}', file=sys.stderr)
    for w in count_warnings(cat):
        print(f"count warning: {w['dataset']} {w['nfiles']} files vs "
              f"{w['sibling']} {w['sibling_nfiles']}", file=sys.stderr)


def cmd_propose(args, source):
    if not args.family:
        print('epochs propose: --family is required (nothing is guessed)', file=sys.stderr)
        return 2
    have = load_epoch_files(args.epochs_dir)
    written = []
    for fam in args.family:
        seen = set()
        for dig in sorted(source.dig_datasets(fam)):
            dsconf = dig.split('.')[3]
            try:
                key = parse_dsconf(dsconf)
            except DsconfParseError as exc:
                print(f'unparseable dig dsconf, skipped: {dig} ({exc})', file=sys.stderr)
                continue
            name = f'{key.family}{key.letters}{key.rev or ""}'
            if name in have or name in seen:
                continue
            seen.add(name)
            written.append(write_epoch_file(args.epochs_dir, propose_epoch(dsconf)))
    if not written:
        print('nothing to propose: every dig letter family has an epoch file')
    for p in written:
        print(f'proposed {p}  <- fill in "purpose"')
    return 0


def cmd_members(args, source):
    cat = _catalog(args, source)
    rows = []
    for m in sorted(cat.members.values(), key=lambda m: (m.epoch, m.tier, m.desc, m.name)):
        if not _keep(args, m.key.family, m.epoch):
            continue
        if args.tier and m.tier != args.tier:
            continue
        if args.status and m.status != args.status:
            continue
        rows.append({'dataset': m.name, 'epoch': m.epoch, 'tier': m.tier, 'desc': m.desc,
                     'status': m.status, 'hold': m.hold, 'nfiles': m.nfiles})
    _emit(rows, args.json, lambda rs: [f"{r['status']:<10} {r['nfiles']:>6} {r['dataset']}"
                                      + (f"  [hold: {r['hold']}]" if r['hold'] else '') for r in rs])
    _report_noise(cat, source)
    return 0


def cmd_gaps(args, source):
    cat = _catalog(args, source)
    rows = [r for r in gaps(cat) if _keep(args, parse_dsconf(r['epoch']).family, r['epoch'])]
    _emit(rows, args.json, lambda rs: [f"{r['kind']:<8} {r['epoch']:<10} {r['tier']:<4} {r['desc']:<40} {r['note']}" for r in rs])
    _report_noise(cat, source)
    return 0


def cmd_consistency(args, source):
    cat = _catalog(args, source)
    gens = generations(cat, source, _load_index(args.epochs_dir))
    rows = [r for r in consistency(cat, gens) if not args.family or r['family'] in args.family]
    _emit(rows, args.json, lambda rs: [f"{r['family']:<8} {r['tier']:<4} {r['count']:>4}  {r['generation']:<32}"
                                      + ('  ' + ', '.join(r['descs']) if r['minority'] else '') for r in rs])
    _report_noise(cat, source)
    return 0


def cmd_retire(args, source):
    cat = _catalog(args, source)
    rows = [r for r in retire(cat) if _retire_keep(args, cat, r)]
    _emit(rows, args.json, purge_lines)
    _report_noise(cat, source)
    return 0


def cmd_lookup(args, source):
    cat = _catalog(args, source)
    info = lookup(cat, args.dataset)
    print(json.dumps(info, indent=1))
    return 0 if info['kind'] != 'unknown' else 1


def cmd_index_cnfs(args, source):
    idx = build_cnf_index(source, _load_index(args.epochs_dir))
    os.makedirs(args.epochs_dir, exist_ok=True)
    p = os.path.join(args.epochs_dir, INDEX_NAME)
    with open(p, 'w') as f:
        json.dump(idx, f, indent=1, sort_keys=True)
        f.write('\n')
    print(f'{p}: {len(idx["__indexed__"])} cnfs indexed, '
          f'{len(idx["__unlocatable__"])} unlocatable, {len(idx["__generic__"])} generic, '
          f'{len(idx["__unreadable__"])} unreadable, '
          f'{len(idx.get("__conflicts__") or {})} conflicting claims')
    for c in idx['__unlocatable__']:
        print(f'unlocatable cnf: {c}', file=sys.stderr)
    for claim, cnfs in sorted((idx.get('__conflicts__') or {}).items()):
        print(f'conflicting cnf claim on {claim}: kept {cnfs[0]}, also claimed by '
              f'{", ".join(cnfs[1:])}', file=sys.stderr)
    for c, err in sorted(idx['__unreadable__'].items()):
        print(f'unreadable cnf: {c}: {err}', file=sys.stderr)
    return 0


def cmd_publish(args, source, now_fn):
    cat = _catalog(args, source)
    gens = generations(cat, source, _load_index(args.epochs_dir))
    doc = catalog_document(cat, gens, now_fn())
    write_catalog(doc, args.out)
    if doc['retire'] is None:
        retire_part = f"retire: refused, catalog incomplete ({len(doc['incomplete'])} reasons)"
    else:
        retire_part = f"{len(doc['retire'])} retire candidates"
    print(f'{args.out}: {len(doc["epochs"])} epochs, '
          f'{sum(len(e["datasets"]) for e in doc["epochs"])} members, '
          f'{len(doc["gaps"])} gaps, {retire_part}')
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog='epochs', description=__doc__)
    p.add_argument('--epochs-dir', default=DEFAULT_EPOCHS_DIR)
    p.add_argument('--input-depth', type=int, default=None,
                   help='cap the upward input walk at N levels above each dig '
                        '(default: no cap, walk to closure). A capped build '
                        'refuses the ENTIRE input section of `retire`.')
    sub = p.add_subparsers(dest='verb', required=True)

    def common(sp, epoch=True):
        sp.add_argument('--family', action='append')
        if epoch:
            sp.add_argument('--epoch')
        sp.add_argument('--json', action='store_true')

    common(sub.add_parser('propose'), epoch=False)
    s = sub.add_parser('members'); common(s)
    s.add_argument('--tier'); s.add_argument('--status')
    common(sub.add_parser('gaps'))
    common(sub.add_parser('consistency'))
    common(sub.add_parser('retire'))
    s = sub.add_parser('lookup'); common(s); s.add_argument('dataset')
    common(sub.add_parser('index-cnfs'), epoch=False)
    s = sub.add_parser('publish'); s.add_argument('--out', required=True)
    return p


def main(argv: Optional[List[str]] = None, source=None, now_fn=None) -> int:
    args = build_parser().parse_args(argv)
    if not hasattr(args, 'epoch'):
        args.epoch = None
    if not hasattr(args, 'family'):
        args.family = []
    source = source or _source()
    now_fn = now_fn or _now
    sam_error = _sam_error_class()
    try:
        if args.verb == 'propose':
            return cmd_propose(args, source)
        if args.verb == 'members':
            return cmd_members(args, source)
        if args.verb == 'gaps':
            return cmd_gaps(args, source)
        if args.verb == 'consistency':
            return cmd_consistency(args, source)
        if args.verb == 'retire':
            return cmd_retire(args, source)
        if args.verb == 'lookup':
            return cmd_lookup(args, source)
        if args.verb == 'index-cnfs':
            return cmd_index_cnfs(args, source)
        if args.verb == 'publish':
            return cmd_publish(args, source, now_fn)
    except EpochFileError as exc:
        print(f'epochs: {exc}', file=sys.stderr)
        return 2
    except sam_error as exc:
        print(f'epochs: SAM error: {exc}', file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(f'epochs: {exc}', file=sys.stderr)
        return 3
    return 2
