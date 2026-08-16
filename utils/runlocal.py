#!/usr/bin/env python3
"""Run cnf jobs locally, several at a time.

Two uses, one driver: smoking a handful of indices before submitting
(`--nevts 10`), and producing art files on a local disk for study (full
events, no grid). Nothing here touches SAM: no pushOutput, no declare,
no manifest. Outputs stay in the job's directory.

Job prep is the worker's own `runmu2e.process_jobdef`, so a local run
exercises the same tarball fetch, inloc handling, chunk-mode
materialization and `--copy-input` staging the grid will do. The only
part not shared is the tail (push), which is exactly the part a local
run must not have.

Layout — one directory per job:

    <workdir>/job_<index>/       fcl, art outputs, art log, stdout.log

The per-job directory is not cosmetic. `process_jobdef` works in cwd,
and its `--copy-input` branch runs `mkdir indir; mv *.art indir/`;
jobs sharing a directory would move each other's files. Each job
therefore runs as a child process with `cwd=` its own directory —
which also means the driver can print a single command that reproduces
any one job by hand.

Index semantics: `--first`/`--num` name cnf indices directly
(`baseSeed = 1 + index`), and `--indices 0,3,7-9` names them one by
one — for rerunning the exact jobs a grid pass lost, which are rarely
contiguous. The synthesized jobdesc carries no `firstjob`, so there is
no second index space to confuse it with.
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.jobdesc import OUTSTAGE_LOCATION
from utils.jobquery import Mu2eJobPars
from utils.prod_utils import _fetch_file_local
from utils.runmu2e import (
    _synthesize_direct_fname,
    build_mu2e_cmd,
    process_jobdef,
)

# Four concurrent mu2e processes is ~10 GB resident. The driver prints
# that arithmetic rather than guessing a node's free memory.
DEFAULT_PARALLEL = 4
GB_PER_JOB = 2.5

# Same tier whitelist job_outputs() uses to tell a real output file from
# a sink like /dev/null.
_OUTPUT_TIERS = ('dts.', 'dig.', 'sim.', 'rec.', 'mcs.', 'nts.', 'cnf.')


def output_globs(tarball):
    """Glob patterns matching what one job of this cnf writes.

    `Mu2eJobBase.job_outputs` owns the placeholder rules — `.owner.`,
    `.version.`, and the `.sequence.`/`.sequencer.` spelling live
    jobpars use — so this asks it for the names with the sequencer and
    `{desc}` wildcarded rather than resolved. The sequencer of an
    input-driven job is not known until the inputs are resolved, and
    the driver only needs to count and report files afterwards.
    """
    jp = Mu2eJobPars(tarball)
    globs = []
    for name in jp.job_outputs(0, override_desc='*', override_seq='*').values():
        # job_outputs passes non-file targets (a `/dev/null` sink)
        # through untouched; only real datasets are worth globbing for.
        if not name.startswith(_OUTPUT_TIERS) or name in globs:
            continue
        globs.append(name)
    return globs


def parse_indices(spec):
    """`'0,3,7-9'` -> `[0, 3, 7, 8, 9]`, sorted and deduplicated.

    Ranges are inclusive at both ends, matching how a recovery list
    reads ("indices 7 through 9 failed"). Raises ValueError on
    anything else — a typo here would silently run the wrong jobs.
    """
    indices = []
    for token in spec.split(','):
        token = token.strip()
        match = re.fullmatch(r'(\d+)(?:-(\d+))?', token)
        if not match:
            raise ValueError(f"bad index token {token!r} in --indices "
                             f"(want N or A-B, comma separated)")
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) else low
        if high < low:
            raise ValueError(f"reversed range {token!r} in --indices")
        indices.extend(range(low, high + 1))
    if not indices:
        raise ValueError("--indices selected no jobs")
    return sorted(set(indices))


def format_indices(indices):
    """The inverse of `parse_indices`, collapsing runs back to `A-B`.

    Children and rerun lines carry this, so a 200-index window does
    not become a 200-token argv.
    """
    parts = []
    start = prev = None
    for index in indices:
        if start is None:
            start = prev = index
        elif index == prev + 1:
            prev = index
        else:
            parts.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = index
    if start is not None:
        parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ','.join(parts)


def resolve_indices(args):
    """The list of cnf indices to run, from whichever flag named them.

    `--indices` and `--first`/`--num` are alternatives, not layers: a
    run that accepted both would have to decide whether the list is
    clipped to the window, and either answer surprises someone.
    """
    if args.indices is not None:
        if args.first is not None or args.num is not None:
            raise ValueError("--indices and --first/--num are alternatives; "
                             "pass one or the other")
        return parse_indices(args.indices)
    first = 0 if args.first is None else args.first
    num = 1 if args.num is None else args.num
    if first < 0:
        raise ValueError("--first must be >= 0")
    if num < 1:
        raise ValueError("--num must be at least 1")
    return list(range(first, first + num))


def synth_jobdesc(tarball, inloc, indices):
    """The jobdesc `process_jobdef` needs, built from CLI flags.

    `njobs` is one past the largest index because `resolve_entry_index`
    rejects any index >= njobs and — with no `firstjob` — maps an index
    to itself. Gaps in `indices` are not holes in the jobdesc: it
    describes the cnf's index space, and the driver decides which of
    those indices actually run. Deliberately no 'firstjob' key: the
    local runner has ONE index space, the cnf's.

    `outputs` is passthrough for `process_jobdef`; its location is
    never read on this path. It says `outstage` so that a jobdesc that
    escapes into the submission path is refused by `enqueue_entry`
    rather than quietly declaring local test output to SAM.
    """
    return {
        'tarball': str(tarball),
        'inloc': inloc,
        'njobs': max(indices) + 1,
        'outputs': [{'dataset': g, 'location': OUTSTAGE_LOCATION}
                    for g in output_globs(tarball)],
    }


def job_dir(workdir, index):
    """`<workdir>/job_<index>` — zero-padded so `ls` sorts numerically."""
    return Path(workdir) / f"job_{index:06d}"


def child_argv(index, args):
    """argv that re-execs this entry point for a single job.

    Printed in the summary, so it must be runnable verbatim.
    """
    # --indices, never --first/--num: one spelling for the child means
    # one code path, whichever flag the user reached for.
    argv = [sys.executable, str(args.entry_point),
            '--one', str(index),
            '--jobdef', str(args.jobdef),
            '--inloc', args.inloc,
            '--indices', format_indices(args.indices),
            '--nevts', str(args.nevts)]
    if args.mu2e_options.strip():
        # `--opt=value`, not `--opt value`: mu2e options start with a dash
        # and argparse would read the next token as another flag.
        argv.append(f'--mu2e-options={args.mu2e_options}')
    if args.copy_input:
        argv.append('--copy-input')
    if getattr(args, 'code_root', None):
        # Children get the already-unpacked directory, never --code:
        # one unpack serves all of them, and the printed command must
        # reproduce the job without redoing several GB of extraction.
        argv.extend(['--code-root', args.code_root])
    return argv


class JobResult:
    """One finished job: what ran, how it ended, what it left behind."""

    def __init__(self, index, rc, seconds, directory, outputs):
        self.index = index
        self.rc = rc
        self.seconds = seconds
        self.directory = directory
        self.outputs = outputs

    @property
    def ok(self):
        return self.rc == 0


def child_env():
    """The caller's environment minus the one variable that breaks a job.

    Each job sources the cnf's own `simjob_setup`, which calls
    museSetup; museSetup refuses to run when `MUSE_WORK_DIR` is already
    set. So a caller who did `muse setup SimJob <tag>` first — the
    habit, since most prodtools commands want it — loses every job to
    `ERROR - Muse already setup for directory`, a message that names
    Muse and never mentions runlocal. Dropping that one variable is the
    same narrow fix the ksu wrappers use: unset MUSE_WORK_DIR only, not
    MUSE_* (which would take MUSE_DIR with it) and not PATH.
    """
    return {key: value for key, value in os.environ.items()
            if key != 'MUSE_WORK_DIR'}


def _run_child(index, args, globs):
    """Launch one job in its own directory, capturing its output."""
    directory = job_dir(args.workdir, index)
    directory.mkdir(parents=True, exist_ok=True)
    argv = child_argv(index, args)
    start = time.time()
    with open(directory / 'stdout.log', 'w') as log:
        log.write(shlex.join(argv) + '\n')
        log.flush()
        rc = subprocess.run(argv, cwd=str(directory), env=child_env(),
                            stdout=log, stderr=subprocess.STDOUT).returncode
    elapsed = time.time() - start
    produced = sorted(p.name for g in globs for p in directory.glob(g))
    return JobResult(index, rc, elapsed, directory, produced)


def drive(args):
    """Run every index in the window, `args.parallel` at a time.

    Never stops early: a failed index is reported, and the rest still
    run. Returns the process exit code (1 if any job failed).
    """
    indices = args.indices
    globs = output_globs(args.jobdef)

    print(f"[local] {len(indices)} job(s), cnf indices "
          f"{format_indices(indices)}, {args.parallel} at a time")
    print(f"[local] a mu2e job is ~{GB_PER_JOB} GB resident; "
          f"{args.parallel} at a time is ~{GB_PER_JOB * args.parallel:.1f} GB")
    print(f"[local] workdir: {args.workdir}")

    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [pool.submit(_run_child, i, args, globs) for i in indices]
        # as_completed, not submission order: a slow index 0 must not
        # sit on the progress lines of the jobs that already finished.
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            state = 'ok  ' if result.ok else f'FAIL({result.rc})'
            print(f"[local] index {result.index}: {state} "
                  f"{result.seconds:.0f}s  {len(result.outputs)} output(s)")

    return report(results, args)


def report(results, args):
    """Print the end-of-run table; return the exit code."""
    failed = [r for r in sorted(results, key=lambda r: r.index) if not r.ok]
    print("\n=== local run summary ===")
    for result in sorted(results, key=lambda r: r.index):
        print(f"  index {result.index:>6}  rc={result.rc:<3} "
              f"{result.seconds:>7.0f}s  {len(result.outputs):>2} output(s)  "
              f"{result.directory}")
    total = len(results)
    print(f"{total - len(failed)}/{total} succeeded")
    for result in failed:
        print(f"  rerun index {result.index}: "
              f"cd {result.directory} && "
              f"{shlex.join(child_argv(result.index, args))}")
    return 1 if failed else 0


def resolve_jobdef(name_or_path, workdir):
    """Absolute path to the cnf tarball, fetching it once if needed.

    Children run in their own directories, so a bare SAM name would
    otherwise be fetched once per job.
    """
    path = Path(name_or_path)
    if path.is_file():
        return str(path.resolve())
    cwd = os.getcwd()
    os.chdir(workdir)
    try:
        _fetch_file_local(path.name)
    finally:
        os.chdir(cwd)
    return str((Path(workdir) / path.name).resolve())


def unpack_code(tarball, workdir):
    """Unpack a `muse tarball` Code.tar.bz2 once, for every child to share.

    Returns the directory holding `Code/` — what `resolve_setup` wants as
    its code root, and what the grid gets from $INPUT_TAR_DIR_LOCAL.

    ONE unpack, not one per job: the build tree runs to several GB and
    the driver launches four jobs at a time by default. Re-running is
    cheap because an already-unpacked tree is detected and left alone.
    """
    root = Path(workdir) / 'code'
    marker = root / 'Code' / 'setup.sh'
    if marker.is_file():
        print(f"[local] code already unpacked at {root}")
        return str(root)
    root.mkdir(parents=True, exist_ok=True)
    print(f"[local] unpacking {tarball} into {root} "
          f"(several GB — this takes a while)")
    with tarfile.open(tarball, 'r:bz2') as tar:
        tar.extractall(root)
    if not marker.is_file():
        sys.exit(f"runlocal: {tarball} has no Code/setup.sh — "
                 f"build it with `muse tarball`")
    return str(root)


def run_one(index, args):
    """The child: prep and run ONE job in the current directory.

    Returns the mu2e exit code. Everything after mu2e — push, declare,
    manifest — is the worker's job and deliberately absent here.
    """
    jobdesc = synth_jobdesc(args.jobdef, args.inloc, args.indices)
    fcl, simjob_setup, _infiles, _outputs, _inloc = process_jobdef(
        jobdesc, _synthesize_direct_fname(index), args)
    cmd = build_mu2e_cmd(fcl, simjob_setup, args)
    print(f"[local] index {index}: {cmd}")
    return subprocess.run(cmd).returncode


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run cnf jobs locally, several at a time. Outputs stay "
                    "on local disk; nothing is pushed or declared to SAM.")
    parser.add_argument('--jobdef', required=True,
                        help='cnf tarball (path, or a SAM name to fetch)')
    parser.add_argument('--inloc', default='tape',
                        help='where inputs live (tape|disk|resilient|stash|'
                             'dir:<path>|none), default tape')
    parser.add_argument('--first', type=int, default=None,
                        help='first cnf index to run (default 0)')
    parser.add_argument('--num', type=int, default=None,
                        help='how many indices to run (default 1)')
    parser.add_argument('--indices',
                        help='explicit cnf indices instead of a window, '
                             'e.g. 0,3,7-9 (ranges inclusive)')
    parser.add_argument('-j', '--parallel', type=int, default=DEFAULT_PARALLEL,
                        help=f'jobs to run at once (default {DEFAULT_PARALLEL}; '
                             f'~{GB_PER_JOB} GB resident each)')
    parser.add_argument('--workdir', default='.',
                        help='where the per-job directories go (default .)')
    parser.add_argument('--nevts', type=int, default=-1,
                        help='events per job (-1 = whatever the fcl says)')
    parser.add_argument('--mu2e-options', default='',
                        help='extra options passed through to mu2e')
    parser.add_argument('--copy-input', action='store_true',
                        help='stage inputs locally with mdh instead of '
                             'streaming them (worker --copy-input parity)')
    parser.add_argument('--code', default=None,
                        help='muse tarball Code.tar.bz2 to run against '
                             'instead of the cnf\'s /cvmfs setup; unpacked '
                             'once into <workdir>/code')
    parser.add_argument('--code-root', default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument('--one', type=int,
                        help=argparse.SUPPRESS)  # internal: run a single index
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.indices = resolve_indices(args)
    except ValueError as exc:
        sys.exit(f"runlocal: {exc}")
    if args.parallel < 1:
        sys.exit("runlocal: --parallel must be at least 1")

    if args.one is not None:
        # Child: cwd is already this job's directory.
        return run_one(args.one, args)

    args.workdir = str(Path(args.workdir).resolve())
    Path(args.workdir).mkdir(parents=True, exist_ok=True)
    args.jobdef = resolve_jobdef(args.jobdef, args.workdir)
    if args.code:
        args.code_root = unpack_code(args.code, args.workdir)
    # The module, not bin/runlocal: that wrapper sources the Mu2e
    # environment, which this process already has and children inherit.
    args.entry_point = Path(__file__).resolve()
    return drive(args)


if __name__ == '__main__':
    sys.exit(main())
