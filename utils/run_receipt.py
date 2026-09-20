#!/usr/bin/env python3
"""Receipts for one-shot outstage runs (`json2jobdef --once`).

Such a run has no ledger row: the ledger verifies against SAM and
resubmits, and an outstage run declares nothing and is never resubmitted
(see submit._check_tracking). The receipt is its only record -- what was
submitted, as which cluster, and where the outputs go -- and nothing
reads it in order to act.

It follows the ledger's reserve / attach pattern, in a file: written as
`submitting` BEFORE jobsub_submit and rewritten after. A process killed
in between leaves `submitting`, and because a name is used once, a
second attempt under it is refused instead of duplicating a cluster the
grid may already have accepted.

Pure stdlib, no Mu2e environment: the read-only MCP server imports it.
"""

import datetime
import getpass
import json
import os

RECEIPT = 'receipt.json'


class RunExists(Exception):
    pass


class RunNotFound(Exception):
    pass


def runs_root(user=None):
    """Next to the personal ledger (submission_ledger.ledger_for)."""
    return f'/exp/mu2e/data/users/{user or getpass.getuser()}/prodtools/runs'


def run_name(tarball):
    """cnf.owner.desc.dsconf.N.tar -> cnf.owner.desc.dsconf.N"""
    name = os.path.basename(tarball)
    return name[:-len('.tar')] if name.endswith('.tar') else name


def _checked(name):
    # A name reaches this from an MCP caller; it must stay a directory
    # name under the root, never become a path.
    if not name or name in ('.', '..') or '/' in name or os.sep in name:
        raise ValueError(f'{name!r} is not a run name')
    return name


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec='seconds')


def _write(run_dir, data):
    tmp = os.path.join(run_dir, RECEIPT + '.part')
    with open(tmp, 'w') as fh:
        json.dump(data, fh, indent=2)
        fh.write('\n')
    os.replace(tmp, os.path.join(run_dir, RECEIPT))


def reserve(root, name, entry, state='submitting'):
    """Create the run directory and its first receipt; return the
    directory. Raises RunExists when the name was ever used."""
    run_dir = os.path.join(root, _checked(name))
    os.makedirs(root, exist_ok=True)
    try:
        os.mkdir(run_dir)
    except FileExistsError:
        raise RunExists(
            f'{run_dir} exists: a desc+dsconf pair is used once, because '
            f'the output file names derive from it and two runs writing '
            f'the same names cannot be told apart. If its receipt says '
            f'"submitting", the previous attempt died mid-submit: look '
            f'for its cluster in jobsub_q before doing anything else. '
            f'Pick a new dsconf.') from None
    _write(run_dir, {'name': name, 'state': state,
                     'created_utc': _now(), 'entry': entry})
    return run_dir


def update(run_dir, **fields):
    path = os.path.join(run_dir, RECEIPT)
    with open(path) as fh:
        data = json.load(fh)
    data.update(fields)
    _write(run_dir, data)
    return data


def read(root, name):
    path = os.path.join(root, _checked(name), RECEIPT)
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise RunNotFound(f'no run {name!r}: {path} does not exist') from None
