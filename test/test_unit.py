#!/usr/bin/env python3
"""
Unit tests for prodtools core modules.

Tests run without SAM/grid access by using in-memory tarballs and mocked
samweb_client. This provides a regression baseline before adding new features
(e.g., stash support).

Run with:  python -m pytest test/test_unit.py -v
       or: python test/test_unit.py
"""

import hashlib
import io
import json
import os
import sys
import tarfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make the package root importable when running from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

from utils.job_common import Mu2eFilename, remove_storage_prefix, Mu2eJobBase


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
# 1. Mu2eFilename (job_common.py)
# ---------------------------------------------------------------------------

class TestMu2eFilename(unittest.TestCase):

    def test_parse_standard_filename(self):
        fn = Mu2eFilename("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertEqual(fn.tier, "dts")
        self.assertEqual(fn.owner, "mu2e")
        self.assertEqual(fn.description, "CeEndpoint")
        self.assertEqual(fn.dsconf, "Run1Bab")
        self.assertEqual(fn.sequencer, "001440_00001234")
        self.assertEqual(fn.extension, "art")

    def test_parse_sim_filename(self):
        fn = Mu2eFilename("sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000000.art")
        self.assertEqual(fn.tier, "sim")
        self.assertEqual(fn.sequencer, "001430_00000000")
        self.assertEqual(fn.dsconf, "MDC2025ac")

    def test_parse_nts_filename(self):
        fn = Mu2eFilename("nts.mu2e.CosmicCRYExtracted.MDC2020av.001205_00000000.root")
        self.assertEqual(fn.tier, "nts")
        self.assertEqual(fn.extension, "root")

    def test_basename_returns_filename(self):
        name = "dig.mu2e.CosmicCRYAllMix1BB.MDC2025af.001430_00000076.art"
        fn = Mu2eFilename(name)
        self.assertEqual(fn.basename(), name)

    def test_invalid_filename_raises(self):
        with self.assertRaises(ValueError):
            Mu2eFilename("too.few.parts")

    def test_invalid_filename_seven_parts_raises(self):
        with self.assertRaises(ValueError):
            Mu2eFilename("a.b.c.d.e.f.g")  # only 5 or 6 fields are valid

    def test_parse_six_parts_ok(self):
        fn = Mu2eFilename("a.b.c.d.e.f")
        self.assertEqual(fn.tier, "a")
        self.assertEqual(fn.extension, "f")

    def test_dataset_derivation(self):
        """Dataset name can be derived from filename by dropping sequencer."""
        fn = Mu2eFilename("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertEqual(str(fn.dataset), "dts.mu2e.CeEndpoint.Run1Bab.art")

    def test_dataset_derivation_sim(self):
        fn = Mu2eFilename("sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000007.art")
        self.assertEqual(str(fn.dataset), "sim.mu2e.MuminusStopsCat.MDC2025ac.art")


# ---------------------------------------------------------------------------
# 1b. Mu2eName extended interface (job_common.py)
# ---------------------------------------------------------------------------

class TestMu2eName(unittest.TestCase):
    """Exercise the unified parse/build/derivation surface of Mu2eName.

    Mu2eFilename is an alias of Mu2eName; this class pins the new behavior
    while TestMu2eFilename keeps the historical contract intact.
    """

    def test_alias(self):
        from utils.job_common import Mu2eName, Mu2eFilename as MF
        self.assertIs(Mu2eName, MF)

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

    def test_dsconf_base_and_version(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("mcs.mu2e.CeEndpoint.MDC2025af_best_v1_3.001440_00001234.art")
        self.assertEqual(n.dsconf_base, "MDC2025af")
        self.assertEqual(n.dsconf_version, "best_v1_3")

    def test_dsconf_version_none_when_plain(self):
        from utils.job_common import Mu2eName
        n = Mu2eName.parse("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        self.assertEqual(n.dsconf_base, "Run1Bab")
        self.assertIsNone(n.dsconf_version)

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
    """Tests for _locate_file without SAM (uses dir: prefix)."""

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
        path = job._locate_file("myfile.art")
        self.assertEqual(path, "/data/inputs/myfile.art")

    def test_dir_prefix_trailing_slash_stripped(self):
        job = self.Cls(self.tar, inloc='dir:/data/inputs/', proto='file')
        path = job._locate_file("myfile.art")
        self.assertEqual(path, "/data/inputs/myfile.art")

    def test_dir_prefix_with_subdirectory(self):
        job = self.Cls(self.tar, inloc='dir:/a/b/c', proto='file')
        path = job._locate_file("x.art")
        self.assertEqual(path, "/a/b/c/x.art")


class TestLocateFileSAM(unittest.TestCase):
    """Tests for _locate_file when SAM is involved (mocked)."""

    def setUp(self):
        from utils.jobfcl import Mu2eJobFCL
        files = ["sim.mu2e.Test.MDC2025ac.001430_00000000.art"]
        jp = _root_input_jobpars(files)
        self.tar = _make_tarball(jp, "#include \"base.fcl\"\nmodule_type : RootInput\n")
        self.Cls = Mu2eJobFCL

    def tearDown(self):
        os.unlink(self.tar)

    def _make_sam_client(self, locations):
        mock_client = MagicMock()
        mock_client.locateFile.return_value = locations
        return mock_client

    def test_tape_location_preferred(self):
        locations = [
            {'location_type': 'disk', 'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/f.art'},
            {'location_type': 'tape', 'full_path': '/pnfs/mu2e/tape/phy-sim/f.art'},
        ]
        with patch('samweb_client.SAMWebClient', return_value=self._make_sam_client(locations)):
            job = self.Cls(self.tar, inloc='tape', proto='file')
            path = job._locate_file("f.art")
        self.assertEqual(path, '/pnfs/mu2e/tape/phy-sim/f.art')

    def test_disk_location_preferred(self):
        locations = [
            {'location_type': 'disk', 'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/f.art'},
            {'location_type': 'tape', 'full_path': '/pnfs/mu2e/tape/phy-sim/f.art'},
        ]
        with patch('samweb_client.SAMWebClient', return_value=self._make_sam_client(locations)):
            job = self.Cls(self.tar, inloc='disk', proto='file')
            path = job._locate_file("f.art")
        self.assertEqual(path, '/pnfs/mu2e/persistent/datasets/phy-sim/f.art')

    def test_fallback_to_first_when_no_match(self):
        """When requested location_type isn't found, fall back to first entry."""
        locations = [
            {'location_type': 'tape', 'full_path': '/pnfs/mu2e/tape/phy-sim/f.art'},
        ]
        with patch('samweb_client.SAMWebClient', return_value=self._make_sam_client(locations)):
            job = self.Cls(self.tar, inloc='disk', proto='file')
            path = job._locate_file("f.art")
        self.assertEqual(path, '/pnfs/mu2e/tape/phy-sim/f.art')

    def test_no_locations_raises(self):
        with patch('samweb_client.SAMWebClient', return_value=self._make_sam_client([])):
            job = self.Cls(self.tar, inloc='tape', proto='file')
            with self.assertRaises(ValueError):
                job._locate_file("f.art")

    def test_sam_exception_raises(self):
        mock_client = MagicMock()
        mock_client.locateFile.side_effect = Exception("SAM unavailable")
        with patch('samweb_client.SAMWebClient', return_value=mock_client):
            job = self.Cls(self.tar, inloc='tape', proto='file')
            with self.assertRaises(ValueError):
                job._locate_file("f.art")


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
        mock_client = MagicMock()
        mock_client.locateFile.return_value = locations
        with patch('samweb_client.SAMWebClient', return_value=mock_client):
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
# 10. datasetFileList Mu2eFilename hash paths
# ---------------------------------------------------------------------------

class TestDatasetFileListFilename(unittest.TestCase):

    def setUp(self):
        # datasetFileList no longer re-exports Mu2eFilename; pull directly
        # from job_common (where the alias still points at Mu2eName).
        from utils.job_common import Mu2eFilename
        self.Cls = Mu2eFilename

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
    derived purely from the filename via Mu2eFilename.
    """

    STASH_ROOT = "/cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash"

    def _stash_path(self, filename: str) -> str:
        """Reference implementation of stash path building (not yet in code)."""
        fn = Mu2eFilename(filename)
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
# 12. jobfcl stash integration (_locate_file and _format_filename)
# ---------------------------------------------------------------------------

STASH_READ_DEFAULT = "/cvmfs/mu2e.osgstorage.org/pnfs/fnal.gov/usr/mu2e/persistent/stash"
STASH_WRITE_DEFAULT = "/pnfs/mu2e/persistent/stash"


class TestLocateFileStash(unittest.TestCase):
    """_locate_file with inloc='stash' — path derived from filename (SAM only as fallback)."""

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
        mock_sam = MagicMock()
        with patch('samweb_client.SAMWebClient', return_value=mock_sam):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            job._locate_file("dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art")
        mock_sam.locateFile.assert_not_called()

    def test_stash_path_structure(self):
        job = self.Cls(self.tar, inloc='stash', proto='file')
        fname = "dts.mu2e.CeEndpoint.Run1Bab.001440_00001234.art"
        path = job._locate_file(fname)
        expected = (
            f"{STASH_READ_DEFAULT}/datasets/dts/mu2e/CeEndpoint/Run1Bab/art/{fname}"
        )
        self.assertEqual(path, expected)

    def test_stash_path_sim_file(self):
        job = self.Cls(self.tar, inloc='stash', proto='file')
        fname = "sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000007.art"
        path = job._locate_file(fname)
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
            path = job._locate_file(fname)
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
        """dry_run=True must not invoke cp or makedirs."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art",
                      "dts.mu2e.CeEndpoint.Run1Bab.001440_00000001.art"]
        mock_locations = [
            {'location_type': 'disk',
             'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'}
        ]

        with patch('utils.stash_utils.list_files', return_value=mock_files), \
             patch('utils.stash_utils.locate_file_full', return_value=mock_locations), \
             patch('os.makedirs') as mock_mkdir, \
             patch('subprocess.run') as mock_run:
            n = stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                dry_run=True,
                verbose=False,
            )

        mock_mkdir.assert_not_called()
        mock_run.assert_not_called()
        self.assertEqual(n, 2)

    def test_copy_dataset_calls_cp(self):
        """copy_dataset_to_stash must call subprocess.run with cp."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art"]
        mock_locations = [
            {'location_type': 'disk',
             'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'}
        ]
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0

        with patch('utils.stash_utils.list_files', return_value=mock_files), \
             patch('utils.stash_utils.locate_file_full', return_value=mock_locations), \
             patch('os.makedirs'), \
             patch('subprocess.run', return_value=mock_run_result) as mock_run:
            n = stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                dry_run=False,
                verbose=False,
            )

        self.assertEqual(n, 1)
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], 'cp')

    def test_copy_dataset_limit(self):
        """--limit N should copy at most N files."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_%08d.art" % i for i in range(10)]
        mock_locations = [
            {'location_type': 'disk',
             'full_path': '/pnfs/mu2e/persistent/datasets/phy-sim/dts/mu2e/CeEndpoint/Run1Bab/art'}
        ]
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0

        with patch('utils.stash_utils.list_files', return_value=mock_files), \
             patch('utils.stash_utils.locate_file_full', return_value=mock_locations), \
             patch('os.makedirs'), \
             patch('subprocess.run', return_value=mock_run_result) as mock_run:
            stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                limit=3,
                dry_run=False,
                verbose=False,
            )

        self.assertEqual(mock_run.call_count, 3)

    def test_copy_dataset_skips_on_locate_failure(self):
        """Files that cannot be located should be skipped, not crash."""
        from utils import stash_utils

        mock_files = ["dts.mu2e.CeEndpoint.Run1Bab.001440_00000000.art"]

        with patch('utils.stash_utils.list_files', return_value=mock_files), \
             patch('utils.stash_utils.locate_file_full', return_value=[]), \
             patch('os.makedirs'), \
             patch('subprocess.run') as mock_run:
            n = stash_utils.copy_dataset_to_stash(
                "dts.mu2e.CeEndpoint.Run1Bab.art",
                source_loc='disk',
                dry_run=False,
                verbose=False,
            )

        mock_run.assert_not_called()
        self.assertEqual(n, 0)


# ---------------------------------------------------------------------------
# 14. prod_utils: stash skips copy_input
# ---------------------------------------------------------------------------

class TestProcessJobdefStashSkipsCopyInput(unittest.TestCase):
    """
    When inloc='stash', process_jobdef must use streaming mode even when
    args.copy_input is True — CVMFS files need no local copying.
    """

    def test_stash_does_not_call_mdh_copy(self):
        from utils import prod_utils

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

        with patch('utils.prod_utils.write_fcl', return_value=mock_fcl) as mock_wfcl, \
             patch('utils.prod_utils.run') as mock_run, \
             patch('utils.jobquery.Mu2eJobPars') as mock_pars:

            mock_pars.return_value.setup.return_value = "/cvmfs/test/setup.sh"

            prod_utils.process_jobdef(
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

    # --- get_fcl_name ---

    def test_fcl_name_default_version(self):
        from utils.json2jobdef import get_fcl_name
        self.assertEqual(get_fcl_name(self._cfg()), 'cnf.mu2e.TestDesc.TestConf.0.fcl')

    def test_fcl_name_with_version(self):
        from utils.json2jobdef import get_fcl_name
        self.assertEqual(get_fcl_name(self._cfg(version=3)), 'cnf.mu2e.TestDesc.TestConf.3.fcl')

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
    """When inloc='stash' and the file is not on CVMFS, _locate_file falls back to SAM."""

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

    def _mock_sam(self, location_type='tape'):
        mock_sam = MagicMock()
        mock_sam.locateFile.return_value = [
            {'location_type': location_type, 'full_path': self._TAPE_DIR}
        ]
        return mock_sam

    def test_sam_called_when_stash_file_missing(self):
        """SAM is contacted as fallback when the stash CVMFS path does not exist."""
        mock_sam = self._mock_sam()
        with patch('samweb_client.SAMWebClient', return_value=mock_sam):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            job._locate_file(self._FNAME)
        mock_sam.locateFile.assert_called_once_with(self._FNAME)

    def test_fallback_returns_sam_path(self):
        """The SAM-provided path is returned when the stash file is absent."""
        mock_sam = self._mock_sam()
        with patch('samweb_client.SAMWebClient', return_value=mock_sam):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            path = job._locate_file(self._FNAME)
        self.assertEqual(path, self._TAPE_DIR)

    def test_fallback_raises_when_sam_has_no_locations(self):
        """ValueError is raised when the file is absent from stash and SAM finds nothing."""
        mock_sam = MagicMock()
        mock_sam.locateFile.return_value = []
        with patch('samweb_client.SAMWebClient', return_value=mock_sam):
            from utils.jobfcl import Mu2eJobFCL
            job = Mu2eJobFCL(self.tar, inloc='stash', proto='file')
            with self.assertRaises(ValueError):
                job._locate_file(self._FNAME)

    def test_fallback_format_filename_applies_xroot(self):
        """_format_filename with proto='root' converts the SAM tape path to an xroot URL."""
        mock_sam = self._mock_sam()
        with patch('samweb_client.SAMWebClient', return_value=mock_sam):
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
        with patch('utils.json2jobdef.list_files', return_value=[]):
            self.assertEqual(_next_version(self._cfg()), 0)

    def test_single_version_zero_returns_one(self):
        from utils.json2jobdef import _next_version
        with patch('utils.json2jobdef.list_files',
                   return_value=['cnf.mu2e.TestDesc.TC.0.tar']):
            self.assertEqual(_next_version(self._cfg()), 1)

    def test_multiple_versions_returns_next(self):
        from utils.json2jobdef import _next_version
        files = ['cnf.mu2e.TestDesc.TC.0.tar',
                 'cnf.mu2e.TestDesc.TC.1.tar',
                 'cnf.mu2e.TestDesc.TC.2.tar']
        with patch('utils.json2jobdef.list_files', return_value=files):
            self.assertEqual(_next_version(self._cfg()), 3)

    def test_sam_exception_returns_zero(self):
        from utils.json2jobdef import _next_version
        with patch('utils.json2jobdef.list_files', side_effect=Exception("SAM down")):
            self.assertEqual(_next_version(self._cfg()), 0)

    def test_non_sequential_versions(self):
        from utils.json2jobdef import _next_version
        files = ['cnf.mu2e.TestDesc.TC.0.tar', 'cnf.mu2e.TestDesc.TC.5.tar']
        with patch('utils.json2jobdef.list_files', return_value=files):
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
             patch('utils.json2jobdef.list_files', side_effect=[
                 parents,            # isparentof query
                 [],                 # _next_version dataset query
             ]):
            cfg = self._cfg()
            result = _compute_extend_exclusions(cfg)

        self.assertEqual(result, set(parents))
        self.assertEqual(cfg['version'], 0)

    def test_version_incremented(self):
        from utils.json2jobdef import _compute_extend_exclusions

        with patch('utils.json2jobdef.get_output_dataset_names',
                   return_value=['mcs.mu2e.TestDesc.TC.art']), \
             patch('utils.json2jobdef.list_files', side_effect=[
                 ['parent.art'],                      # isparentof
                 ['cnf.mu2e.TestDesc.TC.0.tar'],      # _next_version
             ]):
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
             patch('utils.json2jobdef.list_files', side_effect=[
                 ['a.art', 'b.art'],     # parents of ds1
                 ['b.art', 'c.art'],     # parents of ds2
                 [],                     # _next_version
             ]):
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
# 22. validate_jobdesc — three-way mode detection (prod_utils.py)
# ---------------------------------------------------------------------------

class TestValidateJobdesc(unittest.TestCase):

    def test_template_mode(self):
        from utils.prod_utils import validate_jobdesc
        jd = [{'fcl_template': 'base.fcl', 'setup_script': '/s/setup.sh',
               'inloc': 'tape', 'outputs': []}]
        self.assertEqual(validate_jobdesc(jd), 'template')

    def test_direct_input_mode(self):
        from utils.prod_utils import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.Reco.MDC2025af.0.tar',
               'inloc': 'tape', 'outputs': []}]
        self.assertEqual(validate_jobdesc(jd), 'direct_input')

    def test_normal_mode(self):
        from utils.prod_utils import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.T.TC.0.tar', 'njobs': 5,
               'inloc': 'tape', 'outputs': []}]
        self.assertFalse(validate_jobdesc(jd))

    def test_direct_input_is_truthy(self):
        """'direct_input' string must be truthy for backward-compatible if-checks."""
        from utils.prod_utils import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.Reco.MDC2025af.0.tar',
               'inloc': 'tape', 'outputs': []}]
        self.assertTrue(validate_jobdesc(jd))

    def test_normal_mode_is_falsy(self):
        from utils.prod_utils import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.T.TC.0.tar', 'njobs': 5,
               'inloc': 'tape', 'outputs': []}]
        self.assertFalse(validate_jobdesc(jd))

    def test_direct_input_multiple_entries_exits(self):
        from utils.prod_utils import validate_jobdesc
        jd = [
            {'tarball': 'a.tar', 'inloc': 'tape', 'outputs': []},
            {'tarball': 'b.tar', 'inloc': 'tape', 'outputs': []},
        ]
        with self.assertRaises(SystemExit):
            validate_jobdesc(jd)

    def test_direct_input_missing_outputs_exits(self):
        from utils.prod_utils import validate_jobdesc
        jd = [{'tarball': 'cnf.mu2e.Reco.MDC2025af.0.tar', 'inloc': 'tape'}]
        with self.assertRaises(SystemExit):
            validate_jobdesc(jd)

    def test_normal_mode_missing_njobs_exits(self):
        """Entry without tarball: falls through to normal-mode validation which requires njobs."""
        from utils.prod_utils import validate_jobdesc
        jd = [{'inloc': 'tape', 'outputs': []}]  # no tarball, no njobs
        with self.assertRaises(SystemExit):
            validate_jobdesc(jd)

    def test_normal_mode_with_generic_entry_ignored(self):
        """Normal-mode jobdesc with a trailing generic tarball (no njobs) is valid."""
        from utils.prod_utils import validate_jobdesc
        jd = [
            {'tarball': 'a.tar', 'njobs': 100, 'inloc': 'tape', 'outputs': []},
            {'tarball': 'b.tar', 'njobs': 200, 'inloc': 'tape', 'outputs': []},
            {'tarball': 'cnf.mu2e.OnSpillTriggeredReco.MDC2025af.0.tar',
             'inloc': 'tape', 'outputs': []},  # generic - no njobs
        ]
        self.assertFalse(validate_jobdesc(jd))

    def test_empty_list_exits(self):
        from utils.prod_utils import validate_jobdesc
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
             patch.object(json2jobdef, 'get_fcl_name', return_value='cnf.x.0.fcl'), \
             patch.object(json2jobdef, 'append_jobdef'):
            cfg = {'desc': 'reco', 'dsconf': 'D', 'owner': 'mu2e',
                   'simjob_setup': 's', 'inloc': 'tape', 'generic_tarball': True,
                   'fcl': 'f.fcl', 'outloc': {'*.art': 'tape'}}
            try:
                json2jobdef.build_jobdef(cfg, job_args=[], json_output=True)
            except Exception:
                pass  # downstream packaging is mocked/partial; we only assert the guard
            guard.assert_not_called()


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
# 25. process_direct_input (prod_utils.py)
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
        from utils.prod_utils import process_direct_input
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
        from utils.prod_utils import process_direct_input
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
        # Input filename that would fail Mu2eFilename parsing — verifies
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
        jp["njobs"] = njobs
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

    def test_cnf_njobs_for_output(self):
        """Ground-truth njobs = producing cnf's njobs (resolver + Mu2eJobPars)."""
        from utils import jobdef_lookup
        mock_pars = MagicMock()
        mock_pars.return_value.njobs.return_value = 50
        with patch.object(jobdef_lookup, 'cnf_for_output', return_value="cnf.mu2e.X.TC.0.tar"), \
             patch('utils.jobquery.Mu2eJobPars', mock_pars):
            self.assertEqual(
                jobdef_lookup.cnf_njobs_for_output(
                    "dig.mu2e.CeEndpointOnSpill.MDC2025ap_best_v1_1.art"),
                50)

    def test_cnf_njobs_zero_raises(self):
        """Open-ended generator cnf (njobs==0) must fail loud, not return 0 —
        so completeness can't silently compare a real count against a bogus 0."""
        from utils import jobdef_lookup
        mock_pars = MagicMock()
        mock_pars.return_value.njobs.return_value = 0
        with patch.object(jobdef_lookup, 'cnf_for_output', return_value="cnf.mu2e.Gen.TC.0.tar"), \
             patch('utils.jobquery.Mu2eJobPars', mock_pars):
            with self.assertRaises(RuntimeError):
                jobdef_lookup.cnf_njobs_for_output("dts.mu2e.Gen.MDC2025ap.art")

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

    def test_stage_for_tier(self):
        from utils import chain_emit
        self.assertEqual(chain_emit.stage_for_tier("dts"), "digi")
        self.assertEqual(chain_emit.stage_for_tier("dig"), "reco")
        self.assertEqual(chain_emit.stage_for_tier("mcs"), "ntuple")

    def test_stage_for_tier_unknown_raises(self):
        from utils import chain_emit
        with self.assertRaises(ValueError):
            chain_emit.stage_for_tier("nts")

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

    def test_dataset_complete_true_false(self):
        from utils import chain_emit
        self.assertTrue(chain_emit.dataset_complete("d", lambda n: 50, lambda n: 50))
        self.assertFalse(chain_emit.dataset_complete("d", lambda n: 40, lambda n: 50))

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

class TestDatasetInfoGencount(unittest.TestCase):
    """DatasetInfo.gen_per_file and .filter_eff derived from gencount."""

    def _info(self, **kw):
        from utils.poms_db import DatasetInfo
        return DatasetInfo(**kw)

    def test_filter_eff(self):
        i = self._info(nfiles=2000, nevts=2761, gencount=5000)
        self.assertAlmostEqual(i.filter_eff, 2761 / 5000)

    def test_gen_per_file(self):
        i = self._info(nfiles=2000, nevts=2761, gencount=10_000_000)
        self.assertEqual(i.gen_per_file, 5000)

    def test_filter_eff_none_without_gencount(self):
        self.assertIsNone(self._info(nfiles=10, nevts=5, gencount=None).filter_eff)
        self.assertIsNone(self._info(nfiles=10, nevts=5, gencount=0).filter_eff)

    def test_gen_per_file_none_without_gencount(self):
        self.assertIsNone(self._info(nfiles=10, nevts=5, gencount=None).gen_per_file)


class TestGetDatasetGencount(unittest.TestCase):
    """db_builder._get_dataset_gencount: gencount(file) * nfiles, one metadata call."""

    def test_multiplies_per_file_by_nfiles(self):
        from utils import db_builder
        with patch.object(db_builder, 'list_definition_files',
                          return_value=['f0.art', 'f1.art']), \
             patch.object(db_builder, 'get_metadata',
                          return_value={'dh.gencount': 5000}) as gm:
            self.assertEqual(db_builder._get_dataset_gencount('ds', 2000), 5000 * 2000)
            gm.assert_called_once()  # only ONE metadata call regardless of nfiles

    def test_none_when_no_gencount_field(self):
        from utils import db_builder
        with patch.object(db_builder, 'list_definition_files', return_value=['f0.art']), \
             patch.object(db_builder, 'get_metadata', return_value={'event_count': 5}):
            self.assertIsNone(db_builder._get_dataset_gencount('ds', 100))

    def test_none_when_no_files(self):
        from utils import db_builder
        self.assertIsNone(db_builder._get_dataset_gencount('ds', 0))

    def test_none_on_exception(self):
        from utils import db_builder
        with patch.object(db_builder, 'list_definition_files',
                          side_effect=Exception('SAM down')):
            self.assertIsNone(db_builder._get_dataset_gencount('ds', 100))


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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
