"""Location vocabulary tests.

Covers the jobdesc `dir:` predicates that every module now routes
through (the hand-rolled `startswith('dir:')` copies were consolidated
here 2026-08-28). Lives outside test_unit.py deliberately — new
location tests accumulate here, not in the monolith.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.jobdesc import dir_inloc_path, is_dir_inloc
from utils.file_resolver import storage_scope


class TestDirInloc(unittest.TestCase):
    def test_cvmfs_dir_is_dir_inloc(self):
        self.assertTrue(is_dir_inloc('dir:/cvmfs/mu2e.osgstorage.org/x'))

    def test_pnfs_dir_is_dir_inloc(self):
        # dir: under /pnfs is still a dir: inloc — the POSIX-vs-xroot
        # split is a separate question (runmu2e.proto_for_inloc).
        self.assertTrue(is_dir_inloc('dir:/pnfs/mu2e/scratch/users/x'))

    def test_simple_locations_are_not(self):
        for loc in ('tape', 'disk', 'scratch', 'resilient', 'stash', 'none'):
            self.assertFalse(is_dir_inloc(loc))

    def test_non_string_is_not(self):
        self.assertFalse(is_dir_inloc(None))
        self.assertFalse(is_dir_inloc(['dir:/x']))

    def test_path_extraction(self):
        self.assertEqual(dir_inloc_path('dir:/exp/mu2e/data/x'),
                         '/exp/mu2e/data/x')

    def test_path_with_trailing_slash_survives(self):
        # Callers that need it stripped do .rstrip('/') themselves.
        self.assertEqual(dir_inloc_path('dir:/exp/mu2e/data/x/'),
                         '/exp/mu2e/data/x/')


class TestStorageScopeDirInloc(unittest.TestCase):
    def test_dir_location_has_no_scope(self):
        self.assertIsNone(storage_scope(
            'sim.mu2e.Test.MDC2020a.001200_00000000.art', 'dir:/cvmfs/x'))

    def test_none_location_has_no_scope(self):
        self.assertIsNone(storage_scope(
            'sim.mu2e.Test.MDC2020a.001200_00000000.art', None))




def _try_mdh():
    try:
        import mdh  # noqa: F401  (needs the `muse setup ops` spack env)
        return True
    except Exception:
        return False


@unittest.skipUnless(_try_mdh(), "mdh unavailable — run under `muse setup ops`")
class TestMdhParity(unittest.TestCase):
    """Pin our location mirror against the authority.

    file_resolver's path/scope grammar is a MIRROR of mdh
    location_def.py / pushOutput locprefix (see the Location facts
    section in utils/file_resolver.py). Drift caused the 2026-07-27
    tape-scope 403; this test makes that class of drift loud. Skips
    cleanly outside the ops env — run it under `muse setup ops` when
    touching either side.
    """

    SAMPLES = (
        'sim.mu2e.Test.MDC2020a.001200_00000000.art',   # phy-sim
        'sim.oksuzian.Test.MDC2020a.001200_00000000.art',  # usr-sim
        'log.mu2e.Test.MDC2020a.001200_00000000.log',   # phy-etc
        'nts.mu2e.Test.MDC2020a.001200_00000000.root',  # phy-nts
    )

    def test_file_paths_match_mdh(self):
        import mdh
        from utils.file_resolver import file_path_at
        for fname in self.SAMPLES:
            m = mdh.MFile(fname)
            for loc in ('tape', 'disk', 'scratch'):
                with self.subTest(file=fname, location=loc):
                    self.assertEqual(
                        file_path_at(fname, loc),
                        m.url(location=loc, schema='path'))

    def test_storage_scope_is_physical_path_minus_pnfs(self):
        import mdh
        from utils.file_resolver import storage_scope
        for fname in self.SAMPLES:
            m = mdh.MFile(fname)
            for loc in ('tape', 'disk', 'scratch'):
                with self.subTest(file=fname, location=loc):
                    scope = storage_scope(fname, loc)
                    physical = m.url(location=loc, schema='path')
                    assert physical.startswith('/pnfs')
                    self.assertTrue(
                        physical[len('/pnfs'):].startswith(scope + '/'),
                        f"scope {scope!r} does not cover {physical!r}")


if __name__ == '__main__':
    unittest.main()
