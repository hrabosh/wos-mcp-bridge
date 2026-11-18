"""Launch the normal stdio CLI with a mock HTTP transport for transport tests."""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import httpx
from conftest import api_response

from wos_mcp_bridge.__main__ import main

markers = Path(os.environ["WOS_MCP_TEST_MARKERS"])
original_client = httpx.AsyncClient


async def upstream(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "api.clarivate.com"
    if (
        request.url.params.get("q") == "TS=(cancellation test)"
        and not (markers / "started").exists()
    ):
        (markers / "started").touch()
        try:
            await asyncio.sleep(30)
        finally:
            (markers / "finished").touch()
    return api_response(request)


def offline_client(**options):
    return original_client(**(options | {"transport": httpx.MockTransport(upstream)}))


if __name__ == "__main__":
    with patch.object(httpx, "AsyncClient", offline_client):
        main()
