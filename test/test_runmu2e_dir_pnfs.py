#!/usr/bin/env python3
"""Unit tests for utils/runmu2e.py's proto_for_inloc() helper.

Standalone style, mirroring test_jobwait.py: sys.path bootstrap, no
heavy fixtures, no mocked samweb_client. But runmu2e.py itself imports
utils.samweb_wrapper -> `from samweb_client import SAMWebClient`, which
only exists inside the Mu2e env (muse setup). Outside that env the
import raises ModuleNotFoundError, so the whole class is skipped
cleanly (not errored) rather than restructuring runmu2e's imports.

Run with:  python3 test/test_runmu2e_dir_pnfs.py
       or: python -m pytest test/test_runmu2e_dir_pnfs.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from utils.runmu2e import proto_for_inloc
    _IMPORT_ERROR = None
except ImportError as exc:
    proto_for_inloc = None
    _IMPORT_ERROR = str(exc)


@unittest.skipIf(_IMPORT_ERROR,
                  f"utils.runmu2e not importable outside the Mu2e env: {_IMPORT_ERROR}")
class TestDirInlocProto(unittest.TestCase):

    def test_local_dir_is_file(self):
        self.assertEqual(proto_for_inloc("dir:/cvmfs/mu2e/DataFiles"), "file")

    def test_pnfs_dir_is_root(self):
        self.assertEqual(proto_for_inloc(
            "dir:/pnfs/mu2e/scratch/users/u/workflow/default/outstage/1/staged"),
            "root")

    def test_sam_locations_are_root(self):
        self.assertEqual(proto_for_inloc("tape"), "root")


if __name__ == '__main__':
    unittest.main()
