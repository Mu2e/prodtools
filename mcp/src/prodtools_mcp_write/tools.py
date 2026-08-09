"""Write tool implementations for the prodtools-write MCP server.

Signatures are fixed here; real bodies land in Tasks 8 (push_cnf) and
9 (enqueue_campaign, run_submissions).

Thin by design: validate, delegate to runner, read the result back
from the artifact the CLI wrote -- never from its stdout.
"""
import json as _json
from pathlib import Path

from prodtools_mcp_write import runner


def _read_map_entry(map_path):
    """Return (index, entry) for the LAST entry in a jobdefs map.

    json2jobdef appends the entry it just pushed, so the tarball name
    comes from the file it wrote rather than from parsing human output
    through ksu -- which is exactly the parsing this project exists to
    eliminate.
    """
    entries = _json.loads(Path(map_path).read_text())
    if not entries:
        raise RuntimeError(f"{map_path} has no entries after the push")
    return len(entries) - 1, entries[-1]


def push_cnf(json, desc, dsconf, jobdefs_map, run_as, confirm=False,
             simjob_version=None):
    """Build a cnf tarball and register it.

    run_as="self" registers under your own dataset owner and scratch
    outstage. run_as="mu2epro" registers in PRODUCTION SAM and is not
    reversible; it requires confirm=true.

    jobdefs_map must be a path you can write: production_manager/
    direct_maps/ is mu2epro-owned, and this tool never invents a path.
    """
    runner.require_confirmed(run_as, confirm)
    argv = ['bin/json2jobdef', '--json', json, '--desc', desc,
            '--dsconf', dsconf, '--prod', '--jobdefs', jobdefs_map,
            '--verbose']
    result = runner.run_cli(argv, run_as)
    if result['rc'] != 0:
        raise RuntimeError(
            f"json2jobdef failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")
    index, entry = _read_map_entry(jobdefs_map)
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
