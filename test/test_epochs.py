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
from utils.epochs.status import assign_status, group_key, groups, STATUSES


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

    def test_three_campaign_letters_is_refused_not_reinterpreted(self):
        # M9: the non-greedy family would quietly re-split Run1Babc as
        # family Run1Bab + letters c, giving it a family of its own where
        # it can never supersede Run1Bab. Silent, which is the opposite of
        # this module's stated stance.
        with self.assertRaises(DsconfParseError):
            parse_dsconf('Run1Babc')
        with self.assertRaises(DsconfParseError):
            parse_dsconf('MDC2025abc_best_v1_0')
        # two letters plus a revision digit still parse
        self.assertEqual(parse_dsconf('Run1Bab2').letters, 'ab')

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
        self.assertEqual(data['roots'], ['dig.mu2e.%.MDC2025au.art',
                                         'dig.mu2e.%.MDC2025au_%.art',
                                         'dig.mu2e.%.MDC2025au-%.art'])
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


from utils.epochs.source import (group_files_to_datasets, SamSource, DROP_TIERS,
                                 PARENT_KEEP_TIERS)


class FakeSource:
    """In-memory stand-in for SamSource. graph: {dataset: {'n': int, 'children': [...]}}.
    Parents are derived by inverting children. cnfs: {cnf_name: local_path}."""
    def __init__(self, graph, cnfs=None, files=None):
        self.graph = graph
        self.cnfs = cnfs or {}
        self.files = files or {}
        self.foreign = set()
        self.unparseable_defs = []
        self._parents = {}
        for p, rec in graph.items():
            for c in rec.get('children', []):
                self._parents.setdefault(c, []).append(p)
        self.calls = {'children': [], 'parents': []}

    def dig_families(self):
        return {parse_dsconf(d.split('.')[3]).family for d in self.graph if d.startswith('dig.mu2e.')}

    def dig_datasets(self, family):
        return {d: r['n'] for d, r in self.graph.items()
                if d.startswith('dig.mu2e.') and f'.{family}' in d}

    def children(self, dataset):
        self.calls['children'].append(dataset)
        return {c: self.graph[c]['n'] for c in self.graph.get(dataset, {}).get('children', [])
                if c.split('.')[0] not in DROP_TIERS}

    def parents(self, dataset):
        # SamSource.parents counts the PARENT FILES this child consumed,
        # which is not the parent dataset's file count; a node may carry
        # 'used' to model that gap (4907 files of a 5000-file pileup set).
        # It also applies EXACTLY SamSource.parents' tier policy: log/etc
        # dropped, cnf kept (PARENT_KEEP_TIERS). Diverging from it here is
        # what let two generation tests assert a route the real source
        # could not produce.
        self.calls['parents'].append(dataset)
        return {p: self.graph[p].get('used', self.graph[p]['n'])
                for p in self._parents.get(dataset, [])
                if p.split('.')[0] in PARENT_KEEP_TIERS
                or p.split('.')[0] not in DROP_TIERS}

    def nfiles(self, dataset):
        self.calls.setdefault('nfiles', []).append(dataset)
        return self.graph[dataset]['n']

    def cnf_names(self):
        return sorted(self.cnfs)

    def local_path(self, filename):
        return self.cnfs.get(filename) or self.files.get(filename, '')


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

    def test_parents_keeps_the_cnf_tarball_by_file_name(self):
        # ADR 0003: the cnf is a declared parent and is the authoritative
        # generation record, so the parents path keeps it -- under its
        # 6-field FILE name, which is what local_path/jobquery read. log
        # and etc stay dropped.
        def fake_list_files(q):
            return ['dts.mu2e.A.MDC2025ap.001430_00000000.art',
                    'cnf.mu2e.A.MDC2025au_best_v1_5.0.tar',
                    'log.mu2e.A.MDC2025au_best_v1_5.001430_00000000-1.log',
                    'etc.mu2e.A.MDC2025au_best_v1_5.0.txt']
        src = SamSource(list_files_fn=fake_list_files)
        self.assertEqual(src.parents('mcs.mu2e.A.MDC2025au_best_v1_5.art'),
                         {'dts.mu2e.A.MDC2025ap.art': 1,
                          'cnf.mu2e.A.MDC2025au_best_v1_5.0.tar': 1})
        # children keeps the default policy: no cnf
        self.assertEqual(src.children('dig.mu2e.A.MDC2025au_best_v1_5.art'),
                         {'dts.mu2e.A.MDC2025ap.art': 1})

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

    def test_dig_families_discovers_from_sam_and_reports_unparseable(self):
        calls = []
        def fake_defs(defname=None, user=None):
            calls.append(defname)
            return ['dig.mu2e.A.MDC2025au_best_v1_5.art',
                     'dig.mu2e.B.Run1Ban_best_v1_4-000.art',
                     'dig.mu2e.C.MDC2025-003.art',
                     'dig.mu2e.D.weird.art']
        src = SamSource(definitions_fn=fake_defs)
        self.assertEqual(src.dig_families(), {'MDC2025', 'Run1B'})
        self.assertEqual(calls, ['dig.mu2e.%.art'])
        self.assertEqual(src.unparseable_defs, ['dig.mu2e.D.weird.art'])

    def test_nfiles_query(self):
        count_calls = []
        def fake_count_files(q):
            count_calls.append(q)
            return 42
        src = SamSource(count_files_fn=fake_count_files)
        self.assertEqual(src.nfiles('dts.mu2e.A.MDC2025ap.art'), 42)
        self.assertEqual(count_calls, ['dh.dataset dts.mu2e.A.MDC2025ap.art'])

    def test_cnf_names_keeps_six_field_tar_only(self):
        calls = []
        def fake_list_files(q):
            calls.append(q)
            return ['cnf.mu2e.A.B.0.tar', 'cnf.mu2e.A.B.0.tar', 'cnf.mu2e.A.B.tar',
                    'cnf.mu2e.A.B.0.txt', 'cnf.mu2e.A.B.0.fcl']
        src = SamSource(list_files_fn=fake_list_files)
        self.assertEqual(src.cnf_names(), ['cnf.mu2e.A.B.0.tar'])
        self.assertEqual(calls, ["dh.dataset like 'cnf.mu2e.%.tar'"])

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


def _deep_graph():
    """_small_graph plus a 3-level chain above the superseded an dig:
       dig CE@an <- dts Deep1 <- sim Deep2 <- sim Deep3"""
    g = _small_graph()
    g['dts.mu2e.Deep1.MDC2025af.art'] = {
        'n': 10, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
    g['sim.mu2e.Deep2.MDC2025af.art'] = {'n': 10, 'children': ['dts.mu2e.Deep1.MDC2025af.art']}
    g['sim.mu2e.Deep3.MDC2025af.art'] = {'n': 10, 'children': ['sim.mu2e.Deep2.MDC2025af.art']}
    return g


def _v1_probe_graph():
    """verify-findings-2.md's V1 probe, verbatim:

       dig Chain@au (current)              <- P1 <- P2 <- MM <- NN
       dig CeEndpointOnSpill@an (superseded)      <- QQ <- NN

    At a cap of 3, MM is the live dig's cut and no walk ever expands it,
    so it has no parent edges to propagate along; NN is 2 hops above the
    superseded dig and looks fully explored."""
    g = _small_graph()
    g[f'dig.mu2e.Chain.{AU}.art'] = {'n': 10, 'children': []}
    g['dts.mu2e.P1.MDC2025af.art'] = {'n': 10, 'children': [f'dig.mu2e.Chain.{AU}.art']}
    g['dts.mu2e.P2.MDC2025af.art'] = {'n': 10, 'children': ['dts.mu2e.P1.MDC2025af.art']}
    g['dts.mu2e.MM.MDC2025af.art'] = {'n': 10, 'children': ['dts.mu2e.P2.MDC2025af.art']}
    g['dts.mu2e.QQ.MDC2025af.art'] = {
        'n': 10, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
    g['sim.mu2e.NN.MDC2025af.art'] = {'n': 10, 'children': ['dts.mu2e.MM.MDC2025af.art',
                                                            'dts.mu2e.QQ.MDC2025af.art']}
    return g


class TestRootMatches(unittest.TestCase):
    def test_percent_is_glob(self):
        self.assertTrue(root_matches('dig.mu2e.%.MDC2025au_%.art', f'dig.mu2e.CeEndpointOnSpill.{AU}.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2025au_%.art', f'dig.mu2e.CeEndpointOnSpill.{AN}.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2025au_%.art', f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'))

    def test_glob_metacharacters_in_a_root_are_literal(self):
        # M10: only % is a wildcard. fnmatch gave ?, [ and ] shell-glob
        # meaning in a hand-edited root, and a mis-parsed root silently
        # claims (or fails to claim) digs.
        self.assertFalse(root_matches('dig.mu2e.A?.MDC2025au_best_v1_5.art',
                                      'dig.mu2e.AB.MDC2025au_best_v1_5.art'))
        self.assertTrue(root_matches('dig.mu2e.A?.MDC2025au_best_v1_5.art',
                                     'dig.mu2e.A?.MDC2025au_best_v1_5.art'))
        self.assertFalse(root_matches('dig.mu2e.[AB].MDC2025au_best_v1_5.art',
                                      'dig.mu2e.A.MDC2025au_best_v1_5.art'))
        self.assertTrue(root_matches('dig.mu2e.%.MDC2025au_%.art',
                                     f'dig.mu2e.CeEndpointOnSpill.{AU}.art'))

    def test_bare_dsconf_root_claims_bare_dig_only(self):
        self.assertTrue(root_matches('dig.mu2e.%.MDC2020aq.art', 'dig.mu2e.X.MDC2020aq.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2020aq.art', 'dig.mu2e.X.MDC2020aq2_best_v1_3.art'))
        self.assertFalse(root_matches('dig.mu2e.%.MDC2020aq.art', 'dig.mu2e.X.MDC2020aq_best_v1_3.art'))


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

    def test_dig_already_a_member_is_not_rebuilt(self):
        # M8: the dig loop assigned cat.members[name] unconditionally, so a
        # dig already reached as another dig's child had its Member object
        # replaced and its accumulated parents/inputs thrown away.
        # _walk_down guards with cat.members.get(); the dig loop now does too.
        from unittest.mock import patch
        import utils.epochs.graph as graph_mod
        g = _small_graph()
        upper = f'dig.mu2e.Aupper.{AU}.art'   # sorts BEFORE the dig it feeds
        lower = f'dig.mu2e.CeEndpointOnSpill.{AU}.art'
        g[upper] = {'n': 7, 'children': [lower]}
        real, made = graph_mod._make_member, []
        def spy(name, nfiles, epoch, cat):
            made.append(name)
            return real(name, nfiles, epoch, cat)
        with patch.object(graph_mod, '_make_member', spy):
            cat = build_catalog(self.epochs, FakeSource(g), ['MDC2025'])
        self.assertEqual(made.count(lower), 1)
        self.assertIn(upper, cat.members[lower].parents)
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025ap.art', cat.members[lower].inputs)

    def test_dig_matching_no_root_is_unclaimed(self):
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au')}, self.src, ['MDC2025'])
        self.assertEqual(cat.unclaimed_digs, {'MDC2025': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']})

    def test_families_none_discovers_from_sam(self):
        # only the au epoch is loaded; the family MDC2025 IS present
        # (from au), so nothing is missing — the an dig is merely
        # unclaimed (no root matches it), a different, already-tested
        # condition.
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au')}, self.src, families=None)
        self.assertEqual(cat.missing_families, [])
        self.assertEqual(cat.unclaimed_digs, {'MDC2025': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']})

        # now add a family with digs but no epoch file at all: it must
        # be flagged as missing, and its digs still land in
        # unclaimed_digs so the gap is visible.
        g = _small_graph()
        g['dig.mu2e.X.Run1Ban_best_v1_4.art'] = {'n': 1, 'children': []}
        cat2 = build_catalog(self.epochs, FakeSource(g), families=None)
        self.assertEqual(cat2.missing_families, ['Run1B'])
        self.assertIn('dig.mu2e.X.Run1Ban_best_v1_4.art', cat2.unclaimed_digs['Run1B'])

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

    def test_input_nfiles_is_the_dataset_count_not_the_edge_count(self):
        # I3: the parentage query counts the parent FILES the child
        # consumed (4907 of 5000 for a mixing pileup set), and the retire
        # report's nfiles column is read as the size of the deletion. It
        # must come from count_files on the dataset, and it must cost one
        # query per distinct input however many digs reach it.
        g = _small_graph()
        g['dts.mu2e.CeEndpoint.MDC2025ap.art']['used'] = 907
        g['dts.mu2e.CeEndpoint.MDC2025ap.art']['children'].append(
            f'dig.mu2e.CeEndpointOnSpill.{AN}.art')
        src = FakeSource(g)
        cat = build_catalog(self.epochs, src, ['MDC2025'])
        self.assertEqual(cat.inputs['dts.mu2e.CeEndpoint.MDC2025ap.art']['nfiles'], 1000)
        self.assertEqual(src.calls['nfiles'].count('dts.mu2e.CeEndpoint.MDC2025ap.art'), 1)

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

    def test_lineage_queries_are_memoized_per_build(self):
        # a stops Cat shared by two dts, each feeding a claimed dig: the Cat's
        # parents() must be asked ONCE, not once per dts
        g = _small_graph()
        g['dts.mu2e.DIO.MDC2025ap.art'] = {'n': 10, 'children': [f'dig.mu2e.DIOtail.{AU}.art']}
        g[f'dig.mu2e.DIOtail.{AU}.art'] = {'n': 10, 'children': []}
        g['sim.mu2e.MuminusStopsCat.MDC2025ac.art']['children'].append('dts.mu2e.DIO.MDC2025ap.art')
        src = FakeSource(g)
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}, src, ['MDC2025'])
        self.assertEqual(src.calls['parents'].count('sim.mu2e.MuminusStopsCat.MDC2025ac.art'), 1)
        self.assertEqual(src.calls['parents'].count('dts.mu2e.CeEndpoint.MDC2025ap.art'), 1)
        for name in set(src.calls['children']):
            self.assertEqual(src.calls['children'].count(name), 1, name)
        # results unchanged
        self.assertIn('sim.mu2e.MuminusStopsCat.MDC2025ac.art', cat.inputs)
        self.assertEqual(cat.inputs['sim.mu2e.MuminusStopsCat.MDC2025ac.art']['descendants'],
                         {f'dig.mu2e.CeEndpointOnSpill.{AU}.art', f'dig.mu2e.DIOtail.{AU}.art'})

    def test_a_digs_own_root_claim_beats_the_epoch_it_was_walked_in_under(self):
        # NEW-3: M8's guard rightly stopped re-creating a dig that is
        # already a member, but it also changed which epoch wins -- a dig
        # reached as another dig's child kept the epoch _walk_down
        # inherited, and the disagreement was reported nowhere. For a dig
        # the root claim is the authoritative answer. A dig-of-dig across
        # epochs where the upper dig sorts first is the shape; if the
        # lower one were to keep the upper epoch it would lose a frozen
        # epoch's hold and become eligible for the retire list.
        g = _small_graph()
        g[f'dig.mu2e.AAupper.{AU}.art'] = {'n': 10, 'children': [f'dig.mu2e.BBlower.{AN}.art']}
        g[f'dig.mu2e.BBlower.{AN}.art'] = {'n': 10, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[f'dig.mu2e.BBlower.{AN}.art'].epoch, 'MDC2025an')
        self.assertTrue(any('BBlower' in line for line in cat.epoch_conflicts),
                        cat.epoch_conflicts)
        # the members below it still carry the inherited epoch, so this is
        # a catalog we refuse to propose deletions from
        with self.assertRaises(ValueError) as ctx:
            retire(cat)
        self.assertIn('root claim', str(ctx.exception))

    def test_visited_input_still_records_every_descendant(self):
        # dedup must not lose the descendants bookkeeping for the second dig
        g = _small_graph()
        g['dts.mu2e.CeEndpoint.MDC2025ap.art']['children'].append(f'dig.mu2e.CeEndpointOnSpill.{AN}.art')
        cat = build_catalog({'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}, FakeSource(g), ['MDC2025'])
        self.assertEqual(cat.inputs['dts.mu2e.CeEndpoint.MDC2025ap.art']['descendants'],
                         {f'dig.mu2e.CeEndpointOnSpill.{AU}.art', f'dig.mu2e.CeEndpointOnSpill.{AN}.art'})


def _cat(graph=None, epochs=None, input_depth=None):
    """`input_depth=None` is build_catalog's own default: walk upward to
    closure. A test that wants a cut passes an int explicitly."""
    src = FakeSource(graph or _small_graph())
    eps = epochs or {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}
    return assign_status(build_catalog(eps, src, ['MDC2025'], input_depth=input_depth))


class TestStatus(unittest.TestCase):
    def test_newest_sibling_current_older_superseded(self):
        cat = _cat()
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'current')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'superseded')

    def test_status_crosses_epochs_within_family(self):
        cat = _cat()
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'].status, 'superseded')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'current')
        # its dig at an is superseded by the au dig (same desc, purpose best)
        self.assertEqual(cat.members[f'dig.mu2e.CeEndpointOnSpill.{AN}.art'].status, 'superseded')

    def test_stale_when_parent_moved_on_and_no_replacement(self):
        g = _small_graph()
        # re-reco'd mcs at -001 with no nts yet: old nts become stale, not superseded
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'] = {'n': 100, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'].status, 'current')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'superseded')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'stale')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'superseded')

    def test_parent_not_in_group_key(self):
        # same desc/tier/purpose, different parents: newest still wins (decision 3)
        g = _small_graph()
        g[f'dig.mu2e.CeEndpointOnSpill.{AN}.art']['children'].append(f'mcs.mu2e.CeEndpointOnSpill.{AU3}.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU3}.art'] = {'n': 5, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU3}.art'].status, 'superseded')

    def test_purpose_makes_siblings_not_versions(self):
        g = _small_graph()
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append('mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art')
        g['mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art'] = {'n': 100, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members['mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art'].status, 'current')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'current')

    def test_excluded_member_never_competes_and_has_no_status(self):
        e = _epoch('MDC2025au', pins={'exclude': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', 'reason': 'bad build'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'excluded')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'current')

    def test_order_pin_overrides_winner(self):
        e = _epoch('MDC2025au', pins={'order': [
            {'tier': 'nts', 'desc': 'CeEndpointOnSpill', 'purpose': 'best',
             'winner': AU, 'reason': '-001 is a 40-file top-up'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'current')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'superseded')

    def test_hold_does_not_change_status(self):
        e = _epoch('MDC2025au', pins={'hold': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'paper'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        m = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root']
        self.assertEqual((m.status, m.hold), ('superseded', 'paper'))

    def test_own_series_is_superseded_by_a_lettered_sibling(self):
        # I5: an own-series MDC20xx-NNN name parses with purpose=None and
        # used to land in its own group, so it never competed and the
        # catalog published two current answers for one physics line.
        # wiki 5.3: it "ranks below any lettered sibling".
        g = _small_graph()
        own = 'nts.mu2e.CeEndpointOnSpill.MDC2025-001.root'
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(own)
        g[own] = {'n': 100, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[own].status, 'superseded')
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'].status, 'current')
        self.assertEqual(lookup(cat, own)['superseded_by'],
                         f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root')

    def test_own_series_without_a_lettered_sibling_stays_current(self):
        g = _small_graph()
        own = 'nts.mu2e.Legacy.MDC2025-001.root'
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(own)
        g[own] = {'n': 5, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[own].status, 'current')

    def test_best_and_perfect_stay_two_groups_each_with_a_current(self):
        # ranking the own-series name into every purpose group must NOT
        # collapse best and perfect: they are different products.
        g = _small_graph()
        perfect = 'mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art'
        own = 'mcs.mu2e.CeEndpointOnSpill.MDC2025-002.art'
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'] += [perfect, own]
        g[perfect] = {'n': 100, 'children': []}
        g[own] = {'n': 100, 'children': []}
        cat = _cat(g)
        self.assertEqual(cat.members[perfect].status, 'current')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'current')
        self.assertEqual(cat.members[own].status, 'superseded')   # loses in both groups
        keys = {k for k in groups(cat) if k[1] == 'mcs' and k[2] == 'CeEndpointOnSpill'}
        self.assertEqual(keys, {('MDC2025', 'mcs', 'CeEndpointOnSpill', 'best'),
                                ('MDC2025', 'mcs', 'CeEndpointOnSpill', 'perfect')})

    def test_order_pin_on_an_own_series_name_wins_every_group_it_competes_in(self):
        # NEW-5: an own-series member competes in EVERY purpose group of
        # its (family, tier, desc), and `win` was overwritten once per
        # group -- last write wins -- so an order pin naming it in one
        # purpose group made the answer depend on the insertion order of
        # the groups dict. Winning any group makes it current: that is
        # what the pin was placed to do, and keeping the dataset is the
        # fail-closed direction for a tool that emits a delete list.
        g = _small_graph()
        perfect = 'mcs.mu2e.CeEndpointOnSpill.MDC2025au_perfect_v1_5.art'
        own = 'mcs.mu2e.CeEndpointOnSpill.MDC2025-002.art'
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'] += [perfect, own]
        g[perfect] = {'n': 100, 'children': []}
        g[own] = {'n': 100, 'children': []}
        e = _epoch('MDC2025au', pins={'order': [
            {'tier': 'mcs', 'desc': 'CeEndpointOnSpill', 'purpose': 'best',
             'winner': 'MDC2025-002', 'reason': 'the paper ran on the legacy series'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertEqual(cat.pin_problems, [])
        # it loses the perfect group and wins the pinned best group
        self.assertEqual(cat.members[own].status, 'current')
        self.assertEqual(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'].status, 'superseded')
        self.assertEqual(cat.members[perfect].status, 'current')
        self.assertNotIn(own, {x['dataset'] for x in retire(cat)})

    def test_order_pin_matching_no_group_makes_retire_refuse(self):
        # NEW-4: C2 made an unmatched hold/exclude refuse; an order pin
        # still evaporated. I5 made that reachable for a LEGITIMATE pin --
        # a (..., purpose: null) group stops existing the moment a
        # lettered sibling appears -- so the inversion an operator
        # corrected on purpose silently returns and the dataset it
        # protected is proposed for deletion.
        g = _small_graph()
        own = 'nts.mu2e.CeEndpointOnSpill.MDC2025-001.root'
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(own)
        g[own] = {'n': 100, 'children': []}
        e = _epoch('MDC2025au', pins={'order': [
            {'tier': 'nts', 'desc': 'CeEndpointOnSpill', 'purpose': None,
             'winner': 'MDC2025-001', 'reason': 'paper 2026'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertTrue(any('no member competes in' in line for line in cat.pin_problems),
                        cat.pin_problems)
        with self.assertRaises(ValueError) as ctx:
            retire(cat)
        self.assertIn('order pin', str(ctx.exception))

    def test_order_pins_with_and_without_a_purpose_can_coexist(self):
        # the group key carries purpose=None for an own-series pin, so the
        # pin bookkeeping must never compare None with a str: two pins on
        # one (family, tier, desc) differing only in purpose sort against
        # each other.
        g = _small_graph()
        own = 'nts.mu2e.CeEndpointOnSpill.MDC2025-001.root'
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(own)
        g[own] = {'n': 100, 'children': []}
        e = _epoch('MDC2025au', pins={'order': [
            {'tier': 'nts', 'desc': 'CeEndpointOnSpill', 'purpose': None,
             'winner': 'MDC2025-001', 'reason': 'legacy series'},
            {'tier': 'nts', 'desc': 'CeEndpointOnSpill', 'purpose': 'best',
             'winner': AU, 'reason': 'the -001 reco is not the one'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        # the purpose=None pin lands nowhere (lettered siblings exist) and
        # says so; the purpose=best pin applies
        self.assertTrue(any('no member competes in' in line for line in cat.pin_problems),
                        cat.pin_problems)
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].status, 'current')

    def test_two_epochs_pinning_one_group_is_reported(self):
        # the same class: the second pin lands nowhere because the first
        # already owns the group.
        a = _epoch('MDC2025au', pins={'order': [
            {'tier': 'nts', 'desc': 'CeEndpointOnSpill', 'purpose': 'best',
             'winner': AU, 'reason': 'a'}]})
        b = _epoch('MDC2025an', pins={'order': [
            {'tier': 'nts', 'desc': 'CeEndpointOnSpill', 'purpose': 'best',
             'winner': f'{AU}-001', 'reason': 'b'}]})
        cat = _cat(epochs={'MDC2025au': a, 'MDC2025an': b})
        self.assertTrue(any('already pinned' in line for line in cat.pin_problems),
                        cat.pin_problems)

    def test_group_key_shape(self):
        cat = _cat()
        m = cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertEqual(group_key(m), ('MDC2025', 'mcs', 'CeEndpointOnSpill', 'best'))
        self.assertIn(('MDC2025', 'nts', 'CeEndpointOnSpill', 'best'), groups(cat))


from utils.epochs.reports import (gaps, retire, purge_lines, lookup, EXPECTED_TIERS,
                                  input_retirement_refusal, truncated_inputs)


class TestGaps(unittest.TestCase):
    def test_missing_tier_and_stale_reported(self):
        g = _small_graph()
        g[f'dig.mu2e.DIOtail.{AU}.art'] = {'n': 10, 'children': []}      # dig with no mcs
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'] = {'n': 100, 'children': []}   # re-reco, no nts yet
        cat = _cat(g)
        out = gaps(cat)
        kinds = {(x['kind'], x['desc'], x['tier']) for x in out}
        self.assertIn(('missing', 'DIOtail', 'mcs'), kinds)
        self.assertIn(('missing', 'DIOtail', 'nts'), kinds)
        self.assertIn(('stale', 'CeEndpointOnSpill', 'nts'), kinds)

    def test_not_expected_pin_silences_missing(self):
        g = _small_graph()
        g[f'dig.mu2e.ensembleMDS3b.{AU}.art'] = {'n': 10, 'children': []}
        e = _epoch('MDC2025au', pins={'not_expected': [
            {'desc': 'ensembleMDS3b', 'tiers': ['mcs', 'nts'], 'reason': 'kept at dig'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertFalse([x for x in gaps(cat) if x['desc'] == 'ensembleMDS3b'])

    def test_not_expected_pin_naming_an_absent_desc_makes_retire_refuse(self):
        # NEW-4: a typo'd desc suppressed nothing and was reported
        # nowhere, so the gap it was written to explain came back
        # unexplained and the pin read as "no pin".
        g = _small_graph()
        g[f'dig.mu2e.ensembleMDS3b.{AU}.art'] = {'n': 10, 'children': []}
        e = _epoch('MDC2025au', pins={'not_expected': [
            {'desc': 'ensembleMDS3bb', 'tiers': ['mcs', 'nts'], 'reason': 'typo'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertTrue(any('ensembleMDS3bb' in line for line in cat.pin_problems),
                        cat.pin_problems)
        with self.assertRaises(ValueError):
            retire(cat)

    def test_not_expected_pin_naming_an_unreported_tier_is_refused_at_load(self):
        d = _tmpdir()
        with open(os.path.join(d, 'MDC2025au.json'), 'w') as f:
            json.dump({'name': 'MDC2025au', 'purpose': '', 'status': 'current',
                       'roots': ['dig.mu2e.%.MDC2025au_%.art'],
                       'pins': {'not_expected': [
                           {'desc': 'X', 'tiers': ['nst'], 'reason': 'typo'}]}}, f)
        with self.assertRaises(EpochFileError) as ctx:
            load_epoch_files(d)
        self.assertIn('nst', str(ctx.exception))

    def test_frozen_epoch_reports_no_missing(self):
        g = _small_graph()
        g[f'dig.mu2e.DIOtail.{AN}.art'] = {'n': 10, 'children': []}
        e = _epoch('MDC2025an', status='frozen')
        cat = _cat(g, {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e})
        self.assertFalse([x for x in gaps(cat) if x['desc'] == 'DIOtail'])

    def test_frozen_epoch_reports_no_stale_rows(self):
        # wiki 4: frozen = a hold on every member, no gap report. Only the
        # missing branch was gated on epoch status.
        g = _small_graph()
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(
            f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'] = {'n': 100, 'children': []}
        live = _cat(g)
        self.assertTrue([x for x in gaps(live) if x['kind'] == 'stale' and x['epoch'] == 'MDC2025au'])
        frozen = _cat(g, {'MDC2025au': _epoch('MDC2025au', status='frozen'),
                          'MDC2025an': _epoch('MDC2025an')})
        self.assertFalse([x for x in gaps(frozen) if x['kind'] == 'stale' and x['epoch'] == 'MDC2025au'])

    def test_retired_epoch_members_do_not_hide_a_gap(self):
        # M1: a retired epoch's members are all delete candidates, so they
        # must not read as "this tier is covered".
        g = _small_graph()
        g[f'dig.mu2e.Shared.{AU}.art'] = {'n': 10, 'children': []}
        g[f'dig.mu2e.Shared.{AN}.art'] = {'n': 10, 'children': [f'mcs.mu2e.Shared.{AN}.art']}
        g[f'mcs.mu2e.Shared.{AN}.art'] = {'n': 10, 'children': [f'nts.mu2e.Shared.{AN}.root']}
        g[f'nts.mu2e.Shared.{AN}.root'] = {'n': 10, 'children': []}
        live = _cat(g)
        self.assertFalse([x for x in gaps(live) if x['kind'] == 'missing' and x['desc'] == 'Shared'])
        retired = _cat(g, {'MDC2025au': _epoch('MDC2025au'),
                           'MDC2025an': _epoch('MDC2025an', status='retired')})
        kinds = {(x['kind'], x['desc'], x['tier']) for x in gaps(retired)}
        self.assertIn(('missing', 'Shared', 'mcs'), kinds)
        self.assertIn(('missing', 'Shared', 'nts'), kinds)

    def test_nts_expected_only_where_mcs_exists_in_family(self):
        # a superseded mcs still counts as "reached mcs"; nts missing is judged
        # against the CURRENT mcs desc, so a stale-free family reports nothing
        cat = _cat()
        self.assertEqual([x for x in gaps(cat) if x['kind'] == 'missing'], [])


class TestRetire(unittest.TestCase):
    def test_superseded_not_held_listed_with_children(self):
        cat = _cat()
        names = {x['dataset']: x for x in retire(cat)}
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', names)
        mcs_ar = names[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art']
        self.assertEqual(mcs_ar['children'], [f'nts.mu2e.CeEndpointOnSpill.{AR}.root'])
        self.assertIn('superseded by', mcs_ar['reason'])
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', names)

    def test_held_member_kept_off_the_list(self):
        e = _epoch('MDC2025au', pins={'hold': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'paper'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', {x['dataset'] for x in retire(cat)})

    def test_retired_epoch_lists_every_member(self):
        e = _epoch('MDC2025an', status='retired')
        cat = _cat(epochs={'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e})
        names = {x['dataset']: x['reason'] for x in retire(cat)}
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AN}.art', names)
        self.assertIn('epoch MDC2025an is retired', names[f'dig.mu2e.CeEndpointOnSpill.{AN}.art'])

    def test_retired_epoch_reason_flags_a_still_current_member(self):
        # M1: the reason must say the member is still its group's answer,
        # or a too-early `retired` flip reads as a routine cleanup line.
        g = _small_graph()
        g[f'dig.mu2e.Solo.{AN}.art'] = {'n': 10, 'children': []}
        cat = _cat(g, {'MDC2025au': _epoch('MDC2025au'),
                       'MDC2025an': _epoch('MDC2025an', status='retired')})
        entry = [x for x in retire(cat) if x['dataset'] == f'dig.mu2e.Solo.{AN}.art'][0]
        self.assertIn('still the current member', entry['reason'])

    def test_input_with_no_live_descendant_listed_with_letters_guard(self):
        g = _small_graph()
        # an old dts@af whose only dig is superseded
        g['dts.mu2e.CeEndpoint.MDC2025af.art'] = {'n': 500, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        # a fresh dts@av with no dig yet: newer letters than any epoch -> never listed
        g['dts.mu2e.Fresh.MDC2025av.art'] = {'n': 5, 'children': []}
        cat = _cat(g)
        out = {x['dataset']: x for x in retire(cat)}
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025af.art', out)
        self.assertEqual(out['dts.mu2e.CeEndpoint.MDC2025af.art']['kind'], 'input')
        self.assertNotIn('dts.mu2e.CeEndpoint.MDC2025ap.art', out)     # feeds a current dig
        self.assertNotIn('dts.mu2e.Fresh.MDC2025av.art', out)

    def test_input_above_a_superseded_dig_with_a_stale_member_below_is_kept(self):
        # CRITICAL 1: `descendants` holds only the DIG each upward walk
        # started from. An input whose dig is superseded but whose mcs/nts
        # below that dig are still stale must NOT be proposed for deletion
        # (CONTEXT.md / wiki 5.6: retirable when no current or stale
        # MEMBER descends from it). Liveness has to be transitive.
        g = _small_graph()
        # Solo@an: dig superseded by a newer Solo dig at au that has no mcs
        # yet, so the mcs/nts under the an dig are the winners of their own
        # groups with a superseded parent == stale.
        g['dts.mu2e.Solo.MDC2025af.art'] = {'n': 500, 'children': [f'dig.mu2e.Solo.{AN}.art']}
        g[f'dig.mu2e.Solo.{AN}.art'] = {'n': 10, 'children': [f'mcs.mu2e.Solo.{AN}.art']}
        g[f'mcs.mu2e.Solo.{AN}.art'] = {'n': 10, 'children': [f'nts.mu2e.Solo.{AN}.root']}
        g[f'nts.mu2e.Solo.{AN}.root'] = {'n': 10, 'children': []}
        g[f'dig.mu2e.Solo.{AU}.art'] = {'n': 10, 'children': []}
        # mirror case: an input whose whole downward closure is superseded
        # IS still listed, so this test cannot pass by disabling the branch
        g['dts.mu2e.CeEndpoint.MDC2025af.art'] = {'n': 500,
                                                  'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        cat = _cat(g)
        self.assertEqual(cat.members[f'dig.mu2e.Solo.{AN}.art'].status, 'superseded')
        self.assertEqual(cat.members[f'nts.mu2e.Solo.{AN}.root'].status, 'stale')
        listed = {x['dataset'] for x in retire(cat)}
        self.assertNotIn('dts.mu2e.Solo.MDC2025af.art', listed)
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025af.art', listed)

    def test_hold_pin_on_an_input_keeps_it_off_the_list(self):
        # CRITICAL 2: an input could not be held at all before 2026-09-04 —
        # _apply_pins resolved every pin through cat.members only, so a
        # hold naming a dts was accepted, applied to nothing, and reported
        # nowhere. CONTEXT.md: an input is retirable when nothing current
        # or stale descends from it AND IT IS NOT HELD.
        g = _small_graph()
        g['dts.mu2e.CeEndpoint.MDC2025af.art'] = {
            'n': 500, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        e = _epoch('MDC2025an', pins={'hold': [
            {'dataset': 'dts.mu2e.CeEndpoint.MDC2025af.art', 'reason': 'needed for the -KL remake'}]})
        cat = _cat(g, {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e})
        self.assertEqual(cat.pin_problems, [])
        self.assertEqual(cat.inputs['dts.mu2e.CeEndpoint.MDC2025af.art']['hold'],
                         'needed for the -KL remake')
        self.assertNotIn('dts.mu2e.CeEndpoint.MDC2025af.art', {x['dataset'] for x in retire(cat)})

    def test_pin_matching_nothing_makes_retire_refuse(self):
        # a typo'd dataset name in a hold must never read as "no protection
        # requested": fail closed, exactly as on an incomplete catalog.
        e = _epoch('MDC2025au', pins={'hold': [
            {'dataset': f'nts.mu2e.Typo.{AU}.root', 'reason': 'paper 2026'}]})
        cat = _cat(epochs={'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertTrue(any('Typo' in line for line in cat.pin_problems), cat.pin_problems)
        with self.assertRaises(ValueError) as ctx:
            retire(cat)
        self.assertIn('Typo', str(ctx.exception))

    def test_cross_epoch_pin_is_reported_not_applied(self):
        # M7: one epoch's file must not silently exclude another epoch's
        # member. Report it; do not apply it.
        e = _epoch('MDC2025an', pins={'exclude': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'wrong epoch'}]})
        cat = _cat(epochs={'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e})
        self.assertEqual(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'].excluded, '')
        self.assertTrue(any('a member of epoch MDC2025au' in line for line in cat.pin_problems),
                        cat.pin_problems)

    def test_input_pin_from_an_unrelated_epoch_is_reported(self):
        # the same ownership check for an input: it belongs to the epoch
        # whose digs it feeds, and nothing else may pin it.
        g = _small_graph()
        g['dts.mu2e.CeEndpoint.MDC2025af.art'] = {
            'n': 500, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        e = _epoch('MDC2025au', pins={'hold': [
            {'dataset': 'dts.mu2e.CeEndpoint.MDC2025af.art', 'reason': 'mine, honest'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertEqual(cat.inputs['dts.mu2e.CeEndpoint.MDC2025af.art']['hold'], '')
        self.assertTrue(any('feeds no dig of that epoch' in line for line in cat.pin_problems),
                        cat.pin_problems)

    def test_an_excluded_member_keeps_its_input_off_the_list(self):
        # NEW-6: retire() refuses to list an excluded member, but the
        # member conferred no liveness, so the tool proposed deleting the
        # sole input of a dataset it simultaneously refused to delete.
        # We are keeping the dataset, so we keep what made it.
        g = _small_graph()
        g['dts.mu2e.Kept.MDC2025af.art'] = {'n': 500, 'children': [f'dig.mu2e.Solo.{AN}.art']}
        g[f'dig.mu2e.Solo.{AN}.art'] = {'n': 10, 'children': []}
        # mirror: an input whose dig is merely superseded is still listed,
        # so this cannot pass by holding everything back
        g['dts.mu2e.Gone.MDC2025af.art'] = {
            'n': 500, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        e = _epoch('MDC2025an', pins={'exclude': [
            {'dataset': f'dig.mu2e.Solo.{AN}.art', 'reason': 'ensemble input, keep'}]})
        cat = _cat(g, {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': e})
        self.assertEqual(cat.members[f'dig.mu2e.Solo.{AN}.art'].status, 'excluded')
        listed = {x['dataset'] for x in retire(cat)}
        self.assertNotIn('dts.mu2e.Kept.MDC2025af.art', listed)
        self.assertIn('dts.mu2e.Gone.MDC2025af.art', listed)

    def test_a_frozen_epoch_holds_its_inputs_too(self):
        # NEW-7: freezing an epoch stamped a hold on every MEMBER only, so
        # the dts feeding a frozen epoch's (superseded) digs was still a
        # retire candidate -- not what an operator who freezes Run1Bah
        # means. An input feeding a CURRENT epoch's superseded dig is
        # still listed: the hold follows the freeze, not the staleness.
        g = _small_graph()
        g['dts.mu2e.Frozen.MDC2025af.art'] = {
            'n': 500, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        cat = _cat(g, {'MDC2025au': _epoch('MDC2025au'),
                       'MDC2025an': _epoch('MDC2025an', status='frozen')})
        self.assertEqual(cat.inputs['dts.mu2e.Frozen.MDC2025af.art']['hold'],
                         'epoch MDC2025an is frozen')
        self.assertNotIn('dts.mu2e.Frozen.MDC2025af.art', {x['dataset'] for x in retire(cat)})
        live = _cat(g)          # same graph, MDC2025an current: listed again
        self.assertEqual(live.inputs['dts.mu2e.Frozen.MDC2025af.art']['hold'], '')
        self.assertIn('dts.mu2e.Frozen.MDC2025af.art', {x['dataset'] for x in retire(live)})

    def test_walking_to_closure_is_the_default_and_truncates_nothing(self):
        # 2026-09-04 structural change: the upward walk runs to the top of
        # the real DAG unless an operator caps it. With no cut there is no
        # truncation to detect, which is the point -- V1, NEW-1 and C1 were
        # three different shapes of "which parts of a knowingly incomplete
        # input graph are safe to delete", a question that now is not asked.
        epochs = {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}
        # build_catalog's OWN default, passed nothing: no cap
        cat = assign_status(build_catalog(epochs, FakeSource(_deep_graph()), ['MDC2025']))
        self.assertEqual(cat.input_depth, None)
        self.assertEqual(truncated_inputs(cat), [])
        self.assertEqual(input_retirement_refusal(cat), '')
        listed = {x['dataset'] for x in retire(cat)}
        for n in ('dts.mu2e.Deep1.MDC2025af.art', 'sim.mu2e.Deep2.MDC2025af.art',
                  'sim.mu2e.Deep3.MDC2025af.art'):
            self.assertIn(n, listed)

    def test_a_cap_that_truncates_anything_refuses_the_entire_input_section(self):
        # The rule that replaces three rounds of per-input detectors: a
        # partial input graph yields NO input retirement proposals at all.
        # Deep2 was explored and would have been listed under the old
        # per-input rule -- but the walk that stopped at Deep3 never asked
        # what else reaches the region above it, and which OTHER inputs
        # that makes unsafe cannot be read off the marks (V1). Members are
        # unaffected: they come from the downward closure, which the cap
        # never touches.
        cat = _cat(_deep_graph(), input_depth=3)
        self.assertTrue(cat.inputs['sim.mu2e.Deep3.MDC2025af.art']['truncated'])
        self.assertFalse(cat.inputs['sim.mu2e.Deep2.MDC2025af.art']['truncated'])
        rows = retire(cat)
        self.assertEqual([r for r in rows if r['kind'] == 'input'], [])
        self.assertTrue([r for r in rows if r['kind'] == 'member'])
        msg = input_retirement_refusal(cat)
        self.assertIn('--input-depth 3', msg)
        self.assertIn('NO input is proposed', msg)

    def test_raising_input_depth_clears_the_frontier(self):
        # the boundary is a decision the operator can take, and taking it
        # is what puts the input section back.
        cat = _cat(_deep_graph(), input_depth=4)
        self.assertFalse(cat.inputs['sim.mu2e.Deep3.MDC2025af.art']['truncated'])
        self.assertEqual(input_retirement_refusal(cat), '')
        self.assertIn('sim.mu2e.Deep3.MDC2025af.art', {x['dataset'] for x in retire(cat)})

    def test_input_above_a_node_another_dig_already_expanded_is_held_back(self):
        # NEW-1: truncation is a property of the CUT, not of the node.
        # `explored` used to live on the shared input record, so a node cut
        # off for THIS dig but already expanded by an earlier dig's walk was
        # not marked -- and neither was anything above it, whose
        # `descendants` therefore name only the superseded dig.
        #
        #   dig Chain@au (current)     <- P1 <- P2 <- MM <- NN
        #   dig Chain@an (superseded)  <- MM <- NN
        #
        # The an walk (processed first, alphabetically) expands MM and NN;
        # the au walk is cut at MM, 3 hops up. NN is 4 hops above the live
        # dig and is never reached from it. Rewritten 2026-09-04: the cap is
        # now explicit (the default walks to closure), and the assertion is
        # the class-wide rule -- no input at all -- rather than "NN in
        # particular is missing from the list".
        g = _small_graph()
        g[f'dig.mu2e.Chain.{AU}.art'] = {'n': 10, 'children': []}
        g[f'dig.mu2e.Chain.{AN}.art'] = {'n': 10, 'children': []}
        g['dts.mu2e.P1.MDC2025af.art'] = {'n': 10, 'children': [f'dig.mu2e.Chain.{AU}.art']}
        g['dts.mu2e.P2.MDC2025af.art'] = {'n': 10, 'children': ['dts.mu2e.P1.MDC2025af.art']}
        g['dts.mu2e.MM.MDC2025af.art'] = {'n': 10, 'children': ['dts.mu2e.P2.MDC2025af.art',
                                                                f'dig.mu2e.Chain.{AN}.art']}
        g['sim.mu2e.NN.MDC2025af.art'] = {'n': 10, 'children': ['dts.mu2e.MM.MDC2025af.art']}
        cat = _cat(g, input_depth=3)
        self.assertEqual(cat.members[f'dig.mu2e.Chain.{AU}.art'].status, 'current')
        self.assertEqual(cat.members[f'dig.mu2e.Chain.{AN}.art'].status, 'superseded')
        # MM is where the live dig's walk stopped; NN sits above the cut and
        # is marked by the upward propagation, not by the cut itself.
        self.assertTrue(cat.inputs['dts.mu2e.MM.MDC2025af.art']['truncated'])
        self.assertTrue(cat.inputs['sim.mu2e.NN.MDC2025af.art']['truncated'])
        self.assertEqual([r for r in retire(cat) if r['kind'] == 'input'], [])
        # ... and with the default walk NN is reached from the live dig, so
        # it is correctly kept off the list for the real reason.
        closed = _cat(g)
        self.assertEqual(truncated_inputs(closed), [])
        self.assertNotIn('sim.mu2e.NN.MDC2025af.art', {x['dataset'] for x in retire(closed)})

    def test_v1_probe_a_live_digs_input_reached_only_above_a_cut_is_never_listed(self):
        # The V1 probe verbatim (verify-findings-2.md): MM is 3 hops above
        # the live dig and reached by no other walk, so its `parents` set is
        # empty and the fixpoint stopped there; NN, its parent, was recorded
        # 2 hops above the SUPERSEDED dig and looked fully explored. Under
        # the per-input rule NN was proposed for DELETE while a current dig
        # transitively consumed it. Both the default closure walk and the
        # whole-section refusal kill it.
        g = _v1_probe_graph()
        for depth in (None, 3):
            cat = _cat(g, input_depth=depth)
            listed = {x['dataset'] for x in retire(cat)}
            self.assertNotIn('sim.mu2e.NN.MDC2025af.art', listed)

    def test_no_input_is_ever_listed_from_a_capped_catalog_that_truncated_anything(self):
        # The invariant itself, not one more scenario. Three previous fixes
        # each passed their own scenario test and the next reviewer found a
        # live scenario they did not cover, so this asserts the class:
        # for ANY catalog built with a cap, `truncated` non-empty =>
        # `retire()` returns zero input rows.
        import random
        rnd = random.Random(20260904)
        epochs = {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}
        saw_truncation = saw_inputs_listed = 0
        for trial in range(40):
            g = _small_graph()
            made = []
            for c in range(3):
                # chain 0 hangs off the SUPERSEDED dig, so its nodes are
                # genuinely retirable and a per-input rule would list the
                # shallow ones while the deep ones are cut
                anchor = (f'dig.mu2e.CeEndpointOnSpill.{AN}.art' if c == 0
                          else rnd.choice([f'dig.mu2e.CeEndpointOnSpill.{AU}.art',
                                           f'dig.mu2e.CeEndpointOnSpill.{AN}.art']))
                prev = anchor
                for lvl in range(rnd.randint(1, 5)):
                    name = f'dts.mu2e.T{trial}C{c}L{lvl}.MDC2025af.art'
                    kids = [prev]
                    if made and rnd.random() < 0.3:
                        kids.append(rnd.choice(made))   # diamond: a shared ancestor
                    g[name] = {'n': 10, 'children': kids}
                    made.append(name)
                    prev = name
            for depth in (1, 2, 3, 4, None):
                cat = assign_status(build_catalog(epochs, FakeSource(g), ['MDC2025'],
                                                  input_depth=depth))
                rows = retire(cat)
                inputs = [r for r in rows if r['kind'] == 'input']
                if truncated_inputs(cat):
                    self.assertNotEqual(depth, None)
                    self.assertEqual(inputs, [], f'trial {trial} depth {depth}')
                    saw_truncation += 1
                else:
                    saw_inputs_listed += len(inputs)
        # non-vacuity in both directions: cuts really happened, and an
        # uncut catalog really does list inputs
        self.assertGreater(saw_truncation, 0)
        self.assertGreater(saw_inputs_listed, 0)

    def test_cnf_parent_is_never_an_input_or_a_retire_candidate(self):
        # I4: the declared cnf parent route is real now, and a cnf tarball
        # must not become deletable data under any circumstance.
        g = _small_graph()
        cnf = 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar'
        g[cnf] = {'n': 1, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root',
                                       f'dig.mu2e.CeEndpointOnSpill.{AU}.art']}
        cat = _cat(g)
        self.assertNotIn(cnf, cat.inputs)
        self.assertNotIn(cnf, cat.members)
        nts = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']
        self.assertEqual(nts.cnf_parents, {cnf})
        self.assertNotIn(cnf, nts.inputs)
        self.assertEqual(cat.members[f'dig.mu2e.CeEndpointOnSpill.{AU}.art'].cnf_parents, {cnf})
        self.assertNotIn(cnf, {x['dataset'] for x in retire(cat)})

    def test_retire_refuses_incomplete_catalog(self):
        # Run1B digs are fed by MDC2025 stop catalogues; a Run1B-only
        # catalog cannot tell a still-needed MDC2025 input from a
        # retirable one. retire() must refuse rather than guess.
        g = _small_graph()
        g['dig.mu2e.X.Run1Ban_best_v1_4.art'] = {'n': 1, 'children': []}
        epochs = {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}
        cat = assign_status(build_catalog(epochs, FakeSource(g), families=None))
        with self.assertRaises(ValueError) as ctx:
            retire(cat)
        self.assertIn('Run1B', str(ctx.exception))

    def test_letters_guard_true_branch(self):
        # unlike the Fresh@av node in the test above (never reached by
        # any walk because nothing consumes it, so it never enters
        # cat.inputs), this one IS a real input with no live descendant:
        # the guard's key[0] > newest[0] branch must actually fire here,
        # since av outranks the newest loaded epoch, au.
        g = _small_graph()
        g['dts.mu2e.Fresh.MDC2025av.art'] = {'n': 5, 'children': [f'dig.mu2e.CeEndpointOnSpill.{AN}.art']}
        cat = _cat(g)
        self.assertIn('dts.mu2e.Fresh.MDC2025av.art', cat.inputs)
        self.assertNotIn('dts.mu2e.Fresh.MDC2025av.art', {x['dataset'] for x in retire(cat)})

    def test_purge_line_format(self):
        cat = _cat()
        entry = [x for x in retire(cat) if x['dataset'] == f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'][0]
        line = purge_lines([entry])[0]
        cols = line.split('#')[0].split()
        self.assertEqual(cols[:5], ['DELETE', '-', '-', 'YES', '50'])
        self.assertEqual(cols[5], f'mcs.mu2e.CeEndpointOnSpill.{AR}.art')
        self.assertEqual(cols[6], f'nts.mu2e.CeEndpointOnSpill.{AR}.root')
        self.assertIn('superseded by', line.split('#', 1)[1])
        leaf = [x for x in retire(cat) if x['dataset'] == f'nts.mu2e.CeEndpointOnSpill.{AR}.root'][0]
        self.assertIn(' NO ', purge_lines([leaf])[0])
        self.assertIn(' NONE ', purge_lines([leaf])[0])


class TestLookup(unittest.TestCase):
    def test_member_and_input_and_unknown(self):
        cat = _cat()
        m = lookup(cat, f'nts.mu2e.CeEndpointOnSpill.{AU}.root')
        self.assertEqual((m['kind'], m['status'], m['epoch']), ('member', 'superseded', 'MDC2025au'))
        self.assertEqual(m['superseded_by'], f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root')
        i = lookup(cat, 'dts.mu2e.CeEndpoint.MDC2025ap.art')
        self.assertEqual(i['kind'], 'input')
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AU}.art', i['descendants'])
        self.assertEqual(lookup(cat, 'nope.mu2e.x.y.art')['kind'], 'unknown')


from utils.epochs.reports import count_warnings


class TestCountWarnings(unittest.TestCase):
    def test_thin_winner_is_warned(self):
        g = _small_graph()
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']['n'] = 10     # winner, thin
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}.root']['n'] = 100        # superseded, full
        cat = _cat(g)
        w = count_warnings(cat)
        self.assertEqual([x['dataset'] for x in w], [f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'])
        self.assertEqual((w[0]['nfiles'], w[0]['sibling'], w[0]['sibling_nfiles']),
                         (10, f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 100))

    def test_no_warning_when_winner_is_full(self):
        cat = _cat()   # both nts at 100 files
        self.assertEqual([x for x in count_warnings(cat)
                          if x['dataset'].startswith('nts.mu2e.CeEndpointOnSpill')], [])

    def test_ratio_boundary_and_excluded_ignored(self):
        g = _small_graph()
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']['n'] = 50
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}.root']['n'] = 100
        self.assertEqual(count_warnings(_cat(g), ratio=0.5), [])        # 50 is not < 50
        self.assertEqual(len(count_warnings(_cat(g), ratio=0.6)), 1)
        e = _epoch('MDC2025au', pins={'exclude': [
            {'dataset': f'nts.mu2e.CeEndpointOnSpill.{AU}.root', 'reason': 'bad'}]})
        cat = _cat(g, {'MDC2025au': e, 'MDC2025an': _epoch('MDC2025an')})
        self.assertEqual(count_warnings(cat, ratio=0.6), [])            # excluded sibling ignored


import hashlib
import tarfile
from utils.epochs.generation import (Generation, read_generation, build_cnf_index, cnf_for,
                                     generations)
from utils.epochs.reports import consistency


def _make_cnf(d, name, setup, outputs, fcl='services.DbService.version: "v1_5"\n'
              'services.GeometryService.inputFile: "Offline/Mu2eG4/geom/geom_run1_a.txt"\n'
              'services.GeometryService.bfgeomFile: "Offline/Mu2eG4/geom/bfgeom_v01.txt"\n'):
    """A minimal cnf tarball: jobpars.json (setup + tbs.outfiles) and mu2e.fcl.
    `outputs` are tbs.outfiles TEMPLATES exactly as mu2ejobdef writes them:
    'mcs.mu2e.X.MDC2025au_best_v1_5.sequencer.art' or, generic,
    'nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root'.
    `fcl=None` omits the mu2e.fcl member entirely (the g4bl / code-tarball
    cnf shape, which carries no embedded fcl)."""
    path = os.path.join(d, name)
    jobpars = {'setup': setup, 'tbs': {'outfiles': {f'out{i}': t for i, t in enumerate(outputs)}},
               'jobname': name, 'code': ''}
    members = [('jobpars.json', json.dumps(jobpars))]
    if fcl is not None:
        members.append(('mu2e.fcl', fcl))
    with tarfile.open(path, 'w') as tar:
        for member, payload in members:
            p = os.path.join(d, member)
            with open(p, 'w') as f:
                f.write(payload)
            tar.add(p, arcname=member)
    return path


SETUP_A = '/cvmfs/mu2e.opensciencegrid.org/Musings/AnalysisMDC2025/v02_00_00/setup.sh'
SETUP_B = '/cvmfs/mu2e.opensciencegrid.org/Musings/AnalysisMDC2025/v02_01_00/setup.sh'


class TestGeneration(unittest.TestCase):
    def test_read_generation_fields(self):
        d = _tmpdir()
        p = _make_cnf(d, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', SETUP_B, [])
        g = read_generation(p, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', 'index')
        self.assertEqual((g.musing, g.version), ('AnalysisMDC2025', 'v02_01_00'))
        self.assertEqual(g.dbservice, 'v1_5')
        self.assertTrue(g.geometry.endswith('geom_run1_a.txt'))
        self.assertTrue(g.bfield.endswith('bfgeom_v01.txt'))
        self.assertEqual(len(g.fcl_sha256), 64)
        self.assertEqual((g.source, g.cnf), ('index', 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar'))

    def test_unrecognized_setup_path_raises(self):
        d = _tmpdir()
        p = _make_cnf(d, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', '/some/where/setup.sh', [])
        with self.assertRaises(ValueError):
            read_generation(p, 'cnf.mu2e.X.MDC2025au_best_v1_5.0.tar', 'index')

    def test_index_from_declared_outputs_and_generic_dsconf(self):
        d = _tmpdir()
        explicit = _make_cnf(d, 'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar', SETUP_A,
                             [f'mcs.mu2e.CeEndpointOnSpill.{AU}.sequencer.art',
                              f'log.mu2e.CeEndpointOnSpill-reco.{AU}.sequencer.log'])
        generic = _make_cnf(d, 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar', SETUP_B,
                            ['nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root'])
        src = FakeSource(_small_graph(), cnfs={
            'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar': explicit,
            'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar': generic,
            'cnf.mu2e.Lost.MDC2025au_best_v1_5.0.tar': ''})
        idx = build_cnf_index(src, existing={})
        self.assertEqual(idx[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'],
                         'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar')
        self.assertNotIn(f'log.mu2e.CeEndpointOnSpill-reco.{AU}.log', idx)   # log tier never indexed
        self.assertEqual(idx['__generic__']['nts.MDC2025au_best_v1_5-001'],
                         'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar')
        self.assertEqual(idx['__unlocatable__'], ['cnf.mu2e.Lost.MDC2025au_best_v1_5.0.tar'])
        self.assertEqual(sorted(idx['__indexed__']), [k for k, v in sorted(src.cnfs.items()) if v])

    def test_duplicate_claim_keeps_the_first_and_reports_the_conflict(self):
        # M4: cnf_names() is sorted, so a plain assignment handed the
        # dataset to the lexicographically LAST claimant. Two cnfs
        # declaring one output is an anomaly to report, not to resolve by
        # sort order.
        d = _tmpdir()
        first = _make_cnf(d, 'cnf.mu2e.A-reco.MDC2025au_best_v1_5.0.tar', SETUP_A,
                          [f'mcs.mu2e.CeEndpointOnSpill.{AU}.sequencer.art'])
        second = _make_cnf(d, 'cnf.mu2e.Z-reco.MDC2025au_best_v1_5.0.tar', SETUP_B,
                           [f'mcs.mu2e.CeEndpointOnSpill.{AU}.sequencer.art',
                            'nts.mu2e.{desc}.MDC2025au_best_v1_5.sequencer.root'])
        third = _make_cnf(d, 'cnf.mu2e.Zz-evnt.MDC2025au_best_v1_5.0.tar', SETUP_B,
                          ['nts.mu2e.{desc}.MDC2025au_best_v1_5.sequencer.root'])
        src = FakeSource(_small_graph(), cnfs={
            'cnf.mu2e.A-reco.MDC2025au_best_v1_5.0.tar': first,
            'cnf.mu2e.Z-reco.MDC2025au_best_v1_5.0.tar': second,
            'cnf.mu2e.Zz-evnt.MDC2025au_best_v1_5.0.tar': third})
        idx = build_cnf_index(src, existing={})
        self.assertEqual(idx[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'],
                         'cnf.mu2e.A-reco.MDC2025au_best_v1_5.0.tar')
        self.assertEqual(idx['__conflicts__'][f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'],
                         ['cnf.mu2e.A-reco.MDC2025au_best_v1_5.0.tar',
                          'cnf.mu2e.Z-reco.MDC2025au_best_v1_5.0.tar'])
        self.assertEqual(idx['__generic__']['nts.MDC2025au_best_v1_5'],
                         'cnf.mu2e.Z-reco.MDC2025au_best_v1_5.0.tar')
        self.assertEqual(idx['__conflicts__']['__generic__:nts.MDC2025au_best_v1_5'],
                         ['cnf.mu2e.Z-reco.MDC2025au_best_v1_5.0.tar',
                          'cnf.mu2e.Zz-evnt.MDC2025au_best_v1_5.0.tar'])

    def test_index_skips_already_indexed_cnfs(self):
        src = FakeSource(_small_graph(), cnfs={'cnf.mu2e.A.MDC2025au_best_v1_5.0.tar': ''})
        idx = build_cnf_index(src, existing={'__indexed__': ['cnf.mu2e.A.MDC2025au_best_v1_5.0.tar'],
                                             '__generic__': {}, '__unlocatable__': []})
        self.assertEqual(idx['__unlocatable__'], [])

    def test_cnf_for_prefers_parent_then_index(self):
        g = _small_graph()
        g['cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar'] = {'n': 1, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']}
        cat = _cat(g)
        idx = {'__generic__': {f'nts.{AU}': 'cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar'}, '__indexed__': [], '__unlocatable__': [],
               f'mcs.mu2e.CeEndpointOnSpill.{AU}.art': 'cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar'}
        self.assertEqual(cnf_for(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'], cat, idx),
                         ('cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar', 'parent'))
        self.assertEqual(cnf_for(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'], cat, idx),
                         ('cnf.mu2e.CeEndpointOnSpill-reco.MDC2025au_best_v1_5.0.tar', 'index'))
        self.assertEqual(cnf_for(cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root'], cat, idx),
                         ('cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar', 'index'))
        self.assertIsNone(cnf_for(cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AR}.art'], cat, idx))

    def test_two_declared_cnf_parents_are_reported_not_settled_by_sort_order(self):
        # NEW-2: M4's ruling -- two cnfs claiming one output is a real
        # anomaly worth reporting, not resolving by sort order -- applies
        # to the declared-parent route I4 made reachable. After ADR 0003 a
        # campaign cnf plus its recovery cnf on a newer Musing is the
        # NORMAL shape, and reporting one generation with full confidence
        # hides the mixed-generation condition `consistency` exists for.
        g = _small_graph()
        a = 'cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar'
        b = 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar'
        tgt = f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'
        g[a] = {'n': 1, 'children': [tgt]}
        g[b] = {'n': 1, 'children': [tgt]}
        cat = _cat(g)
        self.assertEqual(cat.members[tgt].cnf_parents, {a, b})
        self.assertEqual(cnf_for(cat.members[tgt], cat, {}), (min(a, b), 'parent'))
        self.assertTrue(any(tgt in line and 'declares 2 cnf parents' in line
                            for line in cat.generation_conflicts), cat.generation_conflicts)

    def test_consistency_names_minority_descs(self):
        d = _tmpdir()
        a = _make_cnf(d, 'cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar', SETUP_A,
                      ['nts.mu2e.{desc}.MDC2025au_best_v1_5.sequencer.root'])
        b = _make_cnf(d, 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar', SETUP_B,
                      ['nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root'])
        g = _small_graph()
        g[f'mcs.mu2e.DIO.{AU}.art'] = {'n': 10, 'children': [f'nts.mu2e.DIO.{AU}.root']}
        g[f'dig.mu2e.DIO.{AU}.art'] = {'n': 10, 'children': [f'mcs.mu2e.DIO.{AU}.art']}
        g[f'nts.mu2e.DIO.{AU}.root'] = {'n': 10, 'children': []}
        src = FakeSource(g, cnfs={'cnf.mu2e.evnt.MDC2025au_best_v1_5.0.tar': a,
                                  'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar': b})
        cat = assign_status(build_catalog({'MDC2025au': _epoch('MDC2025au'),
                                           'MDC2025an': _epoch('MDC2025an')}, src, ['MDC2025']))
        idx = build_cnf_index(src, existing={})
        gens = generations(cat, src, idx)
        all_rows = consistency(cat, gens)
        rows = [r for r in all_rows if r['tier'] == 'nts']
        by_gen = {r['generation']: r for r in rows}
        self.assertEqual(by_gen['AnalysisMDC2025/v02_01_00']['descs'], ['CeEndpointOnSpill'])
        self.assertEqual(by_gen['AnalysisMDC2025/v02_00_00']['descs'], ['DIO'])
        # the mcs/dig members have no cnf in this fixture (only the nts-producing
        # evnt cnfs were registered) -> reported as 'unknown', never guessed.
        # Checked against the full report, not the nts-only `rows`: both current
        # nts members are pinned to a known generation by the assertions above,
        # so 'unknown' cannot appear among nts rows in this fixture.
        self.assertIn('unknown', {r['generation'] for r in all_rows})

    def test_index_isolates_unreadable_cnf(self):
        # build_cnf_index reads every cnf SAM knows (~850 in production);
        # one corrupt tarball or one tarball with no jobpars.json must not
        # abort the whole build (that is exactly what Task 11 runs).
        d = _tmpdir()
        good = _make_cnf(d, 'cnf.mu2e.Good.MDC2025au_best_v1_5.0.tar', SETUP_A,
                         ['nts.mu2e.{desc}.MDC2025au_best_v1_5.sequencer.root'])
        garbage_name = 'cnf.mu2e.Bad.MDC2025au_best_v1_5.0.tar'
        garbage_path = os.path.join(d, garbage_name)
        with open(garbage_path, 'wb') as f:
            f.write(b'this is not a tar file at all')
        no_jobpars_name = 'cnf.mu2e.NoJobpars.MDC2025au_best_v1_5.0.tar'
        no_jobpars_path = os.path.join(d, no_jobpars_name)
        with tarfile.open(no_jobpars_path, 'w') as tar:
            p = os.path.join(d, 'mu2e.fcl')
            with open(p, 'w') as f:
                f.write('# a tar with mu2e.fcl but no jobpars.json\n')
            tar.add(p, arcname='mu2e.fcl')
        src = FakeSource(_small_graph(), cnfs={
            'cnf.mu2e.Good.MDC2025au_best_v1_5.0.tar': good,
            garbage_name: garbage_path,
            no_jobpars_name: no_jobpars_path})
        idx = build_cnf_index(src, existing={})
        self.assertIn(garbage_name, idx['__unreadable__'])
        self.assertTrue(idx['__unreadable__'][garbage_name])
        self.assertIn(no_jobpars_name, idx['__unreadable__'])
        self.assertTrue(idx['__unreadable__'][no_jobpars_name])
        self.assertNotIn(garbage_name, idx['__indexed__'])
        self.assertNotIn(no_jobpars_name, idx['__indexed__'])
        self.assertIn('cnf.mu2e.Good.MDC2025au_best_v1_5.0.tar', idx['__indexed__'])
        self.assertEqual(idx['__generic__']['nts.MDC2025au_best_v1_5'],
                         'cnf.mu2e.Good.MDC2025au_best_v1_5.0.tar')

    def test_read_generation_without_fcl(self):
        # g4bl and code-tarball cnfs carry no mu2e.fcl at all — a documented
        # shape, not a corrupt-tarball error.
        d = _tmpdir()
        p = _make_cnf(d, 'cnf.mu2e.G4bl.MDC2025au_best_v1_5.0.tar', SETUP_B, [], fcl=None)
        g = read_generation(p, 'cnf.mu2e.G4bl.MDC2025au_best_v1_5.0.tar', 'index')
        self.assertEqual((g.musing, g.version), ('AnalysisMDC2025', 'v02_01_00'))
        self.assertEqual(g.fcl_sha256, '')
        self.assertEqual(g.dbservice, '')
        self.assertEqual(g.geometry, '')
        self.assertEqual(g.bfield, '')

    def test_generations_source_is_per_member(self):
        # a cnf read once (cached by name) is reached by two different
        # members via two different routes; each member's Generation.source
        # must reflect ITS OWN route, not whichever member populated the
        # cache first.
        d = _tmpdir()
        cnf_path = _make_cnf(d, 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar', SETUP_A,
                             ['nts.mu2e.{desc}.MDC2025au_best_v1_5-001.sequencer.root'])
        g = _small_graph()
        # nts-001 reaches the cnf via a declared SAM parent edge (route: parent)
        g['cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar'] = {
            'n': 1, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']}
        # a second, same-dsconf nts dataset with no cnf parent edge reaches
        # the SAME cnf only through the generic index entry (route: index)
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(f'nts.mu2e.Second.{AU}-001.root')
        g[f'nts.mu2e.Second.{AU}-001.root'] = {'n': 100, 'children': []}
        src = FakeSource(g, cnfs={'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar': cnf_path})
        cat = assign_status(build_catalog({'MDC2025au': _epoch('MDC2025au'),
                                           'MDC2025an': _epoch('MDC2025an')}, src, ['MDC2025']))
        idx = build_cnf_index(src, existing={})
        gens = generations(cat, src, idx)
        via_parent = gens[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']
        via_index = gens[f'nts.mu2e.Second.{AU}-001.root']
        self.assertEqual(via_parent.cnf, via_index.cnf)
        self.assertEqual(via_parent.source, 'parent')
        self.assertEqual(via_index.source, 'index')


from utils.epochs.publish import catalog_document, write_catalog


class TestPublish(unittest.TestCase):
    def test_ray_shape_and_our_fields(self):
        cat = _cat()
        doc = catalog_document(cat, gens={}, generated_at='2026-09-03T00:00:00Z')
        self.assertEqual(doc['generated_at'], '2026-09-03T00:00:00Z')
        names = {e['name']: e for e in doc['epochs']}
        self.assertEqual(set(names), {'MDC2025au', 'MDC2025an'})
        au = names['MDC2025au']
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', au['datasets'])
        member = [m for m in au['members'] if m['name'] == f'nts.mu2e.CeEndpointOnSpill.{AU}.root'][0]
        self.assertEqual(member['status'], 'superseded')
        self.assertEqual(member['superseded_by'], f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root')
        self.assertEqual(member['generation'], '')
        self.assertIn('dts.mu2e.CeEndpoint.MDC2025ap.art', doc['inputs'])
        self.assertIsInstance(doc['gaps'], list)
        self.assertIsInstance(doc['retire'], list)

    def test_count_warnings_key_is_a_list(self):
        cat = _cat()
        doc = catalog_document(cat, gens={}, generated_at='x')
        self.assertIsInstance(doc['count_warnings'], list)

    def test_write_is_valid_json_and_deterministic(self):
        cat = _cat()
        doc = catalog_document(cat, gens={}, generated_at='x')
        d = _tmpdir()
        p = os.path.join(d, 'sim_catalog.json')
        write_catalog(doc, p)
        with open(p) as f:
            back = json.load(f)
        self.assertEqual(back['epochs'][0]['name'], 'MDC2025an')   # sorted by name

    def test_incomplete_catalog_publishes_without_retire(self):
        # A family with digs but no loaded epoch file (Run1B here) makes
        # the catalog incomplete: retire() would raise, so catalog_document
        # must report the reason instead of proposing (or crashing on) a
        # deletion list. The complete part of the catalog (the two loaded
        # MDC2025 epochs) still publishes normally.
        g = _small_graph()
        g['dig.mu2e.X.Run1Ban_best_v1_4.art'] = {'n': 1, 'children': []}
        epochs = {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}
        cat = assign_status(build_catalog(epochs, FakeSource(g), families=None))
        doc = catalog_document(cat, gens={}, generated_at='x')
        self.assertIsNone(doc['retire'])
        self.assertTrue(doc['incomplete'])
        self.assertTrue(any('Run1B' in line for line in doc['incomplete']))
        self.assertEqual(doc['missing_families'], ['Run1B'])
        self.assertEqual({e['name'] for e in doc['epochs']}, {'MDC2025au', 'MDC2025an'})


import io
import contextlib
from utils.epochs import cli as epochs_cli


def _run(argv, source, epochs_dir):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = epochs_cli.main(['--epochs-dir', epochs_dir] + argv, source=source,
                             now_fn=lambda: '2026-09-03T00:00:00Z')
    return rc, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.d = _tmpdir()
        self.src = FakeSource(_small_graph())
        for name in ('MDC2025au', 'MDC2025an'):
            write_epoch_file(self.d, propose_epoch(name + '_best_v1_0'))

    def test_propose_writes_missing_family_files(self):
        d = _tmpdir()
        rc, out, _ = _run(['propose', '--family', 'MDC2025'], self.src, d)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(os.listdir(d)), ['MDC2025an.json', 'MDC2025au.json'])
        rc, out, _ = _run(['propose', '--family', 'MDC2025'], self.src, d)
        self.assertIn('nothing to propose', out)

    def test_propose_without_family_is_usage_error(self):
        rc, _, err = _run(['propose'], self.src, self.d)
        self.assertEqual(rc, 2)

    def test_members_text_and_json(self):
        rc, out, _ = _run(['members', '--tier', 'nts', '--status', 'current'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', out)
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root current', out)
        rc, out, _ = _run(['members', '--json'], self.src, self.d)
        rows = json.loads(out)
        self.assertEqual({r['status'] for r in rows} <= {'current', 'stale', 'superseded', 'excluded'}, True)

    def test_retire_prints_purge_lines(self):
        rc, out, _ = _run(['retire'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith('DELETE - - '))

    def test_input_depth_defaults_to_no_cap_and_a_cap_refuses_the_input_section(self):
        # end-to-end through main(): the flag's default is "walk to
        # closure", and passing a cap that truncates anything drops every
        # input DELETE line and says so on stderr. The 126-test suite of
        # the previous round passed while every verb crashed, so the CLI
        # itself is exercised, not only the reports.
        self.assertIsNone(epochs_cli.build_parser().parse_args(['retire']).input_depth)
        src = FakeSource(_deep_graph())
        rc, out, err = _run(['retire'], src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn('sim.mu2e.Deep3.MDC2025af.art', out)
        self.assertNotIn('input retirement refused', err)
        rc, out, err = _run(['--input-depth', '3', 'retire'], src, self.d)
        self.assertEqual(rc, 0)
        self.assertNotIn('sim.mu2e.Deep2.MDC2025af.art', out)
        self.assertNotIn('sim.mu2e.Deep3.MDC2025af.art', out)
        self.assertIn('input retirement refused', err)
        self.assertIn('NO input is proposed', err)
        # members are unaffected by the cap
        self.assertTrue(out.startswith('DELETE - - '))

    def test_lookup_unknown_is_nonzero(self):
        rc, out, _ = _run(['lookup', 'nope.mu2e.a.b.art'], self.src, self.d)
        self.assertEqual(rc, 1)

    def test_publish_writes_file(self):
        p = os.path.join(_tmpdir(), 'sim_catalog.json')
        rc, out, _ = _run(['publish', '--out', p], self.src, self.d)
        self.assertEqual(rc, 0)
        with open(p) as f:
            self.assertEqual(json.load(f)['generated_at'], '2026-09-03T00:00:00Z')

    def test_malformed_epoch_file_is_exit_2(self):
        with open(os.path.join(self.d, 'MDC2025au.json'), 'w') as f:
            f.write('{"name": "MDC2025au"}')
        rc, _, err = _run(['members'], self.src, self.d)
        self.assertEqual(rc, 2)
        self.assertIn('MDC2025au.json', err)

    def test_family_filter_applies_to_output_only(self):
        # --family/--epoch filter printed rows only; the catalog underneath
        # is always the complete one (ruling of 2026-09-03).
        rc, out, _ = _run(['members', '--family', 'Run1B'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertEqual(out, '')
        rc, out, _ = _run(['members', '--family', 'MDC2025'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', out)

    def test_retire_refuses_incomplete_catalog_exit_3(self):
        g = _small_graph()
        g['dig.mu2e.X.Run1Ban_best_v1_4.art'] = {'n': 3, 'children': []}
        src = FakeSource(g)
        rc, out, err = _run(['retire'], src, self.d)
        self.assertEqual(rc, 3)
        self.assertIn('propose --family Run1B', err)

    def test_sam_client_error_is_exit_3(self):
        # M11: samweb_wrapper raises samweb_client.Error, which derives
        # from neither ValueError nor OSError, so a SAM outage produced a
        # traceback where the schema promises exit 3.
        import samweb_client

        class _FakeSamError(Exception):
            pass

        previous = samweb_client.Error
        samweb_client.Error = _FakeSamError
        try:
            src = FakeSource(_small_graph())
            def boom():
                raise _FakeSamError('SAM is down')
            src.dig_families = boom
            rc, out, err = _run(['members'], src, self.d)
        finally:
            samweb_client.Error = previous
        self.assertEqual(rc, 3)
        self.assertIn('SAM is down', err)

    def test_sam_error_class_is_empty_when_there_is_no_usable_error(self):
        # NEW-8: the M11 test above substitutes its own exception class, so
        # what it pins is the wiring. This pins the property the docstring
        # claims: when samweb_client exposes nothing usable, the resolver
        # catches NOTHING rather than degrading to Exception -- a blanket
        # catch would turn a programming error in the catalog code into a
        # tidy exit 3 instead of a traceback.
        import samweb_client
        from utils.epochs.cli import _sam_error_class

        previous = samweb_client.Error
        try:
            samweb_client.Error = 'not a class at all'
            self.assertEqual(_sam_error_class(), ())
            samweb_client.Error = ValueError            # a class, and usable
            self.assertIs(_sam_error_class(), ValueError)
        finally:
            samweb_client.Error = previous
        # the whole suite runs with samweb_client stubbed by a MagicMock,
        # whose .Error is not a class either: nothing is caught there
        self.assertEqual(_sam_error_class(), ())

    def test_count_warning_goes_to_stderr(self):
        g = _small_graph()
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']['n'] = 10     # winner, thin
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}.root']['n'] = 100        # superseded, full
        src = FakeSource(g)
        rc, out, err = _run(['members'], src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn('count warning:', err)
        self.assertNotIn('count warning:', out)


if __name__ == '__main__':
    unittest.main()
