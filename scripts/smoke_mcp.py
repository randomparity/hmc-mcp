"""Smoke-test the MCP server over stdio using the official client protocol.

Composes through the same generator an operator's migration uses, so this leg also
proves on every ``just verify`` and every CI run that the legacy-equivalent grant
compiles and composes. It does not serialize TOML, so a *rendering* defect is not
caught here — ``tests/unit/test_legacy_policy.py`` owns that round trip.
"""

import asyncio

from fastmcp import Client

from hmc_mcp.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.legacy_policy import compile_legacy_policy
from hmc_mcp.server import TOOL_SECURITY, create_mcp


async def main() -> None:
    # Composed here rather than at module scope: since ADR 0041 nothing in the package
    # holds an application at import, and this script is inside the guard that checks it.
    mcp = create_mcp(compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,)))
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print(f"Connected. {len(tools)} tools exposed:")
        for t in tools:
            print(f"  - {t.name}")


if __name__ == "__main__":
    asyncio.run(main())
