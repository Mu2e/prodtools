#!/usr/bin/env python3
"""A per-user cache of unpacked `muse tarball` code tarballs.

A code-tarball entry (`code` set, no `simjob_setup`) has no Musing to
source: its environment is the tarball's own `Code/setup.sh`, the same
script the grid worker sources from $INPUT_TAR_DIR_LOCAL. Building its
cnf needs the tarball unpacked somewhere. This unpacks each tarball
ONCE, under a directory named for the sha256 of its bytes, so every
build of the same content shares one tree, and a tarball rebuilt in
place gets a new one.

Pure stdlib and SILENT: the write MCP server calls it in-process, and
that server's stdout carries the MCP protocol. runlocal.unpack_code
prints progress and sys.exit()s, which is right for a CLI and fatal
there.

Nothing evicts entries. To free one nothing is using, mv its <sha256>
directory aside and delete that: an rm -rf interrupted in place can
leave Code/setup.sh, which still counts as a hit.
"""

import getpass
import hashlib
import os
import shutil
import tarfile
import tempfile

SETUP = os.path.join('Code', 'setup.sh')


def cache_root(user=None):
    """Next to runs/ (run_receipt.runs_root)."""
    return f'/exp/mu2e/data/users/{user or getpass.getuser()}/prodtools/code'


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def unpacked(tarball, root=None):
    """The directory holding Code/ for this tarball's content, unpacking
    it on first use. Raises ValueError or OSError; never prints.

    The rename is the commit: the tree is extracted into a unique
    `<key>.part.*` directory per call and renamed into place only once
    it is complete and has a Code/setup.sh, so a killed unpack leaves a
    part directory, never half a tree under the real name. A concurrent
    unpack of the same bytes that renamed first wins; this one then uses
    its tree.
    """
    if not isinstance(tarball, str) or not os.path.isabs(tarball):
        raise ValueError(
            f'a code tarball is named by an absolute path, got {tarball!r}')
    if not os.path.isfile(tarball):
        raise ValueError(f'code tarball {tarball} does not exist')
    root = root or cache_root()
    final = os.path.join(root, _sha256(tarball))
    if os.path.isfile(os.path.join(final, SETUP)):
        return final
    os.makedirs(root, exist_ok=True)
    part = tempfile.mkdtemp(
        prefix=os.path.basename(final) + '.part.', dir=root)
    try:
        try:
            with tarfile.open(tarball, 'r:bz2') as tar:
                # Pinned, not left to the interpreter: RHEL's 3.9 backport
                # warns, 3.12 trusts fully, 3.14 defaults to 'data'. 'tar',
                # not 'data': every muse tarball carries Code/backing, a
                # symlink to its /cvmfs release, and 'data' refuses a link
                # to an absolute path. As trusted as before, just explicit.
                if hasattr(tarfile, 'tar_filter'):
                    tar.extractall(part, filter='tar')
                else:
                    tar.extractall(part)
        except tarfile.ReadError as exc:
            raise ValueError(
                f'code tarball {tarball} is not a bzip2 tar file '
                f'({exc}): build it with `muse tarball`') from exc
        except tarfile.TarError as exc:     # a member the filter refuses
            raise ValueError(
                f'code tarball {tarball} cannot be unpacked: {exc}') from exc
        if not os.path.isfile(os.path.join(part, SETUP)):
            raise ValueError(
                f'code tarball {tarball} has no Code/setup.sh: build it '
                f'with `muse tarball`')
        try:
            os.rename(part, final)
        except OSError:
            if not os.path.isfile(os.path.join(final, SETUP)):
                raise
    finally:
        shutil.rmtree(part, ignore_errors=True)
    return final
