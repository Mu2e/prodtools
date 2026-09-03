"""Curated epoch files: data/epochs/<letters>.json.

The ONLY curated input of the catalog (ADR 0004). One file per
digitization campaign, named by its campaign letters (decision 9).
A person writes `purpose`, flips `status`, and adds pins with reasons.
Everything else is derived.
"""
import glob
import json
import os
from typing import Dict, List, NamedTuple

from utils.epochs.dsconf import DsconfParseError, parse_dsconf

EPOCH_STATUSES = ('current', 'frozen', 'retired')
PIN_KINDS = {
    'exclude': ('dataset', 'reason'),
    'hold': ('dataset', 'reason'),
    'not_expected': ('desc', 'tiers', 'reason'),
    'order': ('tier', 'desc', 'purpose', 'winner', 'reason'),
    'notes': ('pattern', 'note'),
}


class EpochFileError(ValueError):
    """A curated epoch file is malformed. Name the file and the field."""


class EpochFile(NamedTuple):
    name: str
    family: str
    purpose: str
    status: str
    roots: List[str]
    pins: Dict[str, list]


def _check_root(path, root):
    parts = root.split('.')
    if len(parts) != 5:
        raise EpochFileError(f'{path}: root must have 5 dot-fields: {root!r}')
    if parts[1] != 'mu2e':
        raise EpochFileError(f'{path}: root owner must be mu2e: {root!r}')


def _check_pins(path, pins):
    if pins is not None and not isinstance(pins, dict):
        raise EpochFileError(f'{path}: pins must be an object, got {type(pins).__name__}')
    out = {k: [] for k in PIN_KINDS}
    for kind, entries in (pins or {}).items():
        if kind not in PIN_KINDS:
            raise EpochFileError(f'{path}: unknown pin kind {kind!r}')
        if not isinstance(entries, list):
            raise EpochFileError(f'{path}: pins.{kind} must be a list')
        for e in entries:
            missing = [f for f in PIN_KINDS[kind] if f not in e]
            if missing:
                raise EpochFileError(f'{path}: pins.{kind} entry missing {missing}: {e}')
            out[kind].append(e)
    return out


def _parse_one(path) -> EpochFile:
    with open(path) as f:
        data = json.load(f)
    stem = os.path.splitext(os.path.basename(path))[0]
    for field in ('name', 'purpose', 'status', 'roots'):
        if field not in data:
            raise EpochFileError(f'{path}: missing {field!r}')
    if data['name'] != stem:
        raise EpochFileError(f'{path}: name {data["name"]!r} != filename stem {stem!r}')
    try:
        key = parse_dsconf(data['name'])
    except DsconfParseError as exc:
        raise EpochFileError(f'{path}: {exc}') from exc
    if not key.letters:
        raise EpochFileError(f'{path}: epoch name needs campaign letters: {data["name"]!r}')
    if data['status'] not in EPOCH_STATUSES:
        raise EpochFileError(f'{path}: status must be one of {EPOCH_STATUSES}, got {data["status"]!r}')
    if not isinstance(data['roots'], list) or not data['roots']:
        raise EpochFileError(f'{path}: roots must be a non-empty list')
    for r in data['roots']:
        _check_root(path, r)
    return EpochFile(name=data['name'], family=key.family, purpose=data['purpose'],
                     status=data['status'], roots=list(data['roots']),
                     pins=_check_pins(path, data.get('pins')))


def load_epoch_files(dirpath: str) -> Dict[str, EpochFile]:
    """Every data/epochs/*.json, validated. A missing directory is an
    empty catalog, not an error (first run). A malformed file is."""
    if not os.path.isdir(dirpath):
        return {}
    out = {}
    for path in sorted(glob.glob(os.path.join(dirpath, '*.json'))):
        if os.path.basename(path) == 'cnf_index.json':
            continue
        e = _parse_one(path)
        out[e.name] = e
    return out


def propose_epoch(sample_dsconf: str) -> dict:
    """The file a new letter family gets before a person touches it."""
    key = parse_dsconf(sample_dsconf)
    if not key.letters:
        raise EpochFileError(f'cannot propose an epoch from {sample_dsconf!r}: no letters')
    name = f'{key.family}{key.letters}{key.rev or ""}'
    return {
        'name': name,
        'purpose': '',
        'status': 'current',
        'roots': [f'dig.mu2e.%.{name}_%.art'],
        'pins': {k: [] for k in PIN_KINDS},
    }


def write_epoch_file(dirpath: str, data: dict) -> str:
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, data['name'] + '.json')
    if os.path.exists(path):
        raise EpochFileError(f'{path} exists; edit it, do not re-propose')
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    return path
