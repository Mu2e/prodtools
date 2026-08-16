#!/usr/bin/env python3
"""Unit tests for utils/json2jobdef.py's dir:-inloc resampler handling.

Bug: json2jobdef crashed with `KeyError: '_max_events_to_skip'` building a
RESAMPLER entry (`resampler_name` + `input_data`) whose `inloc` is
`dir:<path>`. `_build_job_args` unconditionally treated the first
`input_data` key as a SAM dataset name and queried SAM for its event count;
for a `dir:` entry the keys are bare file basenames (per the documented
local-dir shape in `_create_inputs_file`), so the SAM query always failed,
left `_max_events_to_skip` unset, and `build_jobdef` then read that key
unconditionally three lines later, raising KeyError.

Fix: `_is_dir_inloc(config)` gates both the SAM lookup in `_build_job_args`
and the post_line emission in `build_jobdef`, so a `dir:` resampler skips
the auto-computation entirely -- no SAM call, no post_line -- and the
entry's own `fcl_overrides` (or the base FCL's) stands undisturbed.

Full context / repro / traceback:
/exp/mu2e/app/users/oksuzian/autoresearch_wt_localexec/.superpowers/sdd/2026-08-16-prodtools-switch/task-11-report.md
(Step 3, "mustops_ce" section)

Standalone style, mirroring test_runmu2e_dir_pnfs.py: json2jobdef.py
transitively imports utils.samweb_wrapper -> `from samweb_client import
SAMWebClient`, which only exists inside the Mu2e env (muse setup).
test_unit.py:61-70 solves this exact dependency chain by stubbing
samweb_client/ifdh into sys.modules with MagicMock() before any utils.*
import; the same stub is copied here (not imported from test_unit.py, to
keep this file standalone and untangled from the operator's in-flight
test_unit.py) so the assertions actually run in a plain non-Mu2e shell
instead of skipping.

Run with:  python3 test/test_json2jobdef_dir_resampler.py
       or: python -m pytest test/test_json2jobdef_dir_resampler.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# samweb_client and other Fermilab-specific modules are not available
# outside the Mu2e environment. Stub them before any utils import occurs
# so that this test file runs standalone (mirrors test/test_unit.py:64-70).
for _mod in ('samweb_client', 'ifdh'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from utils import json2jobdef


def _resampler_config(inloc, first_key):
    """Minimal RESAMPLER entry: resampler_name + input_data, as built by
    core/prodtools_exec.render_entry for a locally-farmed resampler stage
    (mustops_ce) when `inloc` is `dir:<path>`."""
    return {
        'resampler_name': 'TargetStopResampler',
        'input_data': {first_key: 1},
        'inloc': inloc,
        'fcl': 'base.fcl',
        'fcl_overrides': {},
        'desc': 'Run1A_CeEndpoint_prodsw_smoke01',
        'dsconf': 'Run1Bak_prodsw_smoke01',
        'owner': 'mu2e',
        'code': 'code.tar',
        'generic_tarball': True,  # skips validate_output_filenames in build_jobdef
    }


class TestBuildJobArgsDirResampler(unittest.TestCase):
    """_build_job_args: the SAM-lookup gate."""

    def test_dir_inloc_never_queries_sam(self):
        """A dir:-farmed resampler's first input_data key is a bare file
        basename (e.g. sim.oksuzian.TargetStops...art), not a SAM dataset
        name. The SAM lookup must not even be attempted."""
        config = _resampler_config(
            'dir:/exp/mu2e/data/users/oksuzian/autoresearch_grid/farm',
            'sim.oksuzian.TargetStops.Run1Bak_x.001800_00000000.art')
        with patch.object(
                json2jobdef, 'max_events_to_skip',
                side_effect=AssertionError(
                    'SAM must not be queried for a dir: inloc')) as sam_lookup:
            job_args = json2jobdef._build_job_args(config)

        # The real assertion: the SAM-lookup function itself must never be
        # invoked (not merely that a failed call got swallowed -- the old
        # code's `except Exception` around the SAM call would otherwise
        # make this pass even when the call happened and simply failed).
        sam_lookup.assert_not_called()
        self.assertNotIn('_max_events_to_skip', config)
        self.assertIn('--auxinput', job_args)

    def test_non_dir_inloc_still_queries_sam(self):
        """Non-dir (real SAM dataset) resamplers must keep computing
        MaxEventsToSkip exactly as before -- this fix must not touch that
        path."""
        first_key = 'sim.mu2e.MuBeamCat.Run1Baa.art'
        config = _resampler_config('tape', first_key)
        with patch.object(json2jobdef, 'max_events_to_skip',
                           return_value=319542) as sam_lookup:
            json2jobdef._build_job_args(config)

        sam_lookup.assert_called_once_with(first_key)
        self.assertEqual(config['_max_events_to_skip'], 319542)


class TestBuildJobdefDirResampler(unittest.TestCase):
    """build_jobdef: the post_line-emission gate (FHiCL last-wins, so a
    dangling post_line would silently clobber fcl_overrides even if it
    didn't crash -- see the report's Step 3 sub-question)."""

    def _run_and_capture_post_lines(self, config):
        captured = {}

        def _fake_write_fcl_template(base, overrides, pre_lines=(), post_lines=()):
            captured['post_lines'] = list(post_lines)

        with patch.object(json2jobdef, 'write_fcl_template',
                           side_effect=_fake_write_fcl_template), \
             patch.object(json2jobdef, 'create_jobdef'), \
             patch.object(json2jobdef, 'get_parfile_name',
                           return_value='cnf.mu2e.x.y.0.tar'):
            json2jobdef.build_jobdef(config, job_args=[])
        return captured['post_lines']

    def test_dir_inloc_emits_no_post_line(self):
        config = _resampler_config(
            'dir:/exp/mu2e/data/users/oksuzian/autoresearch_grid/farm',
            'sim.oksuzian.TargetStops.Run1Bak_x.001800_00000000.art')
        # No _max_events_to_skip key at all -- mirrors the real crash
        # scenario (SAM lookup skipped, so the key was never set).
        self.assertNotIn('_max_events_to_skip', config)

        post_lines = self._run_and_capture_post_lines(config)
        self.assertEqual(post_lines, [])

    def test_non_dir_inloc_still_emits_post_line(self):
        first_key = 'sim.mu2e.MuBeamCat.Run1Baa.art'
        config = _resampler_config('tape', first_key)
        config['_max_events_to_skip'] = 319542

        post_lines = self._run_and_capture_post_lines(config)
        self.assertEqual(len(post_lines), 1)
        self.assertIn('physics.filters.TargetStopResampler.mu2e.MaxEventsToSkip: 319542',
                       post_lines[0])


if __name__ == '__main__':
    unittest.main()
