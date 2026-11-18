"""Application ownership and isolation, without real API keys or network traffic."""

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from conftest import api_response
from fastmcp import Client

from wos_mcp_bridge import server
from wos_mcp_bridge.literature_client import LiteratureClient


async def test_factory_servers_use_independent_borrowed_services(make_client):
    def response(title):
        def handle(request):
            body = api_response(request).json()
            body["hits"][0]["title"] = title
            return httpx.Response(200, json=body)

        return handle

    first, first_calls = make_client(response("First application"))
    second, second_calls = make_client(response("Second application"))
    literature = LiteratureClient()
    try:
        first_server = server.create_server(services=server.Services(first, literature))
        second_server = server.create_server(services=server.Services(second, literature))
        async with Client(first_server) as a, Client(second_server) as b:
            one = await a.call_tool("search", {"query": "same"})
            two = await b.call_tool("search", {"query": "same"})
            assert one.structured_content["results"][0]["title"] == "First application"
            assert two.structured_content["results"][0]["title"] == "Second application"
        assert not first._http.is_closed
        assert not second._http.is_closed
        assert not literature.europe_pmc._api._http.is_closed
        assert len(first_calls) == len(second_calls) == 1
    finally:
        await literature.aclose()


async def test_owned_services_are_closed_after_lifespan():
    async with server.open_services(server.Settings(wos_api_key="synthetic-key")) as services:
        assert not services.wos._http.is_closed
        assert not services.literature.europe_pmc._api._http.is_closed
    assert services.wos._http.is_closed
    assert services.literature.europe_pmc._api._http.is_closed
    assert services.literature.openalex._api._http.is_closed


async def test_partial_startup_failure_closes_created_clients(monkeypatch):
    from wos_mcp_bridge import application

    created = []
    original = application.EuropePmcClient
    original_wos = application.WosClient

    def record_pmc(**kwargs):
        client = original(**kwargs)
        created.append(client)
        return client

    def record_wos(*args, **kwargs):
        client = original_wos(*args, **kwargs)
        created.append(client)
        return client

    def fail_openalex(*args, **kwargs):
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(application, "WosClient", record_wos)
    monkeypatch.setattr(application, "EuropePmcClient", record_pmc)
    monkeypatch.setattr(application, "OpenAlexClient", fail_openalex)
    with pytest.raises(RuntimeError, match="startup failure"):
        async with application.open_services(server.Settings(wos_api_key="synthetic-key")):
            pytest.fail("startup should fail")
    assert len(created) == 2
    assert created[0]._http.is_closed
    assert created[1]._api._http.is_closed


async def test_cancelled_lifespan_closes_owned_clients():
    ready = asyncio.Event()
    observed = []

    async def run():
        async with server.open_services(server.Settings(wos_api_key="synthetic-key")) as services:
            observed.append(services)
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(ready.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    services = observed[0]
    assert services.wos._http.is_closed
    assert services.literature.europe_pmc._api._http.is_closed
    assert services.literature.openalex._api._http.is_closed


def test_settings_are_explicit_and_hide_credentials():
    settings = server.Settings.from_env(
        {
            "WOS_API_KEY": "synthetic-wos-secret",
            "OPENALEX_API_KEY": "synthetic-openalex-secret",
        }
    )
    assert settings.wos_api_key == "synthetic-wos-secret"
    assert settings.openalex_api_key == "synthetic-openalex-secret"
    assert "synthetic" not in repr(settings)
    assert server.Settings.from_env({}).wos_api_key == ""


async def test_default_factory_reads_environment_at_startup(monkeypatch):
    received = []
    application_server = server.create_server()

    @asynccontextmanager
    async def observe(settings):
        received.append(settings.wos_api_key)
        yield None  # Discovery never uses a service.

    monkeypatch.setattr(server, "open_services", observe)
    monkeypatch.setenv("WOS_API_KEY", "set-after-factory-creation")
    async with Client(application_server) as client:
        assert len(await client.list_tools()) == 7
    assert received == ["set-after-factory-creation"]
