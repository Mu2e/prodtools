"""FastMCP wiring for the read-only prodtools server.

This module holds NO logic. Every tool is a plain function in tools/,
wrapped in adapters.safe_tool and registered here. That keeps the tools
testable without MCP machinery or a stdio transport.
"""
import logging
import os
import sys

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
- A queue or outputs block with state="unknown" has NO count keys. Do
  NOT read a missing count as zero: the query failed, and the campaign
  may well be running. Never start a recovery pass on an "unknown".
- find_datasets reports a samweb DEFINITION listing (see its `basis`
  field): zero-file definitions appear and -LH/-CH variants do not. Pass
  require_files=True when you need existence.
- Errors arrive as {"error": {"kind", "message", "remedy"}}. Never retry
  an auth_expired — tell the user to renew in their own shell.
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

    @mcp.tool(description='Status of one campaign, or a cheap ledger-only '
                          'summary of all of them when called with no '
                          'argument.')
    def campaign_status(campaign: str = None, campaign_id: int = None,
                        include_queue: bool = True,
                        include_outputs: bool = True) -> dict:
        return TOOL_FUNCTIONS['campaign_status'](
            campaign=campaign, campaign_id=campaign_id,
            include_queue=include_queue, include_outputs=include_outputs)

    @mcp.tool(description='List submission campaigns, optionally filtered '
                          'by state (active/complete/paused/cancelled).')
    def list_campaigns(state: str = None) -> dict:
        return TOOL_FUNCTIONS['list_campaigns'](state=state)

    @mcp.tool(description='Find datasets by campaign, tier, description, '
                          'or glob pattern. Reports a definition listing; '
                          'pass require_files=True for existence.')
    def find_datasets(campaign: str = None, tier: str = None,
                      desc: str = None, pattern: str = None,
                      latest_only: bool = False,
                      require_files: bool = False) -> dict:
        return TOOL_FUNCTIONS['find_datasets'](
            campaign=campaign, tier=tier, desc=desc, pattern=pattern,
            latest_only=latest_only, require_files=require_files)

    @mcp.tool(description='File count, event count, size, and creation '
                          'date for one dataset.')
    def dataset_details(dataset: str) -> dict:
        return TOOL_FUNCTIONS['dataset_details'](dataset=dataset)

    @mcp.tool(description='Trace a file\'s lineage as nodes and edges, '
                          'up (parents) or down (children).')
    def trace_provenance(name: str, direction: str = 'up',
                         depth: int = 3) -> dict:
        return TOOL_FUNCTIONS['trace_provenance'](
            name=name, direction=direction, depth=depth)

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
