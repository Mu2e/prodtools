#!/usr/bin/env python3
"""Spawn the server over stdio, list its tools, call server_info.

Exercises the real transport, which the unit tests deliberately do not.
Run:  mcp/.venv/bin/python mcp/scripts/smoke_test_stdio.py

Must run under the venv interpreter, not /usr/bin/python3 — the latter
is 3.9 (too old for mcp) and does not have the package installed.
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
START = os.path.join(HERE, 'start_mcp.sh')


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command='bash', args=[START])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print('tools:', ', '.join(names))
            assert 'campaign_status' in names, names
            assert 'get_server_info' in names, names
            result = await session.call_tool('get_server_info', {})
            print('get_server_info ok:', not result.isError)
            assert not result.isError, result
    print('SMOKE OK')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f'SMOKE FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
