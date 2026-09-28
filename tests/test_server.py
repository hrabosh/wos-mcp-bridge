"""Exercise the actual MCP protocol in-process, with Clarivate HTTP mocked."""

import json

import pytest
from fastmcp import Client

import server


@pytest.fixture
async def mcp_client(make_client, monkeypatch):
    wos, requests = make_client()
    monkeypatch.setattr(server, "WosClient", lambda _key: wos)
    async with Client(server.mcp) as client:
        yield client, requests
    assert wos._http.is_closed


async def test_discovery_exposes_only_read_only_tools(mcp_client):
    client, requests = mcp_client
    tools = {tool.name: tool for tool in await client.list_tools()}
    assert set(tools) == {"search", "fetch", "search_wos"}
    for tool in tools.values():
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert "ctx" not in tool.inputSchema["properties"]
        assert "api_key" not in tool.inputSchema["properties"]
    assert requests == []


async def test_search_and_fetch_have_matching_json_and_structured_content(mcp_client):
    client, requests = mcp_client
    result = await client.call_tool("search", {"query": "TS=canine"})
    payload = json.loads(result.content[0].text)
    assert payload == result.structured_content
    assert set(payload) == {"results"}
    assert set(payload["results"][0]) == {"id", "title", "url"}
    fetched = await client.call_tool("fetch", {"id": payload["results"][0]["id"]})
    assert json.loads(fetched.content[0].text) == fetched.structured_content
    assert "NOT article full text" in fetched.structured_content["text"]
    assert len(requests) == 1


async def test_detailed_search_returns_query_and_metadata(mcp_client):
    client, _ = mcp_client
    result = await client.call_tool("search_wos", {"query": "TS=canine", "limit": 1})
    assert result.structured_content["query"] == "TS=canine"
    assert result.structured_content["metadata"]["total"] == 42
    assert result.structured_content["results"][0]["citations"] is None


@pytest.mark.parametrize("arguments", [{"limit": 51}, {"page": 0}, {"sort": "invalid"}])
async def test_invalid_mcp_arguments_do_not_reach_clarivate(mcp_client, arguments):
    client, requests = mcp_client
    result = await client.call_tool(
        "search_wos", {"query": "TS=canine", **arguments}, raise_on_error=False
    )
    assert result.is_error
    assert requests == []


async def test_domain_error_is_a_readable_mcp_error(mcp_client):
    client, requests = mcp_client
    result = await client.call_tool("fetch", {"id": "https://example.com"}, raise_on_error=False)
    assert result.is_error
    assert "WOS:" in result.content[0].text
    assert requests == []
