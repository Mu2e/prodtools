"""Tests for utils/epochs: sim-epochs catalog. Runs standalone (samweb stubbed)."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _mod in ('samweb_client', 'ifdh'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from utils.epochs.dsconf import DsconfKey, DsconfParseError, parse_dsconf, family_of

from utils.epochs.epoch_files import (EpochFile, EpochFileError, load_epoch_files,
                                      propose_epoch, write_epoch_file, EPOCH_STATUSES)

from utils.epochs.graph import build_catalog, root_matches, Member, Catalog


def _tmpdir():
    d = tempfile.mkdtemp()
    return d


class TestDsconfParse(unittest.TestCase):
    def test_full_grammar(self):
        k = parse_dsconf('MDC2025au_best_v1_5-001')
        self.assertEqual(k, DsconfKey('MDC2025', 'au', 0, 'best', 1, 5, 1, ''))

    def test_no_purpose_dts_style(self):
        k = parse_dsconf('MDC2025ap')
        self.assertEqual((k.family, k.letters, k.purpose), ('MDC2025', 'ap', None))

    def test_revision_digit(self):
        k = parse_dsconf('Run1Bab2_best_v1_2')
        self.assertEqual((k.family, k.letters, k.rev), ('Run1B', 'ab', 2))

    def test_single_letter_tag(self):
        k = parse_dsconf('MDC2020r')
        self.assertEqual((k.family, k.letters), ('MDC2020', 'r'))

    def test_own_series_no_letters(self):
        k = parse_dsconf('MDC2025-003')
        self.assertEqual((k.family, k.letters, k.suffix), ('MDC2025', '', 3))

    def test_legacy_offline_tail(self):
        k = parse_dsconf('MDC2020aw_best_v1_3_v06_06_00')
        self.assertEqual(k.extra, 'v06_06_00')

    def test_unparseable_raises(self):
        with self.assertRaises(DsconfParseError):
            parse_dsconf('best_v1_5')
        with self.assertRaises(DsconfParseError):
            parse_dsconf('')

    def test_family_of(self):
        self.assertEqual(family_of('Run1Ban_best_v1_4-000'), 'Run1B')


class TestDsconfOrder(unittest.TestCase):
    def key(self, s):
        return parse_dsconf(s).sort_key()

    def test_letters_then_version_then_suffix(self):
        self.assertLess(self.key('MDC2025au_best_v1_3'), self.key('MDC2025au_best_v1_5'))
        self.assertLess(self.key('MDC2025au_best_v1_5'), self.key('MDC2025au_best_v1_5-001'))
        self.assertLess(self.key('MDC2025ar_best_v1_1'), self.key('MDC2025au_best_v1_1'))

    def test_revision_outranks_base(self):
        self.assertLess(self.key('Run1Bab_best_v1_2'), self.key('Run1Bab2_best_v1_2'))

    def test_two_letters_outrank_one(self):
        self.assertLess(self.key('MDC2020r'), self.key('MDC2020aa'))

    def test_own_series_ranks_below_any_letters(self):
        self.assertLess(self.key('MDC2025-003'), self.key('MDC2025ad_best_v1_3'))

    def test_purpose_not_in_order(self):
        # best vs perfect are siblings by purpose; the order key ignores it
        self.assertEqual(self.key('MDC2020ar_best_v1_3'), self.key('MDC2020ar_perfect_v1_3'))


class TestEpochFiles(unittest.TestCase):
    def _write(self, d, name, data):
        with open(os.path.join(d, name + '.json'), 'w') as f:
            json.dump(data, f)

    def test_load_minimal(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {
            'name': 'MDC2025au', 'purpose': 'Run-1 nominal', 'status': 'current',
            'roots': ['dig.mu2e.%.MDC2025au_%.art']})
        files = load_epoch_files(d)
        e = files['MDC2025au']
        self.assertEqual(e.family, 'MDC2025')
        self.assertEqual(e.pins['exclude'], [])
        self.assertEqual(e.pins['hold'], [])

    def test_name_must_match_filename_and_parse(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025an', 'purpose': '', 'status': 'current',
                                     'roots': ['dig.mu2e.%.MDC2025an_%.art']})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_bad_status_rejected(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025au', 'purpose': '', 'status': 'active',
                                     'roots': ['dig.mu2e.%.MDC2025au_%.art']})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_pin_without_reason_rejected(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025au', 'purpose': '', 'status': 'current',
                                     'roots': ['dig.mu2e.%.MDC2025au_%.art'],
                                     'pins': {'exclude': [{'dataset': 'mcs.mu2e.X.MDC2025au_best_v1_5.art'}]}})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_root_must_be_five_field_mu2e_pattern(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025au', 'purpose': '', 'status': 'current',
                                     'roots': ['dig.oksuzian.%.MDC2025au_%.art']})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_non_dict_pins_rejected(self):
        d = _tmpdir()
        self._write(d, 'MDC2025au', {'name': 'MDC2025au', 'purpose': '', 'status': 'current',
                                     'roots': ['dig.mu2e.%.MDC2025au_%.art'],
                                     'pins': ['oops']})
        with self.assertRaises(EpochFileError):
            load_epoch_files(d)

    def test_propose_from_sample_dsconf(self):
        data = propose_epoch('MDC2025au_best_v1_5')
        self.assertEqual(data['name'], 'MDC2025au')
        self.assertEqual(data['roots'], ['dig.mu2e.%.MDC2025au_%.art'])
        self.assertEqual(data['status'], 'current')
        self.assertEqual(data['purpose'], '')
        d = _tmpdir()
        path = write_epoch_file(d, data)
        self.assertTrue(path.endswith('MDC2025au.json'))
        self.assertIn('MDC2025au', load_epoch_files(d))

    def test_write_refuses_to_overwrite(self):
        d = _tmpdir()
        data = propose_epoch('MDC2025au_best_v1_5')
        write_epoch_file(d, data)
        with self.assertRaises(EpochFileError):
            write_epoch_file(d, data)

    def test_missing_dir_is_empty_not_error(self):
        self.assertEqual(load_epoch_files(os.path.join(_tmpdir(), 'nope')), {})


from utils.epochs.source import group_files_to_datasets, SamSource, DROP_TIERS


class FakeSource:
    """In-memory stand-in for SamSource. graph: {dataset: {'n': int, 'children': [...]}}.
    Parents are derived by inverting children. cnfs: {cnf_name: local_path}."""
    def __init__(self, graph, cnfs=None, files=None):
        self.graph = graph
        self.cnfs = cnfs or {}
        self.files = files or {}
        self.foreign = set()
        self._parents = {}
        for p, rec in graph.items():
            for c in rec.get('children', []):
                self._parents.setdefault(c, []).append(p)

    def dig_datasets(self, family):
        return {d: r['n'] for d, r in self.graph.items()
                if d.startswith('dig.mu2e.') and f'.{family}' in d}

    def children(self, dataset):
        return {c: self.graph[c]['n'] for c in self.graph.get(dataset, {}).get('children', [])
                if c.split('.')[0] not in DROP_TIERS}

    def parents(self, dataset):
        return {p: self.graph[p]['n'] for p in self._parents.get(dataset, [])}

    def nfiles(self, dataset):
        return self.graph[dataset]['n']

    def cnf_names(self):
        return sorted(self.cnfs)

    def local_path(self, filename):
        return self.cnfs.get(filename) or self.files.get(filename, '')

    def first_file(self, dataset):
        return self.graph[dataset].get('first', '')


class TestGroupFiles(unittest.TestCase):
    def test_groups_by_dataset_and_drops_log_cnf_etc(self):
        names = ['mcs.mu2e.A.MDC2025au_best_v1_5.001430_00000000.art',
                 'mcs.mu2e.A.MDC2025au_best_v1_5.001430_00000001.art',
                 'log.mu2e.A.MDC2025au_best_v1_5.001430_00000000-1.log',
                 'cnf.mu2e.A.MDC2025au_best_v1_5.0.tar',
                 'etc.mu2e.A.MDC2025au_best_v1_5.0.txt']
        groups, foreign = group_files_to_datasets(names)
        self.assertEqual(groups, {'mcs.mu2e.A.MDC2025au_best_v1_5.art': 2})
        self.assertEqual(foreign, set())

    def test_non_mu2e_owner_goes_to_foreign(self):
        groups, foreign = group_files_to_datasets(
            ['nts.oksuzian.A.MDC2025au_best_v1_5.001430_00000000.root'])
        self.assertEqual(groups, {})
        self.assertEqual(foreign, {'nts.oksuzian.A.MDC2025au_best_v1_5.root'})

    def test_unparseable_name_raises(self):
        with self.assertRaises(ValueError):
            group_files_to_datasets(['not-a-mu2e-name'])


class TestSamSourceQueries(unittest.TestCase):
    """SamSource only builds SAM query strings and groups; exercise it with
    injected wrapper functions, no network."""
    def test_children_query_and_grouping(self):
        calls = []
        def fake_list_files(q):
            calls.append(q)
            return ['nts.mu2e.A.MDC2025au_best_v1_5.001430_00000000.root',
                    'log.mu2e.A.MDC2025au_best_v1_5.001430_00000000-1.log']
        src = SamSource(list_files_fn=fake_list_files)
        out = src.children('mcs.mu2e.A.MDC2025au_best_v1_5.art')
        self.assertEqual(calls, ['ischildof: (dh.dataset mcs.mu2e.A.MDC2025au_best_v1_5.art)'])
        self.assertEqual(out, {'nts.mu2e.A.MDC2025au_best_v1_5.root': 1})

    def test_parents_query_and_grouping(self):
        calls = []
        def fake_list_files(q):
            calls.append(q)
            return ['dts.mu2e.A.MDC2025ap.001430_00000000.art',
                    'dts.mu2e.A.MDC2025ap.001430_00000001.art']
        src = SamSource(list_files_fn=fake_list_files)
        out = src.parents('mcs.mu2e.A.MDC2025au_best_v1_5.art')
        self.assertEqual(calls, ['isparentof: (dh.dataset mcs.mu2e.A.MDC2025au_best_v1_5.art)'])
        self.assertEqual(out, {'dts.mu2e.A.MDC2025ap.art': 2})

    def test_dig_datasets_filters_five_field_art_and_counts(self):
        calls = []
        def fake_defs(defname=None, user=None):
            calls.append(defname)
            return ['dig.mu2e.A.MDC2025au_best_v1_5.art',
                    'dig.mu2e.A.MDC2025au_best_v1_5.001430_00000007',   # per-index def
                    'dig.mu2e.B.MDC2025au_best_v1_3.art',
                    'dig.mu2e.C.MDC2025au_best_v1_3-recovery']
        counts = {'dh.dataset dig.mu2e.A.MDC2025au_best_v1_5.art': 10,
                  'dh.dataset dig.mu2e.B.MDC2025au_best_v1_3.art': 0}
        src = SamSource(definitions_fn=fake_defs, count_files_fn=lambda q: counts[q])
        self.assertEqual(src.dig_datasets('MDC2025'), {'dig.mu2e.A.MDC2025au_best_v1_5.art': 10})
        self.assertEqual(calls, ['dig.mu2e.%.MDC2025%.art'])

    def test_nfiles_and_first_file_queries(self):
        count_calls = []
        def fake_count_files(q):
            count_calls.append(q)
            return 42
        src = SamSource(count_files_fn=fake_count_files)
        self.assertEqual(src.nfiles('dts.mu2e.A.MDC2025ap.art'), 42)
        self.assertEqual(count_calls, ['dh.dataset dts.mu2e.A.MDC2025ap.art'])

        list_calls = []
        def fake_list_files(q):
            list_calls.append(q)
            return ['dts.mu2e.A.MDC2025ap.001430_00000000.art']
        src = SamSource(list_files_fn=fake_list_files)
        self.assertEqual(src.first_file('dts.mu2e.A.MDC2025ap.art'),
                         'dts.mu2e.A.MDC2025ap.001430_00000000.art')
        self.assertEqual(list_calls, ['dh.dataset dts.mu2e.A.MDC2025ap.art with limit 1'])

        src = SamSource(list_files_fn=lambda q: [])
        self.assertEqual(src.first_file('dts.mu2e.A.MDC2025ap.art'), '')

    def test_cnf_names_keeps_six_field_tar_only(self):
        calls = []
        def fake_defs(defname=None, user=None):
            calls.append(defname)
            return ['cnf.mu2e.A.B.0.tar', 'cnf.mu2e.A.B.0.tar',
                    'cnf.mu2e.A.B.tar', 'cnf.mu2e.A.B.0.txt']
        src = SamSource(definitions_fn=fake_defs)
        self.assertEqual(src.cnf_names(), ['cnf.mu2e.A.B.0.tar'])
        self.assertEqual(calls, ['cnf.mu2e.%'])

    def test_local_path_strips_dcache_prefix_and_appends_name(self):
        src = SamSource(locate_fn=lambda f: {'full_path': 'dcache:/pnfs/mu2e/persistent/x/y',
                                             'location_type': 'disk'})
        self.assertEqual(src.local_path('cnf.mu2e.A.B.0.tar'),
                         '/pnfs/mu2e/persistent/x/y/cnf.mu2e.A.B.0.tar')
        src = SamSource(locate_fn=lambda f: '')
        self.assertEqual(src.local_path('cnf.mu2e.A.B.0.tar'), '')
        src = SamSource(locate_fn=lambda f: 'dcache:/pnfs/x')
        with self.assertRaises(ValueError):
            src.local_path('cnf.mu2e.A.B.0.tar')


AU = 'MDC2025au_best_v1_5'
AU3 = 'MDC2025au_best_v1_3'
AR = 'MDC2025ar_best_v1_1'
AN = 'MDC2025an_best_v1_1'


def _epoch(name, roots=None, status='current', pins=None):
    from utils.epochs.dsconf import family_of
    p = {k: [] for k in ('exclude', 'hold', 'not_expected', 'order', 'notes')}
    p.update(pins or {})
    return EpochFile(name=name, family=family_of(name + '_best_v1_0'), purpose='',
                     status=status, roots=roots or [f'dig.mu2e.%.{name}_%.art'], pins=p)


def _small_graph():
    """dts@ap -> dig CE@au v1_5 -> mcs CE@au v1_5 -> nts (old) + nts -001
       Cat@ac -> dts@ap
       dig CE@an v1_1 -> mcs CE@ar v1_1 -> nts CE@ar (an dig re-reco'd under ar)"""
    return {
        'sim.mu2e.MuminusStopsCat.MDC2025ac.art': {'n': 1, 'children': ['dts.mu2e.CeEndpoint.MDC2025ap.art']},
        'dts.mu2e.CeEndpoint.MDC2025ap.art': {'n': 1000, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AU}.art']},
        f'dig.mu2e.CeEndpointOnSpill.{AU}.art': {'n': 100, 'children': [f'mcs.mu2e.CeEndpointOnSpill.{AU}.art',
                                                                        f'log.mu2e.CeEndpointOnSpill.{AU}.log']},
        f'log.mu2e.CeEndpointOnSpill.{AU}.log': {'n': 100, 'children': []},
        f'mcs.mu2e.CeEndpointOnSpill.{AU}.art': {'n': 100, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AU}.root',
                                                                        f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']},
        f'nts.mu2e.CeEndpointOnSpill.{AU}.root': {'n': 100, 'children': []},
        f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root': {'n': 100, 'children': []},
        f'dig.mu2e.CeEndpointOnSpill.{AN}.art': {'n': 50, 'children': [f'mcs.mu2e.CeEndpointOnSpill.{AR}.art']},
        f'mcs.mu2e.CeEndpointOnSpill.{AR}.art': {'n': 50, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AR}.root']},
        f'nts.mu2e.CeEndpointOnSpill.{AR}.root': {'n': 50, 'children': []},
    }


class TestRootMatches(unittest.TestCase):
    def test_percent_is_glob(self):
        self.assertTrue(root_matches('dig.mu2e.%.MDC2025au_%.art', f'dig.mu2e.CeEndpointOnSpill.{AU}.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2025au_%.art', f'dig.mu2e.CeEndpointOnSpill.{AN}.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2025au_%.art', f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'))


class TestBuildCatalog(unittest.TestCase):
    def setUp(self):
        self.src = FakeSource(_small_graph())
        self.epochs = {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}

    def test_members_are_closure_below_roots_without_log(self):
        cat = build_catalog(self.epochs, self.src, ['MDC2025'])
        names = set(cat.members)
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', names)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', names)
        self.assertIn(f'mcs.mu2e.CeEndpointOnSpill.{AR}.art', names)
        self.assertNotIn(f'log.mu2e.CeEndpointOnSpill.{AU}.log', names)
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'].epoch, 'MDC2025an')

    def test_parents_split_into_members_and_inputs(self):
        cat = build_catalog(self.epochs, self.src, ['MDC2025'])
        dig = cat.members[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertEqual(dig.parents, set())
        self.assertEqual(dig.inputs, {'dts.mu2e.CeEndpoint.MDC2025ap.art'})
        nts = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']
        self.assertEqual(nts.parents, {f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'})

    def test_inputs_walk_up_and_record_descendants(self):
        cat = build_catalog(self.epochs, self.src, ['MDC2025'])
        self.assertIn('sim.mu2e.MuminusStopsCat.MDC2025ac.art', cat.inputs)
        dts = cat.inputs['dts.mu2e.CeEndpoint.MDC2025ap.art']
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', dts['descendants'])
        cat_in = cat.inputs['sim.mu2e.MuminusStopsCat.MDC2025ac.art']
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', cat_in['descendants'])

    def test_dig_matching_no_root_is_unclaimed(self):
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au')}, self.src, ['MDC2025'])
        self.assertEqual(cat.unclaimed_digs, {'MDC2025': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']})

    def test_unparseable_dsconf_reported_not_dropped_silently(self):
        g = _small_graph()
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append('nts.mu2e.CeEndpointOnSpill.weird.root')
        g['nts.mu2e.CeEndpointOnSpill.weird.root'] = {'n': 3, 'children': []}
        cat = build_catalog(self.epochs, FakeSource(g), ['MDC2025'])
        self.assertEqual([n for n, _ in cat.unparseable], ['nts.mu2e.CeEndpointOnSpill.weird.root'])
        self.assertNotIn('nts.mu2e.CeEndpointOnSpill.weird.root', cat.members)

    def test_walk_up_unparseable_parent_reported_and_excluded_from_inputs(self):
        # A dig-of-dig-adjacent parent whose dsconf does not parse must be
        # reported in cat.unparseable and never turn into a cat.inputs
        # entry or a member's `inputs` edge (controller ruling amending
        # this task's brief; a later task walks every cat.inputs key
        # through parse_dsconf to compute the retire list, and one badly
        # named ancestor must not crash the whole report).
        g = _small_graph()
        g['dts.mu2e.X.weird.art'] = {'n': 1, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AU}.art']}
        cat = build_catalog(self.epochs, FakeSource(g), ['MDC2025'])
        self.assertEqual([n for n, _ in cat.unparseable], ['dts.mu2e.X.weird.art'])
        self.assertNotIn('dts.mu2e.X.weird.art', cat.inputs)
        dig = cat.members[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertNotIn('dts.mu2e.X.weird.art', dig.inputs)

    def test_input_that_is_also_a_member_is_not_an_input(self):
        # dig...AU's own upward walk records dts.mu2e.CeEndpoint.MDC2025ap.art
        # as an input first (AU sorts before Zed). A later-sorted dig
        # ("Zed", processed after AU) then walks DOWN through that same
        # dataset, making it a member. Member.inputs reconciliation alone
        # is not enough: cat.inputs must drop the stale key too, or the
        # retire report (a later task, which walks cat.inputs keys) would
        # misreport a live member as retirable.
        g = _small_graph()
        zed = f'dig.mu2e.Zed.{AU}.art'
        g[zed] = {'n': 5, 'children': ['dts.mu2e.CeEndpoint.MDC2025ap.art']}
        cat = build_catalog(self.epochs, FakeSource(g), ['MDC2025'])
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025ap.art', cat.members)
        self.assertNotIn('dts.mu2e.CeEndpoint.MDC2025ap.art', cat.inputs)
        dig_au = cat.members[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025ap.art', dig_au.parents)
        self.assertNotIn('dts.mu2e.CeEndpoint.MDC2025ap.art', dig_au.inputs)

    def test_pins_mark_excluded_and_held(self):
        e = _epoch('MDC2025au', pins={
            'exclude': [{'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'calo bug'}],
            'hold': [{'dataset': f'mcs.mu2e.CeEndpointOnSpill.{AU}.art', 'reason': 'paper 2026'}]})
        cat = build_catalog({'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')}, self.src, ['MDC2025'])
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].excluded, 'calo bug')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].hold, 'paper 2026')

    def test_frozen_epoch_holds_every_member(self):
        e = _epoch('MDC2025an', status='frozen')
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e}, self.src, ['MDC2025'])
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AR}.root'].hold, 'epoch MDC2025an is frozen')


if __name__ == '__main__':
    unittest.main()
