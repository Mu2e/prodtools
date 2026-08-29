"""Direct tests for utils/queue_state.py's table grammar.

The trust rule here ("any unrecognized line fails the WHOLE parse")
guards the count that top-up feeds the farm from: a miscount either
floods the farm or starves campaigns. Until 2026-08-28 the rule was
encoded twice (two near-identical parsers) and tested only indirectly
through fake subprocess runs in test_unit.py; the flat parser is now a
projection of the cluster parser, pinned here.

Table shape mirrors the captured 2026-07-21 jobsub_lite DEFAULT table
(same fixture shape as test_unit.py's queue tests).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import queue_state as qs

HDR = ('JOBSUBJOBID                             OWNER       \t'
       'SUBMITTED     RUNTIME   ST PRIO   SIZE  COMMAND')
SUM = ('2 total; 0 completed, 0 removed, 1 idle, 1 running, '
       '0 held, 0 suspended')
NOISE = ('Attempting to get token from https://htvaultprod.fnal.gov ...',
         'Storing bearer token in /tmp/bt_token_mu2e_Analysis_12345')


def row(jobid, st, owner='mu2epro'):
    return (f'{jobid}            {owner}   \t01/09 06:00   '
            f'0+00:00:00 {st}    0    0.0 job.sh ')


def table(*rows, header=HDR, summary=SUM):
    return '\n'.join([header, summary, *rows]) + '\n'


class TestClusterParse(unittest.TestCase):
    def test_groups_by_cluster(self):
        out = table(row('111.0@jobsub01.fnal.gov', 'R'),
                    row('111.1@jobsub01.fnal.gov', 'I'),
                    row('222.0@jobsub02.fnal.gov', 'H'))
        self.assertEqual(qs._jobsub_table_cluster_states(out),
                         {'111': ['R', 'I'], '222': ['H']})

    def test_drained_table_is_empty_dict_not_none(self):
        # Header + summary, zero rows: a real answer ("nothing queued"),
        # acted on very differently from None ("do not trust").
        self.assertEqual(qs._jobsub_table_cluster_states(table()), {})

    def test_missing_header_is_none(self):
        self.assertIsNone(qs._jobsub_table_cluster_states(SUM + '\n'))

    def test_unrecognized_line_fails_whole_parse(self):
        out = table(row('111.0@jobsub01.fnal.gov', 'R'),
                    'unexpected garbage line')
        self.assertIsNone(qs._jobsub_table_cluster_states(out))

    def test_unknown_state_letter_fails_whole_parse(self):
        out = table(row('111.0@jobsub01.fnal.gov', 'Z'))
        self.assertIsNone(qs._jobsub_table_cluster_states(out))

    def test_malformed_jobid_fails_whole_parse(self):
        out = table(row('not-a-jobid', 'R'))
        self.assertIsNone(qs._jobsub_table_cluster_states(out))

    def test_token_noise_is_skipped(self):
        out = '\n'.join([*NOISE, HDR, SUM,
                         row('111.0@jobsub01.fnal.gov', 'R')]) + '\n'
        self.assertEqual(qs._jobsub_table_cluster_states(out),
                         {'111': ['R']})

    def test_dag_worker_rows_parse_like_any_other(self):
        out = table(row('4.0@jobsub02.fnal.gov', 'H', owner='|-WORKER_12'))
        self.assertEqual(qs._jobsub_table_cluster_states(out), {'4': ['H']})


class TestFlatProjection(unittest.TestCase):
    def test_matches_cluster_parse(self):
        out = table(row('111.0@jobsub01.fnal.gov', 'R'),
                    row('111.1@jobsub01.fnal.gov', 'I'),
                    row('222.0@jobsub02.fnal.gov', 'H'))
        flat = qs._jobsub_table_states(out)
        grouped = qs._jobsub_table_cluster_states(out)
        self.assertCountEqual(flat,
                              [s for v in grouped.values() for s in v])

    def test_untrusted_projection_is_none(self):
        self.assertIsNone(qs._jobsub_table_states('garbage\n'))

    def test_empty_table_is_empty_list(self):
        self.assertEqual(qs._jobsub_table_states(table()), [])


if __name__ == '__main__':
    unittest.main()
