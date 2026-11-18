"""Loopback MCP transport tests with synthetic signing keys and provider responses."""

import asyncio
import socket
import sys
import time
from contextlib import aclosing, asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from conftest import api_response
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from wos_mcp_bridge import application, server
from wos_mcp_bridge.literature_client import LiteratureClient

ISSUER = "https://issuer.example"
QUERY = "TS=(quantum computing)"
TOOL_ARGUMENTS = {"query": QUERY}
SLOW_QUERY = {"query": "TS=(cancellation test)"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "transport-test", "version": "1"},
    },
    "id": 1,
}


@asynccontextmanager
async def loopback_server(app, listener):
    config = uvicorn.Config(
        app, lifespan="on", log_level="error", ws="none", timeout_graceful_shutdown=2
    )
    process = uvicorn.Server(config)
    task = asyncio.create_task(process.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not process.started:
                if task.done():
                    task.result()
                    raise RuntimeError("ASGI server failed to start")
                await asyncio.sleep(0.01)
        yield
    finally:
        process.should_exit = True
        await asyncio.wait_for(task, timeout=5)


@pytest.fixture
def signing_keys(monkeypatch):
    keys = RSAKeyPair.generate()

    async def offline_key(_self, _kid):
        return keys.public_key

    monkeypatch.setattr(JWTVerifier, "_get_jwks_key", offline_key)
    return keys


def stdio_client(tmp_path, *, mode="auto"):
    fixture = Path(__file__).with_name("stdio_fixture.py")
    return Client(
        StdioTransport(
            command=sys.executable,
            args=[str(fixture)],
            env={
                "PYTHONPATH": str(fixture.parents[1] / "src"),
                "WOS_API_KEY": "synthetic-key",
                "WOS_MCP_MIN_INTERVAL_SECONDS": "0",
                "WOS_MCP_TEST_MARKERS": str(tmp_path),
                "FASTMCP_CHECK_FOR_UPDATES": "off",
            },
            cwd=str(tmp_path),
            keep_alive=False,
        ),
        timeout=5,
        mode=mode,
    )


async def test_http_auth_discovery_health_and_tool_parity(make_client, signing_keys, tmp_path):
    from wos_mcp_bridge.http_server import HTTPSettings, create_http_app

    wos, upstream_calls = make_client()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        endpoint = f"{origin}/mcp"
        settings = HTTPSettings(origin, ISSUER, f"{ISSUER}/jwks")
        claims = {"issuer": ISSUER, "audience": endpoint, "scopes": ["mcp:read"]}
        token = signing_keys.create_token(**claims)

        async with aclosing(LiteratureClient()) as literature:
            services = server.Services(wos, literature)
            app = create_http_app(settings, services=services)
            async with (
                loopback_server(app, listener),
                httpx.AsyncClient(base_url=origin, trust_env=False) as http,
            ):
                health = await http.get("/healthz")
                assert health.status_code == 200
                assert health.json() == {"status": "ok"}

                metadata = await http.get("/.well-known/oauth-protected-resource/mcp")
                assert metadata.json()["resource"] == endpoint
                assert metadata.json()["authorization_servers"] == [ISSUER]
                assert metadata.json()["scopes_supported"] == ["mcp:read"]

                missing = await http.post("/mcp", json=INITIALIZE)
                assert missing.status_code == 401
                assert "resource_metadata=" in missing.headers["www-authenticate"]
                call = {"name": "search", "arguments": TOOL_ARGUMENTS}
                unauthenticated = await http.post(
                    "/mcp", json=INITIALIZE | {"method": "tools/call", "params": call}
                )
                assert unauthenticated.status_code == 401
                for invalid in (
                    "malformed-token",
                    signing_keys.create_token(**(claims | {"issuer": "https://wrong.example"})),
                    signing_keys.create_token(
                        **(claims | {"audience": "https://wrong.example/mcp"})
                    ),
                    signing_keys.create_token(**claims, expires_in_seconds=-60),
                    signing_keys.create_token(**claims, additional_claims={"exp": None}),
                    signing_keys.create_token(
                        **claims, additional_claims={"nbf": int(time.time()) + 60}
                    ),
                ):
                    response = await http.post(
                        "/mcp", json=INITIALIZE, headers={"Authorization": f"Bearer {invalid}"}
                    )
                    assert response.status_code == 401

                limited = signing_keys.create_token(**(claims | {"scopes": ["unrelated:read"]}))
                insufficient = await http.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={"Authorization": f"Bearer {limited}"},
                )
                assert insufficient.status_code == 403
                assert 'error="insufficient_scope"' in insufficient.headers["www-authenticate"]
                assert 'scope="mcp:read"' in insufficient.headers["www-authenticate"]

                authorization = {"Authorization": f"Bearer {token}"}
                origin_response = await http.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers=authorization | {"Origin": "https://foreign.example"},
                )
                assert origin_response.status_code == 403
                host_response = await http.post(
                    "/mcp", json=INITIALIZE, headers=authorization | {"Host": "foreign.example"}
                )
                assert host_response.status_code == 421
                assert upstream_calls == []

                async with (
                    Client(endpoint, auth=token, timeout=5, mode="legacy") as remote,
                    stdio_client(tmp_path, mode="legacy") as local,
                ):
                    assert remote.protocol_version == local.protocol_version == "2025-11-25"
                    assert sorted(await remote.list_tools(), key=lambda t: t.name) == sorted(
                        await local.list_tools(), key=lambda t: t.name
                    )
                    result = await remote.call_tool("search", TOOL_ARGUMENTS)
                    assert (
                        result.structured_content
                        == (await local.call_tool("search", TOOL_ARGUMENTS)).structured_content
                    )
                    assert result.structured_content["results"][0]["id"] == "WOS:TEST001"
                assert len(upstream_calls) == 1
                assert upstream_calls[0].headers["x-apikey"] == "unit-test-key-not-a-secret"
                assert "authorization" not in upstream_calls[0].headers


def test_http_requires_explicit_auth_configuration():
    from wos_mcp_bridge.http_server import HTTPSettings

    with pytest.raises(ValueError, match="WOS_MCP_HTTP_ORIGIN"):
        HTTPSettings.from_env({})
    with pytest.raises(ValueError, match="WOS_MCP_HTTP_ORIGIN"):
        HTTPSettings("http://public.example", ISSUER, f"{ISSUER}/jwks")
    with pytest.raises(ValueError, match="WOS_MCP_AUTH_JWKS_URI"):
        HTTPSettings("https://public.example", ISSUER, "http://issuer.example/jwks")
    with pytest.raises(ValueError, match="WOS_MCP_AUTH_ISSUER"):
        HTTPSettings("https://public.example", "https://ISSUER.example:443", f"{ISSUER}/jwks")
    for origin in ("https://*", "https://%2a.example.com"):
        with pytest.raises(ValueError, match="WOS_MCP_HTTP_ORIGIN"):
            HTTPSettings(origin, ISSUER, f"{ISSUER}/jwks")


async def test_stdio_cancellation_releases_upstream(tmp_path):
    async with stdio_client(tmp_path) as client:
        assert client.protocol_version == "2026-07-28"
        request = asyncio.create_task(client.call_tool("search", SLOW_QUERY))
        try:
            async with asyncio.timeout(5):
                while not (tmp_path / "started").exists():
                    await asyncio.sleep(0.01)
        finally:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        async with asyncio.timeout(5):
            while not (tmp_path / "finished").exists():
                await asyncio.sleep(0.01)
        assert (await client.call_tool("search", SLOW_QUERY)).structured_content["results"]


async def test_http_cancellation_releases_upstream_and_shutdown_closes_it(
    monkeypatch, signing_keys
):
    from wos_mcp_bridge.http_server import HTTPSettings, create_http_app

    started = asyncio.Event()
    cancelled = asyncio.Event()
    owned_clients = []
    original = application.WosClient

    async def upstream(request):
        if not started.is_set():
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return api_response(request)

    def offline_client(*args, **kwargs):
        client = original(*args, **kwargs, transport=httpx.MockTransport(upstream))
        owned_clients.append(client)
        return client

    monkeypatch.setattr(application, "WosClient", offline_client)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        endpoint = f"{origin}/mcp"
        token = signing_keys.create_token(issuer=ISSUER, audience=endpoint, scopes=["mcp:read"])
        app = create_http_app(
            HTTPSettings(origin, ISSUER, f"{ISSUER}/jwks"),
            settings=server.Settings(wos_api_key="synthetic-key", min_interval=0),
        )
        async with (
            loopback_server(app, listener),
            Client(endpoint, auth=token, timeout=5) as client,
        ):
            assert client.protocol_version == "2026-07-28"
            request = asyncio.create_task(client.call_tool("search", SLOW_QUERY))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
            finally:
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            await asyncio.wait_for(cancelled.wait(), timeout=5)
            assert (await client.call_tool("search", SLOW_QUERY)).structured_content["results"]
        assert len(owned_clients) == 1
        assert owned_clients[0]._http.is_closed
