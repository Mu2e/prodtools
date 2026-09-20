#!/usr/bin/env python3
"""Unit tests for utils/stage_eff.py. No ROOT, no SAM: the reader subprocess
and the samweb module are replaced."""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import stage_eff
from utils.stage_eff import StageEffError


def counts(gen, passed, prescales=None, source='files'):
    return {'source': source, 'files': 1, 'subruns': 1, 'gen': gen,
            'passed': passed, 'prescales': prescales}


class TestIsDatasetName(unittest.TestCase):
    def test_five_fields_is_a_dataset(self):
        self.assertTrue(stage_eff.is_dataset_name('sim.mu2e.MuBeamCat.Run1Bai.art'))

    def test_a_file_name_has_six_fields(self):
        self.assertFalse(stage_eff.is_dataset_name(
            'sim.mu2e.MuBeamCat.Run1Bai.001470_00000010.art'))

    def test_paths_and_urls_are_files(self):
        self.assertFalse(stage_eff.is_dataset_name('/pnfs/a/sim.mu2e.X.Y.art'))
        self.assertFalse(stage_eff.is_dataset_name('root://host//a.b.c.d.art'))

    def test_an_existing_local_file_wins_over_its_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            here = os.getcwd()
            os.chdir(tmp)
            try:
                open('sim.mu2e.X.Y.art', 'w').close()
                self.assertFalse(stage_eff.is_dataset_name('sim.mu2e.X.Y.art'))
            finally:
                os.chdir(here)


class TestExpandFiles(unittest.TestCase):
    def test_glob_expands_sorted_and_duplicates_collapse(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('b.art', 'a.art'):
                open(os.path.join(tmp, name), 'w').close()
            got = stage_eff.expand_files([os.path.join(tmp, '*.art'),
                                          os.path.join(tmp, 'a.art')])
            self.assertEqual([os.path.basename(g) for g in got], ['a.art', 'b.art'])

    def test_url_is_kept_verbatim(self):
        url = 'root://fndcadoor.fnal.gov//pnfs/x/f.art'
        self.assertEqual(stage_eff.expand_files([url]), [url])

    def test_no_match_is_an_error(self):
        with self.assertRaises(StageEffError):
            stage_eff.expand_files(['/nonexistent/*.art'])


class TestReadFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'f.art')
        open(self.path, 'w').close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_last_stdout_line_is_the_result(self):
        payload = {'files': 1, 'subruns': 4, 'gen': 250, 'passed': 63, 'prescales': {}}
        run = lambda cmd, **kw: SimpleNamespace(
            returncode=0, stdout='ROOT noise\n' + json.dumps(payload) + '\n', stderr='')
        got = stage_eff.read_files([self.path], run=run)
        self.assertEqual((got['gen'], got['passed'], got['source']), (250, 63, 'files'))

    def test_reader_failure_raises_with_its_last_stderr_line(self):
        run = lambda cmd, **kw: SimpleNamespace(
            returncode=1, stdout='', stderr='warn\nstage_eff_reader: cannot open f.art\n')
        with self.assertRaisesRegex(StageEffError, 'cannot open f.art'):
            stage_eff.read_files([self.path], run=run)


class TestReaderCommand(unittest.TestCase):
    def test_without_root_it_runs_in_a_clean_muse_shell(self):
        import importlib.util
        real = importlib.util.find_spec
        importlib.util.find_spec = lambda name: None if name == 'ROOT' else real(name)
        try:
            cmd, env = stage_eff.reader_command(['f.art'], musing='MDC2025aw')
        finally:
            importlib.util.find_spec = real
        self.assertEqual(cmd[:2], ['bash', '-c'])
        self.assertIn('muse setup SimJob MDC2025aw', cmd[2])
        self.assertEqual(cmd[-1], 'f.art')
        self.assertNotIn('MUSE_WORK_DIR', env)
        self.assertNotIn('PYTHONPATH', env)


class TestReadDataset(unittest.TestCase):
    def fake(self, metadata):
        return SimpleNamespace(
            files_in_dataset=lambda ds, availability=None: [m['file_name'] for m in metadata],
            metadata_for_files=lambda names: metadata)

    def test_sums_gencount_and_event_count(self):
        md = [{'file_name': 'a', 'dh.gencount': 1000, 'event_count': 10},
              {'file_name': 'b', 'dh.gencount': 1000}]  # SAM omits a zero count
        got = stage_eff.read_dataset('sim.mu2e.X.Y.art', samweb=self.fake(md))
        self.assertEqual((got['gen'], got['passed'], got['files']), (2000, 10, 2))
        self.assertIsNone(got['prescales'])

    def test_a_file_without_gencount_is_an_error_not_a_skip(self):
        md = [{'file_name': 'a', 'dh.gencount': 1000, 'event_count': 10},
              {'file_name': 'b', 'event_count': 5}]
        with self.assertRaisesRegex(StageEffError, 'dh.gencount'):
            stage_eff.read_dataset('sim.mu2e.X.Y.art', samweb=self.fake(md))

    def test_empty_dataset_is_an_error(self):
        with self.assertRaises(StageEffError):
            stage_eff.read_dataset('sim.mu2e.X.Y.art', samweb=self.fake([]))


class TestEvaluate(unittest.TestCase):
    def test_chain_is_the_product(self):
        got = stage_eff.evaluate([counts(2000000000, 25584287),
                                  counts(2000000000, 79203)])
        self.assertAlmostEqual(got['chain_eff'], 0.0127921435 * 3.96015e-05)
        self.assertEqual(got['chain_eff'], got['chain_eff_unprescaled'])

    def test_selected_prescale_is_undone(self):
        prescales = {'TargetStopPrescaleFilter': {'seen': 2000000000, 'passed': 1998707},
                     'EarlyPrescaleFilter': {'seen': 2000000000, 'passed': 66667520}}
        got = stage_eff.evaluate([counts(2000000000, 79203, prescales)],
                                 {1: 'TargetStopPrescaleFilter'})
        stage = got['stages'][0]
        self.assertAlmostEqual(stage['eff'], 3.96015e-05)
        self.assertAlmostEqual(stage['eff_unprescaled'], 79203 / 1998707)
        self.assertEqual(stage['prescale'], 'TargetStopPrescaleFilter')

    def test_prescales_are_never_applied_unasked(self):
        prescales = {'P': {'seen': 100, 'passed': 10}}
        got = stage_eff.evaluate([counts(100, 5, prescales)])
        self.assertEqual(got['stages'][0]['eff_unprescaled'], 0.05)

    def test_unknown_label_lists_what_exists(self):
        with self.assertRaisesRegex(StageEffError, "available: \\['P'\\]"):
            stage_eff.evaluate([counts(100, 5, {'P': {'seen': 100, 'passed': 10}})],
                               {1: 'Q'})

    def test_prescale_on_a_sam_stage_says_to_pass_files(self):
        with self.assertRaisesRegex(StageEffError, 'pass files'):
            stage_eff.evaluate([counts(100, 5, None, source='sam')], {1: 'P'})

    def test_prescale_for_a_stage_that_does_not_exist(self):
        with self.assertRaises(StageEffError):
            stage_eff.evaluate([counts(100, 5)], {2: 'P'})

    def test_zero_generated_is_an_error(self):
        with self.assertRaises(StageEffError):
            stage_eff.evaluate([counts(0, 0)])


class TestStageArguments(unittest.TestCase):
    def test_dataset_mixed_with_files_is_rejected(self):
        with self.assertRaisesRegex(StageEffError, 'not a mix'):
            stage_eff.read_stage(['sim.mu2e.X.Y.art', '/tmp/f.art'])

    def test_parse_prescales(self):
        self.assertEqual(stage_eff.parse_prescales(['2:TargetStopPrescaleFilter']),
                         {2: 'TargetStopPrescaleFilter'})
        for bad in ('TargetStopPrescaleFilter', 'x:P', '2:'):
            with self.assertRaises(StageEffError):
                stage_eff.parse_prescales([bad])


class TestMcpStageEfficiency(unittest.TestCase):
    """The MCP wrapper: argument shapes and error kinds. Imports only
    prodtools_mcp.adapters, so it runs without the mcp SDK."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mcp', 'src'))
        from prodtools_mcp.tools import efficiency
        from prodtools_mcp.adapters import ToolError
        cls.efficiency, cls.ToolError = efficiency, ToolError

    def test_string_stage_keys_become_numbers(self):
        seen = {}
        def fake(stages, prescales, musing):
            seen.update(stages=stages, prescales=prescales, musing=musing)
            return {'chain_eff': 1.0}
        got = self.efficiency.stage_efficiency(
            [['a.b.c.d.art'], ['/x/f.art']], {'2': 'P'}, 'MDC2025aw', evaluate_fn=fake)
        self.assertEqual(got, {'chain_eff': 1.0})
        self.assertEqual(seen['prescales'], {2: 'P'})
        self.assertEqual(seen['musing'], 'MDC2025aw')

    def test_bad_shapes_are_invalid_argument(self):
        for stages in ([], 'sim.mu2e.X.Y.art', [[]], [['ok'], 'flat'], [[1]]):
            with self.assertRaises(self.ToolError) as ctx:
                self.efficiency.stage_efficiency(stages, evaluate_fn=lambda *a: {})
            self.assertEqual(ctx.exception.kind, 'invalid_argument')

    def test_bad_prescale_key(self):
        with self.assertRaises(self.ToolError) as ctx:
            self.efficiency.stage_efficiency([['f.art']], {'two': 'P'},
                                             evaluate_fn=lambda *a: {})
        self.assertEqual(ctx.exception.kind, 'invalid_argument')

    def test_library_error_keeps_its_message(self):
        def boom(*a):
            raise stage_eff.StageEffError('the file list overlaps')
        with self.assertRaises(self.ToolError) as ctx:
            self.efficiency.stage_efficiency([['f.art']], evaluate_fn=boom)
        self.assertEqual(ctx.exception.kind, 'invalid_argument')
        self.assertIn('overlaps', ctx.exception.message)

    def test_sam_auth_failure_is_auth_expired(self):
        class Http(Exception):
            code = 401
        def boom(*a):
            raise Http('nope')
        with self.assertRaises(self.ToolError) as ctx:
            self.efficiency.stage_efficiency([['a.b.c.d.art']], evaluate_fn=boom)
        self.assertEqual(ctx.exception.kind, 'auth_expired')


if __name__ == '__main__':
    unittest.main()
