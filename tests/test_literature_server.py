"""Test real MCP discovery, structured outputs, validation and error presentation."""

import json

import httpx
import pytest
from fastmcp import Client
from test_literature import DOI, JATS, WORK, search_body

import server
from europe_pmc import EuropePmcClient
from literature_client import LiteratureClient
from openalex_client import OpenAlexClient


@pytest.fixture
async def literature_mcp(make_client, monkeypatch):
    wos, _ = make_client()
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json=WORK)
        if request.url.path.endswith("fullTextXML"):
            return httpx.Response(200, content=JATS)
        return httpx.Response(200, json=search_body())

    transport = httpx.MockTransport(handle)
    literature = LiteratureClient(
        EuropePmcClient(transport=transport, min_interval=0),
        OpenAlexClient(transport=transport, min_interval=0),
    )
    monkeypatch.setattr(server, "WosClient", lambda _: wos)
    monkeypatch.setattr(server, "LiteratureClient", lambda **_: literature)
    async with Client(server.mcp) as client:
        yield client, calls
    assert literature.europe_pmc._http.is_closed
    assert literature.openalex._http.is_closed
    assert wos._http.is_closed


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("search_literature", {"query": "canine"}),
        ("resolve_article", {"identifier": DOI}),
        ("get_article", {"identifier": DOI}),
        ("get_article_section", {"identifier": DOI, "section_id": "methods", "max_chars": 5}),
    ],
)
async def test_literature_mcp_json_matches_structured_content(literature_mcp, name, arguments):
    client, calls = literature_mcp
    result = await client.call_tool(name, arguments)
    assert json.loads(result.content[0].text) == result.structured_content
    assert calls


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("search_literature", {"query": "q", "limit": 51}),
        ("search_literature", {"query": "q", "max_chars": 20001}),
        ("resolve_article", {"identifier": "https://evil.example/"}),
        ("get_article", {"identifier": DOI, "include_text": "false"}),
        ("get_article_section", {"identifier": DOI, "section_id": "methods", "offset": -1}),
    ],
)
async def test_invalid_literature_mcp_args_do_not_fetch(literature_mcp, name, arguments):
    client, calls = literature_mcp
    result = await client.call_tool(name, arguments, raise_on_error=False)
    assert result.is_error
    assert calls == []


async def test_section_error_is_actionable(literature_mcp):
    client, _ = literature_mcp
    result = await client.call_tool(
        "get_article_section", {"identifier": DOI, "section_id": "wrong"}, raise_on_error=False
    )
    assert result.is_error
    assert "Use get_article" in result.content[0].text
