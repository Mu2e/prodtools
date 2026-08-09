"""FastMCP wiring for the read-only prodtools server.

This module holds NO logic. Every tool is a plain function in tools/,
wrapped in adapters.safe_tool and registered here. That keeps the tools
testable without MCP machinery or a stdio transport.
"""
import logging
import os
import sys
from typing import Optional

from prodtools_mcp.adapters import safe_tool
from prodtools_mcp.tools import discovery, lineage, status

LOGGER = logging.getLogger('prodtools_mcp')

INSTRUCTIONS = """
Read-only MCP server for Mu2e prodtools production state.

This server performs NO writes: it cannot submit jobs, create or delete
SAM definitions, or modify the submission ledger. Submission remains the
/mu2epro-submit path.

WHAT IT ANSWERS:
- "How is <campaign> doing?"  -> campaign_status(campaign="MDC2025au")
- "What is running at all?"   -> list_campaigns(state="active")
- "What datasets exist?"      -> find_datasets(campaign="MDC2025au")
- "How big is this dataset?"  -> dataset_details(dataset="dig.mu2e...art")
- "Where did this come from?" -> trace_provenance(name="...", direction="up")

READING THE RESULTS:
- campaign_status called with NO argument is ledger-only and cheap. Name
  a campaign to include queue and output counts, which hit the network.
- campaign_status and list_campaigns default to production; omitting
  `mine` means production. Every reply names what it read: `db_path` at
  the top level is the ledger, and `queue.owner` inside each campaign is
  the grid account the counts came from.
- A queue or outputs block with state="unknown" has NO count keys. Do
  NOT read a missing count as zero: the query failed, and the campaign
  may well be running. Never start a recovery pass on an "unknown".
- The queue block comes from live HTCondor ClassAd queries (in-process,
  via the htcondor Python bindings — no jobsub_q table parsing), so held
  jobs carry a reason, not just a count. When held > 0 the block also
  has `hold_reasons`: entries {code, count, example}, grouped by
  HoldReasonCode and sorted by count descending. `example` is ONE
  representative HoldReason string (truncated) — never sum/average
  against it, and never group by the HoldReason text yourself, since
  that text embeds the slot and host and is unique per job.
- find_datasets reports a samweb DEFINITION listing (see its `basis`
  field): zero-file definitions appear and -LH/-CH variants do not. Pass
  require_files=True when you need existence.
- find_datasets `pattern` is a SAM defname filter, a SQL LIKE. Either
  wildcard works: `*` is translated to `%`. Results are capped at
  `limit` (default 500, hard ceiling 5000) and `truncated` says whether
  the cap bit; require_files is REFUSED above the cap rather than
  issuing one SAM query per record.
- trace_provenance caps the walk at `max_nodes` (default 500, hard
  ceiling 2000) as well as `depth`: a mixed dig file has ~33 parents, so
  depth alone can fan out to thousands of serial SAM queries. The budget
  check runs before the next query is issued, not after, and
  `truncated: true` covers both the depth cutoff and the node cutoff.
- campaign_status outputs report `produced` against both `submitted`
  (indices actually handed to the grid) and `expected_at_completion`
  (njobs). Every direct campaign is sliced, so compare against
  `submitted` to judge what is in flight.
- A DRAINING campaign (`njobs: null`) reports outputs differently: one
  row per output dataset with `dispatched` (input files handed to the
  grid that map to it) and `produced`, and NO `expected_at_completion` —
  the input dataset is still growing, so no completion denominator
  exists. Only datasets dispatched so far appear; more descs show up as
  the campaign drains. `produced` can exceed `dispatched` when an
  earlier round or a smoke wrote into the same dataset.
- campaign_status `rows` is a count per submission state. `exhausted`
  means the attempt cap was reached and a human must take over.
- Errors arrive as {"error": {"kind", "message", "remedy"}}. Never retry
  an auth_expired — tell the user to renew in their own shell. This
  server never refreshes credentials.
"""

# Wrapped once, here, so registration and tests see the same objects.
TOOL_FUNCTIONS = {
    'campaign_status': safe_tool(status.campaign_status),
    'list_campaigns': safe_tool(status.list_campaigns),
    'find_datasets': safe_tool(discovery.find_datasets),
    'dataset_details': safe_tool(discovery.dataset_details),
    'trace_provenance': safe_tool(lineage.trace_provenance),
}

TOOL_NAMES = sorted(list(TOOL_FUNCTIONS) + ['get_server_info'])


def get_server_info():
    """Capabilities and safe-usage guidance for this server."""
    return {
        'name': 'prodtools',
        'description': 'Read-only access to Mu2e prodtools campaign '
                       'status and dataset discovery.',
        'writes': False,
        'tools': TOOL_NAMES,
        'ledger_db': os.environ.get(
            'MU2E_SUBMISSION_DB',
            '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db'),
        'identity': {
            'parameter': 'mine (campaign_status, list_campaigns)',
            'default': 'production — the ledger in ledger_db and '
                       'mu2epro\'s grid queue',
            'mine_true': "your own ledger at "
                         "/exp/mu2e/data/users/$USER/prodtools/"
                         "submissions.db, and your own grid queue",
            'other_accounts': 'not available through MCP — use '
                              '`submissions --db <path> status`',
        },
        'guidance': INSTRUCTIONS.strip(),
    }


def _configure_logging():
    logging.basicConfig(
        level=os.environ.get('PRODTOOLS_MCP_LOG_LEVEL', 'INFO'),
        stream=sys.stderr,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP('prodtools', instructions=INSTRUCTIONS)

    # Optional[...] everywhere a parameter defaults to None. `str = None`
    # emits {"default": null, "type": "string"} — null is not a string,
    # and strict schema validators (and other providers' function-calling
    # layers) reject it, which defeats the "reach other clients" goal.
    @mcp.tool(description='Status of one campaign, or a cheap ledger-only '
                          'summary of all of them when called with no '
                          'argument. Pass mine=true to read YOUR ledger '
                          'and queue instead of production\'s.')
    def campaign_status(campaign: Optional[str] = None,
                        campaign_id: Optional[int] = None,
                        include_queue: bool = True,
                        include_outputs: bool = True,
                        mine: bool = False) -> dict:
        return TOOL_FUNCTIONS['campaign_status'](
            campaign=campaign, campaign_id=campaign_id,
            include_queue=include_queue, include_outputs=include_outputs,
            mine=mine)

    @mcp.tool(description='List submission campaigns, optionally filtered '
                          'by state (active/complete/paused/cancelled). '
                          'Pass mine=true for your own ledger.')
    def list_campaigns(state: Optional[str] = None,
                       mine: bool = False) -> dict:
        return TOOL_FUNCTIONS['list_campaigns'](state=state, mine=mine)

    @mcp.tool(description='Find datasets by campaign, tier, description, '
                          'or SAM defname pattern (* or % both work). '
                          'Reports a definition listing; pass '
                          'require_files=True for existence.')
    def find_datasets(campaign: Optional[str] = None,
                      tier: Optional[str] = None,
                      desc: Optional[str] = None,
                      pattern: Optional[str] = None,
                      latest_only: bool = False,
                      require_files: bool = False,
                      limit: int = discovery.DEFAULT_LIMIT) -> dict:
        return TOOL_FUNCTIONS['find_datasets'](
            campaign=campaign, tier=tier, desc=desc, pattern=pattern,
            latest_only=latest_only, require_files=require_files,
            limit=limit)

    @mcp.tool(description='File count, event count, size, and creation '
                          'date for one dataset.')
    def dataset_details(dataset: str) -> dict:
        return TOOL_FUNCTIONS['dataset_details'](dataset=dataset)

    @mcp.tool(description='Trace a file\'s lineage as nodes and edges, '
                          'up (parents) or down (children). Bounded by '
                          'both depth and max_nodes; either can set '
                          'truncated=true.')
    def trace_provenance(name: str, direction: str = 'up',
                         depth: int = 3,
                         max_nodes: int = lineage.DEFAULT_MAX_NODES) -> dict:
        return TOOL_FUNCTIONS['trace_provenance'](
            name=name, direction=direction, depth=depth,
            max_nodes=max_nodes)

    # Registered under the module function's name via name=, because a
    # nested `def get_server_info` would shadow the module-level one and
    # recurse. The advertised name must match TOOL_NAMES.
    @mcp.tool(name='get_server_info',
              description='Server capabilities and safe-usage guidance.')
    def _server_info_tool() -> dict:
        return get_server_info()

    return mcp


def main():
    _configure_logging()
    create_mcp_server().run()


if __name__ == '__main__':
    main()
