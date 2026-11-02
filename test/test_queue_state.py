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
import contextlib
import io
import os
import sys
import types
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


# condor_q's default reply for one cluster, as captured 2026-11-02 from
# the jobsub_lite wrapper (HTCondor 25.0.14). The wrapper exits 0 for an
# unknown schedd too, printing only to stderr.
def cq_reply(schedd, idle=0, running=0, held=0, all_users=True):
    total = idle + running + held
    lines = ['', '',
             f'-- Schedd: {schedd} : <131.225.161.135:9615?... '
             f'@ 11/02/26 11:20:22',
             'OWNER   BATCH_NAME      SUBMITTED   DONE   RUN    IDLE  '
             'TOTAL JOB_IDS',
             '',
             f'Total for query: {total} jobs; 0 completed, 0 removed, '
             f'{idle} idle, {running} running, {held} held, 0 suspended ']
    if all_users:
        lines.append('Total for all users: 4700 jobs; 0 completed, '
                     '0 removed, 2718 idle, 1574 running, 408 held, '
                     '0 suspended')
    return '\n'.join(lines) + '\n'


def runner_for(stdout, rc=0, stderr='', seen=None):
    def run(cmd, capture_output=True, text=True):
        if seen is not None:
            seen.append(cmd)
        return types.SimpleNamespace(returncode=rc, stdout=stdout,
                                     stderr=stderr)
    return run


class TestScheddOf(unittest.TestCase):
    def test_full_jobsub_id(self):
        self.assertEqual(qs.schedd_of('87390804.0@jobsub01.fnal.gov'),
                         'jobsub01.fnal.gov')

    def test_no_schedd(self):
        for jid in (None, '', '87390804', '87390804.0@'):
            self.assertIsNone(qs.schedd_of(jid))


class TestScheddClusterState(unittest.TestCase):
    """The positive drain proof: only the cluster's own schedd, provably
    answering, can call it drained (2026-11-02 incident)."""

    S = 'jobsub01.fnal.gov'

    def state(self, stdout, rc=0, stderr='', schedd=S):
        with contextlib.redirect_stdout(io.StringIO()):
            return qs.schedd_cluster_state(
                '87390804', schedd, runner=runner_for(stdout, rc, stderr))

    def test_asks_the_named_schedd_for_the_cluster(self):
        seen = []
        with contextlib.redirect_stdout(io.StringIO()):
            qs.schedd_cluster_state('87390804', self.S,
                                    runner=runner_for(cq_reply(self.S),
                                                      seen=seen))
        self.assertEqual(seen, [['condor_q', '-name', self.S, '87390804']])

    def test_nothing_left_is_drained(self):
        self.assertEqual(self.state(cq_reply(self.S)), 'drained')

    def test_running_or_idle_is_running(self):
        self.assertEqual(self.state(cq_reply(self.S, running=21)), 'running')
        self.assertEqual(self.state(cq_reply(self.S, idle=3)), 'running')

    def test_running_wins_over_held(self):
        self.assertEqual(self.state(cq_reply(self.S, running=1, held=2)),
                         'running')

    def test_only_held_is_held(self):
        self.assertEqual(self.state(cq_reply(self.S, held=2)), 'held')

    def test_unknown_schedd_exit_zero_is_error(self):
        # The captured reply for a schedd the collector does not know.
        self.assertEqual(
            self.state('', stderr='Error: Collector has no record of '
                                  'schedd/submitter\n'), 'error')

    def test_reply_from_another_schedd_is_error(self):
        self.assertEqual(self.state(cq_reply('jobsub02.fnal.gov')), 'error')

    def test_missing_whole_schedd_total_is_error(self):
        self.assertEqual(self.state(cq_reply(self.S, all_users=False)),
                         'error')

    def test_nonzero_exit_is_error_even_with_a_good_reply(self):
        self.assertEqual(self.state(cq_reply(self.S), rc=1), 'error')

    def test_command_that_cannot_run_is_error(self):
        def boom(cmd, **kw):
            raise FileNotFoundError('condor_q')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                qs.schedd_cluster_state('1', self.S, runner=boom), 'error')

    def test_no_schedd_is_error_without_asking(self):
        seen = []
        with contextlib.redirect_stdout(io.StringIO()):
            state = qs.schedd_cluster_state(
                '1', None, runner=runner_for(cq_reply(self.S), seen=seen))
        self.assertEqual(state, 'error')
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
