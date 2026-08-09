"""Write tool implementations for the prodtools-write MCP server.

Signatures are fixed here; real bodies land in Tasks 8 (push_cnf) and
9 (enqueue_campaign, run_submissions).

Thin by design: validate, delegate to runner, read the result back
from the artifact the CLI wrote -- never from its stdout.

Entry selection is delegated to `utils.json2jobdef.load_json` /
`find_json_entry` -- the SAME functions the real `bin/json2jobdef`
CLI uses -- rather than re-implemented here. A parallel scan over raw
JSON previously matched the literal `desc` key, but `desc` is often
absent in the raw config and auto-derived during expansion (mixing
entries append `pbeam`; every stage can omit it), so the two
implementations inevitably drifted: mixing could never be pushed. This
module now cannot drift from what json2jobdef actually does, by
construction.

`utils.json2jobdef` (transitively `utils.samweb_wrapper`) needs
`samweb_client`, which is not on a bare interpreter's path -- but this
module is never imported except after `samweb_client` is already
either genuinely on PYTHONPATH (the real write-MCP server's launcher,
`mcp/scripts/_mcp_env.sh`, sources the Mu2e ops environment before
starting Python) or stubbed into `sys.modules` (this repo's own
`test/test_unit.py`, before it imports anything under `utils/`).
Nothing imported here performs any I/O (SAM query, subprocess, network)
at import time or during entry selection -- only at model
instantiation, which this module never does.
"""
import json as _json
from pathlib import Path

from prodtools_mcp_write import runner
from utils.job_common import Mu2eName
from utils.json2jobdef import load_json, find_json_entry


def _select_simjob_setup(json_path, desc, dsconf):
    """Read `json_path` and return the `simjob_setup` of the entry
    matching `desc` + `dsconf`, using json2jobdef's own loader and
    selector so this can never disagree with what a real push does.

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
        configs = load_json(path)
    except _json.JSONDecodeError as e:
        raise ValueError(f"push_cnf: {json_path!r} is not valid JSON: {e}")

    # find_json_entry sys.exit()s on 0 or >1 matches -- fine for a CLI,
    # fatal for a long-running server process. Convert to ValueError
    # instead of letting SystemExit propagate and kill the server.
    try:
        entry = find_json_entry(configs, desc, dsconf, None)
    except SystemExit as e:
        raise ValueError(f"push_cnf: {e}") from e

    simjob_setup = entry.get('simjob_setup')
    if not simjob_setup:
        raise ValueError(
            f"push_cnf: entry matching desc={desc!r} dsconf={dsconf!r} in "
            f"{json_path!r} has no simjob_setup field")
    return simjob_setup


def _read_map_entry(map_path, desc, dsconf):
    """Return (index, entry) for the entry THIS push actually produced.

    Selected by parsing desc+dsconf out of each candidate entry's own
    tarball name (`Mu2eName`), not by an owner-qualified tarball name
    computed in this process: `owner` defaults to `$USER` when a JSON
    entry omits it, and this process's `$USER` (the caller) is not the
    identity json2jobdef actually ran as inside the ksu block
    (`mu2epro` -> `mu2e`) -- computing the tarball name here would
    report a successful production push as a failure. Nor by position:
    `_write_jobdef_json_entry` dedupes on (tarball, firstjob) and
    returns WITHOUT appending when the entry already exists --
    re-running `--prod` is the documented way to finish a partial push
    -- so `entries[-1]` could silently return some OTHER campaign's
    tarball and index as if it were this push's result.
    """
    entries = _json.loads(Path(map_path).read_text())
    matches = []
    for i, e in enumerate(entries):
        tarball = e.get('tarball')
        if not tarball:
            continue
        try:
            name = Mu2eName.parse(tarball)
        except ValueError:
            continue
        if name.description == desc and name.dsconf == dsconf:
            matches.append((i, e))
    if not matches:
        raise RuntimeError(
            f"no entry for desc={desc!r} dsconf={dsconf!r} found in "
            f"{map_path} after the push")
    if len(matches) > 1:
        raise RuntimeError(
            f"desc={desc!r} dsconf={dsconf!r} matches {len(matches)} "
            f"entries in {map_path}; cannot select the entry for this "
            f"push unambiguously")
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

    simjob_setup = _select_simjob_setup(json, desc, dsconf)

    argv = ['bin/json2jobdef', '--json', json, '--desc', desc,
            '--dsconf', dsconf, '--prod', '--jobdefs', jobdefs_map]
    result = runner.run_cli(argv, run_as, simjob_setup=simjob_setup)
    if result['rc'] != 0:
        raise RuntimeError(
            f"json2jobdef failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")
    index, entry = _read_map_entry(jobdefs_map, desc, dsconf)
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
