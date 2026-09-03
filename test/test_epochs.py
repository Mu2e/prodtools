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


if __name__ == '__main__':
    unittest.main()
