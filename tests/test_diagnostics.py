"""Exercise public MCP error metadata and safe operation logs."""

import logging
from contextlib import aclosing

import httpx
import pytest
from conftest import api_response
from fastmcp import Client

from wos_mcp_bridge.literature_client import LiteratureClient
from wos_mcp_bridge.server import Services, create_server

ERROR_META = "io.github.hrabosh.wos-mcp-bridge/error"
SECRET = "synthetic-secret-must-not-be-logged"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (400, "invalid_input", False),
        (401, "auth", False),
        (403, "auth", False),
        (404, "not_found", False),
        (429, "rate_limited", True),
        (503, "upstream_unavailable", True),
        (200, "invalid_response", False),
    ],
)
async def test_provider_failures_are_tool_errors_with_safe_metadata(
    make_client, status, code, retryable
):
    def upstream(_request):
        return httpx.Response(status, text=SECRET, headers={"Retry-After": "25"})

    wos, calls = make_client(upstream)
    async with aclosing(LiteratureClient()) as literature:
        async with Client(create_server(services=Services(wos, literature))) as client:
            result = await client.call_tool("search_wos", {"query": SECRET}, raise_on_error=False)
            assert result.is_error and result.structured_content is None
            error = result.meta[ERROR_META]
            assert error["code"] == code and error["source"] == "Clarivate"
            assert error["retryable"] is retryable
            assert SECRET not in repr(result)
            if status == 429:
                assert error["retry_after_seconds"] == 25
                cooldown = await client.call_tool(
                    "search_wos", {"query": SECRET}, raise_on_error=False
                )
                assert cooldown.meta[ERROR_META]["code"] == "rate_limited"
                assert len(calls) == 1


async def test_operation_logs_distinguish_real_requests_cache_and_validation(make_client, caplog):
    def upstream(request):
        if request.url.params["q"] == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        return api_response(request)

    wos, calls = make_client(upstream)
    logger = logging.getLogger("fastmcp.wos_mcp_bridge.operations")
    logger.addHandler(caplog.handler)
    try:
        async with aclosing(LiteratureClient()) as literature:
            async with Client(create_server(services=Services(wos, literature))) as client:
                assert (await client.call_tool("search_wos", {"query": SECRET})).structured_content
                assert (await client.call_tool("search_wos", {"query": SECRET})).structured_content
                timeout = await client.call_tool(
                    "search_wos", {"query": "timeout"}, raise_on_error=False
                )
                assert timeout.meta[ERROR_META]["code"] == "timeout"
                invalid = await client.call_tool(
                    "search_wos", {"query": SECRET, "limit": 100}, raise_on_error=False
                )
                assert invalid.meta[ERROR_META]["code"] == "invalid_input"
    finally:
        logger.removeHandler(caplog.handler)
    assert len(calls) == 2
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert SECRET not in logs
    assert "'upstream_requests': 1" in logs
    assert "'cache_hits': 1" in logs
    assert "limiter_wait_ms" in logs
    assert "outcome=timeout" in logs


@pytest.mark.parametrize("fallback_status", [404, 401])
async def test_incomplete_resolution_keeps_provider_cooldown_metadata(fallback_status):
    from wos_mcp_bridge.europe_pmc import EuropePmcClient
    from wos_mcp_bridge.openalex_client import OpenAlexClient

    pmc = EuropePmcClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, text=SECRET, headers={"Retry-After": "17"})
        )
    )
    openalex = OpenAlexClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(fallback_status))
    )
    async with aclosing(LiteratureClient(pmc, openalex)) as literature:
        async with Client(create_server(services=Services(None, literature))) as client:
            result = await client.call_tool(
                "resolve_article", {"identifier": "10.1234/fixture"}, raise_on_error=False
            )
            cooldown = await client.call_tool(
                "resolve_article", {"identifier": "10.1234/fixture"}, raise_on_error=False
            )
    assert result.is_error and result.structured_content is None
    assert result.meta[ERROR_META] == {
        "source": "Europe PMC",
        "code": "rate_limited",
        "upstream_status": 429,
        "retry_after_seconds": 17,
        "retryable": True,
    }
    assert cooldown.meta[ERROR_META]["code"] == "rate_limited"
    assert "upstream_status" not in cooldown.meta[ERROR_META]
    assert SECRET not in repr(result)
