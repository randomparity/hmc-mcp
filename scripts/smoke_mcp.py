"""Smoke-test the MCP server over stdio using the official client protocol."""

import asyncio

from fastmcp import Client

from hmc_mcp.server import mcp


async def main() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print(f"Connected. {len(tools)} tools exposed:")
        for t in tools:
            print(f"  - {t.name}")


if __name__ == "__main__":
    asyncio.run(main())
