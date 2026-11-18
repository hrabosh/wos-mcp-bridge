"""Regressions at the real provider boundary, with only upstream HTTP replaced."""

import asyncio
import copy
import inspect
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from conftest import HIT as WOS_HIT
from test_literature import DOI, JATS, WORK, search_body

from wos_mcp_bridge.europe_pmc import EuropePmcClient
from wos_mcp_bridge.literature_http import LiteratureError
from wos_mcp_bridge.openalex_client import OpenAlexClient
from wos_mcp_bridge.wos_client import WosClient, WosError


@pytest.fixture(params=["wos", "europe_pmc"])
async def provider(request):
    clients = []
    kind = request.param

    def make(handler=None, **options):
        calls = []

        def healthy():
            if kind == "wos":
                return {"metadata": {"total": 1}, "hits": [copy.deepcopy(WOS_HIT)]}
            return search_body()

        async def upstream(req):
            calls.append(req)
            response = handler(req) if handler else httpx.Response(200, json=healthy())
            return await response if inspect.isawaitable(response) else response

        options.setdefault("min_interval", 0)
        transport = httpx.MockTransport(upstream)
        client = (
            WosClient("synthetic-key", transport=transport, **options)
            if kind == "wos"
            else EuropePmcClient(transport=transport, **options)
        )
        clients.append(client)
        return client, calls, healthy

    yield make
    for client in clients:
        await client.aclose()


ERRORS = (WosError, LiteratureError)


@pytest.mark.parametrize("retry_after", ["60", "invalid", None, "http-date"])
async def test_cooldown_stops_new_requests_but_preserves_cache(provider, retry_after):
    def handler(request):
        if "limited" in str(request.url):
            value = (
                format_datetime(datetime.now(UTC) + timedelta(minutes=1), usegmt=True)
                if retry_after == "http-date"
                else retry_after
            )
            return httpx.Response(429, headers={"Retry-After": value} if value else {})
        return httpx.Response(200, json=healthy())

    client, calls, healthy = provider(handler)
    await client.search("warm")
    with pytest.raises(ERRORS, match="rate limit"):
        await client.search("limited")
    with pytest.raises(ERRORS, match="cooldown"):
        await client.search("different")
    assert (await client.search("warm"))["retrieval"]["cached"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("bad", [b"not-json", b"{}", b'{"title":false}'])
async def test_invalid_response_does_not_prevent_immediate_recovery(provider, bad):
    def handler(_):
        return (
            httpx.Response(200, content=bad)
            if len(calls) == 1
            else httpx.Response(200, json=healthy())
        )

    client, calls, healthy = provider(handler)
    with pytest.raises(ERRORS):
        await client.search("same-query")
    recovered = await client.search("same-query")
    assert recovered["results"]
    assert recovered["retrieval"]["cached"] is False
    assert len(calls) == 2


async def test_warm_cache_does_not_wait_for_unrelated_network_request(provider):
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        if "slow" in str(request.url):
            started.set()
            await release.wait()
        return httpx.Response(200, json=healthy())

    client, calls, healthy = provider(handler)
    await client.search("warm")
    slow = asyncio.create_task(client.search("slow"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        result = await asyncio.wait_for(client.search("warm"), 1)
        assert result["retrieval"]["cached"] is True
        assert len(calls) == 2
    finally:
        release.set()
        await slow


async def test_cancelled_owner_allows_follower_to_recover(provider):
    started = asyncio.Event()

    async def handler(_):
        if len(calls) == 1:
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=healthy())

    client, calls, healthy = provider(handler)
    owner = asyncio.create_task(client.search("same-query"))
    await asyncio.wait_for(started.wait(), 1)
    follower = asyncio.create_task(client.search("same-query"))
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert (await asyncio.wait_for(follower, 1))["results"]
    assert len(calls) == 2
    assert (await client.search("same-query"))["retrieval"]["cached"] is True


async def test_total_deadline_includes_network_queue(provider):
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(_):
        started.set()
        await release.wait()
        return httpx.Response(200, json=healthy())

    client, calls, healthy = provider(handler, operation_timeout=0.1)
    owner = asyncio.create_task(client.search("slow"))
    await asyncio.wait_for(started.wait(), 1)
    try:
        with pytest.raises(ERRORS, match="timed out"):
            await asyncio.wait_for(client.search("queued"), 0.15)
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
    assert (await client.search("recovered"))["results"]


async def test_response_size_is_bounded(provider):
    client, calls, _ = provider(max_response_bytes=5)
    with pytest.raises(ERRORS, match="size limit"):
        await client.search("oversized")
    assert len(calls) == 1


async def test_cache_byte_budget_prevents_oversized_entries(provider):
    client, calls, _ = provider(cache_max_bytes=1)
    await client.search("same-query")
    await client.search("same-query")
    assert len(calls) == 2


async def test_invalid_xml_is_not_cached():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"<article>" if len(calls) == 1 else JATS)

    client = EuropePmcClient(transport=httpx.MockTransport(handler), min_interval=0)
    try:
        with pytest.raises(LiteratureError, match="XML"):
            await client.full_text("PMC12345")
        assert (await client.full_text("PMC12345"))["sections"]
        assert len(calls) == 2
    finally:
        await client.aclose()


async def test_wrong_openalex_identity_is_not_cached():
    calls = []

    def handler(request):
        calls.append(request)
        body = copy.deepcopy(WORK)
        if len(calls) == 1:
            body["doi"] = "https://doi.org/10.1234/wrong"
        return httpx.Response(200, json=body)

    client = OpenAlexClient(transport=httpx.MockTransport(handler), min_interval=0)
    try:
        with pytest.raises(LiteratureError, match="different article"):
            await client.resolve(DOI)
        assert (await client.resolve(DOI))["article"]["doi"] == DOI
        assert len(calls) == 2
    finally:
        await client.aclose()


async def test_total_deadline_includes_rate_limit_wait(provider):
    client, calls, _ = provider(min_interval=5, operation_timeout=0.05)
    await client.search("first")
    with pytest.raises(ERRORS, match="timed out"):
        await client.search("second")
    assert len(calls) == 1


async def test_cancelling_a_queued_waiter_preserves_owner_and_cache(provider):
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(_):
        started.set()
        await release.wait()
        return httpx.Response(200, json=healthy())

    client, calls, healthy = provider(handler)
    owner = asyncio.create_task(client.search("same-query"))
    await asyncio.wait_for(started.wait(), 1)
    waiter = asyncio.create_task(client.search("same-query"))
    await asyncio.sleep(0)
    waiter.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        release.set()
        await owner
    assert (await client.search("same-query"))["retrieval"]["cached"] is True
    assert (await client.search("another-query"))["results"]
    assert len(calls) == 2


async def test_cooldown_expires_without_holding_the_request_queue(provider):
    clock = [100.0]

    def handler(_):
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, json=healthy())

    client, calls, healthy = provider(handler, clock=lambda: clock[0])
    with pytest.raises(ERRORS, match="rate limit"):
        await client.search("first")
    clock[0] = 159
    with pytest.raises(ERRORS, match="cooldown"):
        await client.search("second")
    clock[0] = 160
    assert (await client.search("second"))["results"]
    assert len(calls) == 2
