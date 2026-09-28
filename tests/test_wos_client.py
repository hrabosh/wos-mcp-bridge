import asyncio
import copy
from types import SimpleNamespace

import httpx
import pytest
from conftest import HIT, api_response

import wos_client
from wos_client import WosClient, WosError


async def test_search_sends_fixed_endpoint_and_exact_query(make_client):
    client, calls = make_client()
    query = 'TS=("lung cancer" AND canine)'
    result = await client.search(query, limit=1, page=2, sort="PY+D")
    request = calls[0]
    assert request.method == "GET"
    assert request.url.host == "api.clarivate.com"
    assert request.url.path == "/apis/wos-starter/v1/documents"
    assert request.url.params["q"] == query
    assert request.url.params["sortField"] == "PY+D"
    assert request.url.params["db"] == "WOS"
    assert request.headers["X-ApiKey"] == "unit-test-key-not-a-secret"
    assert result["metadata"] == {"total": 42, "page": 2, "limit": 1}
    assert result["results"][0]["doi"] == "10.0000/test-only"
    assert result["results"][0]["citations"] is None
    assert "unit-test-key" not in str(result)
    assert result["retrieval"]["quota_at_fetch"]["x-ratelimit-remaining-day"] == "48"


@pytest.mark.parametrize("count", [0, 12])
async def test_supplied_citation_counts_are_preserved(make_client, count):
    def handler(request):
        body = api_response(request).json()
        body["hits"][0]["citations"] = [{"db": "WOS", "count": count}]
        return httpx.Response(200, json=body)

    client, _ = make_client(handler)
    assert (await client.search("TS=canine"))["results"][0]["citations"] == {"WOS": count}


@pytest.mark.parametrize("count", [True, -1, "12", None])
async def test_invalid_citation_counts_are_not_reported_as_facts(make_client, count):
    def handler(request):
        body = api_response(request).json()
        body["hits"][0]["citations"] = [{"db": "WOS", "count": count}]
        return httpx.Response(200, json=body)

    client, _ = make_client(handler)
    assert (await client.search("TS=canine"))["results"][0]["citations"] is None


async def test_query_and_record_cache_avoid_extra_requests(make_client):
    client, calls = make_client()
    first = await client.search("TS=canine")
    first["results"][0]["title"] = "Changed by caller"
    cached = await client.search("TS=canine")
    fetched = await client.fetch("WOS:TEST001")
    assert len(calls) == 1
    assert cached["results"][0]["title"] == HIT["title"]
    assert cached["retrieval"]["cached"] is True
    assert first["retrieval"]["cached"] is False
    assert fetched["metadata"]["retrieval"]["cached"] is True
    assert "NOT article full text" in fetched["text"]


async def test_cache_reads_do_not_extend_original_expiry(make_client, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(wos_client, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client, calls = make_client(cache_ttl=10)
    first = await client.search("TS=canine")
    clock[0] = 109.0
    cached = await client.search("TS=canine")
    assert cached["retrieval"]["fetched_at_utc"] == first["retrieval"]["fetched_at_utc"]
    clock[0] = 111.0
    fetched = await client.fetch("WOS:TEST001")
    assert len(calls) == 2
    assert fetched["metadata"]["retrieval"]["cached"] is False


async def test_cache_is_bounded(make_client, monkeypatch):
    monkeypatch.setattr(wos_client, "MAX_CACHE_ENTRIES", 3)
    client, calls = make_client()
    for query in ("TS=one", "TS=two", "TS=three", "TS=one"):
        await client.search(query)
    assert len(calls) == 4
    assert len(client._cache) == 3


async def test_concurrent_identical_searches_share_a_request(make_client):
    client, calls = make_client()
    results = await asyncio.gather(*(client.search("TS=canine") for _ in range(5)))
    assert len(calls) == 1
    assert sum(not result["retrieval"]["cached"] for result in results) == 1


async def test_request_spacing_is_enforced(make_client, monkeypatch):
    clock, delays = [100.0], []

    async def sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(wos_client, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(wos_client, "asyncio", SimpleNamespace(Lock=asyncio.Lock, sleep=sleep))
    client, calls = make_client(min_interval=1.1)
    await client.search("TS=one")
    await client.search("TS=two")
    assert len(calls) == 2
    assert delays == pytest.approx([1.1])


async def test_fetch_uses_document_endpoint(make_client):
    client, calls = make_client()
    result = await client.fetch("WOS:TEST001")
    assert calls[0].url.path == "/apis/wos-starter/v1/documents/WOS:TEST001"
    assert result["id"] == "WOS:TEST001"
    assert result["metadata"]["retrieval"]["cached"] is False


@pytest.mark.parametrize(
    "uid", ["", "https://example.com", "WOS:../secret", "WOS:X?x=1", "DOI:123"]
)
async def test_invalid_ids_do_not_make_requests(make_client, uid):
    client, calls = make_client()
    with pytest.raises(WosError):
        await client.fetch(uid)
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": "a" * 4001},
        {"query": "TS=canine\x00"},
        {"query": "TS=canine", "limit": 51},
        {"query": "TS=canine", "limit": 0},
        {"query": "TS=canine", "limit": True},
        {"query": "TS=canine", "page": 0},
        {"query": "TS=canine", "page": 2**31},
        {"query": "TS=canine", "sort": "invalid"},
    ],
)
async def test_invalid_searches_do_not_make_requests(make_client, kwargs):
    client, calls = make_client()
    with pytest.raises(WosError):
        await client.search(**kwargs)
    assert calls == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 302])
async def test_errors_are_sanitized_and_never_retried(make_client, status):
    client, calls = make_client(
        lambda _: httpx.Response(
            status,
            json={"error": "PRIVATE UPSTREAM BODY"},
            headers={"Location": "https://evil.test"},
        )
    )
    with pytest.raises(WosError) as exc:
        await client.search("TS=canine")
    assert "PRIVATE" not in str(exc.value)
    assert "unit-test-key" not in str(exc.value)
    assert "No automatic retry" in str(exc.value)
    assert len(calls) == 1


async def test_timeout_is_sanitized(make_client):
    def handler(request):
        raise httpx.ReadTimeout("PRIVATE REQUEST DETAILS", request=request)

    client, calls = make_client(handler)
    with pytest.raises(WosError, match="could not be reached") as exc:
        await client.search("TS=canine")
    assert "PRIVATE" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [[], {}, {"metadata": {"total": "42"}, "hits": []}])
async def test_malformed_responses_are_rejected(make_client, body):
    client, _ = make_client(lambda _: httpx.Response(200, json=body))
    with pytest.raises(WosError):
        await client.search("TS=canine")


async def test_invalid_json_is_rejected(make_client):
    client, _ = make_client(lambda _: httpx.Response(200, text="not-json"))
    with pytest.raises(WosError, match="JSON"):
        await client.search("TS=canine")


async def test_wrong_record_id_is_rejected(make_client):
    client, _ = make_client()
    with pytest.raises(WosError, match="different record ID"):
        await client.fetch("WOS:OTHER")


async def test_missing_optional_metadata_is_allowed(make_client):
    client, _ = make_client(lambda _: httpx.Response(200, json={"uid": "WOS:X", "title": "Test"}))
    record = await client.fetch("WOS:X")
    assert record["metadata"]["doi"] is None
    assert record["metadata"]["authors"] == []


@pytest.mark.parametrize("bad_field", [{"title": ""}, {"source": "invalid"}, {"uid": "bad"}])
async def test_malformed_record_is_not_silently_accepted(make_client, bad_field):
    hit = copy.deepcopy(HIT)
    hit.update(bad_field)
    client, _ = make_client(lambda _: httpx.Response(200, json=hit))
    with pytest.raises(WosError):
        await client.fetch("WOS:TEST001")


@pytest.mark.parametrize(
    "url",
    ["https://evil.test/record", "javascript:alert(1)", "https://user:pass@www.webofscience.com/x"],
)
async def test_untrusted_record_urls_are_replaced(make_client, url):
    hit = {**HIT, "links": {"record": url}}
    client, _ = make_client(lambda _: httpx.Response(200, json=hit))
    record = await client.fetch("WOS:TEST001")
    assert record["url"] == "https://www.webofscience.com/wos/woscc/full-record/WOS:TEST001"


def test_missing_key_fails_before_a_request():
    with pytest.raises(WosError, match="WOS_API_KEY"):
        WosClient(" ")


async def test_close_releases_the_connection_pool(make_client):
    client, _ = make_client()
    await client.search("TS=canine")
    await client.aclose()
    assert client._http.is_closed
    assert not client._cache
