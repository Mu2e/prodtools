"""Job cost from a log dataset: CPU, wall time and memory per job.

Every art job log carries `TimeReport CPU = <s> Real = <s>` and
`MemReport VmPeak = <MB> VmHWM = <MB>`. Parsing is utils/logparser.py's
(the `logparser` CLI), so this tool and the CLI cannot disagree on what a
log says.

A log dataset holds one file per job, often 50,000 or more, so the tool
reads an evenly spaced sample and scales the mean by the exact log count.
Reading every log is never needed for a cost estimate: on
log.mu2e.CosmicCRYAll.Run1Bah.log (9,954 logs) a 500-log mean was within
1.4% of the full scan.
"""
import os
import statistics
from concurrent.futures import ThreadPoolExecutor

from prodtools_mcp.adapters import ToolError, classify_catalog_error
from utils.job_common import Mu2eName

DEFAULT_SAMPLE = 500
MAX_SAMPLE = 5000
_WORKERS = 16
# Logs are read from these locations, in this order. Production logs live
# on persistent disk, personal ones on scratch; a tape log would need a
# stage first, so it is reported, not read.
_READ_LOCATIONS = ('disk', 'scratch')

BASIS = ('art TimeReport and MemReport lines of the job logs. Only jobs '
         'that declared a log are counted: an attempt that failed before '
         'pushing its log is missing, so totals are lower bounds.')


def _default_names_fn(dataset):
    from utils.samweb_wrapper import files_in_dataset
    return files_in_dataset(dataset)


def _default_dataset_dir_fn(dataset, location):
    from utils.file_resolver import dataset_dir
    return dataset_dir(dataset, location)


def _default_parse_fn(path):
    from utils.logparser import parse_log_file
    return parse_log_file(path)


def spread_sample(names, k):
    """`k` names spread evenly over sorted `names`, or all of them when
    k >= len(names). Even spacing, not the first k: names sort by job
    index, and recovery passes and late slices sit at the end."""
    names = sorted(names)
    n = len(names)
    if k >= n:
        return names
    return [names[(i * n) // k] for i in range(k)]


def _stats(values):
    if not values:
        return None
    vals = sorted(values)
    p90 = vals[min(len(vals) - 1, int(round(0.9 * (len(vals) - 1))))]
    return {'mean': round(statistics.fmean(vals), 3),
            'median': round(statistics.median(vals), 3),
            'p90': round(p90, 3), 'max': round(vals[-1], 3)}


def log_metrics(dataset, sample=DEFAULT_SAMPLE, names_fn=None,
                dataset_dir_fn=None, parse_fn=None, isdir_fn=os.path.isdir):
    """CPU, wall time and memory per job from a log dataset, plus the
    estimated total CPU hours (mean per job × number of logs)."""
    if (not isinstance(sample, int) or isinstance(sample, bool)
            or sample < 1 or sample > MAX_SAMPLE):
        raise ToolError('invalid_argument',
                        f'sample must be an integer in 1..{MAX_SAMPLE}, '
                        f'got {sample!r}',
                        f'Omit it for the default of {DEFAULT_SAMPLE}.')
    try:
        name = Mu2eName(dataset)
    except ValueError as exc:
        raise ToolError('invalid_argument', f'{dataset}: {exc}',
                        'Pass a log dataset name, '
                        'log.<owner>.<desc>.<dsconf>.log.') from exc
    if not name.is_dataset or name.tier != 'log':
        raise ToolError('invalid_argument',
                        f'{dataset} is not a log dataset',
                        'Pass log.<owner>.<desc>.<dsconf>.log. The log desc '
                        'is the job\'s, which can differ from its output\'s '
                        '(MuBeamCat comes from log.mu2e.POT_Run1_b...); '
                        'find_datasets(tier="log", desc=...) lists them.')

    try:
        names = list((names_fn or _default_names_fn)(dataset))
    except Exception as exc:
        raise classify_catalog_error(
            exc, f'file listing failed for {dataset}: {exc}') from exc
    if not names:
        raise ToolError('not_found', f'{dataset} has no files in SAM',
                        'Check the name with find_datasets(tier="log", '
                        'require_files=True).')

    dir_fn = dataset_dir_fn or _default_dataset_dir_fn
    location = root = None
    for loc in _READ_LOCATIONS:
        candidate = dir_fn(dataset, loc)
        if candidate and isdir_fn(candidate):
            location, root = loc, candidate
            break
    if root is None:
        raise ToolError('not_found',
                        f'{dataset}: no log directory on '
                        f'{" or ".join(_READ_LOCATIONS)}',
                        'Logs on tape must be staged before they can be '
                        'read; see /prestage.')

    picked = spread_sample(names, sample)
    parse = parse_fn or _default_parse_fn

    def read(fname):
        path = f'{root}/{Mu2eName.parse(fname).relpathname()}'
        try:
            return fname, parse(path), None
        except OSError as exc:
            return fname, None, f'{type(exc).__name__}: {exc}'

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(read, picked))

    cpu, wall, hwm, peak = [], [], [], []
    unreadable, no_timereport = [], []
    for fname, metrics, err in results:
        if err is not None:
            unreadable.append(fname)
            continue
        if metrics.get('CPU [h]') is None:
            no_timereport.append(fname)
            continue
        cpu.append(metrics['CPU [h]'])
        if metrics.get('Real [h]') is not None:
            wall.append(metrics['Real [h]'])
        if metrics.get('VmHWM [GB]') is not None:
            hwm.append(metrics['VmHWM [GB]'])
        if metrics.get('VmPeak [GB]') is not None:
            peak.append(metrics['VmPeak [GB]'])

    if not cpu:
        raise ToolError('not_found',
                        f'{dataset}: none of {len(picked)} sampled logs has '
                        f'a TimeReport line ({len(unreadable)} unreadable, '
                        f'{len(no_timereport)} without one)',
                        'Open one log by hand to see how the job ended.')

    cpu_stats = _stats(cpu)
    n_logs = len(names)
    return {
        'dataset': dataset,
        'location': location,
        'n_logs': n_logs,
        'sampled': len(picked),
        'parsed': len(cpu),
        'exact': len(picked) == n_logs and len(cpu) == n_logs,
        'sample_method': ('every log' if len(picked) == n_logs else
                          f'{len(picked)} logs spread evenly over the '
                          f'name-sorted list'),
        'cpu_hours_per_job': cpu_stats,
        'wall_hours_per_job': _stats(wall),
        'cpu_over_wall': (round(sum(cpu) / sum(wall), 3)
                          if wall and sum(wall) > 0 else None),
        'vm_hwm_gb': _stats(hwm),
        'vm_peak_gb': _stats(peak),
        # Mean of the parsed logs × every log in the dataset. Equals the
        # plain sum when `exact`; otherwise an estimate.
        'total_cpu_hours': round(statistics.fmean(cpu) * n_logs, 1),
        'unreadable': {'count': len(unreadable), 'examples': unreadable[:3]},
        'no_timereport': {'count': len(no_timereport),
                          'examples': no_timereport[:3]},
        'basis': BASIS,
    }
