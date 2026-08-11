"""FastMCP registration for the write server."""
from prodtools_mcp_write import tools

# Computed without touching FastMCP, so registration coverage is
# checkable under the system python3.9 that runs test_unit.py (the
# real `mcp` package needs >=3.10). create_write_mcp_server() derives
# its registrations from this dict, so it and TOOL_NAMES cannot drift
# from what actually gets registered.
TOOL_FUNCTIONS = {
    'push_cnf': tools.push_cnf,
    'run_submissions': tools.run_submissions,
}

TOOL_NAMES = tuple(TOOL_FUNCTIONS)


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
    for name, fn in TOOL_FUNCTIONS.items():
        mcp.tool(name=name)(fn)
    return mcp


def main():
    create_write_mcp_server().run()


if __name__ == '__main__':
    main()
