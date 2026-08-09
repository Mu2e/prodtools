"""Write tool implementations for the prodtools-write MCP server.

Signatures are fixed here; real bodies land in Tasks 8 (push_cnf) and
9 (enqueue_campaign, run_submissions).
"""


def push_cnf(json, desc, dsconf, jobdefs_map, run_as, confirm=False,
             simjob_version=None):
    """Build and push a cnf tarball via json2jobdef."""
    raise NotImplementedError('push_cnf lands in Task 8')


def enqueue_campaign(map_path, entry, slice_size, run_as, confirm=False):
    """Enqueue a campaign entry into the submission map/ledger."""
    raise NotImplementedError('enqueue_campaign lands in Task 9')


def run_submissions(campaign_id, run_as, confirm=False):
    """Drive queued submissions for a campaign to the grid."""
    raise NotImplementedError('run_submissions lands in Task 9')
