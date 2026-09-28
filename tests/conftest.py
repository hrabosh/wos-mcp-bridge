"""Synthetic API responses only; no real credentials or network requests."""

import copy
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from wos_client import WosClient

HIT = {
    "uid": "WOS:TEST001",
    "title": "Synthetic veterinary study for automated tests",
    "source": {"sourceTitle": "TEST JOURNAL", "publishYear": 2026},
    "names": {"authors": [{"displayName": "Example, Alex"}]},
    "identifiers": {"doi": "10.0000/test-only", "pmid": "12345"},
    "citations": [],
    "keywords": {"authorKeywords": ["canine"]},
}


def api_response(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/documents"):
        body = {
            "metadata": {"total": 42, "page": 1, "limit": 10},
            "hits": [copy.deepcopy(HIT)],
        }
    else:
        body = copy.deepcopy(HIT)
    return httpx.Response(
        200,
        json=body,
        headers={"x-ratelimit-limit-day": "50", "x-ratelimit-remaining-day": "48"},
    )


@pytest.fixture
async def make_client():
    clients = []

    def factory(
        handler: Callable[[httpx.Request], httpx.Response] = api_response,
        **kwargs: Any,
    ):
        calls = []

        def recording_handler(request):
            calls.append(request)
            return handler(request)

        client = WosClient(
            "unit-test-key-not-a-secret",
            transport=httpx.MockTransport(recording_handler),
            min_interval=kwargs.pop("min_interval", 0),
            **kwargs,
        )
        clients.append(client)
        return client, calls

    yield factory
    for client in clients:
        await client.aclose()
