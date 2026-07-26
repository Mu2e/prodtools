#!/usr/bin/env python3
"""
Unit tests for prodtools core modules.

Tests run without SAM/grid access by using in-memory tarballs and mocked
samweb_client. This provides a regression baseline before adding new features
(e.g., stash support).

Run with:  python -m pytest test/test_unit.py -v
       or: python test/test_unit.py
"""

import copy
import hashlib
import io
import json
import os
import sqlite3
import sys
import types
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make the package root importable when running from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The MCP server package lives outside utils/; add its src root so the
# server's tools are testable in this suite without MCP machinery.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mcp', 'src'))

# samweb_client and other Fermilab-specific modules are not available outside
# the Mu2e environment. Stub them before any utils import occurs so that the
# test suite runs standalone.
_STUB_MODULES = [
    'samweb_client',
    'poms_client',
    'ifdh',
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# SQLAlchemy can't be MagicMock-stubbed (poms_db declares real ORM models),
# so DB-backed tests are skipped when it's absent (plain ops env; see
# reference_pyenv_ana_for_db).
try:
    import sqlalchemy  # noqa: F401
    _HAVE_SQLALCHEMY = True
except ImportError:
    _HAVE_SQLALCHEMY = False
requires_sqlalchemy = unittest.skipUnless(
    _HAVE_SQLALCHEMY,
    "requires SQLAlchemy (source pyenv.sh ana after muse setup ops)")

from utils.job_common import Mu2eName, remove_storage_prefix, Mu2eJobBase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tarball(jobpars: dict, fcl_content: str = "#include \"base.fcl\"\n") -> str:
    """
    Build an in-memory tarball containing jobpars.json + mu2e.fcl and write
    it to a temporary file.  Returns the path to the .tar file.

    The file is placed in /tmp and must be removed by the caller if desired.
    """
    import tempfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        # Add jobpars.json
        jp_bytes = json.dumps(jobpars).encode()
        ti = tarfile.TarInfo(name='jobpars.json')
        ti.size = len(jp_bytes)
        tar.addfile(ti, io.BytesIO(jp_bytes))
        # Add mu2e.fcl
        fcl_bytes = fcl_content.encode()
        ti2 = tarfile.TarInfo(name='mu2e.fcl')
        ti2.size = len(fcl_bytes)
        tar.addfile(ti2, io.BytesIO(fcl_bytes))
    buf.seek(0)

    tmp = tempfile.NamedTemporaryFile(suffix='.tar', delete=False)
    tmp.write(buf.read())
    tmp.close()
    return tmp.name


def _root_input_jobpars(files, merge=1, run=1430, owner='mu2e', dsconf='TestConf'):
    """Return a jobpars.json dict suitable for a RootInput job."""
    return {
        "code": "",
        "setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/TestConf/setup.sh",
        "tbs": {
            "seed": "services.SeedService.baseSeed",
            "subrunkey": "",
            "event_id": {"source.maxEvents": 2147483647},
            "outfiles": {
                "outputs.PrimaryOutput.fileName":
                    f"sim.{owner}.TestDesc.{dsconf}.sequencer.art"
            },
            "inputs": {
                "source.fileNames": [merge, files]
            },
            "sequential_aux": False,
        },
        "jobname": f"cnf.{owner}.TestDesc.{dsconf}.0.tar",
        "owner": owner,
        "dsconf": dsconf,
    }


def _empty_event_jobpars(run=1430, events=1000, owner='mu2e', dsconf='TestConf'):
    """Return a jobpars.json dict suitable for an EmptyEvent job."""
    return {
        "code": "",
        "setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/TestConf/setup.sh",
        "tbs": {
            "seed": "services.SeedService.baseSeed",
            "subrunkey": "source.firstSubRun",
            "event_id": {
                "source.firstRun": run,
                "source.maxEvents": events,
            },
            "outfiles": {
                "outputs.PrimaryOutput.fileName":
                    f"sim.{owner}.TestDesc.{dsconf}.sequencer.art"
            },
        },
        "jobname": f"cnf.{owner}.TestDesc.{dsconf}.0.tar",
        "owner": owner,
        "dsconf": dsconf,
    }


# ---------------------------------------------------------------------------
# 1. Mu2eName Perl-parity contract (job_common.py, formerly Mu2eName)
# ---------------------------------------------------------------------------

class TestMu2eNameParity(unittest.TestCase):

    def test_parse_standard_filename(self):
        fn = Mu2eName("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertEqual(fn.tier, "dts")
        self.assertEqual(fn.owner, "mu2e")
        self.assertEqual(fn.description, "CeEndpoint")
        self.assertEqual(fn.dsconf, "Run1Bab")
        self.assertEqual(fn.sequencer, "001440_00001234")
        self.assertEqual(fn.extension, "art")

    def test_parse_sim_filename(self):
        fn = Mu2eName("sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000000.art")
        self.assertEqual(fn.tier, "sim")
        self.assertEqual(fn.sequencer, "001430_00000000")
        self.assertEqual(fn.dsconf, "MDC2025ac")

    def test_parse_nts_filename(self):
        fn = Mu2eName("nts.mu2e.CosmicCRYExtracted.MDC2020av.001205_00000000.root")
        self.assertEqual(fn.tier, "nts")
        self.assertEqual(fn.extension, "root")

    def test_str_returns_filename(self):
        name = "dig.mu2e.CosmicCRYAllMix1BB.MDC2025af.001430_00000076.art"
        fn = Mu2eName(name)
        self.assertEqual(str(fn), name)

    def test_invalid_filename_raises(self):
        with self.assertRaises(ValueError):
            Mu2eName("too.few.parts")

    def test_invalid_filename_seven_parts_raises(self):
        with self.assertRaises(ValueError):
            Mu2eName("a.b.c.d.e.f.g")  # only 5 or 6 fields are valid

    def test_parse_six_parts_ok(self):
        fn = Mu2eName("a.b.c.d.e.f")
        self.assertEqual(fn.tier, "a")
        self.assertEqual(fn.extension, "f")

    def test_dataset_derivation(self):
        """Dataset name can be derived from filename by dropping sequencer."""
        fn = Mu2eName("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertEqual(str(fn.dataset), "dts.mu2e.CeEndpoint.Run1Bab.art")

    def test_dataset_derivation_sim(self):
        fn = Mu2eName("sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000007.art")
        self.assertEqual(str(fn.dataset), "sim.mu2e.MuminusStopsCat.MDC2025ac.art")


# ---------------------------------------------------------------------------
# 1b. Mu2eName extended interface (job_common.py)
# ---------------------------------------------------------------------------

class TestMu2eName(unittest.TestCase):
    """Exercise the unified parse/build/derivation surface of Mu2eName.

    TestMu2eNameParity above pins the historical (Perl Mu2eName)
    contract; this class pins the extended interface.
    """

    # parse / discriminators

    def test_parse_dataset_five_fields(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("dts.mu2e.CeEndpoint.Run1Bab.art")
        self.assertTrue(n.is_dataset)
        self.assertFalse(n.is_file)
        self.assertFalse(n.is_tarball)
        self.assertIsNone(n.sequencer)
        self.assertEqual(n.extension, "art")

    def test_parse_file_six_fields(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertTrue(n.is_file)
        self.assertFalse(n.is_dataset)
        self.assertFalse(n.is_tarball)
        self.assertEqual(n.sequencer, "001440_00001234")

    def test_parse_tarball(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("cnf.mu2e.CeEndpoint.MDC2025af_best_v1_3.42.tar")
        self.assertTrue(n.is_tarball)
        self.assertFalse(n.is_file)
        self.assertFalse(n.is_dataset)
        self.assertEqual(n.index, 42)

    def test_reject_four_fields(self):
        from utils.job_common import Mu2eName
        with self.assertRaises(ValueError):
            Mu2eName.parse("a.b.c.d")

    def test_reject_seven_fields(self):
        from utils.job_common import Mu2eName
        with self.assertRaises(ValueError):
            Mu2eName.parse("a.b.c.d.e.f.g")

    # sub-fields

    def test_dsconf_base_with_version_suffix(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("mcs.mu2e.CeEndpoint.MDC2025af_best_v1_3.001440_00001234.art")
        self.assertEqual(n.dsconf_base, "MDC2025af")

    def test_dsconf_base_plain(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertEqual(n.dsconf_base, "Run1Bab")

    def test_campaign_extracts_mdc(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("mcs.mu2e.X.MDC2025af_best_v1_3.001440_00001234.art")
        self.assertEqual(n.campaign, "MDC2025af")

    def test_campaign_extracts_run1b(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("dts.mu2e.X.Run1Bab.001440_00001234.art")
        self.assertEqual(n.campaign, "Run1Bab")

    def test_index_raises_on_non_tarball(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        with self.assertRaises(ValueError):
            _ = n.index

    # tier_class parity with the existing module-level map

    def test_tier_class_matches_legacy_map(self):
        """Pin the tier_class umbrella mapping. The legacy module-level
        dict was deleted from jobsub_argv as part of unification; the
        expected values below are the verified Phase-2 list (sim chain,
        ancillary, data, MC ntuples)."""
        from utils.job_common import Mu2eName
        legacy = {
            "sim": "sim", "dig": "sim", "dts": "sim", "mcs": "sim", "mix": "sim",
            "log": "etc", "etc": "etc", "cnf": "etc", "bck": "etc",
            "rec": "dat", "ntd": "dat",
            "nts": "nts",
        }
        for tier, expected in legacy.items():
            n = Mu2eName.build(tier=tier, owner="mu2e", description="X",
                               dsconf="MDC2025af", extension="art")
            self.assertEqual(n.tier_class, expected, f"tier_class mismatch for {tier}")

    def test_tier_class_unknown_passes_through(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.build(tier="zzz", owner="mu2e", description="X",
                           dsconf="MDC2025af", extension="art")
        self.assertEqual(n.tier_class, "zzz")

    # derivations

    def test_dataset_idempotent(self):
        from utils.job_common import Mu2eName
        ds = Mu2eName.parse("dts.mu2e.CeEndpoint.Run1Bab.art")
        self.assertEqual(ds.dataset, ds)

    def test_with_sequencer_and_extension_and_as_tier(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertEqual(str(n.with_sequencer("999999_00000001")),
                         "dts.mu2e.CeEndpoint.Run1Bab.999999_00000001.art")
        self.assertEqual(str(n.with_extension("root")),
                         "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.root")
        self.assertEqual(str(n.as_tier("log").with_extension("log")),
                         "log.mu2e.CeEndpoint.Run1Bab.001440_00001234.log")

    def test_log_dataset_from_tarball(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("cnf.mu2e.FlatMuMinus.MDC2025ab.0.tar")
        self.assertEqual(str(n.log_dataset()), "log.mu2e.FlatMuMinus.MDC2025ab.log")

    def test_log_dataset_matches_legacy_helper(self):
        """Pinned against db_builder._jobdef_to_log_dataset's published output.

        Imported indirectly (expected values listed inline) because db_builder
        uses `str | None` syntax that needs Python 3.10+.
        """
        from utils.job_common import Mu2eName
        cases = [
            ("cnf.mu2e.FlatMuMinus.MDC2025ab.0.tar",
             "log.mu2e.FlatMuMinus.MDC2025ab.log"),
            ("cnf.mu2e.CeEndpoint.MDC2025af_best_v1_3.42.tar",
             "log.mu2e.CeEndpoint.MDC2025af_best_v1_3.log"),
            ("cnf.mu2e.CosmicCRYAll.Run1Bag.123456.tar",
             "log.mu2e.CosmicCRYAll.Run1Bag.log"),
        ]
        for tarball, expected in cases:
            self.assertEqual(
                str(Mu2eName.parse(tarball).log_dataset()),
                expected,
                f"log_dataset mismatch for {tarball}",
            )

    # round-trip

    def test_roundtrip_file(self):
        from utils.job_common import Mu2eName
        s = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        self.assertEqual(str(Mu2eName.parse(s)), s)

    def test_roundtrip_dataset(self):
        from utils.job_common import Mu2eName
        s = "dts.mu2e.CeEndpoint.Run1Bab.art"
        self.assertEqual(str(Mu2eName.parse(s)), s)

    def test_roundtrip_tarball(self):
        from utils.job_common import Mu2eName
        s = "cnf.mu2e.CeEndpoint.MDC2025af_best_v1_3.42.tar"
        self.assertEqual(str(Mu2eName.parse(s)), s)

    # build validation

    def test_build_rejects_dot_in_field(self):
        from utils.job_common import Mu2eName
        with self.assertRaises(ValueError):
            Mu2eName.build(tier="dts", owner="mu2e", description="X.Y",
                           dsconf="MDC2025af", extension="art")

    def test_build_rejects_dot_in_sequencer(self):
        from utils.job_common import Mu2eName
        with self.assertRaises(ValueError):
            Mu2eName.build(tier="dts", owner="mu2e", description="X",
                           dsconf="MDC2025af", sequencer="00.00", extension="art")


# ---------------------------------------------------------------------------
# 1c. POMS-map entry accessors (poms_entry.py)
# ---------------------------------------------------------------------------

class TestPomsEntry(unittest.TestCase):
    """Pin the fail-loud / sentinel-default contract of utils.poms_entry."""

    GOOD = {
        "tarball": "cnf.mu2e.RMCFlatGamma.MDC2025ag.0.tar",
        "outputs": [{"dataset": "sim.mu2e.RMCFlatGamma.MDC2025ag.art",
                     "location": "tape"}],
        "njobs": 50,
        "inloc": "tape",
    }

    def test_tarball_of_happy_path(self):
        from utils.poms_entry import tarball_of
        self.assertEqual(tarball_of(self.GOOD), self.GOOD["tarball"])

    def test_tarball_of_missing_raises(self):
        from utils.poms_entry import tarball_of
        with self.assertRaises(ValueError):
            tarball_of({})

    def test_tarball_of_rejects_non_cnf(self):
        from utils.poms_entry import tarball_of
        with self.assertRaises(ValueError):
            tarball_of({"tarball": "sim.mu2e.X.MDC2025ag.001430_00000000.art"})

    def test_tarball_of_rejects_unparseable(self):
        from utils.poms_entry import tarball_of
        with self.assertRaises(ValueError):
            tarball_of({"tarball": "not-a-mu2e-name.txt"})

    def test_outputs_of_happy_path(self):
        from utils.poms_entry import outputs_of
        self.assertEqual(outputs_of(self.GOOD), self.GOOD["outputs"])

    def test_outputs_of_missing_raises(self):
        from utils.poms_entry import outputs_of
        with self.assertRaises(ValueError):
            outputs_of({"tarball": self.GOOD["tarball"]})

    def test_njobs_of_present(self):
        from utils.poms_entry import njobs_of
        self.assertEqual(njobs_of(self.GOOD), 50)

    def test_njobs_of_absent_returns_default(self):
        from utils.poms_entry import njobs_of
        self.assertIsNone(njobs_of({}))
        self.assertEqual(njobs_of({}, default=0), 0)
        self.assertEqual(njobs_of({}, default="?"), "?")

    def test_inloc_of_present(self):
        from utils.poms_entry import inloc_of
        self.assertEqual(inloc_of(self.GOOD), "tape")

    def test_inloc_of_absent_returns_none_sentinel(self):
        from utils.poms_entry import inloc_of
        self.assertEqual(inloc_of({}), "none")


# ---------------------------------------------------------------------------
# 2. remove_storage_prefix (job_common.py)
# ---------------------------------------------------------------------------

class TestRemoveStoragePrefix(unittest.TestCase):

    def test_enstore_prefix(self):
        path = "enstore:/pnfs/mu2e/tape/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art"
        self.assertEqual(
            remove_storage_prefix(path),
            "/pnfs/mu2e/tape/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art"
        )

    def test_dcache_prefix(self):
        path = "dcache:/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e"
        self.assertEqual(remove_storage_prefix(path), "/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e")

    def test_no_prefix_passthrough(self):
        path = "/pnfs/mu2e/tape/phy-sim/something"
        self.assertEqual(remove_storage_prefix(path), path)

    def test_empty_string(self):
        self.assertEqual(remove_storage_prefix(""), "")


# ---------------------------------------------------------------------------
# 3. Mu2eJobBase._my_random (job_common.py)
# ---------------------------------------------------------------------------

class TestMyRandom(unittest.TestCase):
    """_my_random is accessed via Mu2eJobBase (parent of Mu2eJobFCL)."""

    def setUp(self):
        # Use a minimal concrete subclass to access the method
        class _Stub(Mu2eJobBase):
            def _extract_json(self):
                return {}
        # _Stub needs a real (dummy) tarball path; we just test the hash method
        self._stub = object.__new__(_Stub)

    def _rand(self, *args):
        return Mu2eJobBase._my_random(self._stub, *args)

    def test_deterministic(self):
        a = self._rand(5, "file1.art", "file2.art")
        b = self._rand(5, "file1.art", "file2.art")
        self.assertEqual(a, b)

    def test_different_index(self):
        a = self._rand(0, "file1.art", "file2.art")
        b = self._rand(1, "file1.art", "file2.art")
        self.assertNotEqual(a, b)

    def test_different_files(self):
        a = self._rand(0, "file1.art")
        b = self._rand(0, "file2.art")
        self.assertNotEqual(a, b)

    def test_returns_integer(self):
        self.assertIsInstance(self._rand(0, "x"), int)


# ---------------------------------------------------------------------------
# 4. Mu2eJobFCL: path location and formatting
# ---------------------------------------------------------------------------

class TestLocateFile(unittest.TestCase):
    """Tests for resolver.locate without SAM (uses dir: prefix)."""

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["sim.mu2e.Test.MDC2025ac.001430_00000000.art"]
        jp = _root_input_jobpars(files)
        self.tar = _make_tarball(jp, "#include \"base.fcl\"\nmodule_type : RootInput\n")
        self.Cls = Mu2eJobFCL

    def tearDown(self):
        os.unlink(self.tar)

    def test_dir_prefix_no_sam(self):
        job = self.Cls(self.tar, inloc='dir:/data/inputs', proto='file')
        path = job._resolver.locate("myfile.art")
        self.assertEqual(path, "/data/inputs/myfile.art")

    def test_dir_prefix_trailing_slash_stripped(self):
        job = self.Cls(self.tar, inloc='dir:/data/inputs/', proto='file')
        path = job._resolver.locate("myfile.art")
        self.assertEqual(path, "/data/inputs/myfile.art")

    def test_dir_prefix_with_subdirectory(self):
        job = self.Cls(self.tar, inloc='dir:/a/b/c', proto='file')
        path = job._resolver.locate("x.art")
        self.assertEqual(path, "/a/b/c/x.art")


class TestLocateFileSAM(unittest.TestCase):
    """Tests for resolver.locate when SAM is involved (mocked)."""

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["sim.mu2e.Test.MDC2025ac.001430_00000000.art"]
        jp = _root_input_jobpars(files)
        self.tar = _make_tarball(jp, "#include \"base.fcl\"\nmodule_type : RootInput\n")
        self.Cls = Mu2eJobFCL

    def tearDown(self):
        os.unlink(self.tar)

    def test_tape_location_preferred(self):
        locations = [
            {'location_type': 'disk', 'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/f.art'},
            {'location_type': 'tape', 'full_path': '/pnfs/mu2e/tape/phy-sim/f.art'},
        ]
        with patch('utils.samweb_wrapper.locate_file_strict', return_value=locations):
            job = self.Cls(self.tar, inloc='tape', proto='file')
            path = job._resolver.locate("f.art")
        self.assertEqual(path, '/pnfs/mu2e/tape/phy-sim/f.art')

    def test_disk_location_preferred(self):
        locations = [
            {'location_type': 'disk', 'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/f.art'},
            {'location_type': 'tape', 'full_path': '/pnfs/mu2e/tape/phy-sim/f.art'},
        ]
        with patch('utils.samweb_wrapper.locate_file_strict', return_value=locations):
            job = self.Cls(self.tar, inloc='disk', proto='file')
            path = job._resolver.locate("f.art")
        self.assertEqual(path, '/pnfs/mu2e/persistent/datasets/phy-sim/f.art')

    def test_fallback_to_first_when_no_match(self):
        """When requested location_type isn't found, fall back to first entry."""
        locations = [
            {'location_type': 'tape', 'full_path': '/pnfs/mu2e/tape/phy-sim/f.art'},
        ]
        with patch('utils.samweb_wrapper.locate_file_strict', return_value=locations):
            job = self.Cls(self.tar, inloc='disk', proto='file')
            path = job._resolver.locate("f.art")
        self.assertEqual(path, '/pnfs/mu2e/tape/phy-sim/f.art')

    def test_no_locations_raises(self):
        with patch('utils.samweb_wrapper.locate_file_strict', return_value=[]):
            job = self.Cls(self.tar, inloc='tape', proto='file')
            with self.assertRaises(ValueError):
                job._resolver.locate("f.art")

    def test_sam_exception_raises(self):
        with patch('utils.samweb_wrapper.locate_file_strict',
                   side_effect=Exception("SAM unavailable")):
            job = self.Cls(self.tar, inloc='tape', proto='file')
            with self.assertRaises(ValueError):
                job._resolver.locate("f.art")


class TestFormatFilename(unittest.TestCase):
    """Tests for _format_filename protocol handling."""

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["sim.mu2e.Test.MDC2025ac.001430_00000000.art"]
        jp = _root_input_jobpars(files)
        self.tar = _make_tarball(jp, "#include \"base.fcl\"\nmodule_type : RootInput\n")
        self.Cls = Mu2eJobFCL

    def tearDown(self):
        os.unlink(self.tar)

    def test_file_proto_returns_physical_path(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='file')
        result = job._format_filename("myfile.art")
        self.assertEqual(result, "/pnfs/mu2e/tape/phy-sim/myfile.art")

    def test_root_proto_converts_pnfs_to_xroot(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='root')
        result = job._format_filename("myfile.art")
        self.assertTrue(result.startswith("xroot://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/"))
        self.assertIn("myfile.art", result)

    def test_root_proto_non_pnfs_raises(self):
        """root protocol requires /pnfs/ paths; non-pnfs should raise."""
        job = self.Cls(self.tar, inloc='dir:/local/data', proto='root')
        with self.assertRaises(ValueError):
            job._format_filename("myfile.art")

    def test_root_proto_xroot_path_structure(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim/dts', proto='root')
        result = job._format_filename("dts.mu2e.X.Y.000001_00000001.art")
        expected_prefix = "xroot://fndcadoor.fnal.gov//pnfs/fnal.gov/usr/mu2e/tape/phy-sim/dts/"
        self.assertTrue(result.startswith(expected_prefix),
                        f"Expected prefix: {expected_prefix}\nGot: {result}")

    def test_enstore_prefix_stripped_in_root_proto(self):
        """enstore: prefix in SAM path should be stripped before xroot conversion."""
        locations = [
            {'location_type': 'tape',
             'full_path': 'enstore:/pnfs/mu2e/tape/phy-sim/f.art'},
        ]
        with patch('utils.samweb_wrapper.locate_file_strict', return_value=locations):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='tape', proto='root')
            result = job._format_filename("f.art")
        self.assertTrue(result.startswith("xroot://fndcadoor.fnal.gov//pnfs/"))


# ---------------------------------------------------------------------------
# 5. Mu2eJobFCL: job inputs selection
# ---------------------------------------------------------------------------

class TestJobPrimaryInputs(unittest.TestCase):

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        self.files = [
            "sim.mu2e.Test.MDC2025ac.001430_%08d.art" % i for i in range(10)
        ]
        jp = _root_input_jobpars(self.files, merge=2)
        self.tar = _make_tarball(jp, "#include \"base.fcl\"\nmodule_type : RootInput\n")
        self.Cls = Mu2eJobFCL

    def tearDown(self):
        os.unlink(self.tar)

    def test_first_job_gets_first_merge_files(self):
        job = self.Cls(self.tar, inloc='dir:/tmp')
        result = job.job_primary_inputs(0)
        self.assertEqual(result['source.fileNames'], self.files[0:2])

    def test_second_job_gets_next_slice(self):
        job = self.Cls(self.tar, inloc='dir:/tmp')
        result = job.job_primary_inputs(1)
        self.assertEqual(result['source.fileNames'], self.files[2:4])

    def test_last_job(self):
        job = self.Cls(self.tar, inloc='dir:/tmp')
        result = job.job_primary_inputs(4)
        self.assertEqual(result['source.fileNames'], self.files[8:10])

    def test_out_of_range_raises(self):
        job = self.Cls(self.tar, inloc='dir:/tmp')
        with self.assertRaises(ValueError):
            job.job_primary_inputs(5)

    def test_njobs_correct(self):
        job = self.Cls(self.tar, inloc='dir:/tmp')
        self.assertEqual(job.njobs(), 5)


class TestJobPrimaryInputsMergeOne(unittest.TestCase):
    """Edge case: merge=1 (each job gets exactly 1 file)."""

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        self.files = ["sim.mu2e.T.MDC2025ac.001430_%08d.art" % i for i in range(3)]
        jp = _root_input_jobpars(self.files, merge=1)
        self.tar = _make_tarball(jp, "#include \"base.fcl\"\nmodule_type : RootInput\n")
        self.job = Mu2eJobFCL(self.tar, inloc='dir:/tmp')

    def tearDown(self):
        os.unlink(self.tar)

    def test_each_job_gets_one_file(self):
        for i, f in enumerate(self.files):
            result = self.job.job_primary_inputs(i)
            self.assertEqual(result['source.fileNames'], [f])

    def test_njobs_equals_file_count(self):
        self.assertEqual(self.job.njobs(), 3)


class TestJobAuxInputsRandom(unittest.TestCase):
    """Auxiliary inputs in random (default) mode."""

    def _make_job_with_aux(self, aux_files, nreq=2):
        from utils.jobfcl import Mu2eJobFCL
        jp = {
            "code": "",
            "setup": "/cvmfs/test/setup.sh",
            "tbs": {
                "seed": "services.SeedService.baseSeed",
                "subrunkey": "source.firstSubRun",
                "event_id": {"source.firstRun": 1430, "source.maxEvents": 1000},
                "outfiles": {"outputs.Out.fileName": "sim.mu2e.T.TC.sequencer.art"},
                "auxin": {
                    "physics.producers.gen.fileNames": [nreq, aux_files]
                },
                "sequential_aux": False,
            },
            "jobname": "cnf.mu2e.T.TC.0.tar",
            "owner": "mu2e",
            "dsconf": "TC",
        }
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        return Mu2eJobFCL(tar, inloc='dir:/tmp'), tar

    def test_deterministic_selection(self):
        files = ["aux_%02d.art" % i for i in range(10)]
        job, tar = self._make_job_with_aux(files, nreq=3)
        try:
            r1 = job.job_aux_inputs(0)
            r2 = job.job_aux_inputs(0)
            self.assertEqual(r1, r2)
        finally:
            os.unlink(tar)

    def test_different_indices_different_selection(self):
        files = ["aux_%02d.art" % i for i in range(10)]
        job, tar = self._make_job_with_aux(files, nreq=3)
        try:
            r0 = job.job_aux_inputs(0)
            r1 = job.job_aux_inputs(1)
            self.assertNotEqual(r0, r1)
        finally:
            os.unlink(tar)

    def test_no_duplicates_in_selection(self):
        files = ["aux_%02d.art" % i for i in range(10)]
        job, tar = self._make_job_with_aux(files, nreq=5)
        try:
            result = job.job_aux_inputs(0)
            selected = result['physics.producers.gen.fileNames']
            self.assertEqual(len(selected), len(set(selected)))
        finally:
            os.unlink(tar)

    def test_correct_count_returned(self):
        files = ["aux_%02d.art" % i for i in range(10)]
        job, tar = self._make_job_with_aux(files, nreq=4)
        try:
            result = job.job_aux_inputs(0)
            self.assertEqual(len(result['physics.producers.gen.fileNames']), 4)
        finally:
            os.unlink(tar)


class TestJobAuxInputsSequential(unittest.TestCase):
    """Auxiliary inputs in sequential mode."""

    def _make_job_with_seq_aux(self, aux_files, nreq=2):
        from utils.jobfcl import Mu2eJobFCL
        jp = {
            "code": "",
            "setup": "/cvmfs/test/setup.sh",
            "tbs": {
                "seed": "services.SeedService.baseSeed",
                "subrunkey": "source.firstSubRun",
                "event_id": {"source.firstRun": 1430, "source.maxEvents": 1000},
                "outfiles": {"outputs.Out.fileName": "sim.mu2e.T.TC.sequencer.art"},
                "auxin": {
                    "physics.producers.gen.fileNames": [nreq, aux_files]
                },
                "sequential_aux": True,
            },
            "jobname": "cnf.mu2e.T.TC.0.tar",
            "owner": "mu2e",
            "dsconf": "TC",
        }
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        return Mu2eJobFCL(tar, inloc='dir:/tmp'), tar

    def test_sequential_first_job(self):
        files = ["aux_%02d.art" % i for i in range(6)]
        job, tar = self._make_job_with_seq_aux(files, nreq=2)
        try:
            result = job.job_aux_inputs(0)
            self.assertEqual(result['physics.producers.gen.fileNames'], files[0:2])
        finally:
            os.unlink(tar)

    def test_sequential_second_job(self):
        files = ["aux_%02d.art" % i for i in range(6)]
        job, tar = self._make_job_with_seq_aux(files, nreq=2)
        try:
            result = job.job_aux_inputs(1)
            self.assertEqual(result['physics.producers.gen.fileNames'], files[2:4])
        finally:
            os.unlink(tar)

    def test_sequential_rollover(self):
        """When index * nreq >= nfiles, roll over from the beginning."""
        files = ["aux_%02d.art" % i for i in range(4)]
        job, tar = self._make_job_with_seq_aux(files, nreq=2)
        try:
            # Job 2: first=4, which == nf → rollover → first=0
            result = job.job_aux_inputs(2)
            self.assertEqual(result['physics.producers.gen.fileNames'], files[0:2])
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 6. Mu2eJobFCL: sequencer
# ---------------------------------------------------------------------------

class TestSequencer(unittest.TestCase):

    def test_sequencer_from_event_id(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430)
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            seq = job.sequencer(5)
            self.assertEqual(seq, "001430_00000005")
        finally:
            os.unlink(tar)

    def test_sequencer_from_input_files(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["sim.mu2e.Test.MDC2025ac.001430_00000000.art",
                 "sim.mu2e.Test.MDC2025ac.001430_00000001.art"]
        jp = _root_input_jobpars(files, merge=2)
        tar = _make_tarball(jp, "module_type : RootInput\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            seq = job.sequencer(0)
            # First (sorted) sequencer from input files
            self.assertEqual(seq, "001430_00000000")
        finally:
            os.unlink(tar)

    def test_sequencer_different_indices_differ(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430)
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            self.assertNotEqual(job.sequencer(0), job.sequencer(1))
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 7. Mu2eJobFCL: job outputs
# ---------------------------------------------------------------------------

class TestJobOutputs(unittest.TestCase):

    def test_output_sequencer_substituted(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430)
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            outputs = job.job_outputs(7)
            out_file = outputs['outputs.PrimaryOutput.fileName']
            # Sequencer for index 7 with run 1430 = 001430_00000007
            self.assertIn("001430_00000007", out_file)
        finally:
            os.unlink(tar)

    def test_output_owner_substituted(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430, owner='oksuzian')
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            outputs = job.job_outputs(0)
            out_file = outputs['outputs.PrimaryOutput.fileName']
            self.assertIn("oksuzian", out_file)
        finally:
            os.unlink(tar)

    def test_output_dsconf_substituted(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430, dsconf='MDC2025ac')
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            outputs = job.job_outputs(0)
            out_file = outputs['outputs.PrimaryOutput.fileName']
            self.assertIn("MDC2025ac", out_file)
        finally:
            os.unlink(tar)

    def test_output_follows_mu2e_naming(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430, owner='mu2e', dsconf='TestConf')
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            outputs = job.job_outputs(3)
            out_file = outputs['outputs.PrimaryOutput.fileName']
            parts = out_file.split('.')
            self.assertEqual(len(parts), 6, f"Expected 6 parts, got: {out_file}")
            self.assertEqual(parts[0], "sim")
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 8. Mu2eJobFCL: generate_fcl
# ---------------------------------------------------------------------------

class TestGenerateFCL(unittest.TestCase):

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        self.files = ["sim.mu2e.Test.MDC2025ac.001430_%08d.art" % i for i in range(4)]
        jp = _root_input_jobpars(self.files, merge=2)
        self.tar = _make_tarball(jp, "#include \"base.fcl\"\nmodule_type : RootInput\n")
        self.Cls = Mu2eJobFCL

    def tearDown(self):
        os.unlink(self.tar)

    def test_fcl_contains_header_comment(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='file')
        fcl = job.generate_fcl(0)
        self.assertIn("Code added by mu2ejobfcl", fcl)

    def test_fcl_contains_input_files(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='file')
        fcl = job.generate_fcl(0)
        self.assertIn(self.files[0], fcl)
        self.assertIn(self.files[1], fcl)

    def test_fcl_does_not_contain_other_job_files(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='file')
        fcl = job.generate_fcl(0)
        self.assertNotIn(self.files[2], fcl)

    def test_fcl_contains_output_filename(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='file')
        fcl = job.generate_fcl(1)
        outputs = job.job_outputs(1)
        for fname in outputs.values():
            self.assertIn(fname, fcl)

    def test_fcl_second_job_different_from_first(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='file')
        fcl0 = job.generate_fcl(0)
        fcl1 = job.generate_fcl(1)
        self.assertNotEqual(fcl0, fcl1)

    def test_fcl_contains_source_file_names_key(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='file')
        fcl = job.generate_fcl(0)
        self.assertIn("source.fileNames", fcl)

    def test_fcl_xroot_format_for_root_proto(self):
        job = self.Cls(self.tar, inloc='dir:/pnfs/mu2e/tape/phy-sim', proto='root')
        fcl = job.generate_fcl(0)
        self.assertIn("xroot://fndcadoor.fnal.gov//pnfs/", fcl)

    def test_empty_event_fcl_has_subrun(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430)
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            fcl = job.generate_fcl(3)
            self.assertIn("source.firstSubRun: 3", fcl)
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 9. Mu2eDSName path building (datasetFileList.py)
# ---------------------------------------------------------------------------

class TestMu2eDSName(unittest.TestCase):
    """Path-building tests for the (deleted) Mu2eDSName, retargeted at
    `datasetFileList._dataset_dir`, which folds the logic onto
    Mu2eName.tier_class.
    """

    def setUp(self):
        from utils.datasetFileList import _dataset_dir
        self.dsdir = _dataset_dir

    def test_sim_tape_path(self):
        path = self.dsdir("sim.mu2e.MuminusStopsCat.MDC2025ac.art", 'tape')
        self.assertEqual(path, "/pnfs/mu2e/tape/phy-sim/sim/mu2e/MuminusStopsCat/MDC2025ac/art")

    def test_dts_tape_path(self):
        path = self.dsdir("dts.mu2e.CeEndpoint.Run1Bab.art", 'tape')
        self.assertEqual(path, "/pnfs/mu2e/tape/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art")

    def test_dts_disk_path(self):
        path = self.dsdir("dts.mu2e.CeEndpoint.Run1Bab.art", 'disk')
        self.assertEqual(path, "/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art")

    def test_nts_type(self):
        path = self.dsdir("nts.mu2e.CosmicCRY.MDC2025ac.root", 'tape')
        self.assertIn("phy-nts", path)

    def test_mcs_type(self):
        path = self.dsdir("mcs.mu2e.CosmicCRY.MDC2025ac.art", 'tape')
        self.assertIn("phy-sim", path)

    def test_unknown_type(self):
        path = self.dsdir("log.mu2e.Something.MDC2025ac.log", 'tape')
        self.assertIn("phy-etc", path)

    def test_scratch_path(self):
        path = self.dsdir("sim.mu2e.Test.MDC2025ac.art", 'scratch')
        self.assertIn("/pnfs/mu2e/scratch/datasets/", path)

    def test_unknown_location_returns_empty(self):
        path = self.dsdir("sim.mu2e.Test.MDC2025ac.art", 'stash')  # not yet implemented
        self.assertEqual(path, "")


# ---------------------------------------------------------------------------
# 10. datasetFileList Mu2eName hash paths
# ---------------------------------------------------------------------------

class TestDatasetFileListFilename(unittest.TestCase):

    def setUp(self):
        # datasetFileList no longer re-exports Mu2eName; pull directly
        # from job_common (where the alias still points at Mu2eName).
        from utils.job_common import Mu2eName
        self.Cls = Mu2eName

    def test_relpathname_has_three_parts(self):
        fn = self.Cls("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        relpath = fn.relpathname()
        parts = relpath.split('/')
        self.assertEqual(len(parts), 3, f"Expected 3 path parts, got: {relpath}")

    def test_relpathname_ends_with_filename(self):
        name = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        fn = self.Cls(name)
        self.assertTrue(fn.relpathname().endswith(name))

    def test_relpathname_uses_sha256_prefix(self):
        name = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        fn = self.Cls(name)
        h = hashlib.sha256(name.encode()).hexdigest()
        expected_prefix = f"{h[:2]}/{h[2:4]}"
        self.assertTrue(fn.relpathname().startswith(expected_prefix))

    def test_relpathname_deterministic(self):
        name = "sim.mu2e.Test.MDC2025ac.001430_00000000.art"
        fn1 = self.Cls(name)
        fn2 = self.Cls(name)
        self.assertEqual(fn1.relpathname(), fn2.relpathname())

    def test_different_filenames_different_hash(self):
        fn1 = self.Cls("sim.mu2e.A.MDC2025ac.001430_00000000.art")
        fn2 = self.Cls("sim.mu2e.B.MDC2025ac.001430_00000000.art")
        # Different files should generally hash differently (not guaranteed but
        # extremely likely for these inputs)
        self.assertNotEqual(fn1.relpathname(), fn2.relpathname())


# ---------------------------------------------------------------------------
# 11. Stash path derivation (prerequisite check for future implementation)
# ---------------------------------------------------------------------------

class TestStashPathDerivation(unittest.TestCase):
    """
    Tests for the stash path construction logic described in the StashCache
    plan. These tests specify the expected behavior for inloc='stash' so that
    the implementation can be validated against them.

    The formula is:
        STASH_READ_ROOT/datasets/<tier>/<owner>/<description>/<dsconf>/<ext>/<filename>
    derived purely from the filename via Mu2eName.
    """

    STASH_ROOT = "/cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash"

    def _stash_path(self, filename: str) -> str:
        """Reference implementation of stash path building (not yet in code)."""
        fn = Mu2eName(filename)
        dataset = f"{fn.tier}.{fn.owner}.{fn.description}.{fn.dsconf}.{fn.extension}"
        ds_path = dataset.replace('.', '/')
        return f"{self.STASH_ROOT}/datasets/{ds_path}/{filename}"

    def test_ce_endpoint_path(self):
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        path = self._stash_path(fname)
        expected = (
            f"{self.STASH_ROOT}/datasets/dts/mu2e/CeEndpoint/Run1Bab/art/{fname}"
        )
        self.assertEqual(path, expected)

    def test_sim_file_path(self):
        fname = "sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000007.art"
        path = self._stash_path(fname)
        expected = (
            f"{self.STASH_ROOT}/datasets/sim/mu2e/MuminusStopsCat/MDC2025ac/art/{fname}"
        )
        self.assertEqual(path, expected)

    def test_different_owners(self):
        fname = "dts.oksuzian.CeEndpoint.Run1Bab.001440_00000001.art"
        path = self._stash_path(fname)
        self.assertIn("/oksuzian/", path)

    def test_path_contains_stash_root(self):
        fname = "nts.mu2e.CosmicCRY.MDC2025ac.001430_00000000.root"
        path = self._stash_path(fname)
        self.assertTrue(path.startswith(self.STASH_ROOT))

    def test_path_contains_datasets_prefix(self):
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        path = self._stash_path(fname)
        self.assertIn("/datasets/", path)

    def test_filename_at_end_of_path(self):
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        path = self._stash_path(fname)
        self.assertTrue(path.endswith(fname))


# ---------------------------------------------------------------------------
# 12. jobfcl stash integration (resolver.locate and _format_filename)
# ---------------------------------------------------------------------------

STASH_READ_DEFAULT = "/cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash"
STASH_WRITE_DEFAULT = "/pnfs/mu2e/persistent/stash"


class TestLocateFileStash(unittest.TestCase):
    """resolver.locate with inloc='stash' — path derived from filename (SAM only as fallback)."""

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"]
        jp = _root_input_jobpars(files)
        self.tar = _make_tarball(jp, "module_type : RootInput\n")
        self.Cls = Mu2eJobFCL
        # Simulate files being present on stash CVMFS
        self._exists_patch = patch('os.path.exists', return_value=True)
        self._exists_patch.start()

    def tearDown(self):
        self._exists_patch.stop()
        os.unlink(self.tar)

    def test_stash_locate_no_sam_call(self):
        """SAM must not be contacted when inloc='stash'."""
        with patch('utils.samweb_wrapper.locate_file_strict') as mock_locate:
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            job._resolver.locate("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        mock_locate.assert_not_called()

    def test_stash_path_structure(self):
        job = self.Cls(self.tar, inloc='stash', proto='file')
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        path = job._resolver.locate(fname)
        expected = (
            f"{STASH_READ_DEFAULT}/datasets/dts/mu2e/CeEndpoint/Run1Bab/art/{fname}"
        )
        self.assertEqual(path, expected)

    def test_stash_path_sim_file(self):
        job = self.Cls(self.tar, inloc='stash', proto='file')
        fname = "sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000007.art"
        path = job._resolver.locate(fname)
        self.assertIn("/datasets/sim/mu2e/MuminusStopsCat/MDC2025ac/art/", path)
        self.assertTrue(path.endswith(fname))

    def test_stash_path_uses_env_var(self):
        custom_root = "/custom/stash/root"
        with patch.dict(os.environ, {"MU2E_STASH_READ": custom_root}):
            # Re-import to pick up new env var (module-level constant)
            import importlib
            import utils.jobfcl as jfcl_mod
            importlib.reload(jfcl_mod)
            job = jfcl_mod.Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
            path = job._resolver.locate(fname)
            self.assertTrue(path.startswith(custom_root))
            # Restore
            importlib.reload(jfcl_mod)


class TestFormatFilenameStash(unittest.TestCase):
    """_format_filename with inloc='stash' always returns plain path."""

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"]
        jp = _root_input_jobpars(files)
        self.tar = _make_tarball(jp, "module_type : RootInput\n")
        self.Cls = Mu2eJobFCL
        self.fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        # Simulate files being present on stash CVMFS
        self._exists_patch = patch('os.path.exists', return_value=True)
        self._exists_patch.start()

    def tearDown(self):
        self._exists_patch.stop()
        os.unlink(self.tar)

    def test_stash_file_proto_returns_cvmfs_path(self):
        job = self.Cls(self.tar, inloc='stash', proto='file')
        result = job._format_filename(self.fname)
        self.assertTrue(result.startswith(STASH_READ_DEFAULT))

    def test_stash_root_proto_still_returns_plain_path(self):
        """proto='root' must be ignored for stash — no xroot conversion."""
        job = self.Cls(self.tar, inloc='stash', proto='root')
        result = job._format_filename(self.fname)
        self.assertFalse(result.startswith("xroot://"),
                         f"Expected plain CVMFS path, got: {result}")
        self.assertTrue(result.startswith(STASH_READ_DEFAULT))

    def test_stash_fcl_contains_cvmfs_path(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art",
                 "dts.mu2e.CeEndpoint.Run1Bab.001440_00000001.art"]
        jp = _root_input_jobpars(files, merge=2)
        tar = _make_tarball(jp, "module_type : RootInput\n")
        try:
            job = Mu2eJobFCL(tar, inloc='stash', proto='root')
            fcl = job.generate_fcl(0)
            self.assertIn(STASH_READ_DEFAULT, fcl)
            self.assertNotIn("xroot://", fcl)
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 13. stash_utils module
# ---------------------------------------------------------------------------

class TestStashUtils(unittest.TestCase):
    """Tests for utils/stash_utils.py path helpers."""

    def setUp(self):
        from utils import stash_utils
        self.su = stash_utils

    def test_read_root_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MU2E_STASH_READ", None)
            root = self.su.stash_read_root()
        self.assertEqual(root, STASH_READ_DEFAULT)

    def test_write_root_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MU2E_STASH_WRITE", None)
            root = self.su.stash_write_root()
        self.assertEqual(root, STASH_WRITE_DEFAULT)

    def test_read_root_from_env(self):
        with patch.dict(os.environ, {"MU2E_STASH_READ": "/my/read/root"}):
            root = self.su.stash_read_root()
        self.assertEqual(root, "/my/read/root")

    def test_write_root_from_env(self):
        with patch.dict(os.environ, {"MU2E_STASH_WRITE": "/my/write/root"}):
            root = self.su.stash_write_root()
        self.assertEqual(root, "/my/write/root")

    def test_read_path_for_file(self):
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MU2E_STASH_READ", None)
            path = self.su.read_path_for_file(fname)
        expected = f"{STASH_READ_DEFAULT}/datasets/dts/mu2e/CeEndpoint/Run1Bab/art/{fname}"
        self.assertEqual(path, expected)

    def test_write_path_for_file(self):
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MU2E_STASH_WRITE", None)
            path = self.su.write_path_for_file(fname)
        expected = f"{STASH_WRITE_DEFAULT}/datasets/dts/mu2e/CeEndpoint/Run1Bab/art/{fname}"
        self.assertEqual(path, expected)

    def test_read_and_write_paths_share_subpath(self):
        """The sub-path after the root must be identical for read and write."""
        fname = "sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000007.art"
        rp = self.su.read_path_for_file(fname)
        wp = self.su.write_path_for_file(fname)
        rp_sub = rp[len(self.su.stash_read_root()):]
        wp_sub = wp[len(self.su.stash_write_root()):]
        self.assertEqual(rp_sub, wp_sub)

    def test_read_path_ends_with_filename(self):
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        path = self.su.read_path_for_file(fname)
        self.assertTrue(path.endswith(fname))

    def test_copy_dataset_dry_run(self):
        """dry_run=True must not copy or makedirs."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art",
                      "dts.mu2e.CeEndpoint.Run1Bab.001440_00000001.art"]
        mock_locations = [
            {'location_type': 'disk',
             'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'}
        ]

        with patch('utils.stash_utils.files_in_dataset', return_value=mock_files), \
             patch('utils.stash_utils.locate_files_strict',
                   side_effect=lambda fns: {f: mock_locations for f in fns}), \
             patch('os.makedirs') as mock_mkdir, \
             patch('utils.stash_utils.shutil.copyfile') as mock_run:
            n = stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                dry_run=True,
                verbose=False,
            )

        mock_mkdir.assert_not_called()
        mock_run.assert_not_called()
        self.assertEqual(n, 2)

    def test_copy_dataset_calls_copyfile(self):
        """Copies via shutil.copyfile(src, dest) — not a `cp` subprocess.

        copyfile, not copy2/copy: the destination is dCache, where the
        metadata/permission copy those do is not reliably supported."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art"]
        mock_locations = [
            {'location_type': 'disk',
             'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'}
        ]
        with patch('utils.stash_utils.files_in_dataset', return_value=mock_files), \
             patch('utils.stash_utils.locate_files_strict',
                   side_effect=lambda fns: {f: mock_locations for f in fns}), \
             patch('os.makedirs'), \
             patch('utils.stash_utils.shutil.copyfile') as mock_run:
            n = stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                dry_run=False,
                verbose=False,
            )

        self.assertEqual(n, 1)
        src, dest = mock_run.call_args[0]
        self.assertTrue(src.endswith(mock_files[0]))
        self.assertTrue(dest.endswith(mock_files[0]))

    def test_copy_dataset_counts_oserror_as_failure(self):
        """A copy that raises OSError is a failure, not a silent success.

        The old code read subprocess returncode; shutil raises instead, so
        the failure path must catch OSError or a failed copy would be
        counted as copied."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art",
                      "dts.mu2e.CeEndpoint.Run1Bab.001440_00000001.art"]
        mock_locations = [
            {'location_type': 'disk',
             'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'}
        ]

        with patch('utils.stash_utils.files_in_dataset', return_value=mock_files), \
             patch('utils.stash_utils.locate_files_strict',
                   side_effect=lambda fns: {f: mock_locations for f in fns}), \
             patch('os.makedirs'), \
             patch('utils.stash_utils.shutil.copyfile',
                   side_effect=OSError(28, "No space left on device")):
            n = stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk', dry_run=False, verbose=False)

        self.assertEqual(n, 0)

    def test_copy_dataset_limit(self):
        """--limit N should copy at most N files."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_%08d.art" % i for i in range(10)]
        mock_locations = [
            {'location_type': 'disk',
             'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'}
        ]
        with patch('utils.stash_utils.files_in_dataset', return_value=mock_files), \
             patch('utils.stash_utils.locate_files_strict',
                   side_effect=lambda fns: {f: mock_locations for f in fns}) as mock_loc, \
             patch('os.makedirs'), \
             patch('utils.stash_utils.shutil.copyfile') as mock_run:
            stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                limit=3,
                dry_run=False,
                verbose=False,
            )
        # one batch locate for the (limited) copy list, not one per file
        mock_loc.assert_called_once()
        self.assertEqual(len(mock_loc.call_args[0][0]), 3)

        self.assertEqual(mock_run.call_count, 3)

    def test_copy_dataset_skips_on_locate_failure(self):
        """Files that cannot be located should be skipped, not crash."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art"]

        with patch('utils.stash_utils.files_in_dataset', return_value=mock_files), \
             patch('utils.samweb_wrapper.locate_file_strict', return_value=[]), \
             patch('os.makedirs'), \
             patch('utils.stash_utils.shutil.copyfile') as mock_run:
            n = stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                dry_run=False,
                verbose=False,
            )

        mock_run.assert_not_called()
        self.assertEqual(n, 0)


# ---------------------------------------------------------------------------
# 14. runmu2e: stash skips copy_input
# ---------------------------------------------------------------------------

class TestProcessJobdefStashSkipsCopyInput(unittest.TestCase):
    """
    When inloc='stash', process_jobdef must use streaming mode even when
    args.copy_input is True — CVMFS files need no local copying.
    """

    def test_stash_does_not_call_mdh_copy(self):
        from utils import runmu2e

        files = ["sim.mu2e.Test.TestConf.001440_00000000.art"]
        jp = _root_input_jobpars(files, merge=1)
        tar = _make_tarball(jp, "module_type : RootInput\n")

        args = MagicMock()
        args.copy_input = True   # would trigger mdh copy for tape/disk

        jobdesc = [{
            'tarball': tar,
            'njobs': 1,
            'inloc': 'stash',
            'outputs': [],
        }]

        mock_fcl = tar.replace('.tar', '.fcl')

        with patch('utils.runmu2e.write_fcl', return_value=mock_fcl) as mock_wfcl, \
             patch('utils.runmu2e.run') as mock_run, \
             patch('utils.jobquery.Mu2eJobPars') as mock_pars:

            mock_pars.return_value.setup.return_value = "/cvmfs/test/setup.sh"

            runmu2e.process_jobdef(
                jobdesc,
                fname="cnf.mu2e.Test.TestConf.0.fcl",
                args=args,
            )

        # write_fcl must be called with inloc='stash' (streaming), not 'dir:...'
        call_inloc = mock_wfcl.call_args[0][1]
        self.assertEqual(call_inloc, 'stash',
                         f"Expected inloc='stash' (streaming), got '{call_inloc}'")

        # mdh copy-file must NOT have been called
        for call in mock_run.call_args_list:
            cmd = str(call[0][0]) if call[0] else ''
            self.assertNotIn('mdh copy-file', cmd,
                             "mdh copy-file must not be called for stash inloc")

        os.unlink(tar)


# ---------------------------------------------------------------------------
# 15. version field in tarball names
# ---------------------------------------------------------------------------

class TestVersionField(unittest.TestCase):
    """version field in config controls the version digit in tarball/FCL names."""

    def _cfg(self, **extra):
        return {'owner': 'mu2e', 'desc': 'TestDesc', 'dsconf': 'TestConf', **extra}

    # --- get_parfile_name ---

    def test_default_version_is_zero(self):
        from utils.json2jobdef import get_parfile_name
        self.assertEqual(get_parfile_name(self._cfg()), 'cnf.mu2e.TestDesc.TestConf.0.tar')

    def test_version_one(self):
        from utils.json2jobdef import get_parfile_name
        self.assertEqual(get_parfile_name(self._cfg(version=1)), 'cnf.mu2e.TestDesc.TestConf.1.tar')

    def test_version_five(self):
        from utils.json2jobdef import get_parfile_name
        self.assertEqual(get_parfile_name(self._cfg(version=5)), 'cnf.mu2e.TestDesc.TestConf.5.tar')

    # --- version + tarball_append ---

    def test_version_with_tarball_append(self):
        """version and tarball_append are independent: append modifies desc, version changes digit."""
        from utils.json2jobdef import get_parfile_name
        cfg = self._cfg(version=1, tarball_append='_ext1')
        self.assertEqual(get_parfile_name(cfg), 'cnf.mu2e.TestDesc_ext1.TestConf.1.tar')

    def test_tarball_append_without_version_stays_zero(self):
        from utils.json2jobdef import get_parfile_name
        cfg = self._cfg(tarball_append='_ext1')
        self.assertEqual(get_parfile_name(cfg), 'cnf.mu2e.TestDesc_ext1.TestConf.0.tar')


# ---------------------------------------------------------------------------
# 16. write_fcl FCL filename derivation
# ---------------------------------------------------------------------------

class TestWriteFclFilenameDerivation(unittest.TestCase):
    """write_fcl replaces the tarball version digit with the job index in the FCL filename."""

    def _run_write_fcl(self, tarball_basename, index):
        """Call write_fcl with a mocked Mu2eJobFCL and return the FCL filename created."""
        import tempfile
        from utils.prod_utils import write_fcl

        orig_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                tarball_path = os.path.join(tmpdir, tarball_basename)
                mock_job = MagicMock()
                mock_job.find_index.return_value = index
                mock_job.generate_fcl.return_value = "# test fcl"
                with patch('utils.prod_utils.Mu2eJobFCL', return_value=mock_job):
                    write_fcl(tarball_path, inloc='tape', index=index)
                created = [f for f in os.listdir(tmpdir) if f.endswith('.fcl')]
                return created[0] if created else None
            finally:
                os.chdir(orig_dir)

    def test_version_zero_replaced_by_index(self):
        fcl = self._run_write_fcl("cnf.mu2e.TestDesc.TestConf.0.tar", 7)
        self.assertEqual(fcl, "cnf.mu2e.TestDesc.TestConf.7.fcl")

    def test_version_two_replaced_by_index(self):
        """Non-zero version digit is replaced by the job index, not appended."""
        fcl = self._run_write_fcl("cnf.mu2e.TestDesc.TestConf.2.tar", 7)
        self.assertEqual(fcl, "cnf.mu2e.TestDesc.TestConf.7.fcl")

    def test_multi_digit_index(self):
        fcl = self._run_write_fcl("cnf.mu2e.TestDesc.TestConf.1.tar", 12345)
        self.assertEqual(fcl, "cnf.mu2e.TestDesc.TestConf.12345.fcl")


# ---------------------------------------------------------------------------
# 17. stash SAM-fallback (file not on CVMFS)
# ---------------------------------------------------------------------------

class TestStashFallback(unittest.TestCase):
    """When inloc='stash' and the file is not on CVMFS, resolver.locate falls back to SAM."""

    _TAPE_DIR = '/pnfs/mu2e/tape/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'
    _FNAME = 'dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art'

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        files = [self._FNAME]
        jp = _root_input_jobpars(files)
        self.tar = _make_tarball(jp, "module_type : RootInput\n")
        self.Cls = Mu2eJobFCL
        # Simulate file NOT present on stash CVMFS
        self._exists_patch = patch('os.path.exists', return_value=False)
        self._exists_patch.start()

    def tearDown(self):
        self._exists_patch.stop()
        os.unlink(self.tar)

    def _sam_locations(self, location_type='tape'):
        return [{'location_type': location_type, 'full_path': self._TAPE_DIR}]

    def test_sam_called_when_stash_file_missing(self):
        """SAM is contacted as fallback when the stash CVMFS path does not exist."""
        with patch('utils.samweb_wrapper.locate_file_strict',
                   return_value=self._sam_locations()) as mock_locate:
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            job._resolver.locate(self._FNAME)
        mock_locate.assert_called_once_with(self._FNAME)

    def test_fallback_returns_sam_path(self):
        """The SAM-provided path is returned when the stash file is absent."""
        with patch('utils.samweb_wrapper.locate_file_strict',
                   return_value=self._sam_locations()):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            path = job._resolver.locate(self._FNAME)
        self.assertEqual(path, self._TAPE_DIR)

    def test_fallback_raises_when_sam_has_no_locations(self):
        """ValueError is raised when the file is absent from stash and SAM finds nothing."""
        with patch('utils.samweb_wrapper.locate_file_strict', return_value=[]):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            with self.assertRaises(ValueError):
                job._resolver.locate(self._FNAME)

    def test_fallback_format_filename_applies_xroot(self):
        """_format_filename with proto='root' converts the SAM tape path to an xroot URL."""
        with patch('utils.samweb_wrapper.locate_file_strict',
                   return_value=self._sam_locations()):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='root')
            result = job._format_filename(self._FNAME)
        self.assertTrue(result.startswith("xroot://"),
                        f"Expected xroot URL for tape fallback, got: {result}")
        self.assertIn(self._FNAME, result)


# ---------------------------------------------------------------------------
# 18. _create_inputs_file exclude logic (json2jobdef.py)
# ---------------------------------------------------------------------------

class TestCreateInputsFileExclude(unittest.TestCase):
    """Verify that _create_inputs_file honours the exclude_files parameter."""

    def setUp(self):
        import tempfile
        self._orig_dir = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_dir)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_no_exclusion_writes_all(self):
        from utils.json2jobdef import _create_inputs_file
        all_files = [f"sim.mu2e.Test.TC.00000{i}.art" for i in range(5)]
        config = {'input_data': {'sim.mu2e.Test.TC.art': 1}}
        with patch('utils.json2jobdef.list_files', return_value=all_files):
            _create_inputs_file(config)
        written = Path('inputs.txt').read_text().strip().split('\n')
        self.assertEqual(written, all_files)

    def test_exclusion_removes_files(self):
        from utils.json2jobdef import _create_inputs_file
        all_files = [f"sim.mu2e.Test.TC.00000{i}.art" for i in range(5)]
        exclude = {all_files[1], all_files[3]}
        config = {'input_data': {'sim.mu2e.Test.TC.art': 1}}
        with patch('utils.json2jobdef.list_files', return_value=all_files):
            _create_inputs_file(config, exclude_files=exclude)
        written = Path('inputs.txt').read_text().strip().split('\n')
        self.assertEqual(len(written), 3)
        for f in exclude:
            self.assertNotIn(f, written)

    def test_exclude_all_produces_empty(self):
        from utils.json2jobdef import _create_inputs_file
        all_files = ["sim.mu2e.Test.TC.000000.art"]
        config = {'input_data': {'sim.mu2e.Test.TC.art': 1}}
        with patch('utils.json2jobdef.list_files', return_value=all_files):
            _create_inputs_file(config, exclude_files=set(all_files))
        content = Path('inputs.txt').read_text().strip()
        self.assertEqual(content, '')

    def test_empty_exclude_set_writes_all(self):
        from utils.json2jobdef import _create_inputs_file
        all_files = ["a.art", "b.art"]
        config = {'input_data': {'sim.mu2e.Test.TC.art': 1}}
        with patch('utils.json2jobdef.list_files', return_value=all_files):
            _create_inputs_file(config, exclude_files=set())
        written = Path('inputs.txt').read_text().strip().split('\n')
        self.assertEqual(written, all_files)


# ---------------------------------------------------------------------------
# 19. _next_version auto-increment (json2jobdef.py)
# ---------------------------------------------------------------------------

class TestNextVersion(unittest.TestCase):

    def _cfg(self, **extra):
        return {'owner': 'mu2e', 'desc': 'TestDesc', 'dsconf': 'TC', **extra}

    def test_no_existing_files_returns_zero(self):
        from utils.json2jobdef import _next_version
        with patch('utils.json2jobdef.files_in_dataset', return_value=[]):
            self.assertEqual(_next_version(self._cfg()), 0)

    def test_single_version_zero_returns_one(self):
        from utils.json2jobdef import _next_version
        with patch('utils.json2jobdef.files_in_dataset',
                   return_value=['cnf.mu2e.TestDesc.TC.0.tar']):
            self.assertEqual(_next_version(self._cfg()), 1)

    def test_multiple_versions_returns_next(self):
        from utils.json2jobdef import _next_version
        files = ['cnf.mu2e.TestDesc.TC.0.tar',
                 'cnf.mu2e.TestDesc.TC.1.tar',
                 'cnf.mu2e.TestDesc.TC.2.tar']
        with patch('utils.json2jobdef.files_in_dataset', return_value=files):
            self.assertEqual(_next_version(self._cfg()), 3)

    def test_sam_exception_returns_zero(self):
        from utils.json2jobdef import _next_version
        with patch('utils.json2jobdef.files_in_dataset', side_effect=Exception("SAM down")):
            self.assertEqual(_next_version(self._cfg()), 0)

    def test_non_sequential_versions(self):
        from utils.json2jobdef import _next_version
        files = ['cnf.mu2e.TestDesc.TC.0.tar', 'cnf.mu2e.TestDesc.TC.5.tar']
        with patch('utils.json2jobdef.files_in_dataset', return_value=files):
            self.assertEqual(_next_version(self._cfg()), 6)


# ---------------------------------------------------------------------------
# 20. _compute_extend_exclusions integration (json2jobdef.py)
# ---------------------------------------------------------------------------

class TestComputeExtendExclusions(unittest.TestCase):
    """Test the full extend exclusion logic with mocked SAM and fhicl-get."""

    def _cfg(self, **extra):
        return {
            'owner': 'mu2e',
            'desc': 'TestDesc',
            'dsconf': 'TC',
            'fcl': 'Production/JobConfig/test.fcl',
            'fcl_overrides': {
                'outputs.Out.fileName': 'mcs.mu2e.{desc}.version.sequencer.art'
            },
            **extra,
        }

    def test_exclusion_set_populated(self):
        from utils.json2jobdef import _compute_extend_exclusions
        parents = ['input_a.art', 'input_b.art']

        with patch('utils.json2jobdef.get_output_dataset_names',
                   return_value=['mcs.mu2e.TestDesc.TC.art']), \
             patch('utils.json2jobdef.parents_of_dataset',
                   return_value=parents), \
             patch('utils.json2jobdef.files_in_dataset', return_value=[]):
            cfg = self._cfg()
            result = _compute_extend_exclusions(cfg)

        self.assertEqual(result, set(parents))
        self.assertEqual(cfg['version'], 0)

    def test_version_incremented(self):
        from utils.json2jobdef import _compute_extend_exclusions

        with patch('utils.json2jobdef.get_output_dataset_names',
                   return_value=['mcs.mu2e.TestDesc.TC.art']), \
             patch('utils.json2jobdef.parents_of_dataset',
                   return_value=['parent.art']), \
             patch('utils.json2jobdef.files_in_dataset',
                   return_value=['cnf.mu2e.TestDesc.TC.0.tar']):
            cfg = self._cfg()
            _compute_extend_exclusions(cfg)

        self.assertEqual(cfg['version'], 1)

    def test_no_output_datasets_exits(self):
        from utils.json2jobdef import _compute_extend_exclusions

        with patch('utils.json2jobdef.get_output_dataset_names',
                   return_value=[]):
            with self.assertRaises(SystemExit):
                _compute_extend_exclusions(self._cfg())

    def test_multiple_output_datasets_union(self):
        from utils.json2jobdef import _compute_extend_exclusions

        with patch('utils.json2jobdef.get_output_dataset_names',
                   return_value=['ds1.art', 'ds2.art']), \
             patch('utils.json2jobdef.parents_of_dataset', side_effect=[
                 ['a.art', 'b.art'],     # parents of ds1
                 ['b.art', 'c.art'],     # parents of ds2
             ]), \
             patch('utils.json2jobdef.files_in_dataset', return_value=[]):
            cfg = self._cfg()
            result = _compute_extend_exclusions(cfg)

        self.assertEqual(result, {'a.art', 'b.art', 'c.art'})


# ---------------------------------------------------------------------------
# 21. get_output_dataset_names (jobdef.py) - mocked fhicl-get
# ---------------------------------------------------------------------------

class TestGetOutputDatasetNames(unittest.TestCase):

    def _cfg(self, **extra):
        return {
            'owner': 'mu2e',
            'desc': 'TestDesc',
            'dsconf': 'TC',
            'fcl': 'base.fcl',
            'fcl_overrides': {},
            **extra,
        }

    def test_single_output_module(self):
        from utils.jobdef import get_output_dataset_names

        def mock_fhicl_get(path, cmd, key=''):
            if cmd == '--names-in' and key == 'outputs':
                return 'Out'
            if cmd == '--sequence-of' and key == 'physics.end_paths':
                return 'output_stream'
            if cmd == '--sequence-of' and key == 'physics.output_stream':
                return 'Out'
            if cmd == '--atom-as' and key == 'outputs.Out.fileName':
                return 'mcs.mu2e.{desc}.version.sequencer.art'
            return ''

        with patch('utils.jobdef._run_fhicl_get', side_effect=mock_fhicl_get), \
             patch('utils.prod_utils.write_fcl_template'), \
             patch('os.path.exists', return_value=True), \
             patch('os.unlink'):
            result = get_output_dataset_names(self._cfg())

        self.assertEqual(result, ['mcs.mu2e.TestDesc.TC.art'])

    def test_no_outputs_section(self):
        from utils.jobdef import get_output_dataset_names
        import subprocess as sp

        def mock_fhicl_get(path, cmd, key=''):
            if cmd == '--names-in' and key == 'outputs':
                raise sp.CalledProcessError(1, 'fhicl-get')
            return ''

        with patch('utils.jobdef._run_fhicl_get', side_effect=mock_fhicl_get), \
             patch('utils.prod_utils.write_fcl_template'), \
             patch('os.path.exists', return_value=True), \
             patch('os.unlink'):
            result = get_output_dataset_names(self._cfg())

        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# 22. validate_jobdesc — three-way mode detection (runmu2e.py)
# ---------------------------------------------------------------------------

class TestValidateJobdesc(unittest.TestCase):

    def test_template_mode(self):
        from utils.runmu2e import validate_jobdesc
        jd = [{'fcl_template': 'base.fcl', 'setup_script': '/s/setup.sh',
               'inloc': 'tape', 'outputs': []}]
        self.assertEqual(validate_jobdesc(jd), 'template')

    def test_direct_input_mode(self):
        from utils.runmu2e import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.Reco.MDC2025af.0.tar',
               'inloc': 'tape', 'outputs': []}]
        self.assertEqual(validate_jobdesc(jd), 'direct_input')

    def test_normal_mode(self):
        from utils.runmu2e import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.T.TC.0.tar', 'njobs': 5,
               'inloc': 'tape', 'outputs': []}]
        self.assertFalse(validate_jobdesc(jd))

    def test_direct_input_is_truthy(self):
        """'direct_input' string must be truthy for backward-compatible if-checks."""
        from utils.runmu2e import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.Reco.MDC2025af.0.tar',
               'inloc': 'tape', 'outputs': []}]
        self.assertTrue(validate_jobdesc(jd))

    def test_normal_mode_is_falsy(self):
        from utils.runmu2e import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.T.TC.0.tar', 'njobs': 5,
               'inloc': 'tape', 'outputs': []}]
        self.assertFalse(validate_jobdesc(jd))

    def test_direct_input_multiple_entries_exits(self):
        from utils.runmu2e import validate_jobdesc
        jd = [
            {'tarball': 'a.tar', 'inloc': 'tape', 'outputs': []},
            {'tarball': 'b.tar', 'inloc': 'tape', 'outputs': []},
        ]
        with self.assertRaises(SystemExit):
            validate_jobdesc(jd)

    def test_direct_input_missing_outputs_exits(self):
        from utils.runmu2e import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.Reco.MDC2025af.0.tar', 'inloc': 'tape'}]
        with self.assertRaises(SystemExit):
            validate_jobdesc(jd)

    def test_normal_mode_missing_njobs_exits(self):
        """Entry without tarball: falls through to normal-mode validation which requires njobs."""
        from utils.runmu2e import validate_jobdesc
        jd = [{'inloc': 'tape', 'outputs': []}]  # no tarball, no njobs
        with self.assertRaises(SystemExit):
            validate_jobdesc(jd)

    def test_normal_mode_with_generic_entry_ignored(self):
        """Normal-mode jobdesc with a trailing generic tarball (no njobs) is valid."""
        from utils.runmu2e import validate_jobdesc
        jd = [
            {'tarball': 'a.tar', 'njobs': 100, 'inloc': 'tape', 'outputs': []},
            {'tarball': 'b.tar', 'njobs': 200, 'inloc': 'tape', 'outputs': []},
            {'tarball': 'cnf.mu2e.OnSpillTriggeredReco.MDC2025af.0.tar',
             'inloc': 'tape', 'outputs': []},  # generic - no njobs
        ]
        self.assertFalse(validate_jobdesc(jd))

    def test_empty_list_exits(self):
        from utils.runmu2e import validate_jobdesc
        with self.assertRaises(SystemExit):
            validate_jobdesc([])


# ---------------------------------------------------------------------------
# 23. job_outputs() override_desc / override_seq (direct-input mode)
# ---------------------------------------------------------------------------

def _generic_reco_jobpars(owner='mu2e', dsconf='MDC2025af_best_v1_3'):
    """Jobpars for a generic reco tarball: {desc} deferred in outfiles."""
    return {
        "code": "",
        "setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025af/setup.sh",
        "tbs": {
            "seed": "services.SeedService.baseSeed",
            "subrunkey": "",
            "event_id": {"source.maxEvents": 2147483647},
            "outfiles": {
                "outputs.LoopHelixOutput.fileName":
                    f"mcs.{owner}.{{desc}}.{dsconf}.sequencer.art"
            },
        },
        "jobname": f"cnf.{owner}.OnSpillTriggeredReco.{dsconf}.0.tar",
        "owner": owner,
        "dsconf": dsconf,
    }


class TestJobOutputsOverride(unittest.TestCase):

    def test_override_seq_used_instead_of_computed(self):
        """override_seq must appear in the output filename."""
        from utils.jobfcl import Mu2eJobFCL
        jp = _generic_reco_jobpars()
        tar = _make_tarball(jp, "#include \"OnSpill.fcl\"\n")
        try:
            job = Mu2eJobFCL(tar, inloc='tape')
            outputs = job.job_outputs(0, override_seq='001430_00000042')
            out_file = outputs['outputs.LoopHelixOutput.fileName']
            self.assertIn('001430_00000042', out_file)
        finally:
            os.unlink(tar)

    def test_override_desc_replaces_desc_placeholder(self):
        """{desc} in outfile template is replaced by override_desc."""
        from utils.jobfcl import Mu2eJobFCL
        jp = _generic_reco_jobpars()
        tar = _make_tarball(jp, "#include \"OnSpill.fcl\"\n")
        try:
            job = Mu2eJobFCL(tar, inloc='tape')
            outputs = job.job_outputs(
                0,
                override_desc='CeEndpointOnSpillTriggered',
                override_seq='001430_00000042'
            )
            out_file = outputs['outputs.LoopHelixOutput.fileName']
            self.assertIn('CeEndpointOnSpillTriggered', out_file)
            self.assertNotIn('{desc}', out_file)
        finally:
            os.unlink(tar)

    def test_different_override_desc_yields_different_output(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _generic_reco_jobpars()
        tar = _make_tarball(jp, "#include \"OnSpill.fcl\"\n")
        try:
            job = Mu2eJobFCL(tar, inloc='tape')
            out_a = job.job_outputs(0, override_desc='CeEndpoint', override_seq='001430_00000001')
            out_b = job.job_outputs(0, override_desc='CosmicSignal', override_seq='001430_00000001')
            self.assertNotEqual(
                out_a['outputs.LoopHelixOutput.fileName'],
                out_b['outputs.LoopHelixOutput.fileName']
            )
        finally:
            os.unlink(tar)

    def test_output_follows_six_part_mu2e_convention(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _generic_reco_jobpars()
        tar = _make_tarball(jp, "#include \"OnSpill.fcl\"\n")
        try:
            job = Mu2eJobFCL(tar, inloc='tape')
            outputs = job.job_outputs(
                0,
                override_desc='CeEndpointMix1BBTriggered',
                override_seq='001430_00000042'
            )
            out_file = outputs['outputs.LoopHelixOutput.fileName']
            parts = out_file.split('.')
            self.assertEqual(len(parts), 6)
            self.assertEqual(parts[0], 'mcs')
            self.assertEqual(parts[1], 'mu2e')
            self.assertEqual(parts[2], 'CeEndpointMix1BBTriggered')
            self.assertEqual(parts[4], '001430_00000042')
            self.assertEqual(parts[5], 'art')
        finally:
            os.unlink(tar)

    def test_no_overrides_backward_compatible(self):
        """Existing callers with no overrides must still work."""
        from utils.jobfcl import Mu2eJobFCL
        jp = _empty_event_jobpars(run=1430)
        tar = _make_tarball(jp, "module_type : EmptyEvent\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            outputs = job.job_outputs(3)
            out_file = outputs['outputs.PrimaryOutput.fileName']
            self.assertIn('001430_00000003', out_file)
        finally:
            os.unlink(tar)


class TestGenericTarballGuard(unittest.TestCase):
    """A generic tarball deliberately leaves {desc} (and sequencer) unresolved
    in its outfiles for runtime/direct-input substitution. The build-time
    validate_output_filenames guard must NOT be run against it — it would see
    the literal {desc}/sequencer and abort. These tests pin both halves: the
    guard does raise on a deferred cnf (so skipping it is load-bearing), and
    build_jobdef actually skips it when generic_tarball is set."""

    def test_guard_raises_on_deferred_desc(self):
        from utils.jobfcl import validate_output_filenames
        jp = _generic_reco_jobpars()  # outfiles keep literal {desc}
        tar = _make_tarball(jp, "#include \"OnSpill.fcl\"\n")
        try:
            with self.assertRaises(ValueError):
                validate_output_filenames(tar)
        finally:
            os.unlink(tar)

    def test_build_skips_guard_for_generic_tarball(self):
        """build_jobdef must NOT call validate_output_filenames when
        generic_tarball is set -- the deferred {desc}/sequencer cannot resolve
        at build time, so running the guard would abort the build."""
        from unittest.mock import patch
        from utils import json2jobdef
        with patch.object(json2jobdef, 'validate_output_filenames') as guard, \
             patch.object(json2jobdef, 'create_jobdef'), \
             patch.object(json2jobdef, 'get_parfile_name', return_value='cnf.x.0.tar'), \
             patch.object(json2jobdef, 'append_jobdef'):
            cfg = {'desc': 'reco', 'dsconf': 'D', 'owner': 'mu2e',
                   'simjob_setup': 's', 'inloc': 'tape', 'generic_tarball': True,
                   'fcl': 'f.fcl', 'outloc': {'*.art': 'tape'}}
            try:
                json2jobdef.build_jobdef(cfg, job_args=[])
            except Exception:
                pass  # downstream packaging is mocked/partial; we only assert the guard
            guard.assert_not_called()


def _perdesc_mcs_jobpars(desc='CeEndpoint', dsconf='TestConf'):
    """A normal (non-generic) reco-style cnf: concrete output desc, RootInput so
    the sequencer resolves from the input file."""
    return {
        "code": "", "setup": f"/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/{dsconf}/setup.sh",
        "tbs": {
            "seed": "services.SeedService.baseSeed", "subrunkey": "",
            "event_id": {"source.maxEvents": 2147483647},
            "outfiles": {"outputs.LoopHelixOutput.fileName":
                         f"mcs.mu2e.{desc}.{dsconf}.sequencer.art"},
            "inputs": {"source.fileNames":
                       [1, [f"dig.mu2e.{desc}.{dsconf}.001430_00000000.art"]]},
            "sequential_aux": False,
        },
        "jobname": f"cnf.mu2e.{desc}.{dsconf}.0.tar", "owner": "mu2e", "dsconf": dsconf,
    }


class TestGenericCnfDiscovery(unittest.TestCase):
    """A generic cnf (output desc deferred as {desc}) must be discoverable by
    the dataset->cnf matcher as a LAST resort: exact per-desc cnfs always win,
    a generic cnf in the candidate list must not crash the scan, and fcldump
    flags a generic match (is_generic_cnf) so it reports instead of generating."""

    def test_generic_desc_matches(self):
        from utils.jobdef_lookup import _generic_desc_matches
        self.assertTrue(_generic_desc_matches('{desc}-KL', 'CeEndpoint-KL'))
        self.assertTrue(_generic_desc_matches('{desc}', 'AnythingAtAll'))
        self.assertFalse(_generic_desc_matches('{desc}-KL', 'CeEndpoint-CH'))
        self.assertFalse(_generic_desc_matches('{desc}-KL', 'CeEndpoint'))

    def test_is_generic_cnf_true(self):
        from utils.jobdef_lookup import is_generic_cnf
        tar = _make_tarball(_generic_reco_jobpars(), "#include \"OnSpill.fcl\"\n")
        try:
            self.assertTrue(is_generic_cnf(tar))
        finally:
            os.unlink(tar)

    def test_is_generic_cnf_false_for_resolved(self):
        from utils.jobdef_lookup import is_generic_cnf
        tar = _make_tarball(_perdesc_mcs_jobpars(), "#include \"OnSpill.fcl\"\n")
        try:
            self.assertFalse(is_generic_cnf(tar))
        finally:
            os.unlink(tar)

    def test_generic_fallback_match(self):
        """Only a generic cnf in the list -> matched via the {desc} template."""
        from unittest.mock import patch
        from utils import jobdef_lookup
        tar = _make_tarball(_generic_reco_jobpars(), "#include \"OnSpill.fcl\"\n")
        try:
            with patch.object(jobdef_lookup, 'locate_tarball', return_value=tar):
                result = jobdef_lookup.find_matching_jobdef(
                    ['cnf.mu2e.reco.TestConf.0.tar'], 'CeEndpointOnSpill', 'mcs')
            self.assertEqual(result, tar)
        finally:
            os.unlink(tar)

    def test_exact_wins_and_generic_does_not_crash(self):
        """Per-desc cnf present alongside a generic one -> exact wins, and the
        generic cnf in the candidate list does not abort the scan."""
        from unittest.mock import patch
        from utils import jobdef_lookup
        perdesc = _make_tarball(_perdesc_mcs_jobpars(desc='CeEndpoint'),
                                "#include \"OnSpill.fcl\"\n")
        generic = _make_tarball(_generic_reco_jobpars(dsconf='TestConf'),
                                "#include \"OnSpill.fcl\"\n")
        mapping = {'cnf.mu2e.CeEndpoint.TestConf.0.tar': perdesc,
                   'cnf.mu2e.reco.TestConf.0.tar': generic}
        try:
            with patch.object(jobdef_lookup, 'locate_tarball',
                              side_effect=lambda j: mapping[j]):
                result = jobdef_lookup.find_matching_jobdef(
                    list(mapping.keys()), 'CeEndpoint', 'mcs')
            self.assertEqual(result, perdesc)
        finally:
            os.unlink(perdesc)
            os.unlink(generic)

    def test_generic_desc_capture(self):
        from utils.jobdef_lookup import _generic_desc_capture
        self.assertEqual(_generic_desc_capture('{desc}-KL', 'CeEndpoint-KL'), 'CeEndpoint')
        self.assertEqual(_generic_desc_capture('{desc}', 'FlatGamma'), 'FlatGamma')
        self.assertIsNone(_generic_desc_capture('{desc}-KL', 'CeEndpoint-CH'))
        self.assertIsNone(_generic_desc_capture('NoPlaceholder', 'NoPlaceholder'))

    def test_derive_generic_input_from_target(self):
        """--target output file -> input file: strip suffix to input desc, map
        tier (mcs->dig), find input in SAM by desc+seq (any dsconf)."""
        from unittest.mock import patch
        from utils import jobdef_lookup
        # generic cnf with a {desc}-KL mcs output template
        jp = _generic_reco_jobpars()
        jp['tbs']['outfiles'] = {"outputs.KinematicLineOutput.fileName":
                                 "mcs.mu2e.{desc}-KL.Run1Ban_best_v1_4-000.sequencer.art"}
        tar = _make_tarball(jp, "#include \"OnSpill.fcl\"\n")
        target = "mcs.mu2e.CeEndpoint-KL.Run1Ban_best_v1_4-000.001470_00000004.art"
        # input dig at a DIFFERENT dsconf than the output (the realistic case)
        dig = "dig.mu2e.CeEndpoint.Run1Bai_best_v1_4-000.001470_00000004.art"
        try:
            with patch('utils.samweb_wrapper.files_like', return_value=[dig]) as fl:
                got = jobdef_lookup.derive_generic_input(tar, target)
            self.assertEqual(got, dig)
            # queried the dig tier (mcs->dig) for the stripped desc + exact seq
            pattern = fl.call_args[0][0]
            self.assertIn("dig.mu2e.CeEndpoint.", pattern)
            self.assertEqual(fl.call_args[1].get('sequencer'), "001470_00000004")
        finally:
            os.unlink(tar)

    def test_derive_generic_input_no_match_raises(self):
        from unittest.mock import patch
        from utils import jobdef_lookup
        tar = _make_tarball(_generic_reco_jobpars(), "#include \"OnSpill.fcl\"\n")
        try:
            with patch('utils.samweb_wrapper.files_like', return_value=[]):
                with self.assertRaises(ValueError):
                    jobdef_lookup.derive_generic_input(
                        tar, "mcs.mu2e.X.MDC2025af_best_v1_3.001430_00000001.art")
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 24. _replace_placeholders defer_keys (jobdef.py)
# ---------------------------------------------------------------------------

class TestReplacePlaceholdersDeferKeys(unittest.TestCase):

    def _rp(self, pattern, config, defer_keys=None):
        from utils.jobdef import _replace_placeholders
        return _replace_placeholders(pattern, config, defer_keys=defer_keys)

    def test_desc_replaced_without_defer(self):
        result = self._rp(
            "mcs.mu2e.{desc}.TC.sequencer.art",
            {'desc': 'CeEndpoint', 'dsconf': 'TC'}
        )
        self.assertEqual(result, "mcs.mu2e.CeEndpoint.TC.sequencer.art")

    def test_desc_not_replaced_with_defer(self):
        result = self._rp(
            "mcs.mu2e.{desc}.TC.sequencer.art",
            {'desc': 'CeEndpoint', 'dsconf': 'TC'},
            defer_keys={'desc'}
        )
        self.assertIn('{desc}', result)
        self.assertNotIn('CeEndpoint', result)

    def test_other_keys_still_replaced_when_desc_deferred(self):
        """Deferring desc must not block substitution of other keys."""
        result = self._rp(
            "mcs.mu2e.{desc}.{dsconf}.sequencer.art",
            {'desc': 'CeEndpoint', 'dsconf': 'TestConf'},
            defer_keys={'desc'}
        )
        self.assertIn('{desc}', result)
        self.assertIn('TestConf', result)
        self.assertNotIn('{dsconf}', result)

    def test_none_defer_keys_behaves_like_empty_set(self):
        result = self._rp(
            "mcs.mu2e.{desc}.{dsconf}.sequencer.art",
            {'desc': 'CeEndpoint', 'dsconf': 'TC'},
            defer_keys=None
        )
        self.assertNotIn('{desc}', result)
        self.assertNotIn('{dsconf}', result)

    def test_empty_defer_keys_replaces_all(self):
        result = self._rp(
            "mcs.mu2e.{desc}.{dsconf}.sequencer.art",
            {'desc': 'CeEndpoint', 'dsconf': 'TC'},
            defer_keys=set()
        )
        self.assertNotIn('{desc}', result)
        self.assertNotIn('{dsconf}', result)


# ---------------------------------------------------------------------------
# 25. process_direct_input (runmu2e.py)
# ---------------------------------------------------------------------------

class TestProcessDirectInput(unittest.TestCase):
    """End-to-end tests for process_direct_input() with a real in-memory tarball."""

    def setUp(self):
        import tempfile
        self._orig_dir = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)
        self._tar = _make_tarball(
            _generic_reco_jobpars(),
            "#include \"Production/JobConfig/recoMC/OnSpill.fcl\"\n"
        )

    def tearDown(self):
        os.chdir(self._orig_dir)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, fname):
        from utils.runmu2e import process_direct_input
        jobdesc = [{
            'tarball': self._tar,
            'inloc': 'tape',
            'outputs': [{'dataset': '*.art', 'location': 'disk'}],
        }]
        with patch('utils.jobquery.Mu2eJobPars') as mock_pars:
            mock_pars.return_value.setup.return_value = \
                "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025af/setup.sh"
            return process_direct_input(jobdesc, fname, MagicMock())

    def test_returns_four_tuple(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        result = self._run(fname)
        self.assertEqual(len(result), 4)

    def test_fcl_named_after_input_stem(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        fcl, _, _, _ = self._run(fname)
        self.assertEqual(
            fcl,
            "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.fcl"
        )

    def test_fcl_file_written_to_disk(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        fcl, _, _, _ = self._run(fname)
        self.assertTrue(os.path.isfile(fcl))

    def test_fcl_contains_source_file_names_with_input(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        fcl, _, _, _ = self._run(fname)
        content = Path(fcl).read_text()
        self.assertIn('source.fileNames', content)
        self.assertIn(fname, content)

    def test_fcl_contains_output_fileName_override(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        fcl, _, _, _ = self._run(fname)
        content = Path(fcl).read_text()
        self.assertIn('outputs.LoopHelixOutput.fileName', content)

    def test_output_filename_uses_desc_from_fname(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        fcl, _, _, _ = self._run(fname)
        content = Path(fcl).read_text()
        self.assertIn('CeEndpointOnSpillTriggered', content)

    def test_output_filename_uses_seq_from_fname(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        fcl, _, _, _ = self._run(fname)
        content = Path(fcl).read_text()
        self.assertIn('001430_00000042', content)

    def test_different_input_desc_gives_different_output_filename(self):
        fname_a = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000001.art"
        fname_b = "dig.mu2e.CosmicSignalMix1BBTriggered.MDC2025af_best_v1_3.001430_00000001.art"
        fcl_a, _, _, _ = self._run(fname_a)
        fcl_b, _, _, _ = self._run(fname_b)
        content_a = Path(fcl_a).read_text()
        content_b = Path(fcl_b).read_text()
        # Each FCL must mention only its own desc in the output filename
        self.assertIn('CeEndpointOnSpillTriggered', content_a)
        self.assertIn('CosmicSignalMix1BBTriggered', content_b)

    def test_infiles_is_fname(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        _, _, infiles, _ = self._run(fname)
        self.assertEqual(infiles, fname)

    def test_outputs_from_jobdesc(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        _, _, _, outputs = self._run(fname)
        self.assertEqual(outputs, [{'dataset': '*.art', 'location': 'disk'}])

    def test_setup_script_returned(self):
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        _, simjob_setup, _, _ = self._run(fname)
        self.assertIn('/cvmfs/', simjob_setup)

    def test_bad_fname_format_exits(self):
        from utils.runmu2e import process_direct_input
        jobdesc = [{'tarball': self._tar, 'inloc': 'tape', 'outputs': []}]
        with self.assertRaises(SystemExit):
            process_direct_input(jobdesc, "only.four.parts.art", MagicMock())

    def test_base_fcl_content_included(self):
        """The FCL from the tarball's mu2e.fcl must appear before the overrides."""
        fname = "dig.mu2e.CeEndpointOnSpillTriggered.MDC2025af_best_v1_3.001430_00000042.art"
        fcl, _, _, _ = self._run(fname)
        content = Path(fcl).read_text()
        # The tarball mu2e.fcl starts with an #include
        self.assertIn('#include', content)
        # Direct-input overrides must come after base content
        override_pos = content.find('source.fileNames')
        include_pos = content.find('#include')
        self.assertGreater(override_pos, include_pos)


# ---------------------------------------------------------------------------
# N. calculate_merge_factor — split_lines branch
# ---------------------------------------------------------------------------

class TestCalculateMergeFactorSplitLines(unittest.TestCase):
    """Guard the `split_lines → merge_factor = 1` branch added with the
    text-file splitting input_data shape. Also smoke-test the existing
    shapes to catch accidental regressions."""

    def test_split_lines_returns_one(self):
        from utils.prod_utils import calculate_merge_factor
        config = {"input_data": {"/cvmfs/PBI_Normal.txt": {"split_lines": 1000}}}
        self.assertEqual(calculate_merge_factor(config), 1)

    def test_split_lines_value_is_ignored(self):
        # Any N-line split yields one chunk per job; chunk size doesn't
        # change the merge factor.
        from utils.prod_utils import calculate_merge_factor
        for n in (1, 500, 10000):
            config = {"input_data": {"/cvmfs/x.txt": {"split_lines": n}}}
            self.assertEqual(calculate_merge_factor(config), 1)

    def test_plain_int_still_returned(self):
        from utils.prod_utils import calculate_merge_factor
        config = {"input_data": {"dts.mu2e.X.Y.art": 5}}
        self.assertEqual(calculate_merge_factor(config), 5)

    def test_count_form_still_works(self):
        from utils.prod_utils import calculate_merge_factor
        config = {"input_data": {"dts.mu2e.X.Y.art": {"count": 7, "random": True}}}
        self.assertEqual(calculate_merge_factor(config), 7)

    def test_unknown_dict_spec_raises(self):
        from utils.prod_utils import calculate_merge_factor
        config = {"input_data": {"dts.mu2e.X.Y.art": {"foo": "bar"}}}
        with self.assertRaises(ValueError):
            calculate_merge_factor(config)


# ---------------------------------------------------------------------------
# N+1. Mu2eJobFCL.sequencer — source.runNumber short-circuit
# ---------------------------------------------------------------------------

def _pbi_sequence_jobpars(run=1430, files=None, owner='mu2e', dsconf='TestConf'):
    """Return a jobpars.json dict suitable for a PBISequence job.

    event_id uses `source.runNumber` (the key PBISequence accepts) rather
    than `source.firstRun` (EmptyEvent/RootInput convention). subrunkey
    is empty — PBISequence doesn't accept per-job subrun overrides.
    """
    return {
        "code": "",
        "setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/TestConf/setup.sh",
        "tbs": {
            "seed": "services.SeedService.baseSeed",
            "subrunkey": "",
            "event_id": {"source.runNumber": run},
            "outfiles": {
                "outputs.PrimaryOutput.fileName":
                    f"dts.{owner}.TestDesc.{dsconf}.sequencer.art"
            },
            "inputs": {"source.fileNames": [1, files or ["PBI_Normal.txt"]]},
        },
        "jobname": f"cnf.{owner}.TestDesc.{dsconf}.0.tar",
        "owner": owner,
        "dsconf": dsconf,
    }


class TestSequencerRunNumber(unittest.TestCase):
    """sequencer() short-circuits on explicit run keys before trying to
    parse input filenames as Mu2e names. Recognized keys:
        - source.firstRun (EmptyEvent / RootInput)
        - source.run      (SamplingInput)
        - source.runNumber (PBISequence — added 2026-04-21)
    """

    def test_runNumber_produces_mu2e_standard_sequencer(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = _pbi_sequence_jobpars(run=1430)
        tar = _make_tarball(jp, "module_type : PBISequence\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            self.assertEqual(job.sequencer(0), "001430_00000000")
            self.assertEqual(job.sequencer(5), "001430_00000005")
            self.assertEqual(job.sequencer(42), "001430_00000042")
        finally:
            os.unlink(tar)

    def test_runNumber_bypasses_filename_parsing(self):
        # Input filename that would fail Mu2eName parsing — verifies
        # the short-circuit fires before the fallback path.
        from utils.jobfcl import Mu2eJobFCL
        jp = _pbi_sequence_jobpars(run=1430, files=["not-a-mu2e-name.txt"])
        tar = _make_tarball(jp, "module_type : PBISequence\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            # If the short-circuit broke, this would raise when parsing
            # the non-conforming basename.
            self.assertEqual(job.sequencer(0), "001430_00000000")
        finally:
            os.unlink(tar)

    def test_firstRun_and_runNumber_agree(self):
        # Different event_id keys should produce the same sequencer for
        # the same run+index.
        from utils.jobfcl import Mu2eJobFCL
        jp_first = _empty_event_jobpars(run=1430)
        jp_num = _pbi_sequence_jobpars(run=1430)
        tar_first = _make_tarball(jp_first, "module_type : EmptyEvent\n")
        tar_num = _make_tarball(jp_num, "module_type : PBISequence\n")
        try:
            job_first = Mu2eJobFCL(tar_first, inloc='dir:/tmp')
            job_num = Mu2eJobFCL(tar_num, inloc='dir:/tmp')
            for index in (0, 3, 100):
                self.assertEqual(job_first.sequencer(index), job_num.sequencer(index))
        finally:
            os.unlink(tar_first)
            os.unlink(tar_num)


# ---------------------------------------------------------------------------
# N+2. Mu2eJobFCL.job_event_settings — event_id_per_index linear overrides
# ---------------------------------------------------------------------------

class TestEventIdPerIndex(unittest.TestCase):
    """Per-index linear overrides on event_id fields. Schema:
        tbs.event_id_per_index = { fcl_key: { offset, step } }
    Evaluated per job as: result[fcl_key] = offset + index * step.
    Applied after base event_id and subrunkey so per-index overrides win.
    """

    def _jobpars_with_per_index(self, per_index, event_id=None, subrunkey=''):
        jp = _pbi_sequence_jobpars(run=1430)
        jp["tbs"]["subrunkey"] = subrunkey
        if event_id is not None:
            jp["tbs"]["event_id"] = event_id
        jp["tbs"]["event_id_per_index"] = per_index
        return jp

    def test_linear_override_applied_per_index(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = self._jobpars_with_per_index({
            "source.firstEventNumber": {"offset": 0, "step": 1000},
        })
        tar = _make_tarball(jp, "module_type : PBISequence\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            self.assertEqual(job.job_event_settings(0)["source.firstEventNumber"], 0)
            self.assertEqual(job.job_event_settings(5)["source.firstEventNumber"], 5000)
            self.assertEqual(job.job_event_settings(25)["source.firstEventNumber"], 25000)
        finally:
            os.unlink(tar)

    def test_nonzero_offset(self):
        from utils.jobfcl import Mu2eJobFCL
        jp = self._jobpars_with_per_index({
            "source.firstEventNumber": {"offset": 42, "step": 10},
        })
        tar = _make_tarball(jp, "module_type : PBISequence\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            self.assertEqual(job.job_event_settings(3)["source.firstEventNumber"], 42 + 30)
        finally:
            os.unlink(tar)

    def test_missing_step_defaults_to_zero(self):
        # A spec with only offset should treat step as 0 (i.e. constant).
        from utils.jobfcl import Mu2eJobFCL
        jp = self._jobpars_with_per_index({
            "source.firstEventNumber": {"offset": 100},
        })
        tar = _make_tarball(jp, "module_type : PBISequence\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            self.assertEqual(job.job_event_settings(0)["source.firstEventNumber"], 100)
            self.assertEqual(job.job_event_settings(9)["source.firstEventNumber"], 100)
        finally:
            os.unlink(tar)

    def test_overrides_base_event_id_on_same_key(self):
        # If event_id fixes a value and event_id_per_index names the same
        # key, the per-index computation wins.
        from utils.jobfcl import Mu2eJobFCL
        jp = self._jobpars_with_per_index(
            per_index={"source.firstEventNumber": {"offset": 0, "step": 500}},
            event_id={"source.runNumber": 1430, "source.firstEventNumber": 999},
        )
        tar = _make_tarball(jp, "module_type : PBISequence\n")
        try:
            job = Mu2eJobFCL(tar, inloc='dir:/tmp')
            self.assertEqual(job.job_event_settings(2)["source.firstEventNumber"], 1000)
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# N+3. json2jobdef._configure_chunk_mode — chunk-on-grid submit-side logic
# ---------------------------------------------------------------------------

class TestConfigureChunkMode(unittest.TestCase):
    """Submit-side logic for `input_data = {<path>: {"chunk_lines": N}}`.

    Counts lines, computes njobs=ceil(lines/N), records chunk_mode
    metadata in config, and auto-injects the `source.fileNames`
    fcl_override so every job's FCL references the (per-worker-local)
    chunk file.
    """

    def _make_source(self, nlines):
        import tempfile
        f = tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt')
        for i in range(nlines):
            f.write(f"{i}\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def _base_config(self, src, chunk_lines):
        return {
            "desc": "TestDesc",
            "dsconf": "TestConf",
            "owner": "mu2e",
            "input_data": {src: {"chunk_lines": chunk_lines}},
        }

    def test_computes_njobs_exactly_divisible(self):
        from utils.json2jobdef import _configure_chunk_mode
        src = self._make_source(nlines=5000)
        cfg = self._base_config(src, chunk_lines=1000)
        _configure_chunk_mode(cfg)
        self.assertEqual(cfg['njobs'], 5)

    def test_computes_njobs_with_remainder(self):
        # 25438 / 1000 = 25 full chunks + 1 short → 26 jobs
        from utils.json2jobdef import _configure_chunk_mode
        src = self._make_source(nlines=25438)
        cfg = self._base_config(src, chunk_lines=1000)
        _configure_chunk_mode(cfg)
        self.assertEqual(cfg['njobs'], 26)

    def test_records_chunk_mode_metadata(self):
        from utils.json2jobdef import _configure_chunk_mode
        src = self._make_source(nlines=100)
        cfg = self._base_config(src, chunk_lines=40)
        _configure_chunk_mode(cfg)
        cm = cfg['chunk_mode']
        self.assertEqual(cm['source'], src)
        self.assertEqual(cm['lines'], 40)
        self.assertEqual(cm['local_filename'], 'chunk.txt')

    def test_auto_injects_source_filenames_override(self):
        from utils.json2jobdef import _configure_chunk_mode
        src = self._make_source(nlines=100)
        cfg = self._base_config(src, chunk_lines=50)
        _configure_chunk_mode(cfg)
        self.assertEqual(cfg['fcl_overrides']['source.fileNames'], ['chunk.txt'])

    def test_does_not_clobber_existing_source_filenames_override(self):
        # setdefault: if user set source.fileNames already, respect it.
        from utils.json2jobdef import _configure_chunk_mode
        src = self._make_source(nlines=100)
        cfg = self._base_config(src, chunk_lines=50)
        cfg['fcl_overrides'] = {'source.fileNames': ['user_chunk.txt']}
        _configure_chunk_mode(cfg)
        self.assertEqual(cfg['fcl_overrides']['source.fileNames'], ['user_chunk.txt'])

    def test_rejects_zero_chunk_lines(self):
        from utils.json2jobdef import _configure_chunk_mode
        src = self._make_source(nlines=100)
        cfg = self._base_config(src, chunk_lines=0)
        with self.assertRaises(ValueError):
            _configure_chunk_mode(cfg)

    def test_rejects_negative_chunk_lines(self):
        from utils.json2jobdef import _configure_chunk_mode
        src = self._make_source(nlines=100)
        cfg = self._base_config(src, chunk_lines=-5)
        with self.assertRaises(ValueError):
            _configure_chunk_mode(cfg)

    def test_rejects_missing_source_file(self):
        from utils.json2jobdef import _configure_chunk_mode
        cfg = self._base_config("/nonexistent/path/foo.txt", chunk_lines=100)
        with self.assertRaises(ValueError):
            _configure_chunk_mode(cfg)

    def test_rejects_multiple_sources(self):
        from utils.json2jobdef import _configure_chunk_mode
        src1 = self._make_source(nlines=10)
        src2 = self._make_source(nlines=20)
        cfg = {
            "desc": "TestDesc", "dsconf": "TestConf", "owner": "mu2e",
            "input_data": {src1: {"chunk_lines": 5}, src2: {"chunk_lines": 5}},
        }
        with self.assertRaises(ValueError):
            _configure_chunk_mode(cfg)


# ---------------------------------------------------------------------------
# 30. jobdef_lookup: dataset → cnf resolution (reused by fcldump + latestDatasets)
# ---------------------------------------------------------------------------

def _cnf_with_output(output_filename, run=1430, njobs=None):
    """In-memory cnf whose single declared output (after sequencer substitution)
    is `output_filename` with the `.sequencer.` token replaced by a real
    sequencer. Used to exercise the output-name match in find_matching_jobdef.
    Pass `njobs` to pin an explicit job count in jobpars."""
    jp = {
        "code": "",
        "setup": "/cvmfs/test/setup.sh",
        "tbs": {
            "seed": "services.SeedService.baseSeed",
            "subrunkey": "source.firstSubRun",
            "event_id": {"source.firstRun": run, "source.maxEvents": 100},
            "outfiles": {"outputs.Output.fileName": output_filename},
        },
        "jobname": "cnf.mu2e.X.TC.0.tar",
        "owner": "mu2e",
        "dsconf": "TC",
    }
    if njobs is not None:
        jp["tbs"]["njobs"] = njobs
    return _make_tarball(jp, "module_type : EmptyEvent\n")


class TestJobdefLookup(unittest.TestCase):

    def test_input_type_required(self):
        from utils.jobdef_lookup import find_matching_jobdef
        with self.assertRaises(ValueError):
            find_matching_jobdef([], "X", input_type=None)

    def test_fast_path_1to1_desc(self):
        """cnf desc == output desc: matched on the fast (name-filter) pass."""
        from utils import jobdef_lookup
        tar = _cnf_with_output("dig.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.sequencer.art")
        try:
            with patch.object(jobdef_lookup, 'locate_tarball', return_value=tar):
                res = jobdef_lookup.find_matching_jobdef(
                    ["cnf.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.0.tar"],
                    "CeEndpointOnSpill", input_type="dig")
            self.assertEqual(res, tar)
        finally:
            os.unlink(tar)

    def test_fallback_suffixed_output(self):
        """cnf desc 'CeEndpoint' produces 'CeEndpointOnSpill' output: matched on
        the fallback pass that scans declared outputs (the suffix case)."""
        from utils import jobdef_lookup
        tar = _cnf_with_output("dig.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.sequencer.art")
        try:
            with patch.object(jobdef_lookup, 'locate_tarball', return_value=tar):
                res = jobdef_lookup.find_matching_jobdef(
                    ["cnf.mu2e.CeEndpoint.MDC2025ap_best_v1_1.0.tar"],
                    "CeEndpointOnSpill", input_type="dig")
            self.assertEqual(res, tar)
        finally:
            os.unlink(tar)

    def test_no_match_returns_none(self):
        from utils import jobdef_lookup
        tar = _cnf_with_output("dig.mu2e.Other.MDC2025ap_best_v1_1.sequencer.art")
        try:
            with patch.object(jobdef_lookup, 'locate_tarball', return_value=tar):
                res = jobdef_lookup.find_matching_jobdef(
                    ["cnf.mu2e.Other.MDC2025ap_best_v1_1.0.tar"],
                    "CeEndpointOnSpill", input_type="dig")
            self.assertIsNone(res)
        finally:
            os.unlink(tar)

    def test_wrong_tier_not_matched(self):
        """Output desc matches but tier differs from input_type → no match."""
        from utils import jobdef_lookup
        tar = _cnf_with_output("mcs.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.sequencer.art")
        try:
            with patch.object(jobdef_lookup, 'locate_tarball', return_value=tar):
                res = jobdef_lookup.find_matching_jobdef(
                    ["cnf.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.0.tar"],
                    "CeEndpointOnSpill", input_type="dig")
            self.assertIsNone(res)
        finally:
            os.unlink(tar)

    def test_output_njobs_map(self):
        """Batch map: each cnf scanned once → {(output desc, tier): njobs}."""
        from utils import jobdef_lookup
        tar = _cnf_with_output(
            "dig.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.sequencer.art", njobs=20)
        try:
            with patch.object(jobdef_lookup, 'list_jobdefs',
                              return_value=["cnf.mu2e.CeEndpoint.MDC2025ap_best_v1_1.0.tar"]), \
                 patch.object(jobdef_lookup, 'locate_tarball', return_value=tar):
                m = jobdef_lookup.output_njobs_map("MDC2025ap_best_v1_1")
            self.assertEqual(m.get(("CeEndpointOnSpill", "dig")), 20)
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 31b. chain_emit: per-description merge factor in one entry
# ---------------------------------------------------------------------------

class TestChainEmitDescMapping(unittest.TestCase):
    """`desc` as a {name: settings} mapping — the preferred shape. Keeps
    the merge factor in exactly one place and drops the repeated "desc"
    key that the list-of-dicts form required."""

    TEMPLATE = [{
        "desc": {
            "CeMLeadingLog": 4,
            "NoPrimary": {"merge": 5,
                          "fcl_overrides": {"#include": "mixing/NoPrimary.fcl"}},
        },
        "input_data": ["dts.mu2e.{desc}.{campaign}.art"],
        "dsconf": ["{out_campaign}_best_v1_3"],
        "pbeam": ["Mix1BB"],
        "inloc": ["resilient"],
        "simjob_setup": ["/cvmfs/x/{out_campaign}/setup.sh"],
        "fcl_overrides": [{"services.DbService.version": "v1_3"}],
    }]

    def test_explicit_descs_reads_mapping(self):
        from utils import chain_emit
        self.assertEqual(chain_emit.explicit_descriptions(self.TEMPLATE),
                         ["CeMLeadingLog", "NoPrimary"])

    def test_scalar_value_is_the_merge_factor(self):
        from utils import chain_emit
        self.assertEqual(chain_emit._input_merge(self.TEMPLATE[0], "CeMLeadingLog"), 4)

    def test_dict_value_carries_merge_and_overrides(self):
        from utils import chain_emit
        self.assertEqual(chain_emit._input_merge(self.TEMPLATE[0], "NoPrimary"), 5)

    def test_input_data_is_bare_pattern(self):
        from utils import chain_emit
        self.assertEqual(chain_emit._input_pattern(self.TEMPLATE[0]),
                         "dts.mu2e.{desc}.{campaign}.art")

    def test_synthesize_pins_merge_from_mapping(self):
        from utils import chain_emit
        out = chain_emit.synthesize_entry(
            self.TEMPLATE, "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            out_campaign="MDC2025au", defer_desc=True)
        self.assertEqual(out['input_data'],
                         [{"dts.mu2e.CeMLeadingLog.MDC2025ap.art": 4}])

    def test_synthesize_applies_mapping_fcl_overrides(self):
        from utils import chain_emit
        out = chain_emit.synthesize_entry(
            self.TEMPLATE, "dts.mu2e.NoPrimary.MDC2025af.art",
            out_campaign="MDC2025au", defer_desc=True)
        self.assertEqual(out['fcl_overrides'][0]["#include"], "mixing/NoPrimary.fcl")
        self.assertEqual(out['fcl_overrides'][0]["services.DbService.version"], "v1_3")

    def test_mapping_desc_dropped_when_deferred(self):
        """The whole mapping must not leak into the emitted config."""
        from utils import chain_emit
        out = chain_emit.synthesize_entry(
            self.TEMPLATE, "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            out_campaign="MDC2025au", defer_desc=True)
        self.assertNotIn('desc', out)

    def test_missing_merge_fails_loud(self):
        """A desc added without a merge must error, not silently become 1 —
        that would emit an undersized round with several times the jobs."""
        from utils import chain_emit
        tmpl = copy.deepcopy(self.TEMPLATE)
        tmpl[0]['desc']['Forgotten'] = {}
        with self.assertRaises(ValueError) as cm:
            chain_emit._input_merge(tmpl[0], "Forgotten")
        self.assertIn("merge factor", str(cm.exception))

    def test_bad_mapping_value_rejected(self):
        from utils import chain_emit
        tmpl = copy.deepcopy(self.TEMPLATE)
        tmpl[0]['desc']['Bogus'] = "4"
        with self.assertRaises(ValueError):
            chain_emit._desc_map(tmpl[0])


class TestChainEmitPerDescMerge(unittest.TestCase):
    """One template entry, per-desc merge factors. Without this, giving a
    single desc a different merge means duplicating the entry's whole
    pileup/dsconf/override block — two copies that drift."""

    TEMPLATE = [{
        "desc": ["NoPrimary", {"desc": "MuCap1809keVCalo", "merge": 5}],
        "input_data": [{"dts.mu2e.{desc}.{campaign}.art": 1}],
        "dsconf": ["{out_campaign}_best_v1_3"],
        "pbeam": ["Mix1BB"],
        "inloc": ["resilient"],
        "simjob_setup": ["/cvmfs/x/{out_campaign}/setup.sh"],
    }]

    def test_explicit_descs_reads_both_forms(self):
        from utils import chain_emit
        self.assertEqual(chain_emit.explicit_descriptions(self.TEMPLATE),
                         ["NoPrimary", "MuCap1809keVCalo"])

    def test_match_entry_finds_dict_form_desc(self):
        from utils import chain_emit
        e = chain_emit.match_entry(self.TEMPLATE, "MuCap1809keVCalo")
        self.assertIs(e, self.TEMPLATE[0])

    def test_per_desc_merge_overrides_base(self):
        from utils import chain_emit
        entry = self.TEMPLATE[0]
        self.assertEqual(chain_emit._input_merge(entry, "MuCap1809keVCalo"), 5)

    def test_plain_string_desc_keeps_base_merge(self):
        from utils import chain_emit
        entry = self.TEMPLATE[0]
        self.assertEqual(chain_emit._input_merge(entry, "NoPrimary"), 1)

    def test_synthesize_applies_per_desc_merge(self):
        from utils import chain_emit
        out = chain_emit.synthesize_entry(
            self.TEMPLATE, "dts.mu2e.MuCap1809keVCalo.MDC2025ar.art",
            out_campaign="MDC2025au", defer_desc=True)
        self.assertEqual(out['input_data'],
                         [{"dts.mu2e.MuCap1809keVCalo.MDC2025ar.art": 5}])

    def test_synthesize_other_desc_unaffected(self):
        from utils import chain_emit
        out = chain_emit.synthesize_entry(
            self.TEMPLATE, "dts.mu2e.NoPrimary.MDC2025af.art",
            out_campaign="MDC2025au", defer_desc=True)
        self.assertEqual(out['input_data'],
                         [{"dts.mu2e.NoPrimary.MDC2025af.art": 1}])

    def test_dict_desc_without_name_fails_loud(self):
        """A malformed desc item must raise, not silently vanish from the
        roster — a silently-dropped desc is a whole dataset not produced."""
        from utils import chain_emit
        bad = [{**self.TEMPLATE[0], "desc": [{"merge": 5}]}]
        with self.assertRaises(ValueError):
            chain_emit.explicit_descriptions(bad)


class TestChainEmitPerDescOverrides(unittest.TestCase):
    """Per-desc `fcl_overrides` PATCH the entry's base overrides. Without
    this, one desc needing a single extra override (e.g. the NoPrimary.fcl
    trigger include) forces a duplicate of the entry's whole
    pileup/dsconf/fcl block."""

    TEMPLATE = [{
        "desc": [
            "CeMLeadingLog",
            {"desc": "NoPrimary",
             "fcl_overrides": {"#include": "Production/JobConfig/mixing/NoPrimary.fcl"}},
            {"desc": "MuCap1809keVCalo", "merge": 5,
             "fcl_overrides": {"#include": "Production/JobConfig/mixing/NoPrimary.fcl"}},
        ],
        "input_data": [{"dts.mu2e.{desc}.{campaign}.art": 1}],
        "dsconf": ["{out_campaign}_best_v1_3"],
        "pbeam": ["Mix1BB"],
        "inloc": ["resilient"],
        "simjob_setup": ["/cvmfs/x/{out_campaign}/setup.sh"],
        "fcl_overrides": [{
            "services.DbService.purpose": "Sim_best",
            "physics.producers.PBISim.SDF": 0.8,
        }],
    }]

    def _ov(self, dataset):
        from utils import chain_emit
        out = chain_emit.synthesize_entry(self.TEMPLATE, dataset,
                                          out_campaign="MDC2025au", defer_desc=True)
        ov = out['fcl_overrides']
        return ov[0] if isinstance(ov, list) else ov

    def test_per_desc_override_is_added_to_base(self):
        ov = self._ov("dts.mu2e.NoPrimary.MDC2025af.art")
        self.assertEqual(ov["#include"], "Production/JobConfig/mixing/NoPrimary.fcl")
        # base keys survive the patch
        self.assertEqual(ov["services.DbService.purpose"], "Sim_best")
        self.assertEqual(ov["physics.producers.PBISim.SDF"], 0.8)

    def test_desc_without_overrides_gets_base_only(self):
        ov = self._ov("dts.mu2e.CeMLeadingLog.MDC2025ap.art")
        self.assertNotIn("#include", ov)
        self.assertEqual(ov["services.DbService.purpose"], "Sim_best")

    def test_merge_and_overrides_combine(self):
        from utils import chain_emit
        out = chain_emit.synthesize_entry(
            self.TEMPLATE, "dts.mu2e.MuCap1809keVCalo.MDC2025ar.art",
            out_campaign="MDC2025au", defer_desc=True)
        self.assertEqual(out['input_data'],
                         [{"dts.mu2e.MuCap1809keVCalo.MDC2025ar.art": 5}])
        ov = out['fcl_overrides'][0]
        self.assertEqual(ov["#include"], "Production/JobConfig/mixing/NoPrimary.fcl")

    def test_per_desc_override_wins_over_base_key(self):
        from utils import chain_emit
        tmpl = copy.deepcopy(self.TEMPLATE)
        tmpl[0]['desc'][1]['fcl_overrides']["physics.producers.PBISim.SDF"] = 0.5
        out = chain_emit.synthesize_entry(tmpl, "dts.mu2e.NoPrimary.MDC2025af.art",
                                          out_campaign="MDC2025au", defer_desc=True)
        self.assertEqual(out['fcl_overrides'][0]["physics.producers.PBISim.SDF"], 0.5)

    def test_base_template_not_mutated_across_calls(self):
        """Patching must not leak into the shared template dict — the next
        desc would silently inherit the previous one's overrides."""
        self._ov("dts.mu2e.NoPrimary.MDC2025af.art")
        ov = self._ov("dts.mu2e.CeMLeadingLog.MDC2025ap.art")
        self.assertNotIn("#include", ov)


# ---------------------------------------------------------------------------
# 31. chain_emit: template synthesis for latestDatasets --emit
# ---------------------------------------------------------------------------

class TestChainEmit(unittest.TestCase):

    TEMPLATE = {
        "dsconf": "{campaign}_best_v1_1",
        "fcl": "Production/JobConfig/digitize/OnSpill.fcl",
        "input_data": {"dts.mu2e.{desc}.{campaign}.art": 10},
        "fcl_overrides": {
            "outputs.Output.fileName": "dig.owner.{desc}OnSpill.version.sequencer.art",
            "services.DbService.version": "v1_1",
        },
        "inloc": "tape",
        "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/{campaign}/setup.sh",
    }

    def test_input_tier_for_output(self):
        from utils import chain_emit
        self.assertEqual(chain_emit.input_tier_for_output("mcs"), "dig")
        self.assertEqual(chain_emit.input_tier_for_output("dig"), "dts")
        self.assertEqual(chain_emit.input_tier_for_output("nts"), "mcs")
        self.assertEqual(chain_emit.input_tier_for_output("ntd"), "mcs")

    def test_input_tier_for_output_unknown_raises(self):
        from utils import chain_emit
        with self.assertRaises(ValueError):
            chain_emit.input_tier_for_output("dts")

    def test_family_of(self):
        from utils import chain_emit
        self.assertEqual(chain_emit.family_of("MDC2025ap"), "MDC2025")
        self.assertEqual(chain_emit.family_of("MDC2025"), "MDC2025")
        self.assertEqual(chain_emit.family_of("Run1Ban"), "Run1B")
        self.assertEqual(chain_emit.family_of("Run1B"), "Run1B")

    def test_derive_input_defname_family(self):
        from utils import chain_emit
        self.assertEqual(
            chain_emit.derive_input_defname(self.TEMPLATE, "MDC2025"),
            "dts.mu2e.%.MDC2025%.art")

    def test_derive_input_defname_release(self):
        from utils import chain_emit
        self.assertEqual(
            chain_emit.derive_input_defname(self.TEMPLATE, "MDC2025ap"),
            "dts.mu2e.%.MDC2025ap%.art")

    def test_synthesize_substitutes_campaign(self):
        from utils import chain_emit
        entry = chain_emit.synthesize_entry(self.TEMPLATE, "dts.mu2e.CeEndpoint.MDC2025ap.art")
        self.assertEqual(entry['dsconf'], "MDC2025ap_best_v1_1")
        self.assertIn("SimJob/MDC2025ap/", entry['simjob_setup'])
        self.assertNotIn("{campaign}", json.dumps(entry))

    def test_parent_dsconf_substitution(self):
        """{parent_dsconf} = the full dsconf of the input dataset (incl build
        suffix), so an ntuple output can reuse its reco parent's dsconf."""
        from utils import chain_emit
        tmpl = {
            "desc": "{desc}",
            "dsconf": "{parent_dsconf}",
            "fcl": "EventNtuple/fcl/from_mcs-mockdata.fcl",
            "input_data": {"mcs.mu2e.{desc}.{campaign}_best_v1_1.art": 1},
            "fcl_overrides": {"services.TFileService.fileName":
                              "nts.mu2e.{desc}.version.sequencer.root"},
            "inloc": "disk", "simjob_setup": "x",
        }
        e = chain_emit.synthesize_entry(
            tmpl, "mcs.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.art")
        self.assertEqual(e['dsconf'], "MDC2025ap_best_v1_1")
        # Run1B-style suffix with recovery pass is carried through verbatim
        e2 = chain_emit.synthesize_entry(
            tmpl, "mcs.mu2e.CeEndpoint-KL.Run1Ban_best_v1_4-001.art")
        self.assertEqual(e2['dsconf'], "Run1Ban_best_v1_4-001")

    def test_output_datasets(self):
        from utils import chain_emit
        entry = chain_emit.synthesize_entry(self.TEMPLATE, "dts.mu2e.CeEndpoint.MDC2025ap.art")
        self.assertEqual(chain_emit.output_datasets(entry),
                         ["dig.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.art"])

    def test_explicit_desc_list(self):
        """A `desc` list with no {desc} wildcard restricts to those descs."""
        from utils import chain_emit
        tmpl = dict(self.TEMPLATE, desc=["CeEndpoint", "FlatGamma"])
        self.assertFalse(chain_emit.has_wildcard(tmpl))
        self.assertEqual(set(chain_emit.explicit_descriptions(tmpl)),
                         {"CeEndpoint", "FlatGamma"})
        # discovery defname still derived from input_data pattern
        self.assertEqual(chain_emit.derive_input_defname(tmpl, "MDC2025"),
                         "dts.mu2e.%.MDC2025%.art")
        # synthesize pins the concrete desc
        e = chain_emit.synthesize_entry(tmpl, "dts.mu2e.CeEndpoint.MDC2025ap.art")
        self.assertEqual(e['desc'], "CeEndpoint")
        self.assertEqual(e['fcl_overrides']['outputs.Output.fileName'],
                         "dig.owner.CeEndpointOnSpill.version.sequencer.art")

    def test_wildcard_in_desc_field(self):
        from utils import chain_emit
        tmpl = dict(self.TEMPLATE, desc="{desc}")
        self.assertTrue(chain_emit.has_wildcard(tmpl))
        self.assertEqual(chain_emit.explicit_descriptions(tmpl), [])

    def test_no_desc_field_means_discover_all(self):
        """TEMPLATE has no `desc` field: not a wildcard, no explicit descs →
        discovery is unrestricted (the historical default)."""
        from utils import chain_emit
        self.assertFalse(chain_emit.has_wildcard(self.TEMPLATE))
        self.assertEqual(chain_emit.explicit_descriptions(self.TEMPLATE), [])

    def test_list_template_special_match(self):
        """List template: an explicit-desc entry wins for its primary; the
        {desc} wildcard handles the rest; discovery uses the wildcard."""
        from utils import chain_emit
        tmpl = [
            dict(self.TEMPLATE, desc="{desc}"),
            {"desc": "CosmicCRYExtracted",
             "dsconf": "{campaign}_best_v1_1",
             "fcl": "Production/JobConfig/digitize/Extracted.fcl",
             "input_data": {"dts.mu2e.{desc}.{campaign}.art": 10},
             "fcl_overrides": {
                 "outputs.Output.fileName": "dig.owner.{desc}.version.sequencer.art"},
             "inloc": "tape", "simjob_setup": "x"},
        ]
        e = chain_emit.synthesize_entry(tmpl, "dts.mu2e.CosmicCRYExtracted.MDC2025ap.art")
        self.assertEqual(e['fcl'], "Production/JobConfig/digitize/Extracted.fcl")
        self.assertEqual(chain_emit.output_datasets(e),
                         ["dig.mu2e.CosmicCRYExtracted.MDC2025ap_best_v1_1.art"])
        e2 = chain_emit.synthesize_entry(tmpl, "dts.mu2e.FlatGamma.MDC2025ap.art")
        self.assertEqual(e2['fcl'], "Production/JobConfig/digitize/OnSpill.fcl")
        self.assertEqual(e2['fcl_overrides']['outputs.Output.fileName'],
                         "dig.owner.FlatGammaOnSpill.version.sequencer.art")
        self.assertEqual(chain_emit.derive_input_defname(tmpl, "MDC2025"),
                         "dts.mu2e.%.MDC2025%.art")

    def test_synthesize_entry_pins_input(self):
        from utils import chain_emit
        entry = chain_emit.synthesize_entry(self.TEMPLATE, "dts.mu2e.CeEndpoint.MDC2025ap.art")
        self.assertEqual(entry['input_data'], {"dts.mu2e.CeEndpoint.MDC2025ap.art": 10})

    def test_synthesize_entry_substitutes_desc(self):
        from utils import chain_emit
        entry = chain_emit.synthesize_entry(self.TEMPLATE, "dts.mu2e.CeEndpoint.MDC2025ap.art")
        self.assertEqual(entry['fcl_overrides']['outputs.Output.fileName'],
                         "dig.owner.CeEndpointOnSpill.version.sequencer.art")

    def test_synthesize_entry_copies_physics(self):
        from utils import chain_emit
        entry = chain_emit.synthesize_entry(self.TEMPLATE, "dts.mu2e.CeEndpoint.MDC2025ap.art")
        self.assertEqual(entry['dsconf'], "MDC2025ap_best_v1_1")
        self.assertEqual(entry['fcl_overrides']['services.DbService.version'], "v1_1")

    def test_synthesize_entry_no_template_mutation(self):
        from utils import chain_emit
        chain_emit.synthesize_entry(self.TEMPLATE, "dts.mu2e.CeEndpoint.MDC2025ap.art")
        self.assertIn('{desc}', self.TEMPLATE['fcl_overrides']['outputs.Output.fileName'])

    def test_reco_desc_is_full_dig_desc(self):
        """At a reco hop, {desc} carries the dig dataset's full description."""
        from utils import chain_emit
        reco_tmpl = {
            "dsconf": "MDC2025ap_best_v1_1",
            "fcl": "Production/JobConfig/recoMC/OnSpill.fcl",
            "input_data": {"dig.mu2e.{desc}.MDC2025ap_best_v1_1.art": 1},
            "fcl_overrides": {"outputs.LoopHelixOutput.fileName":
                              "mcs.owner.{desc}.version.sequencer.art"},
            "inloc": "tape",
            "simjob_setup": "x",
        }
        entry = chain_emit.synthesize_entry(
            reco_tmpl, "dig.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.art")
        self.assertEqual(entry['fcl_overrides']['outputs.LoopHelixOutput.fileName'],
                         "mcs.owner.CeEndpointOnSpill.version.sequencer.art")

    def test_emit_config_maps_all(self):
        from utils import chain_emit
        cfg = chain_emit.emit_config(
            self.TEMPLATE, ["dts.mu2e.A.MDC2025ap.art", "dts.mu2e.B.MDC2025ap.art"])
        self.assertEqual(len(cfg), 2)
        self.assertEqual(cfg[0]['input_data'], {"dts.mu2e.A.MDC2025ap.art": 10})

    def test_load_template_missing_fails_loud(self):
        from utils import chain_emit
        with self.assertRaises(FileNotFoundError):
            chain_emit.load_template("NoSuchCampaign", "digi", "/tmp/nonexistent_templates_xyz")

    def test_load_template_by_family(self):
        """A release tag (MDC2025ap) resolves to the family dir (MDC2025)."""
        import tempfile
        from utils import chain_emit
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "MDC2025"))
            with open(os.path.join(d, "MDC2025", "digi.json"), 'w') as f:
                json.dump(self.TEMPLATE, f)
            t = chain_emit.load_template("MDC2025ap", "digi", d)
        self.assertEqual(t['dsconf'], "{campaign}_best_v1_1")

    def test_input_pattern_rejects_multi(self):
        from utils import chain_emit
        with self.assertRaises(ValueError):
            chain_emit.derive_input_defname({"input_data": {"a.art": 1, "b.art": 1}}, "C")

    # --- mixing: out_campaign / defer_desc / dsconf override ---

    MIX_TEMPLATE = {
        "desc": ["CeMLeadingLog", "FlatGamma"],
        "dsconf": ["{out_campaign}_best_v1_1"],
        "input_data": [{"dts.mu2e.{desc}.{campaign}.art": 1}],
        "pbeam": ["Mix1BB"],
        "fcl": ["Production/JobConfig/mixing/Mix.fcl"],
        "fcl_overrides": [{
            "outputs.Output.fileName": "dig.mu2e.{desc}.{dsconf}.sequence.art"}],
        "inloc": ["tape"],
        "simjob_setup": ["/cvmfs/.../SimJob/{out_campaign}/setup.sh"],
    }

    def test_out_campaign_decouples_build_from_input(self):
        """Mixing reads an ap primary but writes the ar build: dsconf and
        simjob_setup use {out_campaign}, not the input campaign."""
        from utils import chain_emit
        e = chain_emit.synthesize_entry(
            self.MIX_TEMPLATE, "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            out_campaign="MDC2025ar", defer_desc=True)
        self.assertEqual(e['dsconf'], ["MDC2025ar_best_v1_1"])
        self.assertIn("SimJob/MDC2025ar/", e['simjob_setup'][0])

    def test_defer_desc_leaves_desc_literal(self):
        """defer_desc drops the `desc` field and leaves {desc} unsubstituted so
        json2jobdef can append pbeam (desc = input_desc + pbeam) at gen time."""
        from utils import chain_emit
        e = chain_emit.synthesize_entry(
            self.MIX_TEMPLATE, "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            out_campaign="MDC2025ar", defer_desc=True)
        self.assertNotIn('desc', e)
        self.assertIn('{desc}', e['fcl_overrides'][0]['outputs.Output.fileName'])

    def test_output_datasets_resolves_deferred_desc_via_pbeam(self):
        """output_datasets must expand the literal {desc} to input_desc+pbeam so
        the produced-output (skip-produced) check matches real SAM names."""
        from utils import chain_emit
        e = chain_emit.synthesize_entry(
            self.MIX_TEMPLATE, "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            out_campaign="MDC2025ar", defer_desc=True)
        self.assertEqual(
            chain_emit.output_datasets(e),
            ["dig.mu2e.CeMLeadingLogMix1BB.MDC2025ar_best_v1_1.art"])

    def test_dsconf_override_pins_build_listform(self):
        """--dsconf overrides the template dsconf outright, preserving the
        list-form container, and flows into the resolved output name."""
        from utils import chain_emit
        e = chain_emit.synthesize_entry(
            self.MIX_TEMPLATE, "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            out_campaign="MDC2025ar", defer_desc=True,
            dsconf="MDC2025ar_best_v1_3")
        self.assertEqual(e['dsconf'], ["MDC2025ar_best_v1_3"])
        self.assertEqual(
            chain_emit.output_datasets(e),
            ["dig.mu2e.CeMLeadingLogMix1BB.MDC2025ar_best_v1_3.art"])

    def test_dsconf_override_scalar_shape(self):
        """For scalar-dsconf templates (digi/reco) the override stays scalar."""
        from utils import chain_emit
        e = chain_emit.synthesize_entry(
            self.TEMPLATE, "dts.mu2e.CeEndpoint.MDC2025ap.art",
            dsconf="MDC2025ap_best_v1_9")
        self.assertEqual(e['dsconf'], "MDC2025ap_best_v1_9")
        self.assertEqual(chain_emit.output_datasets(e),
                         ["dig.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_9.art"])


# ---------------------------------------------------------------------------
# 32. latest_per_description (latestDatasets.py)
# ---------------------------------------------------------------------------

class TestLatestPerDescription(unittest.TestCase):

    def test_picks_greatest_dsconf(self):
        from utils.latestDatasets import latest_per_description
        names = [
            "dts.mu2e.A.MDC2025ao.art",
            "dts.mu2e.A.MDC2025ap.art",
            "dts.mu2e.B.MDC2025ap.art",
        ]
        rows, skipped = latest_per_description(names)
        latest = {desc: name for desc, _, name, _ in rows}
        self.assertEqual(latest["A"], "dts.mu2e.A.MDC2025ap.art")
        self.assertEqual(latest["B"], "dts.mu2e.B.MDC2025ap.art")
        self.assertEqual(skipped, [])

    def test_skips_unparseable(self):
        from utils.latestDatasets import latest_per_description
        rows, skipped = latest_per_description(["not-a-name", "dts.mu2e.A.MDC2025ap.art"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(skipped), 1)

    def test_narrow_to_latest_release(self):
        """Family wildcard spans releases; narrow to the single latest one."""
        from utils.latestDatasets import _narrow_to_latest_release
        out = _narrow_to_latest_release([
            "dts.mu2e.CeEndpoint.MDC2025ac.art",     # older release → dropped
            "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            "dts.mu2e.FlatGamma.MDC2025ap.art",
        ])
        self.assertEqual(set(out), {
            "dts.mu2e.CeMLeadingLog.MDC2025ap.art",
            "dts.mu2e.FlatGamma.MDC2025ap.art",
        })


# ---------------------------------------------------------------------------
# 33. latestDatasets --emit arg validation
# ---------------------------------------------------------------------------

class TestListerArgValidation(unittest.TestCase):
    """Lister mode needs a source. Bare --complete-only (no defname/campaign/
    stdin) must error rather than silently do nothing."""

    def test_no_source_errors(self):
        from utils import latestDatasets
        with patch.object(sys, 'argv', ['latestDatasets', '--complete-only']):
            with self.assertRaises(SystemExit):
                latestDatasets.main()


# ---------------------------------------------------------------------------
# 34. --skip-produced (latestDatasets._filter_unproduced)
# ---------------------------------------------------------------------------

class TestSkipProduced(unittest.TestCase):

    def test_filter_unproduced_drops_existing(self):
        """Inputs whose this-stage output already exists in SAM are dropped."""
        from utils import latestDatasets
        tmpl = TestChainEmit.TEMPLATE
        # digi output of CeEndpoint "exists"; FlatGamma's does not.
        with patch.object(latestDatasets, '_dataset_exists',
                          side_effect=lambda name: "CeEndpoint" in name):
            kept = latestDatasets._filter_unproduced(
                ["dts.mu2e.CeEndpoint.MDC2025ap.art",
                 "dts.mu2e.FlatGamma.MDC2025ap.art"], tmpl)
        self.assertEqual(kept, ["dts.mu2e.FlatGamma.MDC2025ap.art"])


# ---------------------------------------------------------------------------
# 35. gencount + uniformity (poms_db.DatasetInfo, db_builder, pomsMonitor)
# ---------------------------------------------------------------------------

@requires_sqlalchemy
class TestDatasetInfoGencount(unittest.TestCase):
    """DatasetInfo.filter_eff derived from gencount."""

    def _info(self, **kw):
        from utils.poms_db import DatasetInfo
        return DatasetInfo(**kw)

    def test_filter_eff(self):
        i = self._info(nfiles=2000, nevts=2761, gencount=5000)
        self.assertAlmostEqual(i.filter_eff, 2761 / 5000)

    def test_filter_eff_none_without_gencount(self):
        self.assertIsNone(self._info(nfiles=10, nevts=5, gencount=None).filter_eff)
        self.assertIsNone(self._info(nfiles=10, nevts=5, gencount=0).filter_eff)


@requires_sqlalchemy
class TestGetDatasetGencount(unittest.TestCase):
    """db_builder._get_dataset_gencount: gencount(file) * nfiles, one metadata call."""

    def test_multiplies_per_file_by_nfiles(self):
        from utils import db_builder
        with patch.object(db_builder, 'first_file_in_definition',
                          return_value='f0.art'), \
             patch.object(db_builder, 'get_metadata',
                          return_value={'dh.gencount': 5000}) as gm:
            self.assertEqual(db_builder._get_dataset_gencount('ds', 2000), 5000 * 2000)
            gm.assert_called_once()  # only ONE metadata call regardless of nfiles

    def test_none_when_no_gencount_field(self):
        from utils import db_builder
        with patch.object(db_builder, 'first_file_in_definition', return_value='f0.art'), \
             patch.object(db_builder, 'get_metadata', return_value={'event_count': 5}):
            self.assertIsNone(db_builder._get_dataset_gencount('ds', 100))

    def test_none_when_no_files(self):
        from utils import db_builder
        self.assertIsNone(db_builder._get_dataset_gencount('ds', 0))

    def test_none_on_exception(self):
        from utils import db_builder
        with patch.object(db_builder, 'first_file_in_definition',
                          side_effect=Exception('SAM down')):
            self.assertIsNone(db_builder._get_dataset_gencount('ds', 100))

    def test_supplied_first_file_skips_the_list_fetch(self):
        """When the build loop passes first_file, the probes must NOT re-fetch
        the dataset's first file (the round-trip the optimization eliminates).
        infer_dataset_location now lives in file_resolver and does a lazy
        `from .samweb_wrapper import ...` at call time, so its fetch is
        patched on samweb_wrapper."""
        from utils import db_builder, file_resolver, samweb_wrapper
        with patch.object(db_builder, 'first_file_in_definition') as lst, \
             patch.object(samweb_wrapper, 'first_file_in_definition') as lst2, \
             patch.object(db_builder, 'get_metadata',
                          return_value={'dh.gencount': 5000}), \
             patch.object(db_builder, 'children_of_file', return_value=['c.art']), \
             patch.object(samweb_wrapper, 'locate_file_strict',
                          return_value=[{'location_type': 'dcache:/pnfs/x'}]):
            self.assertEqual(
                db_builder._get_dataset_gencount('ds', 2000, 'f0.art'), 5000 * 2000)
            self.assertTrue(db_builder._check_dataset_has_children('ds', 'f0.art'))
            self.assertEqual(
                file_resolver.infer_dataset_location('ds', 'f0.art'), 'dcache')
            lst.assert_not_called()   # first_file supplied -> zero fetches
            lst2.assert_not_called()

    def test_omitted_first_file_still_self_fetches(self):
        """Standalone callers (db_analyzer, tests) that omit first_file keep
        the self-fetch behavior."""
        from utils import db_builder
        with patch.object(db_builder, 'first_file_in_definition',
                          return_value='f0.art') as lst, \
             patch.object(db_builder, 'get_metadata',
                          return_value={'dh.gencount': 5000}):
            self.assertEqual(db_builder._get_dataset_gencount('ds', 2000), 5000 * 2000)
            lst.assert_called_once()


@requires_sqlalchemy
class TestUniformityReport(unittest.TestCase):
    """pomsMonitor.uniformity_report: events/job = round(target/eff)."""

    def _session_with(self, datasets):
        """In-memory DB session seeded with (name, nfiles, nevts, gencount)."""
        from utils.poms_db import get_db_session, DatasetInfo
        s = get_db_session(None)  # in-memory
        for name, nf, ne, gc in datasets:
            s.add(DatasetInfo(dataset_name=name, nfiles=nf, nevts=ne, gencount=gc))
        s.commit()
        return s

    def test_events_per_job_rounded(self):
        from utils import pomsMonitor
        # eff = nevts/gencount.
        #   CeMLeadingLog: 2_761_000/5_000_000 = .5522 -> 2000/.5522 = 3622 -> 4000
        #   DIOtail95:       500_000/1_000_000 = .5000 -> 2000/.50  = 4000
        s = self._session_with([
            ('dts.mu2e.CeMLeadingLog.MDC2025ap.art', 2000, 2_761_000, 5_000_000),
            ('dts.mu2e.DIOtail95.MDC2025ap.art',     1000,   500_000, 1_000_000),
        ])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pomsMonitor.uniformity_report(s, 'MDC2025ap', target=2000, round_to=1000)
        out = buf.getvalue()
        self.assertIn('CeMLeadingLog', out)
        self.assertIn('4,000', out)  # 2000/0.5522 = 3623 -> 4000
        # DIOtail95 eff exactly 0.5 -> 2000/0.5 = 4000
        self.assertRegex(out, r'DIOtail95\s+0\.5000.*4,000')

    def test_requires_campaign(self):
        from utils import pomsMonitor
        s = self._session_with([])
        with self.assertRaises(SystemExit):
            pomsMonitor.uniformity_report(s, None, target=2000)

    def test_skips_missing_gencount(self):
        from utils import pomsMonitor
        s = self._session_with([
            ('dts.mu2e.Good.MDC2025ap.art', 100, 50, 1000),
            ('dts.mu2e.NoGen.MDC2025ap.art', 100, 50, None),
        ])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pomsMonitor.uniformity_report(s, 'MDC2025ap', target=2000, round_to=1000)
        out = buf.getvalue()
        self.assertIn('Good', out)
        self.assertNotIn('NoGen', out)  # missing-gencount goes to stderr, not the table


# ---------------------------------------------------------------------------
# 32. Mu2eJobBase job arithmetic (hoisted single implementation)
# ---------------------------------------------------------------------------

class TestJobArithmeticConsolidation(unittest.TestCase):
    """sequencer/job_outputs/job_event_settings/job_seed/njobs live once in
    Mu2eJobBase; the worker names its real output files through them, so
    Mu2eJobPars (mkrecovery, submit, db_builder, jobdef_lookup) must return
    byte-identical answers. Regression tests for the divergences that existed
    while jobiodetail.py and jobquery.py carried stale copies."""

    def _tar(self, tbs, owner=None, dsconf=None):
        jp = {"code": "", "setup": "/cvmfs/test/setup.sh", "tbs": tbs,
              "jobname": "cnf.mu2e.X.TC.0.tar"}
        if owner is not None:
            jp["owner"] = owner
        if dsconf is not None:
            jp["dsconf"] = dsconf
        return _make_tarball(jp, "module_type : EmptyEvent\n")

    def test_sequencer_from_index_via_jobpars(self):
        """Position-based sequencer (worker semantics), not the parent file's
        name — old Mu2eJobIO returned the parent sequencer, so mkrecovery
        mispredicted filenames whenever the parent dataset had holes."""
        from utils.jobquery import Mu2eJobPars
        tar = self._tar({
            "inputs": {"source.fileNames":
                       [1, ["dts.mu2e.P.C.001470_00000005.art",
                            "dts.mu2e.P.C.001470_00000009.art"]]},
            "sequencer_from_index": True,
        })
        try:
            jp = Mu2eJobPars(tar)
            self.assertEqual(jp.sequencer(0), "001470_00000000")
            self.assertEqual(jp.sequencer(1), "001470_00000001")
        finally:
            os.unlink(tar)

    def test_pbisequence_runnumber_sequencer(self):
        """source.runNumber (PBISequence) — old Mu2eJobIO raised on it."""
        from utils.jobquery import Mu2eJobPars
        tar = self._tar({"event_id": {"source.runNumber": 1202}})
        try:
            self.assertEqual(Mu2eJobPars(tar).sequencer(7), "001202_00000007")
        finally:
            os.unlink(tar)

    def test_event_id_precedence_over_inputs(self):
        """Mix-shaped jobdef (inputs AND event_id): the run number wins —
        old Mu2eJobIO consulted the inputs first."""
        from utils.jobquery import Mu2eJobPars
        tar = self._tar({
            "event_id": {"source.firstRun": 1470},
            "inputs": {"source.fileNames":
                       [1, ["dts.mu2e.P.C.000999_00000042.art"]]},
        })
        try:
            self.assertEqual(Mu2eJobPars(tar).sequencer(0), "001470_00000000")
        finally:
            os.unlink(tar)

    def test_job_outputs_owner_version_substitution(self):
        """`.owner.`/`.version.` placeholders substitute through Mu2eJobPars
        exactly as through Mu2eJobFCL — old Mu2eJobIO left them literal."""
        from utils.jobquery import Mu2eJobPars
        from utils.jobfcl import Mu2eJobFCL
        tar = self._tar({
            "event_id": {"source.firstRun": 1430},
            "outfiles": {"outputs.Output.fileName":
                         "dig.owner.Foo.version.sequencer.art"},
        }, owner="alice", dsconf="Conf1")
        try:
            expected = {"outputs.Output.fileName":
                        "dig.alice.Foo.Conf1.001430_00000003.art"}
            self.assertEqual(Mu2eJobPars(tar).job_outputs(3), expected)
            self.assertEqual(Mu2eJobFCL(tar).job_outputs(3), expected)
        finally:
            os.unlink(tar)

    def test_njobs_embedded_wins(self):
        """tbs.njobs (declared cap) beats the derived count."""
        from utils.jobquery import Mu2eJobPars
        tar = self._tar({
            "njobs": 3,
            "inputs": {"source.fileNames":
                       [1, [f"dts.mu2e.P.C.001470_{i:08d}.art" for i in range(5)]]},
        })
        try:
            self.assertEqual(Mu2eJobPars(tar).njobs(), 3)
        finally:
            os.unlink(tar)

    def test_njobs_derived_from_inputs(self):
        from utils.jobquery import Mu2eJobPars
        tar = self._tar({
            "inputs": {"source.fileNames":
                       [3, [f"dts.mu2e.P.C.001470_{i:08d}.art" for i in range(10)]]},
        })
        try:
            self.assertEqual(Mu2eJobPars(tar).njobs(), 4)  # ceil(10/3)
        finally:
            os.unlink(tar)

    def test_njobs_from_samplinginput(self):
        """Resampler jobdefs derive njobs from samplinginput — the old
        Mu2eJobFCL copy answered 0 for these."""
        from utils.jobfcl import Mu2eJobFCL
        tar = self._tar({
            "samplinginput": {"source.fileNames":
                              [4, [f"sim.mu2e.S.C.001470_{i:08d}.art" for i in range(10)]]},
        })
        try:
            self.assertEqual(Mu2eJobFCL(tar).njobs(), 3)  # ceil(10/4)
        finally:
            os.unlink(tar)

    def test_njobs_open_ended_is_zero(self):
        """Generator with no embedded count: 0 = 'not a property of this
        jobdef' (count lives in the POMS map), never a guess."""
        from utils.jobquery import Mu2eJobPars
        tar = self._tar({"event_id": {"source.firstRun": 1430}})
        try:
            self.assertEqual(Mu2eJobPars(tar).njobs(), 0)
        finally:
            os.unlink(tar)

    def test_njobs_invalid_merge_fails_loud(self):
        """No silent merge_factor=1 fallback (old jobquery behavior)."""
        from utils.jobquery import Mu2eJobPars
        tar = self._tar({
            "inputs": {"source.fileNames": [0, ["dts.mu2e.P.C.001470_00000000.art"]]},
        })
        try:
            with self.assertRaises(ValueError):
                Mu2eJobPars(tar).njobs()
        finally:
            os.unlink(tar)


# ---------------------------------------------------------------------------
# 32b. Mu2eJobPars.recipe (reconstruct the build config from a cnf)
# ---------------------------------------------------------------------------

class TestJobParsRecipe(unittest.TestCase):
    """A cnf in SAM is sometimes the ONLY surviving record of how it was
    built — the MDC2025ar generic reco/evnt entries were never committed.
    recipe() surfaces the embedded template fcl (the `fcl` + `fcl_overrides`
    of the json2jobdef entry), which neither jobquery's other queries nor
    fcldump exposed."""

    FCL = ('#include "Production/JobConfig/recoMC/OnSpill.fcl"\n'
           'outputs.LoopHelixOutput.fileName: "mcs.owner.{desc}.version.sequencer.art"\n'
           'services.DbService.purpose: "Sim_best"\n'
           'services.DbService.version: "v1_1"\n')

    def _tar(self):
        jp = {
            "code": "",
            "setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025au/setup.sh",
            "jobname": "cnf.mu2e.reco.MDC2025au_best_v1_1.0.tar",
            "tbs": {"outfiles": {
                "outputs.LoopHelixOutput.fileName":
                    "mcs.mu2e.{desc}.MDC2025au_best_v1_1.sequencer.art"}},
        }
        return _make_tarball(jp, self.FCL)

    def test_recipe_reports_jobpars_fields(self):
        from utils.jobquery import Mu2eJobPars
        tar = self._tar()
        try:
            out = Mu2eJobPars(tar).recipe()
        finally:
            os.unlink(tar)
        self.assertIn("cnf.mu2e.reco.MDC2025au_best_v1_1.0.tar", out)
        self.assertIn("SimJob/MDC2025au/setup.sh", out)
        self.assertIn("njobs: 0", out)  # no tbs capacity -> generic
        self.assertIn("mcs.mu2e.{desc}.MDC2025au_best_v1_1.sequencer.art", out)

    def test_recipe_includes_override_block_verbatim(self):
        """The override lines are the part you cannot get anywhere else."""
        from utils.jobquery import Mu2eJobPars
        tar = self._tar()
        try:
            out = Mu2eJobPars(tar).recipe()
        finally:
            os.unlink(tar)
        for line in self.FCL.strip().splitlines():
            self.assertIn(line, out)

    def test_recipe_without_embedded_fcl_reports_absence(self):
        """A code-tarball cnf has no mu2e.fcl; say so rather than raising —
        the jobpars half of the recipe is still worth printing."""
        from utils.jobquery import Mu2eJobPars
        import tempfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tar_w:
            jp_bytes = json.dumps({"code": "", "setup": "/cvmfs/test/setup.sh",
                                   "jobname": "cnf.mu2e.X.TC.0.tar",
                                   "tbs": {}}).encode()
            ti = tarfile.TarInfo(name='jobpars.json')
            ti.size = len(jp_bytes)
            tar_w.addfile(ti, io.BytesIO(jp_bytes))
        buf.seek(0)
        tmp = tempfile.NamedTemporaryFile(suffix='.tar', delete=False)
        tmp.write(buf.read())
        tmp.close()
        try:
            out = Mu2eJobPars(tmp.name).recipe()
        finally:
            os.unlink(tmp.name)
        self.assertIn("cnf.mu2e.X.TC.0.tar", out)
        self.assertIn("no embedded mu2e.fcl", out)


# ---------------------------------------------------------------------------
# 33. jobdef._resolve_njobs (build-time tbs.njobs embedding)
# ---------------------------------------------------------------------------

class TestResolveNjobs(unittest.TestCase):

    def _files(self, n):
        return [f"dts.mu2e.P.C.001470_{i:08d}.art" for i in range(n)]

    def test_declared_within_capacity(self):
        from utils.jobdef import _resolve_njobs
        tbs = {"inputs": {"source.fileNames": [2, self._files(10)]}}
        self.assertEqual(_resolve_njobs({"njobs": 4}, tbs), 4)

    def test_declared_exceeds_capacity_fails_loud(self):
        from utils.jobdef import _resolve_njobs
        tbs = {"inputs": {"source.fileNames": [2, self._files(10)]}}
        with self.assertRaises(ValueError):
            _resolve_njobs({"njobs": 6}, tbs)

    def test_query_mode_derives(self):
        from utils.jobdef import _resolve_njobs
        tbs = {"inputs": {"source.fileNames": [3, self._files(10)]}}
        self.assertEqual(_resolve_njobs({"njobs": -1}, tbs), 4)

    def test_generator_declared(self):
        from utils.jobdef import _resolve_njobs
        self.assertEqual(_resolve_njobs({"njobs": 500}, {"event_id": {"source.firstRun": 1430}}), 500)

    def test_generator_undeclared_omitted(self):
        from utils.jobdef import _resolve_njobs
        self.assertIsNone(_resolve_njobs({}, {"event_id": {"source.firstRun": 1430}}))

    def test_generic_tarball_omitted(self):
        """Absence is load-bearing for generic tarballs (direct-input mode)."""
        from utils.jobdef import _resolve_njobs
        tbs = {"outfiles": {"outputs.Output.fileName": "nts.owner.{desc}.version.sequencer.root"}}
        self.assertIsNone(_resolve_njobs({"njobs": 500, "generic_tarball": True}, tbs))

    def test_samplinginput_capacity(self):
        from utils.jobdef import _resolve_njobs
        tbs = {"samplinginput": {"source.fileNames": [4, self._files(10)]}}
        self.assertEqual(_resolve_njobs({"njobs": -1}, tbs), 3)


# ---------------------------------------------------------------------------
# 33. normalize_input_data — single home of the input_data shape grammar
# ---------------------------------------------------------------------------

class TestNormalizeInputData(unittest.TestCase):

    def _one(self, input_data):
        from utils.config_utils import normalize_input_data
        specs = normalize_input_data(input_data)
        self.assertEqual(len(specs), 1)
        return specs[0]

    def test_plain_merge_factor(self):
        s = self._one({"dts.mu2e.NoPrimary.Run1Ban.art": 10})
        self.assertEqual((s.source, s.per_job, s.random, s.max_nfiles),
                         ("dts.mu2e.NoPrimary.Run1Ban.art", 10, False, None))

    def test_count_random(self):
        s = self._one({"dts.mu2e.NeutralsFlash.MDC2025ac.art":
                       {"count": 5000, "random": True}})
        self.assertEqual((s.per_job, s.random), (5000, True))

    def test_merge_factor_with_max_nfiles(self):
        s = self._one({"dts.mu2e.CosmicCRYAll.MDC2025ap.art":
                       {"merge_factor": 10, "max_nfiles": 10000}})
        self.assertEqual((s.per_job, s.max_nfiles), (10, 10000))

    def test_count_wins_over_merge_factor(self):
        s = self._one({"a.b.c.d.art": {"count": 3, "merge_factor": 7}})
        self.assertEqual(s.per_job, 3)

    def test_split_and_chunk_have_no_per_job(self):
        s = self._one({"/cvmfs/x/PBI.txt": {"chunk_lines": 1000}})
        self.assertEqual((s.per_job, s.chunk_lines), (None, 1000))
        s = self._one({"/tmp/PBI.txt": {"split_lines": 500}})
        self.assertEqual((s.per_job, s.split_lines), (None, 500))

    def test_non_dict_raises(self):
        from utils.config_utils import normalize_input_data
        with self.assertRaises(ValueError):
            normalize_input_data("dts.mu2e.X.C.art")
        with self.assertRaises(ValueError):
            normalize_input_data(None)

    def test_unknown_spec_key_raises(self):
        """Typos like 'marge_factor' must fail loud, not be silently ignored."""
        from utils.config_utils import normalize_input_data
        with self.assertRaises(ValueError):
            normalize_input_data({"a.b.c.d.art": {"marge_factor": 2}})

    def test_bad_max_nfiles_raises(self):
        from utils.config_utils import normalize_input_data
        for bad in (0, -5, "10"):
            with self.assertRaises(ValueError):
                normalize_input_data({"a.b.c.d.art": {"count": 1, "max_nfiles": bad}})

    def test_order_preserved_multi_dataset(self):
        from utils.config_utils import normalize_input_data
        specs = normalize_input_data({"a.b.first.d.art": 1, "a.b.second.d.art": 2})
        self.assertEqual([s.source for s in specs],
                         ["a.b.first.d.art", "a.b.second.d.art"])

    def test_consumers_share_the_grammar(self):
        """calculate_merge_factor reads the normalized spec (split_lines→1,
        missing merge info fails with the historical message)."""
        from utils.prod_utils import calculate_merge_factor
        self.assertEqual(calculate_merge_factor(
            {"input_data": {"a.b.c.d.art": {"count": 4}}}), 4)
        self.assertEqual(calculate_merge_factor(
            {"input_data": {"/tmp/f.txt": {"split_lines": 100}}}), 1)
        with self.assertRaises(ValueError):
            calculate_merge_factor({"input_data": {"a.b.c.d.art": {"random": True}}})


# ---------------------------------------------------------------------------
# 34. firstjob index windows — statistics expansion without seed collisions
# ---------------------------------------------------------------------------

class TestFirstjobOf(unittest.TestCase):
    """firstjob_of: fail-loud accessor for the cnf-index window start."""

    def test_default_zero(self):
        from utils.poms_entry import firstjob_of
        self.assertEqual(firstjob_of({'tarball': 'x'}), 0)

    def test_explicit_value(self):
        from utils.poms_entry import firstjob_of
        self.assertEqual(firstjob_of({'firstjob': 5000}), 5000)

    def test_malformed_raises(self):
        """A silently-ignored firstjob would rerun indices [0, njobs) and
        duplicate physics (baseSeed = 1 + index) — must fail loud."""
        from utils.poms_entry import firstjob_of
        for bad in (-1, '5000', 5000.0, True):
            with self.assertRaises(ValueError):
                firstjob_of({'firstjob': bad})


class TestResolveMapIndex(unittest.TestCase):
    """Global (index-dataset) → (entry, local cnf index) dispatch, the
    seed-critical arithmetic: local = global - cumulative + firstjob."""

    MAP = [
        {'tarball': 'cnf.mu2e.A.C.0.tar', 'njobs': 3},
        {'tarball': 'cnf.mu2e.G.C.0.tar'},  # generic: occupies no slots
        {'tarball': 'cnf.mu2e.B.C.0.tar', 'njobs': 2, 'firstjob': 5000},
    ]

    def _resolve(self, global_idx):
        from utils.prod_utils import resolve_map_index
        return resolve_map_index(self.MAP, global_idx)

    def test_plain_entry_starts_at_zero(self):
        entry, i, local = self._resolve(0)
        self.assertEqual((i, local), (0, 0))
        entry, i, local = self._resolve(2)
        self.assertEqual((i, local), (0, 2))

    def test_generic_entry_skipped(self):
        """The generic entry between A and B must not consume index slots."""
        entry, i, local = self._resolve(3)
        self.assertEqual(entry['tarball'], 'cnf.mu2e.B.C.0.tar')
        self.assertEqual(i, 2)

    def test_windowed_entry_offsets_local_index(self):
        """Expansion entry: global slots 3..4 → cnf indices 5000..5001,
        i.e. baseSeed 5001..5002 — no collision with the original 0..2."""
        self.assertEqual(self._resolve(3)[2], 5000)
        self.assertEqual(self._resolve(4)[2], 5001)

    def test_out_of_range_returns_none(self):
        self.assertEqual(self._resolve(5), (None, None, None))

    def test_same_tarball_two_windows(self):
        """Original + expansion entries for one tarball must partition the
        cnf index space with no overlap."""
        from utils.prod_utils import resolve_map_index
        map_ = [
            {'tarball': 'cnf.mu2e.X.C.0.tar', 'njobs': 5000},
            {'tarball': 'cnf.mu2e.X.C.0.tar', 'njobs': 100, 'firstjob': 5000},
        ]
        locals_ = [resolve_map_index(map_, g)[2] for g in (0, 4999, 5000, 5099)]
        self.assertEqual(locals_, [0, 4999, 5000, 5099])


class TestComputeJobsetWindow(unittest.TestCase):
    """Direct backend: jobset stays entry-relative (PROCESS space); a
    windowed entry sizes it from the entry's njobs and validates capacity."""

    def _opts(self, first=None, num=None, indices=None):
        from types import SimpleNamespace
        return SimpleNamespace(first=first, num=num, indices=indices)

    def test_plain_uses_cnf_njobs(self):
        from utils.submit import _compute_jobset
        self.assertEqual(_compute_jobset(self._opts(), 4), [0, 1, 2, 3])

    def test_windowed_open_ended_cnf(self):
        """Open-ended cnf (capacity 0): entry njobs sizes the jobset;
        indices stay 0-based — the offset is applied worker-side."""
        from utils.submit import _compute_jobset
        self.assertEqual(
            _compute_jobset(self._opts(), 0, firstjob=5000, entry_njobs=3),
            [0, 1, 2])

    def test_windowed_within_closed_capacity(self):
        from utils.submit import _compute_jobset
        self.assertEqual(
            _compute_jobset(self._opts(), 7000, firstjob=5000, entry_njobs=2000),
            list(range(2000)))

    def test_window_exceeding_capacity_raises(self):
        from utils.submit import _compute_jobset
        with self.assertRaises(ValueError):
            _compute_jobset(self._opts(), 5000, firstjob=5000, entry_njobs=1)

    def test_windowed_without_njobs_raises(self):
        from utils.submit import _compute_jobset
        with self.assertRaises(ValueError):
            _compute_jobset(self._opts(), 0, firstjob=5000)

    def test_first_num_carve_within_window(self):
        from utils.submit import _compute_jobset
        self.assertEqual(
            _compute_jobset(self._opts(first=1, num=2), 0,
                            firstjob=5000, entry_njobs=4),
            [1, 2])

    def test_indices_returns_exact_scattered_set(self):
        """--indices is the whole point: a set --first/--num cannot express."""
        from utils.submit import _compute_jobset
        self.assertEqual(
            _compute_jobset(self._opts(indices=[14719, 15944, 24301]), 0),
            [14719, 15944, 24301])

    def test_indices_on_open_ended_cnf(self):
        """capacity 0 = open-ended: no upper bound to check against."""
        from utils.submit import _compute_jobset
        self.assertEqual(_compute_jobset(self._opts(indices=[99999]), 0), [99999])

    def test_indices_within_closed_capacity(self):
        from utils.submit import _compute_jobset
        self.assertEqual(_compute_jobset(self._opts(indices=[0, 9]), 10), [0, 9])

    def test_indices_beyond_closed_capacity_raises(self):
        from utils.submit import _compute_jobset
        with self.assertRaises(ValueError):
            _compute_jobset(self._opts(indices=[0, 10]), 10)

    def test_indices_rejects_windowed_entry(self):
        """Indices are absolute; a firstjob offset would double-count."""
        from utils.submit import _compute_jobset
        with self.assertRaises(ValueError):
            _compute_jobset(self._opts(indices=[5001]), 0,
                            firstjob=5000, entry_njobs=10)

    def test_indices_rejects_first_num(self):
        from utils.submit import _compute_jobset
        with self.assertRaises(ValueError):
            _compute_jobset(self._opts(first=1, indices=[5]), 0)

    def test_indices_negative_raises(self):
        from utils.submit import _compute_jobset
        with self.assertRaises(ValueError):
            _compute_jobset(self._opts(indices=[-1, 5]), 0)


class TestParseIndices(unittest.TestCase):
    """--indices/--indices-file parsing: sorted, deduped, comment-tolerant."""

    def test_none_when_neither_given(self):
        from utils.submit import _parse_indices
        self.assertIsNone(_parse_indices(None, None))

    def test_comma_and_whitespace_separated(self):
        from utils.submit import _parse_indices
        self.assertEqual(_parse_indices('3,1 2', None), [1, 2, 3])

    def test_sorts_and_dedupes(self):
        from utils.submit import _parse_indices
        self.assertEqual(_parse_indices('5,1,5,1', None), [1, 5])

    def test_mutually_exclusive(self):
        from utils.submit import _parse_indices
        with self.assertRaises(ValueError):
            _parse_indices('1', '/tmp/whatever')

    def test_non_integer_raises(self):
        from utils.submit import _parse_indices
        with self.assertRaises(ValueError):
            _parse_indices('1,abc', None)

    def test_empty_spec_raises(self):
        from utils.submit import _parse_indices
        with self.assertRaises(ValueError):
            _parse_indices(' , ', None)

    def test_file_ignores_comments_and_blanks(self):
        """Consumes `mkrecovery --print-indices` output, whose `# <tarball>`
        headers must not parse as indices."""
        import tempfile
        from utils.submit import _parse_indices
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write("# cnf.mu2e.MuStopPileup.Run1Ban-001.0.tar\n"
                    "14719\n"
                    "\n"
                    "15944  # trailing comment\n")
            path = f.name
        try:
            self.assertEqual(_parse_indices(None, path), [14719, 15944])
        finally:
            os.unlink(path)


class TestIndicesOpsEntryContract(unittest.TestCase):
    """The worker-side half of --indices: submit_entry_direct ships
    `{**entry, firstjob: 0, njobs: max+1}`, which must make resolve_map_index
    an identity (local == the absolute cnf index) for every submitted index."""

    def test_resolve_map_index_is_identity(self):
        from utils.prod_utils import resolve_map_index
        indices = [14719, 15944, 24301]
        ops_entry = {'tarball': 'cnf.mu2e.X.0.tar', 'firstjob': 0,
                     'njobs': indices[-1] + 1}          # mirrors submit.py
        for k in indices:
            entry, _, local = resolve_map_index([ops_entry], k)
            self.assertIsNotNone(entry, f"index {k} unreachable")
            self.assertEqual(local, k)

    def test_njobs_without_the_plus_one_drops_the_max_index(self):
        """Pins the +1: resolve_map_index gates on `global < njobs`, so
        njobs == max would put the largest index out of range."""
        from utils.prod_utils import resolve_map_index
        ops_entry = {'tarball': 'cnf.mu2e.X.0.tar', 'firstjob': 0, 'njobs': 24301}
        self.assertEqual(resolve_map_index([ops_entry], 24301), (None, None, None))


class TestMkrecoveryPrintIndices(unittest.TestCase):
    """print_indices emits ABSOLUTE cnf indices (firstjob + window-relative)."""

    def test_adds_firstjob_offset_and_headers(self):
        import contextlib
        from utils.mkrecovery import print_indices
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_indices('cnf.mu2e.X.0.tar', 15000, {944, 991})
        self.assertEqual(buf.getvalue().splitlines(),
                         ['# cnf.mu2e.X.0.tar', '15944', '15991'])

    def test_zero_firstjob_is_identity(self):
        import contextlib
        from utils.mkrecovery import print_indices
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_indices('cnf.mu2e.X.0.tar', 0, {7})
        self.assertEqual(buf.getvalue().splitlines(), ['# cnf.mu2e.X.0.tar', '7'])


class TestLogStorageLocation(unittest.TestCase):
    """Logs go to persistent disk regardless of where data lands — the
    Mu2e convention and what the POMS path does. Only `scratch` runs keep
    logs beside their data (no persistent scope for non-mu2epro accounts).

    Regression: the first direct campaign put 500 log files on tape
    because logs inherited the data output's location.
    """

    def test_tape_data_keeps_logs_on_disk(self):
        from utils.job_common import log_storage_location
        outputs = [{'dataset': 'dig.mu2e.*.art', 'location': 'tape'}]
        self.assertEqual(log_storage_location(outputs), 'disk')

    def test_scratch_data_keeps_logs_on_scratch(self):
        from utils.job_common import log_storage_location
        outputs = [{'dataset': 'dig.mu2e.*.art', 'location': 'scratch'}]
        self.assertEqual(log_storage_location(outputs), 'scratch')

    def test_disk_data_keeps_logs_on_disk(self):
        from utils.job_common import log_storage_location
        outputs = [{'dataset': 'dig.mu2e.*.art', 'location': 'disk'}]
        self.assertEqual(log_storage_location(outputs), 'disk')

    def test_accepts_entry_dict(self):
        from utils.job_common import log_storage_location
        entry = {'tarball': 'cnf.mu2e.X.0.tar',
                 'outputs': [{'dataset': 'dig.mu2e.*.art', 'location': 'tape'}]}
        self.assertEqual(log_storage_location(entry), 'disk')

    def test_missing_outputs_defaults_to_disk(self):
        from utils.job_common import log_storage_location
        self.assertEqual(log_storage_location([]), 'disk')
        self.assertEqual(log_storage_location({}), 'disk')

    def test_only_first_output_consulted_for_scratch(self):
        """A tape-first entry stays on disk even with a scratch sibling."""
        from utils.job_common import log_storage_location
        outputs = [{'dataset': 'dig.mu2e.*.art', 'location': 'tape'},
                   {'dataset': 'nts.mu2e.*.root', 'location': 'scratch'}]
        self.assertEqual(log_storage_location(outputs), 'disk')


class TestPushLogsParents(unittest.TestCase):
    """The log push must never name a parents file that isn't on disk.

    pushOutput reports `ERROR - parents file parents_list.txt not found`
    and then exits 0, so a missing parents file makes the log push a
    silent no-op — the log never reaches SAM. That bites hardest on the
    failure path, where push_data is skipped and the log is the only
    evidence left. Observed 2026-07-21 on index 519.
    """

    def _capture(self, tmpdir, *, log_file=None, fcl=None, with_parents):
        """Run push_logs in tmpdir and return the parents column it chose."""
        from utils import runmu2e
        captured = {}

        def fake_push_output(output_specs, output_file="output.txt",
                             simjob_setup=None):
            captured['specs'] = output_specs
            return 0

        logname = log_file or runmu2e.replace_file_extensions(fcl, "log", "log")
        (Path(tmpdir) / logname).write_text('log contents\n')
        if with_parents:
            (Path(tmpdir) / 'parents_list.txt').write_text('in1.art\n')

        cwd = os.getcwd()
        env = dict(os.environ)
        os.environ.pop('JSB_TMP', None)   # don't pull in a jobsub log
        try:
            os.chdir(tmpdir)
            with patch.object(runmu2e, 'push_output', fake_push_output):
                runmu2e.push_logs(fcl=fcl, log_file=log_file)
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(env)

        self.assertIn('specs', captured, "push_output was never called")
        self.assertEqual(len(captured['specs']), 1)
        return captured['specs'][0][2]

    def test_art_success_uses_parents_list(self):
        """Data push ran and wrote parents_list.txt — use it."""
        with tempfile.TemporaryDirectory() as d:
            parents = self._capture(d, fcl='cnf.mu2e.X.MDC2025ar.519.fcl',
                                    with_parents=True)
            self.assertEqual(parents, 'parents_list.txt')

    def test_art_failure_falls_back_to_none(self):
        """mu2e failed, push_data was skipped, so parents_list.txt does not
        exist — the log must still be declarable."""
        with tempfile.TemporaryDirectory() as d:
            parents = self._capture(d, fcl='cnf.mu2e.X.MDC2025ar.519.fcl',
                                    with_parents=False)
            self.assertEqual(parents, 'none')

    def test_untracked_parents_falls_back_to_none(self):
        """track_parents=False (inloc dir:, non-SAM inputs) also leaves no
        parents_list.txt even though the job succeeded."""
        with tempfile.TemporaryDirectory() as d:
            parents = self._capture(d, fcl='cnf.mu2e.Y.MDC2025ar.7.fcl',
                                    with_parents=False)
            self.assertEqual(parents, 'none')

    def test_g4bl_still_none(self):
        """g4bl passes log_file explicitly and has no SAM parents."""
        with tempfile.TemporaryDirectory() as d:
            parents = self._capture(d, log_file='log.mu2e.G.MDC2025ar.3.log',
                                    with_parents=False)
            self.assertEqual(parents, 'none')

    def test_g4bl_ignores_stray_parents_file(self):
        """Even if a parents_list.txt is lying around, g4bl stays 'none'."""
        with tempfile.TemporaryDirectory() as d:
            parents = self._capture(d, log_file='log.mu2e.G.MDC2025ar.3.log',
                                    with_parents=True)
            self.assertEqual(parents, 'none')


class TestSingleBackend(unittest.TestCase):
    """submit_map is single-backend (direct): --backend is gone and
    rejected loudly as an unknown argument."""

    def test_backend_flag_rejected(self):
        from utils import submit
        with patch.object(sys, 'argv',
                          ['submit_map', '--map', 'x.json',
                           '--backend', 'direct']):
            with self.assertRaises(SystemExit) as cm:
                submit.main()
        self.assertEqual(cm.exception.code, 2)  # argparse usage error

    def test_mu2ejobsub_helpers_gone(self):
        from utils import submit
        self.assertFalse(hasattr(submit, 'build_mu2ejobsub_argv'))
        self.assertFalse(hasattr(submit, '_submit_entry_mu2ejobsub'))


class TestRunSubmitClusterVerification(unittest.TestCase):
    """_run_submit must not report 'submitted' without a parsed cluster ID —
    jobsub_lite can exit 0 while its internal condor_submit failed (the
    2026-07-10 condor_vault_storer incident)."""

    def _run(self, returncode, stdout):
        from utils import submit
        fake = MagicMock(returncode=returncode, stdout=stdout, stderr='')
        with patch.object(submit.subprocess, 'run', return_value=fake):
            return submit._run_submit(['jobsub_submit', '-N', '5'], 'cnf.tar', 5)

    def test_exit0_with_cluster_is_submitted(self):
        r = self._run(0, "5000 job(s) submitted to cluster 28708717.\n")
        self.assertEqual((r['status'], r['cluster_id']), ('submitted', '28708717'))

    def test_exit0_without_cluster_is_failed(self):
        r = self._run(0, "Submitting job(s)\nError: condor_submit exited "
                         "with failed status code 1\n")
        self.assertEqual((r['status'], r['cluster_id']), ('failed', None))

    def test_nonzero_exit_is_failed(self):
        r = self._run(1, "")
        self.assertEqual(r['status'], 'failed')


class TestJobdefsDedupePerWindow(unittest.TestCase):
    """_write_jobdef_json_entry dedupes on (tarball, firstjob): the same
    tarball may appear once per index window, but never twice per window."""

    def _write(self, path, entry):
        from utils.json2jobdef import _write_jobdef_json_entry
        _write_jobdef_json_entry(entry, str(path))

    def test_expansion_coexists_original_dedupes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'map.json'
            original = {'tarball': 'cnf.mu2e.X.C.0.tar', 'inloc': 'tape',
                        'njobs': 5000, 'outputs': []}
            expansion = {'tarball': 'cnf.mu2e.X.C.0.tar', 'inloc': 'tape',
                         'njobs': 100, 'firstjob': 5000, 'outputs': []}
            self._write(path, original)
            self._write(path, expansion)   # new window → appended
            self._write(path, dict(expansion))  # same window → deduped
            self._write(path, dict(original))   # same window → deduped
            entries = json.loads(path.read_text())
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[1].get('firstjob'), 5000)


class TestMkrecoveryWindow(unittest.TestCase):
    """find_missing_indices over a window: expected names come from cnf
    indices [firstjob, firstjob+njobs) but returned indices are
    WINDOW-RELATIVE, so callers map to global with plain cumulative+idx."""

    def test_windowed_expected_names(self):
        from utils import mkrecovery
        seen = []

        class FakeJP:
            def __init__(self, path):
                pass

            def job_outputs(self, idx):
                seen.append(idx)
                return {'out': f'dts.mu2e.X.C.001470_{idx:08d}.art'}

        with patch.object(mkrecovery, 'Mu2eJobPars', FakeJP), \
             patch.object(mkrecovery, 'files_in_dataset',
                          return_value=[f'dts.mu2e.X.C.001470_{i:08d}.art'
                                        for i in (5000, 5002)]):
            missing_idx, missing_files = mkrecovery.find_missing_indices(
                'x.tar', 'dts.mu2e.X.C.art', 3, firstjob=5000)
        self.assertEqual(seen, [5000, 5001, 5002])
        self.assertEqual(missing_idx, {1})  # window-relative, not 5001


class TestValidateWindow(unittest.TestCase):
    """validate_window is the single owner of the window rule, shared by
    the map writer (append_jobdef) and the submit path (_compute_jobset)."""

    def test_open_ended_any_window(self):
        from utils.poms_entry import validate_window
        validate_window(5000, 100, 0)      # capacity 0 = open-ended
        validate_window(5000, 100, None)

    def test_closed_capacity_enforced(self):
        from utils.poms_entry import validate_window
        validate_window(5000, 2000, 7000)  # exactly fits
        with self.assertRaises(ValueError):
            validate_window(5000, 2001, 7000)

    def test_njobs_required(self):
        from utils.poms_entry import validate_window
        with self.assertRaises(ValueError):
            validate_window(5000, None, 0)


class TestValidateJobdescFirstjob(unittest.TestCase):
    """Dispatch boundary: firstjob on an njobs-less entry must fail loud
    (maps are hand-edited; a silently-dropped window duplicates physics)."""

    def test_firstjob_without_njobs_rejected(self):
        from utils.runmu2e import validate_jobdesc
        bad = [{'tarball': 'cnf.mu2e.X.C.0.tar', 'inloc': 'tape',
                'outputs': [], 'firstjob': 5000}]
        with self.assertRaises(SystemExit):
            validate_jobdesc(bad)

    def test_firstjob_with_njobs_accepted(self):
        from utils.runmu2e import validate_jobdesc
        ok = [{'tarball': 'cnf.mu2e.X.C.0.tar', 'inloc': 'tape',
               'outputs': [], 'firstjob': 5000, 'njobs': 10}]
        self.assertEqual(validate_jobdesc(ok), False)  # normal mode


# ---------------------------------------------------------------------------
# 35. jobs_payload: static dashboard data builder (web/pomsMonitor/jobs_payload.py)
# ---------------------------------------------------------------------------

@requires_sqlalchemy
class TestJobsPayload(unittest.TestCase):
    """build_jobs_payload replaces the Flask /api/jobs route for render_static."""

    @classmethod
    def setUpClass(cls):
        d = os.path.join(os.path.dirname(__file__), '..', 'web', 'pomsMonitor')
        if d not in sys.path:
            sys.path.insert(0, d)
        import jobs_payload
        cls.jp = jobs_payload

    def test_empty_db_yields_empty_list_and_closes_session(self):
        mock_session = MagicMock()
        mock_session.query.return_value.all.return_value = []
        with patch.object(self.jp, 'get_db_session', return_value=mock_session), \
             patch.object(self.jp, 'build_dataset_info_map', return_value={}):
            self.assertEqual(self.jp.build_jobs_payload('/nonexistent.db'), [])
        mock_session.close.assert_called_once()

    def test_job_row_shape_matches_api_jobs(self):
        job = MagicMock(njobs=3, tarball='', source_file='x.json',
                        complete=True, avg_real_h=None, avg_vmhwm_gb=None,
                        outputs=[])
        mock_session = MagicMock()
        mock_session.query.return_value.all.return_value = [job]
        with patch.object(self.jp, 'get_db_session', return_value=mock_session), \
             patch.object(self.jp, 'build_dataset_info_map', return_value={}):
            payload = self.jp.build_jobs_payload('/nonexistent.db')
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['njobs'], 3)
        self.assertEqual(payload[0]['setup_script'], '')
        self.assertEqual(payload[0]['outputs'], [])
        self.assertEqual(
            sorted(payload[0].keys()),
            sorted(['njobs', 'tarball', 'source_file', 'setup_script',
                    'complete', 'avg_real_h', 'avg_vmhwm_gb', 'outputs']))

# ---------------------------------------------------------------------------
# Submission ledger (utils/submission_ledger.py) — direct-backend recovery
# ---------------------------------------------------------------------------
class TestSubmissionLedger(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 5, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}

    def _record(self, indices=(0, 1, 2), parent=None):
        return self.sl.record_submission(
            self.db, tarball=self.entry['tarball'], entry=self.entry,
            indices=list(indices), jobsub_id='12345678.0@jobsub03.fnal.gov',
            cluster_id='12345678', map_path='/tmp/map.json', parent_id=parent)

    def test_record_and_read_roundtrip(self):
        rid = self._record()
        rows = self.sl.open_rows(self.db)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['id'], rid)
        self.assertEqual(row['state'], 'active')
        self.assertEqual(row['attempt'], 1)
        self.assertIsNone(row['parent_id'])
        self.assertEqual(row['indices'], [0, 1, 2])
        self.assertEqual(row['entry'], self.entry)
        self.assertEqual(row['jobsub_id'], '12345678.0@jobsub03.fnal.gov')
        self.assertEqual(row['cluster_id'], '12345678')

    def test_indices_stored_sorted(self):
        self._record(indices=(7, 2, 5))
        self.assertEqual(self.sl.open_rows(self.db)[0]['indices'], [2, 5, 7])

    def test_child_attempt_increments(self):
        rid = self._record()
        child = self._record(indices=(2,), parent=rid)
        rows = {r['id']: r for r in self.sl.open_rows(self.db)}
        self.assertEqual(rows[child]['attempt'], 2)
        self.assertEqual(rows[child]['parent_id'], rid)

    def test_unknown_parent_rejected(self):
        with self.assertRaises(ValueError):
            self._record(parent=999)

    def test_close_row_removes_from_open(self):
        rid = self._record()
        self.sl.close_row(self.db, rid, 'complete', note='all verified')
        self.assertEqual(self.sl.open_rows(self.db), [])
        allr = self.sl.all_rows(self.db)
        self.assertEqual(allr[0]['state'], 'complete')
        self.assertEqual(allr[0]['note'], 'all verified')
        self.assertIsNotNone(allr[0]['closed_utc'])

    def test_close_invalid_state_rejected(self):
        rid = self._record()
        with self.assertRaises(ValueError):
            self.sl.close_row(self.db, rid, 'bogus')
        with self.assertRaises(ValueError):
            self.sl.close_row(self.db, rid, 'active')

    def test_close_nonactive_row_rejected(self):
        rid = self._record()
        self.sl.close_row(self.db, rid, 'complete')
        with self.assertRaises(ValueError):
            self.sl.close_row(self.db, rid, 'exhausted')

    def test_missing_db_dir_fails_loudly(self):
        import sqlite3
        with self.assertRaises(sqlite3.OperationalError):
            self.sl.record_submission(
                '/nonexistent-dir-recovery-test/sub.db', tarball='t',
                entry={}, indices=[0], jobsub_id=None, cluster_id='1')


class TestCampaignLedger(unittest.TestCase):
    """campaigns table in utils/submission_ledger.py (sliced submission)."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 10, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}

    def _create(self, tarball=None, slice_size=4):
        return self.sl.create_campaign(
            self.db, tarball=tarball or self.entry['tarball'],
            entry=self.entry, slice_size=slice_size,
            map_path='/tmp/map.json')

    def test_create_and_read_roundtrip(self):
        cid = self._create()
        camps = self.sl.active_campaigns(self.db)
        self.assertEqual(len(camps), 1)
        c = camps[0]
        self.assertEqual(c['id'], cid)
        self.assertEqual(c['state'], 'active')
        self.assertEqual(c['cursor'], 0)
        self.assertEqual(c['slice_size'], 4)
        self.assertEqual(c['entry'], self.entry)
        self.assertEqual(c['map_path'], '/tmp/map.json')
        self.assertIsNone(c['closed_utc'])

    def test_duplicate_active_tarball_refused(self):
        self._create()
        with self.assertRaises(ValueError):
            self._create()

    def test_duplicate_paused_tarball_refused(self):
        """A paused campaign still owns its index space: enqueue-after-
        pause then resume would feed two campaigns into the same
        indices (double submit) — refuse it just like an active one."""
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'paused')
        with self.assertRaises(ValueError) as cm:
            self._create()
        self.assertIn('paused', str(cm.exception))

    def test_reenqueue_allowed_after_close(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'cancelled')
        self._create()  # must not raise
        self.assertEqual(len(self.sl.all_campaigns(self.db)), 2)

    def test_slice_size_validated(self):
        with self.assertRaises(ValueError):
            self._create(slice_size=0)

    def test_advance_cursor(self):
        cid = self._create()
        self.sl.advance_campaign(self.db, cid, 4)
        self.assertEqual(self.sl.active_campaigns(self.db)[0]['cursor'], 4)

    def test_advance_backward_refused(self):
        cid = self._create()
        self.sl.advance_campaign(self.db, cid, 4)
        with self.assertRaises(ValueError):
            self.sl.advance_campaign(self.db, cid, 2)

    def test_advance_nonactive_refused(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'paused')
        with self.assertRaises(ValueError):
            self.sl.advance_campaign(self.db, cid, 4)

    def test_state_transitions(self):
        cid = self._create()
        self.sl.set_campaign_state(self.db, cid, 'paused', note='op pause')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'], 'paused')
        self.sl.set_campaign_state(self.db, cid, 'active')   # resume
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['state'], 'active')
        self.assertIsNone(c['closed_utc'])                   # reopened
        self.sl.set_campaign_state(self.db, cid, 'complete')
        self.assertIsNotNone(self.sl.all_campaigns(self.db)[0]['closed_utc'])

    def test_invalid_transitions_raise(self):
        cid = self._create()
        with self.assertRaises(ValueError):
            self.sl.set_campaign_state(self.db, cid, 'nonsense')
        self.sl.set_campaign_state(self.db, cid, 'complete')
        with self.assertRaises(ValueError):
            self.sl.set_campaign_state(self.db, cid, 'active')  # complete is terminal
        with self.assertRaises(ValueError):
            self.sl.set_campaign_state(self.db, 999, 'paused')  # no such id


# ---------------------------------------------------------------------------
# Entry resource keys (utils/poms_entry.py, utils/submit.py)
# ---------------------------------------------------------------------------
class TestEntryResources(unittest.TestCase):
    """memory/disk/expected_lifetime: entry keys, precedence, snapshot."""

    def _opts(self, memory=None, disk=None, expected_lifetime=None):
        import argparse
        return argparse.Namespace(memory=memory, disk=disk,
                                  expected_lifetime=expected_lifetime)

    def test_resources_of_subset(self):
        from utils.poms_entry import resources_of
        self.assertEqual(resources_of({'tarball': 't'}), {})
        self.assertEqual(
            resources_of({'memory': '4000MB', 'njobs': 5}),
            {'memory': '4000MB'})
        self.assertEqual(
            resources_of({'memory': '4000MB', 'disk': '50GB',
                          'expected_lifetime': '48h'}),
            {'memory': '4000MB', 'disk': '50GB', 'expected_lifetime': '48h'})

    def test_resources_of_nonstring_raises(self):
        from utils.poms_entry import resources_of
        with self.assertRaises(ValueError):
            resources_of({'memory': 4000})

    def test_effective_cli_beats_entry(self):
        from utils.submit import _effective_resources
        eff = _effective_resources({'memory': '4000MB'},
                                   self._opts(memory='8000MB'))
        self.assertEqual(eff['memory'], '8000MB')

    def test_effective_entry_beats_default(self):
        from utils.submit import _effective_resources
        eff = _effective_resources({'memory': '4000MB'}, self._opts())
        self.assertEqual(eff['memory'], '4000MB')
        self.assertIsNone(eff['disk'])            # None -> jobsub_argv builtin
        self.assertIsNone(eff['expected_lifetime'])

    def test_snapshot_merges_without_mutating(self):
        from utils.submit import _snapshot_entry
        entry = {'tarball': 't', 'njobs': 5}
        snap = _snapshot_entry(entry, {'memory': '8000MB', 'disk': None,
                                       'expected_lifetime': None})
        self.assertEqual(snap['memory'], '8000MB')
        self.assertNotIn('disk', snap)
        self.assertNotIn('memory', entry)         # original untouched

    def test_append_jobdef_passes_resource_keys(self):
        import tempfile
        from utils import json2jobdef
        out = os.path.join(tempfile.mkdtemp(), 'map.json')
        config = {'desc': 'TestDesc', 'dsconf': 'TestConf', 'owner': 'mu2e',
                  'inloc': 'tape', 'njobs': 5, 'memory': '4000MB',
                  'outloc': {'sim.mu2e.TestDesc.TestConf.art': 'tape'}}
        json2jobdef.append_jobdef(config, jobdefs_file=out)
        with open(out) as f:
            entry = json.load(f)[0]
        self.assertEqual(entry['memory'], '4000MB')
        self.assertNotIn('disk', entry)           # absent key stays absent


class TestSubmitEntryDirectResourceWiring(unittest.TestCase):
    """submit_entry_direct must actually pass the EFFECTIVE resources
    (entry key, no CLI flag) into build_jobsub_argv — the precedence
    logic itself is covered above (_effective_resources), this closes
    the gap that nothing proved submit_entry_direct wires it through."""

    def test_entry_memory_reaches_build_jobsub_argv(self):
        import argparse
        from utils.submit import submit_entry_direct

        entry = {'tarball': 'cnf.mu2e.NoSuchTarballXYZ.TestConf.0.tar',
                 'njobs': 5, 'inloc': 'tape',
                 'outputs': [{'location': 'tape'}], 'memory': '4000MB'}
        opts = argparse.Namespace(
            dry_run=True, indices=None, first=None, num=None,
            prodtools_tar=None, role=None, wftop=None, wfproject=None,
            disk=None, memory=None, expected_lifetime=None,
            no_ledger=True, ledger_db='/tmp/unused-resource-wiring.db',
            ledger_parent=None, map='/tmp/m.json', verbose=False)

        captured = {}

        def fake_build_jobsub_argv(**kwargs):
            captured.update(kwargs)
            return ['--fake-argv']

        with patch('utils.submit._jobsub_argv.build_jobsub_argv',
                   side_effect=fake_build_jobsub_argv), \
             patch('utils.submit._bundle_prodtools',
                   return_value=Path('/tmp/fake-prodtools.tar')):
            result = submit_entry_direct(entry, 0, opts)

        self.assertEqual(result['status'], 'dry_run')
        # entry key, no CLI flag -> _effective_resources picks the entry
        self.assertEqual(captured['memory'], '4000MB')
        self.assertIsNone(captured['disk'])
        self.assertIsNone(captured['expected_lifetime'])


# ---------------------------------------------------------------------------
# submit_map --enqueue (utils/submit.py) — sliced-campaign submission
# ---------------------------------------------------------------------------
class TestEnqueue(unittest.TestCase):
    """submit_map --enqueue: campaign registration, no submission."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        from utils import submit
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.entry = {'tarball': 'cnf.mu2e.TestDesc.TestConf.0.tar',
                      'njobs': 10, 'inloc': 'tape',
                      'outputs': [{'location': 'tape'}]}
        # Task 6 enqueue gate reads the tarball; stub tarball resolution
        # and the pre-flight check so these campaign-registration tests
        # stay file-free (as they were before the gate existed).
        tb_patcher = patch.object(submit, '_ensure_local_tarball',
                                  return_value=Path(self.entry['tarball']))
        ci_patcher = patch.object(submit, 'check_inputs',
                                  return_value=(True, []))
        tb_patcher.start()
        ci_patcher.start()
        self.addCleanup(tb_patcher.stop)
        self.addCleanup(ci_patcher.stop)

    def _opts(self, dry_run=False, slice_size=100, memory=None):
        import argparse
        return argparse.Namespace(
            ledger_db=self.db, slice_size=slice_size, dry_run=dry_run,
            memory=memory, disk=None, expected_lifetime=None)

    def test_enqueue_writes_campaign(self):
        from utils.submit import _enqueue_entries
        ids = _enqueue_entries([(0, self.entry)], '/tmp/m.json', self._opts())
        camps = self.sl.active_campaigns(self.db)
        self.assertEqual([c['id'] for c in camps], ids)
        c = camps[0]
        self.assertEqual(c['tarball'], self.entry['tarball'])
        self.assertEqual(c['slice_size'], 100)
        self.assertEqual(c['cursor'], 0)
        self.assertEqual(c['map_path'], '/tmp/m.json')
        self.assertEqual(c['entry'], self.entry)
        # nothing submitted: the submissions table stays empty
        self.assertEqual(self.sl.open_rows(self.db), [])

    def test_enqueue_merges_cli_resources_into_snapshot(self):
        from utils.submit import _enqueue_entries
        _enqueue_entries([(0, self.entry)], '/tmp/m.json',
                         self._opts(memory='4000MB'))
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['entry']['memory'], '4000MB')
        self.assertNotIn('memory', self.entry)     # original untouched

    def test_enqueue_dry_run_writes_nothing(self):
        from utils.submit import _enqueue_entries
        ids = _enqueue_entries([(0, self.entry)], '/tmp/m.json',
                               self._opts(dry_run=True))
        self.assertEqual(ids, [])
        self.assertEqual(self.sl.all_campaigns(self.db), [])

    def test_enqueue_duplicate_is_hard_error(self):
        from utils.submit import _enqueue_entries
        _enqueue_entries([(0, self.entry)], '/tmp/m.json', self._opts())
        with self.assertRaises(SystemExit):
            _enqueue_entries([(0, self.entry)], '/tmp/m.json', self._opts())

    def test_enqueue_generic_entry_refused(self):
        from utils.submit import _enqueue_entries
        generic = {'tarball': 'cnf.mu2e.G.C.0.tar', 'inloc': 'tape',
                   'outputs': []}   # no njobs
        with self.assertRaises(SystemExit):
            _enqueue_entries([(0, generic)], '/tmp/m.json', self._opts())

    def test_enqueue_zero_njobs_refused(self):
        """njobs_of(entry) is None misses njobs: 0 — a zero-job campaign
        is nonsensical and must be refused just like the missing case."""
        from utils.submit import _enqueue_entries
        zero = {'tarball': 'cnf.mu2e.Z.C.0.tar', 'njobs': 0,
                'inloc': 'tape', 'outputs': []}
        with self.assertRaises(SystemExit):
            _enqueue_entries([(0, zero)], '/tmp/m.json', self._opts())

    def test_enqueue_db_failure_is_hard_error(self):
        from utils.submit import _enqueue_entries
        import argparse
        opts = argparse.Namespace(
            ledger_db='/nonexistent-dir-enqueue-test/s.db', slice_size=10,
            dry_run=False, memory=None, disk=None, expected_lifetime=None)
        with self.assertRaises(SystemExit):
            _enqueue_entries([(0, self.entry)], '/tmp/m.json', opts)


class TestEnqueueErrorStyle(unittest.TestCase):
    """Operator-reachable enqueue failures are one-line submit_map:
    messages, not tracebacks; --enqueue --no-ledger is refused."""

    def setUp(self):
        import tempfile
        from types import SimpleNamespace
        from utils import submit
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, 'sub.db')
        self.opts = SimpleNamespace(
            ledger_db=self.db, slice_size=10, dry_run=False,
            memory=None, disk=None, expected_lifetime=None)
        # Task 6 enqueue gate reads the tarball; stub tarball resolution
        # and the pre-flight check so these tests stay file-free.
        tb_patcher = patch.object(submit, '_ensure_local_tarball',
                                  return_value=Path('cnf.mu2e.E.C.0.tar'))
        ci_patcher = patch.object(submit, 'check_inputs',
                                  return_value=(True, []))
        tb_patcher.start()
        ci_patcher.start()
        self.addCleanup(tb_patcher.stop)
        self.addCleanup(ci_patcher.stop)

    def _entry(self, tarball='cnf.mu2e.E.C.0.tar'):
        return {'tarball': tarball, 'njobs': 50}

    def test_duplicate_enqueue_one_line_no_traceback(self):
        from utils import submit
        submit._enqueue_entries([(0, self._entry())], 'm.json', self.opts)
        with self.assertRaises(SystemExit) as cm:
            submit._enqueue_entries([(0, self._entry())], 'm.json',
                                    self.opts)
        msg = str(cm.exception.code)
        self.assertTrue(msg.startswith('submit_map: '), msg)
        self.assertNotIn('\n', msg)
        self.assertNotIn('Traceback', msg)

    def test_db_error_one_line(self):
        from utils import submit
        self.opts.ledger_db = os.path.join(self.tmp, 'no', 'such',
                                           'dir', 'sub.db')
        with self.assertRaises(SystemExit) as cm:
            submit._enqueue_entries([(0, self._entry())], 'm.json',
                                    self.opts)
        self.assertTrue(str(cm.exception.code).startswith('submit_map: '))

    def test_enqueue_no_ledger_refused(self):
        from utils import submit
        import io as _io
        buf = _io.StringIO()
        with patch('sys.stdout', buf), \
             patch.object(sys, 'argv',
                          ['submit_map', '--map', 'nonexistent.json',
                           '--enqueue', '--no-ledger']):
            with self.assertRaises(SystemExit) as cm:
                submit.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn('--no-ledger contradicts it', buf.getvalue())


# ---------------------------------------------------------------------------
# Submission log (utils/submit.py) — dated per-attempt record
# ---------------------------------------------------------------------------
class TestSubmissionLog(unittest.TestCase):
    """Dated per-submission log beside the ledger DB (all origins:
    manual runs, cron slices, recovery resubmits)."""

    def setUp(self):
        import tempfile
        self.dbdir = tempfile.mkdtemp()
        self.db = os.path.join(self.dbdir, 'submissions.db')

    def _opts(self, no_ledger=False):
        import argparse
        return argparse.Namespace(ledger_db=self.db, no_ledger=no_ledger,
                                  map='/tmp/m.json')

    def _result(self, status='submitted'):
        return {'tarball': 'cnf.mu2e.T.C.0.tar', 'cluster_id': '123',
                'jobsub_id': '123.0@js.fnal.gov', 'njobs': 3,
                'status': status,
                'raw_output': 'Use job id 123.0@js.fnal.gov ...\n'}

    def _read_log(self):
        from utils.submit import _submission_log_path
        with open(_submission_log_path(self.db)) as f:
            return f.read()

    def test_success_block_appended(self):
        from utils.submit import _log_submission
        _log_submission(100, [0, 1, 2], self._result(), self._opts())
        text = self._read_log()
        self.assertIn('status=submitted', text)
        self.assertIn('cnf.mu2e.T.C.0.tar', text)
        self.assertIn('[100..102]', text)          # absolute indices
        self.assertIn('Use job id 123.0@js.fnal.gov', text)

    def test_failure_block_appended(self):
        from utils.submit import _log_submission
        _log_submission(0, [0], self._result(status='failed'), self._opts())
        self.assertIn('status=failed', self._read_log())

    def test_appends_not_truncates(self):
        from utils.submit import _log_submission
        _log_submission(0, [0], self._result(), self._opts())
        _log_submission(0, [1], self._result(), self._opts())
        self.assertEqual(self._read_log().count('=== end'), 2)

    def test_write_failure_never_raises(self):
        from utils.submit import _log_submission
        import argparse
        opts = argparse.Namespace(
            ledger_db='/nonexistent-dir-submitlog-test/s.db',
            no_ledger=False, map='/tmp/m.json')
        _log_submission(0, [0], self._result(), opts)  # must not raise

    def test_run_submit_carries_raw_output(self):
        from utils import submit
        fake = MagicMock(
            returncode=0, stderr='warn\n',
            stdout='1 job(s) submitted to cluster 12345678.\n'
                   'Use job id 12345678.0@jobsub03.fnal.gov to retrieve output\n')
        with patch('utils.submit.subprocess.run', return_value=fake):
            r = submit._run_submit(['jobsub_submit'], 'cnf.tar', 3)
        self.assertIn('Use job id', r['raw_output'])
        self.assertIn('warn', r['raw_output'])

    def test_run_submit_failure_carries_raw_output(self):
        from utils import submit
        fake = MagicMock(returncode=1, stderr='boom\n', stdout='')
        with patch('utils.submit.subprocess.run', return_value=fake):
            r = submit._run_submit(['jobsub_submit'], 'cnf.tar', 3)
        self.assertEqual(r['status'], 'failed')
        self.assertIn('boom', r['raw_output'])


class TestRecoverCap(unittest.TestCase):
    """Cap resolution + queue counting for the top-up phase."""

    def test_resolve_cap_flag_wins(self):
        from utils import submissions as recover
        with patch.dict(os.environ, {'MU2E_MAX_QUEUED': '5'}):
            self.assertEqual(recover.resolve_cap(42), 42)

    def test_resolve_cap_env_beats_default(self):
        from utils import submissions as recover
        with patch.dict(os.environ, {'MU2E_MAX_QUEUED': '5'}):
            self.assertEqual(recover.resolve_cap(None), 5)

    def test_resolve_cap_default(self):
        from utils import submissions as recover
        env = {k: v for k, v in os.environ.items() if k != 'MU2E_MAX_QUEUED'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(recover.resolve_cap(None),
                             recover.DEFAULT_MAX_QUEUED)

    def test_resolve_cap_bad_env_exits(self):
        from utils import submissions as recover
        with patch.dict(os.environ, {'MU2E_MAX_QUEUED': 'lots'}):
            with self.assertRaises(SystemExit):
                recover.resolve_cap(None)

    def _runner(self, stdout, rc=0):
        def run(cmd, capture_output=True, text=True):
            self.cmd = cmd
            return MagicMock(returncode=rc, stdout=stdout, stderr='')
        return run

    # Realistic jobsub_q DEFAULT-table shapes (captured 2026-07-21).
    # The -af JobStatus passthrough is unreliable on jobsub_lite (blank
    # values / dropped --user filter), so the probes parse this table.
    _HDR = ('JOBSUBJOBID                             OWNER       \t'
            'SUBMITTED     RUNTIME   ST PRIO   SIZE  COMMAND')
    _SUM = ('0 total; 0 completed, 0 removed, 0 idle, 0 running, '
            '0 held, 0 suspended')

    @staticmethod
    def _row(jobid, owner, st):
        return (f'{jobid}            {owner}   \t01/09 06:00   '
                f'0+00:00:00 {st}    0    0.0 job.sh ')

    def test_total_queued_counts_idle_and_running_only(self):
        from utils.submissions import total_queued
        table = '\n'.join([
            self._HDR, self._SUM,
            self._row('1.0@jobsub01.fnal.gov', 'mu2epro', 'I'),
            self._row('2.0@jobsub01.fnal.gov', 'mu2epro', 'R'),
            self._row('3.0@jobsub02.fnal.gov', 'mu2epro', 'R'),
            self._row('4.0@jobsub02.fnal.gov', '|-WORKER_12', 'H'),
            self._row('5.0@jobsub02.fnal.gov', 'mu2epro', 'X'),
        ]) + '\n'
        n = total_queued(runner=self._runner(table))
        self.assertEqual(n, 3)              # held / removed excluded
        self.assertEqual(self.cmd, ['jobsub_q', '--user', 'mu2epro'])

    def test_total_queued_empty_table_is_zero(self):
        from utils.submissions import total_queued
        table = self._HDR + '\n' + self._SUM + '\n'
        self.assertEqual(total_queued(runner=self._runner(table)), 0)

    def test_total_queued_headerless_is_none(self):
        # No JOBSUBJOBID header = we did not get the table (error page,
        # empty stdout, or the -af blank-line failure mode) — fail closed.
        from utils.submissions import total_queued
        self.assertIsNone(total_queued(runner=self._runner('')))
        self.assertIsNone(total_queued(runner=self._runner('\n\n\n')))

    def test_total_queued_failure_is_none(self):
        from utils.submissions import total_queued
        self.assertIsNone(total_queued(runner=self._runner('', rc=1)))

    def test_total_queued_garbage_row_is_none(self):
        from utils.submissions import total_queued
        table = '\n'.join([self._HDR,
                           self._row('1.0@jobsub01.fnal.gov', 'mu2epro',
                                     'I'),
                           'some unexpected diagnostic line']) + '\n'
        self.assertIsNone(total_queued(runner=self._runner(table)))

    def test_total_queued_unknown_state_is_none(self):
        from utils.submissions import total_queued
        table = '\n'.join([self._HDR,
                           self._row('1.0@jobsub01.fnal.gov', 'mu2epro',
                                     'Z')]) + '\n'
        self.assertIsNone(total_queued(runner=self._runner(table)))

    def test_total_queued_skips_token_noise(self):
        from utils.submissions import total_queued
        table = '\n'.join([
            'Attempting to get token from https://htvaultprod ... ok',
            'Storing bearer token in /tmp/bt_token_mu2e_x',
            self._HDR, self._SUM,
            self._row('1.0@jobsub01.fnal.gov', 'mu2epro', 'I'),
        ]) + '\n'
        self.assertEqual(total_queued(runner=self._runner(table)), 1)


class TestTopUp(unittest.TestCase):
    """Slice feeding: cap gate, whole slices, round-robin, pause."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.calls = []

    def _campaign(self, tarball='cnf.mu2e.A.C.0.tar', njobs=10, slice=4):
        entry = {'tarball': tarball, 'njobs': njobs, 'inloc': 'tape',
                 'outputs': []}
        return self.sl.create_campaign(self.db, tarball=tarball,
                                       entry=entry, slice_size=slice)

    def _submit(self, ok=True):
        def fn(camp, n, db_path):
            self.calls.append((camp['id'], camp['cursor'], n))
            return ok
        return fn

    def test_feeds_until_complete(self):
        from utils.submissions import top_up
        cid = self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=100, count_fn=lambda: 0,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [(cid, 0, 4), (cid, 4, 4), (cid, 8, 2)])
        self.assertEqual(s['slice'], 3)
        self.assertEqual(s['campaign-complete'], 1)
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'],
                         'complete')

    def test_cap_stops_whole_slice(self):
        from utils.submissions import top_up
        self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=100, count_fn=lambda: 97,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [])            # 97+4 > 100: wait
        self.assertEqual(s['cap-wait'], 1)
        self.assertEqual(self.sl.active_campaigns(self.db)[0]['cursor'], 0)

    def test_cap_exact_fit_submits(self):
        from utils.submissions import top_up
        self._campaign(njobs=4, slice=4)
        top_up(self.db, cap=100, count_fn=lambda: 96,
               submit_fn=self._submit())
        self.assertEqual(len(self.calls), 1)        # 96+4 == 100 fits

    def test_submitted_slices_consume_headroom(self):
        from utils.submissions import top_up
        self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=8, count_fn=lambda: 0,
                   submit_fn=self._submit())
        self.assertEqual(len(self.calls), 2)        # 0+4, 4+4; 8+2 > 8 waits
        self.assertEqual(s['cap-wait'], 1)

    def test_failure_pauses_without_advancing(self):
        from utils.submissions import top_up
        cid = self._campaign()
        s = top_up(self.db, cap=100, count_fn=lambda: 0,
                   submit_fn=self._submit(ok=False))
        c = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(c['state'], 'paused')
        self.assertEqual(c['cursor'], 0)
        self.assertEqual(s['campaign-paused'], 1)

    def test_round_robin_two_campaigns(self):
        from utils.submissions import top_up
        a = self._campaign(tarball='cnf.mu2e.A.C.0.tar', njobs=4, slice=2)
        b = self._campaign(tarball='cnf.mu2e.B.C.0.tar', njobs=2, slice=2)
        top_up(self.db, cap=100, count_fn=lambda: 0,
               submit_fn=self._submit())
        self.assertEqual(self.calls,
                         [(a, 0, 2), (b, 0, 2), (a, 2, 2)])

    def test_no_campaigns_skips_count(self):
        from utils.submissions import top_up
        def boom():
            raise AssertionError("count_fn must not be called")
        self.assertEqual(top_up(self.db, cap=100, count_fn=boom), {})

    def test_count_failure_skips_topup(self):
        from utils.submissions import top_up
        self._campaign()
        s = top_up(self.db, cap=100, count_fn=lambda: None,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [])
        self.assertEqual(s['count-error'], 1)

    def test_dry_run_reports_and_writes_nothing(self):
        from utils.submissions import top_up
        def boom(camp, n, db_path):
            raise AssertionError("submit_fn must not be called in dry-run")
        self._campaign(njobs=10, slice=4)
        s = top_up(self.db, cap=100, dry_run=True, count_fn=lambda: 0,
                   submit_fn=boom)
        self.assertEqual(s['would-slice'], 3)
        self.assertEqual(s['would-campaign-complete'], 1)
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['cursor'], 0)            # DB untouched
        self.assertEqual(c['state'], 'active')

    # -- crash-window / ledger-overlap guard (Fix 2) -----------------------

    def test_overlap_pauses_without_submitting(self):
        """A ledger row already covering part of the next slice window
        (crash-window: parent submit_map died after jobsub_submit
        succeeded but before its own ledger write) must pause the
        campaign rather than resubmit — never a blind double-submit."""
        from utils.submissions import top_up
        tarball = 'cnf.mu2e.A.C.0.tar'
        cid = self._campaign(tarball=tarball, njobs=10, slice=4)
        self.sl.record_submission(
            self.db, tarball=tarball, entry={}, indices=[2],
            jobsub_id='9.0@js', cluster_id='9')  # inside [0,4)
        s = top_up(self.db, cap=100, count_fn=lambda: 0,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [])
        c = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(c['state'], 'paused')
        self.assertEqual(c['cursor'], 0)
        self.assertIn('crash-window', c['note'])
        self.assertEqual(s['campaign-paused'], 1)

    def test_overlap_below_cursor_does_not_block(self):
        """Ledger rows for the same tarball covering only windows BELOW
        the cursor (e.g. the recovery loop's own resubmits of already-
        submitted-but-missing indices) must not block a future slice —
        those indices can never intersect [cursor, cursor+n)."""
        from utils.submissions import top_up
        tarball = 'cnf.mu2e.A.C.0.tar'
        cid = self._campaign(tarball=tarball, njobs=10, slice=4)
        self.sl.advance_campaign(self.db, cid, 4)  # simulate prior slice
        self.sl.record_submission(
            self.db, tarball=tarball, entry={}, indices=[0, 1, 2, 3],
            jobsub_id='9.0@js', cluster_id='9')  # all below cursor=4
        s = top_up(self.db, cap=100, count_fn=lambda: 0,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [(cid, 4, 4), (cid, 8, 2)])
        self.assertNotIn('campaign-paused', s)
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'],
                         'complete')

    def test_overlap_dry_run_reports_and_writes_nothing(self):
        from utils.submissions import top_up
        def boom(camp, n, db_path):
            raise AssertionError("submit_fn must not be called in dry-run")
        tarball = 'cnf.mu2e.A.C.0.tar'
        self._campaign(tarball=tarball, njobs=10, slice=4)
        self.sl.record_submission(
            self.db, tarball=tarball, entry={}, indices=[1],
            jobsub_id='9.0@js', cluster_id='9')  # inside [0,4)
        s = top_up(self.db, cap=100, dry_run=True, count_fn=lambda: 0,
                   submit_fn=boom)
        self.assertEqual(s['would-pause-overlap'], 1)
        self.assertNotIn('would-slice', s)
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['cursor'], 0)            # DB untouched
        self.assertEqual(c['state'], 'active')

    # -- self-heal fully-submitted-but-unclosed campaigns (Fix 3) ----------

    def test_self_heal_closes_stuck_complete_campaign(self):
        """cursor == njobs but state still 'active' (crash between
        advance_campaign and set_campaign_state('complete') on a prior
        tick) must self-heal to 'complete', not stay stuck forever."""
        from utils.submissions import top_up
        cid = self._campaign(njobs=6, slice=4)
        self.sl.advance_campaign(self.db, cid, 6)  # fully submitted already
        s = top_up(self.db, cap=100, count_fn=lambda: 0,
                   submit_fn=self._submit())
        self.assertEqual(self.calls, [])            # nothing left to submit
        c = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(c['state'], 'complete')
        self.assertIn('self-heal', c['note'])
        self.assertEqual(s['campaign-complete'], 1)

    def test_self_heal_dry_run_leaves_active(self):
        from utils.submissions import top_up
        cid = self._campaign(njobs=6, slice=4)
        self.sl.advance_campaign(self.db, cid, 6)
        s = top_up(self.db, cap=100, dry_run=True, count_fn=lambda: 0,
                   submit_fn=lambda *a: self.fail('must not submit'))
        self.assertEqual(s['would-campaign-complete'], 1)
        c = self.sl.active_campaigns(self.db)[0]
        self.assertEqual(c['state'], 'active')       # DB untouched


class TestSubmitSlice(unittest.TestCase):
    """submit_slice shells out through the submit_map CLI."""

    def test_argv_and_map_content(self):
        import tempfile
        from utils import submissions as recover
        entry = {'tarball': 'cnf.mu2e.W.C.0.tar', 'njobs': 50,
                 'firstjob': 100, 'inloc': 'tape', 'outputs': [],
                 'memory': '4000MB'}
        camp = {'id': 7, 'cursor': 10, 'slice_size': 5, 'entry': entry,
                'tarball': entry['tarball']}
        captured = {}
        def runner(cmd, **kw):
            captured['cmd'] = cmd
            # Read the map file content during the runner call (before cleanup)
            map_path = cmd[cmd.index('--map') + 1]
            with open(map_path) as f:
                captured['map_content'] = json.load(f)
            return MagicMock(returncode=0)
        ok = recover.submit_slice(camp, 5, '/tmp/led.db', runner=runner)
        self.assertTrue(ok)
        cmd = captured['cmd']
        self.assertEqual(cmd[cmd.index('--first') + 1], '10')
        self.assertEqual(cmd[cmd.index('--num') + 1], '5')
        self.assertEqual(cmd[cmd.index('--ledger-db') + 1], '/tmp/led.db')
        self.assertEqual(captured['map_content'], [entry])  # firstjob PRESERVED

    def test_nonzero_exit_is_failure(self):
        from utils import submissions as recover
        camp = {'id': 1, 'cursor': 0, 'slice_size': 2, 'tarball': 't',
                'entry': {'tarball': 't', 'njobs': 2}}
        ok = recover.submit_slice(
            camp, 2, '/tmp/led.db',
            runner=lambda cmd, **kw: MagicMock(returncode=1))
        self.assertFalse(ok)


class TestScratchDirCleanup(unittest.TestCase):
    """Hourly cron must not accumulate /tmp scratch dirs: the child
    submit_map's map/indices files are removed after it returns,
    success or failure."""

    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        self.camp = {'id': 1, 'cursor': 0, 'slice_size': 5,
                     'tarball': 'cnf.mu2e.S.C.0.tar',
                     'entry': {'tarball': 'cnf.mu2e.S.C.0.tar',
                               'njobs': 10}}
        self.row = {'id': 7, 'tarball': 'cnf.mu2e.S.C.0.tar',
                    'entry': {'tarball': 'cnf.mu2e.S.C.0.tar',
                              'njobs': 10}}

    def _run_and_capture_dir(self, fn, rc):
        from utils import submissions
        import types
        seen = {}
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(*a, **k):
            seen['dir'] = real_mkdtemp(*a, **k)
            return seen['dir']

        runner = lambda cmd, **k: types.SimpleNamespace(returncode=rc)
        with patch.object(submissions.tempfile, 'mkdtemp', spy_mkdtemp):
            fn(runner)
        return seen['dir']

    def test_submit_slice_cleans_up_on_success(self):
        from utils import submissions
        d = self._run_and_capture_dir(
            lambda r: submissions.submit_slice(self.camp, 5, self.db,
                                               runner=r), 0)
        self.assertFalse(os.path.exists(d))

    def test_submit_slice_cleans_up_on_failure(self):
        from utils import submissions
        d = self._run_and_capture_dir(
            lambda r: submissions.submit_slice(self.camp, 5, self.db,
                                               runner=r), 1)
        self.assertFalse(os.path.exists(d))

    def test_resubmit_cleans_up(self):
        from utils import submissions
        d = self._run_and_capture_dir(
            lambda r: submissions.resubmit(self.row, [2, 4], self.db,
                                           runner=r), 0)
        self.assertFalse(os.path.exists(d))


class TestManageCampaign(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'submissions.db')
        self.cid = sl.create_campaign(
            self.db, tarball='cnf.mu2e.M.C.0.tar',
            entry={'tarball': 'cnf.mu2e.M.C.0.tar', 'njobs': 5},
            slice_size=2)

    def test_pause_resume_cancel(self):
        from utils.submissions import manage_campaign
        manage_campaign(self.db, self.cid, 'pause')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'], 'paused')
        manage_campaign(self.db, self.cid, 'resume')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'], 'active')
        manage_campaign(self.db, self.cid, 'cancel')
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'],
                         'cancelled')

    def test_resume_active_raises(self):
        from utils.submissions import manage_campaign
        with self.assertRaises(ValueError):
            manage_campaign(self.db, self.cid, 'resume')


# ---------------------------------------------------------------------------
# submissions CLI verb structure (utils/submissions.py) — workflow hardening
# ---------------------------------------------------------------------------
class TestSubmissionsVerbs(unittest.TestCase):
    """Safe-by-default CLI: bare invocation is read-only status; the
    mutating tick requires the `run` verb; campaign management verbs
    validate transitions and fail with one-line errors."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.dbdir = tempfile.mkdtemp()
        self.db = os.path.join(self.dbdir, 'sub.db')

    def _campaign(self, tarball='cnf.mu2e.V.C.0.tar', njobs=4):
        return self.sl.create_campaign(
            self.db, tarball=tarball,
            entry={'tarball': tarball, 'njobs': njobs},
            slice_size=2, map_path='m.json')

    def test_bare_invocation_is_status(self):
        from utils import submissions
        import io as _io
        self.sl.record_submission(
            self.db, tarball='cnf.mu2e.V.C.0.tar', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        buf = _io.StringIO()
        with patch('sys.stdout', buf), \
             patch.object(submissions, 'process_row',
                          side_effect=AssertionError('bare must not run')), \
             patch.object(submissions, 'top_up',
                          side_effect=AssertionError('bare must not top up')), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db]):
            submissions.main()
        out = buf.getvalue()
        self.assertIn('queue cap in effect', out)
        self.assertIn('cnf.mu2e.V.C.0.tar', out)
        # read-only: no lock file created
        self.assertFalse(
            os.path.exists(os.path.join(self.dbdir, 'submissions.lock')))

    def test_status_verb_same_as_bare(self):
        from utils import submissions
        import io as _io
        buf = _io.StringIO()
        with patch('sys.stdout', buf), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'status']):
            submissions.main()
        self.assertIn('empty', buf.getvalue().lower())

    def test_run_verb_processes_rows_and_locks(self):
        from utils import submissions
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        with patch.object(submissions, 'process_row',
                          return_value='complete') as pr, \
             patch.object(submissions, 'live_clusters', return_value={}), \
             patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            submissions.main()
        self.assertEqual(pr.call_count, 1)
        self.assertTrue(
            os.path.exists(os.path.join(self.dbdir, 'submissions.lock')))

    def test_run_dry_run_takes_no_lock(self):
        from utils import submissions
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run', '--dry-run']):
            submissions.main()
        self.assertFalse(
            os.path.exists(os.path.join(self.dbdir, 'submissions.lock')))

    def test_pause_and_resume_verbs(self):
        from utils import submissions
        cid = self._campaign()
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'pause', str(cid)]):
            submissions.main()
        camp = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(camp['state'], 'paused')
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'resume', str(cid)]):
            submissions.main()
        camp = self.sl.all_campaigns(self.db)[0]
        self.assertEqual(camp['state'], 'active')

    def test_cancel_verb(self):
        from utils import submissions
        cid = self._campaign()
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'cancel', str(cid)]):
            submissions.main()
        self.assertEqual(self.sl.all_campaigns(self.db)[0]['state'],
                         'cancelled')

    def test_invalid_transition_one_line_exit_1(self):
        from utils import submissions
        cid = self._campaign()
        self.sl.set_campaign_state(self.db, cid, 'cancelled')
        with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'resume', str(cid)]):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        msg = str(cm.exception.code)
        self.assertIn('submissions:', msg)
        self.assertNotIn('\n', msg)

    def test_old_style_flags_rejected(self):
        from utils import submissions
        for bad in (['--status'], ['--dry-run'], ['--pause-campaign', '1']):
            with patch.object(sys, 'argv',
                              ['submissions', '--db', self.db] + bad):
                with self.assertRaises(SystemExit) as cm:
                    submissions.main()
            self.assertNotEqual(cm.exception.code, 0)


# ---------------------------------------------------------------------------
# submit_map ledger hook (utils/submit.py) — direct-backend recovery
# ---------------------------------------------------------------------------
class TestSubmitLedgerHook(unittest.TestCase):
    """Direct-backend ledger hook in utils/submit.py."""

    def test_parse_jobsub_id_full_form(self):
        from utils.submit import _parse_jobsub_id
        out = ("Transferring files...\n"
               "1 job(s) submitted to cluster 12345678.\n"
               "Use job id 12345678.0@jobsub03.fnal.gov to retrieve output\n")
        self.assertEqual(_parse_jobsub_id(out),
                         '12345678.0@jobsub03.fnal.gov')

    def test_parse_jobsub_id_absent(self):
        from utils.submit import _parse_jobsub_id
        self.assertIsNone(_parse_jobsub_id("submitted to cluster 12345678\n"))

    def test_run_submit_carries_jobsub_id(self):
        from utils import submit
        fake = MagicMock(
            returncode=0, stderr='',
            stdout='1 job(s) submitted to cluster 12345678.\n'
                   'Use job id 12345678.0@jobsub03.fnal.gov to retrieve output\n')
        with patch('utils.submit.subprocess.run', return_value=fake):
            r = submit._run_submit(['jobsub_submit'], 'cnf.tar', 3)
        self.assertEqual(r['status'], 'submitted')
        self.assertEqual(r['jobsub_id'], '12345678.0@jobsub03.fnal.gov')

    def _opts(self, db, parent=None):
        import argparse
        return argparse.Namespace(ledger_db=db, ledger_parent=parent,
                                  no_ledger=False, map='/tmp/m.json')

    def test_record_in_ledger_absolute_indices(self):
        import tempfile
        from utils import submit, submission_ledger
        db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        entry = {'tarball': 'cnf.mu2e.T.C.0.tar', 'njobs': 3, 'firstjob': 100}
        result = {'tarball': 'cnf.mu2e.T.C.0.tar', 'cluster_id': '1',
                  'jobsub_id': '1.0@js.fnal.gov', 'njobs': 3,
                  'status': 'submitted'}
        submit._record_in_ledger(entry, 100, [0, 1, 2], result, self._opts(db))
        row = submission_ledger.open_rows(db)[0]
        self.assertEqual(row['indices'], [100, 101, 102])
        self.assertEqual(row['entry'], entry)
        self.assertEqual(row['jobsub_id'], '1.0@js.fnal.gov')
        self.assertEqual(row['map_path'], '/tmp/m.json')

    def test_record_in_ledger_parent_chains(self):
        import tempfile
        from utils import submit, submission_ledger
        db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        rid = submission_ledger.record_submission(
            db, tarball='t', entry={}, indices=[0, 1],
            jobsub_id='1.0@js', cluster_id='1')
        result = {'tarball': 't', 'cluster_id': '2', 'jobsub_id': '2.0@js',
                  'njobs': 1, 'status': 'submitted'}
        submit._record_in_ledger({}, 0, [1], result, self._opts(db, parent=rid))
        rows = submission_ledger.open_rows(db)
        self.assertEqual(rows[1]['attempt'], 2)
        self.assertEqual(rows[1]['parent_id'], rid)

    def test_ledger_failure_does_not_raise(self):
        from utils import submit
        result = {'tarball': 't', 'cluster_id': '1', 'jobsub_id': None,
                  'njobs': 1, 'status': 'submitted'}
        # nonexistent directory → sqlite3.OperationalError inside, warning out
        submit._record_in_ledger(
            {}, 0, [0], result,
            self._opts('/nonexistent-dir-recovery-test/s.db'))  # must not raise


# ---------------------------------------------------------------------------
# 13. mkrecovery scoped index scan (utils/mkrecovery.py)
# ---------------------------------------------------------------------------

class TestBuildFileMapsScoped(unittest.TestCase):
    def test_scoped_scan_matches_windowed_scan(self):
        from utils.jobquery import Mu2eJobPars
        from utils.mkrecovery import build_file_maps
        files = [f"sim.mu2e.In.C.00000000_{i:08d}.art" for i in range(6)]
        tar = _make_tarball(_root_input_jobpars(files))
        try:
            jp = Mu2eJobPars(tar)
            ds = 'sim.mu2e.TestDesc.TestConf.art'
            full = build_file_maps(jp, [ds], njobs=6)[ds]
            self.assertEqual(len(full), 6)
            scoped = build_file_maps(jp, [ds], njobs=0, indices=[1, 4])[ds]
            expect = {f: i for f, i in full.items() if i in (1, 4)}
            self.assertEqual(scoped, expect)
            self.assertEqual(sorted(set(scoped.values())), [1, 4])
        finally:
            os.unlink(tar)


class TestRecoverLoop(unittest.TestCase):
    """utils/submissions.py — drain gate, verify, cap semantics."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        self.entry = {'tarball': 'cnf.mu2e.T.C.0.tar', 'njobs': 3}
        self.rid = sl.record_submission(
            self.db, tarball='cnf.mu2e.T.C.0.tar', entry=self.entry,
            indices=[0, 1, 2], jobsub_id='1.0@js.fnal.gov', cluster_id='1')
        self.row = sl.open_rows(self.db)[0]

    def _process(self, qstate='drained', missing=(), partial=(),
                 resub_ok=True, max_attempts=3, dry_run=False,
                 verify_exc=None, resub_writes_child=True):
        from utils import submissions as recover
        calls = {}

        def fake_verify(row):
            if verify_exc:
                raise verify_exc
            return list(missing), list(partial)

        def fake_resubmit(row, miss, db_path):
            calls['resubmit'] = (row['id'], list(miss), db_path)
            if resub_ok and resub_writes_child:
                self.sl.record_submission(
                    db_path, tarball=row['tarball'], entry=row['entry'],
                    indices=list(miss), jobsub_id='2.0@js.fnal.gov',
                    cluster_id='2', parent_id=row['id'])
            return resub_ok

        action = recover.process_row(
            self.row, self.db, max_attempts, clusters={}, dry_run=dry_run,
            queue_state_fn=lambda cid, clusters: qstate,
            verify_fn=fake_verify, resubmit_fn=fake_resubmit)
        return action, calls

    def test_running_skips(self):
        action, calls = self._process(qstate='running')
        self.assertEqual(action, 'running')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_held_reports_and_skips(self):
        action, calls = self._process(qstate='held')
        self.assertEqual(action, 'held')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_queue_error_skips(self):
        action, _ = self._process(qstate='error')
        self.assertEqual(action, 'queue-error')
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_complete_closes_row(self):
        action, _ = self._process(missing=())
        self.assertEqual(action, 'complete')
        self.assertEqual(self.sl.all_rows(self.db)[0]['state'], 'complete')

    def test_missing_resubmits_and_marks_recovered(self):
        action, calls = self._process(missing=(1,))
        self.assertEqual(action, 'resubmitted')
        self.assertEqual(calls['resubmit'], (self.rid, [1], self.db))
        rows = self.sl.all_rows(self.db)
        self.assertEqual(rows[0]['state'], 'recovered')
        self.assertEqual(rows[1]['state'], 'active')
        self.assertEqual(rows[1]['attempt'], 2)
        self.assertEqual(rows[1]['indices'], [1])

    def test_cap_exhausts_without_resubmit(self):
        action, calls = self._process(missing=(1,), max_attempts=1)
        self.assertEqual(action, 'exhausted')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.all_rows(self.db)[0]['state'], 'exhausted')

    def test_dry_run_never_submits(self):
        action, calls = self._process(missing=(1,), dry_run=True)
        self.assertEqual(action, 'would-resubmit')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_dry_run_complete_keeps_row_active(self):
        action, calls = self._process(missing=(), dry_run=True)
        self.assertEqual(action, 'would-complete')
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_dry_run_at_cap_keeps_row_active(self):
        action, calls = self._process(missing=(1,), max_attempts=1,
                                      dry_run=True)
        self.assertEqual(action, 'would-exhaust')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_verify_error_keeps_row_active(self):
        action, _ = self._process(verify_exc=RuntimeError('no tarball'))
        self.assertEqual(action, 'verify-error')
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_resubmit_failure_keeps_row_active(self):
        action, _ = self._process(missing=(1,), resub_ok=False)
        self.assertEqual(action, 'resubmit-error')
        self.assertEqual(self.sl.open_rows(self.db)[0]['state'], 'active')

    def test_crash_window_child_already_active_repairs(self):
        child = self.sl.record_submission(
            self.db, tarball='cnf.mu2e.T.C.0.tar', entry=self.entry,
            indices=[1], jobsub_id='2.0@js', cluster_id='2',
            parent_id=self.rid)
        action, calls = self._process(missing=(1,))
        self.assertEqual(action, 'child-active')
        self.assertNotIn('resubmit', calls)
        rows = {r['id']: r for r in self.sl.all_rows(self.db)}
        self.assertEqual(rows[self.rid]['state'], 'recovered')
        self.assertEqual(rows[child]['state'], 'active')

    def test_crash_window_dry_run_previews_repair(self):
        child = self.sl.record_submission(
            self.db, tarball='cnf.mu2e.T.C.0.tar', entry=self.entry,
            indices=[1], jobsub_id='2.0@js', cluster_id='2',
            parent_id=self.rid)
        action, calls = self._process(missing=(1,), dry_run=True)
        self.assertEqual(action, 'would-recover')
        self.assertNotIn('resubmit', calls)
        self.assertEqual(sorted(r['id'] for r in self.sl.open_rows(self.db)),
                         [self.rid, child])

    def test_child_active_wins_over_cap(self):
        self.sl.record_submission(
            self.db, tarball='cnf.mu2e.T.C.0.tar', entry=self.entry,
            indices=[1], jobsub_id='2.0@js', cluster_id='2',
            parent_id=self.rid)
        action, _ = self._process(missing=(1,), max_attempts=1)
        self.assertEqual(action, 'child-active')

    def test_resubmit_without_child_row_flags_unwatched(self):
        action, calls = self._process(missing=(1,), resub_writes_child=False)
        self.assertEqual(action, 'child-missing')
        rows = self.sl.all_rows(self.db)
        self.assertEqual(rows[0]['state'], 'recovered')
        self.assertIn('unwatched', rows[0]['note'])

    def test_missing_cluster_id_reported(self):
        rid2 = self.sl.record_submission(
            self.db, tarball='t2', entry={}, indices=[0],
            jobsub_id=None, cluster_id=None)
        row2 = [r for r in self.sl.open_rows(self.db) if r['id'] == rid2][0]
        from utils import submissions as recover
        action = recover.process_row(
            row2, self.db, 3, clusters={'9': ['R']},
            queue_state_fn=lambda cid, cl: self.fail('must not be called'),
            verify_fn=lambda r: ([], []),
            resubmit_fn=lambda r, m, d: self.fail('must not be called'))
        self.assertEqual(action, 'queue-error')

    def test_cluster_queue_state_logic(self):
        from utils.submissions import cluster_queue_state as cqs
        # None snapshot (query untrusted) → error, never drained (fail-closed)
        self.assertEqual(cqs('9', None), 'error')
        # cluster absent from a good snapshot → drained
        self.assertEqual(cqs('9', {}), 'drained')
        self.assertEqual(cqs('9', {'8': ['R']}), 'drained')
        # any idle/running job → running
        self.assertEqual(cqs('9', {'9': ['R']}), 'running')
        self.assertEqual(cqs('9', {'9': ['I']}), 'running')
        # running wins over a stray held job (don't halt a working cluster)
        self.assertEqual(cqs('9', {'9': ['R', 'H']}), 'running')
        # every non-terminal job held → held (all preempted, human decides)
        self.assertEqual(cqs('9', {'9': ['H', 'H']}), 'held')
        # only terminal rows lingering (completed/removed) → drained
        self.assertEqual(cqs('9', {'9': ['C', 'X']}), 'drained')
        self.assertEqual(cqs('9', {'9': ['H', 'C']}), 'held')
        # cluster_id coerced to str (ledger may hand an int)
        self.assertEqual(cqs(9, {'9': ['R']}), 'running')

    def test_live_clusters_parsing(self):
        from utils import submissions as recover
        # Same module — a `from test.test_unit import ...` package-path import
        # resolves only when the suite is run as `python -m unittest
        # test.test_unit`, and blows up under `unittest discover -s test`.
        T = TestRecoverCap
        def r(stdout, rc=0):
            return MagicMock(returncode=rc, stdout=stdout, stderr='')
        hdr, summ, row = T._HDR, T._SUM, T._row
        # a good table → {cluster: [states]}, procs of one cluster aggregate,
        # and the probe queries the --user table (not per-jobid)
        cmd = {}
        def run(c, capture_output=True, text=True):
            cmd['argv'] = c
            return r('\n'.join([
                hdr, summ,
                row('9.0@jobsub01.fnal.gov', 'mu2epro', 'R'),
                row('9.1@jobsub01.fnal.gov', 'mu2epro', 'I'),
                row('12.0@jobsub02.fnal.gov', '|-WORKER_3', 'H'),
            ]) + '\n')
        self.assertEqual(recover.live_clusters(runner=run),
                         {'9': ['R', 'I'], '12': ['H']})
        self.assertEqual(cmd['argv'], ['jobsub_q', '--user', 'mu2epro'])
        # empty-but-valid table → {} (a real drained account), NOT None
        self.assertEqual(
            recover.live_clusters(runner=lambda *a, **k: r(hdr + '\n' + summ)),
            {})
        # command failure / headerless / garbage row → None (fail-closed)
        self.assertIsNone(recover.live_clusters(
            runner=lambda *a, **k: r('', rc=1)))
        self.assertIsNone(recover.live_clusters(
            runner=lambda *a, **k: r('No jobs found\n')))
        self.assertIsNone(recover.live_clusters(runner=lambda *a, **k: r(
            '\n'.join([hdr, row('9.0@jobsub01.fnal.gov', 'mu2epro', 'Z')]))))

    def test_verify_row_missing_and_partial(self):
        from utils import submissions as recover
        files = [f"sim.mu2e.In.C.00000000_{i:08d}.art" for i in range(3)]
        jpars = _root_input_jobpars(files)
        jpars['tbs']['outfiles']['outputs.SecondOutput.fileName'] = \
            "dig.mu2e.TestDesc.TestConf.sequencer.art"
        tar = _make_tarball(jpars)
        try:
            row = {'id': 1, 'tarball': 'cnf.mu2e.T.C.0.tar',
                   'indices': [0, 1, 2], 'entry': {}, 'attempt': 1,
                   'jobsub_id': 'x'}
            dig_ds = 'dig.mu2e.TestDesc.TestConf.art'
            from utils.jobquery import Mu2eJobPars
            jp = Mu2eJobPars(tar)

            def fake_lister(ds):
                out = []
                for i in (0, 1, 2):
                    for f in jp.job_outputs(i).values():
                        if str(Mu2eName.parse(f).dataset) != ds:
                            continue
                        if i == 2:
                            continue          # idx 2: nothing landed
                        if i == 1 and ds == dig_ds:
                            continue          # idx 1: dig stream missing
                        out.append(f)
                return out

            with patch.object(recover, 'locate_tarball', return_value=tar):
                missing, partial = recover.verify_row(
                    row, sam_lister=fake_lister)
            self.assertEqual(missing, [1, 2])
            self.assertEqual(partial, [1])
        finally:
            os.unlink(tar)

    def test_verify_row_unlocatable_tarball_raises(self):
        from utils import submissions as recover
        row = {'id': 1, 'tarball': 'cnf.mu2e.gone.C.0.tar',
               'indices': [0], 'entry': {}, 'attempt': 1, 'jobsub_id': 'x'}
        with patch.object(recover, 'locate_tarball', return_value=None):
            with self.assertRaises(RuntimeError):
                recover.verify_row(row, sam_lister=lambda ds: [])

    def test_verify_row_nonart_outputs_raise_not_complete(self):
        from utils import submissions as recover
        files = [f"sim.mu2e.In.C.00000000_{i:08d}.art" for i in range(2)]
        jpars = _root_input_jobpars(files)
        jpars['tbs']['outfiles']['outputs.PrimaryOutput.fileName'] = \
            "nts.mu2e.TestDesc.TestConf.sequencer.root"
        tar = _make_tarball(jpars)
        try:
            row = {'id': 1, 'tarball': 'cnf.mu2e.T.C.0.tar',
                   'indices': [0, 1], 'entry': {}, 'attempt': 1,
                   'jobsub_id': 'x'}
            with patch.object(recover, 'locate_tarball', return_value=tar):
                with self.assertRaises(RuntimeError):
                    recover.verify_row(row, sam_lister=lambda ds: [])
        finally:
            os.unlink(tar)

    def test_resubmit_drops_firstjob_and_writes_indices(self):
        from utils import submissions as recover
        row = {'id': 7, 'tarball': 'cnf.mu2e.T.C.0.tar',
               'entry': {'tarball': 'cnf.mu2e.T.C.0.tar', 'njobs': 5,
                         'firstjob': 100, 'inloc': 'tape'},
               'indices': [100, 102], 'attempt': 1, 'jobsub_id': '1.0@js'}
        captured = {}

        def fake_runner(cmd, **kwargs):
            captured['cmd'] = cmd
            # Read file contents during the runner call (before cleanup)
            map_path = cmd[cmd.index('--map') + 1]
            captured['map_entry'] = json.loads(Path(map_path).read_text())[0]
            idx_path = cmd[cmd.index('--indices-file') + 1]
            captured['idx_lines'] = Path(idx_path).read_text().splitlines()
            return MagicMock(returncode=0)

        ok = recover.resubmit(row, [100, 102], '/tmp/led.db',
                              runner=fake_runner)
        self.assertTrue(ok)
        cmd = captured['cmd']
        self.assertEqual(cmd[cmd.index('--ledger-parent') + 1], '7')
        self.assertEqual(cmd[cmd.index('--ledger-db') + 1], '/tmp/led.db')
        entry = captured['map_entry']
        self.assertNotIn('firstjob', entry)
        self.assertEqual(entry['njobs'], 5)
        lines = captured['idx_lines']
        self.assertEqual(lines[0], '# cnf.mu2e.T.C.0.tar')
        self.assertEqual(lines[1:], ['100', '102'])
        recover.resubmit(row, [100], '/tmp/led.db', dry_run=True,
                         runner=fake_runner)
        self.assertIn('--dry-run', captured['cmd'])

class TestRecoverCLI(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')

    def test_print_status_empty(self):
        from utils import submissions as recover
        import io as _io
        buf = _io.StringIO()
        with patch('sys.stdout', buf):
            recover.print_status(self.db)
        self.assertIn('empty', buf.getvalue().lower())

    def test_print_status_lists_rows(self):
        from utils import submissions as recover
        import io as _io
        rid = self.sl.record_submission(
            self.db, tarball='cnf.mu2e.T.C.0.tar', entry={}, indices=[0, 1],
            jobsub_id='1.0@js', cluster_id='1')
        self.sl.close_row(self.db, rid, 'complete')
        self.sl.record_submission(
            self.db, tarball='cnf.mu2e.T2.C.0.tar', entry={}, indices=[3],
            jobsub_id='2.0@js', cluster_id='2')
        buf = _io.StringIO()
        with patch('sys.stdout', buf):
            recover.print_status(self.db)
        out = buf.getvalue()
        self.assertIn('complete', out)
        self.assertIn('active', out)
        self.assertIn('cnf.mu2e.T2.C.0.tar', out)

    def test_main_exit_2_on_attention(self):
        from utils import submissions as recover
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        with patch.object(recover, 'process_row', return_value='held'), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            with self.assertRaises(SystemExit) as cm:
                recover.main()
        self.assertEqual(cm.exception.code, 2)

    def test_main_exit_2_on_dry_run_would_exhaust(self):
        from utils import submissions as recover
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        with patch.object(recover, 'process_row',
                          return_value='would-exhaust'), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run', '--dry-run']):
            with self.assertRaises(SystemExit) as cm:
                recover.main()
        self.assertEqual(cm.exception.code, 2)

    def test_main_exit_0_when_clean(self):
        from utils import submissions as recover
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        with patch.object(recover, 'process_row', return_value='complete'), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            recover.main()  # returns without SystemExit

    def test_main_lock_contention_exits(self):
        import fcntl
        from utils import submissions as recover
        self.sl.record_submission(
            self.db, tarball='t', entry={}, indices=[0],
            jobsub_id='1.0@js', cluster_id='1')
        lock_path = os.path.join(os.path.dirname(self.db), 'submissions.lock')
        fh = open(lock_path, 'w')
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                            'run']):
                with self.assertRaises(SystemExit) as cm:
                    recover.main()
            self.assertIn('submissions.lock', str(cm.exception.code))
        finally:
            fh.close()


class TestSubmissionsExitHonesty(unittest.TestCase):
    """A stalled loop must not impersonate a healthy one: queue-count
    failure and lingering paused campaigns exit 2 every tick."""

    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')

    def test_count_error_exits_2(self):
        from utils import submissions
        with patch.object(submissions, 'top_up',
                          return_value={'count-error': 1}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        self.assertEqual(cm.exception.code, 2)

    def test_lingering_paused_campaign_exits_2(self):
        from utils import submissions
        cid = self.sl.create_campaign(
            self.db, tarball='cnf.mu2e.P.C.0.tar',
            entry={'tarball': 'cnf.mu2e.P.C.0.tar', 'njobs': 4},
            slice_size=2)
        self.sl.set_campaign_state(self.db, cid, 'paused',
                                   note='paused on a PREVIOUS tick')
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        self.assertEqual(cm.exception.code, 2)

    def test_lingering_paused_exits_2_under_dry_run(self):
        from utils import submissions
        cid = self.sl.create_campaign(
            self.db, tarball='cnf.mu2e.P2.C.0.tar',
            entry={'tarball': 'cnf.mu2e.P2.C.0.tar', 'njobs': 4},
            slice_size=2)
        self.sl.set_campaign_state(self.db, cid, 'paused')
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run', '--dry-run']):
            with self.assertRaises(SystemExit) as cm:
                submissions.main()
        self.assertEqual(cm.exception.code, 2)

    def test_clean_run_still_exits_0(self):
        from utils import submissions
        with patch.object(submissions, 'top_up', return_value={}), \
             patch.object(sys, 'argv', ['submissions', '--db', self.db,
                                        'run']):
            submissions.main()  # no SystemExit


class TestPauseNotePreservation(unittest.TestCase):
    def setUp(self):
        import tempfile
        from utils import submission_ledger as sl
        self.sl = sl
        self.db = os.path.join(tempfile.mkdtemp(), 'sub.db')
        self.cid = sl.create_campaign(
            self.db, tarball='cnf.mu2e.N.C.0.tar',
            entry={'tarball': 'cnf.mu2e.N.C.0.tar', 'njobs': 4},
            slice_size=2)

    def _note(self):
        return self.sl.all_campaigns(self.db)[0]['note']

    def test_resume_preserves_pause_note(self):
        self.sl.set_campaign_state(self.db, self.cid, 'paused',
                                   note='crash-window suspected')
        self.sl.set_campaign_state(self.db, self.cid, 'active')
        self.assertEqual(self._note(), 'crash-window suspected')

    def test_resume_clears_closed_utc(self):
        self.sl.set_campaign_state(self.db, self.cid, 'paused', note='x')
        self.sl.set_campaign_state(self.db, self.cid, 'active')
        self.assertIsNone(self.sl.all_campaigns(self.db)[0]['closed_utc'])

    def test_pause_verb_custom_note(self):
        from utils import submissions
        with patch.object(sys, 'argv',
                          ['submissions', '--db', self.db, 'pause',
                           str(self.cid), '--note', 'draining for O2']):
            submissions.main()
        self.assertEqual(self._note(), 'draining for O2')

    def test_pause_verb_default_note(self):
        from utils import submissions
        with patch.object(sys, 'argv',
                          ['submissions', '--db', self.db, 'pause',
                           str(self.cid)]):
            submissions.main()
        self.assertEqual(self._note(), 'operator pause')


class TestSplitInputs(unittest.TestCase):
    """split_inputs reads the frozen input file lists from a cnf tarball,
    splitting primary (tbs.inputs) from pileup (tbs.auxin), grouped by
    dataset and deduplicated — no per-index reconstruction."""

    def _tar(self, inputs=None, auxin=None, samplinginput=None):
        jp = {
            "code": "", "setup": "/cvmfs/x/setup.sh",
            "tbs": {"seed": "services.SeedService.baseSeed"},
            "jobname": "cnf.mu2e.TestDesc.TestConf.0.tar",
            "owner": "mu2e", "dsconf": "TestConf",
        }
        if inputs is not None:
            jp["tbs"]["inputs"] = inputs
        if auxin is not None:
            jp["tbs"]["auxin"] = auxin
        if samplinginput is not None:
            jp["tbs"]["samplinginput"] = samplinginput
        return _make_tarball(jp)

    def test_splits_primary_and_pileup_by_dataset(self):
        from utils.check_inputs import split_inputs
        tar = self._tar(
            inputs={"source.fileNames": [1, [
                "dts.mu2e.Prim.CampA.001430_00000000.art",
                "dts.mu2e.Prim.CampA.001430_00000001.art"]]},
            auxin={"physics.filters.M.fileNames": [1, [
                "dts.mu2e.Pile.CampB.001430_00000005.art"]]},
        )
        primary, auxin = split_inputs(tar)
        os.unlink(tar)
        self.assertEqual(set(primary), {"dts.mu2e.Prim.CampA.art"})
        self.assertEqual(len(primary["dts.mu2e.Prim.CampA.art"]), 2)
        self.assertEqual(set(auxin), {"dts.mu2e.Pile.CampB.art"})

    def test_dedups_repeated_files(self):
        from utils.check_inputs import split_inputs
        f = "dts.mu2e.Pile.CampB.001430_00000005.art"
        tar = self._tar(auxin={
            "physics.filters.A.fileNames": [1, [f]],
            "physics.filters.B.fileNames": [1, [f]],
        })
        _, auxin = split_inputs(tar)
        os.unlink(tar)
        self.assertEqual(auxin["dts.mu2e.Pile.CampB.art"], [f])

    def test_missing_sections_yield_empty(self):
        from utils.check_inputs import split_inputs
        tar = self._tar()
        primary, auxin = split_inputs(tar)
        os.unlink(tar)
        self.assertEqual(primary, {})
        self.assertEqual(auxin, {})

    def test_problem_is_frozen(self):
        from utils.check_inputs import Problem
        p = Problem("ds", "f.art", "truncated", "detail")
        self.assertEqual(p.kind, "truncated")
        with self.assertRaises(Exception):
            p.kind = "missing"

    def test_samplinginput_folded_into_primary(self):
        from utils.check_inputs import split_inputs
        tar = self._tar(
            samplinginput={"physics.filters.resampler.fileNames": [1, [
                "dts.mu2e.NeutralsCat.MDC2025ab.001430_00000007.art"]]})
        primary, auxin = split_inputs(tar)
        os.unlink(tar)
        self.assertEqual(set(primary), {"dts.mu2e.NeutralsCat.MDC2025ab.art"})
        self.assertEqual(auxin, {})


class TestCheckResilient(unittest.TestCase):
    """Resilient pileup: present AND size matches SAM. Catches the
    2026-07-21 truncation (1 MiB stub) and a purge (missing entirely).
    mdh cannot see resilient, so this is a direct os.path.getsize vs the
    SAM-recorded size."""

    DS = "dts.mu2e.Pile.CampB.art"
    F1 = "dts.mu2e.Pile.CampB.001430_00000000.art"
    F2 = "dts.mu2e.Pile.CampB.001430_00000001.art"

    def test_all_present_and_sized_ok(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1, self.F2],
            sam_sizes=lambda ds: {self.F1: 100, self.F2: 200},
            disk_size=lambda p: 100 if self.F1 in p else 200)
        self.assertEqual(probs, [])

    def test_truncated_file_flagged(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1],
            sam_sizes=lambda ds: {self.F1: 113643009},
            disk_size=lambda p: 1048576)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "truncated")
        self.assertEqual(probs[0].filename, self.F1)

    def test_missing_file_flagged(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1],
            sam_sizes=lambda ds: {self.F1: 100},
            disk_size=lambda p: None)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "missing")

    def test_no_sam_size_is_query_error(self):
        from utils.check_inputs import check_resilient
        probs = check_resilient(
            self.DS, [self.F1],
            sam_sizes=lambda ds: {},
            disk_size=lambda p: 100)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "query_error")

    def test_sam_lookup_raises_is_query_error(self):
        from utils.check_inputs import check_resilient
        def boom(ds):
            raise RuntimeError("SAM down")
        probs = check_resilient(
            self.DS, [self.F1, self.F2],
            sam_sizes=boom,
            disk_size=lambda p: 100)
        self.assertEqual([p.kind for p in probs], ["query_error", "query_error"])
        self.assertEqual(len(probs), 2)

    def test_default_disk_size_absent_is_none(self):
        from utils.check_inputs import _default_disk_size
        self.assertIsNone(_default_disk_size("/pnfs/mu2e/resilient/nope/x.art"))


class TestFileSizesInDataset(unittest.TestCase):
    """file_sizes_in_dataset returns {filename: size} from one
    list-files --fileinfo call."""

    def test_maps_name_to_size(self):
        import collections
        from utils import samweb_wrapper
        FI = collections.namedtuple("fileinfo",
                                    "file_name file_id file_size event_count")
        fake_client = MagicMock()
        fake_client.listFiles.return_value = [
            FI("dts.mu2e.Pile.CampB.001430_00000000.art", 1, 111, 9),
            FI("dts.mu2e.Pile.CampB.001430_00000001.art", 2, 222, 9),
        ]
        wrapper = object.__new__(samweb_wrapper.SAMWebWrapper)
        wrapper.client = fake_client
        with patch.object(samweb_wrapper, "get_samweb_wrapper",
                          return_value=wrapper):
            out = samweb_wrapper.file_sizes_in_dataset("dts.mu2e.Pile.CampB.art")
        self.assertEqual(out, {
            "dts.mu2e.Pile.CampB.001430_00000000.art": 111,
            "dts.mu2e.Pile.CampB.001430_00000001.art": 222})
        # one query, fileinfo requested
        _, kwargs = fake_client.listFiles.call_args
        self.assertTrue(kwargs.get("fileinfo"))


class TestCheckTape(unittest.TestCase):
    """Primary / tape inputs: NEARLINE (evicted) must block with a
    /prestage hint; ONLINE passes; unknown storage or query failure fails
    closed."""

    DS = "dts.mu2e.Prim.CampA.art"
    F1 = "dts.mu2e.Prim.CampA.001430_00000000.art"
    F2 = "dts.mu2e.Prim.CampA.001430_00000001.art"

    def test_online_passes(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1, self.F2],
            locality=lambda loc, fs: {self.F1: "ONLINE",
                                      self.F2: "ONLINE_AND_NEARLINE"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(probs, [])

    def test_nearline_blocks_with_prestage_hint(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "NEARLINE"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "nearline")
        self.assertIn("/prestage", probs[0].detail)

    def test_disk_dataset_queries_disk_location(self):
        from utils.check_inputs import check_tape
        seen = {}
        def loc(mdh_loc, fs):
            seen["loc"] = mdh_loc
            return {self.F1: "ONLINE"}
        probs = check_tape(self.DS, [self.F1], locality=loc,
                           dataset_location=lambda ds: "dcache")
        self.assertEqual(probs, [])
        self.assertEqual(seen["loc"], "disk")

    def test_enstore_dataset_queries_tape_location(self):
        from utils.check_inputs import check_tape
        seen = {}
        def loc(mdh_loc, fs):
            seen["loc"] = mdh_loc
            return {self.F1: "ONLINE"}
        check_tape(self.DS, [self.F1], locality=loc,
                   dataset_location=lambda ds: "enstore")
        self.assertEqual(seen["loc"], "tape")

    def test_missing_reported(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "MISSING"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(probs[0].kind, "missing")

    def test_unknown_storage_location_fails_closed(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "ONLINE"},
            dataset_location=lambda ds: "N/A")
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "query_error")

    def test_locality_error_fails_closed(self):
        from utils.check_inputs import check_tape
        probs = check_tape(
            self.DS, [self.F1],
            locality=lambda loc, fs: {self.F1: "ERROR"},
            dataset_location=lambda ds: "enstore")
        self.assertEqual(probs[0].kind, "query_error")


class TestDefaultLocalityParsing(unittest.TestCase):
    """_default_locality queries mdh per file via the Python API.

    The `mdh query-dcache` CLI aborts at the FIRST file absent from the
    queried area, so one persistent-resident file in a tape dataset
    truncated stdout and forced a fail-closed ERROR for all 5000 files of
    dts.mu2e.RPCInternalPhysical.MDC2025ap — 4997 of which were in fact
    ONLINE_AND_NEARLINE. Per-file lookups cannot have that failure mode.
    """

    F1 = "dts.mu2e.Prim.CampA.001430_00000000.art"
    F2 = "dts.mu2e.Prim.CampA.001430_00000001.art"

    @staticmethod
    def _client(by_loc, fail_times=0):
        """Fake MdhClient. `by_loc` maps location -> {filename: locality};
        a 404 raises RuntimeError as the real client does."""
        state = {'left': fail_times}

        class FakeClient:
            def query_dcache(self, filename, location="tape"):
                if state['left'] > 0:
                    state['left'] -= 1
                    raise ConnectionError("transient")
                loc = by_loc.get(location, {})
                if filename not in loc:
                    raise RuntimeError(f"File not found in dCache: /pnfs/{location}/{filename}")
                return {'fileLocality': loc[filename]}
        return FakeClient

    def _run(self, by_loc, files=None, fail_times=0):
        from utils import check_inputs
        fake = types.ModuleType("mdh")
        fake.MdhClient = self._client(by_loc, fail_times)
        with patch.dict(sys.modules, {"mdh": fake}):
            return check_inputs._default_locality("tape", files or [self.F1, self.F2])

    def test_all_found_on_tape(self):
        out = self._run({"tape": {self.F1: "ONLINE_AND_NEARLINE", self.F2: "NEARLINE"}})
        self.assertEqual(out, {self.F1: "ONLINE_AND_NEARLINE", self.F2: "NEARLINE"})

    def test_disk_resident_file_is_online_not_missing(self):
        """A file on persistent/disk has no tape copy — nothing to stage."""
        out = self._run({"tape": {self.F1: "ONLINE_AND_NEARLINE"},
                         "disk": {self.F2: "ONLINE"}})
        self.assertEqual(out, {self.F1: "ONLINE_AND_NEARLINE", self.F2: "ONLINE"})

    def test_absent_everywhere_is_missing(self):
        out = self._run({"tape": {self.F1: "ONLINE"}})
        self.assertEqual(out, {self.F1: "ONLINE", self.F2: "MISSING"})

    def test_one_offlocation_file_does_not_poison_the_rest(self):
        """The regression: a split dataset must not fail every file."""
        files = [f"dts.mu2e.Prim.CampA.001430_{i:08d}.art" for i in range(50)]
        tape = {f: "ONLINE_AND_NEARLINE" for f in files if f != files[7]}
        out = self._run({"tape": tape, "disk": {files[7]: "ONLINE"}}, files=files)
        self.assertEqual(out[files[7]], "ONLINE")
        self.assertTrue(all(out[f] == "ONLINE_AND_NEARLINE"
                            for f in files if f != files[7]))

    def test_transient_transport_error_is_retried(self):
        """A concurrency blip must not block a campaign."""
        out = self._run({"tape": {self.F1: "ONLINE_AND_NEARLINE"}},
                        files=[self.F1], fail_times=2)
        self.assertEqual(out, {self.F1: "ONLINE_AND_NEARLINE"})

    def test_persistent_transport_error_fails_closed(self):
        out = self._run({"tape": {self.F1: "ONLINE_AND_NEARLINE"}},
                        files=[self.F1], fail_times=99)
        self.assertEqual(out, {self.F1: "ERROR"})

    def test_unimportable_mdh_fails_closed(self):
        from utils import check_inputs
        with patch.dict(sys.modules, {"mdh": None}):
            out = check_inputs._default_locality("tape", [self.F1])
        self.assertEqual(out, {self.F1: "ERROR"})


class TestCheckInputs(unittest.TestCase):
    """check_inputs assembles split_inputs + the two checks with the
    inloc routing, returning (ok, problems)."""

    def _tar(self, inputs=None, auxin=None, samplinginput=None):
        jp = {"code": "", "setup": "/cvmfs/x/setup.sh",
              "tbs": {"seed": "s"}, "jobname": "cnf.mu2e.T.C.0.tar",
              "owner": "mu2e", "dsconf": "C"}
        if inputs is not None:
            jp["tbs"]["inputs"] = inputs
        if auxin is not None:
            jp["tbs"]["auxin"] = auxin
        if samplinginput is not None:
            jp["tbs"]["samplinginput"] = samplinginput
        return _make_tarball(jp)

    PRIM = "dts.mu2e.Prim.CampA.001430_00000000.art"
    PILE = "dts.mu2e.Pile.CampB.001430_00000005.art"

    def _tar_both(self):
        return self._tar(
            inputs={"source.fileNames": [1, [self.PRIM]]},
            auxin={"physics.filters.M.fileNames": [1, [self.PILE]]})

    def test_all_clean(self):
        from utils.check_inputs import check_inputs
        tar = self._tar_both()
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: 100,
            locality=lambda loc, fs: {f: "ONLINE" for f in fs},
            dataset_location=lambda ds: "dcache")
        os.unlink(tar)
        self.assertTrue(ok)
        self.assertEqual(probs, [])

    def test_resilient_pileup_checked_by_size_not_mdh(self):
        from utils.check_inputs import check_inputs
        tar = self._tar_both()
        called = {"mdh": []}
        def loc(mdh_loc, fs):
            called["mdh"].extend(fs)
            return {f: "ONLINE" for f in fs}
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: 1048576,      # truncated pileup
            locality=loc, dataset_location=lambda ds: "dcache")
        os.unlink(tar)
        self.assertFalse(ok)
        self.assertEqual([p.kind for p in probs], ["truncated"])
        # pileup went through the resilient size path, never mdh
        self.assertNotIn(self.PILE, called["mdh"])

    def test_nearline_primary_blocks(self):
        from utils.check_inputs import check_inputs
        tar = self._tar_both()
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: 100,
            locality=lambda loc, fs: {f: "NEARLINE" for f in fs},
            dataset_location=lambda ds: "enstore")
        os.unlink(tar)
        self.assertFalse(ok)
        self.assertEqual([p.kind for p in probs], ["nearline"])

    def test_missing_resilient_not_reclassified_as_tape(self):
        # The flagged subtlety: a pileup file absent from resilient must
        # be reported 'missing', NOT quietly checked as a tape input.
        from utils.check_inputs import check_inputs
        tar = self._tar(auxin={"physics.filters.M.fileNames":
                               [1, [self.PILE]]})
        def loc(mdh_loc, fs):
            raise AssertionError("pileup must not reach the tape path")
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {self.PILE: 100},
            disk_size=lambda p: None,          # purged from resilient
            locality=loc, dataset_location=lambda ds: "enstore")
        os.unlink(tar)
        self.assertFalse(ok)
        self.assertEqual([p.kind for p in probs], ["missing"])

    def test_non_resilient_inloc_routes_pileup_to_tape(self):
        from utils.check_inputs import check_inputs
        tar = self._tar(auxin={"physics.filters.M.fileNames":
                               [1, [self.PILE]]})
        ok, probs = check_inputs(
            tar, "tape",
            sam_sizes=lambda ds: {},
            disk_size=lambda p: None,
            locality=lambda loc, fs: {f: "ONLINE" for f in fs},
            dataset_location=lambda ds: "enstore")
        os.unlink(tar)
        self.assertTrue(ok)

    def test_samplinginput_nearline_blocks(self):
        from utils.check_inputs import check_inputs
        SAMP = "dts.mu2e.NeutralsCat.MDC2025ab.001430_00000007.art"
        tar = self._tar(samplinginput={"physics.filters.r.fileNames": [1, [SAMP]]})
        ok, probs = check_inputs(
            tar, "resilient",
            sam_sizes=lambda ds: {},
            disk_size=lambda p: None,
            locality=lambda loc, fs: {f: "NEARLINE" for f in fs},
            dataset_location=lambda ds: "enstore")
        os.unlink(tar)
        self.assertFalse(ok)
        self.assertEqual([p.kind for p in probs], ["nearline"])


class TestCheckInputsCLI(unittest.TestCase):
    """format_report + main: grouped report, exit 0 clean / 2 on problems."""

    def test_script_mode_help_runs_standalone(self):
        """bin/check_inputs execs `python3 utils/check_inputs.py`; running
        as a script (not `-m`) must resolve `import utils.*`, and the module
        must load without the Mu2e environment so `--help` works. A fresh
        subprocess has neither the repo root on sys.path nor the test's
        samweb_client stub, so it reproduces the real invocation. Regression:
        the module shipped without the sys.path insert AND with a top-level
        samweb_wrapper import, so `bin/check_inputs` died on ModuleNotFound."""
        import subprocess
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo_root, 'utils', 'check_inputs.py')
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        r = subprocess.run([sys.executable, script, '--help'],
                           capture_output=True, text=True, cwd=repo_root,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('usage', r.stdout)
        self.assertIn('--inloc', r.stdout)

    def test_format_report_ok(self):
        from utils.check_inputs import format_report
        text = format_report("cnf.mu2e.T.C.0.tar", [])
        self.assertIn("cnf.mu2e.T.C.0.tar", text)
        self.assertIn("OK", text)

    def test_format_report_groups_problems(self):
        from utils.check_inputs import format_report, Problem
        probs = [
            Problem("dts.mu2e.Pile.CampB.art", "f1.art", "truncated", "1 != 2"),
            Problem("dts.mu2e.Prim.CampA.art", "f2.art", "nearline",
                    "run /prestage dts.mu2e.Prim.CampA.art"),
        ]
        text = format_report("cnf.mu2e.T.C.0.tar", probs)
        self.assertIn("truncated", text)
        self.assertIn("/prestage", text)
        self.assertIn("dts.mu2e.Pile.CampB.art", text)

    def test_main_returns_2_on_problem(self):
        from utils import check_inputs as ci
        with patch.object(ci, "check_inputs",
                          return_value=(False, [ci.Problem(
                              "ds", "f.art", "truncated", "d")])):
            rc = ci.main(["--inloc", "resilient", "cnf.mu2e.T.C.0.tar"])
        self.assertEqual(rc, 2)

    def test_main_returns_0_when_clean(self):
        from utils import check_inputs as ci
        with patch.object(ci, "check_inputs", return_value=(True, [])):
            rc = ci.main(["cnf.mu2e.T.C.0.tar"])
        self.assertEqual(rc, 0)

    def test_main_default_inloc_is_resilient(self):
        from utils import check_inputs as ci
        seen = {}
        def fake(tar, inloc, **kw):
            seen["inloc"] = inloc
            return (True, [])
        with patch.object(ci, "check_inputs", side_effect=fake):
            ci.main(["cnf.mu2e.T.C.0.tar"])
        self.assertEqual(seen["inloc"], "resilient")


class TestEnqueueInputGate(unittest.TestCase):
    """submit_map --enqueue refuses to create a campaign when an entry's
    inputs fail the pre-flight check (exit 2, no ledger row)."""

    def test_failing_check_blocks_and_creates_no_campaign(self):
        from utils import submit
        entry = {"tarball": "cnf.mu2e.T.C.0.tar", "inloc": "resilient",
                 "njobs": 100, "outputs": [{"dataset": "dig.mu2e.*.art",
                                            "location": "tape"}]}
        opts = MagicMock(dry_run=False, slice_size=500,
                         ledger_db="/tmp/never.db")
        created = []
        with patch.object(submit, "_ensure_local_tarball",
                          return_value=Path("cnf.mu2e.T.C.0.tar")), \
             patch.object(submit, "check_inputs",
                          return_value=(False, [submit.Problem(
                              "dts.mu2e.Pile.CampB.art", "f.art",
                              "truncated", "1 != 2")])), \
             patch.object(submit.submission_ledger, "create_campaign",
                          side_effect=lambda *a, **k: created.append(1)):
            with self.assertRaises(SystemExit) as cm:
                submit._enqueue_entries([(0, entry)], "map.json", opts)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(created, [])   # no campaign row

    def test_passing_check_creates_campaign(self):
        from utils import submit
        entry = {"tarball": "cnf.mu2e.T.C.0.tar", "inloc": "resilient",
                 "njobs": 100, "outputs": [{"dataset": "dig.mu2e.*.art",
                                            "location": "tape"}]}
        opts = MagicMock(dry_run=False, slice_size=500,
                         ledger_db="/tmp/never.db")
        with patch.object(submit, "_ensure_local_tarball",
                          return_value=Path("cnf.mu2e.T.C.0.tar")), \
             patch.object(submit, "check_inputs", return_value=(True, [])), \
             patch.object(submit.submission_ledger, "create_campaign",
                          return_value=7):
            ids = submit._enqueue_entries([(0, entry)], "map.json", opts)
        self.assertEqual(ids, [7])


# ---------------------------------------------------------------------------
# MCP adapters
# ---------------------------------------------------------------------------

class TestMcpAdapters(unittest.TestCase):
    def test_error_shape(self):
        from prodtools_mcp.adapters import error
        e = error('not_found', 'no such dataset', 'check the name')
        self.assertEqual(e, {'error': {'kind': 'not_found',
                                       'message': 'no such dataset',
                                       'remedy': 'check the name'}})

    def test_error_rejects_unknown_kind(self):
        from prodtools_mcp.adapters import error
        with self.assertRaises(ValueError):
            error('banana', 'nope')

    def test_safe_tool_passes_success_through(self):
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def ok():
            return {'value': 1}
        self.assertEqual(ok(), {'value': 1})

    def test_safe_tool_converts_toolerror(self):
        from prodtools_mcp.adapters import safe_tool, ToolError

        @safe_tool
        def boom():
            raise ToolError('catalog_unavailable', 'SAM down', 'retry later')
        self.assertEqual(boom()['error']['kind'], 'catalog_unavailable')
        self.assertEqual(boom()['error']['remedy'], 'retry later')

    def test_safe_tool_traps_systemexit(self):
        """SystemExit derives from BaseException; an uncaught one would
        terminate the server rather than fail one call."""
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def exits():
            sys.exit('MU2E_MAX_QUEUED is not an integer')
        result = exits()
        self.assertEqual(result['error']['kind'], 'internal')
        self.assertIn('MU2E_MAX_QUEUED', result['error']['message'])

    def test_safe_tool_converts_unexpected_exception(self):
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def raises():
            raise RuntimeError('kaboom')
        result = raises()
        self.assertEqual(result['error']['kind'], 'internal')
        self.assertIn('kaboom', result['error']['message'])

    def test_safe_tool_keeps_stdout_clean(self):
        """stdout IS the JSON-RPC channel. A print() inside a util must
        not reach it (utils/famtree.py:71 does exactly this)."""
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def chatty():
            print("No files found for dataset: dts.mu2e.X.Y.art")
            return {'ok': True}

        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdout', out), patch.object(sys, 'stderr', err):
            result = chatty()
        self.assertEqual(result, {'ok': True})
        self.assertEqual(out.getvalue(), '')
        self.assertIn('No files found', err.getvalue())

    def test_safe_tool_preserves_name(self):
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def my_tool():
            """Docstring survives."""
            return {}
        self.assertEqual(my_tool.__name__, 'my_tool')
        self.assertEqual(my_tool.__doc__, 'Docstring survives.')


# ---------------------------------------------------------------------------
# MCP read-only ledger
# ---------------------------------------------------------------------------

class TestMcpLedgerRo(unittest.TestCase):
    def _make_db(self, tmpdir):
        """Build a real ledger via the writer, so the read path is tested
        against the actual schema rather than a hand-rolled copy."""
        from utils import submission_ledger
        db = os.path.join(tmpdir, 'ledger.db')
        entry = {'njobs': 4000, 'outputs': [
            {'dataset': 'dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art',
             'location': 'tape'}]}
        cid = submission_ledger.create_campaign(
            db, tarball='cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar',
            entry=entry, slice_size=500, map_path='/tmp/map_au.json')
        submission_ledger.record_submission(
            db, tarball='cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar',
            entry=entry, indices=[0, 1, 2], jobsub_id='29308498.0@sched',
            cluster_id='29308498', map_path='/tmp/map_au.json')
        return db, cid

    def test_campaigns_returns_parsed_entry(self):
        from prodtools_mcp import ledger_ro
        with tempfile.TemporaryDirectory() as td:
            db, cid = self._make_db(td)
            camps = ledger_ro.campaigns(db)
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0]['id'], cid)
        self.assertEqual(camps[0]['slice_size'], 500)
        self.assertIsInstance(camps[0]['entry'], dict)
        self.assertEqual(camps[0]['entry']['njobs'], 4000)

    def test_campaigns_filters_by_state(self):
        from prodtools_mcp import ledger_ro
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._make_db(td)
            self.assertEqual(len(ledger_ro.campaigns(db, state='active')), 1)
            self.assertEqual(len(ledger_ro.campaigns(db, state='complete')), 0)

    def test_rows_returns_parsed_indices_and_cluster(self):
        from prodtools_mcp import ledger_ro
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._make_db(td)
            rows = ledger_ro.rows(db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['indices'], [0, 1, 2])
        self.assertEqual(rows[0]['cluster_id'], '29308498')

    def test_issues_no_ddl(self):
        """The writer's _connect runs CREATE statements on every connect;
        the read path must not, or a read-only DB raises OperationalError."""
        from prodtools_mcp import ledger_ro
        seen = []
        real_connect = sqlite3.connect

        class ConnectionSpy:
            def __init__(self, con):
                object.__setattr__(self, '_con', con)
            def execute(self, sql, *rest):
                seen.append(sql)
                return self._con.execute(sql, *rest)
            def __getattr__(self, name):
                return getattr(self._con, name)
            def __setattr__(self, name, value):
                if name == '_con':
                    object.__setattr__(self, name, value)
                else:
                    setattr(self._con, name, value)

        def spy_connect(*a, **kw):
            con = real_connect(*a, **kw)
            return ConnectionSpy(con)

        with tempfile.TemporaryDirectory() as td:
            db, _ = self._make_db(td)
            with patch.object(sqlite3, 'connect', spy_connect):
                ledger_ro.campaigns(db)
        self.assertTrue(seen, "expected at least one statement")
        for sql in seen:
            self.assertNotIn('CREATE', sql.upper())

    def test_missing_db_is_catalog_unavailable(self):
        from prodtools_mcp import ledger_ro
        from prodtools_mcp.adapters import ToolError
        with self.assertRaises(ToolError) as ctx:
            ledger_ro.campaigns('/nonexistent/path/ledger.db')
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')

    def test_operational_error_becomes_catalog_unavailable(self):
        """A DB missing an expected object must surface as a typed error,
        not an OperationalError traceback."""
        from prodtools_mcp import ledger_ro
        from prodtools_mcp.adapters import ToolError
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'empty.db')
            sqlite3.connect(db).close()      # exists, but has no tables
            with self.assertRaises(ToolError) as ctx:
                ledger_ro.campaigns(db)
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')

    def test_corrupt_db_becomes_catalog_unavailable(self):
        """A truncated/corrupt ledger raises sqlite3.DatabaseError, which
        is NOT an OperationalError — it must still surface as a typed
        error rather than a raw traceback."""
        from prodtools_mcp import ledger_ro
        from prodtools_mcp.adapters import ToolError
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'corrupt.db')
            with open(db, 'wb') as fh:
                fh.write(b'this is not a sqlite database at all')
            with self.assertRaises(ToolError) as ctx:
                ledger_ro.campaigns(db)
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
