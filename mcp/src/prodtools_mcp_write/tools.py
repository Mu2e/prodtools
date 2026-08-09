"""Write tool implementations for the prodtools-write MCP server.

Signatures are fixed here; real bodies land in Tasks 8 (push_cnf) and
9 (enqueue_campaign, run_submissions).

Thin by design: validate, delegate to runner, read the result back
from the artifact the CLI wrote -- never from its stdout.

`utils.config_utils` and `utils.job_common` are safe to import at
module level: unlike `utils.json2jobdef` (which pulls in
`utils.samweb_wrapper` -> `samweb_client`, unavailable outside the
Mu2e/Muse environment), they are pure string/dict computation with no
Fermilab-specific dependency, so this module still imports cleanly
under bare system python3.9.
"""
import json as _json
from pathlib import Path

from prodtools_mcp_write import runner
from utils.config_utils import cnf_name


def _values(entry, key):
    """A JSON config field as a list, whether it was written as a bare
    scalar or as the single/multi-element list json2jobdef itself
    expands combinatorially."""
    v = entry.get(key)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _unwrap(value):
    """Un-wrap a scalar-or-list JSON field to a single scalar.

    A real campaign never varies owner/version/tarball_append/
    simjob_setup within one desc+dsconf pair, so more than one
    distinct value here is ambiguous and refused rather than guessed.
    """
    if not isinstance(value, list):
        return value
    distinct = set(value)
    if len(distinct) > 1:
        raise ValueError(f"ambiguous list value {value!r}; expected one value")
    return value[0] if value else None


def _select_push_params(json_path, desc, dsconf):
    """Read `json_path` and derive the Musing setup + expected tarball
    name for the entry matching `desc` + `dsconf`.

    Refuses rather than guessing: a caller-passed Musing tag could
    disagree with the entry and silently build a cnf against the wrong
    release, which is worse than not building one. This mirrors the
    refusal behaviour of `.claude/commands/mu2e-run.md`, which derives
    a command's Musing from the same `simjob_setup` field rather than
    accept one as an argument.
    """
    path = Path(json_path)
    if not path.is_file():
        raise ValueError(f"push_cnf: --json config not found: {json_path!r}")
    try:
        raw = _json.loads(path.read_text())
    except _json.JSONDecodeError as e:
        raise ValueError(f"push_cnf: {json_path!r} is not valid JSON: {e}")
    if not isinstance(raw, list):
        raw = [raw]

    matches = [e for e in raw
               if desc in _values(e, 'desc') and dsconf in _values(e, 'dsconf')]
    if not matches:
        raise ValueError(
            f"push_cnf: no entry in {json_path!r} matches desc={desc!r} "
            f"dsconf={dsconf!r}")
    if len(matches) > 1:
        raise ValueError(
            f"push_cnf: desc={desc!r} dsconf={dsconf!r} matches "
            f"{len(matches)} entries in {json_path!r}; expected exactly 1")
    entry = matches[0]

    simjob_setup = _unwrap(entry.get('simjob_setup'))
    if not simjob_setup:
        raise ValueError(
            f"push_cnf: entry matching desc={desc!r} dsconf={dsconf!r} in "
            f"{json_path!r} has no simjob_setup field")

    cfg = {'desc': desc, 'dsconf': dsconf,
           'version': _unwrap(entry.get('version')) or 0}
    owner = _unwrap(entry.get('owner'))
    if owner:
        cfg['owner'] = owner
    tarball_append = _unwrap(entry.get('tarball_append'))
    if tarball_append:
        cfg['tarball_append'] = tarball_append
    tarball = cnf_name(cfg)

    return simjob_setup, tarball


def _read_map_entry(map_path, tarball):
    """Return (index, entry) for the entry THIS push actually produced.

    Selected by the tarball name computed from the pushed config, never
    by position: `_write_jobdef_json_entry` dedupes on (tarball,
    firstjob) and returns WITHOUT appending when the entry already
    exists -- re-running `--prod` is the documented way to finish a
    partial push -- so `entries[-1]` can silently return some OTHER
    campaign's tarball and index as if it were this push's result.
    """
    entries = _json.loads(Path(map_path).read_text())
    matches = [(i, e) for i, e in enumerate(entries)
               if e.get('tarball') == tarball]
    if not matches:
        raise RuntimeError(f"{tarball} not found in {map_path} after the push")
    if len(matches) > 1:
        raise RuntimeError(
            f"{tarball} appears {len(matches)} times in {map_path}; cannot "
            f"select the entry for this push unambiguously")
    return matches[0]


def push_cnf(json, desc, dsconf, jobdefs_map, run_as, confirm=False):
    """Build a cnf tarball and register it.

    run_as="self" registers under your own dataset owner and scratch
    outstage. run_as="mu2epro" registers in PRODUCTION SAM and is not
    reversible; it requires confirm=true.

    jobdefs_map must be an absolute path you can write:
    production_manager/direct_maps/ is mu2epro-owned, and this tool
    never invents a path. It must be absolute because, under
    run_as="mu2epro", the ksu block cd's into its own mktemp workdir --
    a relative path would be written there while the result is read
    back relative to this server's own cwd.

    The Musing (simjob_setup) is never taken as an argument: it is
    derived from the `--json` config's own entry for desc+dsconf, so a
    caller-passed tag can never silently disagree with it.
    """
    runner.require_confirmed(run_as, confirm)

    if not Path(jobdefs_map).is_absolute():
        raise ValueError(
            f"jobdefs_map must be an absolute path (got {jobdefs_map!r})")

    simjob_setup, tarball = _select_push_params(json, desc, dsconf)

    argv = ['bin/json2jobdef', '--json', json, '--desc', desc,
            '--dsconf', dsconf, '--prod', '--jobdefs', jobdefs_map]
    result = runner.run_cli(argv, run_as, simjob_setup=simjob_setup)
    if result['rc'] != 0:
        raise RuntimeError(
            f"json2jobdef failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")
    index, entry = _read_map_entry(jobdefs_map, tarball)
    return {
        'tarball': entry.get('tarball'),
        'datasets': [o.get('dataset') for o in entry.get('outputs', [])],
        'map_path': jobdefs_map,
        'entry_index': index,
    }


def enqueue_campaign(map_path, entry, slice_size, run_as, confirm=False):
    """Enqueue a campaign entry into the submission map/ledger."""
    raise NotImplementedError('enqueue_campaign lands in Task 9')


def run_submissions(campaign_id, run_as, confirm=False):
    """Drive queued submissions for a campaign to the grid."""
    raise NotImplementedError('run_submissions lands in Task 9')
