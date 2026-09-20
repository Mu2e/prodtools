#!/usr/bin/env python3
"""
stageEff - efficiency of a chain of simulation stages, straight from the data.

Each stage is either a list of art files (declared in SAM or not) or a dataset
name. Per stage it reports generated events, events in the files, and their
ratio; the chain efficiency is the product, e.g. POT -> MuBeam -> stops gives
stops per POT.

Why not genFilterEff / SimEfficiencies2: those are per stage, need the
datasets registered, and fold any prescale into the number. Why not carry the
count through the resampler: the mixer never sees a subrun that passed zero
events, so sparse stages read high.

A file stage is read by stage_eff_reader.py, which needs ROOT with the Mu2e
dictionaries. If this interpreter has no ROOT (the ops environment), the
reader runs in a clean `muse setup SimJob` shell instead.
"""
import argparse
import glob
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

READER = str(Path(__file__).with_name('stage_eff_reader.py'))
MU2E_SETUP = '/cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh'
# What a clean shell still needs: identity, and where the bearer token lives
# for xrootd reads of /pnfs files.
_KEPT_ENV = ('HOME', 'USER', 'LOGNAME', 'XDG_RUNTIME_DIR', 'BEARER_TOKEN_FILE',
             'BEARER_TOKEN', 'KRB5CCNAME')


class StageEffError(Exception):
    """A stage could not be evaluated. Never downgraded to a partial number."""


def is_dataset_name(arg):
    """A stage argument is a dataset name when it has the five dot-separated
    fields of one and is not a file on disk or a URL."""
    if '/' in arg or os.path.exists(arg):
        return False
    return len(arg.split('.')) == 5


def expand_files(args):
    """Expand local globs, keep URLs as given, drop exact duplicates.

    The same path twice is harmless and removed here. Different files holding
    the same subrun (a copy, a concatenation next to its inputs) are caught by
    the reader, which refuses them."""
    files = []
    for arg in args:
        if '://' in arg:
            matches = [arg]
        else:
            matches = sorted(glob.glob(arg))
            if not matches:
                raise StageEffError(f'no file matches {arg!r}')
            matches = [os.path.realpath(m) for m in matches]
        for match in matches:
            if match not in files:
                files.append(match)
    return files


def reader_command(files, musing=None):
    """Command and environment that run the reader where ROOT exists."""
    if importlib.util.find_spec('ROOT') is not None:
        return [sys.executable, READER, *files], None
    # muse setup runs once per shell and the caller's shell already spent it
    # on ops, so start from a clean environment.
    setup = f'muse setup SimJob {musing}' if musing else 'muse setup SimJob'
    script = (f'source {MU2E_SETUP} >&2 && {setup} >&2 && '
              'exec python3 "$0" "$@"')
    env = {k: os.environ[k] for k in _KEPT_ENV if k in os.environ}
    env['PATH'] = '/usr/bin:/bin'
    return ['bash', '-c', script, READER, *files], env


def read_files(args, musing=None, run=subprocess.run):
    """Counts for a stage given as files."""
    files = expand_files(args)
    cmd, env = reader_command(files, musing)
    proc = run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StageEffError(
            f'reader failed (exit {proc.returncode}): '
            f'{proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "no output"}')
    lines = proc.stdout.strip().splitlines()
    if not lines:
        raise StageEffError('reader printed nothing')
    counts = json.loads(lines[-1])
    counts['source'] = 'files'
    return counts


def _samweb():
    """Imported on first use: a file stage must work in a SimJob shell, which
    has ROOT but no samweb_client."""
    try:
        from . import samweb_wrapper
    except ImportError:
        # Standalone script: not run as a package.
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from utils import samweb_wrapper
    return samweb_wrapper


def read_dataset(dsname, samweb=None):
    """Counts for a stage given as a dataset name, from SAM metadata - the
    same two fields genFilterEff sums.

    Unlike genFilterEff, a file without dh.gencount is an error here: the
    number feeds a normalization, and a silently shorter denominator is worse
    than no answer. SAM has no prescale information."""
    samweb = samweb or _samweb()
    names = samweb.files_in_dataset(dsname, availability='anylocation')
    if not names:
        raise StageEffError(f'no files in SAM for dataset {dsname}')
    gen = passed = 0
    for metadata in samweb.metadata_for_files(names):
        if 'dh.gencount' not in metadata:
            raise StageEffError(
                f'no dh.gencount in SAM metadata of {metadata.get("file_name", "unknown")}')
        gen += metadata['dh.gencount']
        # SAM omits event_count when it is zero
        passed += metadata.get('event_count', 0)
    return {'source': 'sam', 'files': len(names), 'subruns': None,
            'gen': gen, 'passed': passed, 'prescales': None}


def read_stage(args, musing=None):
    """One stage: a single dataset name, or files."""
    if len(args) == 1 and is_dataset_name(args[0]):
        return read_dataset(args[0])
    if any(is_dataset_name(a) for a in args):
        raise StageEffError(
            'a stage is one dataset name OR files, not a mix: ' + ' '.join(args))
    return read_files(args, musing)


def evaluate(stage_counts, prescales=None):
    """Per-stage and chain efficiencies.

    stage_counts: list of count dicts in chain order (upstream first).
    prescales: {stage number (1-based): module label} - the prescale that
    sits in that stage's OUTPUT path. A job's subrun holds the prescales of
    all its paths and nothing in the file says which one fed this output, so
    it is never chosen for the caller.

    `eff` is passed/gen as stored. `eff_unprescaled` undoes the selected
    prescale: eff * seen/passed of that filter.
    """
    prescales = prescales or {}
    unknown = set(prescales) - set(range(1, len(stage_counts) + 1))
    if unknown:
        raise StageEffError(f'--prescale names stage(s) {sorted(unknown)} '
                            f'but there are {len(stage_counts)} stages')
    stages = []
    chain = chain_unprescaled = 1.0
    for number, counts in enumerate(stage_counts, start=1):
        if counts['gen'] <= 0:
            raise StageEffError(f'stage {number}: generated count is {counts["gen"]}')
        eff = counts['passed'] / counts['gen']
        stage = dict(counts, stage=number, eff=eff, eff_unprescaled=eff,
                     prescale=None)
        label = prescales.get(number)
        if label is not None:
            available = counts['prescales']
            if not available or label not in available:
                raise StageEffError(
                    f'stage {number}: no prescale {label!r}; available: '
                    f'{sorted(available) if available else "none (SAM has no prescale data - pass files)"}')
            tally = available[label]
            if tally['passed'] <= 0:
                raise StageEffError(f'stage {number}: prescale {label!r} passed no events')
            stage['prescale'] = label
            stage['eff_unprescaled'] = eff * tally['seen'] / tally['passed']
        chain *= stage['eff']
        chain_unprescaled *= stage['eff_unprescaled']
        stages.append(stage)
    return {'stages': stages, 'chain_eff': chain,
            'chain_eff_unprescaled': chain_unprescaled}


def stage_efficiency(stages, prescales=None, musing=None):
    """Library entry point: stages is a list of argument lists."""
    if not stages:
        raise StageEffError('at least one stage is required')
    return evaluate([read_stage(args, musing) for args in stages], prescales)


def parse_prescales(items):
    """['2:TargetStopPrescaleFilter'] -> {2: 'TargetStopPrescaleFilter'}"""
    result = {}
    for item in items or []:
        number, sep, label = item.partition(':')
        if not sep or not number.isdigit() or not label:
            raise StageEffError(f'--prescale wants STAGE:LABEL, got {item!r}')
        result[int(number)] = label
    return result


def format_text(result):
    lines = []
    for stage in result['stages']:
        subruns = f', {stage["subruns"]} subruns' if stage['subruns'] is not None else ''
        lines.append(f'stage {stage["stage"]} [{stage["source"]}: {stage["files"]} files{subruns}]')
        lines.append(f'  generated {stage["gen"]}  passed {stage["passed"]}  eff {stage["eff"]:.6g}')
        if stage['prescales'] is None:
            lines.append('  prescales: not available from SAM (pass files to see them)')
        for label, tally in sorted((stage['prescales'] or {}).items()):
            mark = '  <- selected' if label == stage['prescale'] else ''
            fraction = tally['passed'] / tally['seen'] if tally['seen'] else float('nan')
            lines.append(f'  prescale {label}: {tally["passed"]}/{tally["seen"]} = {fraction:.6g}{mark}')
        if stage['prescale']:
            lines.append(f'  eff without prescale {stage["eff_unprescaled"]:.6g}')
    lines.append(f'chain eff {result["chain_eff"]:.6g}')
    if result['chain_eff_unprescaled'] != result['chain_eff']:
        lines.append(f'chain eff without prescales {result["chain_eff_unprescaled"]:.6g}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Efficiency of a chain of simulation stages, read from '
                    'art files (declared or not) or from SAM dataset metadata.')
    parser.add_argument('--stage', action='append', nargs='+', required=True,
                        metavar='FILE_OR_DATASET',
                        help='One stage, upstream first: art files/globs/xrootd '
                             'URLs, or a single dataset name. Repeat per stage.')
    parser.add_argument('--prescale', action='append', metavar='STAGE:LABEL',
                        help='Undo this prescale filter in stage number STAGE '
                             '(1-based), e.g. 2:TargetStopPrescaleFilter.')
    parser.add_argument('--musing', help='SimJob musing for the file reader when '
                                         'this shell has no ROOT (default: current SimJob)')
    parser.add_argument('--json', action='store_true', help='Print JSON')
    args = parser.parse_args(argv)

    try:
        result = stage_efficiency(args.stage, parse_prescales(args.prescale),
                                  args.musing)
    except StageEffError as exc:
        print(f'stageEff: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else format_text(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
