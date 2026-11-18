"""Run a synthetic WoS search through the installed MCP stdio server."""

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

FIXTURE = Path(__file__).with_name("smoke_stdio_server.py").resolve()


async def demo() -> None:
    with TemporaryDirectory(prefix="wos-demo-") as directory:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-I", str(FIXTURE)],
            cwd=directory,
            env={
                "FASTMCP_CHECK_FOR_UPDATES": "off",
                "FASTMCP_SHOW_SERVER_BANNER": "false",
                "WOS_API_KEY": "synthetic-demo-key",
                "OPENALEX_API_KEY": "",
            },
        )
        async with asyncio.timeout(25):
            async with stdio_client(server) as (read, write), ClientSession(read, write) as client:
                await client.initialize()
                result = await client.call_tool("search", {"query": "TS=(quantum computing)"})
                if result.is_error or result.structured_content is None:
                    raise RuntimeError("The synthetic MCP search did not return a result.")
                payload = result.structured_content
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    asyncio.run(demo())
