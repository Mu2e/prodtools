#!/usr/bin/env python3
"""Unit tests for utils/runmu2e.py's proto_for_inloc() helper.

Standalone style, mirroring test_jobwait.py: sys.path bootstrap, no
heavy fixtures. runmu2e.py itself imports utils.samweb_wrapper ->
`from samweb_client import SAMWebClient`, which only exists inside the
Mu2e env (muse setup). test_unit.py:61-70 solves this exact dependency
chain by stubbing samweb_client/ifdh into sys.modules with MagicMock()
before any utils.* import; the same stub is copied here (not imported
from test_unit.py, to keep this file standalone and untangled from the
operator's in-flight test_unit.py) so the assertions actually run in a
plain non-Mu2e shell instead of skipping.

Run with:  python3 test/test_runmu2e_dir_pnfs.py
       or: python -m pytest test/test_runmu2e_dir_pnfs.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# samweb_client and other Fermilab-specific modules are not available
# outside the Mu2e environment. Stub them before any utils import
# occurs so that this test file runs standalone (mirrors
# test/test_unit.py:64-70).
for _mod in ('samweb_client', 'ifdh'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from utils.runmu2e import proto_for_inloc


class TestDirInlocProto(unittest.TestCase):

    def test_local_dir_is_file(self):
        self.assertEqual(proto_for_inloc("dir:/cvmfs/mu2e/DataFiles"), "file")

    def test_pnfs_dir_is_root(self):
        self.assertEqual(proto_for_inloc(
            "dir:/pnfs/mu2e/scratch/users/u/workflow/default/outstage/1/staged"),
            "root")

    def test_pnfs_dir_no_trailing_slash_is_still_file(self):
        # Boundary case: "dir:/pnfs" with no trailing slash does not
        # match the '/pnfs/' prefix check, so it is NOT treated as a
        # dCache mount -- it falls through to 'file'. Documents the
        # literal string-prefix semantics of proto_for_inloc rather
        # than a path-aware /pnfs detector.
        self.assertEqual(proto_for_inloc("dir:/pnfs"), "file")

    def test_sam_locations_are_root(self):
        self.assertEqual(proto_for_inloc("tape"), "root")


if __name__ == '__main__':
    unittest.main()
