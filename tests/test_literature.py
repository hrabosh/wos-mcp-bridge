"""Synthetic Europe PMC/OpenAlex HTTP, including failures and access restrictions."""

import asyncio
import copy
from types import SimpleNamespace

import httpx
import pytest

import literature_http
from article_xml import extract_jats
from europe_pmc import EuropePmcClient, identifier, normalise
from literature_client import LiteratureClient, deduplicate
from literature_http import LiteratureError
from openalex_client import OpenAlexClient

DOI = "10.1234/vet-test"
HIT = {
    "id": "12345",
    "source": "MED",
    "pmcid": "PMC12345",
    "doi": DOI,
    "title": "Synthetic veterinary article",
    "abstractText": "<p>Study abstract.</p>",
    "isOpenAccess": "Y",
    "license": "CC BY",
    "pubYear": "2026",
}
WORK = {
    "id": "https://openalex.org/W12345",
    "title": HIT["title"],
    "doi": f"https://doi.org/{DOI}",
    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345"},
    "open_access": {"is_oa": True},
    "best_oa_location": {
        "is_oa": True,
        "landing_page_url": "https://repository.example/paper",
        "pdf_url": "https://repository.example/paper.pdf",
        "license": "cc-by",
        "version": "acceptedVersion",
    },
}
JATS = b"""<?xml version="1.0"?><!DOCTYPE article SYSTEM "https://untrusted.example/jats.dtd">
<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
<article-id pub-id-type="pmc">12345</article-id>
<abstract><title>Abstract</title><p>XML abstract.</p></abstract>
<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/">
<license-p>Creative Commons Attribution</license-p></license></permissions>
</article-meta></front><body><p>Unsectioned introduction.</p>
<sec id="methods"><title>Materials and <italic>methods</italic></title><p>Dogs were studied.</p>
<sec id="patients"><title>Animals</title><p>Patient details.</p></sec></sec>
<sec><title>Results</title><p>Results &amp; observations.</p><list><list-item><p>One finding.</p>
</list-item></list></sec><sec id="section-4"><title>Discussion</title><p>Limitations.</p></sec>
</body><back><ref-list id="refs"><title>References</title><ref><mixed-citation>Real citation text.
</mixed-citation></ref></ref-list></back></article>"""


def search_body(records=None):
    return {
        "hitCount": len(records) if records is not None else 1,
        "nextCursorMark": "next",
        "resultList": {"result": copy.deepcopy(records if records is not None else [HIT])},
    }


@pytest.fixture
async def literature_factory():
    clients = []

    def factory(epmc_handler=None, oa_handler=None, **kwargs):
        calls = []

        def handle(request):
            calls.append(request)
            if request.url.host == "www.ebi.ac.uk":
                if epmc_handler:
                    return epmc_handler(request)
                if request.url.path.endswith("fullTextXML"):
                    return httpx.Response(200, content=JATS)
                return httpx.Response(200, json=search_body())
            assert request.url.host == "api.openalex.org"
            if oa_handler:
                return oa_handler(request)
            return httpx.Response(200, json=copy.deepcopy(WORK))

        transport = httpx.MockTransport(handle)
        client = LiteratureClient(
            EuropePmcClient(transport=transport, min_interval=0, **kwargs),
            OpenAlexClient("test-secret", transport=transport, min_interval=0, **kwargs),
        )
        clients.append(client)
        return client, calls

    yield factory
    for client in clients:
        await client.aclose()


async def test_search_exact_query_cursor_and_dedup(literature_factory):
    duplicate = {**HIT, "id": "99999", "pmcid": None, "doi": DOI.upper()}
    client, calls = literature_factory(
        lambda _: httpx.Response(200, json=search_body([HIT, duplicate]))
    )
    query = "(canine OR dog) AND FIRST_PDATE:[2020 TO 2026]"
    result = await client.search(query, limit=2, cursor="cursor-token", max_chars=5)
    assert result["returned_count"] == 1
    assert result["total"] == 2
    assert result["results"][0]["abstract"] == "Study"
    assert result["results"][0]["abstract_truncated"]
    assert calls[0].url.params["query"] == query
    assert calls[0].url.params["resultType"] == "core"
    assert calls[0].url.params["cursorMark"] == "cursor-token"
    assert len(calls) == 1  # No OA fanout or full text fetch.


def test_transitive_dedup_preserves_missing_identifiers():
    a = normalise({**HIT, "doi": None})
    b = normalise({**HIT, "pmcid": None, "id": "99999"})
    bridge = normalise(HIT)
    result = deduplicate([a, b, bridge])
    assert len(result) == 1
    assert result[0]["doi"] == DOI
    assert result[0]["pmcid"] == "PMC12345"


def test_dedup_preserves_all_identifier_aliases_and_oa_permissions():
    closed = normalise({**HIT, "pmcid": "PMC11111", "isOpenAccess": "N"})
    opened = normalise({**HIT, "pmcid": "PMC22222"})
    alias = normalise({**HIT, "pmcid": "PMC11111", "doi": None, "id": "99999"})
    result = deduplicate([closed, opened, alias])
    assert len(result) == 1
    assert result[0]["pmcid"] == "PMC22222"
    assert result[0]["access"]["full_text_xml_available"]


async def test_resolution_prefers_record_with_authorized_xml(literature_factory):
    closed = {**HIT, "pmcid": "PMC11111", "isOpenAccess": "N"}
    client, calls = literature_factory(
        lambda request: (
            httpx.Response(200, content=JATS)
            if request.url.path.endswith("fullTextXML")
            else httpx.Response(200, json=search_body([closed, HIT]))
        )
    )
    result = await client.get_article(DOI)
    assert result["pmcid"] == "PMC12345"
    assert calls[-1].url.path.endswith("/PMC12345/fullTextXML")


async def test_unknown_oa_license_and_non_oa_links_not_invented(literature_factory):
    client, calls = literature_factory(
        lambda _: httpx.Response(
            200, json=search_body([{**HIT, "license": None, "isOpenAccess": "N"}])
        ),
        lambda _: httpx.Response(
            200,
            json={
                **WORK,
                "best_oa_location": None,
                "open_access": {},
                "locations": [{"is_oa": False, "pdf_url": "https://publisher.example/paid.pdf"}],
            },
        ),
    )
    result = await client.get_article(DOI)
    assert result["access"]["status"] == "unknown"
    assert result["license"] is None
    assert result["oa_locations"] == []
    assert len(calls) == 2


async def test_oa_links_without_pmcid_never_downloaded(literature_factory):
    client, calls = literature_factory(
        lambda _: httpx.Response(200, json=search_body([{**HIT, "pmcid": None}]))
    )
    article = await client.get_article(DOI)
    assert article["access"]["status"] == "open_access"
    assert not article["access"]["full_text_xml_available"]
    assert article["oa_locations"]
    assert len(calls) == 2
    assert all(r.url.host in {"www.ebi.ac.uk", "api.openalex.org"} for r in calls)


async def test_doi_suffix_cannot_override_request_host_or_query(literature_factory):
    client, calls = literature_factory(
        lambda _: httpx.Response(200, json=search_body([])), lambda _: httpx.Response(404)
    )
    value = "10.1234//evil.example/path?api_key=bad#fragment"
    await client.resolve(value)
    assert calls[0].url.params["query"] == f'DOI:"{value}"'
    assert calls[1].url.host == "api.openalex.org"
    assert not calls[1].url.query
    assert not calls[1].url.fragment


@pytest.mark.parametrize(
    "value",
    [DOI, "DOI:" + DOI.upper(), "https://doi.org/" + DOI, "12345", "PMID:12345", "pmc12345"],
)
async def test_resolution_exact_identifiers_and_oa_links(literature_factory, value):
    client, calls = literature_factory()
    article = await client.resolve(value)
    assert article["doi"] == DOI
    assert article["pmcid"] == "PMC12345"
    assert article["source"] == ["Europe PMC", "OpenAlex"]
    assert article["access"]["status"] == "open_access"
    assert article["oa_locations"][0]["license"] == "cc-by"
    assert len(calls) == 2
    assert "test-secret" not in str(article)
    assert all(request.method == "GET" for request in calls)
    assert calls[1].headers["Authorization"] == "Bearer test-secret"
    assert "Authorization" not in calls[0].headers


async def test_missing_abstract_paywall_does_not_fetch_full_text(literature_factory):
    client, calls = literature_factory(
        lambda _: httpx.Response(
            200, json=search_body([{**HIT, "abstractText": None, "isOpenAccess": "N"}])
        ),
        lambda _: httpx.Response(
            200, json={**WORK, "open_access": {"is_oa": False}, "best_oa_location": None}
        ),
    )
    article = await client.get_article(DOI)
    assert article["abstract"] is None
    assert article["sections"] == []
    assert article["access"] == {
        "status": "closed",
        "content_status": "metadata_only",
        "full_text_xml_available": False,
    }
    assert len(calls) == 2


async def test_no_abstract_does_not_preclude_full_text(literature_factory):
    client, _ = literature_factory(
        lambda request: (
            httpx.Response(200, content=JATS)
            if request.url.path.endswith("fullTextXML")
            else httpx.Response(200, json=search_body([{**HIT, "abstractText": None}]))
        )
    )
    result = await client.get_article(DOI)
    assert result["abstract"] is None
    assert result["access"]["content_status"] == "full_text"
    assert any(s["text"] == "XML abstract." for s in result["sections"])


async def test_article_budget_and_section_paging(literature_factory):
    client, calls = literature_factory()
    article = await client.get_article(DOI, max_chars=30)
    assert article["access"]["content_status"] == "full_text"
    assert article["license"] == "Creative Commons Attribution"
    assert len(article["abstract"]) + sum(len(s["text"]) for s in article["sections"]) <= 30
    assert article["text_budget"]["truncated"]
    methods = next(s for s in article["sections"] if s["id"] == "methods")
    assert methods["heading"] == "Materials and methods"
    first = await client.get_section("PMC12345", "methods", max_chars=5)
    second = await client.get_section(DOI, "methods", offset=first["section"]["next_offset"])
    assert first["section"]["text"] + second["section"]["text"] == "Dogs were studied."
    assert second["section"]["next_offset"] is None
    directory = await client.get_article(DOI, include_text=False)
    assert all("text" not in s for s in directory["sections"])
    assert len(calls) == 4  # Two identifier queries; shared DOI OA lookup and XML cache.
    assert all(item["cached"] for item in directory["retrieval"])
    with pytest.raises(LiteratureError, match="Section not found"):
        await client.get_section(DOI, "invented")
    with pytest.raises(LiteratureError, match="exceeds"):
        await client.get_section(DOI, "methods", offset=999)


def test_jats_hierarchy_ids_inline_markup_and_no_duplicate_child_text():
    result = extract_jats(JATS)
    sections = {s["id"]: s for s in result["sections"]}
    assert sections["patients"]["parent_id"] == "methods"
    assert sections["methods"]["text"] == "Dogs were studied."
    assert sections["methods"]["original_id"] == "methods"
    assert len(sections) == len(result["sections"])
    assert any(s["original_id"] is None and s["heading"] == "Results" for s in sections.values())
    assert any(
        s["kind"] == "body" and s["text"] == "Unsectioned introduction." for s in sections.values()
    )
    assert "Real citation text." in sections["refs"]["text"]
    assert result["license_url"].startswith("https://creativecommons.org/")


@pytest.mark.parametrize(
    "raw",
    [
        b"not XML",
        b"<html/>",
        b'<!DOCTYPE article [<!ENTITY x SYSTEM "file:///etc/passwd">]><article>&x;</article>',
        b'<!DOCTYPE article [<!ENTITY x "bad">]><article>&x;</article>',
    ],
)
def test_invalid_and_entity_xml_rejected(raw):
    with pytest.raises(LiteratureError):
        extract_jats(raw)


def test_wrong_article_xml_rejected():
    with pytest.raises(LiteratureError, match="different article"):
        extract_jats(JATS, expected_pmcid="PMC99999")


def test_empty_and_deeply_nested_xml_are_safe_errors():
    with pytest.raises(LiteratureError, match="without article text"):
        extract_jats(b"<article><body/></article>")
    with pytest.raises(LiteratureError, match="nesting limit"):
        extract_jats(
            b"<article>" + b"<sec>" * 130 + b"<p>Text</p>" + b"</sec>" * 130 + b"</article>"
        )


def test_namespaced_jats_and_duplicate_ids_preserve_originals():
    result = extract_jats(
        b'<article xmlns="urn:jats"><body><sec id="x"><title>One</title><p>A</p>'
        b'</sec><sec id="x"><title>Two</title><p>B</p></sec></body></article>'
    )
    assert len({section["id"] for section in result["sections"]}) == 2
    assert all(section["original_id"] == "x" for section in result["sections"])


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://evil.example/article",
        "PMC../x",
        "PMID:0",
        '10.1234/x" OR canine',
        "10.1234/x\\",
        "10.1234/x\n",
    ],
)
async def test_invalid_identifier_no_requests(literature_factory, value):
    client, calls = literature_factory()
    with pytest.raises(LiteratureError):
        await client.resolve(value)
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": "q", "limit": True},
        {"query": "q", "limit": 51},
        {"query": "q", "cursor": "\x00"},
        {"query": "q", "max_chars": 20_001},
        {"query": "q", "max_chars": False},
    ],
)
async def test_invalid_search_no_requests(literature_factory, kwargs):
    client, calls = literature_factory()
    with pytest.raises(LiteratureError):
        await client.search(**kwargs)
    assert calls == []


@pytest.mark.parametrize("status", [403, 404])
async def test_xml_access_denied_returns_available_metadata(literature_factory, status):
    client, calls = literature_factory(
        lambda request: (
            httpx.Response(status, text="private body")
            if request.url.path.endswith("fullTextXML")
            else httpx.Response(200, json=search_body())
        )
    )
    article = await client.get_article(DOI)
    assert article["access"]["full_text_xml_available"] is False
    assert article["access"]["content_status"] == "abstract_only"
    assert article["warnings"]
    assert "private body" not in str(article)
    assert len(calls) == 3


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500, 503])
async def test_upstream_errors_safe_no_retry_or_redirect(literature_factory, status):
    client, calls = literature_factory(
        lambda _: httpx.Response(
            status, text="SECRET BODY", headers={"Location": "https://evil.example"}
        )
    )
    with pytest.raises(LiteratureError):
        await client.search("canine")
    assert len(calls) == 1


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
async def test_network_failure(literature_factory, exception):
    def fail(request):
        raise exception("secret internal details", request=request)

    client, calls = literature_factory(fail)
    with pytest.raises(LiteratureError) as error:
        await client.search("canine")
    assert "secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_xml_upstream_errors_do_not_claim_missing_article(literature_factory, status):
    client, calls = literature_factory(
        lambda request: (
            httpx.Response(status)
            if request.url.path.endswith("fullTextXML")
            else httpx.Response(200, json=search_body())
        )
    )
    with pytest.raises(LiteratureError) as error:
        await client.get_article(DOI)
    assert error.value.status == status
    assert len(calls) == 3


async def test_failed_resolution_does_not_return_false_not_found(literature_factory):
    client, _ = literature_factory(lambda _: httpx.Response(503), lambda _: httpx.Response(404))
    with pytest.raises(LiteratureError, match="incomplete"):
        await client.resolve(DOI)


async def test_oa_failure_degrades_with_warning(literature_factory):
    client, calls = literature_factory(oa_handler=lambda _: httpx.Response(503))
    result = await client.get_article(DOI)
    assert result["access"]["content_status"] == "full_text"
    assert result["source"] == ["Europe PMC"]
    assert "OpenAlex" in result["warnings"][0]
    assert len(calls) == 3


async def test_europe_pmc_failure_can_fall_back_to_openalex(literature_factory):
    client, calls = literature_factory(lambda _: httpx.Response(503))
    result = await client.resolve(DOI)
    assert result["source"] == ["OpenAlex"]
    assert result["warnings"]
    assert result["pmcid"] is None
    assert len(calls) == 2


async def test_not_found_and_wrong_search_hit(literature_factory):
    client, _ = literature_factory(
        lambda _: httpx.Response(200, json=search_body([{**HIT, "doi": "10.1234/other"}])),
        lambda _: httpx.Response(404),
    )
    assert (await client.resolve(DOI))["access"]["content_status"] == "not_found"


async def test_wrong_openalex_identity_never_merged(literature_factory):
    client, _ = literature_factory(
        oa_handler=lambda _: httpx.Response(200, json={**WORK, "doi": "10.1234/other"})
    )
    result = await client.resolve(DOI)
    assert result["source"] == ["Europe PMC"]
    assert result["oa_locations"] == []
    assert "different article" in result["warnings"][0]


@pytest.mark.parametrize(
    "body", [b"oops", b"[]", b"{}", b'{"hitCount":false,"resultList":{"result":[]}}']
)
async def test_malformed_search(literature_factory, body):
    client, _ = literature_factory(lambda _: httpx.Response(200, content=body))
    with pytest.raises(LiteratureError):
        await client.search("canine")


async def test_cache_copies_concurrency_expiry_and_eviction(literature_factory, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(literature_http, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client, calls = literature_factory(cache_ttl=10)
    results = await asyncio.gather(*(client.search("canine") for _ in range(3)))
    assert len(calls) == 1
    results[0]["results"][0]["title"] = "mutated"
    clock[0] = 109
    assert (await client.search("canine"))["results"][0]["title"] == HIT["title"]
    clock[0] = 111
    await client.search("canine")
    assert len(calls) == 2
    monkeypatch.setattr(literature_http, "MAX_CACHE_ENTRIES", 1)
    await client.search("feline")
    await client.search("canine")
    assert len(calls) == 4


async def test_spacing_and_rate_limit_cooldown(literature_factory, monkeypatch):
    clock, delays = [100.0], []

    async def sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(literature_http, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(literature_http, "asyncio", SimpleNamespace(Lock=asyncio.Lock, sleep=sleep))
    client, calls = literature_factory()
    client.europe_pmc._interval = 1.1
    await client.search("canine")
    await client.search("feline")
    assert delays == pytest.approx([1.1])
    limited, calls = literature_factory(
        lambda _: httpx.Response(429, headers={"Retry-After": "60"})
    )
    with pytest.raises(LiteratureError, match="rate limit"):
        await limited.search("canine")
    with pytest.raises(LiteratureError, match="cooldown"):
        await limited.search("feline")
    assert len(calls) == 1


def test_retry_after_date_and_invalid_values(monkeypatch):
    from datetime import UTC, datetime
    from email.utils import format_datetime

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(literature_http, "datetime", Clock)
    value = format_datetime(datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC))
    assert literature_http._retry_after(value) == 30
    assert literature_http._retry_after("7200") == 3600
    assert literature_http._retry_after("invalid") == 60
    assert literature_http._retry_after(None) == 60


async def test_response_and_cache_byte_bounds(literature_factory, monkeypatch):
    client, calls = literature_factory()
    monkeypatch.setattr(literature_http, "MAX_RESPONSE_BYTES", 5)
    with pytest.raises(LiteratureError, match="size limit"):
        await client.search("canine")
    monkeypatch.setattr(literature_http, "MAX_RESPONSE_BYTES", 10000)
    monkeypatch.setattr(literature_http, "MAX_CACHE_BYTES", 1)
    await client.search("canine")
    await client.search("canine")
    assert len(calls) == 3
    assert client.europe_pmc._cache_bytes == 0


@pytest.mark.parametrize(
    "value, expected",
    [
        ("doi:10.1234/X", ("doi", "10.1234/x")),
        ("PMID:12345", ("pmid", "12345")),
        ("pmc12345", ("pmcid", "PMC12345")),
    ],
)
def test_identifier_normalization(value, expected):
    assert identifier(value) == expected
