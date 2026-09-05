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

    def cnf_names(self):
        return sorted(self.cnfs)

    def local_path(self, filename):
        return self.cnfs.get(filename) or self.files.get(filename, '')


class OwnerPolicyFakeSource(FakeSource):
    """FakeSource plus `SamSource`'s OWNER policy: a dataset whose owner is
    not `mu2e` goes into `.foreign` and is never returned from `children`
    or `parents`.

    That is how a user-produced dataset promoted into a production chain
    severs both walks in reality (verify-3 I2): the walk cannot see it, so
    it cannot walk above it, and the region beyond it is judged on
    whatever some other walk recorded. Plain `FakeSource` hands foreign
    parents back happily, which is why the suite never reproduced it."""
    def _keep(self, name):
        if name.split('.')[1] != 'mu2e':
            self.foreign.add(name)
            return False
        return True

    def children(self, dataset):
        return {c: n for c, n in super().children(dataset).items() if self._keep(c)}

    def parents(self, dataset):
        return {p: n for p, n in super().parents(dataset).items() if self._keep(p)}


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

    def test_downward_walk_records_member_parent_edges(self):
        cat = build_catalog(self.epochs, self.src, ['MDC2025'])
        dig = cat.members[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertEqual(dig.parents, set())   # nothing walks upward from a dig root
        nts = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']
        self.assertEqual(nts.parents, {f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'})

    def test_dig_already_a_member_is_not_rebuilt(self):
        # M8: the dig loop assigned cat.members[name] unconditionally, so a
        # dig already reached as another dig's child had its Member object
        # replaced and its accumulated parents thrown away. _walk_down
        # guards with cat.members.get(); the dig loop now does too.
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
        # A dig reached BOTH as another dig's child (M8) and as its own
        # family entry gets _walk_down invoked on it twice -- once from
        # the upper dig's frontier, once from build_catalog's own dig
        # loop. Without the per-build `_Memo` cache, `children()` and
        # `parents()` would each be queried twice for it.
        g = _small_graph()
        upper = f'dig.mu2e.Aupper.{AU}.art'
        lower = f'dig.mu2e.CeEndpointOnSpill.{AU}.art'
        g[upper] = {'n': 7, 'children': [lower]}
        src = FakeSource(g)
        build_catalog(self.epochs, src, ['MDC2025'])
        self.assertEqual(src.calls['parents'].count(lower), 1)
        for name in set(src.calls['children']):
            self.assertEqual(src.calls['children'].count(name), 1, name)

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

    def test_foreign_child_is_reported_but_the_walk_stops_there(self):
        # Important 2 (strip review, cf557b4): the Keep-list capability
        # "foreign reporting from the downward walk" lost its only test
        # when TestInputRetirementSafety was deleted. A user-owned dataset
        # promoted into a production chain is the real case this models:
        # SamSource never returns it from children()/parents(), so the
        # walk cannot see it and everything below it is simply unreached
        # -- plain FakeSource hands foreign nodes back happily, which is
        # why OwnerPolicyFakeSource (defined above) exists.
        from utils.epochs.publish import catalog_document
        g = _small_graph()
        foreign = f'nts.oksuzian.Private.{AU}.root'
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(foreign)
        g[foreign] = {'n': 5, 'children': []}
        cat = build_catalog(self.epochs, OwnerPolicyFakeSource(g), ['MDC2025'])
        self.assertEqual(cat.foreign, {foreign})
        self.assertNotIn(foreign, cat.members)
        doc = catalog_document(cat, gens={}, generated_at='x')
        self.assertEqual(doc['foreign'], [foreign])


def _cat(graph=None, epochs=None):
    src = FakeSource(graph or _small_graph())
    eps = epochs or {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an')}
    return assign_status(build_catalog(eps, src, ['MDC2025']))


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

    def test_group_keys_of_sorts_none_purposes_without_raising(self):
        # V3: this sort is over RAW GroupKeys, whose fourth field is
        # Optional[str]. It is safe today only because of an invariant of
        # groups() that nothing states or enforces (a base triple gets a
        # purpose=None key only when it has no lettered member), and the
        # same shape one line away took down every verb on 2026-09-04 with
        # `TypeError: '<' not supported between 'str' and 'NoneType'`.
        # retire() reads this function, so the sort has to be total on its
        # own terms whatever the caller hands it -- hence the check pins
        # the function's contract rather than the current invariant.
        from unittest.mock import patch
        from utils.epochs import status as status_mod
        base = ('MDC2025', 'nts', 'CeEndpointOnSpill')
        cat = _cat()
        m = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}.root']
        grouped = {base + ('best',): [], base + (None,): [], base + ('perfect',): []}
        with patch.object(status_mod, 'group_key', lambda mm: base + ('not-a-group',)), \
                patch.object(m.key.__class__, 'purpose', None):
            keys = status_mod.group_keys_of(grouped, m)
        self.assertEqual(keys, [base + (None,), base + ('best',), base + ('perfect',)])

    def test_group_key_shape(self):
        cat = _cat()
        m = cat.members[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']
        self.assertEqual(group_key(m), ('MDC2025', 'mcs', 'CeEndpointOnSpill', 'best'))
        self.assertIn(('MDC2025', 'nts', 'CeEndpointOnSpill', 'best'), groups(cat))


from utils.epochs.reports import gaps, retire, purge_lines, lookup, EXPECTED_TIERS


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

    def test_cnf_parent_is_never_a_retire_candidate(self):
        # I4: the declared cnf parent route is real now, and a cnf tarball
        # must not become deletable data under any circumstance.
        g = _small_graph()
        cnf = 'cnf.mu2e.evnt.MDC2025au_best_v1_5-001.0.tar'
        g[cnf] = {'n': 1, 'children': [f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root',
                                       f'dig.mu2e.CeEndpointOnSpill.{AU}.art']}
        cat = _cat(g)
        self.assertNotIn(cnf, cat.members)
        nts = cat.members[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']
        self.assertEqual(nts.cnf_parents, {cnf})
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
    def test_member_and_unknown(self):
        cat = _cat()
        m = lookup(cat, f'nts.mu2e.CeEndpointOnSpill.{AU}.root')
        self.assertEqual((m['kind'], m['status'], m['epoch']), ('member', 'superseded', 'MDC2025au'))
        self.assertEqual(m['superseded_by'], f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root')
        self.assertEqual(lookup(cat, 'nope.mu2e.x.y.art')['kind'], 'unknown')
        # an upstream, non-member parent (input retirement is not modeled
        # in this version) reads as unknown too, not as a third kind
        self.assertEqual(lookup(cat, 'dts.mu2e.CeEndpoint.MDC2025ap.art')['kind'], 'unknown')


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


from utils.epochs.generation import GENERATION_FAMILIES

BA = 'MDC2020ba_best_v1_3'          # an out-of-scope (legacy) family


def _legacy_graph():
    """One MDC2020 dig with a downstream mcs, plus one in-scope MDC2025
    chain, so a single catalog spans both sides of the era boundary."""
    g = _small_graph()
    g[f'dig.mu2e.Legacy.{BA}.art'] = {'n': 10, 'children': [f'mcs.mu2e.Legacy.{BA}.art']}
    g[f'mcs.mu2e.Legacy.{BA}.art'] = {'n': 10, 'children': []}
    return g


def _both_epochs():
    return {'MDC2025au': _epoch('MDC2025au'), 'MDC2025an': _epoch('MDC2025an'),
            'MDC2020ba': _epoch('MDC2020ba')}


class TestGenerationScope(unittest.TestCase):
    """A+B: generation is attempted only for GENERATION_FAMILIES, and every
    in-scope failure is isolated per cnf and reported by reason."""

    def _cat_src(self, cnfs=None):
        src = FakeSource(_legacy_graph(), cnfs=cnfs or {})
        cat = assign_status(build_catalog(_both_epochs(), src, ['MDC2025', 'MDC2020']))
        return cat, src

    def test_scope_names_the_two_live_families(self):
        self.assertEqual(GENERATION_FAMILIES, frozenset({'MDC2025', 'Run1B'}))

    def test_out_of_scope_family_is_blank_and_reported_once_by_family(self):
        cat, src = self._cat_src()
        gens = generations(cat, src, {})
        legacy = [n for n in gens if f'.{BA}.' in n]
        self.assertTrue(legacy, 'fixture must contain MDC2020 members')
        for n in legacy:
            self.assertIsNone(gens[n])
        # one family-level statement, NOT one complaint per dataset
        self.assertEqual(cat.generation_out_of_scope, {'MDC2020'})
        # and an out-of-scope member is never counted as a fault
        for n in legacy:
            self.assertNotIn(n, cat.generation_unresolved)

    def test_out_of_scope_member_costs_no_cnf_lookup(self):
        # the skip happens before cnf_for, so a legacy member must not be
        # able to claim a cnf through the index either
        cat, src = self._cat_src()
        legacy = next(n for n in cat.members if f'.{BA}.' in n)
        gens = generations(cat, src, {legacy: 'cnf.mu2e.Whatever.MDC2020ba_best_v1_3.0.tar'})
        self.assertIsNone(gens[legacy])
        self.assertEqual(cat.generation_unreadable, {})
        self.assertEqual(cat.generation_unlocatable, set())

    def test_in_scope_member_with_no_cnf_is_reported_unresolved(self):
        cat, src = self._cat_src()
        gens = generations(cat, src, {})
        au = f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'
        self.assertIsNone(gens[au])
        self.assertIn(au, cat.generation_unresolved)

    def test_unreadable_cnf_is_isolated_not_fatal(self):
        # a cnf that SAM locates but that will not open (dCache denial,
        # corrupt tarball, vanished scratch file) must degrade ONE
        # member's generation, never abort the verb.
        d = _tmpdir()
        bad = os.path.join(d, 'not-a-tarball.tar')
        with open(bad, 'w') as f:
            f.write('this is not a tar file')
        good_name = 'cnf.mu2e.Good.MDC2025au_best_v1_5.0.tar'
        good = _make_cnf(d, good_name, SETUP_B,
                         [f'nts.mu2e.CeEndpointOnSpill.{AU}-001.sequencer.root'])
        bad_name = 'cnf.mu2e.Bad.MDC2025au_best_v1_5.0.tar'
        cat, src = self._cat_src(cnfs={good_name: good, bad_name: bad})
        au = f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'
        nts = f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root'
        gens = generations(cat, src, {au: bad_name, nts: good_name})   # no raise
        self.assertIsNone(gens[au])
        self.assertIn(bad_name, cat.generation_unreadable)
        self.assertIn('ReadError', cat.generation_unreadable[bad_name])
        # the healthy member is unaffected
        self.assertIsNotNone(gens[nts])
        self.assertEqual(gens[nts].version, 'v02_01_00')

    def test_unlocatable_cnf_is_recorded_separately(self):
        cat, src = self._cat_src()                     # FakeSource knows no cnfs
        au = f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'
        gens = generations(cat, src, {au: 'cnf.mu2e.Gone.MDC2025au_best_v1_5.0.tar'})
        self.assertIsNone(gens[au])
        self.assertIn('cnf.mu2e.Gone.MDC2025au_best_v1_5.0.tar', cat.generation_unlocatable)
        self.assertEqual(cat.generation_unreadable, {})   # a different reason

    def test_publish_document_carries_each_reason(self):
        cat, src = self._cat_src()
        au = f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'
        gens = generations(cat, src, {au: 'cnf.mu2e.Gone.MDC2025au_best_v1_5.0.tar'})
        doc = catalog_document(cat, gens, '2026-09-04T00:00:00Z')
        self.assertEqual(doc['generation_out_of_scope'], ['MDC2020'])
        self.assertIn('cnf.mu2e.Gone.MDC2025au_best_v1_5.0.tar', doc['generation_unlocatable'])
        self.assertIsInstance(doc['generation_unresolved'], list)
        self.assertIsInstance(doc['generation_unreadable'], dict)

    def test_cli_reports_the_era_once_and_names_a_fault(self):
        d = _tmpdir()
        for name in ('MDC2025au', 'MDC2025an', 'MDC2020ba'):
            with open(os.path.join(d, name + '.json'), 'w') as f:
                e = _epoch(name)
                json.dump({'name': e.name, 'purpose': e.purpose, 'status': e.status,
                           'roots': e.roots, 'pins': e.pins}, f)
        src = FakeSource(_legacy_graph())
        rc, _, err = _run(['consistency'], src, d)
        self.assertEqual(rc, 0)
        era = [ln for ln in err.splitlines() if 'generation not evaluated' in ln]
        self.assertEqual(len(era), 1, f'era line must appear once, got {era}')
        self.assertIn('MDC2020', era[0])
        self.assertIn('.fcl', era[0])
        # an in-scope member with no cnf is named individually
        self.assertTrue([ln for ln in err.splitlines()
                         if ln.startswith('no generation:') and AU in ln])

    def test_consistency_separates_not_evaluated_from_unknown(self):
        # a legacy era and a genuine in-scope failure must not share a
        # label -- that conflation is what hides a real fault.
        cat, src = self._cat_src()
        gens = generations(cat, src, {})
        rows = {(r['family'], r['tier']): r['generation'] for r in consistency(cat, gens)}
        legacy = [v for (fam, _), v in rows.items() if fam == 'MDC2020']
        in_scope = [v for (fam, _), v in rows.items() if fam == 'MDC2025']
        self.assertTrue(legacy and all(v == 'not evaluated' for v in legacy), rows)
        self.assertTrue(in_scope and all(v == 'unknown' for v in in_scope), rows)

    def test_reentrant_reports_each_fault_once(self):
        # `consistency` and `publish` both call generations(); a second
        # call on the same catalog must not double-report.
        cat, src = self._cat_src()
        au = f'mcs.mu2e.CeEndpointOnSpill.{AU}.art'
        generations(cat, src, {au: 'cnf.mu2e.Gone.MDC2025au_best_v1_5.0.tar'})
        first = (set(cat.generation_out_of_scope), set(cat.generation_unresolved),
                 set(cat.generation_unlocatable), dict(cat.generation_unreadable))
        generations(cat, src, {au: 'cnf.mu2e.Gone.MDC2025au_best_v1_5.0.tar'})
        second = (set(cat.generation_out_of_scope), set(cat.generation_unresolved),
                  set(cat.generation_unlocatable), dict(cat.generation_unreadable))
        self.assertEqual(first, second)


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


def _wide_graph(ndescs):
    """`_small_graph`'s shape widened to `ndescs` independent desc chains
    inside one epoch, so a test can watch a cost that used to scale with
    the member count."""
    g = {}
    for i in range(ndescs):
        d = f'Desc{i:02d}'
        dig, mcs = f'dig.mu2e.{d}.{AU}.art', f'mcs.mu2e.{d}.{AU}.art'
        new, old = f'nts.mu2e.{d}.{AU}-001.root', f'nts.mu2e.{d}.{AU}.root'
        g[dig] = {'n': 10, 'children': [mcs]}
        g[mcs] = {'n': 10, 'children': [new, old]}
        g[new] = {'n': 10, 'children': []}
        g[old] = {'n': 10, 'children': []}
    return g


class TestGroupingIsComputedOnce(unittest.TestCase):
    """`groups(cat)` walks and sorts the entire catalog. It used to be
    called once per member by `_winner_of` (so once per member by both
    `retire` and `catalog_document`): 1513 regroups to write one document
    for a 1008-member catalog, 756 to build one retire list. Nothing
    about the ANSWER changes here, so the tests pin both halves — the
    count, and that the answer is the same either way."""

    def _count(self, fn):
        import utils.epochs.publish as publish_mod
        import utils.epochs.reports as reports_mod
        import utils.epochs.status as status_mod
        mods = (publish_mod, reports_mod, status_mod)
        calls = []
        real = status_mod.groups

        def counting(cat):
            calls.append(cat)
            return real(cat)

        saved = [(m, getattr(m, 'groups', None)) for m in mods]
        for m in mods:
            if hasattr(m, 'groups'):
                m.groups = counting
        try:
            out = fn()
        finally:
            for m, orig in saved:
                if orig is not None:
                    m.groups = orig
        return out, len(calls)

    def test_catalog_document_groups_the_catalog_twice_not_once_per_member(self):
        # two: one for the document's own per-member loop, one inside the
        # retire() it calls. A constant, not a function of member count.
        cat = _cat()
        doc, n = self._count(lambda: catalog_document(cat, {}, 'x'))
        self.assertEqual(n, 2)
        self.assertTrue(doc['epochs'])

    def test_the_grouping_cost_does_not_scale_with_the_member_count(self):
        small, big = _cat(_wide_graph(2)), _cat(_wide_graph(40))
        self.assertLess(len(small.members), len(big.members))
        _, n_small = self._count(lambda: catalog_document(small, {}, 'x'))
        _, n_big = self._count(lambda: catalog_document(big, {}, 'x'))
        self.assertEqual((n_small, n_big), (2, 2))
        _, r_small = self._count(lambda: retire(small))
        _, r_big = self._count(lambda: retire(big))
        self.assertEqual((r_small, r_big), (1, 1))

    def test_retire_groups_the_catalog_once(self):
        cat = _cat()
        rows, n = self._count(lambda: retire(cat))
        self.assertEqual(n, 1)
        self.assertTrue(rows)

    def test_lookup_with_and_without_a_shared_grouping_agree(self):
        cat = _cat()
        grouped = groups(cat)
        self.assertTrue(cat.members)
        for name in cat.members:
            self.assertEqual(lookup(cat, name), lookup(cat, name, grouped))

    def test_count_warnings_with_and_without_a_shared_grouping_agree(self):
        g = _small_graph()
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root']['n'] = 10
        g[f'nts.mu2e.CeEndpointOnSpill.{AU}.root']['n'] = 100
        cat = _cat(g)
        grouped = groups(cat)
        self.assertTrue(count_warnings(cat))
        self.assertEqual(count_warnings(cat), count_warnings(cat, grouped=grouped))

    def test_one_grouping_serves_every_member_because_groups_is_pure(self):
        # This is WHY the hoist is legal, stated as a property rather
        # than trusted: groups() reads the member set and the `excluded`
        # flags, both settled by the time build_catalog returns, and it
        # does NOT read `status` -- which is what makes a grouping taken
        # before a status-reading loop still correct inside it.
        cat = _cat()
        a, b = groups(cat), groups(cat)
        self.assertEqual(list(a), list(b))
        for k in a:
            self.assertTrue(all(x is y for x, y in zip(a[k], b[k])))
            self.assertEqual(len(a[k]), len(b[k]))
        for m in cat.members.values():
            m.status = 'superseded'
        after = groups(cat)
        self.assertEqual(list(after), list(a))
        for k in a:
            self.assertEqual([m.name for m in after[k]], [m.name for m in a[k]])


import io
import contextlib
from utils.epochs import cli as epochs_cli
from utils.epochs.progress import Progress


class _Tty(io.StringIO):
    """A StringIO that claims to be a terminal. `redirect_stderr` hands
    the CLI a plain StringIO, whose isatty() is False, so without this
    the progress output can never be exercised end to end -- and progress
    on a non-TTY is precisely what must NOT happen."""
    def isatty(self):
        return True


def _ticking_clock(step=0.1):
    """A monotonic clock that advances a fixed step per read, so the
    redraw throttle and the elapsed figure are deterministic."""
    state = {'t': 0.0}

    def clock():
        state['t'] += step
        return state['t']
    return clock


def _run(argv, source, epochs_dir, tty=False):
    out = io.StringIO()
    err = _Tty() if tty else io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = epochs_cli.main(['--epochs-dir', epochs_dir] + argv, source=source,
                             now_fn=lambda: '2026-09-03T00:00:00Z')
    return rc, out.getvalue(), err.getvalue()


class TestGapsRule(unittest.TestCase):
    """ADR 0006: the newest name must be safe to use. A stale member in a
    current epoch is a rule violation, and `gaps` says so with exit 1."""

    def setUp(self):
        self.d = _tmpdir()
        for name in ('MDC2025au', 'MDC2025an'):
            write_epoch_file(self.d, propose_epoch(name + '_best_v1_0'))

    def _stale_graph(self):
        # re-reco'd mcs at -001 with no nts yet: both nts become stale
        g = _small_graph()
        g[f'dig.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(
            f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art')
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}-001.art'] = {'n': 100, 'children': []}
        return g

    def test_stale_member_in_a_current_epoch_exits_1(self):
        rc, out, err = _run(['gaps'], FakeSource(self._stale_graph()), self.d)
        self.assertEqual(rc, 1, err)
        self.assertIn('stale', out)
        self.assertIn('ADR 0006', err)
        self.assertIn('newest name', err)

    def test_json_output_carries_the_same_verdict(self):
        rc, out, err = _run(['gaps', '--json'], FakeSource(self._stale_graph()), self.d)
        self.assertEqual(rc, 1)
        rows = json.loads(out)
        self.assertTrue([r for r in rows if r['kind'] == 'stale'])

    def test_missing_tier_alone_is_not_a_violation(self):
        # a dig with no mcs yet is work not done, not a broken promise
        g = _small_graph()
        g[f'dig.mu2e.DIOtail.{AU}.art'] = {'n': 10, 'children': []}
        rc, out, err = _run(['gaps'], FakeSource(g), self.d)
        self.assertEqual(rc, 0, err)
        self.assertIn('missing', out)
        self.assertNotIn('ADR 0006', err)

    def test_no_gaps_at_all_exits_0(self):
        rc, out, err = _run(['gaps'], FakeSource(_small_graph()), self.d)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, '')

    def test_verdict_follows_the_printed_rows(self):
        # --epoch X judges X alone: the stale nts sit in MDC2025au
        src = FakeSource(self._stale_graph())
        rc, out, _ = _run(['gaps', '--epoch', 'MDC2025an'], src, self.d)
        self.assertEqual(rc, 0)
        self.assertNotIn('stale', out)
        rc, out, _ = _run(['gaps', '--epoch', 'MDC2025au'], src, self.d)
        self.assertEqual(rc, 1)
        self.assertIn('stale', out)

    def test_frozen_epoch_is_outside_the_rule(self):
        # frozen = a hold on every member, nothing expected to change, no
        # gap report -- so nothing to fail on either
        d = _tmpdir()
        au = propose_epoch('MDC2025au_best_v1_0'); au['status'] = 'frozen'
        write_epoch_file(d, au)
        write_epoch_file(d, propose_epoch('MDC2025an_best_v1_0'))
        rc, out, err = _run(['gaps'], FakeSource(self._stale_graph()), d)
        self.assertEqual(rc, 0, err)
        self.assertNotIn('stale', out)

    def test_members_does_not_fail_on_stale(self):
        # the rule bites on gaps only; members answers "which one do I use"
        rc, out, err = _run(['members', '--status', 'stale'], FakeSource(self._stale_graph()), self.d)
        self.assertEqual(rc, 0, err)
        self.assertIn('stale', out)


class TestProgress(unittest.TestCase):
    def _progress(self, interval=0.0):
        stream = io.StringIO()
        return stream, Progress(stream=stream, enabled=True, interval=interval,
                                clock=_ticking_clock())

    def _drive(self, p):
        p.family('MDC2020', 3)
        for _ in range(3):
            p.dig(10)
        p.family('MDC2025', 2)
        for _ in range(2):
            p.dig(20)
        p.finish(30)

    def test_one_line_per_family_plus_one_summary(self):
        # The requirement is "one updating line per family, or one line
        # per N digs -- one line per SAM call is not". A build with 400
        # digs must therefore emit 3 newline-terminated lines, not 400.
        stream, p = self._progress()
        self._drive(p)
        lines = [ln for ln in stream.getvalue().split('\n') if ln.strip()]
        self.assertEqual(len(lines), 3)
        self.assertIn('MDC2020 3/3 digs', lines[0])
        self.assertIn('MDC2025 2/2 digs', lines[1])
        self.assertIn('built 2 families, 5 digs, 30 members in ', lines[2])

    def test_a_family_line_is_rewritten_in_place(self):
        stream, p = self._progress()
        self._drive(p)
        first = stream.getvalue().split('\n')[0]
        # header + 3 digs + the forced final redraw the next family triggers
        self.assertEqual(first.count('\r'), 5)
        self.assertIn('MDC2020 1/3 digs', first)

    def test_disabled_writes_nothing_at_all(self):
        stream = io.StringIO()
        p = Progress(stream=stream, enabled=False, interval=0.0, clock=_ticking_clock())
        self._drive(p)
        self.assertEqual(stream.getvalue(), '')

    def test_enabled_defaults_to_whether_the_stream_is_a_tty(self):
        self.assertTrue(Progress(stream=_Tty()).enabled)
        self.assertFalse(Progress(stream=io.StringIO()).enabled)

    def test_redraws_are_throttled_between_forced_events(self):
        # A forced redraw (family start/end) always lands; the per-dig
        # ones inside the interval do not.
        stream, p = self._progress(interval=1000.0)
        self._drive(p)
        first = stream.getvalue().split('\n')[0]
        self.assertEqual(first.count('\r'), 2)          # header + the forced final draw


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

    def test_lookup_unknown_is_nonzero(self):
        rc, out, _ = _run(['lookup', 'nope.mu2e.a.b.art'], self.src, self.d)
        self.assertEqual(rc, 1)

    def test_publish_writes_file(self):
        p = os.path.join(_tmpdir(), 'sim_catalog.json')
        rc, out, _ = _run(['publish', '--out', p], self.src, self.d)
        self.assertEqual(rc, 0)
        with open(p) as f:
            self.assertEqual(json.load(f)['generated_at'], '2026-09-03T00:00:00Z')

    def test_progress_is_on_stderr_on_a_tty_and_never_on_stdout(self):
        rc, out, err = _run(['members'], self.src, self.d, tty=True)
        self.assertEqual(rc, 0)
        self.assertIn('epochs: MDC2025 ', err)
        self.assertIn('members in ', err)
        self.assertNotIn('epochs:', out)
        # and stdout is byte-identical to the run that prints no progress
        rc2, out2, err2 = _run(['members'], self.src, self.d)
        self.assertEqual(rc2, 0)
        self.assertEqual(out, out2)
        self.assertNotIn('epochs:', err2)

    def test_progress_is_silent_when_stderr_is_redirected(self):
        # The rule that keeps a redirected log from collecting thousands
        # of progress lines. Nothing is asserted about stdout here that
        # the test above does not already cover.
        for verb in (['members'], ['gaps'], ['retire'],
                     ['publish', '--out', os.path.join(_tmpdir(), 'c.json')]):
            rc, out, err = _run(verb, self.src, self.d)
            self.assertEqual(rc, 0, verb)
            self.assertNotIn('epochs: built', err, verb)

    def test_quiet_suppresses_progress_on_every_verb(self):
        for verb in (['members'], ['gaps'], ['consistency'], ['retire'],
                     ['lookup', f'nts.mu2e.CeEndpointOnSpill.{AU}.root'],
                     ['publish', '--out', os.path.join(_tmpdir(), 'c.json')]):
            rc, out, err = _run(verb + ['--quiet'], self.src, self.d, tty=True)
            self.assertEqual(rc, 0, verb)
            self.assertNotIn('epochs: built', err, verb)

    def test_every_verb_runs_end_to_end_against_a_fake_source(self):
        # The suite has been green three separate times on this branch's
        # parent while a Critical was live, so every verb gets walked,
        # not just the ones a given change is about.
        d = _tmpdir()
        for name in ('MDC2025au', 'MDC2025an'):
            write_epoch_file(d, propose_epoch(name + '_best_v1_0'))
        verbs = [
            (['propose', '--family', 'MDC2025'], 0),
            (['members'], 0),
            (['members', '--family', 'MDC2025', '--tier', 'nts'], 0),
            (['gaps'], 0),
            (['gaps', '--epoch', 'MDC2025au'], 0),
            (['consistency'], 0),
            (['consistency', '--family', 'MDC2025'], 0),
            (['retire'], 0),
            (['retire', '--family', 'MDC2025'], 0),
            (['lookup', f'nts.mu2e.CeEndpointOnSpill.{AU}.root'], 0),
            (['lookup', 'nope.mu2e.a.b.art'], 1),
            (['index-cnfs'], 0),
            (['publish', '--out', os.path.join(_tmpdir(), 'c.json')], 0),
        ]
        for argv, want in verbs:
            rc, out, err = _run(argv, FakeSource(_small_graph()), d)
            self.assertEqual(rc, want, f'{argv}: rc={rc} err={err}')
            rc, out, err = _run(argv + ['--quiet'], FakeSource(_small_graph()), d)
            self.assertEqual(rc, want, f'{argv} --quiet: rc={rc} err={err}')

    def test_malformed_epoch_file_is_exit_2(self):
        with open(os.path.join(self.d, 'MDC2025au.json'), 'w') as f:
            f.write('{"name": "MDC2025au"}')
        rc, _, err = _run(['members'], self.src, self.d)
        self.assertEqual(rc, 2)
        self.assertIn('MDC2025au.json', err)

    def test_family_filter_still_filters_the_printed_rows(self):
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

    def test_foreign_owner_goes_to_stderr(self):
        # Minor 1 (strip review, cf557b4): cat.foreign used to reach the
        # operator folded into input_retirement_refusal's message; that
        # function was deleted with the input subsystem and no
        # replacement line was added, so foreign owners went silent on
        # every CLI verb. _report_noise must carry it, same channel and
        # style as unparseable/unclaimed_digs/missing_families.
        g = _small_graph()
        foreign = f'nts.oksuzian.Private.{AU}.root'
        g[f'mcs.mu2e.CeEndpointOnSpill.{AU}.art']['children'].append(foreign)
        g[foreign] = {'n': 5, 'children': []}
        src = OwnerPolicyFakeSource(g)
        rc, out, err = _run(['members'], src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn(f'foreign owner (walk stops here): {foreign}', err)
        self.assertNotIn(foreign, out)

    def test_members_status_filter(self):
        # Minor 5 (strip review): pre-existing, not a regression -- but
        # cheap. Mutating `members --status` (cli.py) to a no-op left the
        # suite green on both cf557b4 and its parent.
        rc, out, _ = _run(['members', '--status', 'superseded'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', out)
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', out)
        rc, out, _ = _run(['members', '--status', 'current'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}-001.root', out)
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', out)

    def test_retire_family_and_epoch_filter(self):
        # Minor 5 (strip review): _retire_keep (cli.py) had no dedicated
        # test -- mutating it to a no-op left the suite green on both
        # cf557b4 and its parent. It filters retire rows by the MEMBER's
        # own family/epoch (looked up via cat.members[entry['dataset']]),
        # distinct from the plain _keep used by members/gaps/consistency.
        rc, out, _ = _run(['retire', '--family', 'Run1B'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertEqual(out, '')
        rc, out, _ = _run(['retire', '--epoch', 'MDC2025au'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', out)
        rc, out, _ = _run(['retire', '--epoch', 'MDC2025an'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertNotIn(f'nts.mu2e.CeEndpointOnSpill.{AU}.root', out)


class CountingSource(FakeSource):
    """FakeSource with every SAM-shaped entry point recorded, not just
    children/parents. This is the harness the scoping numbers came from:
    counting an injected source is how the cost of a build is measured
    without running production queries in a loop."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls['dig_families'] = []
        self.calls['dig_datasets'] = []

    def dig_families(self):
        self.calls['dig_families'].append('*')
        return super().dig_families()

    def dig_datasets(self, family):
        self.calls['dig_datasets'].append(family)
        return super().dig_datasets(family)

    def lineage_calls(self):
        return len(self.calls['children']) + len(self.calls['parents'])


BAN = 'Run1Ban_best_v1_4'


def _two_family_graph():
    """`_small_graph` plus a whole second family, so a test can watch one
    family's queries not happen."""
    g = _small_graph()
    g[f'dig.mu2e.CosmicCRYSignal.{BAN}.art'] = {
        'n': 20, 'children': [f'mcs.mu2e.CosmicCRYSignal.{BAN}.art']}
    g[f'mcs.mu2e.CosmicCRYSignal.{BAN}.art'] = {
        'n': 20, 'children': [f'nts.mu2e.CosmicCRYSignal.{BAN}.root']}
    g[f'nts.mu2e.CosmicCRYSignal.{BAN}.root'] = {'n': 20, 'children': []}
    # one dig per family with no mcs/nts below it, so `gaps` has a row in
    # each family and a scoped run has something to be equal to
    g[f'dig.mu2e.Lonely.{AU}.art'] = {'n': 1, 'children': []}
    g[f'dig.mu2e.LonelyB.{BAN}.art'] = {'n': 1, 'children': []}
    return g


class TestCliScope(unittest.TestCase):
    """--family restricts the BUILD for members/gaps/lookup/consistency,
    and must not for retire/publish."""

    def setUp(self):
        self.d = _tmpdir()
        for name in (f'{AU}', f'{AN}', f'{BAN}'):
            write_epoch_file(self.d, propose_epoch(name))
        self.src = CountingSource(_two_family_graph())

    def _fresh(self):
        return CountingSource(_two_family_graph())

    # -- the saving ------------------------------------------------------
    def test_family_scopes_the_build_and_never_touches_the_other_family(self):
        rc, out, err = _run(['members', '--family', 'MDC2025'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertEqual(self.src.calls['dig_datasets'], ['MDC2025'])
        # the loaded epoch files already know MDC2025 is a real family, so
        # not even the discovery query is issued
        self.assertEqual(self.src.calls['dig_families'], [])
        asked = self.src.calls['children'] + self.src.calls['parents']
        self.assertFalse([n for n in asked if 'Run1B' in n], asked)

    def test_the_unscoped_build_does_ask_about_both_families(self):
        full = self._fresh()
        rc, out, _ = _run(['members'], full, self.d)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(full.calls['dig_datasets']), ['MDC2025', 'Run1B'])
        self.assertTrue([n for n in full.calls['children'] if 'Run1B' in n])
        scoped = self._fresh()
        _run(['members', '--family', 'MDC2025'], scoped, self.d)
        self.assertLess(scoped.lineage_calls(), full.lineage_calls())

    def test_scoped_rows_equal_the_full_builds_rows_for_that_family(self):
        # The whole safety claim in one assertion: scoping changes which
        # datasets are looked at, never what is decided about them.
        _, full, _ = _run(['members', '--json'], self._fresh(), self.d)
        _, scoped, _ = _run(['members', '--family', 'MDC2025', '--json'], self._fresh(), self.d)
        want = [r for r in json.loads(full) if parse_dsconf(r['epoch']).family == 'MDC2025']
        self.assertTrue(want)
        self.assertEqual(json.loads(scoped), want)

    def test_scoped_gaps_equal_the_full_builds_gaps_for_that_family(self):
        _, full, _ = _run(['gaps', '--json'], self._fresh(), self.d)
        _, scoped, _ = _run(['gaps', '--family', 'MDC2025', '--json'], self._fresh(), self.d)
        want = [r for r in json.loads(full) if parse_dsconf(r['epoch']).family == 'MDC2025']
        self.assertTrue(want)
        self.assertEqual(json.loads(scoped), want)

    # -- --epoch scopes to a FAMILY, never to itself ---------------------
    def test_epoch_scopes_to_its_family_and_no_narrower(self):
        rc, out, _ = _run(['members', '--epoch', 'MDC2025au'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertEqual(self.src.calls['dig_datasets'], ['MDC2025'])
        # MDC2025an is a DIFFERENT epoch of the same family and is still
        # built: an epoch's members compete against the other epochs of
        # its family, so scoping to one epoch would publish wrong statuses
        walked = ' '.join(self.src.calls['children'])
        self.assertIn(f'dig.mu2e.CeEndpointOnSpill.{AN}.art', walked)

    def test_epoch_is_refused_on_a_verb_that_ignores_it(self):
        # `consistency` neither scopes on --epoch nor filters on it.
        # Accepting it silently bought a whole-catalog build and changed
        # nothing about the answer, so it is a usage error (exit 2).
        rc, out, err = _run(['consistency', '--epoch', 'MDC2025au'], self.src, self.d)
        self.assertEqual(rc, 2)
        self.assertIn('--epoch does not apply', err)
        self.assertIn('--family', err)
        self.assertEqual(self.src.calls['dig_datasets'], [])   # refused before any SAM call

    def test_epoch_is_refused_on_lookup_too(self):
        rc, _, err = _run(['lookup', '--epoch', 'MDC2025au', 'x.mu2e.y.z.art'],
                          self.src, self.d)
        self.assertEqual(rc, 2)
        self.assertIn('--epoch does not apply', err)

    def test_epoch_still_works_on_the_verbs_that_use_it(self):
        for verb in ('members', 'gaps'):
            rc, _, _ = _run([verb, '--epoch', 'MDC2025au'], self.src, self.d)
            self.assertEqual(rc, 0, f'{verb} must still accept --epoch')

    # -- what must never be scoped ---------------------------------------
    def test_retire_and_publish_are_not_in_the_scopable_set(self):
        self.assertEqual(epochs_cli.SCOPABLE_VERBS,
                         frozenset({'members', 'gaps', 'lookup', 'consistency'}))

    def test_retire_with_family_still_builds_every_family(self):
        # retire's refusal rests on missing_families/unclaimed_digs, which
        # a scoped build cannot populate for families it never looked at.
        rc, out, err = _run(['retire', '--family', 'MDC2025'], self.src, self.d)
        self.assertEqual(sorted(self.src.calls['dig_datasets']), ['MDC2025', 'Run1B'])
        self.assertTrue([n for n in self.src.calls['children'] if 'Run1B' in n])
        self.assertNotIn('scoped build:', err)

    def test_publish_builds_every_family_and_stamps_no_scope(self):
        p = os.path.join(_tmpdir(), 'c.json')
        rc, out, err = _run(['publish', '--out', p], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(self.src.calls['dig_datasets']), ['MDC2025', 'Run1B'])
        self.assertNotIn('scoped build:', err)
        with open(p) as f:
            doc = json.load(f)
        # no scope stamp is needed because a scoped catalog cannot reach
        # here: publish has no --family flag at all, and is not scopable
        self.assertNotIn('scope', doc)
        self.assertEqual(doc['incomplete'], [])

    # -- errors and visibility -------------------------------------------
    def test_unknown_family_is_exit_2_not_an_empty_result(self):
        rc, out, err = _run(['members', '--family', 'MDC2019'], self.src, self.d)
        self.assertEqual(rc, 2)
        self.assertEqual(out, '')
        self.assertIn('MDC2019', err)
        self.assertIn('unknown family', err)
        # it consulted SAM once before saying so
        self.assertEqual(self.src.calls['dig_families'], ['*'])

    def test_a_family_with_digs_but_no_epoch_file_is_known(self):
        d = _tmpdir()
        write_epoch_file(d, propose_epoch(AU))
        write_epoch_file(d, propose_epoch(AN))
        rc, out, err = _run(['members', '--family', 'Run1B'], self.src, d)
        self.assertEqual(rc, 0)
        self.assertEqual(self.src.calls['dig_families'], ['*'])
        self.assertIn('missing epoch file for family Run1B', err)

    def test_the_scope_notice_survives_quiet_and_a_redirected_stderr(self):
        # A caveat on the rows, not progress: --quiet and a non-TTY
        # stderr silence the progress lines and not this.
        rc, out, err = _run(['members', '--family', 'MDC2025', '--quiet'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertIn('scoped build: this catalog covers MDC2025 only', err)
        self.assertNotIn('epochs: built', err)
        self.assertNotIn('scoped build:', out)

    def test_no_notice_when_nothing_was_scoped(self):
        rc, out, err = _run(['members'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertNotIn('scoped build:', err)

    def test_lookup_scopes_and_says_so(self):
        rc, out, err = _run(['lookup', '--family', 'MDC2025',
                             f'nts.mu2e.CeEndpointOnSpill.{AU}.root'], self.src, self.d)
        self.assertEqual(rc, 0)
        self.assertEqual(self.src.calls['dig_datasets'], ['MDC2025'])
        self.assertEqual(json.loads(out)['kind'], 'member')
        # and a dataset outside the scope reads as unknown, with the
        # notice on stderr saying why
        src = self._fresh()
        rc, out, err = _run(['lookup', '--family', 'MDC2025',
                             f'nts.mu2e.CosmicCRYSignal.{BAN}.root'], src, self.d)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)['kind'], 'unknown')
        self.assertIn('scoped build:', err)


if __name__ == '__main__':
    unittest.main()
