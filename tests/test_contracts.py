"""Published MCP contracts exercised with synthetic upstream API responses."""

import copy
import json

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from test_http_reliability import provider as _provider_fixture
from test_literature import DOI, HIT, WORK, search_body
from test_literature_server import literature_mcp as _literature_fixture
from test_server import mcp_client as _wos_fixture

from wos_mcp_bridge.europe_pmc import EuropePmcClient
from wos_mcp_bridge.literature_http import LiteratureError
from wos_mcp_bridge.openalex_client import OpenAlexClient
from wos_mcp_bridge.wos_client import WosError

provider = _provider_fixture
literature_mcp = _literature_fixture
mcp_client = _wos_fixture


async def test_discovery_publishes_concrete_output_schemas(mcp_client):
    client, calls = mcp_client
    for tool in await client.list_tools():
        assert tool.output_schema is not None, tool.name
        Draft202012Validator.check_schema(tool.output_schema)
        assert tool.output_schema["type"] == "object"
        assert tool.output_schema.get("properties"), tool.name
    assert calls == []


async def test_discovery_describes_text_input_constraints(mcp_client):
    client, _ = mcp_client
    tools = {tool.name: tool for tool in await client.list_tools()}
    fields = [
        ("search", "query", 4000),
        ("search_wos", "query", 4000),
        ("fetch", "id", 104),
        ("search_literature", "query", 4000),
        ("search_literature", "cursor", 2048),
        ("resolve_article", "identifier", 512),
        ("get_article", "identifier", 512),
        ("get_article_section", "identifier", 512),
        ("get_article_section", "section_id", 512),
    ]
    for tool, name, maximum in fields:
        field = tools[tool].input_schema["properties"][name]
        assert field["minLength"] >= 1
        assert field["maxLength"] == maximum
        assert field.get("pattern")
        assert field.get("description")
        assert field.get("examples")
    for tool in tools.values():
        assert not {"ctx", "services", "api_key"} & tool.input_schema["properties"].keys()


@pytest.mark.parametrize(
    "name,args",
    [
        ("search", {"query": "TS=canine"}),
        ("fetch", {"id": "WOS:TEST001"}),
        ("search_wos", {"query": "TS=canine"}),
    ],
)
async def test_wos_wire_payload_matches_discovered_schema(mcp_client, name, args):
    client, _ = mcp_client
    schema = next(t.output_schema for t in await client.list_tools() if t.name == name)
    result = await client.call_tool(name, args)
    assert json.loads(result.content[0].text) == result.structured_content
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(result.structured_content)


@pytest.mark.parametrize(
    "name,args",
    [
        ("search_literature", {"query": "canine", "max_chars": 1}),
        ("resolve_article", {"identifier": DOI}),
        ("get_article", {"identifier": DOI, "max_chars": 1}),
        ("get_article", {"identifier": DOI, "include_text": False}),
        ("get_article_section", {"identifier": DOI, "section_id": "methods", "max_chars": 5}),
    ],
)
async def test_literature_wire_payload_matches_discovered_schema(literature_mcp, name, args):
    client, _ = literature_mcp
    schema = next(t.output_schema for t in await client.list_tools() if t.name == name)
    result = await client.call_tool(name, args)
    assert json.loads(result.content[0].text) == result.structured_content
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(result.structured_content)


async def test_wos_additive_bibliography_preserves_legacy_fields(mcp_client):
    client, _ = mcp_client
    result = await client.call_tool("search_wos", {"query": "TS=canine"})
    record = result.structured_content["results"][0]
    assert record["authors"] == ["Example, Alex"]
    assert record["year"] == 2026
    assert record["citations"] is None
    assert record["bibliography"] == {
        "authors": ["Example, Alex"],
        "year": 2026,
        "journal": "TEST JOURNAL",
    }


async def test_europe_pmc_bibliography_uses_structured_names_without_splitting_legacy_text():
    hit = {
        **HIT,
        "authorString": "Smith J, Doe P",
        "authorList": {"author": [{"fullName": "Smith J"}, {"fullName": "Doe P"}]},
        "journalInfo": {"journal": {"title": "Synthetic journal"}},
    }
    client = EuropePmcClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=search_body([hit]))),
        min_interval=0,
    )
    try:
        record = (await client.search("canine"))["results"][0]
        assert record["authors"] == "Smith J, Doe P"
        assert record["year"] == "2026"
        assert record["bibliography"] == {
            "authors": ["Smith J", "Doe P"],
            "year": 2026,
            "journal": "Synthetic journal",
        }
    finally:
        await client.aclose()


async def test_combined_author_text_is_not_misrepresented_as_structured_authors():
    hit = {**HIT, "authorString": "Smith J, Doe P"}
    client = EuropePmcClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=search_body([hit]))),
        min_interval=0,
    )
    try:
        record = (await client.search("canine"))["results"][0]
        assert record["authors"] == "Smith J, Doe P"
        assert record["bibliography"]["authors"] is None
    finally:
        await client.aclose()


async def test_openalex_bibliography_preserves_legacy_author_null():
    work = {
        **WORK,
        "publication_year": 2026,
        "authorships": [{"author": {"display_name": "Example Author"}}],
        "primary_location": {"source": {"type": "journal", "display_name": "Example journal"}},
    }
    client = OpenAlexClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=work)), min_interval=0
    )
    try:
        article = (await client.resolve(DOI))["article"]
        assert article["authors"] is None
        assert article["bibliography"] == {
            "authors": ["Example Author"],
            "year": 2026,
            "journal": "Example journal",
        }
    finally:
        await client.aclose()


async def test_wos_pages_object_and_zero_citations_survive_validation(make_client):
    from conftest import HIT as WOS_HIT

    hit = copy.deepcopy(WOS_HIT)
    hit["source"]["pages"] = {"range": "1-9", "begin": "1", "end": "9", "count": 9}
    hit["citations"] = [{"db": "WOS", "count": 0}]
    client, _ = make_client(
        lambda _: httpx.Response(200, json={"metadata": {"total": 1}, "hits": [hit]})
    )
    record = (await client.search("TS=canine"))["results"][0]
    assert record["pages"] == {"range": "1-9", "begin": "1", "end": "9", "count": 9}
    assert record["citations"] == {"WOS": 0}
    assert record["bibliography"]["year"] == 2026


async def test_typed_optional_metadata_is_rejected_without_poisoning_cache(provider):
    def handler(_):
        body = healthy()
        if len(calls) == 1:
            if "hits" in body:
                body["hits"][0]["source"]["publishYear"] = True
            else:
                body["resultList"]["result"][0]["pubYear"] = True
        return httpx.Response(200, json=body)

    client, calls, healthy = provider(handler)
    with pytest.raises((WosError, LiteratureError)):
        await client.search("same-query")
    assert (await client.search("same-query"))["results"]
    assert len(calls) == 2


async def test_identifier_schema_advertises_actual_accepted_formats(mcp_client):
    client, _ = mcp_client
    schema = next(t.input_schema for t in await client.list_tools() if t.name == "resolve_article")
    validator = Draft202012Validator(schema)
    for value in [
        DOI,
        "DOI:" + DOI,
        "https://doi.org/" + DOI,
        "HTTPS://DX.DOI.ORG/" + DOI,
        "12345",
        "PMID:12345",
        "pmc12345",
    ]:
        assert validator.is_valid({"identifier": value}), value
    for value in ["https://evil.example/", "WOS:TEST001", "PMID:0", "PMC0", "10.123/x"]:
        assert not validator.is_valid({"identifier": value}), value


@pytest.mark.parametrize(
    "name,args",
    [
        ("search", {"query": "x" * 4001}),
        ("search_wos", {"query": "TS=canine", "limit": True}),
        ("search_wos", {"query": "TS=canine", "limit": "1"}),
        ("search_wos", {"query": "TS=canine", "page": 0}),
    ],
)
async def test_invalid_wos_input_does_not_reach_upstream(mcp_client, name, args):
    client, calls = mcp_client
    assert (await client.call_tool(name, args, raise_on_error=False)).is_error
    assert calls == []


@pytest.mark.parametrize(
    "name,args",
    [
        ("search_literature", {"query": "q", "cursor": "x" * 2049}),
        ("search_literature", {"query": "bad\nquery"}),
        ("get_article_section", {"identifier": DOI, "section_id": "x" * 513}),
        ("resolve_article", {"identifier": "1" * 513}),
    ],
)
async def test_invalid_literature_input_does_not_reach_upstream(literature_mcp, name, args):
    client, calls = literature_mcp
    assert (await client.call_tool(name, args, raise_on_error=False)).is_error
    assert calls == []


@pytest.mark.parametrize("scenario", ["empty-search", "not-found", "fallback", "zero-budget"])
async def test_variant_responses_match_the_schema_without_false_defaults(make_client, scenario):
    from fastmcp import Client

    from wos_mcp_bridge.literature_client import LiteratureClient
    from wos_mcp_bridge.server import Services, create_server

    def upstream(request):
        if request.url.host == "api.openalex.org":
            return (
                httpx.Response(404) if scenario == "not-found" else httpx.Response(200, json=WORK)
            )
        if scenario == "fallback":
            return httpx.Response(503)
        records = (
            []
            if scenario in {"empty-search", "not-found"}
            else [HIT, {**HIT, "id": "99999", "pmcid": "PMC99999", "doi": "10.1234/other"}]
        )
        return httpx.Response(200, json=search_body(records))

    transport = httpx.MockTransport(upstream)
    literature = LiteratureClient(
        EuropePmcClient(transport=transport, min_interval=0),
        OpenAlexClient(transport=transport, min_interval=0),
    )
    wos, _ = make_client()
    name = "resolve_article" if scenario in {"not-found", "fallback"} else "search_literature"
    args = {"identifier": DOI} if name == "resolve_article" else {"query": "q", "max_chars": 1}
    try:
        async with Client(create_server(services=Services(wos, literature))) as client:
            schema = next(t.output_schema for t in await client.list_tools() if t.name == name)
            result = await client.call_tool(name, args)
            payload = result.structured_content
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
            assert json.loads(result.content[0].text) == payload
            if scenario == "empty-search":
                assert payload["results"] == [] and payload["total"] == 0
            elif scenario == "not-found":
                assert payload["access"]["content_status"] == "not_found"
                assert payload["title"] is None
                assert payload["bibliography"] == {"authors": None, "year": None, "journal": None}
                assert "authors" not in payload and "year" not in payload
            elif scenario == "fallback":
                assert payload["source"] == ["OpenAlex"] and payload["warnings"]
            else:
                assert payload["results"][1]["abstract"] == ""
                assert payload["results"][1]["abstract_truncated"] is True
                assert payload["results"][1]["text_budget"]["max_chars"] == 0
    finally:
        await literature.aclose()


def test_response_validation_errors_never_expose_untrusted_payloads():
    from fastmcp.exceptions import ToolError

    from wos_mcp_bridge.models import WosSearchResponse
    from wos_mcp_bridge.tools import _result

    with pytest.raises(ToolError) as error:
        _result(
            {"query": "PRIVATE_RESPONSE_SECRET", "metadata": {"total": True}}, WosSearchResponse
        )
    assert "PRIVATE_RESPONSE_SECRET" not in str(error.value)
    assert "invalid response" in str(error.value)


async def test_incomplete_wos_author_list_stays_unknown_in_common_metadata(make_client):
    from conftest import HIT as WOS_HIT

    hit = copy.deepcopy(WOS_HIT)
    hit["names"]["authors"].append({"researcherId": "synthetic-missing-name"})
    client, _ = make_client(
        lambda _: httpx.Response(200, json={"metadata": {"total": 1}, "hits": [hit]})
    )
    record = (await client.search("TS=canine"))["results"][0]
    assert record["authors"] == ["Example, Alex"]  # Legacy filtering remains compatible.
    assert record["bibliography"]["authors"] is None


async def test_openalex_repository_is_not_labeled_a_journal():
    work = {
        **WORK,
        "primary_location": {
            "source": {"type": "repository", "display_name": "Synthetic repository"}
        },
    }
    client = OpenAlexClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=work)), min_interval=0
    )
    try:
        assert (await client.resolve(DOI))["article"]["bibliography"]["journal"] is None
    finally:
        await client.aclose()


async def test_wos_null_keywords_preserve_empty_legacy_list(make_client):
    from conftest import HIT as WOS_HIT

    hit = copy.deepcopy(WOS_HIT)
    hit["keywords"] = {"authorKeywords": None}
    client, _ = make_client(
        lambda request: httpx.Response(200, json={"metadata": {"total": 1}, "hits": [hit]})
    )
    result = await client.search("TS=(test)")
    assert result["results"][0]["keywords"] == []


def test_contract_format_checkers_are_installed():
    checker = FormatChecker()
    assert {"iri", "date-time"} <= checker.checkers.keys()
    assert not checker.conforms("https://example.org/paper%ZZ", "iri")
    assert not checker.conforms("2026-01-01T10:20:30+00:75", "date-time")


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://example.org/paper%ZZ",
        "https://example.org/{}",
        "https://example.org/[path]",
        "https://example.org/#one#two",
    ],
)
async def test_invalid_openalex_link_does_not_poison_cache(bad_url):
    calls = []

    def handle(request):
        calls.append(request)
        work = copy.deepcopy(WORK)
        if len(calls) == 1:
            work["best_oa_location"]["pdf_url"] = bad_url
        return httpx.Response(200, json=work)

    client = OpenAlexClient(transport=httpx.MockTransport(handle), min_interval=0)
    try:
        with pytest.raises(LiteratureError):
            await client.resolve(DOI)
        assert (await client.resolve(DOI))["article"]["title"]
        assert len(calls) == 2
    finally:
        await client.aclose()


def test_invalid_timestamp_offset_is_rejected():
    from pydantic import ValidationError

    from wos_mcp_bridge.models import LiteratureRetrieval

    with pytest.raises(ValidationError):
        LiteratureRetrieval(
            source="Europe PMC", cached=False, fetched_at_utc="2026-01-01T10:20:30+00:75"
        )


@pytest.mark.parametrize(
    "section_id",
    ["  ", "s" * 513, "bad&#10;id", " padded "],
    ids=["blank", "too-long", "control", "padded"],
)
async def test_invalid_xml_sections_do_not_poison_cache(section_id):
    from test_literature import JATS

    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            content=f'<article><body><sec id="{section_id}"><p>hello</p></sec></body></article>'.encode()
            if len(calls) == 1
            else JATS,
        )

    client = EuropePmcClient(transport=httpx.MockTransport(handle), min_interval=0)
    try:
        with pytest.raises(LiteratureError):
            await client.full_text("PMC12345")
        assert (await client.full_text("PMC12345"))["sections"]
        assert len(calls) == 2
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "bad_cursor", [42, "x" * 2049, "bad\ncursor"], ids=["wrong-type", "too-long", "control"]
)
async def test_invalid_continuation_does_not_poison_cache(bad_cursor):
    calls = []

    def handle(request):
        calls.append(request)
        body = search_body()
        if len(calls) == 1:
            body["nextCursorMark"] = bad_cursor
        return httpx.Response(200, json=body)

    client = EuropePmcClient(transport=httpx.MockTransport(handle), min_interval=0)
    try:
        with pytest.raises(LiteratureError):
            await client.search("same")
        result = await client.search("same")
        assert result["next_cursor"] == "next"
        assert len(calls) == 2
    finally:
        await client.aclose()


async def test_unicode_openalex_url_preserves_iri_contract():
    url = "https://例え.テスト/článek?q=věda#výsledek"
    work = copy.deepcopy(WORK)
    work["best_oa_location"]["pdf_url"] = url
    client = OpenAlexClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=work)), min_interval=0
    )
    try:
        article = (await client.resolve(DOI))["article"]
        assert article["url"] == url
        assert FormatChecker().conforms(article["url"], "iri")
    finally:
        await client.aclose()


async def test_unknown_citation_count_remains_null(make_client):
    from conftest import HIT as WOS_HIT

    hit = copy.deepcopy(WOS_HIT)
    hit["citations"] = [{"db": "WOS", "count": None}]
    client, _ = make_client(
        lambda _: httpx.Response(200, json={"metadata": {"total": 1}, "hits": [hit]})
    )
    assert (await client.search("TS=test"))["results"][0]["citations"] is None
