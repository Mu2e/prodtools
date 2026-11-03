#!/usr/bin/env python3
"""Unit tests for utils/jobwait.py — no grid contact.

jobsub_q and condor_history are faked through the injected `runner`
(the same seam live_clusters exposes), so every queue shape the wait
loop must survive — running, held, error, drained — is a canned string
here, and the suite runs anywhere.

Run with:  python test/test_jobwait.py
       or: python -m pytest test/test_jobwait.py -v
"""

import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.jobwait import (collect_exit_codes, drive, split_jobid,
                           wait_for_drain)

QUIET = lambda *a, **k: None  # noqa: E731 - silence [jobwait] chatter


def _make_tarball(jobpars):
    """In-memory cnf tarball with jobpars.json, written to a temp file.
    Standalone twin of test_unit._make_tarball — deliberately not
    imported, so this file has no coupling to that suite's fixtures."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        jp_bytes = json.dumps(jobpars).encode()
        ti = tarfile.TarInfo(name='jobpars.json')
        ti.size = len(jp_bytes)
        tar.addfile(ti, io.BytesIO(jp_bytes))
    buf.seek(0)
    tmp = tempfile.NamedTemporaryFile(suffix='.tar', delete=False)
    tmp.write(buf.read())
    tmp.close()
    return tmp.name


def _jobpars(njobs=3):
    """EmptyEvent-style jobpars: enough for job_outputs(index)."""
    return {
        "code": "",
        "setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/T/setup.sh",
        "tbs": {
            "njobs": njobs,
            "seed": "services.SeedService.baseSeed",
            "subrunkey": "source.firstSubRun",
            "event_id": {"source.firstRun": 1430,
                         "source.maxEvents": 10},
            "outfiles": {
                "outputs.PrimaryOutput.fileName":
                    "sim.mu2e.TestDesc.TestConf.sequencer.art",
                "outputs.Sink.fileName": "/dev/null",
            },
        },
        "jobname": "cnf.mu2e.TestDesc.TestConf.0.tar",
        "owner": "mu2e",
        "dsconf": "TestConf",
    }


# --- canned jobsub output ---------------------------------------------------

_HEADER = ("JOBSUBJOBID                             OWNER       "
           "SUBMITTED     RUNTIME   ST PRIO   SIZE  COMMAND")


def _q_table(*rows):
    """A trustworthy jobsub_q table: header plus zero or more job rows.
    Row fields mirror the real default table — the state must land in
    fields[5] or _jobsub_table_cluster_states rejects the whole parse."""
    lines = [_HEADER]
    for cluster, state in rows:
        lines.append(f"{cluster}.0@jobsub01.fnal.gov  mu2e  "
                     f"08/16 06:01   0+00:00:00 {state}    0    0.0 runjob.sh")
    lines.append("0 total; 0 completed, 0 removed, 0 idle, 0 running, "
                 "0 held, 0 suspended")
    return '\n'.join(lines) + '\n'


class FakeGrid:
    """Injected `runner`: scripted jobsub_q replies + one history reply.

    Each element of `q_replies` is (returncode, stdout); they are
    consumed one per call, and running past the script is a test bug
    worth crashing on (IndexError) rather than looping forever.
    """

    def __init__(self, q_replies, history_rc=0, history_stdout=''):
        self.q_replies = list(q_replies)
        self.history_rc = history_rc
        self.history_stdout = history_stdout
        self.q_calls = 0
        self.history_cmds = []

    def __call__(self, cmd, capture_output=True, text=True):
        if cmd[0] == 'jobsub_q':
            self.q_calls += 1
            rc, out = self.q_replies.pop(0)
            return SimpleNamespace(returncode=rc, stdout=out, stderr='')
        if cmd[0] == 'condor_history':
            self.history_cmds.append(cmd)
            return SimpleNamespace(returncode=self.history_rc,
                                   stdout=self.history_stdout, stderr='')
        raise AssertionError(f"unexpected command {cmd!r}")


class TestSplitJobid(unittest.TestCase):

    def test_bare_cluster(self):
        self.assertEqual(split_jobid('12345'), ('12345', '12345'))

    def test_cluster_at_schedd(self):
        self.assertEqual(split_jobid('12345@jobsub01.fnal.gov'),
                         ('12345', '12345@jobsub01.fnal.gov'))

    def test_full_jobsub_id_with_proc(self):
        cluster, jobid = split_jobid('12345.0@jobsub01.fnal.gov')
        self.assertEqual(cluster, '12345')

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            split_jobid('cluster.txt')


class TestWaitForDrain(unittest.TestCase):
    """The loop's whole job is knowing what does NOT mean 'done'."""

    def _wait(self, grid):
        sleeps = []
        wait_for_drain('777', '777@s', 300, runner=grid,
                       sleeper=sleeps.append, log=QUIET)
        return sleeps

    def test_running_then_drained(self):
        grid = FakeGrid([(0, _q_table(('777', 'R'))),
                         (0, _q_table())])
        sleeps = self._wait(grid)
        self.assertEqual(grid.q_calls, 2)
        self.assertEqual(sleeps, [300])

    def test_error_snapshot_never_drains(self):
        # A failed query is 'error', never 'drained' — the fail-closed
        # rule. Two bad snapshots must produce two more polls, not an
        # early exit.
        grid = FakeGrid([(1, ''),
                         (0, 'Unexpected error page'),
                         (0, _q_table())])
        sleeps = self._wait(grid)
        self.assertEqual(grid.q_calls, 3)
        self.assertEqual(len(sleeps), 2)

    def test_held_is_waited_out(self):
        grid = FakeGrid([(0, _q_table(('777', 'H'))),
                         (0, _q_table())])
        self._wait(grid)
        self.assertEqual(grid.q_calls, 2)

    def test_terminal_only_rows_are_drained(self):
        # C/X rows linger in the queue briefly; a cluster with nothing
        # else left is done.
        grid = FakeGrid([(0, _q_table(('777', 'C'), ('777', 'X')))])
        sleeps = self._wait(grid)
        self.assertEqual(sleeps, [])

    def test_absent_from_first_snapshot_returns_immediately(self):
        # Mistyped cluster / drained-before-first-poll: fall through to
        # history (whose empty answer becomes `unknown`), never hang.
        grid = FakeGrid([(0, _q_table(('999', 'R')))])
        sleeps = self._wait(grid)
        self.assertEqual(grid.q_calls, 1)
        self.assertEqual(sleeps, [])


class TestCollectExitCodes(unittest.TestCase):

    def test_rows_parsed_header_skipped(self):
        grid = FakeGrid([], history_stdout=f"0 0\n1 84\n\n{_HEADER}\n")
        codes = collect_exit_codes('777@s', 2, runner=grid, log=QUIET)
        self.assertEqual(codes, {0: 0, 1: 84})

    def test_undefined_exitcode_stays_unknown(self):
        # Removed jobs / exited-by-signal have no ExitCode attribute;
        # condor prints 'undefined'. That proc must not appear at all —
        # absent is what the caller reports as unknown.
        grid = FakeGrid([], history_stdout="0 0\n1 undefined\n")
        codes = collect_exit_codes('777@s', 2, runner=grid, log=QUIET)
        self.assertEqual(codes, {0: 0})

    def test_rerun_proc_first_record_wins(self):
        # History is newest-first; a re-run proc appears twice and the
        # newest record is the one that counts.
        grid = FakeGrid([], history_stdout="1 0\n1 84\n")
        codes = collect_exit_codes('777@s', 2, runner=grid, log=QUIET)
        self.assertEqual(codes, {1: 0})

    def test_query_failure_returns_empty(self):
        grid = FakeGrid([], history_rc=1)
        self.assertEqual(
            collect_exit_codes('777@s', 2, runner=grid, log=QUIET), {})

    def test_limit_and_jobid_in_command(self):
        # -name <schedd> is the load-bearing part: jobsub_lite 1.13's
        # jobsub_history drops it and always queries the default
        # SCHEDD_HOST, which is how a fully successful jobsub05 cluster
        # was reported 0/N ok (2026-08-20). condor_history is called
        # directly, schedd split out of the jobid.
        grid = FakeGrid([], history_stdout="0 0\n")
        collect_exit_codes('777@jobsub01.fnal.gov', 40,
                           runner=grid, log=QUIET)
        cmd = grid.history_cmds[0]
        self.assertEqual(cmd[:3], ['condor_history', '-name',
                                   'jobsub01.fnal.gov'])
        self.assertIn('777', cmd)
        self.assertNotIn('777@jobsub01.fnal.gov', cmd)
        self.assertIn('-limit', cmd)
        self.assertIn('40', cmd)

    def test_bare_cluster_queries_without_name(self):
        # A caller that lost the schedd still gets a query — against
        # the node's default schedd only, which is the best available.
        grid = FakeGrid([], history_stdout="0 0\n")
        collect_exit_codes('777', 1, runner=grid, log=QUIET)
        cmd = grid.history_cmds[0]
        self.assertEqual(cmd[0], 'condor_history')
        self.assertNotIn('-name', cmd)
        self.assertIn('777', cmd)

    def test_empty_history_names_schedd(self):
        # Zero usable rows must be announced as history-unavailable on
        # the named schedd — not left to read as N failed jobs.
        lines = []
        grid = FakeGrid([], history_stdout='')
        codes = collect_exit_codes('777@jobsub05.fnal.gov', 2,
                                   runner=grid, log=lines.append)
        self.assertEqual(codes, {})
        self.assertTrue(any('jobsub05.fnal.gov' in l and '777' in l
                            for l in lines), lines)

    def test_nonempty_history_no_empty_warning(self):
        lines = []
        grid = FakeGrid([], history_stdout="0 0\n")
        collect_exit_codes('777@jobsub05.fnal.gov', 1,
                           runner=grid, log=lines.append)
        self.assertFalse(any('no usable records' in l for l in lines),
                         lines)


class TestDrive(unittest.TestCase):
    """drive() end to end over a real (fake) cnf tarball."""

    def setUp(self):
        self.jobdef = _make_tarball(_jobpars(njobs=3))
        self.addCleanup(os.unlink, self.jobdef)
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._rm_tmpdir)
        self.json_path = os.path.join(self.tmpdir, 'wait.json')

    def _rm_tmpdir(self):
        for name in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _args(self, **kw):
        base = dict(jobdef=self.jobdef, cluster='777@jobsub01.fnal.gov',
                    njobs=3, first=0, poll_s=1,
                    outstage=None, json=self.json_path)
        base.update(kw)
        return SimpleNamespace(**base)

    def _drive(self, args, history_stdout):
        grid = FakeGrid([(0, _q_table())], history_stdout=history_stdout)
        rc = drive(args, runner=grid, sleeper=lambda s: None, log=QUIET)
        with open(self.json_path) as fh:
            return rc, json.load(fh)

    def test_all_ok_exits_zero(self):
        rc, data = self._drive(self._args(), "0 0\n1 0\n2 0\n")
        self.assertEqual(rc, 0)
        self.assertEqual(data['ok'], 3)
        self.assertEqual(data['failed'], [])
        self.assertEqual(data['unknown'], [])

    def test_one_failure_exits_one_json_still_written(self):
        rc, data = self._drive(self._args(), "0 0\n1 84\n2 0\n")
        self.assertEqual(rc, 1)
        self.assertEqual(data['ok'], 2)
        self.assertEqual(data['failed'], [1])

    def test_short_history_reports_unknown_not_ok(self):
        # Two records for three jobs: the third is unknown — rc null in
        # the JSON, counted in `unknown`, never in `ok`, and the exit
        # code is nonzero. An unverifiable job is not a successful one.
        rc, data = self._drive(self._args(), "0 0\n1 0\n")
        self.assertEqual(rc, 1)
        self.assertEqual(data['ok'], 2)
        self.assertEqual(data['failed'], [])
        self.assertEqual(data['unknown'], [2])
        self.assertIsNone(data['jobs'][2]['rc'])

    def test_first_offset_maps_proc_to_cnf_index(self):
        rc, data = self._drive(self._args(first=100), "0 0\n1 84\n2 0\n")
        self.assertEqual([j['index'] for j in data['jobs']],
                         [100, 101, 102])
        self.assertEqual(data['failed'], [101])
        self.assertEqual([j['proc'] for j in data['jobs']], [0, 1, 2])

    def test_outstage_makes_output_paths_absolute(self):
        _, data = self._drive(self._args(outstage='/pnfs/out/'),
                              "0 0\n1 0\n2 0\n")
        for proc, job in enumerate(data['jobs']):
            self.assertTrue(job['outputs'],
                            "cnf declares an output; none reported")
            for path in job['outputs']:
                self.assertTrue(
                    path.startswith(f"/pnfs/out/777/{proc}/sim."),
                    path)

    def test_dev_null_sink_not_reported_as_output(self):
        _, data = self._drive(self._args(), "0 0\n1 0\n2 0\n")
        for job in data['jobs']:
            for path in job['outputs']:
                self.assertNotIn('/dev/null', path)

    def test_summary_speaks_runlocal_core_schema(self):
        # The shared contract: a caller reads one schema whether the
        # stage ran via runlocal or jobwait. Core keys per job and per
        # summary must match runlocal's summary().
        _, data = self._drive(self._args(), "0 0\n1 0\n2 0\n")
        for key in ('jobdef', 'jobs', 'ok', 'failed'):
            self.assertIn(key, data)
        for job in data['jobs']:
            for key in ('index', 'rc', 'outputs'):
                self.assertIn(key, job)

    def test_json_written_atomically(self):
        self._drive(self._args(), "0 0\n1 0\n2 0\n")
        self.assertEqual(os.listdir(self.tmpdir), ['wait.json'])


if __name__ == '__main__':
    unittest.main()
