"""FastMCP registration for the write server."""
from prodtools_mcp_write import tools

TOOL_NAMES = ('push_cnf', 'enqueue_campaign', 'run_submissions')


def get_write_server_info():
    return {
        'name': 'prodtools-write',
        'performs_writes': True,
        'description': (
            'Write-capable prodtools submission. run_as="self" needs no '
            'privilege and writes only your own scratch, datasets and '
            'ledger. run_as="mu2epro" registers artifacts in production '
            'SAM and submits production grid jobs; it is refused unless '
            'confirm=true.'),
        'tools': list(TOOL_NAMES),
    }


def create_write_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP('prodtools-write')
    mcp.tool()(tools.push_cnf)
    mcp.tool()(tools.enqueue_campaign)
    mcp.tool()(tools.run_submissions)
    return mcp


def main():
    create_write_mcp_server().run()


if __name__ == '__main__':
    main()
