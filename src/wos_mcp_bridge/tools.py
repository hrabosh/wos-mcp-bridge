"""Thin MCP adapters; application services remain independent of FastMCP."""

from __future__ import annotations

import json
from typing import Annotated, cast

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import Field, ValidationError

from .application import Services
from .literature_http import LiteratureError
from .models import (
    ArticleResponse,
    FetchResponse,
    LiteratureSearchResponse,
    OutputModel,
    SearchResponse,
    SectionResponse,
    SortOrder,
    WosSearchResponse,
)
from .wos_client import JsonObject, WosError

ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
NO_CONTROLS = r"^[^\x00-\x1f\x7f]+$"
ARTICLE_IDENTIFIER_PATTERN = (
    r"^ *(?:(?:[dD][oO][iI]:|[hH][tT][tT][pP][sS]://(?:[dD][xX]\.)?"
    r"[dD][oO][iI]\.[oO][rR][gG]/)?10\.[0-9]{4,9}/[^\s\"<>\\\x00-\x1f\x7f]+"
    r"|[pP][mM][cC][1-9][0-9]*|(?:[pP][mM][iI][dD]:)?[1-9][0-9]{0,11}) *$"
)
RecordLimit = Annotated[
    int,
    Field(
        strict=True,
        ge=1,
        le=50,
        description="Maximum records in this explicit page.",
        examples=[10],
    ),
]
TextLimit = Annotated[
    int,
    Field(
        strict=True,
        ge=1,
        le=20_000,
        description="Maximum returned text characters; metadata is additional.",
        examples=[10000],
    ),
]

WosQuery = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=4000,
        pattern=NO_CONTROLS,
        description="WoS advanced search across available Core Collection disciplines; no control characters.",
        examples=["TS=(quantum computing)", "TS=(climate change) AND PY=(2020-2026)"],
    ),
]
LiteratureQuery = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=4000,
        pattern=NO_CONTROLS,
        description="Europe PMC life-sciences query syntax; this is distinct from WoS syntax.",
        examples=["(canine OR dog) AND pulmonary", "cardiomyopathy AND OPEN_ACCESS:Y"],
    ),
]
WosId = Annotated[
    str,
    Field(
        strict=True,
        min_length=5,
        max_length=104,
        pattern=r"^WOS:[A-Za-z0-9][A-Za-z0-9._-]{0,99}$",
        description="A WOS: accession ID returned by search, not a DOI or URL.",
        examples=["WOS:000123456700001"],
    ),
]
ArticleIdentifier = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=512,
        pattern=ARTICLE_IDENTIFIER_PATTERN,
        description="Bare DOI, DOI: prefix, https://doi.org/ URL, PMID digits/PMID:digits, or PMCID PMCdigits; arbitrary URLs and WOS: IDs are rejected.",
        examples=["10.7717/peerj.4375", "PMID:12345", "PMC12345"],
    ),
]
Cursor = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=2048,
        pattern=NO_CONTROLS,
        description="Opaque Europe PMC next_cursor from the previous page; * starts a search.",
        examples=["*"],
    ),
]
SectionId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=512,
        pattern=NO_CONTROLS,
        description="Exact section.id returned by get_article; never a guessed section heading.",
        examples=["methods", "section-3"],
    ),
]


def _result(data: JsonObject, model: type[OutputModel]) -> ToolResult:
    try:
        payload = model.model_validate(data).model_dump(mode="json", exclude_unset=True)
    except ValidationError:
        # Pydantic error text may include untrusted payloads or credentials.
        raise ToolError(
            "The tool produced an invalid response. Please report this error."
        ) from None
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False, allow_nan=False), structured_content=payload
    )


def _services(ctx: Context) -> Services:
    return cast(Services, ctx.lifespan_context["services"])


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ANNOTATIONS,
        output_schema=SearchResponse.model_json_schema(mode="serialization"),
    )
    async def search(query: WosQuery, ctx: Context) -> ToolResult:
        """Return the first 10 WoS records by relevance using advanced syntax.

        Example: TS=(canine AND lung). Each result has id, title and url.
        Use fetch for bibliographic metadata, not the article full text.
        """
        client = _services(ctx).wos
        try:
            found = await client.search(query)
        except WosError as exc:
            raise ToolError(str(exc)) from None
        return _result(
            {
                "results": [
                    {key: hit[key] for key in ("id", "title", "url")} for hit in found["results"]
                ]
            },
            SearchResponse,
        )

    @mcp.tool(
        annotations=ANNOTATIONS, output_schema=FetchResponse.model_json_schema(mode="serialization")
    )
    async def fetch(id: WosId, ctx: Context) -> ToolResult:
        """Retrieve bibliographic metadata for a WOS: ID, NOT a full article or abstract."""
        client = _services(ctx).wos
        try:
            return _result(await client.fetch(id), FetchResponse)
        except WosError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(
        annotations=ANNOTATIONS,
        output_schema=WosSearchResponse.model_json_schema(mode="serialization"),
    )
    async def search_wos(
        query: WosQuery,
        ctx: Context,
        limit: RecordLimit = 10,
        page: Annotated[
            int,
            Field(
                strict=True,
                ge=1,
                le=2_147_483_647,
                description="One-based WoS page; no automatic pagination.",
                examples=[1],
            ),
        ] = 1,
        sort: Annotated[
            SortOrder,
            Field(
                description="Sort code: RS relevance, PY year, LD indexed date, TC citations; D descending, A ascending.",
                examples=["RS+D", "PY+D"],
            ),
        ] = "RS+D",
    ) -> ToolResult:
        """Search one page of WoS Core Collection and return bibliographic metadata.

        query: WoS advanced syntax, e.g. TS=(canine AND lung) AND PY=(2020-2026).
        limit: 1-50 records. page: starts at 1. No automatic pagination or retries.
        sort: RS+D relevance; PY+D newest; LD+D recently indexed; TC+D most cited
        (citation sorting depends on entitlement). Returns the exact query, total,
        DOIs, authors, available citation counts, and retrieval/quota information.
        """
        client = _services(ctx).wos
        try:
            return _result(
                await client.search(query, limit=limit, page=page, sort=sort), WosSearchResponse
            )
        except WosError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(
        annotations=ANNOTATIONS,
        output_schema=LiteratureSearchResponse.model_json_schema(mode="serialization"),
    )
    async def search_literature(
        query: LiteratureQuery,
        ctx: Context,
        limit: RecordLimit = 10,
        cursor: Cursor = "*",
        max_chars: TextLimit = 10_000,
    ) -> ToolResult:
        """Search one Europe PMC page for life-sciences articles and abstracts.

        Use Europe PMC syntax, e.g. (canine OR dog) AND pulmonary AND FIRST_PDATE:[2020 TO 2026].
        max_chars bounds total abstract text for this page. Results are deduplicated by DOI/PMCID.
        Supply next_cursor explicitly for another page. No OpenAlex requests or XML downloads.
        """
        client = _services(ctx).literature
        try:
            return _result(
                await client.search(query, limit, cursor, max_chars), LiteratureSearchResponse
            )
        except LiteratureError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(
        annotations=ANNOTATIONS,
        output_schema=ArticleResponse.model_json_schema(mode="serialization"),
    )
    async def resolve_article(
        identifier: ArticleIdentifier,
        ctx: Context,
        max_chars: TextLimit = 10_000,
    ) -> ToolResult:
        """Resolve a DOI, PMID or PMCID to metadata, abstract and legal OA links.

        Accepts DOI strings/doi.org URLs, PMID digits/PMID:digits, or PMCdigits.
        Queries Europe PMC and, for a DOI/PMID, OpenAlex. Never downloads publisher URLs.
        Missing abstracts are null; access.status is open_access, closed or unknown.
        """
        client = _services(ctx).literature
        try:
            return _result(await client.resolve(identifier, max_chars), ArticleResponse)
        except LiteratureError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(
        annotations=ANNOTATIONS,
        output_schema=ArticleResponse.model_json_schema(mode="serialization"),
    )
    async def get_article(
        identifier: ArticleIdentifier,
        ctx: Context,
        max_chars: TextLimit = 10_000,
        include_text: Annotated[
            bool,
            Field(
                strict=True,
                description="Include bounded section text; false returns the section directory and abstract.",
                examples=[True, False],
            ),
        ] = True,
    ) -> ToolResult:
        """Retrieve available Europe PMC OA XML as JATS sections, with a total text budget.

        Returns abstract and sections preserving headings, IDs and parent IDs. include_text=False
        returns the section directory plus bounded abstract. Metadata-reported XML availability
        can fail (see warnings). Non-OA articles return available metadata/abstract only.
        Use get_article_section with section.id and next_offset to read omitted text.
        """
        client = _services(ctx).literature
        try:
            return _result(
                await client.get_article(identifier, max_chars, include_text), ArticleResponse
            )
        except LiteratureError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(
        annotations=ANNOTATIONS,
        output_schema=SectionResponse.model_json_schema(mode="serialization"),
    )
    async def get_article_section(
        identifier: ArticleIdentifier,
        section_id: SectionId,
        ctx: Context,
        offset: Annotated[
            int,
            Field(
                strict=True,
                ge=0,
                le=2_147_483_647,
                description="Zero-based character offset within the exact section.",
                examples=[0],
            ),
        ] = 0,
        max_chars: TextLimit = 10_000,
    ) -> ToolResult:
        """Read one exact JATS section ID, using character offsets and bounded text.

        IDs come from get_article. Returns heading, original_id, parent_id, total_chars,
        truncated and next_offset. Never searches headings heuristically or invents missing text.
        """
        client = _services(ctx).literature
        try:
            return _result(
                await client.get_section(identifier, section_id, offset, max_chars), SectionResponse
            )
        except LiteratureError as exc:
            raise ToolError(str(exc)) from None
